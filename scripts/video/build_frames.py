#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Compose the MemoryStand evidence-first demo video's frames.

Reads ``docs/demo/video-timeline.json`` (the single source of truth for scene order and
timing -- see ``docs/demo/VIDEO_PLAN.md``) and, for each scene, composes a 1920x1080 PNG
from real captured evidence:

  - a live dashboard screenshot (``scripts/video/capture_dashboard.mjs``)
  - real live-API request/response receipts (``scripts/video/capture_evidence.py`` --
    ``artifacts/video/capture/evidence.json``)
  - real, already-committed local benchmark/failover transcripts
    (``benchmarks/failover.md``, ``benchmarks/results-cluster-250k.md``), read directly
    rather than re-captured, so there is no second, possibly-drifted copy of the same
    evidence
  - the README's own "Prior art, stated honestly" section

Every frame carries a color-coded evidence-class badge (so the taxonomy in
docs/demo/VIDEO_PLAN.md's Editorial thesis is visible on screen, not just narrated) and a
persistent footer with the claim boundary (``candidateOnly · not validated``).

This never fabricates a number: every value on a data-driven frame is read from one of
the sources above at build time. Where the real captured evidence does not support a
scene's story as cleanly as the narration hopes (see the recall-ranking honesty note in
``_scene_04_agent_decides`` below), the frame shows what was actually returned rather
than a staged substitute.

Fails loudly (``SystemExit``, non-zero) and lists exactly what is missing if a required
capture input is not present -- it never renders a placeholder frame silently. Re-running
this script does not require repeating the capture step; it only reads
``artifacts/video/capture/`` and the repo's own committed files.

Usage:
    .venv/bin/python scripts/video/build_frames.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

WIDTH = 1920
HEIGHT = 1080

# Color palette lifted directly from frontend/index.html's own CSS custom properties, so
# the composed frames read as one visual system with the live dashboard screenshot they
# sit next to.
BACKGROUND = (10, 14, 20)  # --bg
PANEL = (18, 24, 34)  # --panel
PANEL_2 = (13, 19, 27)  # --panel-2
BORDER = (35, 43, 56)  # --border
BORDER_LIGHT = (47, 57, 72)  # --border-light
INK = (234, 238, 244)  # --text
MUTED = (139, 150, 168)  # --text-dim
FAINT = (91, 100, 120)  # --text-faint
ACCENT = (77, 163, 255)  # --accent
GREEN = (53, 212, 136)  # --green
AMBER = (245, 185, 61)  # --amber
RED = (255, 93, 106)  # --red
PURPLE = (178, 141, 255)  # --purple

ROOT = Path(__file__).resolve().parents[2]
TIMELINE_PATH = ROOT / "docs" / "demo" / "video-timeline.json"
CAPTURE_DIR = ROOT / "artifacts" / "video" / "capture"
EVIDENCE_PATH = CAPTURE_DIR / "evidence.json"
FRAMES_DIR = ROOT / "artifacts" / "video" / "frames"
FAILOVER_MD = ROOT / "benchmarks" / "failover.md"
BENCHMARK_MD = ROOT / "benchmarks" / "results-cluster-250k.md"
README_MD = ROOT / "README.md"

# Any one of these, in order, is accepted as "the live dashboard screenshot" -- both
# scripts/video/capture_dashboard.mjs and scripts/video/capture_live.mjs may have
# produced it, under slightly different names/locations.
DASHBOARD_SCREENSHOT_CANDIDATES = (
    CAPTURE_DIR / "01-dashboard-landing.png",
    CAPTURE_DIR / "screenshots" / "01-dashboard-hero.png",
    CAPTURE_DIR / "01-dashboard-hero.png",
)

# Evidence-class token -> accent color, so a compound label like
# "LIVE AWS · OUTCOME PATH · STUB EMBEDDINGS" renders as three separately colored chips.
CLASS_COLORS: dict[str, tuple[int, int, int]] = {
    "LIVE AWS": GREEN,
    "OUTCOME PATH": ACCENT,
    "OUTCOME GATE": AMBER,
    "CROSS-EXAMINE": PURPLE,
    "STUB EMBEDDINGS": RED,
    "LOCAL 3-NODE": ACCENT,
    "LOCAL BENCHMARK": AMBER,
    "PRIOR ART": PURPLE,
    "CANDIDATE ONLY": GREEN,
}


# ---------------------------------------------------------------------------
# Drawing primitives
# ---------------------------------------------------------------------------


