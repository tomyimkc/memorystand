#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Standing -- keep the demo alive unattended through 2026-09-15 by pinging /health.
#
# WHY /health AND NOT JUST THE LAMBDA: a scheduled `lambda:InvokeFunction` only proves the
# *container* is warm. It says nothing about CockroachDB -- a Basic-tier cluster idle for
# long enough can suspend, and a warm Lambda sitting in front of a suspended cluster is
# exactly the failure this script exists to prevent (first real request after idle would
# eat the wake-up latency, or fail, in front of a judge). So the schedule below issues an
# actual HTTPS GET to the Function URL's /health route, which backend/handler.py serves
# by calling backend.db.server_version() -- a real round trip to CockroachDB, not a
# static 200.
#
# HOW: EventBridge Scheduler cannot invoke an arbitrary HTTPS URL directly -- its target
# must be an AWS resource ARN. The supported way to reach an arbitrary URL is an
# EventBridge "API destination" (its own ARN, backed by a Connection that carries whatever
# auth the destination needs). The Function URL here uses auth type NONE, but EventBridge
# still requires *a* Connection auth type, so this creates an API-key connection whose key
# is never checked by anything -- it exists only to satisfy the API.
#
# Usage:
#   ./infra/keepwarm.sh
#   FUNCTION_NAME=standing REGION=us-east-1 END_DATE=2026-09-16T00:00:00Z ./infra/keepwarm.sh
#
set -euo pipefail

FUNCTION_NAME="${FUNCTION_NAME:-standing}"
REGION="${REGION:-${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}}"
INTERVAL_MINUTES="${INTERVAL_MINUTES:-5}"
END_DATE="${END_DATE:-2026-09-16T00:00:00Z}"  # one day past the 2026-09-15 requirement, as a buffer

CONNECTION_NAME="standing-keepwarm-conn"
API_DEST_NAME="standing-keepwarm-dest"
SCHEDULE_NAME="standing-keepwarm"
SCHEDULER_ROLE_NAME="standing-keepwarm-scheduler-role"

echo "==> Checking required tools"
need() { command -v "$1" >/dev/null 2>&1 || { echo "missing required tool: $1" >&2; exit 1; }; }
need aws

echo "==> Checking AWS credentials"
if ! caller_identity="$(aws sts get-caller-identity --region "$REGION" --output json 2>&1)"; then
  echo "No usable AWS credentials in this environment. This script cannot run without them." >&2
  echo "Nothing was created or modified. Details: $caller_identity" >&2
  exit 1
fi
ACCOUNT_ID="$(echo "$caller_identity" | python3 -c 'import json,sys; print(json.load(sys.stdin)["Account"])')"
echo "    ok (account $ACCOUNT_ID, region $REGION)"

echo "==> Looking up the Function URL for $FUNCTION_NAME"
if ! FUNCTION_URL="$(aws lambda get-function-url-config \
      --function-name "$FUNCTION_NAME" --region "$REGION" \
      --query 'FunctionUrl' --output text 2>&1)"; then
  echo "No Function URL found for '$FUNCTION_NAME' in $REGION." >&2
  echo "Run ./infra/deploy.sh first -- it creates the function and its public Function URL." >&2
  echo "Details: $FUNCTION_URL" >&2
  exit 1
fi
HEALTH_URL="${FUNCTION_URL%/}/health"
echo "    $HEALTH_URL"

echo "==> Ensuring EventBridge connection '$CONNECTION_NAME' exists"
echo "    (API key is a placeholder -- the Function URL is public (auth NONE); nothing"
echo "     downstream checks this header. EventBridge just requires some auth type.)"
if aws events describe-connection --name "$CONNECTION_NAME" --region "$REGION" >/dev/null 2>&1; then
  echo "    already exists, reusing"
else
  aws events create-connection \
    --name "$CONNECTION_NAME" \
    --description "Standing keep-warm: unused API key, target is a public Function URL" \
    --authorization-type API_KEY \
    --auth-parameters '{"ApiKeyAuthParameters":{"ApiKeyName":"x-standing-keepwarm","ApiKeyValue":"unused-public-endpoint"}}' \
    --region "$REGION" \
    >/dev/null
  echo "    created"
  # A freshly created connection needs a moment before it can be referenced.
  sleep 5
fi
CONNECTION_ARN="$(aws events describe-connection --name "$CONNECTION_NAME" --region "$REGION" \
  --query 'ConnectionArn' --output text)"

echo "==> Ensuring EventBridge API destination '$API_DEST_NAME' targets $HEALTH_URL"
if aws events describe-api-destination --name "$API_DEST_NAME" --region "$REGION" >/dev/null 2>&1; then
  aws events update-api-destination \
    --name "$API_DEST_NAME" \
    --connection-arn "$CONNECTION_ARN" \
    --http-method GET \
    --invocation-endpoint "$HEALTH_URL" \
    --invocation-rate-limit-per-second 1 \
    --region "$REGION" \
    >/dev/null
  echo "    updated"
