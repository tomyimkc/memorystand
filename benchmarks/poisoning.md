# Does re-checking the outcome catch an ATTACKER, not just an honest mistake?

A companion to `benchmarks/verification.md`, which measures honest-mistake noise. This one
measures deliberate poisoning: someone reporting an outcome that never happened, in order to
install a false memory a future agent will act on. Same trust ladder, same three arms,
adversarial input.

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
`unconfirmed`), and the two action-driving tests described below.

### `trust_the_caller`

| Attack class | n | Reached verified | Reached attested | Refused outright | Drives the action when admitted, alone | Drives the action against an honest verified competitor |
|---|---:|---:|---:|---:|---:|---:|
| fabricated_incident | 60 | 60 (100%) | 0 (0%) | 0 (0%) | 100% | 100% |
| metric_lie | 60 | 60 (100%) | 0 (0%) | 0 (0%) | 100% | 100% |
| magnitude_inflation | 60 | 60 (100%) | 0 (0%) | 0 (0%) | 100% | 100% |
| tier_climb | 300 | 300 (100%) | 0 (0%) | 0 (0%) | 100% | 100% |
| wrong_entity | 60 | 60 (100%) | 0 (0%) | 0 (0%) | 100% | 100% |
| control_honest | 60 | 60 (100%) | 0 (0%) | 0 (0%) | 100% | 100% |

### `outcome_gated`

| Attack class | n | Reached verified | Reached attested | Refused outright | Drives the action when admitted, alone | Drives the action against an honest verified competitor |
|---|---:|---:|---:|---:|---:|---:|
| fabricated_incident | 60 | 0 (0%) | 60 (100%) | 0 (0%) | 100% | 0% |
| metric_lie | 60 | 0 (0%) | 0 (0%) | 60 (100%) | n/a | 0% |
| magnitude_inflation | 60 | 0 (0%) | 0 (0%) | 60 (100%) | n/a | 0% |
| tier_climb | 300 | 0 (0%) | 300 (100%) | 0 (0%) | 100% | 0% |
| wrong_entity | 60 | 0 (0%) | 60 (100%) | 0 (0%) | 100% | 0% |
| control_honest | 60 | 60 (100%) | 0 (0%) | 0 (0%) | 100% | 100% |

### `llm_judge`

| Attack class | n | Reached verified | Reached attested | Refused outright | Drives the action when admitted, alone | Drives the action against an honest verified competitor |
|---|---:|---:|---:|---:|---:|---:|
| fabricated_incident | 6 | 0 (0%) | 0 (0%) | 6 (100%) | n/a | 0% |
| metric_lie | 6 | 0 (0%) | 0 (0%) | 6 (100%) | n/a | 0% |
| magnitude_inflation | 6 | 0 (0%) | 0 (0%) | 6 (100%) | n/a | 0% |
| tier_climb | 6 | 0 (0%) | 0 (0%) | 6 (100%) | n/a | 0% |
| wrong_entity | 6 | 0 (0%) | 0 (0%) | 6 (100%) | n/a | 0% |
| control_honest | 6 | 0 (0%) | 0 (0%) | 6 (100%) | n/a | 100% |

| fabricated_incident | 6 | 0 (0%) | 0 (0%) | 6 (100%) | n/a | 0% |
| metric_lie | 6 | 0 (0%) | 0 (0%) | 6 (100%) | n/a | 0% |
| magnitude_inflation | 6 | 0 (0%) | 0 (0%) | 6 (100%) | n/a | 0% |
| tier_climb | 6 | 0 (0%) | 0 (0%) | 6 (100%) | n/a | 0% |
| wrong_entity | 6 | 0 (0%) | 0 (0%) | 6 (100%) | n/a | 0% |
| control_honest | 6 | 0 (0%) | 0 (0%) | 6 (100%) | n/a | 100% |

**"Drives the action when admitted, alone"** feeds every ADMITTED poisoned memory (attested or
verified -- refused ones are excluded, since they were never admitted) through
`backend.agent._fallback_action`, by itself, and checks whether it changes the action from
what the keyword table alone would have picked. This is close to 100% almost everywhere it is
defined, and that is itself the finding worth stating plainly: `_fallback_action`'s tier filter
(`_TIER_RANK = {"verified": 3, "attested": 2, "unconfirmed": 1}`) excludes only `disputed`
memories. `unconfirmed` is a *usable* tier, not a withheld one -- it is what every memory
starts at before any outcome is reported, and it already outranks the keyword table whenever
nothing else competes. So this metric mostly measures a fact about `_fallback_action`, not
about which arm processed the claim, which is why the second test exists.