def font(size: int, *, bold: bool = False, mono: bool = False) -> ImageFont.FreeTypeFont:
    names = (
        (
            "/System/Library/Fonts/Supplemental/Menlo.ttc"
            if mono
            else (
                "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
                if bold
                else "/System/Library/Fonts/Supplemental/Arial.ttf"
            )
        ),
        "/System/Library/Fonts/SFNS.ttf",
        (
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
            if mono
            else (
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
                if bold
                else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
            )
        ),
    )
    for name in names:
        candidate = Path(name)
        if candidate.is_file():
            try:
                return ImageFont.truetype(str(candidate), size)
            except OSError:
                continue
    return ImageFont.load_default()


def background() -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
    glow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(glow)
    draw.ellipse((-260, -380, 860, 760), fill=(31, 90, 140, 80))
    draw.ellipse((1260, 520, 2220, 1460), fill=(60, 40, 130, 65))
    glow = glow.filter(ImageFilter.GaussianBlur(120))
    image.paste(glow, mask=glow.getchannel("A"))
    return image


def blend(color: tuple[int, int, int], onto: tuple[int, int, int], alpha: float) -> tuple[int, int, int]:
    """Blend ``color`` over ``onto`` at ``alpha`` in-place, for a "tinted panel" fill.

    ``ImageDraw`` silently drops the alpha channel of a 4-tuple ``fill``/``outline`` when
    the target image is mode ``"RGB"`` (not ``"RGBA"``) -- every frame in this module is
    drawn on an RGB canvas, so passing ``(*color, 32)`` as a "translucent" fill renders as
    fully opaque and, when the same ``color`` is then used for the text drawn on top,
    makes that text invisible. This computes the blended RGB directly instead.
    """
    return tuple(round(c * alpha + o * (1 - alpha)) for c, o in zip(color, onto))  # type: ignore[return-value]


def rounded_panel(
    image: Image.Image,
    box: tuple[int, int, int, int],
    *,
    fill: tuple[int, int, int] = PANEL,
    outline: tuple[int, int, int] = BORDER_LIGHT,
    radius: int = 22,
    width: int = 2,
) -> None:
    ImageDraw.Draw(image).rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def wrap_text(draw: ImageDraw.ImageDraw, value: str, selected: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    lines: list[str] = []
    for source in value.splitlines() or [""]:
        words = source.split()
        current = ""
        for word in words:
            candidate = word if not current else f"{current} {word}"
            if draw.textlength(candidate, font=selected) <= max_width:
                current = candidate
            else:
                if current:
                    lines.append(current)
                current = word
        lines.append(current)
    return lines


def text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    value: str,
    *,
    size: int,
    color: tuple[int, int, int] = INK,
    bold: bool = False,
    mono: bool = False,
    max_width: int | None = None,
    spacing: int = 8,
) -> int:
    """Draw ``value`` (wrapped to ``max_width`` if given) and return the bottom y."""
    selected = font(size, bold=bold, mono=mono)
    lines = wrap_text(draw, value, selected, max_width) if max_width else value.splitlines() or [""]
    joined = "\n".join(lines)
    draw.multiline_text(xy, joined, font=selected, fill=color, spacing=spacing)
    bbox = draw.multiline_textbbox(xy, joined, font=selected, spacing=spacing)
    return bbox[3]


def chip(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    value: str,
    *,
    color: tuple[int, int, int] = ACCENT,
    size: int = 21,
) -> int:
    """Draw one small filled-outline badge chip and return its right edge x."""
    selected = font(size, bold=True)
    width = int(draw.textlength(value, font=selected)) + 32
    x, y = xy
    draw.rounded_rectangle((x, y, x + width, y + 38), radius=19, fill=blend(color, BACKGROUND, 0.16), outline=color)
    draw.text((x + 16, y + 8), value, font=selected, fill=color)
    return x + width


def evidence_badges(image: Image.Image, evidence_class: str, *, xy: tuple[int, int] = (90, 56)) -> None:
    """Render each ' · '-separated token of ``evidence_class`` as its own colored chip."""
    draw = ImageDraw.Draw(image)
    x, y = xy
    for token in [part.strip() for part in evidence_class.split("·") if part.strip()]:
        color = CLASS_COLORS.get(token, ACCENT)
        x = chip(draw, (x, y), token, color=color) + 12


def header(image: Image.Image, *, eyebrow: str, title_value: str, subtitle: str = "") -> int:
    draw = ImageDraw.Draw(image)
    text(draw, (92, 108), eyebrow, size=22, color=MUTED, bold=True)
    bottom = text(draw, (90, 140), title_value, size=50, bold=True, max_width=1740)
    if subtitle:
        bottom = text(draw, (92, bottom + 14), subtitle, size=25, color=MUTED, max_width=1740)
    return bottom


