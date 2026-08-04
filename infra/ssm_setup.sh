#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# MemoryStand -- create the SSM Parameter Store entries the deploy and the running Lambda need.
#
# Creates exactly three parameters, all under the /standing/ prefix that
# infra/iam_policy.json scopes ssm:GetParameter to:
#
#   /memorystand/dsn            SecureString  CockroachDB connection string. Read by
#                                           infra/deploy.sh at DEPLOY TIME (with the
#                                           deployer's own credentials) and baked into the
#                                           function's environment -- the running Lambda's
#                                           own execution role is never granted access to
#                                           this one.
#   /memorystand/shared_secret  SecureString  generated here. Read by the running Lambda at
#                                           request time (cached ~60s) to gate the two
#                                           Bedrock-calling routes.
#   /memorystand/kill_switch    String        "off" -- flip to "on" to make the Lambda refuse
#                                           writes without a redeploy (see README "Limits").
#                                           Read by the running Lambda on every write request.
#
# Cost: Parameter Store standard parameters (used here) are free. SecureString
# encryption uses the AWS managed key alias/aws/ssm, which is also free -- only
# customer-managed KMS keys have a monthly charge, and this script does not create one.
#
# Idempotent: an existing parameter is left alone unless --force is passed, in which
# case it is overwritten (aws ssm put-parameter --overwrite). A value is never printed
# to stdout/stderr -- only the parameter NAME and a create/update/skip result.
#
# Usage:
#   MEMORYSTAND_DSN='postgresql://user:pass@host:26257/defaultdb?sslmode=verify-full' \
#     ./infra/ssm_setup.sh [--force]
#
#   REGION=us-west-2 MEMORYSTAND_DSN='...' ./infra/ssm_setup.sh --force
#
# REGION must match infra/deploy.sh's REGION (default us-west-2): SSM parameters are
# regional, and the deployed Lambda reads them from its own region via a plain
# GetParameter call with no cross-region config. A mismatch here fails closed at
# runtime with "parameter not found", not at deploy time.
set -euo pipefail

REGION="${REGION:-${AWS_REGION:-${AWS_DEFAULT_REGION:-us-west-2}}}"
FORCE=0

for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
    -h|--help)
      echo "Usage: MEMORYSTAND_DSN='<dsn>' $0 [--force]"
      exit 0
      ;;
    *)
      echo "Unknown argument: $arg" >&2
      exit 1
      ;;
  esac
done

need() { command -v "$1" >/dev/null 2>&1 || { echo "missing required tool: $1" >&2; exit 1; }; }
need aws

echo "==> Checking AWS credentials"
if ! caller_identity="$(aws sts get-caller-identity --region "$REGION" --output json 2>&1)"; then
  echo "No usable AWS credentials in this environment. This script cannot run without them." >&2
  echo "Details: $caller_identity" >&2
  exit 1
fi
account_id="$(echo "$caller_identity" | python3 -c 'import json,sys; print(json.load(sys.stdin)["Account"])' 2>/dev/null || true)"
echo "    ok (account ${account_id:-unknown}, region $REGION)"

if [[ -z "${MEMORYSTAND_DSN:-}" ]]; then
  echo "MEMORYSTAND_DSN is not set. Provide the CockroachDB connection string, e.g.:" >&2
  echo "  MEMORYSTAND_DSN='postgresql://user:pass@host:26257/defaultdb?sslmode=verify-full' $0" >&2
  exit 1
fi

# put_param NAME TYPE VALUE DESCRIPTION
put_param() {
  local name="$1" type="$2" value="$3" description="$4"
  local exists=0
  if aws ssm get-parameter --name "$name" --region "$REGION" >/dev/null 2>&1; then
    exists=1
  fi

  if [[ "$exists" == 1 && "$FORCE" != 1 ]]; then
    echo "    $name: already exists, skipped (pass --force to overwrite)"
    return 0
  fi

  local overwrite_flag=()
  [[ "$exists" == 1 ]] && overwrite_flag=(--overwrite)

  aws ssm put-parameter \
    --name "$name" \
    --type "$type" \
    --value "$value" \
    --description "$description" \
    --region "$REGION" \
    "${overwrite_flag[@]}" \
    >/dev/null

  if [[ "$exists" == 1 ]]; then
    echo "    $name: updated"
  else
    echo "    $name: created"
  fi
}

echo "==> /memorystand/dsn (SecureString)"
put_param "/memorystand/dsn" "SecureString" "$MEMORYSTAND_DSN" \
  "MemoryStand: CockroachDB connection string used by the Lambda handler"

echo "==> /memorystand/shared_secret (SecureString, generated)"
# openssl is present on every platform this project targets (macOS + the Lambda build
# image); fall back to Python's secrets module if it is ever missing.
if command -v openssl >/dev/null 2>&1; then
  shared_secret="$(openssl rand -base64 48)"
else
  shared_secret="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
fi
put_param "/memorystand/shared_secret" "SecureString" "$shared_secret" \
  "MemoryStand: shared secret validating requests to the Lambda Function URL"
unset shared_secret

echo "==> /memorystand/kill_switch (String)"
put_param "/memorystand/kill_switch" "String" "off" \
  "MemoryStand: 'on' makes the Lambda refuse writes without a redeploy (operator kill switch)"

echo
echo "Done. Parameter names created/updated under /standing/ (values were never printed):"
echo "  /memorystand/dsn"
echo "  /memorystand/shared_secret"
echo "  /memorystand/kill_switch"
