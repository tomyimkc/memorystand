# 50,000 memories on CockroachDB Cloud (Basic, AWS us-west-2)

Seeded from a laptop in Hong Kong against the same managed cluster as
[results-cloud-5k.md](results-cloud-5k.md), scaled up from 5,000 to production-realistic
multi-tenant volume. **This run does not produce a latency comparison** — read "What this
run does NOT establish" below before drawing any conclusion from it.

## What this run establishes

- `agent_memories` now holds 50,131 rows on the live CockroachDB Cloud cluster: 50,000
  synthetic rows across 40 tenants at exactly 1,250 rows each
  (`scripts/loadtest.py --rows 50000 --tenants 40`), plus the 131-row curated demo tenant
  `9c8f6e5a-9d1a-4a1c-8f2e-3b6d1c7a4e10` (117 rows with `verdict='accepted'`) that the live
  demo at `https://ojao6oaxlk26mqfjwpuy7g4dy40tglyi.lambda-url.us-west-2.on.aws` actually
  queries against.
- The vector index is used at this scale on Cloud Basic, for the real
  `backend/memory.py::recall()` query shape: a `vector search` node on
  `agent_memories@agent_memories_tenant_idx`, with `prefix spans` scoped to the tenant +
  `verdict='accepted'` equality predicate, quoted verbatim below.
- The genuinely interesting result of this run has nothing to do with the 50k row count:
  the same index node engages identically on the 117-row demo tenant. See "Index engagement
  does not require tenant scale" below.

## What this run does NOT establish

**No latency comparison can be drawn from this run.** `agent_memories_noindex` — the
apples-to-apples un-indexed control used in [results.md](results.md) and
[results-cluster-250k.md](results-cluster-250k.md) — was supposed to reach 50,000 rows as
well, so recall latency could be measured indexed-vs-unindexed at this volume. It reached
only **36,300 of 50,000 rows** before the seeding run died with:

```
psycopg2.OperationalError: could not receive data from server: No route to host
```

The laptop's network dropped mid-run, trans-Pacific, as it does. A control table that is
27% short of its target is not a valid baseline at this row count, so **no speedup multiple
is quoted from this run** and none should be inferred from it. For a completed
indexed-vs-unindexed comparison, see [results-cluster-250k.md](results-cluster-250k.md)
(local, 250,000 rows, 76.54x faster at p50, 45.01x at p99). For why a trans-Pacific latency
number is close to meaningless regardless of whether the control finishes, see
[results-cloud-5k.md](results-cloud-5k.md) — there, RTT alone buried the index's advantage
down to 1.4x even with a complete control table.

## Exact command

```
python scripts/loadtest.py --rows 50000 --tenants 40
```

## Environment

- CockroachDB Cloud, tier **Basic**, region **AWS us-west-2**: `CockroachDB CCL v26.2.1`.
  Cluster id `3b37f0d1-33ca-4d3f-a7b5-29bb74dcc641`. GC window: 4,500s (75 min) — see
  [results-cloud-5k.md](results-cloud-5k.md) for why that's tighter than a local node's
  default 14,400s.
- Client: laptop in Hong Kong. Server: `us-west-2` (Oregon). Same topology, same caveat, as
  the 5k run.
- `agent_memories`: the real, already-deployed table from `db/schema.sql`, with its
  `VECTOR INDEX agent_memories_tenant_idx (tenant_id, verdict, embedding vector_cosine_ops)`.
- Rows seeded this run: 50,000, across 40 synthetic tenants, 1,250 rows each. Combined with
  the pre-existing 131-row curated demo tenant, `agent_memories` totals 50,131 rows.
- `agent_memories_noindex`: intended as the 50,000-row apples-to-apples un-indexed control.
  Reached 36,300 rows before the network drop above; **incomplete, not usable as a
  control at this size**.

## Write throughput (seeding)

| Table | Rows | Seconds | Rows/sec |
|---|---:|---:|---:|
| agent_memories (vector-indexed) | 50000 | 730.58 | 68.4 |

68.4 rows/sec, trans-Pacific. Read this the same way as the 5k run's 45.1 rows/sec
(see [results-cloud-5k.md](results-cloud-5k.md)): it is a measurement of the Pacific as much
as of CockroachDB. Each batched insert pays a full ocean round trip, and that RTT dominates
the number — it says little about the cluster's actual write capacity, which the local
250k run (same index maintained, no ocean in the way) put at 972.2 rows/sec.

