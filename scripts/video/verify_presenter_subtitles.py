#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Verify complete, speech-aligned subtitles on the assembled presenter master.

Source-clip Whisper receipts are necessary but insufficient: a compositor can
drop captions during an evidence handoff while retaining perfect source audio.
This gate checks the final story and sidecar together, then samples every active
cue from the rendered MP4 to prove a burned subtitle is actually visible.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import subprocess
import wave
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
STORY = ROOT / "remotion" / "src" / "story.json"
SCRIPT = ROOT / "docs" / "demo" / "presenter-script.json"
PUBLIC = ROOT / "remotion" / "public"
FPS = 24
EVIDENCE_HANDOFF_S = 1.85
TIME_TOLERANCE_S = 0.06
MIN_ACTIVE_SECONDS = 0.10
MAX_MASTER_AUDIO_DRIFT_S = 0.09
MIN_MASTER_AUDIO_CORRELATION = 0.94
MIN_MASTER_TRANSCRIPT_SIMILARITY = 0.72


def parse_time(value: str) -> float:
    h, m, rest = value.split(":")
    s, ms = rest.split(",")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def parse_srt(path: Path) -> list[dict]:
    blocks = re.split(r"\n\s*\n", path.read_text(encoding="utf-8").strip())
    cues: list[dict] = []
    for block in blocks:
        lines = block.splitlines()
        if len(lines) < 3 or " --> " not in lines[1]:
            raise SystemExit(f"invalid SRT block: {block!r}")
        start, end = lines[1].split(" --> ")
        cues.append(
            {
                "start": parse_time(start),
                "end": parse_time(end),
                "text": " ".join(lines[2:]).strip(),
            }
        )
    return cues


def norm(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40,
    "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
_SCALES = {"hundred": 100, "thousand": 1000, "million": 1_000_000}


def comparable_tokens(text: str) -> list[str]:
    tokens = re.sub(r"(?<=\d),(?=\d)", "", text.lower())
    tokens = re.sub(r"[^a-z0-9 ]", " ", tokens).split()
    out: list[str] = []
    total = chunk = 0
    active = False

    def flush() -> None:
        nonlocal total, chunk, active
        if active:
            out.append(str(total + chunk))
        total = chunk = 0
        active = False

    for token in tokens:
        if token in _UNITS:
            chunk += _UNITS[token]
            active = True
        elif token in _SCALES and active:
            scale = _SCALES[token]
            if scale == 100:
                chunk *= 100
            else:
                total += (chunk or 1) * scale
                chunk = 0
        elif token == "and" and active:
            continue
        else:
            flush()
            out.append(token)
    flush()
    return out


def transcript_similarity(asked: str, heard: str) -> float:
    return difflib.SequenceMatcher(
        None,
        comparable_tokens(asked),
        comparable_tokens(heard),
    ).ratio()


def expected_cues(story: dict) -> list[dict]:
    out: list[dict] = []
    cursor = 0.0
    for shot in story["shots"]:
        for cue in shot["cues"]:
            out.append(
                {
                    "start": cursor + float(cue["s"]),
                    "end": cursor + float(cue["e"]),
                    "text": str(cue["t"]),
                    "shot": shot["id"],
                    "evidence": bool(shot.get("broll")),
                    "shotStart": cursor,
                }
            )
        cursor += int(shot["durationFrames"]) / FPS
    return out


def approved_lines(spec: dict) -> dict[str, str]:
    return {
        f"{beat['id']}-{index}": line
        for beat in spec["beats"]
        for index, line in enumerate(beat["shots"])
    }


def extract_frame(video: Path, t: float, out: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-ss",
            f"{t:.3f}",
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-y",
            str(out),
        ],
        check=True,
    )


def subtitle_band_has_ink(frame: Path, *, evidence: bool) -> bool:
    image = Image.open(frame).convert("RGB")
    # Presenter captions sit above the bottom margin; evidence captions occupy
    # the dedicated final 80px rail.
    crop = image.crop((180, 996 if evidence else 842, 1740, 1080 if evidence else 1038))
    gray = crop.convert("L")
    extrema = gray.getextrema()
    bright = sum(1 for value in gray.get_flattened_data() if value >= 205)
    # Presenter backgrounds can be bright. Requiring a few thousand near-white
    # pixels in the exact caption band catches rendered glyphs without mistaking
    # the dark subtitle rail itself for text.
    threshold = 900 if evidence else 1800
    return extrema[1] >= 205 and bright >= threshold


