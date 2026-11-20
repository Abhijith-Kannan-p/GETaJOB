"""The eval scripts' own logic: metrics, percentiles, results merging, resumable LLM runs. Fakes only."""

import json

import pytest

from app.llm.client import FakeLLM, Reply, ToolCall
from app.llm.quota import QuotaExceeded
from evals import common, grounding, latency, simulation
from evals import skills as sk

from .test_api_rank import client  # noqa: F401 - fixture reused (8 jobs in a real test DB)


@pytest.fixture
def eval_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "EVAL_DIR", tmp_path)
    monkeypatch.setattr(common, "RESULTS", tmp_path / "results.json")
    monkeypatch.setattr(common, "REPORT", tmp_path / "RESULTS.md")
    monkeypatch.setattr(grounding, "RUNS_PATH", tmp_path / "llm_runs.jsonl")
    monkeypatch.setattr(grounding, "OUTAGES_PATH", tmp_path / "llm_outages.jsonl")
    return tmp_path


# ---------------- metrics ----------------


def test_prf_and_micro_vs_macro():
    rows = [("a", {"Python", "SQL", "Go"}, {"Python", "SQL"}), ("b", {"Java"}, {"Java", "Docker", "AWS"})]
    r = sk.score_rows(rows)
    assert r["micro"] == {"precision": 0.75, "recall": 0.6, "f1": 0.6667, "tp": 3, "fp": 1, "fn": 2}
    assert r["macro"]["precision"] == round((2 / 3 + 1) / 2, 4)
    assert [e["skill"] for e in r["top_false_positives"]] == ["Go"]
    assert {e["skill"] for e in r["top_false_negatives"]} == {"Docker", "AWS"}
    assert sk.prf(0, 0, 0)["f1"] == 1.0  # nothing to find, nothing found


def test_split_accepts_semicolons_and_commas():
    assert sk.split(" Python; SQL ,Go;; ") == ["Python", "SQL", "Go"]


def test_predict_matches_the_production_extractor():
    secs = [["required", "Requirements", "Python and SQL"], ["ignore", "About us", "We love Kubernetes"]]
    assert sk.predict(secs, None) == ["Python", "SQL"]  # boilerplate section skipped, as in ingest


def test_stratified_sample_is_deterministic_and_spreads_companies():
    jobs = [{"key": f"{c}{i}", "company": c} for c in "ABC" for i in range(10)]
    s1, s2 = sk.stratified(jobs, 9, seed=1), sk.stratified(jobs, 9, seed=1)
    assert s1 == s2
    assert sorted(j["company"] for j in s1) == list("AAABBBCCC")


def test_percentile_is_linear_interpolation():
    xs = [10, 20, 30, 40, 50]
    assert common.percentile(xs, 50) == 30
    assert common.percentile(xs, 95) == pytest.approx(48)
    assert common.percentile([7], 95) == 7


# ---------------- results.json ----------------


def test_sections_merge_and_summary_only_has_measured_numbers(eval_dir):
    common.save_section(
        "simulation",
        {
            "agreement": 1.0,
            "n_simulations": 5,
            "n_rank_comparisons": 40,
            "n_profiles": 1,
            "max_abs_score_diff": 0.0,
            "method": "m",
        },
    )
    res = common.save_section(
        "latency",
        {"n": 3, "method": "m", "round_trip_ms": {"p50": 1, "p95": 2}, "server_ms": {"p50": 0.5, "p95": 1}},
        key="local",
    )
    assert set(res) >= {"simulation", "latency", "summary", "updated_at"}
    assert res["summary"] == {
        "simulation": {"agreement": 1.0, "n_simulations": 5},
        "latency": {"local": {"p50_ms": 1, "p95_ms": 2, "n": 3}},
    }
    assert "extractor" not in res["summary"] and "grounding" not in res["summary"]
    assert "Why-Not-Me simulation" in (eval_dir / "RESULTS.md").read_text(encoding="utf-8")


# ---------------- eval 2 + 4 ----------------


def test_simulation_agrees_with_full_rerank_on_a_real_pool(client, eval_dir):  # noqa: F811
    data = simulation.run(no_embed=True)
    assert data["n_simulations"] > 0
    assert data["agreement"] == 1.0 and data["rank_mismatches"] == 0 and data["failures"] == []


def test_latency_uses_warmup_and_reports_percentiles():
    calls = []

    def fake(base, q):
        calls.append(q)
        return 100.0 + len(calls), {"took_ms": 5, "degraded": None}

    d = latency.run("http://x", n=10, spacing=0, fetcher=fake)
    assert len(calls) == len(latency.QUERIES) + 10  # warm-up pass is not timed
    assert d["n"] == 10 and d["first_request_ms"] == 101.0
    assert d["round_trip_ms"]["p50"] == pytest.approx(125.5)
    assert d["degraded_responses"] == 0


# ---------------- eval 3 ----------------


def _good(prompt: str) -> dict:
    fid = next(ln.split(":")[0] for ln in prompt.splitlines() if " ranks #1 of " in ln)
    return {
        "headline": "Your top match is the first job in the list.",
        "headline_facts": [fid],
        "points": [],
        "next_steps": [],
    }


def _fake(c):
    return FakeLLM([Reply("", [ToolCall("rank_jobs", {"limit": 3})]), Reply("done")], narrative=_good)


