# MemoryStand Remotion cut

Presenter-first. Grok lip-synced takes fill the frame. Remotion only adds
captions, a thin lower-third, and the end card — no slide boards, no
screen-capture takeover.

```bash
python scripts/presenter/verify_script.py
python scripts/presenter/make_clips.py --verify-only
python scripts/presenter/build_remotion_story.py
cd remotion && npm install && npm run render
```

Output: `remotion/out/memorystand-presenter.mp4`
