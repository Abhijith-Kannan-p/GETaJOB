"""The ranker: ONE pure, deterministic scoring function used by /api/rank, Why-Not-Me and the agent.

score(job, profile) = sum over terms of weight[term] * term_value, each term in [0, 1].

Pool-level constants (semantic min/max, keyword max, IDF) live in PoolContext. They depend only on the
candidate pool and the corpus, NEVER on the profile's skills, so a counterfactual "add skill X" changes
exactly one term (skills) and nothing else. That is what makes the Why-Not-Me simulation exact.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from functools import lru_cache

import yaml

from app.config import DATA_DIR
from app.ingest.classify import in_india

TERMS = ("semantic", "keyword", "skills", "seniority", "location", "recency")
JUNIOR = frozenset({"intern", "new_grad", "entry"})


@dataclass(frozen=True)
class Weights:
    semantic: float = 0.35
    keyword: float = 0.10
    skills: float = 0.30
    seniority: float = 0.15
    location: float = 0.05
    recency: float = 0.05
    required_mult: float = 1.0
    preferred_mult: float = 0.5
    recency_half_life_days: float = 30.0
    pool_size: int = 200

    def of(self, term: str) -> float:
        return getattr(self, term)


@lru_cache
def load_weights() -> Weights:
    d = yaml.safe_load((DATA_DIR / "ranking.yaml").read_text(encoding="utf-8"))
    w = d["weights"]
    return Weights(
        **{t: float(w[t]) for t in TERMS},
        required_mult=float(d["skill_weight"]["required"]),
        preferred_mult=float(d["skill_weight"]["preferred"]),
        recency_half_life_days=float(d["recency_half_life_days"]),
        pool_size=int(d["pool_size"]),
    )


@dataclass(frozen=True)
class JobFeatures:
    id: int
    sim: float | None  # cosine(profile, job); None if the job or the query has no embedding
    kw: float  # ts_rank_cd(job, profile text)
    required: tuple[str, ...]
    preferred: tuple[str, ...]
    seniority: str
    is_remote: bool
    location: str
    age_days: float | None


@dataclass(frozen=True)
class Profile:
    skills: frozenset[str]
    seniority: frozenset[str] = JUNIOR
    remote_ok: bool = True
    locations: tuple[str, ...] = ()

    def with_skills(self, extra: Iterable[str]) -> Profile:
        return Profile(self.skills | frozenset(extra), self.seniority, self.remote_ok, self.locations)


@dataclass(frozen=True)
class PoolContext:
    idf: Mapping[str, float]
    default_idf: float
    sim_min: float
    sim_max: float
    kw_max: float

    def skill_idf(self, s: str) -> float:
        return self.idf.get(s, self.default_idf)


def make_context(pool: list[JobFeatures], idf: Mapping[str, float]) -> PoolContext:
    sims = [j.sim for j in pool if j.sim is not None]
    return PoolContext(
        idf=dict(idf),
        default_idf=max(idf.values(), default=1.0),
        sim_min=min(sims, default=0.0),
        sim_max=max(sims, default=0.0),
        kw_max=max((j.kw for j in pool), default=0.0),
    )


def idf_from_counts(df: Mapping[str, int], n_jobs: int) -> dict[str, float]:
    """Smoothed IDF: rare skills weigh more, ubiquitous ones ("Communication") weigh little."""
    return {s: math.log((n_jobs + 1) / (c + 1)) + 1.0 for s, c in df.items()}


# ---------- terms ----------


def skill_weight(job: JobFeatures, skill: str, ctx: PoolContext, w: Weights) -> float:
    if skill in job.required:
        return ctx.skill_idf(skill) * w.required_mult
    if skill in job.preferred:
        return ctx.skill_idf(skill) * w.preferred_mult
    return 0.0


def skill_overlap(job: JobFeatures, skills: frozenset[str], ctx: PoolContext, w: Weights) -> float:
    """Sum of weights of the job's skills the profile has / sum of weights of all the job's skills.

    Monotone: adding a skill can only raise the numerator; the denominator does not depend on the profile.
    """
    total = matched = 0.0
    for s in job.required:
        x = ctx.skill_idf(s) * w.required_mult
        total += x
        if s in skills:
            matched += x
    for s in job.preferred:
        x = ctx.skill_idf(s) * w.preferred_mult
        total += x
        if s in skills:
            matched += x
    return matched / total if total > 0 else 0.0


def semantic_term(job: JobFeatures, ctx: PoolContext) -> float:
    if job.sim is None:
        return 0.0
    span = ctx.sim_max - ctx.sim_min
    return 1.0 if span <= 0 else (job.sim - ctx.sim_min) / span


def keyword_term(job: JobFeatures, ctx: PoolContext) -> float:
    return job.kw / ctx.kw_max if ctx.kw_max > 0 else 0.0


def seniority_term(job: JobFeatures, profile: Profile) -> float:
    lvl, want = job.seniority, profile.seniority
    if lvl in want:
        return 1.0
    if lvl == "unknown":
        return 0.5
    if lvl in JUNIOR and want & JUNIOR:
        return 0.6  # adjacent junior level (e.g. wants intern, job is entry)
    if lvl == "mid" and want & {"entry", "new_grad"}:
        return 0.3
    if lvl == "senior":
        return 0.0
    return 0.2


def location_term(job: JobFeatures, profile: Profile) -> float:
    if not profile.locations:
        return 1.0  # no preference -> neutral for every job, so it never changes the order
    if job.is_remote and profile.remote_ok:
        return 1.0
    loc = (job.location or "").lower()
    for pref in profile.locations:
        p = pref.lower().strip()
        if p == "india" and in_india(loc):
            return 1.0
        if p and p in loc:
            return 1.0
    return 0.0


def recency_term(job: JobFeatures, w: Weights) -> float:
    if job.age_days is None:
        return 0.5
    return 0.5 ** (max(job.age_days, 0.0) / w.recency_half_life_days)


# ---------- combine ----------


def term_values(job: JobFeatures, profile: Profile, ctx: PoolContext, w: Weights) -> dict[str, float]:
    return {
        "semantic": semantic_term(job, ctx),
        "keyword": keyword_term(job, ctx),
        "skills": skill_overlap(job, profile.skills, ctx, w),
        "seniority": seniority_term(job, profile),
        "location": location_term(job, profile),
        "recency": recency_term(job, w),
    }


def combine(terms: Mapping[str, float], w: Weights) -> float:
    """The ONLY place a total is computed (fixed term order => bit-identical floats everywhere)."""
    total = 0.0
    for t in TERMS:
        total += w.of(t) * terms[t]
    return total


@dataclass(frozen=True)
class Ranked:
    job_id: int
    rank: int  # 1-based
    score: float
    terms: Mapping[str, float] = field(default_factory=dict)


def order(scores: Mapping[int, float]) -> list[int]:
    """Deterministic order: score desc, then job id asc (stable tie-break)."""
    return sorted(scores, key=lambda jid: (-scores[jid], jid))


def rank_pool(pool: list[JobFeatures], profile: Profile, ctx: PoolContext, w: Weights) -> list[Ranked]:
    terms = {j.id: term_values(j, profile, ctx, w) for j in pool}
    scores = {jid: combine(t, w) for jid, t in terms.items()}
    return [Ranked(jid, i + 1, scores[jid], terms[jid]) for i, jid in enumerate(order(scores))]