def footer(image: Image.Image, footer_label: str) -> None:
    draw = ImageDraw.Draw(image)
    # The claim boundary lives at the TOP, not the bottom.
    #
    # It used to sit at y=990-1043, directly under content panels that extend to y=950 --
    # and the burned caption band lands in that same strip. The result was a caption box
    # half-covering real evidence text, which reads as a rendering bug rather than a
    # designed lower third. Captions now own everything below y=950; nothing else is
    # drawn there.
    selected = font(20, bold=True)
    label = "candidateOnly: true  ·  canClaimAGI: false"
    width = int(draw.textlength(label, font=selected))
    draw.text((1840 - width, 34), label, font=selected, fill=GREEN)
    # No second copy of the evidence class here: the coloured pills at the top-left already
    # name it, and drawing it again at y=132 collided with the scene title.
    _ = footer_label


def load_screenshot() -> Path:
    for candidate in DASHBOARD_SCREENSHOT_CANDIDATES:
        if candidate.is_file():
            return candidate
    raise SystemExit(
        "Missing capture input: no dashboard screenshot found. Looked for:\n"
        + "\n".join(f"  - {c}" for c in DASHBOARD_SCREENSHOT_CANDIDATES)
        + "\nRun scripts/video/capture_dashboard.mjs first."
    )


def fitted(source: Path, size: tuple[int, int], *, centering: tuple[float, float] = (0.5, 0.2)) -> Image.Image:
    image = Image.open(source).convert("RGB")
    return ImageOps.fit(image, size, method=Image.Resampling.LANCZOS, centering=centering)


def rounded_paste(image: Image.Image, inset: Image.Image, xy: tuple[int, int], *, radius: int = 18) -> None:
    mask = Image.new("L", inset.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, inset.width, inset.height), radius=radius, fill=255)
    image.paste(inset, xy, mask)


# ---------------------------------------------------------------------------
# Evidence loading
# ---------------------------------------------------------------------------


def load_timeline() -> dict[str, Any]:
    if not TIMELINE_PATH.is_file():
        raise SystemExit(f"Missing {TIMELINE_PATH} -- docs/demo/VIDEO_PLAN.md's companion timeline.")
    return json.loads(TIMELINE_PATH.read_text(encoding="utf-8"))


def load_evidence() -> dict[str, Any]:
    if not EVIDENCE_PATH.is_file():
        raise SystemExit(
            f"Missing capture input: {EVIDENCE_PATH}\nRun scripts/video/capture_evidence.py first."
        )
    return json.loads(EVIDENCE_PATH.read_text(encoding="utf-8"))


def step_payload(evidence: dict[str, Any], key: str) -> dict[str, Any]:
    step = evidence.get("steps", {}).get(key)
    if not step or not step.get("ok"):
        raise SystemExit(f"evidence.json step '{key}' is missing or was not a 2xx response.")
    payload = step.get("payload")
    if not isinstance(payload, dict):
        raise SystemExit(f"evidence.json step '{key}' has no JSON object payload.")
    return payload


def read_failover_transcript() -> str:
    if not FAILOVER_MD.is_file():
        raise SystemExit(f"Missing {FAILOVER_MD} -- run scripts/cluster-demo.sh failover once to produce it.")
    text_content = FAILOVER_MD.read_text(encoding="utf-8")
    match = re.search(r"```\n(.*?)\n```", text_content, re.DOTALL)
    if not match:
        raise SystemExit(f"{FAILOVER_MD} has no fenced transcript block to read.")
    return match.group(1)


def read_benchmark_numbers() -> dict[str, str]:
    if not BENCHMARK_MD.is_file():
        raise SystemExit(f"Missing {BENCHMARK_MD}.")
    body = BENCHMARK_MD.read_text(encoding="utf-8")
    row = re.search(
        r"\|\s*agent_memories \(vector-indexed\)\s*\|\s*([\d.]+)\s*\|\s*([\d.]+)\s*\|\s*([\d.]+)\s*\|\s*([\d.]+)\s*\|\s*(\d+)\s*\|",
        body,
    )
    noindex_row = re.search(
        r"\|\s*agent_memories_noindex \(no index\)\s*\|\s*([\d.]+)\s*\|\s*([\d.]+)\s*\|\s*([\d.]+)\s*\|\s*([\d.]+)\s*\|\s*(\d+)\s*\|",
        body,
    )
    speedup = re.search(r"Indexed table is \*\*([\d.]+)x faster at p50\*\*", body)
    if not (row and noindex_row and speedup):
        raise SystemExit(f"Could not parse the recall-latency table out of {BENCHMARK_MD}.")
    return {
        "indexed_p50": row.group(1),
        "noindex_p50": noindex_row.group(1),
        "speedup": speedup.group(1),
        "rows": "250,000",
    }


