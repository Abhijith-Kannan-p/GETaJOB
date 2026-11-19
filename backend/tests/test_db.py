"""Ingestion idempotency/close, hybrid SQL, quota fail-closed, API. Needs Postgres (see conftest)."""

import pytest

from app.config import get_settings
from app.ingest import run as ingest
from app.ingest.sources import RawJob
from app.llm import quota
from app.llm.client import FakeEmbedder
from app.search.hybrid import SearchFilters, embed_query, hybrid_search

BOARDS = [
    {"source": "greenhouse", "slug": "acme", "company": "Acme"},
    {"source": "lever", "slug": "beta", "company": "beta"},
]


def raw(i, board="acme", source="greenhouse", title=None, body=None):
    return RawJob(
        source=source,
        external_id=str(i),
        board=board,
        company=board.title(),
        title=title or f"Software Engineer Intern {i}",
        location="Bengaluru, India",
        url=f"https://example.test/{board}/{i}",
        html=body or f"<h3>Requirements</h3><ul><li>Python, SQL and Docker {i}</li></ul>",
    )


class FakeSource:
    def __init__(self, data, broken=()):
        self.data, self.broken = data, set(broken)

    def __call__(self, client, source, slug, company):
        if slug in self.broken:
            raise RuntimeError("boom")
        return list(self.data.get(slug, []))


@pytest.fixture
def boards(monkeypatch):
    monkeypatch.setattr(ingest, "load_boards", lambda only=None: BOARDS)


def test_ingest_is_idempotent_and_closes_vanished(db, boards):
    src = FakeSource({"acme": [raw(1), raw(2), raw(3)], "beta": [raw(9, "beta", "lever")]})
    s1 = ingest.run(100, embedder=FakeEmbedder(), fetcher=src)
    assert (s1.inserted, s1.embedded, s1.closed) == (4, 4, 0)

    s2 = ingest.run(100, embedder=FakeEmbedder(), fetcher=src)
    assert (s2.inserted, s2.updated, s2.unchanged, s2.embedded, s2.closed) == (0, 0, 4, 0, 0)

    src.data["acme"] = [raw(1), raw(2)]  # job 3 vanished upstream
    s3 = ingest.run(100, embedder=FakeEmbedder(), fetcher=src)
    assert s3.closed == 1
    closed = db.execute("SELECT external_id FROM jobs WHERE closed_at IS NOT NULL").fetchall()
    assert [r["external_id"] for r in closed] == ["3"]

    src.data["acme"] = [raw(1), raw(2), raw(3)]  # reappears -> reopened
    ingest.run(100, embedder=FakeEmbedder(), fetcher=src)
    assert db.execute("SELECT count(*) AS n FROM jobs WHERE closed_at IS NULL").fetchone()["n"] == 4
    assert db.execute("SELECT count(*) AS n FROM ingest_runs WHERE finished_at IS NOT NULL").fetchone()["n"] == 4


def test_errored_board_does_not_close_its_jobs(db, boards):
    src = FakeSource({"acme": [raw(1)], "beta": [raw(9, "beta", "lever")]})
    ingest.run(100, embedder=FakeEmbedder(), fetcher=src)
    src.broken = {"beta"}
    s = ingest.run(100, embedder=FakeEmbedder(), fetcher=src)
    assert s.closed == 0 and any("beta" in e for e in s.errors)


def test_changed_text_reembeds_only_that_row(db, boards):
    src = FakeSource({"acme": [raw(1), raw(2)], "beta": []})
    ingest.run(100, embedder=FakeEmbedder(), fetcher=src)
    src.data["acme"] = [raw(1), raw(2, body="<h3>Requirements</h3><p>Rust now</p>")]
    s = ingest.run(100, embedder=FakeEmbedder(), fetcher=src)
    assert (s.updated, s.unchanged, s.embedded) == (1, 1, 1)
    r = db.execute("SELECT required_skills FROM jobs WHERE external_id = '2'").fetchone()
    assert r["required_skills"] == ["Rust"]


def test_embedding_is_capped_and_resumable(db, boards):
    src = FakeSource({"acme": [raw(i) for i in range(7)], "beta": []})
    assert ingest.run(3, embedder=FakeEmbedder(), fetcher=src).embedded == 3
    assert ingest.run(100, embedder=FakeEmbedder(), fetcher=src).embedded == 4


