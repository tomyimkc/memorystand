#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""MemoryStand -- terminal-capture layer for the submission demo video.

Renders four evidence frames to PNG, at 1920x1080, from REAL command output:

  1. EXPLAIN -- proof the vector index is real (vector search + prefix spans),
     run LIVE against the local 3-node-adjacent CockroachDB container
     (crdb-memorystand) and its already-seeded ~101-row fixture tenant.
  2. Node-loss survival (failover) -- read from the already-captured
     benchmarks/failover.md rather than re-run, because re-running kills a
     real container node and takes over a minute; re-running it here would
     not make the evidence any more real, only slower and destructive to a
     cluster other in-flight work on this machine may depend on.
  3. SERIALIZABLE concurrency proof -- read from the already-captured
     benchmarks/concurrency.md for the same reason (it also mutates a shared
     local tenant).
  4. `memorystand recall` -- run LIVE against the local database, read-only.

Every frame says, in its own footer, exactly where its content came from: a
live command run by this script just now, or a specific file in this repo
plus that file's own SHA-256 at capture time. Nothing here is invented; two
of the four are re-renders of real, previously captured evidence, and this
script says so on screen rather than passing them off as freshly run.

Rendering: PIL (Pillow), which IS available in this repo's .venv (verified
before writing this script; the alternative path described in the operator's
brief -- generate HTML and screenshot it with Playwright -- was not needed).
Font: Menlo (macOS system monospace, ships at /System/Library/Fonts/Menlo.ttc),
with a DejaVu Sans Mono fallback for non-macOS hosts.

Usage:
    .venv/bin/python scripts/video/capture_terminal.py

