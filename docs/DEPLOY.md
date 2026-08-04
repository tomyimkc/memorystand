# Deploying MemoryStand

**Status as of this writing: this has been deployed, for real.** `./infra/provision.sh`,
`./infra/ssm_setup.sh`, `./infra/deploy.sh`, and `./infra/deploy_frontend.sh` have all
been run against a real AWS account and a real CockroachDB Cloud cluster: the Lambda's
`State` is `Active`, the CockroachDB Basic cluster is up and holds real data, and the
dashboard is live on AWS Amplify Hosting answering `HTTP 200`. For the specific evidence
behind that claim -- exact URLs, cluster IDs, row counts, and what each check actually
returned -- see [DEPLOY_STATUS.md](DEPLOY_STATUS.md); that page is kept current as the
deployment changes, this one is the how-to.

What is still on a reader to supply: your own AWS credentials and your own CockroachDB
Cloud (`ccloud`) session, if you want a copy of this running under your own account. The
instructions below are the real path that produced the live deployment, written so
anyone with those two things can reproduce it from scratch -- they deliberately don't
hardcode this project's own app ID or Lambda URL ID (AWS assigns both per-deployment),
so redeploying under a different account won't leave this file's shape wrong.

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

Once `deploy.sh` has printed a real API URL, paste it into `frontend/app.js` as the
`DEPLOYED_API` constant *before* running `deploy_frontend.sh` -- that step is manual,
`deploy_frontend.sh` does not do it for you. Do that and the bare dashboard URL becomes
the one link to hand a judge; no `?api=` query parameter is needed. In code,
`frontend/app.js`'s `API_BASE` picks `LOCAL_API`
(`http://127.0.0.1:8077`, matching `scripts/run-local.sh`) when the page itself is
being served from `localhost`/`127.0.0.1`/`file:`, and `DEPLOYED_API` otherwise; `?api=`
still overrides either default, which is what lets `run-local.sh --serve` point this
same file at a local backend and what lets anyone point a deployed dashboard at a
different API without editing the file. So both of these work once the two scripts have
run once:

```
https://<BRANCH_NAME>.<amplify-app-id>.amplifyapp.com
https://<BRANCH_NAME>.<amplify-app-id>.amplifyapp.com/?api=https://<lambda-url-id>.lambda-url.<region>.on.aws
```

One thing worth knowing before opening the API URL directly in a browser: it has no
root route, so `GET /` on it returns `404 {"error":"not_found",...}` by design -- that
404 is expected and is not a sign the deployment is broken; it just means that URL is
an API to be called, not a page to hand a judge on its own.

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

**Bottom line, still stated as an estimate rather than a measured bill** -- these scripts
have now been run against a real account and the deployment has been live for days, but
no full monthly billing cycle has closed yet to check this against: realistic cost for a
6-week judging window is at or near **$0.00** for hosting/compute/logs/scheduling, with
the only real variable cost being Bedrock token usage from actual `/ingest` and
model-reasoned `/decide` calls. In practice that is currently near zero too: this
account's Bedrock quota is ~0, so live `/decide` calls fall back to a deterministic
rule and spend nothing. This dashboard's own traffic pattern
(caller-supplied actions, short content strings) minimizes even that. Verify current
pricing before relying on this table:
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
