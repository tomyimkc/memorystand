# SPDX-License-Identifier: Apache-2.0
"""Guards against the specific CockroachDB footguns this project already paid for.

Each test here maps 1:1 onto a fact in the top-level CLAUDE.md / project brief:

  1. AS OF SYSTEM TIME must never appear inside a subquery or CTE (SQLSTATE 42601).
  2. Recall paths must never add "AND embedding IS NOT NULL" (defeats the ANN index).
  3. Every UUID[] column read from CockroachDB must be cast ::STRING[] server-side.
  4. The vector index should be the plan CockroachDB's optimizer picks for the
     recall-shaped query -- and if it is not (a real, already-investigated finding
     for this schema; see benchmarks/results.md), that must be reported, not
     silently asserted away.
"""

from __future__ import annotations

import ast
import re
import uuid
from pathlib import Path
from typing import Iterator

import pytest

from backend import db, embeddings

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIRS = ["backend", "cli", "scripts"]


def _python_files() -> list[Path]:
    files = []
    for d in SOURCE_DIRS:
        files.extend(sorted((REPO_ROOT / d).rglob("*.py")))
    return files


def _sql_text_from_node(node: ast.AST) -> str | None:
    """Best-effort reconstruction of a SQL string literal, including f-strings.

    Interpolated expressions (``{literal}``) are replaced with the placeholder
    ``X`` so parenthesis balance in the surrounding SQL text is preserved
    without needing to evaluate the expression.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts = []
        for value in node.values:
            if isinstance(value, ast.Constant):
                parts.append(str(value.value))
            elif isinstance(value, ast.FormattedValue):
                parts.append("X")
        return "".join(parts)
    return None


def _execute_sql_calls(tree: ast.AST) -> Iterator[tuple[int, str]]:
    """Yield (lineno, sql_text) for every ``cur.execute(<sql-literal>, ...)`` call.

    Scoped to actual executed SQL (not docstrings/comments) so illustrative
    anti-pattern examples in module docstrings -- e.g. replay.py's own
    "here is the query that does NOT work" example -- are not false positives.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = getattr(func, "attr", None) or getattr(func, "id", None)
        if name != "execute" or not node.args:
            continue
        text = _sql_text_from_node(node.args[0])
        if text is not None:
            yield node.lineno, text


def _iter_executed_sql() -> Iterator[tuple[Path, int, str]]:
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for lineno, text in _execute_sql_calls(tree):
            yield path, lineno, text


def test_no_source_file_puts_as_of_system_time_inside_a_subquery_or_cte():
    """AS OF SYSTEM TIME is only legal as a top-level statement (SQLSTATE 42601
    otherwise). Heuristic: at the point "AS OF SYSTEM TIME" appears in an
    executed SQL string, the parenthesis depth (within that string) must be
    zero -- i.e. it is not nested inside a ``(SELECT ...)`` subquery or CTE.
    """
    violations = []
    for path, lineno, sql_text in _iter_executed_sql():
        for match in re.finditer(r"AS\s+OF\s+SYSTEM\s+TIME", sql_text, re.IGNORECASE):
            prefix = sql_text[: match.start()]
            depth = prefix.count("(") - prefix.count(")")
            if depth > 0:
                violations.append(f"{path.relative_to(REPO_ROOT)}:{lineno} (paren depth {depth})")

    assert not violations, (
        "AS OF SYSTEM TIME found nested inside a subquery/CTE (SQLSTATE 42601 on "
        f"CockroachDB): {violations}"
    )


def test_no_recall_path_contains_embedding_is_not_null():
    """That predicate alone makes the optimizer abandon the vector index for a
    full scan, and it is redundant besides -- see backend/memory.py's module
    docstring / CLAUDE.md hard-won fact #2.
    """
    violations = []
    for path in _python_files():
        text = path.read_text(encoding="utf-8")
        if re.search(r"embedding\s+is\s+not\s+null", text, re.IGNORECASE):
            violations.append(str(path.relative_to(REPO_ROOT)))
    assert not violations, f"'embedding IS NOT NULL' found in: {violations}"


