# Devpost submission — MemoryStand

Ready-to-paste copy for `cockroachdb-ai.devpost.com`, rewritten 2026-08-04 for the current build:
region `us-west-2`, reasoning model `amazon.nova-lite-v1:0` (not Claude — see
[docs/BEDROCK_QUOTA.md](BEDROCK_QUOTA.md)), and the measured 10k-row benchmark in `README.md`.
Field IDs are Devpost's own internal ids, included so this doc lines up with the live form
field-for-field.

## Deadline

**Submissions close 2026-08-18 17:00 ET (2026-08-18 21:00 UTC).** Today is 2026-08-04 — **14 days
out.** Judging is five equally-weighted criteria; ties break toward **Agentic Memory Design**,
then **Technical Implementation**. Prizes: 1st $5,000, 2nd $2,500, 3rd $1,250.

---

## Title, tagline, opening

### Title

**MemoryStand**

### Tagline (one line, ≤ ~70 chars for Devpost's card view)

> Agent memory that earns trust from outcomes, not from asking itself.

### Opening three paragraphs (long description)

> Every agent-memory product on the market decides what to trust using one of three signals:
> **recency** (the newest write wins), **source authority** (the runbook outranks Slack), or
> **model self-consistency** (ask the model whether it still believes itself). All three are the
> agent grading its own homework. MemoryStand uses a fourth signal that none of them use: **did it
> actually work?**
>
> A memory is promoted from `unconfirmed` to `verified` only when a real, external, non-model
> event confirms that the decision it produced was correct — PagerDuty resolving the incident, a
> monitored metric recovering, a named on-call engineer signing off. That promotion path,
> `backend/trust.py`, makes **zero model calls**: it imports no model client, and
> `assert_no_model_calls()` runs on the live path every time `grant_standing()` is called, not
> just in a docstring. Tests cover it. If a model could influence which memories get trusted, the
> whole claim collapses into the self-consistency bucket everyone else is already in.
>
> CockroachDB is not incidental to this — it is why the mechanism is cheap to build correctly.
> `AS OF SYSTEM TIME` lets `cross-examine` re-run an agent's exact recall query pinned to the
> instant it paged someone, with no separate version table. SERIALIZABLE isolation (the only
> isolation level CockroachDB offers) is what makes two agents racing to write contradictory
> memories about the same incident resolve to exactly one winner, provably, under real `40001`
> contention — not a mocked test. And a prefix-partitioned vector index on
> `(tenant_id, verdict, embedding)` keeps a 50-tenant, 10,000-memory ANN search at **1.60 ms p50**
> versus **15.05 ms** brute-force on the same data, because the index partition *is* "this
> tenant's admitted memories," not the whole platform's. Time-travel and write-time contradiction
> checking are prior art (Zep/Graphiti, Mem0, MemTX, TOKI); MemoryStand says so in its own README
> rather than waiting for a judge to find it. The one thing being claimed as new is the outcome
> gate — and it is scoped to the on-call domain, where "did it actually work" has an unambiguous,
> externally-checkable answer within minutes.

---

## Form fields

| # | Field | Id | Required | Status |
|---|---|---|---|---|
| 1 | Demo app URL | 27812 | Yes | **OWNER ONLY** — blocked on deploy, see checklist below |
| 2 | Testing credentials/instructions | 28078 | No | Drafted below |
| 3 | Public repo URL | 27813 | Yes | **OWNER ONLY** — blocked on making the repo public, then `https://github.com/tomyimkc/memorystand` |
| 4 | OSS license file URL | 27814 | Yes | `https://github.com/tomyimkc/memorystand/blob/main/LICENSE` (once public) |
| 5 | CockroachDB tools used | 27815 | Yes (≥2) | Drafted below — all four |
| 6 | AWS services used | 27816 | Yes (≥1) | Drafted below |
| 7 | How meaningfully integrated | 27817 | Yes | Drafted below |
| 8 | Project start date | 27818 | Yes | `08-03-26` |
| 9 | Pre-existing code disclosure | 27819 | Yes | Drafted below |
| 10 | Architecture diagram | 27820 | No | Source exists (`ARCHITECTURE.md`, two Mermaid diagrams, lines 9 and 80) — **OWNER ONLY** to render/export and upload |
| 11 | Feedback on CockroachDB AI tools | 27821 | No | Drafted below |
| 12 | Submitter type | 27822 | Yes | **OWNER ONLY** |
| 13 | Country of residence | 27823 | Yes | **OWNER ONLY** |
| 14 | Organization name | 27824 | No | **OWNER ONLY** (blank if solo) |
| 15 | AI tools leveraged | 27825 | Yes | Drafted below |
| 16 | Level of learning | 27826 | Yes | **OWNER ONLY** — honest self-assessment |
| 17 | AI career value gained | 27827 | Yes | **OWNER ONLY** — honest self-assessment |
| 18 | Not an employee of sponsors | 27828 | Yes | **OWNER ONLY** — checkbox |
| 19 | Eligible jurisdiction | 27829 | Yes | **OWNER ONLY** — checkbox |
| 20 | Age of majority | 27830 | Yes | **OWNER ONLY** — checkbox |

