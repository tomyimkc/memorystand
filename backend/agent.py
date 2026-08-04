# SPDX-License-Identifier: Apache-2.0
"""The on-call agent loop: an alert comes in, a decision comes out.

This is the one path in the codebase where a model gets a vote. It recalls
admitted memories for the alert, asks the configured chat model (Amazon Nova
by default -- see ``backend.bedrock_client``, and docs/BEDROCK_QUOTA.md for why not
Claude) to propose exactly one action from a fixed allow-list grounded only in those
memories, records the decision, and writes back any new durable fact the
alert itself established.

Contrast this deliberately with ``backend/trust.py``: this module imports and
calls a model client; the outcome gate never does. ``reasoning_model_calls()``
here and ``backend.trust.model_calls()`` (always 0) are meant to be read side
by side -- that contrast between the two paths is the product's whole pitch.

DETERMINISTIC FALLBACK. Bedrock access is not guaranteed in every environment
this runs in (no AWS account, no model access granted, throttled). When
``backend.bedrock_client.converse`` raises ``ModelUnavailable``, this module
picks the action from a small explicit keyword rule table instead of
crashing or blocking the on-call loop. The fallback is honest about being a
fallback: its rationale string says so verbatim, and it never dresses itself
up as a model opinion.

Limits, stated plainly. The model's proposal is trusted for *which action*
and *why*, not for facts: memories are handed to it as read-only context and
it is instructed to cite only the memory_ids it actually used, but nothing
here stops a model from citing an irrelevant id or writing a rationale that
misreads a memory it cited correctly. ``cited_memory_ids`` is filtered down
to ids that were actually recalled, which catches a hallucinated id but not
a misreading of a real one.
"""

from __future__ import annotations

import json
from typing import Any

from . import bedrock_client, decisions, memory

ALLOWED_ACTIONS: tuple[str, ...] = (
    "page_oncall",
    "restart_service",
    "scale_up",
    "open_incident",
    "no_action",
)

# Actions that change production state get held for a human rather than taken
# automatically -- see backend/decisions.py's requires_approval contract.
HIGH_RISK_ACTIONS = frozenset({"restart_service", "scale_up"})

_PROPOSE_ACTION_TOOL = {
    "toolSpec": {
        "name": "propose_action",
        "description": (
            "Propose exactly one on-call action for this alert, grounded only in the "
            "memories supplied as context. Do not propose an action not in the enum."
        ),
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": list(ALLOWED_ACTIONS),
                        "description": "Exactly one action from the allow-list.",
                    },
                    "rationale": {
                        "type": "string",
                        "description": "Why this action, in terms of the cited memories.",
                    },
                    "cited_memory_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "memory_id values, taken verbatim from the supplied context, "
                            "that this rationale actually relies on. Empty list if none "
                            "of the recalled memories were relevant."
                        ),
                    },
                },
                "required": ["action", "rationale", "cited_memory_ids"],
            }
        },
    }
}

SYSTEM_PROMPT = (
    "You are an on-call response agent for a production service. You are given an "
    "alert and a list of memories recalled from prior incidents for this tenant. "
    "Call propose_action exactly once with exactly one action chosen from: "
    + ", ".join(ALLOWED_ACTIONS)
    + ". Ground your rationale ONLY in the supplied memories -- do not invent facts "
    "about the system. List the memory_id of every memory your rationale relies on "
    "in cited_memory_ids; if none of the recalled memories are relevant, say so in "
    "the rationale and return an empty cited_memory_ids list rather than fabricating "
    "a reason to cite one."
)

# Deterministic fallback rule table, checked in order against the lower-cased alert
# text. Small and explicit on purpose -- an operator reading this file can see the
# entire fallback policy in six lines, no model required.
_FALLBACK_RULES: tuple[tuple[str, str], ...] = (
    ("5xx", "page_oncall"),
    ("down", "page_oncall"),
    ("unavailable", "page_oncall"),
    ("outage", "page_oncall"),
    ("latency", "scale_up"),
    ("cpu", "scale_up"),
    ("saturat", "scale_up"),
    ("disk", "open_incident"),
    ("degraded", "open_incident"),
)
_FALLBACK_DEFAULT_ACTION = "open_incident"


def _fallback_action(alert_text: str) -> str:
    lowered = alert_text.lower()
    for keyword, action in _FALLBACK_RULES:
        if keyword in lowered:
            return action
    return _FALLBACK_DEFAULT_ACTION


def _alert_text(alert: dict[str, Any]) -> str:
    """Flatten an alert payload into one string for recall and for the prompt."""
    parts = [str(alert[k]) for k in ("title", "summary", "description", "message") if alert.get(k)]
    if not parts:
        parts = [json.dumps(alert, sort_keys=True, default=str)]
    return " -- ".join(parts)


