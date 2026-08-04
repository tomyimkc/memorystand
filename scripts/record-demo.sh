#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# MemoryStand -- recording-grade driver for the under-3:00 submission video.
#
# This is NOT scripts/demo.sh. demo.sh is a correctness harness: it asserts things,
# checks exit codes, and is meant to be read as scrollback. This script has one job:
# make recording the video nearly mechanical. It shares demo.sh's story (the same
# incident, the same six-command arc) but is paced, banner'd, and re-takeable for a
# camera instead of a CI log.
#
# Usage:
#   scripts/record-demo.sh --pause            narrate live: waits for Enter before
#                                              each beat, then a 3-2-1 countdown, then
#                                              runs it and waits again before the next
#   scripts/record-demo.sh --auto             unattended take: same countdown, then
#                                              tuned sleeps (see BEAT_SECONDS below)
#                                              instead of waiting on a keypress
#   scripts/record-demo.sh --pause --beat 5    re-record ONLY beat 5. Quietly (no
#   scripts/record-demo.sh --auto  --beat 5    banner, no countdown, output thrown
#                                              away) replays beats 1-4 first so beat
#                                              5's state (its decision id) is correct,
#                                              then films beat 5 for real. Nobody
#                                              nails 8 beats in one take -- this is
#                                              the escape hatch.
#   scripts/record-demo.sh --list              print the beat table and exit
#
# Every invocation resets to the SAME deterministic state first (a private tenant
# this script owns, distinct from any other demo identity -- see RECORD_TENANT
# below), so beat 5 filmed today looks identical to beat 5 filmed tomorrow. That
# reset is not itself filmed; think clapperboard, not scene.
#
# One honest caveat for the --beat flag: memory and decision ids are server-generated
# (gen_random_uuid()), so the 8-character id beat 4 prints in one take will NOT match
# the id beat 5 or 6 print if they were filmed in a SEPARATE later take -- each take
# starts from the same reset and regenerates fresh ids. Within one continuous take
# (e.g. filming beats 4, 5 and 6 back to back without stopping the script) the ids
# agree, because it is the same run. If you need beats 4-6 to show one consistent id
# on screen, film them together in a single --pause or --auto run rather than
# re-taking one of them alone with --beat. Viewers reading an 8-character id across a
# cut is an edge case worth knowing about, not a reason to avoid --beat.
#
# See docs/VIDEO.md for the shot-by-shot script (narration text, word counts, timing)
# this drives.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# ---------------------------------------------------------------------------
# Flags
# ---------------------------------------------------------------------------
MODE=""
ONLY_BEAT=""
LIST_ONLY=0

usage() {
  sed -n '3,34p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --pause) MODE="pause"; shift ;;
    --auto) MODE="auto"; shift ;;
    --beat)
      [[ $# -ge 2 ]] || { echo "error: --beat requires a number (0-8)" >&2; exit 2; }
      ONLY_BEAT="$2"; shift 2 ;;
    --list) LIST_ONLY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown flag: $1 (see --help)" >&2; exit 2 ;;
  esac
done

# ---------------------------------------------------------------------------
# Beat table -- single source of truth for titles and on-screen dwell time.
# Seconds below are the SAME numbers docs/VIDEO.md's timestamp table uses;
# change one, change both. 0 = hook (no command), 8 = closing (no command).
# ---------------------------------------------------------------------------
BEAT_TITLES=(
  "0:Hook (say this to camera, then cut to the terminal)"
  "1:Admit a runbook fact"
  "2:A Slack claim -> held for review"
  "3:A human corrects it -> CockroachDB keeps the old fact as history"
  "4:An alert arrives -> recall, propose, decide"
  "5:PagerDuty resolves -> grant_standing promotes the memory (0 model calls)"
  "6:Cross-examine -> AS OF SYSTEM TIME"
  "7:EXPLAIN -> the vector index is real"
  "8:Closing (say this to camera after the terminal segment)"
)
# macOS ships bash 3.2 (no associative arrays), so this is a case statement
# rather than a `declare -A` map -- same numbers docs/VIDEO.md's timestamp
# table uses; change one, change both.
beat_seconds() {
  case "$1" in
    0) echo 13 ;; 1) echo 14 ;; 2) echo 14 ;; 3) echo 20 ;; 4) echo 22 ;;
    5) echo 24 ;; 6) echo 20 ;; 7) echo 20 ;; 8) echo 10 ;;
    *) echo 10 ;;
  esac
}

