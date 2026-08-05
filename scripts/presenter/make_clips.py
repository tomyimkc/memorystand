#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Step 2 of the presenter pipeline: animate each base frame into a lip-synced shot.

    base frame (png)  +  the exact line to say
      -> grok image_to_video
        -> transcribe the result
          -> ACCEPT only if it said what it was told

WHY THE TRANSCRIPT CHECK IS NOT OPTIONAL. ``image_to_video`` generates AUDIO as well as
video, and if the prompt does not contain words, IT INVENTS THEM. The first test here, run
with a motion-only prompt, came back with the presenter saying:

    "So, to summarize the key findings from our analysis, we can conclude that the results
     are promising."

Nobody wrote that. Shipping it would have put invented, meaningless words in the author's
mouth in a submission whose entire argument is that unverified claims must be labelled. Given
the exact line it reproduces it faithfully -- but "usually faithful" is precisely the kind of
thing this project refuses to take on trust, so every shot is transcribed and compared, and a
drifting shot fails loudly instead of being quietly assembled into the final cut.

Similarity is measured on normalised words rather than characters, because the transcriber
mangles brand names in harmless ways ("MemoryStand" -> "Memory stand") that should not fail a
shot, while a wholesale substitution should.

    python scripts/presenter/make_clips.py
    python scripts/presenter/make_clips.py --beat 03-measured
    python scripts/presenter/make_clips.py --verify-only     # re-check existing clips
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT_JSON = REPO_ROOT / "docs" / "demo" / "presenter-script.json"
BASE_DIR = REPO_ROOT / "artifacts" / "presenter" / "base"
CLIP_DIR = REPO_ROOT / "artifacts" / "presenter" / "clips"

# The receipt. compose.py refuses to place any shot that is not recorded here as passing, so a
# clip cannot reach the final cut just by existing on disk -- it has to have been checked. That
# is the same rule this project applies to memories, applied to its own video.
#
# It lives under docs/ rather than artifacts/ because artifacts/ is gitignored, and a claim that
# the footage was verified is worth exactly as much as a reader's ability to check it. The video
# itself stays out of git; the evidence that it says what it claims does not.
RECEIPT = REPO_ROOT / "docs" / "demo" / "presenter-verification.json"

# Below this, the clip is not saying what it was told and must not be used.
MIN_SIMILARITY = 0.72


def _norm(text: str) -> list[str]:
    return re.sub(r"[^a-z0-9 ]", " ", text.lower()).split()


def similarity(asked: str, heard: str) -> float:
    return difflib.SequenceMatcher(None, _norm(asked), _norm(heard)).ratio()


_MODEL = None


def transcribe(mp4: Path) -> str:
    global _MODEL
    wav = Path("/tmp") / (mp4.stem + ".wav")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(mp4), "-ac", "1", "-ar", "16000", "-y", str(wav)],
        check=True,
    )
    if _MODEL is None:
        from faster_whisper import WhisperModel

        _MODEL = WhisperModel("tiny", device="cpu", compute_type="int8")
    segs, _ = _MODEL.transcribe(str(wav))
    return " ".join(s.text for s in segs).strip()


def generate(beat_id: str, index: int, line: str, base: Path, out: Path) -> bool:
    prompt = (
        f"Call image_to_video exactly once with:\n"
        f"  image: {base}\n"
        f'  prompt: The man speaks these exact words to camera, and says nothing else: "{line}" '
        f"Natural lip sync to those words, subtle head motion and blinking, relaxed and "
        f"credible. The camera holds still and the background stays unchanged.\n\n"
        f"Save the result to exactly: {out}\n"
        f"Do not create any other assets."
    )
    subprocess.run(["grok", "-p", prompt], capture_output=True, text=True, timeout=900)
    return out.is_file()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--beat", help="only this beat id")
    ap.add_argument("--verify-only", action="store_true", help="re-check existing clips, generate nothing")
    ap.add_argument("--force", action="store_true", help="regenerate even if the clip exists")
    args = ap.parse_args()

    beats = json.loads(SCRIPT_JSON.read_text())["beats"]
    if args.beat:
        beats = [b for b in beats if b["id"] == args.beat]
        if not beats:
            print(f"no beat with id {args.beat!r}", file=sys.stderr)
            return 2

    CLIP_DIR.mkdir(parents=True, exist_ok=True)
    ok = bad = missing = 0
    receipt: dict[str, dict] = {}
    if RECEIPT.is_file():
        receipt = json.loads(RECEIPT.read_text()).get("shots", {})

    for beat in beats:
        base = BASE_DIR / f"{beat['id']}.png"
        if not base.is_file():
            print(f"  {beat['id']}: no base frame -- run make_base_frames.sh first")
            missing += len(beat["shots"])
            continue

        for i, line in enumerate(beat["shots"]):
            out = CLIP_DIR / f"{beat['id']}-{i}.mp4"
            tag = f"{beat['id']}-{i}"

            if not out.is_file() and not args.verify_only:
                print(f"==> {tag}: generating ({len(line.split())} words)")
                if not generate(beat["id"], i, line, base, out):
                    print(f"    FAILED: {out} was not written")
                    missing += 1
                    continue
            elif not out.is_file():
                print(f"    {tag}: no clip yet")
                missing += 1
                continue

            heard = transcribe(out)
            score = similarity(line, heard)
            receipt[tag] = {
                "clip": out.name,
                "asked": line,
                "heard": heard,
                "similarity": round(score, 3),
                "passed": score >= MIN_SIMILARITY,
            }
            if score >= MIN_SIMILARITY:
                print(f"    {tag}: OK  similarity {score:.2f}")
                ok += 1
            else:
                bad += 1
                print(f"    {tag}: DRIFTED  similarity {score:.2f}")
                print(f"        asked: {line}")
                print(f"        heard: {heard}")
                print(f"        -> delete {out.name} and rerun; do NOT ship this shot")

    RECEIPT.write_text(json.dumps(
        {"minSimilarity": MIN_SIMILARITY, "shots": dict(sorted(receipt.items()))}, indent=2
    ) + "\n")
    print(f"\n  receipt -> {RECEIPT.relative_to(REPO_ROOT)}")

    total = ok + bad + missing
    print(f"\n  {ok}/{total} shot(s) verified saying what they were told")
    if bad:
        print(f"  {bad} shot(s) drifted from the script and must be regenerated")
    if missing:
        print(f"  {missing} shot(s) missing")
    return 0 if bad == 0 and missing == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
