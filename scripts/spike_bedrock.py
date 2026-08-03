#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Day-1 Amazon Bedrock spikes: model access and embedding throughput quota.

Answers two go/no-go questions before any application code depends on them:
  3. Is Claude (Converse) + Titan Embeddings V2 access actually granted, and how fast?
  7. What is the Titan embedding throughput quota? Day 11 seeds thousands of
     embeddings; a default per-account throttle would silently wreck that.

    export AWS_REGION=us-east-1
    python scripts/spike_bedrock.py
"""

from __future__ import annotations

import json
import os
import sys
import time

try:
    import boto3
    from botocore.exceptions import ClientError
except ImportError:  # pragma: no cover - operator-facing message
    sys.exit("boto3 not installed. Run: pip install -r requirements.txt")

REGION = os.environ.get("AWS_REGION", "us-east-1")
EMBED_MODEL = os.environ.get("EMBED_MODEL_ID", "amazon.titan-embed-text-v2:0")
CHAT_MODEL = os.environ.get("CHAT_MODEL_ID", "anthropic.claude-3-5-haiku-20241022-v1:0")
EMBED_DIMS = 512

results: list[dict] = []


def record(num: int, name: str, ok: bool, finding: str, fallback: str = "") -> None:
    results.append({"spike": num, "name": name, "ok": ok, "finding": finding, "fallback": fallback})
    print(f"[{'PASS' if ok else 'FAIL'}] Spike {num}: {name}\n       {finding}")
    if not ok and fallback:
        print(f"       -> fallback: {fallback}")
    print()


def spike_identity() -> bool:
    try:
        ident = boto3.client("sts", region_name=REGION).get_caller_identity()
        record(0, "AWS credentials", True, f"account {ident['Account']} as {ident['Arn']}")
        return True
    except Exception as exc:  # noqa: BLE001
        record(
            0,
            "AWS credentials",
            False,
            f"{type(exc).__name__}: {exc}",
            "Run `aws configure` (or set AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY).",
        )
        return False


def spike_list_models() -> None:
    """Which of the two models we need are actually visible in this region?"""
    try:
        client = boto3.client("bedrock", region_name=REGION)
        summaries = client.list_foundation_models().get("modelSummaries", [])
        ids = {m["modelId"] for m in summaries}
        want = {"embed": EMBED_MODEL, "chat": CHAT_MODEL}
        seen = {k: (v in ids) for k, v in want.items()}
        anthropic = sorted(i for i in ids if i.startswith("anthropic."))[:6]
        record(
            31,
            "foundation models visible",
            all(seen.values()),
            f"{len(ids)} models in {REGION}; embed={seen['embed']} chat={seen['chat']}. "
            f"Anthropic ids present (sample): {anthropic}",
            "Pick a different model id from the list above, or switch region to us-east-1.",
        )
    except Exception as exc:  # noqa: BLE001
        record(31, "foundation models visible", False, f"{type(exc).__name__}: {exc}",
               "Check the bedrock:ListFoundationModels IAM permission.")


def spike_3_embeddings() -> bool:
    """Titan Text Embeddings V2 at 512 dims -- the schema's VECTOR(512) depends on this."""
    try:
        rt = boto3.client("bedrock-runtime", region_name=REGION)
        t0 = time.time()
        resp = rt.invoke_model(
            modelId=EMBED_MODEL,
            body=json.dumps(
                {"inputText": "payments-service reads from orders_v2", "dimensions": EMBED_DIMS}
            ),
        )
        dt = time.time() - t0
        vec = json.loads(resp["body"].read())["embedding"]
        ok = len(vec) == EMBED_DIMS
        record(
            3,
            "Titan embeddings access",
            ok,
            f"returned {len(vec)} dims in {dt * 1000:.0f} ms (schema expects VECTOR({EMBED_DIMS}))",
            "" if ok else f"Change VECTOR({EMBED_DIMS}) in db/schema.sql to VECTOR({len(vec)}).",
        )
        return ok
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "?")
        record(
            3,
            "Titan embeddings access",
            False,
            f"{code}: {exc}",
            "If AccessDeniedException: request model access in the Bedrock console. "
            "Meanwhile use the deterministic hash-embedding stub so pipeline work continues. "
            "Never ship the stub.",
        )
        return False


def spike_3b_converse() -> None:
    """Claude via the Converse API -- reasoning and contradiction adjudication."""
    try:
        rt = boto3.client("bedrock-runtime", region_name=REGION)
        t0 = time.time()
        resp = rt.converse(
            modelId=CHAT_MODEL,
            messages=[{"role": "user", "content": [{"text": "Reply with exactly: OK"}]}],
            inferenceConfig={"maxTokens": 16, "temperature": 0.0},
        )
        dt = time.time() - t0
        text = resp["output"]["message"]["content"][0]["text"].strip()
        record(32, "Claude Converse access", True, f"replied {text!r} in {dt * 1000:.0f} ms")
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "?")
        record(
            32,
            "Claude Converse access",
            False,
            f"{code}: {exc}",
            "FIX-6: adjudicate() must default to the DETERMINISTIC rule-only contradiction "
            "check (same entity + same attribute_key + different attribute_value). Bedrock "
            "reasoning is an enrichment layer on top, never the only path.",
        )


def spike_7_quota() -> None:
    """Titan throughput quota -- Day 11 seeds thousands of embeddings in one run."""
    try:
        sq = boto3.client("service-quotas", region_name=REGION)
        found = []
        paginator = sq.get_paginator("list_service_quotas")
        for page in paginator.paginate(ServiceCode="bedrock"):
            for q in page.get("Quotas", []):
                nm = q.get("QuotaName", "")
                if "Titan" in nm and ("embed" in nm.lower() or "Embed" in nm):
                    found.append(f"{nm} = {q.get('Value')}")
        record(
            7,
            "Titan throughput quota",
            bool(found),
            "; ".join(found[:6]) if found else "no Titan embedding quotas surfaced via API",
            "Check Service Quotas in the console. Build exponential backoff into "
            "scripts/loadtest.py regardless, and cut the load test to 2-5k rows if tight.",
        )
    except Exception as exc:  # noqa: BLE001
        record(
            7,
            "Titan throughput quota",
            False,
            f"{type(exc).__name__}: {exc}",
            "Read quotas in the console instead; backoff in loadtest.py is required either way.",
        )


def main() -> int:
    print("=" * 70)
    print(f"Standing -- Day-1 Bedrock spikes (region {REGION})")
    print("=" * 70 + "\n")

    if not spike_identity():
        return 2
    spike_list_models()
    spike_3_embeddings()
    spike_3b_converse()
    spike_7_quota()

    with open("spike_bedrock_results.json", "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2)

    passed = sum(1 for r in results if r["ok"])
    print("=" * 70)
    print(f"{passed}/{len(results)} spikes passed. Written to spike_bedrock_results.json")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
