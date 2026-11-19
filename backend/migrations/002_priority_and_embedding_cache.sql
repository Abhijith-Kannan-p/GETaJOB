-- Embed highest-priority jobs first (software/data/ML, intern/entry) so a partial nightly batch helps most.
ALTER TABLE jobs ADD COLUMN priority SMALLINT NOT NULL DEFAULT 0;
DROP INDEX IF EXISTS jobs_needs_embedding;
CREATE INDEX jobs_needs_embedding ON jobs (priority DESC, id) WHERE embedded_at IS NULL AND closed_at IS NULL;

-- Query/profile embedding cache keyed by sha256(model|dim|task|text). A profile whose facts did not
-- change hashes to the same key, so it is never re-embedded; survives Render restarts.
CREATE TABLE embedding_cache (
    key          TEXT PRIMARY KEY,
    embedding    vector({{EMBED_DIM}}) NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX embedding_cache_last_used ON embedding_cache (last_used_at);
