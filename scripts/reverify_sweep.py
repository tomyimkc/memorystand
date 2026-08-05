#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Scheduled entry point for trust decay: re-check standing that has gone stale.

Intended to run on a timer (Amazon EventBridge Scheduler already invokes /health every five
minutes; this is the same shape at a slower cadence). Deliberately a script rather than an HTTP
route: a sweep is an operator action with unbounded runtime, and putting it behind a public URL
would mean either a long-running request or a route that has to be authenticated and rate
limited for no benefit.

    python scripts/reverify_sweep.py --dry-run
    python scripts/reverify_sweep.py --tenant 9c8f6e5a-9d1a-4a1c-8f2e-3b6d1c7a4e10
    python scripts/reverify_sweep.py            # every tenant

Exit code is 0 even when memories are demoted -- a demotion is the system working, not a
failure. Non-zero only if the sweep itself could not run.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend import reverify  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tenant", help="limit the sweep to one tenant_id")
    ap.add_argument("--dry-run", action="store_true", help="report what would change, change nothing")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    out = reverify.sweep(args.tenant, dry_run=args.dry_run)

    if args.json:
        print(json.dumps(out, indent=2, default=str))
        return 0

    print(f"  stale window     : {out['stale_after_days']} days")
    print(f"  re-checked       : {out['checked']}")
    print(f"  still verified   : {out['still_verified']}")
    print(f"  -> attested      : {out['demoted_to_attested']}  (no longer independently checkable)")
    print(f"  -> disputed      : {out['demoted_to_disputed']}  (the system of record now disagrees)")
    print(f"  model calls      : {out['model_calls']}")
    if out["dry_run"]:
        print("  DRY RUN -- nothing was written")
    for change in out["changes"]:
        print(f"\n  {change['memory_id']}  {change['from']} -> {change['to']}")
        print(f"    {change['why'][:200]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
