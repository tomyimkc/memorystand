#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Build five full-frame, high-legibility receipts for the presenter cut.

The presenter narration already names each claim. These frames therefore show only the
minimum visual receipt needed to judge it: large states/numbers, source provenance, and
claim boundaries. Values come from captured evidence or fixed benchmark claims; nothing
is scraped from the old dense evidence video. The generated PNGs live under gitignored
``artifacts/``; the committed script is their reproducible source of truth.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts" / "video" / "presenter-receipts"
EVIDENCE = ROOT / "artifacts" / "video" / "capture" / "evidence.json"
GUIDED_EVIDENCE = ROOT / "artifacts" / "video" / "capture" / "guided-refusal.json"
WIDTH, HEIGHT = 1920, 1080
SUBTITLE_SAFE_TOP = 1000
BG = (5, 8, 13)
PANEL = (13, 19, 27)
BORDER = (44, 57, 73)
INK = (244, 247, 251)
DIM = (183, 192, 204)
FAINT = (116, 129, 148)
BLUE = (77, 163, 255)
GREEN = (53, 211, 164)
RED = (255, 93, 106)
AMBER = (245, 185, 61)
PURPLE = (178, 141, 255)


def font(size: int, *, bold: bool = False, mono: bool = False) -> ImageFont.FreeTypeFont:
    candidates = [
        "/System/Library/Fonts/Menlo.ttc" if mono else (
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else
            "/System/Library/Fonts/Supplemental/Arial.ttf"
        ),
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf" if mono else (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        ),
    ]
    for candidate in candidates:
        p = Path(candidate)
        if p.is_file():
            return ImageFont.truetype(str(p), size)
    raise SystemExit("No usable font found")


def background() -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT), BG)
    glow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(glow)
    draw.ellipse((-220, -260, 820, 760), fill=(31, 90, 140, 92))
    draw.ellipse((1280, 600, 2260, 1480), fill=(62, 40, 130, 58))
    glow = glow.filter(ImageFilter.GaussianBlur(130))
    image.paste(glow, mask=glow.getchannel("A"))
    return image


def panel(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], *, outline=BORDER, width=2) -> None:
    draw.rounded_rectangle(box, radius=24, fill=PANEL, outline=outline, width=width)


def text(draw: ImageDraw.ImageDraw, xy: tuple[int, int], value: str, *, size: int,
         color=INK, bold=False, mono=False, anchor=None, align="left") -> None:
    draw.multiline_text(xy, value, font=font(size, bold=bold, mono=mono), fill=color,
                        spacing=10, anchor=anchor, align=align)


def pill(draw: ImageDraw.ImageDraw, xy: tuple[int, int], value: str, color=BLUE) -> None:
    f = font(20, bold=True)
    w = int(draw.textlength(value, font=f)) + 34
    x, y = xy
    draw.rounded_rectangle((x, y, x+w, y+43), radius=20, fill=PANEL, outline=color, width=2)
    draw.text((x+17, y+10), value, font=f, fill=color)


def header(draw: ImageDraw.ImageDraw, label: str, title: str, subtitle: str | None = None,
           *, color=BLUE) -> None:
    pill(draw, (74, 58), label, color)
    text(draw, (74, 128), title, size=57, bold=True)
    if subtitle:
        text(draw, (77, 203), subtitle, size=25, color=DIM)


def footer(draw: ImageDraw.ImageDraw, source: str) -> None:
    # The final 80px is deliberately empty. Remotion owns that dedicated
    # subtitle rail during evidence shots, so no receipt word or claim badge
    # can ever sit underneath a burned caption.
    draw.line((74, 946, 1846, 946), fill=BORDER, width=1)
    text(draw, (74, 963), source, size=17, color=FAINT)
    text(draw, (1846, 963), "candidateOnly: true  ·  canClaimAGI: false", size=17,
         color=GREEN, anchor="ra")


def arrow(draw: ImageDraw.ImageDraw, x1: int, x2: int, y: int, color=BLUE) -> None:
    draw.line((x1, y, x2, y), fill=color, width=8)
    draw.polygon([(x2, y), (x2-24, y-17), (x2-24, y+17)], fill=color)


