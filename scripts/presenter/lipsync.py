#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Measure, and correct, lip sync INSIDE a generated clip.

    mouth-region motion  vs  audio envelope  ->  the lag between them

WHY THIS EXISTS, AND WHY compose.py --check DID NOT CATCH IT.

``compose.py --check`` cross-correlates each source clip's audio against the finished film and
reports 0.0ms. That is a true statement about the wrong thing: it proves the compositor puts a
clip's audio exactly where the cut intends, and says nothing about whether the clip's own lips
match the clip's own voice. grok generates picture and sound together but not perfectly aligned,
so a film assembled with flawless 0.0ms placement can still be visibly out of sync everywhere.
Measured across sixteen clips, the sound arrives AFTER the lips by 0-167ms, median 42ms; five
clips exceed the ~45ms at which audio/vision offset becomes perceptible.

HOW IT IS MEASURED. Frame-to-frame absolute difference restricted to a band over the mouth gives
a "visual speech activity" signal; per-frame audio RMS gives the acoustic one. Speech makes both
spike together, so the lag that maximises their correlation is the offset. The band is centred on
the subject using the same motion centroid the compositor crops with, because these portraits are
composed left or right and a fixed centre band lands on the wrong part of the face half the time.

HOW IT IS CORRECTED. The picture is delayed to meet the late audio (``tpad``), rather than the
audio being advanced to meet the picture. Advancing the audio would clip the start of the first
word -- speech begins at t=0 in every one of these clips -- whereas delaying the picture only
holds the first frame a few dozen milliseconds longer, which is invisible.

THE DIRECTION IS VERIFIED, NOT ASSUMED. ``--verify`` applies a correction and re-measures, so the
sign convention is established by experiment. Getting it backwards would double the error while
looking like a fix.

    python scripts/presenter/lipsync.py                 # measure every clip
    python scripts/presenter/lipsync.py --verify        # prove the correction direction
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import wave
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CLIP_DIR = REPO_ROOT / "artifacts" / "presenter" / "clips"
CACHE = REPO_ROOT / "artifacts" / "presenter" / "lip-offsets.json"

FPS = 24
SR = 16000

# Beyond this the offset is perceptible and worth correcting. Below it, correcting costs a
# held frame for no gain.
AUDIBLE_MS = 25.0
MAX_SHIFT_MS = 260.0


def _subject_centre(clip: Path, w: int, h: int, frames) -> float:
    motion = abs(frames[1:] - frames[:-1]).sum(axis=0).sum(axis=0)
    import numpy as np

    motion = np.clip(motion - np.percentile(motion, 25), 0, None)
    if motion.sum() <= 0:
        return 0.5
    return float((motion * np.arange(w)).sum() / motion.sum() / w)


def _frames(clip: Path, w: int, h: int):
    import numpy as np

    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(clip), "-vf", f"scale={w}:{h}",
         "-pix_fmt", "gray", "-f", "rawvideo", "-"],
        capture_output=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.uint8).reshape(-1, h, w).astype(np.float32)


