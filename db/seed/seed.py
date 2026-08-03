#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Idempotent seeder for db/seed/incidents.jsonl.

Reads the fixture file and calls backend.memory.remember() for each record,
so seeding exercises the real admission-control path (deterministic
attribute-conflict check + vector-neighbour similarity search) instead of
inserting rows directly. That is deliberate: the accepted/quarantined tally
this script prints at the end is itself a proof artifact -- it shows the
admission gate actually adjudicating the two designed conflicts in the
fixture data (payments-service.primary_datastore_table and
payments-service.failover_restart_order), not a hand-picked demo number.

Usage:
    export COCKROACH_DSN='postgresql://root@localhost:26257/defaultdb?sslmode=disable'
    python db/seed/seed.py [--dsn DSN] [--tenant UUID] [--force] [--limit N]

Safe to re-run: by default the script checks whether the target tenant
already has rows in agent_memories and, if so, does nothing (prints a notice
and exits 0) unless --force is passed. --force does not delete existing rows;
it re-runs remember() for every fixture row again, which is safe because
remember() itself is the thing deciding accept/quarantine/supersede on each
call -- re-seeding just re-exercises admission control against a now-larger
belief state.

This script does not import backend.embeddings directly; it only sets
STANDING_EMBED_STUB in the environment (before importing backend.memory) so
that backend.embeddings.embed() picks it up. That keeps the embedding
backend decision inside backend/, where it belongs, per the frozen API.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

DEFAULT_DSN = "postgresql://root@localhost:26257/defaultdb?sslmode=disable"

# Fixed so demo recordings, screenshots, and `standing show <decision-id>`
# walkthroughs are reproducible across re-seeds and across machines.
DEFAULT_TENANT_ID = "9c8f6e5a-9d1a-4a1c-8f2e-3b6d1c7a4e10"

# A single synthetic on-call agent identity that "wrote" every seeded memory.
# Real agent runs during the demo use a different agent_id so seeded fixture
# data and live demo actions are trivially distinguishable in queries.
DEFAULT_AGENT_ID = "1a2b3c4d-5e6f-4708-9a0b-1c2d3e4f5061"

FIXTURE_PATH = Path(__file__).resolve().parent / "incidents.jsonl"

REQUIRED_FIELDS = ("memory_type", "entity", "content", "source")


def load_fixtures(path: Path, limit: int | None) -> list[dict]:
    """Parse the JSONL fixture file, failing loudly on a malformed row.

    A malformed fixture row is a bug in the fixture file, not a runtime
    condition to swallow -- so this raises rather than skipping the row.
    """
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{lineno}: invalid JSON ({exc})") from exc
            missing = [f for f in REQUIRED_FIELDS if f not in row]
            if missing:
                raise ValueError(f"{path}:{lineno}: missing required field(s) {missing}")
            rows.append(row)
            if limit is not None and len(rows) >= limit:
                break
    return rows


def existing_row_count(tenant_id: str) -> int:
    """How many agent_memories rows this tenant already has, via backend.db."""
    from backend.db import get_conn

    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM agent_memories WHERE tenant_id = %s",
            (tenant_id,),
        )
        (count,) = cur.fetchone()
    return int(count)


