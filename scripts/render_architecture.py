#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Render docs/architecture.png -- a static architecture diagram for the Devpost
"architectural diagram" upload field.

ARCHITECTURE.md already has a hand-written mermaid diagram; this script renders an
equivalent PNG using the same visual system as the demo video
(scripts/video/build_frames.py -- same background, panel, font and colour helpers, reused
by import rather than copied, so the diagram, the video and the live dashboard read as one
system) because Devpost's field wants an image, not a mermaid block a browser has to render.

Every box and edge below was checked against the code that makes it true, not against
ARCHITECTURE.md's prose alone:

  backend/handler.py    route table, kill-switch-first, shared-secret gating, CORS
  backend/trust.py      grant_standing(), assert_no_model_calls(), the outcome ladder
  backend/evidence.py   CloudWatch re-check: confirmed / contradicted / unavailable /
                         not_verifiable, +-15min window, 50% tolerance, direction-first
  backend/memory.py     admission control, the vector-index-backed recall query
  backend/embeddings.py MEMORYSTAND_EMBED_MODEL default (Titan Text Embeddings V2)
  backend/bedrock_client.py  MEMORYSTAND_CHAT_MODEL default (Nova Lite), ModelUnavailable
  backend/agent.py      the deterministic fallback when Bedrock is unreachable
  db/schema.sql          agent_memories' vector index prefix, tool_audit's row TTL
  cli/memorystand.py     the CLI imports backend/ directly -- it is not an HTTP client
  infra/deploy.sh         SSM param names, Lambda memory/timeout/concurrency, the
                          deploy-time-only DSN bake
  infra/keepwarm.sh      EventBridge Scheduler -> GET /health every 5 min
  ARCHITECTURE.md         the MCP service account name/role finding (independently
                          reproduced in that doc's own "verified live" transcript)

An edge is only drawn here if one of the files above shows it happening. Where the code
does not show an edge (see the return value of main() / the module docstring at the
bottom), none is drawn -- see the caller-facing note this script prints after saving.

Usage:
    .venv/bin/python scripts/render_architecture.py
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = REPO_ROOT / "docs" / "architecture.png"

# Reuse the video's drawing primitives and palette directly rather than re-deriving them,
# so this diagram, the demo video frames, and the live dashboard's own CSS custom
# properties all read as one visual system. scripts/video has no __init__.py (it is not a
# package), so it is added to sys.path and imported as a top-level module rather than via
# `from scripts.video import build_frames`.
VIDEO_DIR = REPO_ROOT / "scripts" / "video"
if str(VIDEO_DIR) not in sys.path:
    sys.path.insert(0, str(VIDEO_DIR))
import build_frames as bf  # noqa: E402

WIDTH, HEIGHT = bf.WIDTH, bf.HEIGHT

# ---------------------------------------------------------------------------
# Arrow drawing -- not in build_frames.py (that module never draws a graph edge), so it is
# added here rather than bent into that module's scene-frame shape.
# ---------------------------------------------------------------------------


def _dashed_line(draw: ImageDraw.ImageDraw, p1, p2, color, width: int) -> None:
    x1, y1 = p1
    x2, y2 = p2
    dist = math.hypot(x2 - x1, y2 - y1)
    if dist < 1:
        return
    dash, gap = 10.0, 7.0
    ux, uy = (x2 - x1) / dist, (y2 - y1) / dist
    pos = 0.0
    while pos < dist:
        seg_end = min(pos + dash, dist)
        draw.line(
            [(x1 + ux * pos, y1 + uy * pos), (x1 + ux * seg_end, y1 + uy * seg_end)],
            fill=color,
            width=width,
        )
        pos += dash + gap


def _arrowhead(draw: ImageDraw.ImageDraw, tip, direction, color, size: int = 12) -> None:
    ang = math.atan2(direction[1], direction[0])
    a1 = ang + math.radians(150)
    a2 = ang - math.radians(150)
    p1 = (tip[0] + size * math.cos(a1), tip[1] + size * math.sin(a1))
    p2 = (tip[0] + size * math.cos(a2), tip[1] + size * math.sin(a2))
    draw.polygon([tip, p1, p2], fill=color)


def poly_arrow(
    draw: ImageDraw.ImageDraw,
    points: list[tuple[float, float]],
    color,
    *,
    width: int = 3,
    dashed: bool = False,
    head: int = 11,
) -> None:
    """Draw a polyline through ``points`` with one arrowhead at the final point."""
    for a, b in zip(points, points[1:]):
        if dashed:
            _dashed_line(draw, a, b, color, width)
        else:
            draw.line([a, b], fill=color, width=width)
    (x1, y1), (x2, y2) = points[-2], points[-1]
    _arrowhead(draw, (x2, y2), (x2 - x1, y2 - y1), color, size=head)


def edge_label(draw: ImageDraw.ImageDraw, xy, value, *, color, size: int = 15, max_width: int = 320) -> None:
    bf.text(draw, xy, value, size=size, color=color, max_width=max_width, spacing=3)


# ---------------------------------------------------------------------------
# Panel content
# ---------------------------------------------------------------------------


_OVERFLOWS: list = []


def _measure(box, title, lines, *, title_size, body_size, pad) -> int:
    """Lay the panel out on a scratch canvas and return the y its content would end at.

    Measuring by drawing is not elegant, but it is exact: it uses the same wrapping code,
    fonts and spacing as the real render, so it cannot disagree with what finally appears.
    Anything cleverer would be a second implementation of the layout and would drift.
    """
    x0, y0, x1, y1 = box
    # Measure from the panel's REAL origin, not from 0. The first version laid out on a
    # scratch canvas starting at (pad, pad) and then compared the resulting y against the
    # box's absolute bottom -- so every panel under-measured by its own y0, auto-fit never
    # engaged, and the bullets clipped exactly as before. The measurement has to share the
    # coordinate space of the thing it is predicting.
    scratch = Image.new("RGB", (max(64, x1 - x0), y1 + 4000))
    d = ImageDraw.Draw(scratch)
    y = bf.text(d, (pad, y0 + pad), title, size=title_size, color=bf.MUTED, bold=True,
                max_width=x1 - x0 - 2 * pad) + 14
    for line in lines:
        y = bf.text(d, (pad, y), f"•  {line}", size=body_size, color=bf.INK,
                    max_width=x1 - x0 - 2 * pad, spacing=4) + 10
    return y


def image_width(box) -> int:
    return max(64, box[2] - box[0])


def panel(
    image: Image.Image,
    box: tuple[int, int, int, int],
    title: str,
    lines: list[str],
    *,
    fill=None,
    outline=None,
    title_color=None,
    title_size: int = 19,
    body_size: int = 16,
    pad: int = 22,
) -> int:
    """Draw a panel, shrinking the body text until it actually fits inside the box.

    The first version of this diagram used a fixed body size and silently clipped six panels'
    bullets -- sentences cut off mid-word, invisible from the code and obvious the moment
    anyone opened the PNG. Trimming the copy by hand fixed it once and would have broken again
    on the next edit.

    So the size is now derived rather than asserted: try the requested size, step down until
    the laid-out content fits, and only record an overflow if even the floor is too small. A
    panel that is one line too long now renders slightly smaller instead of lying.
    """
    fill = fill if fill is not None else bf.PANEL_2
    outline = outline if outline is not None else bf.BORDER_LIGHT
    title_color = title_color if title_color is not None else bf.MUTED
    x0, y0, x1, y1 = box

    size = body_size
    tsize = title_size
    while size >= 11:
        end = _measure(box, title, lines, title_size=tsize, body_size=size, pad=pad)
        if end <= y1 - 8:
            break
        size -= 1
        if size < body_size - 2 and tsize > 15:
            tsize -= 1
    else:
        _OVERFLOWS.append((title, 0, 0, y1))

    bf.rounded_panel(image, box, fill=fill, outline=outline, width=2)
    draw = ImageDraw.Draw(image)
    y = bf.text(draw, (x0 + pad, y0 + pad), title, size=tsize, color=title_color, bold=True,
                max_width=x1 - x0 - 2 * pad) + 14
    for line in lines:
        y = bf.text(draw, (x0 + pad, y), f"•  {line}", size=size, color=bf.INK,
                    max_width=x1 - x0 - 2 * pad, spacing=4) + 10
    return y


def main() -> int:
    image = bf.background()
    draw = ImageDraw.Draw(image)

    # ---- Header ------------------------------------------------------
    bf.text(draw, (90, 40), "COCKROACHDB × AWS — AGENTIC MEMORY HACKATHON", size=21, color=bf.MUTED, bold=True)
    bf.text(draw, (88, 70), "MemoryStand — Architecture", size=46, bold=True)
    bf.text(
        draw,
        (90, 128),
        'Storage is not action authority: only an entity-bound outcome corroborated by Amazon '
        'CloudWatch reaches "verified" and may steer an autonomous action.',
        size=21,
        color=bf.ACCENT,
        bold=True,
        max_width=1740,
    )

    # ---- Column 1: entry points, Lambda, config ----------------------
    entry_box = (50, 205, 480, 380)
    lambda_box = (50, 400, 480, 600)
    config_box = (50, 620, 480, 930)

    panel(
        image,
        entry_box,
        "ENTRY POINTS",
        [
            "Browser → AWS Amplify Hosting (static frontend/, no build step)",
            "curl / agent client → HTTPS to the Function URL",
            "memorystand CLI → imports backend/ in-process, own DSN (--dsn / COCKROACH_DSN) — "
            "see the dashed path below; it never touches the Function URL",
        ],
    )

    panel(
        image,
        lambda_box,
        "AWS Lambda Function URL",
        [
            "handler.py :: lambda_handler; AWS-level auth NONE, application policy below",
            "kill switch fails CLOSED if SSM is unreadable",
            "HMAC secret on all writes; demo credential is restricted to one published tenant",
            "7 routes · 512 MB · 30s timeout · reserved concurrency 15",
        ],
        outline=bf.ACCENT,
    )

    panel(
        image,
        config_box,
        "AWS SSM Parameter Store + EventBridge",
        [
            "/memorystand/shared_secret (SecureString) — read at request time, cached 60s",
            "/memorystand/kill_switch (String) — read at request time, on every write",
            "/memorystand/dsn (SecureString) — DEPLOY-TIME ONLY (infra/deploy.sh); the "
            "Lambda's own execution role is never granted read access to it",
            "Amazon EventBridge Scheduler — GET /health every 5 min, keep-warm (infra/keepwarm.sh)",
        ],
    )

    # ---- Column 2: the three route lanes ------------------------------
    pink_box = (510, 205, 900, 425)
    green_box = (510, 450, 900, 700)
    gray_box = (510, 760, 900, 1000)

    RED_FILL = bf.blend(bf.RED, bf.PANEL_2, 0.14)
    GREEN_FILL = bf.blend(bf.GREEN, bf.PANEL_2, 0.14)

    panel(
        image,
        pink_box,
        "BEDROCK-TOUCHING ROUTES · secret-gated",
        [
            "POST /ingest → memory.remember() → embeddings.embed()",
            "POST /decide → memory.recall() → agent.propose() → decisions.decide()",
            "VERIFIED may steer autonomously; ATTESTED stays advisory + approval",
            "Bedrock first; deterministic fallback on error or zero quota",
        ],
        fill=RED_FILL,
        outline=bf.RED,
        title_color=bf.RED,
    )

    green_bottom = panel(
        image,
        green_box,
        "POST /confirm_outcome — the promotion path",
        [
            "trust.grant_standing(tenant_id, decision_id, evidence)",
            "assert_no_model_calls() runs first — checked structurally on the live path",
            "evidence.verify() re-checks the claim before recording: exact entity, enough "
            "datapoints, direction + magnitude",
            "secret-gated + tenant-scoped",
        ],
        fill=GREEN_FILL,
        outline=bf.GREEN,
        title_color=bf.GREEN,
    )

    panel(
        image,
        gray_box,
        "DEMO-SCOPED READ ROUTES",
        [
            "unauthenticated reads are restricted to the server-selected public demo tenant",
            "operator secret may inspect another tenant; knowing a tenant UUID is insufficient",
            "GET /recall → admitted memories only; k is bounded to 1–20",
            "GET /timemachine + /diff → AS OF SYSTEM TIME and current-state comparison",
            "GET /health → db version, GC window, kill switch, embedding provenance, circuit breakers",
        ],
        outline=bf.BORDER_LIGHT,
        title_color=bf.MUTED,
    )

    # ---- Column 3: CockroachDB Cloud, the memory layer -----------------
    crdb_box = (930, 205, 1400, 1000)
    panel(
        image,
        crdb_box,
        "CockroachDB Cloud — the memory layer",
        [
            "agent_memories — VECTOR INDEX (tenant_id, verdict, embedding vector_cosine_ops)",
            "verdict: accepted / quarantined / superseded — decides what recall can ever see",
            "trust_tier: unconfirmed / attested / verified / disputed — only verified may act "
            "autonomously; attested requires approval",
            "agent_decisions — consulted vs cited vs produced; every referenced memory id is "
            "validated as admitted and same-tenant",
            "belief_snapshots — SHA-256 checkpoint of the admitted set, re-derived with "
            "AS OF SYSTEM TIME",
            "tool_audit — every governed call, native row-level TTL = 180 days",
            "MVCC time travel is the audit trail: nothing is deleted, a correction writes a new row",
        ],
        outline=bf.ACCENT,
        title_color=bf.ACCENT,
        title_size=21,
    )

    # ---- Column 4: Bedrock, CloudWatch, MCP -----------------------------
    bedrock_box = (1430, 205, 1870, 460)
    cw_box = (1430, 485, 1870, 750)
    mcp_box = (1430, 775, 1870, 1000)

    by = panel(
        image,
        bedrock_box,
        "Amazon Bedrock (us-west-2)",
        [
            "Titan Text Embeddings V2 — amazon.titan-embed-text-v2:0",
            "Nova Lite (reasoning) — amazon.nova-lite-v1:0, via Converse",
        ],
        fill=RED_FILL,
        outline=bf.RED,
        title_color=bf.RED,
    )
    quota_box = (1430 + 18, by + 4, 1870 - 18, 460 - 14)
    bf.rounded_panel(image, quota_box, fill=bf.blend(bf.AMBER, bf.PANEL_2, 0.16), outline=bf.AMBER, radius=14, width=2)
    bf.text(
        draw,
        (quota_box[0] + 14, quota_box[1] + 10),
        "Bedrock quota here has no usable on-demand capacity. Live /decide can use a disclosed "
        "router standby that reasoning_source names. The promotion path calls no model either way.",
        size=15,
        color=bf.INK,
        bold=True,
        max_width=quota_box[2] - quota_box[0] - 28,
        spacing=3,
    )

    panel(
        image,
        cw_box,
        "Amazon CloudWatch — outcome evidence",
        [
            "GetMetricStatistics: ±15 min around the decision",
            "≥3 datapoints/window; exact normalized entity dimension match",
            "check direction, then magnitude (50% tolerance)",
            "confirmed → verified; contradicted → refused (HTTP 400)",
            "unavailable / not_verifiable → attested + approval",
            "PagerDuty / human are not re-queryable here → attested",
        ],
        fill=GREEN_FILL,
        outline=bf.GREEN,
        title_color=bf.GREEN,
        title_size=18,
    )

    panel(
        image,
        mcp_box,
        "CockroachDB Cloud — Managed MCP Server",
        [
            "judge-facing, parallel to the app — no application code in this path",
            "memorystand-mcp-readonly — name is aspirational: the role is Cluster Developer + "
            "Cluster Operator Writer (write-capable)",
            "used here only to read: select_query / explain_query (trust-tier counts, "
            "vector-index plan)",
        ],
        outline=bf.PURPLE,
        title_color=bf.PURPLE,
    )

    # ---- Edges ----------------------------------------------------------
    # Column 1 internal flow.
    poly_arrow(draw, [(265, 345), (265, 370)], bf.ACCENT, width=3)
    poly_arrow(draw, [(265, 560), (265, 585)], bf.MUTED, width=2, dashed=True)
    edge_label(draw, (300, 596), "reads live, cached 60s", color=bf.FAINT, size=13, max_width=190)

    # Lambda fans out to the three route lanes.
    poly_arrow(draw, [(480, 460), (510, 315)], bf.ACCENT, width=3)
    poly_arrow(draw, [(480, 460), (510, 575)], bf.ACCENT, width=3)
    poly_arrow(draw, [(480, 460), (510, 862)], bf.ACCENT, width=3)

    # CLI bypass: entry_box -> straight to CockroachDB, never through Lambda. Routed
    # through the gutter to the left of column 2 and under every panel so it never
    # crosses a box it does not actually pass through in the code.
    poly_arrow(
        draw,
        [(480, 330), (495, 330), (495, 1015), (1165, 1015), (1165, 1000)],
        bf.PURPLE,
        width=3,
        dashed=True,
    )
    edge_label(draw, (90, 1012), "CLI: direct backend import — bypasses the Lambda entirely", color=bf.PURPLE, size=14, max_width=420)

    # Pink lane -> CockroachDB (short, adjacent columns) and -> Bedrock (elbowed through
    # the header gutter so it never cuts across the CockroachDB panel between them).
    poly_arrow(draw, [(900, 290), (930, 290)], bf.RED, width=3)
    edge_label(draw, (600, 176), "INSERT agent_memories / agent_decisions", color=bf.RED, size=13, max_width=300)
    poly_arrow(draw, [(890, 205), (890, 178), (1440, 178), (1440, 205)], bf.RED, width=3, dashed=True)
    edge_label(draw, (1230, 150), "embeddings (+ reasoning for /decide)", color=bf.RED, size=14, max_width=380)

    # Green lane -> CockroachDB (short) and -> CloudWatch (elbowed through the gutter
    # between columns 3 and 4 so it clears the Bedrock panel sitting above CloudWatch).
    poly_arrow(draw, [(900, 575), (930, 575)], bf.GREEN, width=4)
    poly_arrow(
        draw,
        [(890, 450), (890, 192), (1415, 192), (1415, 485), (1430, 485)],
        bf.GREEN,
        width=3,
        dashed=True,
    )
    edge_label(draw, (600, 1042), "evidence.verify() runs BEFORE anything is recorded",
               color=bf.GREEN, size=14, max_width=430)

    # Gray lane -> CockroachDB (short, reads only).
    poly_arrow(draw, [(900, 862), (930, 862)], bf.MUTED, width=3)
    edge_label(draw, (905, 838), "SELECT … verdict='accepted'", color=bf.MUTED, size=13, max_width=260)

    # MCP -> CockroachDB: a parallel, judge-facing read path with no application code.
    poly_arrow(draw, [(1430, 887), (1400, 887)], bf.PURPLE, width=3, dashed=True)
    edge_label(draw, (1180, 862), "direct SQL, read path only here", color=bf.PURPLE, size=13, max_width=220)

    # The single most important label in the diagram, drawn after every panel so it cannot be
    # occluded, in the gap deliberately left between the promotion panel and the read routes.
    bf.chip(draw, (green_box[0] + 22, green_box[3] + 16), "ZERO MODEL CALLS", color=bf.GREEN, size=17)

    if _OVERFLOWS:
        print("PANELS THAT DO NOT FIT EVEN AT THE MINIMUM FONT SIZE:")
        for title, _, _, y1 in _OVERFLOWS:
            print(f"  {title!r}")
        raise SystemExit("Diagram not written. Remove a bullet or grow the box.")

    # ---- Footer -----------------------------------------------------------
    bf.text(draw, (90, 1030), "github.com/tomyimkc/memorystand", size=20, color=bf.ACCENT, bold=True)
    right_label = "Live: main.d19xad9aeccy3e.amplifyapp.com"
    selected = bf.font(20, bold=True)
    w = int(draw.textlength(right_label, font=selected))
    draw.text((1830 - w, 1030), right_label, font=selected, fill=bf.MUTED)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    image.save(OUTPUT_PATH)
    print(f"wrote {OUTPUT_PATH} ({image.size[0]}x{image.size[1]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