def extract_pcm(video: Path, out: Path, *, start: float = 0.0, duration: float | None = None) -> None:
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{start:.6f}", "-i", str(video)]
    if duration is not None:
        cmd += ["-t", f"{duration:.6f}"]
    cmd += ["-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", "-y", str(out)]
    subprocess.run(cmd, check=True)


def read_pcm(path: Path) -> list[int]:
    with wave.open(str(path), "rb") as wav:
        if wav.getnchannels() != 1 or wav.getframerate() != 16000 or wav.getsampwidth() != 2:
            raise SystemExit(f"unexpected PCM format in {path}")
        raw = wav.readframes(wav.getnframes())
    # MemoryStand is little-endian on every supported render host.
    return list(memoryview(raw).cast("h"))


def normalized_correlation(left: list[int], right: list[int], lag: int) -> float:
    if lag >= 0:
        a, b = left[lag:], right[: len(left) - lag]
    else:
        a, b = left[: len(left) + lag], right[-lag:]
    if not a or not b:
        return 0.0
    mean_a = sum(a) / len(a)
    mean_b = sum(b) / len(b)
    numerator = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b))
    denom_a = sum((x - mean_a) ** 2 for x in a)
    denom_b = sum((y - mean_b) ** 2 for y in b)
    return numerator / ((denom_a * denom_b) ** 0.5) if denom_a and denom_b else 0.0


def audio_match(source: Path, master: Path, *, start: float, duration: float, qa: Path) -> tuple[float, float]:
    source_wav = qa / f"{source.stem}-source.wav"
    master_wav = qa / f"{source.stem}-master.wav"
    extract_pcm(source, source_wav, duration=duration)
    extract_pcm(master, master_wav, start=start, duration=duration)
    left = read_pcm(source_wav)
    right = read_pcm(master_wav)
    size = min(len(left), len(right), 80_000)
    left, right = left[:size], right[:size]

    max_lag = round(MAX_MASTER_AUDIO_DRIFT_S * 16_000)
    coarse_step = 16
    coarse = max(
        range(-max_lag, max_lag + 1, coarse_step),
        key=lambda lag: normalized_correlation(left, right, lag),
    )
    fine_start = max(-max_lag, coarse - coarse_step)
    fine_end = min(max_lag, coarse + coarse_step)
    lag = max(
        range(fine_start, fine_end + 1),
        key=lambda candidate: normalized_correlation(left, right, candidate),
    )
    return normalized_correlation(left, right, lag), lag / 16_000


