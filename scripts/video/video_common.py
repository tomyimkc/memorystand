#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Shared ffprobe/ffmpeg/SRT/plan-parsing helpers for the demo-video verifiers.

Both ``verify_video.py`` (technical envelope) and ``verify_claims.py`` (claim
discipline) need to run ffprobe/ffmpeg and parse a ``.srt`` file the same way;
this module is the one place that logic lives so the two verifiers cannot
silently drift apart on what counts as "the video's duration" or "a caption
cue's text".

Nothing here talks to the network or mutates anything -- it only shells out
to the ``ffmpeg``/``ffprobe`` binaries and reads local files.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

# scripts/video/video_common.py -> scripts/video -> scripts -> repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]


def display_path(path: Path) -> str:
    """Render ``path`` relative to the repo root when possible, for readable output."""
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def sha256_file(path: Path) -> str:
    """Return the hex SHA-256 digest of ``path``'s bytes, read in chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ffprobe(video: Path) -> dict[str, Any]:
    """Run ``ffprobe -show_streams -show_format`` on ``video`` and return the parsed JSON.

    Raises ``RuntimeError`` (not the raw ``CalledProcessError``/``FileNotFoundError``)
    with the captured stderr, so a caller can print one clean line instead of a
    subprocess traceback.
    """
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(video)],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("ffprobe is not on PATH") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"ffprobe failed on {video}: {exc.stderr.strip()}") from exc
    return json.loads(result.stdout)


def detect_volume(video: Path) -> tuple[float | None, float | None]:
    """Return ``(mean_volume_db, max_volume_db)`` from ffmpeg's ``volumedetect`` filter.

    Returns ``(None, None)`` if ffmpeg's stderr does not contain the expected
    lines (e.g. no audio stream, or an ffmpeg build without the filter). A
    silent track shows up as a very negative ``mean_volume``; real narration
    does not -- this is the check that catches a rendered-but-silent take,
    which a bare ffprobe stream listing cannot.
    """
    result = subprocess.run(
        ["ffmpeg", "-nostdin", "-i", str(video), "-af", "volumedetect", "-vn", "-sn", "-f", "null", "-"],
        capture_output=True,
        text=True,
    )
    stderr = result.stderr
    mean_match = re.search(r"mean_volume:\s*(-?\d+(?:\.\d+)?)\s*dB", stderr)
    max_match = re.search(r"max_volume:\s*(-?\d+(?:\.\d+)?)\s*dB", stderr)
    mean_db = float(mean_match.group(1)) if mean_match else None
    max_db = float(max_match.group(1)) if max_match else None
    return mean_db, max_db


@dataclasses.dataclass(frozen=True)
class Cue:
    """One parsed SubRip caption cue."""

    index: int
    start_seconds: float
    end_seconds: float
    text: str


_TIMECODE = r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})"
_CUE_TIME_RE = re.compile(rf"{_TIMECODE}\s*-->\s*{_TIMECODE}")


def _timecode_to_seconds(hh: str, mm: str, ss: str, ms: str) -> float:
    return int(hh) * 3600 + int(mm) * 60 + int(ss) + int(ms) / 1000.0


def parse_srt(path: Path) -> list[Cue]:
    """Parse a ``.srt`` file into ``Cue`` records.

    Deliberately small and dependency-free (no ``srt`` PyPI package): this
    project's captions are machine-rendered, so the format is regular.
    Raises ``ValueError`` naming the first block with no recognisable
    timecode line, rather than silently dropping it. Raises ``ValueError``
    if the file parses to zero cues.
    """
    raw = path.read_text(encoding="utf-8-sig")
    blocks = re.split(r"\r?\n\r?\n+", raw.strip())
    cues: list[Cue] = []
    for block_number, block in enumerate(blocks, start=1):
        lines = [line for line in block.splitlines() if line.strip() != ""]
        if not lines:
            continue
        timecode_line_index = next(
            (i for i, line in enumerate(lines) if _CUE_TIME_RE.search(line)), None
        )
        if timecode_line_index is None:
            raise ValueError(
                f"{path}: block {block_number} has no 'HH:MM:SS,mmm --> HH:MM:SS,mmm' "
                f"timecode line: {block!r}"
            )
        match = _CUE_TIME_RE.search(lines[timecode_line_index])
        assert match is not None
        groups = match.groups()
        start = _timecode_to_seconds(*groups[0:4])
        end = _timecode_to_seconds(*groups[4:8])
        index_line = lines[timecode_line_index - 1] if timecode_line_index > 0 else None
        try:
            index = int(index_line.strip()) if index_line is not None else block_number
        except ValueError:
            index = block_number
        text = " ".join(lines[timecode_line_index + 1 :]).strip()
        cues.append(Cue(index=index, start_seconds=start, end_seconds=end, text=text))
    if not cues:
        raise ValueError(f"{path}: no cues parsed")
    return cues


_NARRATION_HEADER_RE = re.compile(r"\*\*Narration\b")


def extract_plan_narration_blocks(text: str) -> list[str]:
    """Pull blockquoted spoken narration out of a shot-script style plan doc.

    Matches this repo's own established convention (see ``docs/VIDEO.md``):
    a ``**Narration (...):**`` heading immediately followed by one or more
    ``> `` blockquote lines. Deliberately does NOT return the surrounding
    prose -- a plan doc's rules section legitimately *names* forbidden words
    while describing the rule (e.g. "never say multi-region"), and scanning
    that prose for claim discipline would flag the rule's own definition.
    Returns an empty list if the doc does not use this convention (e.g. it
    keeps narration in a separate timeline JSON instead).
    """
    lines = text.splitlines()
    blocks: list[str] = []
    i = 0
    while i < len(lines):
        if _NARRATION_HEADER_RE.search(lines[i]):
            i += 1
            quoted: list[str] = []
            while i < len(lines) and lines[i].lstrip().startswith(">"):
                quoted.append(lines[i].lstrip()[1:].strip())
                i += 1
            if quoted:
                blocks.append(" ".join(part for part in quoted if part))
            continue
        i += 1
    return blocks


def extract_timeline_cues(path: Path) -> list[str]:
    """Read per-scene spoken cue strings out of a ``video-timeline.json`` companion file.

    Expects the shape this project's ``docs/demo/video-timeline.json`` uses:
    ``{"scenes": [{"cues": ["...", "..."]}, ...]}``. Returns an empty list
    (not an error) if the file does not have that shape, so a caller can
    treat "no cues found" the same way whether the file is absent or simply
    structured differently.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    scenes = data.get("scenes") if isinstance(data, dict) else None
    if not isinstance(scenes, list):
        return []
    cues: list[str] = []
    for scene in scenes:
        if not isinstance(scene, dict):
            continue
        for cue in scene.get("cues", []) or []:
            if isinstance(cue, str) and cue.strip():
                cues.append(cue.strip())
    return cues
