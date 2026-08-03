# SPDX-License-Identifier: Apache-2.0
"""The outcome gate: granting standing to memories that survived contact with reality.

This is the one thing Standing does that shipped agent-memory products do not.

Every mainstream system decides how much to trust a stored memory using one of three
signals: recency (the newest write wins), source authority (a runbook outranks Slack), or
model self-consistency (ask the model whether it still believes itself). All three are
the system grading its own homework.

Standing uses a fourth: did acting on this memory actually work? A memory produced by a
decision is promoted to ``verified`` only when an external, non-model signal confirms the
decision succeeded -- PagerDuty resolving the incident, a monitored metric recovering, or
a named human signing off. If the action was rolled back or turned out to be a false
positive, the memories it produced are demoted to ``disputed``.

INVARIANT, and it is load-bearing: **no model call may occur on this path.** This module
imports no model client -- its entire import list is ``typing``, ``psycopg2.extras`` and
``. db`` -- and ``assert_no_model_calls()`` is invoked at the top of every
``grant_standing()`` call, so the invariant is checked on the live path rather than
asserted in prose. If a model could influence promotion, the claim above would be false
and the product would collapse into the self-consistency bucket with everything else.

The check is structural and deliberately modest: it proves no model client is reachable
through this module's own namespace. It cannot prove the absence of a call in every
conceivable import graph. It does catch the realistic regression -- somebody "improving"
promotion by asking a model to weigh the evidence.

What standing does and does not mean. It means "acting on this produced a confirmed good
outcome at least once". It does not mean "true". An incident can resolve for reasons
unrelated to the action taken, so this is evidence, not proof -- see the README's Limits.
"""

from __future__ import annotations

from typing import Any

from psycopg2.extras import RealDictCursor

from . import db

VALID_OUTCOMES = {"success", "rollback", "false_positive"}
VALID_SOURCES = {"pagerduty", "metric", "human"}

VERIFIED = "verified"
DISPUTED = "disputed"
UNCONFIRMED = "unconfirmed"

# Counts model calls attributable to this module. It is always zero; it is reported
# rather than asserted silently, so the demo can display the number on screen.
_model_calls = 0


class OutcomeRejected(ValueError):
    """The supplied evidence is not admissible as an external outcome signal."""


def _validate(evidence: dict[str, Any]) -> tuple[str, str, float | None, str]:
    """Evidence must be externally attributable. Vague confirmations are refused."""
    if not isinstance(evidence, dict):
        raise OutcomeRejected("evidence must be a dict")

    outcome = evidence.get("outcome")
    if outcome not in VALID_OUTCOMES:
        raise OutcomeRejected(f"outcome must be one of {sorted(VALID_OUTCOMES)}, got {outcome!r}")

    source = evidence.get("source")
    if source not in VALID_SOURCES:
        raise OutcomeRejected(
            f"source must be one of {sorted(VALID_SOURCES)}, got {source!r}. "
            "A model's own assessment is deliberately not an accepted source."
        )

    external_ref = evidence.get("external_ref")
    if not external_ref or not str(external_ref).strip():
        raise OutcomeRejected(
            "external_ref is required: an outcome with no external identifier "
            "(incident id, metric query, or the name of the human who signed off) "
            "is not externally verifiable and cannot grant standing."
        )

    delta = evidence.get("metric_delta")
    if delta is not None:
        delta = float(delta)
    if source == "metric" and delta is None:
        raise OutcomeRejected("source='metric' requires metric_delta")

    return outcome, source, delta, str(external_ref).strip()


