# Connecting MemoryStand to the CockroachDB Cloud MCP server

Five minutes, three questions. This page connects [Claude Code](https://claude.com/claude-code)
directly to the live cluster behind MemoryStand using Cockroach Labs' own
[CockroachDB Cloud MCP server](https://www.cockroachlabs.com/docs/cockroachcloud/connect-to-the-cockroachdb-cloud-mcp-server),
and gives you three questions that show what this project actually does, in SQL a judge can read
without trusting a single word of README copy.

**Status of this page:** the MCP connection itself is now tested and working, live, against the
real CockroachDB Cloud cluster — not simulated, and no longer the untested caveat this page used
to carry. Verified this session, through the MCP server itself: the `initialize` handshake
returns `serverInfo {name: "cockroachdb-cloud", version: "1.0.0"}`; `select_query` against
`agent_memories` returns real rows (`{"rows":[{"trust_tier":"unconfirmed","memories":118},
{"trust_tier":"verified","memories":5}]}`); and `explain_query` against the recall query returns
a plan with `vector search: True`. `scripts/verify_mcp.py` automates the handshake-plus-reads
half of this check — run it yourself to reproduce these numbers (see "How to connect" below).
What is *not* tested is whether this identity's write access is actually enforced or refused —
see "Why this identity is write-capable" below.

The SQL in "Three questions to ask it" further down this page was run and verified separately,
against a local CockroachDB v26.2.5 instance seeded with MemoryStand's own fixture data (see the
repo's `LIVE ENVIRONMENT` setup) — the same SQL, unmodified, against the real Cloud cluster
instead.

## What the MCP server is

Cockroach Labs runs a hosted MCP server at `https://cockroachlabs.cloud/mcp` that lets an MCP
client (Claude Code, Cursor, Cline, GitHub Copilot, Codex, …) query a CockroachDB Cloud cluster
directly — list databases and tables, read a table's schema, and run `SELECT` / `EXPLAIN` / `SHOW`
statements — without you writing any glue code. It is a Cockroach Labs product, not something
MemoryStand built; MemoryStand's contribution here is scoping it to one cluster, against this
specific schema — not making it read-only, which turned out not to be achievable against this
server (see "Why this identity is write-capable" below).

**Verified facts about the server** (from Cockroach Labs' own docs, quoted, 2026-08-04):

- **Endpoint / transport:** `https://cockroachlabs.cloud/mcp`, over HTTP (Claude Code and GitHub
  Copilot config it as `"type": "http"`; Cline uses `"type": "streamableHttp"`). No stdio, no SSE.
- **Auth, two ways**, both via HTTP headers:
  - **OAuth** — interactive browser login; you pick "read" and/or "write" permissions in an
    "Authorize MCP Access" modal. No header needed beyond the optional cluster pin.
  - **API key (service account)** — header `"Authorization": "Bearer {your-service-account-api-key}"`.
    "Access permissions are determined by the role(s) associated with this service account."
  - Either way, an optional header `"mcp-cluster-id": "{your-cluster-id}"` scopes every call to
    one cluster instead of every cluster the account can see.
- **Read tools:** `list_clusters`, `get_cluster`, `list_databases`, `list_tables`,
  `get_table_schema`, `select_query` (runs a `SELECT`), `explain_query` (runs an `EXPLAIN`),
  `show_statement` (runs a `SHOW`), `show_running_queries`.
- **Write tools exist and are offered to every identity, regardless of role:** `create_database`,
  `create_table`, `insert_rows`. Tool availability is not permission — see "Why this identity is
  write-capable" below.
- **Tool argument names are load-bearing, and get this wrong silently:** `select_query` and
  `explain_query` both take a `query` argument, not `statement`; `create_table` takes raw `ddl`.
  Passing the wrong argument name doesn't fail with a helpful "unknown argument" — it comes back
  as `must contain exactly one statement`, which reads like a SQL problem and isn't one.
- **Guardrails Cockroach Labs enforces server-side, regardless of role:** one SQL statement per
  tool call, max 16,384 characters; each query times out after 20 seconds; each response is
  capped at 10 KiB; an unlimited `SELECT` gets `LIMIT 25` by default, an explicit `LIMIT` is
  capped at 10,000 rows (`LIMIT ALL` is rejected); `list_databases`/`list_tables` return 100
  results by default, at most 10,000; `show_statement` returns at most 100 rows and is restricted
  to schema/config introspection; `explain_query` supports only `SELECT` / `INSERT` /
  `CREATE TABLE` (no `EXPLAIN ANALYZE`); and the `system`, `crdb_internal`, `pg_catalog`,
  `information_schema`, and `pg_extension` schemas are off-limits to every tool.
- **There is no dedicated "read-only mode" toggle, and no read-only role that works.** Permission
  scope comes from your OAuth consent choice or your service account's role — but Cockroach Labs'
  own docs state the MCP server requires **the Cluster Admin role or the Cluster Operator role**.
  Verified live: a service account holding only `CLUSTER_DEVELOPER` (the most restrictive
  assignable cluster-scoped role) got `select_query: executing select query: unauthorized`. There
  is no cluster-scoped role below Cluster Operator that this server accepts. See "Why this
  identity is write-capable" below.
- **Not documented on this page:** anything about what lands in an audit log. `ccloud audit list`
  exists as a general CockroachDB Cloud CLI command ("who performed the action, when, and what was
  changed" — see `SPIKE-RESULTS.md` spike 6), but whether it captures individual MCP
  `select_query` calls is not stated anywhere Cockroach Labs publishes, and this page has not
  checked it empirically. Treat it as unconfirmed.

Source: <https://www.cockroachlabs.com/docs/cockroachcloud/connect-to-the-cockroachdb-cloud-mcp-server>
(fetched 2026-08-04).

## How to connect

### Option A — fastest: OAuth, by hand

1. `claude mcp add cockroachdb-cloud https://cockroachlabs.cloud/mcp --transport http`
2. Claude Code opens a browser. Log in, pick the organization, and when the "Authorize MCP
   Access" modal asks which permissions to grant, **pick "read" only.**
3. Optionally pin it to this cluster so `list_clusters` doesn't have to enumerate every cluster in
   the org: add `"headers": {"mcp-cluster-id": "<cluster-id>"}` to the entry Claude Code just
   wrote into your MCP config.

This is the quickest path for a judge trying MemoryStand — no service account to create, no key
to manage. Its downside is exactly what "pick permissions in a browser modal" implies: it is a
human decision made once per login, not an enforced, revocable, auditable identity. And picking
"read" in that modal is a consent choice, not a role grant — it was not tested through this page
whether OAuth enforces anything beyond the account's own underlying role, and Cockroach Labs'
documented requirement (Cluster Admin or Cluster Operator) reads as a property of the server, not
of the auth method. Don't assume "read" here gets you something Option B's service account
couldn't get with `CLUSTER_DEVELOPER`.

### Option B — the one this repo ships: a dedicated service account

This repo's [`.mcp.json`](../.mcp.json) is already wired for this option:

```json
{
  "mcpServers": {
    "cockroachdb-cloud": {
      "type": "http",
      "url": "https://cockroachlabs.cloud/mcp",
      "headers": {
        "mcp-cluster-id": "${MEMORYSTAND_MCP_CLUSTER_ID}",
        "Authorization": "Bearer ${MEMORYSTAND_MCP_API_KEY}"
      }
    }
  }
}
```

To fill in those two environment variables:

```
ccloud auth login
./infra/mcp_setup.sh
```

`infra/mcp_setup.sh` creates a service account named `memorystand-mcp-readonly` (the name is a
holdover from when read-only looked achievable — see the role table below for why it no longer
is), grants it by default the most restrictive assignable cluster-scoped role CockroachDB Cloud
has (`Cluster Developer`), scoped to *this one cluster only*, creates an API key, writes the key
to `~/.memorystand-mcp-key` with file mode `600`, and prints the two `export` lines to run before
starting Claude Code. The key is never printed to the terminal. See the script's own header
comment for exactly which `ccloud` flags were confirmed.

**`Cluster Developer` alone is not enough — the MCP server rejects it.** Verified live:
`select_query` against a `Cluster Developer`-only identity returns
`executing select query: unauthorized`. The account this repo actually uses has since also been
granted `Cluster Operator` (role name `CLUSTER_OPERATOR_WRITER`) on top of `Cluster Developer`,
and with both roles `select_query`/`explain_query` work. `infra/mcp_setup.sh` does not grant that
second role for you — it only ever requests `MEMORYSTAND_MCP_ROLE` (default `CLUSTER_DEVELOPER`)
— so if you run it fresh, expect the same "unauthorized" error until you separately grant
`Cluster Operator`, e.g. `ccloud role add <service-account-id> CLUSTER_OPERATOR_WRITER CLUSTER
<cluster-id>` or the equivalent in the console. Once both roles are in place, run
`scripts/verify_mcp.py` to confirm the connection actually works end to end (see its usage in the
module docstring).

| Role (display name) | What it can do | Used here? |
|---|---|---|
| Cluster Developer | Access the DB console. Nothing else in the permission table. | Yes — the default `infra/mcp_setup.sh` grant, but not sufficient by itself. |
| Cluster Operator | View cluster settings/stats; manage databases, networks, backups, jobs, metrics. | **Yes — also granted, and required.** Cockroach Labs' MCP docs state the server requires this role or Cluster Admin; nothing weaker works. This role is write-capable. |
| Cluster Admin | Everything Cluster Operator can, plus edit role assignments and delete the cluster. | No |

There is no row in this table that is both accepted by the MCP server and read-only. That is a
property of the server, confirmed live against this cluster, not a choice this repo made.

## Why this identity is write-capable — and what that does and doesn't mean

This page used to describe the service account as read-only by design. That turned out not to be
achievable: Cockroach Labs' own docs require the Cluster Admin role or the Cluster Operator role
for the MCP server, and `Cluster Developer` — the role this account started with — is
unauthorized for `select_query`. So the account also holds `Cluster Operator`
(`CLUSTER_OPERATOR_WRITER`) now, and by CockroachDB Cloud's own role definition that is
write-capable, not read-only. Say so plainly rather than keep the old "read-only" framing.

What "write-capable" does **not** mean here: it does not mean a write through this connection has
been tried and has succeeded. The MCP server exposes real write tools (`create_database`,
`create_table`, `insert_rows`) to every identity regardless of role — tool availability is not
permission, and this account's role has never been used to actually call one of them.
`scripts/verify_mcp.py` has a write probe for exactly this (`--probe-writes`: attempts
`create_table` against a throwaway table name), and it is opt-in on purpose — a probe that
succeeds leaves a real table on the live cluster with no MCP tool able to drop it again. That
probe has not been run. Whether a write through this MCP connection is actually refused at the SQL
layer is **untested**, not "refused" and not "allowed" — untested.

Given that, the honest reason this matters is not "the role forbids it" — it doesn't, or at least
that's untested — it's that MemoryStand's actual write path was never meant to be reachable this
way in the first place. `remember()` admitting a memory, `decide()` recording what an agent
consulted and produced, `grant_standing()` promoting or demoting memories off a confirmed outcome
— none of that is "run an `INSERT`." Each is a multi-statement, multi-table decision made inside
one atomic transaction, with invariants a generic SQL tool has no way to know about (a memory's
`verdict` and `trust_tier` are deliberately independent axes; a decision's `produced_memory_ids`
is the only hook the outcome gate is allowed to promote through; nothing is ever deleted, only
corrected by a newer memory that points back at the one it replaces). `insert_rows` through an MCP
tool call cannot express any of that — it can only put rows in a table, and if this account's
`Cluster Operator` role does let that call succeed, the row it writes would bypass every one of
those invariants.

That is a real risk now, not a hypothetical one, precisely because the role turned out to require
write capability. The mitigation is operational, not a permission boundary: nothing in
MemoryStand's own code path (`backend/`, `cli/memorystand.py`) ever calls this MCP connection or
this API key — it exists only for a human or an LLM client to run ad hoc `SELECT`/`EXPLAIN`
queries against the cluster, and the write tools should simply never be invoked through it. If you
are auditing this project, treat "never call `create_database` / `create_table` / `insert_rows`
through this connection" as a rule you're relying on a human to follow, not one CockroachDB Cloud
enforces for you.

## Three questions to ask it

Each one is copy-pasteable as a prompt to Claude Code once the MCP server is connected — ask it in
plain English and let the model call `select_query` itself — or you can run the SQL directly with
`select_query`. Both are shown. All three were run against a live local CockroachDB v26.2.5
cluster seeded with MemoryStand's fixture data; the actual rows returned are quoted below each
one so you can see this isn't hypothetical.

### 1. "Which memories has this agent been told, but refused to trust?"

Every memory MemoryStand is ever handed gets written — nothing is silently dropped — but a memory
that contradicts something already believed is held for review rather than made recallable. This
is the admission-control half of the design (write-time contradiction checking; see the README's
"Prior art" section for what is and isn't new about that half).

```sql
SELECT memory_id::STRING, entity, attribute_key, attribute_value, verdict_reasons
FROM agent_memories
WHERE tenant_id = '9c8f6e5a-9d1a-4a1c-8f2e-3b6d1c7a4e10'   -- the seeded demo tenant
  AND verdict = 'quarantined'                              -- "held for review", in the schema's words
LIMIT 10;
```

Verified output (local cluster, 2026-08-04):

```
memory_id       entity            attribute_key            attribute_value          verdict_reasons
0c68006a-...    payments-service  failover_restart_order    payments-service-first   contradicts memory 87e9cc96-... which asserts 'ledger-worker-first'; sources rank equal, so a human decides
3eb12c08-...    payments-service  primary_datastore_table   payments_v2              contradicts memory 51be0794-... which asserts 'payments'; sources rank equal, so a human decides
```

What it proves: MemoryStand doesn't just accumulate whatever an agent tells it. Two runbooks
disagreed about which table `payments-service` reads from and which service restarts first during
a failover — MemoryStand caught both contradictions at write time and held the newer claim for a
human, instead of quietly overwriting or averaging them.

### 2. "What did the agent believe about payments-service when it paged me?"

CockroachDB's native MVCC history means "what did the recall query return five minutes ago" is a
different `AS OF SYSTEM TIME`, not a different table.

```sql
SET TRANSACTION AS OF SYSTEM TIME '-5m';
SELECT memory_id::STRING, entity, attribute_key, attribute_value, verdict, created_at
FROM agent_memories
WHERE tenant_id = '9c8f6e5a-9d1a-4a1c-8f2e-3b6d1c7a4e10'
  AND entity = 'payments-service'
  AND verdict = 'accepted'
ORDER BY created_at DESC
LIMIT 10;
```

(`AS OF SYSTEM TIME` must be set as the first statement of the transaction — CockroachDB rejects
it inside a subquery or CTE, error `42601`. Replace `-5m` with the actual decision timestamp, or
an interval, when asking about a specific page.)

Verified output (local cluster, 2026-08-04) — 10 of the rows a real recall would have returned,
including a live incident investigation and its on-call escalation path:

```
memory_id      entity            attribute_key                       attribute_value                    verdict    created_at
855b1dfb-...   payments-service  reads_from_table                    orders_v2                          accepted   2026-08-04 00:36:44
08dbfc1a-...   payments-service  incident_INC-4610_next_step         await_marcus_confirmation          accepted   2026-08-04 00:36:20
6fa3652a-...   payments-service  incident_migration_cutover          payments_v2_cutover_2026-06-14     accepted   2026-08-04 00:36:20
330d009b-...   payments-service  incident_INC-4610_hypothesis        long_running_analyze_on_aurora     accepted   2026-08-04 00:36:20
61cf429c-...   payments-service  incident_INC-4610_paging            current_incident_declared          accepted   2026-08-04 00:36:20
8be23ced-...   payments-service  on_call_secondary_contact           human:marcus                        accepted   2026-08-04 00:36:20
```

What it proves: the same recall query MemoryStand's agent actually ran is replayable against
exactly what it knew at that moment, without a separate history table, a CDC pipeline, or hand-rolled
`valid_from`/`valid_to` bookkeeping — CockroachDB's MVCC storage is the audit trail.

### 3. "Show me every memory that earned its trust from a real outcome, and the incident that confirmed it."

This is the thesis of the whole project: `trust_tier = 'verified'` is set exactly once, by
`grant_standing()`, and only when a decision's real-world `outcome` is confirmed — never by a
model deciding it likes its own answer.

```sql
SELECT m.memory_id::STRING, m.entity, m.attribute_key, m.attribute_value,
       d.decision_id::STRING, d.action, d.outcome, d.outcome_confirmed_at
FROM agent_memories m
JOIN agent_decisions d ON m.memory_id = ANY(d.produced_memory_ids)
WHERE m.tenant_id = '<tenant-id>'
  AND m.trust_tier = 'verified'
ORDER BY d.outcome_confirmed_at DESC
LIMIT 10;
```

Verified output (local cluster, 2026-08-04, run against a small hand-seeded tenant created
specifically to exercise this path end to end — `remember()` → `decide()` → `confirm()` — because
the large shared demo tenant's live outcome-confirmation state was mid-flight while this page was
written):

```
memory_id      entity            attribute_key    attribute_value  trust_tier  decision_id    action        outcome  outcome_confirmed_at
d720cacf-...   payments-service  p99_latency_ms   900              verified    88b8c844-...   page_oncall   success  2026-08-04 00:39:21
```

What it proves: `d720cacf-...` didn't earn `verified` because a model re-graded its own claim. It
earned it because decision `88b8c844-...` (`page_oncall`) was later confirmed `success` against a
real PagerDuty reference (`INC-9001`) — `grant_standing()`'s promotion path makes zero model
calls, and this row is the receipt.

## A note on tenant IDs

The queries above hardcode `9c8f6e5a-9d1a-4a1c-8f2e-3b6d1c7a4e10`, the fixed demo tenant this
repo's own seed script (`db/seed/seed.py`) always uses (see its `DEFAULT_TENANT_ID`). If you seed
a different tenant, or ask the agent through `cli/memorystand.py --tenant-id <yours>`, substitute
that UUID instead — or ask the MCP client "what tenant IDs exist in `agent_memories`" first and
let it fill the rest in.
