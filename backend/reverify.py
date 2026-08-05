# SPDX-License-Identifier: Apache-2.0
"""Trust with a half-life: re-check granted standing against the world, and demote it when
the world stops agreeing.

Until now, trust was a one-shot event. A memory promoted to ``verified`` in March was still
``verified`` in August, because nothing ever asked the system of record again. That is the
obvious hole in an argument built on "reality decides": reality keeps moving after the
promotion. The runbook step that genuinely fixed an incident in March can be false by August
because the service was rearchitected, and the memory would still be sitting at the top of the
trust ladder telling an agent to do it.

Operational folklore is mostly made of things that *were* true. A system that can only ever
promote is a folklore generator with extra steps.

WHAT THIS DOES

A memory's standing is not a fact, it is a claim with an expiry. ``STALE_AFTER_DAYS`` after the
last check, the supporting evidence is re-queried:

  * still confirmed        -> stays ``verified``; the clock resets
  * now contradicted       -> demoted to ``disputed``. The world changed its mind, and so do we
  * no longer checkable    -> demoted ``verified`` -> ``attested``. Not disproved, just no
                              longer independently supported, which is a weaker claim and
                              should be recorded as one
  * never checkable        -> ``attested`` memories (pagerduty, human) stay put. There is no
                              system of record to re-query, so there is nothing to learn by
                              trying, and demoting them on a timer would be theatre

DEMOTION IS NOT DELETION. The memory stays recallable; only its tier moves. That matters
because ``backend/agent.py`` ranks candidate actions by tier, so a decayed memory quietly stops
outranking better-supported ones instead of vanishing and taking its history with it.

ZERO MODEL CALLS, same as the promotion path. This module imports ``evidence`` and ``db`` and
nothing model-shaped; ``trust.assert_no_model_calls()`` is invoked on every sweep so the
invariant is checked here too rather than assumed to be inherited.

RELATION TO PRIOR ART, stated because the project's whole posture is to concede first.
`GLOVE <https://arxiv.org/abs/2601.19249>`_ is the closest work: it actively probes for
mismatches between stored memory and new observations. It differs in the thing that matters
here -- it probes the environment *with the model* and explicitly operates without ground
truth, whereas this re-queries an external system of record and makes no model call. Doyle's
JTMS retracts a belief when its justification fails, which is the same instinct with a
justification supplied by another belief rather than by a metrics store.
"""

from __future__ import annotations

import os
from typing import Any

from psycopg2.extras import RealDictCursor

from . import db, evidence, trust

# How long a granted trust stands before it has to be re-earned. Days, not hours: a metric
# window is noisy over short spans, and re-checking constantly would demote memories on
# ordinary variance rather than on real change.
STALE_AFTER_DAYS = float(os.environ.get("MEMORYSTAND_TRUST_STALE_DAYS", "7"))

# Cap on how many memories one sweep will touch. A sweep is meant to run on a schedule against
# a live cluster; an unbounded one could rewrite the whole table in a single transaction burst
# and contend with real traffic.
BATCH_LIMIT = int(os.environ.get("MEMORYSTAND_REVERIFY_BATCH", "200"))


def _stale_verified(conn, tenant_id: str | None) -> list[dict[str, Any]]:
    """Memories whose `verified` standing is older than the staleness window.

    Only ``verified`` is swept. ``attested`` has, by definition, no re-queryable system of
    record, so a sweep could learn nothing about it; ``disputed`` is already at the bottom;
    ``unconfirmed`` has nothing to lose.
    """
    # Only sweep memories whose decision actually recorded WHAT was checked.
    #
    # Standing granted before migration 002 has no outcome_source or outcome_external_ref,
    # because the columns did not exist. Those memories cannot be re-checked -- and a dry run
    # against the live cluster showed the consequence of missing this: every one of them was
    # queued for demotion to `attested`, punishing legitimately-earned standing for a schema
    # change rather than for anything reality said.
    #
    # "We never wrote down what we checked" is not evidence that the check would now fail. The
    # honest handling is to leave them alone; they will be re-checkable the next time an
    # outcome is recorded against them.
    where = [
        "m.trust_tier = 'verified'",
        "d.outcome_source IS NOT NULL",
        "d.outcome_source <> ''",
    ]
    params: list[Any] = []
    if tenant_id:
        where.append("m.tenant_id = %s")
        params.append(tenant_id)
    where.append(
        "(m.trust_checked_at IS NULL OR m.trust_checked_at < now() - "
        f"INTERVAL '{STALE_AFTER_DAYS} days')"
    )
    params.append(BATCH_LIMIT)

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            f"""
            SELECT m.memory_id::string AS memory_id, m.tenant_id::string AS tenant_id,
                   m.trust_checked_at,
                   d.decision_id::string AS decision_id, d.decided_at,
                   d.outcome, d.outcome_source, d.outcome_external_ref,
                   d.outcome_metric_delta, m.entity
            FROM agent_memories m
            JOIN agent_decisions d
              ON m.memory_id = ANY(d.produced_memory_ids) AND d.tenant_id = m.tenant_id
            WHERE {' AND '.join(where)}
            ORDER BY m.trust_checked_at ASC NULLS FIRST
            LIMIT %s
            """,
            params,
        )
        return [dict(r) for r in cur.fetchall()]


