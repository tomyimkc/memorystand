#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# A real 3-node CockroachDB cluster, then kill a node while the agent is reading from it.
#
# This exists because the single most common criticism of an agentic-memory demo is fair:
# "would this work identically on single-node Postgres?" Everything else in this repo answers
# that with an argument. This answers it by unplugging a machine.
#
#   ./scripts/cluster-demo.sh up          start a 3-node cluster, apply schema, seed
#   ./scripts/cluster-demo.sh failover    kill a node mid-recall, show memory survive
#   ./scripts/cluster-demo.sh bench N     scale benchmark at N memories (default 100000)
#   ./scripts/cluster-demo.sh down        tear it all down
#
# Runs anywhere Docker does -- three containers on a laptop is enough for the failover
# story. A larger host only matters for `bench` at seven figures.
#
# Deliberately SEPARATE from the demo cluster run-local.sh manages: different container
# names, different ports, different volumes. Running this must never disturb a demo you
# are about to record.
set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." >/dev/null 2>&1 && pwd)"
cd "$REPO_ROOT"

IMAGE="cockroachdb/cockroach:latest"
NET="ms-cluster-net"
PREFIX="ms-node"
NODES=3
BASE_SQL_PORT=27257     # not 26257: run-local.sh owns that
BASE_UI_PORT=8090
DSN="postgresql://root@localhost:${BASE_SQL_PORT}/defaultdb?sslmode=disable"

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
note() { printf '    %s\n' "$*"; }
die()  { echo "ERROR: $*" >&2; exit 1; }

command -v docker >/dev/null 2>&1 || die "docker not found"
PY="$REPO_ROOT/.venv/bin/python"
[[ -x "$PY" ]] || PY="python3"

sql() { docker exec -i "${PREFIX}1" ./cockroach sql --insecure --host="${PREFIX}1:26257" "$@"; }

# `crdb_internal` is restricted in v26.2 -- reading it raises SQLSTATE 42501 and demands
# allow_unsafe_internals, and `SHOW NODES` does not exist. `cockroach node status` is the
# supported route. Always || true: a status display must never be able to abort the run
# under `set -e`, which is exactly how the first version left the schema unapplied.
node_status() {
  docker exec -i "${PREFIX}1" ./cockroach node status --insecure --host="${PREFIX}1:26257" \
    --format=table 2>/dev/null | cut -c1-96 | sed 's/^/    /' || true
}

cmd_up() {
  say "Network"
  docker network inspect "$NET" >/dev/null 2>&1 || docker network create "$NET" >/dev/null
  note "$NET"

  say "Starting $NODES nodes"
  for i in $(seq 1 $NODES); do
    name="${PREFIX}${i}"
    if docker ps --format '{{.Names}}' | grep -qx "$name"; then
      note "$name already running"; continue
    fi
    docker rm -f "$name" >/dev/null 2>&1 || true
    docker run -d --name "$name" --hostname "$name" --net "$NET" \
      -p "$((BASE_SQL_PORT + i - 1)):26257" -p "$((BASE_UI_PORT + i - 1)):8080" \
      -v "ms-cluster-data-${i}:/cockroach/cockroach-data" \
      "$IMAGE" start --insecure \
        --advertise-addr="${name}:26257" \
        --join="${PREFIX}1:26257,${PREFIX}2:26257,${PREFIX}3:26257" >/dev/null
    note "$name on localhost:$((BASE_SQL_PORT + i - 1))"
  done

  say "Initialising the cluster"
  sleep 6
  docker exec "${PREFIX}1" ./cockroach init --insecure --host="${PREFIX}1:26257" >/dev/null 2>&1 \
    && note "initialised" || note "already initialised"

  printf '    waiting for SQL'
  for _ in $(seq 1 60); do
    if sql -e 'SELECT 1' >/dev/null 2>&1; then printf ' ready\n'; break; fi
    printf '.'; sleep 1
  done
  sql -e 'SELECT 1' >/dev/null 2>&1 || die "cluster never became ready; docker logs ${PREFIX}1"

  say "Cluster members"
  node_status

  say "Replication"
  # THE POINT OF THIS WHOLE SCRIPT: with 3 nodes, every range is Raft-replicated 3x, so a
  # node can die without losing a single memory. On one node this number is 1 and the
  # failover demo below is impossible -- which is exactly why CockroachDB Cloud Basic
  # could not host it.
  sql -e "ALTER RANGE default CONFIGURE ZONE USING num_replicas = 3;" >/dev/null 2>&1 || true
  { sql --format=table -e "SHOW ZONE CONFIGURATION FOR RANGE default;" 2>/dev/null \
    | grep -i "num_replicas" | sed 's/^/    /'; } || true

  say "Schema and seed"
  sql < db/schema.sql 2>&1 | grep -Ei '^ERROR' && die "schema failed"
  note "schema applied"
  MEMORYSTAND_DSN="$DSN" COCKROACH_DSN="$DSN" MEMORYSTAND_EMBED_STUB=1 \
    "$PY" db/seed/seed.py 2>&1 | grep -E 'accepted:|quarantined:' | sed 's/^/    /' || true
  sql -e 'ANALYZE agent_memories;' >/dev/null 2>&1
  note "ANALYZE done"

  cat <<EOF

------------------------------------------------------------------
3-node cluster up.  export MEMORYSTAND_DSN='$DSN'
Console: http://localhost:${BASE_UI_PORT}

  ./scripts/cluster-demo.sh failover     kill a node mid-recall
  ./scripts/cluster-demo.sh bench 100000 scale benchmark
------------------------------------------------------------------
EOF
}