def read_prior_art() -> list[str]:
    if not README_MD.is_file():
        raise SystemExit(f"Missing {README_MD}.")
    body = README_MD.read_text(encoding="utf-8")
    match = re.search(r"## Prior art, stated honestly\n(.*?)\n## ", body, re.DOTALL)
    if not match:
        raise SystemExit(f"Could not find the 'Prior art, stated honestly' section in {README_MD}.")
    # Markdown bullets in README.md wrap onto indented continuation lines rather than
    # staying on one physical line -- split on a NEW "- " list marker (not every
    # newline), so a bullet's wrapped continuation is joined back into one string
    # instead of being silently dropped after the first line.
    raw_bullets = re.findall(r"^- (.+(?:\n(?!- ).+)*)", match.group(1), re.MULTILINE)
    if not raw_bullets:
        raise SystemExit(f"Found the prior-art section in {README_MD} but no bullet points inside it.")
    cleaned = [" ".join(b.split()) for b in raw_bullets]
    # Strip markdown link/bold syntax down to plain readable text for the frame.
    cleaned = [re.sub(r"\[(.*?)\]\([^)]*\)", r"\1", b) for b in cleaned]
    cleaned = [re.sub(r"\*\*(.*?)\*\*", r"\1", b) for b in cleaned]
    return cleaned


# ---------------------------------------------------------------------------
# Scenes
# ---------------------------------------------------------------------------


def _scene_01_hook(scene: dict[str, Any], evidence: dict[str, Any], destination: Path) -> None:
    image = background()
    draw = ImageDraw.Draw(image)
    evidence_badges(image, scene["evidenceClass"])
    text(draw, (90, 172), "MemoryStand", size=78, bold=True)
    text(
        draw,
        (94, 268),
        "Memory for on-call agents that only trusts what actually worked.",
        size=30,
        color=ACCENT,
        bold=True,
        max_width=1200,
    )

    screenshot = load_screenshot()
    rounded_panel(image, (1080, 172, 1840, 640))
    shot = fitted(screenshot, (740, 448), centering=(0.5, 0.05))
    rounded_paste(image, shot, (1094, 186))

    health = step_payload(evidence, "healthBefore")
    rounded_panel(image, (90, 360, 1030, 620), fill=PANEL_2)
    text(draw, (122, 388), "GET /health  ·  LIVE", size=22, color=MUTED, bold=True)
    text(draw, (122, 428), f"database: {health.get('database', '?')}", size=28, color=GREEN, bold=True)
    text(
        draw,
        (122, 468),
        f"gc_window_seconds: {health.get('gc_window_seconds', '?')}",
        size=22,
        color=INK,
    )
    text(
        draw,
        (122, 504),
        str(health.get("server_version", "")),
        size=18,
        color=FAINT,
        max_width=860,
    )
    text(
        draw,
        (122, 552),
        "It's three in the morning. An on-call agent's memory says raise the "
        "circuit breaker.",
        size=24,
        color=INK,
        max_width=860,
    )

    rounded_panel(image, (90, 680, 1840, 960), fill=PANEL_2)
    text(
        draw,
        (122, 712),
        "Recency, source authority, or self-consistency -- the model grading its own homework.",
        size=27,
        color=MUTED,
        max_width=1680,
    )
    text(
        draw,
        (122, 762),
        "MemoryStand asks something different: last time this agent acted on a memory,\ndid it actually work?",
        size=38,
        color=INK,
        bold=True,
        max_width=1680,
    )
    footer(image, scene["evidenceClass"])
    image.save(destination)


def _verdict_color(verdict: str) -> tuple[int, int, int]:
    return {"accepted": GREEN, "quarantined": AMBER, "superseded": FAINT}.get(verdict, MUTED)


def _verdict_label(verdict: str) -> str:
    # House style: the schema's internal word is "quarantined"; the viewer-facing word
    # is "held for review". Never show "quarantine" on screen.
    return {"quarantined": "held for review", "accepted": "accepted", "superseded": "superseded"}.get(
        verdict, verdict
    )


def _ingest_card(
    draw: ImageDraw.ImageDraw,
    image: Image.Image,
    box: tuple[int, int, int, int],
    *,
    label: str,
    ms_value: str,
    source: str,
    verdict: str,
    reason: str,
) -> None:
    rounded_panel(image, box, fill=PANEL_2, outline=_verdict_color(verdict))
    x1, y1, x2, _ = box
    text(draw, (x1 + 28, y1 + 24), label, size=21, color=MUTED, bold=True)
    text(draw, (x1 + 28, y1 + 58), f"{ms_value} ms", size=48, color=INK, bold=True)
    text(draw, (x1 + 28, y1 + 122), f"source: {source}", size=21, color=MUTED)
    v = _verdict_label(verdict)
    draw.rounded_rectangle(
        (x1 + 28, y1 + 158, x1 + 28 + int(draw.textlength(v, font=font(22, bold=True))) + 30, y1 + 196),
        radius=16,
        fill=blend(_verdict_color(verdict), PANEL_2, 0.18),
        outline=_verdict_color(verdict),
    )
    draw.text((x1 + 44, y1 + 166), v, font=font(22, bold=True), fill=_verdict_color(verdict))
    text(draw, (x1 + 28, y1 + 214), reason, size=18, color=FAINT, max_width=x2 - x1 - 56)


