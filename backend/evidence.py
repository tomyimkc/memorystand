# SPDX-License-Identifier: Apache-2.0
"""Independently re-check an outcome claim against the external system of record.

This module exists because the project's central claim was, until now, only half
implemented. ``trust.py`` enforced two of its three parts:

  * the promotion path makes zero model calls -- structurally true, asserted at runtime;
  * a model's own opinion is not an admissible ``source`` -- enforced at the schema level.

The third part -- *that the external signal is real* -- was not enforced at all. ``_validate``
checked that ``source`` was in an allow-list and that ``external_ref`` was a non-empty string,
and then believed it. Anyone holding the shared secret could grant standing by naming an
incident that never happened. The headline sentence was about exactly the half that was
missing, which is the kind of gap this project is supposed to be against.

So: for evidence that CAN be re-checked, this module re-checks it, and the memory is promoted
to ``verified`` only if the external system of record agrees. Evidence that cannot be
re-checked is recorded honestly as ``attested`` -- a real outcome was reported, but nobody
independently confirmed it. That distinction is the point. It is the difference between "a
human told us it worked" and "we asked CloudWatch and CloudWatch agreed".

**Why calling CloudWatch does not violate the zero-model-calls claim.** The claim is not "this
path makes no network calls" -- that would be a strange thing to want, since the whole thesis
is that trust must come from *outside*. The claim is that no *model* participates in deciding
whether a memory is true. Asking a metrics store what a number actually did is the thesis
working as intended; asking an LLM whether a memory seems right is the thing being refused.
``trust.assert_no_model_calls`` is extended to police that distinction directly rather than
banning AWS clients wholesale.

Currently verifiable:
  * ``source='metric'`` -- against Amazon CloudWatch.

Not currently verifiable, and reported as such rather than papered over:
  * ``source='pagerduty'`` -- needs a PagerDuty API token this deployment does not have.
  * ``source='human'``     -- a human sign-off is attestation by definition. There is no
    system of record to re-query, and pretending otherwise would be theatre.
"""

from __future__ import annotations

import datetime as _dt
import os
import re
from dataclasses import dataclass
from typing import Any

# How far either side of the decision to sample, and how much disagreement is tolerated
# between the claimed delta and the observed one.
WINDOW_MINUTES = int(os.environ.get("MEMORYSTAND_EVIDENCE_WINDOW_MINUTES", "15"))
TOLERANCE = float(os.environ.get("MEMORYSTAND_EVIDENCE_TOLERANCE", "0.5"))
MIN_DATAPOINTS = int(os.environ.get("MEMORYSTAND_EVIDENCE_MIN_DATAPOINTS", "3"))
# Deliberately AWS_REGION, NOT MEMORYSTAND_BEDROCK_REGION. CloudWatch metrics exist only in
# the region that emitted them, so pointing the evidence checker at the model's region would
# return "no datapoints" for every query and silently downgrade every outcome to `attested` --
# a verification system that quietly stops verifying. The model region and the truth region
# are different things and must stay different variables.
REGION = os.environ.get("AWS_REGION", "us-west-2")

# Verification outcomes. These are deliberately four values, not a boolean: "we checked and
# it agreed", "we checked and it disagreed", "we could not check", and "this kind of evidence
# is not checkable" are four genuinely different epistemic states, and collapsing them into
# true/false is how a system ends up claiming more than it knows.
CONFIRMED = "confirmed"
CONTRADICTED = "contradicted"
UNAVAILABLE = "unavailable"
NOT_VERIFIABLE = "not_verifiable"
# The metric is real and moved as claimed, but it is not a metric ABOUT the thing the memory
# is about. Found by benchmarks/poisoning_benchmark.py, which showed this was a clean bypass:
# take a genuine, independently-confirmable improvement, file it under a different service, and
# it reached `verified` every time and then outranked honest memories. Verifying that a number
# moved is not the same as verifying it moved for the subject of the claim.
ENTITY_MISMATCH = "entity_mismatch"
ENTITY_UNBOUND = "entity_unbound"

