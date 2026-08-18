# SPDX-License-Identifier: Apache-2.0
"""Decision context must be subject-bound and backed by a real trust receipt."""

from __future__ import annotations

import json
import uuid
import datetime as dt

import pytest

from backend import agent, authority, db, decisions, handler, memory


def _http(path: str, body: dict, secret: str = "operator-secret") -> dict:
    return {
        "requestContext": {"http": {"method": "POST", "path": path}, "requestId": "subject"},
        "rawPath": path,
        "headers": {
            "content-type": "application/json",
            "x-memorystand-secret": secret,
        },
        "body": json.dumps(body),
    }


def _row(memory_id: str, *, entity: str, tier: str, action: str, eligible: bool = True) -> dict:
    reason = (
        authority.ATTESTED_ADVISORY
        if tier == "attested" and eligible
        else authority.VERIFIED_RECEIPT
        if eligible
        else authority.VERIFIED_WITHOUT_CURRENT_RECEIPT
    )
    return {
        "memory_id": memory_id,
        "entity": entity,
        "attribute_key": "remediation",
        "attribute_value": action,
        "content": action,
        "trust_tier": tier,
        "action_eligible": eligible,
        "action_eligibility_reason": reason,
        "action_receipt_external_ref": (
            f"AWS/Lambda|Duration|FunctionName={entity}"
            if tier == "verified" and eligible
            else None
        ),
    }


def test_entity_normalization_is_exact_not_containment() -> None:
    assert authority.entities_match("Payments_Service", "payments-service")
    assert authority.entities_match("payments service", "PAYMENTS-SERVICE")
    assert not authority.entities_match("payments", "payments-canary")
    assert not authority.entities_match("", "payments-service")


def test_wrong_entity_and_receiptless_verified_rows_stay_visible_but_cannot_steer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrong = _row(
        "00000000-0000-4000-8000-000000000001",
        entity="checkout-api",
        tier="verified",
        action="scale_up",
    )
    legacy = _row(
        "00000000-0000-4000-8000-000000000002",
        entity="payments-service",
        tier="verified",
        action="restart_service",
        eligible=False,
    )
    advisory = _row(
        "00000000-0000-4000-8000-000000000003",
        entity="Payments_Service",
        tier="attested",
        action="open_incident",
    )

    monkeypatch.setattr(agent, "_providers", lambda: [])
    out = agent.propose(
        "payments-service latency",
        [wrong, legacy, advisory],
        target_entity="payments-service",
    )

    assert out["action"] == "open_incident"
    assert out["reasoning_source"] == "fallback_memory"
    assert out["cited_memory_ids"] == [advisory["memory_id"]]
    assert out["eligible_memory_ids"] == [advisory["memory_id"]]
    assert {row["memory_id"] for row in out["excluded_memories"]} == {
        wrong["memory_id"],
        legacy["memory_id"],
    }
    reasons = {row["reason"] for row in out["excluded_memories"]}
    assert authority.ENTITY_MISMATCH in reasons
    assert authority.VERIFIED_WITHOUT_CURRENT_RECEIPT in reasons


def test_no_eligible_memory_uses_only_the_disclosed_heuristic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        agent,
        "_providers",
        lambda: pytest.fail("no eligible memory means no model provider may be asked"),
    )
    out = agent.propose(
        "payments-service latency",
        [
            _row(
                "00000000-0000-4000-8000-000000000004",
                entity="checkout-api",
                tier="verified",
                action="restart_service",
            )
        ],
        target_entity="payments-service",
    )
    assert out["action"] == "scale_up"
    assert out["reasoning_source"] == "fallback_heuristic"
    assert out["cited_memory_ids"] == []
    assert "Nothing in memory informed this" in out["rationale"]
    assert "no model was asked" in out["rationale"]


def test_bare_verified_label_without_receipt_annotation_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        agent,
        "_providers",
        lambda: pytest.fail("an unannotated verified label must never reach a model"),
    )
    row = {
        "memory_id": "00000000-0000-4000-8000-000000000007",
        "entity": "payments-service",
        "attribute_key": "remediation",
        "attribute_value": "restart_service",
        "content": "restart_service",
        "trust_tier": "verified",
    }
    out = agent.propose(
        "payments-service latency",
        [row],
        target_entity="payments-service",
    )
    assert out["reasoning_source"] == "fallback_heuristic"
    assert out["eligible_memory_ids"] == []
    assert out["cited_memory_ids"] == []
    assert out["excluded_memories"] == [{
        "memory_id": row["memory_id"],
        "entity": "payments-service",
        "trust_tier": "verified",
        "reason": authority.VERIFIED_WITHOUT_CURRENT_RECEIPT,
    }]


