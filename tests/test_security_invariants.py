# SPDX-License-Identifier: Apache-2.0
"""Three security holes found by an adversarial review of the deployed system.

All three were real, all three were reachable from the public Function URL, and none of them
would have been caught by the existing suite -- which tested that the system does the right
thing for a well-behaved caller, not that it refuses a hostile one.

  1. SQL injection in the time-travel route. ``AS OF SYSTEM TIME`` cannot be parameterised,
     so the instant is interpolated into the statement. ``GET /diff?instant=`` is
     unauthenticated, and the value went in unvalidated.

  2. Missing tenant scoping on the trust grant. ``trust._apply`` matched on ``decision_id``
     alone -- the only query in the codebase without a tenant predicate, and the one that
     promotes memories to ``verified``. Combined with (3) it let any caller grant standing
     to any tenant's memories, falsifying the project's central claim from the outside.

  3. ``/confirm_outcome`` was not behind the shared secret, while ``/ingest`` and ``/decide``
     were. It is the route that grants trust.

The lesson worth keeping: the routes that were gated were the ones that obviously *write
content*. The route that writes *trust* was not, because it reads as a callback rather than a
mutation. Classifying routes by how they feel instead of by what they change is how this gap
opened.
"""

from __future__ import annotations

import json
import uuid

import pytest

from backend import db, decisions, evidence, handler, memory, replay, trust


def _averages(before: float, after: float):
    """A stub CloudWatch average: odd calls return `before`, even return `after`, so a delta of
    `after - before` is observed. Mirrors the helper in test_reverification.py."""
    calls = {"n": 0}

    def _avg(client, namespace, metric, dims, start, end):
        calls["n"] += 1
        return before if calls["n"] % 2 == 1 else after

    return _avg


# --- 1. The injection sink ---------------------------------------------------------------

HOSTILE_INSTANTS = [
    "-1s'; DROP TABLE agent_memories; --",
    "' OR '1'='1",
    "-1s' UNION SELECT NULL --",
    "2026-08-04 10:00:00'; SELECT pg_sleep(10); --",
    "'; SET CLUSTER SETTING sql.defaults.distsql = 'off'; --",
    "\\'; DROP TABLE agent_decisions; --",
    "-1s\x00",
    "; SHOW DATABASES",
]


@pytest.mark.parametrize("hostile", HOSTILE_INSTANTS)
def test_hostile_instants_are_refused_before_reaching_sql(hostile: str) -> None:
    """Nothing that is not an instant may reach the AS OF SYSTEM TIME literal."""
    with pytest.raises(replay.InvalidInstant):
        replay._as_aost_literal(hostile)


LEGITIMATE_INSTANTS = [
    "-30s",
    "-1h",
    "-500ms",
    "1785840000.0000000001",
    "1785840000",
    "2026-08-04 10:00:00+00:00",
    "2026-08-04T10:00:00Z",
    "2026-08-04 10:00:00.123456+00:00",
]


@pytest.mark.parametrize("ok", LEGITIMATE_INSTANTS)
def test_real_instants_still_work(ok: str) -> None:
    """The allow-list must not be so tight that it breaks the actual feature.

    A validator that rejects hostile input by rejecting everything is not a fix; the
    time-travel demo has to keep working, so both halves are asserted.
    """
    assert replay._as_aost_literal(ok) == ok.strip()


def test_datetimes_are_formatted_not_passed_through() -> None:
    """The datetime path cannot carry attacker text, and must stay that way."""
    import datetime as dt

    rendered = replay._as_aost_literal(dt.datetime(2026, 8, 4, 10, 0, 0, tzinfo=dt.timezone.utc))
    assert rendered.startswith("2026-08-04 10:00:00")
    assert "'" not in rendered and ";" not in rendered


# --- 2. Tenant scoping on the trust grant -------------------------------------------------


