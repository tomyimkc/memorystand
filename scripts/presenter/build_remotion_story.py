#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Turn verified presenter clips into remotion/src/story.json.

The Remotion composition is the same layout contract as the earlier contest
films. This script is the only place that measures speech and writes the
timeline Remotion will render. It refuses a clip that is not in the whisper
receipt as passed.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts" / "presenter"))

from compose import caption_cues  # noqa: E402

SCRIPT = REPO_ROOT / "docs" / "demo" / "presenter-script.json"
RECEIPT = REPO_ROOT / "docs" / "demo" / "presenter-verification.json"
CLIP_DIR = REPO_ROOT / "artifacts" / "presenter" / "clips"
PUBLIC = REPO_ROOT / "remotion" / "public"
STORY_OUT = REPO_ROOT / "remotion" / "src" / "story.json"
EVIDENCE = REPO_ROOT / "artifacts" / "video" / "memorystand-evidence-source.mp4"
FPS = 24
HOLD_S = 0.45


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
    if EVIDENCE.is_file():
        shutil.copy2(EVIDENCE, PUBLIC / "evidence.mp4")

    story_shots = []
    for beat in spec["beats"]:
        for index, _line in enumerate(beat["shots"]):
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
            words = _whisper_words(src)
            end = words[-1][2] if words else 8.0
            duration = min(10.0, end + HOLD_S)
            cues = [
                {"s": round(s, 3), "e": round(e, 3), "t": text}
                for s, e, text in caption_cues(words)
            ]
            visual = (beat.get("broll") or {}).get(str(index))
            story_shots.append(
                {
                    "id": tag,
                    "side": beat["presenterSide"],
                    "clip": f"clips/{src.name}",
                    "durationFrames": max(1, round(duration * FPS)),
                    "panel": beat["panelData"],
                    "broll": visual,
                    "cues": cues,
                }
            )

    story = {
        "fps": FPS,
        "outroFrames": 72,
        "shots": story_shots,
        "outro": spec["outro"],
    }
    STORY_OUT.write_text(json.dumps(story, indent=2) + "\n")
    total = sum(s["durationFrames"] for s in story_shots) + 72
    print(f"wrote {STORY_OUT}  {len(story_shots)} shots  {total / FPS:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
