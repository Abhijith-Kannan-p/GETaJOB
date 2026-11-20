"""Eval 2: does Why-Not-Me's fast simulation agree with re-running the ranker from scratch?

    python -m evals.simulation [--no-embed]

For each synthetic profile (evals/profiles.py) the real candidate pool is built from the database, then for
every missing skill that appears in the pool, and for each multi-skill learning plan the engine proposes, the
simulated ranks (Simulator.ranks_with: only the skill term recomputed, only for touched jobs) are compared
with scorer.rank_pool on the extended profile (every term, every job). Agreement = the full rank order and
every score match. Expected: 100% by construction; this checks it on real data, not just in property tests.
"""

from __future__ import annotations

import argparse

from evals.common import now_iso, save_section
from evals.profiles import PROFILES

METHOD = (
    "Real candidate pools from the database for {np} synthetic profiles (UI role buttons + popular-skill buttons). "
    "Each simulation adds one missing skill (every skill present in the pool) or a greedy plan's skill set, then "
    "compares Simulator.ranks_with/scores_with against scorer.rank_pool on the extended profile. A simulation "
    "agrees when the whole rank order is identical and every score is within 1e-12."
)


def compare(sim, profile, ctx, w, add: frozenset[str]) -> tuple[bool, int, int, float]:
    """-> (agrees, jobs compared, rank mismatches, max |score diff|)."""
    from app.rank.scorer import rank_pool

    fast_scores = sim.scores_with(add)
    fast_ranks = sim.ranks_with(add)
    full = rank_pool(sim.pool, profile.with_skills(add), ctx, w)
    mism = sum(1 for r in full if fast_ranks[r.job_id] != r.rank)
    diff = max((abs(fast_scores[r.job_id] - r.score) for r in full), default=0.0)
    return mism == 0 and diff <= 1e-12, len(full), mism, diff


def run(no_embed: bool = False) -> dict:
    from app.db import conn
    from app.llm.client import get_embedder
    from app.rank.scorer import load_weights
    from app.rank.service import build_pool, clear_caches_for_tests, normalise
    from app.whynot.engine import Simulator, greedy_plan, load_effort

    embedder = None if no_embed else get_embedder()
    w, effort = load_weights(), load_effort()
    n_sim = n_agree = n_jobs = n_mism = 0
    max_diff = 0.0
    per_profile, modes, failures = {}, set(), []
    clear_caches_for_tests()
    with conn() as c:
        jobs_active = c.execute("SELECT count(*) AS n FROM jobs WHERE closed_at IS NULL").fetchone()["n"]
        for name, p in PROFILES.items():
            np = normalise(p.to_input())
            pool = build_pool(c, embedder, np, w)
            modes.add(pool.degraded or "hybrid")
            sim = Simulator(pool.features, np.profile, pool.ctx, w)
            sets = [frozenset([s]) for s in sorted(sim.missing_skills())]
            plan = greedy_plan(sim, effort, steps=3)
            if len(plan.steps) > 1:
                sets.append(frozenset(s.skill for s in plan.steps))
            for add in sets:
                ok, jobs, mism, diff = compare(sim, np.profile, pool.ctx, w, add)
                n_sim += 1
                n_agree += ok
                n_jobs += jobs
                n_mism += mism
                max_diff = max(max_diff, diff)
                if not ok:
                    failures.append({"profile": name, "added": sorted(add), "rank_mismatches": mism})
            per_profile[name] = {"pool_size": len(pool.features), "simulations": len(sets)}
            print(f"  {name:10s} pool {len(pool.features):3d}  simulations {len(sets):3d}")
    return {
        "measured_at": now_iso(),
        "method": METHOD.format(np=len(PROFILES)),
        "pool_mode": sorted(modes),
        "jobs_active": jobs_active,
        "n_profiles": len(PROFILES),
        "n_simulations": n_sim,
        "n_agree": n_agree,
        "agreement": round(n_agree / n_sim, 6) if n_sim else 0.0,
        "n_rank_comparisons": n_jobs,
        "rank_mismatches": n_mism,
        "max_abs_score_diff": max_diff,
        "per_profile": per_profile,
        "failures": failures[:20],
    }


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m evals.simulation")
    ap.add_argument("--no-embed", action="store_true", help="full-text pools only (no embedding API calls)")
    args = ap.parse_args(argv)
    data = run(args.no_embed)
    save_section("simulation", data)
    print(
        f"agreement {data['n_agree']}/{data['n_simulations']} = {100 * data['agreement']:.2f}% "
        f"({data['n_rank_comparisons']} job ranks, max score diff {data['max_abs_score_diff']:.2e}, "
        f"pools: {', '.join(data['pool_mode'])})"
    )


if __name__ == "__main__":
    main()
