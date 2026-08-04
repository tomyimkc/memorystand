#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Live proof that CockroachDB SERIALIZABLE protects MemoryStand's memory layer
under concurrent agents, driven entirely through the real application code
(backend.db.retry_serializable, backend.memory.remember) -- not a synthetic
harness that only pretends to use the retry path.

Two properties are proved, each with a self-checking assertion:

  1. Lost-update freedom. N agent processes concurrently read-modify-write a
     counter on the SAME agent_memories row (the same read-then-write shape
     as any confidence/trust_tier update). If SERIALIZABLE isolation and the
     retry path are doing their job, every one of the N increments lands and
     none is silently overwritten by another writer's stale read. The script
     asserts the final tally is EXACT.

  2. The TOCTOU admission guard. Two agents concurrently submit CONTRADICTORY
     memories for the same (tenant, entity, attribute_key) through the real
     backend.memory.remember() admission path. backend/memory.py performs its
     conflict check and its INSERT inside one SERIALIZABLE transaction
     specifically so a concurrent contradicting write forces a 40001 retry
     that re-decides against the fresh state (see the module docstring
     there). Exactly one submission must end up 'accepted'; the other must
     end up 'quarantined'. The script asserts that too.

Usage:
    export COCKROACH_DSN='postgresql://root@localhost:26257/defaultdb?sslmode=disable'
    python scripts/race_demo.py [--writers 10] [--output benchmarks/concurrency.md]

Exit code is non-zero if either assertion fails -- a failed assertion here is
a real correctness finding, not something this script papers over.
"""

from __future__ import annotations

import argparse
import multiprocessing
import os
import random
import sys
import time
import traceback
import uuid
from pathlib import Path
from typing import Any

import psycopg2

# `python scripts/race_demo.py` puts scripts/ (not the repo root) on sys.path[0],
# so the sibling `backend` package would otherwise be unimportable. Insert the
# repo root explicitly -- this also makes each spawned worker process (which
# re-executes this same import chain, see run_part1/run_part2) able to import
# `backend` too, since multiprocessing's spawn context re-runs this module.
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

DEFAULT_DSN = "postgresql://root@localhost:26257/defaultdb?sslmode=disable"
# Respect an operator-supplied MEMORYSTAND_DSN / COCKROACH_DSN; only fill in the
# known-good local default (see the platform facts this script was written
# against) if neither is already set.
os.environ.setdefault("COCKROACH_DSN", DEFAULT_DSN)

MAX_OUTER_RESTARTS = 25  # application-level restarts around a *whole* retry_serializable
# call, for when contention is so high the library's own bounded retry budget
# (backend.db.MAX_ATTEMPTS = 5) is exhausted. Each restart is itself a fresh,
# fully legitimate pass through the real retry path -- see backend/db.py's
# docstring: "an agent memory write that cannot commit after this many
# attempts is a signal worth surfacing, not something to hide behind an
# unbounded loop." We surface every one of these in the report rather than
# hiding them inside a bigger MAX_ATTEMPTS.

WRITER_ESCALATION_CAP = 4  # how many times Part 1 will double the writer count
# chasing a real 40001 before giving up and reporting honestly that none was
# observed at any tested concurrency level.


# ---------------------------------------------------------------------------
# Part 1: lost-update proof via a real read-modify-write on one memory row.
# ---------------------------------------------------------------------------


def _bump_counter(conn, *, memory_id: str, worker_id: int, capture_box: list) -> int:
    """Read-modify-write the demo counter inside ONE SERIALIZABLE transaction.

    This is deliberately the textbook lost-update shape: SELECT the current
    value, compute the new one in Python, UPDATE. Under any isolation level
    weaker than CockroachDB's SERIALIZABLE, two concurrent calls can both
    read N and both write N+1 -- one increment silently vanishes. Under
    SERIALIZABLE the second committer is forced to abort with SQLSTATE 40001
    and retry against the fresh value instead.

    The counter lives in agent_memories.structured_data (a JSONB column with
    no CHECK constraint) rather than in `confidence`, because `confidence`
    carries `CHECK (confidence BETWEEN 0 AND 1)` in db/schema.sql -- a real
    production constraint this demo must not fight or loosen just to get a
    round number. structured_data is the same real row, updated through the
    same real retry path; it just gives an exact integer tally instead of a
    bounded float one.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COALESCE((structured_data->>'race_counter')::INT, 0) "
            "FROM agent_memories WHERE memory_id = %s",
            (memory_id,),
        )
        row = cur.fetchone()
        current = row[0] if row else 0
        new_value = current + 1
        try:
            cur.execute(
                "UPDATE agent_memories "
                "SET structured_data = jsonb_set("
                "      COALESCE(structured_data, '{}'::jsonb), '{race_counter}', to_jsonb(%s::int)"
                "    ) "
                "WHERE memory_id = %s",
                (new_value, memory_id),
            )
        except psycopg2.Error as exc:
            if getattr(exc, "pgcode", None) == "40001":
                tb = traceback.format_exc()
                capture_box.append({"worker_id": worker_id, "traceback": tb, "repr": repr(exc)})
                print(
                    f"\n--- worker {worker_id}: real SQLSTATE 40001 captured live ---\n{tb}"
                    "--- end capture; backend.db.retry_serializable will retry this automatically ---\n",
                    flush=True,
                )
            raise
    return new_value


