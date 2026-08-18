# MemoryStand Remotion proof cut

Presenter-led. Grok lip-synced medium shots carry the narration. Selected
beats keep the presenter visible over reviewed product evidence so judges see
the functioning project and CockroachDB memory layer, not just hear them named.
Remotion adds captions, evidence callouts, and the end card.

```bash
python scripts/presenter/verify_script.py
python scripts/presenter/make_clips.py --verify-only
python scripts/presenter/build_remotion_story.py
cd remotion && npm install && npm run render
```

Output: `remotion/out/memorystand-presenter.mp4`
