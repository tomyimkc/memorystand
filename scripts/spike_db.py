#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Day-1 CockroachDB spikes. Run this BEFORE writing any application code.

Each spike answers one go/no-go question that gates a downstream week of work.
Nothing here is destructive: every object created is dropped, and the script
never touches the real Standing tables.

    export COCKROACH_DSN='postgresql://user:pass@host:26257/defaultdb?sslmode=verify-full'
    python scripts/spike_db.py

Writes a machine-readable summary to spike_db_results.json and prints a table.
Exit code is 0 even when a spike fails -- a failed spike is a *finding*, not a
crash, and the fallbacks are already designed. Read the output.
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback

try:
    import psycopg2
except ImportError:  # pragma: no cover - operator-facing message
    sys.exit("psycopg2 not installed. Run: pip install -r requirements.txt")

DSN_ENV = "COCKROACH_DSN"
RESULTS_PATH = "spike_db_results.json"

results: list[dict] = []


def record(num: int, name: str, ok: bool, finding: str, fallback: str = "") -> None:
    results.append(
        {"spike": num, "name": name, "ok": ok, "finding": finding, "fallback": fallback}
    )
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] Spike {num}: {name}\n       {finding}")
    if not ok and fallback:
        print(f"       -> fallback: {fallback}")
    print()


def spike_0_connectivity(conn) -> None:
    """Sanity: we can actually talk to the cluster, and which version."""
    with conn.cursor() as cur:
        cur.execute("SELECT version()")
        version = cur.fetchone()[0]
        cur.execute("SHOW CLUSTER SETTING version")
        logical = cur.fetchone()[0]
    record(0, "connectivity", True, f"{version.split(' (')[0]} | logical version {logical}")


def spike_1_vector_index(conn) -> bool:
    """Is vector indexing permitted on this cluster (Cloud Basic)?

    This is the single highest-stakes unknown: the 'production-grade at scale'
    argument for judging criterion 1 leans on a prefix-partitioned vector index.
    """
    name = "vector index on Cloud Basic"
    try:
        with conn.cursor() as cur:
            try:
                cur.execute("SET CLUSTER SETTING feature.vector_index.enabled = true")
                conn.commit()
                flag = "cluster setting accepted"
            except Exception as exc:  # noqa: BLE001 - the setting may be unavailable or already on
                conn.rollback()
                flag = f"cluster setting rejected ({type(exc).__name__}); trying index anyway"

            cur.execute("DROP TABLE IF EXISTS _spike_vec")
            cur.execute(
                "CREATE TABLE _spike_vec ("
                "  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),"
                "  tenant_id UUID NOT NULL,"
                "  v VECTOR(3),"
                "  VECTOR INDEX _spike_vec_idx (tenant_id, v vector_cosine_ops)"
                ")"
            )
            conn.commit()

            tenant = "00000000-0000-0000-0000-000000000001"
            for vec in ("[1,0,0]", "[0,1,0]", "[0.9,0.1,0]"):
                cur.execute(
                    "INSERT INTO _spike_vec (tenant_id, v) VALUES (%s, %s)", (tenant, vec)
                )
            conn.commit()

            cur.execute(
                "SELECT id FROM _spike_vec WHERE tenant_id = %s "
                "ORDER BY v <=> %s LIMIT 2",
                (tenant, "[1,0,0]"),
            )
            hits = cur.fetchall()

            cur.execute(
                "EXPLAIN SELECT id FROM _spike_vec WHERE tenant_id = %s "
                "ORDER BY v <=> %s LIMIT 2",
                (tenant, "[1,0,0]"),
            )
            plan = "\n".join(r[0] for r in cur.fetchall())

        uses_index = "vector" in plan.lower() and "index" in plan.lower()
        record(
            1,
            name,
            True,
            f"{flag}; VECTOR INDEX created, {len(hits)} neighbours returned; "
            f"plan {'uses a vector index scan' if uses_index else 'did NOT show an index scan'}",
        )
        print("--- EXPLAIN (capture this for the README) ---")
        print(plan)
        print("--- end EXPLAIN ---\n")
        return True
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        record(
            1,
            name,
            False,
            f"{type(exc).__name__}: {exc}",
            "Delete the VECTOR INDEX clause from db/schema.sql. Every query still runs "
            "verbatim as a brute-force `ORDER BY embedding <=> $1` scan. Say so in the README.",
        )
        return False
    finally:
        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS _spike_vec")
        conn.commit()


def spike_2_aost_window(conn) -> bool:
    """How far back does AS OF SYSTEM TIME actually reach?

    The flagship mechanic. If the GC threshold is short, the video's centrepiece
    beat errors on camera -- so find out now, not on Day 13.
    """
    name = "AS OF SYSTEM TIME reach / GC window"
    reach: list[str] = []
    failed_at: str | None = None
    try:
        with conn.cursor() as cur:
            try:
                cur.execute("SHOW ZONE CONFIGURATION FOR RANGE default")
                zone = "\n".join(str(r) for r in cur.fetchall())
                gc_line = next(
                    (ln for ln in zone.splitlines() if "gc.ttlseconds" in ln), "gc.ttlseconds not visible"
                )
            except Exception:  # noqa: BLE001 - zone config is often not readable on Basic
                conn.rollback()
                gc_line = "zone configuration not readable on this tier"

            for lag in ("-10s", "-5m", "-1h", "-6h", "-24h", "-72h"):
                try:
                    cur.execute(f"SELECT 1 AS OF SYSTEM TIME '{lag}'")
                    cur.fetchone()
                    reach.append(lag)
                except Exception as exc:  # noqa: BLE001
                    conn.rollback()
                    failed_at = f"{lag} ({type(exc).__name__})"
                    break

        deepest = reach[-1] if reach else "nothing beyond now"
        ok = bool(reach) and reach[-1] not in ("-10s",)
        record(
            2,
            name,
            ok,
            f"{gc_line.strip()} | AOST succeeded at: {', '.join(reach) or 'none'} | "
            f"first failure: {failed_at or 'none in range'} | deepest verified: {deepest}",
            ""
            if ok
            else "Pin every demo AOST query to a timestamp captured earlier in the SAME "
            "session, state the retention bound in the README, and let belief_snapshots "
            "carry the beyond-window story.",
        )
        return ok
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        record(2, name, False, f"{type(exc).__name__}: {exc}", "See fallback in the plan, FIX-2.")
        return False


