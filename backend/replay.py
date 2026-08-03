# SPDX-License-Identifier: Apache-2.0
"""Cross-examination: reconstructing what the agent believed at a past instant.

Implementation note that cost a real debugging cycle, recorded here so nobody repeats it:
**CockroachDB rejects AS OF SYSTEM TIME inside a subquery or CTE.**

    SELECT ... FROM t FULL OUTER JOIN (SELECT ... FROM t AS OF SYSTEM TIME '-1h') ...
    ERROR: AS OF SYSTEM TIME must be provided on a top-level statement (SQLSTATE 42601)

So the tempting "diff in one self-join" does not exist. What does exist is better:

    BEGIN AS OF SYSTEM TIME '<ts>'; ...many statements, many tables...; COMMIT;

That pins an entire transaction to one instant, which means the historical read is a
transactionally consistent snapshot across ``agent_memories`` AND ``agent_decisions``
together -- not one table frozen while another moves. Reconstructing an agent's belief
state is exactly a multi-table question, so the pinned transaction is the right primitive
and the single-statement join would have been the weaker answer even if it worked.

Bounded by the cluster's GC window (``gc.ttlseconds``, 14400s / 4h by default). Past that
horizon the history is genuinely gone; ``GCWindowExceeded`` says so in those words rather
than leaking a raw SQLSTATE.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any, Sequence

from psycopg2.extras import RealDictCursor

from . import db, embeddings


class GCWindowExceeded(RuntimeError):
    """The requested instant is older than the cluster's garbage-collection window."""


