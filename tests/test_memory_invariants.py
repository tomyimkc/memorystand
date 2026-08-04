# SPDX-License-Identifier: Apache-2.0
"""Admission-control invariants: what recall() is and is not allowed to leak.

These are the product's core promises. See backend/memory.py's module and
function docstrings -- ``recall`` itself raises ``AssertionError`` if it ever
sees a non-accepted row, so these tests are exercising a belt the code
already wears suspenders for.
"""

from __future__ import annotations

from backend import decisions, memory, trust


def test_recall_never_returns_a_held_memory_even_when_many_exist(tenant_id, agent_id):
    accepted = memory.remember(
        tenant_id,
        agent_id,
        "payments-service reads from orders_v2",
        entity="payments-service",
        attribute_key="reads_from_table",
        attribute_value="orders_v2",
        source="runbook:payments",
    )
    assert accepted["verdict"] == memory.ACCEPTED

    # Many contradicting claims, all from equal-authority sources, so every
    # one of them is held rather than accepted or superseding the original.
    held_ids = []
    for i in range(5):
        held = memory.remember(
            tenant_id,
            agent_id,
            f"payments-service actually reads from legacy_orders_{i}",
            entity="payments-service",
            attribute_key="reads_from_table",
            attribute_value=f"legacy_orders_{i}",
            source="slack:oncall-channel",
        )
        assert held["verdict"] == memory.HELD
        held_ids.append(held["memory_id"])

    results = memory.recall(tenant_id, agent_id, "what table does payments-service read from", k=10)
    returned_ids = {r["memory_id"] for r in results}

    assert accepted["memory_id"] in returned_ids
    assert returned_ids.isdisjoint(held_ids)
    assert all(r["verdict"] == memory.ACCEPTED for r in results)


def test_a_memory_contradicting_an_admitted_one_is_held_and_recall_does_not_see_it(tenant_id, agent_id):
    first = memory.remember(
        tenant_id,
        agent_id,
        "failover order for payments-service is primary-then-replica",
        entity="payments-service",
        attribute_key="failover_restart_order",
        attribute_value="primary-then-replica",
        source="runbook:failover",
    )
    assert first["verdict"] == memory.ACCEPTED

    contradiction = memory.remember(
        tenant_id,
        agent_id,
        "failover order for payments-service is replica-then-primary",
        entity="payments-service",
        attribute_key="failover_restart_order",
        attribute_value="replica-then-primary",
        source="slack:oncall-channel",  # equal-or-lower authority than a runbook -> held
    )
    assert contradiction["verdict"] == memory.HELD
    assert contradiction["memory_id"] != first["memory_id"]

    results = memory.recall(tenant_id, agent_id, "failover order for payments-service", k=10)
    returned_ids = {r["memory_id"] for r in results}
    assert contradiction["memory_id"] not in returned_ids
    assert first["memory_id"] in returned_ids


def test_a_higher_authority_source_corrects_a_lower_one_and_the_old_row_survives_as_history(tenant_id, agent_id):
    low_authority = memory.remember(
        tenant_id,
        agent_id,
        "the on-call rotation owner is Slack rumor: bob",
        entity="payments-service",
        attribute_key="oncall_owner",
        attribute_value="bob",
        source="slack:oncall-channel",
    )
    assert low_authority["verdict"] == memory.ACCEPTED

    correction = memory.remember(
        tenant_id,
        agent_id,
        "the on-call rotation owner is actually alice, per the postmortem",
        entity="payments-service",
        attribute_key="oncall_owner",
        attribute_value="alice",
        source="human:alice",  # highest authority -> supersedes
    )
    assert correction["verdict"] == memory.ACCEPTED
    assert correction["superseded"] == low_authority["memory_id"]

    # The old row is not deleted -- it survives with verdict='superseded'.
    old_row = memory.get(tenant_id, low_authority["memory_id"])
    assert old_row is not None, "superseded memory must survive as history, not be deleted"
    assert old_row["verdict"] == memory.SUPERSEDED

    # And recall only ever sees the current, corrected value.
    results = memory.recall(tenant_id, agent_id, "who owns the on-call rotation for payments-service", k=10)
    returned_ids = {r["memory_id"] for r in results}
    assert correction["memory_id"] in returned_ids
    assert low_authority["memory_id"] not in returned_ids


def test_a_memory_that_has_earned_standing_is_not_silently_overridden_by_a_contradicting_claim(tenant_id, agent_id):
    original = memory.remember(
        tenant_id,
        agent_id,
        "primary datastore table for payments-service is orders_v2",
        entity="payments-service",
        attribute_key="primary_datastore_table",
        attribute_value="orders_v2",
        source="runbook:payments",
    )
    assert original["verdict"] == memory.ACCEPTED

    # Earn standing the real way: a decision produces this memory, and an
    # external outcome confirms the decision worked.
    decision = decisions.decide(
        tenant_id,
        agent_id,
        action="reply",
        rationale="confirmed primary table during incident",
        consulted_memory_ids=[],
        produced_memory_ids=[original["memory_id"]],
    )
    grant = trust.grant_standing(
        tenant_id,
        decision["decision_id"],
        evidence={"source": "human", "outcome": "success", "external_ref": "postmortem-123"},
    )
    assert original["memory_id"] in grant["promoted"]
    assert memory.get(tenant_id, original["memory_id"])["trust_tier"] == trust.ATTESTED

    # Even a HIGHER-authority contradiction cannot silently override it now.
    contradiction = memory.remember(
        tenant_id,
        agent_id,
        "primary datastore table for payments-service is actually orders_v3",
        entity="payments-service",
        attribute_key="primary_datastore_table",
        attribute_value="orders_v3",
        source="human:carol",  # highest authority tier, would normally supersede
    )
    assert contradiction["verdict"] == memory.HELD
    assert "standing" in " ".join(contradiction["verdict_reasons"]).lower()

    results = memory.recall(tenant_id, agent_id, "primary datastore table for payments-service", k=10)
    returned_ids = {r["memory_id"] for r in results}
    assert original["memory_id"] in returned_ids
    assert contradiction["memory_id"] not in returned_ids
