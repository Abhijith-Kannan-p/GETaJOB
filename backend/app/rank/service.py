"""DB-facing side of ranking: profile normalisation, corpus IDF, candidate pool. The maths stays in scorer.py."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime

from app.ingest.skills import load_taxonomy
from app.llm.client import Embedder
from app.rank.scorer import JUNIOR, JobFeatures, PoolContext, Profile, Weights, idf_from_counts, make_context
from app.search.hybrid import embed_cached, hybrid_search

IDF_TTL_S = 600
POOL_TTL_S = 600
POOL_CACHE_MAX = 64


@dataclass(frozen=True)
class ProfileInput:
    skills: tuple[str, ...]
    target_roles: tuple[str, ...] = ()
    seniority: tuple[str, ...] = ("intern", "new_grad", "entry")
    remote_ok: bool = True
    locations: tuple[str, ...] = ()
    free_text: str = ""


@dataclass
class NormalisedProfile:
    profile: Profile
    query_text: str
    unknown_skills: list[str]
    key: str  # stable hash of everything that affects the pool


def normalise(p: ProfileInput) -> NormalisedProfile:
    tax = load_taxonomy()
    known, unknown = [], []
    for raw in p.skills:
        c = tax.canonical(raw)
        (known if c else unknown).append(c or raw.strip())
    skills = sorted(set(known))
    roles = [" ".join(r.split()) for r in p.target_roles if r.strip()]
    text = " ".join(p.free_text.split())[:300]
    # Deterministic text => deterministic embedding cache key: unchanged facts are never re-embedded.
    query_text = "; ".join(x for x in [", ".join(roles), ", ".join(skills), text] if x) or "software engineering intern"
    seniority = frozenset(p.seniority) or JUNIOR
    prof = Profile(
        frozenset(skills), seniority, p.remote_ok, tuple(sorted({x.strip() for x in p.locations if x.strip()}))
    )
    key = hashlib.sha256(
        json.dumps([query_text, sorted(seniority), p.remote_ok, prof.locations], sort_keys=True).encode()
    ).hexdigest()
    return NormalisedProfile(prof, query_text, sorted(set(unknown)), key)


# ---------- corpus IDF (shared, refreshed every 10 min) ----------
_idf_lock = threading.Lock()
_idf: tuple[float, dict[str, float]] | None = None


def corpus_idf(c) -> dict[str, float]:
    global _idf
    with _idf_lock:
        if _idf and time.monotonic() - _idf[0] < IDF_TTL_S:
            return _idf[1]
    n = c.execute("SELECT count(*) AS n FROM jobs WHERE closed_at IS NULL").fetchone()["n"]
    rows = c.execute(
        """SELECT s, count(*) AS df FROM jobs, unnest(required_skills || preferred_skills) AS s
           WHERE closed_at IS NULL GROUP BY s"""
    ).fetchall()
    idf = idf_from_counts({r["s"]: r["df"] for r in rows}, max(n, 1))
    with _idf_lock:
        _idf = (time.monotonic(), idf)
    return idf


# ---------- candidate pool ----------


@dataclass
class Pool:
    features: list[JobFeatures]
    ctx: PoolContext
    cards: dict[int, dict]
    degraded: str | None
    profile: NormalisedProfile


_pools: OrderedDict[str, tuple[float, Pool]] = OrderedDict()
_pools_lock = threading.Lock()


def _features(r: dict, now: datetime) -> JobFeatures:
    posted = r.get("posted_at")
    return JobFeatures(
        id=r["id"],
        sim=None if r.get("sim_all") is None else float(r["sim_all"]),
        kw=float(r.get("kw_all") or 0.0),
        required=tuple(r["required_skills"]),
        preferred=tuple(r["preferred_skills"]),
        seniority=r["seniority"],
        is_remote=r["is_remote"],
        location=r["location"] or "",
        age_days=(now - posted).total_seconds() / 86400 if posted else None,
    )


_ONE_JOB = """
SELECT j.id, j.source, j.company, j.title, j.location, j.is_remote, j.seniority, j.required_skills,
       j.preferred_skills, j.url, j.posted_at, left(j.description, 400) AS snippet,
       CASE WHEN %(qvec)s::vector IS NULL OR j.embedding IS NULL THEN NULL
            ELSE 1 - (j.embedding <=> %(qvec)s::vector) END AS sim_all,
       ts_rank_cd(j.tsv, to_tsquery('english', replace(plainto_tsquery('english', %(q)s)::text, ' & ', ' | ')), 32)
         AS kw_all
FROM jobs j WHERE j.id = %(id)s AND j.closed_at IS NULL
"""


def build_pool(
    c,
    embedder: Embedder | None,
    np: NormalisedProfile,
    w: Weights,
    include_job: int | None = None,
    now: datetime | None = None,
) -> Pool:
    """Hybrid retrieval of the top `pool_size` candidates for this profile (+ an explicitly requested job)."""
    cache_key = f"{np.key}|{include_job}"
    with _pools_lock:
        hit = _pools.get(cache_key)
        if hit and time.monotonic() - hit[0] < POOL_TTL_S:
            _pools.move_to_end(cache_key)
            return hit[1]
    now = now or datetime.now(UTC)
    qvec = embed_cached(c, embedder, np.query_text, "RETRIEVAL_QUERY")
    degraded = None if qvec is not None else "fulltext_only"
    rows = hybrid_search(c, np.query_text, qvec, limit=w.pool_size, k=w.pool_size)
    if include_job is not None and all(r["id"] != include_job for r in rows):
        from pgvector import Vector

        extra = c.execute(
            _ONE_JOB, {"qvec": Vector(qvec) if qvec is not None else None, "q": np.query_text, "id": include_job}
        ).fetchone()
        if extra:
            rows.append(extra)
    feats = [_features(r, now) for r in rows]
    pool = Pool(feats, make_context(feats, corpus_idf(c)), {r["id"]: r for r in rows}, degraded, np)
    with _pools_lock:
        _pools[cache_key] = (time.monotonic(), pool)
        while len(_pools) > POOL_CACHE_MAX:
            _pools.popitem(last=False)
    return pool


def clear_caches_for_tests() -> None:
    global _idf
    _idf = None
    _pools.clear()