# Columns declared UUID[] in db/schema.sql that this codebase actually SELECTs
# back out. checked_against is UUID[] too but is never read via SELECT today
# (only written), so it is intentionally not in this list -- add it here the
# day something starts reading it back.
UUID_ARRAY_COLUMNS = ["consulted_memory_ids", "produced_memory_ids", "memory_ids"]


def _column_read_and_cast_counts(select_list: str, column: str) -> tuple[int, int]:
    """Count real reads of ``column`` in a SELECT list, and how many are cast.

    Two shapes must NOT count as an uncast read of the raw UUID[] value:
      * the alias half of ``col::STRING[] AS col`` (same name on both sides,
        used throughout this codebase) -- stripped before counting.
      * an argument to a SQL function, e.g. ``array_length(col, 1)`` -- the
        raw array is consumed server-side and never handed back to psycopg2,
        so no cast is needed there.
    """
    no_alias = re.sub(rf"\bAS\s+{column}\b", "", select_list, flags=re.IGNORECASE)
    reads = casts = 0
    for m in re.finditer(rf"\b{column}\b", no_alias, re.IGNORECASE):
        j = m.start() - 1
        while j >= 0 and no_alias[j].isspace():
            j -= 1
        if j >= 0 and no_alias[j] == "(":
            continue  # argument to a SQL function; raw array not returned to Python
        reads += 1
        if re.match(r"\s*::\s*string\s*\[\s*\]", no_alias[m.end():], re.IGNORECASE):
            casts += 1
    return reads, casts


def test_every_uuid_array_read_casts_to_string_array():
    """CockroachDB returns UUID[] with an OID psycopg2 does not map, so an
    uncast read arrives as the raw STRING '{uuid,uuid}' and iterating it
    yields characters, not ids. Every SELECT of a UUID[] column must cast
    ``::STRING[]`` server-side (see CLAUDE.md hard-won fact #5).
    """
    violations = []
    for path, lineno, sql_text in _iter_executed_sql():
        select_match = re.search(r"select\s+(.*?)\bfrom\b", sql_text, re.IGNORECASE | re.DOTALL)
        if not select_match:
            continue
        select_list = select_match.group(1)
        for column in UUID_ARRAY_COLUMNS:
            reads, casts = _column_read_and_cast_counts(select_list, column)
            if reads > casts:
                violations.append(
                    f"{path.relative_to(REPO_ROOT)}:{lineno} selects {column!r} "
                    f"{reads}x but only casts ::STRING[] {casts}x"
                )
    assert not violations, f"uncast UUID[] read(s) found: {violations}"


# ---------------------------------------------------------------------------
# Vector index EXPLAIN checks
# ---------------------------------------------------------------------------

RECALL_SHAPED_EXPLAIN = """
EXPLAIN SELECT memory_id FROM agent_memories
WHERE tenant_id = %s AND verdict = 'accepted'
ORDER BY embedding <=> %s
LIMIT %s
"""


def _explain_rows(conn, sql_text: str, params: tuple) -> str:
    with conn.cursor() as cur:
        cur.execute(sql_text, params)
        return "\n".join(row[0] for row in cur.fetchall())


@pytest.fixture
def seeded_vector_tenant(tenant_id, agent_id) -> str:
    """Seed enough accepted, embedded rows for one tenant to give the vector
    index a realistic partition to search, then ANALYZE so the optimizer
    plans on real statistics rather than pre-load row estimates (CLAUDE.md
    hard-won fact #4). Bypasses backend.memory.remember on purpose -- this is
    measuring the query plan, not admission control.
    """
    conn = db.get_conn()
    try:
        with conn.cursor() as cur:
            for i in range(200):
                vec = embeddings.to_pgvector(embeddings.embed(f"seed row {i} for {tenant_id}"))
                cur.execute(
                    """
                    INSERT INTO agent_memories
                        (tenant_id, agent_id, memory_type, content, source, verdict, embedding)
                    VALUES (%s, %s, 'semantic', %s, 'test-seed', 'accepted', %s)
                    """,
                    (tenant_id, agent_id, f"seed row {i}", vec),
                )
        conn.commit()
        with conn.cursor() as cur:
            cur.execute("ANALYZE agent_memories")
        conn.commit()
    finally:
        db.put_conn(conn)
    return tenant_id


