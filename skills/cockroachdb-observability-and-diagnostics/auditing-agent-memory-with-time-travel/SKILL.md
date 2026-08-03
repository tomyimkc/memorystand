---
name: auditing-agent-memory-with-time-travel
description: Reconstructs what an application (or an autonomous agent's memory store) believed at a past instant using CockroachDB's built-in MVCC history via AS OF SYSTEM TIME, and verifies that a vector-indexed audit table is actually using its index instead of silently falling back to a full scan. Use when investigating whether a past decision was justified by the data available at the time, debugging a "why did the agent do that" incident after the fact, or diagnosing a vector-search query that got slower after a bulk load or schema change.
compatibility: "Tested on CockroachDB v26.2.5, self-hosted single node, insecure mode, via psycopg2 2.9. AS OF SYSTEM TIME and SET TRANSACTION AS OF SYSTEM TIME are core SQL features present in all actively supported CockroachDB versions. The vector-index sections require VECTOR columns and vector indexes, which were a preview feature behind the `feature.vector_index.enabled` cluster setting at the time of testing -- confirm availability on your own cluster/version before relying on it."
license: Apache-2.0
metadata:
  author: standing-hackathon
  version: "1.0"
---

# Auditing Agent Memory with Time Travel

Every row CockroachDB stores keeps its recent MVCC history for free. `AS OF SYSTEM TIME` (AOST)
turns that history into a query-able time machine: point any read at a past instant and get back
exactly what a table looked like then, no bitemporal schema, no audit-log table, no triggers. This
is the cheapest, most reliable way to answer "what did the system believe when it made this
decision" -- which is exactly the question you need answered when auditing an AI agent's memory
or decision log after an incident.

This skill also covers a narrower, unrelated-sounding but frequently co-occurring problem: a
vector-indexed table that silently stops using its ANN index. Both techniques below were
independently exercised against a live CockroachDB v26.2.5 node; every SQL statement, error
message, and `EXPLAIN` plan quoted here is copy-pasted from that session, not reconstructed from
memory.

**Complement to other diagnostics:** for optimizer statistics staleness generally (not
specifically after a vector-index bulk load), see the `auditing-table-statistics` skill in this
repository.

## When to Use This Skill

- Reconstructing what an agent (human or AI) knew at the moment it made a specific decision
- Auditing whether a decision was consistent with the data available at decision time, after the
  data has since changed or been corrected
- Debugging "it worked five minutes ago" by diffing current state against a recent snapshot
- A `SELECT ... AS OF SYSTEM TIME` inside a CTE or subquery fails with a syntax error you don't
  understand
- A historical query works in `cockroach sql` but fails only when run from an application using
  `psycopg2` (or another driver with client-side implicit transactions)
- A vector-search (`ORDER BY embedding <-> $1 LIMIT k`) query that used to be fast is now doing a
  full table scan after a bulk load, an index change, or simply growing past a few hundred rows

## Prerequisites

- SQL connection to a CockroachDB cluster with `SELECT` privilege on the audited tables
- For the vector-index sections: `CREATE`/`DROP` on a scratch table, and the
  `feature.vector_index.enabled` cluster setting turned on if your version gates it behind a
  preview flag
- Basic familiarity with CockroachDB's MVCC model (every write is a new timestamped version; old
  versions are not deleted until garbage collection removes them)

## Core Concepts

**MVCC history is the audit trail.** CockroachDB never overwrites a row in place -- every `UPDATE`
or `DELETE` writes a new MVCC version and keeps the old one until the garbage collector reclaims
it. `AS OF SYSTEM TIME <expr>` tells a read to ignore every version newer than `<expr>` and return
the row exactly as it stood then. No application code has to opt in; this works on any table with
no schema change.

**The replay horizon is not infinite.** Old MVCC versions are deleted once they age past
`gc.ttlseconds` for the range (`ALTER RANGE ... CONFIGURE ZONE USING gc.ttlseconds = ...`,
default 90000s / 25h on a fresh cluster, but check yours -- see Step 5). A request for a `t` older
than that horizon does not silently return wrong data; it fails, but the raw error is a low-level
KV error that is unreadable to an end user. Check the horizon yourself and fail with a clear
message before you hit it.

