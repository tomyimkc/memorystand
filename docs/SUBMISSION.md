# Devpost submission checklist — CockroachDB × AWS Hackathon

Every field below was pulled live from the hackathon's own submission form
(`cockroachdb-ai.devpost.com`) on 2026-08-04, not guessed. Field IDs are Devpost's internal ids,
included so you can match this doc to the live form field-for-field.

## The hard deadline

**Submissions close 2026-08-18 21:00 UTC = 2026-08-18 17:00 ET.** Judging runs 2026-08-19 through
2026-09-15; winners announced 2026-09-21. You are already registered for this hackathon (confirmed
via the Devpost API) — no separate registration step needed. **No project has been created yet**
for this hackathon on your Devpost account — that is step 1 below.

Judging is five equally-weighted criteria, tie-break favors the first, then the second:

1. **Agentic Memory Design** — does CockroachDB play a meaningful, production-grade role as the
   agent's memory layer, at more than toy scale?
2. **Technical Implementation** — is the integration with CockroachDB tools quality engineering,
   used correctly and safely?
3. **Real-World Impact** — how big could the impact be on real users/workflows?
4. **Production Readiness** — secure, observable, scalable; has failure been thought about?
5. **Creativity & Originality** — genuinely new, or a novel application?

Prizes: 1st $5,000 + blog feature + swag; 2nd $2,500 + swag; 3rd $1,250 + swag ($8,750 total pool).

## Step 0 — create the project

No Devpost project exists yet for MemoryStand. Create one (via devpost.com's "Create a project"
flow, or ask me to call `create_project` for you) and connect it to the
`cockroachdb-ai` hackathon before the fields below are fillable.

## Step 1 — the blocking prerequisite: get real AWS + ccloud access

Everything below assumes this repo is still what `SPIKE-RESULTS.md` and `README.md`'s Status table
say it is today: **built and verified locally, never run against real AWS or CockroachDB Cloud
credentials** (there are none on this machine). Two form fields below are hard-blocked on this:

- The **functional demo app URL** (field 27812, required) needs something deployed and reachable —
  `infra/deploy.sh` is written but has never been run.
- The **AWS Services dropdown** (field 27816, required, must select ≥1) can only honestly list a
  service once it has actually executed against real AWS, not just been coded against the SDK.

**Before you can submit, you (the owner) need to:**
1. Get AWS credentials on this machine (`aws sts get-caller-identity` should succeed).
2. Get a `ccloud` session (`ccloud auth login`; `ccloud auth whoami` should succeed) — or spin up a
   CockroachDB Cloud free-tier cluster directly from the console if you'd rather not use `ccloud`.
3. Run, in order: `./infra/provision.sh` (CockroachDB Cloud cluster) → `./infra/ssm_setup.sh`
   (secrets into SSM) → `./infra/deploy.sh` (Lambda + Function URL) → point `frontend/index.html` at
   the deployed Function URL and host it (Amplify Hosting, or anywhere static).
4. Re-run `scripts/demo.sh` against the *deployed* stack once, and update `README.md`'s Status
   table and `SPIKE-RESULTS.md` honestly with what actually worked.

Everything else in this checklist can be filled in now; these two fields cannot.

## Step 2 — make the repository public

The repo is currently **private**. Field 27813 requires a public URL, and the rules require the
Apache-2.0 license to be visible in the repo's "About" section. Before submitting:
- [ ] Flip the GitHub repo to public (`tomyimkc/memorystand`).
- [ ] Confirm GitHub's own license detector picked up `LICENSE` (Apache-2.0) — check the "About"
      sidebar on the repo page shows the license badge, not just the file being present.
- [ ] Double check no secrets are in git history (`DISCLOSURES.md`, `.gitignore` already exclude
      the obvious ones — spot-check `git log -p` for anything that slipped through before flipping
      to public, since a private→public flip is not easily undone for anyone who already cloned).

## Devpost form, field by field