def _writer_proc(worker_id: int, memory_id: str, barrier, result_queue) -> None:
    """One simulated concurrent agent process incrementing the shared counter.

    Runs as its own OS process (spawn context) rather than a thread. This
    matches backend/db.py's own operating model: one pooled connection per
    process, exactly as one Lambda execution environment holds exactly one
    connection. N processes here == N concurrent Lambda invocations.
    """
    from backend import db  # imported inside the child: fresh pool per process

    capture_box: list = []
    outer_attempts = 0
    outer_restarts = 0
    barrier.wait()
    start = time.monotonic()
    try:
        while True:
            outer_attempts += 1
            try:
                new_value = db.retry_serializable(
                    _bump_counter, memory_id=memory_id, worker_id=worker_id, capture_box=capture_box
                )
                break
            except db.RetryBudgetExhausted:
                outer_restarts += 1
                if outer_attempts >= MAX_OUTER_RESTARTS:
                    result_queue.put(
                        {
                            "worker_id": worker_id,
                            "ok": False,
                            "error": f"RetryBudgetExhausted {outer_attempts} times in a row",
                            "captured_40001": capture_box,
                        }
                    )
                    return
                time.sleep(random.uniform(0, 0.05))
                continue
        elapsed = time.monotonic() - start
        result_queue.put(
            {
                "worker_id": worker_id,
                "ok": True,
                "final_value_seen": new_value,
                "retries_observed": db.retries_observed(),
                "outer_restarts": outer_restarts,
                "captured_40001": capture_box,
                "elapsed_s": elapsed,
            }
        )
    except Exception as exc:  # noqa: BLE001 - report, don't crash the pool silently
        result_queue.put(
            {
                "worker_id": worker_id,
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "captured_40001": capture_box,
            }
        )


