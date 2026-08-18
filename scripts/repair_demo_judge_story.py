#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Audit and de-duplicate the public demo tenant without fabricating trust.

The live demo tenant accumulated near-duplicate video-capture rows. They all
share almost the same wording, so lexical recall returns five copies at the
same distance and `/decide` falls through to the keyword table. A judge then
never sees trust beat proximity.

This script, against the DSN in MEMORYSTAND_DSN:

1. Supersedes extra accepted copies of the same (entity, attribute_key, stem).
2. Requires one entity-bound unconfirmed `restart_service` memory and one
   entity-bound, genuinely verified `scale_up` remediation to already exist.
3. Prints the top twenty rows for that alert so wrong-entity candidates cannot
   hide just outside the dashboard's normal recall window.
4. Refuses to rewrite a verified memory's content or insert a row directly at
   `verified`; those operations would change the claim after its receipt or
   bypass the real outcome gate.

The verified remediation must be created through the real decision and
CloudWatch-backed outcome path. This script is not an authority-granting path.

It prints the recall ranking for that alert so the story can be checked
   before anyone clicks Submit.

It never deletes a row. Superseded rows stay in MVCC history.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("MEMORYSTAND_EMBED_STUB", "1")

from backend import db, memory  # noqa: E402

DEMO_TENANT = "9c8f6e5a-9d1a-4a1c-8f2e-3b6d1c7a4e10"
DEMO_AGENT = "1a2b3c4d-5e6f-4708-9a0b-1c2d3e4f5061"
DEFAULT_ALERT = "payments-service p99 latency above 2s, error rate climbing"
GUIDED_ENTITY = "payments-service"
GUIDED_ATTRIBUTE_KEY = "remediation"
TIER_RANK = {"verified": 3, "attested": 2, "unconfirmed": 1, "disputed": 0}


def _fetch_accepted(conn, tenant_id: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT memory_id::text, entity, attribute_key, attribute_value,
                   left(content, 80) AS stem, trust_tier, created_at, content
            FROM agent_memories
            WHERE tenant_id = %s AND verdict = 'accepted'
            ORDER BY created_at ASC
            """,
            (tenant_id,),
        )
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def _group_key(row: dict) -> tuple[str, str, str]:
    return (
        str(row.get("entity") or ""),
        str(row.get("attribute_key") or ""),
        str(row.get("stem") or "")[:60],
    )


def plan_supersedes(rows: list[dict]) -> list[str]:
    groups: dict[tuple[str, str, str], list[dict]] = {}
    for row in rows:
        groups.setdefault(_group_key(row), []).append(row)
    drop: list[str] = []
    for members in groups.values():
        if len(members) < 2:
            continue
        keep = max(
            members,
            key=lambda r: (TIER_RANK.get(r["trust_tier"], 0), r["created_at"]),
        )
        for row in members:
            if row["memory_id"] != keep["memory_id"]:
                drop.append(row["memory_id"])
    return drop


def apply_supersedes(conn, ids: list[str]) -> int:
    if not ids:
        return 0
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE agent_memories
            SET verdict = 'superseded',
                verdict_reasons = array_append(
                    COALESCE(verdict_reasons, ARRAY[]::STRING[]),
                    'collapsed near-duplicate for the public demo tenant'
                ),
                verdict_set_at = now()
            WHERE memory_id = ANY(%s::UUID[])
              AND verdict = 'accepted'
            """,
            (ids,),
        )
        return cur.rowcount


def _is_guided_restart(row: dict) -> bool:
    return (
        row.get("entity") == GUIDED_ENTITY
        and row.get("attribute_key") == GUIDED_ATTRIBUTE_KEY
        and row.get("attribute_value") == "restart_service"
        and row.get("trust_tier") == "unconfirmed"
    )


def _is_guided_scale_up(row: dict) -> bool:
    return (
        row.get("entity") == GUIDED_ENTITY
        and row.get("attribute_key") == GUIDED_ATTRIBUTE_KEY
        and row.get("attribute_value") == "scale_up"
        and row.get("trust_tier") == "verified"
    )


def _require_entity_bound_hero(conn, tenant_id: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT memory_id::text AS memory_id, entity, attribute_key,
                   attribute_value, trust_tier, content
            FROM agent_memories
            WHERE tenant_id = %s AND verdict = 'accepted'
              AND attribute_value IN ('restart_service', 'scale_up')
            """,
            (tenant_id,),
        )
        cols = [d.name for d in cur.description]
        existing = [dict(zip(cols, row)) for row in cur.fetchall()]
    has_restart = any(_is_guided_restart(row) for row in existing)
    has_scale = any(_is_guided_scale_up(row) for row in existing)
    print(
        "entity-bound hero pair present: "
        f"unconfirmed restart_service={has_restart} verified scale_up={has_scale}"
    )
    if has_restart and has_scale:
        return
    missing = []
    if not has_restart:
        missing.append("unconfirmed payments-service.remediation=restart_service")
    if not has_scale:
        missing.append("verified payments-service.remediation=scale_up")
    raise RuntimeError(
        "refusing to fabricate the guided story; missing " + ", ".join(missing) +
        ". Create memories through /ingest and grant verified standing only through "
        "the real CloudWatch-backed /confirm_outcome gate."
    )


def show_recall(tenant_id: str, query: str) -> None:
    rows = memory.recall(tenant_id, DEMO_AGENT, query, k=20)
    print(f"recall k=20 for {query!r}:")
    for i, row in enumerate(rows, 1):
        print(
            f"  {i}. {row.get('memory_id','')[:8]}  "
            f"tier={row.get('trust_tier'):<12}  "
            f"dist={row.get('distance')}  "
            f"entity={row.get('entity')}  "
            f"attr={row.get('attribute_key')}={row.get('attribute_value')}  "
            f"{(row.get('content') or '')[:70]}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", default=DEMO_TENANT)
    parser.add_argument("--apply", action="store_true", help="write; default is dry-run")
    args = parser.parse_args()
    if not (os.environ.get("MEMORYSTAND_DSN") or os.environ.get("COCKROACH_DSN")):
        print("set MEMORYSTAND_DSN first", file=sys.stderr)
        return 2

    conn = db.get_conn()
    try:
        rows = _fetch_accepted(conn, args.tenant)
        print(f"accepted rows: {len(rows)}")
        drop = plan_supersedes(rows)
        print(f"near-duplicate extras to supersede: {len(drop)}")
        if args.apply and drop:
            n = apply_supersedes(conn, drop)
            print(f"superseded {n} rows")
        _require_entity_bound_hero(conn, args.tenant)
        if args.apply:
            conn.commit()
        else:
            conn.rollback()
    finally:
        db.put_conn(conn)

    show_recall(args.tenant, DEFAULT_ALERT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
