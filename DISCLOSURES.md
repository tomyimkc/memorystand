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
   verifier. Standing does not reuse that structure. It uses a *single* table whose own MVCC history is
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

**The one claim this submission makes to originality** is narrower: promoting a stored memory's
trust tier only on a *verified external real-world outcome* — a resolved incident, a recovered
metric, a human sign-off — with no model call anywhere in the promotion path. Every shipped
product surveyed uses recency, source authority, or model self-consistency instead. Academic work
(GLOVE; "Supersede") identifies this specific gap as open. The application of that idea to on-call
incident response, on CockroachDB, is what is new.

This scoping is deliberate: an overstated novelty claim that a judge could disprove with one
search would undermine the entire submission, which is itself about not trusting unverified claims.

## Third-party dependencies

Standard open-source libraries only, used under their own licenses and declared in
`requirements.txt`. No vendored third-party source.

## AI tools used

Claude Code (Anthropic) was used throughout the build for architecture discussion, SQL and Python
authoring, and documentation.

Amazon Bedrock (Claude via the Converse API, and Amazon Titan Text Embeddings V2) is disclosed
separately under AWS services rather than here, because it is a **runtime component of the
submitted agent itself** — not a tool used to author the code.