## Steps

### 1. Reconstruct a single table's past state

The simplest form: put `AS OF SYSTEM TIME` on the `FROM` clause of a top-level statement.

```sql
-- What did agent_memories look like 10 minutes ago?
SELECT memory_id, verdict, trust_tier, content
FROM agent_memories AS OF SYSTEM TIME '-10m'
WHERE tenant_id = '9c8f6e5a-9d1a-4a1c-8f2e-3b6d1c7a4e10'
ORDER BY created_at DESC
LIMIT 20;
```

`<expr>` accepts a relative interval (`'-10m'`, `'-1h'`), an absolute timestamp
(`'2026-08-03 18:00:00'`), or a decimal HLC timestamp (`'1785796832.956541860,0'`) if you need to
pin an exact logical tick, e.g. one captured from another query's `cluster_logical_timestamp()`.

### 2. The trap: AS OF SYSTEM TIME cannot appear in a subquery or CTE

This is the single most common way people break this. It fails with SQLSTATE `42601`
(`syntax_error`), verified verbatim against v26.2.5:

```sql
-- FAILS
SELECT count(*) FROM (
    SELECT * FROM agent_memories AS OF SYSTEM TIME '-1m'
) sub;
```

```text
psycopg2.errors.SyntaxError: AS OF SYSTEM TIME must be provided on a top-level statement
```

`AS OF SYSTEM TIME` fixes the read timestamp for an entire statement (or transaction -- see Step
3), not for one table reference nested inside a larger query. CockroachDB rejects it outright
rather than silently picking an inconsistent or ambiguous timestamp. There is no per-subquery
workaround; restructure so the clause is on the outermost `FROM`, or use Step 3 to pin the whole
transaction instead.

### 3. Reconstruct a consistent multi-table snapshot

A single incident audit almost always needs more than one table read at the *same* instant (e.g.
`agent_memories` joined with `agent_decisions`). Putting `AS OF SYSTEM TIME` on each `FROM` clause
separately does not guarantee they land on the same timestamp if time has passed between
statements. `SET TRANSACTION AS OF SYSTEM TIME` pins the whole transaction instead, so every
statement in it reads the same instant:

```sql
SET TRANSACTION AS OF SYSTEM TIME '-10m';
SELECT count(*) FROM agent_memories;      -- as of -10m
SELECT count(*) FROM agent_decisions;     -- also as of -10m, same instant
SELECT m.memory_id, m.content, d.action
FROM agent_memories m
JOIN agent_decisions d ON m.memory_id = ANY(d.consulted_memory_ids)
WHERE m.tenant_id = d.tenant_id;          -- multi-table join, one consistent snapshot
```

Verified end to end on v26.2.5 (via psycopg2, one connection, `autocommit=False`): all three
statements ran without error and returned counts consistent with the pinned instant.

**Ordering matters:** `SET TRANSACTION AS OF SYSTEM TIME` must be the *first statement that reads
data* in the transaction. If any real table read happens first, CockroachDB has already fixed a
(non-historical) read timestamp for the transaction and refuses to move it. Verified error,
SQLSTATE `25000`:

```text
psycopg2.errors.InvalidTransactionState: cannot set fixed timestamp, txn "sql txn" ...
already performed reads
```

A `SELECT 1` (no table access) before it is harmless; a `SELECT count(*) FROM agent_memories`
before it is not.

### 4. The trap: psycopg2's implicit transaction breaks `BEGIN AS OF SYSTEM TIME`

`psycopg2` (like most driver libraries with client-side transaction management) opens an implicit
transaction on the connection before your first `cursor.execute()` call when `autocommit=False`
(the default). That means the transaction has already begun by the time your first statement
runs, so:

```python
cur.execute("BEGIN AS OF SYSTEM TIME '-1m'")
```

fails with SQLSTATE `25001` (`active_sql_transaction`), verified verbatim:

```text
psycopg2.errors.ActiveSqlTransaction: there is already a transaction in progress
```