if [[ "$LIST_ONLY" -eq 1 ]]; then
  echo "MemoryStand recording beats (docs/VIDEO.md has the exact narration):"
  echo
  for entry in "${BEAT_TITLES[@]}"; do
    n="${entry%%:*}"; t="${entry#*:}"
    printf "  %-2s %-70s  ~%ss\n" "$n" "$t" "$(beat_seconds "$n")"
  done
  exit 0
fi

if [[ -z "$MODE" ]]; then
  echo "error: pass --pause or --auto (see --help, or --list to see the beats)" >&2
  exit 2
fi

if [[ -n "$ONLY_BEAT" ]]; then
  case "$ONLY_BEAT" in
    0|1|2|3|4|5|6|7|8) ;;
    *) echo "error: --beat must be 0-8, got '$ONLY_BEAT'" >&2; exit 2 ;;
  esac
fi

# ---------------------------------------------------------------------------
# Presentation helpers -- degrade to plain text off a TTY / when NO_COLOR is
# set, same rule the rest of this repo's demo tooling uses.
# ---------------------------------------------------------------------------
if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
  BOLD=$'\033[1m'; CYAN=$'\033[1;36m'; GREEN=$'\033[1;32m'; YELLOW=$'\033[1;33m'
  DIM=$'\033[2m'; RESET=$'\033[0m'
else
  BOLD=""; CYAN=""; GREEN=""; YELLOW=""; DIM=""; RESET=""
fi

beat_banner() {
  local n="$1" title="$2"
  printf '\n\n\n%s########################################################################%s\n' "$CYAN" "$RESET"
  printf '%s#%s\n' "$CYAN" "$RESET"
  printf '%s#   BEAT %-2s  %s%s\n' "$CYAN" "$n" "$title" "$RESET"
  printf '%s#%s\n' "$CYAN" "$RESET"
  printf '%s########################################################################%s\n\n' "$CYAN" "$RESET"
}

say_narration() {
  printf '%s  narration: %s%s\n\n' "$DIM" "$1" "$RESET"
}

show_cmd() {
  printf '%s$ %s%s\n\n' "$DIM" "$*" "$RESET"
}

countdown() {
  local n
  for n in 3 2 1; do
    printf '\r%s  recording in %s...  %s' "$BOLD$YELLOW" "$n" "$RESET"
    sleep 1
  done
  printf '\r%s  ACTION%s                          \n\n' "$BOLD$GREEN" "$RESET"
}

# Between-beats gate: --pause waits on the owner; --auto sleeps the tuned
# duration so the output stays on screen long enough to narrate over.
gate_after() {
  local n="$1"
  local secs
  secs="$(beat_seconds "$n")"
  echo
  if [[ "$MODE" == "auto" ]]; then
    sleep "$secs"
  else
    read -r -p "${DIM}-- press Enter to continue to the next beat --${RESET}" _ || true
  fi
}

short() { printf '%s' "${1:0:8}"; }

# Runs a command; in quiet (state-rebuild) mode its output is thrown away so
# a --beat N re-take never scrolls the beats it is silently replaying.
run_step() {
  if [[ "$QUIET" -eq 1 ]]; then
    "$@" >/dev/null 2>&1
  else
    "$@"
  fi
}

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
CONTAINER="${MEMORYSTAND_CONTAINER:-crdb-memorystand}"
PYTHON="$REPO_ROOT/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  echo "error: $PYTHON not found. Run ./scripts/run-local.sh first." >&2
  exit 1
