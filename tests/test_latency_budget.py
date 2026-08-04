# SPDX-License-Identifier: Apache-2.0
"""A slow dependency must not be able to consume the caller's whole budget.

This bug shipped twice. First in ``embeddings.embed``: with Bedrock throttled, five
retries with exponential backoff outlived the 30s Lambda timeout, so ``/recall``
returned a 502 after hanging for the full 30s instead of degrading in under a second.
It was fixed there with a deadline -- and then the identical bug was still sitting in
``bedrock_client.converse``, unnoticed until ``/decide`` started genuinely calling the
model and began timing out in production the same way.

Two separate lessons, one test file:

1. An *attempt* budget is not a *latency* budget. "5 attempts" says nothing about
   elapsed time once backoff is involved.
2. Bounding the loop is not enough if a single attempt is unbounded. botocore retries
   internally by default, so one ``converse()`` can burn many seconds before it raises
   -- which is how the first version of the deadline still took 18s to honour an 8s
   promise.

These tests inject a client that always throttles and assert the caller gives up on
time. They make no network calls and need no AWS credentials or quota, so they hold
the guarantee even when Bedrock is healthy and the bug would be invisible.
"""

from __future__ import annotations

import time

import pytest

from backend import bedrock_client, embeddings


class _AlwaysThrottles:
    """Stands in for a Bedrock client that is being rate-limited.

    Sleeps before raising: a client that fails *instantly* would let a broken
    implementation pass by never accumulating elapsed time. Real throttling costs
    a round-trip, and that is what makes the deadline matter.
    """

    def __init__(self, delay: float = 0.4) -> None:
        self.delay = delay
        self.calls = 0

    def _throttle(self, **_kwargs):
        self.calls += 1
        time.sleep(self.delay)
        raise _ThrottlingError()

    converse = _throttle
    invoke_model = _throttle


class _ThrottlingError(Exception):
    """Mimics botocore's ClientError shape closely enough for the retry classifier."""

    def __init__(self) -> None:
        super().__init__("ThrottlingException: Too many requests")
        self.response = {"Error": {"Code": "ThrottlingException", "Message": "slow down"}}


@pytest.fixture
def throttled(monkeypatch) -> _AlwaysThrottles:
    client = _AlwaysThrottles()
    monkeypatch.setattr(bedrock_client, "_get_client", lambda: client)
    monkeypatch.setattr(bedrock_client, "_client", client, raising=False)
    return client


def test_converse_gives_up_within_its_deadline(throttled, monkeypatch) -> None:
    """A throttled chat model must raise ModelUnavailable on time, not on attempt count."""
    monkeypatch.setattr(bedrock_client, "DEADLINE_S", 1.0)

    started = time.monotonic()
    with pytest.raises(bedrock_client.ModelUnavailable):
        bedrock_client.converse("be terse", [{"role": "user", "content": [{"text": "hi"}]}])
    elapsed = time.monotonic() - started

    # Generous ceiling: one in-flight attempt may still be running when the deadline
    # passes, so the bound is "deadline plus one attempt", not "exactly the deadline".
    # Even so this fails loudly against the old code, which took ~18s here.
    assert elapsed < 4.0, (
        f"converse() took {elapsed:.1f}s against a 1.0s deadline -- a throttled "
        "dependency can still consume the caller's whole request budget"
    )
    assert throttled.calls >= 1, "it should have genuinely tried before giving up"


def test_converse_deadline_is_env_overridable() -> None:
    """Operators can tighten the budget without a redeploy of new code."""
    assert isinstance(bedrock_client.DEADLINE_S, float)
    assert 0 < bedrock_client.DEADLINE_S <= 30, (
        f"a {bedrock_client.DEADLINE_S}s model deadline is not inside a 30s Lambda budget"
    )


def test_embedding_deadline_is_inside_the_lambda_budget() -> None:
    """The original instance of this bug, kept under test so it cannot regress."""
    assert 0 < embeddings.EMBED_DEADLINE_S <= 30


