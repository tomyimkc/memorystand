---
name: contest-demo-remotion
description: >
  Build a MemoryStand / contest demo film with Grok lip-sync takes plus Remotion
  compose. Use when the user asks to regenerate the demo video, make a presenter
  cut, run the Remotion pipeline, verify presenter clips, or ship a YouTube-ready
  contest MP4. Slash: /contest-demo-remotion. Negative override: do not use for
  product-only screen recordings with no talking head.
---

# Contest demo Remotion pipeline

Talking-head first. Load `/presenter-video-taste` before generating any still or take.

## Hard rules

- Script: `docs/demo/presenter-script.json`. Prefer 10–24 plain-English words per shot. No
  repeated 3-gram. Accurate nouns may recur; never contort ordinary language merely to make
  every content word globally unique.
- `python scripts/presenter/verify_script.py` must pass before any generate.
- Every take is 16:9 medium. Never animate `artifacts/presenter/base/*` if those PNGs are 3:4 portraits.
- Whisper gate ≥ 0.72 via `python scripts/presenter/make_clips.py --verify-only`.
- Remotion is the compositor. Do not ship a raw Grok concat as the public cut.
- Contest-rule footage is mandatory when the rules ask for a functioning project and the
  CockroachDB memory layer at work. Render every authored `broll` range or fail closed.
- Keep the presenter visible over evidence footage; use a proof cut, not a PPT takeover.
- Trim fixed 10-second Grok takes to measured speech plus a short hold. Never equate maximum
  generation duration with honest editorial duration.
- A number and its background must share provenance. Never place seeded values over unrelated
  live footage; use the matching receipt, or state on screen that the example is seeded/synthetic
  and not production evidence.
- Evidence overlays must respect every label already baked into the reviewed footage. Audit one
  full-resolution frame from each b-roll range and move authored headings out of the safe area
  used by `candidateOnly`, `LIVE`, watermark, or source-caption text.
- Text-dense b-roll uses a non-overlap layout: the complete `contain`-scaled source gets a
  protected viewport, while presenter, proof copy, callouts, and subtitles live in separate
  side/bottom rails. Audit early/middle/late frames against the source, not one representative
  still.
- Fail a technically non-overlapping layout when it makes the screen capture too small to read.
  Prefer a short talking-head setup followed by a full-frame, overlay-free evidence handoff.
- Maximize keypoints, not words: one receipt, one decisive state/number, and at most one short
  interpretation line. A whole dashboard shrunk beside explanatory chrome is not a proof shot.
- If the deployed UI already shows the decisive before/after or trust contrast, use a focused,
  credential-free crop of that real interaction before inventing a custom proof board.
- Low-resolution Grok output may be picture-only upscaled for delivery, but the transform must
  preserve framing, copy audio unchanged, and trigger a fresh N/N Whisper verification.
- Use the empty side of presenter-only medium shots for the headline. Do not reduce the central
  point to a tiny lower-third while half the frame is unused.
- Burn subtitles for every spoken word, including evidence handoffs. Reserve a dedicated empty
  subtitle rail in the receipt rather than covering product words, numbers, or provenance.
- Verify the assembled master, not only source clips: compare all SRT cues with the Remotion
  timeline, prove their combined text covers every approved script word, sample a rendered
  frame inside every cue, independently transcribe every assembled shot, and match each
  master-audio segment back to its verified source.
- Before re-running Whisper or building the Remotion story, verify that the selected Python
  environment imports both `faster_whisper` and `PIL`. Use an isolated environment if needed;
  a saved receipt is not evidence that a fresh rerun actually happened.

## Commands

```bash
python3 scripts/presenter/verify_script.py
# Generate takes with Grok image_to_video from 16:9 masters (2 at a time).
# Copy videos/N.mp4 -> artifacts/presenter/clips/<beat>-0.mp4 before the next pair.
python3 -c 'import faster_whisper; from PIL import Image'
python3 scripts/presenter/make_clips.py --verify-only
python3 scripts/presenter/build_remotion_story.py
( cd remotion && npm run render )
python3 scripts/video/verify_video.py remotion/out/memorystand-presenter.mp4 --profile presenter
cp remotion/out/memorystand-presenter.mp4 ~/Downloads/MemoryStand-Remotion-medium-shot-$(date +%F).mp4
```

## Fail closed

- Missing clip or failed whisper → do not render.
- Extracted frame is ECU / no shoulders → delete the take, do not compose it.
- `objectFit: cover` on a portrait source → failed cut. Fix the source, not the CSS.
- Do not upload to YouTube/Vimeo or press Devpost Submit. Owner-only.
- `candidateOnly: true`. Never claim AGI or validated uplift in the film.