_client: Any = None


@dataclass(frozen=True)
class Verification:
    """What the external system of record said, and how much weight it can carry."""

    status: str
    detail: str
    observed: float | None = None
    claimed: float | None = None

    @property
    def grants_verified_tier(self) -> bool:
        """Only an independently confirmed claim may reach the top of the trust ladder."""
        return self.status == CONFIRMED

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "detail": self.detail,
            "observed": self.observed,
            "claimed": self.claimed,
        }


# `Namespace|MetricName|Dim=Value,Dim=Value` -- e.g.
#   AWS/Lambda|Errors|FunctionName=memorystand
# Deliberately a compact single string: external_ref is one column, and an operator pasting
# an incident id into the same field for a PagerDuty outcome should not have to learn a
# different request shape.
_METRIC_REF = re.compile(r"\A(?P<ns>[A-Za-z0-9/_.-]+)\|(?P<metric>[A-Za-z0-9_.-]+)(\|(?P<dims>.+))?\Z")


def _get_client() -> Any:
    global _client
    if _client is None:
        import boto3  # imported lazily so local tests need no AWS SDK

        _client = boto3.client("cloudwatch", region_name=REGION)
    return _client


def _parse_dims(raw: str | None) -> list[dict[str, str]]:
    if not raw:
        return []
    dims = []
    for pair in raw.split(","):
        if "=" not in pair:
            continue
        name, _, value = pair.partition("=")
        dims.append({"Name": name.strip(), "Value": value.strip()})
    return dims


def _entity_matches(entity: str | None, external_ref: str) -> bool:
    """Is this metric plausibly ABOUT the entity the memory concerns?

    Identity must be carried by an exact CloudWatch dimension value. A containment check over the
    whole reference let `payments` claim a metric for `payments-canary`, and an entityless claim
    could be promoted without binding the evidence to any subject at all. Comparison remains
    case- and separator-insensitive so `payments-service` and `Payments_Service` are equivalent.
    """
    if not entity:
        return False
    match = _METRIC_REF.match(external_ref.strip())
    if not match:
        return False
    norm = lambda t: t.lower().replace("-", "").replace("_", "").replace(" ", "")
    expected = norm(entity)
    return any(norm(dim["Value"]) == expected for dim in _parse_dims(match.group("dims")))


def entity_matches(entity: str | None, external_ref: str) -> bool:
    """Public alias for ``_entity_matches``.

    ``trust._apply`` needs this on the promotion path to decide which produced memories a single
    verified metric actually vouches for; reaching into a private helper on a trust-critical path
    is worth one line to avoid.
    """
    return _entity_matches(entity, external_ref)


