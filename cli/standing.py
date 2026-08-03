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
    STANDING_TENANT_ID / STANDING_AGENT_ID env vars override them for a
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
from typing import Any

# ---------------------------------------------------------------------------
# Demo identity. Deterministic (uuid5), so `remember` and `recall` agree on
# who "the agent" is across separate process invocations with zero flags --
# the point of a CLI a judge can type without first minting UUIDs on camera.
# ---------------------------------------------------------------------------
DEFAULT_TENANT_ID = uuid.uuid5(uuid.NAMESPACE_URL, "standing:demo-tenant")
DEFAULT_AGENT_ID = uuid.uuid5(uuid.NAMESPACE_URL, "standing:demo-agent")

DSN_ENV = "COCKROACH_DSN"
TENANT_ENV = "STANDING_TENANT_ID"
AGENT_ENV = "STANDING_AGENT_ID"

# gc.ttlseconds measured against the local cluster (see SPIKE-RESULTS.md /
# top-of-repo verified facts). Used only to phrase a friendly error -- never
# to decide behaviour, since the live cluster's actual setting is what
# matters and this is purely a message hint.
MEASURED_GC_WINDOW = "4 hours (gc.ttlseconds=14400)"

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
    print("This command line is fully wired against the frozen backend API")
    print("(backend.db / backend.memory / backend.decisions / backend.trust /")
    print("backend.replay). Once that package lands in this checkout, every")
    print("subcommand below runs against the live cluster with no CLI changes.")
    return EXIT_BACKEND_MISSING


def _friendly_gc_window_error(exc: Exception) -> str | None:
    """Translate a likely GC-threshold / AOST failure into plain English.

    Returns a friendly message if `exc` looks like an AS OF SYSTEM TIME /
    garbage-collection-threshold failure, else None (caller re-raises).
    """
    msg = str(exc).lower()
    hints = ("as of system time", "gc threshold", "batch timestamp", "garbage collect")
    if any(h in msg for h in hints):
        return (
            "That instant is older than this cluster keeps history for.\n"
            f"       Measured retention on this cluster: {MEASURED_GC_WINDOW}.\n"
            "       Pick a more recent decision, or rely on a belief_snapshots\n"
            "       checkpoint (a verified digest, not a content replay) for\n"
            "       anything further back."
        )
    return None


# ---------------------------------------------------------------------------
# Shared setup
# ---------------------------------------------------------------------------
def _resolve_ids(args: argparse.Namespace) -> tuple[uuid.UUID, uuid.UUID]:
    tenant = args.tenant_id or os.environ.get(TENANT_ENV) or str(DEFAULT_TENANT_ID)
    agent = args.agent_id or os.environ.get(AGENT_ENV) or str(DEFAULT_AGENT_ID)
    return uuid.UUID(str(tenant)), uuid.UUID(str(agent))


def _apply_dsn(args: argparse.Namespace) -> None:
    """backend.db.get_conn() takes no arguments; it reads COCKROACH_DSN.

    A --dsn flag has to land in the environment before backend.db creates
    its connection pool, so this runs first, before any backend import.
    """
    if args.dsn:
        os.environ[DSN_ENV] = args.dsn


def _identity_line(tenant_id: uuid.UUID, agent_id: uuid.UUID) -> str:
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
    if args.requires_approval:
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
    try:
        result = trust.grant_standing(args.decision_id, evidence)
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
# ---------------------------------------------------------------------------
def _fetch_decision(decision_id: str) -> dict[str, Any] | None:
    """Direct, read-only lookup. Not part of the frozen API: no backend
    module exposes "fetch one decision row", and this needs decided_at /
    tenant_id / agent_id before it can call backend.replay at all.
    """
    db = _import_backend("db")
    conn = db.get_conn()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT tenant_id, agent_id, decided_at, action, rationale "
            "FROM agent_decisions WHERE decision_id = %s",
            (decision_id,),
        )
        row = cur.fetchone()
    if row is None:
        return None
    tenant_id, agent_id, decided_at, action, rationale = row
    return {
        "tenant_id": tenant_id,
        "agent_id": agent_id,
        "decided_at": decided_at,
        "action": action,
        "rationale": rationale,
    }