fi
command -v docker >/dev/null 2>&1 || { echo "error: docker not found on PATH" >&2; exit 1; }
command -v jq >/dev/null 2>&1 || { echo "error: jq not found on PATH (brew install jq)" >&2; exit 1; }
if ! docker ps --filter "name=^${CONTAINER}$" --filter "status=running" --format '{{.Names}}' \
     | grep -qx "$CONTAINER"; then
  echo "error: the $CONTAINER container is not running. Run ./scripts/run-local.sh first." >&2
  exit 1
fi

: "${MEMORYSTAND_DSN:=postgresql://root@localhost:26257/defaultdb?sslmode=disable}"
export MEMORYSTAND_DSN
export COCKROACH_DSN="$MEMORYSTAND_DSN"
export MEMORYSTAND_EMBED_STUB="${MEMORYSTAND_EMBED_STUB:-1}"
export PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"

CLI=("$PYTHON" "$REPO_ROOT/cli/memorystand.py")

# This script's own private demo identity -- deliberately NOT
# cli/memorystand.py's DEFAULT_TENANT_ID. That id and db/seed/seed.py's fixture
# tenant id are the SAME uuid (9c8f6e5a-...), which is fine for demo.sh's own
# arc, but this script's reset step (below) needs to freely wipe its tenant on
# every take without also wiping the ~101-row fixture that beat 7 reads. Using
# a separate, fixed uuid5 sidesteps that collision entirely rather than
# depending on run order.
RECORD_TENANT="c490e275-171e-55b0-8e32-23c20e43fdeb"   # uuid5(NAMESPACE_URL, "memorystand:record-demo:tenant")
RECORD_AGENT="8e674fa3-077b-552b-be79-454de7746f94"    # uuid5(NAMESPACE_URL, "memorystand:record-demo:agent")
export MEMORYSTAND_TENANT_ID="$RECORD_TENANT"
export MEMORYSTAND_AGENT_ID="$RECORD_AGENT"

FIXTURE_TENANT="$("$PYTHON" -c 'from db.seed.seed import DEFAULT_TENANT_ID; print(DEFAULT_TENANT_ID)')"
TASK_ID="$("$PYTHON" -c "import uuid; print(uuid.uuid5(uuid.NAMESPACE_URL, 'memorystand:record-demo:inc-7734'))")"

# ---------------------------------------------------------------------------
# Reset to a deterministic state. Always runs, once, before anything else --
# not itself a beat, not gated by --beat. Fast (a couple of seconds) and
# quiet on purpose.
# ---------------------------------------------------------------------------
reset_state() {
  echo "${DIM}resetting to a clean, deterministic state...${RESET}"
  for t in tool_audit agent_decisions belief_snapshots agent_memories; do
    docker exec -i "$CONTAINER" ./cockroach sql --insecure \
      -e "DELETE FROM ${t} WHERE tenant_id = '${RECORD_TENANT}';" >/dev/null
  done

  # Beat 7 reads the ~101-row incident fixture at realistic volume. It shares a
  # cluster with everything else this repo does against it (including
  # scripts/demo.sh, which -- see the note on RECORD_TENANT above -- can wipe
  # that same tenant as a side effect of its own reset). Rather than assume the
  # fixture is intact, verify it and reseed it if it is not, so beat 7 looks
  # identical on every take regardless of what else ran against this cluster
  # earlier.
  local fixture_count
  fixture_count="$("$PYTHON" - <<PY
from backend import db
conn = db.get_conn()
try:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM agent_memories WHERE tenant_id = %s", ("$FIXTURE_TENANT",))
        print(cur.fetchone()[0])
    conn.commit()
finally:
    db.put_conn(conn)
PY
)"
  if [[ "$fixture_count" != "101" ]]; then
    echo "${DIM}fixture tenant has ${fixture_count}/101 rows -- reseeding for beat 7...${RESET}"
    for t in tool_audit agent_decisions belief_snapshots agent_memories; do
      docker exec -i "$CONTAINER" ./cockroach sql --insecure \
        -e "DELETE FROM ${t} WHERE tenant_id = '${FIXTURE_TENANT}';" >/dev/null
    done
    "$PYTHON" "$REPO_ROOT/db/seed/seed.py" --tenant "$FIXTURE_TENANT" >/dev/null
  fi
  docker exec "$CONTAINER" ./cockroach sql --insecure -e 'ANALYZE agent_memories;' >/dev/null 2>&1
  echo "${DIM}ready.${RESET}"
}

