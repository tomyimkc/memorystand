#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Step 3: render one 1920x1080 background per beat, with the data panel opposite the speaker.

Deliberately Pillow rather than Remotion. The repo already has a verified visual language in
scripts/video/build_frames.py -- the palette, the rounded panels, the fonts, and the
overflow detection that fails a build rather than shipping a clipped sentence. Reusing it means
the presenter cut and the evidence-deck cut look like one project, and it adds no toolchain.
What it gives up is motion design: these are static panels behind a moving presenter, and for
content that is mostly dense tables and measured numbers -- things a viewer reads rather than
watches -- that is the right trade.

The speaker side alternates per beat, so the panel is always in the half the presenter is not
occupying. That is the whole reason the base frames were generated with negative space on a
known side.

    python scripts/presenter/make_panels.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts" / "video"))

from PIL import ImageDraw  # noqa: E402

import build_frames as bf  # noqa: E402

SCRIPT_JSON = REPO_ROOT / "docs" / "demo" / "presenter-script.json"
OUT_DIR = REPO_ROOT / "artifacts" / "presenter" / "panels"

W, H = 1920, 1080

# The presenter occupies one third; the panel gets the rest. These two numbers are the whole
# layout contract, and compose.py reads the same constants so the video and the artwork cannot
# disagree about who owns which pixels.
PRESENTER_W = 660
PANEL_MARGIN = 60


# The vertical band the panel is allowed to occupy, between the title and the disclosure.
BAND_TOP, BAND_BOTTOM = 210, H - 190

# Distance from the panel's bottom edge up to the footnote's baseline. Each draw function
# reports where its body ends, and the panel is then sized so this much room is left underneath
# -- which is what keeps the footnote attached to the content instead of stranded at the bottom
# of a fixed-height box.
FOOTNOTE_LIFT = 70
FOOTNOTE_GAP = 50


def panel_box(side: str, top: int = BAND_TOP, bottom: int = BAND_BOTTOM) -> tuple[int, int, int, int]:
    """Where the data panel lives, given which side the speaker is on."""
    if side == "LEFT":  # speaker left -> panel right
        return (PRESENTER_W + PANEL_MARGIN, top, W - PANEL_MARGIN, bottom)
    return (PANEL_MARGIN, top, W - PRESENTER_W - PANEL_MARGIN, bottom)


def draw_quote(draw, box, data):
    x0, y0, x1, y1 = box
    y = y0 + 34
    for line in data["lines"]:
        y = bf.text(draw, (x0 + 34, y), f"•  {line}", size=27, color=bf.INK,
                    max_width=x1 - x0 - 68, spacing=6) + 26
    return y - 26


def draw_story(draw, box, data):
    x0, y0, x1, y1 = box
    y = bf.text(draw, (x0 + 34, y0 + 40), data["headline"], size=44, color=bf.AMBER,
                bold=True, max_width=x1 - x0 - 68) + 34
    return bf.text(draw, (x0 + 34, y), data["body"], size=27, color=bf.INK,
                   max_width=x1 - x0 - 68, spacing=8)


def draw_table(draw, box, data):
    x0, y0, x1, y1 = box
    colx = [x0 + 34, x0 + 430, x0 + 700]
    y = y0 + 36
    for i, col in enumerate(data["columns"]):
        bf.text(draw, (colx[i], y), col, size=21, color=bf.MUTED, bold=True)
    y += 48
    for row in data["rows"]:
        highlight = row[0].startswith("outcome")
        colour = bf.GREEN if highlight else bf.INK
        bf.text(draw, (colx[0], y), row[0], size=28, color=colour,
                bold=highlight, max_width=380)
        bf.text(draw, (colx[1], y), row[1], size=30, color=colour, bold=True)
        bf.text(draw, (colx[2], y), row[2], size=30, color=colour, bold=True)
        y += 62
    return y - 12


def draw_callout(draw, box, data):
    x0, y0, x1, y1 = box
    draw.text((x0 + 34, y0 + 30), data["big"], font=bf.font(190, bold=True, mono=True),
              fill=bf.GREEN)
    y = bf.text(draw, (x0 + 240, y0 + 130), data["label"], size=30, color=bf.MUTED,
                max_width=x1 - x0 - 300) + 44
    return bf.text(draw, (x0 + 34, max(y, y0 + 250)), data["body"], size=25, color=bf.INK,
                   max_width=x1 - x0 - 68, spacing=8)


