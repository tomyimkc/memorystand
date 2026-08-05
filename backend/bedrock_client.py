# SPDX-License-Identifier: Apache-2.0
"""A thin, honest wrapper over Bedrock's Converse API.

The reasoning model is Amazon Nova by default (``DEFAULT_MODEL_ID`` below), not Claude:
Anthropic models on Bedrock are refused from this operator's country with a
``ValidationException``, independent of IAM and region -- see docs/BEDROCK_QUOTA.md. This
module itself is model-agnostic; it forwards whatever ``MODEL_ID`` resolves to.

"Thin" is a design choice, not a shortcut: this module does prompt-shaping for
nobody. It sends exactly what it is given (system prompt, messages, optional
tool specs) and hands back exactly what Bedrock returns. Every decision about
*what* to ask the model lives in ``backend/agent.py``; this module only knows
how to reach Bedrock and how to fail without taking the caller down with it.

Failure handling is the point of this file. An on-call agent demo has to work
in front of a judge who may not have AWS credentials configured, may not have
requested access to the configured chat model in Bedrock, or may be rate limited. None
of those are bugs in this codebase, and none of them should raise an opaque
botocore exception into ``backend/agent.py`` -- they all collapse into the one
typed ``ModelUnavailable``, which the agent loop catches to run its
deterministic fallback. Throttling is the one case worth retrying before
giving up, so it gets bounded exponential backoff with jitter; everything
else (no credentials, access denied, unknown model id, network failure) is
not going to resolve itself in a few hundred milliseconds and fails fast.
"""

from __future__ import annotations

import os
import random
import time
from typing import Any

from . import breaker

# MEMORYSTAND_CHAT_MODEL overrides this. The default below is a safe *starting point*,
# not a guarantee: it is the id used in scripts/spike_bedrock.py, but Bedrock model
# access is granted per AWS account and per region, so the account actually running
# this must have requested access to whichever model id ends up here (Bedrock
# console -> Model access), or every call raises ModelUnavailable and the agent
# loop runs on its deterministic fallback instead.
DEFAULT_MODEL_ID = "amazon.nova-lite-v1:0"
MODEL_ID = os.environ.get("MEMORYSTAND_CHAT_MODEL", DEFAULT_MODEL_ID)
REGION = os.environ.get("AWS_REGION", "us-west-2")

# The MODEL region is deliberately separate from AWS_REGION.
#
# Bedrock on-demand quota is granted per region, and it is not uniform: on this account
# Nova Lite is 400 requests/min in eu-west-1, ap-southeast-1 and ap-northeast-1, and
# exactly 0 in us-east-1 and us-west-2 -- which is where everything else lives. Reading
# the quota in one region and concluding "the account is held" was wrong; it is a
# per-region grant.
#
# So the model clients get their own knob. AWS_REGION stays us-west-2 for CloudWatch,
# SSM and the database, because CloudWatch metrics only exist in the region that emitted
# them -- pointing the evidence checker elsewhere would silently return "no datapoints"
# and downgrade every outcome to `attested`. Only inference moves.
#
# AWS_REGION cannot be overridden in Lambda's own environment (it is reserved), which is
# a second reason this needs a distinct variable rather than a clever default.
BEDROCK_REGION = os.environ.get(
    "MEMORYSTAND_BEDROCK_REGION", os.environ.get("AWS_REGION", "us-west-2")
)


# Retry policy for throttling only. Small and bounded for the same reason
# backend/db.py's retry budget is small: a caller waiting on this is an on-call
# agent loop, not a batch job, and it has its own fallback to fall back to.
MAX_ATTEMPTS = 5

# Hard ceiling on total time spent trying, regardless of attempts left.
#
# The attempt budget alone is not a latency budget: 5 attempts with backoff up to
# MAX_BACKOFF_S can exceed the 30s Lambda timeout, and when /decide started genuinely
# calling the model it began returning 502 after a full 30s hang instead of falling back
# in a few hundred milliseconds. backend/embeddings.py learned this first; a throttled
# dependency must never be able to consume the caller's whole budget.
DEADLINE_S = float(os.environ.get("MEMORYSTAND_MODEL_DEADLINE_S", "8"))
BASE_BACKOFF_S = 0.5
MAX_BACKOFF_S = 8.0

# Error codes from Bedrock that mean "try again shortly", not "this will never work".
_THROTTLING_CODES = {
    "ThrottlingException",
    "TooManyRequestsException",
    "ServiceQuotaExceededException",
    "ModelNotReadyException",
}

_client = None
_call_count = 0


class ModelUnavailable(RuntimeError):
    """Bedrock could not be reached, is not authorized, has no access to the
    configured model, or is still throttled after the retry budget.

    This is the one exception type ``converse`` ever raises. Callers (in
    particular ``backend/agent.py``) are expected to catch it and fall back to
    a deterministic path -- it must never be allowed to crash the caller,
    because "no AWS account in this room" is a real, expected demo condition,
    not an error state.
    """


def _get_client():
    global _client
    if _client is None:
        try:
            import boto3
            from botocore.config import Config as BotocoreConfig
        except ImportError as exc:  # boto3 is a declared dependency, but be honest anyway
            raise ModelUnavailable(f"boto3 is not installed: {exc}") from exc
        _client = boto3.client(
            "bedrock-runtime",
            region_name=BEDROCK_REGION,
            # Bound EACH attempt, not just the loop around them. botocore retries
            # internally by default, so a single converse() can spend many seconds
            # before raising -- which is how a supposedly 8s deadline took 18s. This
            # module does its own bounded retry with jitter; botocore doing a second,
            # invisible layer of it underneath is the bug.
            config=BotocoreConfig(
                retries={"max_attempts": 1, "mode": "standard"},
                connect_timeout=3,
                read_timeout=5,
            ),
        )
    return _client


