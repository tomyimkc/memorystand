# MemoryStand — general-public Grok presenter cut

**Target runtime:** about 2:25, always below the contest's 3:00 ceiling.

**Delivery:** 1920×1080 H.264/AAC, burned English subtitles, plus a matching
selectable English `.srt`.

## One-sentence story

An AI memory must show an outside receipt before it gets the keys to act alone.

That sentence is the editorial test for every beat. The narration assumes the viewer has
never used an AI agent, CockroachDB, AWS, vector search, or an incident-management tool.
Technical implementation details remain visible in the panels so a technical judge can
inspect them without forcing the general viewer to decode them in the voiceover.

## Eight-beat arc

1. **Receipt first, keys second** — define the product and its safety rule.
2. **A dangerous coincidence** — show how an honest report can still become a false memory.
3. **CockroachDB + AWS** — explain where the memory, audit trail, API, dashboard, and outside
   evidence live.
4. **Held for review** — show three contradicting timeout claims and why weaker evidence waits
   for a person.
5. **CloudWatch receipt** — show that the same service and metric must agree before a memory
   becomes autonomous.
6. **Four trust levels** — explain which memories may act, advise, or only be inspected.
7. **Attack test** — report 540 attacks and 60 honest controls, scoring autonomous authority
   separately from storage.
8. **Honest status** — disclose word-matching search, the fixed decision fallback, and the
   model-free authority gate before repeating the central rule.

The exact words, panels, claim flags, outro, and disclosure live in
[`presenter-script.json`](./presenter-script.json). Run the fail-closed pacing and claim check:

```bash
python scripts/presenter/verify_script.py
```

## Reproducible Grok CLI pipeline

The process is the same verified presenter workflow used for the owner's earlier contest
video:

```bash
# Creator-owned real-person reference. Never commit the source photo.
./scripts/presenter/make_base_frames.sh /absolute/path/to/presenter-reference.png

# Each exact line is animated with Grok image_to_video, transcribed locally,
# and accepted only when similarity is at least 0.72.
python scripts/presenter/make_clips.py

# If Grok CLI reports that Zero Data Retention requires output.upload_url,
# use the same Grok video model through the repository's short-lived,
# token-protected upload bridge:
python scripts/presenter/xai_video.py
python scripts/presenter/make_clips.py --verify-only

# Draw public-friendly data panels and compose the verified clips.
python scripts/presenter/make_panels.py
python scripts/presenter/compose.py --check
```

Outputs are gitignored:

- `artifacts/video/memorystand-presenter.mp4`
- `artifacts/video/memorystand-presenter.srt`

The transcript receipt is committed as
[`presenter-verification.json`](./presenter-verification.json). `compose.py` refuses to use a
clip that does not have a current passing receipt.

## Claim boundaries

- This is a hackathon candidate, not a production-validation claim.
- It does not claim AGI or invention of bitemporal replay.
- Bedrock embeddings are not presented as active: the current build matches words rather
  than meaning because paid quota is unavailable.
- The configured decision-model route returned HTTP 402 on August 9, 2026, so the live
  `/decide` receipt used a fixed fallback with `model_calls: 0`.
- The authority-promotion path is independently model-free and requires external,
  entity-bound evidence.
- The synthetic presenter is disclosed on the end card and in the Devpost AI-tools field.
- With xAI Zero Data Retention enabled, Grok CLI can still create the base frames, but its
  `image_to_video` tool cannot provide the required `output.upload_url`. `xai_video.py` calls
  the same Grok video model through the REST endpoint and receives each MP4 through a
  short-lived, random-token-protected tunnel; it never makes the tunnel a source of trust.
