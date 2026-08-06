-- 003: persist the retrieval that produced a decision, so cross-examination can re-run it.
--
-- WHY. `replay.recall_as_of()` already re-runs the agent's own ranked vector query pinned to a
-- past instant -- same ORDER BY, same k, inside a transaction fixed at that timestamp. It was
-- dead code: nothing called it, because nothing knew what the original query text was.
--
-- So `cross-examine` fell back to `belief_state_at()`, which returns every accepted memory as of
-- the instant. That is a useful audit surface but it is NOT the agent's retrieval: no ranking, no
-- distances, no k. The README and the Devpost copy both claimed the stronger thing ("re-runs the
-- agent's exact recall query") until an outside review caught it, and the honest short-term fix
-- was to weaken the wording.
--
-- This is the real fix: record what was actually asked, and the claim becomes true again.
--
-- NULLABLE ON PURPOSE. Every decision written before this migration has no recorded query, and
-- inventing one would be exactly the sort of fabricated provenance this project exists to refuse.
-- `cross_examine` returns `recalled_as_of: null` with a reason for those rows, rather than
-- silently showing a belief-state dump and letting a reader assume it is a re-ranked recall.

ALTER TABLE agent_decisions ADD COLUMN IF NOT EXISTS query_text STRING;
ALTER TABLE agent_decisions ADD COLUMN IF NOT EXISTS recall_k INT;
