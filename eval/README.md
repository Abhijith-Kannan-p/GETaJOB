# Evals

Four evals, each a script in `backend/evals/`. Every script writes its section of `results.json` (numbers +
method + n + date) and regenerates `RESULTS.md`. The API serves `results.json` at `/api/eval`, and the `/eval`
page renders it. A section that has not been run is absent: no placeholder numbers.

Run everything from `backend/` with the venv's Python (`.\.venv\Scripts\python` on Windows).

| # | Question | Command | Real API calls |
|---|---|---|---|
| 1 | Does the rule-based skill extractor find the skills a posting asks for? | `python -m evals.skills sample / review / score --tag before\|after` | none (job boards only, for `sample`) |
| 2 | Does Why-Not-Me's fast simulation equal a full re-rank? | `python -m evals.simulation` (`--no-embed` for zero calls) | ≤ 7 query embeddings (cached) |
| 3 | How often does the LLM narrative pass the grounding check? | `python -m evals.grounding --n 35` | ≤ 4 Gemini calls per run (≤ 140) |
| 4 | Search latency p50 / p95 | `python -m evals.latency --base <url> --label local\|deployed` | ≤ 20 query embeddings (warm-up) |

## 1. Skill extractor: the labelling workflow

Status: done. All 70 rows were labelled by an AI coding assistant (Claude) at the author's request. Each
row's `notes` column says what was corrected and why. Scored before and after 24 taxonomy fixes (see
`RESULTS.md`).

1. `sample` (already done): fetched the 30 boards from the public APIs, applied the production cap and
   priority selection (1,000 postings), and drew 70 postings round-robin over companies (seed 7).
   - Each posting's sections are frozen in `skill_set.jsonl`, so scores stay reproducible after the postings
     close.
   - `skill_labels.csv` is pre-filled with the extractor's output, both in `predicted` and in `gold`.
2. `review` writes `skill_review.md` (gitignored). For each posting it shows every section, with the
   extractor's matches in **bold** and boilerplate sections marked *skipped*.
3. Correct `skill_labels.csv`, one row per posting:
   - `gold`: the taxonomy skills the posting really asks for or says the role uses, `;`-separated. Remove false
     matches (for example "Go" in "go to market") and add skills the extractor missed.
   - `not_in_taxonomy`: real skills the taxonomy does not have. They are counted, not scored.
   - `reviewed`: set to `y`. Only reviewed rows are scored, so the eval can be done in batches.
4. `score --tag before`: precision/recall/F1 with the current taxonomy, plus the most common false positives
   and false negatives.
5. Fix the taxonomy (`backend/data/skills_taxonomy.yaml`: aliases, `block` phrases) based on those errors,
   then run `score --tag after`. Both runs are kept, together with the taxonomy hash each one used.

Caveat, stated on the page: labels pre-filled from the extractor can anchor the reviewer towards agreeing with
it, which would inflate precision and recall.

## 3. Grounding consistency

- The grid is the 7 synthetic profiles in `evals/profiles.py` × 5 questions: the 4 preset buttons, plus "why
  am I not ranking higher for this job?" on each profile's 5th-ranked job. The order is fixed and
  question-major.
- Every finished run is appended to `llm_runs.jsonl`, with its full event trace. Rerunning resumes where it
  stopped.
- On quota exhaustion or a provider outage the script stops cleanly and records nothing for that run. A model
  answer in the wrong JSON shape counts as a fallback.
- The model name is printed at the start and stored with every run.
- Default pacing is 20 s between runs, to stay under 12 requests/minute.
