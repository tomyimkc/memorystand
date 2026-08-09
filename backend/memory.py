# SPDX-License-Identifier: Apache-2.0
"""Admission control: deciding which memories are allowed to be recalled at all.

The shape of this module is the answer to a specific race. Adjudicating a new memory
needs two expensive, non-transactional things -- an embedding and (optionally) a model
call -- and it is a real anti-pattern to hold a SERIALIZABLE transaction open across
network I/O. But moving the check outside the transaction opens a time-of-check /
time-of-use hole: the neighbour set can change between the check and the commit, so two
concurrent contradictory writes could both be admitted.

The resolution used here:

  * outside the transaction: embed the content, gather context, and optionally ask a
    model for advisory reasoning. None of this decides anything.
  * inside the transaction: re-run the deterministic conflict query against a fresh,
    serializable read and make the actual admission decision there.

Because the deciding read happens inside the transaction, CockroachDB's serializable
conflict detection covers it: a concurrent write that would invalidate the decision
forces a SQLSTATE 40001 retry, and the retry re-decides against the new state. The model
never gets a vote on admission -- it can only annotate.

Limits, stated plainly. This is a *filter*, not a truth oracle. It catches a claim that
contradicts something already admitted. A false claim that contradicts nothing is
admitted. That bounds persisted error; it does not eliminate it.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Iterable, Sequence

from psycopg2.extras import RealDictCursor

from . import db, embeddings

# Cosine distance below which two memories are considered to be talking about the same
# thing. Tuned against the seed corpus; exposed so it can be justified rather than magic.
NEAR_DUPLICATE_DISTANCE = 0.15
NEIGHBOUR_K = 5

ACCEPTED = "accepted"
HELD = "quarantined"  # SQL enum value; user-facing copy says "held for review"
SUPERSEDED = "superseded"


class Verdict:
    """What admission control decided, and why."""

    def __init__(
        self,
        verdict: str,
        reasons: list[str],
        checked_against: list[str],
        supersedes: str | None = None,
    ) -> None:
        self.verdict = verdict
        self.reasons = reasons
        self.checked_against = checked_against
        self.supersedes = supersedes


def _neighbours(conn, tenant_id: str, vec_literal: str, k: int) -> list[dict]:
    """Nearest admitted memories, by cosine distance. Uses the prefix-partitioned index."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
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
        return [dict(r) for r in cur.fetchall()]


def _existing_identical(
    conn, tenant_id: str, entity: str | None, attribute_key: str | None, attribute_value: str | None
) -> dict | None:
    """An already-admitted memory asserting the SAME value for the same entity+attribute.

    ``_hard_conflicts`` deliberately excludes these (``attribute_value IS DISTINCT FROM``),
    and until now nothing else looked at them -- so re-asserting an identical fact created a
    brand new row every time. The live demo tenant had accumulated eleven copies of
    "payments-service.reads_from_table = orders_v2", which surfaced as five identical rows at
    identical distance in a recall result. Unbounded duplication of a fact the store already
    holds is a real defect: it wastes the recall window, and it makes the top-k look broken
    to anyone reading it.
    """
    if not (entity and attribute_key and attribute_value):
        return None
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT memory_id::string AS memory_id, attribute_value, trust_tier, confidence,
                   source, created_at
            FROM agent_memories
            WHERE tenant_id = %s AND entity = %s AND attribute_key = %s
              AND verdict = 'accepted' AND attribute_value IS NOT DISTINCT FROM %s
            ORDER BY created_at ASC
            LIMIT 1
            """,
            (tenant_id, entity, attribute_key, attribute_value),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def _hard_conflicts(
    conn, tenant_id: str, entity: str | None, attribute_key: str | None, attribute_value: str | None
) -> list[dict]:
    """Admitted memories asserting a DIFFERENT value for the same entity+attribute.

    Deterministic, index-backed, and the only thing that can cause a rejection. Scoped by
    entity as well as attribute_key so 'reads_from_table' for one service is never
    compared against the same attribute on an unrelated service.
    """
    if not (entity and attribute_key and attribute_value):
        return []
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT memory_id::string AS memory_id, attribute_value, trust_tier,
                   confidence, source, created_at
            FROM agent_memories
            WHERE tenant_id = %s AND entity = %s AND attribute_key = %s
              AND verdict = 'accepted' AND attribute_value IS DISTINCT FROM %s
            """,
            (tenant_id, entity, attribute_key, attribute_value),
        )
        return [dict(r) for r in cur.fetchall()]


