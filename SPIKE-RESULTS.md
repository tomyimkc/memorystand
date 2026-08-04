# Day-1 Spike Results

Kept in the repo for transparency: these are the go/no-go findings that shaped the design,
including the ones that failed. Every spike has a pre-declared fallback so no downstream day
is blocked on an unknown.

**Legend:** ✅ passed · ❌ failed (fallback engaged) · ⏳ blocked on an account/credential · ⬜ not yet run

| # | Spike | Status | Finding |
|---|---|---|---|
| 1 | Vector index available + used | ✅ | Works — **but only with `verdict` in the index prefix.** See below |
| 2 | GC window / `AS OF SYSTEM TIME` reach | ✅ | `gc.ttlseconds = 14400` (4h) default; AOST verified working |
| 3 | Bedrock model access (Nova + Titan) | ⏳ | Needs an AWS account; deterministic stub covers local runs. Originally targeted Claude — moved to Nova after finding Anthropic models geo-restricted; see `docs/BEDROCK_QUOTA.md` |
| 4 | Lambda → CockroachDB pooling | ✅ | `maxconn=1` verified; found and fixed two leaks it exposed |
| 5 | Basic-tier idle suspension | ⏳ | Needs a Cloud cluster |
| 6 | ccloud CLI command surface | ✅ | Confirmed from `--help` — see below |
| 7 | Titan throughput quota | ⏳ | Needs an AWS account |
| 8 | `ccloud cluster disruption` resilience demo | ⏳ | Command exists; tier support unproven |

All ✅ rows were verified against a real **CockroachDB v26.2.5** single-node cluster
(`docker run cockroachdb/cockroach:latest start-single-node --insecure`), not against docs.

---|---|---|---|
| 1 | Vector index available on Cloud Basic? | ⏳ | Needs a live cluster |
| 2 | GC retention window / `AS OF SYSTEM TIME` reach | ⏳ | Needs a live cluster |
| 3 | Bedrock model access latency (Nova + Titan) | ⏳ | Needs an AWS account |
| 4 | Lambda → CockroachDB connection pooling | ⬜ | Design locked; smoke test on Day 6 |
| 5 | Basic-tier idle suspension policy | ⏳ | Needs a live cluster |
| 6 | ccloud CLI command surface | ✅ | **Confirmed locally — see below** |
| 7 | Titan embedding throughput quota | ⏳ | Needs an AWS account |
| 8 | `ccloud cluster disruption` — usable resilience demo? | ⏳ | **Command exists — see below** |

---

## Spike 6 ✅ — ccloud CLI command surface (resolved 2026-08-03, no account needed)

`ccloud 0.8.23` (CCAPI 2024-09-16), installed via `brew install cockroachdb/tap/ccloud`.

Research had returned **zero confirmed facts** about this CLI, so the plan had demoted it to a
stretch goal. Reading `--help` locally resolves it: the CLI is real, complete, and agent-shaped.
**ccloud is promoted to a full third CockroachDB tool.**

Confirmed, verbatim from `--help`:

- **`-o json` is a *global* flag** on every command (`[standard|json]`), plus `--hide-header`
  and `-q`. The "JSON output on every command" claim in the hackathon brief is accurate.
- **Cluster lifecycle:** `ccloud cluster create [BASIC|STANDARD|ADVANCED] <name> <region...>`
  with `--cloud [GCP|AWS|AZURE]`, `--request-unit-limit`, `--storage-gib-limit`, `--wait`.
  `BASIC` is documented as "On-demand capacity … (formerly serverless)".
- **`--cloud AWS` matters for this hackathon:** the CockroachDB cluster itself can be
  provisioned onto AWS, so "deployed on AWS" describes the memory layer too, not just the
  compute in front of it. **Provision in AWS, not the GCP default.**
- **Service accounts / RBAC:** `ccloud service-account create|list|get|delete` and
  `ccloud service-account api-key …` — the non-interactive path for least-privilege automation.
- **SQL users:** `ccloud cluster user create|list|delete|password`.
- **Connection string:** `ccloud cluster connection-string <name> --sql-user … --database …`.
- **Audit:** `ccloud audit list` — "who performed the action, when, and what was changed."
- Also present: `cluster networking`, `cluster backup`, `cluster restore`, `cluster database`,
  `cluster metric-export`, `cluster log-export`, `folder`, `role`, `organization`, `billing`.

**Impact:** `infra/provision.sh` moves from a Day-9 maybe to a real deliverable, and the
submission can claim three CockroachDB tools (Vector Indexing + Managed MCP Server + ccloud CLI)
plus an authored Agent Skill, against a required minimum of two.

---

## Spike 8 ⏳ — `ccloud cluster disruption` (command confirmed; tier support unknown)

Also unearthed by reading `--help`. Verbatim:

> Simulate cluster disruptions for disaster recovery testing.
> Disruptions allow you to test how your applications behave when parts of your
> CockroachDB Cloud cluster become unavailable.

