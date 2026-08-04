# Bedrock on a new AWS account: what we hit, measured

Written from a real bring-up on an AWS account created 2026-08-04, because none of this is
obvious from the console and two of the three problems produce error messages that point at
the wrong cause.

## The short version

A brand-new AWS account has **essentially no on-demand Bedrock capacity**. Model access shows
`AUTHORIZED`, the models are listed, IAM is correct — and calls still fail. Capacity is a third,
separate gate, and it is the one nobody mentions.

## Three gates, not one

A Bedrock call succeeds only if all three pass. They fail with different, easily-confused errors.

| Gate | How to check | Failure looks like |
|---|---|---|
| **IAM** | `aws sts get-caller-identity`, then read the policy | `AccessDeniedException: not authorized to perform: bedrock:InvokeModel on resource: ...` |
| **Model access** | `aws bedrock get-foundation-model-availability --model-id X` | `AccessDeniedException` mentioning the *model*, not your user |
| **Quota** | `aws service-quotas list-service-quotas --service-code bedrock` | `ThrottlingException: Too many requests` / `Too many tokens per day` |

The third is the one that cost us most of a session: **`ThrottlingException` reads like "slow
down", but on a new account it usually means "your quota is zero".** Waiting does not help.

## What we measured

Account 2026-08-04, `us-east-1`, via the paginated Service Quotas API:

- **157 of 160** "requests per minute" quotas were **0**.
- The only non-zero ones were Guardrails (1,500/min) and AI21 Jamba 1.5 (**1**/min).
- Titan Text Embeddings V2 on-demand: **0**.
- Claude Haiku 4.5, Nova Lite, Nova Micro, Nova Pro, Llama 3 70B, Mistral Large: all **0**.

`us-west-2` had some allowance. One real Titan call there succeeded (512 dims); subsequent calls
failed with a *daily* token cap. So the allowance exists but is very small.

**Do not trust the Service Quotas numbers on their own.** For the same model in the same region
it reported both `6,000` and `0` depending on which quota row matched. The only reliable test is
to make the call.

## Two traps that point at the wrong cause

**`ThrottlingException` on a new account is usually zero quota, not burst.** Retrying with
backoff — which our client does, five times — just turns a fast failure into a slow one.

**Anthropic models are geo-restricted, independently of AWS.**

```
ValidationException: Access to Anthropic models is not allowed from unsupported countries,
regions, or territories.
```

This is Anthropic's own restriction on where the *caller* is, not an IAM or quota problem, and
no region change fixes it. See <https://www.anthropic.com/supported-countries>. If you are
building from an unsupported location, choose a different model family — **Amazon Nova** is
AWS-native, `ON_DEMAND` (so no inference-profile indirection), and carries no such restriction.

## A fourth trap, in IAM rather than Bedrock

Most current Claude models on Bedrock are `INFERENCE_PROFILE`, not `ON_DEMAND`. Calling one
targets a profile ARN in *your* account, which then fans out to member regions — so the policy
needs **both** the profile ARN and the underlying foundation-model ARN in each region. Grant
only one and you get an `AccessDenied` naming a resource you never asked for.

Check before designing around a model:

```bash
aws bedrock list-foundation-models --by-provider anthropic \
  --query 'modelSummaries[].[modelId,inferenceTypesSupported[0]]' --output text
```

## And one in the SDK

`aws login` credentials do not work under plain boto3:

```
MissingDependencyException: Using the login credential provider requires an additional
dependency. You will need to pip install "botocore[crt]"
```

The AWS CLI bundles CRT; your virtualenv does not. It is in `requirements-dev.txt`. A static
profile (`aws configure`) is unaffected.

## What we changed, and what it costs

- **Region is `us-west-2`.** `us-east-1` had zero capacity for every model this project uses.
- **Reasoning model is `amazon.nova-lite-v1:0`**, not Claude — geo-restriction, above.
- **Embeddings remain Titan Text Embeddings V2 at 512 dims**, matching `VECTOR(512)` in the
  schema. No schema change.

## Why this does not block the submission

Worth stating plainly, because it would be easy to panic here.

The claim this project actually makes — that a memory earns trust only from a **verified
external outcome** — runs through `backend/trust.py`, which **makes zero model calls**. That is
asserted on the live path by `assert_no_model_calls()` and covered by tests. Bedrock capacity is
irrelevant to it.

Admission control's *deciding* step is likewise deterministic by design: the attribute-conflict
check is plain SQL inside the transaction, and the model is an enrichment layer with a
documented fallback (`backend/agent.py` picks an action from a rule table when the model is
unavailable). The demo runs end to end with no Bedrock at all.

What genuinely degrades without capacity is **retrieval relevance**: with the deterministic stub,
embeddings carry no semantic meaning, so latency numbers stay valid but ranked results are noise.
`backend/embeddings.py` announces which path produced a vector via `provenance()` rather than
letting a stub result pass as a real one.

## Ask for capacity early

Quota increases are not instant, and a new account ramps over days.

Service Quotas → **Amazon Bedrock** → `us-west-2` → request increases for:

- *On-demand model inference requests per minute for Amazon Titan Text Embeddings V2*
- *On-demand model inference tokens per minute for Amazon Titan Text Embeddings V2*
- the equivalents for **Amazon Nova Lite**

Do it as soon as the account exists, not the week you need it.
