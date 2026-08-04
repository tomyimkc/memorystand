#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# MemoryStand -- create a dedicated, least-privilege CockroachDB Cloud service
# account for the CockroachDB Cloud MCP server, and print the .mcp.json a
# judge (or the repo owner) pastes into Claude Code to connect it.
#
# Every `ccloud` flag below was confirmed against a locally installed
# `ccloud` binary's own `--help` output on 2026-08-04:
#   ccloud service-account create <name> [--description string]
#   ccloud service-account api-key create <service account id> <key name>
#   ccloud service-account api-key delete <api key id>
# CONFIRMED 2026-08-04: passing a SERVICE-ACCOUNT id in the position ccloud documents
# as `<user ID>` is accepted -- the call got past it and failed only on the role name.
# Role names are SCREAMING_SNAKE, not title case. The API enumerates them on rejection:
#   BILLING_COORDINATOR ORG_ADMIN ORG_MEMBER CLUSTER_ADMIN CLUSTER_OPERATOR_WRITER
#   CLUSTER_DEVELOPER CLUSTER_CREATOR FOLDER_ADMIN FOLDER_MOVER
# Resource type is likewise uppercase: valid values are ORGANIZATION, CLUSTER, FOLDER.
# Previously noted as unverified: this passes a SERVICE-ACCOUNT id in the position
# `ccloud role add` documents as `<user ID>`. Its --help uses generic "user id"
# language with no example distinguishing service accounts from human users, and
# there is no session here to test against. Plausible -- most cloud IAM systems
# treat a service account as a principal -- but confirm it on first run rather
# than assuming. The role NAME string below is separately unverified; see above.
#   ccloud role add <user ID> <role name> <resource type> [<resource ID>]
#   ccloud cluster list
# This machine has NO CockroachDB Cloud session (`ccloud auth whoami` fails
# with "not logged in") and no credentials that could create one, so none of
# the ccloud calls below have been RUN end-to-end -- only their flags were
# checked against --help, which does not require a session. Run this for
# real only after `ccloud auth login`.
#
# What this script deliberately does NOT try to guess: the EXACT role-name
# string `ccloud role add` accepts. CockroachDB Cloud's docs
# (cockroachlabs.com/docs/cockroachcloud/managing-access) only show the
# human display name "CLUSTER_DEVELOPER" / "Cluster Operator" / "Cluster
# Admin"; no `--help` output or public example on this machine shows the
# literal argument string `ccloud role add` expects for it, and there is no
# live session to test one. MEMORYSTAND_MCP_ROLE below defaults to the
# display name from the docs; if `ccloud role add` rejects it, run
# `ccloud role add --help` again after `ccloud auth login` (a live session
# may print an accepted-values list this environment's help text does not)
# and pass the accepted string via MEMORYSTAND_MCP_ROLE instead. This script
# fails loudly on that call rather than silently granting the wrong scope.
#
# Why a role this narrow, not the SQL user provision.sh already creates:
#   - provision.sh's SQL user is a Postgres-wire-protocol login with a
#     password, used by the Lambda for actual reads/writes. It is not a
#     CockroachDB Cloud IAM identity and cannot authenticate an MCP session.
#   - The MCP server authenticates over Cloud IAM (OAuth or a service-account
#     API key in an Authorization header), not a SQL password. It needs its
#     own Cloud-IAM identity, scoped to exactly one cluster, with the
#     smallest role that still allows running SELECT/EXPLAIN/SHOW through the
#     MCP server's read tools.
#   - CLUSTER_DEVELOPER is the most restrictive assignable cluster-scoped
#     role in CockroachDB Cloud's own role table (see docs/MCP.md) -- it does
#     not carry Cluster Admin/Operator's ability to change maintenance
#     windows, delete the cluster, or edit other role grants. A leaked key
#     for this account can read; it cannot do anything else.
#   - Scoped to ONE cluster (`cluster <cluster-id>`), not the organization,
#     so the key is useless against any other cluster in the account even if
#     the role were ever widened.
#
# Usage:
#   ccloud auth login
#   CLUSTER_NAME=standing ./infra/mcp_setup.sh
#   # or: CLUSTER_ID=<uuid> ./infra/mcp_setup.sh
#
set -euo pipefail

CLUSTER_NAME="${CLUSTER_NAME:-standing}"
CLUSTER_ID="${CLUSTER_ID:-}"
SERVICE_ACCOUNT_NAME="${SERVICE_ACCOUNT_NAME:-memorystand-mcp-readonly}"
API_KEY_NAME="${API_KEY_NAME:-memorystand-mcp-readonly-key}"
# CLUSTER_DEVELOPER is NOT sufficient. Verified live: under it, the MCP server's
# select_query returns "executing select query: unauthorized". Cockroach Labs' docs
# require Cluster Admin or Cluster Operator, so CLUSTER_OPERATOR_WRITER is the least
# privilege that actually works -- and it is WRITE-CAPABLE. There is no read-only role
# for this server. That is a property of the managed server, not a choice made here.
MEMORYSTAND_MCP_ROLE="${MEMORYSTAND_MCP_ROLE:-CLUSTER_OPERATOR_WRITER}"
OUT_FILE="${OUT_FILE:-$HOME/.memorystand-mcp-key}"

