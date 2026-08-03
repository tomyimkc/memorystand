# AsOf — agentic memory you can cross-examine

> **A memory layer for on-call AI agents, built on CockroachDB and AWS.** Every fact is checked
> against what the agent already knew *before* it becomes recallable — and CockroachDB itself,
> not a log file, can replay exactly what the agent believed at the instant it paged you.

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

**Submission for the [CockroachDB × AWS Hackathon — Build with Agentic Memory](https://cockroachdb-ai.devpost.com).**

> 🚧 **Under construction.** Started 2026-08-03. This README fills in as the build progresses;
> see [SPIKE-RESULTS.md](SPIKE-RESULTS.md) for what has actually been verified so far, including
> what failed.

---

## The problem

When an on-call agent takes an action at 3am, the first question afterwards is always the same:
**what did it actually know when it decided that?**

Today, answering that means grepping Slack, log aggregators and vector-store snapshots, and
hoping nothing was overwritten. Agent memory is usually an append-only pile of whatever the model
emitted — which means a hallucination written once is recalled forever, and there is no way to
reconstruct the belief state behind a past decision.

AsOf treats those as the same problem: **a memory is only trustworthy if something checked it,
and only auditable if you can replay it.**

## The three mechanics

1. **Write-time adjudication.** A new memory is checked against existing accepted memories — a
   deterministic attribute-conflict check plus a vector-neighbour similarity check — *before* it
   is committed. Contradicting memories land `quarantined` and are never returned by recall; a
   corrected fact `supersedes` the old one rather than deleting it. The adjudication runs before
   the transaction opens; the transaction then re-verifies the rows adjudication read, so a
   concurrent contradicting write forces a real retry instead of silently double-accepting.

2. **Outcome-gated trust.** A memory's `trust_tier` is promoted from `unconfirmed` to `verified`
   only when a real external outcome confirms the decision it produced was right — never by a
   model grading its own work.

3. **Time-travel replay.** `SELECT … AS OF SYSTEM TIME '<t>'` re-runs the agent's *identical*
   recall query pinned to a past instant, and a self-join diffs then against now. No bitemporal
   schema, no triggers, no second store.

## Why CockroachDB, specifically

| Capability | Single-node Postgres + pgvector | CockroachDB |
|---|---|---|
| Replay belief state at time T | Hand-build `valid_from`/`valid_to` + triggers, or bolt on CDC | `AS OF SYSTEM TIME` on the same table |
| Diff two instants | Two self-maintained snapshots + app-level diff | One `FULL OUTER JOIN` against the same table |
| Adjudicate-then-commit under concurrency | SERIALIZABLE exists but is opt-in and often left off | SERIALIZABLE is the *only* isolation level |
| Per-tenant ANN that scales | One shared HNSW index; cost grows with everyone's data | C-SPANN prefix-partitioned on `tenant_id` |

Honest limits are stated in [Limits](#limits) rather than buried.

## Status

| Piece | State |
|---|---|
| Repo, license, schema, spike harness | ✅ scaffolded 2026-08-03 |
| ccloud CLI provisioning (`infra/provision.sh`) | ✅ written against a verified command surface |
| Live cluster + Day-1 spikes | ⏳ blocked on account creation |
| Write path, decisions, time machine | ⬜ |
| Lambda + Bedrock backend | ⬜ |
| Dashboard, benchmarks, video | ⬜ |

## Quickstart

```bash
pip install -r requirements.txt

# 1. Provision the memory layer (CockroachDB Basic, on AWS)
./infra/provision.sh

# 2. Verify the platform assumptions before building on them
export COCKROACH_DSN='postgresql://...'
python scripts/spike_db.py
AWS_REGION=us-east-1 python scripts/spike_bedrock.py

# 3. Apply the schema
cockroach sql --url "$COCKROACH_DSN" -f db/schema.sql
```

## CockroachDB tools used

- **Distributed Vector Indexing** — `agent_memories.embedding` is a native `VECTOR(512)` column
  with a prefix-partitioned `VECTOR INDEX (tenant_id, embedding vector_cosine_ops)`.
- **Cloud Managed MCP Server** — a read-only (`mcp:read`) service account, used from Claude Code
  as the judge- and operator-facing inspection surface. Never in the write path, by design.
- **ccloud CLI** — `infra/provision.sh` provisions the cluster, SQL user and connection string
  non-interactively with `-o json`, and `ccloud audit list` provides the org-level audit trail.
- **Agent Skills** — an authored skill documenting the time-travel memory-audit pattern.

## AWS services used

- **Amazon Bedrock** — Claude (Converse API) for reasoning and contradiction adjudication;
  Titan Text Embeddings V2 (512-dim) for every memory's embedding.
- **AWS Lambda** — the whole backend, behind a Function URL.
- **Amazon EventBridge Scheduler** — the periodic checkpoint job and keep-warm ping.
- **AWS Systems Manager Parameter Store** — the CockroachDB DSN as a SecureString.
- **Amazon CloudWatch Logs** — structured, correlated logs with explicit retention.
- **AWS Amplify Hosting** — the static demo dashboard.
- The CockroachDB cluster itself is provisioned with `--cloud AWS`.

## Limits

Written plainly, because a memory system that overstates itself is the exact failure mode this
project exists to prevent.

- **The adjudication gate is a filter, not a truth oracle.** A false statement that contradicts
  nothing already stored can still be accepted. This bounds persisted error; it does not
  eliminate it.
- **The outcome gate covers memories a decision *produced*,** not memories it merely *consulted*.
  Re-adjudicating consulted memories after a failed decision is a roadmap item, not a shipped one.
- **Time travel is bounded by the cluster's garbage-collection window.** The measured bound is
  recorded in [SPIKE-RESULTS.md](SPIKE-RESULTS.md).
- **Multi-region survival is an architectural property here, not a demonstrated one** — unless
  spike 8 confirms `ccloud cluster disruption` works on this tier, in which case the demo shows
  it and this line changes.

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
