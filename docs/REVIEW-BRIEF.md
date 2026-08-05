# Adversarial review brief — MemoryStand

Copy everything below the line into a fresh session with a strong model. Give it to each
reviewer **separately**, and do not show either one the other's answer until both have finished —
two independent reads are worth more than one read plus an echo.

If the reviewer can be given the repository, give it. If not, it still has enough here to argue
about the strategy, the framing, and the risks; tell it to mark anything it could not verify.

---

## ROLE

You are reviewing a hackathon submission that has not been submitted yet. There are 12 days left.
The person who built it wants to win outright, and has asked for a critical review rather than
encouragement.

Adopt this stance: **assume the submission will lose, and work out why.** Your job is to find the
reasons a judge would rank it second, and to say what would change that. Praise is only useful
where it is load-bearing — if something is genuinely strong, say so in one line and move on,
because the remaining time should go where it is weak.

Three specific instructions:

1. **Do not be agreeable.** If the core idea is wrong for this contest, say the core idea is
   wrong for this contest. A pivot recommendation 12 days out is a legitimate answer and is more
   useful than a list of polish items.
2. **Verify before you critique, and mark what you could not verify.** The summary below was
   written by the builder's assistant and has already been caught overstating things twice. Check
   claims against the repository where you can. Where you cannot, say "unverified" rather than
   assuming either way.
3. **Be concrete.** "Improve the documentation" is not actionable. "The README buries the only
   quantitative result under 200 lines of prior-art discussion; move the poisoning table above
   the fold" is.

## THE CONTEST

**CockroachDB × AWS Hackathon — Build with Agentic Memory.** <https://cockroachdb-ai.devpost.com>

- Submissions close **2026-08-18 21:00 UTC** (17:00 ET). Judging 2026-08-19 → 09-15. Winners
  2026-09-21.
- **Brief:** "Build an agentic application that uses CockroachDB as its persistent memory layer,
  deployed on AWS. Your agent should store, retrieve, and act on memory... The best submissions
  will demonstrate that memory is not an afterthought, it is the thing that makes an agent useful
  in production."
- **Mandatory:** at least **two** CockroachDB tools from {Cloud Managed MCP Server, Distributed
  Vector Indexing, ccloud CLI, Agent Skills Repo} and at least **one** AWS service.
- **Deliverables:** public repo with an OSS licence visible in the About sidebar; a URL to a
  *functional* demo app; a video **under 3 minutes** on YouTube or Vimeo, public, that
  "demonstrates your submission and the CockroachDB memory layer at work"; written answers on
  which CockroachDB and AWS components were used and *how*.

**The five judging criteria, verbatim and unweighted:**

1. **Agentic Memory Design** — "Does CockroachDB play a meaningful, production-grade role as the
   agent's memory layer? Is it used for more than toy queries — state, embeddings, context, or
   transactional data at real scale?"
2. **Technical Implementation** — "Is the integration with CockroachDB tools (distributed vector
   index, MCP Server, ccloud CLI) quality software engineering? Does the agent use the tools
   correctly and safely?"
3. **Real-World Impact** — "How big of an impact could the project have on real users or
   workflows? Is the use case meaningful, not just technically impressive?"
4. **Production Readiness** — "Is the design secure, observable, and scalable? Has the team
   thought about resilience, access control, and what happens when things go wrong?"
5. **Creativity & Originality** — "Is this a genuinely new idea or a novel application of the
   technology? Does it demonstrate insight into what makes agentic systems different from
   traditional apps?"

## WHAT WAS BUILT

**MemoryStand** — an agent memory store where a memory has to *earn* the right to be trusted.

The thesis: every shipping agent-memory system (Mem0, Zep, AWS AgentCore) ultimately asks a
language model whether its own memory is true. MemoryStand instead asks the system of record —
Amazon CloudWatch — and refuses to promote a claim the metric contradicts.

**The trust ladder.** A memory is `unconfirmed` → `attested` → `verified`, and can be `disputed`.
Promotion to `verified` requires re-querying CloudWatch and finding the claimed outcome actually
happened. The promotion path (`backend/trust.py`) makes **zero model calls** by construction: it
imports no model client, and a runtime guard fails if one becomes reachable even one import away.

**CockroachDB usage** (3 of the 4 required tools):
- `embedding VECTOR(512)` in the same relational table as the facts (`db/schema.sql:63`).
- `VECTOR INDEX agent_memories_tenant_idx (tenant_id, verdict, embedding vector_cosine_ops)` —
  the prefix columns matter; dropping `verdict` was measured to cause a full table scan
  (`db/schema.sql:74`, `SPIKE-RESULTS.md:97-114`).
- Recall filters by tenant + admission verdict and ranks by cosine distance **in one statement**
  (`backend/memory.py:369-381`). Trust tier is returned as a column and ranked afterwards in
  Python — *not* in the vector query.
- `AS OF SYSTEM TIME` time-travel endpoint: re-ask what the store believed at the moment a past
  decision was made.
- SERIALIZABLE-only, with 40001 retry handling; row-level TTL.
- Cloud Managed MCP Server verified live (handshake, `select_query`, `explain_query` confirming
  vector search) as an operator inspection surface, never on the app's write path.
- `ccloud` CLI provisions the cluster end-to-end (`infra/provision.sh`), no web console.

**AWS usage:** Lambda (Function URL, the whole API), **CloudWatch as a trust oracle rather than
as observability**, SSM SecureStrings for secrets, Amplify for the frontend, EventBridge for the
scheduled re-verification sweep, Bedrock (see weaknesses).

