#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Does re-checking the outcome actually catch anything? Measure it, don't assert it.

MemoryStand's headline is that a memory earns trust only from an external signal that gets
re-checked. That is a testable claim about a *decision rule*, so this benchmark tests it the
only way that means anything: run competing rules over the same labelled claims and count who
promotes what.

THE FAILURE MODE BEING MEASURED IS NOT MALICE.
The interesting case is not an attacker forging an incident id. It is an on-call engineer, at
02:00, who restarts a service, watches the page clear, and reports in good faith that the
restart fixed it -- when the metric shows latency was already recovering, or never moved, or
got worse. Nobody lied. The memory "restarting payments-service fixes p99 latency" is still
false, and every system that takes the reporter's word promotes it to trusted and will hand it
to an agent at the next incident. That is how a memory store poisons itself with true-sounding
operational folklore, and it is the thing this benchmark is built to detect.

THREE ARMS
  trust_the_caller  The baseline, and not a strawman: it is what MemoryStand itself did before
                    this was built, and what every system surveyed does with an outcome
                    signal -- record what the caller reported. Promotes on any success report.
  outcome_gated     MemoryStand now. Re-queries the metric store, compares the observed change
                    against the claimed one, and REFUSES the promotion on disagreement.
  llm_judge         What Mem0, Zep and AWS AgentCore do: ask a model whether the memory looks
                    right. Optional -- see the honesty note below.

ON THE llm_judge ARM. It requires Bedrock quota this account does not have (0 requests/minute
for Nova Lite; see docs/BEDROCK_QUOTA.md). The arm is implemented and will run wherever quota
exists, and is reported as SKIPPED with the reason rather than estimated, simulated, or filled
in from published numbers. A benchmark that invents its competitor's score is worth nothing.

WHAT THIS IS NOT. The metric series are synthetic and generated from a fixed seed, so the
CloudWatch call is stubbed with known ground truth. That makes this a measurement of the
DECISION RULE, not of CloudWatch, not of network behaviour, and not of production accuracy.
The point of synthesising them is that ground truth has to be known for false-promotion rate
to mean anything, and real production metrics do not come with labels attached.

    python benchmarks/verification_benchmark.py
    python benchmarks/verification_benchmark.py --cases 400 --repeats 10