cmd_failover() {
  docker ps --format '{{.Names}}' | grep -qx "${PREFIX}1" || die "cluster not up; run: $0 up"

  say "Before: which nodes are live"
  node_status

  say "Reading memory continuously while a node dies"
  note "killing ${PREFIX}3 in 3 seconds -- recall should never stop returning rows"
  (sleep 3; docker kill "${PREFIX}3" >/dev/null 2>&1; echo "    *** ${PREFIX}3 KILLED ***") &
  KILLER=$!

  MEMORYSTAND_DSN="$DSN" COCKROACH_DSN="$DSN" MEMORYSTAND_EMBED_STUB=1 "$PY" - <<'PYEOF'
import time
from backend import memory, db

T = "9c8f6e5a-9d1a-4a1c-8f2e-3b6d1c7a4e10"
A = "1a2b3c4d-5e6f-4708-9a0b-1c2d3e4f5061"
ok = failed = 0
stamps = []
t0 = time.time()
while time.time() - t0 < 12:
    try:
        hits = memory.recall(T, A, "payments failover restart order", k=3)
        ok += 1
        stamps.append(time.time() - t0)
        print(f"    t+{time.time()-t0:4.1f}s  recall OK  ({len(hits)} memories)", flush=True)
    except Exception as exc:
        failed += 1
        print(f"    t+{time.time()-t0:4.1f}s  recall FAILED: {type(exc).__name__}", flush=True)
        db.close_pool()   # drop the connection bound to the dead node, then keep going
    time.sleep(1)
gaps = [round(b - a, 1) for a, b in zip(stamps, stamps[1:]) if b - a > 1.8]
print(f"\n    {ok} successful reads, {failed} failed, across a node loss")
if gaps:
    print(f"    longest pause between successful reads: {max(gaps)}s "
          f"(Raft lease transfer -- real, and not the same as zero interruption)")
PYEOF

  wait $KILLER 2>/dev/null || true

  say "After: is ${PREFIX}3 really gone?"
  # `cockroach node status` keeps listing a killed node until its liveness lease expires,
  # so it is NOT evidence of the kill. Ask the container runtime, which cannot be wrong.
  if docker ps --format '{{.Names}}' | grep -qx "${PREFIX}3"; then
    note "${PREFIX}3 is STILL RUNNING -- the kill did not take, treat the run above as invalid"
  else
    note "${PREFIX}3 is not in \`docker ps\` -- confirmed down"
  fi
  note "(node status below may still list it; liveness takes ~10s to expire)"
  node_status

  say "Is any memory lost?"
  { sql --format=table -e "SELECT count(*) AS memories_still_readable FROM agent_memories;" \
    2>/dev/null | sed 's/^/    /'; } || true

  say "Restoring ${PREFIX}3"
  docker start "${PREFIX}3" >/dev/null 2>&1 && note "restarted; it will rejoin and re-replicate"
}

cmd_bench() {
  local rows="${1:-100000}"
  docker ps --format '{{.Names}}' | grep -qx "${PREFIX}1" || die "cluster not up; run: $0 up"
  say "Scale benchmark: $rows memories on a 3-node cluster"
  note "single-node numbers live in benchmarks/results.md; this is the multi-node comparison"
  MEMORYSTAND_DSN="$DSN" COCKROACH_DSN="$DSN" MEMORYSTAND_EMBED_STUB=1 \
    "$PY" scripts/loadtest.py --rows "$rows" --tenants 200
  note "report written to benchmarks/results.md -- rename it before re-running single-node"
}

cmd_down() {
  say "Tearing down"
  for i in $(seq 1 $NODES); do docker rm -f "${PREFIX}${i}" >/dev/null 2>&1 && note "removed ${PREFIX}${i}" || true; done
  for i in $(seq 1 $NODES); do docker volume rm "ms-cluster-data-${i}" >/dev/null 2>&1 || true; done
  docker network rm "$NET" >/dev/null 2>&1 || true
  note "the run-local.sh demo cluster was not touched"
}

case "${1:-}" in
  up)       cmd_up ;;
  failover) cmd_failover ;;
  bench)    cmd_bench "${2:-100000}" ;;
  down)     cmd_down ;;
  *) sed -n '3,20p' "$0"; exit 2 ;;
esac