def test_grounding_eval_records_resumes_and_stops_cleanly_on_quota(client, eval_dir):  # noqa: F811
    assert grounding.run(3, pause=0, llm_factory=_fake, embedder=None) == 0
    runs = grounding.load_runs()
    assert [r["i"] for r in runs] == [0, 1, 2]
    assert all(r["outcome"] == "first_try" and r["llm_calls"] == 3 for r in runs)
    assert runs[0]["events"][-1]["type"] == "done"  # full trace kept: usable as a recorded run

    def quota(c):
        return FakeLLM([QuotaExceeded("llm", "rpd")])

    assert grounding.run(5, pause=0, llm_factory=quota, embedder=None) == 2  # stops, records nothing
    assert len(grounding.load_runs()) == 3

    assert grounding.run(5, pause=0, llm_factory=_fake, embedder=None) == 0  # resumes at index 3
    assert [r["i"] for r in grounding.load_runs()] == [0, 1, 2, 3, 4]

    s = grounding.summarise(grounding.load_runs())
    assert s["n_runs"] == 5 and s["rates"]["first_try"] == 1.0 and s["counts"]["fallback"] == 0


def test_bad_json_from_the_model_counts_as_fallback_not_as_an_outage(client, eval_dir):  # noqa: F811
    def bad(c):
        return FakeLLM([Reply("done")], narrative=None)  # structured() -> BadOutput

    assert grounding.run(1, pause=0, llm_factory=bad, embedder=None) == 0
    (r,) = grounding.load_runs()
    assert r["outcome"] == "fallback" and r["interrupted"] == "bad_output"
    assert grounding.summarise([r])["bad_output_runs"] == 1


def test_eval_endpoint_serves_offline_results_and_live_counters(client, db, monkeypatch, tmp_path):  # noqa: F811
    from app import main
    from app.api.agent import count_event

    path = tmp_path / "results.json"
    path.write_text(json.dumps({"summary": {"simulation": {"agreement": 1.0, "n_simulations": 2}}}), encoding="utf-8")
    monkeypatch.setattr(main, "EVAL_CANDIDATES", (path,))
    count_event(db, "grounding_first_try")
    count_event(db, "grounding_first_try")
    count_event(db, "grounding_fallback")
    body = client.get("/api/eval").json()
    assert body["offline"]["summary"]["simulation"]["n_simulations"] == 2
    assert body["live_grounding"]["counts"] == {"first_try": 2, "fallback": 1}
    assert client.get("/api/stats").json()["eval"] == {"simulation": {"agreement": 1.0, "n_simulations": 2}}


# ---------------- recorded runs (slice 7) ----------------


def _run(i, q, outcome, interrupted=None):
    return {"i": i, "question": q, "outcome": outcome, "interrupted": interrupted, "profile": "backend",
            "model": "m", "at": "t", "events": [{"type": "memory"}, {"type": "done"}]}


def test_recorded_runs_are_passed_gemini_runs_one_per_question():
    from evals import recorded

    q = grounding.QUESTIONS
    runs = [
        _run(0, q[0], "fallback"),  # failed grounding: never replayed
        _run(1, q[0], "first_try", interrupted="quota"),
        _run(2, q[1], "first_try"),
        _run(3, q[0], "first_try"),
        _run(4, q[4], "after_retry"),  # shows the self-correction: preferred for one slot
        _run(5, q[0], "first_try"),
    ]
    picked = recorded.choose(runs, 3)
    assert [r["i"] for r in picked] == [4, 3, 2]
    rec = recorded.to_recorded(picked[1])
    assert rec["id"] == "run-3" and rec["profile"]["skills"] and [e["type"] for e in rec["events"]] == ["done"]


def test_recorded_endpoint_serves_the_file_or_nothing(client, monkeypatch, tmp_path):  # noqa: F811
    from app.api import agent

    monkeypatch.setattr(agent, "RECORDED_RUNS", tmp_path / "missing.json")
    agent._recorded.cache_clear()
    assert client.get("/api/agent/recorded").json() == {"runs": []}
    path = tmp_path / "recorded.json"
    path.write_text(json.dumps([{"id": "run-1", "question": "q", "events": []}]), encoding="utf-8")
    monkeypatch.setattr(agent, "RECORDED_RUNS", path)
    agent._recorded.cache_clear()
    assert client.get("/api/agent/recorded").json()["runs"][0]["id"] == "run-1"
    agent._recorded.cache_clear()


def test_provider_outage_retries_the_run_and_is_logged_but_never_recorded(client, eval_dir):  # noqa: F811
    from app.llm.client import ProviderUnavailable

    calls = {"n": 0}

    def flaky(c):
        calls["n"] += 1
        return FakeLLM([ProviderUnavailable("503")]) if calls["n"] == 1 else _fake(c)

    assert grounding.run(1, pause=0, llm_factory=flaky, embedder=None, outage_pause=0) == 0
    assert [r["outcome"] for r in grounding.load_runs()] == ["first_try"]  # the retry, not the outage
    assert [o["kind"] for o in grounding.load_outages()] == ["provider"]
    assert grounding.summarise(grounding.load_runs())["interrupted_attempts"] == 1

    def down(c):
        return FakeLLM([ProviderUnavailable("503")])

    assert grounding.run(2, pause=0, llm_factory=down, embedder=None, outage_retries=2, outage_pause=0) == 2
    assert len(grounding.load_outages()) == 1 + 3 and len(grounding.load_runs()) == 1