def test_one_tenant_cannot_grant_standing_to_another_tenants_decision(
    tenant_id: str, agent_id: str
) -> None:
    """The attack this project's own thesis says must be impossible.

    Memory trust is supposed to be grantable only by a real external outcome. If a caller
    can confirm an outcome against another tenant's decision, they can manufacture
    ``verified`` memories in someone else's agent -- and the promotion path being free of
    model calls is irrelevant if it can be driven by the wrong person.
    """
    mem = memory.remember(
        tenant_id, agent_id, "restarting payments-service before ledger-worker resolved it"
    )
    memory_id = mem["memory_id"]

    decision = decisions.decide(
        tenant_id,
        agent_id,
        action="restart_service",
        rationale="because the runbook says so",
        consulted_memory_ids=[memory_id],
        produced_memory_ids=[memory_id],
    )
    decision_id = decision["decision_id"]

    attacker_tenant = str(uuid.uuid4())
    with pytest.raises(trust.OutcomeRejected) as exc:
        trust.grant_standing(
            attacker_tenant,
            decision_id,
            {"outcome": "success", "source": "pagerduty", "external_ref": "INC-9999"},
        )

    # The error must not confirm that the decision exists -- otherwise it is an oracle for
    # enumerating other tenants' decision ids.
    assert "already has outcome" not in str(exc.value)

    # And the real owner must still be able to do it, or the fix broke the feature.
    result = trust.grant_standing(
        tenant_id,
        decision_id,
        {"outcome": "success", "source": "pagerduty", "external_ref": "INC-9999"},
    )
    assert result["model_calls"] == 0
    assert memory_id in [str(m) for m in result["promoted"]]


def test_attacker_cannot_promote_another_tenants_memory_via_their_own_decision(
    tenant_id: str, agent_id: str
) -> None:
    """The variant that survived the first fix, because the first fix guarded the wrong query.

    The test above proves an attacker cannot confirm an outcome against SOMEONE ELSE'S
    decision. It does not prove they cannot confirm an outcome against THEIR OWN decision
    that names someone else's memories -- and ``produced_memory_ids`` is caller-supplied
    verbatim (``handler.py``) and inserted without validation (``decisions.py``), so nothing
    stopped them.

    The strongest boundary is at decision creation: a tenant must not be allowed to persist a
    decision that points at another tenant's memories. The tenant-scoped promotion UPDATE remains
    defence in depth, but the malformed decision itself is now rejected.
    """
    victim = memory.remember(
        tenant_id, agent_id, "checkout-api circuit breaker trips at 500ms, confirmed by on-call"
    )
    victim_id = victim["memory_id"]
    # remember() does not return the tier, so read it back rather than assuming it. An
    # earlier draft asserted on victim["trust_tier"] and failed with KeyError -- red for a
    # reason that had nothing to do with the vulnerability, which proves nothing at all.
    assert memory.get(tenant_id, victim_id)["trust_tier"] == "unconfirmed"

    attacker_tenant = str(uuid.uuid4())
    attacker_agent = str(uuid.uuid4())
    try:
        with pytest.raises(decisions.InvalidMemoryReference, match="owned by this tenant"):
            decisions.decide(
                attacker_tenant,
                attacker_agent,
                action="scale_up",
                rationale="a decision the attacker is entitled to file",
                consulted_memory_ids=[],
                produced_memory_ids=[victim_id],          # <-- not the attacker's memory
            )

        after = memory.get(tenant_id, victim_id)
        assert after is not None
        assert after["trust_tier"] == "unconfirmed", (
            f"another tenant moved this memory to {after['trust_tier']!r} -- the promotion "
            "UPDATE is not tenant-scoped"
        )
    finally:
        conn = db.get_conn()
        try:
            with conn.cursor() as cur:
                for table in ("tool_audit", "agent_decisions", "agent_memories"):
                    cur.execute(f"DELETE FROM {table} WHERE tenant_id = %s", (attacker_tenant,))
            conn.commit()
        finally:
            db.put_conn(conn)