`SET TRANSACTION AS OF SYSTEM TIME` (Step 3) does not have this problem -- it sets a property of
the transaction psycopg2 already opened, rather than trying to open a new one, so it is the
correct pattern under psycopg2 (or any driver behaving this way). This is also why a bare
`SELECT ... AS OF SYSTEM TIME '-1m'` as your very first statement can *still* fail on such a
connection: the implicit transaction has already fixed a "now" read timestamp before your
statement is parsed, and the historical timestamp you're asking for conflicts with it. Verified,
SQLSTATE `0A000`:

```text
psycopg2.errors.FeatureNotSupported: inconsistent AS OF SYSTEM TIME timestamp; expected: ..., got: ...
HINT: try SET TRANSACTION AS OF SYSTEM TIME
```

The only combinations verified safe on psycopg2:

| Pattern | Result |
|---|---|
| `conn.autocommit = True`, single statement, `FROM t AS OF SYSTEM TIME '...'` | Works |
| `conn.autocommit = False`, `SET TRANSACTION AS OF SYSTEM TIME '...'` as the *first* statement | Works, pins the whole transaction |
| `conn.autocommit = False`, `BEGIN AS OF SYSTEM TIME '...'` | Fails, `25001` |
| `conn.autocommit = False`, inline `AS OF SYSTEM TIME` after any other statement already ran | Fails, `0A000` |

### 5. Know your replay horizon before you hit it

Old MVCC versions age out after `gc.ttlseconds`. Look it up per table (it can be overridden below
the cluster default):

```sql
SHOW ZONE CONFIGURATION FOR TABLE agent_memories;
-- ...
--   gc.ttlseconds = 14400,
-- ...
```

Parse `gc.ttlseconds` out of that (it's the only reliable source -- there is no dedicated system
table column for it) and reject an out-of-range request client-side with a message a human can
act on, instead of letting it fail deep inside the KV layer:

```python
import re
from datetime import datetime, timedelta, timezone

def gc_window_seconds(cur, table: str) -> int:
    cur.execute(f"SHOW ZONE CONFIGURATION FOR TABLE {table}")
    _, raw_config = cur.fetchone()
    match = re.search(r"gc\.ttlseconds\s*=\s*(\d+)", raw_config)
    if not match:
        raise RuntimeError(f"could not find gc.ttlseconds in zone config for {table}")
    return int(match.group(1))

def assert_within_replay_horizon(cur, table: str, requested: datetime) -> None:
    ttl = gc_window_seconds(cur, table)
    horizon = datetime.now(timezone.utc) - timedelta(seconds=ttl)
    if requested < horizon:
        raise ValueError(
            f"Cannot replay {table} as of {requested.isoformat()}: that is older than "
            f"this table's {ttl}s retention window (horizon: {horizon.isoformat()}). "
            "History that old has already been garbage collected."
        )
```

Verified: `SHOW ZONE CONFIGURATION FOR TABLE agent_memories` against the live cluster returned
`gc.ttlseconds = 14400` (4 hours), inherited from the `RANGE default` zone config -- confirming
the value is readable exactly this way and that it is a real, finite window, not a documentation
default that happens to not apply.

### 6. Vector-index prefix design: the filter columns must be IN the prefix

A `VECTOR INDEX` (C-SPANN) is only used by the optimizer when every equality predicate the query
also needs (beyond the vector distance itself) appears as a prefix column of that index, in order.
If one of them is missing from the prefix, the optimizer does not partially use the index -- it
falls back to a full scan and filters afterward.

**Measured before/after, both at 5,000 rows on v26.2.5, otherwise identical schema and data:**

Index missing `verdict` from the prefix:

```sql
CREATE TABLE bad (
    memory_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    verdict   STRING NOT NULL,
    embedding VECTOR(512),
    VECTOR INDEX (tenant_id, embedding vector_cosine_ops)
);
```

```sql
EXPLAIN SELECT memory_id FROM bad
WHERE tenant_id = $1 AND verdict = 'accepted'
ORDER BY embedding <=> $2 LIMIT 5;
```

```text
• filter
│ filter: (tenant_id = '...') AND (verdict = 'accepted')
└── • scan
      estimated row count: 5,000 (100% of the table; stats collected 0 seconds ago)
      table: bad@bad_pkey
      spans: FULL SCAN

index recommendations: 1
1. type: index creation
   SQL command: CREATE INDEX ON defaultdb.public.bad (tenant_id, verdict) STORING (embedding);
```

