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

- Script: `docs/demo/presenter-script.json`. 14–20 words per shot. No repeated 3-gram.
- `python scripts/presenter/verify_script.py` must pass before any generate.
- Every take is 16:9 medium. Never animate `artifacts/presenter/base/*` if those PNGs are 3:4 portraits.
- Whisper gate ≥ 0.72 via `python scripts/presenter/make_clips.py --verify-only`.
- Remotion is the compositor. Do not ship a raw Grok concat as the public cut.

## Commands

```bash
python scripts/presenter/verify_script.py
# Generate takes with Grok image_to_video from 16:9 masters (2 at a time).
# Copy videos/N.mp4 -> artifacts/presenter/clips/<beat>-0.mp4 before the next pair.
python scripts/presenter/make_clips.py --verify-only
python scripts/presenter/build_remotion_story.py
( cd remotion && npm run render )
.venv/bin/python scripts/video/verify_video.py remotion/out/memorystand-presenter.mp4 --profile presenter
cp remotion/out/memorystand-presenter.mp4 ~/Downloads/MemoryStand-Remotion-medium-shot-$(date +%F).mp4
```

## Fail closed

- Missing clip or failed whisper → do not render.
- Extracted frame is ECU / no shoulders → delete the take, do not compose it.
- `objectFit: cover` on a portrait source → failed cut. Fix the source, not the CSS.
- Do not upload to YouTube/Vimeo or press Devpost Submit. Owner-only.
- `candidateOnly: true`. Never claim AGI or validated uplift in the film.
