#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Build the static Hugging Face Space bundle from the canonical frontend.

The Space must not become a second, drifting implementation of the demo. This
script copies the exact frontend that Amplify serves, adds the Hugging Face
static-SDK README metadata, and fails if the two required browser assets are
missing.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"

SPACE_README = """\
---
title: MemoryStand
emoji: 🧾
colorFrom: blue
colorTo: green
sdk: static
pinned: false
license: apache-2.0
short_description: Memory for AI agents that must prove it worked.
---

# MemoryStand

Judge-facing static demo for the CockroachDB AI Hackathon.

The page calls the deployed MemoryStand API from the browser. It contains no
operator credentials or private keys. The API intentionally publishes a
tenant-scoped public demo credential from `/health`; the server refuses that
credential for every other tenant.

Source: https://github.com/tomyimkc/memorystand
"""


def build(out: Path) -> None:
    required = [FRONTEND / "index.html", FRONTEND / "app.js"]
    missing = [str(path.relative_to(ROOT)) for path in required if not path.is_file()]
    if missing:
        raise SystemExit(f"cannot build Hugging Face Space; missing: {', '.join(missing)}")

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for source in required:
        shutil.copy2(source, out / source.name)
    (out / "README.md").write_text(SPACE_README, encoding="utf-8")

    print(f"built static Space bundle: {out}")
    for path in sorted(out.iterdir()):
        print(f"  {path.name}: {path.stat().st_size} bytes")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "dist" / "hf-space",
        help="output directory (default: dist/hf-space)",
    )
    args = parser.parse_args()
    build(args.out.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
