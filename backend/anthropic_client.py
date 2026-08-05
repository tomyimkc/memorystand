# SPDX-License-Identifier: Apache-2.0
"""A second reasoning provider, shaped exactly like the first.

Amazon Bedrock is this project's preferred model host and stays first in the chain. But every
Bedrock inference quota on this AWS account is 0 -- not adjustable through Service Quotas for
the quotas that matter -- so the deployed agent has never once reasoned with a model. Waiting
on a support case is not a plan, and "the agent does not reason" is the weakest sentence in the
submission.

The hackathon requires *at least one* AWS service and lists Bedrock as one option among many.
This deployment uses five (Lambda, CloudWatch, SSM, Amplify, EventBridge), so reaching a model
by another route costs nothing in eligibility and buys a working agent.

DESIGN: this module is an ADAPTER, not a second implementation. It exposes the same
``converse(system, messages, tools, ...)`` signature as ``bedrock_client`` and returns a
response in Bedrock's own Converse shape -- ``{"output": {"message": {"content": [...]}}}`` with
``toolUse`` blocks. ``backend/agent.py`` therefore needs no provider-specific branch and there
is exactly one place that parses a model response. A second parser would be a second thing to
get wrong.

It raises ``bedrock_client.ModelUnavailable`` rather than a type of its own, for the same
reason: the caller already handles that one exception by falling back to the deterministic
rule, and giving the fallback a second trigger to learn about would make it easy to miss one.

THE ZERO-MODEL-CALLS CLAIM IS UNAFFECTED, and is actively defended. ``trust.py`` forbids the
name ``anthropic`` in its own namespace and in the namespace of every backend module it
imports, so wiring a second model provider cannot quietly become reachable from the promotion
path. That guard was written before this module existed and catches it for free -- which is
the point of writing guards structurally rather than as documentation.

CREDENTIALS: read from ``ANTHROPIC_API_KEY``, or from the SSM SecureString named by
``MEMORYSTAND_ANTHROPIC_SSM_PARAM`` (default ``/memorystand/anthropic_api_key``). Never
committed, never logged, never returned in a response -- the same handling as the shared
secret and the database DSN.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any

from . import bedrock_client

# Base URL is configurable because the Anthropic Messages format is spoken by more than
# Anthropic: OpenAI-compatible routers and self-hosted gateways implement it too, and pinning
# the hostname would force a code change to move between them. Verified against both
# api.anthropic.com and a router endpoint, including tool_use, which is the part that matters
# -- the agent's contract is one action from a closed allow-list, not free prose.
API_URL = os.environ.get(
    "MEMORYSTAND_ANTHROPIC_BASE_URL", "https://api.anthropic.com"
).rstrip("/") + "/v1/messages"
API_VERSION = "2023-06-01"

# Haiku by default rather than a larger model, on purpose. This call sits on an on-call
# decision path inside a 30s Lambda budget that it shares with an embedding call and the
# database work, and the task is choosing one action from a five-item enum given a handful of
# recalled memories. That is a small-model task, and latency is a feature here.
MODEL_ID = os.environ.get("MEMORYSTAND_ANTHROPIC_MODEL", "claude-haiku-4-5")
SSM_PARAM = os.environ.get("MEMORYSTAND_ANTHROPIC_SSM_PARAM", "/memorystand/anthropic_api_key")

# Same latency discipline as the Bedrock client: this sits inside a 30s Lambda budget and
# shares it with an embedding call and the database work.
DEADLINE_S = float(os.environ.get("MEMORYSTAND_ANTHROPIC_DEADLINE_S", "10"))

_api_key: str | None = None
_key_loaded = False
_call_count = 0


def _load_key() -> str | None:
    """Env first, then SSM. Cached per container, including the negative result.

    Caching the miss matters: without it, a deployment with no key configured would pay an SSM
    round trip on every single request to re-learn something that will not change within the
    life of the container.
    """
    global _api_key, _key_loaded
    if _key_loaded:
        return _api_key

    _key_loaded = True
    env = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if env:
        _api_key = env
        return _api_key

    try:
        import boto3

        ssm = boto3.client("ssm", region_name=os.environ.get("AWS_REGION", "us-west-2"))
        resp = ssm.get_parameter(Name=SSM_PARAM, WithDecryption=True)
        _api_key = (resp["Parameter"]["Value"] or "").strip() or None
    except Exception:  # noqa: BLE001 - no key configured is a normal state, not an error
        _api_key = None
    return _api_key


def available() -> bool:
    """Is this provider usable at all? Lets the caller skip it without paying a timeout."""
    return bool(_load_key())


def call_count() -> int:
    return _call_count


def reset_call_count() -> None:
    global _call_count
    _call_count = 0


def _to_anthropic_tools(tools: list[dict] | None) -> list[dict]:
    """Translate Bedrock toolSpec entries into Anthropic tool definitions.

    Bedrock:   {"toolSpec": {"name", "description", "inputSchema": {"json": {...}}}}
    Anthropic: {"name", "description", "input_schema": {...}}
    """
    out = []
    for tool in tools or []:
        spec = tool.get("toolSpec") or {}
        schema = (spec.get("inputSchema") or {}).get("json") or {}
        out.append(
            {
                "name": spec.get("name"),
                "description": spec.get("description", ""),
                "input_schema": schema,
            }
        )
    return out


def _to_bedrock_response(payload: dict[str, Any]) -> dict[str, Any]:
    """Reshape an Anthropic Messages response into Bedrock's Converse shape.

    This is the whole point of the module: callers keep one parsing path. Anthropic returns
    ``content: [{"type": "tool_use", "name", "input"}]``; Bedrock returns
    ``output.message.content: [{"toolUse": {"name", "input"}}]``.
    """
    blocks = []
    for block in payload.get("content") or []:
        if block.get("type") == "tool_use":
            blocks.append({"toolUse": {"name": block.get("name"), "input": block.get("input") or {}}})
        elif block.get("type") == "text":
            blocks.append({"text": block.get("text", "")})
    return {"output": {"message": {"role": "assistant", "content": blocks}}}


def converse(
    system: str,
    messages: list[dict],
    tools: list[dict] | None = None,
    max_tokens: int = 1024,
    temperature: float = 0.0,
) -> dict:
    """Same contract as ``bedrock_client.converse``. Raises ``ModelUnavailable`` on any failure."""
    global _call_count

    key = _load_key()
    if not key:
        raise bedrock_client.ModelUnavailable(
            "no Anthropic API key configured (set ANTHROPIC_API_KEY or the SSM SecureString "
            f"{SSM_PARAM})"
        )

    # Bedrock's message shape is {"role", "content": [{"text": ...}]}; Anthropic accepts the
    # same structure with "type": "text" on each block.
    converted = [
        {
            "role": m.get("role", "user"),
            "content": [
                {"type": "text", "text": c.get("text", "")} for c in (m.get("content") or [])
            ],
        }
        for m in messages
    ]

    body: dict[str, Any] = {
        "model": MODEL_ID,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": converted,
    }
    if system:
        body["system"] = system
    if tools:
        body["tools"] = _to_anthropic_tools(tools)
        # Force the tool rather than hoping for it. The agent's contract is one action from a
        # closed allow-list; free prose would just be parsed into a fallback anyway.
        body["tool_choice"] = {"type": "tool", "name": (tools[0].get("toolSpec") or {}).get("name")}

    request = urllib.request.Request(
        API_URL,
        data=json.dumps(body).encode(),
        headers={
            "content-type": "application/json",
            "anthropic-version": API_VERSION,
            "x-api-key": key,
        },
    )

    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=DEADLINE_S) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()[:200]
        raise bedrock_client.ModelUnavailable(
            f"Anthropic API returned HTTP {exc.code}: {detail}"
        ) from exc
    except Exception as exc:  # noqa: BLE001 - never let a provider crash the agent loop
        raise bedrock_client.ModelUnavailable(
            f"Anthropic API unreachable after {time.monotonic() - started:.1f}s: "
            f"{type(exc).__name__}: {exc}"
        ) from exc

    _call_count += 1
    return _to_bedrock_response(payload)


__all__ = ["MODEL_ID", "available", "call_count", "converse", "reset_call_count"]
