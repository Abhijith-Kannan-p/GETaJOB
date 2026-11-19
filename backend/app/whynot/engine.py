"""Why-Not-Me: counterfactual job matching by SIMULATING the real ranker. Pure, deterministic.

"If I add skill X to my profile, where do jobs land?" We re-score the same candidate pool with X added.
Only the skill-overlap term can change: the semantic/keyword terms are held fixed (the profile embedding and
retrieved pool are not recomputed), and every other term ignores skills. Numbers are the ranker's own
simulation, not a hiring prediction.

Efficiency + exactness: per-job term values are computed once; a simulation recomputes the skill term only
for jobs that contain an added skill and re-totals with scorer.combine, so the result is bit-identical to
re-running scorer.rank_pool on the extended profile (property-tested).
"""

from __future__ import annotations

import statistics
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from functools import lru_cache

import yaml

from app.config import DATA_DIR
from app.rank.scorer import (
    JobFeatures,
    PoolContext,
    Profile,
    Weights,
    combine,
    order,
    skill_overlap,
    skill_weight,
    term_values,
)

DISCLAIMER = (
    "These numbers come from re-running this app's own ranker with the skill added to your profile "
    "(semantic and keyword similarity held fixed). They describe how this ranker orders the current "
    "job pool, not your chances of being hired."
)


# ---------- effort ----------


@dataclass(frozen=True)
class Effort:
    tiers: Mapping[str, int]
    default_tier: str
    by_skill: Mapping[str, str]
    not_suggestible: frozenset[str]

    def tier(self, skill: str) -> str:
        return self.by_skill.get(skill, self.default_tier)

    def hours(self, skill: str) -> int:
        return self.tiers[self.tier(skill)]


@lru_cache
def load_effort() -> Effort:
    d = yaml.safe_load((DATA_DIR / "skills_effort.yaml").read_text(encoding="utf-8"))
    return Effort(
        tiers={k: int(v) for k, v in d["tiers"].items()},
        default_tier=d["default_tier"],
        by_skill=dict(d.get("skills") or {}),
        not_suggestible=frozenset(d.get("not_suggestible") or []),
    )


# ---------- simulation core ----------


class Simulator:
    """Holds a pool + profile; answers "what are the ranks if I add these skills?" exactly."""

    def __init__(self, pool: list[JobFeatures], profile: Profile, ctx: PoolContext, w: Weights):
        self.pool = pool
        self.profile = profile
        self.ctx = ctx
        self.w = w
        self.by_id = {j.id: j for j in pool}
        self.terms = {j.id: term_values(j, profile, ctx, w) for j in pool}
        self.base_scores = {jid: combine(t, w) for jid, t in self.terms.items()}
        self.base_order = order(self.base_scores)
        self.base_rank = {jid: i + 1 for i, jid in enumerate(self.base_order)}
        self.jobs_with: dict[str, list[int]] = {}
        for j in pool:
            for s in (*j.required, *j.preferred):
                self.jobs_with.setdefault(s, []).append(j.id)

    def scores_with(self, add: Iterable[str]) -> dict[int, float]:
        add = frozenset(add) - self.profile.skills
        if not add:
            return dict(self.base_scores)
        skills = self.profile.skills | add
        scores = dict(self.base_scores)
        touched = {jid for s in add for jid in self.jobs_with.get(s, ())}
        for jid in touched:
            t = dict(self.terms[jid])
            t["skills"] = skill_overlap(self.by_id[jid], skills, self.ctx, self.w)
            scores[jid] = combine(t, self.w)
        return scores

    def ranks_with(self, add: Iterable[str]) -> dict[int, int]:
        return {jid: i + 1 for i, jid in enumerate(order(self.scores_with(add)))}

    def missing_skills(self) -> Counter:
        """Skills in pool jobs that the profile lacks -> number of pool jobs containing each."""
        return Counter({s: len(ids) for s, ids in self.jobs_with.items() if s not in self.profile.skills})


# ---------- reports ----------


@dataclass(frozen=True)
class SkillEffect:
    skill: str
    jobs_with_skill: int
    entering_top10: int
    entering_top20: int
    mean_rank_change: float  # over jobs containing the skill; positive = moved UP
    rr_gain: float  # sum over jobs containing the skill of (1/new_rank - 1/old_rank)
    effort_tier: str
    effort_hours: int
    gain_per_hour: float
    job_rank: tuple[int, int] | None = None  # (before, after) for the selected job, if any


def _effect(
    sim: Simulator, skills: frozenset[str], base: Mapping[int, int], effort: Effort, label: str, job_id: int | None
) -> SkillEffect:
    after = sim.ranks_with(sim.profile.skills | skills)
    affected = sorted({jid for s in skills for jid in sim.jobs_with.get(s, ())})
    deltas = [base[j] - after[j] for j in affected]
    rr = sum(1 / after[j] - 1 / base[j] for j in affected)
    top10 = sum(1 for j in affected if base[j] > 10 >= after[j])
    top20 = sum(1 for j in affected if base[j] > 20 >= after[j])
    hours = sum(effort.hours(s) for s in skills)
    return SkillEffect(
        skill=label,
        jobs_with_skill=len(affected),
        entering_top10=top10,
        entering_top20=top20,
        mean_rank_change=round(statistics.fmean(deltas), 2) if deltas else 0.0,
        rr_gain=round(rr, 6),
        effort_tier=effort.tier(label) if len(skills) == 1 else "mixed",
        effort_hours=hours,
        gain_per_hour=round(rr / hours, 8) if hours else 0.0,
        job_rank=(base[job_id], after[job_id]) if job_id is not None and job_id in base else None,
    )


