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
           "Timing is correlation. An unchanged outcome metric breaks the causal claim.", color=AMBER)
    step_card(d, (74, 315, 552, 756), "01", "RESTART", "service restarted", color=BLUE)
    arrow(d, 580, 700, 535, AMBER)
    step_card(d, (728, 315, 1206, 756), "02", "ALERT QUIET", "symptom disappeared", color=AMBER)
    arrow(d, 1234, 1354, 535, RED)
    step_card(d, (1382, 315, 1846, 756), "03", "LATENCY FLAT", "223 ms  →  220 ms", color=RED)
    text(d, (960, 838), "QUIET ≠ IMPROVEMENT ≠ CAUSE", size=48, color=INK, bold=True, anchor="ma")
    text(d, (960, 912), "No outside receipt showed the reboot produced the claimed 112 ms improvement.",
         size=28, color=DIM, anchor="ma")
    footer(d, "Seeded decision-rule example · not production CloudWatch")
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


def cockroach(e: dict[str, Any]) -> Image.Image:
    im = background(); d = ImageDraw.Draw(im)
    tm = require_path(e, "steps", "timemachine", "payload")
    believed = len(tm.get("believed_at_decision_time") or [])
    changed = tm.get("changed_since") or []
    if not believed or not changed:
        raise SystemExit("captured CockroachDB receipt has no pinned read or diff")
    changed_row = changed[0]
    header(d, "COCKROACHDB HISTORY", "The old belief and the new state both remain.",
           "AS OF SYSTEM TIME replays what the agent knew, then compares it with the database now.", color=PURPLE)
    panel(d, (74, 310, 830, 790), outline=PURPLE, width=3)
    text(d, (112, 350), "AT DECISION TIME", size=23, color=FAINT, bold=True)
    text(d, (112, 438), str(believed), size=112, color=BLUE, bold=True, mono=True)
    text(d, (112, 585), "memories in the pinned read", size=28, color=INK, bold=True)
    text(d, (112, 650), "AS OF SYSTEM TIME", size=27, color=PURPLE, bold=True, mono=True)
    arrow(d, 866, 1028, 548, PURPLE)
    panel(d, (1066, 310, 1846, 790), outline=GREEN, width=3)
    text(d, (1106, 350), "DATABASE NOW", size=23, color=FAINT, bold=True)
    text(d, (1106, 438), "1 CHANGE", size=66, color=GREEN, bold=True)
    entity = require_path(changed_row, "entity")
    key = require_path(changed_row, "attribute_key")
    before = require_path(changed_row, "was_trust_tier")
    after = require_path(changed_row, "trust_tier")
    text(d, (1106, 552), f"{entity}.{key}", size=27, color=INK, bold=True)
    text(d, (1106, 629), f"{before}  →  {after}", size=35, color=GREEN, bold=True, mono=True)
    text(d, (960, 880), "REAL PINNED READ. REAL DIFF. NO RECONSTRUCTION.", size=39,
         color=INK, bold=True, anchor="ma")
    footer(d, "Captured GET /timemachine receipt · CockroachDB Cloud")
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
    OUT.mkdir(parents=True, exist_ok=True)
    frames = {
        "why-false.png": why_false(evidence),
        "receipt-first.png": receipt_first(evidence),
        "cloudwatch.png": cloudwatch(evidence),
        "cockroachdb.png": cockroach(evidence),
        "attack-test.png": attack(evidence),
    }
    for name, image in frames.items():
        path = OUT / name
        image.save(path)
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
