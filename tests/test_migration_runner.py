# SPDX-License-Identifier: Apache-2.0
"""The SQL splitter in db/migrate.py broke twice. Both breaks are pinned here.

Neither was exotic. Both were a naive split on ';' meeting a real file:

  1. Splitting BEFORE stripping comments. Migration 001's header contains "no PagerDuty
     token; a human sign-off has no system of record" -- the semicolon cut the comment in
     half, and the orphaned remainder was handed to the database as SQL.
  2. Stripping only WHOLE-LINE comments. db/schema.sql has trailing comments on the same
     line as SQL, one of which contains a semicolon, which split a CREATE TABLE in half.

Both failed loudly against a real cluster rather than silently, which is the only reason
they were cheap. These tests make them cheap permanently.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("migrate", REPO_ROOT / "db" / "migrate.py")
migrate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migrate)


def test_a_semicolon_inside_a_full_line_comment_does_not_split_anything():
    sql = """
-- no PagerDuty token; a human sign-off has no system of record
ALTER TABLE t ADD CONSTRAINT c CHECK (x IN ('a', 'b'));
"""
    stmts = migrate._statements(sql)
    assert len(stmts) == 1
    assert stmts[0].startswith("ALTER TABLE t")
    assert "human sign-off" not in stmts[0]


def test_a_semicolon_inside_a_trailing_comment_does_not_split_a_statement():
    sql = """
CREATE TABLE t (
    id   UUID PRIMARY KEY,        -- the id; nothing else
    name STRING NOT NULL
);
"""
    stmts = migrate._statements(sql)
    assert len(stmts) == 1, f"trailing comment split the statement: {stmts}"
    assert "CREATE TABLE t" in stmts[0] and "name STRING NOT NULL" in stmts[0]


def test_a_double_dash_inside_a_string_literal_is_data_not_a_comment():
    """The risk created by stripping inline comments, guarded so the fix has no sharp edge."""
    sql = "INSERT INTO t (v) VALUES ('a--b');"
    stmts = migrate._statements(sql)
    assert len(stmts) == 1
    assert "'a--b'" in stmts[0]


def test_escaped_quotes_do_not_confuse_the_scanner():
    sql = "INSERT INTO t (v) VALUES ('it''s fine -- really');"
    stmts = migrate._statements(sql)
    assert len(stmts) == 1
    assert "really" in stmts[0]


def test_multiple_statements_still_split():
    sql = "ALTER TABLE t DROP CONSTRAINT IF EXISTS c;\nALTER TABLE t ADD CONSTRAINT c CHECK (x > 0);"
    assert len(migrate._statements(sql)) == 2


def test_the_real_migration_and_schema_files_parse():
    """Parse the actual shipped files, not just hand-written samples."""
    for path in sorted((REPO_ROOT / "db" / "migrations").glob("*.sql")) + [REPO_ROOT / "db" / "schema.sql"]:
        stmts = migrate._statements(path.read_text())
        assert stmts, f"{path.name} produced no statements"
        for stmt in stmts:
            # A fragment that starts mid-sentence is the signature of both historical bugs.
            head = stmt.split()[0].upper()
            assert head in {
                "CREATE", "ALTER", "DROP", "INSERT", "SET", "COMMENT", "GRANT", "UPSERT",
            }, f"{path.name}: statement starts with {head!r}, which looks like a split comment:\n{stmt[:160]}"
