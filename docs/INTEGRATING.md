# Integrating MemoryStand

How another team actually wires this into their own agent, today, as of this writing. Three
surfaces, in the order you should reach for them: the HTTP API (fully works, this is what your
agent should call), the CockroachDB Cloud MCP server (cluster inspection, with a role caveat
you should read before adopting it), and the `memorystand` CLI (a human's tool, for a terminal, not a service
integration point).

## 1. HTTP API — the surface that works

Everything here runs against the live Function URL, no account of yours required to try it:

```
https://ojao6oaxlk26mqfjwpuy7g4dy40tglyi.lambda-url.us-west-2.on.aws
```

The four read routes (`/health`, `/recall`, `/timemachine`, `/diff`) are open. The two
Bedrock-calling write routes (`/ingest`, `/decide`) and `/confirm_outcome` need a shared secret
header, `x-memorystand-secret`, checked with `hmac.compare_digest` — see
[`backend/handler.py`](../backend/handler.py) for the exact gating logic. If you're integrating
this against your own deployment, that secret is whatever you set in
`MEMORYSTAND_SHARED_SECRET` / the `/memorystand/shared_secret` SSM parameter; the examples below
assume you have it in `$MEMORYSTAND_SECRET`.

`/confirm_outcome` is gated too, and that is a deliberate correction: it was once left open on
the reasoning that it "only records an outcome your monitoring system reports, not a
Bedrock-spending call". That reasoning was wrong. It is the route that grants a memory its
standing, so an ungated one let any caller promote any tenant's memories to `verified` — the
exact failure this project exists to prevent. Gate by what a route changes, not by whether it
spends money.

Start by checking the deployment is actually up:

```bash
curl -s https://ojao6oaxlk26mqfjwpuy7g4dy40tglyi.lambda-url.us-west-2.on.aws/health | jq .
```

That returns `kill_switch`, `embedding_provenance`, `circuit_breakers` (a map like
`{"bedrock-converse": "open", "bedrock-embed": "open"}` when Bedrock is unreachable and the
breaker has tripped — see the honest note below), `server_version`, `database`, and
`gc_window_seconds`.

### The round trip: `/decide` then `/confirm_outcome`

This is the whole thesis of the project in two calls: an agent makes a decision grounded in
memory, and later the real world — not the model — decides whether that memory earns trust.

**Step 1 — ask the agent to decide something**, against the seeded demo tenant:

```bash
curl -s -X POST \
  https://ojao6oaxlk26mqfjwpuy7g4dy40tglyi.lambda-url.us-west-2.on.aws/decide \
  -H "content-type: application/json" \
  -H "x-memorystand-secret: $MEMORYSTAND_SECRET" \
  -d '{
        "tenant_id": "9c8f6e5a-9d1a-4a1c-8f2e-3b6d1c7a4e10",
        "agent_id":  "1a2b3c4d-5e6f-4708-9a0b-1c2d3e4f5061",
        "query": "payments-service failover order"
      }' | jq .
```

The required body fields are `tenant_id`, `agent_id`, and `query` — note it's `query`, not
`situation` or `alert`; see `_route_decide` in `backend/handler.py`. `k` (how many memories to
consult, default 5) and `task_id` are optional. The response (HTTP 201) is:

```json
{
  "decision_id": "...",
  "decided_at": "...",
  "action": "page_oncall",
  "status": "taken",
  "consulted": [ { "memory_id": "...", "trust_tier": "...", "distance": 0.031, "...": "..." } ],
  "produced": [],
  "rationale": "...",
  "reasoning_source": "fallback_heuristic",
  "model_calls": 0
}
```

Read `reasoning_source` before you read anything else in that response. In a live 2026-08-09
check it was `"fallback_heuristic"`, not `"bedrock:amazon.nova-lite-v1:0"`: Bedrock quota is
effectively zero and the configured router standby returned HTTP 402 `insufficient_balance`.
The explicit deterministic rule in `backend/agent.py` handled the request and the rationale said
so verbatim. Provider availability can change, so do not hard-code this snapshot either — check
`reasoning_source` and `model_calls` in your own client instead of assuming.

If you already know what action you want recorded — you're driving this from your own agent
loop and MemoryStand is just the memory layer — pass `action` and `rationale` directly in the
body and MemoryStand records them without touching the model or the fallback rule at all;
`reasoning_source` comes back `"caller_supplied"` and `model_calls: 0`.

**Step 2 — later, when the real world reports back, close the loop:**

```bash
curl -s -X POST \
  https://ojao6oaxlk26mqfjwpuy7g4dy40tglyi.lambda-url.us-west-2.on.aws/confirm_outcome \
  -H "content-type: application/json" \
  -H "x-memorystand-secret: $MEMORYSTAND_SECRET" \
  -d '{
        "tenant_id": "9c8f6e5a-9d1a-4a1c-8f2e-3b6d1c7a4e10",
        "decision_id": "<decision_id from step 1>",
        "outcome": "success",
        "source": "pagerduty",
        "external_ref": "INC-4610"
      }' | jq .
```

