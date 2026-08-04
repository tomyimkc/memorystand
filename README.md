# MemoryStand — memory that has to stand up

> **Every shipping agent-memory system asks a model whether its own memory is true.**
> MemoryStand asks CloudWatch — and refuses the promotion when CloudWatch disagrees.

A memory layer for on-call AI agents that will not call a belief `verified` until something
outside the model corroborates it: a recovered metric, re-queried and checked; a resolved
incident; a human's sign-off. The promotion path makes **zero model calls**, and that is
enforced by a runtime guard, not by a convention.

Measured, not asserted — [benchmarks/verification.md](benchmarks/verification.md), 300 labelled
outcome reports:

| | trust the caller | outcome-gated |
|---|---:|---:|
| Memories promoted without confirmation | **204** | **0** |
| Precision | 59.2% | **100%** |
| Recall on genuinely good outcomes | 100% | 98.6% |
| Model calls | 0 | 0 |

Regenerated under five independent seeds the baseline lets through 197–229 unconfirmed
memories at 54–61% precision; the gate lets through **zero every time**, at 98.0–99.3% recall.

The 1.1% of recall given up is the honest price, and it is a knob
(`MEMORYSTAND_EVIDENCE_TOLERANCE`) with a published trade-off curve rather than a hidden
default. The failure being caught is not fraud — it is an engineer who restarts a service at
02:00, sees the page clear, and reports in good faith that the restart fixed it when the metric
never moved. That is how a memory store fills up with true-sounding operational folklore.

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

**Live demo (click this): <https://main.d19xad9aeccy3e.amplifyapp.com>** — the dashboard, deployed
on AWS Amplify Hosting. The API it talks to is
<https://ojao6oaxlk26mqfjwpuy7g4dy40tglyi.lambda-url.us-west-2.on.aws>, a JSON API with no root
route (`GET /` returns 404 by design — it is not a page to open in a browser). ·
[deployment status](docs/DEPLOY_STATUS.md)