def test_one_metric_only_verifies_memories_about_its_own_entity(tenant_id, agent_id, monkeypatch):
    """A CloudWatch metric confirms ONE entity; a sibling memory about another entity in the same
    decision must not inherit 'verified' standing it never earned.

    An outside review found this: a decision that produces [memory about payments-service, memory
    about ledger-worker], confirmed by a metric that is only about payments-service, promoted BOTH
    to 'verified' -- the single verdict fanned out over the whole produced array. After the fix,
    only the memory whose own entity matches the checked metric reaches 'verified'; the other had
    a reported-good outcome but no metric of its own, so it lands at 'attested', not 'verified'.

    The evidence lookup must consider every produced-memory entity rather than selecting an
    arbitrary first row. Alphabetical ordering places ``ledger-worker`` before
    ``payments-service`` here, so this also proves valid evidence is not rejected merely because
    the matching subject was not first.
    """
    ref = "AWS/Lambda|Duration|FunctionName=payments-service"
    monkeypatch.setattr(evidence, "_average", _averages(100.0, 60.0))

    mem_x = memory.remember(tenant_id, agent_id, "scaling payments-service cleared the spike",
                            entity="payments-service")
    mem_y = memory.remember(tenant_id, agent_id, "ledger-worker was restarted around then",
                            entity="ledger-worker")
    decision = decisions.decide(
        tenant_id, agent_id, action="scale_up", rationale="r", consulted_memory_ids=[],
        produced_memory_ids=[mem_x["memory_id"], mem_y["memory_id"]],
    )
    trust.grant_standing(
        tenant_id, decision["decision_id"],
        {"outcome": "success", "source": "metric", "external_ref": ref, "metric_delta": -40.0},
    )

    tier_x = memory.get(tenant_id, mem_x["memory_id"])["trust_tier"]
    tier_y = memory.get(tenant_id, mem_y["memory_id"])["trust_tier"]
    assert tier_x == trust.VERIFIED, "the memory the metric actually confirmed must reach verified"
    assert tier_y == trust.ATTESTED, (
        f"ledger-worker reached {tier_y!r}: a metric about payments-service must not grant a "
        "memory about a different service the standing reality never gave it"
    )


def test_grant_standing_requires_tenant_positionally() -> None:
    """An authorisation argument a caller can forget is one a caller will forget."""
    import inspect

    params = list(inspect.signature(trust.grant_standing).parameters.values())
    assert params[0].name == "tenant_id"
    assert params[0].default is inspect.Parameter.empty


# --- 3. The gate on the route that grants trust -------------------------------------------


def test_confirm_outcome_is_behind_the_shared_secret() -> None:
    """It mutates trust, so it belongs with the other mutating routes."""
    assert "/confirm_outcome" in handler.SECRET_GATED_PATHS


def test_every_mutating_route_is_gated() -> None:
    """Guard against the next route being classified by feel rather than by effect.

    /confirm_outcome was left open because it reads like a callback rather than a write.
    This asserts the rule directly: if it is a POST, it changes something, so it is gated.
    """
    mutating = {path for (method, path) in handler.ROUTES if method == "POST"}
    ungated = mutating - handler.SECRET_GATED_PATHS
    assert not ungated, f"POST routes reachable without the shared secret: {sorted(ungated)}"


# --- 4. Fail-closed kill switch -----------------------------------------------------------


def test_kill_switch_fails_closed_when_ssm_is_unreadable(monkeypatch) -> None:
    """An unreadable kill switch must stop writes, not wave them through.

    The failure mode this prevents: SSM is throttled or IAM regresses during an incident,
    the switch reads as absent, and it defaults to 'off' -- so writes continue at exactly
    the moment an operator was trying to stop them.
    """
    monkeypatch.delenv(handler.KILL_SWITCH_ENV, raising=False)
    monkeypatch.setattr(handler, "_get_ssm_param", lambda *a, **k: None)
    assert handler.kill_switch_engaged() is True


def test_kill_switch_off_still_means_off(monkeypatch) -> None:
    """Failing closed must not mean permanently closed."""
    monkeypatch.delenv(handler.KILL_SWITCH_ENV, raising=False)
    monkeypatch.setattr(handler, "_get_ssm_param", lambda *a, **k: "off")
    assert handler.kill_switch_engaged() is False


# --- 4. The scheduled sweep is reachable by IAM, never by HTTP ------------------------------

