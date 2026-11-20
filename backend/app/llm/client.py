"""The ONE wrapper every Gemini call goes through (embeddings and generation).

Guarantees: 30 s timeout, at most 2 retries on transient errors (5xx / network) with backoff,
no retry on 429 (quota is a state, not a glitch), quota guard before every call,
and a structured log line with tokens / retries / latency.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
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
    kind: str,
    fn,
    *,
    units: int = 1,
    tokens: int = 0,
    conn=None,
    wait: bool = False,
    day_ceiling: int | None = None,
    max_wait: float = 65,
):
    """Run fn() under quota guard + retry policy. Returns (result, retries, ms).

    Quota errors (ours or the provider's 429) are raised immediately, never retried.
    """
    from google.genai import errors

    if quota.provider_exhausted(kind):
        raise quota.QuotaExceeded(kind, "provider")
    quota.acquire_minute(kind, units, tokens=tokens, wait=wait, max_wait=max_wait)
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


# ---------------------------------------------------------------- generation (agent) ----------------------------
# A provider-neutral surface so the agent loop and its tests never touch google-genai types.


class BadOutput(Exception):
    """The model answered, but not in the shape we asked for. Not retried (a retry costs quota)."""


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict  # JSON Schema (object)


@dataclass
class ToolCall:
    name: str
    args: dict


@dataclass
class Reply:
    text: str
    calls: list[ToolCall] = field(default_factory=list)


class Chat(Protocol):
    def send(self, text: str | None = None, results: list[tuple[str, dict]] | None = None) -> Reply: ...


class LLM(Protocol):
    def chat(self, system: str, tools: list[ToolSpec]) -> Chat: ...

    def structured(self, system: str, prompt: str, schema: dict) -> dict: ...


GEN_MAX_WAIT_S = 20  # inside a run, wait briefly for the per-minute window instead of failing the run halfway


def _usage(resp) -> dict:
    u = getattr(resp, "usage_metadata", None)
    if u is None:
        return {}
    return {
        "prompt_tokens": u.prompt_token_count,
        "output_tokens": u.candidates_token_count,
        "thought_tokens": u.thoughts_token_count,
    }


class GeminiLLM:
    """Gemini generation through call_with_retries: quota guard, 30 s timeout, 2 transient retries, token log."""

    def __init__(self, conn=None):
        s = get_settings()
        if not s.has_llm:
            raise ProviderUnavailable("LLM not configured (GEMINI_API_KEY / LLM_MODEL / MOCK_LLM)")
        self.model = s.llm_model
        self.conn = conn
        self.calls = 0

    def _generate(self, contents, config, purpose: str):
        client = _genai()
        tokens = quota.estimate_tokens([str(contents)])

        def go():
            return client.models.generate_content(model=self.model, contents=contents, config=config)

        resp, retries, ms = call_with_retries(
            "llm", go, tokens=tokens, conn=self.conn, wait=True, max_wait=GEN_MAX_WAIT_S
        )
        self.calls += 1
        log.info(
            "llm_call",
            extra={
                "kind": "llm",
                "model": self.model,
                "purpose": purpose,
                "retries": retries,
                "ms": round(ms),
                **_usage(resp),
            },
        )
        if not resp.candidates or resp.candidates[0].content is None:
            raise BadOutput("empty response (blocked or no candidate)")
        return resp

    def chat(self, system: str, tools: list[ToolSpec]) -> GeminiChat:
        return GeminiChat(self, system, tools)

    def structured(self, system: str, prompt: str, schema: dict) -> dict:
        import json

        from google.genai import types

        cfg = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_json_schema=schema,
            temperature=0.2,
            max_output_tokens=1500,
        )
        resp = self._generate(prompt, cfg, "narrative")
        try:
            out = json.loads(resp.text or "")
        except ValueError:
            raise BadOutput("narrative was not valid JSON") from None
        if not isinstance(out, dict):
            raise BadOutput("narrative was not a JSON object")
        return out


class GeminiChat:
    """Keeps the provider's own Content objects (including thought signatures) and replays them each turn."""

    def __init__(self, llm: GeminiLLM, system: str, tools: list[ToolSpec]):
        from google.genai import types

        self.llm = llm
        self.contents: list = []
        self.config = types.GenerateContentConfig(
            system_instruction=system,
            tools=[
                types.Tool(
                    function_declarations=[
                        types.FunctionDeclaration(
                            name=t.name, description=t.description, parameters_json_schema=t.parameters
                        )
                        for t in tools
                    ]
                )
            ],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            temperature=0.2,
            max_output_tokens=800,
        )

    def send(self, text=None, results=None) -> Reply:
        from google.genai import types

        parts = []
        if text:
            parts.append(types.Part.from_text(text=text))
        for name, res in results or []:
            parts.append(types.Part.from_function_response(name=name, response=res))
        self.contents.append(types.Content(role="user", parts=parts))
        resp = self.llm._generate(self.contents, self.config, "tools")
        content = resp.candidates[0].content
        self.contents.append(content)
        calls, texts = [], []
        for p in content.parts or []:
            if p.function_call is not None:
                calls.append(ToolCall(p.function_call.name or "", dict(p.function_call.args or {})))
            elif p.text and not p.thought:
                texts.append(p.text)
        return Reply("".join(texts), calls)


class FakeLLM:
    """Scripted, offline LLM for tests and recorded demos: replies are consumed in order."""

    def __init__(self, replies: list[Reply] | None = None, narrative: dict | Exception | None = None):
        self.replies = list(replies or [])
        self.narrative = narrative
        self.calls = 0
        self.prompts: list[str] = []

    def chat(self, system, tools):
        fake = self

        class _C:
            def send(self, text=None, results=None):
                fake.calls += 1
                fake.prompts.append(text or str(results))
                if isinstance(fake.replies[0] if fake.replies else None, Exception):
                    raise fake.replies.pop(0)
                return fake.replies.pop(0) if fake.replies else Reply("done")

        return _C()

    def structured(self, system, prompt, schema):
        self.calls += 1
        self.prompts.append(prompt)
        if isinstance(self.narrative, Exception):
            raise self.narrative
        if self.narrative is None:
            raise BadOutput("no scripted narrative")
        return self.narrative(prompt) if callable(self.narrative) else self.narrative


def get_llm(conn=None) -> LLM | None:
    try:
        return GeminiLLM(conn)
    except ProviderUnavailable:
        return None
