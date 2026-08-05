#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Step 4: cut the verified shots and the data panels into one 1920x1080 film.

    panel png (1920x1080)   <- make_panels.py
      + presenter clip      <- make_clips.py, transcript-verified
        -> one segment per shot, trimmed to where the speech actually stops
          -> concat -> artifacts/video/memorystand-presenter.mp4

THREE THINGS THIS DOES THAT A NAIVE OVERLAY DOES NOT:

1. IT REFUSES UNVERIFIED FOOTAGE. Every shot must appear in docs/demo/presenter-verification.json
   with ``passed: true``. A clip sitting in the clips directory is not sufficient -- it has to have
   been transcribed and matched against the line it was given. The generator invents dialogue
   when left unsupervised, so "the file exists" is exactly the kind of evidence this project
   spends its README arguing against. Same rule as the trust ladder, applied to its own video.

2. IT TRIMS TO MEASURED SPEECH, NOT TO CLIP LENGTH. grok returns a fixed 6.04s regardless of
   how long the line takes. Measured across the fourteen shots that is 18.3s of a man standing
   silently -- and two shots are more than half dead air ("The failure isn't fraud." is 2.0s of
   speech in a 6.04s clip). Word-level timestamps give the real end, and each shot holds for
   HOLD_S past it so the cut lands on a beat instead of clipping the last consonant.

3. IT FEATHERS THE PRESENTER INTO THE FRAME. The clip is a 496x608 portrait with its own
   near-black office background; dropped in as a rectangle it reads as a sticker. A gradient
   alpha mask on the inner edge -- the edge facing the panel -- dissolves the seam. The mask is
   mirrored per beat because the presenter alternates sides.

4. IT FINDS THE SPEAKER RATHER THAN ASSUMING WHERE HE IS. Scaling a 496x608 portrait to 1080
   tall makes it ~880 wide against a 660px column, so 220px has to go. Which 220 depends on how
   the shot was actually composed, and generation does not guarantee that survives. The motion
   centroid locates him and the crop places him at SUBJECT_TARGET within his own column.

5. IT MEASURES ITS OWN LIP SYNC. ``--check`` cross-correlates every source clip against the
   finished film and fails the build if audio leads picture by more than 45ms. The first cut was
   469ms out by the end while total duration matched to 21ms -- see the note above the mux.

    python scripts/presenter/compose.py --check
    python scripts/presenter/compose.py --keep-segments   # leave the per-shot files for review
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts" / "presenter"))

from make_panels import PANEL_MARGIN, PRESENTER_W, W, H  # noqa: E402,F401

SCRIPT_JSON = REPO_ROOT / "docs" / "demo" / "presenter-script.json"
PANEL_DIR = REPO_ROOT / "artifacts" / "presenter" / "panels"
CLIP_DIR = REPO_ROOT / "artifacts" / "presenter" / "clips"
RECEIPT = REPO_ROOT / "docs" / "demo" / "presenter-verification.json"
SPANS = REPO_ROOT / "artifacts" / "presenter" / "speech-spans.json"
OUT = REPO_ROOT / "artifacts" / "video" / "memorystand-presenter.mp4"

# How long a shot holds after the last word. Long enough that the cut does not feel clipped,
# short enough that it does not read as a pause.
#
# This is a CEILING as well as a target: the hold is clamped to the clip's own length. Without
# that clamp three shots were asked for 6.2-7.4s from a 6.04s source, which loops the still
# panel while the presenter and his audio run out -- he freezes mid-gesture and the segment's
# video and audio tracks end at different times, which the concat demuxer then accumulates as
# drift. Reading time at the end belongs on a card that was always still, not on a frozen man.
HOLD_S = 0.45

# The end card, which is a still by design and can therefore hold as long as it likes.
OUTRO_S = 3.5  # snapped to the frame grid below

# Width of the alpha ramp on the presenter's inner edge, in pixels. 300 rather than the 150 this
# started at: the clip carries a real office behind him, noticeably lighter than the panel
# background, and a narrow ramp left a visible vertical seam down the middle of the frame.
#
# The other candidate was grading the clip darker to meet the background (eq brightness -0.07,
# contrast 1.10). Rendered side by side it was worse -- crushing the office to black turned the
# out-of-focus wall behind his head into a hard-edged black rectangle, trading a soft seam for a
# sharp one. Widening the ramp fixes the seam without touching a single pixel of the subject.
FEATHER = 300

FPS = 24


