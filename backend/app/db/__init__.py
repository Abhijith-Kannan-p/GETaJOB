"""Connection pool. psycopg3 + pgvector adapter registered on every connection."""

from __future__ import annotations

from contextlib import contextmanager

from pgvector.psycopg import register_vector
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.config import get_settings

_pool: ConnectionPool | None = None


def _configure(conn) -> None:
    register_vector(conn)
    # HNSW returns ef_search candidates before WHERE filtering; 100 keeps top-100 full after closed_at filter
    conn.execute("SET hnsw.ef_search = 100")


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        # Small pool: Render free = 0.1 CPU / 512 MB, Neon free has a connection cap.
        _pool = ConnectionPool(
            get_settings().database_url,
            min_size=1,
            max_size=4,
            # prepare_threshold=None: no server-side prepared statements, safe behind PgBouncer-style poolers
            kwargs={"row_factory": dict_row, "autocommit": True, "prepare_threshold": None},
            configure=_configure,
            open=True,
        )
    return _pool


@contextmanager
def conn():
    with get_pool().connection() as c:
        yield c


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None
