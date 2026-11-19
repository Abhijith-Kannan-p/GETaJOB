-- The vector dimension placeholder is substituted by app.db.migrate from EMBED_DIM (pgvector needs a fixed dimension).
CREATE EXTENSION IF NOT EXISTS vector;

-- array_to_string is STABLE, but generated columns need IMMUTABLE expressions. For text[] it is in
-- fact immutable, so wrap it (the standard workaround).
CREATE FUNCTION skills_text(text[]) RETURNS text LANGUAGE sql IMMUTABLE PARALLEL SAFE
    AS $$ SELECT array_to_string($1, ' ') $$;

CREATE TABLE jobs (
    id               BIGSERIAL PRIMARY KEY,
    source           TEXT NOT NULL CHECK (source IN ('greenhouse', 'lever')),
    external_id      TEXT NOT NULL,
    board            TEXT NOT NULL,
    company          TEXT NOT NULL,
    title            TEXT NOT NULL,
    location         TEXT,
    is_remote        BOOLEAN NOT NULL DEFAULT FALSE,
    seniority        TEXT NOT NULL DEFAULT 'unknown',
    description      TEXT NOT NULL DEFAULT '',
    required_skills  TEXT[] NOT NULL DEFAULT '{}',
    preferred_skills TEXT[] NOT NULL DEFAULT '{}',
    url              TEXT NOT NULL,
    posted_at        TIMESTAMPTZ,
    first_seen_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at        TIMESTAMPTZ,
    content_hash     TEXT NOT NULL,
    embedding        vector({{EMBED_DIM}}),
    embedded_at      TIMESTAMPTZ,
    tsv              tsvector GENERATED ALWAYS AS (
        setweight(to_tsvector('english', coalesce(title, '')), 'A') ||
        setweight(to_tsvector('english', skills_text(required_skills || preferred_skills)), 'B') ||
        setweight(to_tsvector('english', coalesce(description, '')), 'C')
    ) STORED,
    UNIQUE (source, external_id)
);
CREATE INDEX jobs_embedding_hnsw ON jobs USING hnsw (embedding vector_cosine_ops);
CREATE INDEX jobs_tsv_gin ON jobs USING gin (tsv);
CREATE INDEX jobs_active ON jobs (last_seen_at) WHERE closed_at IS NULL;
CREATE INDEX jobs_needs_embedding ON jobs (id) WHERE embedded_at IS NULL AND closed_at IS NULL;

CREATE TABLE profiles (
    session_id  TEXT PRIMARY KEY,
    facts       JSONB NOT NULL DEFAULT '{}',
    embedding   vector({{EMBED_DIM}}),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE plan_items (
    id          BIGSERIAL PRIMARY KEY,
    session_id  TEXT NOT NULL REFERENCES profiles(session_id) ON DELETE CASCADE,
    skill       TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'todo' CHECK (status IN ('todo', 'learning', 'done')),
    source      TEXT NOT NULL DEFAULT 'user',
    added_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (session_id, skill)
);

CREATE TABLE messages (
    id          BIGSERIAL PRIMARY KEY,
    session_id  TEXT NOT NULL REFERENCES profiles(session_id) ON DELETE CASCADE,
    role        TEXT NOT NULL,
    content     JSONB NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX messages_session ON messages (session_id, id);

CREATE TABLE ingest_runs (
    id           BIGSERIAL PRIMARY KEY,
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at  TIMESTAMPTZ,
    mode         TEXT NOT NULL,
    fetched      INT NOT NULL DEFAULT 0,
    inserted     INT NOT NULL DEFAULT 0,
    updated      INT NOT NULL DEFAULT 0,
    unchanged    INT NOT NULL DEFAULT 0,
    closed       INT NOT NULL DEFAULT 0,
    embedded     INT NOT NULL DEFAULT 0,
    errors       JSONB NOT NULL DEFAULT '[]'
);

CREATE TABLE usage_daily (
    day    DATE NOT NULL DEFAULT current_date,
    kind   TEXT NOT NULL,
    count  INT NOT NULL DEFAULT 0,
    PRIMARY KEY (day, kind)
);

CREATE TABLE events (
    day    DATE NOT NULL DEFAULT current_date,
    name   TEXT NOT NULL,
    count  INT NOT NULL DEFAULT 0,
    PRIMARY KEY (day, name)
);
