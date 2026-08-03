#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# MemoryStand -- build and deploy the Lambda that serves backend/ behind a Function URL.
#
# WHY DOCKER: the machine running this script has Python 3.14 locally, but the newest
# Lambda runtime is python3.13, and psycopg2-binary ships compiled C extensions linked
# against glibc -- a wheel resolved by the host's pip is not guaranteed to be the
# manylinux build Lambda's Amazon Linux runtime needs. Zipping host site-packages would
# deploy a function that fails at `import psycopg2` on cold start. So the dependency
# install runs inside `public.ecr.aws/lambda/python:3.13`, the same base image AWS
# publishes for this runtime, not `python:3.13-slim` -- that guarantees the compiled
# extension matches what the deployed function will actually execute against.
#
# WHY NOT boto3 IN THE ZIP: every AWS Lambda Python runtime ships boto3/botocore
# preinstalled. Vendoring our own would roughly double the package size for no behaviour
# difference this project depends on, and pushes closer to the 50 MB direct-upload zip
# limit. Only psycopg2-binary (not in the runtime) is installed into the package.
#
# Usage:
#   ./infra/deploy.sh
#   FUNCTION_NAME=memorystand REGION=us-east-1 ./infra/deploy.sh
#
# Re-run any time: every AWS call below is written to converge rather than fail on a
# second run (create-if-missing, update-if-present).
#
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." >/dev/null 2>&1 && pwd)"

FUNCTION_NAME="${FUNCTION_NAME:-memorystand}"
REGION="${REGION:-${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}}"
ROLE_NAME="${ROLE_NAME:-standing-lambda-role}"
RUNTIME="python3.13"
HANDLER="backend.handler.handler"
TIMEOUT_SECONDS=30
MEMORY_MB=512
RESERVED_CONCURRENCY=15
LOG_RETENTION_DAYS="${LOG_RETENTION_DAYS:-14}"
BUILD_IMAGE="public.ecr.aws/lambda/python:3.13"

BUILD_DIR="$REPO_ROOT/infra/build"
PACKAGE_DIR="$BUILD_DIR/package"
ZIP_PATH="$BUILD_DIR/standing-lambda.zip"
LOG_GROUP="/aws/lambda/${FUNCTION_NAME}"

echo "==> Checking required tools"
need() { command -v "$1" >/dev/null 2>&1 || { echo "missing required tool: $1" >&2; exit 1; }; }
need aws
need docker
need zip
need python3

echo "==> Checking AWS credentials"
if ! caller_identity="$(aws sts get-caller-identity --region "$REGION" --output json 2>&1)"; then
  echo "No usable AWS credentials in this environment. This script cannot deploy without them." >&2
  echo "Nothing was created or modified. Details: $caller_identity" >&2
  exit 1
fi
ACCOUNT_ID="$(echo "$caller_identity" | python3 -c 'import json,sys; print(json.load(sys.stdin)["Account"])')"
echo "    ok (account $ACCOUNT_ID, region $REGION)"

echo "==> Checking docker daemon is reachable"
if ! docker info >/dev/null 2>&1; then
  echo "docker CLI is installed but the daemon is not reachable (is Docker Desktop running?)." >&2
  exit 1
fi

# The Lambda entry point is not part of this script's job -- it belongs to whoever writes
# backend/handler.py. Fail before touching AWS at all rather than deploy a package that
# 500s on every request.
HANDLER_FILE="$REPO_ROOT/backend/handler.py"
if [[ ! -f "$HANDLER_FILE" ]]; then
  cat >&2 <<EOF
ERROR: $HANDLER_FILE does not exist yet.

This script deploys "$HANDLER" as the Lambda entry point. That module has not
been written. Nothing was created or modified in AWS -- there is no partial deploy to
clean up. Write backend/handler.py (exposing a module-level "handler(event, context)"
or "lambda_handler(event, context)"), then re-run ./infra/deploy.sh.

Contract this script assumes backend/handler.py follows (as of the version last read):
  - Reads the DSN directly from the MEMORYSTAND_DSN environment variable (backend/db.py's
    frozen dsn() contract) -- this script resolves /memorystand/dsn from SSM at DEPLOY
    TIME (with the deployer's own credentials, not the function's) and bakes the value
    into that Lambda environment variable. The function's own execution role is never
    granted permission to read /memorystand/dsn.
  - Resolves the kill switch and shared secret from SSM at RUNTIME, by parameter name,
    via env vars MEMORYSTAND_KILL_SWITCH_SSM_PARAM and MEMORYSTAND_SHARED_SECRET_SSM_PARAM
    (kms:Decrypt is scoped for the SecureString case in infra/iam_policy.json).
  - Serves GET /health by calling backend.db.server_version(), which requires a live
    connection -- this is the endpoint infra/keepwarm.sh pings on a schedule, so a
    warm Lambda in front of a suspended CockroachDB cluster is not mistaken for healthy.
