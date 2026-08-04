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

## Blocked: the public Function URL returns 403

Every route returns `403 Forbidden` with the Function-URL-authorization message, while the
**same function invoked directly returns 200**. So the handler is fine; the auth layer in front
of it is not.

Excluded by testing, not by assumption:

- `AuthType` is `NONE` — read back from the live config.
- The resource policy is correct — **three** statements with `Principal: "*"`,
  `Action: lambda:InvokeFunctionUrl`, `Condition: lambda:FunctionUrlAuthType = NONE`.
- Not propagation — 403 persisted across five retries over ~2 minutes.
- Not a stale URL — deleting and recreating the Function URL produced an identical 403 on a
  brand-new URL with a fresh permission statement.

**Leading hypothesis: account-level restriction.** This account is measurably restricted:

| Limit | This account | Normal |
|---|---|---|
| Lambda concurrent executions | **10** | 1000 |
| Bedrock on-demand quotas | **0** on 157 of 160 | non-zero |

A day-old, unverified AWS account with a 10-execution ceiling and no Bedrock capacity is
plausibly also blocked from exposing public Function URLs. The CLI in this environment has no
`get-public-access-block-config`, so this is **unconfirmed** — stated as a hypothesis, not a
finding.

### What to check

1. Console → **Lambda → memorystand → Configuration → Function URL** — look for a public-access
   or block warning.
2. Console → **Account → billing/verification status** — new accounts are sometimes restricted
   until identity and payment are verified.
3. Open a **Support case** (Account and billing, free tier): "Function URL with AuthType NONE
   returns 403 despite a correct resource policy." Include the function ARN.

### If it stays blocked

The rules require a *functional demo URL*, not specifically a Lambda Function URL. In rough
order of preference:

1. **Wait.** New-account restrictions commonly lift within days, and there are 14 left.
2. **API Gateway** in front of the same Lambda — a different public surface that may not be
   covered by the same restriction.
3. **Self-host the API** on owned hardware behind a tunnel, keeping CockroachDB Cloud as the
   memory layer. Satisfies the requirement, but it must survive unattended until 2026-09-15,
   which is a real risk to weigh.
4. **Submit local-only and say so.** Costs Production Readiness points; keeps the project honest.

## Not yet run

`deploy_frontend.sh` (Amplify) and `mcp_setup.sh` (read-only MCP service account). Neither is
blocked — they were sequenced after the API, and the API is where the blocker is.
