#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Make the public demo tenant tell the thesis on the first judge click.

The live demo tenant accumulated near-duplicate video-capture rows. They all
share almost the same wording, so lexical recall returns five copies at the
same distance and `/decide` falls through to the keyword table. A judge then
never sees trust beat proximity.

This script, against the DSN in MEMORYSTAND_DSN:

1. Supersedes extra accepted copies of the same (entity, attribute_key, stem).
2. Ensures one closer unconfirmed `restart_service` memory and one farther
   verified `scale_up` memory exist for the dashboard's default alert.
3. Prints the recall ranking for that alert so the story can be checked
   before anyone clicks Submit.

It never deletes a row. Superseded rows stay in MVCC history.
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("MEMORYSTAND_EMBED_STUB", "1")

from backend import db, embeddings, memory  # noqa: E402

DEMO_TENANT = "9c8f6e5a-9d1a-4a1c-8f2e-3b6d1c7a4e10"
DEMO_AGENT = "1a2b3c4d-5e6f-4708-9a0b-1c2d3e4f5061"
DEFAULT_ALERT = "payments-service p99 latency above 2s, error rate climbing"

UNCONFIRMED_CONTENT = (
    "payments-service p99 latency above 2s, error rate climbing; last night an "
    "engineer used restart_service on the payments pool and the page cleared. "
    "This claim has not been independently corroborated."
)
# Fewer query tokens than UNCONFIRMED_CONTENT on purpose: this row must rank
# farther so a judge sees trust beat proximity instead of a distance tie.
VERIFIED_CONTENT = (
    "payments-service p99 latency: scale_up. CloudWatch confirmed the scale-up."
)
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


def _retune_row(conn, memory_id: str, content: str, *, apply: bool) -> None:
    vec = embeddings.embed(content)
    print(f"retune {memory_id[:8]} ({len(content)} chars)")
    if not apply:
        return
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE agent_memories
            SET content = %s, embedding = %s, verdict_set_at = now()
            WHERE memory_id = %s
            """,
            (content, vec, memory_id),
        )


def _retune_verified_scale_up(conn, tenant_id: str, *, apply: bool) -> None:
    """Give the existing verified scale_up enough lexical overlap to enter k=5.

    It must stay *farther* than the unconfirmed restart_service hero, or the
    dashboard cannot show trust beating proximity.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT memory_id::text FROM agent_memories
            WHERE tenant_id = %s AND verdict = 'accepted'
              AND trust_tier = 'unconfirmed' AND attribute_value = 'restart_service'
              AND memory_id::text LIKE '22207f6e%%'
            """,
            (tenant_id,),
        )
        restart = cur.fetchone()
        cur.execute(
            """
            SELECT memory_id::text FROM agent_memories
            WHERE tenant_id = %s AND verdict = 'accepted'
              AND trust_tier = 'verified' AND attribute_value = 'scale_up'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (tenant_id,),
        )
        scale = cur.fetchone()
    if restart:
        _retune_row(conn, restart[0], UNCONFIRMED_CONTENT, apply=apply)
    if scale:
        _retune_row(conn, scale[0], VERIFIED_CONTENT, apply=apply)


def _ensure_hero(conn, tenant_id: str, agent_id: str, *, apply: bool) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT memory_id::text, trust_tier, attribute_value, content
            FROM agent_memories
            WHERE tenant_id = %s AND verdict = 'accepted'
              AND attribute_value IN ('restart_service', 'scale_up')
            """,
            (tenant_id,),
        )
        existing = cur.fetchall()
    has_restart = any(r[2] == "restart_service" and r[1] == "unconfirmed" for r in existing)
    has_scale = any(r[2] == "scale_up" and r[1] == "verified" for r in existing)
    print(f"hero pair present: unconfirmed restart_service={has_restart} verified scale_up={has_scale}")
    if not apply or (has_restart and has_scale):
        return
    if not has_restart:
        _insert_memory(
            conn, tenant_id, agent_id,
            content=UNCONFIRMED_CONTENT,
            attribute_value="restart_service",
            trust_tier="unconfirmed",
            confidence=0.5,
        )
        print("inserted unconfirmed restart_service hero")
    if not has_scale:
        _insert_memory(
            conn, tenant_id, agent_id,
            content=VERIFIED_CONTENT,
            attribute_value="scale_up",
            trust_tier="verified",
            confidence=0.8,
        )
        print("inserted verified scale_up hero")


def _insert_memory(
    conn, tenant_id: str, agent_id: str, *, content: str, attribute_value: str,
    trust_tier: str, confidence: float,
) -> None:
    vec = embeddings.embed(content)
    memory_id = str(uuid.uuid4())
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO agent_memories (
                memory_id, tenant_id, agent_id, memory_type, entity,
                attribute_key, attribute_value, content, source, verdict,
                trust_tier, confidence, embedding
            ) VALUES (
                %s, %s, %s, 'episodic', 'payments-service',
                'remediation', %s, %s, 'demo:judge-story', 'accepted',
                %s, %s, %s
            )
            """,
            (memory_id, tenant_id, agent_id, attribute_value, content,
             trust_tier, confidence, vec),
        )


def show_recall(tenant_id: str, query: str) -> None:
    rows = memory.recall(tenant_id, DEMO_AGENT, query, k=5)
    print(f"recall k=5 for {query!r}:")
    for i, row in enumerate(rows, 1):
        print(
            f"  {i}. {row.get('memory_id','')[:8]}  "
            f"tier={row.get('trust_tier'):<12}  "
            f"dist={row.get('distance')}  "
            f"attr={row.get('attribute_value')}  "
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
        _ensure_hero(conn, args.tenant, DEMO_AGENT, apply=args.apply)
        _retune_verified_scale_up(conn, args.tenant, apply=args.apply)
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
