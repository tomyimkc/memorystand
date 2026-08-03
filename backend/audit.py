# SPDX-License-Identifier: Apache-2.0
"""Audit trail as a queryable SQL table rather than a log file to be grepped.

``tool_audit`` lives in the same database as the memories, which means a judge (or an
operator, or an incident reviewer) can read the audit trail with the same read-only
credential they use for everything else -- including through the CockroachDB Cloud
Managed MCP Server, with no application code in the path. Native row-level TTL bounds
its growth, so there is no cron job to forget to run.
"""

from __future__ import annotations

import functools
import uuid
from typing import Any, Callable

from psycopg2.extras import RealDictCursor

from . import db

RISK_LEVELS = {"low", "medium", "high"}


def record(
    tenant_id: str | None,
    actor: str,
    tool_name: str,
    *,
    risk: str = "low",
    tool_kind: str = "app",
    decision_id: str | None = None,
    result_kind: str = "ok",
    request_id: str | None = None,
) -> str:
    """Append one audit row. Never raises into the caller's path."""
    if risk not in RISK_LEVELS:
        raise ValueError(f"risk must be one of {sorted(RISK_LEVELS)}")
    rid = request_id or str(uuid.uuid4())
    conn = db.get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO tool_audit (tenant_id, actor, tool_name, tool_kind, risk,
                                        request_id, decision_id, result_kind)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (tenant_id, actor, tool_name, tool_kind, risk, rid, decision_id, result_kind),
            )
        conn.commit()
    except Exception:  # noqa: BLE001
        # An audit write must never take down the operation it is describing. It is a
        # record, not a gate -- the gates are in memory.py and trust.py.
        conn.rollback()
    finally:
        db.put_conn(conn)
    return rid


def audited(tool_name: str, risk: str = "low", tool_kind: str = "app") -> Callable:
    """Decorator recording one ``tool_audit`` row per call, with its result kind."""

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            tenant_id = kwargs.get("tenant_id") or (args[0] if args else None)
            actor = kwargs.get("actor", "app")
            try:
                result = fn(*args, **kwargs)
            except Exception:
                record(tenant_id, actor, tool_name, risk=risk, tool_kind=tool_kind, result_kind="denied")
                raise
            record(tenant_id, actor, tool_name, risk=risk, tool_kind=tool_kind, result_kind="ok")
            return result

        return wrapper

    return decorator


def trail(tenant_id: str, decision_id: str | None = None, limit: int = 100) -> list[dict]:
    conn = db.get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if decision_id:
                cur.execute(
                    """
                    SELECT ts, actor, tool_name, tool_kind, risk, result_kind,
                           decision_id::string AS decision_id
                    FROM tool_audit WHERE tenant_id = %s AND decision_id = %s
                    ORDER BY ts LIMIT %s
                    """,
                    (tenant_id, decision_id, limit),
                )
            else:
                cur.execute(
                    """
                    SELECT ts, actor, tool_name, tool_kind, risk, result_kind,
                           decision_id::string AS decision_id
                    FROM tool_audit WHERE tenant_id = %s ORDER BY ts DESC LIMIT %s
                    """,
                    (tenant_id, limit),
                )
            rows = [dict(r) for r in cur.fetchall()]
        conn.commit()
        return rows
    finally:
        db.put_conn(conn)


__all__ = ["audited", "record", "trail"]
