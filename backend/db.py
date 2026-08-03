# SPDX-License-Identifier: Apache-2.0
"""Connection handling and the serializable-retry contract.

CockroachDB offers SERIALIZABLE as its *only* isolation level, which means a transaction
can be aborted with SQLSTATE 40001 whenever the cluster detects it would violate
serializability. That is not an error condition to be avoided -- it is the documented
contract, and the application's job is to retry. Everything that writes goes through
``retry_serializable`` so the retry is uniform and observable rather than ad hoc.

Lambda note: RDS Proxy does not support CockroachDB, so pooling is done in-process. A
Lambda container serves one request at a time, so a pool of one connection is correct;
concurrency is bounded by Lambda's reserved concurrency, not by pool size.
"""

from __future__ import annotations

import os
import random
import time
from typing import Any, Callable, TypeVar

import psycopg2
from psycopg2 import errors as pg_errors
from psycopg2 import pool as pg_pool

T = TypeVar("T")

DSN_ENV = "STANDING_DSN"
FALLBACK_DSN_ENV = "COCKROACH_DSN"
SERIALIZATION_FAILURE = "40001"

# Retry policy. Deliberately small and bounded: an agent memory write that cannot commit
# after this many attempts is a signal worth surfacing, not something to hide behind an
# unbounded loop.
MAX_ATTEMPTS = 5
BASE_BACKOFF_S = 0.05
MAX_BACKOFF_S = 1.0

_pool: pg_pool.SimpleConnectionPool | None = None


class RetryBudgetExhausted(RuntimeError):
    """A transaction hit SQLSTATE 40001 more times than the retry budget allows."""


def dsn() -> str:
    value = os.environ.get(DSN_ENV) or os.environ.get(FALLBACK_DSN_ENV)
    if not value:
        raise RuntimeError(
            f"No connection string. Set {DSN_ENV} (or {FALLBACK_DSN_ENV}).\n"
            "Local dev:  export STANDING_DSN='postgresql://root@localhost:26257/defaultdb?sslmode=disable'\n"
            "Cloud:      ccloud cluster connection-string <cluster> --sql-user <user>"
        )
    return value


def _get_pool() -> pg_pool.SimpleConnectionPool:
    global _pool
    if _pool is None:
        # maxconn=1: see module docstring. minconn=0 so an idle container holds nothing.
        _pool = pg_pool.SimpleConnectionPool(0, 1, dsn())
    return _pool


def get_conn():
    """Borrow the pooled connection. Callers must return it with ``put_conn``."""
    return _get_pool().getconn()


def put_conn(conn) -> None:
    if _pool is not None:
        _pool.putconn(conn)


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.closeall()
        _pool = None


def retry_serializable(fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Run ``fn(conn, *args, **kwargs)`` inside one transaction, retrying on 40001.

    ``fn`` receives an open connection and must not commit or roll back -- this function
    owns the transaction boundary, because a retry has to replay the *whole* unit of work
    to be correct. ``fn`` may be called more than once and must therefore be free of
    side effects outside the database.

    Returns whatever ``fn`` returns. Raises ``RetryBudgetExhausted`` if the conflict does
    not clear within ``MAX_ATTEMPTS``.
    """
    conn = get_conn()
    try:
        last_error: Exception | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                result = fn(conn, *args, **kwargs)
                conn.commit()
                if attempt > 1:
                    # Surfaced rather than swallowed: retries are a real operational signal.
                    _record_retry(attempt - 1)
                return result
            except pg_errors.SerializationFailure as exc:
                conn.rollback()
                last_error = exc
                if attempt == MAX_ATTEMPTS:
                    break
                # Full jitter: correlated retries are what turn one conflict into many.
                backoff = min(MAX_BACKOFF_S, BASE_BACKOFF_S * (2 ** (attempt - 1)))
                time.sleep(random.uniform(0, backoff))
            except Exception:
                conn.rollback()
                raise
        raise RetryBudgetExhausted(
            f"transaction still conflicting after {MAX_ATTEMPTS} attempts (SQLSTATE {SERIALIZATION_FAILURE})"
        ) from last_error
    finally:
        put_conn(conn)


# Observability hook. The load/race scripts read this to report retry counts honestly
# instead of asserting that retries "would" happen.
_retry_observations: list[int] = []


def _record_retry(count: int) -> None:
    _retry_observations.append(count)


def retries_observed() -> int:
    return sum(_retry_observations)


def reset_retry_observations() -> None:
    _retry_observations.clear()


def server_version(conn=None) -> str:
    own = conn is None
    conn = conn or get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT version()")
            return cur.fetchone()[0]
    finally:
        if own:
            put_conn(conn)


def is_serialization_failure(exc: BaseException) -> bool:
    return getattr(exc, "pgcode", None) == SERIALIZATION_FAILURE


__all__ = [
    "RetryBudgetExhausted",
    "close_pool",
    "dsn",
    "get_conn",
    "is_serialization_failure",
    "put_conn",
    "reset_retry_observations",
    "retries_observed",
    "retry_serializable",
    "server_version",
    "psycopg2",
]