# ---------------------------------------------------------------------------
# Beats. Each checks $QUIET itself: 1 means "rebuild state for a later beat,
# print nothing"; 0 means "this is the beat being filmed, show everything."
# ---------------------------------------------------------------------------
QUIET=0
DECISION_ID=""
ACTION_MEMORY_ID=""

beat0_hook() {
  if [[ "$QUIET" -eq 1 ]]; then return; fi
  beat_banner 0 "Hook"
  say_narration "Every agent memory product decides what to trust by recency, source authority, or asking the model if it still believes itself. MemoryStand adds a fourth signal: did the decision actually work?"
  echo "${DIM}(say this to camera on a title card or talking head, THEN cut to the terminal)${RESET}"
  countdown
  gate_after 0
}

beat1_admit() {
  if [[ "$QUIET" -eq 0 ]]; then
    beat_banner 1 "Admit a runbook fact"
    say_narration "A runbook says checkout-api's circuit breaker to the payments gateway trips at 800 milliseconds. I write that down as a memory, and CockroachDB admits it -- nothing on file contradicts it yet."
    countdown
    show_cmd "$PYTHON cli/memorystand.py remember --content \"...trips after 800ms...\" --entity checkout-api --key circuit_breaker_timeout_ms --value 800 --source runbook:checkout-resiliency"
  fi
  run_step "${CLI[@]}" remember \
    --content "checkout-api's circuit breaker to the payments gateway trips after 800ms of sustained p99 latency, per the resiliency runbook." \
    --entity checkout-api --key circuit_breaker_timeout_ms --value 800 \
    --source runbook:checkout-resiliency
  if [[ "$QUIET" -eq 0 ]]; then gate_after 1; fi
}

beat2_slack() {
  if [[ "$QUIET" -eq 0 ]]; then
    beat_banner 2 "A Slack claim -> held for review"
    say_narration "Now someone claims in Slack that the breaker actually trips at 300 milliseconds -- no runbook behind it. A low-authority source doesn't outrank a runbook, so this one is held for review, not thrown away."
    countdown
    show_cmd "$PYTHON cli/memorystand.py remember --content \"...300ms after a recent change.\" --entity checkout-api --key circuit_breaker_timeout_ms --value 300 --source slack"
  fi
  run_step "${CLI[@]}" remember \
    --content "Someone in #incidents Slack says checkout-api's breaker now trips at 300ms after a recent change." \
    --entity checkout-api --key circuit_breaker_timeout_ms --value 300 \
    --source slack
  if [[ "$QUIET" -eq 0 ]]; then gate_after 2; fi
}

beat3_human_corrects() {
  if [[ "$QUIET" -eq 0 ]]; then
    beat_banner 3 "A human corrects it -- CockroachDB keeps the old fact as history"
    say_narration "The on-call lead signs off on the real number: 500 milliseconds. A human outranks a runbook, so this is admitted and corrects the old fact. Watch -- CockroachDB keeps the 800-millisecond fact as history, not deletes it; the Slack guess stays held. This is the row's own MVCC history, live."
    countdown
    show_cmd "$PYTHON cli/memorystand.py remember --content \"Alice confirms... trips at 500ms...\" --entity checkout-api --key circuit_breaker_timeout_ms --value 500 --source human:alice"
  fi
  run_step "${CLI[@]}" remember \
    --content "Alice (on-call lead) confirms after the 2026-07 tuning pass: checkout-api's circuit breaker to the payments gateway trips at 500ms, not 800ms and not 300ms. This is authoritative." \
    --entity checkout-api --key circuit_breaker_timeout_ms --value 500 \
    --source human:alice

  if [[ "$QUIET" -eq 0 ]]; then
    echo
    show_cmd "docker exec -i $CONTAINER ./cockroach sql --insecure --format=table -e \"SELECT source, attribute_value, verdict FROM agent_memories WHERE ...\""
  fi
  run_step docker exec -i "$CONTAINER" ./cockroach sql --insecure --format=table \
    -e "SELECT source, attribute_value AS value, verdict FROM agent_memories \
        WHERE tenant_id = '${RECORD_TENANT}' AND entity = 'checkout-api' \
          AND attribute_key = 'circuit_breaker_timeout_ms' \
        ORDER BY created_at;"
  if [[ "$QUIET" -eq 0 ]]; then gate_after 3; fi
}