**Measured results** (all in `benchmarks/`):
- **Poisoning:** 540 poisoned claims across 5 attack classes + 60 honest controls = 600, run
  against three defences. Trust-the-caller admits 100% of attacks. LLM-as-judge admits 0% of
  attacks *and* 0% of honest claims. Outcome-gated: 0% of attacks reach `verified`, 100% of
  honest claims admitted.
- **Verification:** 204 cases that should have been refused — 204 false promotions before the
  gate, 0 after; holds across 5 seeds.
- **Scale:** 50,131 rows on the live CockroachDB Cloud cluster across 40 tenants; 250,000 rows
  across 200 tenants on a local cluster.
- **Concurrency:** 10 concurrent writers, 0 lost updates under real contention.
- **Failover:** reads keep succeeding through a node loss (3 nodes on one machine).

~15,000 lines, 135 tests, 80 commits.

## KNOWN WEAKNESSES — do not spend time rediscovering these

Listed so you can attack the *response* to them, and so you cannot be misled by the summary above.

1. **Bedrock on-demand quota on this AWS account is 0.** Consequences: embeddings are a
   deterministic **lexical** stub (feature hashing), not semantic — so "closest memory" means
   lexically closest; and `/decide` reasons through an Anthropic-shaped standby provider
   (`anthropic:claude-haiku-4-5`), not Bedrock. A support case is open. The zero-model-calls
   claim is unaffected because it concerns the *promotion* path.
2. **The MCP service account is write-capable** (`CLUSTER_OPERATOR_WRITER`). CockroachDB Cloud
   has no working read-only role for this server, so "read-only inspection" cannot be claimed.
3. **The failover test is 3 nodes on one machine**, not multi-region. It shows replication
   surviving a process loss, nothing about datacentre resilience.
4. **The LLM-judge benchmark arm was sampled at n=6 per class**, against n=60 for the other arms,
   because 540 sequential model calls took ~30 minutes. The 0%/0% figures come from that smaller
   sample.
5. **The live demo tenant has ~20 duplicate rows.** A live recall currently returns five
   near-identical memories at *identical* distance 0.547 — which undercuts any on-screen
   demonstration of "the closer memory lost to the more trusted one."
6. **The demo video is mid-rework and blocked** on exhausted Grok Build credits (5 of 17 clips
   ungenerated). The presenter is a synthetic likeness of the author.
7. **Not yet done:** repo is not confirmed public; the Devpost project has not been created; an
   API key exposed in a working transcript still needs rotation; the local AWS session is expired.

## WHAT I WANT FROM YOU

Answer these in order. Be specific and cite files where you can.

### 1. Predicted placement, with reasoning
Given the five criteria and what a strong competing field looks like for this specific contest,
where does this land — 1st, top 3, top 10, or nowhere? Say what you are assuming about the field.
**Give a number, then defend it.** A review that will not commit to a prediction is not useful.

### 2. The single biggest threat to winning
Not a list. One thing. What is the one weakness most likely to cost first place, and what is the
cheapest intervention that removes it inside 12 days?

### 3. Criterion-by-criterion
For each of the five criteria: score /10, the *specific* evidence a judge would find, and the
highest-leverage change. Be hard on **Agentic Memory Design** in particular — the criterion says
"more than toy queries... at real scale", and you should decide whether 50k rows of synthetic
load and a 131-row curated demo tenant actually satisfies that, or only appears to.

### 4. Is the framing right?
The project leads with *trust and verification*. The contest asks for *agentic memory*. Is the
pitch aimed at the rubric, or at a thesis the builder finds interesting? A submission can be
excellent and still lose by answering a question nobody asked. If you would reframe it, write the
new one-sentence pitch.

### 5. Should there be a new direction?
Consider this seriously rather than dismissing it. Options include: (a) stay the course and
polish; (b) keep the engine, re-aim the demo at a different use case with more obvious impact;
(c) add one substantial capability that lands squarely on a weak criterion; (d) something else.
If a pivot is right, say what to **stop** doing — 12 days is a real budget, and anything added
comes out of something else.

### 6. What is missing that a winner would have
Name things that are absent, not things that are imperfect. Absence is invisible to the builder,
which is exactly why an outside read is worth having.

### 7. The dishonesty check
This project's entire argument is that unverified claims must be labelled. Read the README,
`DISCLOSURES.md`, `benchmarks/*.md` and `docs/SUBMISSION.md` as a hostile judge looking for a
claim the evidence does not support. Anything you find is worth more than any improvement
suggestion, because a caught overclaim in a project about honesty is fatal rather than costly.

## OUTPUT FORMAT

```
## Verdict
<predicted placement, one paragraph of reasoning>

## The one thing
<the biggest threat, and the cheapest fix>

## Scorecard
| Criterion | /10 | What a judge sees | Highest-leverage change |

## Framing
<keep or reframe; if reframe, the new pitch in one sentence>

## Direction
<stay / re-aim / add / pivot — and what to stop doing>

## Missing
<absent things a winner would have>

## Overclaims found
<file:line, the claim, why the evidence does not support it — or "none found", and say what you checked>

## 12-day plan
<ordered, each item with rough hours and the criterion it moves>
```

## FINAL INSTRUCTION

End with the one sentence you would say to the builder if you only got one sentence, knowing they
want to win and have 12 days. Make it the true thing, not the kind thing.
