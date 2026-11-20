"""Eval 3: how often does the LLM's narrative pass the grounding check on the first try, after the one retry,
or not at all (rule-based fallback)? Real Gemini calls, so it costs quota and is resumable.

    python -m evals.grounding [--n 35] [--pause 20]

Grid: synthetic profiles (evals/profiles.py) x the assistant's four preset questions + "why am I not ranking
higher for this job?" on the profile's 5th-ranked job, in a fixed order. Each finished run is appended to
eval/llm_runs.jsonl at once; a rerun skips indices already there. A run cut short by quota or a provider
outage is NOT recorded (it says nothing about grounding) and the script stops cleanly; rerun later.
Worst case 4 LLM calls per run (2 tool rounds + narrative + retry), so --n 35 uses at most 140 calls.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter

from evals.common import EVAL_DIR, now_iso, save_section
from evals.profiles import PROFILES

RUNS_PATH = EVAL_DIR / "llm_runs.jsonl"
QUESTIONS = [
    "What should I learn next?",
    "Which jobs fit me best right now?",
    "Why am I not ranking higher?",
    "Find remote roles for my skills",
    "Why am I not ranking higher for this job?",  # asked with job_id = the profile's 5th-ranked job
]
JOB_QUESTION = len(QUESTIONS) - 1
OUTCOMES = ("first_try", "after_retry", "fallback")

METHOD = (
    "Real agent runs (Gemini function calling + structured narrative) against the local database: {np} synthetic "
    "profiles x {nq} questions in a fixed order (the four preset buttons plus a per-job question on each profile's "
    "5th-ranked job). Outcome per run: grounding check passed on the first narrative attempt, passed after the "
    "single corrective retry, or failed twice / returned unusable JSON (rule-based fallback). Runs interrupted by "
    "quota or provider outages are excluded and listed as interrupted."
)


def grid() -> list[tuple[str, int]]:
    """Fixed run order: question-major so a partial run still covers every question."""
    return [(name, q) for q in range(len(QUESTIONS)) for name in PROFILES]


def load_runs() -> list[dict]:
    if not RUNS_PATH.exists():
        return []
    with RUNS_PATH.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def append_run(rec: dict) -> None:
    RUNS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with RUNS_PATH.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def record(i: int, profile: str, q: int, job_id: int | None, events: list[dict], model: str) -> dict:
    nar = next(e for e in events if e["type"] == "narrative")
    done = next(e for e in events if e["type"] == "done")
    checks = [e for e in events if e["type"] == "check"]
    return {
        "i": i,
        "at": now_iso(),
        "model": model,
        "profile": profile,
        "question": QUESTIONS[q],
        "job_id": job_id,
        "outcome": nar["grounding"]["outcome"],
        "interrupted": nar["grounding"].get("interrupted"),
        "llm_calls": done["llm_calls"],
        "ms": done["ms"],
        "tools": [e["name"] for e in events if e["type"] == "tool_call"],
        "checks": [
            {"attempt": c["attempt"], "passed": c["passed"], "reasons": [d["reason"] for d in c["violations"]]}
            for c in checks
        ],
        "events": events,  # the full trace: a replayable recorded run
    }


def summarise(runs: list[dict]) -> dict:
    counts = Counter(r["outcome"] for r in runs)
    n = len(runs)
    reasons = Counter(x for r in runs for c in r["checks"] if c["attempt"] == 1 for x in c["reasons"])
    models = sorted({r["model"] for r in runs})
    return {
        "measured_at": max((r["at"] for r in runs), default=now_iso()),
        "method": METHOD.format(np=len(PROFILES), nq=len(QUESTIONS)),
        "model": ", ".join(models),
        "n_runs": n,
        "counts": {k: counts.get(k, 0) for k in OUTCOMES},
        "rates": {k: round(counts.get(k, 0) / n, 4) if n else 0.0 for k in OUTCOMES},
        "bad_output_runs": sum(1 for r in runs if r.get("interrupted") == "bad_output"),
        "first_attempt_violation_reasons": dict(reasons.most_common()),
        "mean_llm_calls": round(sum(r["llm_calls"] for r in runs) / n, 3) if n else 0.0,
        "median_ms": sorted(r["ms"] for r in runs)[n // 2] if n else None,
        "by_question": {
            QUESTIONS[q]: dict(Counter(r["outcome"] for r in runs if r["question"] == QUESTIONS[q]))
            for q in range(len(QUESTIONS))
        },
    }


def fifth_job(ctx) -> int | None:
    pool, sim = ctx.sim()
    return sim.base_order[4] if len(sim.base_order) >= 5 else None


def run(n: int, pause: float, llm_factory=None, embedder="real", conn_factory=None) -> int:
    """Run until n runs are recorded. Returns 0 when done, 2 when stopped early (quota/outage)."""
    from app.agent.run import run_agent
    from app.agent.tools import AgentContext
    from app.db import conn as default_conn
    from app.llm.client import GeminiLLM, get_embedder

    llm_factory = llm_factory or GeminiLLM
    emb = get_embedder() if embedder == "real" else embedder
    conn_factory = conn_factory or default_conn
    done = {r["i"] for r in load_runs()}
    todo = [(i, p, q) for i, (p, q) in enumerate(grid() * (n // len(grid()) + 1)) if i < n and i not in done]
    print(f"{len(done)} runs already recorded; {len(todo)} to go")
    for k, (i, pname, q) in enumerate(todo):
        with conn_factory() as c:
            llm = llm_factory(c)
            if k == 0:
                print(f"model: {getattr(llm, 'model', type(llm).__name__)}")
            ctx = AgentContext(c, emb, PROFILES[pname])
            job_id = fifth_job(ctx) if q == JOB_QUESTION else None
            events = list(run_agent(ctx, QUESTIONS[q], llm, job_id=job_id))
        rec = record(i, pname, q, job_id, events, getattr(llm, "model", type(llm).__name__))
        if rec["interrupted"] in ("quota", "provider"):
            print(
                f"run {i}: stopped by {rec['interrupted']} ({rec['llm_calls']} calls made); not recorded. "
                "Rerun later to resume."
            )
            return 2
        append_run(rec)
        print(f"run {i:3d} {pname:10s} q{q} -> {rec['outcome']:11s} calls {rec['llm_calls']} {rec['ms'] / 1000:.1f}s")
        if pause and k < len(todo) - 1:
            time.sleep(pause)
    return 0


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m evals.grounding")
    ap.add_argument("--n", type=int, default=35)
    ap.add_argument("--pause", type=float, default=20.0, help="seconds between runs (free tier: 12 requests/min)")
    ap.add_argument("--summarise-only", action="store_true")
    args = ap.parse_args(argv)
    code = 0 if args.summarise_only else run(args.n, args.pause)
    runs = load_runs()
    if runs:
        s = summarise(runs)
        save_section("grounding", s)
        r = s["rates"]
        print(
            f"n={s['n_runs']} ({s['model']}): first try {100 * r['first_try']:.1f}%, after retry "
            f"{100 * r['after_retry']:.1f}%, fallback {100 * r['fallback']:.1f}%; mean {s['mean_llm_calls']} calls/run"
        )
    sys.exit(code)


if __name__ == "__main__":
    main()
