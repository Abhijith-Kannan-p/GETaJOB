"""Build the committed fixture snapshot (ONE-TIME, uses the real embedding API):

    python -m app.ingest.fixtures --n 150

Picks ~n real postings spread across companies (round-robin by priority), trims each to the fields
we're allowed to keep (description excerpt <= 600 chars), embeds exactly that excerpt text, and
appends to data/fixtures/jobs.jsonl. Resumable: rows already in the file are skipped.
Daily quota is counted in Postgres, so docker-compose db must be running.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict

from app import logs
from app.config import DATA_DIR, get_settings
from app.db import conn as db_conn
from app.ingest import skills
from app.ingest.run import EMBED_BATCH, EMBED_EXCERPT, embed_text, load_boards, process
from app.ingest.sources import fetch_board, make_client
from app.llm import quota
from app.llm.client import ProviderUnavailable, get_embedder

FIXTURES = DATA_DIR / "fixtures" / "jobs.jsonl"


def _round_robin(by_company: dict[str, list], n: int) -> list:
    out, i = [], 0
    queues = {k: sorted(v, key=lambda r: r.priority, reverse=True) for k, v in by_company.items()}
    while len(out) < n and any(queues.values()):
        for k in list(queues):
            if queues[k]:
                out.append(queues[k].pop(0))
                if len(out) >= n:
                    break
        i += 1
    return out


def to_fixture(r) -> dict:
    excerpt = r.description[:EMBED_EXCERPT]
    return {
        "source": r.source,
        "external_id": r.external_id,
        "board": r.board,
        "company": r.company,
        "title": r.title,
        "location": r.location,
        "is_remote": r.is_remote,
        "seniority": r.seniority,
        "description": excerpt,
        "required_skills": r.required_skills,
        "preferred_skills": r.preferred_skills,
        "url": r.url,
        "posted_at": r.posted_at.isoformat() if r.posted_at else None,
        "attribution": f"{r.company} via {'Greenhouse' if r.source == 'greenhouse' else 'Lever'} public job board API",
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=150)
    a = ap.parse_args()
    logs.setup(get_settings().log_level)
    emb = get_embedder()
    if emb is None:
        raise SystemExit("Needs GEMINI_API_KEY + EMBED_MODEL in backend/.env (and MOCK_LLM=0).")

    done = set()
    if FIXTURES.exists():
        for line in FIXTURES.read_text(encoding="utf-8").splitlines():
            if line.strip():
                d = json.loads(line)
                done.add((d["source"], d["external_id"]))
    tax = skills.load_taxonomy()
    by_company: dict[str, list] = defaultdict(list)
    with make_client() as client:
        for b in load_boards():
            try:
                raws = fetch_board(client, b["source"], b["slug"], b["company"])
            except Exception as e:  # noqa: BLE001
                print(f"skip {b['slug']}: {type(e).__name__}")
                continue
            for raw in raws:
                rec = process(raw, tax)
                if rec.priority >= 5:  # tech AND (junior or India/remote): our audience
                    by_company[rec.company].append(rec)
    picked = [r for r in _round_robin(by_company, a.n) if (r.source, r.external_id) not in done]
    print(f"already have {len(done)}, embedding {len(picked)} more with model={emb.model} dim={emb.dim}")

    FIXTURES.parent.mkdir(parents=True, exist_ok=True)
    with db_conn() as c, FIXTURES.open("a", encoding="utf-8", newline="\n") as fh:
        for i in range(0, len(picked), EMBED_BATCH):
            batch = [to_fixture(r) for r in picked[i : i + EMBED_BATCH]]
            texts = [embed_text(d["title"], d["company"], d["location"], d["description"]) for d in batch]
            try:
                vecs = emb.embed(texts, task="RETRIEVAL_DOCUMENT", conn=c, wait=True, purpose="ingest")
            except (quota.QuotaExceeded, ProviderUnavailable) as e:
                print(f"stopped cleanly: {e}. Re-run later to resume.")
                break
            for d, v in zip(batch, vecs, strict=True):
                d["embedding"] = [round(x, 6) for x in v]
                d["embed_model"] = emb.model
                fh.write(json.dumps(d, ensure_ascii=False) + "\n")
            fh.flush()
            print(f"  wrote {i + len(batch)}/{len(picked)}")


if __name__ == "__main__":
    main()