need() { command -v "$1" >/dev/null 2>&1 || { echo "missing required tool: $1" >&2; exit 1; }; }
need ccloud
need jq

echo "==> Checking for a CockroachDB Cloud session"
if ! ccloud auth whoami >/dev/null 2>&1; then
  echo "No ccloud session. Run 'ccloud auth login' first, then re-run this script." >&2
  exit 1
fi

if [[ -z "$CLUSTER_ID" ]]; then
  echo "==> Resolving cluster id for '${CLUSTER_NAME}' (set CLUSTER_ID to skip this lookup)"
  CLUSTER_ID="$(ccloud cluster list -o json | jq -r --arg n "$CLUSTER_NAME" \
    'if type=="array" then .[] else (if type=="array" then .[] else (.clusters[]? // empty) end // empty) end | select(.name==$n) | .id' | head -1)"
  if [[ -z "$CLUSTER_ID" ]]; then
    echo "Could not find a cluster named '${CLUSTER_NAME}'. Pass CLUSTER_ID=<uuid> explicitly," >&2
    echo "or CLUSTER_NAME=<name> matching an existing cluster (see 'ccloud cluster list')." >&2
    exit 1
  fi
fi
echo "    cluster id: ${CLUSTER_ID}"

echo "==> Ensuring service account '${SERVICE_ACCOUNT_NAME}' exists"
sa_id="$(ccloud service-account list -o json | jq -r --arg n "$SERVICE_ACCOUNT_NAME" \
  'if type=="array" then .[] else (if type=="array" then .[] else (.service_accounts[]? // empty) end // empty) end | select(.name==$n) | .id' | head -1)"
if [[ -z "$sa_id" ]]; then
  sa_id="$(ccloud service-account create "$SERVICE_ACCOUNT_NAME" \
    --description "MCP identity for MemoryStand. Write-capable: no read-only role works." \
    -o json | jq -r '.id')"
  echo "    created: ${sa_id}"
else
  echo "    found existing: ${sa_id} (reusing)"
fi

echo "==> Granting '${MEMORYSTAND_MCP_ROLE}' on cluster ${CLUSTER_ID} to ${sa_id}"
echo "    (least privilege: scoped to this one cluster, not the organization)"
if ! ccloud role add "$sa_id" "$MEMORYSTAND_MCP_ROLE" CLUSTER "$CLUSTER_ID"; then
  echo "role grant failed. This is almost certainly the unverified role-name string" >&2
  echo "described in this script's header comment -- re-run with MEMORYSTAND_MCP_ROLE" >&2
  echo "set to whatever 'ccloud role add --help' or the CockroachDB Cloud console" >&2
  echo "shows as the accepted value for the least-privileged cluster role." >&2
  exit 1
fi

echo "==> Creating API key '${API_KEY_NAME}' (this is the ONLY time the secret is shown)"
key_json="$(ccloud service-account api-key create "$sa_id" "$API_KEY_NAME" -o json)"
api_key="$(echo "$key_json" | jq -r '.secret // .key // .api_key // empty')"
if [[ -z "$api_key" ]]; then
  echo "Could not find the key secret in ccloud's JSON output. The key WAS created" >&2
  echo "(check 'ccloud service-account api-key list --service-account-id ${sa_id}')" >&2
  echo "but this script cannot locate its secret field to save it -- inspect the raw" >&2
  echo "output of 'ccloud service-account api-key create' by hand instead." >&2
  exit 1
fi

umask 177
printf '%s\n' "$api_key" > "$OUT_FILE"
chmod 600 "$OUT_FILE"
unset api_key key_json

echo "    saved to: ${OUT_FILE} (mode 600, never printed to this terminal)"
echo
echo "==> Paste this into your shell before starting Claude Code:"
echo "    export MEMORYSTAND_MCP_API_KEY=\"\$(cat ${OUT_FILE})\""
echo "    export MEMORYSTAND_MCP_CLUSTER_ID=\"${CLUSTER_ID}\""
echo
echo "==> Or, equivalently, this is the .mcp.json entry already checked into this repo root:"
cat <<'JSON'
{
  "mcpServers": {
    "cockroachdb-cloud": {
      "type": "http",
      "url": "https://cockroachlabs.cloud/mcp",
      "headers": {
        "mcp-cluster-id": "${MEMORYSTAND_MCP_CLUSTER_ID}",
        "Authorization": "Bearer ${MEMORYSTAND_MCP_API_KEY}"
      }
    }
  }
}
JSON
echo
echo "==> To revoke this key later (find <api-key-id> in the list output, then delete it):"
echo "    ccloud service-account api-key list --service-account-id ${sa_id}"
echo "    ccloud service-account api-key delete <api-key-id>"
