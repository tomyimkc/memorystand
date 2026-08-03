# Standing — which memories have standing

> **A memory layer for on-call AI agents that refuses to call a belief "verified" until something
> outside the model corroborates it** — a resolved incident, a recovered metric, a human's
> sign-off. Never the model grading its own work.

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

**Submission for the [CockroachDB × AWS Hackathon — Build with Agentic Memory](https://cockroachdb-ai.devpost.com).**

> 🚧 **Under construction.** Started 2026-08-03. See [SPIKE-RESULTS.md](SPIKE-RESULTS.md) for what
> has actually been verified so far, including what failed.

---

## The idea

In law, **standing** is the recognised right to have your claim heard and credited. Not every
claim gets it. You have to earn it.

Every agent-memory system on the market decides what to trust using one of three signals:
**recency** (newest fact wins), **source authority** (trust the runbook over Slack), or
**self-consistency** (ask the model whether it believes itself). All three are the agent grading
its own homework.

Standing uses a fourth signal that none of them use: **did it actually work?**

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
you, and diff that against now.

```
$ standing show <decision-id> --as-of page-time
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
| Diff two instants | Two self-maintained snapshots + app-level diff | One `FULL OUTER JOIN` against the same table |
| Check-then-commit under concurrency | SERIALIZABLE exists but is opt-in and often left off | SERIALIZABLE is the *only* isolation level |
| Per-tenant ANN that scales | One shared HNSW index; cost grows with everyone's data | C-SPANN prefix-partitioned on `tenant_id` |

## Status

| Piece | State |
|---|---|
| Repo, license, schema, spike harness | ✅ 2026-08-03 |
| ccloud provisioning (`infra/provision.sh`) | ✅ written against a verified command surface |
| Live cluster + Day-1 spikes | ⏳ blocked on account creation |
| `grant_standing()` + external verifier webhook | ⬜ |
| Admission control, cross-examination | ⬜ |
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
  non-interactively with `-o json`; `ccloud audit list` gives the org-level audit trail.
- **Agent Skills** — an authored skill documenting the outcome-gated memory pattern.

## AWS services used

- **Amazon Bedrock** — Claude (Converse API) for reasoning and admission control; Titan Text
  Embeddings V2 (512-dim) for embeddings. Deliberately *absent* from the promotion path.
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
- **Standing is granted to memories a decision *produced*,** not memories it merely *consulted*.
  Re-examining consulted memories after a failed decision is a roadmap item, not a shipped one.
- **A confirmed outcome is evidence, not proof.** An incident can resolve for reasons unrelated
  to the action taken. Standing means "survived contact with reality once", not "true".
- **Time travel is bounded by the cluster's garbage-collection window.** The measured bound is
  recorded in [SPIKE-RESULTS.md](SPIKE-RESULTS.md).
- **Multi-region survival is an architectural property here, not a demonstrated one** — unless
  spike 8 confirms `ccloud cluster disruption` works on this tier.

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