def _demote(conn, memory_id: str, tenant_id: str, tier: str, delta: float) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE agent_memories
            SET trust_tier = %s,
                trust_checked_at = now(),
                confidence = greatest(0.0, least(1.0, confidence + %s)),
                verdict_set_at = now()
            WHERE memory_id = %s AND tenant_id = %s
            """,
            (tier, delta, memory_id, tenant_id),
        )


def _touch(conn, memory_id: str, tenant_id: str) -> None:
    """Standing survived the re-check: reset the clock, leave the tier alone."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE agent_memories SET trust_checked_at = now() "
            "WHERE memory_id = %s AND tenant_id = %s",
            (memory_id, tenant_id),
        )


def sweep(tenant_id: str | None = None, *, dry_run: bool = False) -> dict[str, Any]:
    """Re-check every stale `verified` memory and demote the ones reality no longer supports.

    Returns a summary rather than logging and forgetting, so a scheduler, a test and the demo
    can all assert on the same numbers.
    """
    trust.assert_no_model_calls()

    conn = db.get_conn()
    conn.autocommit = True
    result: dict[str, Any] = {
        "checked": 0,
        "still_verified": 0,
        "demoted_to_attested": 0,
        "demoted_to_disputed": 0,
        "model_calls": 0,
        "stale_after_days": STALE_AFTER_DAYS,
        "dry_run": dry_run,
        "changes": [],
    }
    try:
        for row in _stale_verified(conn, tenant_id):
            result["checked"] += 1
            check = evidence.verify(
                row.get("outcome_source") or "",
                row.get("outcome_external_ref") or "",
                row.get("outcome_metric_delta"),
                row.get("decided_at"),
                entity=row.get("entity"),
            )

            if check.status == evidence.CONFIRMED:
                result["still_verified"] += 1
                if not dry_run:
                    _touch(conn, row["memory_id"], row["tenant_id"])
                continue

            if check.status == evidence.CONTRADICTED:
                tier, delta = trust.DISPUTED, -0.3
                result["demoted_to_disputed"] += 1
            else:
                # UNAVAILABLE or NOT_VERIFIABLE. Not disproved -- just no longer independently
                # supported. `attested` is the honest tier for that, and it is a demotion
                # rather than a hold because the claim being made is genuinely weaker now.
                tier, delta = trust.ATTESTED, -0.1
                result["demoted_to_attested"] += 1

            result["changes"].append(
                {
                    "memory_id": row["memory_id"],
                    "from": "verified",
                    "to": tier,
                    "why": check.detail,
                    "verification": check.status,
                }
            )
            if not dry_run:
                _demote(conn, row["memory_id"], row["tenant_id"], tier, delta)

        return result
    finally:
        # Hand the connection back in the mode the rest of the codebase expects. This sweep runs
        # with autocommit=True (each demote/touch commits on its own, so one sweep does not hold
        # a table-wide transaction against live traffic). Reset it at the leak site rather than
        # rely on db.put_conn's backstop or on psycopg2's pool happening to reset it -- returning
        # a connection in a non-default mode is a hazard even where the current pool masks it.
        conn.autocommit = False
        db.put_conn(conn)


__all__ = ["STALE_AFTER_DAYS", "sweep"]
