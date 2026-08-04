# Deploying MemoryStand

**Status as of this writing: nothing here has been deployed.** The machine that wrote
this document has no AWS credentials and no CockroachDB Cloud session (`aws sts
get-caller-identity` returns `NoCredentials`, `ccloud auth whoami` is not logged in), and
cannot create either. Every script below has been read carefully against the AWS CLI's
own `help` output, but none has been run against a real account. Do not treat anything
in this file as "it works" until someone with real credentials has run it and can point
at a URL that actually answers.

## What the judge-facing URL will look like

There are two independently deployed pieces:

1. **The dashboard** (`frontend/`) -- a static site, deployed to **AWS Amplify Hosting**
   by `./infra/deploy_frontend.sh`. Its URL has a fixed, predictable shape:

   ```
   https://<BRANCH_NAME>.<amplify-app-id>.amplifyapp.com
   ```

   `BRANCH_NAME` defaults to `main`; the app ID is assigned by AWS the first time
   `create-app` runs and is not knowable in advance. Run the script and read its final
   "Public URL:" line for the real value.

2. **The API** (`backend/handler.py`) -- an AWS Lambda behind a Function URL, deployed by
   `./infra/deploy.sh`. Its URL shape is:

   ```
   https://<lambda-url-id>.lambda-url.<region>.on.aws/
   ```

   also not knowable until `deploy.sh` runs and prints it.

The dashboard does not hardcode the API's URL (there is no way to, before the API
exists) -- it is passed at share time as a query parameter:

```
https://<BRANCH_NAME>.<amplify-app-id>.amplifyapp.com/?api=https://<lambda-url-id>.lambda-url.<region>.on.aws
```

That combined URL, once both scripts have been run once, is the one link to hand a
judge. `frontend/app.js`'s `API_BASE` reads `?api=` first and falls back to
`http://127.0.0.1:8077` (this repo's local dev port, matching `scripts/run-local.sh`)
only when `?api=` is absent -- so a judge who follows a link with `?api=` set never sees
the local-dev fallback.

## Deploy order

```bash
./infra/provision.sh              # CockroachDB Basic cluster, via ccloud
./infra/ssm_setup.sh               # DSN, shared secret, kill switch -> SSM
db/schema.sql applied, db/seed/seed.py run against the cluster (see README Quickstart)
./infra/deploy.sh                  # Lambda + Function URL  -> prints the API URL
./infra/deploy_frontend.sh         # Amplify Hosting          -> prints the dashboard URL
./infra/keepwarm.sh                # keeps CockroachDB warm behind the Function URL
```

Each script fails immediately and clearly (checked `aws sts get-caller-identity` /
`ccloud auth whoami` before doing anything else) if credentials are missing, rather than
half-deploying. Each is safe to re-run: every AWS/ccloud call is written to
find-or-create rather than fail on a second pass.

## What stays free for a ~6-week judging window (2026-08-04 through the 2026-09-15 target
in `infra/keepwarm.sh`)

Stated plainly, with the free-tier thresholds each service publishes, because this
project's whole thesis is not overclaiming things nobody has checked:

| Service | Free tier | Why this demo stays inside it |
|---|---|---|
| **AWS Amplify Hosting** | 5 GB CDN storage/month free, then $0.023/GB-month; 15 GB data transfer/month free, then $0.15/GB served | The whole site (`index.html` + `app.js`) is well under 100 KB. Even hundreds of judge page loads over 6 weeks stay orders of magnitude under 15 GB served. |
| **AWS Lambda** | 1M requests + 400,000 GB-seconds compute/month free (always-free, not time-limited) | `infra/deploy.sh` sizes the function at 512 MB / 30s timeout; a demo's request volume is nowhere near 1M/month. |
| **Amazon CloudWatch Logs** | 5 GB ingested + stored free/month | `infra/deploy.sh` sets a 14-day retention specifically so a demo left running does not grow unbounded past that. |
| **AWS Systems Manager Parameter Store** | Standard parameters and the AWS-managed `alias/aws/ssm` KMS key are free | `infra/ssm_setup.sh` uses only standard parameters and the managed key -- see that script's own cost comment. |
| **Amazon EventBridge Scheduler** | The scheduled rule itself and its invocations of `/health` (a handful of KB every 5 minutes) are negligible; API-destination connections carry no separate charge | `infra/keepwarm.sh` pings `/health` on a schedule to keep CockroachDB from suspending mid-demo. |
| **CockroachDB Basic (via ccloud, `--cloud AWS`)** | Basic tier has its own free monthly Request Unit and storage allowance | `infra/provision.sh` sets `--request-unit-limit 50000000 --storage-gib-limit 10` specifically to stay inside that allowance -- see the script's own comment. |
| **Amazon Bedrock** (Nova Converse + Titan embeddings) | No free tier; billed per token/request | The only genuinely metered cost here, and it is small: `/decide` calls Bedrock only when the caller does *not* supply `action` itself (this dashboard always supplies it, so it skips Bedrock entirely -- see `frontend/app.js`'s API-contract comment), and `/ingest` embeds one short string per call. A judging window's worth of manual clicking is cents, not dollars. |

**Bottom line, stated as an estimate because nobody has run these scripts against a
paying account yet:** realistic cost for a 6-week judging window is at or near **$0.00**
for hosting/compute/logs/scheduling, with the only real variable cost being Bedrock
token usage from actual `/ingest` and model-reasoned `/decide` calls -- and this
dashboard's own traffic pattern (caller-supplied actions, short content strings)
minimizes even that. Verify current pricing before relying on this table:
<https://aws.amazon.com/amplify/pricing/>, <https://aws.amazon.com/lambda/pricing/>,
<https://aws.amazon.com/systems-manager/pricing/>, and the CockroachDB Cloud / Bedrock
pricing pages.

## Teardown

```bash
aws amplify delete-app --app-id <app-id> --region <region>     # dashboard + all deployments
aws lambda delete-function --function-name memorystand --region <region>
aws lambda delete-function-url-config --function-name memorystand --region <region>  # if delete-function leaves it
ccloud cluster delete <cluster-name>                             # the CockroachDB cluster itself
```

`infra/deploy_frontend.sh` and `infra/deploy.sh` print their own app-id / function-name
specific teardown notes at the end of a successful run.
