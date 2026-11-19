"""Load the committed fixture snapshot into Postgres with ZERO API calls:

    python -m app.ingest.seed --fixtures

Embeddings are precomputed in the file. If EMBED_DIM differs from the fixture vectors we skip the
vectors (full-text search still works) rather than crash.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime

from pgvector import Vector

from app import logs
from app.config import get_settings
from app.db import conn as db_conn
from app.db.migrate import migrate
from app.ingest.fixtures import FIXTURES
from app.ingest.run import JobRecord, RunStats, content_hash, upsert


def load_fixtures() -> list[dict]:
    return [json.loads(x) for x in FIXTURES.read_text(encoding="utf-8").splitlines() if x.strip()]


def seed(rows: list[dict]) -> RunStats:
    dim = get_settings().dim
    stats = RunStats(fetched=len(rows))
    recs = [
        JobRecord(
            source=d["source"],
            external_id=d["external_id"],
            board=d["board"],
            company=d["company"],
            title=d["title"],
            location=d["location"],
            is_remote=d["is_remote"],
            seniority=d["seniority"],
            description=d["description"],
            required_skills=d["required_skills"],
            preferred_skills=d["preferred_skills"],
            url=d["url"],
            posted_at=datetime.fromisoformat(d["posted_at"]) if d.get("posted_at") else None,
            content_hash=content_hash(d["title"], d["location"], d["description"]),
        )
        for d in rows
    ]
    with db_conn() as c:
        upsert(c, recs, stats)
        vec_rows = [
            (Vector(d["embedding"]), d["source"], d["external_id"])
            for d in rows
            if d.get("embedding") and len(d["embedding"]) == dim
        ]
        if len(vec_rows) < len(rows):
            stats.errors.append(f"{len(rows) - len(vec_rows)} fixtures had no vector of dim {dim}; FTS only for those")
        with c.transaction(), c.cursor() as cur:
            cur.executemany(
                "UPDATE jobs SET embedding = %s, embedded_at = now() WHERE source = %s AND external_id = %s",
                vec_rows,
            )
        stats.embedded = len(vec_rows)
        c.execute(
            """INSERT INTO ingest_runs (mode, finished_at, fetched, inserted, updated, unchanged, embedded, errors)
               VALUES ('fixtures', now(), %s, %s, %s, %s, %s, %s::jsonb)""",
            (stats.fetched, stats.inserted, stats.updated, stats.unchanged, stats.embedded, json.dumps(stats.errors)),
        )
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixtures", action="store_true", required=True)
    ap.parse_args()
    logs.setup(get_settings().log_level)
    migrate()
    st = seed(load_fixtures())
    print(
        f"seeded: inserted={st.inserted} updated={st.updated} unchanged={st.unchanged} "
        f"with_vectors={st.embedded} notes={st.errors}"
    )


if __name__ == "__main__":
    main()
