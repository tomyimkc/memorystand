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

def load(path):
    try:
        d = json.load(open(path))
    except Exception:
        return {}
    return json.loads(d) if isinstance(d, str) else d

def index(doc):
    """Sid -> (actions, resources). Both matter."""
    out = {}
    for st in doc.get("Statement", []):
        a, r = st.get("Action", []), st.get("Resource", [])
        out[st.get("Sid", "<no-sid>")] = (
            frozenset(a if isinstance(a, list) else [a]),
            frozenset(r if isinstance(r, list) else [r]),
        )
    return out

live, new = index(load(sys.argv[1])), index(load(sys.argv[2]))

changed = False
for sid in sorted(set(live) | set(new)):
    la, lr = live.get(sid, (frozenset(), frozenset()))
    na, nr = new.get(sid, (frozenset(), frozenset()))
    if (la, lr) == (na, nr):
        continue
    changed = True
    label = "+ new statement" if sid not in live else "- removed" if sid not in new else "~ changed"
    print("    [%s] %s" % (label, sid))
    for x in sorted(na - la):
        print("        + action    %s" % x)
    for x in sorted(la - na):
        print("        - action    %s" % x)
    # Resource changes matter as much as action changes: a permission can be granted in
    # name and still denied because the ARN does not cover what you actually call. The
    # first version of this diff showed only actions, which hid exactly that case.
    for x in sorted(nr - lr):
        print("        + resource  %s" % x)
    for x in sorted(lr - nr):
        print("        - resource  %s" % x)

if not changed:
    print("    (identical -- nothing to apply)")
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
  AWS_PROFILE=memorystand AWS_REGION=us-west-2 .venv/bin/python scripts/spike_bedrock.py
EOF
