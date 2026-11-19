"""Quota guard: per-minute (in-process sliding window) + per-day (Postgres, atomic, fail-closed).

Why fail-closed: on a free tier, "the DB was unreachable so we let it through" is how you burn
a day's quota in a retry storm. If we can't prove we're under the limit, we say no.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque

from app.config import get_settings

log = logging.getLogger("getajob.quota")


class QuotaExceeded(Exception):
    def __init__(self, kind: str, scope: str):
        super().__init__(f"{kind} quota exceeded ({scope})")
        self.kind = kind
        self.scope = scope


class _Window:
    def __init__(self):
        self.calls: deque[tuple[float, int, int]] = deque()  # (time, requests, tokens)
        self.lock = threading.Lock()


def estimate_tokens(texts: list[str]) -> int:
    """Cheap, provider-agnostic estimate (chars/4). Conservative enough for TPM pacing."""
    return sum(len(t) // 4 + 1 for t in texts)


_windows: dict[str, _Window] = {}
_exhausted_until: dict[str, float] = {}  # set when the provider itself returns 429


def _limits(kind: str) -> tuple[int, int]:
    s = get_settings()
    return s.limit(f"{kind}_rpm"), s.limit(f"{kind}_rpd")


def mark_provider_exhausted(kind: str, seconds: float = 600) -> None:
    """Provider said 429: stop calling for a while instead of retrying into the wall."""
    _exhausted_until[kind] = time.monotonic() + seconds
    log.warning("provider quota hit for %s; pausing %ss", kind, seconds)


def provider_exhausted(kind: str) -> bool:
    return time.monotonic() < _exhausted_until.get(kind, 0)


def acquire_minute(kind: str, n: int = 1, tokens: int = 0, wait: bool = False, max_wait: float = 65) -> None:
    """Sliding 60 s window on requests AND (for embeddings) estimated tokens.

    wait=True (batch ingest) sleeps until the window frees, bounded by max_wait; wait=False (live API) fails fast.
    A single oversized call is let through only into an empty window, so it can never stack on others.
    """
    rpm, _ = _limits(kind)
    tpm = get_settings().limit(f"{kind}_tpm") if kind == "embed" else None
    w = _windows.setdefault(kind, _Window())
    deadline = time.monotonic() + max_wait
    while True:
        with w.lock:
            now = time.monotonic()
            while w.calls and now - w.calls[0][0] >= 60:
                w.calls.popleft()
            used_r = sum(c[1] for c in w.calls)
            used_t = sum(c[2] for c in w.calls)
            fits = used_r + n <= rpm and (tpm is None or used_t + tokens <= tpm)
            if fits or not w.calls:
                w.calls.append((now, n, tokens))
                return
            sleep_for = 60 - (now - w.calls[0][0]) + 0.05
        if not wait or time.monotonic() + sleep_for > deadline:
            raise QuotaExceeded(kind, "minute")
        time.sleep(sleep_for)


def count_day(conn, kind: str, n: int = 1, ceiling: int | None = None) -> int:
    """Atomically add n to today's counter iff it stays within the ceiling (default RPD).

    Ingestion passes ceiling = RPD - reserve, live queries use the full RPD, both on the SAME counter,
    so the reserve is always left for queries no matter what ran first. Raises QuotaExceeded otherwise.
    """
    _, rpd = _limits(kind)
    rpd = min(rpd, ceiling) if ceiling is not None else rpd
    if n > rpd:  # the INSERT branch below has no WHERE, so reject oversize batches up front
        raise QuotaExceeded(kind, "day")
    try:
        row = conn.execute(
            """
            INSERT INTO usage_daily (day, kind, count) VALUES (current_date, %(k)s, %(n)s)
            ON CONFLICT (day, kind) DO UPDATE SET count = usage_daily.count + %(n)s
              WHERE usage_daily.count + %(n)s <= %(lim)s
            RETURNING count
            """,
            {"k": kind, "n": n, "lim": rpd},
        ).fetchone()
    except Exception:  # noqa: BLE001 - fail closed on any DB problem
        log.exception("usage_daily check failed; failing closed")
        raise QuotaExceeded(kind, "day") from None
    if row is None or (row["count"] if isinstance(row, dict) else row[0]) > rpd:
        raise QuotaExceeded(kind, "day")
    return row["count"] if isinstance(row, dict) else row[0]


def used_today(conn, kind: str) -> int:
    row = conn.execute("SELECT count FROM usage_daily WHERE day = current_date AND kind = %s", (kind,)).fetchone()
    return 0 if row is None else (row["count"] if isinstance(row, dict) else row[0])


def reset_for_tests() -> None:
    _windows.clear()
    _exhausted_until.clear()
