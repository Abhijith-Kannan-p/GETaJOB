"""Central settings. Everything provider-specific comes from env; nothing is hardcoded.

Why: free-tier limits and model IDs change, and the same image runs locally, in CI and on Render.
If a limit is unset we fall back to deliberately tiny values so a misconfigured deploy can't burn quota.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger("getajob.config")

BACKEND_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BACKEND_DIR / "data"

CONSERVATIVE_RPM = 5
CONSERVATIVE_RPD = 100
DEFAULT_EMBED_DIM = 768
DEFAULT_EMBED_TPM = 24_000
_warned: set[str] = set()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    database_url: str = "postgresql://getajob:getajob@localhost:5432/getajob"

    gemini_api_key: str | None = None
    llm_model: str | None = None
    embed_model: str | None = None
    embed_dim: int | None = None
    llm_rpm: int | None = None
    llm_rpd: int | None = None
    embed_rpm: int | None = None
    embed_rpd: int | None = None
    # tokens per minute for embeddings (~80% of the 30K free limit); estimated as chars/4
    embed_tpm: int | None = None
    # daily embedding requests kept back for live search/profile embeddings; ingest may use RPD - reserve
    embed_reserve_rpd: int = 250

    mock_llm: bool = False
    cors_origins: str = "http://localhost:3000"
    log_level: str = "INFO"

    # ingest: start at ~1,000 active jobs; embeddings fill in over nightly batches within the ingest budget
    max_active_jobs: int = 1000
    http_sleep_s: float = 1.0
    user_agent: str = "GETaJOB-portfolio-indexer/0.1 (+https://github.com/Abhijith-Kannan-p/GETaJOB)"

    @property
    def has_llm(self) -> bool:
        return bool(self.gemini_api_key and self.llm_model) and not self.mock_llm

    @property
    def has_embedder(self) -> bool:
        return bool(self.gemini_api_key and self.embed_model) and not self.mock_llm

    def limit(self, name: str) -> int:
        """RPM/RPD with a conservative fallback + warning when unset."""
        val = getattr(self, name, None)
        if val is None or val <= 0:
            fallback = {"rpm": CONSERVATIVE_RPM, "rpd": CONSERVATIVE_RPD, "tpm": DEFAULT_EMBED_TPM}[name[-3:]]
            if name not in _warned:  # once per process, not on every call
                _warned.add(name)
                log.warning("%s unset; using conservative default %s", name.upper(), fallback)
            return fallback
        return val

    def ingest_embed_budget(self) -> int:
        """Daily embedding requests ingestion may reach (the rest is reserved for live queries)."""
        return max(0, self.limit("embed_rpd") - self.embed_reserve_rpd)

    @property
    def dim(self) -> int:
        if not self.embed_dim:
            log.warning("EMBED_DIM unset; using %s", DEFAULT_EMBED_DIM)
            return DEFAULT_EMBED_DIM
        return self.embed_dim


@lru_cache
def get_settings() -> Settings:
    return Settings()