beat4_alert_decide() {
  if [[ "$QUIET" -eq 0 ]]; then
    beat_banner 4 "An alert arrives -- recall, propose, decide"
    say_narration "An incident hits: the payments gateway's latency is spiking. Before doing anything, the agent recalls what it knows about checkout-api's breaker -- that 500-millisecond fact comes back first, ranked by CockroachDB's own vector index. It proposes raising the timeout to 1200 milliseconds, and records that decision, with the recalled memory as its evidence."
    countdown
    show_cmd "$PYTHON cli/memorystand.py recall --query \"checkout-api circuit breaker timeout gateway latency incident\""
  fi
  run_step "${CLI[@]}" recall --query "checkout-api circuit breaker timeout gateway latency incident"

  # The two calls below use --json so the full (unabbreviated) memory_id and
  # decision_id can be captured for the next beats. That means bypassing the
  # CLI's own colourised table for these two lines only; the summary below is
  # printed by hand instead, matching scripts/demo.sh's own step 4.
  local remember_json
  remember_json="$("${CLI[@]}" remember --json \
    --content "Proposed action: temporarily raise checkout-api's circuit breaker timeout to 1200ms while the payments gateway p99 latency spike (INC-7734) is ongoing, to reduce false-positive trips." \
    --entity checkout-api --key last_incident_action --value breaker_timeout_raised_to_1200ms_temp \
    --source agent:memorystand-oncall --task-id "$TASK_ID")"
  ACTION_MEMORY_ID="$(jq -r '.memory_id' <<<"$remember_json")"
  if [[ "$QUIET" -eq 0 ]]; then
    echo
    printf '  %sproposed action%s memory %s  (verdict: %s)\n' "$GREEN" "$RESET" "$(short "$ACTION_MEMORY_ID")" "$(jq -r '.verdict' <<<"$remember_json")"
  fi

  local consulted_ids
  consulted_ids="$("${CLI[@]}" recall --json \
    --query "checkout-api circuit breaker timeout gateway latency incident" | jq -r '[.[].memory_id] | join(",")')"

  local decide_json
  decide_json="$("${CLI[@]}" decide --json \
    --action raise_circuit_breaker_timeout \
    --rationale "p99 latency spike on the payments gateway (INC-7734); temporarily raising checkout-api's breaker timeout to cut false trips while the gateway recovers." \
    --query "checkout-api circuit breaker timeout gateway latency incident" \
    --produced "$ACTION_MEMORY_ID" --task-id "$TASK_ID")"
  DECISION_ID="$(jq -r '.decision_id' <<<"$decide_json")"

  if [[ "$QUIET" -eq 0 ]]; then
    echo
    printf '  %sdecision%s     %s\n' "$BOLD" "$RESET" "$(short "$DECISION_ID")"
    printf '  action:      %s\n' "$(jq -r '.action' <<<"$decide_json")"
    printf '  consulted:   %s memory(ies) -- %s\n' "$(jq -r '.consulted | length' <<<"$decide_json")" "$consulted_ids"
    printf '  status:      %s\n' "$(jq -r '.status' <<<"$decide_json")"
    gate_after 4
  fi
}