def test_a_request_body_cannot_trigger_the_scheduled_sweep(monkeypatch):
    """The public surface must not be able to reach an internal, mutating maintenance task.

    reverify.sweep() DEMOTES trust tiers. It is invoked by EventBridge Scheduler calling the
    Lambda directly, which is gated by IAM rather than by the shared secret -- so it is not an
    HTTP route at all. This test pins the boundary from the attacker's side: a Function URL
    request whose JSON body says {"memorystand_task": "reverify"} must be treated as an ordinary
    HTTP request (and 404, since no such route exists), never as a scheduled task.

    The property that makes this hold: AWS puts a caller's JSON in event["body"] as a STRING and
    builds every top-level key itself, so a client cannot set one.
    """
    called = {"n": 0}
    from backend import reverify as _rv

    monkeypatch.setattr(_rv, "sweep", lambda *a, **k: called.__setitem__("n", called["n"] + 1))

    hostile = {
        "requestContext": {"http": {"method": "POST", "path": "/reverify"}, "requestId": "r"},
        "rawPath": "/reverify",
        "headers": {"content-type": "application/json"},
        "body": json.dumps({"memorystand_task": "reverify", "tenant_id": None}),
    }
    resp = handler.lambda_handler(hostile, None)
    assert called["n"] == 0, "an HTTP request body reached the scheduled sweep"
    assert resp["statusCode"] == 404

    # And the marker at the TOP level of an HTTP-shaped event is still refused, because the
    # HTTP markers are present.
    hostile_top = dict(hostile)
    hostile_top["memorystand_task"] = "reverify"
    handler.lambda_handler(hostile_top, None)
    assert called["n"] == 0, "an HTTP-shaped event with a top-level marker reached the sweep"


def test_a_direct_invoke_does_run_the_sweep(monkeypatch):
    """The other half: the scheduler's own event shape must actually work, or the job is dead."""
    seen = {}
    from backend import reverify as _rv

    monkeypatch.setattr(_rv, "sweep", lambda tenant_id=None, dry_run=False: seen.update(
        {"tenant_id": tenant_id, "dry_run": dry_run}) or {
        "checked": 3, "still_verified": 2, "demoted_to_attested": 1,
        "demoted_to_disputed": 0, "model_calls": 0, "dry_run": dry_run, "changes": []})

    out = handler.lambda_handler({"memorystand_task": "reverify"}, None)
    assert out["ok"] is True
    assert out["receipt"]["checked"] == 3
    assert out["receipt"]["model_calls"] == 0, "the sweep must stay model-free"
    assert seen == {"tenant_id": None, "dry_run": False}


def test_an_unknown_scheduled_task_is_refused_not_guessed():
    out = handler.lambda_handler({"memorystand_task": "drop_everything"}, None)
    assert out["ok"] is False and "unknown task" in out["error"]


# --- 5. The public demo credential is scoped to one tenant, server-side ---------------------

def _http(path, body, secret):
    return {
        "requestContext": {"http": {"method": "POST", "path": path}, "requestId": "r"},
        "rawPath": path,
        "headers": {"content-type": "application/json", "x-memorystand-secret": secret},
        "body": json.dumps(body),
    }


def _http_get(path, query, secret=None):
    headers = {}
    if secret is not None:
        headers["x-memorystand-secret"] = secret
    return {
        "requestContext": {"http": {"method": "GET", "path": path}, "requestId": "r"},
        "rawPath": path,
        "headers": headers,
        "queryStringParameters": query,
    }


def test_demo_credential_cannot_write_to_another_tenant(monkeypatch, agent_id):
    """The whole reason the demo secret can be published.

    A single global secret authorised every write on every tenant, so letting a judge try the
    product meant handing over everything -- which is why three of four dashboard panels were
    read-only. The demo credential authenticates and is then refused for any tenant but the demo
    one, ENFORCED HERE rather than documented. Published credential, contained blast radius.
    """
    demo_tenant = str(uuid.uuid4())
    monkeypatch.setenv("MEMORYSTAND_SHARED_SECRET", "operator-secret")
    monkeypatch.setenv("MEMORYSTAND_DEMO_SECRET", "demo-secret")
    monkeypatch.setenv("MEMORYSTAND_DEMO_TENANT", demo_tenant)
    monkeypatch.setenv("MEMORYSTAND_KILL_SWITCH", "off")

    victim = str(uuid.uuid4())
    for path, body in (
        ("/ingest", {"tenant_id": victim, "agent_id": agent_id, "content": "x"}),
        ("/decide", {"tenant_id": victim, "agent_id": agent_id, "query": "x"}),
        ("/confirm_outcome", {"tenant_id": victim, "decision_id": str(uuid.uuid4()),
                              "outcome": "success", "source": "metric", "external_ref": "r"}),
    ):
        resp = handler.lambda_handler(_http(path, body, "demo-secret"), None)
        assert resp["statusCode"] == 401, f"{path} let the demo credential touch another tenant"
        assert "scoped to the public demo tenant" in resp["body"]