def test_verified_row_without_a_metric_receipt_is_annotated_non_authoritative(
    tenant_id: str,
    agent_id: str,
) -> None:
    created = memory.remember(
        tenant_id,
        agent_id,
        "legacy scale_up row",
        entity="payments-service",
        attribute_key="remediation",
        attribute_value="scale_up",
    )
    conn = db.get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE agent_memories
                SET trust_tier = 'verified', trust_checked_at = NULL
                WHERE tenant_id = %s AND memory_id = %s
                """,
                (tenant_id, created["memory_id"]),
            )
        conn.commit()
    finally:
        db.put_conn(conn)

    recalled = memory.recall(tenant_id, agent_id, "legacy scale_up row", k=5)
    row = next(item for item in recalled if item["memory_id"] == created["memory_id"])
    assert row["trust_tier"] == "verified"
    assert row["action_eligible"] is False
    assert row["action_eligibility_reason"] == authority.VERIFIED_WITHOUT_CURRENT_RECEIPT
    assert row["action_receipt_external_ref"] is None


def test_receipt_lookup_failure_fails_closed_without_hiding_recall() -> None:
    class Cursor:
        def __init__(self) -> None:
            self.queries = []

        def execute(self, query, params=None) -> None:
            self.queries.append(query)
            if "SELECT DISTINCT" in query:
                raise RuntimeError("simulated schema mismatch")

    row = {
        "memory_id": "00000000-0000-4000-8000-000000000006",
        "entity": "payments-service",
        "trust_tier": "verified",
    }
    cursor = Cursor()
    annotated = authority.annotate_action_eligibility(
        cursor,
        str(uuid.uuid4()),
        [row],
    )
    assert annotated[0]["memory_id"] == row["memory_id"]
    assert annotated[0]["action_eligible"] is False
    assert (
        annotated[0]["action_eligibility_reason"]
        == authority.VERIFIED_WITHOUT_CURRENT_RECEIPT
    )
    assert any("SAVEPOINT memorystand_authority_receipt" in q for q in cursor.queries)
    assert any("ROLLBACK TO SAVEPOINT" in q for q in cursor.queries)
    assert any("RELEASE SAVEPOINT" in q for q in cursor.queries)


def test_verified_row_with_a_complete_matching_metric_receipt_is_authoritative(
    tenant_id: str,
    agent_id: str,
) -> None:
    created = memory.remember(
        tenant_id,
        agent_id,
        "receipt-backed scale_up row",
        entity="payments-service",
        attribute_key="remediation",
        attribute_value="scale_up",
    )
    decision = decisions.decide(
        tenant_id,
        agent_id,
        action="scale_up",
        rationale="receipt fixture",
        consulted_memory_ids=[],
        produced_memory_ids=[created["memory_id"]],
        target_entity="payments-service",
    )
    conn = db.get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE agent_memories
                SET trust_tier = 'verified', trust_checked_at = now()
                WHERE tenant_id = %s AND memory_id = %s
                """,
                (tenant_id, created["memory_id"]),
            )
            cur.execute(
                """
                UPDATE agent_decisions
                SET outcome = 'success',
                    outcome_confirmed_at = now(),
                    outcome_metric_delta = -40,
                    outcome_source = 'metric',
                    outcome_external_ref =
                      'AWS/Lambda|Duration|FunctionName=payments-service'
                WHERE tenant_id = %s AND decision_id = %s
                """,
                (tenant_id, decision["decision_id"]),
            )
        conn.commit()
    finally:
        db.put_conn(conn)

    recalled = memory.recall(tenant_id, agent_id, "receipt-backed scale_up row", k=5)
    row = next(item for item in recalled if item["memory_id"] == created["memory_id"])
    assert row["action_eligible"] is True
    assert row["action_eligibility_reason"] == authority.VERIFIED_RECEIPT
    assert (
        row["action_receipt_external_ref"]
        == "AWS/Lambda|Duration|FunctionName=payments-service"
    )


def test_historical_receipt_must_precede_the_historical_trust_check() -> None:
    memory_id = "00000000-0000-4000-8000-000000000005"
    checked_at = dt.datetime(2026, 8, 18, 12, 0, tzinfo=dt.UTC)

    class Cursor:
        def __init__(self) -> None:
            self.rows = []
            self.calls = 0

        def execute(self, query, params) -> None:
            self.calls += 1
            if self.calls == 1:
                self.rows = [{
                    "memory_id": memory_id,
                    "entity": "payments-service",
                    "trust_checked_at": checked_at,
                }]
            else:
                self.rows = [{
                    "decided_at": checked_at + dt.timedelta(minutes=1),
                    "produced_memory_ids": [memory_id],
                    "outcome": "success",
                    "outcome_confirmed_at": checked_at + dt.timedelta(minutes=2),
                    "outcome_source": "metric",
                    "outcome_external_ref":
                        "AWS/Lambda|Duration|FunctionName=payments-service",
                    "outcome_metric_delta": -40.0,
                }]

        def fetchall(self):
            return self.rows

    annotated = authority.annotate_action_eligibility_at(
        Cursor(),
        str(uuid.uuid4()),
        [{
            "memory_id": memory_id,
            "entity": "payments-service",
            "trust_tier": "verified",
        }],
    )
    assert annotated[0]["action_eligible"] is False
    assert annotated[0]["action_receipt_external_ref"] is None
    assert (
        annotated[0]["action_eligibility_reason"]
        == authority.VERIFIED_WITHOUT_CURRENT_RECEIPT
    )


