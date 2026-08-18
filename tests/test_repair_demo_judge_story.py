# SPDX-License-Identifier: Apache-2.0
"""The demo repair utility must audit receipts, not manufacture authority."""

from __future__ import annotations

from pathlib import Path

from scripts import repair_demo_judge_story as repair


def test_guided_scale_up_requires_the_exact_entity_and_attribute() -> None:
    valid = {
        "entity": "payments-service",
        "attribute_key": "remediation",
        "attribute_value": "scale_up",
        "trust_tier": "verified",
    }
    assert repair._is_guided_scale_up(valid)
    assert not repair._is_guided_scale_up({**valid, "entity": "checkout-api"})
    assert not repair._is_guided_scale_up({**valid, "attribute_key": "video_demo_3"})
    assert not repair._is_guided_scale_up({**valid, "trust_tier": "attested"})


def test_repair_script_cannot_insert_or_rewrite_a_verified_claim() -> None:
    source = Path(repair.__file__).read_text(encoding="utf-8")
    assert "INSERT INTO agent_memories" not in source
    assert "SET content =" not in source
    assert "memory.recall(tenant_id, DEMO_AGENT, query, k=20)" in source
