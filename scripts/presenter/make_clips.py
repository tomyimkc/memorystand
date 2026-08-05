#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Step 2 of the presenter pipeline: animate each base frame into a lip-synced talking clip.

    base frame (png, on disk)
      -> base64 data URI
        -> xAI grok-imagine-video-1.5   (image + speech -> talking clip)
          -> poll until ready
            -> download to artifacts/presenter/clips/

NO PUBLIC INGRESS. The API accepts a ``data:`` URI for the input image, so nothing local is
ever exposed to the internet. An earlier version of this pipeline in another project stood up
a local file server behind a cloudflared tunnel to hand the model a public URL; that was never
necessary here and is not done.

CREDENTIALS. ``XAI_API_KEY`` from the environment, and nowhere else. The grok CLI on this
machine has its own credential store, and this script deliberately does not read it -- another
tool's secrets are not this script's to take.

    export XAI_API_KEY=xai-...
    python scripts/presenter/make_clips.py
    python scripts/presenter/make_clips.py --beat 03-measured    # regenerate one
    python scripts/presenter/make_clips.py --dry-run             # show what would be sent
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT_JSON = REPO_ROOT / "docs" / "demo" / "presenter-script.json"
BASE_DIR = REPO_ROOT / "artifacts" / "presenter" / "base"
CLIP_DIR = REPO_ROOT / "artifacts" / "presenter" / "clips"

API = "https://api.x.ai/v1/videos/generations"
MODEL = os.environ.get("MEMORYSTAND_VIDEO_MODEL", "grok-imagine-video-1.5")

POLL_INTERVAL_S = 5
POLL_TIMEOUT_S = 600


def _data_uri(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()


def _post(url: str, payload: dict, key: str) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"content-type": "application/json", "Authorization": f"Bearer {key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"xAI HTTP {exc.code}: {exc.read().decode()[:400]}") from exc


def _get(url: str, key: str) -> dict:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode())


def generate(beat: dict, key: str, *, dry_run: bool) -> Path | None:
    base = BASE_DIR / f"{beat['id']}.png"
    out = CLIP_DIR / f"{beat['id']}.mp4"
    if not base.is_file():
        print(f"    {beat['id']}: no base frame at {base} -- run make_base_frames.sh first")
        return None
    if out.is_file():
        print(f"    {beat['id']}: already generated, skipping")
        return out

    payload = {
        "model": MODEL,
        # The speech is what drives the lip sync; the image fixes who is speaking. Keeping the
        # spoken text identical to what the narration track will say is the entire point --
        # a clip whose mouth is saying something else is worse than no presenter at all.
        "prompt": beat["speech"],
        "image": _data_uri(base),
        "duration_seconds": beat.get("seconds", 15),
    }

    if dry_run:
        redacted = dict(payload)
        redacted["image"] = f"<data URI, {len(payload['image'])} chars>"
        print(f"    {beat['id']}: would POST {json.dumps(redacted)[:220]}...")
        return None

    print(f"==> {beat['id']}: submitting ({beat.get('seconds', 15)}s)")
    job = _post(API, payload, key)

    # The API may answer synchronously with a URL, or hand back a job to poll. Handle both
    # rather than assuming, because assuming one shape is how this breaks silently later.
    url = _extract_url(job)
    job_id = job.get("id") or job.get("request_id")
    started = time.monotonic()
    while url is None and job_id:
        if time.monotonic() - started > POLL_TIMEOUT_S:
            print(f"    {beat['id']}: still not ready after {POLL_TIMEOUT_S}s -- giving up")
            return None
        time.sleep(POLL_INTERVAL_S)
        job = _get(f"{API}/{job_id}", key)
        url = _extract_url(job)
        print(f"    polling... {job.get('status', '?')}")

    if not url:
        print(f"    {beat['id']}: no video URL in response: {json.dumps(job)[:300]}")
        return None

    CLIP_DIR.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=300) as resp:
        out.write_bytes(resp.read())
    print(f"    wrote {out} ({out.stat().st_size // 1024} KB)")
    return out


def _extract_url(job: dict) -> str | None:
    """Find the finished video URL wherever this response shape happens to put it."""
    for path in (("video", "url"), ("data", 0, "url"), ("url",), ("output", "url")):
        node = job
        try:
            for part in path:
                node = node[part]
            if isinstance(node, str) and node.startswith("http"):
                return node
        except (KeyError, IndexError, TypeError):
            continue
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--beat", help="only this beat id")
    ap.add_argument("--dry-run", action="store_true", help="show the request, send nothing")
    args = ap.parse_args()

    key = os.environ.get("XAI_API_KEY", "").strip()
    if not key and not args.dry_run:
        print(
            "XAI_API_KEY is not set.\n"
            "  export XAI_API_KEY=xai-...\n"
            "This script reads the key only from the environment -- it will not read the grok\n"
            "CLI's credential store, which belongs to another tool.",
            file=sys.stderr,
        )
        return 2

    beats = json.loads(SCRIPT_JSON.read_text())["beats"]
    if args.beat:
        beats = [b for b in beats if b["id"] == args.beat] or []
        if not beats:
            print(f"no beat with id {args.beat!r}", file=sys.stderr)
            return 2

    made = [generate(b, key, dry_run=args.dry_run) for b in beats]
    ok = [m for m in made if m]
    print(f"\n  {len(ok)}/{len(beats)} clip(s) ready in {CLIP_DIR}")
    return 0 if len(ok) == len(beats) or args.dry_run else 1


if __name__ == "__main__":
    raise SystemExit(main())
