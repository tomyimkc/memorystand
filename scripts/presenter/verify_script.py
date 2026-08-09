#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Fail closed when the public presenter script drifts outside its claim or pacing contract."""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT_JSON = REPO_ROOT / "docs" / "demo" / "presenter-script.json"

WORDS_MIN = 17
WORDS_MAX = 24
EXPECTED_BEATS = 7
EXPECTED_TOTAL_SHOTS = 12
MIN_SHOTS_PER_BEAT = 1
MAX_SHOTS_PER_BEAT = 2
SUPPORTED_PANEL_KINDS = {
    "admission",
    "callout",
    "close",
    "hero",
    "oracle",
    "quote",
    "recall",
    "schema",
    "story",
    "table",
}

REQUIRED_SPOKEN_PHRASES = (
    "receipt before it gets the keys",
    "cockroachdb",
    "aws",
    "cloudwatch",
    "five hundred forty attack",
    "sixty honest controls",
    "zero model calls",
    "fixed fallback",
)

BANNED_PATTERNS = {
    "unsupported AGI claim": r"\b(?:is|achieved|proves?)\s+(?:an?\s+)?agi\b",
    "unsupported production validation": r"\bproduction[- ]validated\b",
    "unsupported multi-region claim": r"\bmulti[- ]region\b",
    "unsupported semantic-search claim": r"\bsemantic (?:search|embedding|retrieval)\b",
    "stale model-use claim": r"live /decide also calls a model",
    "overbroad competitor claim": r"\bevery other agent memory\b",
}


def validate(spec: dict) -> list[str]:
    errors: list[str] = []
    beats = spec.get("beats", [])

    if spec.get("candidateOnly") is not True:
        errors.append("candidateOnly must remain true")
    if spec.get("canClaimAGI") is not False:
        errors.append("canClaimAGI must remain false")
    if spec.get("subtitles") is not True:
        errors.append("subtitles must remain enabled")
    if not 1 <= int(spec.get("targetDurationSeconds", 0)) < 180:
        errors.append("targetDurationSeconds must be between 1 and 179")
    if len(beats) != EXPECTED_BEATS:
        errors.append(f"expected {EXPECTED_BEATS} beats, found {len(beats)}")

    ids: set[str] = set()
    all_spoken: list[str] = []
    for beat in beats:
        beat_id = beat.get("id", "<missing>")
        if beat_id in ids:
            errors.append(f"duplicate beat id: {beat_id}")
        ids.add(beat_id)

        shots = beat.get("shots", [])
        if not MIN_SHOTS_PER_BEAT <= len(shots) <= MAX_SHOTS_PER_BEAT:
            errors.append(
                f"{beat_id}: expected {MIN_SHOTS_PER_BEAT}-{MAX_SHOTS_PER_BEAT} "
                f"shots, found {len(shots)}"
            )
        for index, line in enumerate(shots):
            words = len(line.split())
            if not WORDS_MIN <= words <= WORDS_MAX:
                errors.append(
                    f"{beat_id}-{index}: {words} words; expected {WORDS_MIN}-{WORDS_MAX}"
                )
            all_spoken.append(line)

        panel_kind = beat.get("panelData", {}).get("kind")
        if panel_kind not in SUPPORTED_PANEL_KINDS:
            errors.append(f"{beat_id}: unsupported panel kind {panel_kind!r}")
        if beat.get("presenterSide") not in {"LEFT", "RIGHT"}:
            errors.append(f"{beat_id}: presenterSide must be LEFT or RIGHT")

    spoken = " ".join(all_spoken).lower()
    if len(all_spoken) != EXPECTED_TOTAL_SHOTS:
        errors.append(
            f"expected {EXPECTED_TOTAL_SHOTS} total shots, found {len(all_spoken)}"
        )
    for phrase in REQUIRED_SPOKEN_PHRASES:
        if phrase not in spoken:
            errors.append(f"required spoken phrase missing: {phrase!r}")
    for label, pattern in BANNED_PATTERNS.items():
        if re.search(pattern, spoken, flags=re.IGNORECASE):
            errors.append(label)

    outro = spec.get("outro", {})
    if "synthetic presenter" not in outro.get("disclosure", "").lower():
        errors.append("outro must disclose the synthetic presenter")
    if "github.com/tomyimkc/memorystand" not in outro.get("repository", ""):
        errors.append("outro must show the public repository")

    return errors


def main() -> int:
    spec = json.loads(SCRIPT_JSON.read_text())
    errors = validate(spec)
    if errors:
        print("Presenter script verification FAILED:")
        for error in errors:
            print(f"  - {error}")
        return 1

    shot_count = sum(len(beat["shots"]) for beat in spec["beats"])
    print(
        "Presenter script verification PASSED: "
        f"{len(spec['beats'])} beats, {shot_count} shots, "
        f"target {spec['targetDurationSeconds']}s, subtitles enabled"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
