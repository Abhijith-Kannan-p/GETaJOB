"""Ranker + Why-Not-Me properties (pure, no DB, no network)."""

import random

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.ingest.skills import load_taxonomy
from app.rank.scorer import (
    TERMS,
    JobFeatures,
    Profile,
    Weights,
    idf_from_counts,
    make_context,
    rank_pool,
    skill_overlap,
    term_values,
)
from app.whynot.engine import Effort, Simulator, gap, greedy_plan, load_effort, skill_effects

SKILLS = [f"S{i}" for i in range(12)]
LEVELS = ["intern", "new_grad", "entry", "mid", "senior", "unknown"]
W = Weights()
EFFORT = Effort(
    tiers={"low": 10, "medium": 30, "high": 80},
    default_tier="medium",
    by_skill={"S0": "low", "S1": "high"},
    not_suggestible=frozenset({"S11"}),
)


@st.composite
def jobs(draw, jid):
    skills = draw(st.lists(st.sampled_from(SKILLS), unique=True, max_size=6))
    k = draw(st.integers(0, len(skills)))
    return JobFeatures(
        id=jid,
        sim=draw(st.one_of(st.none(), st.floats(-1, 1, allow_nan=False))),
        kw=draw(st.floats(0, 2, allow_nan=False)),
        required=tuple(skills[:k]),
        preferred=tuple(skills[k:]),
        seniority=draw(st.sampled_from(LEVELS)),
        is_remote=draw(st.booleans()),
        location=draw(st.sampled_from(["Bengaluru, India", "Remote", "Dublin", ""])),
        age_days=draw(st.one_of(st.none(), st.floats(0, 365, allow_nan=False))),
    )


@st.composite
def scenario(draw):
    n = draw(st.integers(1, 40))
    pool = [draw(jobs(i + 1)) for i in range(n)]
    df = {s: sum(s in (*j.required, *j.preferred) for j in pool) for s in SKILLS}
    ctx = make_context(pool, idf_from_counts(df, n))
    prof = Profile(
        skills=frozenset(draw(st.lists(st.sampled_from(SKILLS), unique=True, max_size=6))),
        seniority=frozenset(draw(st.lists(st.sampled_from(LEVELS), unique=True, min_size=1, max_size=3))),
        remote_ok=draw(st.booleans()),
        locations=tuple(draw(st.lists(st.sampled_from(["India", "Dublin", "Berlin"]), unique=True, max_size=2))),
    )
    add = frozenset(draw(st.lists(st.sampled_from(SKILLS), unique=True, min_size=1, max_size=3)))
    return pool, ctx, prof, add


@settings(max_examples=300, deadline=None)
@given(scenario())
def test_adding_a_skill_never_lowers_skill_overlap(sc):
    pool, ctx, prof, add = sc
    for j in pool:
        assert skill_overlap(j, prof.skills | add, ctx, W) >= skill_overlap(j, prof.skills, ctx, W)


@settings(max_examples=300, deadline=None)
@given(scenario())
def test_simulated_rank_equals_actual_rerank(sc):
    pool, ctx, prof, add = sc
    sim = Simulator(pool, prof, ctx, W)
    rerun = rank_pool(pool, prof.with_skills(add), ctx, W)
    assert sim.ranks_with(add) == {r.job_id: r.rank for r in rerun}
    assert sim.scores_with(add) == {r.job_id: r.score for r in rerun}  # bit-identical, not approx


@settings(max_examples=200, deadline=None)
@given(scenario())
def test_only_the_skill_term_changes(sc):
    pool, ctx, prof, add = sc
    for j in pool:
        a, b = term_values(j, prof, ctx, W), term_values(j, prof.with_skills(add), ctx, W)
        assert {t: a[t] for t in TERMS if t != "skills"} == {t: b[t] for t in TERMS if t != "skills"}


@settings(max_examples=100, deadline=None)
@given(scenario(), st.randoms(use_true_random=False))
def test_deterministic_and_independent_of_pool_order(sc, rnd):
    pool, ctx, prof, _ = sc
    shuffled = pool[:]
    rnd.shuffle(shuffled)
    a = Simulator(pool, prof, ctx, W)
    b = Simulator(shuffled, prof, ctx, W)
    assert a.base_rank == b.base_rank
    assert skill_effects(a, EFFORT, min_jobs=1) == skill_effects(b, EFFORT, min_jobs=1)
    assert greedy_plan(a, EFFORT, min_jobs=1) == greedy_plan(b, EFFORT, min_jobs=1)


