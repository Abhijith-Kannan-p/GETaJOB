# GETaJOB eval results

Generated from `eval/results.json` (updated 2026-11-20T16:50:33+00:00).
Every number below was measured by the scripts in `backend/evals/`; sections not yet run are omitted.

## 2. Why-Not-Me simulation vs. full re-rank

Agreement **100.0%** over **1532** simulations (229800 job-rank comparisons, 7 profiles); max score difference 0.00e+00.

Method: Real candidate pools from the database for 7 synthetic profiles (UI role buttons + popular-skill buttons). Each simulation adds one missing skill (every skill present in the pool) or a greedy plan's skill set, then compares Simulator.ranks_with/scores_with against scorer.rank_pool on the extended profile. A simulation agrees when the whole rank order is identical and every score is within 1e-12.

## 4. Search latency (`GET /api/search`)

| Where | Jobs | n | Round trip p50 | p95 | Server p50 | p95 |
|---|---|---|---|---|---|---|
| local | 150 | 60 | 22 ms | 33 ms | 4 ms | 6 ms |

Method: 20 fixed queries; one untimed warm-up pass (wakes the host, fills the query-embedding cache), then n timed sequential requests spaced 2.1 s apart from one client. Round trip = client wall time for the HTTP request; server = the API's own took_ms (embedding lookup + hybrid SQL). Cached query embeddings, so the embedding API's latency is not included.
