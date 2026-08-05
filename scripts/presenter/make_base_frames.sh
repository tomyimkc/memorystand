#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Step 1 of the presenter pipeline: turn one reference portrait into six per-beat base frames.
#
# MEASURED, NOT ASSUMED: image_edit IGNORES aspect_ratio. Asking for 16:9 against a 912x1136
# reference returns 912x1136 -- it preserves the source aspect and crops nothing. The request
# is left in the prompt because it costs nothing and may start working, but the pipeline does
# not depend on it. The presenter stays a PORTRAIT clip and Remotion composes it onto one
# third of a 1920x1080 canvas with the data panels opposite, which is the layout that was
# wanted anyway.
#
#   reference portrait
#     -> [this script] base frames, 16:9, speaker on a third
#          -> xAI grok-imagine-video-1.5 (image -> lip-synced clip)
#               -> Remotion composition (presenter + live data panels)
#                    -> ffmpeg
#
# WHY image_edit AND NOT image_gen. Grok's own `imagine` guidance is explicit: never use pure
# image_gen for a real person -- always image_edit against a real reference. That is both the
# policy and the practical answer, because a generated-from-scratch face drifts between beats
# and the six clips stop looking like the same human. One reference in, six variations out.
#
# WHY THE FRAMING LANGUAGE IS STILL THERE. Even though the output is portrait, asking for the
# subject on a third and for negative space opposite changes where the head sits inside the
# frame, which is what lets Remotion crop toward the face without decapitating anyone. The
# generator still drifts between beats, so the composition measures each clip rather than
# trusting the brief.
#
# NOTE ON PERMISSIONS: this deliberately does NOT pass --permission-mode bypassPermissions.
# An earlier version of this pipeline in another project did, along with exposing a local file
# server through a cloudflared tunnel. Neither was ever authorised here, and neither is needed:
# grok is already authenticated, and the video step takes a base64 data URI rather than a
# public URL.
#
#   ./scripts/presenter/make_base_frames.sh [reference.png] [outdir]
set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." >/dev/null 2>&1 && pwd)"
REF="${1:-/private/tmp/armledger-video-20260804/gen/presenter-reference.png}"
OUT="${2:-$REPO_ROOT/artifacts/presenter/base}"
SCRIPT_JSON="$REPO_ROOT/docs/demo/presenter-script.json"

command -v grok >/dev/null 2>&1 || { echo "ERROR: grok CLI not found on PATH" >&2; exit 1; }
[[ -f "$REF" ]] || { echo "ERROR: reference portrait not found: $REF" >&2; exit 1; }
[[ -f "$SCRIPT_JSON" ]] || { echo "ERROR: missing $SCRIPT_JSON" >&2; exit 1; }

mkdir -p "$OUT"
echo "==> reference: $REF"
echo "==> output:    $OUT"

# One shared wardrobe/lighting/setting clause across every beat. Consistency across cuts is
# the difference between "one person presenting" and "six similar people", and the model will
# happily change a shirt between calls if not told otherwise.
CONSTANT="The same man from the reference photograph, same face, same dark shirt, in a quiet \
modern office with soft even lighting and a deep blue-black background that stays clean and \
uncluttered. Photographic, natural skin texture, shallow depth of field, no text anywhere in \
the image."

beats=$(python3 -c "
import json
d=json.load(open('$SCRIPT_JSON'))
for b in d['beats']:
    print(b['id'], b['presenterSide'])
")

while read -r beat side; do
  out="$OUT/${beat}.png"
  if [[ -f "$out" ]]; then
    echo "    $beat -- already generated, skipping"
    continue
  fi

  if [[ "$side" == "LEFT" ]]; then
    placement="Compose him toward the LEFT of the frame, facing slightly right toward the \
viewer, with clean uncluttered space to his right."
  else
    placement="Compose him toward the RIGHT of the frame, facing slightly left toward the \
viewer, with clean uncluttered space to his left."
  fi

  echo "==> $beat ($side)"
  # The prompt tells grok to make exactly one tool call and write exactly one file. Left to its
  # own devices it will happily produce several variations and a contact sheet.
  grok -p "Use the real-person reference image at: $REF

Call the image_edit tool exactly once. Pass that reference image and set aspect_ratio to 16:9.

$CONSTANT $placement He is mid-sentence, speaking to camera, relaxed and credible.

After image_edit returns, save the resulting edited image to exactly this path: $out
Do not create any other assets. If image_edit is unavailable or the request is blocked, print
the exact error text and stop." 2>&1 | tail -3

  [[ -f "$out" ]] && echo "    wrote $out" || echo "    FAILED: $out was not written"
done <<< "$beats"

echo
echo "==> base frames in $OUT"
ls -1 "$OUT" 2>/dev/null | sed 's/^/    /'
