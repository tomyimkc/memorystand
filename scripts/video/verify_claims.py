#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Verify claim discipline for the MemoryStand demo video's narration.

Checks the actual spoken narration -- pulled from ``docs/demo/VIDEO_PLAN.md``
(its blockquoted ``**Narration (...):**`` sections, if it uses that
convention), its companion ``docs/demo/video-timeline.json`` (this project's
real pre-render per-scene cue text), and the rendered ``.srt`` once it
exists -- against three rules:

  1. FORBIDDEN phrases must never appear: "multi-region", "datacenter"/
     "datacentre" failover, "production-ready"/"production-validated",
     "validated", "AGI", "guarantees", "always", "never fails", and the
     house-style-banned "quarantine"/"supersede"/"belief state".
  2. REQUIRED disclosures must appear somewhere: the stub-embedding
     disclosure, the prior-art concession, and the zero-model-calls phrase.
  3. Every numeric claim in the narration (e.g. "76.5x", "6.87ms", "101")
     must be traceable to a real artifact -- cross-checked against
     ``benchmarks/*.md`` and any ``*.json`` receipt under
     ``artifacts/video/capture/``.

Deliberately never scans a plan doc's whole prose for forbidden phrases: a
plan's own "Narration and claim discipline" section legitimately *names*
these words while describing the rule (e.g. "never say multi-region"), and
scanning that prose would flag the rule's own definition as a violation.
Only actual spoken-cue text is scanned.

This is pattern matching, not semantic understanding -- it cannot tell an
honest negation ("this is NOT a datacentre failure test") from an overclaim
that happens to use safer surrounding words. The forbidden words are banned
outright on purpose: narration that needs to make an honest disclaimer
about one of these concepts should do it without the banned word itself
(e.g. "not a facility outage" instead of "not a datacentre failure").

Writes ``artifacts/video/claims-receipt.json`` and exits non-zero, printing
which rule failed and where, on any violation. Exits 2 (not 0 or 1) if
nothing has been authored/rendered yet -- there is nothing to verify, which
is different from verifying and finding it clean.

Usage:
    python3 scripts/video/verify_claims.py [--plan PATH] [--timeline PATH] [--srt PATH]
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import video_common  # noqa: E402

REPO_ROOT = video_common.REPO_ROOT

# ---------------------------------------------------------------------------
# Rule 1: forbidden phrases
# ---------------------------------------------------------------------------

FORBIDDEN_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("multi-region", re.compile(r"multi[- ]region", re.IGNORECASE)),
    (
        "datacenter/datacentre failover",
        re.compile(
            r"data\s*cent(?:er|re)(?:\s+\S+){0,3}?\s+failover"
            r"|failover(?:\s+\S+){0,3}?\s+data\s*cent(?:er|re)",
            re.IGNORECASE,
        ),
    ),
    ("production-ready", re.compile(r"production[- ]read(?:y|iness)", re.IGNORECASE)),
    ("production-validated", re.compile(r"production[- ]validated", re.IGNORECASE)),
    ("validated", re.compile(r"\bvalidated\b", re.IGNORECASE)),
    ("AGI", re.compile(r"\bAGI\b", re.IGNORECASE)),
    ("guarantees", re.compile(r"\bguarantees?\b", re.IGNORECASE)),
    ("always", re.compile(r"\balways\b", re.IGNORECASE)),
    ("never fails", re.compile(r"never\s+fails?", re.IGNORECASE)),
    ("quarantine", re.compile(r"\bquarantine[sd]?\b", re.IGNORECASE)),
    ("supersede", re.compile(r"\bsupersede[sd]?\b", re.IGNORECASE)),
    ("belief state", re.compile(r"belief\s+state", re.IGNORECASE)),
]

# ---------------------------------------------------------------------------
# Rule 2: required disclosures
# ---------------------------------------------------------------------------

REQUIRED_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "stub-embedding disclosure",
        re.compile(r"stub\w*.{0,80}embedd\w*|embedd\w*.{0,80}stub\w*", re.IGNORECASE),
    ),
    (
        "prior-art concession",
        re.compile(r"\bprior art\b|\bnot novel\b|\bnot new\b|\bzep\b|\bgraphiti\b|\bmem0\b", re.IGNORECASE),
    ),
    (
        "zero model calls on the promotion path",
        re.compile(r"zero model calls|model calls[^.]{0,40}?\b0\b|\b0 model calls\b", re.IGNORECASE),
    ),
]

# ---------------------------------------------------------------------------
# Rule 3: numeric claims must trace to a real artifact
# ---------------------------------------------------------------------------

# Matches "76.5x", "6.87ms", "250k", "101", "4500", "4,500" -- a maximal run
# of digits (either comma-grouped -- 1-3 digits then one or more ",DDD"
# groups -- or a plain uninterrupted run, so bare 4+ digit numbers like
# "4500" are not silently missed just because they have no thousands
# comma) with an optional unit suffix directly attached. Not embedded
# inside a larger word/number on the left (the lookbehind), but trailing
# text is not required to be a boundary: a number glued to an unrecognised
# suffix (e.g. "4,500s" for seconds) still yields the number, not nothing.
_NUMBER_TOKEN_RE = re.compile(
    r"(?<![\w.])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(x|ms|k|%)?",
    re.IGNORECASE,
)


def extract_numeric_claims(text: str) -> list[str]:
    """Return the numeric tokens in ``text`` worth cross-checking against an artifact.

    Skips bare single-digit numbers with no unit and no decimal point (e.g.
    the "3" in "a 3-node cluster", or a stray digit in a version string) --
    these are overwhelmingly narrative filler, not a fact a judge would
    check, and including them would drown real findings in noise. Does not
    recognise numbers spelled as words ("zero", "twelve") -- narration that
    spells out a figure in words is outside this heuristic's reach.
    """
    claims: list[str] = []
    for match in _NUMBER_TOKEN_RE.finditer(text):
        digits, unit = match.group(1), match.group(2) or ""
        bare_digits = digits.replace(",", "")
        if not unit and "." not in bare_digits and len(bare_digits) <= 1:
            continue
        claims.append(match.group(0))
    return claims


_THOUSANDS_GROUP_RE = re.compile(r"\d{1,3}(?:,\d{3})+")


def _normalize_haystack(text: str) -> str:
    """Strip thousands separators and lowercase, so "250,000" and "4,500s" match "250000"/"4500s".

    Deliberately does not require a boundary after the digits (unlike a
    naive ``(?<=\\d),(?=\\d{3}\\b)`` lookaround) -- this project's own
    benchmarks write figures like "4,500s" with a unit letter glued
    directly on, and a boundary requirement would leave that comma
    unstripped.
    """
    return _THOUSANDS_GROUP_RE.sub(lambda m: m.group(0).replace(",", ""), text).lower()


def numeric_claim_in_haystack(claim: str, haystack: str) -> bool:
    """True if ``claim`` (e.g. "76.5x", "250k") is traceable to ``haystack``.

    Matches the literal token, the bare digits without their unit (so a
    narration's "76.5x" matches an artifact's more precise "76.54x", since
    76.5 is a true prefix of 76.54), and -- for a "k" suffix -- the
    thousands-expanded form ("250k" also matches an artifact's "250000").
    """
    digits_match = re.match(r"(\d{1,3}(?:,\d{3})*(?:\.\d+)?)([a-z%]*)", claim, re.IGNORECASE)
    if digits_match is None:
        return False
    digits = digits_match.group(1).replace(",", "")
    unit = digits_match.group(2).lower()
    candidates = {f"{digits}{unit}".lower(), digits}
    if unit == "k":
        try:
            scaled = float(digits) * 1000
        except ValueError:
            scaled = None
        if scaled is not None:
            candidates.add(str(int(scaled)) if scaled.is_integer() else str(scaled))
    return any(candidate and candidate in haystack for candidate in candidates)


def load_benchmark_haystack(repo_root: Path) -> tuple[str, list[Path]]:
    """Concatenate benchmarks/*.md and any capture receipt JSON into one search haystack."""
    sources = sorted((repo_root / "benchmarks").glob("*.md"))
    capture_dir = repo_root / "artifacts" / "video" / "capture"
    if capture_dir.is_dir():
        sources.extend(sorted(capture_dir.glob("*.json")))
    text = "\n".join(p.read_text(encoding="utf-8") for p in sources)
    return _normalize_haystack(text), sources


# ---------------------------------------------------------------------------
# Rule checks
# ---------------------------------------------------------------------------


def find_forbidden(source_label: str, text: str) -> list[dict[str, str]]:
    """Return one finding per forbidden-phrase occurrence in ``text``."""
    findings: list[dict[str, str]] = []
    for label, pattern in FORBIDDEN_PATTERNS:
        for match in pattern.finditer(text):
            start = max(match.start() - 30, 0)
            end = min(match.end() + 30, len(text))
            findings.append(
                {
                    "source": source_label,
                    "rule": f"forbidden phrase: {label}",
                    "snippet": text[start:end].strip(),
                }
            )
    return findings


def find_missing_required(combined_text: str) -> list[str]:
    """Return the labels of any required disclosure not found anywhere in ``combined_text``."""
    return [label for label, pattern in REQUIRED_PATTERNS if not pattern.search(combined_text)]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--plan",
        type=Path,
        default=REPO_ROOT / "docs" / "demo" / "VIDEO_PLAN.md",
    )
    parser.add_argument(
        "--timeline",
        type=Path,
        default=None,
        help="cue-text companion JSON; defaults to video-timeline.json next to --plan",
    )
    parser.add_argument("--srt", type=Path, default=None, help="defaults to the newest artifacts/video/*.srt")
    args = parser.parse_args()

    sources: list[tuple[str, str]] = []  # (label, text) pairs scanned for forbidden phrases
    narration_parts: list[str] = []  # combined text scanned for required concepts / numbers

    if args.plan.is_file():
        plan_text = args.plan.read_text(encoding="utf-8")
        plan_blocks = video_common.extract_plan_narration_blocks(plan_text)
        if plan_blocks:
            joined = " ".join(plan_blocks)
            sources.append((f"{video_common.display_path(args.plan)} (narration blockquotes)", joined))
            narration_parts.append(joined)
    else:
        print(f"note: no plan doc at {video_common.display_path(args.plan)} yet", file=sys.stderr)

    timeline_path = args.timeline or (args.plan.parent / "video-timeline.json")
    if timeline_path.is_file():
        cues = video_common.extract_timeline_cues(timeline_path)
        if cues:
            joined = " ".join(cues)
            sources.append((video_common.display_path(timeline_path), joined))
            narration_parts.append(joined)
    else:
        print(f"note: no timeline companion at {video_common.display_path(timeline_path)} (optional)", file=sys.stderr)

    srt_path = args.srt
    if srt_path is None:
        video_dir = REPO_ROOT / "artifacts" / "video"
        candidates = sorted(video_dir.glob("*.srt")) if video_dir.is_dir() else []
        srt_path = candidates[-1] if candidates else None
    if srt_path is not None and srt_path.is_file():
        cues = video_common.parse_srt(srt_path)
        joined = " ".join(cue.text for cue in cues)
        sources.append((video_common.display_path(srt_path), joined))
        narration_parts.append(joined)
    else:
        print("note: no rendered .srt found yet", file=sys.stderr)

    if not sources:
        print(
            "Nothing to verify yet: no plan-doc narration blockquotes, no video-timeline.json "
            "cues, and no rendered .srt exist. Author the narration or render the video first."
        )
        return 2

    findings: list[dict[str, str]] = []
    for label, text in sources:
        findings.extend(find_forbidden(label, text))

    combined_narration = " ".join(narration_parts)
    for label in find_missing_required(combined_narration):
        findings.append(
            {"source": "combined narration", "rule": f"missing required disclosure: {label}", "snippet": ""}
        )

    haystack, haystack_sources = load_benchmark_haystack(REPO_ROOT)
    seen_claims: set[str] = set()
    for claim in extract_numeric_claims(combined_narration):
        key = claim.lower()
        if key in seen_claims:
            continue
        seen_claims.add(key)
        if not numeric_claim_in_haystack(claim, haystack):
            findings.append(
                {
                    "source": "combined narration",
                    "rule": (
                        f"numeric claim {claim!r} not found in any of: "
                        f"{[video_common.display_path(p) for p in haystack_sources] or '(no artifacts present)'}"
                    ),
                    "snippet": claim,
                }
            )

    status = "PASS" if not findings else "FAIL"
    receipt: dict[str, Any] = {
        "status": status,
        "checkedAtUtc": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "sourcesChecked": [label for label, _ in sources],
        "artifactSourcesForNumbers": [video_common.display_path(p) for p in haystack_sources],
        "findings": findings,
    }
    receipt_dir = REPO_ROOT / "artifacts" / "video"
    receipt_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = receipt_dir / "claims-receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(json.dumps(receipt, indent=2, sort_keys=True))
    if findings:
        print(f"\nFAIL: {len(findings)} claim-discipline finding(s); receipt: {receipt_path}", file=sys.stderr)
        for finding in findings:
            print(f"  - [{finding['source']}] {finding['rule']}", file=sys.stderr)
        return 1
    print(f"\nPASS: claim discipline holds across {len(sources)} source(s); receipt: {receipt_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
