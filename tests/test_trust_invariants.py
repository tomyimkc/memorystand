# SPDX-License-Identifier: Apache-2.0
"""The outcome gate (backend/trust.py): MemoryStand's one novel claim.

Promotion to 'verified' must happen with zero model calls, must require
externally-attributable evidence, and must never be recorded twice for the
same decision. Only PRODUCED memories are ever re-tiered.
"""

from __future__ import annotations

import pytest

from backend import decisions, memory, trust


def _produced_decision(tenant_id, agent_id, *, consulted=(), produced=()):
    return decisions.decide(
        tenant_id,
        agent_id,
        action="restart_service",
        rationale="test decision",
        consulted_memory_ids=list(consulted),
        produced_memory_ids=list(produced),
    )


def test_grant_standing_reports_model_calls_equal_zero(tenant_id, agent_id):
    produced = memory.remember(tenant_id, agent_id, "restarting payments-service resolved the incident")
    decision = _produced_decision(tenant_id, agent_id, produced=[produced["memory_id"]])

    result = trust.grant_standing(
        tenant_id,
        decision["decision_id"],
        evidence={"source": "pagerduty", "outcome": "success", "external_ref": "INC-42"},
    )
    assert result["model_calls"] == 0


def test_trust_module_namespace_contains_no_model_client():
    # Structural check, run on the live path inside grant_standing() too --
    # asserted directly here as its own regression guard.
    trust.assert_no_model_calls()

    forbidden = {"boto3", "embeddings", "bedrock", "converse", "invoke_model", "openai", "anthropic"}
    present = forbidden & {name.lower() for name in vars(trust)}
    assert not present, f"model-carrying names reachable from trust.py: {present}"


def test_evidence_lacking_external_ref_is_refused(tenant_id, agent_id):
    produced = memory.remember(tenant_id, agent_id, "scaling up the pool fixed latency")
    decision = _produced_decision(tenant_id, agent_id, produced=[produced["memory_id"]])

    with pytest.raises(trust.OutcomeRejected):
        trust.grant_standing(
            tenant_id, decision["decision_id"],
            evidence={"source": "human", "outcome": "success"},
        )

    # Refused evidence must not have moved the memory's trust tier.
    assert memory.get(tenant_id, produced["memory_id"])["trust_tier"] == trust.UNCONFIRMED


def test_model_is_not_an_accepted_evidence_source(tenant_id, agent_id):
    produced = memory.remember(tenant_id, agent_id, "the model itself thinks this worked")
    decision = _produced_decision(tenant_id, agent_id, produced=[produced["memory_id"]])

    with pytest.raises(trust.OutcomeRejected):
        trust.grant_standing(
            tenant_id,
            decision["decision_id"],
            evidence={"source": "model", "outcome": "success", "external_ref": "self-assessment"},
        )
    assert "model" not in trust.VALID_SOURCES


def test_an_outcome_cannot_be_recorded_twice_for_the_same_decision(tenant_id, agent_id):
    produced = memory.remember(tenant_id, agent_id, "rolling back the deploy fixed the error rate")
    decision = _produced_decision(tenant_id, agent_id, produced=[produced["memory_id"]])

    first = trust.grant_standing(
        tenant_id,
        decision["decision_id"],
        evidence={"source": "metric", "outcome": "success", "external_ref": "error_rate_p99", "metric_delta": -0.4},
    )
    assert first["promoted"] == [produced["memory_id"]]

    with pytest.raises(trust.OutcomeRejected):
        trust.grant_standing(
            tenant_id,
            decision["decision_id"],
            evidence={"source": "human", "outcome": "success", "external_ref": "second-attempt"},
        )


def test_only_produced_memories_are_retiered_never_merely_consulted_ones(tenant_id, agent_id):
    consulted_only = memory.remember(tenant_id, agent_id, "this was read but not acted on directly")
    produced = memory.remember(tenant_id, agent_id, "this is what the decision actually wrote back")
    decision = _produced_decision(
        tenant_id, agent_id, consulted=[consulted_only["memory_id"]], produced=[produced["memory_id"]]
    )

    result = trust.grant_standing(
        tenant_id,
        decision["decision_id"],
        evidence={"source": "pagerduty", "outcome": "success", "external_ref": "INC-99"},
    )

    assert produced["memory_id"] in result["promoted"]
    assert consulted_only["memory_id"] not in result["promoted"]
    assert consulted_only["memory_id"] not in result["demoted"]

    # ATTESTED, not VERIFIED: the evidence here is a PagerDuty incident id, and this
    # deployment has no PagerDuty token to re-check it with. An outcome nobody could
    # independently confirm must not reach the top of the ladder -- that distinction is
    # the whole point of backend/evidence.py.
    assert memory.get(tenant_id, produced["memory_id"])["trust_tier"] == trust.ATTESTED
    assert memory.get(tenant_id, consulted_only["memory_id"])["trust_tier"] == trust.UNCONFIRMED