def _apply(conn, decision_id: str, outcome: str, source: str, delta: float | None, ref: str) -> dict:
    """Record the outcome and move trust tiers, atomically with it.

    Recording the outcome and re-tiering the memories it produced happen in ONE
    serializable transaction. If they could drift apart, a memory could carry standing
    that no recorded outcome justifies -- which is precisely the failure this project is
    about.
    """
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT decision_id::string AS decision_id, tenant_id::string AS tenant_id,
                   produced_memory_ids::STRING[] AS produced_memory_ids,
                   outcome AS existing_outcome
            FROM agent_decisions WHERE decision_id = %s
            """,
            (decision_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise OutcomeRejected(f"no such decision: {decision_id}")
        if row["existing_outcome"] is not None:
            raise OutcomeRejected(
                f"decision {decision_id} already has outcome {row['existing_outcome']!r}; "
                "outcomes are recorded once and never rewritten"
            )

        # NOTE: the column is cast to STRING[] in the query above on purpose. CockroachDB
        # returns UUID[] with an OID psycopg2 does not map, so an uncast read arrives as
        # the raw literal '{uuid,uuid}' -- and iterating that yields CHARACTERS, not ids.
        # Casting server-side is the fix; every UUID[] read in this codebase does it.
        produced = [str(m) for m in (row["produced_memory_ids"] or [])]

        cur.execute(
            """
            UPDATE agent_decisions
            SET outcome = %s, outcome_confirmed_at = now(), outcome_metric_delta = %s
            WHERE decision_id = %s
            """,
            (outcome, delta, decision_id),
        )

        promoted: list[str] = []
        demoted: list[str] = []

        if produced:
            new_tier = VERIFIED if outcome == "success" else DISPUTED
            cur.execute(
                """
                UPDATE agent_memories
                SET trust_tier = %s,
                    confidence = CASE WHEN %s = 'verified'
                                      THEN least(1.0, confidence + 0.3)
                                      ELSE greatest(0.0, confidence - 0.3) END,
                    verdict_set_at = now()
                WHERE memory_id = ANY(%s::UUID[]) AND trust_tier = %s
                RETURNING memory_id::string
                """,
                (new_tier, new_tier, produced, UNCONFIRMED),
            )
            touched = [r["memory_id"] for r in cur.fetchall()]
            if outcome == "success":
                promoted = touched
            else:
                demoted = touched

        cur.execute(
            """
            INSERT INTO tool_audit (tenant_id, actor, tool_name, tool_kind, risk,
                                    request_id, decision_id, result_kind)
            VALUES (%s, %s, 'grant_standing', 'app', 'medium', gen_random_uuid(), %s, 'ok')
            """,
            (row["tenant_id"], f"{source}:{ref}", decision_id),
        )

    return {
        "decision_id": decision_id,
        "outcome": outcome,
        "source": source,
        "external_ref": ref,
        "metric_delta": delta,
        "promoted": promoted,
        "demoted": demoted,
        "model_calls": _model_calls,
    }


def grant_standing(decision_id: str, evidence: dict[str, Any]) -> dict:
    """Apply an external outcome to a decision and re-tier the memories it produced.

    ``evidence`` requires ``outcome``, ``source`` and ``external_ref``; ``metric_delta``
    is required when ``source='metric'``. Returns which memories were promoted or
    demoted, and ``model_calls``, which is always 0.
    """
    assert_no_model_calls()  # checked on the live path, not merely documented
    outcome, source, delta, ref = _validate(evidence)
    result = db.retry_serializable(_apply, decision_id, outcome, source, delta, ref)
    assert result["model_calls"] == 0, "the promotion path must never call a model"
    return result


def assert_no_model_calls() -> None:
    """Fail loudly if anything model-shaped became reachable from this module.

    Called at the top of every ``grant_standing()``. Cheap enough to run on the live
    path (a set intersection over module globals), which is the point: a safeguard that
    only runs in a test nobody runs is not a safeguard.
    """
    forbidden = {"boto3", "embeddings", "bedrock", "converse", "invoke_model", "openai", "anthropic"}
    found = sorted(name for name in globals() if name.lower() in forbidden)
    if found:
        raise AssertionError(
            f"model-carrying names reachable from trust.py: {found}. "
            "The outcome gate must stay model-free; that is the product's central claim."
        )


def model_calls() -> int:
    return _model_calls


__all__ = [
    "DISPUTED",
    "OutcomeRejected",
    "UNCONFIRMED",
    "VALID_OUTCOMES",
    "VALID_SOURCES",
    "VERIFIED",
    "assert_no_model_calls",
    "grant_standing",
    "model_calls",
]
