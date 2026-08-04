#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# MemoryStand -- end-to-end demo. This IS the rehearsal harness and the spine of the
# under-3-minute submission video: every section below is a beat in that video, in
# order, using the real `memorystand` CLI wherever the beat does not need a full UUID
# fed into the next command (a few steps use `--json` for that reason and print a
# formatted summary instead of the CLI's own colour table -- noted inline).
#
# Usage:
#   scripts/demo.sh            # run straight through, no stops (what CI runs)
#   scripts/demo.sh --pause    # stop after every section for narration while filming
#
# Idempotent: every run resets ONE deterministic demo tenant (the `memorystand` CLI's
# own built-in demo identity, so the commands below never need --tenant-id) to empty
# and replays the identical scripted arc against it. Re-run as many times as you like.
#
# What this script deliberately does NOT do: seed the demo tenant with the large
# 101-row incident fixture (db/seed/incidents.jsonl). That fixture is real and used
# elsewhere (db/seed/seed.py, the ~104-memory tenant already sitting in this cluster),
# but MEMORYSTAND_EMBED_STUB's embeddings are a deterministic hash of the input text with
# NO semantic meaning (see backend/embeddings.py's own docstring) -- mixing 100+
# unrelated stub vectors into the recall step below would make which memory comes
# back a coin flip dressed up as "semantic search". A small, hand-authored backdrop
# keeps every step of the arc, including the vector recall, exactly reproducible.
# Step 7 below switches to the large, already-seeded fixture tenant specifically
# because THAT step's whole point is showing the ANN index at realistic volume.

set -euo pipefail

# Parameterised to match run-local.sh and record-demo.sh; hardcoding it here
# meant the three scripts disagreed about which container they were driving.
CONTAINER="${MEMORYSTAND_CONTAINER:-crdb-memorystand}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PAUSE=0
for arg in "$@"; do
  case "$arg" in
    --pause) PAUSE=1 ;;
    -h|--help)
      sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "unknown flag: $arg (supported: --pause)" >&2
      exit 2
      ;;
  esac
done

# ---------------------------------------------------------------------------
# Presentation helpers -- degrade to plain text off a TTY / piped into a log,
# same rule cli/memorystand.py uses for its own output.
# ---------------------------------------------------------------------------
if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
  BOLD=$'\033[1m'; CYAN=$'\033[1;36m'; DIM=$'\033[2m'; RESET=$'\033[0m'
else
  BOLD=""; CYAN=""; DIM=""; RESET=""
fi

banner() {
  printf '\n%s================================================================\n' "$CYAN"
  printf '%s\n' "$*"
  printf '================================================================%s\n\n' "$RESET"
}

say() { printf '%s%s%s\n' "$DIM" "$*" "$RESET"; }

pause() {
  if [[ "$PAUSE" -eq 1 ]]; then
    read -r -p "${DIM}-- press Enter to continue --${RESET}" _ || true
  fi
}

# ---------------------------------------------------------------------------
# 0. Prerequisites
# ---------------------------------------------------------------------------
banner "0. Prerequisites"

PYTHON="$REPO_ROOT/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  echo "error: $PYTHON not found. Create it first:" >&2
  echo "       python3.13 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi
command -v docker >/dev/null 2>&1 || { echo "error: docker not found on PATH" >&2; exit 1; }
command -v jq >/dev/null 2>&1 || { echo "error: jq not found on PATH (brew install jq)" >&2; exit 1; }

if ! docker ps --filter "name=^${CONTAINER}$" --filter "status=running" --format '{{.Names}}' \
     | grep -qx "$CONTAINER"; then
  echo "error: the "$CONTAINER" container is not running (docker ps shows no match)." >&2
  echo "       This script talks to an already-running cluster; it does not start one." >&2
  exit 1
fi

: "${MEMORYSTAND_DSN:=postgresql://root@localhost:26257/defaultdb?sslmode=disable}"
export MEMORYSTAND_DSN
export COCKROACH_DSN="$MEMORYSTAND_DSN"
export MEMORYSTAND_EMBED_STUB="${MEMORYSTAND_EMBED_STUB:-1}"
export PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"