`tenant_id` (which scopes the lookup — a decision belonging to another tenant is reported as not
existing, so a decision id alone is not enough to promote anything), `decision_id`, `outcome`
(one of `success` / `rollback` / `false_positive`), `source` (one of
`pagerduty` / `metric` / `human` — a model's own opinion is deliberately not an accepted value,
enforced in `backend/trust.py`), and `external_ref` are all required; `metric_delta` is
optional. The response includes `promoted` and `demoted` memory-id lists and, again,
**`model_calls`, which is genuinely, structurally `0`** — `backend/trust.py` imports no model
client at all and asserts that at the top of every call. That zero is the one claim in this
project you don't have to take on trust; go read the module.

If `/decide` didn't produce any new memories (`produced` was empty, as in the example above —
this decision only *consulted* memories, it didn't write any), there is nothing for
`/confirm_outcome` to promote, and `promoted`/`demoted` both come back empty. That's expected,
not a bug: only memories a decision *produced* are eligible for promotion, never ones it merely
read. To see a non-empty `promoted` list, ingest a memory first via `/ingest` (same shared
secret, required fields `tenant_id`, `agent_id`, `content`), pass its `memory_id` back into
`/decide`'s `produced_memory_ids` array, then confirm that decision's outcome.

### What else is on the API surface

- `GET /recall?tenant_id=...&q=...&k=5` — the vector search directly, no decision recorded.
  `agent_id` is optional.
- `GET /timemachine?tenant_id=...&decision_id=...` — what the agent believed at decision time,
  diffed against now (`AS OF SYSTEM TIME` under the hood).
- `GET /diff?tenant_id=...&instant=...` — belief changes since a given instant.
- Calling a secret-gated route without `x-memorystand-secret` returns HTTP 401 with CORS headers
  attached — the response is not a bare unauthenticated stack trace, it's a normal JSON error
  your client can parse.
- A CockroachDB outage comes back as HTTP 503 `{"degraded": "memory_unreachable"}`, not a 500 or
  an empty-but-valid answer. If your integration treats "no memories" and "memory is down" the
  same way, you will misread this API; they are deliberately distinguishable.

## 2. MCP — cluster inspection, and the least-privilege problem you cannot solve

[`docs/MCP.md`](MCP.md) is the full writeup of how to connect Claude Code (or Cursor, Cline,
Copilot — any MCP client) directly to the CockroachDB Cloud cluster behind MemoryStand, with
three copy-pasteable questions and their verified answers. This section is the short version for
someone deciding whether to adopt that surface for their own integration or ops tooling, plus one
thing you need to know that the rest of this project would rather not have to say plainly.

**The role problem, stated up front.** This repo provisions a service account
(`memorystand-mcp-readonly`). It was first granted `Cluster Developer` — CockroachDB Cloud's most
restrictive assignable cluster-scoped role — and under that role, verified live, `select_query`
through the MCP server returns `"executing select query: unauthorized"`. Cockroach Labs' own
documentation says the MCP server's read tools require **the Cluster Admin role or the Cluster
Operator role**, both of which carry real administrative capability (Cluster Operator can manage
databases, networks, backups and jobs; Cluster Admin adds role assignment and cluster deletion).
There is **no role in CockroachDB Cloud that is both assignable to a service account and
sufficient to run `select_query` through the managed MCP server.**

So the connection you see working in this project was bought, deliberately, by granting
`CLUSTER_OPERATOR_WRITER` alongside the developer role. **The identity is write-capable.** It is
named `-readonly` for historical reasons and that name is now misleading; it is kept only so the
account id in older logs still resolves. Read tools are the only ones this project calls, but
that is a matter of what it chooses to invoke, not of what the credential is permitted to do.

What that means for your integration: if you want an MCP identity that can actually run
`select_query` / `explain_query`, you must grant it Cluster Operator or Cluster Admin even though
the only tools you intend to call are read ones. Make that trade-off deliberately. The
least-privilege posture most teams would want here is not currently available.

The alternative is Option A in `docs/MCP.md`: interactive OAuth login, picking "read" permissions
in the browser consent screen. That is a human decision made once per login rather than an
enforced, revocable, auditable service identity — but it is the only path that runs `select_query`
without a standing write-capable credential.

Reproduce all of this yourself with [`scripts/verify_mcp.py`](../scripts/verify_mcp.py), which
performs the handshake, runs the reads, and reports what the server allowed. Its write probe is
opt-in behind `--probe-writes` and has not been run here, so whether the SQL layer would actually
*refuse* a write from this identity is **untested** — the tool list offers `create_table` and
`insert_rows` to every identity, and tool availability is not permission.

