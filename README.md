# MemoryStand — memory that has to stand up

> **A memory layer for on-call AI agents that refuses to call a belief "verified" until something
> outside the model corroborates it** — a resolved incident, a recovered metric, a human's
> sign-off. Never the model grading its own work.

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

**Live demo: <https://ojao6oaxlk26mqfjwpuy7g4dy40tglyi.lambda-url.us-west-2.on.aws>** · [deployment status](docs/DEPLOY_STATUS.md)

**Submission for the [CockroachDB × AWS Hackathon — Build with Agentic Memory](https://cockroachdb-ai.devpost.com).**

> 🚧 **Under construction.** Started 2026-08-03. See [SPIKE-RESULTS.md](SPIKE-RESULTS.md) for what
> has actually been verified so far, including what failed.

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
anyway and the omission would read worse than the admission:

- **Bitemporal / time-travel memory is not new.** [Zep/Graphiti](https://arxiv.org/abs/2501.13956)
  ships it as a headline feature, and 2026 papers ([TOKI](https://arxiv.org/pdf/2606.06240),
  Memento, graph-native bitemporal stores) build on SQL:2011's `FOR SYSTEM_TIME AS OF`. Here it is
  an *implementation choice* — CockroachDB's native MVCC means no separate version table and no
  second store — not a claim of novelty.
- **Write-time contradiction checking is not new.** Mem0, Graphiti, and
  [MemTX](https://arxiv.org/html/2607.23929v2) all do a version of it; MemTX's paper uses much the
  same lifecycle vocabulary this schema does.
- **What is new** is using a *verified real-world outcome* as the promotion signal for memory
  trust, in the on-call domain. Research flags this as an open gap
  ([GLOVE](https://arxiv.org/html/2601.19249v1), [Supersede](https://arxiv.org/html/2606.27472v1));
  no shipped agent-memory product does it.

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
| Test suite (`pytest`) | ✅ **19/19 passing** |
| Concurrency + TOCTOU proof (`scripts/race_demo.py`) | ✅ real `40001` captured |
| Benchmark harness (`scripts/loadtest.py`) | ✅ 10k rows, numbers below |
| Lambda handler, 7 routes, kill switch, degraded mode | ✅ exercised over HTTP |
| Bedrock agent loop + deterministic fallback | ✅ fallback path verified |
| `memorystand` CLI (6 subcommands) | ✅ |
| Static dashboard (`frontend/`) | ✅ 4 panels + seeded-demo preview strip, no build step, driven against a live local API |
| Tamper-evident checkpoints (`backend/snapshots.py`) | ✅ |
| Authored CockroachDB Agent Skill | ✅ upstream format |
| One-command demo (`scripts/demo.sh`) | ✅ 8 beats, exit 0 |
| ccloud provisioning (`infra/provision.sh`) | ✅ flags verified against `ccloud 0.8.23` |
| AWS deploy scripts (`infra/deploy.sh`, `infra/deploy_frontend.sh`, IAM, SSM, keep-warm) | ⚠️ written, **never run** — no AWS account yet. See [docs/DEPLOY.md](docs/DEPLOY.md) for the exact judge-facing URL shape and what stays free. |
| Bedrock with real credentials | ⏳ deterministic stub covers local runs |
| Cloud cluster, MCP server wiring, video | ⬜ |

## Measured results

10,000 memories across 50 tenants, CockroachDB v26.2.5, single node in Docker on an M-series
Mac. Single-run measurement on a laptop, not a controlled benchmark environment — reproduce
with `python scripts/loadtest.py --rows 10000 --tenants 50`.

| Recall latency | p50 | p95 | p99 |
|---|---|---|---|
| **Vector-indexed** (`agent_memories`) | **1.60 ms** | **1.86 ms** | **2.01 ms** |
| Brute-force, same data, no vector index | 15.05 ms | 18.09 ms | 25.20 ms |

**~9.4× faster at p50, ~12.5× at p99.** Write throughput during seeding: 4,093 rows/sec.

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
.venv/bin/python -m pytest -q                                   # 19 tests
.venv/bin/python cli/memorystand.py recall --query "payments failover"
.venv/bin/python scripts/loadtest.py --rows 10000 --tenants 50  # reproduce the numbers below
```

Deploying to real infrastructure instead (needs accounts; **never run against a real
account as of this writing** — see [docs/DEPLOY.md](docs/DEPLOY.md) for the honest status,
the exact judge-facing URL shape, and what stays free for a ~6-week judging window):

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
- **Cloud Managed MCP Server** — a read-only (`mcp:read`) service account, used from Claude Code
  as the judge- and operator-facing inspection surface. Never in the write path, by design.
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