def test_operator_credential_is_not_tenant_scoped(monkeypatch, agent_id):
    """The demo scoping must not accidentally restrict the real operator credential."""
    monkeypatch.setenv("MEMORYSTAND_SHARED_SECRET", "operator-secret")
    monkeypatch.setenv("MEMORYSTAND_DEMO_SECRET", "demo-secret")
    monkeypatch.setenv("MEMORYSTAND_DEMO_TENANT", str(uuid.uuid4()))
    monkeypatch.setenv("MEMORYSTAND_KILL_SWITCH", "off")

    resp = handler.lambda_handler(
        _http("/ingest", {"tenant_id": str(uuid.uuid4()), "agent_id": agent_id,
                          "content": "operator writes anywhere"}, "operator-secret"), None)
    assert resp["statusCode"] != 401, "the operator secret must not be tenant-scoped"


def test_a_wrong_secret_is_still_refused(monkeypatch, agent_id):
    monkeypatch.setenv("MEMORYSTAND_SHARED_SECRET", "operator-secret")
    monkeypatch.setenv("MEMORYSTAND_DEMO_SECRET", "demo-secret")
    monkeypatch.setenv("MEMORYSTAND_DEMO_TENANT", str(uuid.uuid4()))
    monkeypatch.setenv("MEMORYSTAND_KILL_SWITCH", "off")

    resp = handler.lambda_handler(
        _http("/ingest", {"tenant_id": "t", "agent_id": agent_id, "content": "x"}, "guess"), None)
    assert resp["statusCode"] == 401
    assert "invalid or missing" in resp["body"]


def test_demo_credential_is_inert_when_unconfigured(monkeypatch, agent_id):
    """With no demo secret configured, nothing changes -- the feature is opt-in by deployment."""
    monkeypatch.setenv("MEMORYSTAND_SHARED_SECRET", "operator-secret")
    monkeypatch.delenv("MEMORYSTAND_DEMO_SECRET", raising=False)
    monkeypatch.setenv("MEMORYSTAND_DEMO_SECRET_SSM_PARAM", "/nonexistent/demo_secret")
    monkeypatch.setenv("MEMORYSTAND_KILL_SWITCH", "off")

    resp = handler.lambda_handler(
        _http("/ingest", {"tenant_id": "t", "agent_id": agent_id, "content": "x"}, "demo-secret"), None)
    assert resp["statusCode"] == 401


def test_health_publishes_the_demo_credential_but_never_the_operator_secret(monkeypatch):
    """/health hands out the demo credential on purpose, and must never hand out the other one.

    Publishing a write credential on an unauthenticated route is only defensible because the
    server refuses it for any tenant but the demo one. That reasoning does not extend one inch
    further: the operator secret authorises every tenant, so its appearance here would be a total
    compromise. This asserts the negative, because that is the half that would be catastrophic.
    """
    monkeypatch.setenv("MEMORYSTAND_SHARED_SECRET", "OPERATOR-SUPER-SECRET-VALUE")
    monkeypatch.setenv("MEMORYSTAND_DEMO_SECRET", "demo-public-value")
    monkeypatch.setenv("MEMORYSTAND_DEMO_TENANT", "11111111-2222-3333-4444-555555555555")

    _, body = handler._route_health({}, {}, "req")
    assert body["demo"]["credential"] == "demo-public-value"
    assert body["demo"]["tenant_id"] == "11111111-2222-3333-4444-555555555555"
    assert "OPERATOR-SUPER-SECRET-VALUE" not in json.dumps(body), "/health leaked the operator secret"


def test_health_omits_the_demo_block_when_unconfigured(monkeypatch):
    """With no demo credential configured, /health must not grow a demo block.

    Patches the RESOLVERS, not the environment. `DEMO_SECRET_SSM_PARAM` is a module-level
    constant bound at import time, so setting the env var afterwards changes nothing -- an
    earlier version of this test did that and passed only because the machine had no working
    AWS credentials. The moment credentials existed it read the real parameter and failed. A test
    whose result depends on whether you happen to be logged in is not a test.
    """
    monkeypatch.setattr(handler, "_configured_demo_secret", lambda: None)
    monkeypatch.setattr(handler, "_configured_demo_tenant", lambda: None)
    _, body = handler._route_health({}, {}, "req")
    assert "demo" not in body


# --- 6. Public reads are inspectable but contained -------------------------------------------

