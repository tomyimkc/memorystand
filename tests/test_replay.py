# SPDX-License-Identifier: Apache-2.0
"""Time-travel correctness (backend/replay.py).

Covers the two platform facts replay.py's own docstring paid for: AS OF
SYSTEM TIME cannot appear in a subquery/CTE (so a pinned top-level statement
or transaction is used instead), and a request past the cluster's GC window
must raise a named exception, not a raw psycopg2 error.
"""

from __future__ import annotations

import datetime as dt

import pytest

from backend import decisions, memory, replay, trust


def test_belief_state_at_returns_the_pre_change_state_after_a_change(tenant_id, agent_id, db_now):
    before = memory.remember(tenant_id, agent_id, "belief before the cutoff")
    instant = db_now(before["memory_id"], tenant_id)
    after = memory.remember(tenant_id, agent_id, "belief written after the cutoff")

    belief = replay.belief_state_at(tenant_id, instant)
    ids = {m["memory_id"] for m in belief}

    assert before["memory_id"] in ids
    assert after["memory_id"] not in ids


def test_recall_as_of_composes_aost_with_a_vector_order_by(tenant_id, agent_id, db_now):
    before = memory.remember(tenant_id, agent_id, "the payments queue backs up under load")
    instant = db_now(before["memory_id"], tenant_id)
    memory.remember(tenant_id, agent_id, "an unrelated fact written after the cutoff")

    results = replay.recall_as_of(tenant_id, agent_id, "payments queue backs up", instant, k=10)
    ids = {r["memory_id"] for r in results}

    assert before["memory_id"] in ids
    assert all("distance" in r for r in results), "recall_as_of must still rank by the vector ORDER BY"


def test_belief_diff_marks_added_and_changed_correctly(tenant_id, agent_id, db_now):
    to_be_promoted = memory.remember(tenant_id, agent_id, "restarting the worker cleared the backlog")
    instant = db_now(to_be_promoted["memory_id"], tenant_id)

    added_after = memory.remember(tenant_id, agent_id, "a brand new fact written after the cutoff")

    decision = decisions.decide(
        tenant_id,
        agent_id,
        action="restart_service",
        rationale="test",
        consulted_memory_ids=[],
        produced_memory_ids=[to_be_promoted["memory_id"]],
    )
    trust.grant_standing(
        tenant_id,
        decision["decision_id"],
        evidence={"source": "pagerduty", "outcome": "success", "external_ref": "INC-7"},
    )

    diff = {row["memory_id"]: row for row in replay.belief_diff(tenant_id, instant)}

    assert diff[added_after["memory_id"]]["delta"] == "added"
    assert diff[to_be_promoted["memory_id"]]["delta"] == "changed"
    assert diff[to_be_promoted["memory_id"]]["was_trust_tier"] == trust.UNCONFIRMED


def test_an_instant_beyond_the_gc_horizon_raises_gcwindowexceeded_not_a_raw_error(tenant_id):
    ancient = dt.datetime(2000, 1, 1, tzinfo=dt.timezone.utc)
    with pytest.raises(replay.GCWindowExceeded):
        replay.belief_state_at(tenant_id, ancient)