else
  aws events create-api-destination \
    --name "$API_DEST_NAME" \
    --connection-arn "$CONNECTION_ARN" \
    --http-method GET \
    --invocation-endpoint "$HEALTH_URL" \
    --invocation-rate-limit-per-second 1 \
    --region "$REGION" \
    >/dev/null
  echo "    created"
fi
API_DEST_ARN="$(aws events describe-api-destination --name "$API_DEST_NAME" --region "$REGION" \
  --query 'ApiDestinationArn' --output text)"

echo "==> Ensuring IAM role '$SCHEDULER_ROLE_NAME' exists (trust: scheduler.amazonaws.com only)"
BUILD_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." >/dev/null 2>&1 && pwd)/infra/build"
mkdir -p "$BUILD_DIR"
SCHEDULER_TRUST_POLICY="$BUILD_DIR/scheduler-trust-policy.json"
cat > "$SCHEDULER_TRUST_POLICY" <<'EOF'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": { "Service": "scheduler.amazonaws.com" },
      "Action": "sts:AssumeRole"
    }
  ]
}
EOF

scheduler_role_created=0
if aws iam get-role --role-name "$SCHEDULER_ROLE_NAME" >/dev/null 2>&1; then
  echo "    already exists, reusing"
else
  aws iam create-role \
    --role-name "$SCHEDULER_ROLE_NAME" \
    --assume-role-policy-document "file://$SCHEDULER_TRUST_POLICY" \
    --description "Standing keep-warm: lets EventBridge Scheduler call one API destination" \
    >/dev/null
  scheduler_role_created=1
  echo "    created"
fi

SCHEDULER_POLICY="$BUILD_DIR/scheduler-invoke-policy.rendered.json"
cat > "$SCHEDULER_POLICY" <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "InvokeThisApiDestinationOnly",
      "Effect": "Allow",
      "Action": "events:InvokeApiDestination",
      "Resource": "$API_DEST_ARN"
    }
  ]
}
EOF
aws iam put-role-policy \
  --role-name "$SCHEDULER_ROLE_NAME" \
  --policy-name standing-keepwarm-invoke \
  --policy-document "file://$SCHEDULER_POLICY" \
  >/dev/null
SCHEDULER_ROLE_ARN="$(aws iam get-role --role-name "$SCHEDULER_ROLE_NAME" --query 'Role.Arn' --output text)"

if [[ "$scheduler_role_created" == 1 ]]; then
  echo "    (new role -- pausing 10s for IAM propagation before create-schedule references it)"
  sleep 10
fi

echo "==> Ensuring schedule '$SCHEDULE_NAME' pings /health every ${INTERVAL_MINUTES} minutes until $END_DATE"
echo "    (cost note: EventBridge Scheduler is \$1.00 per million invocations; every"
echo "     5 minutes is ~8,640/month, a fraction of a cent. The GET itself falls inside"
echo "     Lambda's free tier. Rate-limited to 1 request/sec at the destination.)"
TARGET_JSON="{\"Arn\":\"$API_DEST_ARN\",\"RoleArn\":\"$SCHEDULER_ROLE_ARN\"}"
SCHEDULE_EXPR="rate(${INTERVAL_MINUTES} minutes)"

if aws scheduler get-schedule --name "$SCHEDULE_NAME" --region "$REGION" >/dev/null 2>&1; then
  aws scheduler update-schedule \
    --name "$SCHEDULE_NAME" \
    --schedule-expression "$SCHEDULE_EXPR" \
    --flexible-time-window '{"Mode":"OFF"}' \
    --target "$TARGET_JSON" \
    --end-date "$END_DATE" \
    --description "Standing: periodic GET $HEALTH_URL to keep Lambda + CockroachDB warm" \
    --state ENABLED \
    --region "$REGION" \
    >/dev/null
  echo "    updated"
else
  aws scheduler create-schedule \
    --name "$SCHEDULE_NAME" \
    --schedule-expression "$SCHEDULE_EXPR" \
    --flexible-time-window '{"Mode":"OFF"}' \
    --target "$TARGET_JSON" \
    --end-date "$END_DATE" \
    --description "Standing: periodic GET $HEALTH_URL to keep Lambda + CockroachDB warm" \
    --state ENABLED \
    --region "$REGION" \
    >/dev/null
  echo "    created"
fi

echo
echo "==> Keep-warm schedule active"
echo "    Target:    $HEALTH_URL"
echo "    Frequency: every ${INTERVAL_MINUTES} minutes"
echo "    Ends:      $END_DATE (edit END_DATE / re-run to extend, or:"
echo "               aws scheduler delete-schedule --name $SCHEDULE_NAME --region $REGION)"
