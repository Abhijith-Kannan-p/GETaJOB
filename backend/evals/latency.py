"""Eval 4: search latency (p50 / p95) of GET /api/search, measured from the client.

    python -m evals.latency --base http://127.0.0.1:8000 --label local
    python -m evals.latency --base https://<your-api>.onrender.com --label deployed

20 fixed queries. One untimed warm-up pass first (wakes a sleeping Render instance and fills the query
embedding cache, so the timed pass measures retrieval, not the embedding API or a cold start; the first
request's time is reported separately as `first_request_ms`, n = 1). Then --n timed requests, round-robin,
spaced 2.1 s apart to stay under the endpoint's 30/minute rate limit. Reports the client round trip and the
server's own `took_ms` (embedding lookup + SQL, without network and serialisation).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request

from evals.common import now_iso, percentile, save_section

QUERIES = [
    "python backend intern",
    "react frontend",
    "machine learning internship",
    "data analyst sql",
    "devops kubernetes",
    "java spring",
    "remote software engineer",
    "new grad software engineer",
    "data engineer airflow",
    "ios developer",
    "android kotlin",
    "security engineer",
    "cloud aws",
    "full stack typescript",
    "golang backend",
    "nlp research intern",
    "product analyst",
    "site reliability",
    "embedded c++",
    "computer vision",
]
METHOD = (
    "20 fixed queries; one untimed warm-up pass (wakes the host, fills the query-embedding cache), then n timed "
    "sequential requests spaced 2.1 s apart from one client. Round trip = client wall time for the HTTP request; "
    "server = the API's own took_ms (embedding lookup + hybrid SQL). Cached query embeddings, so the embedding "
    "API's latency is not included."
)


def fetch(base: str, q: str, timeout: float = 90) -> tuple[float, dict]:
    url = f"{base}/api/search?" + urllib.parse.urlencode({"q": q, "limit": 20})
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "getajob-eval"})
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = json.loads(r.read())
    return (time.perf_counter() - t0) * 1000, body


def stats(xs: list[float]) -> dict:
    return {
        "p50": round(percentile(xs, 50), 1),
        "p95": round(percentile(xs, 95), 1),
        "min": round(min(xs), 1),
        "max": round(max(xs), 1),
    }


def run(base: str, n: int, spacing: float, fetcher=fetch) -> dict:
    first_ms = None
    for k, q in enumerate(QUERIES):  # warm-up, untimed
        ms, _ = fetcher(base, q)
        if k == 0:
            first_ms = round(ms, 1)
        time.sleep(spacing)
    rt, sv, degraded = [], [], 0
    for i in range(n):
        ms, body = fetcher(base, QUERIES[i % len(QUERIES)])
        rt.append(ms)
        sv.append(float(body["took_ms"]))
        degraded += body.get("degraded") is not None
        if i < n - 1:
            time.sleep(spacing)
    return {
        "measured_at": now_iso(),
        "method": METHOD,
        "n": n,
        "round_trip_ms": stats(rt),
        "server_ms": stats(sv),
        "first_request_ms": first_ms,
        "degraded_responses": degraded,
    }


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m evals.latency")
    ap.add_argument("--base", required=True)
    ap.add_argument("--label", required=True, choices=["local", "deployed"])
    ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--spacing", type=float, default=2.1)
    args = ap.parse_args(argv)
    base = args.base.rstrip("/")
    try:
        data = run(base, args.n, args.spacing)
        with urllib.request.urlopen(f"{base}/api/stats", timeout=60) as r:  # corpus size the numbers refer to
            data["jobs_active"] = json.loads(r.read())["jobs_active"]
    except OSError as e:
        sys.exit(f"request failed ({e}); nothing recorded")
    save_section("latency", data, key=args.label)
    r, s = data["round_trip_ms"], data["server_ms"]
    print(
        f"[{args.label}] n={data['n']}: round trip p50 {r['p50']} ms, p95 {r['p95']} ms; server p50 {s['p50']} ms, "
        f"p95 {s['p95']} ms; first request {data['first_request_ms']} ms; degraded {data['degraded_responses']}"
    )


if __name__ == "__main__":
    main()