def snap(seconds: float) -> float:
    """Round a duration UP to a whole frame.

    This is the difference between a film that stays in sync and one that does not. ``-r 24``
    emits whole frames, so a 5.77s request becomes 5.875s of video against exactly 5.770s of
    audio -- video 0.105s long. The concat demuxer joins the two tracks independently, so that
    gap does not cancel out, it ACCUMULATES: measured at 0.04-0.11s across fifteen segments, the
    audio finished 1.3s ahead of the picture by the end card. On a lip-synced talking head that
    is the one defect a viewer cannot help noticing. Snapping to the frame grid and padding the
    audio to match makes every segment's two tracks exactly equal, so there is nothing to add up.
    """
    return math.ceil(seconds * FPS) / FPS


def run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"ffmpeg failed:\n{' '.join(cmd)}\n\n{proc.stderr[-2500:]}")


def _probe(path: Path, entries: str, stream: str | None = None) -> str:
    cmd = ["ffprobe", "-v", "error"]
    if stream:
        cmd += ["-select_streams", stream]
    cmd += ["-show_entries", entries, "-of", "csv=p=0", str(path)]
    return subprocess.run(cmd, capture_output=True, text=True).stdout.strip()


def _clip_size(clip: Path) -> tuple[int, int]:
    w, h = _probe(clip, "stream=width,height", "v:0").split(",")[:2]
    return int(w), int(h)


def speech_end(clip: Path, cache: dict) -> float:
    """Seconds at which the last word finishes. Cached -- whisper is slow and this never moves."""
    if clip.stem in cache:
        return float(cache[clip.stem][1])

    wav = Path(tempfile.gettempdir()) / f"compose_{clip.stem}.wav"
    run(["ffmpeg", "-v", "error", "-i", str(clip), "-ac", "1", "-ar", "16000", "-y", str(wav)])
    from faster_whisper import WhisperModel

    segs, _ = WhisperModel("tiny", device="cpu", compute_type="int8").transcribe(
        str(wav), word_timestamps=True
    )
    words = [w for s in segs for w in (s.words or [])]
    if not words:
        raise SystemExit(f"{clip.name}: no speech found -- refusing to guess its length")
    cache[clip.stem] = [round(words[0].start, 2), round(words[-1].end, 2)]
    return float(cache[clip.stem][1])


# Where the speaker should sit inside his own column, as a fraction of its width. Slightly
# off-centre and away from the panel, so he faces into the data rather than off the edge.
SUBJECT_TARGET = {"LEFT": 0.45, "RIGHT": 0.55}


def subject_centre(clip: Path, w: int = 124, h: int = 152) -> float:
    """Horizontal centre of the moving subject, as a fraction of the clip's width.

    The speaker moves; the office behind him does not. Summing frame-to-frame absolute
    difference gives a map dominated by head and shoulders, and its column centroid is a cheap,
    dependency-free stand-in for face detection.

    Worth measuring rather than assuming, because framing is not guaranteed to survive
    generation. Across the current clips it reports 36-43% on the beats composed left and 63-65%
    on the beats composed right -- close enough to the old fixed crop that it changes little
    today, which is rather the point: it confirms the assumption instead of trusting it, and it
    keeps holding when a continuity-seeded shot inherits an already-drifted frame.
    """
    import numpy as np

    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(clip), "-vf", f"scale={w}:{h}",
         "-pix_fmt", "gray", "-f", "rawvideo", "-"],
        capture_output=True,
    ).stdout
    frames = np.frombuffer(raw, dtype=np.uint8).reshape(-1, h, w).astype(np.int16)
    motion = np.abs(np.diff(frames, axis=0)).sum(axis=0).sum(axis=0).astype(np.float64)
    # Drop the noise floor so compression shimmer in the background cannot pull the centroid.
    motion = np.clip(motion - np.percentile(motion, 25), 0, None)
    if motion.sum() <= 0:
        return 0.5
    return float((motion * np.arange(w)).sum() / motion.sum() / w)


def make_mask(side: str, path: Path) -> None:
    """A 660x1080 grayscale ramp: opaque over the presenter, fading out toward the panel."""
    from PIL import Image

    mask = Image.new("L", (PRESENTER_W, H), 255)
    px = mask.load()
    for x in range(FEATHER):
        value = int(255 * (x / FEATHER) ** 0.8)
        # side LEFT -> presenter on the left -> fade the RIGHT edge, and vice versa.
        column = PRESENTER_W - 1 - x if side == "LEFT" else x
        for y in range(H):
            px[column, y] = value
    mask.save(path)


# Audio may lead the picture by no more than this before the shot is called out of sync.
# ITU-R BT.1359 puts the detectability threshold for audio-ahead at about 45ms; anything past
# that on a talking head is visible as bad dubbing.
MAX_LAG_MS = 45.0