def verify(
    source: str,
    external_ref: str,
    claimed_delta: float | None,
    decided_at: _dt.datetime | None,
    entity: str | None = None,
) -> Verification:
    """Re-check an outcome claim. Never raises -- an unverifiable claim is a result, not an error.

    A thrown exception here would either take down the promotion path (bad: the outcome is
    real even if our check failed) or get swallowed into a silent pass (worse: an unverified
    claim promoted as if verified). Returning a status forces the caller to decide explicitly.
    """
    if source != "metric":
        return Verification(
            NOT_VERIFIABLE,
            f"source={source!r} has no machine-checkable system of record wired up in this "
            "deployment; the outcome is recorded as attested, not independently verified",
        )

    match = _METRIC_REF.match(external_ref.strip())
    if not match:
        return Verification(
            NOT_VERIFIABLE,
            f"external_ref {external_ref!r} is not a CloudWatch metric reference. Expected "
            "'Namespace|MetricName' or 'Namespace|MetricName|Dim=Value,...' "
            "(e.g. 'AWS/Lambda|Errors|FunctionName=memorystand')",
        )

    if claimed_delta is None:
        return Verification(NOT_VERIFIABLE, "source='metric' with no metric_delta to check")

    if not entity:
        return Verification(
            ENTITY_UNBOUND,
            "the produced memory has no entity, so this metric cannot be bound to the subject "
            "of the claim. The outcome may be attested, but it cannot grant verified standing.",
            claimed=claimed_delta,
        )

    # Check the SUBJECT before checking the number. A real improvement attached to the wrong
    # service is the most dangerous input this function sees: every downstream check passes,
    # the memory reaches `verified`, and it then outranks honest memories about the service it
    # was misfiled under. Confirming that a metric moved says nothing about whose metric it is.
    if not _entity_matches(entity, external_ref):
        return Verification(
            ENTITY_MISMATCH,
            f"the memory concerns {entity!r} but the evidence is {external_ref!r}, which does "
            "not identify that entity. The metric may well have moved exactly as claimed -- it "
            "is not evidence about this subject, so it cannot grant this memory standing.",
            claimed=claimed_delta,
        )

    anchor = decided_at or _dt.datetime.now(_dt.timezone.utc)
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=_dt.timezone.utc)
    window = _dt.timedelta(minutes=WINDOW_MINUTES)

    try:
        client = _get_client()
        dims = _parse_dims(match.group("dims"))
        before = _average(client, match.group("ns"), match.group("metric"), dims, anchor - window, anchor)
        after = _average(client, match.group("ns"), match.group("metric"), dims, anchor, anchor + window)
    except Exception as exc:  # noqa: BLE001 - an unreachable CloudWatch must not grant OR deny
        return Verification(
            UNAVAILABLE,
            f"could not reach CloudWatch to check {external_ref!r}: {type(exc).__name__}. "
            "The outcome is recorded as attested; no memory is promoted to verified on an "
            "unchecked claim.",
        )

    if before is None or after is None:
        return Verification(
            UNAVAILABLE,
            f"CloudWatch returned fewer than {MIN_DATAPOINTS} usable datapoints for "
            f"{external_ref!r} in one or both {WINDOW_MINUTES}-minute windows around the "
            "decision, so the claimed change cannot be confirmed or denied",
            claimed=claimed_delta,
        )

    observed = after - before

    # Direction first, magnitude second. A claim of "latency fell 40ms" that CloudWatch shows
    # RISING is wrong in the way that matters, regardless of how far off the number is.
    if claimed_delta != 0 and observed != 0 and (claimed_delta > 0) != (observed > 0):
        return Verification(
            CONTRADICTED,
            f"claimed a change of {claimed_delta:+.4g} but CloudWatch shows {observed:+.4g} "
            f"for {external_ref!r} -- opposite direction. The outcome is refused: a memory "
            "must not gain standing from a claim the system of record disagrees with.",
            observed=observed,
            claimed=claimed_delta,
        )

    scale = max(abs(claimed_delta), abs(observed), 1e-9)
    if abs(observed - claimed_delta) / scale > TOLERANCE:
        return Verification(
            CONTRADICTED,
            f"claimed {claimed_delta:+.4g} but CloudWatch shows {observed:+.4g} for "
            f"{external_ref!r}, a disagreement beyond the {TOLERANCE:.0%} tolerance",
            observed=observed,
            claimed=claimed_delta,
        )

    return Verification(
        CONFIRMED,
        f"CloudWatch independently corroborates that {external_ref!r} moved {observed:+.4g} "
        f"against a claimed {claimed_delta:+.4g}, within tolerance. This is evidence of a "
        "consistent monitored outcome, not proof that the action alone caused the change.",
        observed=observed,
        claimed=claimed_delta,
    )


def _average(client, namespace: str, metric: str, dims, start, end) -> float | None:
    resp = client.get_metric_statistics(
        Namespace=namespace,
        MetricName=metric,
        Dimensions=dims,
        StartTime=start,
        EndTime=end,
        Period=60,
        Statistics=["Average"],
    )
    points = resp.get("Datapoints") or []
    if len(points) < MIN_DATAPOINTS:
        return None
    return sum(p["Average"] for p in points) / len(points)