def test_agent_selected_decide_requires_a_target_before_reasoning(
    monkeypatch: pytest.MonkeyPatch,
    agent_id: str,
) -> None:
    tenant_id = str(uuid.uuid4())
    monkeypatch.setenv(handler.SHARED_SECRET_ENV, "operator-secret")
    monkeypatch.setenv(handler.KILL_SWITCH_ENV, "off")
    monkeypatch.setattr(memory, "recall", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        agent,
        "propose",
        lambda *args, **kwargs: pytest.fail("missing target must fail before reasoning"),
    )

    response = handler.lambda_handler(
        _http(
            "/decide",
            {
                "tenant_id": tenant_id,
                "agent_id": agent_id,
                "query": "payments latency",
            },
        ),
        None,
    )
    assert response["statusCode"] == 400
    assert "target_entity is required" in response["body"]


def test_invalid_optional_task_uuid_is_rejected_before_recall(
    monkeypatch: pytest.MonkeyPatch,
    agent_id: str,
) -> None:
    tenant_id = str(uuid.uuid4())
    monkeypatch.setenv(handler.SHARED_SECRET_ENV, "operator-secret")
    monkeypatch.setenv(handler.KILL_SWITCH_ENV, "off")
    monkeypatch.setattr(
        memory,
        "recall",
        lambda *args, **kwargs: pytest.fail("invalid task_id must fail before recall"),
    )
    response = handler.lambda_handler(
        _http(
            "/decide",
            {
                "tenant_id": tenant_id,
                "agent_id": agent_id,
                "query": "payments latency",
                "target_entity": "payments-service",
                "task_id": "inc-4471",
            },
        ),
        None,
    )
    assert response["statusCode"] == 400
    assert "task_id must be a UUID" in response["body"]


def test_ingest_rejects_an_invalid_optional_task_uuid_before_memory_write(
    monkeypatch: pytest.MonkeyPatch,
    agent_id: str,
) -> None:
    tenant_id = str(uuid.uuid4())
    monkeypatch.setenv(handler.SHARED_SECRET_ENV, "operator-secret")
    monkeypatch.setenv(handler.KILL_SWITCH_ENV, "off")
    monkeypatch.setattr(
        memory,
        "remember",
        lambda *args, **kwargs: pytest.fail("invalid task_id must fail before memory write"),
    )
    response = handler.lambda_handler(
        _http(
            "/ingest",
            {
                "tenant_id": tenant_id,
                "agent_id": agent_id,
                "content": "fact",
                "task_id": "inc-4471",
            },
        ),
        None,
    )
    assert response["statusCode"] == 400
    assert "task_id must be a UUID" in response["body"]


def test_caller_supplied_action_remains_a_separately_labeled_recording_path(
    monkeypatch: pytest.MonkeyPatch,
    agent_id: str,
) -> None:
    tenant_id = str(uuid.uuid4())
    monkeypatch.setenv(handler.SHARED_SECRET_ENV, "operator-secret")
    monkeypatch.setenv(handler.KILL_SWITCH_ENV, "off")
    monkeypatch.setattr(memory, "recall", lambda *args, **kwargs: [])
    captured = {}

    def record(*args, **kwargs):
        captured.update(kwargs)
        return {
            "decision_id": str(uuid.uuid4()),
            "decided_at": "2026-08-18T00:00:00Z",
            "action": args[2],
            "status": "taken",
            "consulted": [],
            "produced": [],
            "target_entity": kwargs.get("target_entity"),
        }

    monkeypatch.setattr(decisions, "decide", record)
    response = handler.lambda_handler(
        _http(
            "/decide",
            {
                "tenant_id": tenant_id,
                "agent_id": agent_id,
                "query": "record this",
                "action": "open_incident",
                "rationale": "caller owns the policy",
            },
        ),
        None,
    )
    payload = json.loads(response["body"])
    assert response["statusCode"] == 201
    assert payload["reasoning_source"] == "caller_supplied"
    assert payload["target_entity"] is None
    assert payload["eligible_memory_ids"] == []
    assert payload["excluded_memories"] == []


def test_target_entity_is_persisted_and_returned(tenant_id: str, agent_id: str) -> None:
    decision = decisions.decide(
        tenant_id,
        agent_id,
        action="open_incident",
        rationale="subject-bound",
        consulted_memory_ids=[],
        query_text="payments latency",
        recall_k=5,
        target_entity="payments-service",
    )
    stored = decisions.get(tenant_id, decision["decision_id"])
    assert decision["target_entity"] == "payments-service"
    assert stored and stored["target_entity"] == "payments-service"
