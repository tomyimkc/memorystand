# SPDX-License-Identifier: Apache-2.0
"""An identical claim is corroboration, not a new memory — and it must not buy standing.

`_hard_conflicts` matches on `attribute_value IS DISTINCT FROM`, so it only ever saw
CONTRADICTIONS. Nothing looked at exact repeats, and re-asserting the same fact wrote a new row
every time. The live demo tenant accumulated eleven copies of one fact, which surfaced as five
identical rows at identical distance in a single recall result — a top-k that looks broken to
anyone reading it, and a recall window wasted on things the store already knew.

The second test is the important one. Deduplicating is easy; the temptation is to treat a
repeat as weak evidence and nudge confidence. That would reopen the `tier_climb` attack in
benchmarks/poisoning_benchmark.py, which is built on resubmitting one false claim to accumulate
standing. Repetition is not evidence.
"""

from __future__ import annotations

from backend import memory, trust


def test_an_identical_claim_does_not_create_a_second_memory(tenant_id, agent_id):
    first = memory.remember(
        tenant_id, agent_id, "payments-service reads from orders_v2",
        entity="payments-service", attribute_key="reads_from_table",
        attribute_value="orders_v2", source="runbook:db-failover",
    )
    second = memory.remember(
        tenant_id, agent_id, "payments-service reads from orders_v2, per the runbook",
        entity="payments-service", attribute_key="reads_from_table",
        attribute_value="orders_v2", source="runbook:db-failover",
    )

    assert second["memory_id"] == first["memory_id"], "a repeat must point at the memory that already says it"
    assert second["verdict"] == "accepted"
    assert second.get("corroborated") is True
    assert "corroboration, not a new memory" in " ".join(second["verdict_reasons"])


def test_corroboration_does_not_raise_confidence(tenant_id, agent_id):
    """The whole premise: repetition is not evidence.

    Rewarding a repeat would reopen the tier_climb attack — resubmit one false claim enough
    times and it accumulates standing. Only an external outcome moves a memory up the ladder.
    """
    first = memory.remember(
        tenant_id, agent_id, "checkout-api breaker trips at 800ms",
        entity="checkout-api", attribute_key="circuit_breaker_timeout_ms",
        attribute_value="800", source="runbook:checkout",
    )
    before = memory.get(tenant_id, first["memory_id"])["confidence"]

    for _ in range(5):
        memory.remember(
            tenant_id, agent_id, "checkout-api breaker trips at 800ms",
            entity="checkout-api", attribute_key="circuit_breaker_timeout_ms",
            attribute_value="800", source="runbook:checkout",
        )

    after = memory.get(tenant_id, first["memory_id"])
    assert after["confidence"] == before, "repeating a claim must not buy confidence"
    assert after["trust_tier"] == trust.UNCONFIRMED, "nor standing"


def test_a_contradicting_claim_is_still_treated_as_a_conflict(tenant_id, agent_id):
    """Dedup must not swallow the contradiction path it sits in front of."""
    memory.remember(
        tenant_id, agent_id, "checkout-api breaker trips at 800ms",
        entity="checkout-api", attribute_key="circuit_breaker_timeout_ms",
        attribute_value="800", source="runbook:checkout",
    )
    other = memory.remember(
        tenant_id, agent_id, "checkout-api breaker trips at 300ms",
        entity="checkout-api", attribute_key="circuit_breaker_timeout_ms",
        attribute_value="300", source="slack:#ops",
    )
    assert other["verdict"] == "quarantined", "a different value is still a contradiction"


def test_a_free_text_memory_with_no_attributes_is_unaffected(tenant_id, agent_id):
    """Dedup keys on entity+attribute; a plain narrative memory has none and must still write."""
    a = memory.remember(tenant_id, agent_id, "the on-call lead prefers paging before restarting")
    b = memory.remember(tenant_id, agent_id, "the on-call lead prefers paging before restarting")
    assert a["memory_id"] != b["memory_id"]
