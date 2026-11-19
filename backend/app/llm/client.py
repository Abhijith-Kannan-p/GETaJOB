"""The ONE wrapper every Gemini call goes through (embeddings now, generation in slice 4).

Guarantees: 30 s timeout, at most 2 retries on transient errors (5xx / network) with backoff,
no retry on 429 (quota is a state, not a glitch), quota guard before every call,
and a structured log line with tokens / retries / latency.
"""

from __future__ import annotations

import logging
import math
import time
from typing import Protocol

from app.config import get_settings
from app.llm import quota

log = logging.getLogger("getajob.llm")

TIMEOUT_MS = 30_000
MAX_RETRIES = 2


class ProviderUnavailable(Exception):
    """No key / mock mode / provider down after retries. Callers degrade gracefully."""


class Embedder(Protocol):
    dim: int

    def embed(
        self, texts: list[str], *, task: str, conn=None, wait: bool = False, purpose: str = "query"
    ) -> list[list[float]]: ...


def _normalise(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


_client = None


def _genai():
    global _client
    s = get_settings()
    if not s.gemini_api_key:
        raise ProviderUnavailable("no GEMINI_API_KEY")
    if _client is None:
        from google import genai
        from google.genai import types

        _client = genai.Client(api_key=s.gemini_api_key, http_options=types.HttpOptions(timeout=TIMEOUT_MS))
    return _client


def call_with_retries(
    kind: str, fn, *, units: int = 1, tokens: int = 0, conn=None, wait: bool = False, day_ceiling: int | None = None
):
    """Run fn() under quota guard + retry policy. Returns (result, retries, ms).

    Quota errors (ours or the provider's 429) are raised immediately, never retried.
    """
    from google.genai import errors

    if quota.provider_exhausted(kind):
        raise quota.QuotaExceeded(kind, "provider")
    quota.acquire_minute(kind, units, tokens=tokens, wait=wait)
    if conn is not None:
        quota.count_day(conn, kind, units, ceiling=day_ceiling)
    last: Exception | None = None
    for attempt in range(MAX_RETRIES + 1):
        t0 = time.perf_counter()
        try:
            res = fn()
            return res, attempt, (time.perf_counter() - t0) * 1000
        except errors.APIError as e:
            if e.code == 429:
                quota.mark_provider_exhausted(kind)
                raise quota.QuotaExceeded(kind, "provider") from None
            if e.code and e.code < 500:
                log.error("llm %s client error %s", kind, e.code)
                raise ProviderUnavailable(f"{kind} client error {e.code}") from None
            last = e
        except Exception as e:  # noqa: BLE001 - timeouts / network
            last = e
        if attempt < MAX_RETRIES:
            time.sleep(1.5 * (2**attempt))
    log.error("llm %s failed after retries: %s", kind, type(last).__name__)
    raise ProviderUnavailable(f"{kind} failed after retries")


class GeminiEmbedder:
    def __init__(self):
        s = get_settings()
        if not s.has_embedder:
            raise ProviderUnavailable("embedder not configured (GEMINI_API_KEY / EMBED_MODEL / MOCK_LLM)")
        self.model = s.embed_model
        self.dim = s.dim

    def embed(self, texts, *, task="RETRIEVAL_DOCUMENT", conn=None, wait=False, purpose="query"):
        from google.genai import types

        client = _genai()
        cfg = types.EmbedContentConfig(task_type=task, output_dimensionality=self.dim)
        tokens = quota.estimate_tokens(texts)
        ceiling = get_settings().ingest_embed_budget() if purpose == "ingest" else None

        def go():
            return client.models.embed_content(model=self.model, contents=texts, config=cfg)

        res, retries, ms = call_with_retries(
            "embed", go, units=len(texts), tokens=tokens, conn=conn, wait=wait, day_ceiling=ceiling
        )
        vecs = [_normalise(list(e.values)) for e in res.embeddings]
        log.info(
            "llm_call",
            extra={
                "kind": "embed",
                "model": self.model,
                "purpose": purpose,
                "n": len(texts),
                "est_tokens": tokens,
                "retries": retries,
                "ms": round(ms),
            },
        )
        return vecs


class FakeEmbedder:
    """Deterministic, offline. Bag-of-hashed-words so similar texts get similar vectors (tests + MOCK_LLM)."""

    def __init__(self, dim: int | None = None):
        self.dim = dim or get_settings().dim

    def embed(self, texts, *, task="RETRIEVAL_DOCUMENT", conn=None, wait=False, purpose="query"):
        import hashlib
        import re

        out = []
        for t in texts:
            v = [0.0] * self.dim
            for w in re.findall(r"[a-z0-9+#]+", t.lower()):
                h = int(hashlib.md5(w.encode()).hexdigest(), 16)
                v[h % self.dim] += 1.0 if (h >> 64) & 1 else -1.0
            out.append(_normalise(v) if any(v) else [1.0 / math.sqrt(self.dim)] * self.dim)
        return out


def get_embedder() -> Embedder | None:
    """Real embedder if configured, else None (callers fall back to full-text only)."""
    try:
        return GeminiEmbedder()
    except ProviderUnavailable:
        return None