def _format_memories_for_prompt(recalled: list[dict]) -> str:
    if not recalled:
        return "(no memories were recalled for this alert)"
    lines = []
    for row in recalled:
        attr = ""
        if row.get("entity") or row.get("attribute_key"):
            attr = f" [{row.get('entity') or '-'}.{row.get('attribute_key') or '-'}={row.get('attribute_value') or '-'}]"
        lines.append(
            f"- memory_id={row['memory_id']} trust={row.get('trust_tier', '?')}{attr}: {row.get('content', '')}"
        )
    return "\n".join(lines)


def _ask_model(alert_text: str, recalled: list[dict]) -> dict[str, Any]:
    """Ask the configured chat model to propose one action. Raises ``bedrock_client.ModelUnavailable``
    if Bedrock cannot be reached, or if the model's response does not include a
    well-formed ``propose_action`` call -- both are treated the same way by the
    caller, which falls back to the deterministic rule table either way.
    """
    context = _format_memories_for_prompt(recalled)
    user_text = f"Alert:\n{alert_text}\n\nRecalled memories:\n{context}"
    response = bedrock_client.converse(
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": [{"text": user_text}]}],
        tools=[_PROPOSE_ACTION_TOOL],
        max_tokens=512,
        temperature=0.0,
    )

    content_blocks = response.get("output", {}).get("message", {}).get("content", [])
    for block in content_blocks:
        tool_use = block.get("toolUse")
        if tool_use and tool_use.get("name") == "propose_action":
            tool_input = tool_use.get("input") or {}
            action = tool_input.get("action")
            if action not in ALLOWED_ACTIONS:
                raise bedrock_client.ModelUnavailable(
                    f"model proposed an action outside the allow-list: {action!r}"
                )
            return {
                "action": action,
                "rationale": str(tool_input.get("rationale") or ""),
                "cited_memory_ids": [str(x) for x in (tool_input.get("cited_memory_ids") or [])],
            }

    raise bedrock_client.ModelUnavailable(
        "model response did not include a propose_action tool call"
    )


def handle_alert(tenant_id: str, agent_id: str, alert: dict[str, Any]) -> dict[str, Any]:
    """Recall memory for an alert, get a proposed action, and record the decision.

    1. Recalls memories relevant to the alert text via vector search.
    2. Asks the configured chat model to propose one action grounded in those memories, falling back to
       a deterministic keyword rule if the model is unavailable.
    3. Marks the decision ``requires_approval`` if the action is high-risk.
    4. Writes any new durable fact the alert established (which entity last saw
       which action) via ``memory.remember`` -- that becomes this decision's
       ``produced_memory_ids``, which is what ``backend.trust.grant_standing`` later
       re-tiers once a real outcome is confirmed.
    5. Records the decision with ``backend.decisions.decide``.

    Returns the decision, the recalled context, the model's (or fallback's)
    rationale, and how many Bedrock calls this invocation made.
    """
    alert_text = _alert_text(alert)
    recalled = memory.recall(tenant_id, agent_id, alert_text, k=5)
    recalled_ids = {row["memory_id"] for row in recalled}

    calls_before = bedrock_client.call_count()
    try:
        proposal = _ask_model(alert_text, recalled)
        action = proposal["action"]
        rationale = proposal["rationale"]
        cited_memory_ids = [mid for mid in proposal["cited_memory_ids"] if mid in recalled_ids]
        used_model = True
    except bedrock_client.ModelUnavailable as exc:
        action = _fallback_action(alert_text)
        rationale = f"deterministic fallback (model unavailable): {exc}"
        cited_memory_ids = list(recalled_ids)
        used_model = False
    model_calls_this_call = bedrock_client.call_count() - calls_before

    requires_approval = action in HIGH_RISK_ACTIONS

    produced_memory_ids: list[str] = []
    entity = alert.get("entity") or alert.get("service")
    if entity:
        remembered = memory.remember(
            tenant_id,
            agent_id,
            f"Alert observed for {entity}: {alert_text} -> action taken: {action}",
            entity=str(entity),
            attribute_key="last_alert_action",
            attribute_value=action,
            memory_type="episodic",
            source=str(alert.get("source") or "pagerduty"),
            structured_data=alert,
        )
        if remembered.get("verdict") == memory.ACCEPTED:
            produced_memory_ids.append(remembered["memory_id"])

    decision = decisions.decide(
        tenant_id,
        agent_id,
        action,
        rationale,
        list(recalled_ids),
        produced_memory_ids=produced_memory_ids,
        requires_approval=requires_approval,
    )

    return {
        "decision": decision,
        "recalled": recalled,
        "action": action,
        "rationale": rationale,
        "cited_memory_ids": cited_memory_ids,
        "used_model": used_model,
        "model_calls": model_calls_this_call,
    }


def reasoning_model_calls() -> int:
    """Total Bedrock Converse calls made by ``handle_alert`` in this process.

    Compare with ``backend.trust.model_calls()``, which is always 0: this is the
    number the demo shows going up when ``/decide`` runs, next to the number that
    never moves when ``/confirm_outcome`` runs. That contrast is the pitch.
    """
    return bedrock_client.call_count()


__all__ = [
    "ALLOWED_ACTIONS",
    "HIGH_RISK_ACTIONS",
    "handle_alert",
    "reasoning_model_calls",
]
