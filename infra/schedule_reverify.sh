#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Put trust decay on a schedule, with a receipt and an alarm.
#
# WHAT THIS SOLVES. `backend/reverify.py` re-checks granted standing against CloudWatch and
# demotes memories reality no longer supports. It existed and worked, but nothing ran it: the
# README claimed a periodic re-verification job that the infrastructure never actually created.
# A trust ladder that can only ever climb is a folklore generator with extra steps, so "it can
# decay" has to mean "it does decay, on a clock, and you can prove it ran".
#
# WHY A DIRECT LAMBDA INVOKE AND NOT AN HTTP ROUTE. The sweep DEMOTES trust tiers. Exposing it
# over HTTP would put a mutating endpoint on a Function URL whose auth type is NONE, and would
# require giving EventBridge the shared secret -- a second copy of the credential, in a second
# service, for a job that never needs to leave AWS. EventBridge Scheduler can invoke the function
# directly instead, so the trigger is gated by IAM: this role may perform exactly one action
# (lambda:InvokeFunction) on exactly one resource (this function). The public surface gains
# nothing, and `backend/handler.py::_is_scheduled_invocation` refuses anything HTTP-shaped.
#
#   ./infra/schedule_reverify.sh
#   RATE='rate(6 hours)' ./infra/schedule_reverify.sh
set -euo pipefail

FUNCTION_NAME="${FUNCTION_NAME:-memorystand}"
REGION="${REGION:-${AWS_REGION:-${AWS_DEFAULT_REGION:-us-west-2}}}"
# Daily by default. MEMORYSTAND_TRUST_STALE_DAYS is 7, so a memory becomes eligible a week after
# its last check; sweeping daily means it is re-checked within a day of becoming stale, and the
# BATCH_LIMIT of 200 bounds what any single run can touch.
RATE="${RATE:-rate(1 day)}"
SCHEDULE_NAME="${SCHEDULE_NAME:-memorystand-reverify}"
ROLE_NAME="${ROLE_NAME:-memorystand-reverify-scheduler-role}"
ALARM_NAME="${ALARM_NAME:-memorystand-reverify-failed}"
LOG_GROUP="/aws/lambda/${FUNCTION_NAME}"

echo "==> Function: $FUNCTION_NAME   Region: $REGION   Schedule: $RATE"

FUNCTION_ARN="$(aws lambda get-function-configuration --function-name "$FUNCTION_NAME" \
  --region "$REGION" --query 'FunctionArn' --output text)"
echo "    $FUNCTION_ARN"

BUILD_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." >/dev/null 2>&1 && pwd)/infra/build"
mkdir -p "$BUILD_DIR"

# --- 1. A role EventBridge Scheduler can assume, able to do one thing ------------------------
echo "==> IAM role '$ROLE_NAME' (trust: scheduler.amazonaws.com only)"
cat > "$BUILD_DIR/reverify-trust.json" <<'EOF'
{
  "Version": "2012-10-17",
  "Statement": [
    { "Effect": "Allow", "Principal": { "Service": "scheduler.amazonaws.com" },
      "Action": "sts:AssumeRole" }
  ]
}
EOF

if aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
  echo "    already exists, reusing"
else
  aws iam create-role --role-name "$ROLE_NAME" \
    --assume-role-policy-document "file://$BUILD_DIR/reverify-trust.json" \
    --description "MemoryStand: lets EventBridge Scheduler invoke the re-verification sweep" \
    >/dev/null
  echo "    created"
fi

# Least privilege, and worth being literal about it: one action, one resource. Not lambda:*,
# not a wildcard ARN. If this role leaked it could invoke this function and nothing else.
cat > "$BUILD_DIR/reverify-invoke.json" <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    { "Effect": "Allow", "Action": "lambda:InvokeFunction", "Resource": "$FUNCTION_ARN" }
  ]
}
EOF
aws iam put-role-policy --role-name "$ROLE_NAME" --policy-name "invoke-memorystand" \
  --policy-document "file://$BUILD_DIR/reverify-invoke.json"
ROLE_ARN="$(aws iam get-role --role-name "$ROLE_NAME" --query 'Role.Arn' --output text)"
echo "    policy attached: lambda:InvokeFunction on this function only"