def call_count() -> int:
    """Number of Converse calls that actually completed successfully against
    Bedrock in this process. Retried-then-succeeded calls count once; calls
    that exhausted their retry budget and raised ``ModelUnavailable`` do not
    count, because no model reasoning happened.
    """
    return _call_count


def reset_call_count() -> None:
    """Zero the counter. Exists for tests and for a demo that wants a clean
    per-run number rather than a process-lifetime total."""
    global _call_count
    _call_count = 0


def converse(
    system: str,
    messages: list[dict],
    tools: list[dict] | None = None,
    max_tokens: int = 1024,
    temperature: float = 0.0,
) -> dict:
    """Call the configured chat model (``MODEL_ID``, Amazon Nova by default) on Bedrock
    via the Converse API and return the raw response dict.

    ``messages`` and ``tools`` are passed through verbatim in Converse's own
    shape (``messages`` as ``[{"role": ..., "content": [{"text": ...}]}]``;
    ``tools`` as a list of ``{"toolSpec": {...}}`` entries) -- this function
    does no translation, so callers read the Bedrock docs, not this docstring,
    for the wire format.

    Raises ``ModelUnavailable`` -- never a raw botocore/boto3 exception -- if
    the account has no usable credentials, is denied access to ``MODEL_ID``,
    the endpoint cannot be reached, or throttling does not clear within
    ``MAX_ATTEMPTS`` retries **or** ``DEADLINE_S`` seconds, whichever comes
    first. The time bound is the load-bearing one: attempts alone are not a
    latency budget, and this call sits inside a 30s Lambda timeout.
    """
    try:
        client = _get_client()
    except ModelUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 - client construction should never crash the caller
        raise ModelUnavailable(f"could not construct a bedrock-runtime client: {exc}") from exc

    kwargs: dict[str, Any] = {
        "modelId": MODEL_ID,
        "messages": messages,
        "inferenceConfig": {"maxTokens": max_tokens, "temperature": temperature},
    }
    if system:
        kwargs["system"] = [{"text": system}]
    if tools:
        kwargs["toolConfig"] = {"tools": tools}

    # Refuse instantly if the model has been failing: rediscovering that costs the
    # caller the full DEADLINE_S, and the answer does not change second to second.
    try:
        breaker.chat.check()
    except breaker.CircuitOpen as exc:
        raise ModelUnavailable(str(exc)) from exc

    try:
        return _converse_attempts(client, kwargs)
    except ModelUnavailable:
        breaker.chat.record_failure()
        raise


def _converse_attempts(client, kwargs: dict[str, Any]) -> dict:
    """The bounded retry loop itself. Split out so the breaker wraps every exit path."""
    global _call_count

    from botocore.exceptions import (
        ClientError,
        EndpointConnectionError,
        NoCredentialsError,
    )

    last_exc: Exception | None = None
    started = time.monotonic()
    for attempt in range(1, MAX_ATTEMPTS + 1):
        if time.monotonic() - started > DEADLINE_S:
            raise ModelUnavailable(
                f"Bedrock did not answer within {DEADLINE_S}s ({attempt - 1} attempts); "
                "falling back rather than holding the request open"
            ) from last_exc
        try:
            response = client.converse(**kwargs)
            _call_count += 1
            breaker.chat.record_success()
            return response
        except NoCredentialsError as exc:
            raise ModelUnavailable(f"no AWS credentials available: {exc}") from exc
        except EndpointConnectionError as exc:
            raise ModelUnavailable(f"could not reach the Bedrock endpoint: {exc}") from exc
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "?")
            last_exc = exc
            if code in _THROTTLING_CODES and attempt < MAX_ATTEMPTS:
                # Full jitter, same shape as backend/db.py's retry -- correlated
                # retries across concurrent callers are what turn one throttle
                # response into a pile of them.
                backoff = min(MAX_BACKOFF_S, BASE_BACKOFF_S * (2 ** (attempt - 1)))
                remaining = DEADLINE_S - (time.monotonic() - started)
                if remaining <= 0:
                    raise ModelUnavailable(
                        f"Bedrock still throttling at the {DEADLINE_S}s deadline ({code})"
                    ) from exc
                time.sleep(min(random.uniform(0, backoff), remaining))
                continue
            if code in _THROTTLING_CODES:
                raise ModelUnavailable(
                    f"Bedrock still throttling after {MAX_ATTEMPTS} attempts ({code})"
                ) from exc
            # AccessDeniedException (no model access granted), ValidationException
            # (bad/unavailable model id), ResourceNotFoundException, etc: none of
            # these clear on retry, so fail fast rather than burn the budget.
            raise ModelUnavailable(f"Bedrock Converse call failed ({code}): {exc}") from exc
        except Exception as exc:  # noqa: BLE001 - any other transport/config failure
            raise ModelUnavailable(f"Bedrock Converse call failed: {type(exc).__name__}: {exc}") from exc

    raise ModelUnavailable(f"Bedrock Converse call failed after {MAX_ATTEMPTS} attempts") from last_exc


__all__ = [
    "DEFAULT_MODEL_ID",
    "MODEL_ID",
    "ModelUnavailable",
    "call_count",
    "converse",
    "reset_call_count",
]