def run_part1(writers: int, report: list[str]) -> dict[str, Any]:
    """Spawn `writers` concurrent processes incrementing one memory row.

    Returns a dict the caller uses both to print the required summary line
    and to decide whether contention needs to be escalated to force a real
    40001.
    """
    from backend import db, memory

    tenant_id = memory.new_id()
    agent_id = memory.new_id()
    base = memory.remember(
        tenant_id,
        agent_id,
        content="race_demo.py shared counter row (Part 1: lost-update proof)",
        entity="race-demo-counter",
        attribute_key="purpose",
        attribute_value="concurrency-proof",
        memory_type="semantic",
    )
    assert base["verdict"] == "accepted", f"setup row was not accepted: {base}"
    memory_id = base["memory_id"]

    ctx = multiprocessing.get_context("spawn")
    barrier = ctx.Barrier(writers)
    q: multiprocessing.Queue = ctx.Queue()
    procs = [ctx.Process(target=_writer_proc, args=(i, memory_id, barrier, q)) for i in range(writers)]

    t0 = time.monotonic()
    for p in procs:
        p.start()
    for p in procs:
        p.join()
    wall_elapsed = time.monotonic() - t0

    results = []
    while not q.empty():
        results.append(q.get())

    landed = sum(1 for r in results if r.get("ok"))
    failed = [r for r in results if not r.get("ok")]
    total_retries = sum(r.get("retries_observed", 0) for r in results if r.get("ok"))
    total_outer_restarts = sum(r.get("outer_restarts", 0) for r in results if r.get("ok"))
    captured = [c for r in results for c in r.get("captured_40001", [])]

    conn = db.get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COALESCE((structured_data->>'race_counter')::INT, 0) "
                "FROM agent_memories WHERE memory_id = %s",
                (memory_id,),
            )
            final_value = cur.fetchone()[0]
        conn.commit()
    finally:
        db.put_conn(conn)

    lost = writers - final_value

    report.append(f"### Part 1: lost-update proof (writers={writers})\n")
    report.append(f"- memory row: `{memory_id}` (tenant `{tenant_id}`)")
    report.append(f"- wall time for {writers} concurrent processes: {wall_elapsed:.3f}s")
    report.append(f"- workers that landed: {landed}/{writers}")
    if failed:
        report.append(f"- workers that FAILED: {len(failed)} -> {failed}")
    report.append(f"- final counter value read back from CockroachDB: {final_value}")
    report.append(f"- retries observed via `backend.db.retries_observed()`: {total_retries}")
    report.append(
        f"- application-level outer restarts (backend.db.RetryBudgetExhausted, "
        f"budget={5}): {total_outer_restarts}"
    )
    report.append(f"- real SQLSTATE 40001 tracebacks captured live: {len(captured)}")

    print(
        f"\n[Part 1] writers={writers} landed={landed}/{writers} final_value={final_value} "
        f"retries={total_retries} outer_restarts={total_outer_restarts} captured_40001={len(captured)} "
        f"wall={wall_elapsed:.3f}s"
    )

    return {
        "writers": writers,
        "landed": landed,
        "failed": failed,
        "final_value": final_value,
        "lost": lost,
        "total_retries": total_retries,
        "total_outer_restarts": total_outer_restarts,
        "captured": captured,
        "wall_elapsed": wall_elapsed,
        "memory_id": memory_id,
        "tenant_id": tenant_id,
    }


# ---------------------------------------------------------------------------
# Part 2: TOCTOU admission guard via two contradictory concurrent remembers().
# ---------------------------------------------------------------------------


def _contradiction_writer_proc(
    worker_id: int,
    tenant_id: str,
    agent_id: str,
    entity: str,
    attribute_key: str,
    attribute_value: str,
    barrier,
    result_queue,
) -> None:
    """One of two agents concurrently asserting a different value for the
    SAME (tenant, entity, attribute_key) through the real admission path
    (backend.memory.remember). No source is given on either side, so
    backend/memory.py's authority ranking treats them as equals -- neither
    can supersede the other, so a genuine contradiction must be quarantined,
    not silently resolved.
    """
    from backend import memory

    barrier.wait()
    try:
        result = memory.remember(
            tenant_id,
            agent_id,
            content=f"writer {worker_id} claims {entity}.{attribute_key} = {attribute_value!r}",
            entity=entity,
            attribute_key=attribute_key,
            attribute_value=attribute_value,
            memory_type="semantic",
        )
        result_queue.put({"worker_id": worker_id, "ok": True, "attribute_value": attribute_value, **result})
    except Exception as exc:  # noqa: BLE001
        result_queue.put(
            {
                "worker_id": worker_id,
                "ok": False,
                "attribute_value": attribute_value,
                "error": f"{type(exc).__name__}: {exc}",
            }
        )


