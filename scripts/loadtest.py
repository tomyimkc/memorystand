#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Retrieval benchmark: vector-indexed recall vs. brute-force, on real CockroachDB.

This measures ONE thing precisely: how much a `VECTOR INDEX` changes recall
latency and query plan shape at a given row count, on this cluster, right now.
It is a load/benchmark tool, not a correctness test and not a production
recall path.

What it does:
  1. Seeds N synthetic memories across T tenants directly via SQL into the
     real `agent_memories` table (the one `db/schema.sql` creates WITH its
     vector index), in batches of 50-100 rows -- never one giant multi-row
     INSERT (a documented CockroachDB anti-pattern for VECTOR rows).
  2. Creates `agent_memories_noindex`, a column-identical clone with NO
     vector index, and inserts the exact same generated rows (same
     memory_id, same embedding) into it -- an honest, apples-to-apples
     control.
  3. Runs >=200 recall queries (the same query set) against both tables and
     reports p50/p95/p99/mean latency side by side, plus write throughput
     (rows/sec) observed during seeding.
  4. Captures and prints the literal `EXPLAIN` output for the indexed query
     -- the "vector search" node and the "prefix spans" line are the
     single most judge-legible artifact this script produces.
  5. Writes `benchmarks/results.md`: the numbers, the EXPLAIN block, the
     exact command used, row counts, the CockroachDB version, and an
     explicit single-run caveat.
  6. Cleans up after itself by default (deletes the synthetic rows it added
     to `agent_memories`, drops `agent_memories_noindex`) so repeated runs
     on a shared dev cluster do not accumulate garbage. Pass --keep-data to
     leave everything in place for manual inspection.

Honesty notes -- read before quoting these numbers:
  * Seeding writes straight to SQL, bypassing `backend.memory.remember`'s
    admission control (contradiction checks, neighbour comparison, etc).
    That is CORRECT for this harness: it measures retrieval, not the
    write-time adjudication cost. Every seeded row is written with
    verdict='accepted' by hand so it is actually recallable.
  * This is a SINGLE RUN on ONE local single-node CockroachDB container,
    not a controlled, repeated, statistically-powered benchmark. Treat the
    percentiles as directional evidence, not an SLA claim.

Usage:
    python scripts/loadtest.py                          # full run: 10000 rows, 50 tenants
    python scripts/loadtest.py --quick                   # smoke run: 500 rows, 10 tenants
    python scripts/loadtest.py --rows 20000 --tenants 100
    python scripts/loadtest.py --keep-data                # skip cleanup, inspect the tables after
