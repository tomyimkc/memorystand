#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Apply the SQL files in db/migrations/ using the driver this repo already depends on.

Written because the documented instruction did not work. `cockroach sql --url ... -f ...`
assumes the CockroachDB CLI is installed, and it is not on a plain macOS box -- neither is
`psql`. Telling someone to install a database CLI in order to run one `ALTER TABLE` against a
managed cluster is a bad trade when `psycopg2` is already in requirements.txt and already
connects to that cluster from `backend/db.py` every day.

Design notes:

* **Ledger table.** Applied filenames are recorded in `schema_migrations`, so re-running is a
  no-op and a half-finished run resumes rather than restarting. The ledger is created on first
  use; there is no bootstrap step.
* **One transaction per file**, not per statement. A migration that fails halfway is worse than
  one that fails cleanly -- the whole file commits or none of it does.
* **Autocommit for schema changes.** CockroachDB permits DDL inside a transaction, but mixing
  several schema changes with a ledger insert in one txn can hit
  "schema change statement cannot follow a statement that has written in the same transaction".
  So DDL runs first, and the ledger row is written after, in its own transaction.
* **No down-migrations.** Rolling a schema change back automatically is how data gets lost
  quietly. Write a new forward migration instead.

    python db/migrate.py                 # apply anything unapplied
    python db/migrate.py --schema        # apply db/schema.sql first, then migrations
    python db/migrate.py --dry-run       # list what would run, touch nothing
    python db/migrate.py --status        # what is applied, what is pending
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend import db  # noqa: E402

MIGRATIONS_DIR = REPO_ROOT / "db" / "migrations"

LEDGER = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename    STRING PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


def _applied(conn) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(LEDGER)
        cur.execute("SELECT filename FROM schema_migrations")
        return {r[0] for r in cur.fetchall()}


def _statements(sql: str) -> list[str]:
    """Strip `--` comments FIRST, then split on semicolons.

    The order matters and the reverse order is a real bug, not a hypothetical one: the first
    version of this function split on ';' first, and migration 001's header comment contains
    the phrase "no PagerDuty token; a human sign-off has no system of record". Splitting first
    cut that comment in half, and the orphaned remainder -- no longer behind a `--` -- was
    handed to the database as SQL, which failed with `syntax error at or near "a"`.

    Whole-line stripping is not enough either, which the second version of this function found
    out against db/schema.sql: that file has TRAILING comments on the same line as SQL, and one
    of them contains a semicolon, which split a CREATE TABLE in half and produced
    `syntax error at or near "EOF"`. So comments are stripped inline as well.

    The quote tracking below is what keeps that safe: a `--` inside a string literal is data,
    not a comment. These are hand-written DDL files with no dollar-quoted bodies, so single
    quotes are the only literal form that needs handling. If that ever changes, this needs a
    real parser rather than a cleverer loop.
    """
    cleaned = []
    for line in sql.splitlines():
        in_string = False
        cut = len(line)
        i = 0
        while i < len(line):
            ch = line[i]
            if ch == "'":
                # '' inside a string is an escaped quote, not a terminator.
                if in_string and i + 1 < len(line) and line[i + 1] == "'":
                    i += 2
                    continue
                in_string = not in_string
            elif ch == "-" and not in_string and line[i:i + 2] == "--":
                cut = i
                break
            i += 1
        text = line[:cut].rstrip()
        if text:
            cleaned.append(text)
    return [stmt.strip() for stmt in "\n".join(cleaned).split(";") if stmt.strip()]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="show what would run, change nothing")
    ap.add_argument("--status", action="store_true", help="show applied and pending, change nothing")
    ap.add_argument("--schema", action="store_true",
                    help="apply db/schema.sql first (every CREATE is IF NOT EXISTS, so this is safe "
                         "to re-run against an existing cluster)")
    args = ap.parse_args()

    files = sorted(p for p in MIGRATIONS_DIR.glob("*.sql"))
    if not files:
        print(f"No migrations found in {MIGRATIONS_DIR}")
        return 0

    conn = db.get_conn()
    conn.autocommit = True
    try:
        if args.schema and not (args.status or args.dry_run):
            # The base schema, for a cluster that has never been provisioned. Kept behind a
            # flag rather than run automatically: applying a schema is a bigger action than
            # applying a migration, and it should be something you asked for.
            schema = REPO_ROOT / "db" / "schema.sql"
            print(f"==> {schema.name}")
            with conn.cursor() as cur:
                for stmt in _statements(schema.read_text()):
                    cur.execute(stmt)
            print("    applied")

        done = _applied(conn)
        pending = [f for f in files if f.name not in done]

        if args.status or args.dry_run:
            for f in files:
                mark = "applied" if f.name in done else "PENDING"
                print(f"  [{mark:>7}] {f.name}")
            if args.dry_run and pending:
                print(f"\n{len(pending)} migration(s) would run. Nothing was changed.")
            elif not pending:
                print("\nUp to date.")
            return 0

        if not pending:
            print("Up to date; nothing to apply.")
            return 0

        for f in pending:
            print(f"==> {f.name}")
            statements = _statements(f.read_text())
            with conn.cursor() as cur:
                for stmt in statements:
                    first = " ".join(stmt.split())[:78]
                    print(f"    {first}")
                    cur.execute(stmt)
                cur.execute(
                    "INSERT INTO schema_migrations (filename) VALUES (%s) "
                    "ON CONFLICT (filename) DO NOTHING",
                    (f.name,),
                )
            print(f"    applied ({len(statements)} statement(s))")

        print(f"\n{len(pending)} migration(s) applied.")
        return 0
    finally:
        db.put_conn(conn)


if __name__ == "__main__":
    raise SystemExit(main())