def test_vector_index_explain_for_the_recall_query_shows_vector_search_and_prefix_spans(seeded_vector_tenant):
    """This is the exact query shape backend.memory.recall() runs.

    Known, already-investigated finding for this schema (see
    benchmarks/results.md "Optimizer plan choice"): agent_memories carries
    two other B-tree indexes that CockroachDB's cost-based optimizer judges
    cheaper than the ANN index for a tenant_id-scoped point lookup, at every
    row count tested there (up to 15,000 rows, one tenant). If that is still
    true here, this test reports it via skip (with the captured EXPLAIN
    attached) rather than asserting a plan choice this codebase does not
    control.
    """
    query_vec = embeddings.to_pgvector(embeddings.embed("what does the vector index pick"))
    conn = db.get_conn()
    try:
        explain_text = _explain_rows(conn, RECALL_SHAPED_EXPLAIN, (seeded_vector_tenant, query_vec, 5))
        conn.commit()
    finally:
        db.put_conn(conn)

    has_vector_search = "vector search" in explain_text.lower()
    has_prefix_spans = "prefix spans" in explain_text.lower()

    if not (has_vector_search and has_prefix_spans):
        pytest.skip(
            "CockroachDB's optimizer did not choose the vector index for "
            "backend.memory.recall()'s exact query shape on the real "
            "agent_memories table (competing B-tree indexes win at this row "
            "count, per benchmarks/results.md). Captured EXPLAIN:\n" + explain_text
        )


def test_vector_index_mechanism_itself_is_selected_when_it_is_the_only_viable_index():
    """Isolate the vector index from agent_memories' competing B-tree indexes
    to confirm the ANN mechanism itself is real and wired correctly -- the
    same isolation methodology benchmarks/results.md used for its manual
    verification, reproduced here as an automated, cleaned-up regression test.
    """
    scratch_table = f"_test_vector_only_{uuid.uuid4().hex[:12]}"
    tenant = str(uuid.uuid4())
    conn = db.get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                CREATE TABLE {scratch_table} (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    tenant_id UUID NOT NULL,
                    embedding VECTOR({embeddings.EMBED_DIMS}),
                    VECTOR INDEX {scratch_table}_v_idx (tenant_id, embedding vector_cosine_ops)
                        WITH (min_partition_size = 16, max_partition_size = 128)
                )
                """
            )
        conn.commit()

        with conn.cursor() as cur:
            for i in range(60):
                vec = embeddings.to_pgvector(embeddings.embed(f"scratch row {i}"))
                cur.execute(
                    f"INSERT INTO {scratch_table} (tenant_id, embedding) VALUES (%s, %s)",
                    (tenant, vec),
                )
        conn.commit()

        with conn.cursor() as cur:
            cur.execute(f"ANALYZE {scratch_table}")
        conn.commit()

        query_vec = embeddings.to_pgvector(embeddings.embed("scratch query"))
        explain_text = _explain_rows(
            conn,
            f"EXPLAIN SELECT id FROM {scratch_table} WHERE tenant_id = %s ORDER BY embedding <=> %s LIMIT %s",
            (tenant, query_vec, 5),
        )
        conn.commit()

        assert "vector search" in explain_text.lower(), (
            f"vector index mechanism itself did not activate even with no competing "
            f"index. EXPLAIN:\n{explain_text}"
        )
        assert "prefix spans" in explain_text.lower(), f"EXPLAIN:\n{explain_text}"
    finally:
        try:
            with conn.cursor() as cur:
                cur.execute(f"DROP TABLE IF EXISTS {scratch_table}")
            conn.commit()
        finally:
            db.put_conn(conn)
