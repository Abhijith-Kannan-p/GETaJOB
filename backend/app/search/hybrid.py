"""Hybrid retrieval: vector top-K + full-text top-K fused with Reciprocal Rank Fusion, in ONE SQL query.

Why RRF: cosine similarity and ts_rank live on different scales; fusing by *rank* needs no tuning
and is robust when one side is missing (degraded mode = no query vector -> FTS side only).
Each side is ranked inside its own LIMITed subquery so Postgres can use the HNSW / GIN index.
"""

from __future__ import annotations

import hashlib
import logging
from collections import OrderedDict
from dataclasses import dataclass

from pgvector import Vector

from app.llm import quota
from app.llm.client import Embedder, ProviderUnavailable

log = logging.getLogger("getajob.search")

RRF_K = 60
CANDIDATES = 100

_VEC = """
  SELECT id, sim, row_number() OVER (ORDER BY sim DESC) AS r FROM (
    SELECT id, 1 - (embedding <=> %(qvec)s) AS sim FROM jobs
    WHERE closed_at IS NULL AND embedding IS NOT NULL {filters}
    ORDER BY embedding <=> %(qvec)s LIMIT %(k)s
  ) v
"""
_NO_VEC = "SELECT NULL::bigint AS id, NULL::float8 AS sim, NULL::bigint AS r WHERE false"

_SQL = """
-- OR the stemmed terms: AND (websearch_to_tsquery) returned nothing for "frontend react internship";
-- ts_rank_cd still ranks rows matching more terms higher.
WITH q AS (SELECT to_tsquery('english', replace(plainto_tsquery('english', %(q)s)::text, ' & ', ' | ')) AS tsq),
vec AS ({vec}),
fts AS (
  SELECT id, kw, row_number() OVER (ORDER BY kw DESC, id) AS r FROM (
    SELECT j.id, ts_rank_cd(j.tsv, q.tsq, 32) AS kw FROM jobs j, q
    WHERE j.closed_at IS NULL AND j.tsv @@ q.tsq {filters}
    ORDER BY kw DESC, j.id LIMIT %(k)s
  ) f
),
fused AS (
  SELECT coalesce(vec.id, fts.id) AS id,
         coalesce(1.0 / (%(rrf_k)s + vec.r), 0) + coalesce(1.0 / (%(rrf_k)s + fts.r), 0) AS rrf,
         vec.sim, fts.kw, vec.r AS vec_rank, fts.r AS fts_rank
  FROM vec FULL OUTER JOIN fts ON vec.id = fts.id
)
SELECT j.id, j.source, j.company, j.title, j.location, j.is_remote, j.seniority, j.required_skills,
       j.preferred_skills, j.url, j.posted_at, left(j.description, 400) AS snippet,
       f.rrf, f.sim, f.kw, f.vec_rank, f.fts_rank,
       -- the ranker needs BOTH signals for every pooled job, not just the side that retrieved it
       {sim_all} AS sim_all, ts_rank_cd(j.tsv, q.tsq, 32) AS kw_all
FROM fused f JOIN jobs j ON j.id = f.id CROSS JOIN q
ORDER BY f.rrf DESC, j.id
LIMIT %(limit)s
"""


@dataclass
class SearchFilters:
    remote_only: bool = False
    seniority: list[str] | None = None


def _filters_sql(f: SearchFilters, alias: str = "") -> str:
    p = f"{alias}." if alias else ""
    parts = []
    if f.remote_only:
        parts.append(f"AND {p}is_remote")
    if f.seniority:
        parts.append(f"AND {p}seniority = ANY(%(seniority)s)")
    return " ".join(parts)


def hybrid_sql(has_vector: bool, f: SearchFilters) -> str:
    vec = _VEC.format(filters=_filters_sql(f)) if has_vector else _NO_VEC
    sim_all = (
        "CASE WHEN j.embedding IS NULL THEN NULL ELSE 1 - (j.embedding <=> %(qvec)s) END"
        if has_vector
        else "NULL::float8"
    )
    return _SQL.format(vec=vec, filters=_filters_sql(f, "j"), sim_all=sim_all)


def hybrid_search(
    c, q: str, qvec: list[float] | None, limit: int = 20, filters: SearchFilters | None = None, k: int = CANDIDATES
) -> list[dict]:
    f = filters or SearchFilters()
    params = {"q": q, "qvec": qvec, "k": k, "limit": limit, "rrf_k": RRF_K, "seniority": f.seniority}
    if qvec is not None:
        params["qvec"] = Vector(qvec)
    return c.execute(hybrid_sql(qvec is not None, f), params).fetchall()


# --- query/profile embeddings: in-memory LRU -> Postgres cache -> API (live share of the daily quota) ---
_lru: OrderedDict[str, list[float]] = OrderedDict()
_LRU_MAX = 256


def cache_key(embedder: Embedder, task: str, text: str) -> str:
    model = getattr(embedder, "model", type(embedder).__name__)
    raw = f"{model}|{embedder.dim}|{task}|{text}"
    return hashlib.sha256(raw.encode()).hexdigest()


def embed_cached(c, embedder: Embedder | None, text: str, task: str = "RETRIEVAL_QUERY") -> list[float] | None:
    """Vector for `text`, or None if it cannot be had (no embedder, quota used up, provider down).

    The key hashes model|dim|task|text, so a profile whose facts did not change is never re-embedded,
    and a model/dim change can never serve a vector from the wrong space.
    """
    if embedder is None:
        return None
    key = cache_key(embedder, task, text)
    if key in _lru:
        _lru.move_to_end(key)
        return _lru[key]
    row = c.execute(
        "UPDATE embedding_cache SET last_used_at = now() WHERE key = %s RETURNING embedding", (key,)
    ).fetchone()
    if row is not None:
        v = [float(x) for x in row["embedding"].to_list()]
    else:
        try:
            v = embedder.embed([text], task=task, conn=c, purpose="query")[0]
        except (quota.QuotaExceeded, ProviderUnavailable) as e:
            log.info("query embedding unavailable; full-text only", extra={"reason": type(e).__name__})
            return None
        c.execute(
            "INSERT INTO embedding_cache (key, embedding) VALUES (%s, %s) ON CONFLICT (key) DO NOTHING",
            (key, Vector(v)),
        )
    _lru[key] = v
    if len(_lru) > _LRU_MAX:
        _lru.popitem(last=False)
    return v


def normalise_query(q: str) -> str:
    return " ".join(q.lower().split())


def embed_query(c, embedder: Embedder | None, q: str) -> tuple[list[float] | None, str | None]:
    """Returns (vector, degraded_reason). degraded_reason is None when semantic search is on."""
    v = embed_cached(c, embedder, normalise_query(q), "RETRIEVAL_QUERY")
    return (v, None) if v is not None else (None, "fulltext_only")


def clear_lru_for_tests() -> None:
    _lru.clear()