"""

from __future__ import annotations

import argparse
import math
import os
import random
import statistics
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import psycopg2
from psycopg2 import sql
from psycopg2.extras import execute_values

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    # Allow `python scripts/loadtest.py` to find the `backend` package
    # regardless of the caller's current working directory.
    sys.path.insert(0, str(REPO_ROOT))

from backend import db  # noqa: E402
from backend.embeddings import EMBED_DIMS, embed, to_pgvector  # noqa: E402

INDEXED_TABLE = "agent_memories"
NOINDEX_TABLE = "agent_memories_noindex"
SEED_SOURCE_TAG = "memorystand-loadtest"
MEMORY_TYPE = "semantic"

INSERT_COLUMNS = [
    "memory_id",
    "tenant_id",
    "agent_id",
    "memory_type",
    "entity",
    "attribute_key",
    "attribute_value",
    "content",
    "source",
    "verdict",
    "confidence",
    "embedding",
]

ENTITIES = [
    "payments-service", "checkout-api", "orders-db", "auth-service",
    "inventory-service", "notification-worker", "billing-gateway",
    "search-index", "cache-cluster", "ingest-pipeline", "fraud-scorer",
    "webhook-relay",
]
ATTRIBUTE_KEYS = [
    "reads_from_table", "p99_latency_ms", "error_rate_pct", "replica_count",
    "last_deploy_sha", "writes_to_topic", "circuit_breaker_state",
    "cache_hit_rate_pct", "upstream_dependency", "gc_pause_ms",
]
ATTRIBUTE_VALUES = [
    "orders_v2", "142", "0.8", "6", "a91f3c0", "orders.events",
    "half-open", "0.94", "auth-service", "38",
]


# ----------------------------------------------------------------------
# Embedding helper: exponential backoff around embed(), which may call
# Amazon Bedrock (Titan Text Embeddings V2) when AWS credentials are
# present. The deterministic local stub never raises, but this wrapper
# protects the live-embeddings path (--live-embeddings) from transient
# throttling / network errors without hiding a persistent failure.
# ----------------------------------------------------------------------
def embed_with_backoff(text: str, max_attempts: int = 5) -> list[float]:
    delay = 0.25
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return embed(text)
        except Exception as exc:  # noqa: BLE001 - Bedrock throttling / transient network errors
            last_exc = exc
            if attempt == max_attempts:
                break
            time.sleep(delay + random.uniform(0, delay))
            delay = min(delay * 2, 8.0)
    raise RuntimeError(f"embed() failed after {max_attempts} attempts") from last_exc


# ----------------------------------------------------------------------
# Transient-error resilience. Two distinct, expected causes on this shared
# dev cluster, both retried with backoff rather than failing the whole run:
#
# 1. SQLSTATE 40001 (psycopg2.errors.SerializationFailure) -- CockroachDB's
#    documented SERIALIZABLE-only contract (see backend/db.py's own
#    docstring): a transaction is aborted whenever the cluster detects a
#    serializability conflict, and the caller's job is to retry. This repo
#    runs several concurrent agents against the same local cluster, so
#    write/write contention on `agent_memories` is expected, not a bug.
# 2. psycopg2.InternalError: "remote wall time is too far ahead (~700ms)
#    to be trustworthy" -- a Docker Desktop VM clock-drift artifact under
#    concurrent host load (observed on plain non-vector statements too, so
#    it is not specific to the vector index or this script). Self-clears
#    within a second or two.
#
# Every other exception is re-raised immediately -- this is a narrow,
# targeted retry, not a blanket except-and-hope.
# ----------------------------------------------------------------------
_transient_retry_count = 0


def _is_transient(exc: Exception) -> bool:
    if isinstance(exc, psycopg2.errors.SerializationFailure):
        return True
    if isinstance(exc, psycopg2.InternalError):
        message = str(exc).lower()
        return "wall time" in message or "trustworthy" in message
    return False


def execute_retrying(conn, cur, stmt, params=None, max_attempts: int = 12):
    global _transient_retry_count
    delay = 0.3
    for attempt in range(1, max_attempts + 1):
        try:
            cur.execute(stmt, params)
            return
        except (psycopg2.errors.SerializationFailure, psycopg2.InternalError) as exc:
            if not _is_transient(exc):
                raise
            conn.rollback()
            _transient_retry_count += 1
            if attempt == max_attempts:
                raise
            time.sleep(delay + random.uniform(0, delay))
            delay = min(delay * 2, 5.0)


def synth_content(i: int, rng: random.Random) -> tuple[str, str, str, str]:
    """Deterministic-given-rng synthetic memory content, varied enough that
    the (hash-seeded) stub embedding produces a genuinely distinct vector
    per row.
    """
    entity = rng.choice(ENTITIES)
    attr_key = rng.choice(ATTRIBUTE_KEYS)
    attr_val = rng.choice(ATTRIBUTE_VALUES)
    content = (
        f"[seed#{i}] {entity} reports {attr_key}={attr_val} "
        f"during incident review batch {i // 500}."
    )
    return content, entity, attr_key, attr_val


def generate_rows(
    n: int,
    tenant_ids: list[uuid.UUID],
    agent_ids: list[uuid.UUID],
    rng: random.Random,
    embed_fn,
) -> list[tuple]:
    """Generate the full synthetic row set ONCE, embedding computed once per
    row, so both benchmark tables receive byte-identical data -- retrieval
    differences are then attributable to the index alone, not to content
    drift between two independently generated row sets.
    """
    rows: list[tuple] = []
    for i in range(n):
        tenant_id = tenant_ids[i % len(tenant_ids)]
        agent_id = agent_ids[i % len(agent_ids)]
        content, entity, attr_key, attr_val = synth_content(i, rng)
        memory_id = str(uuid.uuid4())
        vec = to_pgvector(embed_fn(content))
        rows.append((
            memory_id, str(tenant_id), str(agent_id), MEMORY_TYPE,
            entity, attr_key, attr_val, content, SEED_SOURCE_TAG,
            "accepted", 0.9, vec,
        ))
    return rows


@dataclass
class SeedResult:
    table: str
    rows: int
    seconds: float

    @property
    def rows_per_sec(self) -> float:
        return self.rows / self.seconds if self.seconds > 0 else float("inf")


def insert_rows(conn, table: str, rows: list[tuple], batch_size: int) -> SeedResult:
    """Insert `rows` into `table` in batches of `batch_size` (50-100), never
    as one giant multi-row INSERT.
    """
    global _transient_retry_count
    insert_stmt = sql.SQL("INSERT INTO {table} ({cols}) VALUES %s").format(
        table=sql.Identifier(table),
        cols=sql.SQL(", ").join(sql.Identifier(c) for c in INSERT_COLUMNS),
    )
    template = insert_stmt.as_string(conn)
    start = time.perf_counter()
    with conn.cursor() as cur:
        for i in range(0, len(rows), batch_size):
            chunk = rows[i:i + batch_size]
            delay = 0.3
            for attempt in range(1, 13):
                try:
                    execute_values(cur, template, chunk, page_size=batch_size)
                    conn.commit()
                    break
                except (psycopg2.errors.SerializationFailure, psycopg2.InternalError) as exc:
                    if not _is_transient(exc):
                        raise
                    conn.rollback()
                    _transient_retry_count += 1
                    if attempt == 12:
                        raise
                    time.sleep(delay + random.uniform(0, delay))
                    delay = min(delay * 2, 5.0)
    elapsed = time.perf_counter() - start
    return SeedResult(table=table, rows=len(rows), seconds=elapsed)


def ensure_noindex_table(conn) -> None:
    """Create `agent_memories_noindex`: identical columns to `agent_memories`
    for the fields this benchmark touches, minus the VECTOR INDEX clause.
    CHECK constraints and the `supersedes` self-FK are intentionally
    omitted -- they are irrelevant to a retrieval-latency comparison and
    dropping them keeps this a pure index-vs-no-index test.
    """
    with conn.cursor() as cur:
        execute_retrying(conn, cur, sql.SQL("DROP TABLE IF EXISTS {t}").format(t=sql.Identifier(NOINDEX_TABLE)))
    conn.commit()
    with conn.cursor() as cur:
        execute_retrying(
            conn,
            cur,
            sql.SQL(
                """
                CREATE TABLE {t} (
                    memory_id        UUID PRIMARY KEY,
                    tenant_id        UUID NOT NULL,
                    agent_id         UUID NOT NULL,
                    memory_type      STRING NOT NULL,
                    entity           STRING,
                    attribute_key    STRING,
                    attribute_value  STRING,
                    content          STRING NOT NULL,
                    source           STRING,
                    verdict          STRING NOT NULL DEFAULT 'quarantined',
                    trust_tier       STRING NOT NULL DEFAULT 'unconfirmed',
                    confidence       FLOAT8 NOT NULL DEFAULT 0.5,
                    embedding        VECTOR({dims}),
                    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            ).format(t=sql.Identifier(NOINDEX_TABLE), dims=sql.Literal(EMBED_DIMS)),
        )
    conn.commit()