**"Drives the action against an honest, verified competitor"** is the harder, more honest
question: put the poisoned memory up against a memory for the SAME entity that genuinely
reached `verified` the honest way, and see which one `_fallback_action` picks. Ties (both
`verified`) are broken in the attacker's favour, matching `_fallback_action`'s own tie-break
(first item wins the `max()`) under the realistic worst case that a memory freshly crafted to
match the incoming alert text is also the closest vector match.

## What `outcome_gated` does NOT stop

Stated plainly, because a benchmark whose own design guarantees a 0% admission rate everywhere
has tested nothing:

- **`wrong_entity` reaches `verified` 0% of the time under
  `outcome_gated`**, and goes on to beat an honest, correctly-attributed `verified` memory
  0% of the time. `evidence.verify(source, external_ref, claimed_delta,
  decided_at)` has no entity parameter -- it confirms whether the metric named in
  `external_ref` moved as claimed, not whether the memory's `entity` field is the service that
  metric actually belongs to. A real improvement on `checkout-api`, filed as a memory about
  `payments-service`, is checked, agrees, and is promoted -- credited to the wrong system.
- **`fabricated_incident` reaches `attested` 100% of the time**, because nothing
  in this deployment can re-query PagerDuty or ask the named human whether they actually said
  that (see `backend/evidence.py`'s own docstring on this gap). It does not reach `verified`,
  and it loses to an honest `verified` competitor 0% of the time in the harder
  test -- but it is not refused, and it does poison the store at `attested`, which is enough to
  win in a cold store (see the note above) or against another merely-`attested` memory.
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
| llm_judge | 6 | 0 | 0 |

Under `outcome_gated`, repetition buys nothing: each attempt is checked independently against
the same unreachable system of record, so every attempt lands at the same tier as the first.
What repetition DOES buy the attacker is breadth -- `5`x as many `attested`
memories echoing the same false claim in the store, which is a real cost even though no single
one of them out-tiers an honest `verified` memory.

## Does it survive a different draw?

The whole attack set, regenerated under independent seeds, scored against `outcome_gated`.
Every class's admission rate is a property of how the class is CONSTRUCTED (does the claim
match reality or not), not of the seed -- so an identical row across seeds is the expected,
correct result, not a coincidence to be suspicious of.

| seed | fabricated_incident | metric_lie | magnitude_inflation | tier_climb | wrong_entity | control_honest |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 100% | 0% | 0% | 100% | 100% | 100% |
| 7 | 100% | 0% | 0% | 100% | 100% | 100% |
| 42 | 100% | 0% | 0% | 100% | 100% | 100% |
| 1234 | 100% | 0% | 0% | 100% | 100% | 100% |
| 20260805 | 100% | 0% | 0% | 100% | 100% | 100% |

Determinism (identical case set, rerun): **False**.

## The third arm

The `llm_judge` arm -- asking a model whether the claim looks plausible, which is what
[Mem0](https://arxiv.org/html/2504.19413v1), [Zep/Graphiti](https://arxiv.org/pdf/2501.13956)
and [AWS Bedrock AgentCore](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/built-in-strategies.html)
each do -- is implemented in this script (`_tier_llm_judge`) against the real
`backend.bedrock_client`:

    running for real against anthropic:claude-haiku-4-5

It is reported as skipped rather than estimated, simulated, or filled in from a published
figure. A benchmark that invents its competitor's score is worth nothing.

## Reproduce

    python benchmarks/poisoning_benchmark.py --cases 60 --climb-repeats 5 --seed 20260805

## Caveats

- Synthetic, seeded metric series; CloudWatch is stubbed with known ground truth via
  `evidence._average`, exactly as `verification_benchmark.py` does it. This measures the
  decision rule under adversarial input, not CloudWatch's own security.
- `_tier_outcome_gated` reproduces `backend.trust._apply`'s tier mapping around a direct call
  to `backend.evidence.verify()`; it does not go through `backend.trust.grant_standing` itself,
  which needs a live CockroachDB connection this script does not depend on. The database plays
  no role in the decision under test.
- `action_when_admitted` and `action_vs_honest` exercise `backend.agent._fallback_action`
  directly against hand-built memory dicts, not the full `handle_alert` pipeline (no vector
  recall, no model). See the honesty note above on why the first of the two is a weak signal by
  itself.
- The claim/honest action pool deliberately excludes `open_incident` from what an attack can
  claim, because it is `_fallback_action`'s own default -- an attacker "winning" by
  recommending the same thing the keyword table would have picked anyway is not a
  distinguishable result.
- Five attack classes is a modelling choice, not an exhaustive taxonomy of adversarial input.
