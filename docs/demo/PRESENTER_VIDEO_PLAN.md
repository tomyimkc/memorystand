# MemoryStand — general-public Grok presenter cut

**Target runtime:** about 1:10, always below the contest's 3:00 ceiling.

**Framing rule (hard):** 16:9 medium shot only. Head + both shoulders + upper chest.
Never cover-crop a 3:4 portrait onto 16:9. See `~/.grok/skills/presenter-video-taste/SKILL.md`.

**Delivery:** 1920×1080 H.264/AAC, burned English subtitles, plus a matching
selectable English `.srt`.

## One-sentence story

An AI memory must show an outside receipt before it gets the keys to act alone.

That sentence is the editorial test for every beat. The narration assumes the viewer has
never used an AI agent, CockroachDB, AWS, vector search, or an incident-management tool.
The film uses one large headline and at most three short evidence callouts. Five shots use
reviewed deployed-demo footage while keeping the presenter visible, so judges can see the
project functioning, the CockroachDB memory history, the AWS outcome gate, and the adversarial
test receipt without turning the film into a screen-capture walkthrough.

## Nine-beat arc

Causal order. The Grok source take remains 10 seconds, but Remotion trims the final shot to
measured speech plus a 0.65-second hold. Fixed generation length is not editorial runtime.

1. **A dangerous coincidence** — an honest restart is remembered as the fix.
2. **Why that is false** — in a clearly labeled seeded decision-rule test, the page cleared
   while service latency stayed essentially flat (223 ms before, 220 ms after, versus a claimed
   112 ms improvement); timing alone does not prove the reboot caused recovery. The film states
   that these numbers are not production CloudWatch data.
3. **Receipt first, keys second** — a memory must show a receipt before it acts alone.
4. **CloudWatch receipt** — AWS must agree on metric, direction, and size.
5. **Refuse on mismatch** — CloudWatch showing the wrong direction or amount blocks acting alone.
6. **CockroachDB ledger** — the fact, its source, its trust, and later decisions stay together.
7. **Held for review** — disagreeing memories are kept; the weaker one waits for a person.
8. **Attack test** — show the receipt: 540 attacks stored, 0 granted authority, 60 honest
   controls preserved.
9. **Honest status** — word search, a fixed fallback, and zero model calls on promotion.

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

The Remotion component renders every `broll` entry. Each evidence beat keeps the talking-head
visible in a medium-shot picture-in-picture; the product footage supplies the proof, not a PPT
board. `build_remotion_story.py` fails closed if a requested evidence range would exceed the
reviewed source.

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
