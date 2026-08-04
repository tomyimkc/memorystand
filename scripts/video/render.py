#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Render the MemoryStand evidence-first demo video: narration, video, burned captions.

Reads ``docs/demo/video-timeline.json`` (the same single source of truth
``scripts/video/build_frames.py`` reads) and the frames it wrote to
``artifacts/video/frames/``, then:

  1. Synthesises per-cue narration with macOS ``say``, time-fits each cue to its
     scene's share of the scene duration with ffmpeg's ``atempo`` (chained so no single
     factor exceeds the 0.5-2.0 range ``atempo`` accepts), and concatenates the cues into
     one continuous narration track.
  2. Builds one video segment per scene (each frame held for its scene's exact duration,
     with a small Ken-Burns drift for visual interest) and concatenates them.
  3. Burns captions. This machine's ffmpeg build has neither ``drawtext`` (no
     libfreetype) nor ``subtitles`` (no libass) compiled in -- confirmed by probing
     ``ffmpeg -filters`` before writing this, not assumed -- so captions are rendered as
     transparent PNG overlays (one per non-blank interval) and composited with ffmpeg's
     ``overlay`` filter instead of a text filter.
  4. Muxes video + narration + captions into ``artifacts/video/memorystand-demo.mp4``
     (1920x1080, 30fps, H.264 crf 18, AAC 192k) and writes the selectable
     ``artifacts/video/memorystand-demo.srt`` alongside it.

Prints the final duration and fails loudly (non-zero exit) if it is at or past the
hackathon's hard 3:00 cutoff -- judges are not required to watch past that.

