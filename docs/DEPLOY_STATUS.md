# Deployment status

Last updated 2026-08-04. Written to be accurate rather than encouraging — a project whose
argument is "check claims before trusting them" cannot have an aspirational status page.

## Working, verified against real AWS

| Component | Evidence |
|---|---|
| CockroachDB Cloud cluster `memorystand` | BASIC, AWS, us-west-2, CockroachDB CCL v26.2.1 (cluster id `3b37f0d1-33ca-4d3f-a7b5-29bb74dcc641`). `agent_memories` now holds 50,131 rows: 40 synthetic tenants at exactly 1,250 rows each (`scripts/loadtest.py --rows 50000 --tenants 40`), plus a curated 131-row demo tenant `9c8f6e5a-9d1a-4a1c-8f2e-3b6d1c7a4e10` (117 with `verdict='accepted'`) |
| Vector index on Cloud Basic | `vector search` + `prefix spans` on the real `recall()` query, confirmed by `EXPLAIN` on both a 1,250-row synthetic tenant and the 117-row demo tenant. The index engaged at 117 rows — the C-SPANN index here is prefix-scoped per tenant, so per-tenant row count does not gate index selection. An earlier hypothesis this session, that 131 rows was too few and the planner would fall back to a scan, was wrong; the 50k seed supplied realistic multi-tenant volume, not a working index — say that plainly rather than implying the seed is what made the index work |
| Circuit breakers (`backend/breaker.py`) | shared by both Bedrock clients — 2 consecutive failures opens for 60 s, then one half-open probe. State exposed live at `GET /health` |
| SSM SecureStrings | `/memorystand/{dsn,shared_secret,kill_switch}`, values never printed |
| IAM role + least-privilege policy | `memorystand-lambda-role` |
| CloudWatch log group | 14-day retention |
| **Lambda `memorystand`** | `State: Active`, python3.13, 512 MB / 30 s |
| **Lambda → CockroachDB Cloud** | direct invoke returns `200`, `database: reachable`, `gc_window_seconds: 4500` |
| Graceful degradation | exercised in production: before the TLS fix the handler returned `200` with `database: unreachable` and the underlying error, rather than crashing |

Two things measured alongside the seed that are worth recording precisely rather than rounding
away:

- **`agent_memories_noindex` is not a usable control.** It was meant to reach 50,000 rows as an
  apples-to-apples un-indexed comparison; the seeding run died mid-way at 36,300 rows
  (`psycopg2.OperationalError: could not receive data from server: No route to host` — a dropped
  laptop network, not a database fault). At 36,300 rows against a 50,131-row indexed table it is
  not a valid control, and no latency comparison may be drawn from it.
- **Write throughput is a network number as much as a database number.** The 50,000-row synthetic
  seed took 730.58 s (68.4 rows/sec), written from a laptop in Hong Kong to the cluster in
  Oregon. That measures the trans-Pacific link at least as much as CockroachDB.

## Resolved: the Function URL 403

**Cause: a missing permission, not an account restriction.** My hypothesis -- that a day-old
account with a 10-execution Lambda ceiling and zero Bedrock quota was also blocked from public
Function URLs -- was **wrong**, and the console said so plainly:

> Your function URL auth type is NONE, but is missing permissions required for public access...
> create a resource-based policy that grants **lambda:invokeFunction and lambda:invokeFunctionUrl**
> permissions to all principals (*)

AWS requires **both** actions. Every instinct says `lambda:InvokeFunctionUrl` is *the*
Function-URL permission, so granting only that looks complete -- and fails closed with a 403
naming neither the missing action nor the fix.

One wrinkle: `--function-url-auth-type NONE` is only accepted alongside `InvokeFunctionUrl`, so
the second grant must omit it. Both statements are required:

    aws lambda add-permission --function-name memorystand \
      --statement-id publicInvokeFunctionUrl --action lambda:InvokeFunctionUrl \
      --principal '*' --function-url-auth-type NONE

    aws lambda add-permission --function-name memorystand \
      --statement-id publicInvokeFunction --action lambda:InvokeFunction --principal '*'

## Then: 30-second timeouts on the embedding routes

With auth fixed, `/health` returned 200 but `/recall` and `/decide` returned Internal Server
Error. CloudWatch showed `Status: timeout` at exactly 30,000 ms.

Bedrock quota is zero, and `embed()` retried five times with exponential backoff -- which can
outlast the entire Lambda timeout. A throttled dependency could consume the whole request
budget. Embedding is now time-boxed (`MEMORYSTAND_EMBED_DEADLINE_S`, default 6s): past the
deadline it stops retrying and degrades to the deterministic stub, announcing that it did.

That is a better system regardless of quota. Bounded latency under dependency failure is the
point of the Production Readiness criterion, and it was found by deploying rather than by
reasoning about it.

## Then: `/decide` 502s for the same reason `/recall` did, one layer deeper

With `/recall` fixed, `POST /decide` still returned **502** after hanging the full 30 s Lambda
timeout. `bedrock_client.converse` had the same shape of bug already fixed in `embeddings.py`,
but it took longer to find: an **attempt** budget (`MAX_ATTEMPTS=5`, backoff to 8 s) with no
**time** budget. Five attempts at up to 8 s of backoff each can, on their own, outlast a 30 s
Lambda.