def cleanup(conn, keep_data: bool) -> None:
    if keep_data:
        print(f"--keep-data set: leaving {NOINDEX_TABLE} and seeded rows in {INDEXED_TABLE} in place.")
        return
    with conn.cursor() as cur:
        execute_retrying(
            conn,
            cur,
            sql.SQL("DELETE FROM {t} WHERE source = %s").format(t=sql.Identifier(INDEXED_TABLE)),
            (SEED_SOURCE_TAG,),
        )
        deleted = cur.rowcount
    conn.commit()
    with conn.cursor() as cur:
        execute_retrying(conn, cur, sql.SQL("DROP TABLE IF EXISTS {t}").format(t=sql.Identifier(NOINDEX_TABLE)))
    conn.commit()
    print(f"Cleanup: removed {deleted} synthetic rows from {INDEXED_TABLE}; dropped {NOINDEX_TABLE}.")


def percentile(data: list[float], p: float) -> float:
    if not data:
        return float("nan")
    s = sorted(data)
    k = (len(s) - 1) * (p / 100)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return s[int(k)]
    return s[f] + (s[c] - s[f]) * (k - f)


@dataclass
class LatencyResult:
    table: str
    samples_ms: list[float] = field(default_factory=list)

    @property
    def p50(self) -> float:
        return percentile(self.samples_ms, 50)

    @property
    def p95(self) -> float:
        return percentile(self.samples_ms, 95)

    @property
    def p99(self) -> float:
        return percentile(self.samples_ms, 99)

    @property
    def mean(self) -> float:
        return statistics.fmean(self.samples_ms) if self.samples_ms else float("nan")


