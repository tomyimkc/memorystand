#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Generate Grok presenter clips when xAI Zero Data Retention blocks the CLI.

``make_clips.py`` is the preferred path because it drives Grok CLI's ``image_to_video`` tool
directly. xAI teams with Zero Data Retention enabled receive a hard API error on that path:

    Zero Data Retention teams must provide output.upload_url for video generation.

The CLI tool surface cannot supply that field. This fallback keeps the same model, prompt,
base frames, continuity seeding, and verification contract, but calls the xAI REST endpoint
and provides the required destination. A short-lived Cloudflare quick tunnel exposes exactly
two token-protected routes per active shot:

* GET the current base frame.
* PUT the generated MP4.

There is no directory listing, the random token is never printed, and the tunnel closes when
the process exits. The local clip is still untrusted output: run ``make_clips.py --verify-only``
afterward so every line is transcribed and compared before ``compose.py`` will use it.

Authentication prefers ``XAI_API_KEY``. If it is absent, the script reuses the current Grok
CLI OAuth session from ``~/.grok/auth.json`` without printing or persisting its bearer token.

    python scripts/presenter/xai_video.py --list
    python scripts/presenter/xai_video.py
    python scripts/presenter/xai_video.py --beat 05-cloudwatch-receipt
"""

from __future__ import annotations

import argparse
import http.server
import json
import os
import re
import secrets
import select
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT_JSON = REPO_ROOT / "docs" / "demo" / "presenter-script.json"
BASE_DIR = REPO_ROOT / "artifacts" / "presenter" / "base"
CLIP_DIR = REPO_ROOT / "artifacts" / "presenter" / "clips"
RECEIPT = REPO_ROOT / "docs" / "demo" / "presenter-verification.json"

API_ROOT = os.environ.get("XAI_API_BASE", "https://api.x.ai/v1")
MODEL = os.environ.get("XAI_VIDEO_MODEL", "grok-imagine-video-1.5")
DURATION_S = 10
POLL_INTERVAL_S = 5.0
POLL_TIMEOUT_S = 900.0
MIN_CLIP_BYTES = 50_000


def _api_key() -> str:
    key = os.environ.get("XAI_API_KEY", "").strip()
    if key:
        return key

    auth_path = Path.home() / ".grok" / "auth.json"
    if not auth_path.is_file():
        raise SystemExit(
            "No XAI_API_KEY and no active ~/.grok/auth.json session. "
            "Run `grok login` or export XAI_API_KEY."
        )
    records = json.loads(auth_path.read_text())
    candidates = [
        value.get("key", "").strip()
        for value in records.values()
        if isinstance(value, dict) and value.get("key", "").strip()
    ]
    if len(candidates) != 1:
        raise SystemExit(
            f"Expected exactly one authenticated Grok session, found {len(candidates)}. "
            "Export XAI_API_KEY to choose explicitly."
        )
    print("  authentication: active Grok CLI session")
    return candidates[0]


def _request(method: str, path: str, key: str, payload: dict | None = None) -> dict:
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {"authorization": f"Bearer {key}"}
    if body is not None:
        headers["content-type"] = "application/json"
    request = urllib.request.Request(
        f"{API_ROOT}{path}",
        data=body,
        method=method,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:800]
        raise RuntimeError(f"{method} {path} -> HTTP {exc.code}: {detail}") from exc


class UploadBridge:
    """One localhost server and one random quick tunnel for the generation batch."""

    def __init__(self) -> None:
        self.token = secrets.token_urlsafe(32)
        self.inputs: dict[str, Path] = {}
        self.outputs: dict[str, Path] = {}
        self._server: http.server.ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._cloudflared: subprocess.Popen[str] | None = None
        self.base_url = ""

    def start(self) -> None:
        bridge = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def _authorised_path(self) -> str | None:
                parsed = urllib.parse.urlparse(self.path)
                token = urllib.parse.parse_qs(parsed.query).get("token", [""])[0]
                if not secrets.compare_digest(token, bridge.token):
                    self.send_error(403)
                    return None
                return parsed.path

            def do_GET(self) -> None:  # noqa: N802
                route = self._authorised_path()
                source = bridge.inputs.get(route or "")
                if source is None or not source.is_file():
                    self.send_error(404)
                    return
                size = source.stat().st_size
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(size))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                with source.open("rb") as handle:
                    shutil.copyfileobj(handle, self.wfile)

            def do_PUT(self) -> None:  # noqa: N802
                route = self._authorised_path()
                destination = bridge.outputs.get(route or "")
                if destination is None:
                    self.send_error(404)
                    return
                try:
                    length = int(self.headers.get("Content-Length", ""))
                except ValueError:
                    length = 0
                if length < MIN_CLIP_BYTES:
                    self.send_error(400, "missing or implausibly small Content-Length")
                    return

                destination.parent.mkdir(parents=True, exist_ok=True)
                partial = destination.with_suffix(destination.suffix + ".uploading")
                remaining = length
                with partial.open("wb") as handle:
                    while remaining:
                        chunk = self.rfile.read(min(1024 * 1024, remaining))
                        if not chunk:
                            partial.unlink(missing_ok=True)
                            self.send_error(400, "upload ended early")
                            return
                        handle.write(chunk)
                        remaining -= len(chunk)
                partial.replace(destination)
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, _format: str, *_args: object) -> None:
                return

        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

        cloudflared = shutil.which("cloudflared")
        if not cloudflared:
            raise SystemExit(
                "cloudflared is required for the ZDR upload bridge but was not found on PATH."
            )
        self._cloudflared = subprocess.Popen(
            [
                cloudflared,
                "tunnel",
                "--url",
                f"http://127.0.0.1:{port}",
                "--no-autoupdate",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert self._cloudflared.stdout is not None
        deadline = time.time() + 45
        recent: deque[str] = deque(maxlen=12)
        pattern = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
        while time.time() < deadline:
            if self._cloudflared.poll() is not None:
                break
            ready, _, _ = select.select([self._cloudflared.stdout], [], [], 1.0)
            if not ready:
                continue
            line = self._cloudflared.stdout.readline().strip()
            if line:
                recent.append(line)
            match = pattern.search(line)
            if match:
                self.base_url = match.group(0)
                print("  ZDR upload bridge: ready (short-lived, token-protected)")
                return

        detail = "\n".join(recent)
        self.close()
        raise SystemExit(f"cloudflared quick tunnel did not become ready:\n{detail}")

    def prepare(self, tag: str, source: Path, destination: Path) -> tuple[str, str]:
        safe = urllib.parse.quote(tag, safe="-")
        input_route = f"/input/{safe}.png"
        output_route = f"/output/{safe}.mp4"
        self.inputs[input_route] = source
        self.outputs[output_route] = destination
        token = urllib.parse.quote(self.token, safe="")
        return (
            f"{self.base_url}{input_route}?token={token}",
            f"{self.base_url}{output_route}?token={token}",
        )

    def close(self) -> None:
        if self._cloudflared is not None and self._cloudflared.poll() is None:
            self._cloudflared.terminate()
            try:
                self._cloudflared.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self._cloudflared.kill()
                self._cloudflared.wait(timeout=5)
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=3)

    def __enter__(self) -> "UploadBridge":
        self.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def _last_frame(clip: Path, destination: Path) -> Path:
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-sseof",
            "-0.05",
            "-i",
            str(clip),
            "-update",
            "1",
            "-frames:v",
            "1",
            "-y",
            str(destination),
        ],
        check=True,
    )
    return destination


def generate(
    tag: str,
    line: str,
    source: Path,
    destination: Path,
    bridge: UploadBridge,
    key: str,
) -> bool:
    destination.unlink(missing_ok=True)
    image_url, upload_url = bridge.prepare(tag, source, destination)
    prompt = (
        f'The man speaks these exact words to camera, and says nothing else: "{line}" '
        "Natural lip sync to those words, subtle head motion and blinking, relaxed and "
        "credible. The camera holds still and the background stays unchanged."
    )
    try:
        started = _request(
            "POST",
            "/videos/generations",
            key,
            {
                "model": MODEL,
                "prompt": prompt,
                "image": {"url": image_url},
                "duration": DURATION_S,
                "output": {"upload_url": upload_url},
            },
        )
    except RuntimeError as exc:
        print(f"    {exc}")
        return False

    request_id = started.get("request_id") or started.get("id")
    if not request_id:
        print(f"    no request id in response: {json.dumps(started)[:300]}")
        return False

    deadline = time.time() + POLL_TIMEOUT_S
    last_status = "pending"
    while time.time() < deadline:
        time.sleep(POLL_INTERVAL_S)
        try:
            state = _request("GET", f"/videos/{request_id}", key)
        except RuntimeError as exc:
            print(f"    {exc}")
            return False
        last_status = str(state.get("status", "")).lower()
        if last_status in {"done", "succeeded", "completed"}:
            break
        if last_status in {"failed", "expired", "error"}:
            video = state.get("video") or {}
            if video.get("respect_moderation") is False:
                print("    generation refused by content moderation")
            print(f"    generation {last_status}: {json.dumps(state)[:500]}")
            return False
    else:
        print(f"    timed out after {POLL_TIMEOUT_S:.0f}s; last status {last_status!r}")
        return False

    upload_deadline = time.time() + 45
    while time.time() < upload_deadline:
        if destination.is_file() and destination.stat().st_size >= MIN_CLIP_BYTES:
            return True
        time.sleep(0.5)
    print(
        f"    xAI reported {last_status}, but no plausible MP4 reached the upload bridge"
    )
    return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--beat", help="generate only this beat id")
    parser.add_argument("--list", action="store_true", help="list missing clips and exit")
    parser.add_argument("--force", action="store_true", help="regenerate clips even if present")
    args = parser.parse_args()

    spec = json.loads(SCRIPT_JSON.read_text())
    beats = spec["beats"]
    if args.beat:
        beats = [beat for beat in beats if beat["id"] == args.beat]
        if not beats:
            print(f"no beat with id {args.beat!r}", file=sys.stderr)
            return 2

    receipt = json.loads(RECEIPT.read_text()).get("shots", {}) if RECEIPT.is_file() else {}
    todo: list[tuple[dict, int, str, str, Path]] = []
    for beat in beats:
        for index, line in enumerate(beat["shots"]):
            tag = f"{beat['id']}-{index}"
            clip = CLIP_DIR / f"{tag}.mp4"
            has_clip = clip.is_file() and clip.stat().st_size >= MIN_CLIP_BYTES
            recorded_line = receipt.get(tag, {}).get("asked", "").strip()
            stale_by_receipt = bool(recorded_line) and recorded_line != line.strip()
            # A fresh clip does not have a transcript receipt until make_clips.py --verify-only
            # runs, so absence from the receipt is not a reason to pay to generate it twice.
            if args.force or not has_clip or stale_by_receipt:
                todo.append((beat, index, tag, line, clip))

    print(f"  {len(todo)} clip(s) to generate with {MODEL}")
    for _, _, tag, line, _ in todo:
        print(f"    {tag:32s} {len(line.split())} words")
    if args.list or not todo:
        return 0

    key = _api_key()
    CLIP_DIR.mkdir(parents=True, exist_ok=True)
    completed = 0
    with UploadBridge() as bridge:
        for beat, index, tag, line, clip in todo:
            source = BASE_DIR / f"{beat.get('baseFrom', beat['id'])}.png"
            if index:
                previous = CLIP_DIR / f"{beat['id']}-{index - 1}.mp4"
                if previous.is_file():
                    source = _last_frame(
                        previous,
                        CLIP_DIR / f"{beat['id']}-{index - 1}-last.png",
                    )
            if not source.is_file():
                print(f"  {tag}: missing source frame {source}")
                continue
            print(f"==> {tag}: generating from {source.name}")
            if generate(tag, line, source, clip, bridge, key):
                completed += 1
                print(f"    wrote {clip.name} ({clip.stat().st_size // 1000} kB)")
            else:
                print(f"    FAILED: {clip.name}")

    print(f"\n  {completed}/{len(todo)} clip(s) generated")
    print("  Next: python scripts/presenter/make_clips.py --verify-only")
    return 0 if completed == len(todo) else 1


if __name__ == "__main__":
    raise SystemExit(main())