@settings(max_examples=150, deadline=None)
@given(scenario())
def test_greedy_plan_cumulative_matches_rerank(sc):
    pool, ctx, prof, _ = sc
    sim = Simulator(pool, prof, ctx, W)
    plan = greedy_plan(sim, EFFORT, min_jobs=1, job_id=pool[0].id)
    chosen = [s.skill for s in plan.steps]
    assert len(chosen) == len(set(chosen)) <= 3
    assert not set(chosen) & prof.skills and "S11" not in chosen
    if chosen:
        rerun = {r.job_id: r.rank for r in rank_pool(pool, prof.with_skills(chosen), ctx, W)}
        assert plan.job_rank == (sim.base_rank[pool[0].id], rerun[pool[0].id])


def _job(jid, req=(), pref=(), sim=0.5, level="intern"):
    return JobFeatures(jid, sim, 0.1, tuple(req), tuple(pref), level, True, "Remote", 10.0)


def test_worked_example_docker_moves_jobs_up():
    pool = [
        _job(1, req=["Python"], sim=0.70),
        _job(2, req=["Python", "Docker"], sim=0.60),
        _job(3, req=["Docker", "Kubernetes"], sim=0.65),
        _job(4, req=["Java"], sim=0.66),
    ]
    df = {"Python": 2, "Docker": 2, "Kubernetes": 1, "Java": 1}
    ctx = make_context(pool, idf_from_counts(df, 4))
    sim = Simulator(pool, Profile(frozenset({"Python"})), ctx, W)
    assert sim.base_rank[1] == 1
    eff = {e.skill: e for e in skill_effects(sim, EFFORT, min_jobs=1)}
    d = eff["Docker"]
    assert d.jobs_with_skill == 2 and d.rr_gain > 0 and d.mean_rank_change > 0
    after = sim.ranks_with(["Docker"])
    assert after[2] < sim.base_rank[2] and after[3] < sim.base_rank[3]
    g = gap(sim, 3)
    assert [m.skill for m in g["missing"]] == ["Kubernetes", "Docker"]  # rarer skill weighs more
    assert g["missing"][0].kind == "required" and abs(sum(m.share for m in g["missing"]) - 1) < 1e-9


def test_no_skills_job_and_empty_profile_are_safe():
    pool = [_job(1), _job(2, req=["Go"])]
    ctx = make_context(pool, {"Go": 1.0})
    sim = Simulator(pool, Profile(frozenset()), ctx, W)
    assert skill_overlap(pool[0], frozenset({"Go"}), ctx, W) == 0.0
    assert greedy_plan(sim, EFFORT, min_jobs=1).steps[0].skill == "Go"


def test_effort_file_names_are_real_taxonomy_skills():
    tax = set(load_taxonomy().names)
    e = load_effort()
    unknown = sorted(s for s in (*e.by_skill, *e.not_suggestible) if s not in tax)
    assert unknown == [], f"skills_effort.yaml names not in the taxonomy: {unknown}"
    assert set(e.by_skill.values()) <= set(e.tiers)


@pytest.mark.parametrize("seed", range(3))
def test_large_random_pool_fast_and_exact(seed):
    rnd = random.Random(seed)
    pool = [
        JobFeatures(
            i,
            rnd.random(),
            rnd.random(),
            tuple(rnd.sample(SKILLS, 3)),
            (),
            rnd.choice(LEVELS),
            rnd.random() < 0.5,
            "Remote",
            rnd.random() * 90,
        )
        for i in range(200)
    ]
    pool = [
        JobFeatures(
            j.id,
            j.sim,
            j.kw,
            j.required,
            tuple(s for s in SKILLS[:4] if s not in j.required),
            j.seniority,
            j.is_remote,
            j.location,
            j.age_days,
        )
        for j in pool
    ]
    df = {s: sum(s in (*j.required, *j.preferred) for j in pool) for s in SKILLS}
    ctx = make_context(pool, idf_from_counts(df, 200))
    prof = Profile(frozenset(rnd.sample(SKILLS, 3)))
    sim = Simulator(pool, prof, ctx, W)
    for s in SKILLS:
        assert sim.ranks_with([s]) == {r.job_id: r.rank for r in rank_pool(pool, prof.with_skills([s]), ctx, W)}


@settings(max_examples=200, deadline=None)
@given(scenario())
def test_job_focused_plan_never_worsens_that_job(sc):
    pool, ctx, prof, _ = sc
    sim = Simulator(pool, prof, ctx, W)
    for job in pool[:3]:
        plan = greedy_plan(sim, EFFORT, job_id=job.id)
        own = set(job.required) | set(job.preferred)
        assert all(s.skill in own for s in plan.steps)
        if plan.steps:
            before, after = plan.job_rank
            assert after < before
            ranks = [s.effect.job_rank for s in plan.steps]
            assert all(r[1] < r[0] for r in ranks)  # every single step strictly helps this job
