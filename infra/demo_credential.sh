#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Mint the PUBLIC demo credential: a write secret scoped to exactly one tenant.
#
# WHY THIS EXISTS. Until now one global secret authorised every write on every tenant. That single
# fact had two consequences, one obvious and one not:
#
#   * a reviewer could not be given write access without being given EVERYTHING, so three of the
#     four dashboard panels were read-only and the product could only be described, not driven;
#   * and the authorisation story was "one credential owns the store", which is the weakest part
#     of an otherwise careful security posture.
#
# A tenant-scoped credential fixes both at once. It is published at GET /health and pre-filled
# into the dashboard, and `backend/handler.py::_scope_tenant` returns 401 if it is used for any
# tenant other than the one below -- enforced server-side, with a regression test that was checked
# to fail when the enforcement is removed. Publishing it is safe because it is powerless
# elsewhere; that is the whole design, not a mitigation bolted on afterwards.
#
# The operator secret (/memorystand/shared_secret) is untouched and is never served by /health.
#
#   ./infra/demo_credential.sh                 # mint (or rotate) and store
#   DEMO_TENANT=<uuid> ./infra/demo_credential.sh
set -euo pipefail

REGION="${REGION:-${AWS_REGION:-${AWS_DEFAULT_REGION:-us-west-2}}}"
# The seeded demo tenant the dashboard already opens on, so a reviewer can write to the same
# tenant whose memories they are looking at.
DEMO_TENANT="${DEMO_TENANT:-9c8f6e5a-9d1a-4a1c-8f2e-3b6d1c7a4e10}"
SECRET_PARAM="${SECRET_PARAM:-/memorystand/demo_secret}"
TENANT_PARAM="${TENANT_PARAM:-/memorystand/demo_tenant}"

echo "==> Region: $REGION"
echo "==> Demo tenant: $DEMO_TENANT"

# Rotatable by design: re-running mints a new value, and because the dashboard reads it from
# /health rather than from a baked-in constant, rotation needs no frontend redeploy.
VALUE="${DEMO_SECRET_VALUE:-demo_$(python3 -c 'import secrets;print(secrets.token_urlsafe(24))')}"

aws ssm put-parameter --region "$REGION" --name "$SECRET_PARAM" \
  --type SecureString --overwrite --value "$VALUE" \
  --description "MemoryStand PUBLIC demo credential -- write access to the demo tenant only" >/dev/null
echo "    stored $SECRET_PARAM (SecureString)"

aws ssm put-parameter --region "$REGION" --name "$TENANT_PARAM" \
  --type String --overwrite --value "$DEMO_TENANT" \
  --description "MemoryStand: the only tenant the demo credential may write to" >/dev/null
echo "    stored $TENANT_PARAM"

echo
echo "==> Done. The Lambda reads both at request time (60s cache), so no redeploy is needed."
echo "    Verify:   curl -s \$API/health | python3 -m json.tool | grep -A4 '\"demo\"'"
echo
echo "    Confirm the scoping actually holds (should be 401):"
echo "      curl -s -X POST \$API/ingest -H 'content-type: application/json' \\"
echo "        -H \"x-memorystand-secret: $VALUE\" \\"
echo "        -d '{\"tenant_id\":\"00000000-0000-0000-0000-000000000000\",\"agent_id\":\"...\",\"content\":\"x\"}'"
echo
echo "    Revoke (removes the public write path entirely; /health drops the demo block):"
echo "      aws ssm delete-parameter --name $SECRET_PARAM --region $REGION"
