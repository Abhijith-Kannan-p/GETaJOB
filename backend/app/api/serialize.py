"""Shared response shapes."""

from __future__ import annotations


def job_out(r: dict) -> dict:
    out = {
        "id": r["id"],
        "title": r["title"],
        "company": r["company"],
        "location": r["location"],
        "is_remote": r["is_remote"],
        "seniority": r["seniority"],
        "required_skills": r["required_skills"],
        "preferred_skills": r["preferred_skills"],
        "url": r["url"],
        "posted_at": r["posted_at"].isoformat() if r.get("posted_at") else None,
        "source": r["source"],
        "attribution": f"via {'Greenhouse' if r['source'] == 'greenhouse' else 'Lever'} job board API",
    }
    if "rrf" in r:
        out["snippet"] = r["snippet"]
        out["retrieval"] = {
            "rrf": round(float(r["rrf"]), 5),
            "semantic_sim": None if r["sim"] is None else round(float(r["sim"]), 4),
            "keyword": None if r["kw"] is None else round(float(r["kw"]), 4),
            "vec_rank": r["vec_rank"],
            "fts_rank": r["fts_rank"],
        }
    return out
