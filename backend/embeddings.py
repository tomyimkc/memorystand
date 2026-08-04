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
import time
from typing import Sequence

EMBED_DIMS = 512
MODEL_ID = os.environ.get("MEMORYSTAND_EMBED_MODEL", "amazon.titan-embed-text-v2:0")
REGION = os.environ.get("AWS_REGION", "us-west-2")
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

        _client = boto3.client("bedrock-runtime", region_name=REGION)
    return _client


def _stub_embedding(text: str) -> list[float]:
    """Deterministic unit vector derived from the text digest."""
    seed = int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big")
    rng = random.Random(seed)
    vec = [rng.gauss(0.0, 1.0) for _ in range(EMBED_DIMS)]
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


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

    body = json.dumps({"inputText": text, "dimensions": EMBED_DIMS})
    for attempt in range(1, max_retries + 1):
        try:
            resp = client.invoke_model(modelId=MODEL_ID, body=body)
            vec = json.loads(resp["body"].read())["embedding"]
            _real_used = True
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
        return f"deterministic local stub ({EMBED_DIMS}d, no semantic meaning)"
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