def _adjudicate(
    conflicts: Sequence[dict], neighbours: Sequence[dict], source: str | None
) -> Verdict:
    """The decision rule. Pure, deterministic, and called INSIDE the transaction.

    A new claim that contradicts an admitted one is held for review, unless the admitted
    claim is merely 'unconfirmed' and the new one comes from a higher-authority source --
    in which case the new claim supersedes it. A memory that has earned standing is never
    silently overridden: something in the real world has already backed it, so a
    contradicting claim is held for a human instead.

    "Earned standing" means ``attested`` OR ``verified``. Both had an external outcome
    reported against the decision that produced them; they differ in whether this
    deployment could independently re-check that outcome (see backend/evidence.py). The
    protection is about an outcome having been reported at all, so both rungs get it --
    but the held-for-review message names which rung it was, because "a human said it
    worked" and "CloudWatch confirmed it" are different strengths of reason and the
    reviewer should not have to guess which one they are looking at.
    """
    checked = [c["memory_id"] for c in conflicts] + [n["memory_id"] for n in neighbours]

    standing_conflicts = [c for c in conflicts if c["trust_tier"] in ("verified", "attested")]
    if standing_conflicts:
        held = standing_conflicts[0]
        how = (
            "independently confirmed against the external system of record"
            if held["trust_tier"] == "verified"
            else "reported by an external outcome, but not independently re-checked"
        )
        return Verdict(
            HELD,
            [
                "contradicts a memory that has earned standing "
                f"(memory {held['memory_id']} asserts {held['attribute_value']!r}; "
                f"trust_tier={held['trust_tier']}, {how})"
            ],
            checked,
        )

    if conflicts:
        target = conflicts[0]
        if _authority(source) > _authority(target.get("source")):
            return Verdict(
                ACCEPTED,
                [
                    f"supersedes {target['memory_id']} "
                    f"({target['attribute_value']!r} -> new value) on source authority"
                ],
                checked,
                supersedes=target["memory_id"],
            )
        return Verdict(
            HELD,
            [
                f"contradicts memory {target['memory_id']} which asserts "
                f"{target['attribute_value']!r}; sources rank equal, so a human decides"
            ],
            checked,
        )

    near = [n for n in neighbours if n["distance"] is not None and n["distance"] < NEAR_DUPLICATE_DISTANCE]
    if near:
        return Verdict(
            ACCEPTED,
            [f"near-duplicate of {near[0]['memory_id']} (distance {near[0]['distance']:.3f}); admitted"],
            checked,
        )

    return Verdict(ACCEPTED, ["no contradiction found among admitted memories"], checked)


# Source authority ranking. Deliberately explicit and small: an ordering nobody can read
# is an ordering nobody can audit.
_AUTHORITY = {"human": 40, "postmortem": 30, "runbook": 20, "pagerduty": 15, "slack": 5}


def _authority(source: str | None) -> int:
    if not source:
        return 0
    return _AUTHORITY.get(source.split(":", 1)[0].strip().lower(), 0)