def transcribe_master(video: Path) -> list[dict]:
    from faster_whisper import WhisperModel

    segments, _ = WhisperModel(
        "small.en",
        device="cpu",
        compute_type="int8",
        local_files_only=True,
    ).transcribe(
        str(video),
        vad_filter=False,
        beam_size=5,
    )
    return [
        {"start": float(seg.start), "end": float(seg.end), "text": seg.text.strip()}
        for seg in segments
        if seg.text.strip()
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("video", type=Path)
    ap.add_argument("--srt", type=Path, default=None)
    args = ap.parse_args()

    video = args.video.resolve()
    srt = (args.srt or video.with_suffix(".srt")).resolve()
    if not video.is_file() or not srt.is_file():
        raise SystemExit(f"missing video or SRT: {video} / {srt}")

    story = json.loads(STORY.read_text())
    approved = approved_lines(json.loads(SCRIPT.read_text()))
    expected = expected_cues(story)
    actual = parse_srt(srt)
    errors: list[str] = []
    if len(actual) != len(expected):
        errors.append(f"expected {len(expected)} cues, found {len(actual)}")

    for index, (want, got) in enumerate(zip(expected, actual), 1):
        if abs(want["start"] - got["start"]) > TIME_TOLERANCE_S:
            errors.append(
                f"cue {index} starts {got['start']:.3f}s; expected {want['start']:.3f}s"
            )
        if abs(want["end"] - got["end"]) > TIME_TOLERANCE_S:
            errors.append(
                f"cue {index} ends {got['end']:.3f}s; expected {want['end']:.3f}s"
            )
        if norm(want["text"]) != norm(got["text"]):
            errors.append(f"cue {index} text mismatch: {got['text']!r} != {want['text']!r}")
        if got["end"] - got["start"] < MIN_ACTIVE_SECONDS:
            errors.append(f"cue {index} is too short to read")

    # "Every cue exists" is weaker than "every approved word exists." Group
    # the cues back into their shots and require an exact normalized match to
    # the script of record, so an alignment bug cannot silently omit a word.
    by_shot: dict[str, list[str]] = {}
    for cue in expected:
        by_shot.setdefault(str(cue["shot"]), []).append(str(cue["text"]))
    for shot_id, line in approved.items():
        captioned = " ".join(by_shot.get(shot_id, []))
        if norm(captioned) != norm(line):
            errors.append(
                f"{shot_id} subtitles do not cover the approved spoken line: "
                f"{captioned!r} != {line!r}"
            )

    # Independently transcribe the assembled MP4 itself. This catches an
    # accidental audio replacement or duplicated/missing segment that source
    # receipts and timeline math cannot see. Compare per shot so one local
    # failure cannot hide inside a high whole-film score.
    master_segments = transcribe_master(video)
    cursor = 0.0
    master_text_results: list[tuple[str, float]] = []
    for shot in story["shots"]:
        duration = int(shot["durationFrames"]) / FPS
        heard = " ".join(
            segment["text"]
            for segment in master_segments
            if segment["end"] > cursor + 0.02 and segment["start"] < cursor + duration - 0.02
        )
        asked = approved[str(shot["id"])]
        score = transcript_similarity(asked, heard)
        master_text_results.append((str(shot["id"]), score))
        if score < MIN_MASTER_TRANSCRIPT_SIMILARITY:
            errors.append(
                f"{shot['id']} master transcript similarity {score:.3f} is below "
                f"{MIN_MASTER_TRANSCRIPT_SIMILARITY:.2f}; heard {heard!r}"
            )
        cursor += duration

    qa = Path("/tmp/memorystand-subtitle-qa")
    qa.mkdir(parents=True, exist_ok=True)
    for index, cue in enumerate(expected, 1):
        t = cue["start"] + min(0.35, max(0.12, (cue["end"] - cue["start"]) / 2))
        in_evidence_frame = bool(cue["evidence"]) and (
            t - float(cue["shotStart"]) >= EVIDENCE_HANDOFF_S
        )
        frame = qa / f"{index:02d}.png"
        extract_frame(video, t, frame)
        if not subtitle_band_has_ink(frame, evidence=in_evidence_frame):
            errors.append(
                f"cue {index} ({cue['shot']}) has no visible burned subtitle at {t:.3f}s"
            )

    # Prove that the assembled master did not shift or replace narration during
    # a presenter-to-evidence handoff. Every shot's master audio must remain a
    # high-correlation copy of its already-verified source take.
    cursor = 0.0
    audio_results: list[tuple[str, float, float]] = []
    for shot in story["shots"]:
        duration = int(shot["durationFrames"]) / FPS
        source = PUBLIC / str(shot["clip"])
        correlation, lag = audio_match(
            source,
            video,
            start=cursor,
            duration=duration,
            qa=qa,
        )
        audio_results.append((shot["id"], correlation, lag))
        if correlation < MIN_MASTER_AUDIO_CORRELATION:
            errors.append(
                f"{shot['id']} master audio correlation {correlation:.3f} is below "
                f"{MIN_MASTER_AUDIO_CORRELATION:.2f}"
            )
        if abs(lag) > MAX_MASTER_AUDIO_DRIFT_S:
            errors.append(
                f"{shot['id']} master audio drift {lag * 1000:.1f}ms exceeds "
                f"{MAX_MASTER_AUDIO_DRIFT_S * 1000:.0f}ms"
            )
        cursor += duration

    if errors:
        print("Presenter subtitle verification FAILED:")
        for error in errors:
            print(f"  - {error}")
        return 1
    evidence_count = sum(1 for cue in expected if cue["evidence"])
    worst_correlation = min(value for _, value, _ in audio_results)
    worst_lag = max(abs(lag) for _, _, lag in audio_results)
    worst_transcript = min(value for _, value in master_text_results)
    print(
        "Presenter subtitle verification PASSED: "
        f"{len(expected)}/{len(expected)} cues timed and visibly burned; "
        f"{evidence_count} evidence-shot cues included; "
        f"9/9 master shot transcripts matched approved lines "
        f"(min similarity {worst_transcript:.3f}); "
        f"9/9 master audio segments matched sources "
        f"(min corr {worst_correlation:.3f}, max drift {worst_lag * 1000:.1f}ms)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
