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


def test_cross_examine_reruns_the_ranked_query_when_a_receipt_exists(tenant_id, agent_id):
    """Cross-examination must replay the agent's own RANKED retrieval, not a belief dump.

    `recall_as_of()` could always do this; nothing called it, because the decision row did not
    record what was asked. So `cross_examine` fell back to `belief_state_at()` -- every accepted
    memory as of the instant, unranked, no distances -- while the README and the Devpost copy
    both claimed it re-ran "the agent's exact recall query". An outside review caught that and the
    wording had to be weakened. Migration 003 records the query and k, which makes the strong
    claim true; this test is what keeps it true.
    """
    memory.remember(tenant_id, agent_id, "scaling checkout-api cleared the payments latency spike",
                    entity="checkout-api", attribute_key="remediation", attribute_value="scale_up")
    memory.remember(tenant_id, agent_id, "restarting ledger-worker did nothing for latency",
                    entity="ledger-worker", attribute_key="remediation", attribute_value="restart_service")

    query = "checkout-api latency spike remediation"
    recalled = memory.recall(tenant_id, agent_id, query, k=5)
    decision = decisions.decide(
        tenant_id, agent_id, action="scale_up", rationale="r",
        consulted_memory_ids=[r["memory_id"] for r in recalled],
        query_text=query, recall_k=5,
    )

    out = replay.cross_examine(tenant_id, decision["decision_id"])
    ranked = out["recalled_as_of"]
    assert ranked, "a decision with a retrieval receipt must replay its ranked recall"
    assert out["recall_note"] is None
    # The distinguishing property vs a belief-state dump: real per-row distances, in order.
    assert all("distance" in r for r in ranked)
    assert [r["distance"] for r in ranked] == sorted(r["distance"] for r in ranked)


def test_cross_examine_refuses_to_fake_a_receipt_it_does_not_have(tenant_id, agent_id):
    """A decision with no recorded query gets None plus a reason -- never a belief dump presented
    as if it were the agent's ranked recall. Fabricated provenance is the failure this project
    exists to refuse, and pre-003 decisions genuinely have no query to replay."""
    mem = memory.remember(tenant_id, agent_id, "a fact with no recorded retrieval")
    decision = decisions.decide(
        tenant_id, agent_id, action="page_oncall", rationale="r",
        consulted_memory_ids=[mem["memory_id"]],
    )  # no query_text -- the pre-migration shape

    out = replay.cross_examine(tenant_id, decision["decision_id"])
    assert out["recalled_as_of"] is None
    assert "no retrieval receipt" in (out["recall_note"] or "")