def measure(clip: Path) -> tuple[float, float]:
    """Return (lag_ms, correlation). Positive lag means the sound arrives after the lips."""
    import numpy as np

    w, h = 96, 118
    frames = _frames(clip, w, h)
    if len(frames) < 8:
        return 0.0, 0.0

    centre = _subject_centre(clip, w, h, frames)
    x0 = max(0, min(int((centre - 0.16) * w), w - 1))
    x1 = max(x0 + 4, min(int((centre + 0.16) * w), w))
    band = frames[:, int(h * 0.42):int(h * 0.64), x0:x1]
    visual = abs(band[1:] - band[:-1]).mean(axis=(1, 2))

    wav = Path("/tmp") / f"lipsync_{clip.stem}.wav"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(clip), "-ac", "1", "-ar", str(SR), "-y", str(wav)],
        check=True,
    )
    with wave.open(str(wav)) as handle:
        samples = np.frombuffer(handle.readframes(handle.getnframes()), dtype=np.int16).astype(np.float32)
    # The two streams need not be the same length -- tpad adds video frames, and a clip's audio
    # can end before its picture. Trimming both to the shorter is not cosmetic: without it the
    # trailing audio slices are empty, their mean is NaN, every correlation becomes NaN, and the
    # search silently falls through to k=0 and reports PERFECT SYNC. The first --verify run did
    # exactly that and printed "DIRECTION CONFIRMED" off a correlation of -1e9.
    per = SR // FPS
    usable = min(len(visual), len(samples) // per)
    if usable < 8:
        return 0.0, 0.0
    visual = visual[:usable]
    envelope = np.array([
        np.sqrt((samples[i * per:(i + 1) * per] ** 2).mean() + 1e-9) for i in range(usable)
    ])

    visual = (visual - visual.mean()) / (visual.std() + 1e-9)
    envelope = (envelope - envelope.mean()) / (envelope.std() + 1e-9)

    span = int(MAX_SHIFT_MS / 1000 * FPS)
    best_corr, best_k = -1e9, 0
    for k in range(-span, span + 1):
        shifted = np.roll(envelope, k)
        corr = float((visual * shifted).mean())
        if not np.isfinite(corr):
            raise SystemExit(f"{clip.name}: correlation is not finite -- refusing to guess a lag")
        if corr > best_corr:
            best_corr, best_k = corr, k
    # roll(envelope, k) moves the sound k frames LATER. The correlation peaks where the sound has
    # been pushed back onto the lips, so a negative k means the sound was already late by |k|.
    return -best_k * 1000.0 / FPS, best_corr


def offsets(clips: list[Path], cache_path: Path = CACHE) -> dict[str, float]:
    """Measured correction per clip, in seconds of picture delay. Cached; this never moves."""
    cache = json.loads(cache_path.read_text()) if cache_path.is_file() else {}
    changed = False
    for clip in clips:
        if clip.stem in cache:
            continue
        lag_ms, corr = measure(clip)
        shift = lag_ms if abs(lag_ms) >= AUDIBLE_MS else 0.0
        cache[clip.stem] = {"lagMs": round(lag_ms, 1), "correlation": round(corr, 3),
                            "delayPictureS": round(max(shift, 0.0) / 1000.0, 4)}
        changed = True
    if changed:
        cache_path.write_text(json.dumps(dict(sorted(cache.items())), indent=2) + "\n")
    return {k: v["delayPictureS"] for k, v in cache.items()}


def _corrected_copy(clip: Path, delay_s: float, out: Path) -> Path:
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-i", str(clip),
        "-filter_complex", f"[0:v]tpad=start_duration={delay_s:.4f}:start_mode=clone[v]",
        "-map", "[v]", "-map", "0:a", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-r", str(FPS), "-c:a", "aac", "-b:a", "160k", str(out),
    ], check=True, capture_output=True)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--verify", action="store_true",
                    help="apply the correction to the worst clip and re-measure, to prove the sign")
    args = ap.parse_args()

    clips = sorted(CLIP_DIR.glob("*.mp4"))
    if not clips:
        print("no clips", file=sys.stderr)
        return 2

    rows = []
    for clip in clips:
        lag, corr = measure(clip)
        rows.append((clip, lag, corr))
        flag = "  <-- perceptible" if abs(lag) > 45 else ""
        print(f"  {clip.stem:30s} sound is {lag:+7.0f} ms vs lips   r={corr:.2f}{flag}")

    if args.verify:
        clip, lag, _ = max(rows, key=lambda r: abs(r[1]))
        print(f"\n  VERIFY on the worst clip: {clip.stem} at {lag:+.0f} ms")
        out = Path("/tmp") / f"corrected_{clip.stem}.mp4"
        _corrected_copy(clip, max(lag, 0.0) / 1000.0, out)
        after, corr_after = measure(out)
        print(f"    delayed the picture by {max(lag, 0.0):.0f} ms")
        print(f"    before {lag:+.0f} ms   ->   after {after:+.0f} ms  (r={corr_after:.2f})")
        print("    " + ("DIRECTION CONFIRMED" if abs(after) < abs(lag)
                        else "WRONG DIRECTION -- do not apply"))
        return 0 if abs(after) < abs(lag) else 1

    offsets(clips)
    print(f"\n  offsets -> {CACHE.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