def _corpus():
    jobs = [
        raw(1, title="Machine Learning Intern", body="<h3>Requirements</h3><p>PyTorch, Python, NLP</p>"),
        raw(2, title="Frontend Engineer Intern", body="<h3>Requirements</h3><p>React, TypeScript, CSS</p>"),
        raw(3, title="Data Analyst Intern", body="<h3>Requirements</h3><p>SQL, Tableau, Excel</p>"),
    ]
    return FakeSource({"acme": jobs, "beta": []})


def test_hybrid_search_fuses_both_sides(db, boards):
    ingest.run(100, embedder=FakeEmbedder(), fetcher=_corpus())
    qvec, degraded = embed_query(db, FakeEmbedder(), "machine learning python")
    assert degraded is None
    rows = hybrid_search(db, "machine learning python", qvec, limit=3)
    assert rows[0]["title"] == "Machine Learning Intern"
    assert rows[0]["vec_rank"] is not None and rows[0]["fts_rank"] is not None
    assert float(rows[0]["rrf"]) == pytest.approx(1 / (60 + rows[0]["vec_rank"]) + 1 / (60 + rows[0]["fts_rank"]))


def test_hybrid_search_fulltext_only_when_no_embedder(db, boards):
    ingest.run(100, embedder=FakeEmbedder(), fetcher=_corpus())
    qvec, degraded = embed_query(db, None, "react typescript")
    assert qvec is None and degraded == "fulltext_only"
    rows = hybrid_search(db, "react typescript", None, limit=3)
    assert [r["title"] for r in rows] == ["Frontend Engineer Intern"]
    assert rows[0]["vec_rank"] is None


def test_search_excludes_closed_and_applies_filters(db, boards):
    ingest.run(100, embedder=FakeEmbedder(), fetcher=_corpus())
    db.execute("UPDATE jobs SET closed_at = now() WHERE title LIKE 'Data Analyst%'")
    assert hybrid_search(db, "sql tableau", None) == []
    assert hybrid_search(db, "react", None, filters=SearchFilters(remote_only=True)) == []
    rows = hybrid_search(db, "react", None, filters=SearchFilters(seniority=["intern"]))
    assert [r["title"] for r in rows] == ["Frontend Engineer Intern"]


def test_day_quota_is_atomic_and_fail_closed(db, monkeypatch):
    monkeypatch.setenv("EMBED_RPD", "3")
    get_settings.cache_clear()
    try:
        assert quota.count_day(db, "embed", 2) == 2
        with pytest.raises(quota.QuotaExceeded):
            quota.count_day(db, "embed", 2)
        assert quota.used_today(db, "embed") == 2  # rejected call did not count

        class Broken:
            def execute(self, *a, **k):
                raise RuntimeError("db down")

        with pytest.raises(quota.QuotaExceeded):
            quota.count_day(Broken(), "embed", 1)
    finally:
        get_settings.cache_clear()


def test_api_search_stats_health(db, boards):
    from fastapi.testclient import TestClient

    from app.main import app

    ingest.run(100, embedder=FakeEmbedder(), fetcher=_corpus())
    with TestClient(app) as client:
        assert client.get("/health").json() == {"ok": True}
        st = client.get("/api/stats").json()
        assert st["jobs_active"] == 3 and st["embedding_coverage"] == 1.0
        r = client.get("/api/search", params={"q": "react typescript"}).json()
        assert r["degraded"] == "fulltext_only"  # MOCK_LLM -> no query embedder -> honest banner
        assert r["results"][0]["attribution"] == "via Greenhouse job board API"
        assert client.get("/api/jobs/999999").status_code == 404
        assert client.get("/api/search", params={"q": "x"}).status_code == 422


def test_fulltext_matches_any_term_and_ranks_more_matches_higher(db, boards):
    ingest.run(100, embedder=FakeEmbedder(), fetcher=_corpus())
    rows = hybrid_search(db, "frontend react internship kotlin", None, limit=3)
    assert rows and rows[0]["title"] == "Frontend Engineer Intern"
    assert hybrid_search(db, "!!! ???", None) == []  # query with no lexemes is safe
