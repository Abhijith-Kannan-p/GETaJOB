import pytest

from app.config import Settings, get_settings
from app.ingest.run import JobRecord, content_hash, select_capped
from app.llm import quota
from app.llm.client import FakeEmbedder


def _rec(i, prio):
    return JobRecord("greenhouse", str(i), "b", "Co", f"t{i}", "", False, "unknown", "", [], [], "u", None, "h", prio)


def test_content_hash_tracks_source_text_only():
    assert content_hash("a", "b", "c") == content_hash("a", "b", "c")
    assert content_hash("a", "b", "c") != content_hash("a", "b", "c!")


def test_cap_keeps_highest_priority():
    kept = select_capped([_rec(i, i % 5) for i in range(20)], 4)
    assert len(kept) == 4 and all(r.priority == 4 for r in kept)


def test_fake_embedder_deterministic_and_normalised():
    e = FakeEmbedder(64)
    a, b = e.embed(["python backend"]), e.embed(["python backend"])
    assert a == b
    assert abs(sum(x * x for x in a[0]) - 1) < 1e-9


def test_minute_window_fails_fast(monkeypatch):
    quota.reset_for_tests()
    monkeypatch.setenv("LLM_RPM", "2")
    get_settings.cache_clear()
    try:
        quota.acquire_minute("llm")
        quota.acquire_minute("llm")
        with pytest.raises(quota.QuotaExceeded):
            quota.acquire_minute("llm")
    finally:
        get_settings.cache_clear()
        quota.reset_for_tests()


def test_unset_limit_uses_conservative_default(caplog):
    import app.config

    app.config._warned.clear()
    s = Settings(llm_rpm=None, llm_rpd=None)
    assert s.limit("llm_rpm") == 5 and s.limit("llm_rpd") == 100
    assert "conservative default" in caplog.text


def test_cors_origins_are_normalised():
    s = Settings(cors_origins=' "https://a.vercel.app/" , http://localhost:3000/,, ')
    assert s.cors_origin_list == ["https://a.vercel.app", "http://localhost:3000"]


def test_cors_regex_allows_deployment_urls():
    import re

    rx = r"^https://frontend-eight-sigma(-[a-z0-9]+)*\.vercel\.app$"
    assert re.match(rx, "https://frontend-eight-sigma-mj8ezgzzva.vercel.app")
    assert re.match(rx, "https://frontend-eight-sigma.vercel.app")
    assert not re.match(rx, "https://frontend-eight-sigma.vercel.app.evil.com")
    assert not re.match(rx, "https://evil-frontend-eight-sigma.vercel.app")