EOF
  exit 1
fi

echo "==> Resolving the KMS key backing SSM SecureString (alias/aws/ssm)"
if ! KMS_KEY_ARN="$(aws kms describe-key --key-id alias/aws/ssm --region "$REGION" \
      --query 'KeyMetadata.Arn' --output text 2>&1)"; then
  echo "Could not resolve alias/aws/ssm in account $ACCOUNT_ID / region $REGION." >&2
  echo "That AWS-managed key is provisioned automatically the first time an SSM" >&2
  echo "SecureString parameter is created. Run ./infra/ssm_setup.sh first, then re-run this script." >&2
  echo "Details: $KMS_KEY_ARN" >&2
  exit 1
fi
echo "    $KMS_KEY_ARN"

echo "==> Rendering infra/iam_policy.json with ACCOUNT_ID=$ACCOUNT_ID REGION=$REGION"
mkdir -p "$BUILD_DIR"
RENDERED_POLICY="$BUILD_DIR/iam_policy.rendered.json"
sed \
  -e "s#KMS_KEY_ARN_PLACEHOLDER#${KMS_KEY_ARN}#g" \
  -e "s#ACCOUNT_ID#${ACCOUNT_ID}#g" \
  -e "s#REGION#${REGION}#g" \
  "$REPO_ROOT/infra/iam_policy.json" > "$RENDERED_POLICY"
# The rendered file also fixes the log group Sid to this function's name specifically.
sed -i.bak "s#log-group:/aws/lambda/memorystand:\\*#log-group:/aws/lambda/${FUNCTION_NAME}:*#g" "$RENDERED_POLICY"
rm -f "$RENDERED_POLICY.bak"

echo "==> Ensuring IAM role $ROLE_NAME exists (trust: lambda.amazonaws.com only)"
TRUST_POLICY="$BUILD_DIR/trust-policy.json"
cat > "$TRUST_POLICY" <<'EOF'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": { "Service": "lambda.amazonaws.com" },
      "Action": "sts:AssumeRole"
    }
  ]
}
EOF

role_created=0
if aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
  echo "    already exists, reusing"
else
  aws iam create-role \
    --role-name "$ROLE_NAME" \
    --assume-role-policy-document "file://$TRUST_POLICY" \
    --description "MemoryStand Lambda execution role -- least privilege, see infra/iam_policy.json" \
    >/dev/null
  role_created=1
  echo "    created"
fi

echo "==> Attaching the least-privilege inline policy (idempotent: put-role-policy overwrites)"
aws iam put-role-policy \
  --role-name "$ROLE_NAME" \
  --policy-name standing-least-privilege \
  --policy-document "file://$RENDERED_POLICY" \
  >/dev/null
ROLE_ARN="$(aws iam get-role --role-name "$ROLE_NAME" --query 'Role.Arn' --output text)"
echo "    $ROLE_ARN"

if [[ "$role_created" == 1 ]]; then
  echo "    (new role -- pausing 10s for IAM propagation before create-function references it)"
  sleep 10
fi

echo "==> Ensuring log group $LOG_GROUP exists with a ${LOG_RETENTION_DAYS}-day retention"
echo "    (cost note: CloudWatch Logs is free for the first 5 GB/month ingested + stored;"
echo "     retention keeps a demo left running past the hackathon from growing unbounded)"
aws logs create-log-group --log-group-name "$LOG_GROUP" --region "$REGION" 2>/dev/null || true
aws logs put-retention-policy --log-group-name "$LOG_GROUP" --retention-in-days "$LOG_RETENTION_DAYS" --region "$REGION"

echo "==> Building the deployment package inside $BUILD_IMAGE"
echo "    (image pull is free from public.ecr.aws; nothing here costs money until deploy)"
rm -rf "$PACKAGE_DIR"
mkdir -p "$PACKAGE_DIR"

