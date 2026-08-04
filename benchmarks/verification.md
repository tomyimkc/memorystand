# Does re-checking the outcome catch anything?

A measurement of the **decision rule**, not of CloudWatch and not of production accuracy. The
metric series are synthetic and seeded (`--seed 20260804`) because false-promotion rate is
meaningless without known ground truth, and real production metrics do not arrive labelled.

## What is being measured

Not malice. The case that matters is an on-call engineer who restarts a service at 02:00, sees
the page clear, and reports in good faith that the restart fixed it — when the metric shows
latency never moved, or was already recovering, or got worse. Nobody lied, and the memory
"restarting payments-service fixes p99 latency" is still false. Every system that takes the
reporter's word promotes it to trusted and hands it to an agent at the next incident.

## Case mix (500 cases)

| Kind | Count | Should promote |
|---|---:|---|
| correct — the action worked and the metric agrees | 284 | yes |
| borderline — real improvement, meaningfully smaller than claimed | 12 | yes (contested) |
| no_change — honest report, metric did not move | 105 | no |
| regressed — honest report, metric got worse | 54 | no |
| overstated — right direction, wildly wrong magnitude | 27 | no |
| no_data — empty window, nothing to check against | 18 | no |

## Results

| | trust_the_caller | outcome_gated |
|---|---:|---:|
| False promotions | **204** | **0** |
| False-promotion rate | 100.0% | 0.0% |
| True promotions | 296 | 292 |
| Recall on genuinely good outcomes | 100.0% | 98.6% |
| Precision | 59.2% | 100.0% |
| Differing runs out of 4 reruns | 0 | 0 |
| Latency per check | 0.000 ms | 0.249 ms |
| Model calls | 0 | 0 |

**204 memories** would have entered the store as trusted under the baseline and were
refused by the gate. Of those, 186 are
claims the metric actively contradicts — it did not move, moved the wrong way, or moved far
less than reported — and 18 are claims with no data to check against at all,
refused because "cannot tell" is not "confirmed".

## The honest cost, and the knob that sets it

`outcome_gated` recall is **98.6%** at the default 50% tolerance — every
point below 100% is a real outcome that genuinely happened and was refused anyway, because the
observed change was too far from the claimed one to confirm. Those memories are not lost; they
sit at `attested` instead of `verified`.

The trade is a knob, not a law, and reporting a single operating point would be cherry-picking:

| `MEMORYSTAND_EVIDENCE_TOLERANCE` | False promotions | Recall on good outcomes | Precision |
|---:|---:|---:|---:|
| 15% | 0 | 97.0% | 100.0% |
| 25% | 0 | 98.0% | 100.0% |
| 50% | 0 | 98.6% | 100.0% |
| 75% | 4 | 99.7% | 98.7% |
| 100% | 77 | 100.0% | 79.4% |

Read the curve, not the headline. A tight tolerance refuses more honest reports; a loose one
lets overstated claims through. **The gate is deliberately biased toward refusing**: a memory
wrongly left at `attested` costs an agent some usefulness, while one wrongly promoted to
`verified` costs it correctness — and correctness is the thing being sold.

The `borderline` cases exist specifically so this benchmark is not trivially won. Without them
the generated classes separate so cleanly that any threshold rule scores perfectly, which would
measure the generator's assumptions rather than the rule.

## Does it survive a different draw?

The whole benchmark, regenerated under independent seeds. If the result only held for the seed
that happened to be committed, it would be a property of that draw rather than of the rule.

| seed | baseline false promotions | baseline precision | gated false promotions | gated precision | gated recall |
|---:|---:|---:|---:|---:|---:|
| 1 | 229 | 54.2% | 0 | 100.0% | 98.9% |
| 7 | 197 | 60.6% | 0 | 100.0% | 98.0% |
| 42 | 211 | 57.8% | 0 | 100.0% | 98.3% |
| 1234 | 211 | 57.8% | 0 | 100.0% | 99.3% |
| 20260804 | 204 | 59.2% | 0 | 100.0% | 98.6% |

## The third arm, not run

The `llm_judge` arm — asking a model whether the memory still looks true, which is what
[Mem0](https://arxiv.org/html/2504.19413v1), [Zep/Graphiti](https://arxiv.org/pdf/2501.13956)
and [AWS Bedrock AgentCore](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/built-in-strategies.html)
each do — is implemented in this script and did not run:

    SKIPPED, not estimated: ModelUnavailable: Bedrock still throttling after 5 attempts (ThrottlingException)

It is reported as skipped rather than estimated or filled in from published figures. A
benchmark that invents its competitor's score is worth nothing. What can be said without
measuring it is structural: an LLM judge cannot produce the `0/4 differing
runs` line above, because it is sampled rather than computed — and
[graphiti#1666](https://github.com/getzep/graphiti/issues/1666) records contradiction detection
scoring 1 of 9 on a weaker model, with the consequence that "stale facts survive their own
contradiction".

## Reproduce

    python benchmarks/verification_benchmark.py --cases 500 --repeats 5 --seed 20260804

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
