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

# MEASURED, NOT ASSUMED. image_to_video accepts 6 or 10 seconds and nothing else -- asking for 15
# returns "`duration` must be either 6 or 10 seconds. Got 15." and generates nothing.
#
# 10 is the right choice and not merely the bigger one. The generator fits the line it is given
# into the clip it is given, so a 6s clip forces the delivery: the 6s shots came back at 3.3-4.7
# words per second, which sounds hurried because it is. At 10s a 20-word line lands in 8.54s --
# 2.34 words/sec, an unhurried presenting pace -- and each beat becomes one or two long takes
# instead of three short ones, so the cut stops chopping sentences in half.
#
# The corollary is that shot length is now a WRITING constraint: 19-23 words per shot. Fewer and
# the clip pads with silence; more and he starts racing the clock again.
DURATION_S = 10
WORDS_MIN, WORDS_MAX = 17, 24


# Spoken numbers and written numbers are the same claim. The transcriber always returns digits.
_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90,
}
_SCALES = {"hundred": 100, "thousand": 1000, "million": 1_000_000}


def _numberise(tokens: list[str]) -> list[str]:
    """Fold runs of number words into the digits the transcriber would have written.

    WHY THIS EXISTS. Two shots were failed by the similarity gate for saying exactly what they
    were told. The script asks for "minus fourteen thousand two hundred"; whisper hears
    "minus 14,200". Word-for-word that is four tokens against one, and the shot scored 0.53
    against a 0.72 threshold -- a false alarm on a perfect take.

    The tempting fix is to lower the threshold. That would be the wrong fix: it buys these two
    shots by weakening the gate against the failure it exists to catch, which is the generator
    inventing whole sentences. Comparing numbers AS NUMBERS keeps the gate exactly as strict
    about words while making it correct about digits.
    """
    out: list[str] = []
    total = chunk = 0
    active = False

    def flush():
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
            continue  # "two hundred and five" is one number, not a conjunction
        else:
            flush()
            out.append(token)
    flush()
    return out


def _norm(text: str) -> list[str]:
    # Strip digit-group separators first so "14,200" is one token, not two.
    text = re.sub(r"(?<=\d),(?=\d)", "", text.lower())
    return _numberise(re.sub(r"[^a-z0-9 ]", " ", text).split())


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


def last_frame(mp4: Path, out: Path) -> Path:
    """Grab a clip's final frame, to seed the next shot from.

    This is what makes a two-shot beat play as one continuous take. Both shots share a first
    frame with the previous shot's last, so the cut between them lands on identical pixels and
    reads as a single unbroken take rather than a jump cut on the same framing -- which is what
    the six-second version looked like, and it looked like an editing mistake.
    """
    subprocess.run(
        ["ffmpeg", "-v", "error", "-sseof", "-0.05", "-i", str(mp4),
         "-update", "1", "-frames:v", "1", "-y", str(out)],
        check=True,
    )
    if not out.is_file():
        raise SystemExit(f"could not extract the last frame of {mp4.name}")
    return out


def generate(line: str, source: Path, out: Path) -> bool:
    prompt = (
        f"Call image_to_video exactly once with:\n"
        f"  image: {source}\n"
        f"  duration: {DURATION_S}\n"
        f'  prompt: The man speaks these exact words to camera, and says nothing else: "{line}" '
        f"Natural lip sync to those words, subtle head motion and blinking, relaxed and "
        f"credible. The camera holds still and the background stays unchanged.\n\n"
        f"Save the result to exactly: {out}\n"
        f"Do not create any other assets."
    )
    proc = subprocess.run(["grok", "-p", prompt], capture_output=True, text=True, timeout=1500)
    # Never report success on the strength of the CLI's exit code alone -- an earlier render step
    # in this pipeline printed "wrote" three times over an empty directory.
    if out.is_file() and out.stat().st_size > 50_000:
        return True

    # SURFACE THE REASON. This used to discard grok's output entirely, so a hard, permanent
    # failure was indistinguishable from a flaky one. Five clips failed with
    # "API error (status 402 Payment Required): Grok Build usage balance exhausted" and the
    # pipeline reported only "was not written", which reads as transient -- so it was retried
    # three times, sequentially and in parallel, against an account that could not possibly
    # succeed. A generator that cannot say WHY it failed will be retried until someone gives up.
    detail = ((proc.stderr or "") + (proc.stdout or "")).strip()
    for marker in ("Payment Required", "usage balance", "rate limit", "quota", "error"):
        line = next((ln for ln in detail.splitlines() if marker.lower() in ln.lower()), None)
        if line:
            print(f"    grok said: {line.strip()[:180]}")
            break
    return False


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
        # Beats may share a base frame -- the presenter only has to be composed left or right,
        # and generating a fresh portrait per beat buys drift, not variety.
        base = BASE_DIR / f"{beat.get('baseFrom', beat['id'])}.png"
        if not base.is_file():
            print(f"  {beat['id']}: no base frame -- run make_base_frames.sh first")
            missing += len(beat["shots"])
            continue

        for i, line in enumerate(beat["shots"]):
            out = CLIP_DIR / f"{beat['id']}-{i}.mp4"
            tag = f"{beat['id']}-{i}"

            # A continuous beat seeds each shot after the first from its predecessor's last
            # frame, so the whole beat plays as one take.
            source = base
            if i and beat.get("continuous", True):
                previous = CLIP_DIR / f"{beat['id']}-{i - 1}.mp4"
                if previous.is_file():
                    source = last_frame(previous, CLIP_DIR / f"{beat['id']}-{i - 1}-last.png")

            if not out.is_file() and not args.verify_only:
                words = len(line.split())
                if not WORDS_MIN <= words <= WORDS_MAX:
                    print(f"    {tag}: {words} words is outside {WORDS_MIN}-{WORDS_MAX} for a "
                          f"{DURATION_S}s shot -- it will rush or pad. Rewrite the line.")
                print(f"==> {tag}: generating ({words} words, {DURATION_S}s, from {source.name})")
                if not generate(line, source, out):
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

    # THE RECEIPT IS PRUNED ON EVERY RUN, NOT ONLY FULL ONES.
    #
    # This used to be gated on `if not args.beat`, which sounded careful -- a single-beat run
    # should not throw away other beats' results -- and was exactly wrong. Six consecutive
    # per-beat regenerations left the receipt asserting PASS for four clips that no longer
    # existed on disk and for seven whose scripted line had since changed. An outside reviewer
    # then read that receipt, concluded the video was finished, and built a plan around the
    # reclaimed time. A receipt that vouches for footage nobody is shipping is worse than no
    # receipt, because it is believed.
    #
    # An entry survives only if its clip is still on disk AND still says what the current script
    # says. That is safe for partial runs: an untouched beat's entries satisfy both tests and
    # stay, while anything the script moved out from under is dropped rather than left to rot.
    current = {f"{b['id']}-{i}": line
               for b in json.loads(SCRIPT_JSON.read_text())["beats"]
               for i, line in enumerate(b["shots"])}
    receipt = {
        tag: v for tag, v in receipt.items()
        if tag in current
        and (CLIP_DIR / f"{tag}.mp4").is_file()
        and v.get("asked", "").strip() == current[tag].strip()
    }

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
