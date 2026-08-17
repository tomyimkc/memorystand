# MemoryStand — general-public Grok presenter cut

**Target runtime:** about 0:50–1:00, always below the contest's 3:00 ceiling.

**Framing rule (hard):** 16:9 medium shot only. Head + both shoulders + upper chest.
Never cover-crop a 3:4 portrait onto 16:9. See `~/.grok/skills/presenter-video-taste/SKILL.md`.

**Delivery:** 1920×1080 H.264/AAC, burned English subtitles, plus a matching
selectable English `.srt`.

## One-sentence story

An AI memory must show an outside receipt before it gets the keys to act alone.

That sentence is the editorial test for every beat. The narration assumes the viewer has
never used an AI agent, CockroachDB, AWS, vector search, or an incident-management tool.
The film uses one large headline and at most two short keypoints per panel. Four shots cut to
the reviewed deployed-demo footage so judges can see the project functioning, the CockroachDB
memory history, and the AWS outcome gate without forcing the general viewer to decode those
details in the voiceover.

## Seven-beat arc

1. **Receipt first, keys second** — define the product and its safety rule.
2. **A dangerous coincidence** — show how an honest report can still become a false memory.
3. **CockroachDB ledger** — explain why the memory and its audit trail stay together.
4. **Held for review** — show why contradicting memories are preserved instead of silently
   overwritten.
5. **CloudWatch receipt** — show that the same service and metric must agree before a memory
   becomes autonomous.
6. **Attack test** — report 540 attacks and 60 honest controls, scoring autonomous authority
   separately from storage.
7. **Honest status** — disclose basic search, the fixed decision fallback, and the
   model-free authority gate before repeating the central rule.

The voiceover and main panel copy deliberately omit the Lambda/Amplify inventory, the numeric
timeout walkthrough, the four-level taxonomy, HTTP 402, and Bedrock quota mechanics. The exact
technical facts remain in the submission documentation; only a compact footnote survives where
needed for judge verification. Paragraphs were removed from the film because they made the
public story denser without changing its central promise.

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

# Put the reviewed deployed-demo evidence cut at the path referenced by presenter-script.json.
# It remains gitignored because it is a generated delivery artifact.
cp /absolute/path/to/reviewed-deployed-demo.mp4 \
  artifacts/video/memorystand-evidence-source.mp4

# Remotion compose — same layout contract as the earlier contest films.
python scripts/presenter/build_remotion_story.py
( cd remotion && npm install && npm run render )
cp remotion/out/memorystand-presenter.mp4 artifacts/video/memorystand-presenter.mp4
```

Outputs are gitignored:

- `artifacts/video/memorystand-presenter.mp4`
- `artifacts/video/memorystand-presenter.srt`

The transcript receipt is committed as
[`presenter-verification.json`](./presenter-verification.json). `compose.py` refuses to use a
clip that does not have a current passing receipt. It also refuses a live-evidence time range
that exceeds the reviewed source file. Evidence footage is muted; the already-verified narration
remains the only audio, so the film keeps one subtitle and timing source of truth.

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
