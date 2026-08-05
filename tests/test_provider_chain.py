# SPDX-License-Identifier: Apache-2.0
"""Two reasoning providers, one parsing path, and an honest label on the result.

Bedrock is preferred and stays first: it is the AWS service this project is built around, and
the moment its quota is granted it should take over with no code change. Anthropic is the
standby that makes the agent actually reason today, because every Bedrock inference quota on
this account is 0 and the ones that matter are not adjustable.

What these tests protect:
  * the adapter really does hand back Bedrock's response shape, so agent.py keeps ONE parser
  * reasoning_source names the provider that ANSWERED, never the one that was configured
  * adding a second model provider did not make a model reachable from the promotion path
"""

from __future__ import annotations

import pytest

from backend import agent, anthropic_client, bedrock_client, trust


def _tool_response(action="restart_service", rationale="because the memory says so"):
    """An Anthropic Messages response carrying a tool_use block."""
    return {
        "content": [
            {"type": "tool_use", "name": "propose_action",
             "input": {"action": action, "rationale": rationale, "cited_memory_ids": ["m-1"]}}
        ]
    }


def test_the_adapter_returns_bedrocks_shape():
    """The whole reason this module is an adapter: agent.py must not learn a second format."""
    shaped = anthropic_client._to_bedrock_response(_tool_response())
    blocks = shaped["output"]["message"]["content"]
    assert blocks[0]["toolUse"]["name"] == "propose_action"
    assert blocks[0]["toolUse"]["input"]["action"] == "restart_service"


def test_tools_are_translated_into_anthropics_schema():
    converted = anthropic_client._to_anthropic_tools([agent._PROPOSE_ACTION_TOOL])
    assert converted[0]["name"] == "propose_action"
    assert "input_schema" in converted[0]
    assert converted[0]["input_schema"]["properties"]["action"]["enum"]


def test_bedrock_is_tried_first(monkeypatch):
    """Preference order is load-bearing: quota landing must silently restore Bedrock."""
    monkeypatch.setattr(anthropic_client, "available", lambda: True)
    names = [name for name, _ in agent._providers()]
    assert names[0].startswith("bedrock:")
    assert any(n.startswith("anthropic:") for n in names)


def test_a_provider_with_no_key_is_skipped_not_attempted(monkeypatch):
    """Skipping beats timing out. A deployment with no key must not pay a network round trip
    on every request to rediscover that."""
    monkeypatch.setattr(anthropic_client, "available", lambda: False)
    assert [n for n, _ in agent._providers() if n.startswith("anthropic:")] == []


def test_the_source_names_the_provider_that_actually_answered(monkeypatch):
    """Never the configured one. A reader has to be able to tell what decided this."""
    monkeypatch.setattr(anthropic_client, "available", lambda: True)

    def bedrock_down(*a, **k):
        raise bedrock_client.ModelUnavailable("quota is zero")

    monkeypatch.setattr(bedrock_client, "converse", bedrock_down)
    monkeypatch.setattr(
        anthropic_client, "converse",
        lambda **k: anthropic_client._to_bedrock_response(_tool_response("scale_up")),
    )

    out = agent.propose("payments-service latency climbing", [])
    assert out["reasoning_source"].startswith("anthropic:")
    assert out["action"] == "scale_up"
    assert out["model_calls"] >= 0


def test_every_provider_failing_falls_back_to_memory(monkeypatch):
    """The fallback must still be reachable, and must still say so plainly."""
    monkeypatch.setattr(anthropic_client, "available", lambda: False)

    def down(*a, **k):
        raise bedrock_client.ModelUnavailable("quota is zero")

    monkeypatch.setattr(bedrock_client, "converse", down)
    out = agent.propose(
        "payments-service latency climbing",
        [{"memory_id": "m-9", "trust_tier": "verified", "attribute_key": "remediation",
          "attribute_value": "restart_service", "content": "restart_service"}],
    )
    assert out["reasoning_source"] == "fallback_memory"
    assert out["action"] == "restart_service"
    assert out["model_calls"] == 0


def test_a_missing_key_raises_the_shared_unavailable_type(monkeypatch):
    """One exception type, so the fallback has one trigger to handle rather than two."""
    monkeypatch.setattr(anthropic_client, "_load_key", lambda: None)
    with pytest.raises(bedrock_client.ModelUnavailable):
        anthropic_client.converse("s", [{"role": "user", "content": [{"text": "hi"}]}])


def test_adding_a_second_provider_did_not_reach_the_promotion_path():
    """The guard was written before this module existed and must still catch it.

    That is the point of enforcing an invariant structurally rather than documenting it: a
    change made months later, by someone not thinking about trust.py at all, is still checked.
    """
    trust.assert_no_model_calls()
    import backend.trust as t
    assert not hasattr(t, "anthropic_client")
