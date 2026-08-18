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
The film uses one large headline and only the decisive state or number. Five shots begin on the
medium-shot presenter, then hand the complete 1920×1080 frame to focused receipts derived from
captured deployed API, CockroachDB, and CloudWatch evidence. The previous receipt-first beat is
rejected because it let a `checkout-api` row steer a `payments-service` incident. The
replacement must show the corrected subject policy: the closer unconfirmed restart and farther
wrong-service scale-up stay visible, both are excluded from authority, the fixed fallback is
disclosed, and `model calls: 0` remains visible. A whole-dashboard shrink remains rejected
because its product words are unreadable at normal playback size.

Before a deployed interaction becomes a “success” receipt, verify the cited memory's entity
and the receipt that earned its trust tier. A green label is not enough: if the entity differs,
the row is evidence of exclusion, not evidence for the action. Capture the structured target,
eligible ids, exclusion reasons, cited ids, reasoning source, approval state, and model-call
count together so the picture cannot imply a stronger result than the API returned.

## Nine-beat arc

Causal order. The Grok source take remains 10 seconds, but Remotion trims the final shot to
measured speech plus a 0.65-second hold. Fixed generation length is not editorial runtime.

1. **A dangerous coincidence** — an honest restart is remembered as the fix.
2. **Why that is false** — in a clearly labeled seeded decision-rule test, the alert went
   quiet while service latency changed only 3 ms (223 ms before, 220 ms after, versus a claimed
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

# Build five focused 1920x1080 receipts from captured evidence and fixed test claims.
python scripts/video/capture_guided_refusal.py
python scripts/video/build_presenter_receipts.py

# Remotion compose and final-master verification.
python scripts/presenter/build_remotion_story.py
( cd remotion && npm install && npm run render )
cp remotion/public/memorystand-presenter.srt remotion/out/memorystand-presenter.srt
python scripts/video/verify_presenter_subtitles.py remotion/out/memorystand-presenter.mp4
python scripts/video/verify_video.py remotion/out/memorystand-presenter.mp4 --profile presenter
cp remotion/out/memorystand-presenter.mp4 artifacts/video/memorystand-presenter.mp4
```

The Remotion component renders every `broll` entry. Each evidence beat starts on the medium-shot
presenter for about 1.85 seconds, then makes a hard handoff to a full-frame receipt. Presenter
chrome, headings, gradients, and callouts do not cover the evidence. The receipt generator
reserves an empty lower rail for burned subtitles, so captions remain present for every spoken
word without covering a product label, metric, number, provenance line, or the presenter's mouth.
`build_remotion_story.py` rejects a still that is not exactly 1920×1080.

Presenter-only beats use the empty side of the genuine medium shot for one large headline and at
most two short keypoint pills. The conflict beat also uses this same hero contract; a prior
quote-only panel shape was rejected after the compositor silently dropped its `lines` field.
The source 736×400 Grok takes are picture-only upscaled with a center crop, Lanczos resampling,
and restrained sharpening before Remotion; their AAC audio is copied byte-for-byte and every
transformed file must pass Whisper again.

Outputs are gitignored:

- `artifacts/video/memorystand-presenter.mp4`
- `artifacts/video/memorystand-presenter.srt`

The transcript receipt is committed as
[`presenter-verification.json`](./presenter-verification.json). The story builder refuses to use
a clip that does not have a current passing receipt. The final-master subtitle gate then compares
all 20 SRT cues with the Remotion timeline, samples a rendered frame inside every cue to prove the
burned text exists, independently transcribes all nine assembled shots against their approved
lines, and cross-correlates each master-audio segment against its verified source clip. Source
verification alone is not accepted as proof that the compositor kept subtitles or audio in sync.

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
