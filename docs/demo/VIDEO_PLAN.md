# MemoryStand — evidence-first demo video

**Target runtime:** 2:52 (172 seconds)

**Accepted runtime window:** 2:35–2:55

**Hard ceiling:** 3:00 — the hackathon rules let judges stop watching at 3:00, so going
over is a worse failure than finishing a little short.

**Delivery:** 1920×1080, 30 fps, H.264/AAC (crf ~18, AAC 192k), burned English captions,
selectable English `.srt`.

This is **not** a screen recording. No human filmed a take. Every frame is composed from
a real screenshot of the live deployed system or from real, captured command output;
narration is synthesised per cue and time-fitted to its scene; ffmpeg assembles the
final file. [`video-timeline.json`](./video-timeline.json) is the executable source of
truth for scene order, exact cue text, and timing; this plan explains its evidence and
claim boundaries. `scripts/video/build_frames.py` and `scripts/video/render.py` both read
that JSON directly.

## Editorial thesis

The video separates evidence classes instead of blending them into one product claim. A
color-coded label badge names the exact class on every scene so the taxonomy is visible,
not just stated once in narration:

| Class | What it proves | What it does not prove |
|---|---|---|
| **LIVE AWS** | The deployed Lambda + CockroachDB Cloud (us-west-2) answering real HTTP calls against the public demo tenant, live, during capture. | Sustained production load, multi-region behaviour. |
| **LOCAL 3-NODE** | Node-loss survival on a real 3-node CockroachDB cluster: 12 reads, 0 failed, 101 memories intact through a node kill. | A datacentre failure — three containers share one machine, one disk, one power supply. |
| **LOCAL BENCHMARK** | 250,000 memories, single machine: the vector index reads at 6.87ms p50 vs 526.21ms brute force (76.5x). | A controlled, repeated, statistically-powered benchmark — this is one run. |
| **STUB EMBEDDINGS** | Real latency numbers on a real index. | Relevance ranking — the AWS account has near-zero Bedrock quota, so embeddings are currently a deterministic stub with no semantic meaning. |
| **DETERMINISTIC BENCHMARK** | 540 adversarial cases and 60 honest controls, with storage and autonomous authority scored separately. | A reproduction of any named vendor or an LLM-product comparison. |
| **PRIOR ART** | Which pieces are implementation choices rather than novelty claims (including bitemporal replay and write-time contradiction checks). | A claim that outcome-indexed trust was invented here. |

The claim boundary is visible on every frame's footer:

```
candidateOnly · not validated
```

## Timeline

Roughly 8 scenes, following the arc: the 3am question, admission control holding a
contradicting claim, a human correction, the agent deciding, the outcome gate promoting
(centerpiece — the largest scene budget of any in the video), cross-examination,
node-loss survival, and the close.

| # | Time | Scene id | Duration | Evidence label | What's on screen |
|---:|---|---|---:|---|---|
| 1 | 0:00–0:15 | `01-hook` | 15s | `LIVE AWS` | Title card over the live Amplify dashboard hero + a live `GET /health` response |
| 2 | 0:15–0:35 | `02-admission-holds` | 20s | `LIVE AWS · OUTCOME PATH` | Real `POST /ingest` ×2: runbook fact accepted, Slack claim held for review |
| 3 | 0:35–0:54 | `03-human-correction` | 19s | `LIVE AWS · OUTCOME PATH` | Real `POST /ingest`: the on-call lead's correction, accepted, recorded as corrected-by |
| 4 | 0:54–1:15 | `04-agent-decides` | 21s | `LIVE AWS · OUTCOME PATH · STUB EMBEDDINGS` | Real `GET /recall` + `POST /decide`; recalled rows and cited rows are shown separately |
| 5 | 1:15–1:53 | `05-outcome-gate` | 38s | `LIVE AWS · OUTCOME GATE · RE-CHECKED` | Attested/verified authority split, entity binding, refusal, and `model_calls: 0` |
| 6 | 1:53–2:13 | `06-cross-examine` | 20s | `LIVE AWS · CROSS-EXAMINE` | Real `GET /timemachine`, an `AS OF SYSTEM TIME` diff against the live database |
| 7 | 2:13–2:34 | `07-node-loss-scale` | 21s | `LOCAL 3-NODE · LOCAL BENCHMARK` | Real captured terminal text from `scripts/cluster-demo.sh failover` + the 250k-row benchmark numbers |
| 8 | 2:34–2:52 | `08-close` | 18s | `PRIOR ART · CANDIDATE ONLY` | Prior-art citations, poisoning authority result, repo link, claim boundary |

Total: **172 seconds (2:52)**, eight seconds under the 3:00 ceiling.

## Beat by beat: exact narration

The blockquotes below mirror the executable timeline. The renderer allocates each scene's fixed
duration across its cues by word count and time-fits the synthesised narration without changing
the caption text.

### 1. Storage is not authority — 15s budget (`01-hook`)

