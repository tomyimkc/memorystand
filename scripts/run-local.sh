#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Standing -- bring up everything locally, from nothing, with one command.
#
#   ./scripts/run-local.sh          start the database, apply the schema, seed it
#   ./scripts/run-local.sh --fresh  wipe and rebuild from scratch
#   ./scripts/run-local.sh --serve  ...and then run the API + open the dashboard
#
# No AWS account and no CockroachDB Cloud account needed. Embeddings fall back to a
# deterministic local stub, which is announced rather than hidden: latency numbers stay
# honest, retrieval *relevance* does not become meaningful until real Bedrock embeddings
# are wired in.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

CONTAINER="${STANDING_CONTAINER:-crdb-standing}"
VOLUME="${STANDING_VOLUME:-crdb-standing-data}"
SQL_PORT="${STANDING_SQL_PORT:-26257}"
UI_PORT="${STANDING_UI_PORT:-8080}"
API_PORT="${STANDING_LOCAL_PORT:-8077}"
IMAGE="cockroachdb/cockroach:latest"
DSN="postgresql://root@localhost:${SQL_PORT}/defaultdb?sslmode=disable"

FRESH=0
SERVE=0
for arg in "$@"; do
  case "$arg" in
    --fresh) FRESH=1 ;;
    --serve) SERVE=1 ;;
    -h|--help) sed -n '3,12p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
need() { command -v "$1" >/dev/null 2>&1 || { echo "missing required tool: $1" >&2; exit 1; }; }

need docker

say "1/5  Database"
if [[ "$FRESH" == "1" ]]; then
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  docker volume rm "$VOLUME" >/dev/null 2>&1 || true
  echo "    wiped container and volume"
fi

if docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  echo "    '$CONTAINER' already running"
elif docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  docker start "$CONTAINER" >/dev/null
  echo "    restarted '$CONTAINER'"
else
  # A NAMED VOLUME, not --store=type=mem. An in-memory store loses every memory the
  # moment Docker restarts, which is a poor property for a project about durable memory.
  docker volume create "$VOLUME" >/dev/null
  docker run -d --name "$CONTAINER" \
    -p "${SQL_PORT}:26257" -p "${UI_PORT}:8080" \
    -v "${VOLUME}:/cockroach/cockroach-data" \
    "$IMAGE" start-single-node --insecure >/dev/null
  echo "    created '$CONTAINER' with persistent volume '$VOLUME'"
fi

printf '    waiting for SQL'
for _ in $(seq 1 60); do
  if docker exec "$CONTAINER" ./cockroach sql --insecure -e 'SELECT 1' >/dev/null 2>&1; then
    printf ' ready\n'; break
  fi
  printf '.'; sleep 1
done
docker exec "$CONTAINER" ./cockroach sql --insecure -e 'SELECT 1' >/dev/null 2>&1 || {
  echo; echo "database did not become ready; check: docker logs $CONTAINER" >&2; exit 1; }
echo "    $(docker exec "$CONTAINER" ./cockroach sql --insecure -e 'SELECT version()' 2>/dev/null | tail -1 | cut -d'(' -f1)"

say "2/5  Python environment"
# Pick an interpreter that has binary wheels for our dependencies. Falling back to a
# source build of psycopg2 fails with "pg_config executable not found", which looks like
# a missing PostgreSQL install and is actually just a missing wheel.
PY_BIN=""
for candidate in python3.13 python3.12 python3.11 python3; do
  command -v "$candidate" >/dev/null 2>&1 || continue
  PY_BIN="$candidate"; break
done
[[ -n "$PY_BIN" ]] || { echo "no python3 found" >&2; exit 1; }
echo "    using $PY_BIN ($($PY_BIN --version 2>&1))"

if [[ ! -x .venv/bin/python ]]; then
  "$PY_BIN" -m venv .venv
  .venv/bin/pip install --quiet --upgrade pip
  echo "    created .venv"
fi
# --only-binary=:all: turns a silent, confusing source build into a clear error.
if ! .venv/bin/pip install --quiet --only-binary=:all: -r requirements.txt; then
  echo "    dependency install failed -- no binary wheel for $(.venv/bin/python --version 2>&1)." >&2
  echo "    Recreate with a supported interpreter:  rm -rf .venv && python3.12 -m venv .venv" >&2
  exit 1
fi
[[ -f requirements-dev.txt ]] && .venv/bin/pip install --quiet -r requirements-dev.txt
echo "    dependencies installed"

export STANDING_DSN="$DSN"
export COCKROACH_DSN="$DSN"
export STANDING_EMBED_STUB=1

say "3/5  Schema"
docker exec -i "$CONTAINER" ./cockroach sql --insecure < db/schema.sql 2>&1 \
  | grep -Ei '^ERROR' && { echo "schema failed" >&2; exit 1; }
echo "    four tables + the prefix-partitioned vector index are in place"

say "4/5  Seed data"
.venv/bin/python db/seed/seed.py 2>&1 | grep -E 'accepted:|quarantined:|already has|Loaded' || true

say "5/5  Statistics"
docker exec "$CONTAINER" ./cockroach sql --insecure -e 'ANALYZE agent_memories;' >/dev/null 2>&1
echo "    ANALYZE done -- without this the optimizer ignores the vector index"

cat <<EOF

------------------------------------------------------------------
Ready. Everything below runs with no AWS account.

  export STANDING_DSN='$DSN'
  export STANDING_EMBED_STUB=1

Watch the whole story end to end (this is the video's shot list):
  ./scripts/demo.sh

Run the tests:
  .venv/bin/python -m pytest -q

Ask the memory something:
  .venv/bin/python cli/standing.py recall --query "payments failover"

Cross-examine a past decision:
  .venv/bin/python cli/standing.py cross-examine --decision-id <id>

Re-measure the benchmark:
  .venv/bin/python scripts/loadtest.py --rows 10000 --tenants 50

Database console: http://localhost:${UI_PORT}
------------------------------------------------------------------
EOF

if [[ "$SERVE" == "1" ]]; then
  say "API + dashboard"
  echo "    API:       http://127.0.0.1:${API_PORT}"
  echo "    Dashboard: open frontend/index.html?api=http://127.0.0.1:${API_PORT}"
  echo "    Ctrl-C to stop."
  STANDING_LOCAL_PORT="$API_PORT" exec .venv/bin/python backend/handler.py
fi
