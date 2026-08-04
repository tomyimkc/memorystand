#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# MemoryStand -- provision the CockroachDB Cloud memory layer with the agent-ready ccloud CLI.
#
# Every flag here was confirmed against `ccloud 0.8.23 --help` on 2026-08-03
# (see SPIKE-RESULTS.md, spike 6). Nothing is invented.
#
# Note --cloud AWS: the memory layer itself runs on AWS, not just the compute
# in front of it.
#
# REGION defaults to us-west-2 to match infra/deploy.sh's Lambda region -- the Lambda
# calls this cluster on every request, so co-locating avoids cross-region latency on top
# of the measured query numbers in README.md. Nothing about ccloud itself requires this;
# override REGION if you deploy the compute elsewhere.
#
#   ./infra/provision.sh                    # create
#   CLUSTER_NAME=standing-demo ./infra/provision.sh
#
set -euo pipefail

CLUSTER_NAME="${CLUSTER_NAME:-standing}"
REGION="${REGION:-us-west-2}"
CLOUD="${CLOUD:-AWS}"
SQL_USER="${SQL_USER:-memorystand_app}"
RU_LIMIT="${RU_LIMIT:-50000000}"     # stay inside the free monthly allowance
STORAGE_LIMIT="${STORAGE_LIMIT:-10}" # GiB

need() { command -v "$1" >/dev/null 2>&1 || { echo "missing: $1" >&2; exit 1; }; }
need ccloud
need jq

CRED_FILE="${CRED_FILE:-$HOME/.memorystand-sql-user.json}"
DSN_FILE="${DSN_FILE:-$HOME/.memorystand-dsn}"

echo "==> Authenticating (no-op if a valid session exists)"
ccloud auth whoami >/dev/null 2>&1 || ccloud auth login

echo "==> Looking for an existing cluster named '${CLUSTER_NAME}'"
# `ccloud cluster list -o json` returns a bare ARRAY, not {clusters:[...]}. Accept either,
# because guessing wrong here fails with a jq type error that says nothing useful.
existing="$(ccloud cluster list -o json 2>/dev/null | jq -r --arg n "$CLUSTER_NAME" \
  'if type=="array" then .[] else (if type=="array" then .[] else (.clusters[]? // empty) end // empty) end | select(.name==$n) | .id' \
  | head -1)"

if [[ -n "${existing}" ]]; then
  echo "    found: ${existing} (reusing)"
else
  echo "==> Creating BASIC cluster '${CLUSTER_NAME}' on ${CLOUD}/${REGION}"
  ccloud cluster create BASIC "${CLUSTER_NAME}" "${REGION}" \
    --cloud "${CLOUD}" \
    --request-unit-limit "${RU_LIMIT}" \
    --storage-gib-limit "${STORAGE_LIMIT}" \
    --wait \
    -o json
fi

echo "==> Cluster details"
ccloud cluster info "${CLUSTER_NAME}" -o json 2>/dev/null \
  | jq '{id, name, state, cloud_provider, plan: (.plan // .config)}' 2>/dev/null \
  || ccloud cluster info "${CLUSTER_NAME}" 2>/dev/null | grep -viE "retrieving|∙|●"

echo "==> Ensuring SQL user '${SQL_USER}' exists"
if ! ccloud cluster user list "${CLUSTER_NAME}" -o json 2>/dev/null | jq -e \
     --arg u "$SQL_USER" \
     'if type=="array" then .[] else (if type=="array" then .[] else (.users[]? // empty) end // empty) end
      | select((.name // .) == $u)' >/dev/null 2>&1; then
  # ccloud shows a generated password exactly once. Capture it to a 0600 file rather
  # than letting it land in scrollback -- this terminal is the one a demo gets recorded
  # from, and a credential on screen is unrecoverable once it is in a video.
  umask 077
  ccloud cluster user create "${CLUSTER_NAME}" "${SQL_USER}" -o json > "$CRED_FILE"
  chmod 600 "$CRED_FILE"
  echo "    created; password written to $CRED_FILE (not printed)"
else
  echo "    already exists"
fi

echo "==> Assembling the DSN"
# Build the full DSN (with password) into the same 0600 file. Print only the redacted
# form, so the shape is verifiable on screen without the secret being on screen.
BASE_URL="$(ccloud cluster connection-string "${CLUSTER_NAME}" --sql-user "${SQL_USER}" \
              --database defaultdb 2>/dev/null | grep -Eo 'postgresql://[^ ]+' | head -1)"
if [[ -n "$BASE_URL" && -s "$CRED_FILE" ]]; then
  PW="$(python3 -c 'import json,sys;d=json.load(open(sys.argv[1]));print(d.get("password",""))' "$CRED_FILE" 2>/dev/null || true)"
  if [[ -n "$PW" ]]; then
    DSN="${BASE_URL/postgresql:\/\//postgresql://${SQL_USER}:${PW}@}"
    printf '%s\n' "$DSN" > "$DSN_FILE"; chmod 600 "$DSN_FILE"
    echo "    DSN written to $DSN_FILE"
    echo "    ${DSN//$PW/********}"
  fi
else
  echo "    could not assemble automatically; connection string:"
  ccloud cluster connection-string "${CLUSTER_NAME}" --sql-user "${SQL_USER}" --database defaultdb
fi

cat <<'EOF'

Next:
  1. aws ssm put-parameter --name /memorystand/dsn --type SecureString --value '<dsn-from-above>'
  2. export COCKROACH_DSN='<dsn>' && python scripts/spike_db.py
  3. cockroach sql --url "$COCKROACH_DSN" -f db/schema.sql

Least privilege (do this before the app goes public), via `ccloud cluster sql`:
  GRANT SELECT, INSERT, UPDATE ON agent_memories, agent_decisions,
        belief_snapshots, tool_audit TO standing_app;
  -- deliberately no DELETE: MemoryStand supersedes, it never deletes.

Org-level audit trail:
  ccloud audit list -o json
EOF
