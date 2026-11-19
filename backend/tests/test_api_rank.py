"""Ranking / Why-Not-Me API against real Postgres (MOCK_LLM: full-text pool, no network)."""

import pytest
from fastapi.testclient import TestClient

from app.ingest import run as ingest
from app.llm.client import FakeEmbedder
from app.rank import service

from .test_db import BOARDS, FakeSource, raw

REQ = "<h3>Requirements</h3><p>{}</p>"
JOBS = [
    ("Backend Engineer Intern", "Python, SQL, Docker"),
    ("Platform Engineer Intern", "Python, Docker, Kubernetes"),
    ("Data Engineer Intern", "Python, SQL, Apache Airflow, Docker"),
    ("ML Engineer Intern", "Python, PyTorch, Docker"),
    ("Frontend Engineer Intern", "React, TypeScript, CSS"),
    ("Software Engineer Intern", "Java, SQL, Docker"),
    ("DevOps Engineer Intern", "Kubernetes, Terraform, Docker, Python"),
    ("Analytics Intern", "SQL, Tableau, Python"),
]


@pytest.fixture
def client(db, monkeypatch):
    monkeypatch.setattr(ingest, "load_boards", lambda only=None: BOARDS)
    src = FakeSource({"acme": [raw(i, title=t, body=REQ.format(s)) for i, (t, s) in enumerate(JOBS)], "beta": []})
    ingest.run(100, embedder=FakeEmbedder(), fetcher=src)
    service.clear_caches_for_tests()
    from app.main import app

    with TestClient(app) as c:
        yield c
    service.clear_caches_for_tests()


PROFILE = {"skills": ["python", "sql", "made-up-skill"], "target_roles": ["backend engineer intern"]}


def test_rank_returns_breakdown_that_sums_to_score(client):
    r = client.post("/api/rank", json={"profile": PROFILE}).json()
    assert r["degraded"] == "fulltext_only"
    assert r["profile"] == {"skills": ["Python", "SQL"], "unknown_skills": ["made-up-skill"]}
    assert [x["rank"] for x in r["results"]] == list(range(1, len(r["results"]) + 1))
    for x in r["results"]:
        assert sum(t["contribution"] for t in x["breakdown"].values()) == pytest.approx(x["score"], abs=1e-3)
    top = r["results"][0]
    assert set(top["matched_skills"]) <= {"Python", "SQL"}


def test_why_not_me_for_one_job(client):
    ranked = client.post("/api/rank", json={"profile": PROFILE}).json()["results"]
    job = next(x for x in ranked if "Docker" in x["missing_required"])
    r = client.post("/api/why-not-me", json={"profile": PROFILE, "job_id": job["id"]}).json()
    assert "not your chances of being hired" in r["disclaimer"]
    assert r["rank"] == job["rank"]
    assert "Docker" in [m["skill"] for m in r["missing"]]
    assert r["fixes"] and all(f["job_rank"]["after"] <= f["job_rank"]["before"] for f in r["fixes"])
    docker = next(f for f in r["fixes"] if f["skill"] == "Docker")
    assert docker["effort_tier"] == "low" and docker["effort_hours"] == 10
    # job-focused plan: only this job's missing skills, and it may be empty if nothing strictly helps this job
    assert {s["skill"] for s in r["plan"]["steps"]} <= {m["skill"] for m in r["missing"]}
    jr = r["plan"]["cumulative"]["job_rank"]
    assert jr is None or jr["after"] < jr["before"]


def test_why_not_me_overall_and_simulate(client):
    r = client.post("/api/why-not-me", json={"profile": PROFILE}).json()
    assert r["fixes"][0]["jobs_with_skill"] >= 3
    s = client.post("/api/simulate", json={"profile": PROFILE, "skill": "docker"}).json()
    assert s["effect"]["skill"] == "Docker" and s["effect"]["rr_gain"] > 0
    assert client.post("/api/simulate", json={"profile": PROFILE, "skill": "nope"}).status_code == 422
    assert client.post("/api/simulate", json={"profile": PROFILE, "skill": "Python"}).status_code == 422


def test_bad_input_rejected(client):
    long = {**PROFILE, "free_text": "x" * 301}
    assert client.post("/api/rank", json={"profile": long}).status_code == 422
    assert client.post("/api/why-not-me", json={"profile": PROFILE, "job_id": 999999}).status_code == 404


def test_skills_autocomplete(client):
    r = client.get("/api/skills", params={"q": "dock"}).json()
    assert r["skills"][0] == "Docker"
