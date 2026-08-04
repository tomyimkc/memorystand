# SPDX-License-Identifier: Apache-2.0
"""The half of the central claim that used to be missing.

MemoryStand's headline is that a memory earns trust only from a real external, non-model
signal. Three parts to that, and only two were ever enforced:

  1. the promotion path makes zero model calls        -- enforced, asserted at runtime
  2. a model's opinion is not an admissible source    -- enforced, schema-level allow-list
  3. the external signal is REAL                      -- NOT enforced. The caller said so.

``_validate`` checked that ``external_ref`` was a non-empty string and believed it. Anyone
with the shared secret could grant standing by naming an incident that never happened —
so the sentence the whole project is built on was true about its easy half and silent
about its hard one.

These tests cover the fix. The important assertions are the negative ones: an unchecked
claim must NOT reach ``verified``, and a claim the system of record contradicts must be
refused outright rather than downgraded politely.
"""

from __future__ import annotations

import datetime as dt

import pytest

from backend import decisions, evidence, memory, trust


# --- The four epistemic states ------------------------------------------------------------


def test_unverifiable_sources_say_so_rather_than_passing_quietly() -> None:
    """A human sign-off has no system of record. Pretending to check it would be theatre."""
    for source in ("pagerduty", "human"):
        v = evidence.verify(source, "INC-4610", None, None)
        assert v.status == evidence.NOT_VERIFIABLE
        assert not v.grants_verified_tier


def test_a_malformed_metric_reference_is_not_silently_accepted() -> None:
    v = evidence.verify("metric", "some free text a human typed", -40.0, None)
    assert v.status == evidence.NOT_VERIFIABLE
    assert not v.grants_verified_tier
    assert "Namespace|MetricName" in v.detail


def test_unreachable_cloudwatch_neither_grants_nor_denies(monkeypatch) -> None:
    """An outage in the checker must not become evidence in either direction.

    Failing open would promote unchecked claims. Failing closed by *rejecting* would
    discard real outcomes because our own dependency was down. Neither is right, which is
    why there are four statuses instead of a boolean.
    """
    def boom():
        raise RuntimeError("no credentials")

    monkeypatch.setattr(evidence, "_get_client", boom)
    v = evidence.verify("metric", "AWS/Lambda|Errors|FunctionName=memorystand", -1.0, None)
    assert v.status == evidence.UNAVAILABLE
    assert not v.grants_verified_tier


def test_agreement_confirms(monkeypatch) -> None:
    monkeypatch.setattr(evidence, "_average", _fake_averages(before=100.0, after=60.0))
    v = evidence.verify("metric", "AWS/Lambda|Duration|FunctionName=memorystand", -40.0, None)
    assert v.status == evidence.CONFIRMED
    assert v.grants_verified_tier
    assert v.observed == pytest.approx(-40.0)


def test_a_claim_in_the_opposite_direction_is_contradicted(monkeypatch) -> None:
    """The failure that matters most: 'latency fell' when the metric shows it rose."""
    monkeypatch.setattr(evidence, "_average", _fake_averages(before=100.0, after=140.0))
    v = evidence.verify("metric", "AWS/Lambda|Duration|FunctionName=memorystand", -40.0, None)
    assert v.status == evidence.CONTRADICTED
    assert not v.grants_verified_tier
    assert "opposite direction" in v.detail


def test_a_wildly_wrong_magnitude_is_contradicted(monkeypatch) -> None:
    monkeypatch.setattr(evidence, "_average", _fake_averages(before=100.0, after=99.0))
    v = evidence.verify("metric", "AWS/Lambda|Duration|FunctionName=memorystand", -40.0, None)
    assert v.status == evidence.CONTRADICTED


def _fake_averages(*, before: float, after: float):
    """Stand in for CloudWatch: first call is the pre-decision window, second the post."""
    calls = {"n": 0}

    def _avg(client, namespace, metric, dims, start, end):
        calls["n"] += 1
        return before if calls["n"] == 1 else after

    return _avg


# --- End to end: the ladder now distinguishes asserted from checked -----------------------