# requirements.txt lines carry an inline "# ..." comment for humans; strip it and any
# trailing whitespace so pip gets a bare PEP 440 spec, not "psycopg2-binary==2.9.10   #
# CockroachDB is PostgreSQL wire-compatible" as one literal (invalid) argument.
PSYCOPG2_SPEC="$(grep -m1 '^psycopg2-binary' "$REPO_ROOT/requirements.txt" | sed 's/#.*//' | xargs)"
docker run --rm \
  --platform linux/amd64 \
  --entrypoint /bin/bash \
  --user "$(id -u):$(id -g)" \
  -v "$PACKAGE_DIR":/out \
  "$BUILD_IMAGE" \
  -c "pip install --no-cache-dir --target /out '${PSYCOPG2_SPEC}'"

cp -r "$REPO_ROOT/backend" "$PACKAGE_DIR/backend"
find "$PACKAGE_DIR" -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true

echo "==> Zipping package"
rm -f "$ZIP_PATH"
( cd "$PACKAGE_DIR" && zip -r9q "$ZIP_PATH" . -x '*.pyc' )
ZIP_BYTES="$(wc -c < "$ZIP_PATH" | tr -d ' ')"
ZIP_MB=$(( ZIP_BYTES / 1024 / 1024 ))
echo "    $ZIP_PATH (${ZIP_MB} MB)"
if (( ZIP_BYTES > 50 * 1024 * 1024 )); then
  echo "ERROR: package is over the 50 MB direct zip-upload limit for UpdateFunctionCode/CreateFunction." >&2
  echo "This script only supports the small direct-upload path; an S3-staged upload is out of scope here." >&2
  exit 1
fi

# Environment variables backend/handler.py actually reads. Two different trust levels,
# deliberately handled two different ways:
#
#   - MEMORYSTAND_DSN: backend/db.py's dsn() (frozen contract) reads this env var directly --
#     handler.py never calls SSM for it. So THIS SCRIPT resolves /memorystand/dsn from SSM
#     right now, with the *deployer's* credentials, and bakes the resolved value into the
#     function's environment (Lambda encrypts environment variables at rest). The
#     function's own execution role is deliberately never granted ssm:GetParameter on
#     /memorystand/dsn -- infra/iam_policy.json only covers kill_switch and shared_secret,
#     the two values handler.py resolves itself at runtime.
#   - MEMORYSTAND_KILL_SWITCH_SSM_PARAM / MEMORYSTAND_SHARED_SECRET_SSM_PARAM: parameter NAMES,
#     not values -- handler.py resolves these at runtime via ssm:GetParameter (matching
#     iam_policy.json), decrypting the shared secret with kms:Decrypt. Setting them here
#     is redundant with handler.py's own defaults but pins the contract explicitly.
#
# AWS_REGION is a reserved Lambda env key, already injected automatically -- not set here.
echo "==> Resolving /memorystand/dsn from SSM (deploy-time only; the function's own role cannot read it)"
if ! DSN_VALUE="$(aws ssm get-parameter --name /memorystand/dsn --with-decryption \
      --region "$REGION" --query 'Parameter.Value' --output text 2>&1)"; then
  echo "Could not read /memorystand/dsn from SSM in $REGION." >&2
  echo "Run ./infra/ssm_setup.sh first, then re-run this script. Details: $DSN_VALUE" >&2
  exit 1
fi

