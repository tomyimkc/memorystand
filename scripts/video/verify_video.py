#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Verify the MemoryStand demo video's technical envelope.

Checks (via ffprobe/ffmpeg -- not by trusting the render step):
  - duration is inside the selected cut's accepted window, and strictly under
    the hackathon's hard 3:00 cutoff (judges are not required to watch past it,
    so going over is a worse failure than finishing a little early)
  - 1920x1080, the selected cut's expected frame rate, H.264 video, AAC audio
  - the audio track actually has signal. A silent narration track passes a
    naive ffprobe stream check (the stream exists) but is a real, classic,
    humiliating failure mode -- this runs ffmpeg's ``volumedetect`` filter
    and asserts ``mean_volume`` clears a floor
  - a ``.srt`` caption file exists next to the video, parses, and its last
    cue ends at or before the video's own runtime

Writes ``artifacts/video/video-receipt.json`` (file SHA-256, the probe
summary, a UTC timestamp) whether it passes or fails, and exits non-zero
naming every check that failed.

Usage:
    python3 scripts/video/verify_video.py [video.mp4] [--profile auto|evidence|presenter]

With no arguments, verifies the newest ``artifacts/video/*.mp4``.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from fractions import Fraction
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import video_common  # noqa: E402

REPO_ROOT = video_common.REPO_ROOT

HARD_MAX_DURATION_S = 180.0  # 3:00 -- hard fail; judges may stop watching here
EXPECTED_WIDTH = 1920
EXPECTED_HEIGHT = 1080
FPS_TOLERANCE = 0.5
MIN_MEAN_VOLUME_DB = -50.0  # below this floor, treat the track as effectively silent
SRT_END_TOLERANCE_S = 0.5

PROFILES = {
    "evidence": {
        "minDurationSeconds": 155.0,
        "maxDurationSeconds": 175.0,
        "expectedFps": 30.0,
        "windowLabel": "2:35-2:55",
    },
    "presenter": {
        "minDurationSeconds": 45.0,
        "maxDurationSeconds": 120.0,
        "expectedFps": 24.0,
        "windowLabel": "0:45-2:00",
    },
}


def _frame_rate_to_float(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        return float(Fraction(value))
    except (ValueError, ZeroDivisionError):
        return 0.0


def _find_default_video() -> Path | None:
    video_dir = REPO_ROOT / "artifacts" / "video"
    if not video_dir.is_dir():
        return None
    candidates = sorted(video_dir.glob("*.mp4"), key=lambda p: p.stat().st_mtime)
    return candidates[-1] if candidates else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "video",
        type=Path,
        nargs="?",
        default=None,
        help="path to the rendered .mp4; defaults to the newest artifacts/video/*.mp4",
    )
    parser.add_argument("--srt", type=Path, default=None, help="defaults to <video> with a .srt suffix")
    parser.add_argument(
        "--profile",
        choices=("auto", *PROFILES),
        default="auto",
        help=(
            "technical envelope to apply; auto selects presenter for filenames containing "
            "'presenter', otherwise evidence"
        ),
    )
    args = parser.parse_args()

    video = args.video or _find_default_video()
    if video is None:
        parser.error("no video given and no artifacts/video/*.mp4 found -- render one first")
    assert video is not None  # for the type checker; parser.error() above never returns
    video = video.resolve()
    if not video.is_file():
        parser.error(f"video does not exist: {video}")

    profile_name = (
        "presenter"
        if args.profile == "auto" and "presenter" in video.stem.lower()
        else "evidence"
        if args.profile == "auto"
        else args.profile
    )
    profile = PROFILES[profile_name]
    min_duration = float(profile["minDurationSeconds"])
    max_duration = float(profile["maxDurationSeconds"])
    expected_fps = float(profile["expectedFps"])

    try:
        probe = video_common.ffprobe(video)
    except RuntimeError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    streams = probe.get("streams", [])
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

    duration = float(probe.get("format", {}).get("duration", 0.0))
    width = int(video_stream.get("width", 0))
    height = int(video_stream.get("height", 0))
    codec = video_stream.get("codec_name")
    fps = _frame_rate_to_float(video_stream.get("avg_frame_rate"))

    errors: list[str] = []

    if duration >= HARD_MAX_DURATION_S:
        errors.append(
            f"HARD FAIL: duration {duration:.2f}s is at or past the 3:00 (180s) cutoff -- "
            "judges are not required to watch past this"
        )
    if not (min_duration <= duration <= max_duration):
        errors.append(
            f"duration {duration:.2f}s is outside the {profile_name} cut's accepted "
            f"{profile['windowLabel']} window ({min_duration:.0f}-{max_duration:.0f}s)"
        )
    if (width, height) != (EXPECTED_WIDTH, EXPECTED_HEIGHT):
        errors.append(f"expected {EXPECTED_WIDTH}x{EXPECTED_HEIGHT}; got {width}x{height}")
    if abs(fps - expected_fps) > FPS_TOLERANCE:
        errors.append(
            f"{profile_name} cut expects ~{expected_fps:.0f}fps; got "
            f"{fps:.3f}fps ({video_stream.get('avg_frame_rate')!r})"
        )
    if codec != "h264":
        errors.append(f"expected H.264 video; got {codec!r}")

    mean_db: float | None = None
    max_db: float | None = None
    if audio_stream is None:
        errors.append("no AAC audio stream found -- narration track is missing")
    else:
        audio_codec = audio_stream.get("codec_name")
        if audio_codec != "aac":
            errors.append(f"expected AAC audio; got {audio_codec!r}")
        mean_db, max_db = video_common.detect_volume(video)
        if mean_db is None:
            errors.append("could not measure audio volume (ffmpeg volumedetect produced no mean_volume reading)")
        elif mean_db < MIN_MEAN_VOLUME_DB:
            errors.append(
                f"narration audio is effectively silent: mean_volume {mean_db:.1f}dB is "
                f"below the {MIN_MEAN_VOLUME_DB:.0f}dB floor"
            )

    srt_path = (args.srt.resolve() if args.srt else video.with_suffix(".srt"))
    cue_count = 0
    last_cue_end: float | None = None
    if not srt_path.is_file():
        errors.append(f"no .srt caption file found at {srt_path}")
    else:
        try:
            cues = video_common.parse_srt(srt_path)
        except ValueError as exc:
            errors.append(f".srt did not parse: {exc}")
        else:
            cue_count = len(cues)
            last_cue_end = max(cue.end_seconds for cue in cues)
            if last_cue_end > duration + SRT_END_TOLERANCE_S:
                errors.append(
                    f"last caption cue ends at {last_cue_end:.2f}s, after the video's "
                    f"{duration:.2f}s runtime"
                )

    receipt: dict[str, Any] = {
        "status": "PASS" if not errors else "FAIL",
        "checkedAtUtc": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "profile": {
            "name": profile_name,
            **profile,
        },
        "video": {
            "path": video_common.display_path(video),
            "bytes": video.stat().st_size,
            "sha256": video_common.sha256_file(video),
            "durationSeconds": duration,
            "width": width,
            "height": height,
            "fps": fps,
            "videoCodec": codec,
            "audioCodec": audio_stream.get("codec_name") if audio_stream else None,
            "meanVolumeDb": mean_db,
            "maxVolumeDb": max_db,
        },
        "srt": {
            "path": video_common.display_path(srt_path),
            "cueCount": cue_count,
            "lastCueEndSeconds": last_cue_end,
        },
        "errors": errors,
    }
    receipt_dir = REPO_ROOT / "artifacts" / "video"
    receipt_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = receipt_dir / "video-receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(json.dumps(receipt, indent=2, sort_keys=True))
    if errors:
        print(f"\nFAIL: {len(errors)} check(s) failed; receipt: {receipt_path}", file=sys.stderr)
        return 1
    print(f"\nPASS: technical envelope holds; receipt: {receipt_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