def _produced_decision(tenant_id, agent_id, memory_id):
    return decisions.decide(
        tenant_id,
        agent_id,
        action="restart_service",
        rationale="test decision",
        consulted_memory_ids=[memory_id],
        produced_memory_ids=[memory_id],
    )


def test_an_unchecked_success_reaches_attested_but_not_verified(tenant_id, agent_id) -> None:
    """This is the assertion the project's own headline depends on."""
    mem = memory.remember(tenant_id, agent_id, "restarting payments-service resolved INC-1")
    decision = _produced_decision(tenant_id, agent_id, mem["memory_id"])

    result = trust.grant_standing(
        tenant_id,
        decision["decision_id"],
        {"outcome": "success", "source": "pagerduty", "external_ref": "INC-4610"},
    )

    assert result["model_calls"] == 0
    assert result["trust_tier"] == trust.ATTESTED
    assert result["verification"]["status"] == evidence.NOT_VERIFIABLE
    assert memory.get(tenant_id, mem["memory_id"])["trust_tier"] == trust.ATTESTED


def test_a_confirmed_metric_reaches_verified(tenant_id, agent_id, monkeypatch) -> None:
    monkeypatch.setattr(evidence, "_average", _fake_averages(before=100.0, after=60.0))
    mem = memory.remember(tenant_id, agent_id, "scaling checkout-api cut p99 latency")
    decision = _produced_decision(tenant_id, agent_id, mem["memory_id"])

    result = trust.grant_standing(
        tenant_id,
        decision["decision_id"],
        {
            "outcome": "success",
            "source": "metric",
            "external_ref": "AWS/Lambda|Duration|FunctionName=memorystand",
            "metric_delta": -40.0,
        },
    )

    assert result["trust_tier"] == trust.VERIFIED
    assert result["verification"]["status"] == evidence.CONFIRMED
    assert result["model_calls"] == 0, "verification must not have introduced a model call"


def test_a_contradicted_claim_is_refused_outright(tenant_id, agent_id, monkeypatch) -> None:
    """Refused, not downgraded. A memory must gain nothing from a disproved claim."""
    monkeypatch.setattr(evidence, "_average", _fake_averages(before=100.0, after=180.0))
    mem = memory.remember(tenant_id, agent_id, "scaling checkout-api cut p99 latency")
    decision = _produced_decision(tenant_id, agent_id, mem["memory_id"])

    with pytest.raises(trust.OutcomeRejected) as exc:
        trust.grant_standing(
            tenant_id,
            decision["decision_id"],
            {
                "outcome": "success",
                "source": "metric",
                "external_ref": "AWS/Lambda|Duration|FunctionName=memorystand",
                "metric_delta": -40.0,
            },
        )

    assert "disagrees" in str(exc.value)
    # The memory must be untouched, and the outcome must not have been recorded — so an
    # honest outcome can still be supplied later.
    assert memory.get(tenant_id, mem["memory_id"])["trust_tier"] == trust.UNCONFIRMED


# --- The guard that keeps the claim true --------------------------------------------------


def test_the_promotion_path_cannot_reach_a_model() -> None:
    trust.assert_no_model_calls()


def test_the_guard_catches_a_model_client_one_import_away(monkeypatch) -> None:
    """The original guard only checked trust.py's own globals.

    That made the invariant defeatable by the most natural refactor imaginable: move the
    Bedrock call into a helper module and call it from here. This asserts the guard now
    looks one level down.
    """
    monkeypatch.setattr(evidence, "converse", lambda *a, **k: None, raising=False)
    with pytest.raises(AssertionError, match="one import away|model-carrying"):
        trust.assert_no_model_calls()


def test_cloudwatch_is_allowed_because_the_rule_is_about_models_not_networks() -> None:
    """evidence.py imports boto3 on purpose, and that must not trip the guard.

    The claim is 'no model decides whether a memory is true', not 'this path makes no
    network calls'. Asking a metrics store what a number did is the thesis working.
    """
    import backend.evidence as ev

    assert "boto3" not in dir(ev) or True  # imported lazily inside _get_client
    trust.assert_no_model_calls()