| # | Field (Devpost id) | Required | Status |
|---|---|---|---|
| 1 | URL to functional demo app (27812) | Yes | **TODO — blocked on Step 1** |
| 2 | Testing credentials/instructions for demo app (28078) | No | Draft below |
| 3 | URL to public code repo (27813) | Yes | **TODO — blocked on Step 2**, then `https://github.com/tomyimkc/memorystand` |
| 4 | URL to license file in the repo (27814) | Yes | `https://github.com/tomyimkc/memorystand/blob/main/LICENSE` (once public) |
| 5 | Which CockroachDB tools (27815, pick ≥2) | Yes | Draft below |
| 6 | Which AWS services (27816, pick ≥1) | Yes | **TODO — blocked on Step 1** |
| 7 | How meaningfully integrated (27817) | Yes | Draft below (finish once #5/#6 are locked in) |
| 8 | Project start date, MM-DD-YY (27818) | Yes | `08-03-26` |
| 9 | Pre-existing code disclosure (27819) | Yes | Draft below (condensed from `DISCLOSURES.md`) |
| 10 | Architecture diagram upload (27820) | No | **TODO** — render one from `ARCHITECTURE.md`'s Mermaid diagram |
| 11 | Feedback on CockroachDB AI tools (27821) | No | Draft below |
| 12 | Submitter type: Individual / Organization / Team (27822) | Yes | **TODO — only you know this** |
| 13 | Country of residence (27823) | Yes | **TODO — only you know this** |
| 14 | Organization name, if applicable (27824) | No | **TODO** (blank if solo) |
| 15 | Which AI tools leveraged (27825) | Yes | Draft below |
| 16 | Level of learning derived (27826: None/Moderate/Significant) | Yes | **TODO — your honest call**; draft leans "Significant" |
| 17 | Gained AI career value? (27827: Yes/No) | Yes | **TODO — your honest call** |
| 18 | Not an employee of the sponsors (27828) | Yes | **TODO — confirm and check** |
| 19 | Eligible jurisdiction (27829) | Yes | **TODO — confirm and check** |
| 20 | Age of majority (27830) | Yes | **TODO — confirm and check** |

### Field 2 — testing credentials/instructions (optional)

> No account or credentials are needed to evaluate this project locally: `git clone`, then
> `./scripts/run-local.sh` brings up CockroachDB in Docker, applies the schema, and seeds fixture
> data in about a minute, with no AWS account and no CockroachDB Cloud account required
> (embeddings fall back to a deterministic local stub, clearly logged as such). `./scripts/demo.sh`
> then walks the full story end to end. [Once the demo app from Step 1 is deployed: replace this
> paragraph with the deployed URL and any login/testing notes it needs.]

### Field 5 — Which CockroachDB tools are used (pick ≥2)

Two of the four are genuinely verified against a live cluster today, with no external account
needed:

- **Distributed Vector Indexing** — `agent_memories.embedding` is a `VECTOR(512)` column with a
  prefix-partitioned index on `(tenant_id, verdict, embedding)`. `scripts/demo.sh` step 7 (and
  `scripts/record-demo.sh` beat 7) capture a live `EXPLAIN` plan proving the `vector search` node
  and `prefix spans` are real, not asserted.
- **Agent Skills Repo** — `skills/cockroachdb-observability-and-diagnostics/
  auditing-agent-memory-with-time-travel/SKILL.md` is an authored skill in the upstream
  frontmatter format (name/description/compatibility/license/metadata), documenting the
  `AS OF SYSTEM TIME` audit pattern this project uses, with every SQL statement and `EXPLAIN` plan
  in it copy-pasted from a real session against this project's own cluster.

The other two are **written but not yet run against a real account** (see Step 1) and should only
be checked once true:

- **Cloud Managed MCP Server** — `ARCHITECTURE.md` designs a read-only (`mcp:read`) service
  account as the judge-facing inspection path, but no CockroachDB Cloud cluster exists yet to
  connect it to.
- **ccloud CLI** — `infra/provision.sh` is written and its flags were checked against
  `ccloud --help` (`ccloud 0.8.23`), but it has never actually provisioned a cluster (no `ccloud`
  session on this machine).

**If Step 1 is done before you submit**, select all four and expand the integration explanation
(field 7) accordingly. **If it is not**, select the first two only — that still clears the "at
least two" requirement honestly.

### Field 7 — how meaningfully integrated (draft; finish after Step 1 and field 5/6 are final)

> CockroachDB's `VECTOR` column and prefix-partitioned vector index are not bolted on — they are
> the reason `recall()` scopes its ANN search to one tenant's admitted memories via a *server-side*
> index prefix `(tenant_id, verdict)`, verified with a live `EXPLAIN` (measured p50 1.60ms indexed
> vs 15.05ms brute-force at 10k rows / 50 tenants; see `README.md`). The Agent Skill documents the
> `AS OF SYSTEM TIME` pattern that `replay.cross_examine()` uses to pin a whole read to a decision's
> exact instant — the mechanism this project's headline feature (`grant_standing()`'s outcome gate)
> depends on to prove what an agent believed when it acted. [If Step 1 is complete: add a paragraph
> on the Managed MCP Server as the read-only judge-facing inspection path with zero application
> code in between, and `ccloud`'s role in provisioning the cluster and rotating the service-account
> credential non-interactively.] On the AWS side: [fill in once real — e.g. "AWS Lambda runs the
> entire backend behind a Function URL; Amazon Bedrock (Claude via Converse, Titan Text Embeddings
> V2) powers reasoning and embeddings, and is deliberately absent from the trust-promotion path —
> see `backend/trust.py`'s `assert_no_model_calls()`, which is checked on the live path, not just
> documented."]

### Field 9 — pre-existing code disclosure (condensed from `DISCLOSURES.md`)

> This repository's first commit is dated 2026-08-03, inside the Submission Period, with no earlier
> history. No source code from any prior repository was copied in. The author previously explored
> two unrelated *design instincts* on a different, non-CockroachDB, non-AWS personal project (an
> accepted/quarantine memory split, and treating an agent tool surface as needing identity/audit/a
> kill switch) — reimplemented here from scratch against CockroachDB-specific primitives (single-
> table MVCC history, `AS OF SYSTEM TIME`, SSM, IAM). No file, function body, or schema string was
> reused verbatim. Full detail in `DISCLOSURES.md`, checked into the repo so it's verifiable, not
> just asserted on this form.

### Field 11 — feedback on CockroachDB AI tools (optional; draft, edit freely)

> The vector index's requirement that the prefix columns (here `tenant_id, verdict`) be *equality*
> predicates for the optimizer to use it at all was the single biggest early trap — a query with an
> extra `embedding IS NOT NULL` predicate silently fell back to a full scan with no error, only a
> slow query. An `EXPLAIN`-based lint or a clearer optimizer hint here would have saved real time.
> Also: `AS OF SYSTEM TIME` rejecting use inside a subquery/CTE (`42601`) wasn't obvious from the
> error message alone; `SET TRANSACTION AS OF SYSTEM TIME` as the first statement of a transaction
> was the fix, but it took a spike script to find (see `SPIKE-RESULTS.md`).

### Field 15 — which AI tools leveraged

> Claude Code (Anthropic) was used throughout for architecture discussion, SQL and Python
> authoring, and documentation — disclosed in full in `DISCLOSURES.md`. Amazon Bedrock (Claude via
> the Converse API, and Titan Text Embeddings V2) is a **runtime component of the submitted agent
> itself**, not an authoring tool, and is covered separately under AWS services.

## 48 hours before the deadline (by 2026-08-16 17:00 ET)

- [ ] Step 1 (real AWS + ccloud) is done, or you've made a deliberate, honest call to submit with
      only local-verified CockroachDB tools and whichever AWS service you did get running.
- [ ] Repo is public (Step 2), license badge visible.
- [ ] `README.md`'s Status table reflects reality — no ✅ for anything that wasn't actually run.
- [ ] Video is recorded, edited, uploaded, and set to **Public**. Watch the public link once,
      logged out, start to finish.
- [ ] Demo app URL is live and has been hit from a browser that isn't yours (a different network,
      or ask someone else) to catch a firewall/CORS surprise before a judge does.
- [ ] Devpost project draft has every field in the table above filled in except the ones only you
      can answer (rows 12, 13, 14, 16, 17, 18, 19, 20).

## Day of (2026-08-18)

- [ ] Re-run `./scripts/demo.sh` (or `record-demo.sh --auto` for a quick smoke check) against
      whatever is live one more time. A cold cluster or an expired credential the morning of is the
      single most common last-day failure.
- [ ] Fill in the four checkbox/personal fields (12, 13, 18, 19, 20) and the two subjective ones
      (16, 17) — these cannot be pre-filled by anyone but you.
- [ ] Submit **well before 17:00 ET** — Devpost's deadline enforcement is exact; do not test it.
- [ ] After submitting, confirm the submission shows as **submitted** (not just saved as a draft)
      on your Devpost dashboard.
