"""Anonymous sessions + server-side memory (profile, learning plan, agent messages) + forget-me.

The browser keeps a random token; the server stores only sha256(token). No accounts, no emails, no resumes.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from app.api.limits import limiter
from app.api.rank import ProfileIn
from app.db import conn as db_conn
from app.ingest.skills import load_taxonomy
from app.llm.client import get_embedder
from app.rank.service import clear_pools, normalise
from app.search.hybrid import cache_key, clear_query_lru

router = APIRouter(prefix="/api")
SESSION_TTL_DAYS = 30
MAX_PLAN_ITEMS = 20
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32,64}$")


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def session_key(x_session_token: str | None = Header(None)) -> str:
    """Dependency: the hashed session id, or 401. Does not check existence (routes do, to return 404)."""
    if not x_session_token or not _TOKEN_RE.match(x_session_token):
        raise HTTPException(401, "missing or malformed session token")
    return hash_token(x_session_token)


def load_session(c, sid: str) -> dict:
    row = c.execute("UPDATE profiles SET updated_at = now() WHERE session_id = %s RETURNING facts", (sid,)).fetchone()
    if row is None:
        raise HTTPException(404, "session not found (expired or forgotten)")
    return row["facts"] or {}


def prune_expired(c) -> int:
    return c.execute(
        "DELETE FROM profiles WHERE updated_at < now() - make_interval(days => %s)", (SESSION_TTL_DAYS,)
    ).rowcount


def plan_rows(c, sid: str) -> list[dict]:
    rows = c.execute(
        "SELECT id, skill, status, source, added_at FROM plan_items WHERE session_id = %s ORDER BY id", (sid,)
    ).fetchall()
    return [{**r, "added_at": r["added_at"].isoformat()} for r in rows]


@router.post("/session", status_code=201)
@limiter.limit("10/minute")
def create_session(request: Request):
    token = secrets.token_urlsafe(32)
    with db_conn() as c:
        c.execute("INSERT INTO profiles (session_id) VALUES (%s)", (hash_token(token),))
    return {"token": token, "ttl_days": SESSION_TTL_DAYS}


@router.get("/me")
@limiter.limit("60/minute")
def me(request: Request, sid: str = Depends(session_key)):
    with db_conn() as c:
        facts = load_session(c, sid)
        msgs = c.execute(
            """SELECT role, content, created_at FROM
                 (SELECT * FROM messages WHERE session_id = %s ORDER BY id DESC LIMIT 20) m ORDER BY created_at""",
            (sid,),
        ).fetchall()
        plan = plan_rows(c, sid)
    return {
        "profile": facts.get("profile"),
        "plan": plan,
        "messages": [{"role": m["role"], "content": m["content"], "at": m["created_at"].isoformat()} for m in msgs],
    }


@router.put("/me/profile")
@limiter.limit("30/minute")
def save_profile(body: ProfileIn, request: Request, sid: str = Depends(session_key)):
    with db_conn() as c:
        load_session(c, sid)
        c.execute(
            "UPDATE profiles SET facts = jsonb_set(facts, '{profile}', %s) WHERE session_id = %s",
            (Jsonb(body.model_dump()), sid),
        )
    return {"ok": True}


class PlanAdd(BaseModel):
    skill: str = Field(..., min_length=1, max_length=60)
    source: Literal["user", "agent", "why_not_me"] = "user"


class PlanPatch(BaseModel):
    status: Literal["todo", "learning", "done"]


@router.post("/me/plan", status_code=201)
@limiter.limit("30/minute")
def add_plan(body: PlanAdd, request: Request, sid: str = Depends(session_key)):
    skill = load_taxonomy().canonical(body.skill)
    if skill is None:
        raise HTTPException(422, "unknown skill (not in the taxonomy)")
    with db_conn() as c:
        load_session(c, sid)
        n = c.execute("SELECT count(*) AS n FROM plan_items WHERE session_id = %s", (sid,)).fetchone()["n"]
        if n >= MAX_PLAN_ITEMS:
            raise HTTPException(422, f"a plan holds at most {MAX_PLAN_ITEMS} skills")
        c.execute(
            "INSERT INTO plan_items (session_id, skill, source) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            (sid, skill, body.source),
        )
        return {"plan": plan_rows(c, sid)}


@router.patch("/me/plan/{item_id}")
@limiter.limit("60/minute")
def patch_plan(item_id: int, body: PlanPatch, request: Request, sid: str = Depends(session_key)):
    """Marking a skill done also adds it to the saved profile, so the next ranking counts it."""
    with db_conn() as c:
        facts = load_session(c, sid)
        row = c.execute(
            "UPDATE plan_items SET status = %s WHERE id = %s AND session_id = %s RETURNING skill",
            (body.status, item_id, sid),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "plan item not found")
        prof = facts.get("profile")
        added = False
        if body.status == "done" and prof is not None and row["skill"] not in prof.get("skills", []):
            prof["skills"] = [*prof.get("skills", []), row["skill"]][:50]
            c.execute(
                "UPDATE profiles SET facts = jsonb_set(facts, '{profile}', %s) WHERE session_id = %s",
                (Jsonb(prof), sid),
            )
            added = True
        return {"plan": plan_rows(c, sid), "added_to_profile": added, "profile": prof}


@router.delete("/me/plan/{item_id}")
@limiter.limit("60/minute")
def delete_plan(item_id: int, request: Request, sid: str = Depends(session_key)):
    with db_conn() as c:
        load_session(c, sid)
        c.execute("DELETE FROM plan_items WHERE id = %s AND session_id = %s", (item_id, sid))
        return {"plan": plan_rows(c, sid)}


@router.delete("/me")
@limiter.limit("10/minute")
def forget_me(request: Request, sid: str = Depends(session_key)):
    """Delete everything tied to this session: profile, plan, messages (cascade) and the cached profile embedding."""
    with db_conn() as c:
        row = c.execute("DELETE FROM profiles WHERE session_id = %s RETURNING facts", (sid,)).fetchone()
        if row is None:
            return {"deleted": False}
        prof = (row["facts"] or {}).get("profile")
        emb = request.app.state.embedder or get_embedder()
        if prof and emb is not None:
            text = normalise(ProfileIn(**prof).to_input()).query_text
            c.execute("DELETE FROM embedding_cache WHERE key = %s", (cache_key(emb, "RETRIEVAL_QUERY", text),))
    clear_query_lru()  # in-memory copies may hold this profile's vector/text; dropping them is cheap
    clear_pools()
    return {"deleted": True}