`verdict` in the prefix, same data, same query shape:

```sql
CREATE TABLE good (
    memory_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    verdict   STRING NOT NULL,
    embedding VECTOR(512),
    VECTOR INDEX (tenant_id, verdict, embedding vector_cosine_ops)
);
```

```text
└── • vector search
      table: good@good_tenant_id_verdict_embedding_idx
      target count: 5
      prefix spans: [/'...'/'accepted' - /'...'/'accepted']
```

Two more things worth knowing, both re-verified in this session:

- **The optimizer also chooses a full/regular scan over an ANN index scan at small scale.** On a
  ~100-row table, the same "good" query planned through a plain B-tree partial index instead of the
  vector index at all -- not because the index was misconfigured, but because a cost-based
  optimizer correctly judges the ANN index not worth it yet. Don't conclude an index is broken from
  an `EXPLAIN` on a near-empty table; test near your real row count.
- **`<->` and `<=>` are different operators** (Euclidean/L2 vs. cosine distance). A vector index
  built `WITH vector_cosine_ops` is only a candidate for queries ordering by `<=>`; ordering by
  `<->` against the same index will not match it. Match the operator in your query to the operator
  class the index was built with.

### 7. Remember to ANALYZE after a bulk load

Right after a bulk `INSERT`/`COPY`/restore, `EXPLAIN` on the freshly loaded table shows a `missing
stats` marker on the scan node instead of an `estimated row count`, verified on a 6,000-row table
immediately after loading it and before running `ANALYZE`:

```text
• scan
      missing stats
      table: analyze_probe2@analyze_probe2_tenant_id_verdict_idx
```

Without statistics the optimizer is working from defaults, not from what's actually in the table,
and on a table that changed shape during the bulk load (new columns populated, new value
distributions) its plan choices can be wrong in either direction -- for or against an index. Run
`ANALYZE` (or wait for automatic stats collection, which triggers on its own schedule, not
instantly) before measuring or `EXPLAIN`-ing a query against data you just bulk-loaded:

```sql
ANALYZE agent_memories;
```

## Common Workflows

### Workflow: was this decision justified by what the agent knew at the time?

**Scenario:** An agent's action is being second-guessed after the fact, once more memories have
been written and some may have since been corrected.

1. Look up the decision and the memory IDs it says it consulted:
   ```sql
   SELECT decision_id, action, rationale, consulted_memory_ids, decided_at
   FROM agent_decisions
   WHERE decision_id = $1;
   ```
2. Pin a transaction at (or just after) `decided_at`, and re-read exactly those memories as they
   stood then:
   ```sql
   SET TRANSACTION AS OF SYSTEM TIME '2026-08-03 18:40:00';
   SELECT memory_id, content, verdict, trust_tier
   FROM agent_memories
   WHERE memory_id = ANY($1::UUID[]);
   ```
3. Compare that historical content/verdict against the current row for the same `memory_id` to see
   what, if anything, has since changed:
   ```sql
   SELECT memory_id, content, verdict, trust_tier FROM agent_memories
   WHERE memory_id = ANY($1::UUID[]);  -- no AOST: current state
   ```
4. If the requested instant might be older than the table's retention window, run the check in
   Step 5 first and surface a clear error instead of a raw KV failure.

**Expected outcome:** a side-by-side of "what the agent saw" vs. "what we know now", with no
guessing about clock drift between separate queries because both multi-row reads in step 2 share
one pinned transaction timestamp.

## Safety Considerations

- All queries in this skill are read-only (`SELECT`, `SHOW`); none require write privileges beyond
  the scratch tables used to demonstrate the vector-index comparison in Step 6.
- `ANALYZE` is CPU/IO-intensive on large tables but non-blocking (does not lock the table or block
  writes); stagger it across large tables rather than running it on all of them at once.
- Do not use a historical (`AS OF SYSTEM TIME`) read as an authoritative precondition for a write
  in the same transaction -- it is a read-only, follower-read-eligible snapshot, not a lock, and it
  cannot be upgraded to a writing transaction.