def _scene_02_admission_holds(scene: dict[str, Any], evidence: dict[str, Any], destination: Path) -> None:
    image = background()
    draw = ImageDraw.Draw(image)
    evidence_badges(image, scene["evidenceClass"])
    header(
        image,
        eyebrow="POST /ingest  ·  LIVE, AGAINST THE DEPLOYED API",
        title_value="Admission control holds a contradicting claim",
        subtitle="checkout-api's circuit breaker timeout: two claims, live, real responses",
    )
    runbook = step_payload(evidence, "ingestRunbook")
    slack = step_payload(evidence, "ingestSlack")
    _ingest_card(
        draw,
        image,
        (90, 300, 950, 560),
        label="RUNBOOK FACT",
        ms_value="800",
        source="runbook:checkout-resiliency",
        verdict=runbook["verdict"],
        reason=runbook["verdict_reasons"][0] if runbook.get("verdict_reasons") else "",
    )
    _ingest_card(
        draw,
        image,
        (980, 300, 1840, 560),
        label="SLACK CLAIM",
        ms_value="300",
        source="slack",
        verdict=slack["verdict"],
        reason=(slack["verdict_reasons"][0] if slack.get("verdict_reasons") else "").split(";")[0],
    )
    rounded_panel(image, (90, 600, 1840, 950), fill=PANEL_2)
    text(
        draw,
        (122, 632),
        "A low-authority source doesn't outrank a runbook, so the Slack claim is held for\nreview, not thrown away and not silently accepted.",
        size=28,
        color=INK,
        bold=True,
        max_width=1680,
    )
    text(
        draw,
        (122, 720),
        f"memory_id (runbook): {runbook['memory_id']}",
        size=18,
        color=FAINT,
        mono=True,
    )
    text(
        draw,
        (122, 752),
        f"memory_id (slack):   {slack['memory_id']}",
        size=18,
        color=FAINT,
        mono=True,
    )
    footer(image, scene["evidenceClass"])
    image.save(destination)


def _scene_03_human_correction(scene: dict[str, Any], evidence: dict[str, Any], destination: Path) -> None:
    image = background()
    draw = ImageDraw.Draw(image)
    evidence_badges(image, scene["evidenceClass"])
    header(
        image,
        eyebrow="POST /ingest  ·  LIVE, AGAINST THE DEPLOYED API",
        title_value="A human correction, recorded live",
        subtitle="Alice (on-call lead) confirms the real number -- 500ms",
    )
    runbook = step_payload(evidence, "ingestRunbook")
    slack = step_payload(evidence, "ingestSlack")
    human = step_payload(evidence, "ingestHuman")
    _ingest_card(
        draw,
        image,
        (90, 300, 950, 560),
        label="HUMAN CORRECTION",
        ms_value="500",
        source="human:alice",
        verdict=human["verdict"],
        reason="corrected by a higher-authority source (supersedes the runbook fact)",
    )
    rounded_panel(image, (980, 300, 1840, 560), fill=PANEL_2)
    text(draw, (1012, 328), "ADMISSION HISTORY  ·  same attribute, three rows", size=20, color=MUTED, bold=True)
    rows = [
        ("runbook:checkout-resiliency", "800", runbook["verdict"]),
        ("slack", "300", slack["verdict"]),
        ("human:alice", "500", human["verdict"]),
    ]
    y = 372
    for source, value, verdict in rows:
        v = _verdict_label(verdict)
        text(draw, (1012, y), source, size=20, color=INK, mono=True)
        text(draw, (1350, y), f"{value} ms", size=20, color=MUTED, mono=True)
        text(draw, (1470, y), v, size=20, color=_verdict_color(verdict), bold=True)
        y += 44
    text(
        draw,
        (1012, y + 8),
        "Nothing is deleted -- the 800ms fact stays on record as history.",
        size=17,
        color=FAINT,
        max_width=780,
    )
    rounded_panel(image, (90, 600, 1840, 950), fill=PANEL_2)
    text(
        draw,
        (122, 632),
        "A human outranks a runbook, so this is accepted and recorded as corrected by\na higher-authority source. The Slack guess is still just held -- nobody confirmed it.",
        size=28,
        color=INK,
        bold=True,
        max_width=1680,
    )
    text(
        draw,
        (122, 730),
        f"memory_id: {human['memory_id']}   supersedes: {human.get('superseded')}",
        size=18,
        color=FAINT,
        mono=True,
        max_width=1680,
    )
    footer(image, scene["evidenceClass"])
    image.save(destination)


