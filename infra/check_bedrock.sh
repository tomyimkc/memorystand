#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Has Bedrock quota landed yet? Run this after AWS answers the quota-increase request.
#
# WHY THIS EXISTS. The deployed agent already tries Bedrock FIRST on every request and only
# falls through to the third-party router when Bedrock fails (backend/agent.py `_providers`).
# The circuit breaker re-probes Bedrock every 60s (backend/breaker.py), so the moment quota is
# granted the breaker closes and Bedrock takes over the reasoning path WITH NO REDEPLOY -- and
# `reasoning_source` flips from `api.teamorouter.com:claude-haiku-4-5` to
# `bedrock:amazon.nova-lite-v1:0` on its own. Nova Lite, not Claude, because Claude-on-Bedrock is
# geo-refused for this account (docs/BEDROCK_QUOTA.md).
#
# So nothing needs to be "switched to Bedrock" by hand. This script only tells you WHETHER the
# switch has happened / can happen yet, two ways:
#   1. a direct Bedrock Converse call with the configured model -- the ground truth for quota, and
#   2. the live /health circuit-breaker state -- what the deployed Lambda currently sees.
#
#   ./infra/check_bedrock.sh
set -euo pipefail

REGION="${MEMORYSTAND_BEDROCK_REGION:-${AWS_REGION:-us-west-2}}"
MODEL="${MEMORYSTAND_CHAT_MODEL:-amazon.nova-lite-v1:0}"
API="${MEMORYSTAND_API_BASE:-https://ojao6oaxlk26mqfjwpuy7g4dy40tglyi.lambda-url.us-west-2.on.aws}"

echo "==> Region: $REGION   Model: $MODEL"

# 1. Ground truth: can this account actually invoke the model on demand right now?
echo "==> Direct Bedrock Converse probe"
body=$(mktemp)
if aws bedrock-runtime converse \
      --region "$REGION" \
      --model-id "$MODEL" \
      --messages '[{"role":"user","content":[{"text":"reply with the single word: up"}]}]' \
      --inference-config '{"maxTokens":5,"temperature":0}' \
      >"$body" 2>"$body.err"; then
  reply=$(python3 -c "import json,sys;print(json.load(open('$body'))['output']['message']['content'][0]['text'].strip())" 2>/dev/null || echo "(parsed empty)")
  echo "    QUOTA IS LIVE ✓  model replied: '$reply'"
  echo "    The deployed agent will reason with Bedrock within ~60s (breaker half-open probe),"
  echo "    with no redeploy. reasoning_source will read bedrock:$MODEL."
  quota_up=1
else
  err=$(tr -d '\n' <"$body.err" | sed 's/  */ /g')
  echo "    quota NOT yet available:"
  echo "    ${err:0:220}"
  echo "    The agent keeps reasoning through the router standby until this clears."
  quota_up=0
fi
rm -f "$body" "$body.err"

# 2. What the live deployment currently sees.
echo "==> Live /health circuit-breaker state"
curl -s --max-time 25 "$API/health" 2>/dev/null | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
except Exception:
    print('    (could not reach /health)'); raise SystemExit(0)
cb = d.get('circuit_breakers', {})
print('    circuit_breakers:', json.dumps(cb))
print('    embedding_provenance:', d.get('embedding_provenance'))
b = str(cb.get('bedrock-converse', '')).lower()
# The breaker only opens after repeated /decide failures, so under low demo traffic it reads
# 'closed' by default -- that is NOT evidence Bedrock works. The direct probe above is the
# authoritative signal; this line only shows what the deployed Lambda has seen lately.
if b in ('open', 'half_open'):
    print('    bedrock-converse is', b.upper(), '-> recent /decide calls hit Bedrock and it failed; router served them.')
elif b == 'closed':
    print('    bedrock-converse is CLOSED -> no recent repeated Bedrock failures (default under low traffic).')
    print('    Trust the direct probe above, not this, for whether quota has landed.')
" || echo "    (health unreachable)"

echo
if [[ "${quota_up:-0}" == "1" ]]; then
  echo "==> RESULT: Bedrock quota is available. No action needed -- the agent self-recovers."
else
  echo "==> RESULT: still on the router standby. Re-run this after AWS grants the increase."
fi