The first fix -- `DEADLINE_S` (default 8 s, `MEMORYSTAND_MODEL_DEADLINE_S`), checked between
attempts and clamping the backoff sleep -- was **not enough**. Measured, it still took 18.1 s.
botocore was retrying *inside* a single `converse()` call, invisibly to the attempt loop wrapped
around it, so an 8 s deadline checked only between attempts never got to fire. Second fix:
disable botocore's own retries (`retries={"max_attempts": 1}`), plus `connect_timeout=3`,
`read_timeout=5`. Measured: 9.7 s.

Third fix, `backend/breaker.py`: a circuit breaker shared by both Bedrock clients -- 2
consecutive failures opens it for 60 s, then allows exactly one half-open probe. Four
consecutive live `POST /decide` calls against one warm container, all `HTTP 201`:

    23.776 s -> 9.162 s -> 0.851 s -> 0.814 s

The first call pays the full discovery cost against a still-throttled Bedrock; the breaker opens
after the second failure; the third and fourth return in under a second because the breaker
short-circuits straight to the deterministic fallback instead of dialing Bedrock at all. The
local equivalent for `agent.propose()` shows the same shape: 12.35 s -> 8.66 s -> 0.00 s -> 0.00 s.

Test suite: 55 passing (was 47); new file `tests/test_latency_budget.py`.

## Honest state: the deployed agent is not reasoning with a model

`GET /health`'s `circuit_breakers` field spends a lot of its time `"open"` right now, for a
mundane reason: Bedrock quota on this account is still effectively zero. Every live
`POST /decide` currently returns `reasoning_source: "fallback_heuristic"` and `model_calls: 0` --
the action is chosen by an explicit deterministic keyword rule in `backend/agent.py`, not by a
model, and the `rationale` string in the response says so verbatim. Do not describe the deployed
agent as "reasoning with an LLM"; it currently is not. That is a different, incidental zero from
the `confirm_outcome` / trust-promotion path's zero model calls (`backend/trust.py`), which is a
deliberate design property and is genuine.

## Explored: the CockroachDB Cloud MCP server

Handshake against `https://cockroachlabs.cloud/mcp` succeeds: `serverInfo` name
`cockroachdb-cloud`, version `1.0.0`, offering 12 tools (`create_database`, `create_table`,
`explain_query`, `get_cluster`, `get_table_schema`, `insert_rows`, `list_clusters`,
`list_databases`, `list_tables`, `select_query`, `show_running_queries`, `show_statement`).

The service account `memorystand-mcp-readonly`
(`a7f75cdd-04fb-4e66-889b-a1216e15f57d`) currently holds `CLUSTER_DEVELOPER` on the cluster plus
`ORG_MEMBER` on the org -- the least-privilege pair intended for read-only use. With that role,
`select_query` returns `"executing select query: unauthorized"`. Cockroach Labs' own docs
require the Cluster Admin or Cluster Operator role for this server. There is no role that is
both read-only and authorized: `docs/MCP.md`'s "read-only by design" claim is not merely
untested, it is unachievable as written against the managed server.

Two argument-naming traps cost real debugging time: `select_query` / `explain_query` take
`query`, not `statement`; `create_table` takes a raw `ddl` string. Either the wrong tool or the
wrong argument name comes back as the same misleading error, `"must contain exactly one
statement"`.

`scripts/verify_mcp.py` now automates the handshake, tool listing, and permission check above.
Its write probe is opt-in behind `--probe-writes` and has not been run.

## Live and verified end to end

**Demo URL: <https://ojao6oaxlk26mqfjwpuy7g4dy40tglyi.lambda-url.us-west-2.on.aws>**

| Route | Result |
|---|---|
| `GET /health` | 200 -- `database: reachable`, `gc_window_seconds: 4500`, plus `kill_switch`, `embedding_provenance`, `server_version`, and now **`circuit_breakers`** (e.g. `{"bedrock-converse":"open","bedrock-embed":"open"}`) |
| `GET /recall` | 200 -- real memories from CockroachDB Cloud, ranked with distances |
| `POST /ingest` without the secret | **401**, with CORS headers on the error |
| `POST /decide` with the secret | 201 -- required body: `tenant_id`, `agent_id`, `query` (not `situation`). Returns `decision_id`, `decided_at`, `action`, `status`, `consulted[]` (with distance per memory), `produced[]`, **`rationale`**, `reasoning_source`, **`model_calls`** |
| `POST /confirm_outcome` | `outcome: success`, **`model_calls: 0`** |
| `GET /timemachine` | memories reconstructed at decision time -- last counted at 99 against the earlier 101-row seed; not re-counted this session against the current 50,131-row cluster, so that figure is stale and should not be repeated as current |

The central claim now holds **in production**: a memory's trust is granted by an external
outcome, on a path that makes zero model calls -- with the caveat above that the *decision*
itself is currently made by a deterministic fallback, not a model, because Bedrock quota is
still zero.

## Not yet run

`deploy_frontend.sh` (Amplify). Not blocked -- sequenced after the API, and the API was where
the blocker was.

`mcp_setup.sh` has effectively been superseded by direct exploration (see above): the read-only
service account exists and the MCP handshake works, but no role short of Cluster Admin/Cluster
Operator makes `select_query` succeed. Read-only MCP access is unresolved rather than merely
unattempted -- running `mcp_setup.sh` as originally written would not fix this.
