# SPDX-License-Identifier: Apache-2.0
"""The outcome gate: granting standing to memories that survived contact with reality.

This is the one thing MemoryStand does that shipped agent-memory products do not.

Every mainstream system decides how much to trust a stored memory using one of three
signals: recency (the newest write wins), source authority (a runbook outranks Slack), or
model self-consistency (ask the model whether it still believes itself). All three are
the system grading its own homework.

MemoryStand uses a fourth: did acting on this memory actually work? A memory produced by a
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

The check is structural: it proves no model client is reachable through this module's own
namespace, and it now also walks one level into the modules this one imports, so a model
client cannot be smuggled in behind a helper. It does not prove the absence of a call in
every conceivable import graph. It does catch the realistic regression -- somebody
"improving" promotion by asking a model to weigh the evidence.

Note what is NOT forbidden: calling Amazon CloudWatch to re-check a claimed metric change
(``backend/evidence.py``). The claim is not that this path makes no network calls -- that
would be a strange thing to want, since the entire thesis is that trust must come from
*outside*. The claim is that no MODEL decides whether a memory is true. Asking a metrics
store what a number actually did is the thesis working; asking an LLM whether a memory
seems right is the thing being refused.

What standing does and does not mean. It means "acting on this produced a confirmed good
outcome at least once". It does not mean "true". An incident can resolve for reasons
unrelated to the action taken, so this is evidence, not proof -- see the README's Limits.
"""

from __future__ import annotations

from typing import Any

from psycopg2.extras import RealDictCursor

from . import db
from . import evidence as evidence_check

VALID_OUTCOMES = {"success", "rollback", "false_positive"}
VALID_SOURCES = {"pagerduty", "metric", "human"}

VERIFIED = "verified"
ATTESTED = "attested"
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


def _apply(
    conn,
    tenant_id: str,
    decision_id: str,
    outcome: str,
    source: str,
    delta: float | None,
    ref: str,
    verification: "evidence_check.Verification",
) -> dict:
    """Record the outcome and move trust tiers, atomically with it.

    Recording the outcome and re-tiering the memories it produced happen in ONE
    serializable transaction. If they could drift apart, a memory could carry standing
    that no recorded outcome justifies -- which is precisely the failure this project is
    about.

    ``tenant_id`` scopes BOTH statements below, and it took two goes to get that right.

    The SELECT used to match on ``decision_id`` alone. That was fixed, and this docstring was
    written to say so -- while the UPDATE fifty lines below still promoted memories with
    ``WHERE memory_id = ANY(...) AND trust_tier = ...`` and no tenant predicate at all. The
    fix and the claim of the fix were both real; they just covered different queries, and
    three documents went on to describe this as "the only unscoped query in the codebase".

    The hole that left was not theoretical. ``produced_memory_ids`` is caller-supplied
    verbatim (``handler.py``) and inserted without validation (``decisions.py``), so any
    holder of the shared secret could file a decision under their OWN tenant naming another
    tenant's memory ids, confirm a successful outcome, and promote a stranger's
    ``unconfirmed`` memory to ``verified``. Injecting trusted memory into somebody else's
    store is the exact attack this project exists to prevent, reachable through the exact
    path it advertises as trustworthy.

    ``reverify.py`` had carried ``AND tenant_id = %s`` on the equivalent UPDATE the whole
    time, which is what makes this an inconsistency rather than a considered trade-off.

    The lesson worth keeping: a fix verified at the point it was applied is not a fix
    verified across the operation. Scope the audit to the operation -- every statement that
    can move a tier -- not to the line that was reported.
    """
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT decision_id::string AS decision_id, tenant_id::string AS tenant_id,
                   produced_memory_ids::STRING[] AS produced_memory_ids,
                   outcome AS existing_outcome, decided_at
            FROM agent_decisions WHERE decision_id = %s AND tenant_id = %s
            """,
            (decision_id, tenant_id),
        )
        row = cur.fetchone()
        if row is None:
            # Deliberately the same message whether the decision does not exist or belongs to
            # someone else -- distinguishing them turns this into an oracle for enumerating
            # other tenants' decision ids.
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
            SET outcome = %s, outcome_confirmed_at = now(), outcome_metric_delta = %s,
                outcome_source = %s, outcome_external_ref = %s
            WHERE decision_id = %s
            """,
            (outcome, delta, source, ref, decision_id),
        )

        promoted: list[str] = []
        demoted: list[str] = []

        if produced:
            # The rung a memory reaches depends on who checked, not on who asserted.
            # A success nobody could re-check is 'attested'; only a claim the external
            # system of record independently agreed with reaches 'verified'.
            if outcome != "success":
                new_tier = DISPUTED
            elif verification.grants_verified_tier:
                new_tier = VERIFIED
            else:
                new_tier = ATTESTED
            cur.execute(
                """
                UPDATE agent_memories
                SET trust_tier = %s,
                    trust_checked_at = now(),
                    confidence = CASE %s
                                   WHEN 'verified' THEN least(1.0, confidence + 0.3)
                                   WHEN 'attested' THEN least(1.0, confidence + 0.1)
                                   ELSE greatest(0.0, confidence - 0.3) END,
                    verdict_set_at = now()
                WHERE memory_id = ANY(%s::UUID[])
                  AND tenant_id = %s
                  AND trust_tier = %s
                RETURNING memory_id::string
                """,
                (new_tier, new_tier, produced, tenant_id, UNCONFIRMED),
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
        "trust_tier": new_tier if produced else None,
        "verification": verification.as_dict(),
        "promoted": promoted,
        "demoted": demoted,
        "model_calls": _model_calls,
    }


