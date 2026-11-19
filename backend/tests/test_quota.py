"""Quota split, TPM pacing, 429 handling and the embedding cache, through the REAL GeminiEmbedder wrapper
with an in-process fake SDK client (no network, per CLAUDE.md)."""

from types import SimpleNamespace

import pytest
from google.genai import errors

from app.config import get_settings
from app.ingest import run as ingest
from app.llm import client as llm
from app.llm import quota
from app.llm.client import FakeEmbedder
from app.search import hybrid

from .test_db import BOARDS, FakeSource, raw


class FakeModels:
    def __init__(self, fail_429=False):
        self.calls = 0
        self.texts = 0
        self.fail_429 = fail_429
        self._fake = FakeEmbedder(64)

    def embed_content(self, model, contents, config):
        self.calls += 1
        if self.fail_429:
            raise errors.ClientError(429, {"error": {"code": 429, "message": "quota", "status": "RESOURCE_EXHAUSTED"}})
        self.texts += len(contents)
        return SimpleNamespace(embeddings=[SimpleNamespace(values=v) for v in self._fake.embed(contents)])


@pytest.fixture
def real_wrapper(monkeypatch):
    """GeminiEmbedder configured as if a key + model were set, but talking to FakeModels."""

    def make(rpd=1000, reserve=250, rpm=1000, tpm=10**6, fail_429=False):
        for k, v in {
            "GEMINI_API_KEY": "test-key",
            "EMBED_MODEL": "fake-embed",
            "MOCK_LLM": "0",
            "EMBED_RPD": str(rpd),
            "EMBED_RESERVE_RPD": str(reserve),
            "EMBED_RPM": str(rpm),
            "EMBED_TPM": str(tpm),
        }.items():
            monkeypatch.setenv(k, v)
        get_settings.cache_clear()
        quota.reset_for_tests()
        hybrid.clear_lru_for_tests()
        models = FakeModels(fail_429)
        monkeypatch.setattr(llm, "_genai", lambda: SimpleNamespace(models=models))
        return llm.GeminiEmbedder(), models

    yield make
    get_settings.cache_clear()
    quota.reset_for_tests()


def test_tpm_window_counts_tokens(monkeypatch):
    monkeypatch.setenv("EMBED_TPM", "100")
    monkeypatch.setenv("EMBED_RPM", "1000")
    get_settings.cache_clear()
    quota.reset_for_tests()
    try:
        quota.acquire_minute("embed", 1, tokens=60)
        with pytest.raises(quota.QuotaExceeded) as e:
            quota.acquire_minute("embed", 1, tokens=60)  # 120 > 100 tokens in the window
        assert e.value.scope == "minute"
        assert quota.estimate_tokens(["x" * 400]) == 101
    finally:
        get_settings.cache_clear()
        quota.reset_for_tests()


def test_ingest_stops_at_budget_and_leaves_reserve(db, monkeypatch, real_wrapper):
    emb, models = real_wrapper(rpd=60, reserve=50)  # ingest may use 10/day
    monkeypatch.setattr(ingest, "load_boards", lambda only=None: BOARDS)
    src = FakeSource({"acme": [raw(i) for i in range(30)], "beta": []})
    s = ingest.run(10_000, embedder=emb, fetcher=src)
    assert s.embedded <= 10 and quota.used_today(db, "embed") <= 10
    assert any("quota" in e for e in s.errors)
    calls_before = models.calls
    # live query can still use the reserve
    v, degraded = hybrid.embed_query(db, emb, "python intern")
    assert degraded is None and v is not None and models.calls == calls_before + 1


def test_oversize_batch_rejected_without_counting(db, real_wrapper):
    real_wrapper(rpd=5)
    with pytest.raises(quota.QuotaExceeded):
        quota.count_day(db, "embed", 6)
    assert quota.used_today(db, "embed") == 0


def test_provider_429_stops_immediately_no_retry(db, real_wrapper):
    emb, models = real_wrapper(fail_429=True)
    with pytest.raises(quota.QuotaExceeded) as e:
        emb.embed(["a"], task="RETRIEVAL_QUERY", conn=db)
    assert e.value.scope == "provider" and models.calls == 1
    # subsequent calls short-circuit without touching the provider
    with pytest.raises(quota.QuotaExceeded):
        emb.embed(["b"], task="RETRIEVAL_QUERY", conn=db)
    assert models.calls == 1
    # and search degrades to full-text instead of erroring
    assert hybrid.embed_query(db, emb, "anything") == (None, "fulltext_only")


def test_embedding_cache_avoids_repeat_calls(db, real_wrapper):
    emb, models = real_wrapper()
    a, _ = hybrid.embed_query(db, emb, "Python  Intern")
    hybrid.clear_lru_for_tests()  # force the Postgres cache path (as after a Render restart)
    b, _ = hybrid.embed_query(db, emb, "python intern")
    assert models.calls == 1 and a == pytest.approx(b)
    hybrid.embed_cached(db, emb, "profile: skills=Python,SQL", "RETRIEVAL_QUERY")
    hybrid.embed_cached(db, emb, "profile: skills=Python,SQL", "RETRIEVAL_QUERY")  # unchanged facts
    assert models.calls == 2
    hybrid.embed_cached(db, emb, "profile: skills=Python,SQL,Docker", "RETRIEVAL_QUERY")  # facts changed
    assert models.calls == 3


def test_embeds_highest_priority_first(db, monkeypatch):
    monkeypatch.setattr(ingest, "load_boards", lambda only=None: BOARDS)
    jobs = [raw(1, title="Account Executive"), raw(2, title="Machine Learning Intern")]
    ingest.run(1, embedder=FakeEmbedder(), fetcher=FakeSource({"acme": jobs, "beta": []}))
    r = db.execute("SELECT title FROM jobs WHERE embedded_at IS NOT NULL").fetchall()
    assert [x["title"] for x in r] == ["Machine Learning Intern"]
