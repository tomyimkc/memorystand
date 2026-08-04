# SPDX-License-Identifier: Apache-2.0
"""standing -- the command line a judge types on camera.

This module is the ONLY place CLI concerns (argument parsing, output
formatting, plain-English framing) live. It contains no memory, decision,
trust, or replay logic of its own -- every one of those is imported from
`backend`, which is a frozen contract this file must never redefine:

    backend.db        get_conn(), retry_serializable()
    backend.memory     remember(), recall()
    backend.decisions  decide()
    backend.trust      grant_standing()
    backend.replay     recall_as_of(), belief_diff()

If `backend` is not importable, every subcommand fails with a plain,
honest message instead of a raw traceback -- this checkout may simply
not have the backend package built yet.

Design notes for the demo:
  - Six subcommands: remember, recall, decide, confirm, cross-examine,
    audit. Every one accepts --json for machine-readable output.
  - `tenant_id` / `agent_id` are cross-cutting, like `--dsn`, so they are
    NOT per-subcommand flags. They default to stable, deterministic demo
    IDs (see DEFAULT_TENANT_ID / DEFAULT_AGENT_ID) so the six commands
    below work with zero setup; --tenant-id / --agent-id or the
    MEMORYSTAND_TENANT_ID / MEMORYSTAND_AGENT_ID env vars override them for a
    real multi-tenant run.
  - House style bans three words in USER-FACING TEXT (they are already
    load-bearing terms in competing papers): "quarantine", "supersede",
    "belief state". The Python API and schema still use that vocabulary
    internally -- only what prints to the terminal is reworded, into the
    on-call English this project's pitch promises: "held for review",
    "escalated", "closed the loop".
  - Colour is applied only when stdout is a TTY and NO_COLOR is unset, so
    piped/recorded output and CI logs stay plain.
"""

from __future__ import annotations

import argparse
import datetime
import decimal
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

# `python cli/memorystand.py` puts cli/ on sys.path[0], not the repo root, so `import
# backend` fails with "No module named 'backend'". Same footgun as db/seed/seed.py.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# ---------------------------------------------------------------------------
# Demo identity. Deterministic (uuid5), so `remember` and `recall` agree on
# who "the agent" is across separate process invocations with zero flags --
# the point of a CLI a judge can type without first minting UUIDs on camera.
# ---------------------------------------------------------------------------
# Must match db/seed/seed.py's DEFAULT_TENANT_ID. If these drift, the first command
# a new reader types after seeding ("memorystand recall ...") returns nothing at all,
# which reads as "the product is broken" rather than "you are looking at an empty tenant".
DEFAULT_TENANT_ID = uuid.UUID("9c8f6e5a-9d1a-4a1c-8f2e-3b6d1c7a4e10")
DEFAULT_AGENT_ID = uuid.UUID("1a2b3c4d-5e6f-4708-9a0b-1c2d3e4f5061")

DSN_ENV = "COCKROACH_DSN"
TENANT_ENV = "MEMORYSTAND_TENANT_ID"
AGENT_ENV = "MEMORYSTAND_AGENT_ID"

VERDICT_LABELS = {"accepted": "accepted", "quarantined": "held for review"}
OUTCOME_LABELS = {
    "success": "success",
    "rollback": "rolled back",
    "false_positive": "false positive",
}

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_BACKEND_MISSING = 3
EXIT_RUNTIME = 4


