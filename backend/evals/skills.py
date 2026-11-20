"""Eval 1: precision / recall / F1 of the rule-based skill extractor against hand-corrected labels.

    python -m evals.skills sample [--n 70] [--seed 7]   # fetch postings, snapshot them, pre-fill the labels
    python -m evals.skills review                        # eval/skill_review.md: each posting, matches in bold
    python -m evals.skills score --tag before|after      # score the reviewed rows with the CURRENT taxonomy

`sample` fetches the boards in data/companies.yaml from the public Greenhouse/Lever APIs (exactly what the
nightly sync does), applies the same cap and priority selection, samples postings round-robin over companies
and snapshots each posting's sections to eval/skill_set.jsonl. Scoring runs the production extractor on
those sections, so the eval is reproducible after the postings close.

What is measured: which taxonomy skills a posting asks for (required + preferred together). The
required/preferred split is not scored. Skills outside the taxonomy are counted separately
(`not_in_taxonomy`) because they are a coverage gap, not a matching error.

Only rows marked reviewed are scored. The labels start as the extractor's own output (to save typing),
which can anchor the reviewer towards agreeing with it; the report says so.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import sys
from collections import Counter, defaultdict

from evals.common import EVAL_DIR, now_iso, save_section

SET_PATH = EVAL_DIR / "skill_set.jsonl"
LABELS_PATH = EVAL_DIR / "skill_labels.csv"
REVIEW_PATH = EVAL_DIR / "skill_review.md"
FIELDS = ["key", "company", "title", "predicted", "gold", "not_in_taxonomy", "reviewed", "notes"]
YES = {"y", "yes", "1", "x", "true"}

METHOD = (
    "Postings fetched from the public Greenhouse/Lever board APIs with the production cap and priority selection, "
    "then sampled round-robin over companies with a fixed seed; sections snapshotted to eval/skill_set.jsonl. "
    "Gold = taxonomy skills the posting asks for or says the role uses (not company/customer names, partner "
    "teams, degree-field lists, legal or benefits text). Labelled by an AI coding assistant (Claude) at the "
    "author's request, reading every posting in full; single annotator, no inter-rater check; starting from a "
    "pre-filled copy of the extractor output (possible anchoring bias towards the extractor). Prediction = the "
    "production extractor (required + preferred, boilerplate sections skipped) with the taxonomy at scoring time. "
    "Micro P/R/F1 over (posting, skill) pairs."
)


def split(cell: str) -> list[str]:
    return [x.strip() for x in (cell or "").replace(",", ";").split(";") if x.strip()]


def predict(secs: list[list[str]], tax) -> list[str]:
    from app.ingest.skills import extract

    ex = extract([tuple(s) for s in secs], tax)
    return sorted(set(ex.required) | set(ex.preferred))


def _prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    p = tp / (tp + fp) if tp + fp else 1.0
    r = tp / (tp + fn) if tp + fn else 1.0
    return p, r, (2 * p * r / (p + r) if p + r else 0.0)


def prf(tp: int, fp: int, fn: int) -> dict:
    p, r, f = _prf(tp, fp, fn)
    return {"precision": round(p, 4), "recall": round(r, 4), "f1": round(f, 4), "tp": tp, "fp": fp, "fn": fn}


def score_rows(rows: list[tuple[str, set[str], set[str]]]) -> dict:
    """rows = [(key, predicted, gold)] -> micro + macro P/R/F1 and the most common errors."""
    tp = fp = fn = 0
    per = []
    fps: Counter = Counter()
    fns: Counter = Counter()
    example: dict[tuple[str, str], str] = {}
    for key, pred, gold in rows:
        a, b, c = len(pred & gold), len(pred - gold), len(gold - pred)
        tp, fp, fn = tp + a, fp + b, fn + c
        per.append(_prf(a, b, c))  # unrounded, so the macro mean is rounded once
        for s in pred - gold:
            fps[s] += 1
            example.setdefault(("fp", s), key)
        for s in gold - pred:
            fns[s] += 1
            example.setdefault(("fn", s), key)
    n = len(per) or 1

    def top(cnt: Counter, kind: str) -> list[dict]:
        return [{"skill": s, "count": c, "example": example[(kind, s)]} for s, c in cnt.most_common(10)]

    return {
        "micro": prf(tp, fp, fn),
        "macro": {k: round(sum(x[i] for x in per) / n, 4) for i, k in enumerate(("precision", "recall", "f1"))},
        "top_false_positives": top(fps, "fp"),
        "top_false_negatives": top(fns, "fn"),
    }


# ---------------------------------------------------------------- sample


def stratified(jobs: list[dict], n: int, seed: int) -> list[dict]:
    """Round-robin over companies (shuffled with the seed) so no single board dominates the sample."""
    rng = random.Random(seed)
    by_co: dict[str, list[dict]] = defaultdict(list)
    for j in sorted(jobs, key=lambda j: j["key"]):
        by_co[j["company"]].append(j)
    for xs in by_co.values():
        rng.shuffle(xs)
    cos = sorted(by_co)
    rng.shuffle(cos)
    out: list[dict] = []
    while len(out) < n and any(by_co.values()):
        for co in cos:
            if by_co[co] and len(out) < n:
                out.append(by_co[co].pop())
    return out


def fetch_postings() -> list[dict]:
    """Same fetch + processing + cap as the nightly sync, keeping each posting's sections."""
    from app.config import get_settings
    from app.ingest import clean
    from app.ingest.run import load_boards, process, select_capped
    from app.ingest.skills import load_taxonomy
    from app.ingest.sources import fetch_board, make_client

    tax = load_taxonomy()
    secs: dict[str, list] = {}
    records = []
    with make_client() as client:
        for b in load_boards():
            try:
                raws = fetch_board(client, b["source"], b["slug"], b["company"])
            except Exception as e:  # noqa: BLE001 - one bad board must not stop the sample
                print(f"  skip {b['slug']}: {type(e).__name__}")
                continue
            for r in raws:
                if r.url and r.title:
                    records.append(process(r, tax))
                    secs[f"{r.source}:{r.external_id}"] = [list(s) for s in clean.sections(r.html, r.extra_sections)]
    kept = select_capped(records, get_settings().max_active_jobs)
    return [
        {
            "key": f"{r.source}:{r.external_id}",
            "company": r.company,
            "title": r.title,
            "url": r.url,
            "sections": secs[f"{r.source}:{r.external_id}"],
        }
        for r in kept
    ]


