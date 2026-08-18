#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Capture the public wrong-service refusal and its CockroachDB replay.

The presenter receipt must be built from the exact deployed API response it
describes. This script calls the public guided tenant, verifies the target,
fallback, approval hold, zero-model-call count, and wrong-service exclusion,
then immediately captures ``/timemachine`` while CockroachDB's GC window still
contains the decision timestamp.

The public tenant-scoped credential is read from ``/health``. It is never
written into the receipt.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts" / "video" / "capture" / "guided-refusal.json"
API_BASE = os.environ.get(
    "MEMORYSTAND_API_BASE",
    "https://ojao6oaxlk26mqfjwpuy7g4dy40tglyi.lambda-url.us-west-2.on.aws",
).rstrip("/")
AGENT_ID = "1a2b3c4d-5e6f-4708-9a0b-1c2d3e4f5061"
TARGET = "payments-service"
QUERY = "payments-service p99 latency above 2s, error rate climbing"


def call(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
    secret: str | None = None,
) -> dict[str, Any]:
    url = f"{API_BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    raw_body = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Accept": "application/json"}
    if raw_body is not None:
        headers["Content-Type"] = "application/json"
    if secret:
        headers["x-memorystand-secret"] = secret
    request = urllib.request.Request(url, data=raw_body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            status = response.status
            raw = response.read()
    except urllib.error.HTTPError as exc:
        status = exc.code
        raw = exc.read()
    try:
        payload: Any = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = None
    return {
        "method": method,
        "path": path,
        "status": status,
        "ok": 200 <= status < 300,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
        "requestBody": body,
        "payload": payload,
    }


def fail(message: str) -> None:
    raise SystemExit(f"refusing capture: {message}")


def main() -> int:
    health = call("GET", "/health")
    if not health["ok"] or not isinstance(health["payload"], dict):
        fail("/health did not return JSON")
    if health["payload"].get("database") != "reachable":
        fail("CockroachDB is not reachable")
    demo = health["payload"].get("demo") or {}
    secret = str(demo.get("credential") or "")
    tenant_id = str(demo.get("tenant_id") or "")
    if not secret or not tenant_id:
        fail("/health did not publish the tenant-scoped demo credential")

    recall = call(
        "GET",
        "/recall",
        params={"tenant_id": tenant_id, "q": QUERY, "k": 5},
    )
    if not recall["ok"] or not isinstance(recall["payload"], dict):
        fail("/recall did not return the guided rows")

    decision = call(
        "POST",
        "/decide",
        secret=secret,
        body={
            "tenant_id": tenant_id,
            "agent_id": AGENT_ID,
            "query": QUERY,
            "target_entity": TARGET,
            "action": None,
            "rationale": "Guided demo: verify subject-bound action authority.",
            "k": 5,
            "task_id": None,
            "produced_memory_ids": [],
            "requires_approval": False,
        },
    )
    if not decision["ok"] or not isinstance(decision["payload"], dict):
        fail("/decide did not return a decision")
    payload = decision["payload"]
    if payload.get("target_entity") != TARGET:
        fail("the decision did not persist target_entity=payments-service")
    if payload.get("reasoning_source") != "fallback_heuristic":
        fail("the decision did not disclose fallback_heuristic")
    if payload.get("action") != "scale_up":
        fail("the disclosed fixed fallback did not select scale_up")
    if payload.get("status") != "held_for_approval":
        fail("the risky action was not held for approval")
    if payload.get("model_calls") != 0:
        fail("the guided decision did not report zero model calls")
    cited = set(payload.get("cited_memory_ids") or [])
    eligible = payload.get("eligible_memory_ids")
    if not isinstance(eligible, list) or eligible:
        fail("the wrong-service refusal must have no action-eligible memories")
    if cited:
        fail("the wrong-service refusal must cite no memory")
    exclusions = payload.get("excluded_memories") or []
    wrong = [
        row for row in exclusions
        if row.get("entity") == "checkout-api"
        and row.get("trust_tier") == "verified"
        and row.get("reason") == "entity_mismatch"
    ]
    if not wrong:
        fail("the verified checkout-api row was not excluded as entity_mismatch")
    wrong_id = str(wrong[0].get("memory_id") or "")
    if not wrong_id:
        fail("the verified checkout-api exclusion omitted memory_id")
    if wrong_id in cited:
        fail("the wrong-service row was cited")

    decision_id = str(payload.get("decision_id") or "")
    if not decision_id:
        fail("the decision response omitted decision_id")
    timemachine = call(
        "GET",
        "/timemachine",
        params={"tenant_id": tenant_id, "decision_id": decision_id},
    )
    if not timemachine["ok"] or not isinstance(timemachine["payload"], dict):
        fail("/timemachine did not return the CockroachDB receipt")
    replay = timemachine["payload"]
    replay_decision = replay.get("decision") or {}
    if replay_decision.get("target_entity") != TARGET:
        fail("time-travel receipt did not preserve the target")
    eligible_as_of = replay.get("eligible_memory_ids_as_of")
    if not isinstance(eligible_as_of, list):
        fail("time-travel receipt omitted eligible_memory_ids_as_of")
    if eligible_as_of:
        fail("time-travel receipt unexpectedly reconstructed an eligible memory")
    excluded_as_of = replay.get("excluded_memories_as_of")
    if not isinstance(excluded_as_of, list):
        fail("time-travel receipt omitted excluded_memories_as_of")
    if wrong_id in {str(value) for value in eligible_as_of}:
        fail("time-travel receipt made the wrong-service row eligible")
    if not any(
        str(row.get("memory_id") or "") == wrong_id
        and row.get("reason") == "entity_mismatch"
        for row in excluded_as_of
    ):
        fail("time-travel receipt did not preserve the wrong-service entity exclusion")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    receipt = {
        "capturedAt": datetime.now(UTC).isoformat(),
        "apiBase": API_BASE,
        "health": health,
        "recall": recall,
        "decision": decision,
        "timemachine": timemachine,
    }
    # Remove the only secret before serializing. Request receipts never include
    # headers, but the health response contains the public tenant credential.
    public_demo = receipt["health"]["payload"].get("demo") or {}
    public_demo["credential"] = "[redacted public tenant-scoped credential]"
    OUT.write_text(json.dumps(receipt, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")
    print(
        f"decision={decision_id} excluded={len(exclusions)} "
        f"eligible={len(payload.get('eligible_memory_ids') or [])} cited={len(cited)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
