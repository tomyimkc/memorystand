# The submission video — shot-by-shot script

Target: **under 3:00**, public on YouTube or Vimeo, and it "must include footage showing the
CockroachDB memory layer at work" (the hackathon's own wording). This document is the shot list;
`scripts/record-demo.sh` is the driver that makes recording it mechanical. Read that script's
`--help` before your first take.

The timestamp table below sums to **2:37** of *narration*. That is not the same as runtime: the
unattended `--auto` take measures **2:54.78–2:56.04**, because commands take time to run between
spoken lines. Plan against the measured figure, not the narration total — the real margin before
the hard 3:00 cutoff is about **4–5 seconds**, not 23. Judges are not required to watch past 3:00,
so going over
is a worse failure mode than finishing a little early.

**One number to be honest about up front:** `scripts/record-demo.sh --auto` was run start-to-finish
against the live local cluster three times while building this script; the final run (the current
version of the script, after fixing a `set -e` bug found during testing — see the script's own
comments) took **2 minutes 56.04 seconds of real wall-clock time**
(`0.48s user 0.23s system 0% cpu 2:56.04 total`), consistent with the two runs before it
(2:54.78 and 2:55.87). That is the *unedited, unattended* raw take —
it includes nine 3-second "recording in 3…2…1…action" countdowns (27s total) that a real cut
removes, and its between-beat pauses are deliberately sized to the narration length below, not to
how long the underlying command actually takes (every command in this demo is local-Docker-fast:
sub-second to ~2 seconds; see `scripts/record-demo.sh`'s own timing notes). The *edited* video —
countdowns cut, dead air trimmed to roughly the narration length — is what the table below
describes, and it is grounded in that real run, not a guess: the words-per-beat arithmetic is
checked against the actual narration strings the script prints, and the per-beat durations below
are the exact numbers `scripts/record-demo.sh --list` reports.

## Timestamp table

| Time | Beat | On screen | CockroachDB visibly at work? |
|---|---|---|---|
| 0:00–0:13 | 0. Hook | Title card / talking head | — |
| 0:13–0:27 | 1. Admit a runbook fact | Terminal: `memorystand remember` | |
| 0:27–0:41 | 2. Slack claim held for review | Terminal: `memorystand remember` | |
| 0:41–1:01 | 3. Human correction | Terminal: `remember` + SQL history query | **✅ #1** |
| 1:01–1:23 | 4. Alert → recall, propose, decide | Terminal: `recall` + `remember` + `decide` | |
| 1:23–1:47 | 5. PagerDuty resolves → `grant_standing` | Terminal: `memorystand confirm` | **✅ #2 (the novel claim)** |
| 1:47–2:07 | 6. Cross-examine | Terminal: `memorystand cross-examine` | **✅ #3** |
| 2:07–2:27 | 7. EXPLAIN — the vector index is real | Terminal: raw `EXPLAIN` output | **✅ #4** |
| 2:27–2:37 | 8. Closing | Title card / talking head | — |

**Narration total: 2:37** (spoken words only; measured end-to-end runtime is ~2:55 — see above). Run `scripts/record-demo.sh --list` any time to reprint this table's beat numbers
and durations from the script itself — they are the same numbers, one source of truth.

## The four beats that satisfy "CockroachDB memory layer at work"

The submission requirement is unambiguous about wanting to *see* CockroachDB doing something, not
just hear about it. These four beats are the ones to protect if you have to cut anything else:

1. **Beat 3** — a live `SELECT` against `agent_memories` showing three rows for the same attribute
   with three different verdicts (`superseded`, `quarantined`, `accepted`) — CockroachDB's own MVCC
   history, not a diagram of it.
2. **Beat 5** — `grant_standing()` executing as one CockroachDB `SERIALIZABLE` transaction, with the
   CLI printing `model calls used to decide this: 0` on screen. This is the beat that carries the
   whole pitch.
3. **Beat 6** — `cross-examine` issuing a real `SET TRANSACTION AS OF SYSTEM TIME` read and diffing
   it against the present.
4. **Beat 7** — a raw `EXPLAIN` plan with a `vector search` node and a `prefix spans` line, proving
   the recall path is index-backed, not a table scan.

If you need to trim time under pressure, beats 1, 2 and 4 are the ones with slack (they set up the
story; they do not carry the CockroachDB-at-work requirement).

## Narration lead: the outcome gate, not time-travel

The hook and beat 5 both state the one claim this project makes to originality — a memory is
promoted only when a **real external event** confirms it, with **zero model calls** on that path.
Do not lead with, or spend beat time selling, bitemporal replay (beat 6) as novel: it is
Zep/Graphiti's shipped headline feature, and `README.md`'s own "Prior art, stated honestly" section
says so. Beat 6's narration below is deliberately framed as *proof of a real pinned read*, not as
"look what we invented."

## Beat by beat

Word counts below are counted from the literal narration strings `scripts/record-demo.sh` prints
before each beat's countdown — read them off your own terminal while recording if you don't want
to memorize this page. Reading time is `words ÷ 150 × 60` seconds (150 wpm, a comfortable
conversational pace); every beat's reading time comes in at or under its on-screen budget, which
is deliberate — it leaves room for the terminal output itself to be visible before you move on.

---

### 0. Hook — 0:00–0:13 (13s budget)

**On screen:** title card, or you talking to camera. Not the terminal yet.

**Command:** none.

**Narration (31 words → 12.4s):**
> Every agent memory product decides what to trust by recency, source authority, or asking the
> model if it still believes itself. MemoryStand adds a fourth signal: did the decision actually
> work?

---

### 1. Admit a runbook fact — 0:13–0:27 (14s budget)

**On screen:** terminal, `memorystand remember`, colorized `accepted` verdict.

**Command:**
```
.venv/bin/python cli/memorystand.py remember \
  --content "checkout-api's circuit breaker to the payments gateway trips after 800ms of sustained p99 latency, per the resiliency runbook." \
  --entity checkout-api --key circuit_breaker_timeout_ms --value 800 \
  --source runbook:checkout-resiliency
```

**Narration (31 words → 12.4s):**
> A runbook says checkout-api's circuit breaker to the payments gateway trips at 800 milliseconds.
> I write that down as a memory, and CockroachDB admits it — nothing on file contradicts it yet.

---

### 2. A Slack claim → held for review — 0:27–0:41 (14s budget)

**On screen:** terminal, `memorystand remember`, colorized `held for review` verdict and the
"held for review because" reason line.

**Command:**
```
.venv/bin/python cli/memorystand.py remember \
  --content "Someone in #incidents Slack says checkout-api's breaker now trips at 300ms after a recent change." \
  --entity checkout-api --key circuit_breaker_timeout_ms --value 300 --source slack
```

**Narration (34 words → 13.6s):**
> Now someone claims in Slack that the breaker actually trips at 300 milliseconds — no runbook
> behind it. A low-authority source doesn't outrank a runbook, so this one is held for review, not
> thrown away.

---

### 3. A human corrects it — 0:41–1:01 (20s budget) — **CockroachDB at work #1**

**On screen:** terminal, `memorystand remember` (`accepted`, "replaces the earlier memory"), then
a raw `cockroach sql --format=table` query listing all three rows for the same attribute with
their verdicts: `superseded`, `quarantined`, `accepted`.

**Commands:**
```
.venv/bin/python cli/memorystand.py remember \
  --content "Alice (on-call lead) confirms after the 2026-07 tuning pass: checkout-api's circuit breaker to the payments gateway trips at 500ms, not 800ms and not 300ms. This is authoritative." \
  --entity checkout-api --key circuit_breaker_timeout_ms --value 500 --source human:alice

docker exec -i crdb-memorystand ./cockroach sql --insecure --format=table \
  -e "SELECT source, attribute_value AS value, verdict FROM agent_memories \
      WHERE tenant_id = '<tenant>' AND entity = 'checkout-api' \
        AND attribute_key = 'circuit_breaker_timeout_ms' ORDER BY created_at;"
```

**Narration (49 words → 19.6s):**
> The on-call lead signs off on the real number: 500 milliseconds. A human outranks a runbook, so
> this is admitted and corrects the old fact. Watch — CockroachDB keeps the 800-millisecond fact
> as history, not deletes it; the Slack guess stays held. This is the row's own MVCC history, live.

---

### 4. An alert arrives — 1:01–1:23 (22s budget)

**On screen:** terminal, `memorystand recall` (a ranked table with the 500ms fact on top), then a
short printed summary of the proposed-action memory and the recorded decision.

**Commands:**
```
.venv/bin/python cli/memorystand.py recall \
  --query "checkout-api circuit breaker timeout gateway latency incident"

# (the script captures the rest via --json so it can pass the full memory id
# into --produced; see scripts/record-demo.sh for the exact calls)
```

**Narration (52 words → 20.8s):**
> An incident hits: the payments gateway's latency is spiking. Before doing anything, the agent
> recalls what it knows about checkout-api's breaker — that 500-millisecond fact comes back first,
> ranked by CockroachDB's own vector index. It proposes raising the timeout to 1200 milliseconds,
> and records that decision, with the recalled memory as its evidence.

---

### 5. PagerDuty resolves — 1:23–1:47 (24s budget) — **CockroachDB at work #2 (the novel claim)**

**On screen:** terminal, `memorystand confirm` — "closed the loop on decision …", outcome, promoted
memory id, and the line `model calls used to decide this: 0` in bold.

**Command:**
```
.venv/bin/python cli/memorystand.py confirm \
  --decision-id <decision-id> --outcome success --source pagerduty --ref INC-7734
```

**Narration (48 words → 19.2s):**
> Here's the part nobody else does. The incident resolves in PagerDuty. That real-world outcome —
> not a model — is what promotes this memory to verified, in one serializable CockroachDB
> transaction. Zero model calls on this path. This isn't the model deciding it worked. It's the
> world confirming it did.

---

### 6. Cross-examine — 1:47–2:07 (20s budget) — **CockroachDB at work #3**

**On screen:** terminal, `memorystand cross-examine` — the decision, its rationale, and a diff
table of what changed since the decision was made.

**Command:**
```
.venv/bin/python cli/memorystand.py cross-examine --decision-id <decision-id>
```

**Narration (44 words → 17.6s):**
> Now I ask CockroachDB what the agent believed at the exact instant it made that decision, using
> AS OF SYSTEM TIME — not a guess, not an approximation, a real pinned read of the past. Then I
> diff it against what it believes right now.

---

### 7. EXPLAIN — 2:07–2:27 (20s budget) — **CockroachDB at work #4**

**On screen:** terminal, a raw `EXPLAIN` plan against the ~101-row fixture tenant, ending in the
script's own two assertion lines (`vector search node present: True`, `prefix spans line present:
True`).

**Command (conceptually — the script runs this via `backend.embeddings` so the vector literal is
real, not typed by hand):**
```
EXPLAIN SELECT memory_id FROM agent_memories
WHERE tenant_id = '<fixture-tenant>' AND verdict = 'accepted'
ORDER BY embedding <=> '<query-vector>' LIMIT 5;
```

**Narration (34 words → 13.6s):**
> One more proof: this recall isn't scanning every memory. EXPLAIN shows a real vector-search node
> with prefix spans scoped to this tenant's admitted memories — cost grows with one tenant's data,
> not the whole platform's.

---

### 8. Closing — 2:27–2:37 (10s budget)

**On screen:** back to a title card, or you to camera. Not the terminal.

**Command:** none.

**Narration (23 words → 9.2s):**
> That's MemoryStand: memory an on-call agent can trust, because CockroachDB proved it worked —
> not because a model said so. Code's public; link's below.

---

## Recording checklist

**Before you hit record**

- [ ] `./scripts/run-local.sh` has been run at least once and `crdb-memorystand` is `Up` in
      `docker ps`.
- [ ] Run `./scripts/record-demo.sh --auto` once, off-camera, as a dry run. Confirm it exits 0.
      This also reseeds the ~101-row fixture tenant if anything else has touched it.
- [ ] Terminal font size: **18–20pt minimum** at 1080p so text is legible on a phone screen.
      Bigger is safer than clever.
- [ ] Window size: match your recording resolution exactly (e.g. a terminal sized so the whole
      window is 1920×1080, not a smaller window that gets upscaled) — avoids a blurry re-encode.
- [ ] Close anything that could pop a notification during the take: Slack, Mail, Messages, calendar
      reminders, and anything else that could rap dwell on top of a beat.
- [ ] Close other terminal tabs/panes so a stray prompt or password manager autofill can't leak
      onto screen.
- [ ] `NO_COLOR` must be **unset** for this recording — the CLI's colorized verdicts (green
      `accepted`, yellow `held for review`, etc.) are part of what makes beats 1–3 readable at a
      glance. (`echo $NO_COLOR` should print nothing.)
- [ ] Check your shell prompt does not print anything sensitive (hostname is fine; don't have a
      DSN, API key, or `AWS_ACCESS_KEY_ID` sitting in `$PS1` or a recently-run `history` visible
      via up-arrow). This project uses no real credentials for the demo (`MEMORYSTAND_EMBED_STUB=1`,
      local Docker DSN only) — but check your own shell environment for anything else you have set
      globally before you hit record.
- [ ] Do a `clear` (or open a fresh terminal) immediately before recording so there's no scrollback
      above beat 0.

**During recording**

- [ ] Use `--pause` if you want to narrate live and control your own pacing; use `--auto` for an
      unattended take you'll narrate over in post, or to rehearse timing.
- [ ] Use `--beat N` to re-take a single beat instead of the whole run — see
      `scripts/record-demo.sh`'s own `--help` for the one caveat (an 8-character memory/decision id
      shown in a beat recorded separately from a later beat that references it will differ between
      the two takes; film beats 4, 5 and 6 together in one run if you want the id to visibly match
      across those three cuts).

**After recording**

- [ ] Cut the "recording in 3…2…1…action" countdowns from the final edit; they exist so you know
      when to start talking, not for the viewer.
- [ ] Confirm the final cut is **under 3:00**. Under 2:55 gives you margin; use it if you have it.
- [ ] Confirm all four "CockroachDB at work" beats (3, 5, 6, 7) survived the edit.
- [ ] Upload to **YouTube or Vimeo** and set visibility to **Public** (not Unlisted — the rules say
      public) before you paste the link into the Devpost submission form.
- [ ] Watch the uploaded, public version start to finish once, on the platform, before submitting —
      confirm captions/thumbnail didn't do anything unexpected and the link actually plays for a
      logged-out viewer.