**Narration:**
> Agent-memory products commonly use recency, source authority, or model-led reconciliation.
> MemoryStand separates a memory being stored from that memory having authority to act. Only an
> outcome re-checked against CloudWatch can make a memory autonomous. If CloudWatch disagrees,
> the promotion is refused.

### 2. Admission control holds a contradicting claim — 20s budget (`02-admission-holds`)

**Narration:**
> A runbook fact goes in first, live: checkout-api's breaker trips at 800 milliseconds.
> Nothing on file contradicts it, so it's accepted. A Slack claim arrives next, saying
> 300 milliseconds, with no runbook behind it. Slack doesn't outrank a runbook, so that
> claim is held for review, not thrown away.

### 3. A human correction — 19s budget (`03-human-correction`)

**Narration:**
> The on-call lead corrects it, live: 500 milliseconds, the real number. A human outranks
> a runbook, so this is accepted, and recorded as corrected by a higher-authority source.
> The 800-millisecond fact stays on record, not deleted — the Slack guess is still just
> held, nobody confirmed it.

### 4. The agent decides — 21s budget (`04-agent-decides`)

**Narration:**
> An incident hits: payments-gateway latency is spiking. Recall returns the recorded history,
> but unchecked memories are inspection-only and cannot steer the action. The agent proposes one
> action from a closed allow-list. Its receipt separates every recalled row from the smaller set
> it actually cited, and only verified memory may enter autonomous reasoning. One honest
> disclosure: this AWS account has zero Bedrock quota, so embeddings are still a lexical stub,
> not semantic. Recall matches wording, not meaning.

This is the one required disclosure point (§ Claim discipline): say the fallback is
engaged and why; never claim Bedrock is serving embeddings right now.

### 5. The authority gate — 0 model calls — 38s budget (`05-outcome-gate`), centerpiece

**Narration:**
> A PagerDuty resolution is recorded as attested because this deployment cannot re-check it.
> Attested memory may advise, but the action is held for human approval. For a metric
> claim, MemoryStand re-queries CloudWatch, requires enough datapoints, and binds the metric to
> the memory's entity. Only a match reaches verified and may act autonomously. Zero model calls
> occur on that authority path. A wrong magnitude, opposite direction, or another service's metric is
> refused. A real number about the wrong subject is still not evidence for this memory.

This scene gets the largest budget of any scene in the video — this is the one claim the
whole pitch rests on.

### 6. Cross-examination — 20s budget (`06-cross-examine`)

**Narration:**
> This part isn't new — Zep and others already ship bitemporal replay. What this proves
> is a real pinned read: AS OF SYSTEM TIME re-runs the exact query at the instant the
> decision was made, against the live database, not a reconstruction. Then it's diffed
> against what's true right now.

Framed deliberately as proof of a real pinned read, never as "look what we invented" —
see § Editorial thesis, PRIOR ART.

### 7. Node-loss survival + scale — 21s budget (`07-node-loss-scale`)

**Narration:**
> Separately, on a real three-node cluster, we kill a node mid-recall. Twelve reads, zero
> failures, all 101 memories still there. Three containers on one machine — this proves
> the replication mechanism, not a datacenter failing. At 250,000 memories on that
> cluster, indexed recall held at 6.87 milliseconds against 526 unindexed: 76.5 times
> faster.

### 8. Close: the claim boundary — 18s budget (`08-close`)

**Narration:**
> None of this is a new idea. Doyle's truth maintenance systems retracted beliefs when their
> justification failed, in 1979. Bitemporal replay ships in Zep today. Across six hundred
> deterministic cases, the permissive baseline gave every attack autonomous authority. The
> outcome gate gave unverifiable reports zero autonomous control, refused bad metrics and wrong
> entities, and preserved every honest control. Hackathon build: candidate only, no
> general-intelligence claim, code's public.

## Capture sources (all real; see `scripts/video/capture_*` for how each is produced)

- **Live dashboard screenshot** — `scripts/video/capture_dashboard.mjs` (Playwright)
  against `https://main.d19xad9aeccy3e.amplifyapp.com`, saved to
  `artifacts/video/capture/screenshots/`.
- **Live API evidence (scenes 2–6)** — `scripts/video/capture_evidence.py` against
  `https://ojao6oaxlk26mqfjwpuy7g4dy40tglyi.lambda-url.us-west-2.on.aws`, using a fresh
  per-capture tenant and the seeded agent
  `1a2b3c4d-5e6f-4708-9a0b-1c2d3e4f5061`:
  - `GET /health` (before any write, to capture the pre-call baseline)
  - `POST /ingest` ×3 — runbook fact (800ms), Slack claim (300ms), Alice's correction
    (500ms) — each requires the `x-memorystand-secret` header, fetched at build time via
    `AWS_PROFILE=memorystand aws ssm get-parameter --name /memorystand/shared_secret
    --with-decryption --region us-west-2 --query Parameter.Value --output text` and never
    printed, logged, or captured in a frame
  - `GET /recall?tenant_id=...&q=...&k=...` (the full returned ranking is shown, while
    the decision receipt separately identifies the smaller cited set)
  - `POST /decide` (requires the same secret header)
  - `POST /confirm_outcome` — the response's `model_calls: 0` field is the centerpiece
    evidence
  - `GET /timemachine?tenant_id=...&decision_id=...`
  - `GET /health` again, after the `/recall` call above — the `embedding_provenance`
    field only reports `deterministic local stub (512d, no semantic meaning)` once an
    embedding call has actually happened in that warm Lambda container; capture order
    matters here and the capture script must call `/recall` before the second `/health`
    check, or the field will still read `no embeddings computed`.
  - Raw request/response pairs plus a SHA-256 of each response body are written to
    `artifacts/video/capture/evidence.json`.
