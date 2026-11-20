"""POST /api/agent/run: runs the agent for the caller's saved profile and streams its trace as Server-Sent Events.

Limits, checked before any stream starts (so refusals are plain JSON errors):
- per IP: 4 runs / minute (slowapi);
- per session: SESSION_MAX_RUNS per Pacific day;
- global: DAILY_MAX_RUNS LLM runs per day (= 70% of LLM_RPD / 4 calls). Past it, runs still work offline;
- concurrency: 2 runs at once (one uvicorn worker, a pool of 4 DB connections).
"""

from __future__ import annotations

import functools
import itertools
import json
import logging
import queue
import re
import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from app.agent.run import run_agent
from app.agent.tools import AgentContext
from app.api.limits import limiter
from app.api.rank import ProfileIn
from app.api.session import load_session, session_key
from app.config import DATA_DIR, get_settings
from app.db import conn as db_conn
from app.llm import quota

router = APIRouter(prefix="/api")
log = logging.getLogger("getajob.api.agent")

MAX_CONCURRENT_RUNS = 2
RUN_TIMEOUT_S = 90
HEARTBEAT_S = 10
RUN_SLOT_TTL_S = 180  # a slot whose stream never started (client left early) frees itself after this


class _Slots:
    """Concurrency slots that cannot leak: entries expire even if a stream is never consumed."""

    def __init__(self, n: int):
        self.n, self.lock, self.active = n, threading.Lock(), {}

    def acquire(self) -> int | None:
        with self.lock:
            now = time.monotonic()
            self.active = {k: t for k, t in self.active.items() if now - t < RUN_SLOT_TTL_S}
            if len(self.active) >= self.n:
                return None
            key = next(_ids)
            self.active[key] = now
            return key

    def release(self, key: int) -> None:
        with self.lock:
            self.active.pop(key, None)


_ids = itertools.count(1)
_slots = _Slots(MAX_CONCURRENT_RUNS)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(?:\+?\d[\s().-]?){9,}")
_TODAY = "(created_at AT TIME ZONE 'America/Los_Angeles')::date = " + quota.QUOTA_DAY


class AgentIn(BaseModel):
    message: str = Field("", max_length=300)
    job_id: int | None = None


def count_event(c, name: str) -> None:
    """Anonymous daily counters (no session id): e.g. how often answers pass grounding on the first try."""
    c.execute(
        "INSERT INTO events (day, name, count) VALUES (" + quota.QUOTA_DAY + ", %s, 1) "
        "ON CONFLICT (day, name) DO UPDATE SET count = events.count + 1",
        (name,),
    )


def looks_personal(text: str) -> bool:
    return bool(_EMAIL.search(text) or _PHONE.search(text))


def _sse(ev: dict) -> str:
    return f"event: {ev['type']}\ndata: {json.dumps(ev, default=str)}\n\n"


def _history(c, sid: str) -> list[dict]:
    rows = c.execute(
        "SELECT role, content FROM messages WHERE session_id = %s ORDER BY id DESC LIMIT 4", (sid,)
    ).fetchall()
    out = []
    for r in reversed(rows):
        content = r["content"] or {}
        if r["role"] == "user":
            text = content.get("text") or ""
        else:
            text = ((content.get("narrative") or {}).get("headline") or {}).get("text") or ""
        if text:
            out.append({"role": r["role"], "text": text})
    return out


RECORDED_RUNS = DATA_DIR / "recorded_runs.json"


@functools.lru_cache(maxsize=1)
def _recorded() -> tuple[dict, ...]:
    if not RECORDED_RUNS.exists():
        return ()
    try:
        return tuple(json.loads(RECORDED_RUNS.read_text(encoding="utf-8")))
    except ValueError:
        log.warning("recorded runs unreadable")
        return ()


@router.get("/agent/recorded")
def recorded_runs():
    """Real past Gemini runs for SAMPLE profiles (made by `python -m evals.recorded`), replayed unchanged when
    the live model is unavailable. They are offered next to the user's rule-based answer, never instead of it."""
    return {"runs": list(_recorded())}


