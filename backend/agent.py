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

from . import anthropic_client, bedrock_client, decisions, memory

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
    "alert and the highest-authority usable memory tier recalled for this tenant. Every supplied "
    "memory is either VERIFIED or ATTESTED and includes its trust_tier. VERIFIED memory may "
    "support an autonomous action. ATTESTED memory is advisory only and the caller will hold "
    "the result for human approval. "
    "Call propose_action exactly once with exactly one action chosen from: "
    + ", ".join(ALLOWED_ACTIONS)
    + ". Ground your rationale ONLY in the supplied memories -- do not invent facts "
    "about the system. List the memory_id of every memory your rationale relies on "
    "in cited_memory_ids; if none of the recalled memories are relevant, say so in "
    "the rationale and return an empty cited_memory_ids list rather than fabricating "
    "a reason to cite one. Unconfirmed and disputed memories are deliberately not supplied "
    "because they may not steer even an advisory recommendation."
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


# Which memories may influence an action, and with what authority.
#
# `unconfirmed` is intentionally absent. It is retained and returned by recall for inspection,
# contradiction detection, and later corroboration, but it may not steer a recommendation.
# `attested` can produce an advisory recommendation, but the result is always held for approval.
# Only `verified` memory can autonomously outrank the baseline policy.
_TIER_RANK = {"verified": 2, "attested": 1}


def _action_from_memory(row: dict[str, Any]) -> str | None:
    """Does this memory actually name an action from the allow-list?

    Structured fields first: a memory carrying attribute_value='scale_up' is asserting the
    remediation directly, which is stronger evidence than the same word appearing in prose.
    Falling back to the content text catches the seeded incident memories, which describe what
    was done in sentences rather than in an attribute.
    """
    value = str(row.get("attribute_value") or "").strip().lower()
    if value in ALLOWED_ACTIONS:
        return value
    haystack = f"{row.get('attribute_key') or ''} {row.get('content') or ''}".lower()
    for action in ALLOWED_ACTIONS:
        if action == "no_action":
            continue
        if action.replace("_", " ") in haystack or action in haystack:
            return action
    return None


def _fallback_decision(
    alert_text: str, recalled: list[dict] | None = None
) -> tuple[str, str, bool, list[str]]:
    """Choose deterministically while enforcing the trust ladder as an action policy.

    `verified` memory may drive a recommendation. `attested` memory may produce an advisory
    recommendation, but it is held for human approval because the reported outcome was not
    independently corroborated. `unconfirmed` and `disputed` memories never steer an action.

    Returns ``(action, reason, memory_requires_approval, cited_memory_ids)``.
    """
    usable = [
        r for r in (recalled or [])
        if r.get("trust_tier") in _TIER_RANK and _action_from_memory(r)
    ]
    if usable:
        # Highest trust wins; ties break on the closest vector match, which is the order
        # recall() already returned them in.
        best = max(usable, key=lambda r: _TIER_RANK[r["trust_tier"]])
        action = _action_from_memory(best)
        if best["trust_tier"] == "verified":
            return action, (
                f"Chosen from memory {best['memory_id']} (trust_tier=verified), which "
                f"records {action!r} for this situation. No model was consulted; this memory "
                "may outrank the keyword table because an external system of record "
                "corroborated its outcome."
            ), False, [str(best["memory_id"])]
        return action, (
            f"Advisory recommendation from memory {best['memory_id']} (trust_tier=attested), "
            f"which records {action!r}. No model was consulted. The outcome was reported but "
            "not independently corroborated, so this recommendation is held for human approval."
        ), True, [str(best["memory_id"])]

    lowered = alert_text.lower()
    for keyword, action in _FALLBACK_RULES:
        if keyword in lowered:
            return action, (
                f"No recalled memory named an action, so this fell through to the keyword "
                f"table: {keyword!r} implies {action!r}. Nothing in memory informed this."
            ), False, []
    return _FALLBACK_DEFAULT_ACTION, (
        "No recalled memory named an action and no keyword matched, so this is the default "
        f"action ({_FALLBACK_DEFAULT_ACTION!r}). Nothing in memory informed this."
    ), False, []


def _fallback_action(alert_text: str, recalled: list[dict] | None = None) -> tuple[str, str]:
    """Backward-compatible action/reason view used by the CLI and benchmark harness."""
    action, reason, _requires_approval, _cited = _fallback_decision(alert_text, recalled)
    return action, reason


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


