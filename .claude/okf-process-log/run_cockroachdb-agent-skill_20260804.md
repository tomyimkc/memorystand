---
id: run_cockroachdb-agent-skill_20260804
pageType: memory
sources: ["claude_code/standing"]
mode: claude_code
task: "Author a CockroachDB Agent Skill (skills/) teaching AS OF SYSTEM TIME time-travel audits, matching the upstream cockroachlabs/cockroachdb-skills format exactly, for the Standing hackathon submission."
status: done
model: "claude-sonnet-5"
candidateOnly: true
canClaimAGI: false
links: []
---

# Task: CockroachDB Agent Skill — auditing agent memory with time travel

## Goal
Write, under `skills/`, a CockroachDB Agent Skill that teaches a reusable technique (AS OF SYSTEM
TIME time-travel audits + vector-index prefix design) matching the exact format of
`github.com/cockroachlabs/cockroachdb-skills`, with every SQL/error claim independently verified
against the live CockroachDB v26.2.5 node, then validated with the upstream `validate-spec.py`.

## Plan
1. Fetch upstream repo README, CONTRIBUTING.md, two real SKILL.md examples, and `validate-spec.py`.
2. Verify every claimed SQL trap against the live `crdb-standing` container via psycopg2.
3. Write `skills/cockroachdb-observability-and-diagnostics/auditing-agent-memory-with-time-travel/SKILL.md`.
4. Run upstream's `python scripts/validate-spec.py skills/` and fix findings.

## Actions
- [x] Fetched README.md, CONTRIBUTING.md, `scripts/validate-spec.py`, and two real SKILL.md files
      (`auditing-table-statistics`, `designing-application-transactions`) via raw.githubusercontent.com
- [x] Verified via psycopg2 against v26.2.5: AOST-in-subquery → 42601; `BEGIN AS OF SYSTEM TIME` →
      25001; `SET TRANSACTION AS OF SYSTEM TIME` not-first-statement → 25000; inline AOST after
      implicit txn already fixed → 0A000 (`inconsistent AS OF SYSTEM TIME timestamp`)
- [x] Verified `gc.ttlseconds` is readable via `SHOW ZONE CONFIGURATION FOR TABLE agent_memories`
      (returned 14400s on this cluster)
- [x] Built disposable clone tables (`vidx_good`/`vidx_bad`, 5,000 rows each) to capture real
      `EXPLAIN` output proving vector-index prefix must include every equality filter column
      (`verdict`) or the optimizer falls back to `FULL SCAN` — all scratch tables dropped after
- [x] Verified `missing stats` EXPLAIN annotation appears immediately after a bulk load, before
      `ANALYZE`
- [x] Wrote `skills/cockroachdb-observability-and-diagnostics/auditing-agent-memory-with-time-travel/SKILL.md`
      (400 lines) matching upstream frontmatter (`name`, `description`, `compatibility`, `license`,
      `metadata`) and section conventions
- [x] Ran `python3 scripts/validate-spec.py skills/` (upstream's exact CI command) — 0 errors

## Evidence
- File: `skills/cockroachdb-observability-and-diagnostics/auditing-agent-memory-with-time-travel/SKILL.md`
- Validation: `0 errors, 1 warning` (gerund-form naming nitpick — confirmed upstream's own
  `auditing-table-statistics` skill triggers the identical warning, so this is a validator quirk,
  not a real defect)
- No changes made to `agent_memories`/`agent_decisions`/etc. — all experimentation used disposable
  tables (`badidx`, `vidx_good`, `vidx_bad`, `analyze_probe`, `analyze_probe2`, `gc_probe`), each
  dropped at the end of its script

## Outcome
Shipped one new skill file, format-validated against the real upstream validator with zero errors.
Every SQLSTATE, error string, and EXPLAIN plan quoted in the skill was captured verbatim from this
session's live queries against the CockroachDB v26.2.5 container, not written from memory. One claim
from the task brief (GC-threshold rejection error text) could not be reproduced live in the time
available — the GC queue did not run within the ~45s tested even at `gc.ttlseconds=1` — so the skill
teaches the proactive, verified client-side horizon check instead of quoting an unverified raw
server error string.

## Lessons
**Why:** the upstream validator's naming/third-person checks are naive substring matches (e.g. "AI"
lowercases to contain "i "), so they false-positive on legitimate text; don't over-index on
warnings, only on errors.
**How to apply:** when writing further CockroachDB skills for this submission, always run
`python scripts/validate-spec.py skills/` (the literal CI command) against the whole `skills/`
directory, not just the new skill's own path, since the repo-vs-single-skill traversal logic differs
and a wrong invocation can silently validate nothing.
