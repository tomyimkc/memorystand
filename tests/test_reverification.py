# SPDX-License-Identifier: Apache-2.0
"""Trust must be able to decay, or the system is a folklore generator.

Until re-verification existed, a memory promoted to `verified` stayed `verified` forever. That
is the obvious hole in an argument built on "reality decides": reality keeps moving after the
promotion. The runbook step that genuinely fixed an incident in March can be false by August,
and the memory would still be sitting at the top of the ladder telling an agent to do it.

These tests pin the four outcomes of a re-check, and -- more importantly -- that a decayed
memory stops winning decisions, which is the only reason the tier matters at all.
"""

from __future__ import annotations

import datetime as dt

import pytest

from backend import agent, decisions, evidence, memory, reverify, trust


def _averages(before: float, after: float):
    calls = {"n": 0}

    def _avg(client, namespace, metric, dims, start, end):
        calls["n"] += 1
        return before if calls["n"] % 2 == 1 else after

    return _avg


METRIC_REF = "AWS/Lambda|Duration|FunctionName=memorystand"


def _verified_memory(tenant_id, agent_id, monkeypatch, *, before=100.0, after=60.0):
    """Create a memory and take it all the way to `verified` through the real path."""
    monkeypatch.setattr(evidence, "_average", _averages(before, after))
    mem = memory.remember(tenant_id, agent_id, "scaling checkout-api cleared the latency spike")
    decision = decisions.decide(
        tenant_id, agent_id, action="scale_up", rationale="r",
        consulted_memory_ids=[mem["memory_id"]], produced_memory_ids=[mem["memory_id"]],
    )
    result = trust.grant_standing(
        tenant_id, decision["decision_id"],
        {"outcome": "success", "source": "metric", "external_ref": METRIC_REF,
         "metric_delta": -40.0},
    )
    assert result["trust_tier"] == trust.VERIFIED
    return mem["memory_id"]


def _make_stale(tenant_id, memory_id):
    """Backdate the last check so the sweep considers it."""
    from backend import db

    conn = db.get_conn()
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE agent_memories SET trust_checked_at = now() - INTERVAL '400 days' "
                "WHERE memory_id = %s AND tenant_id = %s",
                (memory_id, tenant_id),
            )
    finally:
        db.put_conn(conn)


def test_a_still_true_memory_keeps_its_standing(tenant_id, agent_id, monkeypatch):
    mid = _verified_memory(tenant_id, agent_id, monkeypatch)
    _make_stale(tenant_id, mid)

    monkeypatch.setattr(evidence, "_average", _averages(100.0, 60.0))  # world unchanged
    out = reverify.sweep(tenant_id)

    assert out["checked"] == 1
    assert out["still_verified"] == 1
    assert out["model_calls"] == 0
    assert memory.get(tenant_id, mid)["trust_tier"] == trust.VERIFIED


def test_a_memory_reality_now_contradicts_is_demoted_to_disputed(tenant_id, agent_id, monkeypatch):
    """The headline case: it was true, the world moved, the memory must stop being trusted."""
    mid = _verified_memory(tenant_id, agent_id, monkeypatch)
    _make_stale(tenant_id, mid)

    # The metric now moves the OPPOSITE way -- scaling no longer helps.
    monkeypatch.setattr(evidence, "_average", _averages(100.0, 180.0))
    out = reverify.sweep(tenant_id)

    assert out["demoted_to_disputed"] == 1
    assert memory.get(tenant_id, mid)["trust_tier"] == trust.DISPUTED
    assert out["changes"][0]["from"] == "verified"
    assert out["changes"][0]["to"] == trust.DISPUTED


def test_unverifiable_now_means_attested_not_disputed(tenant_id, agent_id, monkeypatch):
    """Losing the ability to check is not the same as being disproved.

    Demoting to `disputed` on an unreachable CloudWatch would let an outage in the checker
    destroy standing that was legitimately earned. `attested` is the honest tier: a real
    outcome was reported, it just is not independently supported any more.
    """
    mid = _verified_memory(tenant_id, agent_id, monkeypatch)
    _make_stale(tenant_id, mid)

    def _boom():
        raise RuntimeError("no credentials")

    monkeypatch.setattr(evidence, "_get_client", _boom)
    out = reverify.sweep(tenant_id)

    assert out["demoted_to_attested"] == 1
    assert memory.get(tenant_id, mid)["trust_tier"] == trust.ATTESTED


def test_a_fresh_memory_is_not_swept(tenant_id, agent_id, monkeypatch):
    """Standing has a half-life, not a hair trigger."""
    _verified_memory(tenant_id, agent_id, monkeypatch)  # trust_checked_at = now()
    out = reverify.sweep(tenant_id)
    assert out["checked"] == 0


def test_dry_run_changes_nothing(tenant_id, agent_id, monkeypatch):
    mid = _verified_memory(tenant_id, agent_id, monkeypatch)
    _make_stale(tenant_id, mid)

    monkeypatch.setattr(evidence, "_average", _averages(100.0, 180.0))
    out = reverify.sweep(tenant_id, dry_run=True)

    assert out["demoted_to_disputed"] == 1, "it should still report what it would do"
    assert memory.get(tenant_id, mid)["trust_tier"] == trust.VERIFIED, "but change nothing"


def test_a_decayed_memory_stops_winning_the_decision(tenant_id, agent_id, monkeypatch):
    """The point of the tier. Decay is meaningless if it does not change behaviour.

    Before the sweep, the verified memory outranks a closer unconfirmed one. After reality
    stops agreeing, it must stop outranking it -- otherwise trust decay is bookkeeping.
    """
    mid = _verified_memory(tenant_id, agent_id, monkeypatch)

    def recalled(tier):
        return [
            {"memory_id": "closer", "trust_tier": "unconfirmed", "attribute_key": "remediation",
             "attribute_value": "restart_service", "content": "restart_service"},
            {"memory_id": mid, "trust_tier": tier, "attribute_key": "remediation",
             "attribute_value": "scale_up", "content": "scale_up"},
        ]

    before, _ = agent._fallback_action("checkout-api latency", recalled("verified"))
    assert before == "scale_up", "verified memory should outrank the closer unconfirmed one"

    _make_stale(tenant_id, mid)
    monkeypatch.setattr(evidence, "_average", _averages(100.0, 180.0))
    reverify.sweep(tenant_id)
    new_tier = memory.get(tenant_id, mid)["trust_tier"]

    after, _ = agent._fallback_action("checkout-api latency", recalled(new_tier))
    assert new_tier == trust.DISPUTED
    assert after == "restart_service", "a disputed memory must no longer steer the action"
    assert after != before, "decay has to change behaviour or it is just bookkeeping"


def test_the_sweep_makes_no_model_calls(tenant_id, agent_id, monkeypatch):
    mid = _verified_memory(tenant_id, agent_id, monkeypatch)
    _make_stale(tenant_id, mid)
    monkeypatch.setattr(evidence, "_average", _averages(100.0, 60.0))
    assert reverify.sweep(tenant_id)["model_calls"] == 0
    trust.assert_no_model_calls()