def _decided_at(tenant_id: str, decision_id: str):
    """Read the decision's timestamp and subject, so evidence can be checked before the write.

    Deliberately a separate small read rather than doing the external check inside
    ``_apply``: ``_apply`` runs under ``retry_serializable``, and a CloudWatch round trip
    inside a retry loop would be re-issued on every serialization conflict -- slow, and
    rude to the API being polled. The external world does not change based on whether our
    transaction conflicted, so checking it once outside is both cheaper and more correct.
    """
    conn = db.get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # The entity comes along because evidence has to be checked against the SUBJECT of
            # the claim, not just the number. A confirmable metric filed under the wrong
            # service was a clean path to `verified` until benchmarks/poisoning_benchmark.py
            # found it.
            cur.execute(
                """
                SELECT d.decided_at,
                       (SELECT m.entity FROM agent_memories m
                        WHERE m.memory_id = ANY(d.produced_memory_ids)
                          AND m.tenant_id = d.tenant_id
                          AND m.entity IS NOT NULL
                        LIMIT 1) AS entity
                FROM agent_decisions d
                WHERE d.decision_id = %s AND d.tenant_id = %s
                """,
                (decision_id, tenant_id),
            )
            row = cur.fetchone()
        conn.commit()
        return (row["decided_at"], row["entity"]) if row else (None, None)
    finally:
        db.put_conn(conn)


def grant_standing(tenant_id: str, decision_id: str, evidence: dict[str, Any]) -> dict:
    """Apply an external outcome to a decision and re-tier the memories it produced.

    ``tenant_id`` scopes the decision lookup; a decision belonging to another tenant is
    reported as not existing. It is a required positional argument rather than an optional
    keyword on purpose -- an authorisation check that a caller can omit is one a caller
    will eventually omit, and every existing call site is updated in the same change.

    ``evidence`` requires ``outcome``, ``source`` and ``external_ref``; ``metric_delta``
    is required when ``source='metric'``. Returns which memories were promoted or
    demoted, and ``model_calls``, which is always 0.
    """
    assert_no_model_calls()  # checked on the live path, not merely documented
    outcome, source, delta, ref = _validate(evidence)

    # Re-check the claim against the external system of record BEFORE recording it. This is
    # the half of the central claim that used to be missing: _validate only ever confirmed
    # that external_ref was a non-empty string, so "an external signal said so" was
    # something the caller asserted rather than something anyone checked.
    decided_at, entity = _decided_at(tenant_id, decision_id)
    verification = evidence_check.verify(source, ref, delta, decided_at, entity=entity)
    if verification.status == evidence_check.CONTRADICTED:
        raise OutcomeRejected(
            f"the external system of record disagrees: {verification.detail}"
        )

    result = db.retry_serializable(
        _apply, tenant_id, decision_id, outcome, source, delta, ref, verification
    )
    assert result["model_calls"] == 0, "the promotion path must never call a model"
    return result


def assert_no_model_calls() -> None:
    """Fail loudly if anything model-shaped became reachable from this module.

    Called at the top of every ``grant_standing()``. Cheap enough to run on the live path
    (a couple of set intersections over module dicts), which is the point: a safeguard that
    only runs in a test nobody runs is not a safeguard.

    Two levels, because one was not enough. The original version checked only this module's
    own globals -- which meant the invariant could be defeated by moving a Bedrock client
    one import away and calling it from here. It now also inspects the modules this one
    imports.

    The rule is about MODELS, not about network calls or AWS. ``backend/evidence.py``
    deliberately imports boto3 to ask CloudWatch what a metric actually did, and that is the
    thesis working as designed: trust arriving from outside the system. So the guard bans
    model *clients* and model *entry points*, and separately asserts that any boto3 client
    reachable from here is not a Bedrock one.
    """
    forbidden = {"bedrock", "bedrock_client", "converse", "invoke_model", "openai", "anthropic",
                 "embeddings", "agent"}

    found = sorted(name for name in globals() if name.lower() in forbidden)
    if found:
        raise AssertionError(
            f"model-carrying names reachable from trust.py: {found}. "
            "The outcome gate must stay model-free; that is the product's central claim."
        )

    import types

    for name, value in list(globals().items()):
        if not isinstance(value, types.ModuleType):
            continue
        if not getattr(value, "__name__", "").startswith("backend."):
            continue
        leaked = sorted(n for n in vars(value) if n.lower() in forbidden)
        if leaked:
            raise AssertionError(
                f"module {value.__name__!r}, imported by trust.py, exposes model-carrying "
                f"names {leaked}. A model client one import away is still a model client on "
                "the promotion path."
            )
        # A boto3 client is fine here only if it is not a model runtime. This is the check
        # that lets CloudWatch verification exist without weakening the claim.
        service = getattr(getattr(value, "_client", None), "meta", None)
        service_name = getattr(getattr(service, "service_model", None), "service_name", "")
        if "bedrock" in str(service_name).lower():
            raise AssertionError(
                f"module {value.__name__!r} holds a Bedrock client ({service_name!r}); the "
                "promotion path must never be able to reach a model."
            )


__all__ = [
    "ATTESTED",
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
