# SPDX-License-Identifier: Apache-2.0
"""Static regression checks for the judge-facing web experience."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
APP = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")


def test_judge_path_leads_with_the_causal_explanation() -> None:
    required = [
        "Memory that must prove it worked.",
        "Alert stopped ≠ restart worked",
        "223 ms → 220 ms",
        "Seeded decision-rule example · not production CloudWatch data",
        "Proof must match the service.",
    ]
    for text in required:
        assert text in INDEX


def test_guided_path_exercises_real_routes_and_preserves_claim_limits() -> None:
    for text in [
        'apiGet("/recall"',
        'apiPost("/decide"',
        'apiGet("/timemachine"',
        "held for human approval",
        "model calls",
        "matches words, not meaning",
        "trust promotion itself uses zero model calls",
        "unexpected live result",
        "this page will not relabel it as proof",
        "receipt incomplete",
    ]:
        assert text.lower() in (INDEX + APP).lower()


def test_advanced_operator_controls_remain_available_without_leading_the_page() -> None:
    assert '<details class="advanced">' in INDEX
    for element_id in [
        "form-decide",
        "form-ingest",
        "form-confirm",
        "form-timemachine",
        "tenantId",
        "agentId",
        "sharedSecret",
    ]:
        assert f'id="{element_id}"' in INDEX


def test_every_html_id_is_unique() -> None:
    ids = re.findall(r'\bid="([^"]+)"', INDEX)
    assert len(ids) == len(set(ids))


def test_space_builder_declares_static_sdk() -> None:
    builder = (ROOT / "scripts" / "build_hf_space.py").read_text(encoding="utf-8")
    assert "sdk: static" in builder
    assert 'FRONTEND / "index.html"' in builder
    assert 'FRONTEND / "app.js"' in builder
    assert "delete_patterns=stale or None" in builder
    assert "parent_commit=info.sha" in builder


def test_live_status_and_expected_results_are_not_hardcoded_as_current_facts() -> None:
    assert 'id="heroStatusText">Checking the live CockroachDB-backed demo…' in INDEX
    assert "Expected closer memory" in INDEX
    assert "Expected wrong-service row" in INDEX
    assert "Expected policy" in INDEX
    assert '<span>Policy</span><strong>Risky action held</strong>' not in INDEX
    assert "expectedTrustOverrule" in APP
    assert "receiptMatchesExpected" in APP
    assert 'data.model_calls === 0' in APP
    assert 'decision.query_text === GUIDED_QUERY' in APP
    assert 'decision.decision_id === expectedDecisionId' in APP
    assert 'data.target_entity === GUIDED_ENTITY' in APP
    assert 'eligibleIds.length === 0' in APP
    assert 'citedIds.length === 0' in APP
    assert '!data.policy_note' in APP
    assert 'eligibleAsOf.length === 0' in APP
    assert 'var GUIDED_ENTITY = "payments-service"' in APP
    assert "matchesGuidedEntity(pair.restart)" in APP
    assert "matchesGuidedEntity(chosen)" in APP
    assert "matchesGuidedEntity(pair.verified)" in APP
    assert "wrong service — proof refused" in APP
    assert "policy failure — wrong service cited" in APP
    assert "This response is unsafe and is shown as a failure, not proof." in APP
    assert "target_entity: GUIDED_ENTITY" in APP
    assert 'id="decideTargetEntity"' in INDEX
    assert "subject policy enforced" in APP
    assert "Target service is required when MemoryStand selects the action." in APP
    assert "interactive demo ready" in APP.lower()
    assert "database unavailable" in APP.lower()
    assert "demo credential unavailable" in APP.lower()


def test_optional_typed_task_fields_are_labeled_as_uuids() -> None:
    assert "Task / incident UUID" in INDEX
    assert 'placeholder="inc-4471"' not in INDEX
    assert "validateOptionalUuid" in APP
    assert "must be a UUID or left blank" in APP


def test_inline_favicon_avoids_a_useless_network_404() -> None:
    assert '<link rel="icon" href="data:image/svg+xml,' in INDEX