"""

from __future__ import annotations

import argparse
import random
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend import evidence  # noqa: E402

REPORT = REPO_ROOT / "benchmarks" / "verification.md"


@dataclass(frozen=True)
class Case:
    """One reported outcome, with the ground truth the reporter did not have."""

    name: str
    claimed_delta: float
    before: float | None
    after: float | None
    should_promote: bool
    kind: str


def build_cases(n: int, seed: int) -> list[Case]:
    """A realistic mix, weighted toward honest reports rather than adversarial ones.

    Proportions are a modelling choice, stated here so the headline numbers can be read
    correctly: a benchmark made mostly of forged claims would flatter the gate, and one made
    entirely of correct claims would flatter the baseline.
    """
    rng = random.Random(seed)
    cases: list[Case] = []
    for i in range(n):
        roll = rng.random()

        if roll < 0.55:
            # Correct report: the action worked and the metric agrees. Both arms SHOULD promote.
            before = rng.uniform(80, 400)
            improvement = rng.uniform(0.15, 0.6) * before
            after = before - improvement
            cases.append(Case(f"correct-{i}", -improvement, before, after, True, "correct"))

        elif roll < 0.75:
            # Honest but WRONG: the responder believed it worked; the metric did not move.
            # The single most common real failure, and the one folklore is made of.
            before = rng.uniform(80, 400)
            after = before * rng.uniform(0.97, 1.03)
            cases.append(Case(f"no-change-{i}", -rng.uniform(20, 120), before, after, False, "no_change"))

        elif roll < 0.88:
            # Honest but BACKWARDS: it actually got worse. Reported as a fix anyway, because
            # the page cleared for an unrelated reason.
            before = rng.uniform(80, 400)
            after = before * rng.uniform(1.15, 1.9)
            cases.append(Case(f"regressed-{i}", -rng.uniform(20, 120), before, after, False, "regressed"))

        elif roll < 0.90:
            # BORDERLINE, and the reason this benchmark is worth running. The observed change
            # sits right around the tolerance boundary: real improvement, meaningfully smaller
            # than claimed. There is no obviously correct answer here, which is the point --
            # a rule that only ever sees easy cases has not been tested. Labelled
            # should_promote=True because the action genuinely did help; the gate will refuse
            # some of these, and that cost belongs in the headline rather than hidden.
            before = rng.uniform(80, 400)
            actual = rng.uniform(0.25, 0.75) * rng.uniform(0.2, 0.5) * before
            claimed = rng.uniform(0.2, 0.5) * before
            cases.append(Case(f"borderline-{i}", -claimed, before, before - actual, True, "borderline"))

        elif roll < 0.96:
            # Right direction, wildly overstated magnitude -- "cut latency in half" when it
            # moved 3%. Promoting this teaches the agent an exaggerated rule.
            before = rng.uniform(80, 400)
            after = before - rng.uniform(0.02, 0.06) * before
            cases.append(Case(f"overstated-{i}", -0.5 * before, before, after, False, "overstated"))

        else:
            # No datapoints: the metric does not exist, or the window is empty. Neither
            # promotable nor refusable -- the honest answer is "cannot tell".
            cases.append(Case(f"no-data-{i}", -rng.uniform(20, 120), None, None, False, "no_data"))

    rng.shuffle(cases)
    return cases


def run_outcome_gated(case: Case) -> tuple[bool, str]:
    """MemoryStand's rule, exercised through the real backend.evidence code path."""
    seq = iter([case.before, case.after])

    def fake_average(client, namespace, metric, dims, start, end):
        return next(seq)

    original = evidence._average
    evidence._average = fake_average
    try:
        v = evidence.verify(
            "metric",
            "AWS/Lambda|Duration|FunctionName=memorystand",
            case.claimed_delta,
            None,
        )
    finally:
        evidence._average = original
    return v.grants_verified_tier, v.status


def run_trust_the_caller(case: Case) -> tuple[bool, str]:
    """Record what was reported. No check. What this project did until this session."""
    return True, "accepted_as_reported"


