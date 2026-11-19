"""Tiny forward-only migration runner: python -m app.db.migrate

Applies migrations/NNN_*.sql in order inside a transaction each, records them in schema_migrations.
Plain SQL keeps the schema reviewable and avoids an ORM in a 512 MB container.
"""

from __future__ import annotations

import logging

import psycopg

from app.config import BACKEND_DIR, get_settings

log = logging.getLogger("getajob.migrate")
MIGRATIONS = BACKEND_DIR / "migrations"


def migrate(database_url: str | None = None) -> list[str]:
    s = get_settings()
    url = database_url or s.database_url
    applied: list[str] = []
    with psycopg.connect(url, autocommit=True) as c:
        c.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ DEFAULT now())"
        )
        done = {r[0] for r in c.execute("SELECT name FROM schema_migrations")}
        for f in sorted(MIGRATIONS.glob("*.sql")):
            if f.name in done:
                continue
            sql = f.read_text(encoding="utf-8").replace("{{EMBED_DIM}}", str(s.dim))
            with c.transaction():
                c.execute(sql)
                c.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (f.name,))
            applied.append(f.name)
            log.info("applied %s", f.name)
    return applied


if __name__ == "__main__":
    logging.basicConfig(level="INFO")
    print("applied:", migrate() or "nothing (up to date)")