CLI=("$PYTHON" "$REPO_ROOT/cli/memorystand.py")

DB_VERSION="$("$PYTHON" -c 'from backend import db; print(db.server_version())')"

# The CLI's own deterministic demo identity (uuid5, defined in cli/memorystand.py) --
# using it means every command below can omit --tenant-id/--agent-id entirely,
# which is what actually gets typed on camera.
read -r DEMO_TENANT DEMO_AGENT < <(
  "$PYTHON" -c 'from cli.memorystand import DEFAULT_TENANT_ID, DEFAULT_AGENT_ID; print(DEFAULT_TENANT_ID, DEFAULT_AGENT_ID)'
)
# The fixture tenant db/seed/seed.py already loaded ~104 memories into (see
# CLAUDE.md-style environment notes) -- imported, not hardcoded, so this script
# never drifts from that script's own constant.
FIXTURE_TENANT="$("$PYTHON" -c 'from db.seed.seed import DEFAULT_TENANT_ID; print(DEFAULT_TENANT_ID)')"
TASK_ID="$("$PYTHON" -c "import uuid; print(uuid.uuid5(uuid.NAMESPACE_URL, 'standing:demo-inc-7734'))")"

echo "DSN:              $MEMORYSTAND_DSN"
echo "CockroachDB:      $DB_VERSION"
echo "Embeddings:       deterministic local stub, no AWS account used (MEMORYSTAND_EMBED_STUB=$MEMORYSTAND_EMBED_STUB)"
echo "Demo tenant:      $DEMO_TENANT  (agent $DEMO_AGENT)"
echo "Fixture tenant:   $FIXTURE_TENANT  (~104 pre-seeded memories, used only in step 7)"
pause

# ---------------------------------------------------------------------------
# Reset the demo tenant to empty, then lay down a small, deterministic backdrop.
# Not the 101-row fixture -- see the file header for why.
# ---------------------------------------------------------------------------
banner "Reset: clear the demo tenant, then seed a small deterministic backdrop"

for t in tool_audit agent_decisions belief_snapshots agent_memories; do
  docker exec -i "$CONTAINER" ./cockroach sql --insecure \
    -e "DELETE FROM ${t} WHERE tenant_id = '${DEMO_TENANT}';" >/dev/null
done
echo "cleared agent_memories, agent_decisions, belief_snapshots, tool_audit for $DEMO_TENANT"

"${CLI[@]+"${CLI[@]}"}" remember --content "redis-session's cache uses an LRU eviction policy, per the caching runbook." \
  --entity redis-session --key cache_eviction_policy --value lru \
  --source runbook:redis-config >/dev/null
"${CLI[@]+"${CLI[@]}"}" remember --content "auth-gateway issues tokens with a 3600 second TTL, per the auth runbook." \
  --entity auth-gateway --key token_ttl_seconds --value 3600 \
  --source runbook:auth-config >/dev/null
"${CLI[@]+"${CLI[@]}"}" remember --content "notification-worker retries failed sends with exponential backoff, per the notification runbook." \
  --entity notification-worker --key retry_backoff_strategy --value exponential \
  --source runbook:notif-config >/dev/null
echo "seeded 3 unrelated background memories (redis-session, auth-gateway, notification-worker)"
pause

# ---------------------------------------------------------------------------
# 1. Admit a runbook fact
# ---------------------------------------------------------------------------
banner "1. Admit a runbook fact"
say "A runbook says how checkout-api's circuit breaker to the payments gateway is tuned."

"${CLI[@]+"${CLI[@]}"}" remember \
  --content "checkout-api's circuit breaker to the payments gateway trips after 800ms of sustained p99 latency, per the resiliency runbook." \
  --entity checkout-api --key circuit_breaker_timeout_ms --value 800 \
  --source runbook:checkout-resiliency
pause

# ---------------------------------------------------------------------------
# 2. A Slack claim contradicts it -> held for review
# ---------------------------------------------------------------------------
banner "2. A Slack claim contradicts it -> held for review"
say "Someone claims in Slack, with no runbook behind it, that the number changed."
say "A Slack source (authority: low) does not outrank a runbook (authority: higher)."
say "NOTE: the CLI's held-for-review message below always reads \"sources rank equal\" --"
say "that phrasing is imprecise (this case is lower-authority, not tied); see"
say "ARCHITECTURE.md's Known Limits. The verdict itself -- held, not admitted -- is correct."