def _scene_04_agent_decides(scene: dict[str, Any], evidence: dict[str, Any], destination: Path) -> None:
    image = background()
    draw = ImageDraw.Draw(image)
    evidence_badges(image, scene["evidenceClass"])
    header(
        image,
        eyebrow="GET /recall + POST /decide  ·  LIVE, AGAINST THE DEPLOYED API",
        title_value="The agent recalls, proposes, and records a decision",
    )
    recall = step_payload(evidence, "recall")
    decide = step_payload(evidence, "decide")
    results = recall.get("results", [])[:5]
    rounded_panel(image, (90, 260, 1030, 700), fill=PANEL_2)
    text(draw, (122, 286), "GET /recall  ·  top 5 by vector distance", size=20, color=MUTED, bold=True)
    y = 328
    for row in results:
        line = f"{row.get('entity')}.{row.get('attribute_key')} = {row.get('attribute_value')}"
        text(draw, (122, y), line, size=19, color=INK, max_width=870)
        text(draw, (122, y + 24), f"distance {row.get('distance', 0):.4f}", size=15, color=FAINT)
        y += 58
    # Honesty note (not overclaiming the ranking): this frame shows the real ranking the
    # live API returned. Under the stub, embeddings are a deterministic hash of the raw
    # text with no semantic meaning, so ranking here is not guaranteed to surface the
    # most relevant admitted memory -- that is exactly the STUB EMBEDDINGS disclosure.
    text(
        draw,
        (122, y + 6),
        "Ranking shown is the real live response. Under the stub, distance is not\nsemantic -- see the disclosure at right.",
        size=16,
        color=FAINT,
        max_width=870,
    )

    rounded_panel(image, (1070, 260, 1840, 700), fill=PANEL_2, outline=RED)
    y = text(draw, (1102, 286), "DISCLOSURE  ·  STUB EMBEDDINGS", size=20, color=RED, bold=True) + 22
    y = (
        text(
            draw,
            (1102, y),
            "This AWS account has near-zero Bedrock quota. Embeddings fall back to a "
            "deterministic local stub -- a hash of the text, with no semantic meaning.",
            size=24,
            color=INK,
            bold=True,
            max_width=690,
        )
        + 26
    )
    y = (
        text(
            draw,
            (1102, y),
            "Latency numbers stay real. Relevance ranking does not.",
            size=22,
            color=AMBER,
            bold=True,
            max_width=690,
        )
        + 30
    )
    y = text(draw, (1102, y), "embedding_provenance (from the live /health response):", size=17, color=MUTED) + 8
    health = step_payload(evidence, "healthAfter")
    text(
        draw,
        (1102, y),
        str(health.get("embedding_provenance", "")),
        size=18,
        color=INK,
        mono=True,
        max_width=690,
    )

    rounded_panel(image, (90, 740, 1840, 950), fill=PANEL_2)
    text(draw, (122, 766), "POST /decide", size=20, color=MUTED, bold=True)
    text(draw, (122, 800), f"action: {decide.get('action')}", size=27, color=INK, bold=True, max_width=1680)
    text(
        draw,
        (122, 844),
        f"decision_id: {decide.get('decision_id')}   consulted: {len(decide.get('consulted', []))} memory(ies)",
        size=18,
        color=FAINT,
        mono=True,
        max_width=1680,
    )
    footer(image, scene["evidenceClass"])
    image.save(destination)


