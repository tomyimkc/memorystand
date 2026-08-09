#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Step 4: cut verified narration over large panels or live evidence into one 1920x1080 film.

    large panel OR deployed evidence footage
      + presenter narration <- make_clips.py, transcript-verified
        -> one segment per shot, trimmed to where the speech actually stops
          -> concat -> artifacts/video/memorystand-presenter.mp4

THREE THINGS THIS DOES THAT A NAIVE OVERLAY DOES NOT:

1. IT REFUSES UNVERIFIED FOOTAGE. Every shot must appear in docs/demo/presenter-verification.json
   with ``passed: true``. A clip sitting in the clips directory is not sufficient -- it has to have
   been transcribed and matched against the line it was given. The generator invents dialogue
   when left unsupervised, so "the file exists" is exactly the kind of evidence this project
   spends its README arguing against. Same rule as the trust ladder, applied to its own video.

2. IT TRIMS TO MEASURED SPEECH, NOT TO CLIP LENGTH. Grok returns a fixed 10s clip regardless of
   how long the line takes. The current twelve-shot cut removes roughly 13s of unused tails.
   Word-level timestamps give the real end, and each shot holds for HOLD_S past it so the cut
   lands on a beat instead of clipping the last consonant.

3. IT FEATHERS THE PRESENTER INTO THE FRAME. The clip is a 496x608 portrait with its own
   near-black office background; dropped in as a rectangle it reads as a sticker. A gradient
   alpha mask on the inner edge -- the edge facing the panel -- dissolves the seam. The mask is
   mirrored per beat because the presenter alternates sides.

4. IT FINDS THE SPEAKER RATHER THAN ASSUMING WHERE HE IS. Scaling a 496x608 portrait to 1080
   tall makes it ~880 wide against a 660px column, so 220px has to go. Which 220 depends on how
   the shot was actually composed, and generation does not guarantee that survives. The motion
   centroid locates him and the crop places him at SUBJECT_TARGET within his own column.

5. IT MEASURES ITS OWN LIP SYNC. ``--check`` first proves that the measured picture delay fixes
   each full-resolution presenter clip, then cross-correlates every source waveform against the
   finished film to prove the corrected shot landed on the intended timeline. Measuring mouth
   motion after shrinking the presenter beside a large static panel produced false failures, so
   the two temporal guarantees are tested separately at the stage where each signal is reliable.

6. IT SHOWS THE PRODUCT WORKING. Selected shots replace the talking head with muted footage from
   the deployed evidence cut. The verified presenter audio remains on the exact same frame grid,
   while a large headline and fresh subtitles replace the evidence cut's dense explanatory text.

    python scripts/presenter/compose.py --check
    python scripts/presenter/compose.py --keep-segments   # leave the per-shot files for review