Surface: `ccloud cluster disruption set <cluster> --region <r> [--whole-region | --azs a,b | --pods p1,p2]`,
plus `get` and `clear`.

**Why this matters.** The plan had explicitly cut any resilience demo, on the grounds that
Cloud Basic exposes no node control. This is a first-party, supported way to make the memory
layer genuinely unavailable and watch the agent respond — which is the sponsor's own stated
thesis ("an agent whose memory goes offline doesn't degrade gracefully, it stops") and lands
directly on judging criterion 4, *"what happens when things go wrong."*

**Not yet proven, and not to be claimed until it is.** The `--azs` / `--pods` vocabulary
suggests this may be ADVANCED/dedicated-only. **Test on the live Basic cluster before writing
a single word about it in the README or the video.** If it is unavailable on Basic, the honest
fallback stands unchanged: state resilience as an architectural property, demonstrate graceful
degradation by pointing the app at an unreachable DSN instead, and label it precisely as that.

---

## Environment notes (2026-08-03)

- `ccloud 0.8.23`, `aws-cli/2.36.14` — both installed.
- Local Python is **3.14.6**, ahead of the newest AWS Lambda runtime (3.13). Build the Lambda
  package against a pinned 3.13 base image (Docker is available) rather than the host
  interpreter, or `psycopg2` binaries will not load in Lambda.


---

## Spike 1 ✅ — vector indexing works, and the index prefix decides whether it is used

The DDL is valid and the ANN search works. The finding that mattered was subtler and cost
two debugging rounds; both halves are now baked into `db/schema.sql` and `backend/memory.py`.

**(a) `verdict` must be in the index prefix.** Recall must filter `verdict = 'accepted'`.
That predicate is not satisfiable from a `(tenant_id, embedding)` prefix, so the optimizer
abandoned the vector index entirely. Measured at 4,000 rows:

| Index prefix | Resulting plan |
|---|---|
| `(tenant_id, embedding)` | `scan agent_memories@..._pkey` — **vector index unused** |
| `(tenant_id, verdict, embedding)` | `vector search` + `prefix spans` — correct |

This is also a design win: the ANN partition *is* "admitted memories of one tenant", so a
held or superseded memory is not merely filtered out of recall — it is not in the searched
partition. The invariant is physical, not just a `WHERE` clause.

**(b) `AND embedding IS NOT NULL` silently disables the index.** A defensive predicate,
added for safety, was enough on its own to force a full scan. It was also redundant: rows
with a NULL embedding are not in the vector index anyway. Removed from every recall path.

**(c) Statistics must be refreshed after a bulk load.** Straight after seeding 10,000 rows
the optimizer still estimated ~1 row per tenant and chose a scan, reporting
`estimated row count: 1 ... stats collected N minutes ago`. `ANALYZE` fixes it, and
`scripts/loadtest.py` now does it before measuring anything.

## Spike 2 ✅ — `AS OF SYSTEM TIME`, and the two forms that do not work

- Default `gc.ttlseconds = 14400`, so history reaches back ~4 hours on this configuration.
- `SELECT ... FROM t AS OF SYSTEM TIME '<ts>' WHERE ... ORDER BY embedding <=> $1 LIMIT k`
  **works** — AOST composes with a vector `ORDER BY` in one top-level statement.
- ❌ **AOST cannot appear in a subquery or CTE**:
  `ERROR: AS OF SYSTEM TIME must be provided on a top-level statement (SQLSTATE 42601)`.
  The planned "diff in one `FULL OUTER JOIN`" does not exist.
- ❌ **`BEGIN AS OF SYSTEM TIME` fails under psycopg2**, which opens a transaction
  implicitly, so an explicit `BEGIN` raises `25001`.
- ✅ **`SET TRANSACTION AS OF SYSTEM TIME '<ts>'`** as the first statement of the implicit
  transaction works, and is the better primitive anyway: it pins a whole multi-statement,
  multi-table read to one instant, giving a consistent snapshot across `agent_memories`
  *and* `agent_decisions` — which is what reconstructing an agent's belief actually needs.

## Spike 4 ✅ — pooling at `maxconn=1` exposed two real bugs

Correct for Lambda, and unforgiving: any code path that borrows the connection and does not
return it starves the whole process. It caught (1) an error handler that called
`gc_window_seconds()` while still holding the connection, deadlocking on
`connection pool exhausted`, and (2) a seeder helper that leaked the only connection and
turned all 101 fixture inserts into errors. Both fixed.

## Other measured findings

- **CockroachDB returns `UUID[]` with an OID psycopg2 does not map**, so an uncast read
  arrives as the literal string `'{uuid,uuid}'` — and iterating it yields *characters*.
  Every `UUID[]` read now casts to `STRING[]` server-side.
- Running `python <script>.py` puts the script's own directory on `sys.path[0]`, not the
  repo root, so `import backend` fails. All entry points now bootstrap the repo root.
