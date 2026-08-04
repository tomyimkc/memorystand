#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Push infra/deployer_policy.json to the live MemoryStandDeployer policy as a new default
# version. Run this after editing that file.
#
#   ./infra/update_deployer_policy.sh
#
# Deliberately a separate script rather than something the agent does inline: this WIDENS the
# permissions of the identity the deploy runs as, which is a change a human should approve
# rather than have happen as a side effect of debugging.
#
# It prints a diff of the actions being added or removed before doing anything, and asks for
# confirmation unless --yes is passed.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." >/dev/null 2>&1 && pwd)"
POLICY_SRC="$REPO_ROOT/infra/deployer_policy.json"
POLICY_NAME="${POLICY_NAME:-MemoryStandDeployer}"

ASSUME_YES=0
[[ "${1:-}" == "--yes" ]] && ASSUME_YES=1

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
die() { echo "ERROR: $*" >&2; exit 1; }

command -v aws >/dev/null 2>&1 || die "aws CLI not found"
[[ -f "$POLICY_SRC" ]] || die "missing $POLICY_SRC"

say "Identity"
ident="$(aws sts get-caller-identity --output json 2>&1)" \
  || die "no usable AWS credentials. Run 'aws login' (or set AWS_PROFILE) and retry."
ACCOUNT_ID="$(python3 -c 'import json,sys;print(json.load(sys.stdin)["Account"])' <<<"$ident")"
echo "    account $ACCOUNT_ID as $(python3 -c 'import json,sys;print(json.load(sys.stdin)["Arn"])' <<<"$ident")"

POLICY_ARN="arn:aws:iam::${ACCOUNT_ID}:policy/${POLICY_NAME}"

# The deployer identity deliberately cannot read or modify its own policy -- that is the
# whole point of least privilege, and it means THIS script must run as an admin credential.
# Detect that case by name so the error is the real one rather than a downstream mystery.
CALLER_ARN="$(python3 -c 'import json,sys;print(json.load(sys.stdin)["Arn"])' <<<"$ident")"
if [[ "$CALLER_ARN" == *":user/${USER_NAME:-memorystand-deployer}" ]]; then
  cat >&2 <<EOF
You are running as the scoped deploy identity ($CALLER_ARN).

By design it cannot change its own permissions -- an identity that can widen itself is not
really scoped. Re-run with an admin credential instead:

    AWS_PROFILE=default ./infra/update_deployer_policy.sh
  or
    unset AWS_PROFILE && aws login && ./infra/update_deployer_policy.sh

Nothing was changed.
EOF
  exit 1
fi

# Separate "not allowed to look" from "not there". Conflating them sends you to bootstrap
# for a policy that already exists, where you then hit a different denial entirely.
probe="$(aws iam get-policy --policy-arn "$POLICY_ARN" 2>&1)" || true
case "$probe" in
  *NoSuchEntity*) die "no policy $POLICY_ARN. Run ./infra/bootstrap_deployer.sh first." ;;
  *AccessDenied*) die "this credential cannot read $POLICY_ARN. Use an admin credential (AWS_PROFILE=default)." ;;
esac

RENDERED="$(mktemp)"; trap 'rm -f "$RENDERED"' EXIT
python3 - "$POLICY_SRC" "$ACCOUNT_ID" > "$RENDERED" <<'PY'
import json, sys
doc = json.load(open(sys.argv[1]))
doc.pop("Comment", None)          # IAM rejects unknown top-level members
print(json.dumps(doc).replace("ACCOUNT_ID", sys.argv[2]))
PY

say "What changes"
CURRENT_VER="$(aws iam get-policy --policy-arn "$POLICY_ARN" --query 'Policy.DefaultVersionId' --output text)"
aws iam get-policy-version --policy-arn "$POLICY_ARN" --version-id "$CURRENT_VER" \
    --query 'PolicyVersion.Document' --output json > "$RENDERED.live" 2>/dev/null || echo '{}' > "$RENDERED.live"
python3 - "$RENDERED.live" "$RENDERED" <<'PY'
import json, sys
def actions(path):
    try: d = json.load(open(path))
    except Exception: return set()
    if isinstance(d, str): d = json.loads(d)
    out = set()
    for s in d.get("Statement", []):
        a = s.get("Action", [])
        out.update(a if isinstance(a, list) else [a])
    return out
live, new = actions(sys.argv[1]), actions(sys.argv[2])
added, removed = sorted(new - live), sorted(live - new)
print(f"    + {a}" for a in []) if False else None
for a in added:   print(f"    + {a}")
for a in removed: print(f"    - {a}")
if not added and not removed:
    print("    (no action-level changes; resource scopes may still differ)")
PY
rm -f "$RENDERED.live"

if [[ "$ASSUME_YES" != "1" ]]; then
  printf '\nApply this to %s? [y/N] ' "$POLICY_ARN"
  read -r reply
  [[ "$reply" =~ ^[Yy]$ ]] || { echo "Aborted. Nothing changed."; exit 0; }
fi

say "Applying"
# A managed policy holds at most 5 versions; prune non-default ones first.
for v in $(aws iam list-policy-versions --policy-arn "$POLICY_ARN" \
             --query 'Versions[?IsDefaultVersion==`false`].VersionId' --output text); do
  aws iam delete-policy-version --policy-arn "$POLICY_ARN" --version-id "$v" >/dev/null 2>&1 || true
done
aws iam create-policy-version --policy-arn "$POLICY_ARN" \
    --policy-document "file://$RENDERED" --set-as-default >/dev/null
echo "    new default version set"

say "Waiting for IAM to propagate"
sleep 12
echo "    done"

cat <<EOF

Re-run the Bedrock check:
  AWS_PROFILE=memorystand AWS_REGION=us-east-1 .venv/bin/python scripts/spike_bedrock.py
EOF