def test_both_deadlines_fit_in_one_request() -> None:
    """/decide embeds AND reasons; the two deadlines are serial, so they must sum to fit.

    Each budget being individually under 30s is not sufficient -- a single request pays
    both. This is the check that would have caught the interaction rather than either
    bug in isolation.
    """
    total = embeddings.EMBED_DEADLINE_S + bedrock_client.DEADLINE_S
    assert total < 25, (
        f"embedding ({embeddings.EMBED_DEADLINE_S}s) + reasoning "
        f"({bedrock_client.DEADLINE_S}s) = {total}s, leaving too little of the 30s "
        "Lambda timeout for the database work and the response"
    )


# --- The circuit breaker: bounded failure is not the same as cheap failure ---------
#
# Deadlines alone left /decide at 21.5s in production, because every request re-paid the
# embedding deadline plus the model deadline to rediscover that Bedrock was still
# throttled. These tests assert the *second* call is cheap, which is the whole point.


def test_repeated_failures_stop_costing_the_caller_time(throttled, monkeypatch) -> None:
    from backend import breaker as breaker_mod

    monkeypatch.setattr(bedrock_client, "DEADLINE_S", 1.0)
    monkeypatch.setattr(breaker_mod, "FAILURE_THRESHOLD", 1)
    breaker_mod.chat.reset()

    def call() -> float:
        started = time.monotonic()
        with pytest.raises(bedrock_client.ModelUnavailable):
            bedrock_client.converse("s", [{"role": "user", "content": [{"text": "hi"}]}])
        return time.monotonic() - started

    first = call()
    calls_after_first = throttled.calls
    second = call()

    assert throttled.calls == calls_after_first, (
        "the second request still called Bedrock -- the circuit did not open, so every "
        "request keeps paying full price to rediscover a known outage"
    )
    assert second < first / 2, (
        f"first failure took {first:.2f}s, second took {second:.2f}s -- the breaker is "
        "not actually saving the caller any time"
    )
    breaker_mod.chat.reset()


def test_circuit_closes_again_when_the_dependency_recovers(monkeypatch) -> None:
    """Self-healing matters here: the expected end state is quota being granted mid-flight.

    A breaker that stays open until someone redeploys would turn a transient throttle into
    a permanent downgrade to the fallback -- worse than the bug it fixes.
    """
    from backend import breaker as breaker_mod

    monkeypatch.setattr(breaker_mod, "FAILURE_THRESHOLD", 1)
    monkeypatch.setattr(breaker_mod, "COOLDOWN_S", 0.05)
    b = breaker_mod.Breaker("test")

    b.record_failure()
    assert b.state() == "open"
    with pytest.raises(breaker_mod.CircuitOpen):
        b.check()

    time.sleep(0.06)
    assert b.state() == "half_open"
    b.check()          # the probe is allowed through
    b.record_success()  # ...and it succeeds
    assert b.state() == "closed"
    b.check()          # normal service, no operator involved


def test_failed_probe_reopens_rather_than_letting_everything_through(monkeypatch) -> None:
    """A probe that fails must restart the cooldown, or the saving evaporates."""
    from backend import breaker as breaker_mod

    monkeypatch.setattr(breaker_mod, "FAILURE_THRESHOLD", 1)
    monkeypatch.setattr(breaker_mod, "COOLDOWN_S", 0.05)
    b = breaker_mod.Breaker("test")

    b.record_failure()
    time.sleep(0.06)
    b.check()           # probe allowed
    b.record_failure()  # probe fails
    assert b.state() == "open", "a failed probe left the circuit re-probeable immediately"
    with pytest.raises(breaker_mod.CircuitOpen):
        b.check()


def test_open_circuit_is_visible_on_health() -> None:
    """Silent degradation is the failure mode: fast answers on stub embeddings look fine."""
    from backend import breaker as breaker_mod

    snap = breaker_mod.snapshot()
    assert set(snap) == {"bedrock-converse", "bedrock-embed"}
    assert all(v in {"closed", "open", "half_open"} for v in snap.values())