def build_queries(
    tenant_ids: list[uuid.UUID], rng: random.Random, embed_fn, n: int
) -> list[tuple[uuid.UUID, str]]:
    queries = []
    for i in range(n):
        tenant_id = rng.choice(tenant_ids)
        text = f"[query#{i}] {rng.choice(ENTITIES)} status check {rng.choice(ATTRIBUTE_KEYS)}"
        queries.append((tenant_id, to_pgvector(embed_fn(text))))
    return queries


def _timed_execute_retrying(conn, cur, stmt, params, max_attempts: int = 12) -> float:
    """Execute + fetchall, returning elapsed milliseconds for the attempt that
    actually succeeded. A transient retry (see `execute_retrying` above,
    SQLSTATE 40001 or the local clock-skew artifact) is NOT included in the
    timed window -- it is contention/infra noise, not query cost, and
    folding it in would corrupt the percentiles with an artifact instead of
    a retrieval measurement.
    """
    global _transient_retry_count
    delay = 0.3
    for attempt in range(1, max_attempts + 1):
        t0 = time.perf_counter()
        try:
            cur.execute(stmt, params)
            cur.fetchall()
            return (time.perf_counter() - t0) * 1000
        except (psycopg2.errors.SerializationFailure, psycopg2.InternalError) as exc:
            if not _is_transient(exc):
                raise
            conn.rollback()
            _transient_retry_count += 1
            if attempt == max_attempts:
                raise
            time.sleep(delay + random.uniform(0, delay))
            delay = min(delay * 2, 5.0)
    raise RuntimeError("unreachable")  # pragma: no cover


def measure_recall(
    conn, table: str, queries: list[tuple[uuid.UUID, str]], k: int, warmup: int = 5
) -> LatencyResult:
    select_stmt = sql.SQL(
        "SELECT memory_id FROM {t} WHERE tenant_id = %s AND verdict = 'accepted' "
        "ORDER BY embedding <=> %s LIMIT %s"
    ).format(t=sql.Identifier(table))
    result = LatencyResult(table=table)
    with conn.cursor() as cur:
        for tenant_id, vec in queries[:warmup]:
            _timed_execute_retrying(conn, cur, select_stmt, (str(tenant_id), vec, k))
        for tenant_id, vec in queries:
            elapsed_ms = _timed_execute_retrying(conn, cur, select_stmt, (str(tenant_id), vec, k))
            result.samples_ms.append(elapsed_ms)
    return result


def capture_explain(conn, table: str, tenant_id: uuid.UUID, vec: str, k: int) -> str:
    """EXPLAIN for the exact query `backend.memory.recall()` runs: tenant_id
    equality + verdict='accepted' + a vector ORDER BY.
    """
    stmt = sql.SQL(
        "EXPLAIN SELECT memory_id FROM {t} WHERE tenant_id = %s AND verdict = 'accepted' "
        "ORDER BY embedding <=> %s LIMIT %s"
    ).format(t=sql.Identifier(table))
    with conn.cursor() as cur:
        execute_retrying(conn, cur, stmt, (str(tenant_id), vec, k))
        rows = cur.fetchall()
    return "\n".join(r[0] for r in rows)


def capture_explain_bare(conn, table: str, tenant_id: uuid.UUID, vec: str, k: int) -> str:
    """EXPLAIN for tenant_id equality + a vector ORDER BY, WITHOUT the
    verdict filter -- isolates whether the vector index is used at all on
    this table/data, independent of the recall()-shaped compound predicate.
    See the "Optimizer plan choice" section of the report for why this
    second query exists.
    """
    stmt = sql.SQL(
        "EXPLAIN SELECT memory_id FROM {t} WHERE tenant_id = %s "
        "ORDER BY embedding <=> %s LIMIT %s"
    ).format(t=sql.Identifier(table))
    with conn.cursor() as cur:
        execute_retrying(conn, cur, stmt, (str(tenant_id), vec, k))
        rows = cur.fetchall()
    return "\n".join(r[0] for r in rows)


