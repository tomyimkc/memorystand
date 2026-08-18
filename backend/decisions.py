# SPDX-License-Identifier: Apache-2.0
"""Decisions: what the agent did, and exactly which memories it used to get there.

Two id lists, deliberately separate:

  ``consulted_memory_ids`` -- what recall returned and the agent reasoned over.
  ``produced_memory_ids``  -- what this decision itself wrote back.

Only *produced* memories are re-tiered by the outcome gate. A memory that was merely
consulted is not promoted when the action works, because it did not necessarily
contribute; and it is not demoted when the action fails, because a correct fact can be
consulted by a bad plan. Re-examining consulted memories after a failure is a real
open problem and is listed as a roadmap item rather than fudged.

``requires_approval`` with a NULL ``approved_by`` is a held action: recorded, not taken.
"""

from __future__ import annotations

import uuid
from typing import Any, Sequence

from psycopg2.extras import RealDictCursor

from . import db


class InvalidMemoryReference(ValueError):
    """A decision named a memory outside its own admitted tenant memory set."""


def _normalise_memory_ids(values: Sequence[str], field: str) -> list[str]:
    normalised: list[str] = []
    for value in values:
        try:
            normalised.append(str(uuid.UUID(str(value))))
        except (ValueError, TypeError, AttributeError) as exc:
            raise InvalidMemoryReference(f"{field} contains an invalid memory id") from exc
    return normalised


def _normalise_optional_uuid(value: str | None, field: str) -> str | None:
    if value is None or not str(value).strip():
        return None
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise InvalidMemoryReference(f"{field} must be a UUID") from exc


def _validate_memory_ids(cur, tenant_id: str, memory_ids: Sequence[str], field: str) -> None:
    """Every decision reference must name an admitted memory owned by the same tenant."""
    unique = list(dict.fromkeys(memory_ids))
    if not unique:
        return
    cur.execute(
        """
        SELECT memory_id::string AS memory_id
        FROM agent_memories
        WHERE tenant_id = %s
          AND verdict = 'accepted'
          AND memory_id = ANY(%s::UUID[])
        """,
        (tenant_id, unique),
    )
    found = {row["memory_id"] for row in cur.fetchall()}
    missing = [mid for mid in unique if mid not in found]
    if missing:
        raise InvalidMemoryReference(
            f"{field} contains {len(missing)} memory id(s) that are not admitted memories "
            "owned by this tenant"
        )


def _insert(
    conn,
    *,
    tenant_id: str,
    agent_id: str,
    action: str,
    rationale: str | None,
    consulted: Sequence[str],
    produced: Sequence[str],
    requires_approval: bool,
    task_id: str | None,
    query_text: str | None = None,
    recall_k: int | None = None,
    target_entity: str | None = None,
) -> dict:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        _validate_memory_ids(cur, tenant_id, consulted, "consulted_memory_ids")
        _validate_memory_ids(cur, tenant_id, produced, "produced_memory_ids")
        cur.execute(
            """
            INSERT INTO agent_decisions
                (tenant_id, agent_id, task_id, action, rationale,
                 consulted_memory_ids, produced_memory_ids, requires_approval,
                 query_text, recall_k, target_entity)
            VALUES (%s,%s,%s,%s,%s,%s::UUID[],%s::UUID[],%s,%s,%s,%s)
            RETURNING decision_id::string AS decision_id, decided_at, requires_approval
            """,
            (
                tenant_id,
                agent_id,
                task_id,
                action,
                rationale,
                list(consulted),
                list(produced),
                requires_approval,
                query_text,
                recall_k,
                target_entity,
            ),
        )
        row = dict(cur.fetchone())

        cur.execute(
            """
            INSERT INTO tool_audit (tenant_id, actor, tool_name, tool_kind, risk,
                                    request_id, decision_id, result_kind)
            VALUES (%s,%s,'decide','app',%s, gen_random_uuid(), %s, %s)
            """,
            (
                tenant_id,
                f"agent:{agent_id}",
                "high" if requires_approval else "medium",
                row["decision_id"],
                "held" if requires_approval else "ok",
            ),
        )
    return row


def decide(
    tenant_id: str,
    agent_id: str,
    action: str,
    rationale: str | None,
    consulted_memory_ids: Sequence[str],
    produced_memory_ids: Sequence[str] = (),
    requires_approval: bool = False,
    task_id: str | None = None,
    query_text: str | None = None,
    recall_k: int | None = None,
    target_entity: str | None = None,
) -> dict:
    """Record an action and its evidential basis.

    ``query_text``/``recall_k`` are the RETRIEVAL RECEIPT: what was actually asked, and how many
    rows were taken. Storing them is what lets ``replay.cross_examine`` re-run the agent's own
    ranked query against the past rather than approximating it with a belief-state dump.
    """
    consulted = _normalise_memory_ids(consulted_memory_ids, "consulted_memory_ids")
    produced = _normalise_memory_ids(produced_memory_ids, "produced_memory_ids")
    task = _normalise_optional_uuid(task_id, "task_id")
    row = db.retry_serializable(
        _insert,
        tenant_id=tenant_id,
        agent_id=agent_id,
        action=action,
        rationale=rationale,
        consulted=consulted,
        produced=produced,
        requires_approval=requires_approval,
        task_id=task,
        query_text=query_text,
        recall_k=recall_k,
        target_entity=target_entity,
    )
    return {
        "decision_id": row["decision_id"],
        "decided_at": row["decided_at"],
        "action": action,
        "target_entity": target_entity,
        "status": "held_for_approval" if row["requires_approval"] else "taken",
        "consulted": consulted,
        "produced": produced,
    }


def get(tenant_id: str, decision_id: str) -> dict | None:
    conn = db.get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT decision_id::string AS decision_id, action, rationale, decided_at,
                       outcome, outcome_confirmed_at, outcome_metric_delta,
                       requires_approval, approved_by,
                       target_entity,
                       consulted_memory_ids::STRING[] AS consulted_memory_ids,
                       produced_memory_ids::STRING[] AS produced_memory_ids
                FROM agent_decisions WHERE tenant_id = %s AND decision_id = %s
                """,
                (tenant_id, decision_id),
            )
            row = cur.fetchone()
        conn.commit()
        return dict(row) if row else None
    finally:
        db.put_conn(conn)


def recent(tenant_id: str, limit: int = 20) -> list[dict]:
    conn = db.get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT decision_id::string AS decision_id, action, decided_at, outcome,
                       requires_approval, approved_by, target_entity
                FROM agent_decisions WHERE tenant_id = %s
                ORDER BY decided_at DESC LIMIT %s
                """,
                (tenant_id, limit),
            )
            rows = [dict(r) for r in cur.fetchall()]
        conn.commit()
        return rows
    finally:
        db.put_conn(conn)


__all__ = ["InvalidMemoryReference", "decide", "get", "recent"]
