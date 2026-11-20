"""Agent loop, sessions/memory, forget-me and the SSE endpoint. Fake LLMs only; real Postgres."""

import json

import pytest

from app.agent.run import run_agent
from app.agent.tools import AgentContext
from app.api.limits import limiter
from app.api.rank import ProfileIn
from app.config import AGENT_MAX_LLM_CALLS, Settings, get_settings
from app.llm.client import FakeEmbedder, FakeLLM, Reply, ToolCall
from app.llm.quota import QuotaExceeded

from .test_api_rank import PROFILE, client  # noqa: F401 - fixture reused

PROF = ProfileIn(**PROFILE)


@pytest.fixture(autouse=True)
def _reset_limits():
    limiter.reset()
    yield
    limiter.reset()


def sse(text: str) -> list[dict]:
    out = []
    for block in text.strip().split("\n\n"):
        data = [ln[6:] for ln in block.splitlines() if ln.startswith("data: ")]
        if data:
            out.append(json.loads(data[0]))
    return out


def new_session(client, profile=PROFILE):  # noqa: F811
    tok = client.post("/api/session").json()["token"]
    h = {"X-Session-Token": tok}
    if profile is not None:
        assert client.put("/api/me/profile", json=profile, headers=h).status_code == 200
    return h


def run(ctx, llm, **kw):
    return list(run_agent(ctx, kw.pop("q", "what next?"), llm, **kw))


# ---------------- config ----------------


def test_daily_max_runs_is_70_percent_of_llm_rpd_over_worst_case_calls():
    assert AGENT_MAX_LLM_CALLS == 4
    assert Settings(_env_file=None, llm_rpd=400).daily_max_runs() == 70  # 70 * 4 = 280 = 70% of 400
    assert Settings(_env_file=None, llm_rpd=400, DAILY_MAX_RUNS=500).daily_max_runs() == 70  # can only lower
    assert Settings(_env_file=None, llm_rpd=400, DAILY_MAX_RUNS=20).daily_max_runs() == 20


# ---------------- loop (direct) ----------------


def test_offline_run_uses_rules_and_a_fully_grounded_template(client, db):  # noqa: F811
    evs = run(AgentContext(db, None, PROF), None)
    assert evs[0] == {"type": "start", "mode": "offline", "max_llm_calls": 0}
    assert [e["name"] for e in evs if e["type"] == "tool_call"] == ["rank_jobs", "best_skills"]
    nar = next(e for e in evs if e["type"] == "narrative")
    assert nar["source"] == "template"
    assert nar["grounding"]["dropped"] == []  # the rule-based narrative must pass its own check
    assert nar["narrative"]["headline"]["text"].startswith("Your best current match is")
    assert evs[-1]["type"] == "done" and evs[-1]["llm_calls"] == 0


def test_offline_run_for_one_job_explains_it(client, db):  # noqa: F811
    job = client.post("/api/rank", json={"profile": PROFILE}).json()["results"][2]
    evs = run(AgentContext(db, None, PROF), None, job_id=job["id"])
    nar = next(e for e in evs if e["type"] == "narrative")
    head = nar["narrative"]["headline"]
    assert head["text"].startswith(f"For {job['title']} at {job['company']} you rank #{job['rank']} of ")
    assert [e["name"] for e in evs if e["type"] == "tool_call"][0] == "explain_job"
    assert nar["grounding"]["dropped"] == []


def _narrative_from_prompt(prompt: str) -> dict:
    fid = next(ln.split(":")[0] for ln in prompt.splitlines() if " ranks #1 of " in ln)
    return {
        "headline": "Your top match is the first job in the list.",
        "headline_facts": [fid],
        "points": [
            {"text": "You are #999 for every job.", "facts": [fid]},  # invented number
            {"text": "This is grounded in nothing.", "facts": ["F99"]},  # unknown fact
        ],
        "next_steps": [{"skill": "Underwater Basket Weaving", "why": "Fun.", "facts": [fid]}],
    }


def test_llm_run_calls_tools_and_drops_ungrounded_claims(client, db):  # noqa: F811
    fake = FakeLLM(
        [Reply("", [ToolCall("rank_jobs", {"limit": 3}), ToolCall("best_skills", {})]), Reply("Enough facts.")],
        narrative=_narrative_from_prompt,
    )
    evs = run(AgentContext(db, None, PROF), fake)
    calls = [(e["name"], e["by"]) for e in evs if e["type"] == "tool_call"]
    assert calls == [("rank_jobs", "model"), ("best_skills", "model")]
    nar = next(e for e in evs if e["type"] == "narrative")
    assert nar["source"] == "llm"
    assert nar["narrative"]["headline"]["text"] == "Your top match is the first job in the list."
    assert nar["narrative"]["points"] == [] and nar["narrative"]["next_steps"] == []
    assert sorted(d["reason"] for d in nar["grounding"]["dropped"]) == [
        "cites unknown fact F99",
        "not a known skill",
        "number 999 not in cited facts",
    ]
    assert fake.calls == 3  # 2 tool turns + 1 narrative


def test_llm_calls_are_bounded_even_if_the_model_never_stops(client, db):  # noqa: F811
    forever = [Reply("", [ToolCall("rank_jobs", {"limit": 2})]) for _ in range(20)]
    fake = FakeLLM(forever, narrative={"headline": "x", "headline_facts": [], "points": [], "next_steps": []})
    evs = run(AgentContext(db, None, PROF), fake)
    assert fake.calls == AGENT_MAX_LLM_CALLS
    assert sum(e["type"] == "thinking" for e in evs) == AGENT_MAX_LLM_CALLS
    # the model's headline cited nothing, so the grounded rule-based narrative is shown instead
    nar = next(e for e in evs if e["type"] == "narrative")
    assert nar["source"] == "template"
    assert any(e["type"] == "notice" and "grounding" in e["message"] for e in evs)