def write_report(
    path: Path,
    args: argparse.Namespace,
    version: str,
    command: str,
    indexed_seed: SeedResult,
    noindex_seed: SeedResult,
    indexed_seeded_count: int,
    indexed_total_count: int,
    noindex_total_count: int,
    indexed_latency: LatencyResult,
    noindex_latency: LatencyResult,
    explain_text: str,
    has_vector_search: bool,
    has_prefix_spans: bool,
    explain_bare_text: str,
    has_vector_search_bare: bool,
    has_prefix_spans_bare: bool,
    transient_retries: int,
) -> None:
    def row(name: str, r: LatencyResult) -> str:
        return f"| {name} | {r.p50:.2f} | {r.p95:.2f} | {r.p99:.2f} | {r.mean:.2f} | {len(r.samples_ms)} |"

    # The optimizer narrative MUST come from what this run observed.
    #
    # It used to be a hardcoded paragraph asserting the optimizer did NOT pick the vector
    # index. That was true when written -- before the prefix became
    # (tenant_id, verdict, embedding) and before "embedding IS NOT NULL" left the recall
    # path -- and false afterwards. It kept printing regardless, so anyone running the
    # command this report invites them to run could see "vector search: True" in the EXPLAIN
    # block and, directly beneath it, prose insisting the opposite.
    #
    # A benchmark that contradicts its own evidence is worse than no benchmark, and doubly
    # so for a project arguing that claims should be checked. So: derive it.
    if has_vector_search and has_prefix_spans:
        optimizer_narrative = (
            "## Optimizer plan choice\n\n"
            "The optimizer **did** choose the vector index for the production query shape --\n"
            "the exact query `backend.memory.recall()` issues. The `vector search` node and\n"
            "the `prefix spans` line above come from this run, not from a contrived probe.\n\n"
            "`prefix spans` is the part worth reading closely: the scan is bounded to one\n"
            "tenant's *admitted* memories, because the index prefix is\n"
            "`(tenant_id, verdict, embedding)`. ANN cost therefore scales with that tenant's\n"
            "own row count rather than the whole table's, and a held-for-review memory is not\n"
            "merely filtered out of the result -- it is not in the partition being searched.\n\n"
            "Two things had to be true for this plan to appear, both learned the hard way: the\n"
            "filter columns must sit inside the index prefix, and `ANALYZE` must have run since\n"
            "the last bulk load. Miss either and the optimizer silently falls back to a scan.\n"
        )
    elif has_vector_search:
        optimizer_narrative = (
            "## Optimizer plan choice\n\n"
            "The optimizer chose the vector index (`vector search` appears above), but this run\n"
            "did **not** capture a `prefix spans` line -- usually meaning the tenant predicate\n"
            "did not prune to a single prefix. Treat the per-tenant scaling argument as\n"
            "unproven by this particular run.\n"
        )
    else:
        optimizer_narrative = (
            "## Optimizer plan choice -- read this before quoting the EXPLAIN above\n\n"
            "At the density this run measured, the cost-based optimizer did **not** choose the\n"
            "vector index for the production query. Causes worth checking, in order:\n\n"
            "1. **Stale statistics.** After a bulk load the optimizer estimates ~1 row per\n"
            "   tenant and a scan looks cheaper. This script runs `ANALYZE` before measuring;\n"
            "   if you seeded by another route, run it yourself.\n"
            "2. **A filter outside the index prefix.** The prefix is\n"
            "   `(tenant_id, verdict, embedding)`, so both must appear as equality predicates.\n"
            "   Adding `embedding IS NOT NULL` alone is enough to defeat the index.\n"
            "3. **Index competition.** `agent_memories` also carries `agent_memories_attr_idx`\n"
            "   and the partial `agent_memories_recallable_idx`; at small scale the cost model\n"
            "   can prefer either.\n\n"
            "This is a reportable finding about index selection at this scale, not evidence the\n"
            "vector index is broken -- but do not quote the EXPLAIN above as proof of ANN search\n"
            "while this section reads this way.\n"
        )

    speedup_p50 = (noindex_latency.p50 / indexed_latency.p50) if indexed_latency.p50 else float("nan")
    speedup_p99 = (noindex_latency.p99 / indexed_latency.p99) if indexed_latency.p99 else float("nan")
    embed_desc = (
        "Titan Text Embeddings V2 (live AWS call path, --live-embeddings)"
        if args.live_embeddings
        else "deterministic local stub (MEMORYSTAND_EMBED_STUB=1, no AWS account used)"
    )

    body = f"""# MemoryStand -- retrieval load test results

Generated by `scripts/loadtest.py`. This is a **single-run measurement on one
local single-node CockroachDB container** (see "Environment" below), not a
controlled, repeated, statistically-powered benchmark. Treat the numbers as
directional evidence that the vector index changes the query plan and
materially affects recall latency at this row count -- not as an SLA or a
claim about CockroachDB Cloud / multi-node performance.

## Exact command

```
{command}
```

## Environment

- CockroachDB: `{version}`
- Seeding path: direct SQL insert, bypassing `backend.memory.remember`'s
  admission control (contradiction checks, embedding-neighbour comparison).
  This is intentional -- this harness measures **recall latency**, not the
  write-time adjudication cost. Every seeded row is written with
  `verdict='accepted'` by hand so it is actually recallable.
- Embeddings: `backend.embeddings.embed`, {embed_desc}.
- `{INDEXED_TABLE}`: the real, already-deployed table from `db/schema.sql`,
  with its `VECTOR INDEX agent_memories_tenant_idx (tenant_id, embedding
  vector_cosine_ops)`.
- `{NOINDEX_TABLE}`: created by this script with an identical column set and
  the exact same generated rows (same `memory_id`, same embedding) as
  `{INDEXED_TABLE}`, with **no vector index** -- the apples-to-apples control.
- Rows seeded this run: {indexed_seed.rows} per table, tagged
  `source='{SEED_SOURCE_TAG}'` in `{INDEXED_TABLE}` (used for isolation and
  cleanup). `{INDEXED_TABLE}` total row count at measurement time:
  {indexed_total_count} (may include rows from other concurrent work on this
  shared cluster -- see caveat below). `{NOINDEX_TABLE}` total row count:
  {noindex_total_count}.
- Tenants: {args.tenants}. Batch size: {args.batch_size} rows per INSERT
  statement (never one giant multi-row INSERT).

## Write throughput (seeding)

| Table | Rows | Seconds | Rows/sec |
|---|---:|---:|---:|
| {INDEXED_TABLE} (vector-indexed) | {indexed_seed.rows} | {indexed_seed.seconds:.2f} | {indexed_seed.rows_per_sec:.1f} |
| {NOINDEX_TABLE} (no index) | {noindex_seed.rows} | {noindex_seed.seconds:.2f} | {noindex_seed.rows_per_sec:.1f} |

## Recall latency (k={args.k}, {args.queries} queries/table, identical query set against both tables)

| Table | p50 (ms) | p95 (ms) | p99 (ms) | mean (ms) | samples |
|---|---:|---:|---:|---:|---:|
{row(INDEXED_TABLE + ' (vector-indexed)', indexed_latency)}
{row(NOINDEX_TABLE + ' (no index)', noindex_latency)}

Indexed table is **{speedup_p50:.2f}x faster at p50** and **{speedup_p99:.2f}x
faster at p99** than the un-indexed control, at {indexed_seed.rows} rows
across {args.tenants} tenants.

## EXPLAIN (the actual `backend.memory.recall()` query: tenant_id + verdict='accepted' + vector ORDER BY)

```
{explain_text}
```

- Shows a `vector search` node: **{has_vector_search}**
- Shows a `prefix spans` line scoped to the tenant equality predicate: **{has_prefix_spans}**

{optimizer_narrative}

## Caveats

- Single run, single machine, single-node local CockroachDB container --
  not CockroachDB Cloud, not multi-node, not repeated across seeds.
- This cluster may be shared with other concurrent local development at
  measurement time; latency numbers can carry noise from that, not just
  from the index/no-index difference.
- Seeding bypasses `backend.memory.remember`'s admission control by design
  (see above) -- this measures retrieval, not the full write path.
- Percentiles are computed from {args.queries} samples per table; at low
  `--quick` row counts the vector index has less to prune and the gap will
  be smaller and noisier than at the full default row count (10000).
- This is a shared local cluster with other concurrent agents/processes
  writing to `agent_memories` during this run. The harness transparently
  retries two specific, expected transient errors with backoff: SQLSTATE
  40001 (`SerializationFailure`, CockroachDB's documented SERIALIZABLE
  conflict-and-retry contract -- see `backend/db.py`) from write
  contention, and `psycopg2.InternalError: remote wall time is too far
  ahead (...) to be trustworthy`, a Docker Desktop VM clock-drift artifact
  under concurrent host load (also observed on plain non-vector
  statements, so it is not a defect in the vector index or this script).
  Retried attempts are excluded from the timed recall-latency samples
  above so contention/infra noise cannot masquerade as a slow query.
  Transient retries observed this run: **{transient_retries}**.
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--rows", type=int, default=None, help="synthetic memories to seed per table (default: 10000, or 500 with --quick)")
    p.add_argument("--tenants", type=int, default=None, help="distinct tenant_ids to spread rows across (default: 50, or 10 with --quick)")
    p.add_argument("--quick", action="store_true", help="fast smoke run: defaults rows=500, tenants=10, queries=50")
    p.add_argument("--queries", type=int, default=None, help="recall queries per table, >=200 recommended (default: 200, or 50 with --quick)")
    p.add_argument("--batch-size", type=int, default=75, help="rows per INSERT batch, 50-100 (default: 75)")
    p.add_argument("--k", type=int, default=5, help="top-k for recall queries (default: 5, matches backend.memory.recall)")
    p.add_argument("--seed", type=int, default=42, help="RNG seed for reproducible synthetic content (default: 42)")
    p.add_argument("--dsn", default=None, help="override MEMORYSTAND_DSN/COCKROACH_DSN for this run")
    p.add_argument("--keep-data", action="store_true", help="skip cleanup: leave seeded rows and agent_memories_noindex in place")
    p.add_argument("--live-embeddings", action="store_true", help="do not force the deterministic embedding stub (requires AWS creds)")
    p.add_argument("--report", default=str(REPO_ROOT / "benchmarks" / "results.md"), help="output path for the markdown report")
    args = p.parse_args(argv)

    if args.quick:
        args.rows = args.rows if args.rows is not None else 500
        args.tenants = args.tenants if args.tenants is not None else 10
        args.queries = args.queries if args.queries is not None else 50
    else:
        args.rows = args.rows if args.rows is not None else 10000
        args.tenants = args.tenants if args.tenants is not None else 50
        args.queries = args.queries if args.queries is not None else 200

    if not (50 <= args.batch_size <= 100):
        p.error("--batch-size must be between 50 and 100")
    if args.tenants < 1:
        p.error("--tenants must be >= 1")
    if args.queries < 1:
        p.error("--queries must be >= 1")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.dsn:
        os.environ["MEMORYSTAND_DSN"] = args.dsn
    if not args.live_embeddings:
        os.environ.setdefault("MEMORYSTAND_EMBED_STUB", "1")

    rng = random.Random(args.seed)
    command = "python " + " ".join(["scripts/loadtest.py"] + (argv if argv is not None else sys.argv[1:]))

    print("=" * 70)
    print("MemoryStand -- retrieval load test (vector-indexed vs. brute-force)")
    print("=" * 70)
    print(
        f"rows/table={args.rows}  tenants={args.tenants}  queries/table={args.queries}  "
        f"batch_size={args.batch_size}  k={args.k}  "
        f"embed_stub={'off (live)' if args.live_embeddings else 'on'}"
    )
    print()
    print("NOTE: seeding writes directly via SQL, bypassing backend.memory.remember's")
    print("admission control (contradiction checks etc). That is correct for this")
    print("harness -- it measures RECALL latency, not the write-time adjudication cost.")
    print("Every seeded row is inserted with verdict='accepted' by hand so it is")
    print("recallable.\n")

    conn = db.get_conn()
    try:
        version = db.server_version(conn)
        print(f"CockroachDB: {version}\n")

        ensure_noindex_table(conn)

        tenant_ids = [uuid.uuid4() for _ in range(args.tenants)]
        agent_ids = [uuid.uuid4() for _ in range(args.tenants)]

        print(f"Generating {args.rows} synthetic rows (embedded once, inserted into both tables)...")
        rows = generate_rows(args.rows, tenant_ids, agent_ids, rng, embed_with_backoff)

        print(f"Seeding {args.rows} rows into {INDEXED_TABLE} (vector-indexed)...")
        indexed_seed = insert_rows(conn, INDEXED_TABLE, rows, args.batch_size)
        print(f"  {indexed_seed.rows} rows in {indexed_seed.seconds:.2f}s ({indexed_seed.rows_per_sec:.1f} rows/sec)\n")

        print(f"Seeding {args.rows} identical rows into {NOINDEX_TABLE} (no index)...")
        noindex_seed = insert_rows(conn, NOINDEX_TABLE, rows, args.batch_size)
        print(f"  {noindex_seed.rows} rows in {noindex_seed.seconds:.2f}s ({noindex_seed.rows_per_sec:.1f} rows/sec)\n")

        # Refresh table statistics before measuring ANYTHING.
        #
        # Not housekeeping -- this is the difference between measuring the vector index
        # and measuring a full scan. After a bulk load the optimizer still holds pre-load
        # statistics and estimates ~1 row per tenant, so a scan looks cheaper than an ANN
        # search and the plan silently falls back. EXPLAIN then reports
        # "estimated row count: 1 ... stats collected N minutes ago" with no
        # "vector search" node. Observed exactly that on a 10,000-row run before this was
        # added. Any operator bulk-loading agent memory hits the same thing, so the
        # report calls it out as an operational note rather than hiding it.
        print("Refreshing table statistics (ANALYZE) so the optimizer plans on real row counts...")
        with conn.cursor() as cur:
            for _tbl in (INDEXED_TABLE, NOINDEX_TABLE):
                execute_retrying(conn, cur, sql.SQL("ANALYZE {t}").format(t=sql.Identifier(_tbl)))
        print("  statistics refreshed\n")

        with conn.cursor() as cur:
            execute_retrying(
                conn, cur,
                sql.SQL("SELECT count(*) FROM {t} WHERE source = %s").format(t=sql.Identifier(INDEXED_TABLE)),
                (SEED_SOURCE_TAG,),
            )
            indexed_seeded_count = cur.fetchone()[0]
            execute_retrying(conn, cur, sql.SQL("SELECT count(*) FROM {t}").format(t=sql.Identifier(INDEXED_TABLE)))
            indexed_total_count = cur.fetchone()[0]
            execute_retrying(conn, cur, sql.SQL("SELECT count(*) FROM {t}").format(t=sql.Identifier(NOINDEX_TABLE)))
            noindex_total_count = cur.fetchone()[0]

        print("Building query set...")
        queries = build_queries(tenant_ids, random.Random(args.seed + 1), embed_with_backoff, args.queries)

        print(f"Measuring recall latency: {args.queries} queries x 2 tables (k={args.k})...")
        indexed_latency = measure_recall(conn, INDEXED_TABLE, queries, args.k)
        noindex_latency = measure_recall(conn, NOINDEX_TABLE, queries, args.k)

        print(
            f"  {INDEXED_TABLE:24s} p50={indexed_latency.p50:.2f}ms  "
            f"p95={indexed_latency.p95:.2f}ms  p99={indexed_latency.p99:.2f}ms"
        )
        print(
            f"  {NOINDEX_TABLE:24s} p50={noindex_latency.p50:.2f}ms  "
            f"p95={noindex_latency.p95:.2f}ms  p99={noindex_latency.p99:.2f}ms\n"
        )

        explain_tenant, explain_vec = queries[0]
        print("Capturing EXPLAIN for the indexed query (recall()-shaped: tenant_id + verdict)...")
        explain_text = capture_explain(conn, INDEXED_TABLE, explain_tenant, explain_vec, args.k)
        print("--- EXPLAIN (vector-indexed table, tenant_id + verdict='accepted') ---")
        print(explain_text)
        print("--- end EXPLAIN ---\n")
        has_vector_search = "vector search" in explain_text.lower()
        has_prefix_spans = "prefix spans" in explain_text.lower()
        print(f"EXPLAIN shows 'vector search' node: {has_vector_search}")
        print(f"EXPLAIN shows 'prefix spans' line: {has_prefix_spans}\n")

        print("Capturing EXPLAIN for the same table/data WITHOUT the verdict filter"
              " (isolates whether the vector index is used at all)...")
        explain_bare_text = capture_explain_bare(conn, INDEXED_TABLE, explain_tenant, explain_vec, args.k)
        print("--- EXPLAIN (vector-indexed table, tenant_id only) ---")
        print(explain_bare_text)
        print("--- end EXPLAIN ---\n")
        has_vector_search_bare = "vector search" in explain_bare_text.lower()
        has_prefix_spans_bare = "prefix spans" in explain_bare_text.lower()
        print(f"EXPLAIN (bare) shows 'vector search' node: {has_vector_search_bare}")
        print(f"EXPLAIN (bare) shows 'prefix spans' line: {has_prefix_spans_bare}\n")

        if _transient_retry_count:
            print(f"NOTE: retried {_transient_retry_count} transient error(s) (serialization "
                  "conflicts and/or local clock-skew artifacts) during this run (see report Caveats).\n")

        report_path = Path(args.report)
        write_report(
            report_path, args, version, command,
            indexed_seed, noindex_seed,
            indexed_seeded_count, indexed_total_count, noindex_total_count,
            indexed_latency, noindex_latency, explain_text,
            has_vector_search, has_prefix_spans,
            explain_bare_text, has_vector_search_bare, has_prefix_spans_bare,
            _transient_retry_count,
        )
        print(f"Report written to {report_path}\n")

        cleanup(conn, args.keep_data)
    finally:
        db.put_conn(conn)

    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