def run_part2(report: list[str]) -> dict[str, Any]:
    from backend import db, memory

    tenant_id = memory.new_id()
    agent_id = memory.new_id()
    entity = f"race-demo-toctou-{uuid.uuid4().hex[:8]}"
    attribute_key = "reads_from_table"
    values = ("orders_v2", "orders_v3")

    ctx = multiprocessing.get_context("spawn")
    barrier = ctx.Barrier(2)
    q: multiprocessing.Queue = ctx.Queue()
    procs = [
        ctx.Process(
            target=_contradiction_writer_proc,
            args=(i, tenant_id, agent_id, entity, attribute_key, values[i], barrier, q),
        )
        for i in range(2)
    ]

    t0 = time.monotonic()
    for p in procs:
        p.start()
    for p in procs:
        p.join()
    wall_elapsed = time.monotonic() - t0

    results = []
    while not q.empty():
        results.append(q.get())
    results.sort(key=lambda r: r["worker_id"])

    report.append("\n### Part 2: TOCTOU admission guard (two contradictory concurrent remembers)\n")
    report.append(f"- tenant `{tenant_id}`, entity `{entity}`, attribute_key `{attribute_key}`")
    report.append(f"- wall time for both concurrent submissions: {wall_elapsed:.3f}s")

    for r in results:
        if r["ok"]:
            report.append(
                f"- writer {r['worker_id']} (value={r['attribute_value']!r}): "
                f"verdict=`{r['verdict']}`, reasons={r['verdict_reasons']}, memory_id=`{r['memory_id']}`"
            )
        else:
            report.append(f"- writer {r['worker_id']} (value={r['attribute_value']!r}): ERROR {r['error']}")

    ok_results = [r for r in results if r["ok"]]
    verdicts = [r["verdict"] for r in ok_results]

    # Independent verification: re-read straight from the table, not from what
    # the workers claim, so a bug in a worker's own bookkeeping can't hide a
    # real double-accept.
    conn = db.get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT memory_id::string, verdict, attribute_value FROM agent_memories "
                "WHERE tenant_id = %s AND entity = %s AND attribute_key = %s ORDER BY created_at",
                (tenant_id, entity, attribute_key),
            )
            rows = cur.fetchall()
        conn.commit()
    finally:
        db.put_conn(conn)

    accepted_rows = [r for r in rows if r[1] == "accepted"]
    quarantined_rows = [r for r in rows if r[1] == "quarantined"]

    passed = (
        len(results) == 2
        and len(ok_results) == 2
        and len(rows) == 2
        and len(accepted_rows) == 1
        and len(quarantined_rows) == 1
    )

    report.append(f"- independently re-read from `agent_memories`: {rows}")
    report.append(f"- **TOCTOU guard: {'PASS' if passed else 'FAIL'}** "
                   f"(expected exactly 1 accepted + 1 quarantined for this entity+attribute_key)")

    print(
        f"\n[Part 2] worker verdicts={verdicts} db_rows={rows} "
        f"accepted={len(accepted_rows)} quarantined={len(quarantined_rows)} "
        f"-> {'PASS' if passed else 'FAIL'} (wall={wall_elapsed:.3f}s)"
    )

    return {
        "passed": passed,
        "results": results,
        "rows": rows,
        "wall_elapsed": wall_elapsed,
        "tenant_id": tenant_id,
        "entity": entity,
    }


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--writers", type=int, default=10, help="concurrent writers for Part 1 (default: 10)")
    ap.add_argument(
        "--output", default="benchmarks/concurrency.md", help="where to write the markdown report"
    )
    args = ap.parse_args()

    print("=" * 78)
    print("MemoryStand -- scripts/race_demo.py: SERIALIZABLE concurrency proof")
    print("=" * 78)

    report: list[str] = []
    report.append("# Concurrency proof: SERIALIZABLE isolation under concurrent agents\n")
    report.append(
        "Generated by `scripts/race_demo.py`, run directly against the real "
        "`backend.db.retry_serializable` / `backend.memory.remember` code paths -- "
        "no isolation from the application, no mocked database calls.\n"
    )
    report.append(f"- DSN: `{os.environ.get('MEMORYSTAND_DSN') or os.environ.get('COCKROACH_DSN')}`")
    report.append(f"- requested writers: {args.writers}\n")

    overall_ok = True

    # ---- Part 1, escalating writer count until a real 40001 is captured ----
    writers = args.writers
    part1 = run_part1(writers, report)
    escalations = 0
    while not part1["captured"] and escalations < WRITER_ESCALATION_CAP:
        escalations += 1
        msg = (
            f"No real SQLSTATE 40001 observed at writers={writers}. Escalating contention "
            f"(attempt {escalations}/{WRITER_ESCALATION_CAP}): doubling writers and re-running, "
            "as instructed -- not pretending a conflict happened."
        )
        print(f"\n[Part 1] {msg}")
        report.append(f"\n> {msg}\n")
        writers *= 2
        part1 = run_part1(writers, report)

    if not part1["captured"]:
        msg = (
            f"No real SQLSTATE 40001 was observed even after escalating to writers={writers} "
            f"({WRITER_ESCALATION_CAP} escalations). Reporting this honestly: the lost-update "
            "assertion below still holds (SERIALIZABLE prevented any lost update whether or not "
            "a conflict was actually forced), but the live-traceback requirement was not met at "
            "the concurrency levels this run reached."
        )
        print(f"\n[Part 1] {msg}")
        report.append(f"\n**NOTE:** {msg}\n")

    part1_pass = part1["landed"] == part1["writers"] and part1["lost"] == 0 and not part1["failed"]

    if not part1_pass:
        fail_msg = (
            f"[Part 1] FAIL: landed={part1['landed']}/{part1['writers']}, "
            f"lost={part1['lost']}, failed_workers={part1['failed']}"
        )
        print(f"\n{fail_msg}")
        report.append(f"\n**Part 1 result: FAIL** -- {fail_msg}\n")
        overall_ok = False
    else:
        summary_line = (
            f"N={part1['writers']} concurrent writers, {part1['landed']}/{part1['writers']} "
            f"updates landed, {part1['lost']} lost updates, {part1['total_retries']} retries "
            f"observed (SQLSTATE 40001)"
        )
        print(f"\n{summary_line}")
        report.append(f"\n**Summary:** `{summary_line}`\n")
        if part1["captured"]:
            first = part1["captured"][0]
            report.append("**Captured live traceback (first occurrence):**\n")
            report.append("```")
            report.append(first["traceback"].rstrip())
            report.append("```\n")

    # ---- Part 2 ----
    part2 = run_part2(report)
    if not part2["passed"]:
        overall_ok = False
        print(
            "\n[Part 2] FAIL: the TOCTOU admission guard did not resolve to exactly one "
            "accepted + one quarantined memory. This is a real correctness finding, not "
            "a flaky test -- see the report for the raw rows."
        )

    report.append("\n## Overall result\n")
    report.append(f"- Part 1 (lost-update freedom): {'PASS' if part1_pass else 'FAIL'}")
    report.append(f"- Part 2 (TOCTOU admission guard): {'PASS' if part2['passed'] else 'FAIL'}")
    report.append(f"- **{'ALL CHECKS PASSED' if overall_ok else 'AT LEAST ONE CHECK FAILED'}**\n")

    out_path = args.output
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(report) + "\n")
    print(f"\nWrote report to {out_path}")

    print("=" * 78)
    print("PASS" if overall_ok else "FAIL", "-- race_demo.py finished")
    print("=" * 78)

    return 0 if overall_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
