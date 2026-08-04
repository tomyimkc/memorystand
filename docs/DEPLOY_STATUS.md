# Deployment status

Last updated 2026-08-04. Written to be accurate rather than encouraging — a project whose
argument is "check claims before trusting them" cannot have an aspirational status page.

## Working, verified against real AWS

| Component | Evidence |
|---|---|
| CockroachDB Cloud cluster `memorystand` | BASIC, AWS, us-west-2. Schema applied, 101 memories seeded (99 admitted, 2 held) |
| Vector index on Cloud Basic | `vector search` + `prefix spans` on the real `recall()` query |
| SSM SecureStrings | `/memorystand/{dsn,shared_secret,kill_switch}`, values never printed |
| IAM role + least-privilege policy | `memorystand-lambda-role` |
| CloudWatch log group | 14-day retention |
| **Lambda `memorystand`** | `State: Active`, python3.13, 512 MB / 30 s |
| **Lambda → CockroachDB Cloud** | direct invoke returns `200`, `database: reachable`, `gc_window_seconds: 4500` |
| Graceful degradation | exercised in production: before the TLS fix the handler returned `200` with `database: unreachable` and the underlying error, rather than crashing |

## Resolved: the Function URL 403

**Cause: a missing permission, not an account restriction.** My hypothesis -- that a day-old
account with a 10-execution Lambda ceiling and zero Bedrock quota was also blocked from public
Function URLs -- was **wrong**, and the console said so plainly:

> Your function URL auth type is NONE, but is missing permissions required for public access...
> create a resource-based policy that grants **lambda:invokeFunction and lambda:invokeFunctionUrl**
> permissions to all principals (*)

AWS requires **both** actions. Every instinct says `lambda:InvokeFunctionUrl` is *the*
Function-URL permission, so granting only that looks complete -- and fails closed with a 403
naming neither the missing action nor the fix.

One wrinkle: `--function-url-auth-type NONE` is only accepted alongside `InvokeFunctionUrl`, so
the second grant must omit it. Both statements are required:

    aws lambda add-permission --function-name memorystand \
      --statement-id publicInvokeFunctionUrl --action lambda:InvokeFunctionUrl \
      --principal '*' --function-url-auth-type NONE

    aws lambda add-permission --function-name memorystand \
      --statement-id publicInvokeFunction --action lambda:InvokeFunction --principal '*'

## Then: 30-second timeouts on the embedding routes

With auth fixed, `/health` returned 200 but `/recall` and `/decide` returned Internal Server
Error. CloudWatch showed `Status: timeout` at exactly 30,000 ms.

Bedrock quota is zero, and `embed()` retried five times with exponential backoff -- which can
outlast the entire Lambda timeout. A throttled dependency could consume the whole request
budget. Embedding is now time-boxed (`MEMORYSTAND_EMBED_DEADLINE_S`, default 6s): past the
deadline it stops retrying and degrades to the deterministic stub, announcing that it did.

That is a better system regardless of quota. Bounded latency under dependency failure is the
point of the Production Readiness criterion, and it was found by deploying rather than by
reasoning about it.

## Live and verified end to end

**Demo URL: <https://ojao6oaxlk26mqfjwpuy7g4dy40tglyi.lambda-url.us-west-2.on.aws>**

| Route | Result |
|---|---|
| `GET /health` | 200 -- `database: reachable`, `gc_window_seconds: 4500` |
| `GET /recall` | 200 -- real memories from CockroachDB Cloud, ranked with distances |
| `POST /ingest` without the secret | **401**, with CORS headers on the error |
| `POST /decide` with the secret | 201 -- decision recorded |
| `POST /confirm_outcome` | `outcome: success`, **`model_calls: 0`** |
| `GET /timemachine` | 99 memories reconstructed at decision time |

The central claim now holds **in production**: a memory's trust is granted by an external
outcome, on a path that makes zero model calls.

## Not yet run

`deploy_frontend.sh` (Amplify) and `mcp_setup.sh` (read-only MCP service account). Neither is
blocked — they were sequenced after the API, and the API is where the blocker is.