@router.post("/agent/run")
@limiter.limit("4/minute")
def agent_run(body: AgentIn, request: Request, sid: str = Depends(session_key)):
    message = " ".join(body.message.split())
    if looks_personal(message):
        raise HTTPException(422, "That looks like an email address or phone number. Please leave personal details out.")
    s = get_settings()
    slot = _slots.acquire()
    if slot is None:
        raise HTTPException(503, "The assistant is busy with other requests. Try again in a few seconds.")
    try:
        with db_conn() as c:
            facts = load_session(c, sid)
            prof = facts.get("profile")
            if not prof or not (prof.get("skills") or prof.get("target_roles")):
                raise HTTPException(422, "Add some skills or a target role to your profile first.")
            n = c.execute(
                f"SELECT count(*) AS n FROM messages WHERE session_id = %s AND role = 'user' AND {_TODAY}", (sid,)
            ).fetchone()["n"]
            if n >= s.session_max_runs:
                raise HTTPException(
                    429, f"You've used today's {s.session_max_runs} assistant runs. Try again tomorrow."
                )
            history = _history(c, sid)
            use_llm, cap_notice = request.app.state.llm_factory is not None, None
            if use_llm:
                try:
                    quota.count_day(c, "agent_run", 1)
                except quota.QuotaExceeded:
                    use_llm = False
                    cap_notice = "Today's AI runs are used up; the assistant is running in rule-based mode."
            c.execute(
                "INSERT INTO messages (session_id, role, content) VALUES (%s, 'user', %s)",
                (sid, Jsonb({"text": message, "job_id": body.job_id})),
            )
    except BaseException:
        _slots.release(slot)
        raise

    profile = ProfileIn(**prof)
    factory, embedder = request.app.state.llm_factory, request.app.state.embedder

    def work(out: queue.Queue) -> None:
        """Runs in its own thread so the response can send heartbeats and give up at RUN_TIMEOUT_S."""
        try:
            with db_conn() as c:
                llm = factory(c) if use_llm else None
                ctx = AgentContext(c, embedder, profile, sid=sid)
                trace, final = [], None
                for ev in run_agent(ctx, message, llm, history, body.job_id):
                    if ev["type"] == "tool_call":
                        trace.append({"name": ev["name"], "args": ev["args"], "by": ev["by"]})
                    elif ev["type"] == "narrative":
                        final = ev
                    out.put(ev)
                if final is not None:
                    content = {k: final[k] for k in ("narrative", "facts", "source", "grounding")}
                    c.execute(
                        "INSERT INTO messages (session_id, role, content) VALUES (%s, 'assistant', %s)",
                        (sid, Jsonb({**content, "trace": trace})),
                    )
                    count_event(c, f"grounding_{final['grounding']['outcome']}")
        except Exception:  # noqa: BLE001 - the stream has started; report in-band, details stay in logs
            log.exception("agent run failed")
            out.put({"type": "error", "message": "Something went wrong during this run. Please try again."})
        finally:
            _slots.release(slot)
            out.put(None)

    def stream():
        if cap_notice:
            yield _sse({"type": "notice", "message": cap_notice})
        out: queue.Queue = queue.Queue()
        threading.Thread(target=work, args=(out,), daemon=True, name="agent-run").start()
        deadline = time.monotonic() + RUN_TIMEOUT_S
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                log.warning("agent run timed out", extra={"timeout_s": RUN_TIMEOUT_S})
                yield _sse({"type": "error", "message": "This run took too long and was stopped. Please try again."})
                return
            try:
                ev = out.get(timeout=min(HEARTBEAT_S, left))
            except queue.Empty:
                yield ": keep-alive\n\n"  # SSE comment: keeps proxies from closing an idle stream
                continue
            if ev is None:
                return
            yield _sse(ev)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
