-- SPDX-License-Identifier: Apache-2.0
--
-- 001 — add the `attested` rung to the trust ladder.
--
-- WHY
-- ---
-- The trust ladder had three rungs: unconfirmed -> verified -> disputed. That schema could
-- not express the difference between "an external outcome was reported" and "we re-queried
-- the external system of record and it agreed", so the application had to call both of them
-- `verified`. Since the project's headline claim is precisely that trust comes from a checked
-- external signal, a schema that cannot represent the check forced the code to overstate.
--
--   unconfirmed : no outcome reported yet
--   attested    : an external outcome was reported, but this deployment could not
--                 independently re-check it (no PagerDuty token; a human sign-off has no
--                 system of record to re-query)
--   verified    : re-queried against the system of record, which agreed — see
--                 backend/evidence.py
--   disputed    : the outcome was a rollback or a false positive
--
-- ORDERING — READ THIS BEFORE DEPLOYING
-- ------------------------------------
-- Run this BEFORE deploying the code that writes `attested`. The old CHECK constraint
-- rejects the value, so a Lambda deployed ahead of this migration will fail every
-- /confirm_outcome that lands on the attested path. Migration first, then deploy.
--
-- Safe to run on a live cluster: it rewrites a CHECK constraint only. No rows are read,
-- written, or moved, and every existing value ('unconfirmed', 'verified', 'disputed')
-- remains valid under the new constraint, so there is nothing to backfill and nothing to
-- undo if you stop halfway.
--
-- Idempotent: re-running it is a no-op that succeeds.
--
--   cockroach sql --url "$MEMORYSTAND_DSN" -f db/migrations/001_attested_trust_tier.sql

ALTER TABLE agent_memories DROP CONSTRAINT IF EXISTS check_trust_tier;

ALTER TABLE agent_memories ADD CONSTRAINT check_trust_tier
    CHECK (trust_tier IN ('unconfirmed', 'attested', 'verified', 'disputed'));

-- Verify:
--   SELECT trust_tier, count(*) FROM agent_memories GROUP BY 1 ORDER BY 2 DESC;