def skill_effects(
    sim: Simulator, effort: Effort, min_jobs: int = 3, job_id: int | None = None, only: Iterable[str] | None = None
) -> list[SkillEffect]:
    """Simulate each missing skill present in >= min_jobs pool jobs (or each skill in `only`)."""
    missing = sim.missing_skills()
    cands = sorted(only) if only is not None else sorted(s for s, n in missing.items() if n >= min_jobs)
    cands = [s for s in cands if s not in effort.not_suggestible and s not in sim.profile.skills]
    out = [_effect(sim, frozenset([s]), sim.base_rank, effort, s, job_id) for s in cands]
    out.sort(key=lambda e: (-e.gain_per_hour, -e.rr_gain, e.skill))
    return out


@dataclass
class PlanStep:
    step: int
    skill: str
    effort_tier: str
    effort_hours: int
    effect: SkillEffect  # marginal effect of this step given the previous steps


@dataclass
class Plan:
    steps: list[PlanStep] = field(default_factory=list)
    affected_jobs: int = 0
    median_rank_before: float | None = None
    median_rank_after: float | None = None
    entering_top10: int = 0
    entering_top20: int = 0
    job_rank: tuple[int, int] | None = None
    total_hours: int = 0


def greedy_plan(sim: Simulator, effort: Effort, steps: int = 3, min_jobs: int = 3, job_id: int | None = None) -> Plan:
    """Greedy: pick the best skill, add it, re-simulate, repeat (<= `steps`). Cumulative effect vs. the start.

    Without job_id: best pool-wide reciprocal-rank gain per hour. With job_id: best rank jump for that job
    per hour, among its own missing skills, only taking steps that strictly improve its rank.
    """
    plan = Plan()
    chosen: list[str] = []
    focus = job_id is not None and job_id in sim.by_id
    for i in range(steps):
        cur = Simulator(sim.pool, sim.profile.with_skills(chosen), sim.ctx, sim.w)
        if focus:
            # Job view: only that job's missing skills, judged by how far THEY move THIS job per hour.
            # A step is taken only if it strictly improves the job's rank, so the job never ends worse off.
            j = cur.by_id[job_id]
            own = [s for s in (*j.required, *j.preferred) if s not in cur.profile.skills]
            effects = [
                e
                for e in skill_effects(cur, effort, job_id=job_id, only=own)
                if e.job_rank and e.job_rank[1] < e.job_rank[0]
            ]
            effects.sort(key=lambda e: (-(e.job_rank[0] - e.job_rank[1]) / max(e.effort_hours, 1), -e.rr_gain, e.skill))
        else:
            effects = [e for e in skill_effects(cur, effort, min_jobs=min_jobs) if e.rr_gain > 0]
        if not effects:
            break
        best = effects[0]
        chosen.append(best.skill)
        plan.steps.append(PlanStep(i + 1, best.skill, best.effort_tier, best.effort_hours, best))
    if not chosen:
        return plan
    after = sim.ranks_with(chosen)
    affected = sorted({jid for s in chosen for jid in sim.jobs_with.get(s, ())})
    before = sim.base_rank
    plan.affected_jobs = len(affected)
    plan.median_rank_before = statistics.median(before[j] for j in affected)
    plan.median_rank_after = statistics.median(after[j] for j in affected)
    plan.entering_top10 = sum(1 for j in affected if before[j] > 10 >= after[j])
    plan.entering_top20 = sum(1 for j in affected if before[j] > 20 >= after[j])
    plan.total_hours = sum(s.effort_hours for s in plan.steps)
    if job_id is not None and job_id in before:
        plan.job_rank = (before[job_id], after[job_id])
    return plan


@dataclass(frozen=True)
class MissingSkill:
    skill: str
    kind: str  # required | preferred
    weight: float
    share: float  # share of the job's total skill weight


def gap(sim: Simulator, job_id: int) -> dict:
    """Per-job gap analysis: which of its skills you lack, how much they weigh, your current rank."""
    j = sim.by_id[job_id]
    total = sum(skill_weight(j, s, sim.ctx, sim.w) for s in (*j.required, *j.preferred)) or 1.0
    missing = [
        MissingSkill(
            s,
            "required" if s in j.required else "preferred",
            round(skill_weight(j, s, sim.ctx, sim.w), 4),
            round(skill_weight(j, s, sim.ctx, sim.w) / total, 4),
        )
        for s in (*j.required, *j.preferred)
        if s not in sim.profile.skills
    ]
    missing.sort(key=lambda m: (-m.weight, m.skill))
    matched = [s for s in (*j.required, *j.preferred) if s in sim.profile.skills]
    return {
        "job_id": job_id,
        "rank": sim.base_rank[job_id],
        "pool_size": len(sim.pool),
        "score": sim.base_scores[job_id],
        "terms": sim.terms[job_id],
        "matched": matched,
        "missing": missing,
    }
