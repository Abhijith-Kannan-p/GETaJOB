"""Shared plumbing for the evals: results.json (merge one section at a time), percentiles, the markdown report.

Every section records how it was measured (method, n, date, models, corpus) next to its numbers, so nothing
on the /eval page is a number without provenance. A section that has not been run is simply absent.
"""

from __future__ import annotations

import json
import math
import os
from datetime import UTC, datetime
from pathlib import Path

EVAL_DIR = Path(os.environ.get("EVAL_DIR") or Path(__file__).resolve().parents[2] / "eval")
RESULTS = EVAL_DIR / "results.json"
REPORT = EVAL_DIR / "RESULTS.md"

SECTIONS = ("extractor", "simulation", "grounding", "latency")


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def percentile(values: list[float], p: float) -> float:
    """Linear-interpolated percentile (same as numpy's default), p in [0, 100]."""
    if not values:
        raise ValueError("no values")
    xs = sorted(values)
    k = (len(xs) - 1) * p / 100
    lo, hi = math.floor(k), math.ceil(k)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def load() -> dict:
    if RESULTS.exists():
        return json.loads(RESULTS.read_text(encoding="utf-8"))
    return {}


def save_section(name: str, data: dict, key: str | None = None) -> dict:
    """Write results[name] (or results[name][key]), rebuild the summary and the markdown report."""
    assert name in SECTIONS, name
    res = load()
    if key is None:
        res[name] = data
    else:
        res.setdefault(name, {})[key] = data
    res["updated_at"] = now_iso()
    rewrite(res)
    return res


def rewrite(res: dict) -> None:
    """Recompute the summary and the markdown report from res, and write both files."""
    res["summary"] = summarise(res)
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(json.dumps(res, indent=2) + "\n", encoding="utf-8", newline="\n")
    REPORT.write_text(report(res), encoding="utf-8", newline="\n")


def summarise(res: dict) -> dict:
    """Headline numbers for /api/stats. Only what was measured; each with its n."""
    out: dict = {}
    ex = res.get("extractor") or {}
    cur = ex.get("after") or ex.get("before")
    if cur:
        out["extractor"] = {
            "f1": cur["micro"]["f1"],
            "precision": cur["micro"]["precision"],
            "recall": cur["micro"]["recall"],
            "n_postings": cur["n_postings"],
            "which": "after" if ex.get("after") else "before",
        }
    sim = res.get("simulation")
    if sim:
        out["simulation"] = {"agreement": sim["agreement"], "n_simulations": sim["n_simulations"]}
    g = res.get("grounding")
    if g:
        out["grounding"] = {
            "first_try": g["rates"]["first_try"],
            "after_retry": g["rates"]["after_retry"],
            "fallback": g["rates"]["fallback"],
            "n_runs": g["n_runs"],
        }
    lat = res.get("latency") or {}
    if lat:
        out["latency"] = {
            k: {"p50_ms": v["round_trip_ms"]["p50"], "p95_ms": v["round_trip_ms"]["p95"], "n": v["n"]}
            for k, v in lat.items()
        }
    return out


def _pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def report(res: dict) -> str:
    """Markdown summary written next to results.json (same numbers, human-readable)."""
    L = [
        "# GETaJOB eval results",
        "",
        f"Generated from `eval/results.json` (updated {res.get('updated_at')}).",
        "Every number below was measured by the scripts in `backend/evals/`; sections not yet run are omitted.",
        "",
    ]
    ex = res.get("extractor") or {}
    if ex:
        L += ["## 1. Skill extractor (rule-based, no LLM)", ""]
        L += ["| Run | Postings | Pairs (gold) | Precision | Recall | F1 | Taxonomy |", "|---|---|---|---|---|---|---|"]
        for tag in ("before", "after"):
            r = ex.get(tag)
            if r:
                m = r["micro"]
                L.append(
                    f"| {tag} | {r['n_postings']} | {r['n_gold']} | {_pct(m['precision'])} | {_pct(m['recall'])} "
                    f"| {_pct(m['f1'])} | `{r['taxonomy_sha']}` ({r['aliases']} aliases) |"
                )
        r = ex.get("after") or ex.get("before")
        L += ["", f"Method: {r['method']}", ""]
    sim = res.get("simulation")
    if sim:
        L += [
            "## 2. Why-Not-Me simulation vs. full re-rank",
            "",
            f"Agreement **{_pct(sim['agreement'])}** over **{sim['n_simulations']}** simulations "
            f"({sim['n_rank_comparisons']} job-rank comparisons, {sim['n_profiles']} profiles); "
            f"max score difference {sim['max_abs_score_diff']:.2e}.",
            "",
            f"Method: {sim['method']}",
            "",
        ]
    g = res.get("grounding")
    if g:
        r = g["rates"]
        L += ["## 3. LLM narrative grounding", "", "| Outcome | Runs | Share |", "|---|---|---|"]
        for k in ("first_try", "after_retry", "fallback"):
            L.append(f"| {k.replace('_', ' ')} | {g['counts'][k]} | {_pct(r[k])} |")
        L += [
            "",
            f"n = {g['n_runs']} runs, model `{g['model']}`; mean {g['mean_llm_calls']:.2f} LLM calls per run.",
            "",
            f"Method: {g['method']}",
            "",
        ]
    lat = res.get("latency") or {}
    if lat:
        L += [
            "## 4. Search latency (`GET /api/search`)",
            "",
            "| Where | Jobs | n | Round trip p50 | p95 | Server p50 | p95 |",
            "|---|---|---|---|---|---|---|",
        ]
        for k, v in lat.items():
            rt, sv = v["round_trip_ms"], v["server_ms"]
            L.append(
                f"| {k} | {v.get('jobs_active', '?')} | {v['n']} | {rt['p50']:.0f} ms | {rt['p95']:.0f} ms "
                f"| {sv['p50']:.0f} ms | {sv['p95']:.0f} ms |"
            )
        L += ["", f"Method: {next(iter(lat.values()))['method']}", ""]
    return "\n".join(L)
