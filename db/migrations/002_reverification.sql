-- SPDX-License-Identifier: Apache-2.0
--
-- 002 — make a granted trust re-checkable later.
--
-- WHY
-- ---
-- Trust was a one-shot event. A memory promoted to `verified` in March stayed `verified` in
-- August, because nothing ever asked the system of record again. That is the obvious hole in
-- an argument built on "reality decides": reality keeps moving after the promotion, and a
-- runbook that was true when the outcome landed can be false a month later. Operational
-- folklore is mostly made of memories that WERE true.
--
-- Re-verification needs two things the schema could not express:
--
--   1. WHAT was checked. agent_decisions recorded outcome, the confirmation time and the
--      metric delta, but not the evidence source or its external reference -- those went only
--      into tool_audit.actor as a joined string like 'metric:AWS/Lambda|Duration|...'. Parsing
--      an audit label to re-run a check is fragile; the evidence belongs on the decision.
--
--   2. WHEN it was last checked. verdict_set_at moves for unrelated reasons (write-time
--      adjudication), so it cannot answer "is this trust stale?".
--
-- Both are nullable and default NULL, so every existing row stays valid and nothing needs
-- backfilling. A memory with trust_checked_at IS NULL has simply never been re-checked, which
-- is exactly true of everything promoted before this migration.
--
--   python db/migrate.py            # idempotent
--   python db/migrate.py --status
--
-- Safe on a live cluster: three ADD COLUMN IF NOT EXISTS statements. No rows are read,
-- rewritten or moved.

ALTER TABLE agent_decisions ADD COLUMN IF NOT EXISTS outcome_source STRING;

ALTER TABLE agent_decisions ADD COLUMN IF NOT EXISTS outcome_external_ref STRING;

ALTER TABLE agent_memories ADD COLUMN IF NOT EXISTS trust_checked_at TIMESTAMPTZ;

-- Verify:
--   SELECT trust_tier, count(*) FILTER (WHERE trust_checked_at IS NULL) AS never_rechecked,
--          count(*) AS total
--   FROM agent_memories GROUP BY trust_tier ORDER BY total DESC;
