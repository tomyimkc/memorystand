# SPDX-License-Identifier: Apache-2.0
"""Embeddings via Amazon Bedrock (Titan Text Embeddings V2), with a deterministic stub.

The stub exists for one honest reason: benchmarks and tests must be reproducible and
runnable without an AWS account, and a reviewer cloning this repo should be able to see
the memory layer work before they have credentials. It is NOT a silent fallback -- it
announces itself, and ``used_stub()`` reports whether any real embedding call was made,
so no result can quietly claim to be a Bedrock measurement when it is not.

The stub is seeded from a SHA-256 of the text, so identical text always yields an
identical vector, and semantically unrelated text yields near-orthogonal vectors. That is
enough for correctness and latency testing. It is emphatically NOT enough for relevance
testing -- stub vectors carry no semantics, so any claim about retrieval *quality* must
be made against real Titan embeddings.
"""

from __future__ import annotations

import hashlib
import math
import os
import random
import re
import time

from . import breaker
from typing import Sequence

EMBED_DIMS = 512
MODEL_ID = os.environ.get("MEMORYSTAND_EMBED_MODEL", "amazon.titan-embed-text-v2:0")
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

STUB_ENV = "MEMORYSTAND_EMBED_STUB"

_client = None
_stub_used = False
_real_used = False


def _force_stub() -> bool:
    return os.environ.get(STUB_ENV, "").lower() in {"1", "true", "yes", "on"}


def _get_client():
    global _client
    if _client is None:
        import boto3  # imported lazily so the stub path needs no boto3

        _client = boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)
    return _client


_TOKEN_RE = re.compile(r"[a-z0-9_]+")


def _stub_embedding(text: str) -> list[float]:
    """Deterministic LEXICAL embedding by feature hashing. Not semantic -- say so, always.

    The previous stub hashed the WHOLE string into one seed and emitted a random gaussian
    vector. That is fine as a placeholder and disastrous as a retriever: two nearly identical
    sentences got entirely unrelated vectors, so cosine similarity was pure noise. Recall still
    returned five rows with plausible-looking distances, which is worse than returning nothing
    -- it looked like ranking while being arbitrary. On this deployment, where Bedrock quota is
    0 and the stub is always in use, that meant the memory layer could not actually retrieve
    the relevant memory even when the query quoted it almost verbatim.

    Feature hashing fixes the part that can be fixed without a model: each token is hashed into
    one of EMBED_DIMS buckets and accumulated with sublinear term frequency, then the vector is
    L2-normalised so cosine similarity behaves. Texts that share words now score close
    together, which is genuine lexical retrieval.

    What it still is NOT: semantic. "restart the service" and "bounce the process" share no
    tokens and will not match. That limitation is real and is reported in provenance() rather
    than hidden, because a lexical retriever presented as a semantic one is exactly the kind of
    unearned claim this project exists to argue against. Titan embeddings replace this
    automatically the moment quota exists.
    """
    counts: dict[int, float] = {}
    for token in _TOKEN_RE.findall(text.lower()):
        # Signed hashing: the sign bit reduces collision bias, so two unrelated tokens landing
        # in the same bucket are as likely to cancel as to reinforce.
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        h = int.from_bytes(digest, "big")
        bucket = h % EMBED_DIMS
        sign = 1.0 if (h >> 63) & 1 else -1.0
        counts[bucket] = counts.get(bucket, 0.0) + sign

    vec = [0.0] * EMBED_DIMS
    for bucket, raw in counts.items():
        # Sublinear TF: a word repeated ten times should not dominate a word used once.
        vec[bucket] = math.copysign(1.0 + math.log(abs(raw)), raw) if raw else 0.0

    norm = math.sqrt(sum(v * v for v in vec))
    if not norm:
        # Empty or token-free text. Fall back to a stable non-zero vector so the value is a
        # legal VECTOR(512) and cosine distance stays defined.
        seed = int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big")
        rng = random.Random(seed)
        vec = [rng.gauss(0.0, 1.0) for _ in range(EMBED_DIMS)]
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


# Hard ceiling on how long embedding may take before we stop waiting and degrade.
# Sized against the Lambda timeout (30s) with room for the rest of the request: a
# dependency that is throttled must not be able to consume the whole budget. Override
# with MEMORYSTAND_EMBED_DEADLINE_S.
EMBED_DEADLINE_S = float(os.environ.get("MEMORYSTAND_EMBED_DEADLINE_S", "6"))