def draw_recall(draw, box, data):
    x0, y0, x1, y1 = box
    y = y0 + 34
    for row in data["rows"]:
        chosen = row["chosen"]
        tier_colour = bf.GREEN if row["tier"] == "verified" else bf.MUTED
        colour = bf.INK if chosen else bf.MUTED
        bf.text(draw, (x0 + 34, y), f"{row['id']}   distance {row['distance']}",
                size=26, color=colour, mono=True)
        bf.text(draw, (x0 + 34, y + 36), f"{row['tier']}   ->   {row['value']}",
                size=24, color=tier_colour, bold=chosen)
        if chosen:
            bf.text(draw, (x1 - 250, y + 12), "CHOSEN", size=26, color=bf.GREEN, bold=True)
        y += 118
    y = bf.text(draw, (x0 + 34, y + 6),
                "The closer memory lost to the more trusted one.",
                size=30, color=bf.INK, bold=True, max_width=x1 - x0 - 68)
    if data.get("note"):
        y = bf.text(draw, (x0 + 34, y + 26), data["note"], size=21, color=bf.MUTED,
                    max_width=x1 - x0 - 68, spacing=6)
    return y


def draw_close(draw, box, data):
    x0, y0, x1, y1 = box
    y = y0 + 34
    for line in data["lines"]:
        y = bf.text(draw, (x0 + 34, y), f"•  {line}", size=25, color=bf.MUTED,
                    max_width=x1 - x0 - 68, spacing=6) + 24
    return bf.text(draw, (x0 + 34, y + 20),
                   "What is unusual is enforcing it.", size=38, color=bf.INK, bold=True,
                   max_width=x1 - x0 - 68)


def draw_schema(draw, box, data):
    """The CockroachDB memory layer, as the actual DDL and the actual recall query.

    Shown as code rather than paraphrased into a diagram, because "a vector column inside the
    same relational table" is a claim a judge can check in one glance against db/schema.sql, and
    a box-and-arrow drawing of it would not be.
    """
    x0, y0, x1, y1 = box
    y = y0 + 34
    colour = bf.INK
    for line in data["code"]:
        if not line:
            y += 16
            colour = bf.INK
            continue
        # An indented line continues the statement above it, so it keeps that statement's
        # colour -- the index's column list is part of the VECTOR INDEX, not a new thought.
        if not line.startswith(" "):
            colour = bf.GREEN if line.split(" ")[0] in {"VECTOR", "WHERE", "ORDER"} else bf.INK
        y = bf.text(draw, (x0 + 34, y), line, size=25, color=colour, mono=True,
                    max_width=x1 - x0 - 68) + 8
    return bf.text(draw, (x0 + 34, y + 26), data["note"], size=22, color=bf.MUTED,
                   max_width=x1 - x0 - 68, spacing=6)


def draw_admission(draw, box, data):
    """Three real ingest receipts: what was written, and what the store did with it."""
    x0, y0, x1, y1 = box
    y = y0 + 36
    for row in data["rows"]:
        held = row["verdict"] != "accepted"
        colour = bf.AMBER if held else bf.GREEN
        bf.text(draw, (x0 + 34, y), row["source"], size=27, color=bf.INK, max_width=430)
        bf.text(draw, (x0 + 470, y), row["value"], size=29, color=bf.INK, bold=True, mono=True)
        bf.text(draw, (x0 + 610, y), row["verdict"], size=27, color=colour, bold=True)
        y += 62
    return bf.text(draw, (x0 + 34, y + 20), data["reason"], size=21, color=bf.MUTED,
                   max_width=x1 - x0 - 68, spacing=6)


def draw_oracle(draw, box, data):
    """Two live confirm_outcome calls -- one the metric supports, one it does not."""
    x0, y0, x1, y1 = box
    bf.text(draw, (x0 + 34, y0 + 34), "claimed", size=20, color=bf.MUTED, bold=True)
    bf.text(draw, (x0 + 300, y0 + 34), "CloudWatch", size=20, color=bf.MUTED, bold=True)
    bf.text(draw, (x0 + 590, y0 + 34), "verdict", size=20, color=bf.MUTED, bold=True)
    y = y0 + 82
    for row in data["rows"]:
        refused = row["verdict"] == "refused"
        colour = bf.AMBER if refused else bf.GREEN
        bf.text(draw, (x0 + 34, y), row["claimed"], size=30, color=bf.INK, bold=True, mono=True)
        bf.text(draw, (x0 + 300, y), row["observed"], size=30, color=bf.INK, bold=True, mono=True)
        bf.text(draw, (x0 + 590, y), f"{row['verdict']}  {row['status']}", size=28,
                color=colour, bold=True)
        y += 66
    return bf.text(draw, (x0 + 34, y + 18), data["note"], size=22, color=bf.MUTED,
                   max_width=x1 - x0 - 68, spacing=6)


