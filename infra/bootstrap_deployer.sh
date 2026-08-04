#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Create the scoped `memorystand-deployer` IAM user, its least-privilege policy, and an access
# key wired into a named CLI profile -- so the day-to-day credential is NOT your console
# identity.
#
# Run this ONCE, with an admin-capable credential active (easiest: `aws login`, which gets
# temporary console-derived credentials). After it finishes, everything else in infra/ runs as
# `--profile memorystand`, which can only touch resources named memorystand*.
#
#   aws login                      # or any admin credential
#   ./infra/bootstrap_deployer.sh
#   export AWS_PROFILE=memorystand
#   aws sts get-caller-identity
#
# The secret access key is NEVER printed. It is written straight into ~/.aws/credentials via
# `aws configure set`, which creates the file 0600. Nothing echoes it, so it cannot end up in
# your scrollback or a screen recording.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." >/dev/null 2>&1 && pwd)"

USER_NAME="${USER_NAME:-memorystand-deployer}"
POLICY_NAME="${POLICY_NAME:-MemoryStandDeployer}"
PROFILE="${PROFILE:-memorystand}"
REGION="${REGION:-us-west-2}"
POLICY_SRC="$REPO_ROOT/infra/deployer_policy.json"

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
die() { echo "ERROR: $*" >&2; exit 1; }

command -v aws >/dev/null 2>&1 || die "aws CLI not found"
[[ -f "$POLICY_SRC" ]] || die "missing $POLICY_SRC"

say "Checking the credential you are bootstrapping with"
if ! ident="$(aws sts get-caller-identity --output json 2>&1)"; then
  cat >&2 <<'EOF'
No usable AWS credentials.

Get one, then re-run this script:
  aws login          # temporary credentials from your console session (no long-lived key)
or
  aws configure      # if you already made an access key by hand

Nothing was created.
EOF
  exit 1
fi
ACCOUNT_ID="$(python3 -c 'import json,sys;print(json.load(sys.stdin)["Account"])' <<<"$ident")"
CALLER_ARN="$(python3 -c 'import json,sys;print(json.load(sys.stdin)["Arn"])' <<<"$ident")"
echo "    account $ACCOUNT_ID"
echo "    as      $CALLER_ARN"
case "$CALLER_ARN" in
  *":root") echo "    note: this is the ROOT identity. Fine for this one bootstrap step;" ;
            echo "          the whole point of what follows is that you stop using it." ;;
  *":user/$USER_NAME")
    cat >&2 <<EOF

You are already running AS $USER_NAME, so this has been bootstrapped before.

That identity intentionally cannot create IAM policies or users -- it is scoped to
memorystand* resources and nothing else. Re-running here can only fail.

  - To CHANGE its permissions:  AWS_PROFILE=default ./infra/update_deployer_policy.sh
  - To rebuild it from nothing: unset AWS_PROFILE && aws login && ./infra/bootstrap_deployer.sh

Nothing was changed.
EOF
    exit 1 ;;
esac

say "Rendering the policy for account $ACCOUNT_ID"
RENDERED="$(mktemp)"; trap 'rm -f "$RENDERED"' EXIT
# Strip the JSON "Comment" key -- IAM rejects unknown top-level members.
python3 - "$POLICY_SRC" "$ACCOUNT_ID" > "$RENDERED" <<'PY'
import json, sys
doc = json.load(open(sys.argv[1]))
doc.pop("Comment", None)
text = json.dumps(doc).replace("ACCOUNT_ID", sys.argv[2])
print(text)
PY
echo "    $(python3 -c 'import json,sys;d=json.load(open(sys.argv[1]));print(len(d["Statement"]),"statements")' "$RENDERED")"

say "Creating or updating the customer-managed policy $POLICY_NAME"
POLICY_ARN="arn:aws:iam::${ACCOUNT_ID}:policy/${POLICY_NAME}"
if aws iam get-policy --policy-arn "$POLICY_ARN" >/dev/null 2>&1; then
  # A policy may hold at most 5 versions; prune non-default ones before adding another.
  for v in $(aws iam list-policy-versions --policy-arn "$POLICY_ARN" \
               --query 'Versions[?IsDefaultVersion==`false`].VersionId' --output text); do
    aws iam delete-policy-version --policy-arn "$POLICY_ARN" --version-id "$v" >/dev/null 2>&1 || true
  done
  aws iam create-policy-version --policy-arn "$POLICY_ARN" \
      --policy-document "file://$RENDERED" --set-as-default >/dev/null
  echo "    updated (new default version)"