Re-running this script does not require repeating the frame-composition or capture
steps: it only reads ``docs/demo/video-timeline.json`` and
``artifacts/video/frames/*.png``.

Usage:
    .venv/bin/python scripts/video/render.py [--voice Samantha] [--rate 175]
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
TIMELINE_PATH = ROOT / "docs" / "demo" / "video-timeline.json"
FRAMES_DIR = ROOT / "artifacts" / "video" / "frames"
OUT_DIR = ROOT / "artifacts" / "video"
WORK_DIR = OUT_DIR / "render"

WIDTH = 1920
HEIGHT = 1080
FPS = 30
HARD_MAX_DURATION_S = 180.0  # 3:00 -- judges are not required to watch past this


def run(command: list[str], *, capture: bool = False) -> str:
    result = subprocess.run(command, check=True, text=True, capture_output=capture)
    return result.stdout.strip() if capture else ""


def ffprobe_duration(path: Path) -> float:
    return float(
        run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture=True,
        )
    )


def srt_time(seconds: float) -> str:
    millis = round(seconds * 1000)
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def atempo_chain(factor: float) -> str:
    """ffmpeg's ``atempo`` only accepts 0.5-2.0 per instance; chain instances for more."""
    factors: list[float] = []
    remaining = factor
    while remaining > 2.0:
        factors.append(2.0)
        remaining /= 2.0
    while remaining < 0.5:
        factors.append(0.5)
        remaining /= 0.5
    factors.append(remaining)
    return ",".join(f"atempo={item:.8f}" for item in factors)


def allocate_cue_durations(cues: list[str], scene_seconds: float) -> list[float]:
    """Split a scene's duration across its cues, weighted by word count."""
    weights = [max(1, len(cue.split())) for cue in cues]
    total = sum(weights)
    raw = [scene_seconds * weight / total for weight in weights]
    minimum = min(2.5, scene_seconds / len(cues))
    allocated = [max(minimum, item) for item in raw]
    scale = scene_seconds / sum(allocated)
    allocated = [item * scale for item in allocated]
    allocated[-1] += scene_seconds - sum(allocated)
    return allocated


def check_ffmpeg_filter(name: str) -> bool:
    listing = run(["ffmpeg", "-hide_banner", "-filters"], capture=True)
    return bool(re.search(rf"\b{re.escape(name)}\b", listing))


# ---------------------------------------------------------------------------
# Narration
# ---------------------------------------------------------------------------


def build_narration(timeline: dict[str, Any], work: Path, *, voice: str, rate: int) -> tuple[Path, Path, float]:
    say = shutil.which("say")
    if not say:
        raise SystemExit("macOS 'say' is required to synthesise narration.")
    cue_dir = work / "narration-cues"
    cue_dir.mkdir(parents=True, exist_ok=True)
    concat_lines: list[str] = []
    srt_blocks: list[str] = []
    cursor = 0.0
    cue_number = 1

    for scene in timeline["scenes"]:
        cues = list(scene["cues"])
        allocations = allocate_cue_durations(cues, float(scene["durationSeconds"]))
        for cue, allocation in zip(cues, allocations, strict=True):
            stem = cue_dir / f"cue-{cue_number:03d}"
            text_path = stem.with_suffix(".txt")
            aiff_path = stem.with_suffix(".aiff")
            spoken_path = stem.with_name(stem.name + "-spoken.wav")
            fitted_path = stem.with_suffix(".wav")
            text_path.write_text(cue + "\n", encoding="utf-8")
            run([say, "-v", voice, "-r", str(rate), "-f", str(text_path), "-o", str(aiff_path)])
            run(
                [
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-i", str(aiff_path),
                    "-ar", "48000", "-ac", "1",
                    str(spoken_path),
                ]
            )
            spoken_duration = ffprobe_duration(spoken_path)
            # Leave a small pad so a cue never runs right up against the next one.
            target_spoken = max(0.5, allocation - 0.35)
            filters: list[str] = []
            if spoken_duration > target_spoken:
                filters.append(atempo_chain(spoken_duration / target_spoken))
            filters.extend([f"apad=pad_dur={allocation:.6f}", f"atrim=0:{allocation:.6f}"])
            run(
                [
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-i", str(spoken_path),
                    "-af", ",".join(filters),
                    "-ar", "48000", "-ac", "1",
                    str(fitted_path),
                ]
            )
            concat_lines.append(f"file '{fitted_path.resolve()}'")
            start, end = cursor, cursor + allocation
            srt_blocks.append(f"{cue_number}\n{srt_time(start)} --> {srt_time(end)}\n{cue}\n")
            cursor = end
            cue_number += 1

    narration = work / "narration.wav"
    concat_path = cue_dir / "concat.txt"
    concat_path.write_text("\n".join(concat_lines) + "\n", encoding="utf-8")
    run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "concat", "-safe", "0", "-i", str(concat_path),
            "-c", "copy", str(narration),
        ]
    )
    captions = OUT_DIR / "memorystand-demo.srt"
    captions.write_text("\n".join(srt_blocks), encoding="utf-8")
    return narration, captions, cursor


# ---------------------------------------------------------------------------
# Video segments
# ---------------------------------------------------------------------------


def build_video(timeline: dict[str, Any], work: Path) -> Path:
    segments_dir = work / "segments"
    segments_dir.mkdir(parents=True, exist_ok=True)
    concat_lines: list[str] = []
    for index, scene in enumerate(timeline["scenes"]):
        source = FRAMES_DIR / scene["frame"]
        if not source.is_file():
            raise SystemExit(f"Missing frame: {source} -- run scripts/video/build_frames.py first.")
        destination = segments_dir / f"{index:02d}-{scene['id']}.mp4"
        duration = float(scene["durationSeconds"])
        # A very small, alternating Ken-Burns drift -- enough to keep a static frame from
        # reading as a dead slide, subtle enough not to distract from the evidence text.
        direction = 1 if index % 2 == 0 else -1
        frame_count = max(1, round(duration * FPS))
        x_expr = "iw/2-(iw/zoom/2)" if direction > 0 else f"(iw-iw/zoom)*(1-on/{frame_count})"
        filter_graph = (
            "scale=2000:1125,"
            f"zoompan=z='min(zoom+0.00006,1.02)':x='{x_expr}':"
            f"y='ih/2-(ih/zoom/2)':d=1:s={WIDTH}x{HEIGHT}:fps={FPS},"
            "format=yuv420p"
        )
        run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-loop", "1", "-framerate", str(FPS), "-t", f"{duration:.6f}",
                "-i", str(source),
                "-vf", filter_graph,
                "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                "-pix_fmt", "yuv420p", "-r", str(FPS),
                str(destination),
            ]
        )
        concat_lines.append(f"file '{destination.resolve()}'")
    concat_path = segments_dir / "concat.txt"
    concat_path.write_text("\n".join(concat_lines) + "\n", encoding="utf-8")
    silent = work / "silent.mp4"
    run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "concat", "-safe", "0", "-i", str(concat_path),
            "-c", "copy", str(silent),
        ]
    )
    return silent


# ---------------------------------------------------------------------------
# Captions: PNG overlays (this ffmpeg build has no drawtext/subtitles filter)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Cue:
    start: float
    end: float
    text: str


_TIMECODE = r"(\d{2}):(\d{2}):(\d{2}),(\d{3})"
_CUE_RE = re.compile(rf"{_TIMECODE}\s*-->\s*{_TIMECODE}")


def _to_seconds(hh: str, mm: str, ss: str, ms: str) -> float:
    return int(hh) * 3600 + int(mm) * 60 + int(ss) + int(ms) / 1000.0


def parse_srt(path: Path) -> list[Cue]:
    blocks = re.split(r"\n\s*\n", path.read_text(encoding="utf-8").strip())
    cues: list[Cue] = []
    for block in blocks:
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        idx = next((i for i, ln in enumerate(lines) if _CUE_RE.search(ln)), None)
        if idx is None:
            continue
        match = _CUE_RE.search(lines[idx])
        assert match is not None
        g = match.groups()
        start, end = _to_seconds(*g[0:4]), _to_seconds(*g[4:8])
        text = " ".join(lines[idx + 1 :]).strip()
        cues.append(Cue(start, end, text))
    return cues


def _caption_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in (
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ):
        if Path(candidate).is_file():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, selected: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    words = text.split()
    if not words:
        return [""]
    lines, current = [], words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        box = draw.textbbox((0, 0), candidate, font=selected, stroke_width=2)
        if box[2] - box[0] <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def render_caption_png(cue: Cue, destination: Path, *, font_size: int = 38, bottom_margin: int = 34) -> None:
    # Anchored low on purpose: build_frames.py reserves everything below y=950 for
    # captions, so a smaller bottom margin puts the band in empty space instead of
    # over evidence panels.
    image = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    selected = _caption_font(font_size)
    lines: list[str] = []
    for source_line in cue.text.splitlines():
        lines.extend(_wrap(draw, source_line, selected, WIDTH - 320))
    line_gap = 10
    measured = [draw.textbbox((0, 0), ln, font=selected, stroke_width=3) for ln in lines]
    heights = [box[3] - box[1] for box in measured]
    total_height = sum(heights) + line_gap * max(0, len(lines) - 1)
    pad_x, pad_y = 32, 18
    box_width = max(box[2] - box[0] for box in measured) + pad_x * 2
    box_height = total_height + pad_y * 2
    left = (WIDTH - box_width) // 2
    top = HEIGHT - bottom_margin - box_height
    draw.rounded_rectangle(
        (left, top, left + box_width, top + box_height),
        radius=16,
        fill=(4, 8, 14, 210),
        outline=(255, 255, 255, 70),
        width=2,
    )
    y = top + pad_y
    for line, box, height in zip(lines, measured, heights, strict=True):
        line_width = box[2] - box[0]
        draw.text(
            ((WIDTH - line_width) / 2, y),
            line,
            font=selected,
            fill=(240, 244, 250, 255),
            stroke_width=3,
            stroke_fill=(4, 8, 14, 255),
        )
        y += height + line_gap
    image.save(destination)


def build_caption_overlay(captions: Path, work: Path, *, total_duration: float) -> Path:
    cues = parse_srt(captions)
    if not cues:
        raise SystemExit(f"{captions} parsed to zero cues -- cannot build the caption overlay.")
    overlay_dir = work / "captions"
    overlay_dir.mkdir(parents=True, exist_ok=True)
    blank = overlay_dir / "blank.png"
    Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0)).save(blank)

    entries: list[tuple[Path, float]] = []
    cursor = 0.0
    for index, cue in enumerate(cues, start=1):
        if cue.start > cursor:
            entries.append((blank, cue.start - cursor))
        image_path = overlay_dir / f"caption-{index:03d}.png"
        render_caption_png(cue, image_path)
        entries.append((image_path, max(0.05, cue.end - cue.start)))
        cursor = cue.end
    if cursor < total_duration:
        entries.append((blank, total_duration - cursor))

    manifest = overlay_dir / "captions.ffconcat"
    lines = ["ffconcat version 1.0"]
    for image_path, duration in entries:
        lines.append(f"file '{image_path.resolve()}'")
        lines.append(f"duration {duration:.6f}")
    lines.append(f"file '{entries[-1][0].resolve()}'")
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return manifest


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--voice", default="Samantha", help="macOS 'say' voice (default: Samantha, en_US)")
    parser.add_argument("--rate", type=int, default=175, help="words per minute for 'say' (default: 175)")
    args = parser.parse_args()

    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise SystemExit(f"{tool} is required and was not found on PATH")

    if not TIMELINE_PATH.is_file():
        raise SystemExit(f"Missing {TIMELINE_PATH}")
    timeline = json.loads(TIMELINE_PATH.read_text(encoding="utf-8"))

    if timeline.get("candidateOnly") is not True or timeline.get("canClaimAGI") is not False:
        raise SystemExit(
            "docs/demo/video-timeline.json violates the claim boundary: "
            "candidateOnly must be true and canClaimAGI must be false."
        )

    scenes = timeline.get("scenes", [])
    if not scenes:
        raise SystemExit("docs/demo/video-timeline.json has no scenes.")
    total_planned = sum(float(s["durationSeconds"]) for s in scenes)
    target = float(timeline.get("targetDurationSeconds", total_planned))
    if not math.isclose(total_planned, target, abs_tol=1.0):
        raise SystemExit(
            f"Scene durations sum to {total_planned:.1f}s but targetDurationSeconds is "
            f"{target:.1f}s -- docs/demo/video-timeline.json is internally inconsistent."
        )
    if total_planned >= HARD_MAX_DURATION_S:
        raise SystemExit(
            f"docs/demo/video-timeline.json plans {total_planned:.1f}s, at or past the "
            f"hard {HARD_MAX_DURATION_S:.0f}s (3:00) cutoff -- shorten scenes before rendering."
        )

    missing_frames = [s["frame"] for s in scenes if not (FRAMES_DIR / s["frame"]).is_file()]
    if missing_frames:
        raise SystemExit(
            "Missing frame(s): " + ", ".join(missing_frames) + "\nRun scripts/video/build_frames.py first."
        )

    # This build's ffmpeg has neither filter -- confirmed by probing, not assumed. If a
    # future ffmpeg build on this machine DOES have one of them, that is a genuine
    # improvement opportunity (a native text filter is cheaper than per-cue PNGs), not
    # something this script needs to special-case today.
    has_drawtext = check_ffmpeg_filter("drawtext")
    has_subtitles = check_ffmpeg_filter("subtitles")
    print(f"ffmpeg drawtext filter available: {has_drawtext}; subtitles filter available: {has_subtitles}")

    WORK_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print("Synthesising narration...")
    narration, captions, narration_total = build_narration(timeline, WORK_DIR, voice=args.voice, rate=args.rate)
    print(f"  narration track: {narration_total:.2f}s, captions: {captions}")

    print("Building video segments...")
    silent = build_video(timeline, WORK_DIR)

    print("Building caption overlay (PNG-based -- no drawtext/subtitles filter on this ffmpeg build)...")
    caption_manifest = build_caption_overlay(captions, WORK_DIR, total_duration=total_planned)

    final = OUT_DIR / "memorystand-demo.mp4"
    print(f"Muxing final video: {final}")
    run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(silent),
            "-i", str(narration),
            "-f", "concat", "-safe", "0", "-i", str(caption_manifest),
            "-filter_complex", "[0:v][2:v]overlay=0:0:shortest=1[v]",
            "-map", "[v]",
            "-map", "1:a:0",
            "-t", f"{total_planned:.6f}",
            "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-r", str(FPS),
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            "-metadata", "title=MemoryStand Evidence-First Demo",
            str(final),
        ]
    )

    duration = ffprobe_duration(final)
    minutes, seconds = divmod(duration, 60)
    print(f"\nFinal duration: {duration:.2f}s ({int(minutes)}:{seconds:05.2f})")
    if duration >= HARD_MAX_DURATION_S:
        raise SystemExit(
            f"FAIL: rendered duration {duration:.2f}s is at or past the hard "
            f"{HARD_MAX_DURATION_S:.0f}s (3:00) cutoff."
        )

    print(f"Wrote {final}")
    print(f"Wrote {captions}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