DRAW = {
    "quote": draw_quote, "story": draw_story, "table": draw_table,
    "callout": draw_callout, "recall": draw_recall, "close": draw_close,
    "schema": draw_schema, "admission": draw_admission, "oracle": draw_oracle,
}


def draw_outro(spec):
    """The end card. Full width, no presenter -- the one frame a judge might pause on.

    It exists because the final shot cannot simply be held: a presenter clip is a fixed 6.04s
    and freezing a person mid-blink to buy reading time looks like a stall. A card that was
    always meant to be still does not have that problem, and it keeps the repository URL on
    screen for longer than anyone needs to write it down.
    """
    image = bf.background()
    draw = ImageDraw.Draw(image)

    y = bf.text(draw, (PANEL_MARGIN + 60, 340), "MemoryStand", size=96, color=bf.INK, bold=True)
    y = bf.text(draw, (PANEL_MARGIN + 60, y + 34),
                "An agent memory store that will not promote a claim its own metrics contradict.",
                size=38, color=bf.MUTED, max_width=W - 2 * PANEL_MARGIN - 120, spacing=10)
    bf.text(draw, (PANEL_MARGIN + 60, y + 56), "github.com/tomyimkc/memorystand",
            size=34, color=bf.GREEN, mono=True)

    bf.text(draw, (PANEL_MARGIN, H - 116), spec["presenterDisclosure"],
            size=21, color=bf.FAINT, max_width=W - 2 * PANEL_MARGIN)
    bf.text(draw, (W - 470, H - 116), "candidateOnly · no AGI claim", size=19, color=bf.FAINT)
    return image


def main() -> int:
    spec = json.loads(SCRIPT_JSON.read_text())
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    for beat in spec["beats"]:
        side, data = beat["presenterSide"], beat["panelData"]
        render = DRAW[data["kind"]]

        # Two passes. The first draws onto a scratch canvas purely to find out how tall the
        # content actually is; the second sizes the panel to that and centres it in the band.
        # Measuring beats guessing: the six beats differ by nearly 300px of content, and a
        # single fixed height left four of them with a lake of empty panel under the text.
        probe = panel_box(side)
        bottom = render(ImageDraw.Draw(bf.background()), probe, data)
        # Beat 02 is a pull-quote with no citation, so the footnote band is only reserved when
        # there is something to put in it.
        footnote = data.get("footnote")
        tail = (FOOTNOTE_LIFT + FOOTNOTE_GAP) if footnote else 40
        height = min(BAND_BOTTOM - BAND_TOP, bottom - probe[1] + tail)
        top = BAND_TOP + (BAND_BOTTOM - BAND_TOP - height) // 2

        image = bf.background()
        draw = ImageDraw.Draw(image)
        box = panel_box(side, top, top + height)

        bf.rounded_panel(image, box, fill=bf.PANEL_2, outline=bf.BORDER_LIGHT, width=2)
        render(draw, box, data)
        if footnote:
            bf.text(draw, (box[0] + 34, box[3] - FOOTNOTE_LIFT), footnote,
                    size=19, color=bf.FAINT, max_width=box[2] - box[0] - 68)

        # Title and footer align to the PANEL's column, not the frame's. Pinned to the frame
        # they sit underneath the presenter on the beats where he stands left -- the first cut
        # lost the title entirely and clipped the disclosure to "...otograph.". Moving with the
        # panel keeps every word in the half of the screen nobody is standing in.
        px0, px1 = box[0], box[2]
        bf.text(draw, (px0, 96), spec["title"], size=30, color=bf.MUTED, bold=True)

        # The disclosure rides on every single frame, not just a title card. A viewer who joins
        # halfway still sees it, and it cannot be lost to an edit.
        bf.text(draw, (px0, H - 116), spec["presenterDisclosure"],
                size=21, color=bf.FAINT, max_width=px1 - px0 - 330)
        bf.text(draw, (px1 - 300, H - 116), "candidateOnly · no AGI claim",
                size=19, color=bf.FAINT)

        out = OUT_DIR / f"{beat['id']}.png"
        image.save(out)
        print(f"  wrote {out.name}  panel={beat['panelData']['kind']}  speaker={beat['presenterSide']}")

    draw_outro(spec).save(OUT_DIR / "99-outro.png")
    print("  wrote 99-outro.png  panel=outro  speaker=none")

    print(f"\n  {len(spec['beats']) + 1} panel(s) in {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