"${CLI[@]+"${CLI[@]}"}" remember \
  --content "Someone in #incidents Slack says checkout-api's breaker now trips at 300ms after a recent change." \
  --entity checkout-api --key circuit_breaker_timeout_ms --value 300 \
  --source slack
pause

# ---------------------------------------------------------------------------
# 3. A human corrects it -> the old fact becomes history
# ---------------------------------------------------------------------------
banner "3. A human corrects it -> the old fact becomes history"
say "The on-call lead signs off on the real number. A human source outranks a runbook,"
say "so this one is admitted AND replaces (supersedes) the original -- it is not deleted,"
say "it is corrected. The Slack claim from step 2 is still just held; nobody has confirmed it."

"${CLI[@]+"${CLI[@]}"}" remember \
  --content "Alice (on-call lead) confirms after the 2026-07 tuning pass: checkout-api's circuit breaker to the payments gateway trips at 500ms, not 800ms and not 300ms. This is authoritative." \
  --entity checkout-api --key circuit_breaker_timeout_ms --value 500 \
  --source human:alice

echo
say "What checkout-api.circuit_breaker_timeout_ms looks like now, oldest first:"
docker exec -i "$CONTAINER" ./cockroach sql --insecure --format=table \
  -e "SELECT source, attribute_value AS value, verdict FROM agent_memories \
      WHERE tenant_id = '${DEMO_TENANT}' AND entity = 'checkout-api' \
        AND attribute_key = 'circuit_breaker_timeout_ms' \
      ORDER BY created_at;"
say "800ms (the runbook) has been corrected by a newer fact -- kept as history, not deleted."
say "300ms (Slack) is still held for review. 500ms (the human) is the one recall() serves."
pause

# ---------------------------------------------------------------------------
# 4. An alert arrives: recall, then propose an action
# ---------------------------------------------------------------------------
banner "4. An alert arrives: the agent recalls, then proposes an action"
say "INC-7734: the payments gateway's p99 latency is spiking. Before doing anything,"
say "the on-call agent recalls what it currently believes about checkout-api's breaker."

"${CLI[@]+"${CLI[@]}"}" recall --query "checkout-api circuit breaker timeout gateway latency incident"
pause

say "It proposes a temporary mitigation -- written down as its own memory, produced by"
say "the decision it is about to record, not merely consulted by it (see backend/decisions.py)."

REMEMBER_JSON="$("${CLI[@]+"${CLI[@]}"}" remember --json \
  --content "Proposed action: temporarily raise checkout-api's circuit breaker timeout to 1200ms while the payments gateway p99 latency spike (INC-7734) is ongoing, to reduce false-positive trips." \
  --entity checkout-api --key last_incident_action --value breaker_timeout_raised_to_1200ms_temp \
  --source agent:standing-oncall --task-id "$TASK_ID")"
ACTION_MEMORY_ID="$(jq -r '.memory_id' <<<"$REMEMBER_JSON")"
echo "  proposed action memory: $ACTION_MEMORY_ID  (verdict: $(jq -r '.verdict' <<<"$REMEMBER_JSON"))"

CONSULTED_IDS="$("${CLI[@]+"${CLI[@]}"}" recall --json \
  --query "checkout-api circuit breaker timeout gateway latency incident" | jq -r '[.[].memory_id] | join(",")')"

DECIDE_JSON="$("${CLI[@]+"${CLI[@]}"}" decide --json \
  --action raise_circuit_breaker_timeout \
  --rationale "p99 latency spike on the payments gateway (INC-7734); temporarily raising checkout-api's breaker timeout to cut false trips while the gateway recovers." \
  --query "checkout-api circuit breaker timeout gateway latency incident" \
  --produced "$ACTION_MEMORY_ID" \
  --task-id "$TASK_ID")"
DECISION_ID="$(jq -r '.decision_id' <<<"$DECIDE_JSON")"