def test_quota_error_mid_run_is_not_retried_and_falls_back(client, db):  # noqa: F811
    fake = FakeLLM([Reply("", [ToolCall("rank_jobs", {})]), QuotaExceeded("llm", "day")], narrative={})
    evs = run(AgentContext(db, None, PROF), fake)
    assert fake.calls == 2  # no retry, and no narrative call after the quota error
    assert any(e["type"] == "notice" and "quota" in e["message"] for e in evs)
    nar = next(e for e in evs if e["type"] == "narrative")
    assert nar["source"] == "template" and nar["narrative"]["headline"] is not None


def test_bad_tool_arguments_do_not_crash_the_run(client, db):  # noqa: F811
    fake = FakeLLM(
        [
            Reply(
                "",
                [
                    ToolCall("explain_job", {"job_id": "abc"}),
                    ToolCall("no_such_tool", {}),
                    ToolCall("simulate_skill", {"skill": "not a skill"}),
                ],
            )
        ],
        narrative=None,
    )
    evs = run(AgentContext(db, None, PROF), fake)
    results = [e for e in evs if e["type"] == "tool_result"]
    assert [("error" in r) for r in results[:3]] == [True, True, True]
    assert evs[-1]["type"] == "done"


# ---------------- sessions, memory, forget-me ----------------


def test_session_plan_and_profile_memory(client):  # noqa: F811
    h = new_session(client)
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/me", headers={"X-Session-Token": "short"}).status_code == 401
    me = client.get("/api/me", headers=h).json()
    assert me["profile"]["skills"] == PROFILE["skills"] and me["plan"] == []

    assert client.post("/api/me/plan", json={"skill": "nope-skill"}, headers=h).status_code == 422
    plan = client.post("/api/me/plan", json={"skill": "docker", "source": "agent"}, headers=h).json()["plan"]
    assert [(p["skill"], p["status"], p["source"]) for p in plan] == [("Docker", "todo", "agent")]
    r = client.patch(f"/api/me/plan/{plan[0]['id']}", json={"status": "done"}, headers=h).json()
    assert r["added_to_profile"] is True and "Docker" in r["profile"]["skills"]
    assert "Docker" in client.get("/api/me", headers=h).json()["profile"]["skills"]
    assert client.delete(f"/api/me/plan/{plan[0]['id']}", headers=h).json()["plan"] == []


def test_forget_me_deletes_everything(client, db):  # noqa: F811
    from app.main import app

    app.state.embedder = FakeEmbedder()
    try:
        h = new_session(client)
        client.post("/api/me/plan", json={"skill": "Docker"}, headers=h)
        client.post("/api/agent/run", json={"message": "what next?"}, headers=h)
        client.post("/api/rank", json={"profile": PROFILE})  # caches the profile embedding
        assert db.execute("SELECT count(*) AS n FROM embedding_cache").fetchone()["n"] >= 1
        n_cache = db.execute("SELECT count(*) AS n FROM embedding_cache").fetchone()["n"]

        assert client.delete("/api/me", headers=h).json() == {"deleted": True}
        for t in ("profiles", "plan_items", "messages"):
            assert db.execute(f"SELECT count(*) AS n FROM {t}").fetchone()["n"] == 0, t
        assert db.execute("SELECT count(*) AS n FROM embedding_cache").fetchone()["n"] == n_cache - 1
        assert client.get("/api/me", headers=h).status_code == 404
        assert client.delete("/api/me", headers=h).json() == {"deleted": False}
    finally:
        app.state.embedder = None


# ---------------- SSE endpoint ----------------


def test_agent_endpoint_streams_and_remembers(client):  # noqa: F811
    h = new_session(client)
    r = client.post("/api/agent/run", json={"message": "What should I learn next?"}, headers=h)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    evs = sse(r.text)
    assert [e["type"] for e in evs][0] == "start" and evs[-1]["type"] == "done"
    msgs = client.get("/api/me", headers=h).json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[1]["content"]["source"] == "template"
    assert [t["name"] for t in msgs[1]["content"]["trace"]] == ["rank_jobs", "best_skills"]


def test_agent_endpoint_guards(client, monkeypatch):  # noqa: F811
    assert client.post("/api/agent/run", json={}).status_code == 401
    h = new_session(client, profile=None)
    assert client.post("/api/agent/run", json={}, headers=h).status_code == 422  # no profile yet
    h = new_session(client)
    bad = client.post("/api/agent/run", json={"message": "mail me at a.b@example.com"}, headers=h)
    assert bad.status_code == 422 and "personal" in bad.json()["detail"]
    assert client.post("/api/agent/run", json={"message": "x" * 301}, headers=h).status_code == 422

    monkeypatch.setattr(get_settings(), "session_max_runs", 1)
    assert client.post("/api/agent/run", json={}, headers=h).status_code == 200
    assert client.post("/api/agent/run", json={}, headers=h).status_code == 429


def test_global_daily_cap_switches_to_offline_without_llm_calls(client, monkeypatch):  # noqa: F811
    from app.main import app

    fake = FakeLLM([], narrative=None)
    monkeypatch.setattr(app.state, "llm_factory", lambda c: fake, raising=False)
    monkeypatch.setattr(get_settings(), "daily_max_runs_env", 0)
    h = new_session(client)
    evs = sse(client.post("/api/agent/run", json={}, headers=h).text)
    assert evs[0]["type"] == "notice" and "used up" in evs[0]["message"]
    assert evs[1] == {"type": "start", "mode": "offline", "max_llm_calls": 0}
    assert fake.calls == 0
