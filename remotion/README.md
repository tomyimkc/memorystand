# MemoryStand Remotion cut

Same composition style as the earlier contest films: Grok supplies lip-synced
takes; Remotion lays the presenter (or live evidence) on one third, a large
claim panel on the other, and captions at the bottom.

```bash
python scripts/presenter/verify_script.py
python scripts/presenter/make_clips.py --verify-only
python scripts/presenter/build_remotion_story.py
cd remotion && npm install && npm run render
```

Output: `remotion/out/memorystand-presenter.mp4`
