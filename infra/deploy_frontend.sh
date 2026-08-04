#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# MemoryStand -- deploy frontend/ (the static dashboard) to AWS Amplify Hosting.
#
# HONESTY NOTE, written before this script was ever run: the machine that wrote this
# script has no AWS credentials and no CockroachDB Cloud session (see the top of the repo
# for how the memory layer itself is reached). This script has therefore been read
# carefully against the AWS CLI's own `help` output for every subcommand it calls, and
# against `aws amplify create-deployment help` / `start-deployment help` / `get-job help`
# specifically, but it has NEVER actually created an Amplify app or served a byte of
# frontend/ from AWS. Do not read "the dashboard is live" anywhere in this repo until
# someone with real credentials has run this end to end and can point at a URL that
# actually answers. If this script has a bug that only shows up against the real Amplify
# API, that is expected -- report it back rather than assume it works because it reads
# plausibly.
#
# WHY AMPLIFY HOSTING, NOT S3+CLOUDFRONT: the assignment for this repo already commits to
# Amplify Hosting (see README's AWS services list and amplify.yml at the repo root), and
# Amplify Hosting is a strictly smaller script than hand-rolling S3 (bucket + website
# config + public-access block) plus CloudFront (distribution + OAC + cache invalidation)
# for the same two-file static site -- one API surface instead of two, and Amplify already
# gives a stable HTTPS URL with no separate ACM certificate or CloudFront distribution to
# provision. The cost trade-off is a few cents either way at this traffic (see the bottom
# of this file); it did not decide the choice.
#
# WHY "MANUAL" DEPLOYMENT (create-deployment), NOT GIT-CONNECTED: Amplify Hosting's
# normal continuous-deployment mode needs either an OAuth/GitHub-App connection or a
# personal access token so Amplify can clone the repo itself. Wiring that up is an
# interactive console step (or a token this script would have to be handed), and it
# would also mean every push to this repo triggers a rebuild of a 2-file static site.
# `aws amplify create-deployment` + `start-deployment` instead uploads a zip of exactly
# what's in frontend/ right now, on demand, from a plain `aws` credential -- the same
# shape as infra/deploy.sh's Lambda zip upload. amplify.yml is still committed at the
# repo root for the day the owner *does* want to connect Git continuous deployment
# through the console; this script does not read or need that file.
#
# Usage:
#   ./infra/deploy_frontend.sh
#   APP_NAME=memorystand BRANCH_NAME=main REGION=us-east-1 ./infra/deploy_frontend.sh
#
# Re-run any time: every AWS call below is written to converge (find-or-create the app,
# find-or-create the branch, always push a fresh deployment) rather than fail on a second
# run -- the same idempotency contract as infra/deploy.sh.
#
# Teardown (see also the printed note at the end of a successful run):
#   aws amplify delete-app --app-id <app-id> --region "$REGION"
#   This deletes the app, every branch on it, and every deployment -- there is nothing
#   else this script creates outside of Amplify (no S3 bucket, no CloudFront
#   distribution, no Route 53 record) to clean up separately.
#
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." >/dev/null 2>&1 && pwd)"

APP_NAME="${APP_NAME:-memorystand}"
BRANCH_NAME="${BRANCH_NAME:-main}"
REGION="${REGION:-${AWS_REGION:-${AWS_DEFAULT_REGION:-us-west-2}}}"
FRONTEND_DIR="$REPO_ROOT/frontend"
BUILD_DIR="$REPO_ROOT/infra/build"
ZIP_PATH="$BUILD_DIR/frontend.zip"
# create-deployment -> start-deployment must both happen inside an 8-hour window (AWS
# enforces this); this script does both back to back, well inside that limit.
JOB_POLL_INTERVAL_S=5
JOB_POLL_TIMEOUT_S=180

echo "==> Checking required tools"
need() { command -v "$1" >/dev/null 2>&1 || { echo "missing required tool: $1" >&2; exit 1; }; }
need aws
need zip
need curl
need python3

echo "==> Checking frontend/ exists and has content to deploy"
if [[ ! -f "$FRONTEND_DIR/index.html" ]]; then
  echo "ERROR: $FRONTEND_DIR/index.html does not exist. Nothing to deploy." >&2
  exit 1
fi

echo "==> Checking AWS credentials"
if ! caller_identity="$(aws sts get-caller-identity --region "$REGION" --output json 2>&1)"; then
  echo "No usable AWS credentials in this environment. This script cannot deploy without them." >&2
  echo "Nothing was created or modified. Details: $caller_identity" >&2
  exit 1
fi
ACCOUNT_ID="$(echo "$caller_identity" | python3 -c 'import json,sys; print(json.load(sys.stdin)["Account"])')"
echo "    ok (account $ACCOUNT_ID, region $REGION)"

echo "==> Looking for an existing Amplify app named '$APP_NAME'"
APP_ID="$(aws amplify list-apps --region "$REGION" \
  --query "apps[?name=='${APP_NAME}'].appId | [0]" --output text 2>&1)"
if [[ "$APP_ID" == "None" || -z "$APP_ID" ]]; then
  echo "    not found, creating"
  APP_ID="$(aws amplify create-app \
    --name "$APP_NAME" \
    --description "MemoryStand demo dashboard -- static site, no build step (see amplify.yml)" \
    --platform WEB \
    --region "$REGION" \
    --query 'app.appId' --output text)"
  echo "    created app $APP_ID"
else
  echo "    found: $APP_ID (reusing)"
fi