else
  aws iam create-policy --policy-name "$POLICY_NAME" \
      --policy-document "file://$RENDERED" \
      --description "Least-privilege deploy permissions for MemoryStand" >/dev/null
  echo "    created $POLICY_ARN"
fi

say "Creating the user $USER_NAME (CLI-only: no console password)"
if aws iam get-user --user-name "$USER_NAME" >/dev/null 2>&1; then
  echo "    already exists"
else
  aws iam create-user --user-name "$USER_NAME" \
      --tags Key=project,Value=memorystand Key=purpose,Value=hackathon-deploy >/dev/null
  echo "    created"
fi

say "Attaching $POLICY_NAME"
aws iam attach-user-policy --user-name "$USER_NAME" --policy-arn "$POLICY_ARN"
echo "    attached"

say "Access key"
EXISTING="$(aws iam list-access-keys --user-name "$USER_NAME" \
            --query 'AccessKeyMetadata[].AccessKeyId' --output text 2>/dev/null || true)"
if [[ -n "$EXISTING" ]]; then
  cat <<EOF
    This user already has a key: $EXISTING
    AWS shows a secret only at creation, so an existing key cannot be re-read.
    If you still have it, configure it yourself:
        aws configure --profile $PROFILE
    If you lost it, delete and re-run:
        aws iam delete-access-key --user-name $USER_NAME --access-key-id $EXISTING
EOF
else
  KEY_JSON="$(aws iam create-access-key --user-name "$USER_NAME" --output json)"
  AK="$(python3 -c 'import json,sys;print(json.load(sys.stdin)["AccessKey"]["AccessKeyId"])' <<<"$KEY_JSON")"
  SK="$(python3 -c 'import json,sys;print(json.load(sys.stdin)["AccessKey"]["SecretAccessKey"])' <<<"$KEY_JSON")"
  # Written, never echoed.
  aws configure set aws_access_key_id     "$AK" --profile "$PROFILE"
  aws configure set aws_secret_access_key "$SK" --profile "$PROFILE"
  aws configure set region                "$REGION" --profile "$PROFILE"
  aws configure set output                json --profile "$PROFILE"
  unset SK KEY_JSON
  chmod 600 "$HOME/.aws/credentials" 2>/dev/null || true
  echo "    created $AK and wrote it into profile '$PROFILE' (secret never printed)"
fi

say "Verifying the new profile works"
sleep 8  # IAM is eventually consistent; a brand-new key is not instantly usable
if new_ident="$(aws sts get-caller-identity --profile "$PROFILE" --output json 2>&1)"; then
  echo "    $(python3 -c 'import json,sys;print(json.load(sys.stdin)["Arn"])' <<<"$new_ident")"
else
  echo "    not usable yet -- IAM propagation can take up to ~30s. Retry:" >&2
  echo "      aws sts get-caller-identity --profile $PROFILE" >&2
fi

cat <<EOF

------------------------------------------------------------------
Done. Use the scoped profile from here on:

  export AWS_PROFILE=$PROFILE
  aws sts get-caller-identity

Still to do by hand in the console (nothing can automate them):
  1. Enable MFA on the root account, then stop using root.
  2. Bedrock -> Model access -> enable Amazon Nova Lite and Titan Text Embeddings V2
     (not Claude -- Anthropic models on Bedrock are geo-restricted from this operator's
     country; see docs/BEDROCK_QUOTA.md).
  3. Billing -> Budgets -> a \$20 monthly cost budget. Bedrock has no free tier and the
     demo endpoint is public.

Then verify Bedrock end to end:
  AWS_PROFILE=$PROFILE AWS_REGION=$REGION .venv/bin/python scripts/spike_bedrock.py

When judging closes (after 2026-09-15), revoke:
  aws iam delete-access-key --user-name $USER_NAME --access-key-id <id>
  aws iam detach-user-policy --user-name $USER_NAME --policy-arn $POLICY_ARN
  aws iam delete-user --user-name $USER_NAME
------------------------------------------------------------------
EOF
