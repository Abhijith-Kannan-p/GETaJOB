"""Ranking + Why-Not-Me endpoints. Stateless for now: the profile travels in the request body
(slice 4 persists it per anonymous session)."""

from __future__ import annotations

from dataclasses import asdict
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from app.api.limits import limiter
from app.api.serialize import job_out
from app.db import conn as db_conn
from app.ingest.skills import load_taxonomy
from app.rank.scorer import TERMS, load_weights
from app.rank.service import ProfileInput, build_pool, corpus_idf, normalise
from app.whynot.engine import DISCLAIMER, Simulator, gap, greedy_plan, load_effort, skill_effects

router = APIRouter(prefix="/api")
Level = Literal["intern", "new_grad", "entry", "mid", "senior", "unknown"]


class ProfileIn(BaseModel):
    skills: list[str] = Field(default_factory=list, max_length=50)
    target_roles: list[str] = Field(default_factory=list, max_length=5)
    seniority: list[Level] = Field(default_factory=lambda: ["intern", "new_grad", "entry"], max_length=6)
    remote_ok: bool = True
    locations: list[str] = Field(default_factory=list, max_length=5)
    free_text: str = Field("", max_length=300)

    @field_validator("skills", "target_roles", "locations")
    @classmethod
    def _short_items(cls, v: list[str]) -> list[str]:
        if any(len(x) > 60 for x in v):
            raise ValueError("each item must be at most 60 characters")
        return v

    def to_input(self) -> ProfileInput:
        return ProfileInput(
            tuple(self.skills),
            tuple(self.target_roles),
            tuple(self.seniority),
            self.remote_ok,
            tuple(self.locations),
            self.free_text,
        )


class RankIn(BaseModel):
    profile: ProfileIn
    limit: int = Field(20, ge=1, le=50)


class WhyNotIn(BaseModel):
    profile: ProfileIn
    job_id: int | None = None


class SimulateIn(BaseModel):
    profile: ProfileIn
    skill: str = Field(..., min_length=1, max_length=60)
    job_id: int | None = None


def _breakdown(terms: dict, w) -> dict:
    return {
        t: {"value": round(terms[t], 4), "weight": w.of(t), "contribution": round(w.of(t) * terms[t], 4)} for t in TERMS
    }


def _effect_out(e) -> dict:
    d = asdict(e)
    d["job_rank"] = None if e.job_rank is None else {"before": e.job_rank[0], "after": e.job_rank[1]}
    return d


def _plan_out(p) -> dict:
    return {
        "steps": [
            {
                "step": s.step,
                "skill": s.skill,
                "effort_tier": s.effort_tier,
                "effort_hours": s.effort_hours,
                "effect": _effect_out(s.effect),
            }
            for s in p.steps
        ],
        "cumulative": {
            "affected_jobs": p.affected_jobs,
            "median_rank_before": p.median_rank_before,
            "median_rank_after": p.median_rank_after,
            "entering_top10": p.entering_top10,
            "entering_top20": p.entering_top20,
            "total_hours": p.total_hours,
            "job_rank": None if p.job_rank is None else {"before": p.job_rank[0], "after": p.job_rank[1]},
        },
    }


def _setup(request: Request, profile: ProfileIn, include_job: int | None = None):
    w = load_weights()
    np = normalise(profile.to_input())
    with db_conn() as c:
        pool = build_pool(c, request.app.state.embedder, np, w, include_job=include_job)
    sim = Simulator(pool.features, np.profile, pool.ctx, w)
    return w, np, pool, sim


def _meta(np, pool) -> dict:
    return {
        "degraded": pool.degraded,
        "pool_size": len(pool.features),
        "profile": {"skills": sorted(np.profile.skills), "unknown_skills": np.unknown_skills},
    }


@router.post("/rank")
@limiter.limit("20/minute")
def rank(body: RankIn, request: Request):
    w, np, pool, sim = _setup(request, body.profile)
    results = []
    for jid in sim.base_order[: body.limit]:
        j = sim.by_id[jid]
        results.append(
            {
                **job_out(pool.cards[jid]),
                "rank": sim.base_rank[jid],
                "score": round(sim.base_scores[jid], 4),
                "breakdown": _breakdown(sim.terms[jid], w),
                "matched_skills": [s for s in (*j.required, *j.preferred) if s in np.profile.skills],
                "missing_required": [s for s in j.required if s not in np.profile.skills],
            }
        )
    return {**_meta(np, pool), "weights": {t: w.of(t) for t in TERMS}, "results": results}


@router.post("/why-not-me")
@limiter.limit("20/minute")
def why_not_me(body: WhyNotIn, request: Request):
    w, np, pool, sim = _setup(request, body.profile, include_job=body.job_id)
    effort = load_effort()
    out = {**_meta(np, pool), "disclaimer": DISCLAIMER, "effort_note": "Effort hours are my estimates."}
    if body.job_id is not None:
        if body.job_id not in sim.by_id:
            raise HTTPException(404, "job not found or no longer open")
        g = gap(sim, body.job_id)
        missing = [m.skill for m in g["missing"]]
        fixes = skill_effects(sim, effort, job_id=body.job_id, only=missing)
        # for one job: biggest rank jump for THAT job per hour of effort, then pool-wide gain
        fixes.sort(key=lambda e: (-(e.job_rank[0] - e.job_rank[1]) / max(e.effort_hours, 1), -e.rr_gain, e.skill))
        out.update(
            {
                "job": job_out(pool.cards[body.job_id]),
                "rank": g["rank"],
                "score": round(g["score"], 4),
                "breakdown": _breakdown(g["terms"], w),
                "matched": g["matched"],
                "missing": [asdict(m) for m in g["missing"]],
                "fixes": [_effect_out(e) for e in fixes[:3]],
            }
        )
    else:
        out["fixes"] = [_effect_out(e) for e in skill_effects(sim, effort)[:3]]
    out["plan"] = _plan_out(greedy_plan(sim, effort, job_id=body.job_id))
    return out


@router.post("/simulate")
@limiter.limit("30/minute")
def simulate(body: SimulateIn, request: Request):
    skill = load_taxonomy().canonical(body.skill)
    if skill is None:
        raise HTTPException(422, "unknown skill (not in the taxonomy)")
    w, np, pool, sim = _setup(request, body.profile, include_job=body.job_id)
    if skill in np.profile.skills:
        raise HTTPException(422, "that skill is already in your profile")
    e = skill_effects(sim, load_effort(), job_id=body.job_id, only=[skill])
    if not e:
        raise HTTPException(422, "that skill is not something this app suggests as a fix")
    return {**_meta(np, pool), "disclaimer": DISCLAIMER, "effect": _effect_out(e[0])}


@router.get("/skills")
@limiter.limit("60/minute")
def skills(request: Request, q: str = "", limit: int = 20):
    """Autocomplete for skill chips: taxonomy names ranked by how many active jobs mention them."""
    q = q.strip().lower()[:40]
    with db_conn() as c:
        idf = corpus_idf(c)
    names = [n for n in load_taxonomy().names if q in n.lower()] if q else list(load_taxonomy().names)
    names.sort(key=lambda n: (idf.get(n, 99), n))  # lower IDF = more common
    return {"skills": names[: max(1, min(limit, 50))]}