echo "==> Looking for branch '$BRANCH_NAME' on app $APP_ID"
if aws amplify get-branch --app-id "$APP_ID" --branch-name "$BRANCH_NAME" --region "$REGION" >/dev/null 2>&1; then
  echo "    already exists, reusing"
else
  echo "    not found, creating"
  aws amplify create-branch \
    --app-id "$APP_ID" \
    --branch-name "$BRANCH_NAME" \
    --stage PRODUCTION \
    --description "MemoryStand judge-facing demo dashboard" \
    --region "$REGION" \
    >/dev/null
  echo "    created"
fi

echo "==> Zipping $FRONTEND_DIR"
mkdir -p "$BUILD_DIR"
rm -f "$ZIP_PATH"
# cd into frontend/ so index.html and app.js land at the ZIP ROOT, not under a
# "frontend/" prefix -- Amplify Hosting serves whatever is at the root of the uploaded
# zip as the site root.
( cd "$FRONTEND_DIR" && zip -r9q "$ZIP_PATH" . -x '.*' )
ZIP_BYTES="$(wc -c < "$ZIP_PATH" | tr -d ' ')"
echo "    $ZIP_PATH (${ZIP_BYTES} bytes)"

echo "==> Requesting a deployment slot (create-deployment)"
DEPLOYMENT_JSON="$(aws amplify create-deployment \
  --app-id "$APP_ID" \
  --branch-name "$BRANCH_NAME" \
  --region "$REGION" \
  --output json)"
JOB_ID="$(echo "$DEPLOYMENT_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["jobId"])')"
ZIP_UPLOAD_URL="$(echo "$DEPLOYMENT_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["zipUploadUrl"])')"
echo "    job $JOB_ID"

echo "==> Uploading zip to the presigned URL"
if ! curl -sS --fail -X PUT -T "$ZIP_PATH" "$ZIP_UPLOAD_URL"; then
  echo "ERROR: zip upload failed. The deployment job ($JOB_ID) was created but never" >&2
  echo "started -- it will simply expire unused; nothing is live. Re-run this script." >&2
  exit 1
fi
echo "    uploaded"

echo "==> Starting the deployment"
aws amplify start-deployment \
  --app-id "$APP_ID" \
  --branch-name "$BRANCH_NAME" \
  --job-id "$JOB_ID" \
  --region "$REGION" \
  >/dev/null

echo "==> Waiting for the deployment to finish (polling every ${JOB_POLL_INTERVAL_S}s, up to ${JOB_POLL_TIMEOUT_S}s)"
elapsed=0
status="PENDING"
while (( elapsed < JOB_POLL_TIMEOUT_S )); do
  status="$(aws amplify get-job \
    --app-id "$APP_ID" \
    --branch-name "$BRANCH_NAME" \
    --job-id "$JOB_ID" \
    --region "$REGION" \
    --query 'job.summary.status' --output text)"
  echo "    status: $status (${elapsed}s elapsed)"
  case "$status" in
    SUCCEED) break ;;
    FAILED|CANCELLED)
      echo "ERROR: deployment job $JOB_ID ended with status $status." >&2
      echo "Inspect it with: aws amplify get-job --app-id $APP_ID --branch-name $BRANCH_NAME --job-id $JOB_ID --region $REGION" >&2
      exit 1
      ;;
  esac
  sleep "$JOB_POLL_INTERVAL_S"
  elapsed=$(( elapsed + JOB_POLL_INTERVAL_S ))
done
if [[ "$status" != "SUCCEED" ]]; then
  echo "ERROR: deployment job $JOB_ID did not reach SUCCEED within ${JOB_POLL_TIMEOUT_S}s (last status: $status)." >&2
  echo "It may still finish -- check with the get-job command above rather than re-running this script blind." >&2
  exit 1
fi

DEFAULT_DOMAIN="$(aws amplify get-app --app-id "$APP_ID" --region "$REGION" \
  --query 'app.defaultDomain' --output text)"
SITE_URL="https://${BRANCH_NAME}.${DEFAULT_DOMAIN}"

echo
echo "==> Deploy complete"
echo "    App ID:    $APP_ID"
echo "    Branch:    $BRANCH_NAME"
echo "    Region:    $REGION"
echo "    Public URL: $SITE_URL"
echo
echo "The dashboard talks to the API over ?api=<url>; point a judge at, e.g.:"
echo "    ${SITE_URL}/?api=<the Lambda Function URL from infra/deploy.sh>"
echo
echo "Cost (verify current pricing at https://aws.amazon.com/amplify/pricing/ -- figures"
echo "below are what that page states as of the time this script was written):"
echo "    - CDN storage: free up to 5 GB/month, then \$0.023/GB-month -- this site is under"
echo "      100 KB total, so storage cost is \$0."
echo "    - Data transfer out: free up to 15 GB/month, then \$0.15/GB served. A hackathon"
echo "      judging window (~6 weeks) with even a few hundred page loads stays multiple"
echo "      orders of magnitude under 15 GB. Realistic cost for judge traffic: \$0.00,"
echo "      almost certainly inside the always-free hosting tier (and separately, new AWS"
echo "      accounts get 12 months of free tier / trial credits on top of that)."
echo "    - No build minutes are consumed by this manual-deployment path (create-deployment"
echo "      + start-deployment never invokes the build phase in amplify.yml)."
echo "    - The only ongoing charge if this is left running past the hackathon is the"
echo "      recurring storage charge, and that is \$0 at this site's size regardless."
echo
echo "Teardown: aws amplify delete-app --app-id $APP_ID --region $REGION"
echo "  (deletes the app, this branch, and all deployment history -- nothing else in AWS"
echo "  was created by this script.)"
