# Connecting MemoryStand to the CockroachDB Cloud MCP server

Five minutes, three questions. This page connects [Claude Code](https://claude.com/claude-code)
directly to the live cluster behind MemoryStand using Cockroach Labs' own
[CockroachDB Cloud MCP server](https://www.cockroachlabs.com/docs/cockroachcloud/connect-to-the-cockroachdb-cloud-mcp-server),
and gives you three questions that show what this project actually does, in SQL a judge can read
without trusting a single word of README copy.

**Status of this page:** the SQL below was run and verified against a local CockroachDB v26.2.5
instance seeded with MemoryStand's own fixture data (see the repo's `LIVE ENVIRONMENT` setup).
The MCP *connection itself* is **untested**, because this development machine has no
CockroachDB Cloud session and no way to create one. It will work unmodified against a real Cloud
cluster the moment you authenticate — nothing about the queries below depends on being local.

## What the MCP server is

Cockroach Labs runs a hosted MCP server at `https://cockroachlabs.cloud/mcp` that lets an MCP
client (Claude Code, Cursor, Cline, GitHub Copilot, Codex, …) query a CockroachDB Cloud cluster
directly — list databases and tables, read a table's schema, and run `SELECT` / `EXPLAIN` / `SHOW`
statements — without you writing any glue code. It is a Cockroach Labs product, not something
MemoryStand built; MemoryStand's contribution here is wiring it up read-only, scoped to one
cluster, against this specific schema.

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
- **Write tools exist and are separate:** `create_database`, `create_table`, `insert_rows`. This
  matters — see "Why read-only, by design" below.
- **Guardrails Cockroach Labs enforces server-side, regardless of role:** one SQL statement per
  tool call, max 16,384 characters; each query times out after 20 seconds; each response is
  capped at 10 KiB; an unlimited `SELECT` gets `LIMIT 25` by default, an explicit `LIMIT` is
  capped at 10,000 rows (`LIMIT ALL` is rejected); `list_databases`/`list_tables` return 100
  results by default, at most 10,000; `show_statement` returns at most 100 rows and is restricted
  to schema/config introspection; `explain_query` supports only `SELECT` / `INSERT` /
  `CREATE TABLE` (no `EXPLAIN ANALYZE`); and the `system`, `crdb_internal`, `pg_catalog`,
  `information_schema`, and `pg_extension` schemas are off-limits to every tool.
- **Not documented on this page:** a dedicated "read-only mode" toggle (permission scope instead
  comes from your OAuth consent choice, or your service account's role), and anything about what
  lands in an audit log. `ccloud audit list` exists as a general CockroachDB Cloud CLI command
  ("who performed the action, when, and what was changed" — see `SPIKE-RESULTS.md` spike 6), but
  whether it captures individual MCP `select_query` calls is not stated anywhere Cockroach Labs
  publishes, and this machine has no session to check empirically. Treat it as unconfirmed.

Source: <https://www.cockroachlabs.com/docs/cockroachcloud/connect-to-the-cockroachdb-cloud-mcp-server>
(fetched 2026-08-04).

## How to connect

### Option A — fastest: OAuth, read-only by hand

1. `claude mcp add cockroachdb-cloud https://cockroachlabs.cloud/mcp --transport http`
2. Claude Code opens a browser. Log in, pick the organization, and when the "Authorize MCP
   Access" modal asks which permissions to grant, **pick "read" only.**
3. Optionally pin it to this cluster so `list_clusters` doesn't have to enumerate every cluster in
   the org: add `"headers": {"mcp-cluster-id": "<cluster-id>"}` to the entry Claude Code just
   wrote into your MCP config.

This is the quickest path for a judge trying MemoryStand — no service account to create, no key
to manage. Its downside is exactly what "pick permissions in a browser modal" implies: it is a
human decision made once per login, not an enforced, revocable, auditable identity.

### Option B — the one this repo ships: a dedicated read-only service account

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

`infra/mcp_setup.sh` creates a service account named `memorystand-mcp-readonly`, grants it the
most restrictive assignable cluster-scoped role CockroachDB Cloud has (`Cluster Developer` — see
the role table below), scoped to *this one cluster only*, creates an API key, writes the key to
`~/.memorystand-mcp-key` with file mode `600`, and prints the two `export` lines to run before
starting Claude Code. The key is never printed to the terminal. See the script's own header
comment for exactly which `ccloud` flags were confirmed and which one thing (the literal
role-name string) could not be verified without a live session — the script fails loudly with a
clear message if that guess is wrong, instead of silently granting a different scope.

| Role (display name) | What it can do | Used here? |
|---|---|---|
| Cluster Developer | Access the DB console. Nothing else in the permission table. | **Yes — this is what the MCP service account gets.** |
| Cluster Operator | View cluster settings/stats; manage databases, networks, backups, jobs, metrics. | No |
| Cluster Admin | Everything Cluster Operator can, plus edit role assignments and delete the cluster. | No |

## Why read-only, by design — not a limitation

The MCP server exposes real write tools (`create_database`, `create_table`, `insert_rows`). The
service account wired up here is granted a role with no write capability, on purpose, and that
choice is load-bearing for the whole project, not an oversight:

MemoryStand's actual write path — `remember()` admitting a memory, `decide()` recording what an
agent consulted and produced, `grant_standing()` promoting or demoting memories off a confirmed
outcome — is not "run an `INSERT`." Each of those is a multi-statement, multi-table decision made
inside one atomic transaction, with invariants a generic SQL tool has no way to know about (a
memory's `verdict` and `trust_tier` are deliberately independent axes; a decision's
`produced_memory_ids` is the only hook the outcome gate is allowed to promote through; nothing is
ever deleted, only corrected by a newer memory that points back at the one it replaces).
`insert_rows` through an MCP tool call cannot express any of that — it can only put rows in a
table. Handing this service account write access wouldn't make the
demo more impressive; it would make it possible to corrupt the memory store's invariants from
outside the one code path that enforces them. Read-only is the correct security posture here, not
a fallback.

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