def _commit(
    conn,
    *,
    tenant_id: str,
    agent_id: str,
    task_id: str | None,
    memory_type: str,
    entity: str | None,
    attribute_key: str | None,
    attribute_value: str | None,
    content: str,
    structured_data: Any,
    source: str | None,
    vec_literal: str,
) -> dict:
    """Decide and write, atomically. Runs under ``db.retry_serializable``."""
    # Fresh reads INSIDE the transaction -- this is what closes the TOCTOU hole. Both are
    # part of this transaction's read set, so a concurrent conflicting write forces 40001.
    # Corroboration, not duplication. If the store already holds this exact claim, the honest
    # response is to point at the memory that already says it rather than write a second row.
    #
    # DELIBERATELY WITHOUT RAISING ITS CONFIDENCE. Repetition is not evidence -- that is the
    # whole premise of this project, and benchmarks/poisoning_benchmark.py has a `tier_climb`
    # attack built precisely on resubmitting the same false claim to accumulate standing. It
    # currently escalates nothing, and rewarding repeats here would open exactly that door.
    # Only an external outcome moves a memory up the ladder.
    identical = _existing_identical(conn, tenant_id, entity, attribute_key, attribute_value)
    if identical:
        return {
            "memory_id": identical["memory_id"],
            "verdict": ACCEPTED,
            "verdict_reasons": [
                "identical claim already on record: this is corroboration, not a new memory. "
                "Confidence is unchanged -- repetition is not evidence, only an external "
                "outcome moves a memory up the trust ladder."
            ],
            "checked_against": [identical["memory_id"]],
            "superseded": None,
            "corroborated": True,
        }

    conflicts = _hard_conflicts(conn, tenant_id, entity, attribute_key, attribute_value)
    neighbours = _neighbours(conn, tenant_id, vec_literal, NEIGHBOUR_K)
    decision = _adjudicate(conflicts, neighbours, source)

    with conn.cursor() as cur:
        if decision.supersedes:
            cur.execute(
                """
                UPDATE agent_memories
                SET verdict = 'superseded', verdict_set_at = now()
                WHERE memory_id = %s AND tenant_id = %s AND verdict = 'accepted'
                """,
                (decision.supersedes, tenant_id),
            )
            if cur.rowcount != 1:
                # Someone else moved it first. Abandon the supersede and hold instead;
                # the retry path would otherwise silently double-supersede.
                decision = Verdict(
                    HELD,
                    ["target of supersede changed concurrently; held for review"],
                    decision.checked_against,
                )

        cur.execute(
            """
            INSERT INTO agent_memories
                (tenant_id, agent_id, task_id, memory_type, entity, attribute_key,
                 attribute_value, content, structured_data, source, verdict,
                 verdict_reasons, checked_against, embedding)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING memory_id::string
            """,
            (
                tenant_id,
                agent_id,
                task_id,
                memory_type,
                entity,
                attribute_key,
                attribute_value,
                content,
                json.dumps(structured_data) if structured_data is not None else None,
                source,
                decision.verdict,
                decision.reasons,
                decision.checked_against,
                vec_literal,
            ),
        )
        memory_id = cur.fetchone()[0]

    return {
        "memory_id": memory_id,
        "verdict": decision.verdict,
        "verdict_reasons": decision.reasons,
        "checked_against": decision.checked_against,
        "superseded": decision.supersedes,
    }


def remember(
    tenant_id: str,
    agent_id: str,
    content: str,
    *,
    entity: str | None = None,
    attribute_key: str | None = None,
    attribute_value: str | None = None,
    memory_type: str = "semantic",
    source: str | None = None,
    task_id: str | None = None,
    structured_data: Any = None,
) -> dict:
    """Submit a memory for admission. Returns the verdict and the reasoning behind it."""
    if memory_type not in {"episodic", "semantic", "task_state", "tool_call"}:
        raise ValueError(f"unknown memory_type: {memory_type!r}")

    # Expensive, non-transactional work happens here, before any transaction opens.
    vec_literal = embeddings.to_pgvector(embeddings.embed(content))

    return db.retry_serializable(
        _commit,
        tenant_id=tenant_id,
        agent_id=agent_id,
        task_id=task_id,
        memory_type=memory_type,
        entity=entity,
        attribute_key=attribute_key,
        attribute_value=attribute_value,
        content=content,
        structured_data=structured_data,
        source=source,
        vec_literal=vec_literal,
    )


def recall(tenant_id: str, agent_id: str | None, query: str, k: int = 5) -> list[dict]:
    """Semantic recall over ADMITTED memories only.

    The ``verdict = 'accepted'`` predicate is the invariant this whole project exists to
    protect: a held or superseded memory must never reach the agent. It is asserted here
    and covered by a test, because shipping a recall path that leaks a rejected memory
    would falsify the product's central claim.
    """
    vec_literal = embeddings.to_pgvector(embeddings.embed(query))
    conn = db.get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT memory_id::string AS memory_id, content, entity, attribute_key,
                       attribute_value, trust_tier, confidence, source, verdict,
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
    finally:
        db.put_conn(conn)

    leaked = [r for r in rows if r["verdict"] != ACCEPTED]
    if leaked:
        raise AssertionError(f"recall returned non-admitted memories: {[r['memory_id'] for r in leaked]}")
    return rows


def get(tenant_id: str, memory_id: str) -> dict | None:
    conn = db.get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT memory_id::string AS memory_id, content, entity, attribute_key,
                       attribute_value, verdict, verdict_reasons, trust_tier, confidence,
                       source, supersedes::string AS supersedes, created_at
                FROM agent_memories WHERE tenant_id = %s AND memory_id = %s
                """,
                (tenant_id, memory_id),
            )
            row = cur.fetchone()
        conn.commit()
        return dict(row) if row else None
    finally:
        db.put_conn(conn)


def new_id() -> str:
    return str(uuid.uuid4())


__all__ = [
    "ACCEPTED",
    "HELD",
    "NEAR_DUPLICATE_DISTANCE",
    "SUPERSEDED",
    "Verdict",
    "get",
    "new_id",
    "recall",
    "remember",
]
