# SPDX-License-Identifier: Apache-2.0
"""Action-authority policy for recalled memories.

Recall is an audit surface: it returns accepted memories even when they are not
allowed to steer a decision.  This module owns the narrower question, "may this
row enter the agent's decision context for this incident subject?"

Two independent gates apply:

* **Subject binding.**  A memory about ``checkout-api`` cannot justify an
  action for ``payments-service`` merely because the prose and embedding are
  similar.  Comparison is case- and separator-insensitive, but otherwise exact.
* **Earned standing.**  ``verified`` is action-authoritative only when the
  database still contains the outcome receipt that earned it: a successful
  metric outcome, a non-empty external reference, a metric delta, a confirmation
  timestamp, and a trust-check timestamp.  Legacy rows that were inserted or
  retagged directly as ``verified`` remain visible for audit but fail closed at
  read time.

``attested`` memories remain eligible only as advisory context; ``agent.py``
always holds any action they influence for human approval.  Unconfirmed and
disputed rows remain inspectable but never enter decision context.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Iterable

from . import evidence

ACTION_TIERS = frozenset({"verified", "attested"})

ENTITY_MISMATCH = "entity_mismatch"
ENTITY_UNBOUND = "entity_unbound"
TRUST_TIER_NOT_ELIGIBLE = "trust_tier_not_eligible"
VERIFIED_RECEIPT = "verified_receipt"
VERIFIED_WITHOUT_CURRENT_RECEIPT = "verified_without_current_receipt"
ATTESTED_ADVISORY = "attested_advisory"

_SEPARATORS = re.compile(r"[\s_-]+")


def normalize_entity(value: Any) -> str:
    """Normalize one entity for exact subject comparison.

    ``Payments_Service``, ``payments-service`` and ``payments service`` are the
    same subject.  Containment is deliberately not accepted:
    ``payments`` is not ``payments-canary``.
    """

    return _SEPARATORS.sub("", str(value or "").strip().lower())


def entities_match(left: Any, right: Any) -> bool:
    """Return true only for two non-empty, exactly-normalized subjects."""

    left_norm = normalize_entity(left)
    right_norm = normalize_entity(right)
    return bool(left_norm and right_norm and left_norm == right_norm)


def _receipt_backed_verified_ids(
    cur,
    tenant_id: str,
    rows: Iterable[dict[str, Any]],
) -> dict[str, str]:
    """Which verified rows still have a complete, entity-bound metric receipt?

    The query intentionally reads the receipt instead of trusting the tier label
    alone.  It does not mutate or demote legacy data; the row stays available to
    recall and time-travel, but it cannot act until authority is earned through
    the real outcome path.
    """

    by_id = {
        str(row["memory_id"]): row
        for row in rows
        if row.get("memory_id") and row.get("trust_tier") == "verified"
    }
    if not by_id:
        return {}

    # The savepoint is not optional. A caught SQL error still leaves PostgreSQL /
    # CockroachDB's surrounding transaction aborted; rolling back only this
    # authority probe lets recall return its audit-visible rows with authority
    # removed instead of failing later at commit.
    cur.execute("SAVEPOINT memorystand_authority_receipt")
    try:
        cur.execute(
            """
            SELECT DISTINCT m.memory_id::string AS memory_id,
                   m.entity,
                   d.outcome_external_ref
            FROM agent_memories m
            JOIN agent_decisions d
              ON m.memory_id = ANY(d.produced_memory_ids)
             AND d.tenant_id = m.tenant_id
            WHERE m.tenant_id = %s
              AND m.memory_id = ANY(%s::UUID[])
              AND m.trust_tier = 'verified'
              AND m.trust_checked_at IS NOT NULL
              AND d.decided_at <= m.trust_checked_at
              AND d.outcome_confirmed_at <= m.trust_checked_at
              AND d.outcome = 'success'
              AND d.outcome_confirmed_at IS NOT NULL
              AND d.outcome_source = 'metric'
              AND d.outcome_external_ref IS NOT NULL
              AND d.outcome_external_ref <> ''
              AND d.outcome_metric_delta IS NOT NULL
            """,
            (tenant_id, list(by_id)),
        )
        receipts = list(cur.fetchall())
    except Exception:
        # This is an action-authority gate. A schema mismatch or database query
        # failure must remove authority, not make every verified label trusted.
        cur.execute("ROLLBACK TO SAVEPOINT memorystand_authority_receipt")
        cur.execute("RELEASE SAVEPOINT memorystand_authority_receipt")
        return {}
    cur.execute("RELEASE SAVEPOINT memorystand_authority_receipt")

    valid: dict[str, str] = {}
    for receipt in receipts:
        memory_id = str(receipt["memory_id"])
        row = by_id.get(memory_id)
        if row and evidence.entity_matches(
            row.get("entity"),
            str(receipt["outcome_external_ref"]),
        ):
            valid[memory_id] = str(receipt["outcome_external_ref"])
    return valid


def _receipt_backed_verified_ids_at(
    cur,
    tenant_id: str,
    rows: Iterable[dict[str, Any]],
) -> dict[str, str]:
    """Historical form used inside an AS OF SYSTEM TIME transaction.

    A JOIN to ``agent_decisions`` would pull that table into the pinned
    transaction anyway, but issuing the reads separately avoids depending on
    mixed current/historical optimizer behaviour and keeps the provenance
    explicit: both tables are read at the same transaction timestamp.
    """

    by_id = {
        str(row["memory_id"]): row
        for row in rows
        if row.get("memory_id") and row.get("trust_tier") == "verified"
    }
    if not by_id:
        return {}

    cur.execute(
        """
        SELECT memory_id::string AS memory_id, entity, trust_checked_at
        FROM agent_memories
        WHERE tenant_id = %s
          AND memory_id = ANY(%s::UUID[])
          AND trust_tier = 'verified'
        """,
        (tenant_id, list(by_id)),
    )
    historical_memories = {
        str(row["memory_id"]): dict(row)
        for row in cur.fetchall()
        if row["trust_checked_at"] is not None
    }
    if not historical_memories:
        return {}

    cur.execute(
        """
        SELECT decided_at,
               produced_memory_ids::STRING[] AS produced_memory_ids,
               outcome, outcome_confirmed_at, outcome_source,
               outcome_external_ref, outcome_metric_delta
        FROM agent_decisions
        WHERE tenant_id = %s
          AND outcome = 'success'
          AND outcome_confirmed_at IS NOT NULL
          AND outcome_source = 'metric'
          AND outcome_external_ref IS NOT NULL
          AND outcome_external_ref <> ''
          AND outcome_metric_delta IS NOT NULL
        """,
        (tenant_id,),
    )
    valid: dict[str, str] = {}
    for decision in cur.fetchall():
        produced = {str(value) for value in (decision["produced_memory_ids"] or [])}
        for memory_id in produced & set(historical_memories):
            row = historical_memories[memory_id]
            trust_checked_at = row.get("trust_checked_at")
            if (
                trust_checked_at is not None
                and decision.get("decided_at") is not None
                and decision.get("outcome_confirmed_at") is not None
                and decision["decided_at"] <= trust_checked_at
                and decision["outcome_confirmed_at"] <= trust_checked_at
                and evidence.entity_matches(
                    row.get("entity"),
                    str(decision["outcome_external_ref"]),
                )
            ):
                valid[memory_id] = str(decision["outcome_external_ref"])
    return valid


def annotate_action_eligibility(
    cur,
    tenant_id: str,
    rows: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Copy rows and attach their target-independent standing eligibility."""

    annotated = [dict(row) for row in rows]
    receipt_backed = _receipt_backed_verified_ids(cur, tenant_id, annotated)
    for row in annotated:
        tier = row.get("trust_tier")
        memory_id = str(row.get("memory_id") or "")
        row["action_receipt_external_ref"] = receipt_backed.get(memory_id)
        if tier == "verified":
            row["action_eligible"] = memory_id in receipt_backed
            row["action_eligibility_reason"] = (
                VERIFIED_RECEIPT
                if memory_id in receipt_backed
                else VERIFIED_WITHOUT_CURRENT_RECEIPT
            )
        elif tier == "attested":
            row["action_eligible"] = True
            row["action_eligibility_reason"] = ATTESTED_ADVISORY
        else:
            row["action_eligible"] = False
            row["action_eligibility_reason"] = TRUST_TIER_NOT_ELIGIBLE
    return annotated