beat5_grant_standing() {
  if [[ "$QUIET" -eq 0 ]]; then
    beat_banner 5 "PagerDuty resolves -- grant_standing (0 model calls)"
    say_narration "Here's the part nobody else does. The incident resolves in PagerDuty. That real-world outcome -- not a model -- is what promotes this memory to verified, in one serializable CockroachDB transaction. Zero model calls on this path. This isn't the model deciding it worked. It's the world confirming it did."
    countdown
    show_cmd "$PYTHON cli/memorystand.py confirm --decision-id ${DECISION_ID:+$(short "$DECISION_ID")} --outcome success --source pagerduty --ref INC-7734"
  fi
  run_step "${CLI[@]}" confirm \
    --decision-id "$DECISION_ID" \
    --outcome success --source pagerduty --ref INC-7734
  if [[ "$QUIET" -eq 0 ]]; then gate_after 5; fi
}

beat6_cross_examine() {
  if [[ "$QUIET" -eq 0 ]]; then
    beat_banner 6 "Cross-examine -- AS OF SYSTEM TIME"
    say_narration "Now I ask CockroachDB what the agent believed at the exact instant it made that decision, using AS OF SYSTEM TIME -- not a guess, not an approximation, a real pinned read of the past. Then I diff it against what it believes right now."
    countdown
    show_cmd "$PYTHON cli/memorystand.py cross-examine --decision-id $(short "$DECISION_ID")"
  fi
  run_step "${CLI[@]}" cross-examine --decision-id "$DECISION_ID"
  if [[ "$QUIET" -eq 0 ]]; then gate_after 6; fi
}

beat7_explain() {
  if [[ "$QUIET" -eq 0 ]]; then
    beat_banner 7 "EXPLAIN -- the vector index is real"
    say_narration "One more proof: this recall isn't scanning every memory. EXPLAIN shows a real vector-search node with prefix spans scoped to this tenant's admitted memories -- cost grows with one tenant's data, not the whole platform's."
    countdown
    show_cmd "EXPLAIN SELECT memory_id FROM agent_memories WHERE tenant_id = ... AND verdict = 'accepted' ORDER BY embedding <=> ... LIMIT 5"
  fi
  run_step "$PYTHON" - "$FIXTURE_TENANT" <<'PY'
import sys
sys.path.insert(0, ".")
from backend import db, embeddings

tenant = sys.argv[1]
conn = db.get_conn()
try:
    with conn.cursor() as cur:
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
  if [[ "$QUIET" -eq 0 ]]; then gate_after 7; fi
}

beat8_closing() {
  if [[ "$QUIET" -eq 1 ]]; then return; fi
  beat_banner 8 "Closing"
  say_narration "That's MemoryStand: memory an on-call agent can trust, because CockroachDB proved it worked -- not because a model said so. Code's public; link's below."
  echo "${DIM}(say this to camera after cutting away from the terminal)${RESET}"
  countdown
}

# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
BEAT_FUNCS=(beat0_hook beat1_admit beat2_slack beat3_human_corrects beat4_alert_decide \
            beat5_grant_standing beat6_cross_examine beat7_explain beat8_closing)

reset_state

if [[ -n "$ONLY_BEAT" ]]; then
  # Quietly rebuild any state earlier beats would have left behind, then film
  # only the requested beat. Beats 0, 1, 2, 3, 7 and 8 have no dependency on
  # an earlier beat's output; 4 produces $DECISION_ID / $ACTION_MEMORY_ID that
  # 5 and 6 read, so re-taking 5 or 6 alone replays 1-4 quietly first.
  for ((i = 0; i < ONLY_BEAT; i++)); do
    QUIET=1
    "${BEAT_FUNCS[$i]}"
  done
  QUIET=0
  "${BEAT_FUNCS[$ONLY_BEAT]}"
else
  for f in "${BEAT_FUNCS[@]}"; do
    QUIET=0
    "$f"
  done
fi

printf '\n\n%sDone.%s\n' "$BOLD$GREEN" "$RESET"
if [[ -n "$DECISION_ID" ]]; then
  echo "Decision:        $DECISION_ID"
  echo "Produced memory: $ACTION_MEMORY_ID"
fi
echo "Record tenant:   $RECORD_TENANT"