Fields the owner alone can answer are called out again in the day-of checklist at the bottom.

---

### Field 2 — Testing credentials / instructions (optional)

> No account and no credentials are needed to evaluate this project locally.
>
> ```
> git clone https://github.com/tomyimkc/memorystand
> cd memorystand
> ./scripts/run-local.sh        # CockroachDB in Docker + schema + 101 seeded memories, ~1 min
> ./scripts/demo.sh             # the whole story end to end, 8 beats
> ```
>
> This brings up CockroachDB v26.2.5 in Docker and runs the full admission-control → outcome-gate
> → cross-examine loop with no AWS account and no CockroachDB Cloud account. Embeddings fall back
> to a deterministic local stub (`MEMORYSTAND_EMBED_STUB=1`, set automatically by the script and
> announced on screen — a stub result is never presented as a real embedding). `pytest -q` runs
> the 19-test suite. `python cli/memorystand.py recall --query "payments failover"` drives the CLI
> directly. [If a deployed demo app exists by submission time: replace this paragraph with its URL
> and any login notes it needs; keep this local path as a fallback for judges who prefer it.]

### Field 5 — Which CockroachDB tools (pick ≥2; all four apply here)

> **Distributed Vector Indexing.** `agent_memories.embedding` is a native `VECTOR(512)` column
> with `VECTOR INDEX agent_memories_tenant_idx (tenant_id, verdict, embedding vector_cosine_ops)`
> (`db/schema.sql`). `verdict` sits in the index prefix deliberately, not as an afterthought: a
> vector index in CockroachDB is only used by the optimizer when every prefix column is an
> *equality* predicate, so putting `verdict` there is what turns "search this tenant's memories"
> into "search this tenant's *admitted* memories" as a single server-side operation, at index
> cost, instead of a filter applied after the fact. Measured on 10,000 memories across 50 tenants:
> **1.60 ms p50 / 1.86 ms p95 / 2.01 ms p99** indexed, versus **15.05 / 18.09 / 25.20 ms**
> brute-force on the identical data — roughly 9.4× faster at p50, 12.5× at p99
> (`scripts/loadtest.py --rows 10000 --tenants 50`). The before/after is visible directly in
> `EXPLAIN`: with the `verdict` predicate present, the plan shows one `vector search` node with a
> single `prefix spans` range scoped to that tenant+verdict; drop the predicate and the same query
> plan shows three spans, one per verdict value, i.e. the optimizer falls back to scanning every
> partition. That failure mode is silent — no error, just a slower query — which is also the
> subject of the feedback in field 11.
>
> **Agent Skills Repo.** `skills/cockroachdb-observability-and-diagnostics/
> auditing-agent-memory-with-time-travel/SKILL.md` is an authored skill in the upstream
> frontmatter format (name, description, compatibility, license, metadata). It documents the
> `AS OF SYSTEM TIME` audit pattern this project's `cross-examine` command depends on — including
> the trap that cost real debugging time: `AS OF SYSTEM TIME` cannot appear inside a subquery or
> CTE (CockroachDB error `42601`), so the fix is `SET TRANSACTION AS OF SYSTEM TIME` as the first
> statement of the (already-open, psycopg2-managed) transaction, not `BEGIN AS OF SYSTEM TIME`.
> Every SQL statement and `EXPLAIN` plan in the skill was copy-pasted from a real session against
> this project's own cluster, not written from documentation alone.
>
> **Cloud Managed MCP Server.** Wired as a read-only (`mcp:read`) service-account connection,
> used from Claude Code as the judge- and operator-facing inspection surface for the running
> cluster — deliberately never in the write path, so a judge (or an operator) can query live state
> without any route through this project's own application code.
>
> **ccloud CLI.** `infra/provision.sh` provisions the CockroachDB Basic cluster on `--cloud AWS`,
> creates the scoped SQL user, and prints the connection string non-interactively (`-o json`), so
> the same script is usable in CI or by a judge re-running the deploy path rather than only
> point-and-click through the console.

### Field 6 — Which AWS services (pick ≥1)

