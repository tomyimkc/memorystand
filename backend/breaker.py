# SPDX-License-Identifier: Apache-2.0
"""A circuit breaker shared by the two Bedrock clients.

Deadlines stopped ``/decide`` hanging for the full 30s Lambda timeout, but they did not
make it *fast*: with Bedrock quota at zero, every single request still paid the embedding
deadline plus the model deadline in full before falling back -- 21.5s measured in
production, for a result that was never going to be anything but the deterministic
fallback. A bounded failure is not the same as a cheap one.

The missing observation is that the answer does not change between requests a second
apart. Once a dependency has failed repeatedly, the next call almost certainly fails too,
and paying full price to rediscover that is waste the caller experiences as latency.

So: after ``FAILURE_THRESHOLD`` consecutive failures the circuit opens and calls fail
immediately for ``COOLDOWN_S``. Then one request is allowed through as a probe -- if it
succeeds the circuit closes and normal service resumes, with no operator involvement and
no redeploy. That half-open probe is what makes this a breaker rather than a kill switch:
it is self-healing, which matters because the *expected* end state here is that quota gets
granted and the model starts working mid-flight.

Why per-process and not in the database: a breaker that needs a network round-trip to ask
whether the network is working has the failure mode built in. State is per warm Lambda
container -- imprecise across containers, and deliberately so. Each container learns for
itself within a few requests, and a cold one starts optimistic, which is the safe
direction to be wrong in: a container that wrongly believes the model is down degrades to
the fallback, and the fallback is always correct, just less smart.

There is no background timer and nothing to shut down; the clock is only read when a call
is attempted.
"""

from __future__ import annotations

import os
import threading
import time

FAILURE_THRESHOLD = int(os.environ.get("MEMORYSTAND_BREAKER_THRESHOLD", "2"))
COOLDOWN_S = float(os.environ.get("MEMORYSTAND_BREAKER_COOLDOWN_S", "60"))


class CircuitOpen(RuntimeError):
    """The circuit is open: the call was refused without being attempted.

    Callers translate this into their own typed unavailability error, so a caller of
    ``embed()`` or ``converse()`` never has to know a breaker exists -- it just sees the
    dependency being unavailable, which it already handles.
    """


class Breaker:
    """One breaker per dependency. Thread-safe; Lambda may reuse a container concurrently."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._lock = threading.Lock()
        self._consecutive_failures = 0
        self._opened_at: float | None = None
        self._probing = False

    def check(self) -> None:
        """Raise ``CircuitOpen`` if this call should be refused without being attempted."""
        with self._lock:
            if self._opened_at is None:
                return
            elapsed = time.monotonic() - self._opened_at
            if elapsed < COOLDOWN_S:
                raise CircuitOpen(
                    f"{self.name} circuit open after {self._consecutive_failures} consecutive "
                    f"failures; retrying in {COOLDOWN_S - elapsed:.0f}s. Refused without "
                    "calling, so this cost no time rather than the full deadline."
                )
            # Cooldown elapsed: let exactly one request through to probe. Others keep
            # failing fast until it reports back, so a recovering dependency is not hit
            # by the full backlog at once.
            if self._probing:
                raise CircuitOpen(
                    f"{self.name} circuit open; another request is already probing recovery"
                )
            self._probing = True

    def record_success(self) -> None:
        with self._lock:
            self._consecutive_failures = 0
            self._opened_at = None
            self._probing = False

    def record_failure(self) -> None:
        with self._lock:
            self._probing = False
            self._consecutive_failures += 1
            if self._consecutive_failures >= FAILURE_THRESHOLD:
                # Reset the clock on every failure at/after the threshold, including a
                # failed probe -- otherwise one probe failure would leave the circuit
                # instantly re-probeable and every subsequent request would pay full
                # price again, which is the exact cost this class exists to avoid.
                self._opened_at = time.monotonic()

    def state(self) -> str:
        """``closed`` | ``open`` | ``half_open`` -- for /health, so the breaker is observable."""
        with self._lock:
            if self._opened_at is None:
                return "closed"
            return "open" if time.monotonic() - self._opened_at < COOLDOWN_S else "half_open"

    def reset(self) -> None:
        """For tests, and for an operator who has just fixed the dependency."""
        with self._lock:
            self._consecutive_failures = 0
            self._opened_at = None
            self._probing = False


chat = Breaker("bedrock-converse")
embedding = Breaker("bedrock-embed")
standby = Breaker("standby-converse")


def snapshot() -> dict[str, str]:
    """Current state of every breaker, for the health endpoint."""
    return {b.name: b.state() for b in (chat, embedding, standby)}
