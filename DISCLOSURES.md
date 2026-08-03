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
   verifier. AsOf does not reuse that structure. It uses a *single* table whose own MVCC history is
   the audit trail, adjudication inside a serializable transaction, and CockroachDB's
   `AS OF SYSTEM TIME` for replay — mechanisms that did not exist in the earlier work and are
   specific to CockroachDB.

2. **Treating an agent tool surface as something that needs identity, an audit log, and a kill
   switch.** Re-implemented here from scratch against different primitives: a SQL `tool_audit`
   table with native row-level TTL, AWS SSM for secrets, IAM scoped to named model ARNs, and
   separately-scoped CockroachDB Cloud service accounts.

No file, function body, schema string, or configuration from that project was reused verbatim.
The earlier project is not a CockroachDB project and not an AWS project.

**What is genuinely new in AsOf**, and what the originality of this submission rests on:
using `AS OF SYSTEM TIME` as a *user-facing product feature* — so an agent's memory can be
cross-examined about what it believed at the instant it acted — combined with a trust tier that
is promoted only by a confirmed real-world outcome rather than by a model's opinion of itself.

## Third-party dependencies

Standard open-source libraries only, used under their own licenses and declared in
`requirements.txt`. No vendored third-party source.

## AI tools used

Claude Code (Anthropic) was used throughout the build for architecture discussion, SQL and Python
authoring, and documentation.

Amazon Bedrock (Claude via the Converse API, and Amazon Titan Text Embeddings V2) is disclosed
separately under AWS services rather than here, because it is a **runtime component of the
submitted agent itself** — not a tool used to author the code.