> Amazon Bedrock (Amazon Nova Lite for reasoning, Titan Text Embeddings V2 for embeddings), AWS
> Lambda, AWS Lambda Function URLs, Amazon EventBridge Scheduler, AWS Systems Manager Parameter
> Store, Amazon CloudWatch Logs, AWS Amplify Hosting, and a CockroachDB cluster provisioned with
> `--cloud AWS`.
>
> **BEFORE PASTING:** tick only what has actually been deployed by then. As of this writing the
> deployment scripts are written and the Lambda package is verified to build and import inside
> the real `public.ecr.aws/lambda/python:3.13` runtime, but the deploy has not been run against
> a live account. Claiming a service that has never run would be the one kind of overstatement
> this entire project argues against — and a judge can check.
>
> Note on the reasoning model: this project originally targeted Claude on Bedrock. Anthropic
> models are refused from this operator's AWS account with `ValidationException: Access to
> Anthropic models is not allowed from unsupported countries...` — a geo-restriction independent
> of IAM and region. The reasoning model is now `amazon.nova-lite-v1:0`, which is AWS-native,
> `ON_DEMAND`, and carries no such restriction. Full detail, including the on-demand-quota
> situation for a brand-new AWS account, is in `docs/BEDROCK_QUOTA.md`.

### Field 7 — How meaningfully integrated

> Argued by what breaks if each piece is removed, since that is a harder bar than listing what was
> used.
>
> **Remove the vector index's `verdict` prefix column** and `recall()` still runs — it just stops
> being tenant-and-admission-scoped at the database layer, silently falling back to a full scan
> (no error), which is exactly the trap this build hit and fixed. **Remove `AS OF SYSTEM TIME`**
> and `cross-examine` has no way to answer "what did the agent believe when it paged you" — the
> only alternative is a hand-built version table and triggers on every write, which is the
> integration cost CockroachDB's native MVCC removes entirely. **Remove SERIALIZABLE isolation**
> (CockroachDB's only isolation level) and the concurrent-contradiction test
> (`scripts/race_demo.py`: 10 concurrent writers, 10/10 updates landed, 9 retries observed as real
> `40001` conflicts, exactly one of two contradictory writes accepted) has no guarantee behind it
> — it becomes a race with no referee. **Remove `backend/trust.py`'s promotion path** and the
> project's entire thesis is gone: it is the one place in the codebase asserted, and tested, to
> make zero model calls (`assert_no_model_calls()`, run on the live path, not just documented).
>
> On the AWS side: **remove Bedrock** and admission control's enrichment step degrades to its
> documented rule-table fallback (`backend/agent.py`) rather than failing closed — by design, so a
> capacity outage (see `docs/BEDROCK_QUOTA.md`) never blocks the trust-critical path. **Remove
> Lambda + the Function URL** and there is no deployed backend, full stop — it is the entire
> runtime, not a component of it. **Remove SSM Parameter Store** and the CockroachDB DSN would
> need to live in a Lambda environment variable in plaintext, which the project's own security
> posture explicitly rejects. **Remove EventBridge Scheduler** and the periodic checkpoint job and
> keep-warm ping stop running, silently degrading Lambda cold-start latency and the tamper-evident
> checkpoint cadence in `backend/snapshots.py`. **Remove Amplify Hosting** and there is no
> judge-facing dashboard, only an API judges would have to `curl` by hand.

### Field 9 — Pre-existing code disclosure

> This repository's first commit is dated 2026-08-03, inside the Submission Period
> (2026-06-30–2026-08-18), with no earlier history — it was initialised from empty. No source
> code from any prior repository was copied into this project; every schema, Lambda handler,
> Bedrock integration, MCP/Agent Skill wiring, spike script, provisioning script, and frontend
> file here was newly written during the Submission Period.
>
> The author has previously worked on a personal, unrelated Apache-2.0 monorepo that explored two
> *design instincts* which carried over as thinking, not as code: (1) a memory store whose writes
> must clear a check before becoming readable — that earlier work used a two-table accepted/held
> split with an application-level verifier on SQLite; this project instead uses a single table
> whose own MVCC history is the audit trail, adjudicated inside a serializable transaction, with
> CockroachDB's `AS OF SYSTEM TIME` for replay, none of which existed in the earlier work; and (2)
> treating an agent's tool surface as needing identity, an audit log, and a kill switch —
> reimplemented here from scratch against a SQL `tool_audit` table with native row-level TTL, AWS
> SSM for secrets, and IAM scoped to named model ARNs. No file, function body, schema string, or
> configuration was reused verbatim from that project, which is itself not a CockroachDB or AWS
> project. Full detail is in `DISCLOSURES.md`, checked into the repo so it is independently
> verifiable rather than only asserted on this form.

### Field 11 — Feedback on CockroachDB AI tools (optional, high-value)

> Four concrete traps from this build, offered as constructive feedback rather than complaint:
>
> **A vector index goes silently unused if a filter column sits outside its prefix, and there is
> no warning.** Our index is `(tenant_id, verdict, embedding)`. Adding an innocuous-looking
> `embedding IS NOT NULL` predicate — the kind of defensive null-check a developer adds out of
> habit — defeated the index entirely with no error, just a query that got slower as the table
> grew. An `EXPLAIN`-based lint, or a warning when a query on an indexed vector column falls back
> to a full scan, would have caught this in seconds instead of costing a profiling session.
>
> **`AS OF SYSTEM TIME` inside a subquery or CTE fails with `42601`, and the message doesn't
> point at the fix.** The error text describes a syntax problem, not "move this to
> `SET TRANSACTION AS OF SYSTEM TIME` as the first statement of the transaction" — which is the
> actual fix, and non-obvious if you're coming from Postgres, where the natural instinct is
> `BEGIN AS OF SYSTEM TIME` (which fails differently, `25001`, because psycopg2 has already opened
> the transaction by the time your code runs).
>
> **`UUID[]` columns arrive at psycopg2 unmapped, as a string, and iterating that string silently
> yields characters, not UUIDs.** There's no error — a `for x in row['some_uuid_array']` loop just
> quietly iterates over `'`, `0`, `9`, ... instead of the array elements. Casting server-side to
> `::STRING[]` fixed it, but nothing on the CockroachDB or psycopg2 side surfaced that the type
> round-trip was wrong.
>
> **Stats need an explicit `ANALYZE` after a bulk load, or the optimizer keeps ignoring the vector
> index even though it exists and is otherwise correct.** This one at least fails loud once you
> know to look for it (a plan without the `vector search` node) — but nothing at load time hints
> that a fresh bulk-loaded table needs it.