def test_public_read_cannot_enumerate_another_tenant(monkeypatch):
    demo_tenant = str(uuid.uuid4())
    victim_tenant = str(uuid.uuid4())
    monkeypatch.setenv("MEMORYSTAND_SHARED_SECRET", "operator-secret")
    monkeypatch.setenv("MEMORYSTAND_DEMO_SECRET", "demo-secret")
    monkeypatch.setenv("MEMORYSTAND_DEMO_TENANT", demo_tenant)
    monkeypatch.setattr(
        memory,
        "recall",
        lambda *args, **kwargs: pytest.fail("tenant scoping must happen before recall"),
    )

    resp = handler.lambda_handler(
        _http_get("/recall", {"tenant_id": victim_tenant, "q": "secrets"}),
        None,
    )

    assert resp["statusCode"] == 401
    assert "isolated demo tenant" in resp["body"]


def test_demo_tenant_read_remains_public_and_bounded(monkeypatch):
    demo_tenant = str(uuid.uuid4())
    seen = {}
    monkeypatch.setenv("MEMORYSTAND_SHARED_SECRET", "operator-secret")
    monkeypatch.setenv("MEMORYSTAND_DEMO_SECRET", "demo-secret")
    monkeypatch.setenv("MEMORYSTAND_DEMO_TENANT", demo_tenant)

    def _recall(tenant_id, agent_id, query, *, k):
        seen.update({"tenant_id": tenant_id, "query": query, "k": k})
        return []

    monkeypatch.setattr(memory, "recall", _recall)
    resp = handler.lambda_handler(
        _http_get("/recall", {"tenant_id": demo_tenant, "q": "latency", "k": "20"}),
        None,
    )

    assert resp["statusCode"] == 200
    assert seen == {"tenant_id": demo_tenant, "query": "latency", "k": 20}


def test_operator_can_read_a_non_demo_tenant(monkeypatch):
    demo_tenant = str(uuid.uuid4())
    victim_tenant = str(uuid.uuid4())
    seen = {}
    monkeypatch.setenv("MEMORYSTAND_SHARED_SECRET", "operator-secret")
    monkeypatch.setenv("MEMORYSTAND_DEMO_SECRET", "demo-secret")
    monkeypatch.setenv("MEMORYSTAND_DEMO_TENANT", demo_tenant)
    monkeypatch.setattr(
        memory,
        "recall",
        lambda tenant_id, agent_id, query, *, k: seen.update({"tenant_id": tenant_id}) or [],
    )

    resp = handler.lambda_handler(
        _http_get(
            "/recall",
            {"tenant_id": victim_tenant, "q": "latency"},
            secret="operator-secret",
        ),
        None,
    )

    assert resp["statusCode"] == 200
    assert seen["tenant_id"] == victim_tenant


@pytest.mark.parametrize("bad_k", ["0", "21", "not-an-integer"])
def test_public_recall_rejects_unbounded_or_invalid_k(monkeypatch, bad_k):
    demo_tenant = str(uuid.uuid4())
    monkeypatch.setenv("MEMORYSTAND_DEMO_TENANT", demo_tenant)
    monkeypatch.setattr(
        memory,
        "recall",
        lambda *args, **kwargs: pytest.fail("invalid k must be rejected before querying"),
    )

    resp = handler.lambda_handler(
        _http_get("/recall", {"tenant_id": demo_tenant, "q": "latency", "k": bad_k}),
        None,
    )

    assert resp["statusCode"] == 400
    assert "\"error\": \"bad_request\"" in resp["body"]


def test_unhandled_errors_do_not_leak_internal_details(monkeypatch):
    demo_tenant = str(uuid.uuid4())
    monkeypatch.setenv("MEMORYSTAND_DEMO_TENANT", demo_tenant)
    monkeypatch.setattr(
        memory,
        "recall",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("postgresql://admin:super-secret@internal.example")
        ),
    )

    resp = handler.lambda_handler(
        _http_get("/recall", {"tenant_id": demo_tenant, "q": "latency"}),
        None,
    )

    assert resp["statusCode"] == 500
    assert "super-secret" not in resp["body"]
    assert "internal.example" not in resp["body"]
    assert json.loads(resp["body"]) == {
        "error": "internal_error",
        "detail": "the request could not be completed",
        "request_id": "r",
    }