# ---------------------------------------------------------------------------
# Colour / rendering helpers -- degrade to plain text off a TTY.
# ---------------------------------------------------------------------------
_ANSI = {
    "green": "\033[32m",
    "yellow": "\033[33m",
    "red": "\033[31m",
    "cyan": "\033[36m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "reset": "\033[0m",
}


def _use_color() -> bool:
    return sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def c(text: str, *styles: str) -> str:
    """Wrap text in ANSI styles; a no-op when not a TTY or NO_COLOR is set."""
    if not styles or not _use_color():
        return text
    prefix = "".join(_ANSI[s] for s in styles)
    return f"{prefix}{text}{_ANSI['reset']}"


def short(value: Any) -> str:
    """First 8 chars of a UUID-shaped id, for a screen that fits at 1080p."""
    if value is None:
        return "-"
    return str(value)[:8]


def _json_default(obj: Any) -> Any:
    if isinstance(obj, uuid.UUID):
        return str(obj)
    if isinstance(obj, (datetime.datetime, datetime.date)):
        return obj.isoformat()
    if isinstance(obj, decimal.Decimal):
        return float(obj)
    raise TypeError(f"not JSON serialisable: {type(obj).__name__}")


def print_json(payload: Any) -> None:
    print(json.dumps(payload, indent=2, default=_json_default, sort_keys=True))


def print_table(headers: list[str], rows: list[list[str]]) -> None:
    """Aligned, dependency-free column printer -- readable on a recording."""
    if not rows:
        print(c("  (nothing to show)", "dim"))
        return
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(c(fmt.format(*headers), "bold"))
    print(c(fmt.format(*["-" * w for w in widths]), "dim"))
    for row in rows:
        print(fmt.format(*row))


def banner(text: str, style: str = "bold") -> None:
    print(c(text, style))


# ---------------------------------------------------------------------------
# backend import guard -- the whole point of "import it, never redefine it".
# Every subcommand imports its backend module lazily (inside the handler)
# so `standing --help` and argument-parsing errors work even in a checkout
# that has no backend/ package yet.
# ---------------------------------------------------------------------------
class BackendUnavailable(RuntimeError):
    def __init__(self, module: str, exc: Exception) -> None:
        self.module = module
        self.exc = exc
        super().__init__(str(exc))


def _import_backend(module: str):
    try:
        return __import__(f"backend.{module}", fromlist=[module])
    except ImportError as exc:
        raise BackendUnavailable(module, exc) from exc


def _handle_backend_unavailable(err: BackendUnavailable, args: argparse.Namespace) -> int:
    if getattr(args, "json", False):
        print_json(
            {"error": "backend_unavailable", "module": f"backend.{err.module}", "detail": str(err.exc)}
        )
        return EXIT_BACKEND_MISSING
    print(c(f"error: backend.{err.module} is not importable in this checkout.", "red", "bold"))
    print(f"       ({err.exc})")
    print()
    print("Run this from the repository root, or install the package:")
    print("    pip install -r requirements.txt && python cli/memorystand.py ...")
    print("and make sure COCKROACH_DSN (or MEMORYSTAND_DSN) points at your cluster.")
    return EXIT_BACKEND_MISSING


# ---------------------------------------------------------------------------
# Shared setup
# ---------------------------------------------------------------------------
def _resolve_ids(args: argparse.Namespace) -> tuple[str, str]:
    """Return (tenant_id, agent_id) as plain strings -- every backend function is
    typed ``tenant_id: str`` / ``agent_id: str``, and psycopg2 has no adapter for
    Python's uuid.UUID registered, so a UUID object here fails at the SQL layer.
    Validated by round-tripping through uuid.UUID() before stringifying, so a
    malformed --tenant-id / --agent-id fails fast with a clear error.
    """
    tenant = args.tenant_id or os.environ.get(TENANT_ENV) or str(DEFAULT_TENANT_ID)
    agent = args.agent_id or os.environ.get(AGENT_ENV) or str(DEFAULT_AGENT_ID)
    return str(uuid.UUID(str(tenant))), str(uuid.UUID(str(agent)))


def _apply_dsn(args: argparse.Namespace) -> None:
    """backend.db.get_conn() takes no arguments; it reads COCKROACH_DSN.

    A --dsn flag has to land in the environment before backend.db creates
    its connection pool, so this runs first, before any backend import.
    """
    if args.dsn:
        os.environ[DSN_ENV] = args.dsn


def _identity_line(tenant_id: str, agent_id: str) -> str:
    return c(f"tenant {short(tenant_id)} - agent {short(agent_id)}", "dim")


# ---------------------------------------------------------------------------
# remember
# ---------------------------------------------------------------------------
def cmd_remember(args: argparse.Namespace) -> int:
    tenant_id, agent_id = _resolve_ids(args)
    try:
        memory = _import_backend("memory")
    except BackendUnavailable as err:
        return _handle_backend_unavailable(err, args)

    try:
        result = memory.remember(
            tenant_id,
            agent_id,
            args.content,
            entity=args.entity,
            attribute_key=args.key,
            attribute_value=args.value,
            memory_type=args.type,
            source=args.source,
            task_id=args.task_id,
        )
    except Exception as exc:  # noqa: BLE001 - render, don't crash, on camera
        print(c(f"error: remember failed: {exc}", "red"))
        return EXIT_RUNTIME

    if args.json:
        print_json(result)
        return EXIT_OK

    print(_identity_line(tenant_id, agent_id))
    verdict = result.get("verdict", "?")
    label = VERDICT_LABELS.get(verdict, verdict)
    style = "green" if verdict == "accepted" else "yellow"
    print(f"{c(label, style, 'bold')}  memory {short(result.get('memory_id'))}")

    if verdict != "accepted":
        reasons = result.get("verdict_reasons") or []
        if reasons:
            print(c("  held for review because:", "yellow"))
            for reason in reasons:
                print(f"    - {reason}")
        checked = result.get("checked_against") or []
        if checked:
            print("  checked against:")
            for mid in checked:
                print(f"    - {short(mid)}")
        else:
            print(c("  checked against: nothing on file yet for this tenant", "dim"))

    superseded = result.get("superseded")
    if superseded:
        target = short(superseded) if not isinstance(superseded, bool) else ""
        print(f"  {c('replaces the earlier memory' + (f' {target}' if target else ''), 'cyan')}")

    return EXIT_OK


# ---------------------------------------------------------------------------
# recall
# ---------------------------------------------------------------------------
def cmd_recall(args: argparse.Namespace) -> int:
    tenant_id, agent_id = _resolve_ids(args)
    try:
        memory = _import_backend("memory")
    except BackendUnavailable as err:
        return _handle_backend_unavailable(err, args)

    try:
        results = memory.recall(tenant_id, agent_id, args.query, k=args.k)
    except Exception as exc:  # noqa: BLE001
        print(c(f"error: recall failed: {exc}", "red"))
        return EXIT_RUNTIME

    if args.json:
        print_json(results)
        return EXIT_OK

    print(_identity_line(tenant_id, agent_id))
    print(f"query: {args.query!r}")
    if not results:
        print(c("  no accepted memories match yet", "dim"))
        return EXIT_OK

    rows = []
    for i, row in enumerate(results, start=1):
        trust = row.get("trust_tier", "?")
        trust_style = {"verified": "green", "disputed": "red"}.get(trust, "yellow")
        attr = ""
        if row.get("entity") or row.get("attribute_key"):
            attr = f"{row.get('entity') or '-'}.{row.get('attribute_key') or '-'}={row.get('attribute_value') or '-'}"
        content = (row.get("content") or "")[:60]
        rows.append(
            [
                str(i),
                short(row.get("memory_id")),
                c(trust, trust_style),
                f"{row.get('distance', 0):.4f}",
                attr,
                content,
            ]
        )
    print_table(["#", "id", "trust", "distance", "attribute", "content"], rows)
    return EXIT_OK


# ---------------------------------------------------------------------------
# decide
# ---------------------------------------------------------------------------
def cmd_decide(args: argparse.Namespace) -> int:
    tenant_id, agent_id = _resolve_ids(args)
    try:
        memory = _import_backend("memory")
        decisions = _import_backend("decisions")
    except BackendUnavailable as err:
        return _handle_backend_unavailable(err, args)

    try:
        consulted = memory.recall(tenant_id, agent_id, args.query, k=args.k)
        consulted_ids = [row["memory_id"] for row in consulted]
        produced_ids = tuple(x.strip() for x in args.produced.split(",") if x.strip()) if args.produced else ()
        result = decisions.decide(
            tenant_id,
            agent_id,
            args.action,
            args.rationale,
            consulted_ids,
            produced_memory_ids=produced_ids,
            requires_approval=args.requires_approval,
            task_id=args.task_id,
        )
    except Exception as exc:  # noqa: BLE001
        print(c(f"error: decide failed: {exc}", "red"))
        return EXIT_RUNTIME

    if args.json:
        print_json({**result, "consulted": consulted})
        return EXIT_OK

    print(_identity_line(tenant_id, agent_id))
    print(f"{c('decision', 'bold')} {short(result.get('decision_id'))}")
    print(f"  action:    {args.action}")
    print(f"  rationale: {args.rationale}")
    print(f"  consulted: {len(consulted_ids)} memory(ies) from recall({args.query!r})")
    for row in consulted:
        print(f"    - {short(row['memory_id'])}  [{row.get('trust_tier', '?')}]  {(row.get('content') or '')[:50]}")
    if result.get("status") == "held_for_approval":
        print(c("  status: escalated - held for a human sign-off before it runs", "yellow"))
    else:
        print(c("  status: cleared to proceed", "green"))
    return EXIT_OK


# ---------------------------------------------------------------------------
# confirm
# ---------------------------------------------------------------------------
def cmd_confirm(args: argparse.Namespace) -> int:
    try:
        trust = _import_backend("trust")
    except BackendUnavailable as err:
        return _handle_backend_unavailable(err, args)

    evidence = {
        "source": args.source,
        "outcome": args.outcome,
        "metric_delta": args.metric_delta,
        "external_ref": args.ref,
    }
    # Tenant-scoped: granting standing now requires saying whose decision it is, so a
    # decision id on its own is no longer sufficient to promote a memory.
    tenant_id, _ = _resolve_ids(args)
    try:
        result = trust.grant_standing(tenant_id, args.decision_id, evidence)
    except Exception as exc:  # noqa: BLE001
        print(c(f"error: confirm failed: {exc}", "red"))
        return EXIT_RUNTIME

    if args.json:
        print_json(result)
        return EXIT_OK

    outcome = result.get("outcome", args.outcome)
    outcome_label = OUTCOME_LABELS.get(outcome, outcome)
    outcome_style = "green" if outcome == "success" else "red"
    promoted = result.get("promoted") or []
    demoted = result.get("demoted") or []
    model_calls = result.get("model_calls", 0)

    banner(f"closed the loop on decision {short(result.get('decision_id') or args.decision_id)}")
    print(f"  outcome: {c(outcome_label, outcome_style)}  (source: {args.source}, ref: {args.ref})")
    print(f"  confirmed: {len(promoted)} memory(ies)")
    for mid in promoted:
        print(f"    - {short(mid)}")
    print(f"  disputed:  {len(demoted)} memory(ies)")
    for mid in demoted:
        print(f"    - {short(mid)}")
    print()
    print(c(f"  model calls used to decide this: {model_calls}", "bold", "green" if model_calls == 0 else "red"))
    return EXIT_OK


# ---------------------------------------------------------------------------
# cross-examine -- the headline command.
#
# backend.replay.cross_examine(tenant_id, decision_id) already does the whole
# job: looks up the decision, pins a transaction to its decided_at instant
# with BEGIN/SET TRANSACTION AS OF SYSTEM TIME, and diffs that snapshot
# against the live one. There is nothing left for this CLI to reimplement --
# it renders the result and translates replay.GCWindowExceeded into on-call
# English instead of a raw SQLSTATE.
# ---------------------------------------------------------------------------
def cmd_cross_examine(args: argparse.Namespace) -> int:
    tenant_id, _agent_id = _resolve_ids(args)
    try:
        replay = _import_backend("replay")
    except BackendUnavailable as err:
        return _handle_backend_unavailable(err, args)

    try:
        result = replay.cross_examine(tenant_id, args.decision_id)
    except replay.GCWindowExceeded as exc:
        if args.json:
            print_json({"error": "gc_window_exceeded", "detail": str(exc)})
        else:
            print(c("error: can't reach that far back.", "red", "bold"))
            print(f"       {exc}")
        return EXIT_RUNTIME
    except ValueError as exc:  # "no such decision: ..."
        if args.json:
            print_json({"error": "not_found", "detail": str(exc)})
        else:
            print(c(f"error: {exc}", "red"))
        return EXIT_RUNTIME
    except Exception as exc:  # noqa: BLE001
        print(c(f"error: cross-examine failed: {exc}", "red"))
        return EXIT_RUNTIME

    if args.json:
        print_json(result)
        return EXIT_OK

    decision = result["decision"]
    print(f"decision {short(decision['decision_id'])}  action={decision['action']!r}  at {decision['decided_at']}")
    print(f"rationale: {decision.get('rationale')}")
    print(f"outcome so far: {decision.get('outcome') or 'not yet confirmed'}")
    print()
    print("What the agent believed then, compared with what it believes now:")
    changes = result.get("changed_since") or []
    if not changes:
        print(c("  nothing changed since that instant", "dim"))
        return EXIT_OK

    rows = []
    for row in changes:
        delta = row.get("delta", "?")
        style = {"added": "green", "removed": "red", "changed": "yellow"}.get(delta, "cyan")
        attr = f"{row.get('entity') or '-'}.{row.get('attribute_key') or '-'}"
        rows.append(
            [
                c(delta, style),
                short(row.get("memory_id")),
                attr,
                str(row.get("attribute_value") or row.get("content") or "")[:50],
            ]
        )
    print_table(["change", "id", "attribute", "value now"], rows)
    return EXIT_OK


# ---------------------------------------------------------------------------
# audit
# ---------------------------------------------------------------------------
def cmd_audit(args: argparse.Namespace) -> int:
    tenant_id, _agent_id = _resolve_ids(args)
    try:
        audit = _import_backend("audit")
    except BackendUnavailable as err:
        return _handle_backend_unavailable(err, args)

    try:
        rows = audit.trail(tenant_id, decision_id=args.decision_id)
    except Exception as exc:  # noqa: BLE001
        print(c(f"error: audit lookup failed: {exc}", "red"))
        return EXIT_RUNTIME

    if args.json:
        print_json(rows)
        return EXIT_OK

    print(f"audit trail for decision {short(args.decision_id)}")
    if not rows:
        print(c("  no audited tool calls recorded for this decision yet", "dim"))
        return EXIT_OK

    table_rows = []
    for row in rows:
        risk = row.get("risk", "low")
        result_kind = row.get("result_kind") or "-"
        risk_style = {"high": "red", "medium": "yellow"}.get(risk, "green")
        result_style = {"denied": "red", "held": "yellow"}.get(result_kind, "green")
        table_rows.append(
            [
                str(row.get("ts")),
                row.get("actor", "-"),
                f"{row.get('tool_name', '-')} ({row.get('tool_kind', '-')})",
                c(risk, risk_style),
                c(result_kind, result_style),
            ]
        )
    print_table(["time", "actor", "tool", "risk", "result"], table_rows)
    return EXIT_OK


# ---------------------------------------------------------------------------
# argument parser
# ---------------------------------------------------------------------------
def _build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--dsn", default=argparse.SUPPRESS, help=f"CockroachDB DSN (default: ${DSN_ENV})")
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="print machine-readable JSON instead of a table")
    common.add_argument(
        "--tenant-id", default=argparse.SUPPRESS, help=f"tenant UUID (default: ${TENANT_ENV}, else a fixed demo tenant)"
    )
    common.add_argument(
        "--agent-id", default=argparse.SUPPRESS, help=f"agent UUID (default: ${AGENT_ENV}, else a fixed demo agent)"
    )

    parser = argparse.ArgumentParser(
        prog="standing",
        description="MemoryStand -- a memory layer for on-call agents that only trusts what actually worked.",
        parents=[common],
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_remember = sub.add_parser(
        "remember", parents=[common], help="write a memory; prints the admission verdict"
    )
    p_remember.add_argument("--entity", default=None, help="e.g. payments-service")
    p_remember.add_argument("--key", dest="key", default=None, help="attribute key, e.g. reads_from_table")
    p_remember.add_argument("--value", default=None, help="attribute value, e.g. orders_v2")
    p_remember.add_argument("--content", required=True, help="the text that gets embedded and recalled")
    p_remember.add_argument("--source", default=None, help="e.g. pagerduty_webhook, runbook:db-failover, human:alice")
    p_remember.add_argument(
        "--type",
        dest="type",
        default="semantic",
        choices=["episodic", "semantic", "task_state", "tool_call"],
        help="memory_type (default: semantic)",
    )
    p_remember.add_argument("--task-id", default=None, help="correlates this memory to one incident/run")
    p_remember.set_defaults(func=cmd_remember)

    p_recall = sub.add_parser("recall", parents=[common], help="vector-search accepted memories")
    p_recall.add_argument("--query", required=True)
    p_recall.add_argument("-k", type=int, default=5, help="number of results (default: 5)")
    p_recall.set_defaults(func=cmd_recall)

    p_decide = sub.add_parser(
        "decide", parents=[common], help="recall, then record what the agent decided to do"
    )
    p_decide.add_argument("--action", required=True, help="e.g. page_oncall, restart_service, reply")
    p_decide.add_argument("--rationale", required=True)
    p_decide.add_argument("--query", required=True, help="recall query used to gather what was consulted")
    p_decide.add_argument("-k", type=int, default=5, help="how many memories to consult (default: 5)")
    p_decide.add_argument(
        "--produced", default=None, help="comma-separated memory ids this decision produced (default: none)"
    )
    p_decide.add_argument(
        "--requires-approval", action="store_true", help="hold this decision for a human sign-off"
    )
    p_decide.add_argument("--task-id", default=None)
    p_decide.set_defaults(func=cmd_decide)

    p_confirm = sub.add_parser(
        "confirm", parents=[common], help="report a real-world outcome; promotes/demotes memories (0 model calls)"
    )
    p_confirm.add_argument("--decision-id", required=True)
    p_confirm.add_argument("--outcome", required=True, choices=["success", "rollback", "false_positive"])
    p_confirm.add_argument("--source", required=True, choices=["pagerduty", "metric", "human"])
    p_confirm.add_argument("--ref", required=True, help="external reference, e.g. an incident id")
    p_confirm.add_argument("--metric-delta", type=float, default=None, help="e.g. percent latency change")
    p_confirm.set_defaults(func=cmd_confirm)

    p_cross = sub.add_parser(
        "cross-examine", parents=[common], help="what the agent believed at decision time, diffed against now"
    )
    p_cross.add_argument("--decision-id", required=True)
    p_cross.set_defaults(func=cmd_cross_examine)

    p_audit = sub.add_parser("audit", parents=[common], help="human-readable tool_audit trail for a decision")
    p_audit.add_argument("--decision-id", required=True)
    p_audit.set_defaults(func=cmd_audit)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    # The shared global options carry argparse.SUPPRESS defaults so a subparser's unset
    # copy cannot silently overwrite a value parsed before the subcommand. Previously
    # `standing --tenant-id X recall ...` -- the order the tool's own --help documents --
    # fell back to the demo tenant with no warning, which on a multi-tenant memory
    # product means quietly showing the wrong tenant's memories. SUPPRESS means the
    # attribute can be absent, so backfill the real defaults exactly once, here.
    for _name, _default in (("dsn", None), ("json", False), ("tenant_id", None), ("agent_id", None)):
        if not hasattr(args, _name):
            setattr(args, _name, _default)

    _apply_dsn(args)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print(c("\ninterrupted", "yellow"))
        return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