- **Local 3-node failover** — read directly from the committed
  [`benchmarks/failover.md`](../../benchmarks/failover.md), not re-captured. That file is
  the real terminal transcript of `scripts/cluster-demo.sh failover` (dated
  2026-08-04); reading it directly avoids a second, possibly-drifted copy of the same
  evidence.
- **Local 250k benchmark** — read directly from the committed
  [`benchmarks/results-cluster-250k.md`](../../benchmarks/results-cluster-250k.md), same
  reasoning — regenerating it costs roughly 257 seconds of write-seeding alone (see that
  file's own write-throughput table), which is why it is a published receipt rather than
  a live capture for this pipeline.
- **Prior art** — static citations from [`README.md`](../../README.md)'s "Prior art,
  stated honestly" section; no capture needed.

## Claim discipline

Checkable statements `scripts/video/verify_claims.py` asserts against:

1. **Lead with the authority boundary.** Scene 1 states that storage is not permission to act
   and names the external outcome check. Scene 6 explicitly states bitemporal replay is not new
   before showing it.
2. **Never claim Bedrock serves embeddings right now.** Scene 4's narration states the
   deterministic stub is in use and why (near-zero Bedrock quota on this AWS account). No
   other scene claims or implies a real embedding call.
3. **Never say multi-region, datacenter failover, or production-validated.** Scene 7's
   narration explicitly says the node-loss run is *not* a datacenter test. The verifier
   greps every narration string for `multi-region`, `datacenter failover`,
   `production-validated` (case-insensitive) — must be zero hits, with the sole permitted
   exception being the negation in scene 7 ("not a datacenter failing").
4. **Say "held for review" and "corrected by"; never "quarantine" / "supersede" /
   "belief state".** This applies to every spoken narration string and every burned
   caption. It does not constrain the schema's own internal vocabulary (visible only in
   `db/schema.sql` and the Python source, never on screen) — the CLI and API responses
   this video captures already avoid those three words in user-facing output, matching
   `cli/memorystand.py`'s own house style.
5. **Numbers are exact, not rounded or invented.** `540` attack cases and `60` honest
   controls come from `benchmarks/poisoning.md`; `76.5` (p50 speedup), `6.87` / `526`
   (ms, indexed / unindexed at 250k), `101` (memories surviving node loss), `12` / `0`
   (successful reads / failed reads during the kill), `800` / `300` / `500` (millisecond
   values in the incident story), `0` (model calls). The verifier diffs every number
   token in the narration strings against this fixed table.
6. **`candidateOnly: true` and `canClaimAGI: false`** appear in the closing card and in
   the render manifest, and neither claim is contradicted anywhere else in the video.

## Build

```bash
# 1. Capture (writes artifacts/video/capture/, gitignored)
PLAYWRIGHT_PACKAGE=/Users/tom/Documents/GitHub/HVE-V1.0/website/node_modules/playwright \
  node scripts/video/capture_dashboard.mjs
AWS_PROFILE=memorystand .venv/bin/python scripts/video/capture_evidence.py

# 2. Compose frames (writes artifacts/video/frames/, gitignored)
.venv/bin/python scripts/video/build_frames.py

# 3. Render (writes artifacts/video/memorystand-demo.mp4 + .srt, gitignored)
.venv/bin/python scripts/video/render.py

# 4. Verify the technical envelope (duration, resolution, codec, captions)
.venv/bin/python scripts/video/verify_video.py \
  artifacts/video/memorystand-demo.mp4

# 5. Verify claim discipline against § Claim discipline above
.venv/bin/python scripts/video/verify_claims.py
```

Steps 2 and 3 are re-runnable without repeating step 1 as long as
`artifacts/video/capture/` already has its screenshots and `evidence.json`. All five
scripts exist under `scripts/video/`; `scripts/video/capture_live.mjs` and
`scripts/video/capture_terminal.py` are optional richer/alternate capture producers
(a fuller dashboard walkthrough and locally-rendered terminal frames, respectively) --
`build_frames.py` accepts their output where it overlaps with `capture_dashboard.mjs`'s,
but only `capture_dashboard.mjs` and `capture_evidence.py` are required to build every
frame in the current 8-scene timeline.