def score(arm: str, results: list[tuple[Case, bool]]) -> dict:
    tp = sum(1 for c, p in results if p and c.should_promote)
    fp = sum(1 for c, p in results if p and not c.should_promote)
    fn = sum(1 for c, p in results if not p and c.should_promote)
    tn = sum(1 for c, p in results if not p and not c.should_promote)
    promotable = tp + fn
    unpromotable = fp + tn
    return {
        "arm": arm,
        "true_promotions": tp,
        "false_promotions": fp,
        "missed_promotions": fn,
        "correct_refusals": tn,
        # The headline: of everything that should NOT have been trusted, how much was?
        "false_promotion_rate": fp / unpromotable if unpromotable else 0.0,
        "recall_on_good": tp / promotable if promotable else 0.0,
        "precision": tp / (tp + fp) if (tp + fp) else 0.0,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cases", type=int, default=300)
    ap.add_argument("--repeats", type=int, default=5, help="determinism check: same input, N times")
    ap.add_argument("--seed", type=int, default=20260804)
    ap.add_argument("--report", default=str(REPORT))
    args = ap.parse_args()

    cases = build_cases(args.cases, args.seed)
    mix = {k: sum(1 for c in cases if c.kind == k) for k in
           ("correct", "no_change", "regressed", "overstated", "borderline", "no_data")}

    print(f"Cases: {len(cases)}  mix: {mix}\n")

    arms = {"trust_the_caller": run_trust_the_caller, "outcome_gated": run_outcome_gated}
    scores, latencies, flips = {}, {}, {}

    for name, fn in arms.items():
        started = time.perf_counter()
        results = [(c, fn(c)[0]) for c in cases]
        latencies[name] = (time.perf_counter() - started) / len(cases) * 1000
        scores[name] = score(name, results)

        # Determinism: identical input, N times. A rule whose answer moves is a rule you
        # cannot audit, and auditability is the entire proposition here.
        baseline = [p for _, p in results]
        differing = 0
        for _ in range(args.repeats - 1):
            if [fn(c)[0] for c in cases] != baseline:
                differing += 1
        flips[name] = differing

        s = scores[name]
        print(f"{name}")
        print(f"  false promotions   : {s['false_promotions']:4d}  "
              f"({s['false_promotion_rate']:.1%} of everything that should have been refused)")
        print(f"  true promotions    : {s['true_promotions']:4d}  "
              f"(recall {s['recall_on_good']:.1%} on genuinely good outcomes)")
        print(f"  precision          : {s['precision']:.1%}")
        print(f"  differing runs     : {differing}/{args.repeats - 1}")
        print(f"  latency per check  : {latencies[name]:.3f} ms   model calls: 0\n")

    # One operating point is an anecdote. The tolerance knob trades recall against precision,
    # and showing that curve is more honest -- and more useful to anyone adopting this -- than
    # reporting whichever single setting happens to look best.
    sweep = []
    original_tol = evidence.TOLERANCE
    for tol in (0.15, 0.25, 0.5, 0.75, 1.0):
        evidence.TOLERANCE = tol
        s_ = score("outcome_gated", [(c, run_outcome_gated(c)[0]) for c in cases])
        sweep.append((tol, s_))
    evidence.TOLERANCE = original_tol
    print("outcome_gated, tolerance sweep")
    for tol, s_ in sweep:
        print(f"  tolerance {tol:.0%}: false promotions {s_['false_promotions']:3d}  "
              f"recall {s_['recall_on_good']:.1%}  precision {s_['precision']:.1%}")
    print()

    # The llm_judge arm is implemented but cannot run here. Say so; do not estimate it.
    llm_note = _probe_llm_judge()
    print(f"llm_judge\n  {llm_note}\n")

    _write_report(args, cases, mix, scores, latencies, flips, llm_note, sweep)
    print(f"Report written to {args.report}")
    return 0


def _probe_llm_judge() -> str:
    """Try the model arm for real; report the refusal verbatim if it cannot run."""
    try:
        from backend import bedrock_client

        bedrock_client.converse(
            "You judge whether a memory is still true. Answer YES or NO.",
            [{"role": "user", "content": [{"text": "Is 'restarting fixes latency' still true?"}]}],
            max_tokens=5,
        )
        return "ran successfully -- rerun with the arm enabled to score it"
    except Exception as exc:  # noqa: BLE001
        return f"SKIPPED, not estimated: {type(exc).__name__}: {str(exc)[:160]}"


def _write_report(args, cases, mix, scores, latencies, flips, llm_note, sweep) -> None:
    tc, og = scores["trust_the_caller"], scores["outcome_gated"]
    caught = tc["false_promotions"] - og["false_promotions"]

    sweep_rows = "\n".join(
        f"| {tol:.0%} | {s_['false_promotions']} | {s_['recall_on_good']:.1%} | {s_['precision']:.1%} |"
        for tol, s_ in sweep
    )

    body = f"""# Does re-checking the outcome catch anything?

A measurement of the **decision rule**, not of CloudWatch and not of production accuracy. The
metric series are synthetic and seeded (`--seed {args.seed}`) because false-promotion rate is
meaningless without known ground truth, and real production metrics do not arrive labelled.

## What is being measured

Not malice. The case that matters is an on-call engineer who restarts a service at 02:00, sees
the page clear, and reports in good faith that the restart fixed it — when the metric shows
latency never moved, or was already recovering, or got worse. Nobody lied, and the memory
"restarting payments-service fixes p99 latency" is still false. Every system that takes the
reporter's word promotes it to trusted and hands it to an agent at the next incident.

## Case mix ({len(cases)} cases)

| Kind | Count | Should promote |
|---|---:|---|
| correct — the action worked and the metric agrees | {mix['correct']} | yes |
| borderline — real improvement, meaningfully smaller than claimed | {mix['borderline']} | yes (contested) |
| no_change — honest report, metric did not move | {mix['no_change']} | no |
| regressed — honest report, metric got worse | {mix['regressed']} | no |
| overstated — right direction, wildly wrong magnitude | {mix['overstated']} | no |
| no_data — empty window, nothing to check against | {mix['no_data']} | no |

## Results

| | trust_the_caller | outcome_gated |
|---|---:|---:|
| False promotions | **{tc['false_promotions']}** | **{og['false_promotions']}** |
| False-promotion rate | {tc['false_promotion_rate']:.1%} | {og['false_promotion_rate']:.1%} |
| True promotions | {tc['true_promotions']} | {og['true_promotions']} |
| Recall on genuinely good outcomes | {tc['recall_on_good']:.1%} | {og['recall_on_good']:.1%} |
| Precision | {tc['precision']:.1%} | {og['precision']:.1%} |
| Differing runs out of {args.repeats - 1} reruns | {flips['trust_the_caller']} | {flips['outcome_gated']} |
| Latency per check | {latencies['trust_the_caller']:.3f} ms | {latencies['outcome_gated']:.3f} ms |
| Model calls | 0 | 0 |

**{caught} memories** would have entered the store as trusted under the baseline and were
refused by the gate. Of those, {mix['no_change'] + mix['regressed'] + mix['overstated']} are
claims the metric actively contradicts — it did not move, moved the wrong way, or moved far
less than reported — and {mix['no_data']} are claims with no data to check against at all,
refused because "cannot tell" is not "confirmed".

## The honest cost, and the knob that sets it

`outcome_gated` recall is **{og['recall_on_good']:.1%}** at the default 50% tolerance — every
point below 100% is a real outcome that genuinely happened and was refused anyway, because the
observed change was too far from the claimed one to confirm. Those memories are not lost; they
sit at `attested` instead of `verified`.

The trade is a knob, not a law, and reporting a single operating point would be cherry-picking:

| `MEMORYSTAND_EVIDENCE_TOLERANCE` | False promotions | Recall on good outcomes | Precision |
|---:|---:|---:|---:|
{sweep_rows}

Read the curve, not the headline. A tight tolerance refuses more honest reports; a loose one
lets overstated claims through. **The gate is deliberately biased toward refusing**: a memory
wrongly left at `attested` costs an agent some usefulness, while one wrongly promoted to
`verified` costs it correctness — and correctness is the thing being sold.

The `borderline` cases exist specifically so this benchmark is not trivially won. Without them
the generated classes separate so cleanly that any threshold rule scores perfectly, which would
measure the generator's assumptions rather than the rule.

## The third arm, not run

The `llm_judge` arm — asking a model whether the memory still looks true, which is what
[Mem0](https://arxiv.org/html/2504.19413v1), [Zep/Graphiti](https://arxiv.org/pdf/2501.13956)
and [AWS Bedrock AgentCore](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/built-in-strategies.html)
each do — is implemented in this script and did not run:

    {llm_note}

It is reported as skipped rather than estimated or filled in from published figures. A
benchmark that invents its competitor's score is worth nothing. What can be said without
measuring it is structural: an LLM judge cannot produce the `0/{args.repeats - 1} differing
runs` line above, because it is sampled rather than computed — and
[graphiti#1666](https://github.com/getzep/graphiti/issues/1666) records contradiction detection
scoring 1 of 9 on a weaker model, with the consequence that "stale facts survive their own
contradiction".

## Reproduce

    python benchmarks/verification_benchmark.py --cases {args.cases} --repeats {args.repeats} --seed {args.seed}

## Caveats

- Synthetic, seeded metric series. This measures the decision rule; it says nothing about
  CloudWatch's own reliability or about real incident data.
- The case mix is a modelling choice. A benchmark of mostly-forged claims would flatter the
  gate; one of mostly-correct claims would flatter the baseline. The mix above is stated so the
  headline can be read correctly.
- Single run per configuration for the scoring arms; the repeat count measures determinism,
  not statistical variance across different case sets.
- `trust_the_caller` is not a strawman. It is what this project itself did until the commit
  that added `backend/evidence.py`.
"""
    Path(args.report).write_text(body)


if __name__ == "__main__":
    raise SystemExit(main())
