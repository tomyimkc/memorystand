# SPDX-License-Identifier: Apache-2.0
"""Tamper-evident checkpoints of what the agent knew.

What this is: a small digest, taken on a schedule, over the set of memories that were
admitted at a given instant. Later, a historical reconstruction can be re-derived with
``AS OF SYSTEM TIME`` and checked against the digest recorded at the time.

What this is NOT, said plainly because the distinction is easy to blur and a careful
reviewer will catch it: this is **not** a durability mechanism, and it does not let you
replay content from beyond the cluster's garbage-collection window. It stores identifiers
and a hash, not the memories. Past the GC horizon the history is genuinely gone; what
survives is the ability to say whether a reconstruction someone shows you is the one that
was actually recorded.

The case it covers is therefore narrow and honest: it detects a reconstruction that has
been altered. Because nothing in this system is ever hard-deleted -- a corrected fact
supersedes rather than removes -- that is a smaller job than it would be elsewhere, and
this module is correspondingly small.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from psycopg2.extras import RealDictCursor

from . import db, replay

DIGEST_VERSION = "sha256-v1"


def _digest(rows: list[dict]) -> str:
    """Order-independent digest over (memory_id, content) pairs.

    Sorted by memory_id so two reconstructions of the same instant hash identically
    regardless of the order the database happened to return them in -- otherwise the
    check would produce false alarms on nothing more than a different query plan.
    """
    payload = [[r["memory_id"], r.get("content") or ""] for r in rows]
    payload.sort(key=lambda pair: pair[0])
    blob = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    return f"{DIGEST_VERSION}:{hashlib.sha256(blob.encode('utf-8')).hexdigest()}"


def _insert(conn, tenant_id: str, as_of: Any, memory_ids: list[str], digest: str) -> dict:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            INSERT INTO belief_snapshots (tenant_id, as_of_time, memory_ids, memory_digest)
            VALUES (%s, %s, %s::UUID[], %s)
            RETURNING snapshot_id::string AS snapshot_id, as_of_time, created_at
            """,
            (tenant_id, as_of, memory_ids, digest),
        )
        return dict(cur.fetchone())


def take(tenant_id: str, instant: Any = "-5s") -> dict:
    """Record a checkpoint of the admitted memories as of ``instant``.

    Defaults to a few seconds in the past rather than ``now()`` so the read is served
    from settled MVCC history instead of contending with in-flight writes.
    """
    rows = replay.belief_state_at(tenant_id, instant)
    memory_ids = sorted(r["memory_id"] for r in rows)
    digest = _digest(rows)

    # Resolve the instant to a concrete timestamp. put_conn() must be in a finally:
    # psycopg2's connection context manager scopes the TRANSACTION, not the connection,
    # so `with db.get_conn() as c: ...` followed by put_conn() outside leaks the pool's
    # only connection whenever the body raises -- and at maxconn=1 that wedges the
    # process. This is rule 6 in SPIKE-RESULTS.md, and this function broke it.
    conn = db.get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT now()")
            resolved = cur.fetchone()[0]
        conn.commit()
    finally:
        db.put_conn(conn)

    result = db.retry_serializable(_insert, tenant_id, resolved, memory_ids, digest)
    result.update({"tenant_id": tenant_id, "memory_count": len(memory_ids), "digest": digest})
    return result


def verify(tenant_id: str, snapshot_id: str) -> dict:
    """Re-derive the historical state and compare it to the recorded digest.

    Returns a verdict of ``verified``, ``altered``, or ``unverifiable``. ``unverifiable``
    is the honest answer when the instant has aged out of the GC window -- the checkpoint
    cannot prove anything about history the cluster no longer holds, and saying so is the
    whole point of building this rather than claiming durability we do not have.
    """
    conn = db.get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT snapshot_id::string AS snapshot_id, as_of_time,
                       memory_ids::STRING[] AS memory_ids, memory_digest
                FROM belief_snapshots WHERE tenant_id = %s AND snapshot_id = %s
                """,
                (tenant_id, snapshot_id),
            )
            snap = cur.fetchone()
        conn.commit()
    finally:
        db.put_conn(conn)

    if snap is None:
        raise ValueError(f"no such snapshot: {snapshot_id}")
    snap = dict(snap)

    try:
        rows = replay.belief_state_at(tenant_id, snap["as_of_time"])
    except replay.GCWindowExceeded as exc:
        return {
            "snapshot_id": snapshot_id,
            "verdict": "unverifiable",
            "reason": str(exc),
            "recorded_memory_count": len(snap["memory_ids"] or []),
        }

    recomputed = _digest(rows)
    matches = recomputed == snap["memory_digest"]
    return {
        "snapshot_id": snapshot_id,
        "as_of_time": snap["as_of_time"],
        "verdict": "verified" if matches else "altered",
        "recorded_digest": snap["memory_digest"],
        "recomputed_digest": recomputed,
        "recorded_memory_count": len(snap["memory_ids"] or []),
        "recomputed_memory_count": len(rows),
    }


def latest(tenant_id: str, limit: int = 10) -> list[dict]:
    conn = db.get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT snapshot_id::string AS snapshot_id, as_of_time, memory_digest,
                       array_length(memory_ids, 1) AS memory_count, created_at
                FROM belief_snapshots WHERE tenant_id = %s
                ORDER BY as_of_time DESC LIMIT %s
                """,
                (tenant_id, limit),
            )
            rows = [dict(r) for r in cur.fetchall()]
        conn.commit()
        return rows
    finally:
        db.put_conn(conn)


def lambda_handler(event: dict | None = None, context: Any = None) -> dict:
    """EventBridge Scheduler target: checkpoint every tenant that has memories.

    Also serves as the keep-warm ping, because it issues a real query against
    CockroachDB. A scheduler that only invokes the Lambda would keep the function warm in
    front of a suspended cluster -- which is precisely the outage this is meant to avoid.
    """
    conn = db.get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT tenant_id::string FROM agent_memories")
            tenants = [r[0] for r in cur.fetchall()]
        conn.commit()
    finally:
        db.put_conn(conn)

    taken, errors = [], []
    for tenant in tenants:
        try:
            taken.append(take(tenant))
        except Exception as exc:  # noqa: BLE001 - one bad tenant must not stop the rest
            errors.append({"tenant_id": tenant, "error": f"{type(exc).__name__}: {exc}"})

    return {
        "tenants": len(tenants),
        "snapshots_taken": len(taken),
        "errors": errors,
        "db_reachable": True,
    }


__all__ = ["DIGEST_VERSION", "lambda_handler", "latest", "take", "verify"]
