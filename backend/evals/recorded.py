"""Pick a few real agent runs from eval/llm_runs.jsonl and save them as the app's "recorded runs".

    python -m evals.recorded [--n 3]

When the live model is unavailable (no key, free quota used up, provider down) the assistant still answers
the user's own question with the rule-based narrative, and additionally offers these recorded runs: real
Gemini runs, replayed unchanged, for a SAMPLE profile, clearly labelled with the date and model. They are
never presented as an answer about the user. Output: backend/data/recorded_runs.json (shipped in the image).
"""

from __future__ import annotations

import argparse
import json

from app.config import DATA_DIR
from evals.grounding import QUESTIONS, load_runs
from evals.profiles import PROFILES

OUT = DATA_DIR / "recorded_runs.json"
# One run per question, in this order of preference; the per-job question shows explain_job.
PREFERENCE = [QUESTIONS[0], QUESTIONS[4], QUESTIONS[1], QUESTIONS[2], QUESTIONS[3]]


def choose(runs: list[dict], n: int) -> list[dict]:
    """Passed-grounding Gemini runs, one per question; prefer a run that needed the retry for one slot,
    so the replay also shows the self-correction, and otherwise the earliest run."""
    ok = [r for r in runs if r["outcome"] in ("first_try", "after_retry") and not r.get("interrupted")]
    picked: list[dict] = []
    retry = next((r for r in ok if r["outcome"] == "after_retry"), None)
    if retry:
        picked.append(retry)
    for q in PREFERENCE:
        if len(picked) >= n:
            break
        if any(p["question"] == q for p in picked):
            continue
        cand = [r for r in ok if r["question"] == q and r["outcome"] == "first_try"]
        if cand:
            picked.append(cand[0])
    return picked[:n]


def to_recorded(r: dict) -> dict:
    p = PROFILES[r["profile"]]
    return {
        "id": f"run-{r['i']}",
        "question": r["question"],
        "profile_name": r["profile"],
        "profile": {
            "skills": p.skills,
            "target_roles": p.target_roles,
            "remote_ok": p.remote_ok,
            "locations": p.locations,
        },
        "model": r["model"],
        "recorded_at": r["at"],
        "outcome": r["outcome"],
        "events": [e for e in r["events"] if e["type"] != "memory"],
    }


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m evals.recorded")
    ap.add_argument("--n", type=int, default=3)
    args = ap.parse_args(argv)
    picked = choose(load_runs(), args.n)
    if not picked:
        raise SystemExit("No passed-grounding runs in eval/llm_runs.jsonl yet; run python -m evals.grounding first.")
    OUT.write_text(
        json.dumps([to_recorded(r) for r in picked], ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    for r in picked:
        print(f"saved run {r['i']}: {r['profile']} / {r['question']} ({r['outcome']}, {r['model']})")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
