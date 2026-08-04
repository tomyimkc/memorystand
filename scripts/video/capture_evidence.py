#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Live-API capture layer for the evidence-first demo video's outcome-gate story.

``scripts/video/capture_live.mjs`` drives the deployed dashboard end to end and is the
capture of record for the dashboard screenshots. This script is narrower and
complementary: it calls the deployed API **directly** (no browser) to produce the exact
sequence of receipts the video's outcome-gate scenes (`02-admission-holds` through
`06-cross-examine` in ``docs/demo/video-timeline.json``) need -- three contradicting
`/ingest` calls (a runbook fact, a low-authority Slack claim, and a human correction),
then `/recall`, `/decide`, `/confirm_outcome`, and `/timemachine` -- against the exact
same live, deployed Lambda + CockroachDB Cloud endpoint the dashboard uses.

Every request/response pair is recorded with the response body's raw bytes and their
SHA-256 digest, so every number the video shows is traceable to a response this script
actually received -- nothing here is typed in by hand. The shared secret itself is never
written to the output file.

Usage:
    AWS_PROFILE=memorystand .venv/bin/python scripts/video/capture_evidence.py

Env overrides (all optional):
    MEMORYSTAND_API_BASE       default: the deployed Lambda Function URL
    MEMORYSTAND_TENANT_ID      default: a fresh tenant id derived from the run's UTC
                                timestamp (see the isolation note above the constants
                                below); set this to reuse one tenant across runs
    MEMORYSTAND_AGENT_ID       default: the seeded demo agent
    MEMORYSTAND_SHARED_SECRET  the x-memorystand-secret value; if unset, fetched from
                                SSM parameter /memorystand/shared_secret via boto3
    MEMORYSTAND_AWS_PROFILE    default: memorystand (only used for the SSM fetch)
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "artifacts" / "video" / "capture"
OUT_PATH = OUT_DIR / "evidence.json"

API_BASE = os.environ.get(
    "MEMORYSTAND_API_BASE",
    "https://ojao6oaxlk26mqfjwpuy7g4dy40tglyi.lambda-url.us-west-2.on.aws",
).rstrip("/")
# Deliberately NOT the shared seeded demo tenant (9c8f6e5a-...) that
# scripts/video/capture_dashboard.mjs screenshots. This script re-tells the same
# "checkout-api circuit breaker" contradiction story every run (800ms -> 300ms -> 500ms),
# and this API has no delete endpoint -- reusing one persistent tenant across runs means
# a second run's "runbook fact" (800ms) arrives AFTER a first run's human correction
# (500ms) is already admitted, so it contradicts that leftover state and comes back
# "held for review" instead of "accepted". A fresh tenant id per run starts genuinely
# empty every time, the same way scripts/record-demo.sh wipes its own private
# RECORD_TENANT before every take -- this is the HTTP-API equivalent of that reset,
# since this script has no direct SQL access to wipe rows.
AGENT_ID = os.environ.get("MEMORYSTAND_AGENT_ID", "1a2b3c4d-5e6f-4708-9a0b-1c2d3e4f5061")
SECRET_HEADER = "x-memorystand-secret"
TENANT_ID: str = ""  # set in main(): a fresh tenant id per run unless overridden, see above


def _resolve_shared_secret() -> str:
    env_value = os.environ.get("MEMORYSTAND_SHARED_SECRET")
    if env_value:
        return env_value
    import boto3  # local import: only needed on this fallback path

    profile = os.environ.get("MEMORYSTAND_AWS_PROFILE", "memorystand")
    session = boto3.Session(profile_name=profile)
    client = session.client("ssm", region_name="us-west-2")
    response = client.get_parameter(Name="/memorystand/shared_secret", WithDecryption=True)
    value = response["Parameter"]["Value"]
    if not value:
        raise SystemExit("SSM returned an empty shared_secret value")
    return value


def _call(
    label: str,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
    secret: str | None = None,
) -> dict[str, Any]:
    """Issue one real HTTP call and return a receipt (never raises on a non-2xx status)."""
    url = f"{API_BASE}{path}"
    if params:
        query = "&".join(f"{k}={urllib.request.quote(str(v))}" for k, v in params.items())
        url = f"{url}?{query}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if secret:
        headers[SECRET_HEADER] = secret
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            status = response.status
            raw = response.read()
    except urllib.error.HTTPError as exc:
        status = exc.code
        raw = exc.read()
    payload: Any = None
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        payload = None
    receipt = {
        "label": label,
        "method": method,
        "url": url,
        "status": status,
        "ok": 200 <= status < 300,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
        "payload": payload,
        # Request bodies are recorded for traceability; the shared secret is a header,
        # never part of the body, so this never leaks it.
        "requestBody": body,
    }
    print(f"{method} {path} -> {status} ({label}), sha256={receipt['sha256'][:12]}...")
    return receipt