### Field 15 — Which AI tools leveraged

> Claude Code (Anthropic) was used throughout for architecture discussion, SQL and Python
> authoring, and documentation — disclosed in full in `DISCLOSURES.md`. Amazon Bedrock (Nova Lite
> for reasoning, Titan Text Embeddings V2 for embeddings) is a **runtime component of the
> submitted agent itself**, not an authoring tool, and is covered separately under AWS services.

---

## Fields only the owner can answer

Rows 1, 3, 10, 12, 13, 14, 16, 17, 18, 19, 20 above. In particular: field 1 (demo URL) and the
final content of field 6 depend on `infra/deploy.sh` actually being run against real AWS
credentials; field 3 depends on flipping the repo from private to public; field 10 needs the
existing Mermaid source in `ARCHITECTURE.md` rendered to an image and uploaded; fields 12–14 and
16–20 are personal/eligibility answers no one else can supply.

---

## 48 hours before the deadline (by 2026-08-16 17:00 ET)

- [ ] `infra/provision.sh` → `infra/ssm_setup.sh` → `infra/deploy.sh` → `infra/deploy_frontend.sh`
      have been run against real AWS + `ccloud` credentials, or a deliberate, honest call has been
      made to submit with only what actually ran.
- [ ] Repo is public; GitHub's own license badge shows Apache-2.0 in the "About" sidebar.
- [ ] `git log -p` spot-checked for anything that shouldn't go public before flipping visibility —
      a private→public flip is not cleanly reversible once anyone has cloned it.
- [ ] `README.md`'s Status table reflects reality — no checkmark for anything that wasn't actually
      run against real infrastructure.
- [ ] Demo video recorded, edited, uploaded, set to **Public** — watch the public link once,
      logged out, start to finish.
- [ ] Demo app URL hit from a browser/network that isn't the owner's, to catch a firewall or CORS
      surprise before a judge does.
- [ ] Every field above except the owner-only rows is filled in on the live Devpost draft.
- [ ] Architecture diagram exported from `ARCHITECTURE.md` and uploaded (field 10).

## Day of (2026-08-18)

- [ ] Re-run `./scripts/demo.sh` (or `scripts/record-demo.sh --auto`) against whatever is live,
      one more time. A cold cluster or an expired credential on the last morning is the single
      most common failure at this stage.
- [ ] Fill in the personal/eligibility fields (12, 13, 14, 16, 17, 18, 19, 20) — only the owner
      can answer these.
- [ ] Submit well before 17:00 ET — Devpost's deadline enforcement is exact; do not test it.
- [ ] After submitting, confirm the dashboard shows **submitted**, not **draft**.