# --- 2. The schedule ------------------------------------------------------------------------
# The payload IS the authorisation-relevant part: a top-level marker the Function URL can never
# produce. See handler._is_scheduled_invocation.
PAYLOAD='{"memorystand_task":"reverify"}'
echo "==> Schedule '$SCHEDULE_NAME'  $RATE"
SCHEDULE_ARGS=(
  --name "$SCHEDULE_NAME"
  --region "$REGION"
  --schedule-expression "$RATE"
  --flexible-time-window '{"Mode":"OFF"}'
  --description "MemoryStand: re-check granted trust against CloudWatch and demote what no longer holds"
  --target "{\"Arn\":\"$FUNCTION_ARN\",\"RoleArn\":\"$ROLE_ARN\",\"Input\":$(python3 -c "import json,sys;print(json.dumps(sys.argv[1]))" "$PAYLOAD"),\"RetryPolicy\":{\"MaximumRetryAttempts\":2,\"MaximumEventAgeInSeconds\":3600}}"
)
if aws scheduler get-schedule --name "$SCHEDULE_NAME" --region "$REGION" >/dev/null 2>&1; then
  aws scheduler update-schedule "${SCHEDULE_ARGS[@]}" >/dev/null
  echo "    updated"
else
  # A new role can take a few seconds to become assumable; retry rather than fail the run.
  for attempt in 1 2 3 4 5; do
    if aws scheduler create-schedule "${SCHEDULE_ARGS[@]}" >/dev/null 2>"$BUILD_DIR/sched.err"; then
      echo "    created"; break
    fi
    if grep -qi "assume\|not authorized\|ValidationException" "$BUILD_DIR/sched.err" && [ "$attempt" -lt 5 ]; then
      echo "    role not assumable yet, retrying ($attempt/5)"; sleep 6
    else
      cat "$BUILD_DIR/sched.err" >&2; exit 1
    fi
  done
fi

# --- 3. A failure alarm keyed on the sweep's own log token -----------------------------------
# NOT the Lambda's Errors metric: this function also serves every HTTP route, so that metric
# cannot distinguish "the nightly trust sweep is broken" from "someone sent a bad request".
# handler._run_scheduled_task logs the exact token below when the sweep raises.
echo "==> Metric filter + alarm on 'reverify_sweep_failed'"
aws logs put-metric-filter \
  --region "$REGION" \
  --log-group-name "$LOG_GROUP" \
  --filter-name "memorystand-reverify-failed" \
  --filter-pattern '"reverify_sweep_failed"' \
  --metric-transformations \
    metricName=ReverifySweepFailed,metricNamespace=MemoryStand,metricValue=1,defaultValue=0 \
  >/dev/null
echo "    metric filter: MemoryStand/ReverifySweepFailed"

aws cloudwatch put-metric-alarm \
  --region "$REGION" \
  --alarm-name "$ALARM_NAME" \
  --alarm-description "MemoryStand re-verification sweep raised. Trust decay may have stopped." \
  --namespace MemoryStand --metric-name ReverifySweepFailed \
  --statistic Sum --period 3600 --evaluation-periods 1 \
  --threshold 1 --comparison-operator GreaterThanOrEqualToThreshold \
  --treat-missing-data notBreaching \
  >/dev/null
echo "    alarm: $ALARM_NAME"

echo
echo "==> Done."
echo "    Schedule : $SCHEDULE_NAME  ($RATE)"
echo "    Receipt  : each run logs 'reverify_sweep_completed' with the counts, in $LOG_GROUP"
echo "    Alarm    : $ALARM_NAME fires on 'reverify_sweep_failed'"
echo
echo "    Run it once by hand:"
echo "      aws lambda invoke --function-name $FUNCTION_NAME --region $REGION \\"
echo "        --cli-binary-format raw-in-base64-out \\"
echo "        --payload '{\"memorystand_task\":\"reverify\",\"dry_run\":true}' /dev/stdout"
echo
echo "    Teardown:"
echo "      aws scheduler delete-schedule --name $SCHEDULE_NAME --region $REGION"
echo "      aws cloudwatch delete-alarms --alarm-names $ALARM_NAME --region $REGION"
echo "      aws logs delete-metric-filter --log-group-name $LOG_GROUP --filter-name memorystand-reverify-failed --region $REGION"
echo "      aws iam delete-role-policy --role-name $ROLE_NAME --policy-name invoke-memorystand"
echo "      aws iam delete-role --role-name $ROLE_NAME"