def seed(rows: list[dict], tenant_id: str, agent_id: str) -> dict:
    """Call backend.memory.remember() for every fixture row; return a tally."""
    from backend.memory import remember

    tally = {"accepted": 0, "quarantined": 0, "other": 0, "errors": 0}
    per_verdict_examples: dict[str, list[str]] = {"accepted": [], "quarantined": []}

    for i, row in enumerate(rows, start=1):
        try:
            result = remember(
                tenant_id,
                agent_id,
                row["content"],
                entity=row.get("entity"),
                attribute_key=row.get("attribute_key"),
                attribute_value=row.get("attribute_value"),
                memory_type=row.get("memory_type", "semantic"),
                source=row.get("source"),
                structured_data=row.get("structured_data"),
            )
        except Exception as exc:  # noqa: BLE001 - report and keep seeding the rest
            tally["errors"] += 1
            print(f"  [{i}/{len(rows)}] ERROR remember() failed: {exc}", file=sys.stderr)
            continue

        verdict = result.get("verdict", "other")
        tally[verdict] = tally.get(verdict, 0) + 1
        if verdict in per_verdict_examples and len(per_verdict_examples[verdict]) < 3:
            per_verdict_examples[verdict].append(
                f"{row.get('entity')}.{row.get('attribute_key')}={row.get('attribute_value')!r} "
                f"(source={row.get('source')})"
            )

    tally["_examples"] = per_verdict_examples
    return tally


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dsn",
        default=os.environ.get("COCKROACH_DSN", DEFAULT_DSN),
        help=f"CockroachDB DSN (default: env COCKROACH_DSN, else {DEFAULT_DSN})",
    )
    parser.add_argument(
        "--tenant",
        default=DEFAULT_TENANT_ID,
        help=f"Tenant UUID to seed (default: fixed demo tenant {DEFAULT_TENANT_ID})",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-seed even if the tenant already has agent_memories rows",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only seed the first N fixture rows (default: all)",
    )
    args = parser.parse_args()

    # backend.db.get_conn() reads COCKROACH_DSN from the environment (it is
    # Lambda-safe and does not accept a DSN argument), so make sure the
    # environment reflects --dsn / the resolved default before importing it.
    os.environ["COCKROACH_DSN"] = args.dsn

    if "AWS_ACCESS_KEY_ID" not in os.environ and "AWS_PROFILE" not in os.environ:
        os.environ.setdefault("STANDING_EMBED_STUB", "1")
        print(
            "No AWS credentials detected (AWS_ACCESS_KEY_ID / AWS_PROFILE unset); "
            "forcing STANDING_EMBED_STUB=1 so embeddings use the deterministic local stub."
        )
    else:
        print("AWS credentials detected; embeddings will use Amazon Titan Text Embeddings V2.")

    if not FIXTURE_PATH.exists():
        print(f"Fixture file not found: {FIXTURE_PATH}", file=sys.stderr)
        return 1

    rows = load_fixtures(FIXTURE_PATH, args.limit)
    print(f"Loaded {len(rows)} fixture rows from {FIXTURE_PATH}")

    try:
        existing = existing_row_count(args.tenant)
    except Exception as exc:  # noqa: BLE001 - surface connectivity problems plainly
        print(f"Could not check existing rows for tenant {args.tenant}: {exc}", file=sys.stderr)
        return 1

    if existing > 0 and not args.force:
        print(
            f"Tenant {args.tenant} already has {existing} row(s) in agent_memories. "
            "Skipping seed (pass --force to re-seed anyway)."
        )
        return 0
    if existing > 0 and args.force:
        print(f"Tenant {args.tenant} already has {existing} row(s); --force set, re-seeding.")

    print(f"Seeding tenant={args.tenant} agent={DEFAULT_AGENT_ID} dsn={args.dsn}")
    tally = seed(rows, args.tenant, DEFAULT_AGENT_ID)
    examples = tally.pop("_examples")

    print()
    print("=== Seed summary ===")
    print(f"  accepted:    {tally.get('accepted', 0)}")
    print(f"  quarantined: {tally.get('quarantined', 0)}")
    if tally.get("other"):
        print(f"  other:       {tally['other']}")
    if tally.get("errors"):
        print(f"  errors:      {tally['errors']}")
    for verdict, examples_list in examples.items():
        if examples_list:
            print(f"  sample {verdict}:")
            for ex in examples_list:
                print(f"    - {ex}")
    print(f"  total rows attempted: {len(rows)}")

    return 1 if tally.get("errors") else 0


if __name__ == "__main__":
    raise SystemExit(main())