# Same principle as bedrock_client's probe budget: an embedding provider that has never once
# answered in this container does not get the full deadline to fail in. On a cold Lambda the
# breaker starts closed, so the first request paid 6s here AND the chat deadline before
# reaching anything that works. Discovering a dependency is down should be cheap; only a
# dependency that has demonstrated it works has earned the full budget.
EMBED_PROBE_DEADLINE_S = float(os.environ.get("MEMORYSTAND_EMBED_PROBE_DEADLINE_S", "2"))


def embed(text: str, *, max_retries: int = 5) -> list[float]:
    """Return a ``EMBED_DIMS``-dimensional embedding for ``text``.

    Uses Bedrock when credentials are available, otherwise the deterministic stub.
    Retries throttling responses with exponential backoff -- Titan has a per-account
    requests-per-minute quota and a bulk seed will hit it without this.
    """
    global _stub_used, _real_used

    if _force_stub():
        _stub_used = True
        return _stub_embedding(text)

    import json

    try:
        client = _get_client()
    except Exception:
        _stub_used = True
        return _stub_embedding(text)

    # Same reasoning as bedrock_client: once Bedrock has failed repeatedly, paying
    # EMBED_DEADLINE_S again on every request to learn it a third time is pure latency.
    # Degrading straight to the stub is what would have happened anyway, just sooner.
    try:
        breaker.embedding.check()
    except breaker.CircuitOpen:
        _stub_used = True
        return _stub_embedding(text)

    body = json.dumps({"inputText": text, "dimensions": EMBED_DIMS})
    deadline = EMBED_DEADLINE_S if _real_used else min(EMBED_DEADLINE_S, EMBED_PROBE_DEADLINE_S)
    started = time.monotonic()
    for attempt in range(1, max_retries + 1):
        # Retrying a throttled model is only worth it while there is time left. Without
        # this the backoff (0.5s, 1s, 2s, 4s, 8s + jitter) can outlast the whole Lambda
        # timeout, and the caller sees a 30s hang instead of a fast, honest degradation.
        # Observed exactly that in production: /recall and /decide returned
        # "Internal Server Error" via `Status: timeout` while Bedrock quota was zero.
        if time.monotonic() - started > deadline:
            print(
                f"[embeddings] giving up on Bedrock after {deadline}s "
                f"({attempt - 1} attempts); using the deterministic stub. "
                f"Latency stays valid; relevance does NOT.",
                flush=True,
            )
            _stub_used = True
            breaker.embedding.record_failure()
            break
        try:
            resp = client.invoke_model(modelId=MODEL_ID, body=body)
            vec = json.loads(resp["body"].read())["embedding"]
            _real_used = True
            breaker.embedding.record_success()
            return vec
        except Exception as exc:  # noqa: BLE001
            name = type(exc).__name__
            throttled = "Throttl" in name or "TooManyRequests" in name
            if throttled and attempt < max_retries:
                time.sleep(min(8.0, 0.5 * (2 ** (attempt - 1))) * (0.5 + random.random()))
                continue
            if attempt == 1:
                # Credentials/access problems should not silently degrade a benchmark.
                print(
                    f"[embeddings] Bedrock unavailable ({name}); using the deterministic stub. "
                    f"Latency numbers remain valid; relevance numbers do NOT.",
                    flush=True,
                )
            _stub_used = True
            return _stub_embedding(text)

    _stub_used = True
    breaker.embedding.record_failure()
    return _stub_embedding(text)


def embed_batch(texts: Sequence[str]) -> list[list[float]]:
    return [embed(t) for t in texts]


def to_pgvector(vec: Sequence[float]) -> str:
    """Format a vector as a CockroachDB VECTOR literal."""
    return "[" + ",".join(f"{v:.6f}" for v in vec) + "]"


def used_stub() -> bool:
    return _stub_used


def used_real_model() -> bool:
    return _real_used


def provenance() -> str:
    """One line describing where the embeddings in this process actually came from."""
    if _real_used and _stub_used:
        return f"MIXED: some Bedrock {MODEL_ID}, some deterministic stub"
    if _real_used:
        return f"Amazon Bedrock {MODEL_ID} ({EMBED_DIMS}d)"
    if _stub_used:
        return (
            f"deterministic local stub ({EMBED_DIMS}d, LEXICAL: token overlap only -- "
            "matches wording, not meaning, so synonyms will not match)"
        )
    return "no embeddings computed"


__all__ = [
    "EMBED_DIMS",
    "MODEL_ID",
    "embed",
    "embed_batch",
    "provenance",
    "to_pgvector",
    "used_real_model",
    "used_stub",
]