def spike_2b_aost_with_vector(conn, vector_ok: bool) -> None:
    """Can AS OF SYSTEM TIME and a vector ORDER BY compose in one statement?

    The video's reveal beat depends on exactly this composition. Untested
    combinations are where demos die.
    """
    name = "AOST + vector ORDER BY compose"
    if not vector_ok:
        record(21, name, False, "skipped: vector index spike did not pass", "n/a")
        return
    try:
        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS _spike_aost_vec")
            cur.execute(
                "CREATE TABLE _spike_aost_vec ("
                "  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),"
                "  tenant_id UUID NOT NULL,"
                "  v VECTOR(3),"
                "  VECTOR INDEX _spike_aost_vec_idx (tenant_id, v vector_cosine_ops)"
                ")"
            )
            conn.commit()
            tenant = "00000000-0000-0000-0000-000000000002"
            cur.execute("INSERT INTO _spike_aost_vec (tenant_id, v) VALUES (%s, %s)", (tenant, "[1,0,0]"))
            conn.commit()
            time.sleep(2)
            cur.execute(
                "SELECT id FROM _spike_aost_vec AS OF SYSTEM TIME '-1s' "
                "WHERE tenant_id = %s ORDER BY v <=> %s LIMIT 1",
                (tenant, "[1,0,0]"),
            )
            cur.fetchall()
        record(21, name, True, "AS OF SYSTEM TIME composes with a vector ORDER BY in one statement")
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        record(
            21,
            name,
            False,
            f"{type(exc).__name__}: {exc}",
            "Split the reveal beat into two queries: AOST to fetch the historical row set, "
            "then rank in the application. Still a genuine time-travel proof.",
        )
    finally:
        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS _spike_aost_vec")
        conn.commit()


def spike_3_serializable_retry(conn_factory) -> None:
    """Confirm SQLSTATE 40001 is observable and retryable -- the concurrency proof beat."""
    name = "SERIALIZABLE conflict (40001) observable"
    a = b = None
    try:
        a, b = conn_factory(), conn_factory()
        with a.cursor() as ca, b.cursor() as cb:
            ca.execute("DROP TABLE IF EXISTS _spike_race")
            a.commit()
            ca.execute("CREATE TABLE _spike_race (id INT PRIMARY KEY, n INT NOT NULL)")
            ca.execute("INSERT INTO _spike_race VALUES (1, 0)")
            a.commit()

            ca.execute("BEGIN")
            ca.execute("SELECT n FROM _spike_race WHERE id = 1")
            ca.fetchone()
            cb.execute("BEGIN")
            cb.execute("SELECT n FROM _spike_race WHERE id = 1")
            cb.fetchone()
            ca.execute("UPDATE _spike_race SET n = n + 1 WHERE id = 1")
            a.commit()

            saw_40001 = False
            try:
                cb.execute("UPDATE _spike_race SET n = n + 1 WHERE id = 1")
                b.commit()
            except Exception as exc:  # noqa: BLE001
                saw_40001 = getattr(exc, "pgcode", "") == "40001"
                b.rollback()

        record(
            3,
            name,
            True,
            "live 40001 serialization failure captured -- usable for the video"
            if saw_40001
            else "no 40001 in this interleaving; race_demo.py needs more writers to force one",
        )
    except Exception as exc:  # noqa: BLE001
        record(3, name, False, f"{type(exc).__name__}: {exc}", "Retune scripts/race_demo.py.")
    finally:
        for c in (a, b):
            if c is not None:
                try:
                    with c.cursor() as cur:
                        cur.execute("DROP TABLE IF EXISTS _spike_race")
                    c.commit()
                    c.close()
                except Exception:  # noqa: BLE001, S110 - best-effort cleanup
                    pass


def main() -> int:
    dsn = os.environ.get(DSN_ENV)
    if not dsn:
        print(
            f"{DSN_ENV} is not set.\n\n"
            "Get one with:\n"
            "  ccloud cluster connection-string <cluster-name> --sql-user <user>\n",
            file=sys.stderr,
        )
        return 2

    def factory():
        return psycopg2.connect(dsn)

    print("=" * 70)
    print("Standing -- Day-1 CockroachDB spikes")
    print("=" * 70 + "\n")

    conn = factory()
    try:
        spike_0_connectivity(conn)
        vector_ok = spike_1_vector_index(conn)
        spike_2_aost_window(conn)
        spike_2b_aost_with_vector(conn, vector_ok)
        spike_3_serializable_retry(factory)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
    finally:
        conn.close()

    with open(RESULTS_PATH, "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2)

    passed = sum(1 for r in results if r["ok"])
    print("=" * 70)
    print(f"{passed}/{len(results)} spikes passed. Written to {RESULTS_PATH}")
    print("Transcribe these into SPIKE-RESULTS.md and commit it.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