def annotate_action_eligibility_at(
    cur,
    tenant_id: str,
    rows: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Historical eligibility inside an already-pinned read transaction."""

    annotated = [dict(row) for row in rows]
    receipt_backed = _receipt_backed_verified_ids_at(
        cur,
        tenant_id,
        annotated,
    )
    for row in annotated:
        tier = row.get("trust_tier")
        memory_id = str(row.get("memory_id") or "")
        row["action_receipt_external_ref"] = receipt_backed.get(memory_id)
        if tier == "verified":
            row["action_eligible"] = memory_id in receipt_backed
            row["action_eligibility_reason"] = (
                VERIFIED_RECEIPT
                if memory_id in receipt_backed
                else VERIFIED_WITHOUT_CURRENT_RECEIPT
            )
        elif tier == "attested":
            row["action_eligible"] = True
            row["action_eligibility_reason"] = ATTESTED_ADVISORY
        else:
            row["action_eligible"] = False
            row["action_eligibility_reason"] = TRUST_TIER_NOT_ELIGIBLE
    return annotated


def filter_for_target(
    rows: Iterable[dict[str, Any]],
    target_entity: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split recalled rows into decision context and audit-only exclusions.

    Input order is preserved.  A duplicate ``memory_id`` is ignored after its
    first occurrence so repeated context cannot gain weight by repetition.
    """

    if not normalize_entity(target_entity):
        raise ValueError("target_entity must name a non-empty incident subject")

    eligible: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    seen: set[str] = set()
    for original in rows:
        row = dict(original)
        memory_id = str(row.get("memory_id") or "")
        dedupe_key = memory_id or f"anonymous:{id(original)}"
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        if not normalize_entity(row.get("entity")):
            reason = ENTITY_UNBOUND
        elif not entities_match(row.get("entity"), target_entity):
            reason = ENTITY_MISMATCH
        elif row.get("trust_tier") not in ACTION_TIERS:
            reason = TRUST_TIER_NOT_ELIGIBLE
        elif (
            row.get("trust_tier") == "verified"
            and (
                row.get("action_eligible") is not True
                or row.get("action_eligibility_reason") != VERIFIED_RECEIPT
                or not str(row.get("action_receipt_external_ref") or "").strip()
            )
        ):
            # A bare ``trust_tier=verified`` label is not a receipt. Production
            # recall attaches all three fields above only after re-reading the
            # complete metric outcome that earned standing. Fail closed when a
            # direct/internal caller hands ``agent.propose`` an unannotated row;
            # otherwise the public policy could be bypassed simply by constructing
            # a dict instead of calling recall().
            reason = VERIFIED_WITHOUT_CURRENT_RECEIPT
        elif (
            "action_eligible" in row
            and row.get("action_eligible") is not True
        ):
            reason = str(
                row.get("action_eligibility_reason")
                or VERIFIED_WITHOUT_CURRENT_RECEIPT
            )
        else:
            eligible.append(row)
            continue

        excluded.append(
            {
                "memory_id": memory_id or None,
                "entity": row.get("entity"),
                "trust_tier": row.get("trust_tier"),
                "reason": reason,
            }
        )
    return eligible, excluded


def exclusion_summary(excluded: Iterable[dict[str, Any]]) -> str:
    """Compact deterministic reason counts for an auditable rationale."""

    counts = Counter(str(row.get("reason") or "unknown") for row in excluded)
    return ", ".join(f"{reason}={counts[reason]}" for reason in sorted(counts))


__all__ = [
    "ACTION_TIERS",
    "ATTESTED_ADVISORY",
    "ENTITY_MISMATCH",
    "ENTITY_UNBOUND",
    "TRUST_TIER_NOT_ELIGIBLE",
    "VERIFIED_RECEIPT",
    "VERIFIED_WITHOUT_CURRENT_RECEIPT",
    "annotate_action_eligibility",
    "annotate_action_eligibility_at",
    "entities_match",
    "exclusion_summary",
    "filter_for_target",
    "normalize_entity",
]