def _providers() -> list[tuple[str, Any]]:
    """Reasoning providers in preference order.

    Bedrock stays FIRST and that ordering is deliberate rather than sentimental: it is the AWS
    service this project is built around, and the moment its quota is granted it should take
    over with no code change and no redeploy. Anthropic is the standby that makes the agent
    work today, because every Bedrock inference quota on this account is 0.

    Trying Bedrock first is cheap even while it is failing -- backend/breaker.py opens after
    two consecutive failures and then refuses in microseconds, so the chain costs a warm
    container almost nothing before reaching the provider that works. The circuit breaker was
    built for latency and turns out to make provider fallback nearly free.

    A provider that cannot possibly work is skipped rather than attempted, so a deployment with
    no Anthropic key does not pay a network timeout to rediscover that on every request.
    """
    chain: list[tuple[str, Any]] = [(f"bedrock:{bedrock_client.MODEL_ID}", bedrock_client)]
    if anthropic_client.available():
        # Named after the endpoint that will actually answer, not after the API shape it
        # speaks -- see anthropic_client.provider_label().
        chain.append((f"{anthropic_client.provider_label()}:{anthropic_client.MODEL_ID}",
                      anthropic_client))
    return chain


def _ask_model(alert_text: str, recalled: list[dict], client=bedrock_client) -> dict[str, Any]:
    """Ask the configured chat model to propose one action. Raises ``bedrock_client.ModelUnavailable``
    if Bedrock cannot be reached, or if the model's response does not include a
    well-formed ``propose_action`` call -- both are treated the same way by the
    caller, which falls back to the deterministic rule table either way.
    """
    context = _format_memories_for_prompt(recalled)
    user_text = f"Alert:\n{alert_text}\n\nRecalled memories:\n{context}"
    response = client.converse(
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


def propose(situation: str, recalled: list[dict[str, Any]]) -> dict[str, Any]:
    """Choose ONE action for ``situation``, grounded in ``recalled`` memories.

    The single reasoning entry point. ``handle_alert`` uses it, and so does the
    deployed ``/decide`` route -- previously ``backend/handler.py`` carried its own
    near-identical copy, so the loop documented here was NOT the loop running in
    production. Two implementations of the thing the project calls "the agent" is a
    defect regardless of which one is better.

    Returns ``action``, ``rationale``, ``requires_approval``, ``reasoning_source`` and
    ``model_calls``. ``reasoning_source`` is deliberately explicit -- ``bedrock:<model>``
    when the model actually answered, ``fallback_heuristic`` when it did not -- because a
    keyword table silently impersonating an agent is precisely the failure this project
    should not ship.
    """
    calls_before = bedrock_client.call_count() + anthropic_client.call_count()

    # Try each provider in preference order. reasoning_source names the one that actually
    # answered -- never the one that was configured -- so a reader can always tell whether a
    # model decided this, and which.
    proposal = None
    source = ""
    last_error: Exception | None = None
    # Use one authority tier per reasoning pass. VERIFIED outranks ATTESTED, so it is the only
    # tier supplied when present. If there is no verified context, attested memory may support an
    # advisory recommendation, but the result is held for approval even if the model omits its
    # required citation -- anything shown to the model may have influenced it.
    verified_context = [r for r in recalled if r.get("trust_tier") == "verified"]
    attested_context = [r for r in recalled if r.get("trust_tier") == "attested"]
    model_context = verified_context or attested_context
    model_context_ids = {str(r["memory_id"]) for r in model_context}
    model_context_is_advisory = bool(attested_context and not verified_context)
    for name, client in _providers():
        try:
            proposal = _ask_model(situation, model_context, client)
            source = name
            break
        except bedrock_client.ModelUnavailable as exc:
            last_error = exc

    memory_requires_approval = False
    cited_memory_ids: list[str] = []
    if proposal is not None:
        action = str(proposal["action"])
        rationale = str(proposal["rationale"])
        cited_memory_ids = [
            str(mid)
            for mid in proposal.get("cited_memory_ids", [])
            if str(mid) in model_context_ids
        ]
        memory_requires_approval = model_context_is_advisory
    else:
        action, why, memory_requires_approval, cited_memory_ids = _fallback_decision(
            situation, recalled
        )
        rationale = (
            f"Deterministic fallback: no reasoning model was available ({last_error}). {why}"
        )
        source = (
            "fallback_memory"
            if cited_memory_ids
            else "fallback_heuristic"
        )

    return {
        "action": action,
        "rationale": rationale,
        "requires_approval": memory_requires_approval or action in HIGH_RISK_ACTIONS,
        "reasoning_source": source,
        "cited_memory_ids": cited_memory_ids,
        "model_calls": (bedrock_client.call_count() + anthropic_client.call_count())
        - calls_before,
    }


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

    proposed = propose(alert_text, recalled)
    action = proposed["action"]
    rationale = proposed["rationale"]
    used_model = not proposed["reasoning_source"].startswith("fallback_")
    cited_memory_ids = proposed["cited_memory_ids"]
    model_calls_this_call = proposed["model_calls"]
    requires_approval = proposed["requires_approval"]

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