def _as_aost_literal(instant: Any) -> str:
    """Render an instant for AS OF SYSTEM TIME.

    Accepts a datetime, an HLC decimal string from ``cluster_logical_timestamp()``, or a
    relative string like '-30s'. Interval strings pass through untouched.
    """
    if isinstance(instant, str):
        return instant
    if isinstance(instant, _dt.datetime):
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=_dt.timezone.utc)
        return instant.astimezone(_dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f+00:00")
    return str(instant)


def _is_history_horizon_error(exc: BaseException) -> bool:
    """Did this fail because the requested instant is beyond usable history?

    Three distinct shapes, all meaning the same thing to a user:
      * "batch timestamp ... must be after replica GC threshold" -- garbage collected.
      * "must be after replica GC threshold" on a range with a shorter TTL.
      * InvalidCatalogName / "does not exist" -- the instant predates the creation of the
        database or table itself. Observed live: querying AS OF SYSTEM TIME '-72h' against
        a cluster created today reports 'database "defaultdb" does not exist', which is
        technically accurate and completely baffling as an error message.
    """
    text = str(exc).lower()
    return (
        "gc threshold" in text
        or "batch timestamp" in text
        or "does not exist" in text
    )


# Backwards-compatible alias; the old name described only one of the three cases.
_is_gc_error = _is_history_horizon_error


def gc_window_seconds() -> int | None:
    """The cluster's configured GC TTL, so callers can explain the horizon to a human."""
    conn = db.get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT raw_config_sql FROM [SHOW ZONE CONFIGURATION FOR RANGE default]")
            row = cur.fetchone()
        conn.commit()
        if not row:
            return None
        for line in str(row[0]).splitlines():
            if "gc.ttlseconds" in line:
                return int(line.split("=")[1].strip().rstrip(","))
        return None
    except Exception:  # noqa: BLE001 - a zone config that cannot be read is not fatal
        conn.rollback()
        return None
    finally:
        db.put_conn(conn)


def belief_state_at(tenant_id: str, instant: Any, *, limit: int = 500) -> list[dict]:
    """Every memory that was admitted as of ``instant``, in one pinned transaction.

    Error-path note: the friendly horizon message needs ``gc_window_seconds()``, which
    needs a connection. The pool holds exactly one (correct for Lambda), so calling it
    from inside the ``except`` -- while this function still holds that connection --
    deadlocks with "connection pool exhausted". The horizon error is therefore captured,
    the connection released in ``finally``, and only then is the message built.
    """
    literal = _as_aost_literal(instant)
    horizon_error: BaseException | None = None
    conn = db.get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Pin the whole transaction to one instant.
            #
            # NOT "BEGIN AS OF SYSTEM TIME": psycopg2 opens a transaction implicitly on
            # the first execute, so an explicit BEGIN raises
            # "there is already a transaction in progress" (SQLSTATE 25001). CockroachDB
            # accepts SET TRANSACTION AS OF SYSTEM TIME as the first statement of an
            # already-open transaction, which is exactly what a DB-API driver gives us.
            # Cannot be parameterised -- AOST takes a literal.
            cur.execute(f"SET TRANSACTION AS OF SYSTEM TIME '{literal}'")
            cur.execute(
                """
                SELECT memory_id::string AS memory_id, content, entity, attribute_key,
                       attribute_value, trust_tier, confidence, source
                FROM agent_memories
                WHERE tenant_id = %s AND verdict = 'accepted'
                ORDER BY created_at
                LIMIT %s
                """,
                (tenant_id, limit),
            )
            rows = [dict(r) for r in cur.fetchall()]
            conn.commit()
        return rows
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        if not _is_history_horizon_error(exc):
            raise
        horizon_error = exc  # enriched below, after the connection is back in the pool
    finally:
        db.put_conn(conn)

    window = gc_window_seconds()
    hint = f" This cluster keeps {window}s (~{window // 3600}h) of history." if window else ""
    raise GCWindowExceeded(
        f"Cannot replay {literal}: that instant is beyond this cluster's usable "
        f"history -- either garbage-collected, or earlier than the data itself.{hint}"
    ) from horizon_error


def recall_as_of(tenant_id: str, agent_id: str | None, query: str, instant: Any, k: int = 5) -> list[dict]:
    """Re-run the agent's own recall query, pinned to a past instant.

    The ranking is the identical one the agent saw, not a reconstruction: the whole
    transaction is pinned to the instant, then the same vector ORDER BY runs inside it.
    """
    literal = _as_aost_literal(instant)
    vec_literal = embeddings.to_pgvector(embeddings.embed(query))
    conn = db.get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Statement-level "FROM t AS OF SYSTEM TIME" is valid SQL, but NOT once
            # psycopg2's implicit transaction has already fixed a timestamp -- that
            # raises FeatureNotSupported: "inconsistent AS OF SYSTEM TIME timestamp".
            # Pin the transaction instead, exactly as belief_state_at() does, and then
            # run an ordinary query inside it. Same result, and it composes with the
            # vector ORDER BY.
            cur.execute(f"SET TRANSACTION AS OF SYSTEM TIME '{literal}'")
            cur.execute(
                """
                SELECT memory_id::string AS memory_id, content, entity, attribute_key,
                       attribute_value, trust_tier, confidence,
                       embedding <=> %s AS distance
                FROM agent_memories
                WHERE tenant_id = %s AND verdict = 'accepted'
                ORDER BY embedding <=> %s
                LIMIT %s
                """,
                (vec_literal, tenant_id, vec_literal, k),
            )
            rows = [dict(r) for r in cur.fetchall()]
        conn.commit()
        return rows
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        if _is_gc_error(exc):
            raise GCWindowExceeded(f"Cannot replay {literal}: outside the GC window.") from exc
        raise
    finally:
        db.put_conn(conn)


def belief_diff(tenant_id: str, instant: Any, *, limit: int = 500) -> list[dict]:
    """What changed between ``instant`` and now.

    Two pinned reads, diffed in the application, because AOST cannot appear in a
    subquery. Each row is marked added / removed / changed / unchanged.
    """
    then = {r["memory_id"]: r for r in belief_state_at(tenant_id, instant, limit=limit)}

    conn = db.get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT memory_id::string AS memory_id, content, entity, attribute_key,
                       attribute_value, trust_tier, confidence, source
                FROM agent_memories
                WHERE tenant_id = %s AND verdict = 'accepted'
                ORDER BY created_at LIMIT %s
                """,
                (tenant_id, limit),
            )
            now = {r["memory_id"]: dict(r) for r in cur.fetchall()}
        conn.commit()
    finally:
        db.put_conn(conn)

    out: list[dict] = []
    for mid in sorted(set(then) | set(now)):
        before, after = then.get(mid), now.get(mid)
        if before is None:
            out.append({**after, "delta": "added"})
        elif after is None:
            out.append({**before, "delta": "removed"})
        elif before.get("trust_tier") != after.get("trust_tier"):
            out.append({**after, "delta": "changed", "was_trust_tier": before.get("trust_tier")})
        else:
            out.append({**after, "delta": "unchanged"})
    return out


def cross_examine(tenant_id: str, decision_id: str) -> dict:
    """The headline operation: what did the agent know when it made this call?"""
    conn = db.get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT decision_id::string AS decision_id, action, rationale, decided_at,
                       outcome, consulted_memory_ids::STRING[] AS consulted_memory_ids,
                       produced_memory_ids::STRING[] AS produced_memory_ids
                FROM agent_decisions WHERE decision_id = %s AND tenant_id = %s
                """,
                (decision_id, tenant_id),
            )
            decision = cur.fetchone()
        conn.commit()
    finally:
        db.put_conn(conn)

    if decision is None:
        raise ValueError(f"no such decision: {decision_id}")

    decision = dict(decision)
    at_the_time = belief_state_at(tenant_id, decision["decided_at"])
    changes = [d for d in belief_diff(tenant_id, decision["decided_at"]) if d["delta"] != "unchanged"]

    return {
        "decision": decision,
        "believed_at_decision_time": at_the_time,
        "changed_since": changes,
        "consulted": [str(m) for m in (decision["consulted_memory_ids"] or [])],
    }


__all__ = [
    "GCWindowExceeded",
    "belief_diff",
    "belief_state_at",
    "cross_examine",
    "gc_window_seconds",
    "recall_as_of",
]
