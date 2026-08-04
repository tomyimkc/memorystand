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

## Case mix (300 cases)

| Kind | Count | Should promote |
|---|---:|---|
| correct — the action worked and the metric agrees | 171 | yes |
| borderline — real improvement, meaningfully smaller than claimed | 8 | yes (contested) |
| no_change — honest report, metric did not move | 59 | no |
| regressed — honest report, metric got worse | 39 | no |
| overstated — right direction, wildly wrong magnitude | 13 | no |
| no_data — empty window, nothing to check against | 10 | no |

## Results

| | trust_the_caller | outcome_gated |
|---|---:|---:|
| False promotions | **121** | **0** |
| False-promotion rate | 100.0% | 0.0% |
| True promotions | 179 | 177 |
| Recall on genuinely good outcomes | 100.0% | 98.9% |
| Precision | 59.7% | 100.0% |
| Differing runs out of 4 reruns | 0 | 0 |
| Latency per check | 0.000 ms | 0.411 ms |
| Model calls | 0 | 0 |

**121 memories** would have entered the store as trusted under the baseline and were
refused by the gate. Of those, 111 are
claims the metric actively contradicts — it did not move, moved the wrong way, or moved far
less than reported — and 10 are claims with no data to check against at all,
refused because "cannot tell" is not "confirmed".

## The honest cost, and the knob that sets it

`outcome_gated` recall is **98.9%** at the default 50% tolerance — every
point below 100% is a real outcome that genuinely happened and was refused anyway, because the
observed change was too far from the claimed one to confirm. Those memories are not lost; they
sit at `attested` instead of `verified`.

The trade is a knob, not a law, and reporting a single operating point would be cherry-picking:

| `MEMORYSTAND_EVIDENCE_TOLERANCE` | False promotions | Recall on good outcomes | Precision |
|---:|---:|---:|---:|
| 15% | 0 | 96.6% | 100.0% |
| 25% | 0 | 97.8% | 100.0% |
| 50% | 0 | 98.9% | 100.0% |
| 75% | 3 | 100.0% | 98.4% |
| 100% | 42 | 100.0% | 81.0% |

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

    SKIPPED, not estimated: ModelUnavailable: Bedrock still throttling after 5 attempts (ThrottlingException)

It is reported as skipped rather than estimated or filled in from published figures. A
benchmark that invents its competitor's score is worth nothing. What can be said without
measuring it is structural: an LLM judge cannot produce the `0/4 differing
runs` line above, because it is sampled rather than computed — and
[graphiti#1666](https://github.com/getzep/graphiti/issues/1666) records contradiction detection
scoring 1 of 9 on a weaker model, with the consequence that "stale facts survive their own
contradiction".

## Reproduce

    python benchmarks/verification_benchmark.py --cases 300 --repeats 5 --seed 20260804

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