Env overrides (all optional):
    MEMORYSTAND_ROOT           repo root (default: this file's repo)
    MEMORYSTAND_VIDEO_OUTPUT   output dir (default: <root>/artifacts/video/capture)
    MEMORYSTAND_CONTAINER      local CockroachDB container name (default: crdb-memorystand)
    MEMORYSTAND_DSN            local DSN (default: postgresql://root@localhost:26257/defaultdb?sslmode=disable)
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(os.environ.get("MEMORYSTAND_ROOT", Path(__file__).resolve().parents[2]))
OUT = Path(os.environ.get("MEMORYSTAND_VIDEO_OUTPUT", ROOT / "artifacts/video/capture"))
CONTAINER = os.environ.get("MEMORYSTAND_CONTAINER", "crdb-memorystand")
DSN = os.environ.get("MEMORYSTAND_DSN", "postgresql://root@localhost:26257/defaultdb?sslmode=disable")

WIDTH, HEIGHT = 1920, 1080
MARGIN_X = 72
HEADER_H = 190
FOOTER_H = 110

BG = (10, 14, 20)          # matches frontend/index.html --bg
PANEL = (18, 24, 34)       # --panel
PANEL_2 = (13, 19, 27)     # --panel-2
BORDER = (35, 43, 56)      # --border
TEXT = (234, 238, 244)     # --text
TEXT_DIM = (139, 150, 168)  # --text-dim
TEXT_FAINT = (91, 100, 120)  # --text-faint
GREEN = (53, 212, 136)
AMBER = (245, 185, 61)
RED = (255, 93, 106)
ACCENT = (77, 163, 255)

BODY_SIZE = 27
HEADER_SIZE = 34
LABEL_SIZE = 20
FOOTER_SIZE = 19
LINE_SPACING = 8


# ---------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------

_FONT_CACHE: dict[tuple[int, bool], ImageFont.FreeTypeFont] = {}


def _find_font(bold: bool) -> tuple[str, int | None]:
    """Return (path, ttc_index) for the first available monospace font."""
    candidates: list[tuple[str, int | None]] = [
        ("/System/Library/Fonts/Menlo.ttc", 1 if bold else 0),
        ("/System/Library/Fonts/Supplemental/Andale Mono.ttf", None),
        (
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
            if bold
            else "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
            None,
        ),
    ]
    for path, index in candidates:
        if Path(path).is_file():
            return path, index
    raise RuntimeError(
        "No monospace TrueType font found (looked for Menlo, Andale Mono, DejaVu Sans Mono). "
        "Install one, or add its path to _find_font()."
    )


def mono(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    key = (size, bold)
    if key not in _FONT_CACHE:
        path, index = _find_font(bold)
        _FONT_CACHE[key] = (
            ImageFont.truetype(path, size, index=index) if index is not None else ImageFont.truetype(path, size)
        )
    return _FONT_CACHE[key]


# ---------------------------------------------------------------------------
# Text layout
# ---------------------------------------------------------------------------


def wrap_line(draw: ImageDraw.ImageDraw, line: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """Wrap one logical line of terminal text to fit max_width, preferring to break
    on whitespace but hard-breaking a single unbroken token that is itself wider than
    max_width (e.g. a long stack-trace line with no spaces near the wrap point)."""
    if not line:
        return [""]
    if draw.textlength(line, font=font) <= max_width:
        return [line]

    words = line.split(" ")
    out: list[str] = []
    current = ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if draw.textlength(candidate, font=font) <= max_width:
            current = candidate
            continue
        if current:
            out.append(current)
            current = ""
        # word itself may still be too wide (e.g. a long unbroken token) -- hard-break it
        while draw.textlength(word, font=font) > max_width:
            lo, hi = 1, len(word)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if draw.textlength(word[:mid], font=font) <= max_width:
                    lo = mid
                else:
                    hi = mid - 1
            out.append(word[:lo])
            word = word[lo:]
        current = word
    if current:
        out.append(current)
    return out or [""]


# ---------------------------------------------------------------------------
# Frame model + renderer
# ---------------------------------------------------------------------------


@dataclass
class Frame:
    filename: str
    eyebrow: str               # e.g. "LIVE LOCAL CLUSTER" / "CAPTURED EARLIER"
    eyebrow_color: tuple[int, int, int]
    title: str                 # e.g. "$ EXPLAIN SELECT ... (vector search)"
    body_lines: list[str]
    footer: str                 # provenance line, always shown
    accent_matches: Sequence[str] = field(default_factory=list)  # substrings to highlight green


def render_frame(frame: Frame, destination: Path) -> None:
    image = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(image)

    # header band
    draw.rectangle((0, 0, WIDTH, HEADER_H), fill=PANEL)
    draw.line((0, HEADER_H, WIDTH, HEADER_H), fill=BORDER, width=2)

    label_font = mono(LABEL_SIZE, bold=True)
    eyebrow_w = draw.textlength(frame.eyebrow, font=label_font) + 32
    draw.rounded_rectangle(
        (MARGIN_X, 34, MARGIN_X + eyebrow_w, 34 + 38),
        radius=19,
        outline=frame.eyebrow_color,
        width=2,
    )
    draw.text((MARGIN_X + 16, 34 + 9), frame.eyebrow, font=label_font, fill=frame.eyebrow_color)

    title_font = mono(HEADER_SIZE, bold=True)
    draw.text((MARGIN_X, 96), frame.title, font=title_font, fill=TEXT)

    # body: dark terminal panel
    body_top = HEADER_H + 36
    body_bottom = HEIGHT - FOOTER_H - 20
    draw.rounded_rectangle((MARGIN_X, body_top, WIDTH - MARGIN_X, body_bottom), radius=14, fill=PANEL_2, outline=BORDER, width=2)

    body_font = mono(BODY_SIZE)
    inner_x = MARGIN_X + 34
    max_text_width = (WIDTH - MARGIN_X - 34) - inner_x

    wrapped: list[str] = []
    for raw_line in frame.body_lines:
        wrapped.extend(wrap_line(draw, raw_line, body_font, max_text_width))

    line_h = body_font.getbbox("Xg")[3] - body_font.getbbox("Xg")[1] + LINE_SPACING
    available_lines = max(1, (body_bottom - body_top - 28) // line_h)
    truncated = len(wrapped) > available_lines
    # Reserve one line's worth of vertical space for the truncation notice itself, so
    # it never overlaps the last real line of content.
    max_lines = available_lines - 1 if truncated else available_lines
    shown = wrapped[:max_lines]

    y = body_top + 20
    for line in shown:
        color = TEXT
        stripped = line.strip()
        if stripped.startswith("==>"):
            color = ACCENT
        elif stripped.startswith("***"):
            color = RED
        elif any(match in line for match in frame.accent_matches):
            color = GREEN
        elif stripped.startswith("#") or stripped.startswith("--"):
            color = TEXT_DIM
        draw.text((inner_x, y), line, font=body_font, fill=color)
        y += line_h

    if truncated:
        draw.text(
            (inner_x, y),
            f"... ({len(wrapped) - max_lines} more line(s) not shown in this frame)",
            font=mono(BODY_SIZE - 4),
            fill=TEXT_FAINT,
        )

    # footer: provenance -- wrapped to the canvas width so a long citation never runs
    # off the right edge.
    footer_font = mono(FOOTER_SIZE)
    draw.line((MARGIN_X, HEIGHT - FOOTER_H, WIDTH - MARGIN_X, HEIGHT - FOOTER_H), fill=BORDER, width=1)
    footer_max_width = WIDTH - 2 * MARGIN_X
    footer_lines: list[str] = []
    for raw_line in frame.footer.splitlines():
        footer_lines.extend(wrap_line(draw, raw_line, footer_font, footer_max_width))
    footer_line_h = footer_font.getbbox("Xg")[3] - footer_font.getbbox("Xg")[1] + 6
    fy = HEIGHT - FOOTER_H + 22
    for line in footer_lines[:2]:
        draw.text((MARGIN_X, fy), line, font=footer_font, fill=TEXT_DIM)
        fy += footer_line_h

    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination, format="PNG")


# ---------------------------------------------------------------------------
# Evidence sources
# ---------------------------------------------------------------------------


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_command(cmd: list[str], *, env: dict[str, str], timeout: int = 60) -> tuple[str, int]:
    result = subprocess.run(
        cmd,
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    combined = (result.stdout or "") + (result.stderr or "")
    return combined, result.returncode


def local_env() -> dict[str, str]:
    env = dict(os.environ)
    env["MEMORYSTAND_DSN"] = DSN
    env["COCKROACH_DSN"] = DSN
    env["MEMORYSTAND_EMBED_STUB"] = env.get("MEMORYSTAND_EMBED_STUB", "1")
    env["PYTHONPATH"] = str(ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["NO_COLOR"] = "1"
    return env


def require_container_running() -> None:
    result = subprocess.run(
        ["docker", "ps", "--filter", f"name=^{CONTAINER}$", "--filter", "status=running", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if CONTAINER not in result.stdout.split():
        raise RuntimeError(
            f"Local CockroachDB container '{CONTAINER}' is not running (docker ps shows no match). "
            "This script talks to an already-running local cluster; start it first "
            "(e.g. ./scripts/run-local.sh)."
        )


EXPLAIN_SCRIPT = """
import sys
sys.path.insert(0, %(root)r)
from backend import db, embeddings

tenant = sys.argv[1]
conn = db.get_conn()
try:
    with conn.cursor() as cur:
        cur.execute("ANALYZE agent_memories")
        conn.commit()
        vec = embeddings.to_pgvector(
            embeddings.embed("checkout-api circuit breaker timeout gateway latency incident")
        )
        cur.execute(
            "EXPLAIN SELECT memory_id FROM agent_memories "
            "WHERE tenant_id = %%s AND verdict = 'accepted' "
            "ORDER BY embedding <=> %%s LIMIT 5",
            (tenant, vec),
        )
        rows = cur.fetchall()
    conn.commit()
finally:
    db.put_conn(conn)

text = "\\n".join(r[0] for r in rows)
print(text)
print()
print(f"vector search node present: {'vector search' in text}")
print(f"prefix spans line present:  {'prefix spans' in text}")
"""


def capture_explain(manifest: dict) -> Frame:
    require_container_running()
    env = local_env()
    fixture_tenant_out, code = run_command(
        [sys.executable, "-c", "from db.seed.seed import DEFAULT_TENANT_ID; print(DEFAULT_TENANT_ID)"],
        env=env,
    )
    if code != 0:
        raise RuntimeError(f"Could not resolve the fixture tenant id:\n{fixture_tenant_out}")
    fixture_tenant = fixture_tenant_out.strip()

    script = EXPLAIN_SCRIPT % {"root": str(ROOT)}
    output, code = run_command([sys.executable, "-c", script, fixture_tenant], env=env)
    if code != 0:
        raise RuntimeError(f"Live EXPLAIN command failed (exit {code}):\n{output}")
    if "vector search node present: True" not in output or "prefix spans line present:  True" not in output:
        raise RuntimeError(f"Live EXPLAIN did not show the expected vector-search plan:\n{output}")

    manifest["explain"] = {
        "kind": "live-command",
        "command": "EXPLAIN SELECT memory_id FROM agent_memories WHERE tenant_id = ... AND verdict = 'accepted' ORDER BY embedding <=> ... LIMIT 5",
        "fixtureTenant": fixture_tenant,
        "exitCode": code,
        "capturedAtUtc": datetime.now(timezone.utc).isoformat(),
        "stdoutSha256": hashlib.sha256(output.encode("utf-8")).hexdigest(),
    }

    body = [
        "$ EXPLAIN SELECT memory_id FROM agent_memories",
        "      WHERE tenant_id = %s AND verdict = 'accepted'",
        "      ORDER BY embedding <=> %s LIMIT 5",
        "",
        *output.strip("\n").splitlines(),
    ]
    return Frame(
        filename="06-explain-vector-search.png",
        eyebrow="LIVE LOCAL CLUSTER",
        eyebrow_color=ACCENT,
        title="The vector index is real: EXPLAIN",
        body_lines=body,
        footer=(
            f"Run live just now against the local CockroachDB container ({CONTAINER}), "
            f"fixture tenant {fixture_tenant[:8]} (~101 seeded memories)."
        ),
        accent_matches=["vector search", "prefix spans", "present: True"],
    )


def capture_recall(manifest: dict) -> Frame:
    require_container_running()
    env = local_env()
    query = "checkout-api circuit breaker timeout gateway latency incident"
    output, code = run_command(
        [sys.executable, str(ROOT / "cli/memorystand.py"), "recall", "--query", query],
        env=env,
    )
    if code != 0:
        raise RuntimeError(f"Live `memorystand recall` failed (exit {code}):\n{output}")

    manifest["recall"] = {
        "kind": "live-command",
        "command": f'memorystand recall --query "{query}"',
        "exitCode": code,
        "capturedAtUtc": datetime.now(timezone.utc).isoformat(),
        "stdoutSha256": hashlib.sha256(output.encode("utf-8")).hexdigest(),
    }

    body = [f'$ memorystand recall --query "{query}"', ""] + output.strip("\n").splitlines()
    return Frame(
        filename="09-recall-cli.png",
        eyebrow="LIVE LOCAL CLUSTER",
        eyebrow_color=ACCENT,
        title="memorystand recall -- read-only, ranked by CockroachDB's vector index",
        body_lines=body,
        footer=f"Run live just now against the local CockroachDB container ({CONTAINER}).",
        accent_matches=[],
    )


def extract_first_fenced_block(markdown: str) -> str:
    match = re.search(r"```(?:[a-zA-Z0-9_-]*)\n(.*?)```", markdown, re.S)
    if not match:
        raise RuntimeError("No fenced code block found in the source markdown.")
    return match.group(1).rstrip("\n")


def clean_markdown_line(line: str) -> str:
    line = line.strip()
    line = re.sub(r"^- ", "", line)
    line = line.replace("**", "")
    line = line.replace("`", "")
    return line


def capture_failover(manifest: dict) -> Frame:
    path = ROOT / "benchmarks/failover.md"
    text = path.read_text(encoding="utf-8")
    block = extract_first_fenced_block(text)
    file_hash = sha256_file(path)

    manifest["failover"] = {
        "kind": "read-from-file",
        "sourceFile": "benchmarks/failover.md",
        "sourceSha256": file_hash,
        "readAtUtc": datetime.now(timezone.utc).isoformat(),
    }

    return Frame(
        filename="07-failover-node-loss.png",
        eyebrow="CAPTURED EARLIER -- benchmarks/failover.md",
        eyebrow_color=AMBER,
        title="Node-loss survival: ./scripts/cluster-demo.sh failover",
        body_lines=block.splitlines(),
        footer=(
            f"Read from benchmarks/failover.md (sha256 {file_hash[:16]}...), not re-run here -- "
            "re-running kills a real container node and takes over a minute. Three containers on "
            "one machine: proves the replication mechanism, not a datacentre failure."
        ),
        accent_matches=["12 successful reads, 0 failed", "memories_still_readable", "101"],
    )


def capture_concurrency(manifest: dict) -> Frame:
    path = ROOT / "benchmarks/concurrency.md"
    text = path.read_text(encoding="utf-8")
    file_hash = sha256_file(path)

    def section(heading_pattern: str) -> str:
        match = re.search(
            rf"{heading_pattern}\n(.*?)(?=\n#{{1,3}} |\Z)", text, re.S
        )
        return match.group(1).strip("\n") if match else ""

    part1 = section(r"### Part 1: lost-update proof \(writers=10\)")
    part2 = section(r"### Part 2: TOCTOU admission guard \(two contradictory concurrent remembers\)")
    overall = section(r"## Overall result")

    lines: list[str] = ["$ python scripts/race_demo.py --writers 10", ""]
    lines.append("Part 1: lost-update proof (writers=10)")
    for raw in part1.splitlines():
        if raw.strip().startswith("**Captured live traceback"):
            break
        if raw.strip():
            lines.append("  " + clean_markdown_line(raw))
    lines.append("")
    lines.append("Part 2: TOCTOU admission guard (two contradictory concurrent remembers)")
    for raw in part2.splitlines():
        if raw.strip():
            lines.append("  " + clean_markdown_line(raw))
    lines.append("")
    lines.append("Overall result")
    for raw in overall.splitlines():
        if raw.strip():
            lines.append("  " + clean_markdown_line(raw))

    manifest["concurrency"] = {
        "kind": "read-from-file",
        "sourceFile": "benchmarks/concurrency.md",
        "sourceSha256": file_hash,
        "readAtUtc": datetime.now(timezone.utc).isoformat(),
    }

    return Frame(
        filename="08-concurrency-serializable.png",
        eyebrow="CAPTURED EARLIER -- benchmarks/concurrency.md",
        eyebrow_color=AMBER,
        title="SERIALIZABLE isolation under real concurrent writers (SQLSTATE 40001)",
        body_lines=lines,
        footer=(
            f"Read from benchmarks/concurrency.md (sha256 {file_hash[:16]}...), not re-run here -- "
            "it mutates a shared local tenant. Driven through the real backend.db.retry_serializable "
            "/ backend.memory.remember code paths, no isolation from the application."
        ),
        accent_matches=["PASS", "ALL CHECKS PASSED"],
    )


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    manifest: dict = {
        "schemaVersion": "1.0",
        "candidateOnly": True,
        "canClaimAGI": False,
        "renderer": "PIL (Pillow)",
        "frames": {},
    }

    builders = [
        ("EXPLAIN (live)", capture_explain),
        ("failover (read from file)", capture_failover),
        ("concurrency (read from file)", capture_concurrency),
        ("recall CLI (live)", capture_recall),
    ]

    produced: list[tuple[str, int]] = []
    for label, builder in builders:
        print(f"capturing: {label}")
        frame = builder(manifest["frames"])
        destination = OUT / frame.filename
        render_frame(frame, destination)
        size = destination.stat().st_size
        produced.append((str(destination), size))
        print(f"  wrote {destination} ({size} bytes)")

    manifest_path = OUT / "terminal-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"\nTerminal capture complete: {OUT}")
    for path, size in produced:
        print(f"  {path}  ({size} bytes)")
    print(f"  manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