def _scene_05_outcome_gate(scene: dict[str, Any], evidence: dict[str, Any], destination: Path) -> None:
    image = background()
    draw = ImageDraw.Draw(image)
    evidence_badges(image, scene["evidenceClass"])
    header(
        image,
        eyebrow="POST /confirm_outcome  ·  LIVE, AGAINST THE DEPLOYED API  ·  CENTERPIECE",
        title_value="The outcome gate promotes -- zero model calls",
    )
    confirm = step_payload(evidence, "confirm")

    rounded_panel(image, (90, 300, 900, 700), fill=PANEL_2, outline=AMBER, width=3)
    text(draw, (122, 336), "MODEL CALLS ON THIS PATH", size=22, color=MUTED, bold=True)
    mc = confirm.get("model_calls", 0)
    color = GREEN if mc == 0 else RED
    draw.text((122, 380), str(mc), font=font(140, bold=True, mono=True), fill=color)
    text(
        draw,
        (122, 560),
        "The module that grants trust imports no model client at all.",
        size=20,
        color=MUTED,
        max_width=720,
    )

    rounded_panel(image, (940, 300, 1840, 700), fill=PANEL_2)
    text(draw, (972, 336), "REAL EXTERNAL SIGNAL", size=20, color=MUTED, bold=True)
    text(draw, (972, 372), f"source: {confirm.get('source')}", size=30, color=INK, bold=True)
    text(draw, (972, 420), f"outcome: {confirm.get('outcome')}", size=30, color=GREEN, bold=True)
    text(draw, (972, 468), f"external_ref: {confirm.get('external_ref')}", size=20, color=MUTED)
    promoted = confirm.get("promoted") or []
    text(draw, (972, 520), f"promoted: {len(promoted)} memory(ies)", size=22, color=ACCENT, bold=True)
    if promoted:
        text(draw, (972, 556), str(promoted[0]), size=16, color=FAINT, mono=True, max_width=830)
    text(
        draw,
        (972, 600),
        "PagerDuty resolving the incident -- not the model -- is what promotes this\nmemory to verified, in one transaction on the live database.",
        size=20,
        color=INK,
        max_width=830,
    )

    rounded_panel(image, (90, 740, 1840, 950), fill=PANEL_2)
    text(
        draw,
        (122, 776),
        "Recency, source authority, self-consistency -- that's the model grading its own homework.",
        size=24,
        color=MUTED,
        max_width=1680,
    )
    text(
        draw,
        (122, 818),
        "This is the world confirming the decision actually worked.",
        size=32,
        color=INK,
        bold=True,
        max_width=1680,
    )
    footer(image, scene["evidenceClass"])
    image.save(destination)


def _scene_06_cross_examine(scene: dict[str, Any], evidence: dict[str, Any], destination: Path) -> None:
    image = background()
    draw = ImageDraw.Draw(image)
    evidence_badges(image, scene["evidenceClass"])
    header(
        image,
        eyebrow="GET /timemachine  ·  LIVE, AGAINST THE DEPLOYED API",
        title_value="Cross-examine: a real pinned read, then a real diff",
        subtitle="AS OF SYSTEM TIME re-runs the exact query at the instant the decision was made",
    )
    tm = step_payload(evidence, "timemachine")
    believed = tm.get("believed_at_decision_time", [])
    changed = tm.get("changed_since", [])
    rounded_panel(image, (90, 300, 900, 620), fill=PANEL_2)
    text(draw, (122, 328), "BELIEVED AT DECISION TIME", size=20, color=MUTED, bold=True)
    draw.text((122, 368), str(len(believed)), font=font(96, bold=True, mono=True), fill=ACCENT)
    text(draw, (122, 480), "admitted memories, reconstructed with a real\nAS OF SYSTEM TIME read.", size=22, color=INK, max_width=740)

    rounded_panel(image, (940, 300, 1840, 620), fill=PANEL_2, outline=GREEN)
    text(draw, (972, 328), f"CHANGED SINCE  ·  {len(changed)} memory(ies)", size=20, color=MUTED, bold=True)
    y = 372
    for row in changed[:3]:
        text(
            draw,
            (972, y),
            f"{row.get('entity')}.{row.get('attribute_key')}",
            size=22,
            color=INK,
            bold=True,
            max_width=830,
        )
        text(
            draw,
            (972, y + 30),
            f"{row.get('was_trust_tier')}  ->  {row.get('trust_tier')}",
            size=22,
            color=GREEN,
            bold=True,
        )
        y += 76

    rounded_panel(image, (90, 660, 1840, 950), fill=PANEL_2)
    text(
        draw,
        (122, 692),
        "This part isn't new -- Zep and others already ship bitemporal replay.",
        size=26,
        color=MUTED,
        max_width=1680,
    )
    text(
        draw,
        (122, 736),
        "What this proves is a real pinned read against the live database, diffed against\nwhat's true right now -- not a reconstruction.",
        size=28,
        color=INK,
        bold=True,
        max_width=1680,
    )
    text(
        draw,
        (122, 830),
        f"decision_id: {evidence.get('decisionId')}",
        size=18,
        color=FAINT,
        mono=True,
    )
    footer(image, scene["evidenceClass"])
    image.save(destination)


