# GETaJOB eval results

Generated from `eval/results.json` (updated 2026-11-20T18:17:29+00:00).
Every number below was measured by the scripts in `backend/evals/`; sections not yet run are omitted.

## 1. Skill extractor (rule-based, no LLM)

| Run | Postings | Pairs (gold) | Precision | Recall | F1 | Taxonomy |
|---|---|---|---|---|---|---|
| before | 70 | 993 | 88.9% | 86.2% | 87.5% | `188617de3c71` (817 aliases) |
| after | 70 | 993 | 90.6% | 88.7% | 89.7% | `a15b3cb0fdd5` (828 aliases) |

Method: Postings fetched from the public Greenhouse/Lever board APIs with the production cap and priority selection, then sampled round-robin over companies with a fixed seed; sections snapshotted to eval/skill_set.jsonl. Gold = taxonomy skills the posting asks for or says the role uses (not company/customer names, partner teams, degree-field lists, legal or benefits text). Labelled by an AI coding assistant (Claude) at the author's request, reading every posting in full; single annotator, no inter-rater check; starting from a pre-filled copy of the extractor output (possible anchoring bias towards the extractor). Prediction = the production extractor (required + preferred, boilerplate sections skipped) with the taxonomy at scoring time. Micro P/R/F1 over (posting, skill) pairs.

Caveat: the taxonomy fixes behind the 'after' run were chosen by looking at errors on this same sample, so 'after' is an optimistic, in-sample estimate. A fresh sample would measure them fairly.

## 2. Why-Not-Me simulation vs. full re-rank

Agreement **100.0%** over **1532** simulations (229800 job-rank comparisons, 7 profiles); max score difference 0.00e+00.

Method: Real candidate pools from the database for 7 synthetic profiles (UI role buttons + popular-skill buttons). Each simulation adds one missing skill (every skill present in the pool) or a greedy plan's skill set, then compares Simulator.ranks_with/scores_with against scorer.rank_pool on the extended profile. A simulation agrees when the whole rank order is identical and every score is within 1e-12.

## 3. LLM narrative grounding

| Outcome | Runs | Share |
|---|---|---|
| first try | 31 | 88.6% |
| after retry | 4 | 11.4% |
| fallback | 0 | 0.0% |

n = 35 runs, model `gemini-3.5-flash-lite`; mean 3.11 LLM calls per run.

Method: Real agent runs (Gemini function calling + structured narrative) against the local database: 7 synthetic profiles x 5 questions in a fixed order (the four preset buttons plus a per-job question on each profile's 5th-ranked job). Outcome per run: grounding check passed on the first narrative attempt, passed after the single corrective retry, or failed twice / returned unusable JSON (rule-based fallback). Runs interrupted by quota or provider outages are excluded and listed as interrupted.

## 4. Search latency (`GET /api/search`)

| Where | Jobs | n | Round trip p50 | p95 | Server p50 | p95 |
|---|---|---|---|---|---|---|
| local | 150 | 60 | 22 ms | 33 ms | 4 ms | 6 ms |
| deployed | 1000 | 60 | 181 ms | 223 ms | 16 ms | 19 ms |

Method: 20 fixed queries; one untimed warm-up pass (wakes the host, fills the query-embedding cache), then n timed sequential requests spaced 2.1 s apart from one client. Round trip = client wall time for the HTTP request; server = the API's own took_ms (embedding lookup + hybrid SQL). Cached query embeddings, so the embedding API's latency is not included.
