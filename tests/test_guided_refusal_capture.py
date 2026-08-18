# SPDX-License-Identifier: Apache-2.0
"""The film capture must fail closed unless it proves the exact refusal story."""

from __future__ import annotations

from pathlib import Path

from scripts.video import capture_guided_refusal
from scripts.video import build_presenter_receipts


def test_capture_checks_the_action_and_empty_authority_sets() -> None:
    source = Path(capture_guided_refusal.__file__).read_text(encoding="utf-8")
    assert 'payload.get("action") != "scale_up"' in source
    assert "the wrong-service refusal must have no action-eligible memories" in source
    assert "the wrong-service refusal must cite no memory" in source
    assert "time-travel receipt unexpectedly reconstructed an eligible memory" in source
    assert "time-travel receipt made the wrong-service row eligible" in source
    assert "time-travel receipt did not preserve the wrong-service entity exclusion" in source
    assert "replay_cited" not in source


def test_receipt_builder_rechecks_the_same_claims() -> None:
    source = Path(build_presenter_receipts.__file__).read_text(encoding="utf-8")
    assert 'decision.get("action") != "scale_up"' in source
    assert "guided refusal unexpectedly contains an eligible memory" in source
    assert "guided refusal unexpectedly cites a memory" in source
    assert "time-travel receipt unexpectedly reconstructed an eligible memory" in source
    assert "time-travel receipt made the wrong-service row eligible" in source
    assert '"wrong row eligible"' in source
    assert '"wrong row cited"' not in source
