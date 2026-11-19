"""FastAPI app. Run: uvicorn app.main:app --port 8000 (one worker)."""

from __future__ import annotations

import json
import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded

from app import logs
from app.api.limits import limiter
from app.api.rank import router as rank_router
from app.api.serialize import job_out
from app.config import BACKEND_DIR, get_settings
from app.db import close_pool
from app.db import conn as db_conn
from app.db.migrate import migrate
from app.llm.client import get_embedder
from app.rank.scorer import TERMS, load_weights
from app.search.hybrid import SearchFilters, embed_query, hybrid_search

log = logging.getLogger("getajob.api")
EVAL_RESULTS = BACKEND_DIR.parent / "eval" / "results.json"
SENIORITIES = {"intern", "new_grad", "entry", "mid", "senior", "unknown"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    logs.setup(s.log_level)
    # Render free has no pre-deploy hook; migrations are idempotent so we apply them at boot.
    try:
        migrate()
    except Exception:  # noqa: BLE001 - /health must still come up so the deploy can be diagnosed
        log.exception("migration failed at startup")
    app.state.embedder = get_embedder()
    log.info(
        "startup",
        extra={
            "semantic": app.state.embedder is not None,
            "mock_llm": s.mock_llm,
            "cors_origins": s.cors_origin_list,
            "cors_origin_regex": s.cors_origin_regex,
        },
    )
    yield
    close_pool()


app = FastAPI(title="GETaJOB API", lifespan=lifespan)
app.state.limiter = limiter
app.include_router(rank_router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_origin_regex=get_settings().cors_origin_regex or None,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    token = logs.request_id.set(rid)
    t0 = time.perf_counter()
    try:
        response = await call_next(request)
    finally:
        logs.request_id.reset(token)
    ms = (time.perf_counter() - t0) * 1000
    response.headers["X-Request-ID"] = rid
    if request.url.path != "/health":
        log.info(
            "request", extra={"rid": rid, "path": request.url.path, "status": response.status_code, "ms": round(ms, 1)}
        )
    return response


@app.exception_handler(RateLimitExceeded)
async def _rate_limited(request: Request, exc: RateLimitExceeded):
    return JSONResponse({"error": "rate_limited", "message": "Too many requests. Please wait a moment."}, 429)


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception):
    log.exception("unhandled error")  # full detail stays in logs; users never see raw errors
    return JSONResponse({"error": "internal", "message": "Something went wrong. Please try again."}, 500)


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/api/stats")
def stats():
    with db_conn() as c:
        j = c.execute(
            """SELECT count(*) AS indexed,
                      count(*) FILTER (WHERE closed_at IS NULL) AS active,
                      count(*) FILTER (WHERE closed_at IS NULL AND first_seen_at > now() - interval '24 hours')
                        AS new_24h,
                      count(*) FILTER (WHERE closed_at IS NULL AND embedded_at IS NOT NULL) AS embedded,
                      count(DISTINCT company) FILTER (WHERE closed_at IS NULL) AS companies
               FROM jobs"""
        ).fetchone()
        last = c.execute(
            "SELECT finished_at, mode FROM ingest_runs WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 1"
        ).fetchone()
        levels = c.execute(
            "SELECT seniority, count(*) AS n FROM jobs WHERE closed_at IS NULL GROUP BY 1 ORDER BY 2 DESC"
        ).fetchall()
        top = c.execute(
            "SELECT company, count(*) AS n FROM jobs WHERE closed_at IS NULL GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 12"
        ).fetchall()
    eval_summary = None
    if EVAL_RESULTS.exists():
        try:
            eval_summary = json.loads(EVAL_RESULTS.read_text(encoding="utf-8")).get("summary")
        except ValueError:
            eval_summary = None
    active = j["active"] or 0
    return {
        "jobs_indexed": j["indexed"],
        "jobs_active": active,
        "new_24h": j["new_24h"],
        "companies": j["companies"],
        "embedding_coverage": round(j["embedded"] / active, 3) if active else 0.0,
        "last_sync": last["finished_at"].isoformat() if last else None,
        "last_sync_mode": last["mode"] if last else None,
        "semantic_search": app.state.embedder is not None,
        "by_seniority": {r["seniority"]: r["n"] for r in levels},
        "top_companies": [{"company": r["company"], "jobs": r["n"]} for r in top],
        "ranking_weights": {t: load_weights().of(t) for t in TERMS},
        "eval": eval_summary,
    }


@app.get("/api/search")
@limiter.limit("30/minute")
def search(
    request: Request,
    q: str = Query(..., min_length=2, max_length=200),
    limit: int = Query(20, ge=1, le=50),
    remote: bool = False,
    seniority: str | None = Query(None, description="comma-separated: intern,new_grad,entry,mid,senior"),
):
    levels = [x for x in (seniority or "").split(",") if x in SENIORITIES] or None
    t0 = time.perf_counter()
    with db_conn() as c:
        qvec, degraded = embed_query(c, app.state.embedder, q)
        rows = hybrid_search(c, q, qvec, limit, SearchFilters(remote_only=remote, seniority=levels))
    return {
        "query": q,
        "degraded": degraded,
        "took_ms": round((time.perf_counter() - t0) * 1000, 1),
        "results": [job_out(r) for r in rows],
    }


@app.get("/api/jobs/{job_id}")
def get_job(job_id: int):
    with db_conn() as c:
        r = c.execute(
            """SELECT id, source, company, title, location, is_remote, seniority, description, required_skills,
                      preferred_skills, url, posted_at, first_seen_at, last_seen_at, closed_at FROM jobs
               WHERE id = %s""",
            (job_id,),
        ).fetchone()
    if r is None:
        raise HTTPException(404, "job not found")
    return {**job_out(r), "description": r["description"], "closed": r["closed_at"] is not None}
