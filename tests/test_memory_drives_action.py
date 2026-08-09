# SPDX-License-Identifier: Apache-2.0
"""Verified memory may change what the agent does; lower tiers may not do so autonomously.

This is the project's central premise stated as a test, and until now it was false on the
deployed system. `_fallback_action` took only the alert text and never looked at the recalled
memories, so with Bedrock quota at zero -- which is this deployment's permanent state --
memory was retrieved, rendered on screen, and then ignored. Every action came from matching a
word in the alert string.

That mattered more than it looked. The hackathon brief asks for memory that "is the thing that
makes an agent useful in production", and a judge inspecting the live API would have found a
decision path memory does not touch.

The rule now: verified memory can outrank the keyword table; attested memory can recommend an
action only behind approval; unconfirmed memory is inspectable context but is not
action-authoritative. Still zero model calls, deterministic, and auditable down to the memory_id.
"""

from __future__ import annotations

from backend import agent


def _mem(memory_id, tier, value, content="restarting the service cleared it"):
    return {
        "memory_id": memory_id,
        "trust_tier": tier,
        "entity": "payments-service",
        "attribute_key": "remediation",
        "attribute_value": value,
        "content": content,
    }


def test_without_memory_the_keyword_table_decides():
    """The old behaviour, kept: with nothing recalled, keywords still work."""
    action, why = agent._fallback_action("payments-service latency is climbing", [])
    assert action == "scale_up"
    assert "keyword table" in why
    assert "Nothing in memory informed this" in why


def test_a_verified_memory_overrides_the_keyword_table():
    """The claim, made testable: memory changes the action, with no model in the path."""
    alert = "payments-service latency is climbing"
    without, _ = agent._fallback_action(alert, [])
    with_memory, why = agent._fallback_action(
        alert, [_mem("m-1", "verified", "restart_service")]
    )

    assert without == "scale_up", "keyword table should have said scale_up"
    assert with_memory == "restart_service", "a verified memory must change the action"
    assert without != with_memory, "the whole point is that memory changes the outcome"
    assert "m-1" in why and "verified" in why, "the reason must name the deciding memory"


def test_higher_trust_wins_when_memories_disagree():
    """trust_tier has to be load-bearing, not decorative."""
    action, why = agent._fallback_action(
        "payments-service latency is climbing",
        [
            _mem("m-low", "unconfirmed", "scale_up"),
            _mem("m-high", "verified", "restart_service"),
            _mem("m-mid", "attested", "open_incident"),
        ],
    )
    assert action == "restart_service"
    assert "m-high" in why


def test_an_unconfirmed_memory_cannot_override_the_keyword_table():
    action, why = agent._fallback_action(
        "payments-service latency is climbing",
        [_mem("m-unchecked", "unconfirmed", "restart_service")],
    )
    assert action == "scale_up"
    assert "m-unchecked" not in why
    assert "keyword table" in why


def test_an_attested_memory_is_advisory_and_requires_approval():
    action, why, requires_approval, cited = agent._fallback_decision(
        "unusual behavior detected",
        [_mem("m-attested", "attested", "restart_service")],
    )
    assert action == "restart_service"
    assert requires_approval is True
    assert cited == ["m-attested"]
    assert "held for human approval" in why


def test_only_verified_memories_are_sent_to_a_reasoning_model(monkeypatch):
    seen = {}

    class Provider:
        @staticmethod
        def converse(**kwargs):
            seen["prompt"] = kwargs["messages"][0]["content"][0]["text"]
            return {
                "output": {"message": {"content": [{"toolUse": {
                    "name": "propose_action",
                    "input": {
                        "action": "page_oncall",
                        "rationale": "The verified memory supports paging.",
                        "cited_memory_ids": ["m-verified"],
                    },
                }}]}}
            }

    monkeypatch.setattr(agent, "_providers", lambda: [("test:model", Provider)])
    out = agent.propose(
        "payments-service is down",
        [
            _mem("m-verified", "verified", "page_oncall"),
            _mem("m-attested", "attested", "restart_service"),
            _mem("m-unconfirmed", "unconfirmed", "scale_up"),
        ],
    )
    assert "m-verified" in seen["prompt"]
    assert "m-attested" not in seen["prompt"]
    assert "m-unconfirmed" not in seen["prompt"]
    assert out["cited_memory_ids"] == ["m-verified"]


def test_attested_only_model_context_is_advisory_and_held(monkeypatch):
    seen = {}

    class Provider:
        @staticmethod
        def converse(**kwargs):
            seen["prompt"] = kwargs["messages"][0]["content"][0]["text"]
            return {
                "output": {"message": {"content": [{"toolUse": {
                    "name": "propose_action",
                    "input": {
                        "action": "restart_service",
                        "rationale": "The attested memory supports an advisory restart.",
                        "cited_memory_ids": ["m-attested"],
                    },
                }}]}}
            }

    monkeypatch.setattr(agent, "_providers", lambda: [("test:model", Provider)])
    out = agent.propose(
        "payments-service is unhealthy",
        [
            _mem("m-attested", "attested", "restart_service"),
            _mem("m-unconfirmed", "unconfirmed", "scale_up"),
        ],
    )

    assert "m-attested" in seen["prompt"]
    assert "m-unconfirmed" not in seen["prompt"]
    assert out["cited_memory_ids"] == ["m-attested"]
    assert out["requires_approval"] is True


def test_a_disputed_memory_never_steers_the_action():
    """A memory whose outcome was a rollback must not be able to win on proximity alone."""
    action, why = agent._fallback_action(
        "payments-service latency is climbing",
        [_mem("m-bad", "disputed", "restart_service")],
    )
    assert action == "scale_up", "disputed memory must be ignored, keyword table applies"
    assert "m-bad" not in why


def test_the_reason_is_specific_enough_to_audit():
    """'The memory told me to' is only useful if you can go and read that memory."""
    _, why = agent._fallback_action(
        "anything at all", [_mem("m-42", "verified", "page_oncall")]
    )
    assert "m-42" in why
    assert "No model was consulted" in why


def test_a_memory_naming_no_action_falls_through_cleanly():
    """Most memories are facts, not remediations; they must not hijack the decision."""
    fact = {
        "memory_id": "m-fact",
        "trust_tier": "verified",
        "entity": "payments-service",
        "attribute_key": "reads_from_table",
        "attribute_value": "orders_v2",
        "content": "payments-service reads from orders_v2 per the db-failover runbook.",
    }
    action, why = agent._fallback_action("payments-service is down", [fact])
    assert action == "page_oncall", "should fall through to the keyword table"
    assert "m-fact" not in why
