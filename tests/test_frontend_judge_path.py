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
        "Trust beats proximity.",
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