echo
echo "  decision:  $DECISION_ID"
echo "  action:    $(jq -r '.action' <<<"$DECIDE_JSON")"
echo "  status:    $(jq -r '.status' <<<"$DECIDE_JSON")"
echo "  consulted: $(jq -r '.consulted | length' <<<"$DECIDE_JSON") memory(ies) -- $CONSULTED_IDS"
echo "  produced:  $ACTION_MEMORY_ID"
pause

# ---------------------------------------------------------------------------
# 5. PagerDuty resolves -> grant_standing promotes the memory. Zero model calls.
# ---------------------------------------------------------------------------
banner "5. PagerDuty resolves -> grant_standing promotes the memory (0 model calls)"
say "The incident resolves. This is the one call in the whole system that is allowed"
say "to change what is trusted, and backend/trust.py imports no model client at all --"
say "assert_no_model_calls() checks that on this exact path, not just in a docstring."

"${CLI[@]+"${CLI[@]}"}" confirm \
  --decision-id "$DECISION_ID" \
  --outcome success --source pagerduty --ref INC-7734
echo
echo "  Model calls on this promotion path: 0"
pause

# ---------------------------------------------------------------------------
# 6. Cross-examine the decision
# ---------------------------------------------------------------------------
banner "6. Cross-examine: what the agent knew then, vs. what it knows now"
say "SET TRANSACTION AS OF SYSTEM TIME pins a whole read to the instant this decision"
say "was made, so this is a replay of the agent's actual belief state, not a guess at it."

"${CLI[@]+"${CLI[@]}"}" cross-examine --decision-id "$DECISION_ID"
pause

# ---------------------------------------------------------------------------
# 7. EXPLAIN: vector search + prefix spans
# ---------------------------------------------------------------------------
banner "7. Prove the vector index is real: EXPLAIN, vector search + prefix spans"
say "The demo tenant above is deliberately tiny (see the file header) -- too small for"
say "the ANN partition to mean anything. This step switches to the tenant already"
say "carrying ~104 seeded memories, and runs backend.memory.recall()'s exact query."

"$PYTHON" - "$FIXTURE_TENANT" <<'PY'
import sys
sys.path.insert(0, ".")
from backend import db, embeddings

tenant = sys.argv[1]
conn = db.get_conn()
try:
    with conn.cursor() as cur:
        cur.execute("ANALYZE agent_memories")  # stale stats -> optimizer estimates ~1 row/tenant -> full scan
        conn.commit()
        vec = embeddings.to_pgvector(
            embeddings.embed("checkout-api circuit breaker timeout gateway latency incident")
        )
        cur.execute(
            "EXPLAIN SELECT memory_id FROM agent_memories "
            "WHERE tenant_id = %s AND verdict = 'accepted' "
            "ORDER BY embedding <=> %s LIMIT 5",
            (tenant, vec),
        )
        rows = cur.fetchall()
    conn.commit()
finally:
    db.put_conn(conn)

text = "\n".join(r[0] for r in rows)
print(text)
print()
print(f"  vector search node present: {'vector search' in text}")
print(f"  prefix spans line present:  {'prefix spans' in text}")
PY
pause

# ---------------------------------------------------------------------------
# 8. Concurrency: the real 40001
# ---------------------------------------------------------------------------
banner "8. Concurrency: SERIALIZABLE + retry under real contention (SQLSTATE 40001)"
say "10 concurrent processes race a read-modify-write on one memory row, and two"
say "concurrent agents submit contradictory memories for the same entity+attribute."
say "Both properties are asserted, not eyeballed; a failed assertion here is a real"
say "correctness finding. This regenerates benchmarks/concurrency.md."

"$PYTHON" "$REPO_ROOT/scripts/race_demo.py" --writers 10 --output benchmarks/concurrency.md
pause

# ---------------------------------------------------------------------------
banner "Done"
echo "Decision:        $DECISION_ID"
echo "Produced memory: $ACTION_MEMORY_ID"
echo "Demo tenant:     $DEMO_TENANT"
echo
echo "Full concurrency report: benchmarks/concurrency.md"
echo "Retrieval benchmark:     benchmarks/results.md  (python scripts/loadtest.py to regenerate)"
