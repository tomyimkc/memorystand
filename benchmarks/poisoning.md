# Does re-checking the outcome catch an ATTACKER, not just an honest mistake?

A companion to `benchmarks/verification.md`, which measures honest-mistake noise. This one
measures deliberate poisoning: someone reporting an outcome that never happened, in order to
install a false memory a future agent will act on. Same trust ladder, controlled comparison
arms, adversarial input.

`--seed 20260805 --cases 60 --climb-repeats 5`

## The five attack classes (600 attempts total)

| Attack class | What it tries to achieve | Attempts |
|---|---|---:|
| fabricated_incident | A PagerDuty ticket or human sign-off for an incident that never happened. | 60 |
| metric_lie | A metric claim whose real CloudWatch series contradicts it outright. | 60 |
| magnitude_inflation | Right direction, wildly overstated magnitude. | 60 |
| tier_climb | The same false claim, resubmitted many times to accumulate standing. | 300 |
| wrong_entity | A REAL, verifiable outcome filed under the wrong service. | 60 |
| control_honest | NOT AN ATTACK. A genuine remediation, correctly attributed, whose metric really moved as claimed. Every arm SHOULD admit these -- an arm that does not is rejecting good evidence, and its attack numbers are meaningless without this column. | 60 |

## Results, by arm

For each class: how many of the 60-ish attempts per class reached
`verified`, how many reached only `attested`, how many were refused outright (never left
`unconfirmed`), and the two autonomous-action tests described below.

### `trust_the_caller`

| Attack class | n | Reached verified | Reached attested | Refused outright | Autonomously drives action when alone | Autonomously beats an honest verified competitor |
|---|---:|---:|---:|---:|---:|---:|
| fabricated_incident | 60 | 60 (100%) | 0 (0%) | 0 (0%) | 100% | 100% |
| metric_lie | 60 | 60 (100%) | 0 (0%) | 0 (0%) | 100% | 100% |
| magnitude_inflation | 60 | 60 (100%) | 0 (0%) | 0 (0%) | 100% | 100% |
| tier_climb | 300 | 300 (100%) | 0 (0%) | 0 (0%) | 100% | 100% |
| wrong_entity | 60 | 60 (100%) | 0 (0%) | 0 (0%) | 100% | 100% |
| control_honest | 60 | 60 (100%) | 0 (0%) | 0 (0%) | 100% | 100% |

### `outcome_gated`

| Attack class | n | Reached verified | Reached attested | Refused outright | Autonomously drives action when alone | Autonomously beats an honest verified competitor |
|---|---:|---:|---:|---:|---:|---:|
| fabricated_incident | 60 | 0 (0%) | 60 (100%) | 0 (0%) | 0% | 0% |
| metric_lie | 60 | 0 (0%) | 0 (0%) | 60 (100%) | n/a | 0% |
| magnitude_inflation | 60 | 0 (0%) | 0 (0%) | 60 (100%) | n/a | 0% |
| tier_climb | 300 | 0 (0%) | 300 (100%) | 0 (0%) | 0% | 0% |
| wrong_entity | 60 | 0 (0%) | 0 (0%) | 60 (100%) | n/a | 0% |
| control_honest | 60 | 60 (100%) | 0 (0%) | 0 (0%) | 100% | 100% |

**"Autonomously drives action when alone"** feeds each admitted memory through
`backend.agent._fallback_decision` and counts it only when the memory changes the baseline
action **without** a human-approval hold. `verified` is action-authoritative. `attested` is
advisory and always held for approval. `unconfirmed` and `disputed` do not steer an action.

**"Autonomously beats an honest, verified competitor"** is the harder test: put the poisoned
memory against a memory for the same entity that genuinely reached `verified`, then count a
poisoning success only when the poisoned memory is the cited basis for an action that is not
held for approval. Ties between verified memories are ordered in the attacker's favour.

## What `outcome_gated` admits, refuses, and authorizes

Stated plainly, because refusal-only defenses can look perfect while rejecting useful evidence:

