"""Ingestion: python -m app.ingest.run [--embed-limit N] [--no-embed] [--boards slug1,slug2]

fetch -> clean -> extract skills -> classify -> prioritise & cap -> upsert by (source, external_id)
-> close vanished -> embed new/changed (resumable, capped) -> ingest_runs row.

Idempotent: running twice with the same upstream data changes nothing but last_seen_at.
Safe on partial failure: a board that errors is NOT treated as "all its jobs vanished".
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime

import yaml

from app import logs
from app.config import DATA_DIR, get_settings
from app.db import conn as db_conn
from app.ingest import classify, clean, skills
from app.ingest.sources import RawJob, fetch_board, make_client
from app.llm import quota
from app.llm.client import Embedder, ProviderUnavailable, get_embedder

log = logging.getLogger("getajob.ingest")

EMBED_EXCERPT = 600  # chars of description that go into the embedding (and into fixtures)
EMBED_BATCH = 50


@dataclass
class JobRecord:
    source: str
    external_id: str
    board: str
    company: str
    title: str
    location: str
    is_remote: bool
    seniority: str
    description: str
    required_skills: list[str]
    preferred_skills: list[str]
    url: str
    posted_at: datetime | None
    content_hash: str
    priority: int = 0


@dataclass
class RunStats:
    fetched: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    closed: int = 0
    embedded: int = 0
    errors: list[str] = field(default_factory=list)


def content_hash(title: str, location: str, description: str) -> str:
    """Hash of the SOURCE text only. Taxonomy edits change skills without forcing a re-embed."""
    return hashlib.sha256(f"{title}\x1f{location}\x1f{description}".encode()).hexdigest()[:32]


def embed_text(title: str, company: str, location: str, description: str) -> str:
    return f"{title}\n{company} - {location}\n{description[:EMBED_EXCERPT]}"


def process(raw: RawJob, tax: skills.Taxonomy | None = None) -> JobRecord:
    secs = clean.sections(raw.html, raw.extra_sections)
    ex = skills.extract(secs, tax)
    kept = [s for s in secs if s[0] != "ignore"]  # drop benefits/EEO boilerplate from storage too
    desc = clean.plain_text(kept)
    remote = classify.is_remote(raw.title, raw.location, raw.workplace_type)
    level = classify.seniority(raw.title, desc)
    return JobRecord(
        source=raw.source,
        external_id=raw.external_id,
        board=raw.board,
        company=raw.company,
        title=raw.title,
        location=raw.location,
        is_remote=remote,
        seniority=level,
        description=desc,
        required_skills=ex.required,
        preferred_skills=ex.preferred,
        url=raw.url,
        posted_at=raw.posted_at,
        content_hash=content_hash(raw.title, raw.location, desc),
        priority=classify.priority(raw.title, raw.location, remote, level),
    )


def select_capped(records: list[JobRecord], cap: int) -> list[JobRecord]:
    """Keep the `cap` most relevant jobs: priority desc, then newest."""
    epoch = datetime.min.replace(tzinfo=None)
    return sorted(
        records,
        key=lambda r: (r.priority, (r.posted_at.replace(tzinfo=None) if r.posted_at else epoch)),
        reverse=True,
    )[:cap]


_UPSERT = """
INSERT INTO jobs (source, external_id, board, company, title, location, is_remote, seniority, description,
                  required_skills, preferred_skills, url, posted_at, content_hash, priority)
VALUES (%(source)s, %(external_id)s, %(board)s, %(company)s, %(title)s, %(location)s, %(is_remote)s,
        %(seniority)s, %(description)s, %(required_skills)s, %(preferred_skills)s, %(url)s, %(posted_at)s,
        %(content_hash)s, %(priority)s)
ON CONFLICT (source, external_id) DO UPDATE SET
    board = EXCLUDED.board, company = EXCLUDED.company, title = EXCLUDED.title, location = EXCLUDED.location,
    is_remote = EXCLUDED.is_remote, seniority = EXCLUDED.seniority, description = EXCLUDED.description,
    required_skills = EXCLUDED.required_skills, preferred_skills = EXCLUDED.preferred_skills,
    url = EXCLUDED.url, posted_at = EXCLUDED.posted_at, priority = EXCLUDED.priority,
    last_seen_at = now(), closed_at = NULL,
    embedding   = CASE WHEN jobs.content_hash = EXCLUDED.content_hash THEN jobs.embedding END,
    embedded_at = CASE WHEN jobs.content_hash = EXCLUDED.content_hash THEN jobs.embedded_at END,
    content_hash = EXCLUDED.content_hash
