# Disclosures

The hackathon rules require that projects be newly created during the Submission Period
(2026-06-30 to 2026-08-18) and that any pre-existing code or work incorporated into the project
be disclosed. This file is that disclosure, kept in the repo so it is verifiable rather than
merely asserted on a form.

## Project start date

**2026-08-03.** The first commit in this repository's git history is dated that day, inside the
Submission Period. There is no earlier history: this repository was initialised from empty.

## Pre-existing code

**No source code from any prior repository was copied into this project.** All SQL schema,
Lambda handlers, Bedrock integration, MCP and Agent Skill wiring, spike scripts, provisioning
scripts, and frontend code in this repository were newly written during the Submission Period.

The author has previously worked on a personal, unrelated Apache-2.0 project that explored two
*design instincts* which carried over as thinking, not as code:

1. **A memory store whose writes must clear a check before they become readable.** That earlier
   project was SQLite-based and used a two-table accepted/quarantine split with an application-level
   verifier. MemoryStand does not reuse that structure. It uses a *single* table whose own MVCC history is
   the audit trail, adjudication inside a serializable transaction, and CockroachDB's
   `AS OF SYSTEM TIME` for replay — mechanisms that did not exist in the earlier work and are
   specific to CockroachDB.

2. **Treating an agent tool surface as something that needs identity, an audit log, and a kill
   switch.** Re-implemented here from scratch against different primitives: a SQL `tool_audit`
   table with native row-level TTL, AWS SSM for secrets, IAM scoped to named model ARNs, and
   separately-scoped CockroachDB Cloud service accounts.

No file, function body, schema string, or configuration from that project was reused verbatim.
The earlier project is not a CockroachDB project and not an AWS project.

## What is and is not novel here

Stated narrowly on purpose. A survey of the 2026 agent-memory landscape (Mem0, Zep/Graphiti,
Letta/MemGPT, Cognee, LangMem, OpenAI's and Anthropic's memory features) and the related
literature found that two of this project's three mechanics already exist elsewhere:

- **Bitemporal / time-travel replay of an agent's belief state is prior art.** It is Zep/Graphiti's
  shipped headline feature, is the subject of multiple 2026 papers (TOKI; Memento; graph-native
  bitemporal memory stores), and descends from SQL:2011's `FOR SYSTEM_TIME AS OF`. Using
  CockroachDB's native `AS OF SYSTEM TIME` instead of an application-level version table is an
  engineering choice, **not** a novelty claim.
- **Write-time contradiction checking is prior art.** Mem0's ADD/UPDATE/DELETE/NONE pipeline,
  Graphiti's ingest-time conflict detection, and MemTX's multi-step commit gate all do a version
  of it, in places with the same vocabulary this schema uses.

**Correction, and it is the kind this document exists for.** An earlier version of this
paragraph claimed that academic work (GLOVE; "Supersede") "identifies this specific gap as open".
Both citations were checked and **neither supports the claim**:

- [GLOVE](https://arxiv.org/abs/2601.19249) says the opposite of what it was cited for. It
  describes memory validity as being established "either through external task-success signals
  ... or through internal model cognition" — so grounding memory validity in external task
  success is presented as one of *two existing assumptions*, not an unfilled gap. GLOVE's actual
  contribution is that **both** assumptions break under environment drift.
- [Supersede](https://arxiv.org/abs/2606.27472) concerns the memory-update gap — agents failing
  to prefer current facts over stale ones — and reduces it by RL training against a benchmark.
  It never frames external real-world outcome verification as an open problem.

Using a paper as evidence for a claim it does not make is exactly the failure mode this project
argues against, and it appeared in the document about honesty. It is recorded rather than
quietly deleted.

**The claim this submission actually makes**, stated so it can be checked:

> Trust is granted only by an external non-model signal, on a promotion path that makes zero
> model calls, with credit assigned through the decision that produced the memory — and where
> the signal is machine-checkable, it is re-checked against the system of record before it counts.

That is a claim about **enforcement, not about priority of idea**. The idea is old:
[Doyle's JTMS (1979)](https://cse.buffalo.edu/~rapaport/Papers/Papers.by.Others/NONMONOTONIC/doyle79.pdf)
retracts a belief when its justification fails, and
[CHEF (1986)](https://aaai.org/papers/00267-aaai86-044-chef-a-model-of-case-based-planning/)
retains cases indexed by whether the plan worked. Even the trust ladder has precedent —
[NELL](https://cdn.aaai.org/ojs/7519/7519-13-11049-1-2-20201228.pdf) promoted candidate beliefs
to a trusted KB above a 0.9 confidence threshold, though on its own extractors corroborating each
other, which is self-consistency at scale (74% precision) rather than external grounding.

What is unusual is a 2026 memory system that refuses to let a model grade its own memory and
makes the refusal structural — while every shipping system does the opposite: Mem0 has no
per-memory confidence score at all, Zep's validity windows are LLM-set and documented by Zep as
"not authoritative", and AWS Bedrock AgentCore performs extraction, consolidation and reflection
with LLM prompts and exposes no verification API. See the README's comparison table for the
per-vendor citations.

This scoping is deliberate: an overstated novelty claim that a judge could disprove with one
search would undermine the entire submission, which is itself about not trusting unverified claims.

## Third-party dependencies

Standard open-source libraries only, used under their own licenses and declared in
`requirements.txt`. No vendored third-party source.

## The reasoning provider: a third-party router, used deliberately and named honestly

The deployed agent reasons through **`https://api.teamorouter.com`**, a third-party
Anthropic-compatible router serving `claude-haiku-4-5`. This is a deliberate, ongoing choice, and
it is worth reading how it got here, because for two days it was the exact failure this project
exists to condemn.

**Why a router at all.** Every Bedrock inference quota on this AWS account is 0
(`docs/BEDROCK_QUOTA.md`), so Bedrock — the preferred provider, tried first on every request —
never answers. The alternative to a working standby is an agent that never reasons with a model
at all, which is the weakest thing a submission can say. The router is the model credential to
hand.

**The failure.** From 2026-08-04 to 2026-08-06 that endpoint was configured, but `/decide`
reported `reasoning_source: anthropic:claude-haiku-4-5` — a label that reads as a direct Anthropic
connection and was not one. The endpoint appeared in exactly one line of the repository and in no
README, disclosure, or submission field. A project whose whole argument is that a claim must name
the thing that produced it shipped a claim that did not. It was caught by an outside adversarial
review, not by us.

**What changed.** The label is no longer hand-written. `anthropic_client.provider_label()` derives
it from the endpoint in use, so live `/decide` now reports
`reasoning_source: api.teamorouter.com:claude-haiku-4-5` — it names the router, and a future
redeploy against any other gateway relabels itself. The `/decide` captures quoted in `README.md`
and under `docs/demo/` are real responses recorded against this router; their `model_calls: 1` and
their latency are router figures.

**What you are trusting.** Prompts and the SSM-held API key traverse `teamorouter.com`. That is a
real third party in the request path, stated plainly so a judge does not have to discover it. The
**promotion path is unaffected** — `backend/trust.py` imports no model client and a runtime guard
fails if one becomes reachable, so no third party is ever in the path that grants trust. The API
key is present in this project's development history and **must be rotated after the contest.**

## AI tools used

Claude Code (Anthropic) was used throughout the build for architecture discussion, SQL and Python
authoring, and documentation.

Amazon Bedrock (Amazon Nova Lite via the Converse API — not Claude; Anthropic models on
Bedrock are refused from this operator's country, see `docs/BEDROCK_QUOTA.md` — and Amazon
Titan Text Embeddings V2) is disclosed
separately under AWS services rather than here, because it is a **runtime component of the
submitted agent itself** — not a tool used to author the code.