- A time-travel query at massive scale (e.g. `SELECT *` across a huge table `AS OF SYSTEM TIME`
  months ago) still has to read that much data; it costs the same as an equivalent present-day
  query over the same row count, not less.

## Troubleshooting

| Symptom | SQLSTATE | Cause | Fix |
|---|---|---|---|
| `AS OF SYSTEM TIME must be provided on a top-level statement` | `42601` | `AS OF SYSTEM TIME` used inside a CTE or subquery | Move it to the outermost `FROM`, or use `SET TRANSACTION AS OF SYSTEM TIME` |
| `there is already a transaction in progress` | `25001` | `BEGIN AS OF SYSTEM TIME` sent on a driver (e.g. psycopg2) that already opened an implicit transaction | Use `SET TRANSACTION AS OF SYSTEM TIME` as the first statement instead of `BEGIN AS OF SYSTEM TIME` |
| `cannot set fixed timestamp ... already performed reads` | `25000` | `SET TRANSACTION AS OF SYSTEM TIME` issued after another statement already read a table in the same transaction | Issue it before any real table read; a `SELECT 1` before it is fine, a `SELECT ... FROM table` is not |
| `inconsistent AS OF SYSTEM TIME timestamp; expected ..., got ...` | `0A000` | Inline `FROM t AS OF SYSTEM TIME` used as a later statement in a transaction whose read timestamp is already fixed | Use `SET TRANSACTION AS OF SYSTEM TIME` for the whole transaction instead of per-statement `AS OF SYSTEM TIME` |
| Historical query fails deep in the KV layer with an unreadable error | n/a | Requested instant is older than `gc.ttlseconds` for that range | Check the horizon with `SHOW ZONE CONFIGURATION FOR TABLE ...` before issuing the query (Step 5) |
| `EXPLAIN` shows `FULL SCAN` on a table with a `VECTOR INDEX` | n/a | An equality predicate the query needs is not a prefix column of the vector index | Rebuild the index with every filter column in the prefix, in the order the query filters on them (Step 6) |
| `EXPLAIN` shows `missing stats` right after a bulk load | n/a | No optimizer statistics yet for the newly loaded data | Run `ANALYZE table_name;` before measuring (Step 7) |

## Key Considerations

- `AS OF SYSTEM TIME` reads MVCC history that already exists; it is not a substitute for an
  application-level audit table if you need history to survive past `gc.ttlseconds`, or if you
  need to record *why* something changed, not just *that* it changed.
- A snapshot captured this way is a live read against the cluster at query time, not a durable
  artifact -- rerun the query to get the same data again; it is not stored anywhere.
- Prefer `SET TRANSACTION AS OF SYSTEM TIME` over per-statement `AS OF SYSTEM TIME` as soon as a
  task needs more than one table read at the same instant; it is also the only form that reliably
  works on drivers with client-side implicit transactions.

## References

**Official CockroachDB Documentation:**
- [AS OF SYSTEM TIME](https://www.cockroachlabs.com/docs/stable/as-of-system-time) -- syntax and supported timestamp forms
- [SET TRANSACTION](https://www.cockroachlabs.com/docs/stable/set-transaction) -- pinning a whole transaction's read timestamp
- [Configure Replication Zones (gc.ttlseconds)](https://www.cockroachlabs.com/docs/stable/configure-replication-zones) -- garbage collection window configuration
- [Vector Indexes](https://www.cockroachlabs.com/docs/stable/vector-search) -- C-SPANN vector index design and prefix column rules
- [SHOW ZONE CONFIGURATION](https://www.cockroachlabs.com/docs/stable/show-zone-configurations) -- reading the effective `gc.ttlseconds` for a table
- [CREATE STATISTICS](https://www.cockroachlabs.com/docs/stable/create-statistics) -- refreshing optimizer statistics after a bulk load
- [EXPLAIN](https://www.cockroachlabs.com/docs/stable/explain) -- reading scan/index/vector-search plan nodes

**Related Skills:**
- `auditing-table-statistics` -- general optimizer statistics staleness diagnosis (not vector-index specific)
