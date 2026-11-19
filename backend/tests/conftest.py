"""Test setup. Real LLM/embedding APIs are impossible here: key blanked, MOCK_LLM on, fakes injected.

DB tests use TEST_DATABASE_URL (default: a separate getajob_test DB on the docker-compose server)
and are skipped if Postgres is not reachable, so pure unit tests still run anywhere.
"""

import os

os.environ["GEMINI_API_KEY"] = ""
os.environ["MOCK_LLM"] = "1"
os.environ["EMBED_DIM"] = "64"
os.environ["EMBED_RPM"] = "1000"
os.environ["EMBED_RPD"] = "100000"
os.environ["HTTP_SLEEP_S"] = "0"
TEST_DB = os.environ.get("TEST_DATABASE_URL", "postgresql://getajob:getajob@localhost:5432/getajob_test")
os.environ["DATABASE_URL"] = TEST_DB

import psycopg  # noqa: E402
import pytest  # noqa: E402

from app.config import get_settings  # noqa: E402

get_settings.cache_clear()


def _ensure_db() -> bool:
    admin = TEST_DB.rsplit("/", 1)[0] + "/postgres"
    name = TEST_DB.rsplit("/", 1)[1]
    try:
        with psycopg.connect(admin, autocommit=True, connect_timeout=3) as c:
            if not c.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone():
                c.execute(f'CREATE DATABASE "{name}"')
        return True
    except psycopg.OperationalError:
        return False


_DB_OK = None


@pytest.fixture
def db():
    """Fresh schema per test: drop everything, migrate, hand out a pooled connection."""
    global _DB_OK
    if _DB_OK is None:
        _DB_OK = _ensure_db()
    if not _DB_OK:
        pytest.skip("Postgres not reachable (start docker compose or set TEST_DATABASE_URL)")
    from app.db import close_pool, conn
    from app.db.migrate import migrate
    from app.llm import quota

    close_pool()
    with psycopg.connect(TEST_DB, autocommit=True) as c:
        c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    migrate(TEST_DB)
    quota.reset_for_tests()
    with conn() as c:
        yield c
    close_pool()