def cmd_sample(args) -> None:
    from app.ingest.skills import load_taxonomy

    if LABELS_PATH.exists() and not args.force:
        sys.exit(f"{LABELS_PATH} exists (it may hold your corrections). Use --force to overwrite.")
    jobs = [j for j in fetch_postings() if sum(len(s[2]) for s in j["sections"]) > 300]
    sample = stratified(jobs, args.n, args.seed)
    tax = load_taxonomy()
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    with SET_PATH.open("w", encoding="utf-8", newline="\n") as f:
        for j in sample:
            f.write(json.dumps({**j, "fetched_at": now_iso()}, ensure_ascii=False) + "\n")
    with LABELS_PATH.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, FIELDS)
        w.writeheader()
        for j in sample:
            pred = "; ".join(predict(j["sections"], tax))
            w.writerow(
                {
                    "key": j["key"],
                    "company": j["company"],
                    "title": j["title"],
                    "predicted": pred,
                    "gold": pred,
                    "not_in_taxonomy": "",
                    "reviewed": "",
                    "notes": "",
                }
            )
    print(f"sampled {len(sample)} of {len(jobs)} postings from {len({j['company'] for j in sample})} companies")
    print(f"wrote {SET_PATH.name} and {LABELS_PATH.name}; next: python -m evals.skills review")


# ---------------------------------------------------------------- review


def _bold(text: str, tax) -> str:
    parts, last = [], 0
    for m in tax.find(text):
        parts += [text[last : m.start], f"**{text[m.start : m.end]}**"]
        last = m.end
    parts.append(text[last:])
    return "".join(parts)