"""

from __future__ import annotations

import argparse
import json
import difflib
import math
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts" / "presenter"))
sys.path.insert(0, str(REPO_ROOT / "scripts" / "video"))

import build_frames as bf  # noqa: E402  (fonts and palette, shared with the panels)
import lipsync  # noqa: E402
from make_panels import PANEL_MARGIN, PRESENTER_W, W, H  # noqa: E402,F401

SCRIPT_JSON = REPO_ROOT / "docs" / "demo" / "presenter-script.json"
PANEL_DIR = REPO_ROOT / "artifacts" / "presenter" / "panels"
CLIP_DIR = REPO_ROOT / "artifacts" / "presenter" / "clips"
RECEIPT = REPO_ROOT / "docs" / "demo" / "presenter-verification.json"
SPANS = REPO_ROOT / "artifacts" / "presenter" / "speech-spans.json"
OUT = REPO_ROOT / "artifacts" / "video" / "memorystand-presenter.mp4"
SRT = REPO_ROOT / "artifacts" / "video" / "memorystand-presenter.srt"
WORDS = REPO_ROOT / "artifacts" / "presenter" / "word-timings.json"

# How long a shot holds after the last word. Long enough that the cut does not feel clipped,
# short enough that it does not read as a pause.
#
# This is a CEILING as well as a target: the hold is clamped to the clip's own length. Without
# that clamp, a requested hold can exceed a source clip and loop the still panel while the
# presenter and audio run out -- he freezes mid-gesture and the segment's tracks end at different
# times, which the concat demuxer then accumulates as drift. Extra reading time belongs on a card
# that was always still, not on a frozen person.
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
    gap does not cancel out, it ACCUMULATES across segments. On a lip-synced talking head that
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
    """Seconds at which the last word finishes, cached against the exact source render."""
    source_sha256 = lipsync._sha256(clip)
    cached = cache.get(clip.stem, {})
    if isinstance(cached, dict) and cached.get("sourceSha256") == source_sha256:
        return float(cached["end"])

    wav = Path(tempfile.gettempdir()) / f"compose_{clip.stem}.wav"
    run(["ffmpeg", "-v", "error", "-i", str(clip), "-ac", "1", "-ar", "16000", "-y", str(wav)])
    from faster_whisper import WhisperModel

    segs, _ = WhisperModel("tiny", device="cpu", compute_type="int8").transcribe(
        str(wav), word_timestamps=True
    )
    words = [w for s in segs for w in (s.words or [])]
    if not words:
        raise SystemExit(f"{clip.name}: no speech found -- refusing to guess its length")
    cache[clip.stem] = {
        "sourceSha256": source_sha256,
        "start": round(words[0].start, 2),
        "end": round(words[-1].end, 2),
    }
    return float(cache[clip.stem]["end"])


# Captions live in the band the panel no longer occupies (see BAND_BOTTOM in make_panels).
CAPTION_TOP = H - 232

# Two short lines read faster than one long one at this size, and a caption that runs the full
# 1920 makes the eye travel further than the shot lasts.
CAPTION_MAX_CHARS = 52

CAPTION_FONT = "Helvetica"
CAPTION_FONT_SIZE = 46

# Distance from the bottom of the frame to the bottom of the caption block. Sized so a two-line
# cue sits inside the band make_panels vacated, and never over the panel.
CAPTION_MARGIN_V = 110

# Captions stay centred in the frame rather than following the panel from side to side. Moving
# them each beat would make the eye hunt for them twice a beat; centred and fixed is what every
# viewer already knows how to read. They do cross the presenter's shoulder, which is what the
# outline is for.
CAPTION_MARGIN_X = 300
CAPTION_MAX_WORDS = 12
CAPTION_ORPHAN_MAX_WORDS = 2
CAPTION_ORPHAN_MERGE_CHARS = 68


def _srt_time(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    sec, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"


def caption_cues(words: list[tuple[str, float, float]]) -> list[tuple[float, float, str]]:
    """Group timed words into readable cues.

    Timed from the SAME whisper word timestamps that decide where each shot is trimmed, so a
    caption cannot drift from the audio it belongs to -- there is one source of truth for when a
    word is spoken, and both the cut and the caption read it.

    Cues break on sentence-final punctuation first, then at a clause boundary, then on length.
    The em dash is deliberately NOT a breakpoint -- it is a stylistic pause mid-sentence, and
    treating it as one produced a three-word cue that flashed by in 1.5 seconds.
    """
    cues: list[tuple[float, float, str]] = []
    chunk: list[tuple[str, float, float]] = []

    def emit(upto: int):
        """Cut the chunk after `upto` words, keeping the remainder for the next cue."""
        head, tail = chunk[:upto], chunk[upto:]
        if head:
            cues.append((head[0][1], head[-1][2], " ".join(w for w, _, _ in head).strip()))
        chunk[:] = tail

    for word, start, end in words:
        chunk.append((word, start, end))
        text = " ".join(w for w, _, _ in chunk)

        if word.strip().endswith((".", "!", "?")):
            emit(len(chunk))
            continue
        if len(text) < CAPTION_MAX_CHARS and len(chunk) < CAPTION_MAX_WORDS:
            continue

        # Over the limit. Prefer the last clause boundary inside the chunk to a hard cut: a cue
        # reading "It's for anyone on call at two in the" and then "morning, when an agent..."
        # splits a phrase across a screen change, which is harder to read than a short line.
        breakpoints = [i for i, (w, _, _) in enumerate(chunk[:-1])
                       if w.strip().endswith((",", ";", ":"))]
        emit(breakpoints[-1] + 1 if breakpoints else len(chunk))
    emit(len(chunk))

    # A hard character cut near the end can leave a one-word final cue ("case." / "keys.")
    # flashing for half a second. That is technically timed and practically unreadable. Merge a
    # tiny orphan back into the preceding cue when the result still fits comfortably as two
    # rendered lines; render_caption() performs the actual pixel-width wrapping.
    if len(cues) >= 2 and len(cues[-1][2].split()) <= CAPTION_ORPHAN_MAX_WORDS:
        previous = cues[-2]
        final = cues[-1]
        merged_text = f"{previous[2]} {final[2]}".strip()
        if (
            len(merged_text) <= CAPTION_ORPHAN_MERGE_CHARS
            and len(merged_text.split()) <= CAPTION_MAX_WORDS
        ):
            cues[-2:] = [(previous[0], final[1], merged_text)]
    return cues


def align_to_script(scripted: str, heard: list[tuple[str, float, float]]) -> list[tuple[str, float, float]]:
    """Give the SCRIPT's words the transcriber's timings.

    The captions must read what was written, not what whisper thought it heard. Using the
    transcript directly put "I built memory stand, a memory for AI agents" on screen -- the
    product name lower-cased and split in two, in the opening caption of the film. Punctuation,
    capitalisation and spelled-out numbers all come back mangled in harmless-for-verification
    ways that are not harmless to read.

    So the transcript is used for TIMING only. Words are matched up with difflib; runs that do
    not match one-to-one (heard "300" against scripted "three hundred") have that span's time
    divided evenly across the scripted words in it. Timing accuracy costs at most a fraction of
    a word, and the text on screen is exactly the text that was approved.
    """
    def key(word: str) -> str:
        return re.sub(r"[^a-z0-9]", "", word.lower())

    script_words = scripted.split()
    if not heard:
        return []

    matcher = difflib.SequenceMatcher(None, [key(w) for w, _, _ in heard], [key(w) for w in script_words])
    out: list[tuple[str, float, float]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for offset in range(j2 - j1):
                _, start, end = heard[i1 + offset]
                out.append((script_words[j1 + offset], start, end))
            continue
        if j1 == j2:
            continue  # the transcriber heard something the script never said; drop it
        span_start = heard[i1][1] if i1 < len(heard) else heard[-1][2]
        span_end = heard[i2 - 1][2] if i2 - 1 < len(heard) and i2 > i1 else span_start
        if span_end <= span_start:
            span_end = span_start + 0.05 * (j2 - j1)
        step = (span_end - span_start) / (j2 - j1)
        for offset in range(j2 - j1):
            out.append((script_words[j1 + offset], span_start + offset * step,
                        span_start + (offset + 1) * step))
    return out


def clip_words(clip: Path, cache: dict) -> list[tuple[str, float, float]]:
    """Per-word timings, cached only while the exact source render is unchanged."""
    source_sha256 = lipsync._sha256(clip)
    cached = cache.get(clip.stem, {})
    if isinstance(cached, dict) and cached.get("sourceSha256") == source_sha256:
        return [tuple(w) for w in cached["words"]]

    wav = Path(tempfile.gettempdir()) / f"words_{clip.stem}.wav"
    run(["ffmpeg", "-v", "error", "-i", str(clip), "-ac", "1", "-ar", "16000", "-y", str(wav)])
    from faster_whisper import WhisperModel

    segs, _ = WhisperModel("tiny", device="cpu", compute_type="int8").transcribe(
        str(wav), word_timestamps=True
    )
    cache[clip.stem] = {
        "sourceSha256": source_sha256,
        "words": [
            [w.word.strip(), round(w.start, 3), round(w.end, 3)]
            for seg in segs for w in (seg.words or [])
        ],
    }
    return [tuple(w) for w in cache[clip.stem]["words"]]


def rebalance_caption_lines(lines: list[str]) -> list[str]:
    """Move words off an overfull first line so the visual wrap has no tiny orphan."""
    if len(lines) != 2:
        return lines
    first = lines[0].split()
    second = lines[1].split()
    while len(second) <= CAPTION_ORPHAN_MAX_WORDS and len(first) > 4:
        second.insert(0, first.pop())
    return [" ".join(first), " ".join(second)]


def render_caption(text: str, path: Path) -> None:
    """Draw one caption as a transparent PNG, to be overlaid for its cue's duration.

    THIS FFMPEG HAS NO TEXT FILTERS. `ffmpeg -filters` lists neither `subtitles`, `ass` nor
    `drawtext` -- the build carries no libass and no freetype, so every filtergraph naming one is
    rejected with "No option name near ...", which reads like a quoting bug and is not one.

    Rendering the captions with Pillow instead is the better answer anyway: they get the same
    typeface, weight and palette as the data panels, so they look like part of the design rather
    than a subtitle track laid over it. The heavy stroke is what lets them cross the presenter's
    shoulder and stay readable.
    """
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    font = bf.font(CAPTION_FONT_SIZE, bold=True)
    usable = W - 2 * CAPTION_MARGIN_X

    words, lines, current = text.split(), [], ""
    for word in words:
        trial = f"{current} {word}".strip()
        if draw.textlength(trial, font=font) <= usable or not current:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    lines = rebalance_caption_lines(lines)

    line_h = CAPTION_FONT_SIZE + 16
    bottom = H - CAPTION_MARGIN_V
    y = bottom - line_h * len(lines)
    for line in lines:
        x = (W - draw.textlength(line, font=font)) / 2
        draw.text((x, y), line, font=font, fill=(255, 255, 255, 255),
                  stroke_width=5, stroke_fill=(6, 10, 18, 235))
        y += line_h
    image.save(path)


def render_broll_overlay(visual: dict, path: Path) -> None:
    """Cover dense legacy text with one large claim and a clean subtitle band.

    The evidence source is intentionally allowed to remain technical: judges can still inspect
    the live API and database receipts. The editorial layer is not technical. It states one idea
    in large type, labels the footage as live evidence, and covers the old paragraph subtitle so
    viewers never have to choose between two competing blocks of copy.
    """
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, W, 198), fill=(6, 10, 18, 232))
    draw.rectangle((0, CAPTION_TOP - 24, W, H), fill=(6, 10, 18, 242))
    bf.text(draw, (60, 28), visual["label"], size=24, color=bf.GREEN, bold=True)
    bf.text(
        draw,
        (60, 72),
        visual["headline"],
        size=68,
        color=bf.INK,
        bold=True,
        max_width=W - 120,
        spacing=8,
    )
    image.save(path)


def write_srt(path: Path, cues: list[tuple[float, float, str]]) -> None:
    blocks = []
    for i, (start, end, text) in enumerate(cues, 1):
        blocks.append(f"{i}\n{_srt_time(start)} --> {_srt_time(end)}\n{text}\n")
    path.write_text("\n".join(blocks), encoding="utf-8")


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


# At 24 fps, the lip-motion measurement resolves in 41.7ms frame steps. A limit below half a
# frame therefore accepts only a measured zero-frame residual; even one frame fails the build.
MAX_LAG_MS = 20.0


def check_sync(timeline: list[tuple[str, float]], picture_delays: dict[str, float]) -> bool:
    """Verify corrected source lips and final-film placement. True if either drifted.

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

    failed = abs(worst) > MAX_LAG_MS
    if failed:
        print(f"\n  PLACEMENT OUT OF SYNC: worst {worst:+.1f} ms at {worst_tag} (limit {MAX_LAG_MS:.0f} ms)")
    else:
        print(f"  placement in sync: worst {worst:+.1f} ms at {worst_tag or 'n/a'}")

    # Placement is only half of it. Verify the full-resolution presenter after applying the
    # exact same tpad correction used by the compositor. Doing this on the finished 1920x1080
    # frame is not equivalent: the presenter occupies only 660px beside a large static panel,
    # and the changing burned captions dominate the motion centroid after downscaling. That
    # produced confident-looking false lags up to 250ms even though the same corrected source
    # measured 0ms. Spatial scaling, cropping, masking, and overlay do not change time; the
    # waveform-placement check above proves the corrected shot lands at the intended film time.
    print(f"\n  corrected-source lip check (tolerance {MAX_LAG_MS:.0f} ms)")
    lip_worst, lip_tag = 0.0, ""
    for tag, _expected in timeline:
        source = CLIP_DIR / f"{tag}.mp4"
        delay = picture_delays.get(tag, 0.0)
        corrected = Path(tempfile.gettempdir()) / f"lipcheck_corrected_{tag}.mp4"
        if delay > 0:
            run([
                "ffmpeg", "-y", "-v", "error", "-i", str(source),
                "-filter_complex", f"[0:v]tpad=start_duration={delay:.4f}:start_mode=clone[v]",
                "-map", "[v]", "-map", "0:a",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-r", str(FPS),
                "-c:a", "aac", "-b:a", "160k", str(corrected),
            ])
        else:
            corrected = source
        lag, corr = lipsync.measure(corrected)
        if abs(lag) > abs(lip_worst):
            lip_worst, lip_tag = lag, tag
        flag = "  <-- perceptible" if abs(lag) > MAX_LAG_MS else ""
        print(
            f"    {tag:30s} delay picture {delay * 1000:5.0f} ms"
            f"   residual {lag:+7.0f} ms   r={corr:.2f}{flag}"
        )
    if abs(lip_worst) > MAX_LAG_MS:
        print(f"\n  LIPS OUT OF SYNC: worst {lip_worst:+.0f} ms at {lip_tag}")
        failed = True
    else:
        print(f"  lips in sync: worst {lip_worst:+.0f} ms at {lip_tag or 'n/a'}")
    return failed


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

    # Measured picture delay per clip, correcting the generator's own lip sync. See lipsync.py:
    # grok's picture and sound are generated together but not perfectly aligned, and --check
    # cannot see it because --check only asks whether a clip's audio lands where the cut expects.
    lip_delay = lipsync.offsets([CLIP_DIR / f"{b['id']}-{i}.mp4"
                                 for b in spec["beats"] for i in range(len(b["shots"]))])

    masks = {s: work / f"mask-{s.lower()}.png" for s in ("LEFT", "RIGHT")}
    for side, path in masks.items():
        make_mask(side, path)

    shots = [
        (
            beat["id"],
            i,
            beat["presenterSide"],
            beat.get("broll", {}).get(str(i)),
        )
        for beat in spec["beats"]
        for i in range(len(beat["shots"]))
    ]
    spec_lines = {
        f"{beat['id']}-{i}": line
        for beat in spec["beats"]
        for i, line in enumerate(beat["shots"])
    }

    words_cache = json.loads(WORDS.read_text()) if WORDS.is_file() else {}
    film_cues: list[tuple[float, float, str]] = []
    segments: list[Path] = []
    audio: list[Path] = []
    timeline: list[tuple[str, float]] = []
    total = 0.0
    for n, (beat_id, i, side, visual) in enumerate(shots):
        tag = f"{beat_id}-{i}"
        if tag not in passed:
            raise SystemExit(
                f"{tag} is not recorded as verified in {RECEIPT.name}. "
                "Re-run make_clips.py; a shot that has not been checked does not go in the cut."
            )

        clip = CLIP_DIR / f"{tag}.mp4"
        panel = PANEL_DIR / f"{beat_id}.png"
        needed_files = [clip]
        if visual is None:
            needed_files.append(panel)
        for needed in needed_files:
            if not needed.is_file():
                raise SystemExit(f"missing {needed}")

        clip_len = float(_probe(clip, "format=duration"))
        duration = snap(min(clip_len, speech_end(clip, spans) + HOLD_S))
        segment_start = total
        timeline.append((tag, segment_start))
        total += duration

        # Hold the first frame for the measured offset so the lips arrive when the sound does.
        # Delaying the picture rather than advancing the audio matters: speech starts at t=0 in
        # every one of these clips, so advancing the audio would clip the first word.
        delay = lip_delay.get(tag, 0.0)
        lip = f"tpad=start_duration={delay:.4f}:start_mode=clone," if delay > 0 else ""

        crop_x = None
        if visual is None:
            # The clip is portrait; scaled to 1080 tall it is wider than the 660px presenter
            # column, so something has to be cropped. Rather than assume which side, find the
            # speaker and put him where he belongs in his own column.
            probe_w, probe_h = _clip_size(clip)
            scaled_w = round(probe_w * H / probe_h / 2) * 2
            crop_x = int(
                round(subject_centre(clip) * scaled_w - SUBJECT_TARGET[side] * PRESENTER_W)
            )
            crop_x = max(0, min(crop_x, scaled_w - PRESENTER_W))
            overlay_x = 0 if side == "LEFT" else W - PRESENTER_W

        # Only the opening touches black; the closing fade lives on the end card. Everything
        # between is a straight cut, which is what a person talking should look like.
        vfade = ",fade=t=in:st=0:d=0.6" if n == 0 else ""

        # Captions are burned in rather than left as a sidecar track. A judge opening an mp4 or a
        # Devpost embed will not go looking for a CC button, and a caption nobody switches on is
        # the same as no caption. The .srt is still written alongside, for the YouTube upload.
        scripted = spec_lines[tag]
        timed = align_to_script(scripted, clip_words(clip, words_cache))
        cues = [c for c in caption_cues(timed) if c[0] < duration]
        # segment_start, NOT total -- total has already been advanced past this segment, and
        # using it put every cue in the sidecar one whole shot late.
        film_cues += [(segment_start + a, min(segment_start + b, segment_start + duration), t)
                      for a, b, t in cues]
        write_srt(work / f"{n:02d}-{tag}.srt", [(a, min(b, duration), t) for a, b, t in cues])
        # Commas inside force_style MUST be escaped: filter_complex splits filters on commas
        # before libavfilter ever sees the quotes, so an unescaped style string is parsed as a
        # series of nonexistent filters ("No option name near ...").
        cue_inputs: list[str] = []
        cue_filters = ""
        stage = "[base]"
        for c, (start, end, text) in enumerate(cues):
            png = work / f"{n:02d}-{tag}-cue{c}.png"
            render_caption(text, png)
            cue_inputs += ["-loop", "1", "-t", f"{duration:.6f}", "-i", str(png)]
            nxt = f"[cap{c}]"
            cue_filters += (f"{stage}[{3 + c}:v]overlay=0:0:format=auto:"
                            f"enable='between(t\\,{start:.3f}\\,{min(end, duration):.3f})'{nxt};")
            stage = nxt
        subs = ""

        seg = work / f"{n:02d}-{tag}.mp4"
        if visual is not None:
            source = REPO_ROOT / visual["source"]
            if not source.is_file():
                raise SystemExit(
                    f"missing live-evidence source {source}. Copy the reviewed deployed-demo "
                    "render there before composing the hybrid cut."
                )
            start = float(visual["startSeconds"])
            source_len = float(_probe(source, "format=duration"))
            if start + duration > source_len + 0.05:
                raise SystemExit(
                    f"{tag}: evidence range {start:.2f}-{start + duration:.2f}s exceeds "
                    f"{source.name} ({source_len:.2f}s)"
                )
            evidence_overlay = work / f"{n:02d}-{tag}-evidence-overlay.png"
            render_broll_overlay(visual, evidence_overlay)
            video_inputs = [
                "-ss", f"{start:.6f}", "-t", f"{duration:.6f}", "-i", str(source),
                "-t", f"{duration:.6f}", "-i", str(clip),
                "-loop", "1", "-t", f"{duration:.6f}", "-i", str(evidence_overlay),
            ]
            base_filter = (
                f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,"
                f"crop={W}:{H},setsar=1[bg];"
                f"[2:v]format=rgba[evidence];"
                f"[bg][evidence]overlay=0:0:format=auto[base];"
            )
        else:
            video_inputs = [
                "-loop", "1", "-t", f"{duration:.6f}", "-i", str(panel),
                "-t", f"{duration:.6f}", "-i", str(clip),
                "-loop", "1", "-t", f"{duration:.6f}", "-i", str(masks[side]),
            ]
            base_filter = (
                f"[1:v]{lip}scale={scaled_w}:{H},"
                f"crop={PRESENTER_W}:{H}:{crop_x}:0,setsar=1,format=yuva420p[p];"
                f"[2:v]format=gray[m];"
                f"[p][m]alphamerge[pa];"
                f"[0:v]setsar=1[bg];"
                f"[bg][pa]overlay={overlay_x}:0:format=auto[base];"
            )
        run([
            "ffmpeg", "-y", "-v", "error",
            *video_inputs,
            *cue_inputs,
            "-filter_complex",
            base_filter
            + cue_filters
            + f"{stage}format=yuv420p{vfade}[v]",
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
        visual_label = (
            f"evidence @{float(visual['startSeconds']):.0f}s"
            if visual is not None
            else f"presenter crop x={crop_x}"
        )
        print(f"  {tag:30s} {side:5s}  {duration:5.2f}s  {visual_label}")

    SPANS.write_text(json.dumps(spans, indent=2) + "\n")
    WORDS.write_text(json.dumps(words_cache, indent=1) + "\n")

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
    # The obvious build -- encode every complete MP4 segment and concat them -- produces a
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

    write_srt(SRT, film_cues)
    print(f"  {len(film_cues)} caption cue(s) -> {SRT.relative_to(REPO_ROOT)}")

    size_mb = OUT.stat().st_size / 1e6
    print(f"\n  {len(segments)} shot(s), {float(measured):.1f}s, {size_mb:.1f} MB")
    print(f"  -> {OUT.relative_to(REPO_ROOT)}")

    failed = check_sync(timeline, lip_delay) if args.check else False
    if not args.keep_segments:
        shutil.rmtree(work)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