def cmd_cross_examine(args: argparse.Namespace) -> int:
    try:
        decision = _fetch_decision(args.decision_id)
    except BackendUnavailable as err:
        return _handle_backend_unavailable(err, args)
    except Exception as exc:  # noqa: BLE001
        print(c(f"error: could not look up decision {args.decision_id}: {exc}", "red"))
        return EXIT_RUNTIME

    if decision is None:
        print(c(f"error: no decision with id {args.decision_id}", "red"))
        return EXIT_RUNTIME

    try:
        replay = _import_backend("replay")
    except BackendUnavailable as err:
        return _handle_backend_unavailable(err, args)

    try:
        diff = replay.belief_diff(decision["tenant_id"], decision["agent_id"], decision["decided_at"])
    except Exception as exc:  # noqa: BLE001
        friendly = _friendly_gc_window_error(exc)
        if friendly:
            print(c("error: can't reach that far back.", "red", "bold"))
            print(f"       {friendly}")
            return EXIT_RUNTIME
        print(c(f"error: cross-examine failed: {exc}", "red"))
        return EXIT_RUNTIME

    if args.json:
        print_json({"decision": decision, "diff": diff})
        return EXIT_OK

    print(f"decision {short(args.decision_id)}  action={decision['action']!r}  at {decision['decided_at']}")
    print(f"rationale: {decision['rationale']}")
    print()
    print(f"What the agent believed then, compared with what it believes now:")
    if not diff:
        print(c("  nothing changed since that instant", "dim"))
        return EXIT_OK

    rows = []
    for row in diff:
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
    try:
        db = _import_backend("db")
    except BackendUnavailable as err:
        return _handle_backend_unavailable(err, args)

    try:
        conn = db.get_conn()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT ts, actor, tool_name, tool_kind, risk, result_kind, request_id "
                "FROM tool_audit WHERE decision_id = %s ORDER BY ts ASC",
                (args.decision_id,),
            )
            rows = cur.fetchall()
    except Exception as exc:  # noqa: BLE001
        print(c(f"error: audit lookup failed: {exc}", "red"))
        return EXIT_RUNTIME

    if args.json:
        print_json(
            [
                {
                    "ts": ts,
                    "actor": actor,
                    "tool_name": tool_name,
                    "tool_kind": tool_kind,
                    "risk": risk,
                    "result_kind": result_kind,
                    "request_id": request_id,
                }
                for ts, actor, tool_name, tool_kind, risk, result_kind, request_id in rows
            ]
        )
        return EXIT_OK

    print(f"audit trail for decision {short(args.decision_id)}")
    if not rows:
        print(c("  no audited tool calls recorded for this decision yet", "dim"))
        return EXIT_OK

    table_rows = []
    for ts, actor, tool_name, tool_kind, risk, result_kind, request_id in rows:
        risk_style = {"high": "red", "medium": "yellow"}.get(risk, "green")
        result_style = {"denied": "red", "held": "yellow"}.get(result_kind, "green")
        table_rows.append(
            [
                str(ts),
                actor,
                f"{tool_name} ({tool_kind})",
                c(risk, risk_style),
                c(result_kind or "-", result_style),
                short(request_id),
            ]
        )
    print_table(["time", "actor", "tool", "risk", "result", "request"], table_rows)
    return EXIT_OK


# ---------------------------------------------------------------------------
# argument parser
# ---------------------------------------------------------------------------
def _build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--dsn", default=None, help=f"CockroachDB DSN (default: ${DSN_ENV})")
    common.add_argument("--json", action="store_true", help="print machine-readable JSON instead of a table")
    common.add_argument(
        "--tenant-id", default=None, help=f"tenant UUID (default: ${TENANT_ENV}, else a fixed demo tenant)"
    )
    common.add_argument(
        "--agent-id", default=None, help=f"agent UUID (default: ${AGENT_ENV}, else a fixed demo agent)"
    )

    parser = argparse.ArgumentParser(
        prog="standing",
        description="Standing -- a memory layer for on-call agents that only trusts what actually worked.",
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
    _apply_dsn(args)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print(c("\ninterrupted", "yellow"))
        return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