- **`wrong_entity` reaches `verified` 0% of the time under
  `outcome_gated`**, is refused outright 100% of the time, and
  autonomously beats an honest, correctly-attributed `verified` memory
  0% of the time. The verifier binds the claim to an exact normalized
  CloudWatch dimension value; a real improvement on `checkout-api` cannot validate a memory
  about `payments-service`.
- **`fabricated_incident` reaches `attested` 100% of the time**, because nothing
  in this deployment can re-query PagerDuty or ask the named human whether they actually said
  that (see `backend/evidence.py`'s own docstring on this gap). It does not reach `verified`,
  autonomously acts when alone 0% of the time, and autonomously beats an
  honest `verified` competitor 0% of the time. It remains inspectable as a
  reported outcome, but can only produce a recommendation held for human approval.
- **`metric_lie` and `magnitude_inflation` are refused outright** (100% and
  100% respectively) -- this is the gate working as designed, included here so the
  classes it stops and the ones it does not are visible side by side rather than cherry-picked.

## tier_climb: does repetition buy standing a single attempt does not?

Each family below is the SAME false claim, resubmitted `5` times as if
against `5` separate decisions.

| Arm | Families | Families whose tier rose between attempt 1 and the last attempt | Families that ever reached `verified` |
|---|---:|---:|---:|
| trust_the_caller | 60 | 0 | 60 |
| outcome_gated | 60 | 0 | 0 |

Under `outcome_gated`, repetition buys nothing: each attempt is checked independently against
the same unreachable system of record, so every attempt lands at the same tier as the first.
What repetition does buy the attacker is breadth -- `5`x as many `attested`
memories echoing the same false claim in the store. That is a review and storage cost, but the
attested copies cannot autonomously steer an action.

## Does it survive a different draw?

The whole attack set, regenerated under independent seeds, scored against `outcome_gated`.
Every class's admission rate is a property of how the class is CONSTRUCTED (does the claim
match reality or not), not of the seed -- so an identical row across seeds is the expected,
correct result, not a coincidence to be suspicious of.

| seed | fabricated_incident | metric_lie | magnitude_inflation | tier_climb | wrong_entity | control_honest |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 100% | 0% | 0% | 100% | 0% | 100% |
| 7 | 100% | 0% | 0% | 100% | 0% | 100% |
| 42 | 100% | 0% | 0% | 100% | 0% | 100% |
| 1234 | 100% | 0% | 0% | 100% | 0% | 100% |
| 20260805 | 100% | 0% | 0% | 100% | 0% | 100% |

Determinism (identical case set, rerun): **True**.

## The third arm

The optional `llm_judge` arm asks the configured reasoning model whether a claim looks
plausible based only on the submitted fields. It is a local baseline implemented by this
benchmark, **not** a claimed reproduction of any named third-party product:

    SKIPPED by explicit --skip-llm; deterministic arms only

It is reported as skipped rather than estimated, simulated, or filled in from a published
figure. A benchmark that invents its competitor's score is worth nothing.

## Reproduce

    python benchmarks/poisoning_benchmark.py --cases 60 --climb-repeats 5 --seed 20260805 --skip-llm

## Caveats

- Synthetic, seeded metric series; CloudWatch is stubbed with known ground truth via
  `evidence._average`, exactly as `verification_benchmark.py` does it. This measures the
  decision rule under adversarial input, not CloudWatch's own security.
- `_tier_outcome_gated` reproduces `backend.trust._apply`'s tier mapping around a direct call
  to `backend.evidence.verify()`; it does not go through `backend.trust.grant_standing` itself,
  which needs a live CockroachDB connection this script does not depend on. The database plays
  no role in the decision under test.
- `autonomous_when_alone` and `autonomous_vs_honest` exercise
  `backend.agent._fallback_decision`
  directly against hand-built memory dicts, not the full `handle_alert` pipeline (no vector
  recall, no model).
- The claim/honest action pool deliberately excludes `open_incident` from what an attack can
  claim, because it is `_fallback_action`'s own default -- an attacker "winning" by
  recommending the same thing the keyword table would have picked anyway is not a
  distinguishable result.
- Five attack classes is a modelling choice, not an exhaustive taxonomy of adversarial input.
