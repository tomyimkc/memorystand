# MemoryStand presenter cut — hostile review

Review date: **2026-08-18**

## Rejected handover cut

File: `MemoryStand-Remotion-why-hold-2026-08-18.mp4`
SHA-256: `1dddd4d17c122dd3fc7445c5de2531187a104593712fec0e72a364e8ee83885c`
Runtime: **98.048 seconds**

**Verdict: REWORK-BOTH**

- **0:10–0:17 — the why-beat is asserted, not demonstrated.** “The latency
  figure never moved” names neither the metric nor a value. The viewer never
  sees the outside receipt, so “alert stopped” can still feel like evidence
  that the restart worked.
- **0:00–1:28 — talking head only.** AWS CloudWatch, CockroachDB, the ledger,
  and the attack test are spoken but never shown. That is weak evidence for a
  contest asking to see the functioning project and CockroachDB memory at work.
- **0:07.47–0:10.12, 0:17.17–0:20.08, 0:25.85–0:30.13,
  0:36.26–0:40.08, and 0:45.79–0:50.11 — visible padding.** Each fixed
  ten-second source take holds on an idle face for roughly 2.7–4.3 seconds.
  The longer runtime does not add understanding.
- **0:40–0:46 — “heading or scale” is jargon.** It is an unnatural substitute
  for “direction or amount” and makes the refusal rule harder to parse.
- **1:10–1:17 — 540 / 60 / 0 have no receipt.** Important numbers exist only
  in narration and small subtitles.
- The presenter is a correct 16:9 medium shot, but the generated takes are
  approximately **736×400** and visibly soft when enlarged to 1920×1080.
- Repeated left-composed takes and an unchanged background make the film
  visually monotonous.

The full audio transcript was extracted from the assembled master. Silence
detection independently confirmed the long idle tails above.

## Replacement cut

File: `MemoryStand-Remotion-large-keypoints-subtitle-safe-2026-08-18.mp4`
SHA-256: `d5ca8ea60407ccd1ec02b46da953daade8bb39c229220a25cb900cce6fb7c44a`
Runtime: **72.597 seconds**

**Verdict: KEEP**

- **0:08–0:16.25 — the false-cause explanation is now concrete.** At about
  **0:09.85**, the frame shows `RESTART → ALERT QUIET → LATENCY FLAT`,
  **223 ms → 220 ms**, `QUIET ≠ IMPROVEMENT ≠ CAUSE`, and a visible warning
  that this is a seeded example rather than production CloudWatch data.
- **0:16.25–0:22.71 — receipt before keys.** The live-project receipt shows a
  reachable database and the compact rule `STORE + OUTCOME RECEIPT = MAY ACT`.
- **0:22.71–0:29.54 — CloudWatch check.** The decisive values
  **−14,200 ms claimed / −14,205 ms observed** and the refusal state occupy
  the full frame rather than a small side panel.
- **0:37.71–0:44.96 — CockroachDB history.** A pinned read of **129 memories**
  and the later **1 CHANGE** remain visible together.
- **0:52.17–0:59.54 — attack receipt.** **540 stored / 0 promoted / 60 of 60
  honest controls kept** are the largest elements on screen.
- Every evidence handoff uses the complete 1920×1080 frame. Presenter chrome
  and callouts do not cover evidence. Burned subtitles occupy a deliberately
  empty bottom rail.
- Final-master verification passed: **20/20 visible subtitle cues**, exact
  approved-script coverage, **9/9** independently transcribed master shots
  (minimum similarity **0.844**), and **9/9** audio-source matches (minimum
  correlation **0.985**, maximum measured drift **43 ms**).

## Still unproven

- The receipts are focused visualizations built from captured deployed
  evidence, not a fresh live screen recording inside the film. The public
  dashboard and `/health` endpoint returned HTTP 200 on 2026-08-18, and
  `/health` reported the CockroachDB database reachable, but the uploaded
  contest video must not be described as a live recording.
- Presenter softness remains because the Grok source takes are approximately
  736×400.
- No public YouTube/Vimeo URL exists until the owner uploads the MP4.
- No Devpost submission is proven until the owner presses **Submit** and the
  dashboard shows a submitted state.