**Submission for the [CockroachDB × AWS Hackathon — Build with Agentic Memory](https://cockroachdb-ai.devpost.com).**

> **Built 2026-08-03 onward.** Deployed and verified against real AWS and CockroachDB Cloud —
> see [docs/DEPLOY_STATUS.md](docs/DEPLOY_STATUS.md), which records the failures found along the
> way (an SQL injection, a cross-tenant trust escalation, an ungated trust-granting route, a
> fail-open kill switch) alongside the fixes. Two things are pending and named there: a schema
> migration and a CloudWatch IAM grant, without which `verified` is unreachable in production.
> [SPIKE-RESULTS.md](SPIKE-RESULTS.md) has the earlier spike record, including what failed.

---

## The idea

A witness statement is worth something only if it **stands up** — if it survives being checked
against what else is known, and then against what actually happened. Most of what an agent
"remembers" has never been through either test.

MemoryStand makes a memory stand up twice before an agent is allowed to rely on it: once when it
is written, against everything already believed, and once afterwards, against reality.

Every agent-memory system on the market decides what to trust using one of three signals:
**recency** (newest fact wins), **source authority** (trust the runbook over Slack), or
**self-consistency** (ask the model whether it believes itself). All three are the agent grading
its own homework.

MemoryStand uses a fourth signal that none of them use: **did it actually work?**

A memory is promoted to `verified` only when a real external event confirms the decision it
produced was right — PagerDuty resolving the incident, a latency metric recovering, an on-call
engineer signing off. The promotion path makes **zero model calls**. That is the whole thesis.

## How it works

**1. Earning standing (the part that's new).** A memory enters `unconfirmed`. When the agent acts
on it, that decision is recorded. Later, when the outside world reports back, `grant_standing()`
promotes the memories that decision *produced* to `verified` — or demotes them to `disputed` if
the action was rolled back. No LLM is in that path, by design.

**2. Admission control.** Before a memory is recallable at all, it is checked against what the
agent already believes: a deterministic attribute-conflict check plus a vector-neighbour
similarity search. Conflicting memories are held for review and never returned by recall; a
corrected fact supersedes the old one rather than deleting it.

**3. Cross-examination.** `SELECT … AS OF SYSTEM TIME '<t>'` re-runs the agent's *identical*
recall query pinned to a past instant, so you can ask what it believed at the moment it paged
you, and diff that against now. (CockroachDB rejects `AS OF SYSTEM TIME` inside a subquery, so
the diff is two pinned reads rather than one self-join — see [SPIKE-RESULTS.md](SPIKE-RESULTS.md).)

```
$ memorystand cross-examine --decision-id <id>
```

## Prior art, stated honestly

Being precise about what is and is not new here, because a five-minute search would surface this
anyway and the omission would read worse than the admission.

**The core idea is 47 years old.** Doyle's
[Truth Maintenance System (1979)](https://cse.buffalo.edu/~rapaport/Papers/Papers.by.Others/NONMONOTONIC/doyle79.pdf)
labels a belief `IN` only while a valid justification supports it, and retracts it when no
justification remains — and justifications are never deleted, only invalidated, which is also
this schema's append-only outcome model. Hammond's
[CHEF (1986)](https://aaai.org/papers/00267-aaai86-044-chef-a-model-of-case-based-planning/) and
the wider case-based-reasoning literature index retained cases by whether the plan actually
worked, and tune index strengths on observed success or failure. **"Ground memory trust in
external outcomes" is not a new idea, and this project does not claim it as one.**

**Bitemporal / time-travel memory is not new either.** [Zep/Graphiti](https://arxiv.org/abs/2501.13956)
ships it as a headline feature, and 2026 work ([TOKI](https://arxiv.org/pdf/2606.06240), Memento)
builds on SQL:2011's `FOR SYSTEM_TIME AS OF`. Here it is an *implementation choice* — CockroachDB's
native MVCC means no separate version table and no second store — not a claim of novelty.
**Write-time contradiction checking is not new**: Mem0, Graphiti and
[MemTX](https://arxiv.org/html/2607.23929v2) all do a version of it.

**The trust ladder itself is not new either.**
[NELL](https://cdn.aaai.org/ojs/7519/7519-13-11049-1-2-20201228.pdf) (CMU, running since 2010)
split beliefs into *candidates* and *promoted*, promoting above a 0.9 confidence threshold with
ontological consistency checks and a per-iteration promotion cap. The difference is what supplies
the confidence: NELL's own extractors corroborating each other — self-consistency at scale, which
delivered roughly 74% precision on promoted facts. And in reputation systems
([EigenTrust](https://nlp.stanford.edu/pubs/eigentrust.pdf), Beta reputation) trust updates *only*
on observed transaction outcomes and never on self-report, which is the same normative stance this
project takes, applied to peers rather than to memories.

**The closest live research** is [GovMem](https://arxiv.org/abs/2607.02579) (June 2026), which
governs *when not to write memory*, mapping a candidate to `promote` / `reject` / `needs-review`
and discounting correlated traces so that repetition is not mistaken for independent evidence.
It is genuinely adjacent and arrives at the same instinct from the other end: GovMem governs
promotion at **write** time by analysing the agent's own traces; MemoryStand governs it at
**outcome** time by re-querying something outside the agent entirely. Neither subsumes the other.

### So what is actually different: who is allowed to decide a memory is true

Every shipping agent-memory system answers "is this memory still true?" by asking a model.

| System | Who decides truth | Evidence |
|---|---|---|
| [Mem0](https://arxiv.org/html/2504.19413v1) | An LLM chooses `ADD`/`UPDATE`/`DELETE` per fact. There is **no per-memory confidence score at all**. The 2026 rewrite dropped the reconciliation pass, so contradictory memories now accumulate | [mem0#5867](https://github.com/mem0ai/mem0/issues/5867) — "ADD-only memory extraction can create conflicting memories" |
| [Zep / Graphiti](https://arxiv.org/pdf/2501.13956) | An LLM sets `valid_at` / `invalid_at`. Zep's own guidance is to treat these as **model-inferred, not authoritative** | [graphiti#1666](https://github.com/getzep/graphiti/issues/1666) — on a non-reasoning model, contradiction detection scored 1 of 9, so "temporal invalidation silently underperforms and stale facts survive their own contradiction" |
| [AWS Bedrock AgentCore Memory](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/built-in-strategies.html) | Extraction, consolidation and reflection are each **an LLM system prompt**. No verification or confidence API is exposed | AWS docs |
| **MemoryStand** | A non-model signal, re-checked against the system of record. `backend/trust.py` imports no model client and asserts that on every call | `tests/test_evidence_verification.py` |

The narrow claim, stated so it can be checked rather than admired:

> Trust is granted only by an external non-model signal, on a promotion path that makes zero
> model calls, with credit assigned through the decision that produced the memory — and where
> the signal is machine-checkable, it is re-checked before it counts.

That is a claim about **enforcement**, not about having had the idea first. Doyle had the idea.
What is unusual is a 2026 memory system that refuses to let a model grade its own memory, and
that makes the refusal structural instead of a convention — including a guard that fails if a
model client becomes reachable even one import away
([`assert_no_model_calls`](backend/trust.py)).

### Four rungs, because "someone said so" is not "we checked"

| `trust_tier` | Meaning |
|---|---|
| `unconfirmed` | No outcome reported yet |
| `attested` | An external outcome was reported, but this deployment could not independently re-check it — no PagerDuty token; a human sign-off has no system of record |
| `verified` | Re-queried against the external system of record, which agreed ([`backend/evidence.py`](backend/evidence.py)) |
| `disputed` | The outcome was a rollback or a false positive |

A claim the system of record **contradicts** is refused outright, not quietly downgraded: a memory
must gain nothing from a disproved claim. A claim that could not be checked because CloudWatch was
unreachable is recorded as `attested` — an outage in the checker is not evidence in either
direction, which is why there are four verification states and not a boolean.

## Why CockroachDB, specifically

| Capability | Single-node Postgres + pgvector | CockroachDB |
|---|---|---|
| Replay belief state at time T | Hand-build `valid_from`/`valid_to` + triggers, or bolt on CDC | `AS OF SYSTEM TIME` on the same table |
| Consistent multi-table snapshot at time T | Not available without hand-built history on every table | `SET TRANSACTION AS OF SYSTEM TIME` pins a whole transaction |
| Check-then-commit under concurrency | SERIALIZABLE exists but is opt-in and often left off | SERIALIZABLE is the *only* isolation level |
| Per-tenant ANN that scales | One shared HNSW index; cost grows with everyone's data | C-SPANN prefix-partitioned on `tenant_id` |

## Status

Everything marked ✅ was run against a real CockroachDB v26.2.5 cluster, not just written.

| Piece | State |
|---|---|
| Schema, admission control, outcome gate, cross-examination | ✅ end to end |
| Incident fixtures (101 records, 2 designed conflicts) | ✅ 99 admitted, 2 held |
| Test suite (`pytest`) | ✅ **78 passing** |
| Concurrency + TOCTOU proof (`scripts/race_demo.py`) | ✅ real `40001` captured |
| Benchmark harness (`scripts/loadtest.py`) | ✅ 10k rows, numbers below |
| Lambda handler, 7 routes, kill switch, degraded mode | ✅ exercised over HTTP |
| Bedrock agent loop + deterministic fallback | ✅ fallback path verified |
| `memorystand` CLI (6 subcommands) | ✅ |
| Static dashboard (`frontend/`) | ✅ 4 panels + seeded-demo preview strip, no build step; deployed live on AWS Amplify Hosting at <https://main.d19xad9aeccy3e.amplifyapp.com> |
| Tamper-evident checkpoints (`backend/snapshots.py`) | ✅ |
| Authored CockroachDB Agent Skill | ✅ upstream format |
| One-command demo (`scripts/demo.sh`) | ✅ 8 beats, exit 0 |
| ccloud provisioning (`infra/provision.sh`) | ✅ flags verified against `ccloud 0.8.23`, and run for real — see cloud cluster row below |
| AWS deploy scripts (`infra/provision.sh`, `infra/ssm_setup.sh`, `infra/deploy.sh`, `infra/deploy_frontend.sh`, IAM, SSM, keep-warm) | ✅ **all run against a real AWS account.** Lambda is Active, dashboard is live on Amplify. See [docs/DEPLOY.md](docs/DEPLOY.md) for the URL shape. |
| Bedrock with real credentials | ⚠️ deployed and reachable, but this account's Bedrock quota is ~0 — every live `/decide` call falls back to the deterministic heuristic in `backend/agent.py` (`reasoning_source: fallback_heuristic`, `model_calls: 0`). No live call has actually been reasoned over by a model. |
| Cloud cluster | ✅ CockroachDB Cloud BASIC, AWS us-west-2, CCL v26.2.1. `agent_memories` holds 50,131 rows: 40 synthetic tenants at 1,250 rows each, plus the curated demo tenant (131 rows, 117 accepted). |
| MCP server wiring | ✅ working end to end (see [CockroachDB tools used](#cockroachdb-tools-used) for the honest access-level finding) |
| Video | ⬜ |

## Measured results

10,000 memories across 50 tenants, CockroachDB v26.2.5, single node in Docker on an M-series
Mac. Single-run measurement on a laptop, not a controlled benchmark environment — reproduce
with `python scripts/loadtest.py --rows 10000 --tenants 50`.

| Recall latency | p50 | p95 | p99 |
|---|---|---|---|
| **Vector-indexed** (`agent_memories`) | **2.38 ms** | **2.76 ms** | **2.91 ms** |
| Brute-force, same data, no vector index | 23.61 ms | 28.95 ms | 35.36 ms |

**9.92× faster at p50, 12.14× at p99.** Write throughput during seeding: 2,623 rows/sec
(indexed) versus 6,751 rows/sec (unindexed) — maintaining the vector index costs write
throughput, and that trade is the point of measuring both.

### And at 25× the scale, on a real 3-node cluster

250,000 memories across 200 tenants, `num_replicas = 3`
([full report](benchmarks/results-cluster-250k.md)):

| Recall latency | p50 | p95 | p99 |
|---|---|---|---|
| **Vector-indexed** | **6.87 ms** | **14.37 ms** | **15.65 ms** |
| Brute-force, same data | 526.21 ms | 558.45 ms | 704.59 ms |

**76.5× faster at p50, 45× at p99.** The gap widens with scale, which is the whole
argument for an ANN index: brute force grows with the corpus, the index does not.
Recall stayed in single-digit milliseconds at a quarter-million memories.

The honest other half: maintaining that index cost **5.7× on write throughput**
(972 rows/sec indexed vs 5,520 unindexed). For agent memory that is usually the right
trade — memories are read far more than written — but it is a real cost, and it is
reported rather than omitted.

The plan for the exact query `recall()` issues:

```
└── • lookup join
    │ table: agent_memories@agent_memories_pkey
    │
    └── • vector search
          table: agent_memories@agent_memories_tenant_idx
          target count: 5
          prefix spans: [/'ed2c4a30-…'/'accepted' - /'ed2c4a30-…'/'accepted']
```

That `prefix spans` line is the whole argument for criterion 1: ANN search is scoped to one
tenant's *admitted* memories, so cost scales with that tenant's own data rather than the
platform's. Drop the `verdict` filter and the same EXPLAIN shows three spans — one per
verdict — which is the partition pruning made visible.

Node loss, from `scripts/cluster-demo.sh` — a real 3-node cluster, `num_replicas = 3`:

```
    t+ 2.0s  recall OK  (3 memories)
    *** ms-node3 KILLED ***
    t+ 3.6s  recall OK  (3 memories)
    ...
    12 successful reads, 0 failed, across a node loss
    ms-node3 is not in `docker ps` -- confirmed down
    memories_still_readable: 101
```

This is the one claim that cannot be made on single-node Postgres: the node hosting your
memory died and recall never stopped serving. Full run and its caveats — three containers
share a machine, so this proves the replication mechanism, not a datacentre failure — in
[benchmarks/failover.md](benchmarks/failover.md).

Concurrency, from `scripts/race_demo.py`:

```
N=10 concurrent writers, 10/10 updates landed, 0 lost updates, retries observed (SQLSTATE 40001)
[Part 2] concurrent contradictory writes -> accepted=1 quarantined=1 -> PASS
```

Part 2 is the one a hostile reviewer should probe: two agents submitting contradictory
memories for the same entity at the same instant. Exactly one wins. That is the TOCTOU
guard doing its job under real serializable conflict, not a mocked test.

### Security fixes shipped against the live deployment

All five found and fixed against the real Lambda, not in theory:

- **SQL injection in `GET /diff?instant=`.** `AS OF SYSTEM TIME` cannot be parameterised, and the
  value was being interpolated raw on an *unauthenticated* route. Now validated against an
  allow-list (HLC decimal, negative interval, ISO-8601). Live check: a hostile `instant` now
  returns HTTP 400 and the table is untouched.
- **`trust._apply` had no tenant predicate** — the only unscoped query in the codebase, and the
  one that promotes a memory to `verified`. `tenant_id` is now a required positional argument of
  `trust.grant_standing(tenant_id, decision_id, evidence)`.
- **`POST /confirm_outcome` wasn't behind the shared secret** while `/ingest` and `/decide` were —
  `frontend/app.js` even documented it as "no secret required (read-adjacent)". It's the route
  that grants trust. Now gated; a live check without the secret returns 401.
- **The kill switch failed open:** any SSM read error defaulted to "off" (i.e. keep serving). It
  now fails closed.
- A test now asserts every POST route is secret-gated, so this class of bug can't regress silently.

### Latency, fixed against the live deployment

`/decide` used to return 502 after hanging for the full 30 s. Root cause: `MAX_ATTEMPTS=5` is an
attempt budget, not a latency budget. Fixed with an explicit `DEADLINE_S`, plus disabling
botocore's own hidden retries (an "8 s deadline" measured 18.1 s wall-clock until that was found),
plus `backend/breaker.py`. Live, four consecutive `POST /decide` calls on one warm container, all
HTTP 201: **23.776 s → 9.162 s → 0.851 s → 0.814 s.** `GET /health` now reports
`circuit_breakers`; `POST /decide` responses include `rationale`, `reasoning_source`, and
`model_calls`.

## Quickstart

One command. No AWS account, no CockroachDB Cloud account, no Postgres install.

**Needs:** Docker, Python 3.11–3.13, and `jq` (`brew install jq` / `apt install jq`) — the
demo script parses JSON with it.

```bash
git clone <this-repo> && cd memorystand
./scripts/run-local.sh            # database + schema + seed data, ~1 minute
./scripts/demo.sh                 # watch the whole story end to end
```

`run-local.sh --serve` also starts the API and prints the dashboard URL.
`run-local.sh --fresh` wipes and rebuilds from nothing.

Then:

```bash
.venv/bin/python -m pytest -q                                   # 78 tests
.venv/bin/python cli/memorystand.py recall --query "payments failover"
.venv/bin/python scripts/loadtest.py --rows 10000 --tenants 50  # reproduce the numbers below
```

Deploying to real infrastructure instead (needs accounts; **this has been run against a real
AWS account** — the live demo above is the result — see [docs/DEPLOY.md](docs/DEPLOY.md) for the
honest status, the exact judge-facing URL shape, and what stays free for a ~6-week judging window):

```bash
./infra/provision.sh              # CockroachDB Basic cluster, on AWS, via ccloud
./infra/ssm_setup.sh              # secrets as SSM SecureStrings
./infra/deploy.sh                 # Lambda + Function URL
./infra/deploy_frontend.sh        # dashboard -> AWS Amplify Hosting (amplify.yml at repo root)
```

## CockroachDB tools used

- **Distributed Vector Indexing** — `agent_memories.embedding` is a native `VECTOR(512)` column
  with a prefix-partitioned `VECTOR INDEX (tenant_id, verdict, embedding vector_cosine_ops)`.
  `verdict` is in the prefix deliberately: it is what makes the optimizer use the index at
  all, and it makes the ANN partition *be* "admitted memories of this tenant".
- **Cloud Managed MCP Server** — verified live end to end: handshake to
  `https://cockroachlabs.cloud/mcp` returns `serverInfo {name: "cockroachdb-cloud", version:
  "1.0.0"}`, `select_query` returns real rows, and `explain_query` confirms `vector search: True`.
  Used from Claude Code as the judge- and operator-facing inspection surface, and never in the
  application's write path, by design. The honest access-level finding: CockroachDB Cloud has no
  read-only role that works with this MCP server — its docs require the Cluster Admin or Cluster
  Operator role. The service account `memorystand-mcp-readonly` was first tried with only
  `CLUSTER_DEVELOPER`, under which `select_query` returned "unauthorized"; it now also holds
  `CLUSTER_OPERATOR_WRITER` (shown in the console as Cluster Developer / Cluster Operator), so the
  identity is **write-capable**, not read-only. The server also *offers* `create_database`,
  `create_table`, and `insert_rows` to every identity — tool availability is not permission.
  `scripts/verify_mcp.py` automates this whole check; its write probe is opt-in behind
  `--probe-writes` and has not been run, so whether writes are actually refused is untested.
- **ccloud CLI** — `infra/provision.sh` provisions the cluster, SQL user and connection string
  non-interactively with `-o json`; `ccloud audit list` gives the org-level audit trail.
- **Agent Skills** — an authored skill documenting the outcome-gated memory pattern.

## AWS services used

- **Amazon Bedrock** — Amazon Nova Lite (Converse API) for reasoning and admission control
  (not Claude — Anthropic models on Bedrock are refused from this operator's country; see
  `docs/BEDROCK_QUOTA.md`); Titan Text Embeddings V2 (512-dim) for embeddings. Deliberately
  *absent* from the promotion path.
- **AWS Lambda** — the whole backend, behind a Function URL.
- **Amazon EventBridge Scheduler** — the periodic checkpoint job and keep-warm ping.
- **AWS Systems Manager Parameter Store** — the CockroachDB DSN as a SecureString.
- **Amazon CloudWatch Logs** — structured, correlated logs with explicit retention.
- **AWS Amplify Hosting** — the static demo dashboard.
- The CockroachDB cluster itself is provisioned with `--cloud AWS`.

## Limits

Written plainly, because a memory system that overstates itself is the exact failure mode this
project exists to prevent.

- **Admission control is a filter, not a truth oracle.** A false statement that contradicts
  nothing already stored can still be admitted. This bounds persisted error; it does not
  eliminate it.
- **MemoryStand is granted to memories a decision *produced*,** not memories it merely *consulted*.
  Re-examining consulted memories after a failed decision is a roadmap item, not a shipped one.
- **A confirmed outcome is evidence, not proof.** An incident can resolve for reasons unrelated
  to the action taken. MemoryStand means "survived contact with reality once", not "true".
- **Time travel is bounded by the cluster's garbage-collection window.** The measured bound is
  recorded in [SPIKE-RESULTS.md](SPIKE-RESULTS.md).
- **Node loss is demonstrated** (`benchmarks/failover.md`) on a 3-node cluster. **Multi-region
  survival is not** — that remains an architectural property, not something shown here.

## Repo layout

```
db/schema.sql        the data model, with the reasoning inline
scripts/spike_*.py   Day-1 go/no-go checks; findings land in SPIKE-RESULTS.md
infra/provision.sh   ccloud provisioning
backend/             Lambda handlers (in progress)
benchmarks/          captured latency and concurrency numbers
```

## License

[Apache-2.0](LICENSE). See [DISCLOSURES.md](DISCLOSURES.md) for project provenance.