def cmd_review(args) -> None:
    from app.ingest.skills import load_taxonomy

    tax = load_taxonomy()
    out = [
        "# Skill label review",
        "",
        "For each posting: fix the `gold` cell in `skill_labels.csv` (taxonomy names, `;`-separated), put real",
        "skills the taxonomy lacks in `not_in_taxonomy`, then set `reviewed` to `y`. **Bold** = what the extractor",
        "matched. Gold = taxonomy skills the posting asks for or says the role uses (the taxonomy includes a few",
        "soft skills such as Communication; count them when asked for). Not company products mentioned in passing,",
        "and nothing from the sections marked *skipped* (company boilerplate).",
        "",
    ]
    for i, j in enumerate(load_set().values(), 1):
        out += [
            f"## {i}. {j['title']} at {j['company']}",
            "",
            f"`{j['key']}` · predicted: {', '.join(predict(j['sections'], tax)) or '(none)'}",
            "",
        ]
        for kind, heading, text in j["sections"]:
            label = f"### {heading or '(no heading)'} · _{kind}_"
            if kind == "ignore":
                out += [
                    label + " · *skipped*",
                    "",
                    text[:300].replace("\n", "  \n") + ("…" if len(text) > 300 else ""),
                    "",
                ]
            else:
                out += [label, "", _bold(text, tax).replace("\n", "  \n"), ""]
        out += ["---", ""]
    REVIEW_PATH.write_text("\n".join(out), encoding="utf-8", newline="\n")
    print(f"wrote {REVIEW_PATH}")


# ---------------------------------------------------------------- score


def load_set() -> dict[str, dict]:
    with SET_PATH.open(encoding="utf-8") as f:
        return {j["key"]: j for j in (json.loads(line) for line in f if line.strip())}


def read_labels() -> list[dict]:
    with LABELS_PATH.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def cmd_score(args) -> None:
    from app.ingest.skills import TAXONOMY_PATH, load_taxonomy

    tax = load_taxonomy()
    posts = load_set()
    labels = read_labels()
    reviewed = [r for r in labels if (r.get("reviewed") or "").strip().lower() in YES]
    if not reviewed:
        sys.exit("No rows have reviewed=y yet. Review eval/skill_labels.csv first; unreviewed rows are never scored.")
    rows, unknown, missing = [], [], 0
    for r in reviewed:
        gold = set()
        for name in split(r["gold"]):
            c = tax.canonical(name)
            if c is None:
                unknown.append(f"{r['key']}: {name}")
            else:
                gold.add(c)
        missing += len(split(r.get("not_in_taxonomy", "")))
        rows.append((r["key"], set(predict(posts[r["key"]]["sections"], tax)), gold))
    if unknown:
        sys.exit(
            "Gold names not in the taxonomy (move them to not_in_taxonomy, or add them to the taxonomy):\n  "
            + "\n  ".join(unknown)
        )
    res = score_rows(rows)
    sha = hashlib.sha256(TAXONOMY_PATH.read_bytes()).hexdigest()[:12]
    data = {
        "measured_at": now_iso(),
        "method": METHOD,
        "n_postings": len(rows),
        "n_unreviewed": len(labels) - len(reviewed),
        "n_gold": sum(len(g) for _, _, g in rows),
        "n_companies": len({posts[k]["company"] for k, _, _ in rows}),
        "not_in_taxonomy": missing,
        "taxonomy_sha": sha,
        "aliases": tax.pattern_count,
        **res,
    }
    save_section("extractor", data, key=args.tag)
    m = res["micro"]
    print(
        f"[{args.tag}] n={len(rows)} postings ({data['n_gold']} gold pairs, {data['n_unreviewed']} unreviewed rows "
        f"skipped): precision {m['precision']:.3f} recall {m['recall']:.3f} F1 {m['f1']:.3f}  (taxonomy {sha})"
    )
    for kind in ("top_false_positives", "top_false_negatives"):
        print(f"  {kind}: " + ", ".join(f"{e['skill']} x{e['count']}" for e in res[kind][:6]))


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m evals.skills")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sample")
    s.add_argument("--n", type=int, default=70)
    s.add_argument("--seed", type=int, default=7)
    s.add_argument("--force", action="store_true")
    sub.add_parser("review")
    sc = sub.add_parser("score")
    sc.add_argument("--tag", choices=["before", "after"], required=True)
    args = ap.parse_args(argv)
    {"sample": cmd_sample, "review": cmd_review, "score": cmd_score}[args.cmd](args)


if __name__ == "__main__":
    main()