def check_sync(timeline: list[tuple[str, float]]) -> bool:
    """Cross-correlate each source clip against the finished film. True if anything drifted.

    This exists because every cheaper check passed while the film was badly out of sync. Total
    duration matched to 21ms; per-segment stream durations matched to 0-42ms; the transcript
    landed roughly where expected. Only correlating the actual waveforms against the actual
    timeline found the half-second of accumulated slip. A build step that can only be validated
    by watching it is a build step that will ship broken, so this measures it instead.
    """
    import numpy as np

    sr = 16000
    wav_dir = Path(tempfile.gettempdir())

    def pcm(src: Path, name: str):
        out = wav_dir / f"synccheck_{name}.wav"
        run(["ffmpeg", "-v", "error", "-i", str(src), "-ac", "1", "-ar", str(sr), "-y", str(out)])
        import wave

        with wave.open(str(out)) as handle:
            return np.frombuffer(handle.readframes(handle.getnframes()), dtype=np.int16).astype(np.float64)

    film = pcm(OUT, "film")
    print(f"\n  sync check ({len(timeline)} shots, tolerance {MAX_LAG_MS:.0f} ms)")
    worst, worst_tag = 0.0, ""
    for tag, expected in timeline:
        source = pcm(CLIP_DIR / f"{tag}.mp4", tag)[: int(1.5 * sr)]
        lo = max(0, int((expected - 0.8) * sr))
        window = film[lo : int((expected + 0.8) * sr) + len(source)]
        if len(window) <= len(source):
            continue
        xc = np.correlate(window - window.mean(), source - source.mean(), mode="valid")
        lag_ms = ((lo + int(np.argmax(xc))) / sr - expected) * 1000
        if abs(lag_ms) > abs(worst):
            worst, worst_tag = lag_ms, tag
        flag = "  <-- DRIFT" if abs(lag_ms) > MAX_LAG_MS else ""
        print(f"    {tag:30s} expected {expected:6.2f}s   lag {lag_ms:+7.1f} ms{flag}")

    if abs(worst) > MAX_LAG_MS:
        print(f"\n  OUT OF SYNC: worst {worst:+.1f} ms at {worst_tag} (limit {MAX_LAG_MS:.0f} ms)")
        return True
    print(f"  in sync: worst {worst:+.1f} ms at {worst_tag or 'n/a'}")
    return False


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--keep-segments", action="store_true", help="do not delete the per-shot files")
    ap.add_argument("--check", action="store_true",
                    help="cross-correlate each source clip against the finished film and fail on drift")
    args = ap.parse_args()

    spec = json.loads(SCRIPT_JSON.read_text())
    if not RECEIPT.is_file():
        raise SystemExit(f"no {RECEIPT.name} -- run make_clips.py first; unverified shots are not composable")
    passed = {k for k, v in json.loads(RECEIPT.read_text())["shots"].items() if v.get("passed")}
    spans = json.loads(SPANS.read_text()) if SPANS.is_file() else {}

    work = REPO_ROOT / "artifacts" / "presenter" / "segments"
    work.mkdir(parents=True, exist_ok=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)

    masks = {s: work / f"mask-{s.lower()}.png" for s in ("LEFT", "RIGHT")}
    for side, path in masks.items():
        make_mask(side, path)

    shots = [
        (beat["id"], i, beat["presenterSide"])
        for beat in spec["beats"]
        for i in range(len(beat["shots"]))
    ]

    segments: list[Path] = []
    audio: list[Path] = []
    timeline: list[tuple[str, float]] = []
    total = 0.0
    for n, (beat_id, i, side) in enumerate(shots):
        tag = f"{beat_id}-{i}"
        if tag not in passed:
            raise SystemExit(
                f"{tag} is not recorded as verified in {RECEIPT.name}. "
                "Re-run make_clips.py; a shot that has not been checked does not go in the cut."
            )

        clip = CLIP_DIR / f"{tag}.mp4"
        panel = PANEL_DIR / f"{beat_id}.png"
        for needed in (clip, panel):
            if not needed.is_file():
                raise SystemExit(f"missing {needed}")

        clip_len = float(_probe(clip, "format=duration"))
        duration = snap(min(clip_len, speech_end(clip, spans) + HOLD_S))
        timeline.append((tag, total))
        total += duration

        # The clip is portrait; scaled to 1080 tall it is wider than the 660px presenter column,
        # so something has to be cropped. Rather than assume which side, find the speaker and put
        # him where he belongs in his own column.
        probe_w, probe_h = _clip_size(clip)
        scaled_w = round(probe_w * H / probe_h / 2) * 2
        crop_x = int(round(subject_centre(clip) * scaled_w - SUBJECT_TARGET[side] * PRESENTER_W))
        crop_x = max(0, min(crop_x, scaled_w - PRESENTER_W))
        overlay_x = 0 if side == "LEFT" else W - PRESENTER_W

        # Only the opening touches black; the closing fade lives on the end card. Everything
        # between is a straight cut, which is what a person talking should look like.
        vfade = ",fade=t=in:st=0:d=0.6" if n == 0 else ""

        seg = work / f"{n:02d}-{tag}.mp4"
        run([
            "ffmpeg", "-y", "-v", "error",
            "-loop", "1", "-t", f"{duration:.6f}", "-i", str(panel),
            "-t", f"{duration:.6f}", "-i", str(clip),
            "-loop", "1", "-t", f"{duration:.6f}", "-i", str(masks[side]),
            "-filter_complex",
            f"[1:v]scale={scaled_w}:{H},crop={PRESENTER_W}:{H}:{crop_x}:0,setsar=1,format=yuva420p[p];"
            f"[2:v]format=gray[m];"
            f"[p][m]alphamerge[pa];"
            f"[0:v]setsar=1[bg];"
            f"[bg][pa]overlay={overlay_x}:0:format=auto,format=yuv420p{vfade}[v]",
            "-map", "[v]", "-an",
            "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-r", str(FPS),
            "-t", f"{duration:.6f}",
            str(seg),
        ])

        # Sound is built as PCM on the same frame grid and stays uncompressed until the very
        # last step. See the note on AAC priming above the mux.
        wav = work / f"{n:02d}-{tag}.wav"
        run([
            "ffmpeg", "-y", "-v", "error",
            "-t", f"{duration:.6f}", "-i", str(clip),
            "-af", f"afade=t=out:st={max(duration - 0.3, 0):.3f}:d=0.3,apad",
            "-vn", "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2",
            "-t", f"{duration:.6f}",
            str(wav),
        ])
        segments.append(seg)
        audio.append(wav)
        print(f"  {tag:30s} {side:5s}  {duration:5.2f}s  crop x={crop_x}")

    SPANS.write_text(json.dumps(spans, indent=2) + "\n")

    outro_panel = PANEL_DIR / "99-outro.png"
    if not outro_panel.is_file():
        raise SystemExit(f"missing {outro_panel} -- re-run make_panels.py")
    outro_s = snap(OUTRO_S)
    outro = work / "99-outro.mp4"
    run([
        "ffmpeg", "-y", "-v", "error",
        "-loop", "1", "-t", f"{outro_s:.6f}", "-i", str(outro_panel),
        "-filter_complex",
        f"[0:v]setsar=1,format=yuv420p,fade=t=in:st=0:d=0.5,"
        f"fade=t=out:st={outro_s - 0.9:.3f}:d=0.9[v]",
        "-map", "[v]", "-an",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-r", str(FPS),
        "-t", f"{outro_s:.6f}", str(outro),
    ])
    outro_wav = work / "99-outro.wav"
    run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-t", f"{outro_s:.6f}", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-c:a", "pcm_s16le", "-t", f"{outro_s:.6f}", str(outro_wav),
    ])
    segments.append(outro)
    audio.append(outro_wav)
    total += outro_s
    print(f"  {'99-outro':30s} {'-':5s}  {outro_s:5.2f}s")

    # WHY PICTURE AND SOUND ARE JOINED SEPARATELY AND THE AUDIO IS ENCODED EXACTLY ONCE.
    #
    # The obvious build -- encode fifteen complete mp4 segments and concat them -- produces a
    # file whose total duration is correct to 21ms and whose lip sync is destroyed. Every AAC
    # stream carries an encoder priming delay at its head, and the concat demuxer sums those
    # delays instead of absorbing them. Cross-correlating each source clip against the finished
    # film measured the audio landing +21ms, +171ms, +284ms, +383ms, +469ms later and later
    # through the cut: roughly 33ms of slip per join, monotonically accumulating.
    #
    # Duration checks cannot see this, which is the point -- the totals matched. So the video
    # segments carry no audio at all, the sound is assembled as PCM on the same frame grid, and
    # the single AAC encode happens here at the mux. One encode, one priming delay, at t=0,
    # where the container's edit list accounts for it. --check re-measures and fails the build.
    vlist = work / "concat-v.txt"
    vlist.write_text("".join(f"file '{s.name}'\n" for s in segments))
    silent = work / "picture.mp4"
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(vlist),
         "-c", "copy", str(silent)])

    alist = work / "concat-a.txt"
    alist.write_text("".join(f"file '{a.name}'\n" for a in audio))
    track = work / "sound.wav"
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(alist),
         "-c", "copy", str(track)])

    run([
        "ffmpeg", "-y", "-v", "error", "-i", str(silent), "-i", str(track),
        "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart", str(OUT),
    ])

    measured = _probe(OUT, "format=duration")

    size_mb = OUT.stat().st_size / 1e6
    print(f"\n  {len(segments)} shot(s), {float(measured):.1f}s, {size_mb:.1f} MB")
    print(f"  -> {OUT.relative_to(REPO_ROOT)}")

    failed = check_sync(timeline) if args.check else False
    if not args.keep_segments:
        shutil.rmtree(work)
    return 1 if failed else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