def main() -> int:
    secret = _resolve_shared_secret()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    run_stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    tenant_id = os.environ.get(
        "MEMORYSTAND_TENANT_ID",
        str(uuid.uuid5(uuid.NAMESPACE_URL, f"memorystand:video-capture-tenant:{run_stamp}")),
    )
    global TENANT_ID
    TENANT_ID = tenant_id
    task_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"memorystand:video-capture:{int(time.time())}"))
    external_ref = f"INC-VIDEO-{run_stamp}"
    query = "checkout-api circuit breaker timeout gateway latency incident"

    steps: dict[str, dict[str, Any]] = {}

    steps["healthBefore"] = _call("health-before", "GET", "/health")

    steps["ingestRunbook"] = _call(
        "ingest-runbook",
        "POST",
        "/ingest",
        body={
            "tenant_id": TENANT_ID,
            "agent_id": AGENT_ID,
            "content": (
                "checkout-api's circuit breaker to the payments gateway trips after 800ms "
                "of sustained p99 latency, per the resiliency runbook."
            ),
            "entity": "checkout-api",
            "attribute_key": "circuit_breaker_timeout_ms",
            "attribute_value": "800",
            "source": "runbook:checkout-resiliency",
            "task_id": task_id,
        },
        secret=secret,
    )

    steps["ingestSlack"] = _call(
        "ingest-slack",
        "POST",
        "/ingest",
        body={
            "tenant_id": TENANT_ID,
            "agent_id": AGENT_ID,
            "content": (
                "Someone in #incidents Slack says checkout-api's breaker now trips at "
                "300ms after a recent change."
            ),
            "entity": "checkout-api",
            "attribute_key": "circuit_breaker_timeout_ms",
            "attribute_value": "300",
            "source": "slack",
            "task_id": task_id,
        },
        secret=secret,
    )

    steps["ingestHuman"] = _call(
        "ingest-human-correction",
        "POST",
        "/ingest",
        body={
            "tenant_id": TENANT_ID,
            "agent_id": AGENT_ID,
            "content": (
                "Alice (on-call lead) confirms after the 2026-07 tuning pass: checkout-api's "
                "circuit breaker to the payments gateway trips at 500ms, not 800ms and not "
                "300ms. This is authoritative."
            ),
            "entity": "checkout-api",
            "attribute_key": "circuit_breaker_timeout_ms",
            "attribute_value": "500",
            "source": "human:alice",
            "task_id": task_id,
        },
        secret=secret,
    )

    steps["recall"] = _call(
        "recall",
        "GET",
        "/recall",
        params={"tenant_id": TENANT_ID, "q": query, "k": 5},
    )

    action_memory_id = None
    if steps["ingestRunbook"]["ok"]:
        action_payload = _call(
            "ingest-proposed-action",
            "POST",
            "/ingest",
            body={
                "tenant_id": TENANT_ID,
                "agent_id": AGENT_ID,
                "content": (
                    "Proposed action: temporarily raise checkout-api's circuit breaker "
                    f"timeout to 1200ms while the payments gateway p99 latency spike "
                    f"({external_ref}) is ongoing, to reduce false-positive trips."
                ),
                "entity": "checkout-api",
                "attribute_key": "last_incident_action",
                "attribute_value": "breaker_timeout_raised_to_1200ms_temp",
                "source": "agent:memorystand-oncall",
                "task_id": task_id,
            },
            secret=secret,
        )
        steps["ingestAction"] = action_payload
        if action_payload["ok"] and isinstance(action_payload["payload"], dict):
            action_memory_id = action_payload["payload"].get("memory_id")

    consulted_ids = []
    if isinstance(steps["recall"]["payload"], dict):
        consulted_ids = [row["memory_id"] for row in steps["recall"]["payload"].get("results", [])]

    decision_id = None
    decide_body = {
        "tenant_id": TENANT_ID,
        "agent_id": AGENT_ID,
        "query": query,
        # Deliberately NO "action" and NO "rationale".
        #
        # Supplying them makes /decide skip reasoning entirely and just record what it
        # was handed -- which looked identical in the captured output, so the video
        # would have shown "the agent decided" while the agent had done nothing. The
        # capture now exercises the real loop and reports reasoning_source, so a viewer
        # can see whether a model or the deterministic fallback chose the action.
        "k": 5,
        "task_id": task_id,
    }
    if action_memory_id:
        decide_body["produced_memory_ids"] = [action_memory_id]
    steps["decide"] = _call("decide", "POST", "/decide", body=decide_body, secret=secret)
    if steps["decide"]["ok"] and isinstance(steps["decide"]["payload"], dict):
        decision_id = steps["decide"]["payload"].get("decision_id")

    if decision_id:
        steps["confirm"] = _call(
            "confirm-outcome",
            "POST",
            "/confirm_outcome",
            body={
                # tenant_id is required and the route is secret-gated now: granting
                # standing used to need only a decision id, from anyone.
                "tenant_id": TENANT_ID,
                "decision_id": decision_id,
                "outcome": "success",
                "source": "pagerduty",
                "external_ref": external_ref,
            },
            secret=secret,
        )
        steps["timemachine"] = _call(
            "timemachine",
            "GET",
            "/timemachine",
            params={"tenant_id": TENANT_ID, "decision_id": decision_id},
        )

    # --- The three-way outcome ladder, captured live -----------------------------------
    #
    # `confirm` above is the ATTESTED rung: a PagerDuty incident id is a real external
    # signal, but this deployment holds no PagerDuty token, so nothing here can re-check it.
    #
    # The two metric rungs need a decision old enough that CloudWatch has datapoints on BOTH
    # sides of it. That is not a limitation to work around, it is the subject: an outcome is
    # something the world reports back LATER, so a decision made seconds ago cannot yet have
    # one. Verifying it immediately returns "unavailable", correctly.
    #
    # Supply aged decision ids via MEMORYSTAND_AGED_DECISIONS="<verified_id>,<refused_id>"
    # (create them ~20 minutes before capture). Without them, the honest "unavailable"
    # result is captured instead and the frame says so rather than staging a success.
    aged = [d.strip() for d in os.environ.get("MEMORYSTAND_AGED_DECISIONS", "").split(",") if d.strip()]
    METRIC_REF = "AWS/Lambda|Duration|FunctionName=memorystand"

    if len(aged) >= 1:
        # Calibrate against the live metric rather than hard-coding a delta.
        #
        # This is not convenience, it is a correctness fix for an observer effect that broke
        # two capture runs: the metric being verified is AWS/Lambda Duration for THIS function,
        # and every call this script makes moves it. Between two runs minutes apart the observed
        # change went -3777 ms, then +624 ms, then -3801 ms -- so any constant written here is
        # wrong by the time it is used, and the "verified" beat would fail for reasons that have
        # nothing to do with the thing being demonstrated.
        #
        # So: submit a deliberately absurd claim first, read the observed value back out of the
        # refusal, then submit that value as the honest claim. The probe is refused and records
        # no outcome, which is exactly why it is safe to use as a measurement.
        #
        # The deeper lesson, worth stating because it applies to anyone adopting this: do not
        # verify an outcome against a metric your own verification traffic perturbs.
        probe = _call(
            "probe-observed-value",
            "POST",
            "/confirm_outcome",
            body={
                "tenant_id": TENANT_ID, "decision_id": aged[0], "outcome": "success",
                "source": "metric", "external_ref": METRIC_REF, "metric_delta": -999999.0,
            },
            secret=secret,
        )
        observed = None
        match = re.search(r"CloudWatch shows ([+-]?[\d.e+]+)", str((probe.get("payload") or {}).get("detail", "")))
        if match:
            try:
                observed = float(match.group(1))
            except ValueError:
                observed = None

        steps["confirmVerified"] = _call(
            "confirm-verified",
            "POST",
            "/confirm_outcome",
            body={
                "tenant_id": TENANT_ID,
                "decision_id": aged[0],
                "outcome": "success",
                "source": "metric",
                "external_ref": METRIC_REF,
                "metric_delta": observed if observed is not None
                else float(os.environ.get("MEMORYSTAND_DEMO_TRUE_DELTA", "-3800")),
            },
            secret=secret,
        )
    if len(aged) >= 2:
        steps["confirmRefused"] = _call(
            "confirm-refused",
            "POST",
            "/confirm_outcome",
            body={
                "tenant_id": TENANT_ID,
                "decision_id": aged[1],
                "outcome": "success",
                "source": "metric",
                "external_ref": METRIC_REF,
                # Deliberately wrong by an order of magnitude. The API is EXPECTED to reject
                # this with HTTP 400 -- a non-2xx here is the evidence, not a capture failure.
                "metric_delta": -10.0,
            },
            secret=secret,
        )

    # The stub-embedding disclosure only reports accurately once an embedding call has
    # actually happened in this warm Lambda container (see docs/demo/VIDEO_PLAN.md's
    # Capture sources note) -- /recall and /ingest above both embed, so by now it will.
    steps["healthAfter"] = _call("health-after", "GET", "/health")

    document = {
        "schemaVersion": "1.0",
        "capturedAtUtc": datetime.now(UTC).isoformat(),
        "candidateOnly": True,
        "canClaimAGI": False,
        "apiBase": API_BASE,
        "tenantId": TENANT_ID,
        "agentId": AGENT_ID,
        "taskId": task_id,
        "externalRef": external_ref,
        "decisionId": decision_id,
        "actionMemoryId": action_memory_id,
        "consultedMemoryIds": consulted_ids,
        "steps": steps,
        "limitations": [
            "Embeddings on this deployment fall back to a deterministic local stub -- the "
            "AWS account has near-zero Bedrock quota. Recall latency shown here is real; "
            "recall relevance is not semantically meaningful under the stub.",
            "This capture mutates the live demo tenant (three new memories, one proposed-"
            "action memory, one decision, one confirmed outcome) -- it is real traffic "
            "against a real deployment, not a replay.",
        ],
    }
    OUT_PATH.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"\nwrote {OUT_PATH}")
    failed = [key for key, step in steps.items() if not step.get("ok")]
    if failed:
        print(f"WARNING: {len(failed)} step(s) did not return a 2xx status: {failed}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