"""


def upsert(c, records: list[JobRecord], stats: RunStats) -> None:
    existing = {
        (r["source"], r["external_id"]): r["content_hash"]
        for r in c.execute("SELECT source, external_id, content_hash FROM jobs")
    }
    for r in records:
        old = existing.get((r.source, r.external_id))
        if old is None:
            stats.inserted += 1
        elif old != r.content_hash:
            stats.updated += 1
        else:
            stats.unchanged += 1
    with c.transaction(), c.cursor() as cur:
        cur.executemany(_UPSERT, [vars(r) for r in records])


def close_vanished(c, kept: list[JobRecord], ok_boards: set[tuple[str, str]], all_boards: set[tuple[str, str]]) -> int:
    """Close active jobs that (a) came from a board fetched OK this run but weren't kept, or
    (b) belong to a board no longer configured. Jobs from boards that ERRORED are left alone."""
    keep = {(r.source, r.external_id) for r in kept}
    active = c.execute("SELECT id, source, external_id, board FROM jobs WHERE closed_at IS NULL").fetchall()
    to_close = [
        a["id"]
        for a in active
        if (a["source"], a["external_id"]) not in keep
        and ((a["source"], a["board"]) in ok_boards or (a["source"], a["board"]) not in all_boards)
    ]
    if to_close:
        c.execute("UPDATE jobs SET closed_at = now() WHERE id = ANY(%s)", (to_close,))
    return len(to_close)


def embed_pending(c, embedder: Embedder, limit: int, stats: RunStats) -> None:
    """Embed active rows with embedded_at IS NULL, highest priority first, in batches, until `limit` or the
    ingest share of the daily quota (EMBED_RPD - EMBED_RESERVE_RPD) is used. Stops on the first quota error
    (no retry loop); the next run resumes where this one stopped."""
    remaining = limit
    while remaining > 0:
        rows = c.execute(
            """SELECT id, title, company, location, description FROM jobs
               WHERE embedded_at IS NULL AND closed_at IS NULL ORDER BY priority DESC, id LIMIT %s""",
            (min(EMBED_BATCH, remaining),),
        ).fetchall()
        if not rows:
            return
        texts = [embed_text(r["title"], r["company"], r["location"] or "", r["description"]) for r in rows]
        try:
            vecs = embedder.embed(texts, task="RETRIEVAL_DOCUMENT", conn=c, wait=True, purpose="ingest")
        except quota.QuotaExceeded as e:
            stats.errors.append(f"embedding stopped: {e.scope} quota; rerun later to resume")
            log.warning("embedding stopped on quota (%s); resumable", e.scope)
            return
        except ProviderUnavailable as e:
            stats.errors.append(f"embedding stopped: {e}")
            return
        with c.transaction(), c.cursor() as cur:
            cur.executemany(
                "UPDATE jobs SET embedding = %s, embedded_at = now() WHERE id = %s",
                [(v, r["id"]) for v, r in zip(vecs, rows, strict=True)],
            )
        stats.embedded += len(rows)
        remaining -= len(rows)
        log.info("embedded batch", extra={"n": len(rows), "total": stats.embedded})


def load_boards(only: set[str] | None = None) -> list[dict]:
    boards = yaml.safe_load((DATA_DIR / "companies.yaml").read_text(encoding="utf-8"))["boards"]
    return [b for b in boards if not only or b["slug"] in only]


def run(
    embed_limit: int,
    do_embed: bool = True,
    only: set[str] | None = None,
    embedder: Embedder | None = None,
    fetcher=None,
) -> RunStats:
    s = get_settings()
    stats = RunStats()
    tax = skills.load_taxonomy()
    boards = load_boards(only)
    all_boards = {(b["source"], b["slug"]) for b in load_boards()}
    ok_boards: set[tuple[str, str]] = set()
    records: list[JobRecord] = []
    fetch = fetcher or fetch_board
    with make_client() as client:
        for b in boards:
            try:
                raws = fetch(client, b["source"], b["slug"], b["company"])
            except Exception as e:  # noqa: BLE001 - one bad board must not kill the run
                stats.errors.append(f"{b['source']}:{b['slug']}: {type(e).__name__}")
                log.warning("board failed", extra={"board": b["slug"], "err": type(e).__name__})
                continue
            ok_boards.add((b["source"], b["slug"]))
            stats.fetched += len(raws)
            records.extend(process(r, tax) for r in raws if r.url and r.title)
    kept = select_capped(records, s.max_active_jobs)
    with db_conn() as c:
        run_id = c.execute("INSERT INTO ingest_runs (mode) VALUES ('sync') RETURNING id").fetchone()["id"]
        upsert(c, kept, stats)
        stats.closed = close_vanished(c, kept, ok_boards, all_boards)
        c.execute("DELETE FROM embedding_cache WHERE last_used_at < now() - interval '30 days'")
        if do_embed:
            emb = embedder or get_embedder()
            if emb is None:
                stats.errors.append("embedding skipped: no embedder configured")
            else:
                embed_pending(c, emb, embed_limit, stats)
        c.execute(
            """UPDATE ingest_runs SET finished_at = now(), fetched=%s, inserted=%s, updated=%s, unchanged=%s,
               closed=%s, embedded=%s, errors=%s::jsonb WHERE id=%s""",
            (
                stats.fetched,
                stats.inserted,
                stats.updated,
                stats.unchanged,
                stats.closed,
                stats.embedded,
                json.dumps(stats.errors),
                run_id,
            ),
        )
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--embed-limit",
        type=int,
        default=None,
        help="max rows to embed this run (default: the ingest budget, EMBED_RPD - EMBED_RESERVE_RPD)",
    )
    ap.add_argument("--no-embed", action="store_true")
    ap.add_argument("--boards", default="", help="comma-separated slugs (default: all in companies.yaml)")
    a = ap.parse_args()
    s = get_settings()
    logs.setup(s.log_level)
    limit = a.embed_limit if a.embed_limit is not None else s.ingest_embed_budget()
    only = {x.strip() for x in a.boards.split(",") if x.strip()} or None
    st = run(limit, not a.no_embed, only)
    print(
        f"fetched={st.fetched} inserted={st.inserted} updated={st.updated} unchanged={st.unchanged} "
        f"closed={st.closed} embedded={st.embedded} errors={st.errors}"
    )


if __name__ == "__main__":
    main()