## EXPLAIN (the actual `backend.memory.recall()` query: tenant_id + verdict='accepted' + vector ORDER BY)

Verified live against the 117-row curated demo tenant:

```
        └── • lookup join
            │ table: agent_memories@agent_memories_pkey
            │ equality: (memory_id) = (memory_id)
            │ equality cols are key
            │
            └── • vector search
                  table: agent_memories@agent_memories_tenant_idx
                  target count: 5
                  prefix spans: [/'9c8f6e5a-9d1a-4a1c-8f2e-3b6d1c7a4e10'/'accepted' - /'9c8f6e5a-9d1a-4a1c-8f2e-3b6d1c7a4e10'/'accepted']
```

The identical node shape — `vector search` on `agent_memories@agent_memories_tenant_idx`,
`target count: 5`, `prefix spans` bounded to a single tenant + `accepted`, sitting above a
`lookup join` on `agent_memories@agent_memories_pkey` and under a `top-k` — was also
confirmed against one of the 40 synthetic tenants (1,250 rows). Both plans came from
EXPLAINing the exact statement `backend/memory.py::recall()` issues, not a contrived probe.

- Shows a `vector search` node, at 117 rows and at 1,250 rows: **True**
- Shows a `prefix spans` line scoped to the tenant + verdict equality predicate, at both
  sizes: **True**

### Index engagement does not require tenant scale

This is the result worth reading closely, and it cuts against an assumption made earlier in
this project. The working hypothesis going into this run was that 131 rows — the curated demo
tenant — was too few for the optimizer to bother with the vector index, and that it would fall
back to a sequential scan; the 50k seed existed partly to give the planner a data volume it
would "take seriously." That hypothesis was wrong, and it was disproved by measurement, not
by re-reading documentation: EXPLAIN on the 117-row demo tenant shows the exact same
`vector search` / `prefix spans` node as EXPLAIN on a 1,250-row synthetic tenant.

The reason is structural, not a coincidence of this run: the index prefix is
`(tenant_id, verdict, embedding)`, so C-SPANN here is scoped per-tenant before it ever
considers row count. The optimizer isn't choosing between "scan a small table" and "search a
big index" — it's choosing to search a partition that is small *by construction*, regardless
of how many other tenants' rows sit in the same physical table. Per-tenant row count did not
gate index selection at any point in this project; total table size was never the variable
that mattered. Say that plainly: the 50k seed was not what made the index work. What the 50k
seed actually provides is realistic multi-tenant *volume* on the cluster — useful for write-
throughput and operational testing — not index engagement, which was already settled at 131
rows before this run started.

## Query-shape trap (costs real debugging time — a reproducibility warning)

Two hand-written variants of "the same" query, tried while chasing the EXPLAIN output above,
silently produced a plain scan with no `vector search` node at all:

1. **Ordering with `<->` (L2 distance) instead of `<=>` (cosine distance).** The index is
   built `vector_cosine_ops`; an L2 ordering cannot use a cosine-ops index at all, and the
   optimizer falls back without any warning.
2. **Adding an explicit `embedding IS NOT NULL` predicate.** This reads like a harmless
   defensive filter and instead defeats the index outright.

The EXPLAIN output above is only valid for the exact statement `backend/memory.py::recall()`
issues. Any hand-written variant — even one that looks semantically identical — needs its own
EXPLAIN before it can be trusted to hit the index.

## Caveats

- Single run, live CockroachDB Cloud Basic cluster shared with other concurrent work on this
  project (the demo Lambda queries the same cluster) — not a controlled, repeated,
  statistically-powered benchmark.
- Trans-Pacific: client in Hong Kong, server in `us-west-2`. Write throughput above is not a
  cluster-capacity number; see [results-cloud-5k.md](results-cloud-5k.md) and the local
  250k run for that.
- The un-indexed control (`agent_memories_noindex`) reached 36,300 of 50,000 rows before a
  `psycopg2.OperationalError: could not receive data from server: No route to host` killed
  the seeding run. It was not re-run to completion as of this writing. No recall-latency
  numbers are reported for this run as a result — see "What this run does NOT establish"
  above.
- Seeding bypasses `backend.memory.remember`'s admission control (contradiction checks,
  embedding-neighbour comparison) by design, same as the 5k and 250k runs — this exercises
  the storage and index path, not the full write-time adjudication path.