def step_card(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], number: str,
              label: str, detail: str, *, color=BLUE) -> None:
    panel(draw, box, outline=color, width=3)
    x1, y1, x2, _ = box
    text(draw, (x1+34, y1+30), number, size=24, color=color, bold=True, mono=True)
    text(draw, ((x1+x2)//2, y1+124), label, size=39, bold=True, anchor="ma", align="center")
    text(draw, ((x1+x2)//2, y1+222), detail, size=24, color=DIM, anchor="ma", align="center")


def load() -> dict[str, Any]:
    if not EVIDENCE.is_file():
        raise SystemExit(f"missing {EVIDENCE}")
    return json.loads(EVIDENCE.read_text())


def load_guided() -> dict[str, Any]:
    if not GUIDED_EVIDENCE.is_file():
        raise SystemExit(f"missing {GUIDED_EVIDENCE}")
    return json.loads(GUIDED_EVIDENCE.read_text())


def require_path(data: dict[str, Any], *keys: str) -> Any:
    value: Any = data
    for key in keys:
        if not isinstance(value, dict) or key not in value:
            raise SystemExit(f"captured evidence is missing {'/'.join(keys)}")
        value = value[key]
    return value


def why_false(_: dict[str, Any]) -> Image.Image:
    im = background(); d = ImageDraw.Draw(im)
    header(d, "SEEDED CAUSAL EXAMPLE", "Alert stopped does not prove the restart worked.",
           "Timing is correlation. The observed change was only three milliseconds.", color=AMBER)
    step_card(d, (74, 315, 552, 756), "01", "RESTART", "service restarted", color=BLUE)
    arrow(d, 580, 700, 535, AMBER)
    step_card(d, (728, 315, 1206, 756), "02", "ALERT QUIET", "symptom disappeared", color=AMBER)
    arrow(d, 1234, 1354, 535, RED)
    step_card(d, (1382, 315, 1846, 756), "03", "ONLY 3 MS", "223 ms  →  220 ms", color=RED)
    text(d, (960, 838), "QUIET ALERT ≠ PROVEN CAUSE", size=48, color=INK, bold=True, anchor="ma")
    text(d, (960, 912), "The measured 3 ms change does not support the claimed 112 ms improvement.",
         size=28, color=DIM, anchor="ma")
    footer(d, "Seeded decision-rule example · not production CloudWatch")
    return im


def decision_refusal(e: dict[str, Any]) -> Image.Image:
    im = background(); d = ImageDraw.Draw(im)
    decision = require_path(e, "decision", "payload")
    recall = require_path(e, "recall", "payload")
    if require_path(e, "decision", "status") not in {200, 201}:
        raise SystemExit("guided decision receipt is not successful")
    if decision.get("target_entity") != "payments-service":
        raise SystemExit("guided decision target is not payments-service")
    if decision.get("reasoning_source") != "fallback_heuristic":
        raise SystemExit("guided decision did not use the disclosed fixed fallback")
    if decision.get("action") != "scale_up":
        raise SystemExit("guided decision did not return scale_up")
    if decision.get("model_calls") != 0:
        raise SystemExit("guided decision no longer reports model_calls=0")
    if decision.get("status") != "held_for_approval":
        raise SystemExit("guided decision is not held for approval")
    if decision.get("eligible_memory_ids"):
        raise SystemExit("guided refusal unexpectedly contains an eligible memory")
    if decision.get("cited_memory_ids"):
        raise SystemExit("guided refusal unexpectedly cites a memory")

    rows = recall.get("results") or []
    wrong = next(
        (
            row for row in rows
            if row.get("entity") == "checkout-api"
            and row.get("trust_tier") == "verified"
        ),
        None,
    )
    if not wrong:
        raise SystemExit("guided recall has no verified checkout-api row")
    wrong_id = str(wrong.get("memory_id") or "")
    excluded = decision.get("excluded_memories") or []
    if not any(
        str(row.get("memory_id") or "") == wrong_id
        and row.get("reason") == "entity_mismatch"
        for row in excluded
    ):
        raise SystemExit("guided decision did not exclude the checkout-api row")
    if wrong_id in (decision.get("cited_memory_ids") or []):
        raise SystemExit("guided decision cited the wrong-service row")

    header(
        d,
        "LIVE DEPLOYED DECISION",
        "Wrong service. No authority.",
        "The row stays visible in recall, but it cannot steer a payments-service incident.",
        color=GREEN,
    )
    panel(d, (74, 304, 742, 805), outline=BLUE, width=3)
    text(d, (112, 344), "TARGET", size=22, color=FAINT, bold=True)
    text(d, (112, 402), "payments-service", size=46, color=INK, bold=True)
    text(d, (112, 526), "RECALLED ROW", size=22, color=FAINT, bold=True)
    text(d, (112, 584), "checkout-api", size=43, color=RED, bold=True)
    text(d, (112, 654), "verified label", size=27, color=DIM)
    text(d, (112, 714), f"id {wrong_id[:8]}…", size=22, color=FAINT, mono=True)
    arrow(d, 782, 940, 550, RED)
    panel(d, (978, 304, 1846, 805), outline=GREEN, width=3)
    text(d, (1018, 344), "SUBJECT POLICY", size=22, color=FAINT, bold=True)
    text(d, (1412, 438), "EXCLUDED", size=72, color=RED, bold=True, anchor="ma")
    text(d, (1412, 548), "entity mismatch", size=35, color=INK, bold=True, anchor="ma")
    text(d, (1412, 635), "fixed fallback · scale up", size=29, color=DIM, anchor="ma")
    text(d, (1412, 689), "held for human approval", size=29, color=AMBER, bold=True, anchor="ma")
    text(d, (1412, 748), "model calls: 0", size=25, color=GREEN, mono=True, anchor="ma")
    text(d, (960, 882), "VISIBLE FOR AUDIT. BARRED FROM ACTION.", size=47,
         color=INK, bold=True, anchor="ma")
    footer(d, "Captured live POST /decide receipt · AWS Lambda + CockroachDB Cloud")
    return im


def receipt_first(e: dict[str, Any]) -> Image.Image:
    im = background(); d = ImageDraw.Draw(im)
    health = require_path(e, "steps", "healthBefore", "payload")
    if health.get("database") != "reachable":
        raise SystemExit("captured health receipt does not show database=reachable")
    header(d, "LIVE PROJECT", "A memory earns authority only after an outside check.",
           "The deployed API and CockroachDB are reachable; storage alone still grants no keys.", color=GREEN)
    panel(d, (74, 304, 664, 790), outline=GREEN, width=3)
    text(d, (112, 342), "DEPLOYED HEALTH", size=22, color=FAINT, bold=True)
    text(d, (112, 420), "LIVE", size=86, color=GREEN, bold=True)
    text(d, (112, 552), f"database: {health.get('database', 'reachable')}", size=31, color=INK, bold=True)
    text(d, (112, 610), "CockroachDB Cloud", size=25, color=DIM)
    text(d, (112, 652), "AWS Lambda API", size=25, color=DIM)
    text(d, (112, 694), "Public demo path", size=25, color=DIM)
    arrow(d, 708, 862, 548, BLUE)
    panel(d, (900, 304, 1846, 790), outline=BLUE, width=3)
    text(d, (950, 342), "AUTHORITY RULE", size=22, color=FAINT, bold=True)
    text(d, (1373, 454), "STORE", size=42, color=DIM, bold=True, anchor="ma")
    text(d, (1373, 532), "+ OUTCOME RECEIPT", size=49, color=BLUE, bold=True, anchor="ma")
    text(d, (1373, 620), "= MAY ACT", size=58, color=GREEN, bold=True, anchor="ma")
    text(d, (1373, 707), "No receipt → human review", size=27, color=DIM, anchor="ma")
    text(d, (960, 881), "RECEIPT BEFORE KEYS", size=50, color=INK, bold=True, anchor="ma")
    footer(d, "Captured deployed evidence · AWS Lambda + CockroachDB Cloud")
    return im


def cloudwatch(e: dict[str, Any]) -> Image.Image:
    im = background(); d = ImageDraw.Draw(im)
    verified = require_path(e, "steps", "confirmVerified", "payload")
    refused = require_path(e, "steps", "confirmRefused", "payload")
    verification = require_path(verified, "verification")
    observed = require_path(verification, "observed")
    claimed = require_path(verification, "claimed")
    if verification.get("status") != "confirmed":
        raise SystemExit("captured CloudWatch receipt is not confirmed")
    if verified.get("model_calls") != 0:
        raise SystemExit("captured authority receipt no longer reports model_calls=0")
    if require_path(e, "steps", "confirmRefused").get("ok") is not False:
        raise SystemExit("captured mismatch receipt is not a refusal")
    header(d, "LIVE CLOUDWATCH RE-CHECK", "Match or refuse.",
           "The promotion path checks the metric, direction, amount, and entity—with zero model calls.", color=GREEN)
    panel(d, (74, 300, 1170, 815), outline=GREEN, width=3)
    text(d, (116, 338), "CONFIRMED RECEIPT", size=23, color=GREEN, bold=True)
    text(d, (116, 408), "CLAIMED", size=22, color=FAINT, bold=True)
    text(d, (116, 447), f"{claimed:,.0f} ms",
         size=62, color=INK, bold=True, mono=True)
    text(d, (622, 408), "CLOUDWATCH", size=22, color=FAINT, bold=True)
    text(d, (622, 447), f"{observed:,.0f} ms",
         size=62, color=GREEN, bold=True, mono=True)
    arrow(d, 492, 586, 490, GREEN)
    text(d, (116, 580), "metric", size=26, color=DIM, bold=True)
    text(d, (338, 580), "direction", size=26, color=DIM, bold=True)
    text(d, (620, 580), "amount", size=26, color=DIM, bold=True)
    text(d, (842, 580), "entity", size=26, color=DIM, bold=True)
    text(d, (116, 674), "CONFIRMED → VERIFIED", size=44, color=GREEN, bold=True)
    text(d, (116, 748), f"model calls on authority path: {verified.get('model_calls', 0)}",
         size=26, color=DIM, mono=True)
    panel(d, (1214, 300, 1846, 815), outline=RED, width=3)
    text(d, (1254, 338), "MISMATCH", size=23, color=RED, bold=True)
    text(d, (1530, 474), "REFUSED", size=70, color=RED, bold=True, anchor="ma")
    text(d, (1530, 604), "wrong direction\nwrong amount\nwrong entity", size=31, color=INK,
         bold=True, anchor="ma", align="center")
    detail = str(refused.get("detail") or "CloudWatch disagreement beyond tolerance")
    short = "CloudWatch disagreed beyond tolerance." if detail else "CloudWatch disagreed."
    text(d, (1530, 748), short, size=24, color=DIM, anchor="ma")
    footer(d, "Captured live metric receipt · AWS CloudWatch · backend/trust.py")
    return im


def cockroach_guided(e: dict[str, Any]) -> Image.Image:
    im = background(); d = ImageDraw.Draw(im)
    tm = require_path(e, "timemachine", "payload")
    decision = require_path(tm, "decision")
    recalled = tm.get("recalled_as_of") or []
    excluded = tm.get("excluded_memories_as_of") or []
    eligible = tm.get("eligible_memory_ids_as_of")
    if decision.get("target_entity") != "payments-service":
        raise SystemExit("time-travel receipt has the wrong target entity")
    if not recalled or not isinstance(eligible, list):
        raise SystemExit("time-travel receipt has no ranked recall or eligibility list")
    if eligible:
        raise SystemExit("time-travel receipt unexpectedly reconstructed an eligible memory")
    wrong = next(
        (
            row for row in recalled
            if row.get("entity") == "checkout-api"
            and row.get("trust_tier") == "verified"
        ),
        None,
    )
    if not wrong:
        raise SystemExit("time-travel receipt has no wrong-service row")
    wrong_id = str(wrong.get("memory_id") or "")
    if not any(
        str(row.get("memory_id") or "") == wrong_id
        and row.get("reason") == "entity_mismatch"
        for row in excluded
    ):
        raise SystemExit("time-travel receipt did not preserve the entity exclusion")
    if wrong_id in {str(value) for value in eligible}:
        raise SystemExit("time-travel receipt made the wrong-service row eligible")

    header(
        d,
        "COCKROACHDB DECISION RECEIPT",
        "The ranking and refusal remain together.",
        "A pinned read reconstructs the query, target, eligible set, and every exclusion.",
        color=PURPLE,
    )
    panel(d, (74, 304, 642, 805), outline=PURPLE, width=3)
    text(d, (112, 344), "RECORDED QUERY", size=22, color=FAINT, bold=True)
    text(d, (112, 410), "payments-service\np99 latency", size=42, color=INK, bold=True)
    text(d, (112, 565), "TARGET", size=22, color=FAINT, bold=True)
    text(d, (112, 620), "payments-service", size=33, color=BLUE, bold=True)
    text(d, (112, 714), "AS OF SYSTEM TIME", size=23, color=PURPLE, bold=True, mono=True)
    arrow(d, 682, 814, 550, PURPLE)
    panel(d, (852, 304, 1846, 805), outline=GREEN, width=3)
    text(d, (892, 344), "PINNED DECISION CONTEXT", size=22, color=FAINT, bold=True)
    text(d, (892, 410), f"{len(recalled)} ranked rows", size=42, color=INK, bold=True)
    text(d, (892, 486), f"{len(eligible)} eligible", size=37, color=GREEN, bold=True)
    text(d, (1220, 486), f"{len(excluded)} excluded", size=37, color=RED, bold=True)
    text(d, (892, 590), "checkout-api row", size=29, color=DIM, bold=True)
    text(d, (1220, 590), "entity_mismatch", size=29, color=RED, bold=True, mono=True)
    text(d, (892, 672), "wrong row eligible", size=26, color=DIM)
    text(d, (1220, 672), "NO", size=34, color=GREEN, bold=True)
    text(d, (960, 882), "QUERY · TARGET · RANKING · REFUSAL", size=47,
         color=INK, bold=True, anchor="ma")
    footer(d, "Captured live GET /timemachine receipt · CockroachDB Cloud")
    return im


def attack(_: dict[str, Any]) -> Image.Image:
    im = background(); d = ImageDraw.Draw(im)
    header(d, "DETERMINISTIC TEST RECEIPT", "Storage and authority are scored separately.",
           "All attacks remained inspectable. None earned autonomous control.", color=AMBER)
    panel(d, (74, 310, 596, 790), outline=AMBER, width=3)
    text(d, (335, 421), "540", size=126, color=INK, bold=True, mono=True, anchor="ma")
    text(d, (335, 585), "ATTACKS STORED", size=31, color=DIM, bold=True, anchor="ma")
    panel(d, (700, 310, 1222, 790), outline=RED, width=3)
    text(d, (961, 421), "0", size=126, color=GREEN, bold=True, mono=True, anchor="ma")
    text(d, (961, 585), "ATTACKS PROMOTED", size=31, color=DIM, bold=True, anchor="ma")
    panel(d, (1326, 310, 1846, 790), outline=GREEN, width=3)
    text(d, (1586, 421), "60 / 60", size=92, color=GREEN, bold=True, mono=True, anchor="ma")
    text(d, (1586, 585), "HONEST CONTROLS KEPT", size=28, color=DIM, bold=True, anchor="ma")
    text(d, (960, 882), "STORED IS NOT AUTHORITY", size=50, color=INK, bold=True, anchor="ma")
    footer(d, "600 deterministic cases · candidate benchmark receipt")
    return im


def main() -> int:
    evidence = load()
    guided = load_guided()
    OUT.mkdir(parents=True, exist_ok=True)
    frames = {
        "why-false.png": why_false(evidence),
        "decision-live.png": decision_refusal(guided),
        "cloudwatch.png": cloudwatch(evidence),
        "cockroachdb.png": cockroach_guided(guided),
        "attack-test.png": attack(evidence),
    }
    for name, image in frames.items():
        path = OUT / name
        image.save(path)
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
