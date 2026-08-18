#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Turn verified presenter clips into remotion/src/story.json.

This script is the only place that measures speech and writes the timeline
Remotion will render. It refuses a clip that is not in the whisper receipt as
passed. Evidence may be a reviewed video or a 1920x1080 receipt image; every
source is copied separately so one beat cannot silently display another beat's
evidence.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts" / "presenter"))

from compose import align_to_script, caption_cues  # noqa: E402

SCRIPT = REPO_ROOT / "docs" / "demo" / "presenter-script.json"
RECEIPT = REPO_ROOT / "docs" / "demo" / "presenter-verification.json"
CLIP_DIR = REPO_ROOT / "artifacts" / "presenter" / "clips"
PUBLIC = REPO_ROOT / "remotion" / "public"
STORY_OUT = REPO_ROOT / "remotion" / "src" / "story.json"
FPS = 24
# Grok returns fixed 10s takes, but the film should not inherit their unused
# tails. Keep just enough measured post-speech room for the final consonant and
# a natural visual breath.
HOLD_S = 0.65
MIN_SHOT_S = 5.0
OUTRO_FRAMES = 120


def _srt_time(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    hours, ms = divmod(ms, 3_600_000)
    minutes, ms = divmod(ms, 60_000)
    secs, ms = divmod(ms, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def _whisper_words(mp4: Path) -> list[tuple[str, float, float]]:
    from faster_whisper import WhisperModel

    wav = Path("/tmp") / f"{mp4.stem}-remotion.wav"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(mp4), "-ac", "1", "-ar", "16000", "-y", str(wav)],
        check=True,
    )
    model = WhisperModel("tiny", device="cpu", compute_type="int8")
    segs, _ = model.transcribe(str(wav), word_timestamps=True)
    words: list[tuple[str, float, float]] = []
    for seg in segs:
        for word in seg.words or []:
            token = (word.word or "").strip()
            if token:
                words.append((token, float(word.start), float(word.end)))
    return words


def main() -> int:
    spec = json.loads(SCRIPT.read_text())
    receipt = json.loads(RECEIPT.read_text()) if RECEIPT.is_file() else {}
    shots_receipt = receipt.get("shots") or {}

    public_clips = PUBLIC / "clips"
    public_clips.mkdir(parents=True, exist_ok=True)
    public_evidence = PUBLIC / "evidence"
    public_evidence.mkdir(parents=True, exist_ok=True)

    story_shots = []
    for beat in spec["beats"]:
        for index, line in enumerate(beat["shots"]):
            tag = f"{beat['id']}-{index}"
            rec = shots_receipt.get(tag) or {}
            if not rec.get("passed"):
                print(f"refusing {tag}: not in the passing whisper receipt", file=sys.stderr)
                return 2
            src = CLIP_DIR / f"{tag}.mp4"
            if not src.is_file():
                print(f"missing clip {src}", file=sys.stderr)
                return 2
            dest = public_clips / src.name
            shutil.copy2(src, dest)
            words = align_to_script(line, _whisper_words(src))
            if not words:
                print(f"refusing {tag}: no speech found in clip", file=sys.stderr)
                return 2
            speech_end = max(end for _, _, end in words)
            duration = min(10.0, max(MIN_SHOT_S, speech_end + HOLD_S))
            cues = [
                {"s": round(s, 3), "e": round(e, 3), "t": text}
                for s, e, text in caption_cues(words)
            ]
            folded: list[dict] = []
            for cue in cues:
                if folded and len(str(cue["t"]).split()) <= 2:
                    folded[-1]["e"] = cue["e"]
                    folded[-1]["t"] = f"{folded[-1]['t']} {cue['t']}".strip()
                else:
                    folded.append(cue)
            cues = folded
            visual = (beat.get("broll") or {}).get(str(index))
            if visual:
                visual = dict(visual)
                evidence_path = REPO_ROOT / str(visual["source"])
                if not evidence_path.is_file():
                    print(f"refusing {tag}: evidence source is missing: {evidence_path}", file=sys.stderr)
                    return 2
                visual_duration = float(visual.get("durationSeconds", duration))
                start = float(visual["startSeconds"])
                suffix = evidence_path.suffix.lower()
                if suffix in {".png", ".jpg", ".jpeg", ".webp"}:
                    from PIL import Image

                    with Image.open(evidence_path) as image:
                        if image.size != (1920, 1080):
                            print(
                                f"refusing {tag}: still evidence must be 1920x1080, "
                                f"found {image.size[0]}x{image.size[1]}",
                                file=sys.stderr,
                            )
                            return 2
                    kind = "image"
                elif suffix in {".mp4", ".mov", ".m4v", ".webm"}:
                    evidence_probe = json.loads(
                        subprocess.run(
                            [
                                "ffprobe",
                                "-v",
                                "error",
                                "-show_entries",
                                "format=duration",
                                "-of",
                                "json",
                                str(evidence_path),
                            ],
                            check=True,
                            capture_output=True,
                            text=True,
                        ).stdout
                    )
                    evidence_duration = float(evidence_probe["format"]["duration"])
                    if start + visual_duration > evidence_duration + 0.05:
                        print(
                            f"refusing {tag}: evidence range {start:.2f}-"
                            f"{start + visual_duration:.2f}s exceeds the "
                            f"{evidence_duration:.2f}s reviewed source",
                            file=sys.stderr,
                        )
                        return 2
                    kind = "video"
                else:
                    print(f"refusing {tag}: unsupported evidence type {suffix!r}", file=sys.stderr)
                    return 2
                public_name = f"{tag}{suffix}"
                shutil.copy2(evidence_path, public_evidence / public_name)
                visual["asset"] = f"evidence/{public_name}"
                visual["kind"] = kind
                visual["durationSeconds"] = round(visual_duration, 3)
            story_shots.append(
                {
                    "id": tag,
                    "side": beat["presenterSide"],
                    "clip": f"clips/{src.name}",
                    "durationFrames": max(1, round(duration * FPS)),
                    "speechEndSeconds": round(speech_end, 3),
                    "panel": beat["panelData"],
                    "broll": visual,
                    "cues": cues,
                }
            )

    story = {
        "fps": FPS,
        "outroFrames": OUTRO_FRAMES,
        "shots": story_shots,
        "outro": spec["outro"],
    }
    STORY_OUT.write_text(json.dumps(story, indent=2) + "\n")
    srt_rows: list[str] = []
    cursor_s = 0.0
    cue_index = 1
    for shot in story_shots:
        for cue in shot["cues"]:
            start = cursor_s + float(cue["s"])
            end = cursor_s + float(cue["e"])
            srt_rows.extend(
                [
                    str(cue_index),
                    f"{_srt_time(start)} --> {_srt_time(end)}",
                    str(cue["t"]),
                    "",
                ]
            )
            cue_index += 1
        cursor_s += shot["durationFrames"] / FPS
    srt_out = PUBLIC / "memorystand-presenter.srt"
    srt_out.write_text("\n".join(srt_rows).rstrip() + "\n", encoding="utf-8")
    total = sum(s["durationFrames"] for s in story_shots) + OUTRO_FRAMES
    print(
        f"wrote {STORY_OUT} and {srt_out}  "
        f"{len(story_shots)} shots  {total / FPS:.1f}s"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
