# Day-1 Spike Results

Kept in the repo for transparency: these are the go/no-go findings that shaped the design,
including the ones that failed. Every spike has a pre-declared fallback so no downstream day
is blocked on an unknown.

**Legend:** ✅ passed · ❌ failed (fallback engaged) · ⏳ blocked on an account/credential · ⬜ not yet run

| # | Spike | Status | Finding |
|---|---|---|---|
| 1 | Vector index available on Cloud Basic? | ⏳ | Needs a live cluster |
| 2 | GC retention window / `AS OF SYSTEM TIME` reach | ⏳ | Needs a live cluster |
| 3 | Bedrock model access latency (Claude + Titan) | ⏳ | Needs an AWS account |
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