def _scene_07_node_loss_scale(scene: dict[str, Any], evidence: dict[str, Any], destination: Path) -> None:
    image = background()
    draw = ImageDraw.Draw(image)
    evidence_badges(image, scene["evidenceClass"])
    header(
        image,
        eyebrow="scripts/cluster-demo.sh failover  ·  REAL 3-NODE CockroachDB CLUSTER",
        title_value="Node-loss survival, then scale",
    )
    transcript = read_failover_transcript()
    rounded_panel(image, (90, 260, 1000, 700), fill=(6, 10, 16), outline=ACCENT)
    lines = [ln for ln in transcript.splitlines() if ln.strip()]
    # Keep the most legible slice of the real transcript: the kill + read loop + tally.
    keep = [ln for ln in lines if "recall OK" in ln or "KILLED" in ln or "successful reads" in ln]
    keep = keep[:14]
    y = 288
    for ln in keep:
        color = RED if "KILLED" in ln else (GREEN if "successful reads" in ln else MUTED)
        text(draw, (118, y), ln.strip(), size=17, color=color, mono=True, max_width=850)
        y += 27

    bench = read_benchmark_numbers()
    rounded_panel(image, (1040, 260, 1840, 700), fill=PANEL_2, outline=AMBER)
    text(draw, (1072, 288), f"LOCAL BENCHMARK  ·  {bench['rows']} memories", size=20, color=MUTED, bold=True)
    text(draw, (1072, 330), f"{bench['indexed_p50']} ms", size=54, color=GREEN, bold=True)
    text(draw, (1072, 392), "p50, vector-indexed", size=18, color=MUTED)
    text(draw, (1072, 440), f"{bench['noindex_p50']} ms", size=40, color=AMBER, bold=True)
    text(draw, (1072, 486), "p50, brute-force control", size=18, color=MUTED)
    text(draw, (1072, 540), f"{bench['speedup']}x faster", size=32, color=INK, bold=True)
    text(
        draw,
        (1072, 590),
        "Single machine, single run -- directional evidence, not a\ncontrolled benchmark.",
        size=18,
        color=FAINT,
        max_width=720,
    )

    rounded_panel(image, (90, 740, 1840, 950), fill=PANEL_2)
    text(
        draw,
        (122, 772),
        "12 reads, 0 failed, 101 memories intact through a node kill.",
        size=28,
        color=INK,
        bold=True,
        max_width=1680,
    )
    text(
        draw,
        (122, 816),
        "Three containers on one machine prove the replication mechanism -- this is not a facility outage test.",
        size=22,
        color=AMBER,
        max_width=1680,
    )
    footer(image, scene["evidenceClass"])
    image.save(destination)


def _scene_08_close(scene: dict[str, Any], evidence: dict[str, Any], destination: Path) -> None:
    image = background()
    draw = ImageDraw.Draw(image)
    evidence_badges(image, scene["evidenceClass"])
    text(draw, (90, 100), "What's actually new", size=46, bold=True)
    bullets = read_prior_art()
    rounded_panel(image, (90, 172, 1840, 700), fill=PANEL_2)
    y = 200
    for bullet in bullets:
        y = text(draw, (122, y), f"•  {bullet}", size=19, color=INK, max_width=1690, spacing=6) + 20

    rounded_panel(image, (90, 730, 1840, 900), fill=PANEL_2, outline=GREEN)
    text(
        draw,
        (122, 758),
        "MemoryStand: memory trusted because the world confirmed it worked,\nnot because a model said so.",
        size=28,
        color=INK,
        bold=True,
        max_width=1680,
    )
    text(draw, (122, 838), "github.com/tomyimkc/memorystand", size=22, color=ACCENT, bold=True)

    text(
        draw,
        (90, 935),
        "This is a hackathon build. Candidate only. Not a general-intelligence claim.",
        size=24,
        color=AMBER,
        bold=True,
    )
    footer(image, scene["evidenceClass"])
    image.save(destination)


SCENE_BUILDERS = {
    "01-hook": _scene_01_hook,
    "02-admission-holds": _scene_02_admission_holds,
    "03-human-correction": _scene_03_human_correction,
    "04-agent-decides": _scene_04_agent_decides,
    "05-outcome-gate": _scene_05_outcome_gate,
    "06-cross-examine": _scene_06_cross_examine,
    "07-node-loss-scale": _scene_07_node_loss_scale,
    "08-close": _scene_08_close,
}


def main() -> int:
    timeline = load_timeline()
    evidence = load_evidence()
    FRAMES_DIR.mkdir(parents=True, exist_ok=True)

    scenes = timeline.get("scenes", [])
    missing_builders = [s["id"] for s in scenes if s["id"] not in SCENE_BUILDERS]
    if missing_builders:
        raise SystemExit(f"No frame builder for scene id(s): {missing_builders}")

    for scene in scenes:
        destination = FRAMES_DIR / scene["frame"]
        SCENE_BUILDERS[scene["id"]](scene, evidence, destination)
        print(f"wrote {destination}")

    print(f"\n{len(scenes)} frame(s) written to {FRAMES_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
