#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Recompute stored embeddings after the embedding function changes.

An embedding column is only meaningful relative to the function that produced it. When that
function changes, every stored vector becomes a measurement in a unit the query no longer
speaks -- and the failure is silent: recall still returns k rows with plausible distances, they
are just the wrong rows. That is exactly what happened here. The old stub hashed the whole text
into one seed and emitted a random gaussian, so similarity was noise; replacing it with lexical
feature hashing fixed new writes and left every existing row incomparable.

Scoped by tenant on purpose. The demo tenant's ~130 curated memories are what `/recall` and the
demo actually query, so those must be correct. The ~50,000 synthetic rows the load test seeded
exist only to measure whether the optimizer picks the vector index at scale -- their content is
generated filler and their relevance to anything is undefined either way, so re-embedding them
would cost a long trans-Pacific write for no gain. Pass --all if you disagree.

    python scripts/reembed.py --tenant 9c8f6e5a-9d1a-4a1c-8f2e-3b6d1c7a4e10
    python scripts/reembed.py --tenant <id> --dry-run
    python scripts/reembed.py --all
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend import db, embeddings  # noqa: E402

BATCH = 200


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tenant", help="only this tenant_id")
    ap.add_argument("--all", action="store_true", help="every tenant, including load-test filler")
    ap.add_argument("--dry-run", action="store_true", help="count and show samples, write nothing")
    args = ap.parse_args()

    if not args.tenant and not args.all:
        ap.error("pass --tenant <id> or --all")

    where, params = ("", [])
    if args.tenant:
        where, params = ("WHERE tenant_id = %s", [args.tenant])

    conn = db.get_conn()
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT count(*) FROM agent_memories {where}", params)
            total = cur.fetchone()[0]
            print(f"  rows in scope: {total}")
            if not total:
                return 0

            cur.execute(
                f"SELECT memory_id::string, content FROM agent_memories {where} ORDER BY created_at",
                params,
            )
            rows = cur.fetchall()

        if args.dry_run:
            print("  dry run -- nothing written. First three:")
            for mid, content in rows[:3]:
                print(f"    {mid[:8]}  {str(content)[:70]}")
            return 0

        done = 0
        for start in range(0, len(rows), BATCH):
            chunk = rows[start : start + BATCH]
            with conn.cursor() as cur:
                for mid, content in chunk:
                    vec = embeddings.to_pgvector(embeddings.embed(str(content or "")))
                    cur.execute(
                        "UPDATE agent_memories SET embedding = %s WHERE memory_id = %s",
                        (vec, mid),
                    )
            done += len(chunk)
            print(f"    re-embedded {done}/{len(rows)}")

        # The optimizer's row-count estimates drive whether the vector index is chosen at all;
        # a bulk rewrite invalidates them.
        with conn.cursor() as cur:
            cur.execute("ANALYZE agent_memories")
        print("  ANALYZE done")
        print(f"\n  {done} embedding(s) recomputed with: {embeddings.provenance()}")
        return 0
    finally:
        db.put_conn(conn)


if __name__ == "__main__":
    raise SystemExit(main())