Once you have a working identity (OAuth read grant, or a service account holding Cluster
Operator/Admin), the connection itself is standard MCP-over-HTTP with an `Authorization: Bearer
<key>` header and an optional `mcp-cluster-id` header to scope calls to one cluster — see
`docs/MCP.md` for the full config and the exact tool names (`select_query` and `explain_query`
take an argument named `query`, not `statement`; `create_table` takes raw `ddl`; a wrong argument
name returns the misleading error `"must contain exactly one statement"`, which is not what it
sounds like). `scripts/verify_mcp.py` automates the handshake and tool-argument checks this
section relies on; its write-probe path is opt-in behind `--probe-writes` and has not been run
against this cluster.

## 3. CLI — a human's tool, not an integration point

[`cli/memorystand.py`](../cli/memorystand.py) is the terminal front end used to drive demos; it
is not a client library and there is no stability contract on its output format beyond `--json`.
If you're building a service integration, use the HTTP API above. If you're operating,
debugging, or demoing against a real cluster from a terminal, this is the tool.

Install and point it at your cluster:

```bash
pip install -r requirements.txt
export COCKROACH_DSN="postgresql://...">/dev/null  # or pass --dsn on every command
```

Six subcommands, all under `python cli/memorystand.py <subcommand>` (or `standing <subcommand>`
if installed as a script). Every one accepts `--json` for machine-readable output, `--dsn`, and
`--tenant-id`/`--agent-id` (both default to a fixed demo tenant/agent so the commands work with
zero setup against a freshly seeded database):

```bash
# write a memory; prints the admission verdict (accepted, or held for review with reasons)
python cli/memorystand.py remember \
  --entity payments-service --key reads_from_table --value orders_v2 \
  --content "payments-service reads from orders_v2" \
  --source runbook:db-failover

# vector-search accepted memories
python cli/memorystand.py recall --query "payments failover"

# recall, then record what the agent decided to do
python cli/memorystand.py decide \
  --action page_oncall \
  --rationale "elevated p99 latency on payments-service" \
  --query "payments-service latency"

# report a real-world outcome; promotes/demotes memories, 0 model calls
python cli/memorystand.py confirm \
  --decision-id <decision_id> --outcome success --source pagerduty --ref INC-4610

# what the agent believed at decision time, diffed against now
python cli/memorystand.py cross-examine --decision-id <decision_id>

# human-readable tool_audit trail for a decision
python cli/memorystand.py audit --decision-id <decision_id>
```

Two things worth knowing before you rely on the CLI in a script rather than typing it by hand:

- Global flags (`--dsn`, `--json`, `--tenant-id`, `--agent-id`) work either before or after the
  subcommand — `standing --tenant-id X recall ...` and `standing recall --tenant-id X ...` both
  resolve to the same tenant (verified against `_build_parser()` directly). An earlier version of
  this parser had a bug where the pre-subcommand form silently fell back to the default tenant;
  the fix (`argparse.SUPPRESS` defaults, backfilled once in `main()`) is what you're running
  against today. Both `--tenant-id` and `--agent-id` are validated as real UUIDs and fail fast if
  malformed.
- If `backend/` isn't importable in your checkout (wrong working directory, package not
  installed), every subcommand exits with code `3` and a plain message rather than a Python
  traceback — safe to rely on that exit code in a script.

The CLI's own house style is worth knowing if you're going to parse its non-JSON output: it never
prints the words "quarantine", "supersede", or "belief state" to the terminal (those stay as
internal/schema vocabulary) — expect "held for review", "escalated", "closed the loop" instead.
Use `--json` if you're parsing programmatically; the plain-text framing is not a stable contract.

## What I could not verify while writing this

- Whether a write from the MCP identity would actually be *refused*. The account is now
  write-capable by role, and `scripts/verify_mcp.py`'s write probe is opt-in and has not been
  run. "We only call read tools" is a statement about this project's behaviour, not an enforced
  guarantee.

  (Superseded, kept because the sequence is the point: this section originally said no
  successful `select_query` had been made against the live cluster. That was true when written
  and is no longer. After granting `CLUSTER_OPERATOR_WRITER`, the full handshake, a trust-ladder
  read returning `{"unconfirmed":118,"verified":5}`, and an `explain_query` showing a
  `vector search` node were all verified live through the managed MCP server.)
- Whether `ccloud audit list` (or any CockroachDB Cloud audit log) records individual MCP
  `select_query` calls is not documented anywhere Cockroach Labs publishes and was not checked
  empirically.
- The `/ingest` route's shared-secret gating and the full `/ingest` → `/decide` → produced-memory
  → `/confirm_outcome` promotion round trip were not driven live end to end this session; the
  `/decide` → `/confirm_outcome` round trip above was.