ENV_FILE="$BUILD_DIR/lambda-environment.json"
( umask 077 && MEMORYSTAND_DSN_FOR_JSON="$DSN_VALUE" python3 -c '
import json, os
print(json.dumps({"Variables": {
    "MEMORYSTAND_DSN": os.environ["MEMORYSTAND_DSN_FOR_JSON"],
    "MEMORYSTAND_KILL_SWITCH_SSM_PARAM": "/memorystand/kill_switch",
    "MEMORYSTAND_SHARED_SECRET_SSM_PARAM": "/memorystand/shared_secret",
    "MEMORYSTAND_EMBED_MODEL": "amazon.titan-embed-text-v2:0",
    "MEMORYSTAND_CHAT_MODEL": "anthropic.claude-3-5-haiku-20241022-v1:0",
}}))
' > "$ENV_FILE" )
unset DSN_VALUE
trap 'rm -f "$ENV_FILE"' EXIT

echo "==> Creating or updating function $FUNCTION_NAME"
echo "    (cost note: Lambda's free tier is 1M requests + 400,000 GB-s compute per month;"
echo "     a demo at ${MEMORY_MB}MB/${TIMEOUT_SECONDS}s worst case stays well inside that)"

create_or_update_code() {
  local attempt
  for attempt in 1 2 3 4 5 6; do
    if aws lambda get-function --function-name "$FUNCTION_NAME" --region "$REGION" >/dev/null 2>&1; then
      aws lambda update-function-code \
        --function-name "$FUNCTION_NAME" \
        --zip-file "fileb://$ZIP_PATH" \
        --region "$REGION" \
        >/dev/null
      aws lambda wait function-updated --function-name "$FUNCTION_NAME" --region "$REGION"
      aws lambda update-function-configuration \
        --function-name "$FUNCTION_NAME" \
        --runtime "$RUNTIME" \
        --handler "$HANDLER" \
        --timeout "$TIMEOUT_SECONDS" \
        --memory-size "$MEMORY_MB" \
        --role "$ROLE_ARN" \
        --environment "file://$ENV_FILE" \
        --region "$REGION" \
        >/dev/null
      aws lambda wait function-updated --function-name "$FUNCTION_NAME" --region "$REGION"
      return 0
    fi

    local create_err
    if create_err="$(aws lambda create-function \
        --function-name "$FUNCTION_NAME" \
        --runtime "$RUNTIME" \
        --handler "$HANDLER" \
        --role "$ROLE_ARN" \
        --timeout "$TIMEOUT_SECONDS" \
        --memory-size "$MEMORY_MB" \
        --environment "file://$ENV_FILE" \
        --zip-file "fileb://$ZIP_PATH" \
        --region "$REGION" 2>&1)"; then
      aws lambda wait function-active --function-name "$FUNCTION_NAME" --region "$REGION"
      return 0
    fi

    if [[ "$create_err" == *"cannot be assumed"* || "$create_err" == *"InvalidParameterValueException"* ]]; then
      echo "    role not yet assumable (IAM propagation), retrying in 5s (attempt $attempt/6)..."
      sleep 5
      continue
    fi

    echo "$create_err" >&2
    return 1
  done
  echo "Gave up waiting for IAM role propagation after 6 attempts." >&2
  return 1
}
create_or_update_code

echo "==> Setting reserved concurrency to $RESERVED_CONCURRENCY"
echo "    (CockroachDB Basic tolerates roughly 25 concurrent connections; the pool is 1"
echo "     connection per container, so this caps concurrent containers well under that,"
echo "     leaving headroom for the CLI and MCP server. No extra cost -- it only reserves"
echo "     capacity out of the account's shared 1000 concurrent-execution limit.)"
aws lambda put-function-concurrency \
  --function-name "$FUNCTION_NAME" \
  --reserved-concurrent-executions "$RESERVED_CONCURRENCY" \
  --region "$REGION" \
  >/dev/null

echo "==> Ensuring a public Function URL (auth type NONE) exists"
echo "    (no charge beyond normal Lambda invocation cost; anyone with the URL can call it --"
echo "     that is why /memorystand/shared_secret exists for the handler to check)"
if aws lambda get-function-url-config --function-name "$FUNCTION_NAME" --region "$REGION" >/dev/null 2>&1; then
  aws lambda update-function-url-config \
    --function-name "$FUNCTION_NAME" \
    --auth-type NONE \
    --region "$REGION" \
    >/dev/null
else
  aws lambda create-function-url-config \
    --function-name "$FUNCTION_NAME" \
    --auth-type NONE \
    --region "$REGION" \
    >/dev/null
fi

permission_err="$(aws lambda add-permission \
  --function-name "$FUNCTION_NAME" \
  --statement-id FunctionURLAllowPublicAccess \
  --action lambda:InvokeFunctionUrl \
  --principal "*" \
  --function-url-auth-type NONE \
  --region "$REGION" 2>&1)" && true
if [[ -n "$permission_err" && "$permission_err" != *"ResourceConflictException"* ]]; then
  echo "$permission_err" >&2
  exit 1
fi

FUNCTION_URL="$(aws lambda get-function-url-config \
  --function-name "$FUNCTION_NAME" \
  --region "$REGION" \
  --query 'FunctionUrl' --output text)"

echo
echo "==> Deploy complete"
echo "    Function:     $FUNCTION_NAME"
echo "    Runtime:      $RUNTIME"
echo "    Region:       $REGION"
echo "    Concurrency:  reserved=$RESERVED_CONCURRENCY"
echo "    Function URL: $FUNCTION_URL"
echo
echo "Next: ./infra/keepwarm.sh (keeps CockroachDB warm behind this URL through 2026-09-15)"
