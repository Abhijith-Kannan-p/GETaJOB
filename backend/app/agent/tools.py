"""The agent's tools: thin, READ-ONLY wrappers over the real ranker / Why-Not-Me engine / hybrid search.

Each tool writes short factual sentences into a FactBook and returns {"facts": [{"id", "text"}]} to the model.
The model only ever sees these facts (never raw job descriptions), and the narrative must cite them.
Numbers are formatted once, here, so the grounding check compares like with like.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.api.rank import ProfileIn
from app.ingest.skills import load_taxonomy
from app.llm.client import Embedder, ToolSpec
from app.rank.scorer import load_weights
from app.rank.service import build_pool, normalise
from app.search.hybrid import SearchFilters, embed_query, hybrid_search
from app.whynot.engine import Simulator, gap, greedy_plan, load_effort, skill_effects

MAX_RANK_LIMIT = 10
MAX_SEARCH = 5


class FactBook:
    def __init__(self):
        self.facts: dict[str, str] = {}
        self._by_text: dict[str, str] = {}

    def add(self, text: str) -> str:
        if text in self._by_text:
            return self._by_text[text]
        fid = f"F{len(self.facts) + 1}"
        self.facts[fid] = text
        self._by_text[text] = fid
        return fid

    def out(self, ids: list[str], **extra) -> dict:
        return {"facts": [{"id": i, "text": self.facts[i]} for i in ids], **extra}


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def _fmt(x: float) -> str:
    return f"{x:g}"


def _list(xs) -> str:
    return ", ".join(xs) if xs else "none"


def _job(card: dict) -> str:
    where = card.get("location") or "location not listed"
    remote = ", remote" if card.get("is_remote") else ""
    return f'job {card["id"]} "{card["title"]}" at {card["company"]} ({where}{remote})'


@dataclass
class AgentContext:
    conn: object
    embedder: Embedder | None
    profile: ProfileIn
    book: FactBook = field(default_factory=FactBook)
    data: dict = field(default_factory=dict)  # structured copies for the rule-based narrative
    degraded: str | None = None
    _sims: dict = field(default_factory=dict)

    def sim(self, include_job: int | None = None):
        if include_job not in self._sims:
            w = load_weights()
            np = normalise(self.profile.to_input())
            pool = build_pool(self.conn, self.embedder, np, w, include_job=include_job)
            self.degraded = pool.degraded
            self._sims[include_job] = (pool, Simulator(pool.features, np.profile, pool.ctx, w))
        return self._sims[include_job]

    def profile_fact(self) -> str:
        p = self.profile
        np = normalise(p.to_input())
        return self.book.add(
            f"Your profile: skills {_list(sorted(np.profile.skills))}; target roles {_list(p.target_roles)}; "
            f"levels {_list(p.seniority)}; remote {'ok' if p.remote_ok else 'not wanted'}; "
            f"locations {_list(p.locations)}."
        )


# ------------------------------------------------------------------ tools


def rank_jobs(ctx: AgentContext, limit: int = 5) -> dict:
    limit = max(1, min(int(limit or 5), MAX_RANK_LIMIT))
    pool, sim = ctx.sim()
    ids, rows = [], []
    for jid in sim.base_order[:limit]:
        j, card = sim.by_id[jid], pool.cards[jid]
        matched = [s for s in (*j.required, *j.preferred) if s in sim.profile.skills]
        missing = [s for s in j.required if s not in sim.profile.skills]
        ids.append(
            ctx.book.add(
                f"{_cap(_job(card))} ranks #{sim.base_rank[jid]} of {len(sim.pool)} for your profile "
                f"(score {sim.base_scores[jid]:.2f}). Skills you match: {_list(matched)}. "
                f"Required skills you lack: {_list(missing)}."
            )
        )
        rows.append(
            {
                "job_id": jid,
                "title": card["title"],
                "company": card["company"],
                "rank": sim.base_rank[jid],
                "pool_size": len(sim.pool),
                "fact": ids[-1],
            }
        )
    ctx.data.setdefault("rank_jobs", rows)
    return ctx.book.out(ids)


def _effect_fact(ctx: AgentContext, e, job_id: int | None) -> str:
    parts = [f"Adding {e.skill}:"]
    if job_id is not None and e.job_rank:
        b, a = e.job_rank
        parts.append(f"job {job_id} would move from #{b} to #{a} (up {b - a});")
    parts.append(
        f"{e.jobs_with_skill} jobs in your pool list {e.skill} and move up {_fmt(round(e.mean_rank_change, 1))} "
        f"places on average; {e.entering_top20} would enter your top 20; effort about {e.effort_hours} hours "
        "(the author's estimate)."
    )
    return ctx.book.add(" ".join(parts))


def _plan_fact(ctx: AgentContext, p, job_id: int | None) -> str | None:
    if not p.steps:
        return None
    text = (
        f"Suggested plan (greedy, best ranking gain per hour): {', '.join(s.skill for s in p.steps)}; about "
        f"{p.total_hours} hours in total; the median rank of the {p.affected_jobs} jobs that list them goes from "
        f"#{_fmt(p.median_rank_before)} to #{_fmt(p.median_rank_after)}"
    )
    if job_id is not None and p.job_rank:
        text += f"; job {job_id} goes from #{p.job_rank[0]} to #{p.job_rank[1]}"
    return ctx.book.add(text + ".")


def best_skills(ctx: AgentContext) -> dict:
    _, sim = ctx.sim()
    effort = load_effort()
    effects = skill_effects(sim, effort)[:3]
    ids = [_effect_fact(ctx, e, None) for e in effects]
    plan = greedy_plan(sim, effort)
    pid = _plan_fact(ctx, plan, None)
    rows = [(e.skill, e.jobs_with_skill, e.effort_hours, fid) for e, fid in zip(effects, ids, strict=True)]
    if pid:
        ids.append(pid)
    if not ids:
        ids.append(ctx.book.add("No single missing skill changes your ranking in this pool."))
    ctx.data.setdefault(
        "best_skills", {"rows": rows, "plan": {"skills": [s.skill for s in plan.steps], "fact": pid} if pid else None}
    )
    return ctx.book.out(ids)


def explain_job(ctx: AgentContext, job_id: int) -> dict:
    job_id = int(job_id)
    pool, sim = ctx.sim(include_job=job_id)
    if job_id not in sim.by_id:
        return {"error": f"job {job_id} was not found or is no longer open"}
    g = gap(sim, job_id)
    card = pool.cards[job_id]
    ids = [
        ctx.book.add(
            f"For {_job(card)} you rank #{g['rank']} of {g['pool_size']} (score {g['score']:.2f}). "
            f"Skills you match: {_list(g['matched'])}."
        )
    ]
    if g["missing"]:
        miss = ", ".join(f"{m.skill} ({m.kind}, {round(m.share * 100)}% of its skill weight)" for m in g["missing"][:6])
        ids.append(ctx.book.add(f"Job {job_id} lists skills you lack: {miss}."))
    effort = load_effort()
    fixes = skill_effects(sim, effort, job_id=job_id, only=[m.skill for m in g["missing"]])
    fixes.sort(key=lambda e: (-(e.job_rank[0] - e.job_rank[1]) / max(e.effort_hours, 1), -e.rr_gain, e.skill))
    fix_rows = [(e.skill, e.job_rank, _effect_fact(ctx, e, job_id)) for e in fixes[:3]]
    ids += [f for _, _, f in fix_rows]
    plan = greedy_plan(sim, effort, job_id=job_id)
    pid = _plan_fact(ctx, plan, job_id)
    if pid:
        ids.append(pid)
    ctx.data.setdefault(
        "explain_job",
        {
            "job_id": job_id,
            "title": card["title"],
            "company": card["company"],
            "rank": g["rank"],
            "pool_size": g["pool_size"],
            "ids": ids,
            "fix_rows": fix_rows,
            "plan": {"skills": [s.skill for s in plan.steps], "fact": pid} if pid else None,
        },
    )
    return ctx.book.out(ids)


def simulate_skill(ctx: AgentContext, skill: str, job_id: int | None = None) -> dict:
    canon = load_taxonomy().canonical(str(skill or ""))
    if canon is None:
        return {"error": f"{skill!r} is not a skill this app knows"}
    job_id = int(job_id) if job_id is not None else None
    _, sim = ctx.sim(include_job=job_id)
    if canon in sim.profile.skills:
        return {"error": f"{canon} is already in the profile"}
    if job_id is not None and job_id not in sim.by_id:
        return {"error": f"job {job_id} was not found or is no longer open"}
    e = skill_effects(sim, load_effort(), job_id=job_id, only=[canon])
    if not e:
        return {"error": f"{canon} is not something this app suggests learning"}
    return ctx.book.out([_effect_fact(ctx, e[0], job_id)])


def search_jobs(ctx: AgentContext, query: str, remote_only: bool = False) -> dict:
    q = " ".join(str(query or "").split())[:120]
    if len(q) < 2:
        return {"error": "query too short"}
    qvec, _ = embed_query(ctx.conn, ctx.embedder, q)
    rows = hybrid_search(ctx.conn, q, qvec, MAX_SEARCH, SearchFilters(remote_only=bool(remote_only)))
    ids = [ctx.book.add(f'Search "{q}" found {_job(r)}.') for r in rows]
    if not ids:
        ids = [ctx.book.add(f'Search "{q}" found no open jobs.')]
    return ctx.book.out(ids)


TOOLS = {
    "rank_jobs": rank_jobs,
    "best_skills": best_skills,
    "explain_job": explain_job,
    "simulate_skill": simulate_skill,
    "search_jobs": search_jobs,
}

TOOL_SPECS = [
    ToolSpec(
        "rank_jobs",
        "Rank open jobs for the user's saved profile with the transparent ranker. Returns the top jobs as facts.",
        {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": MAX_RANK_LIMIT}}},
    ),
    ToolSpec(
        "best_skills",
        "Simulate the ranker with each missing skill added; returns the skills that move the user up most per "
        "hour of effort, plus a 3-step greedy plan.",
        {"type": "object", "properties": {}},
    ),
    ToolSpec(
        "explain_job",
        "Why-Not-Me for one job: the user's rank for it, the skills they lack, and which additions move that job up.",
        {"type": "object", "properties": {"job_id": {"type": "integer"}}, "required": ["job_id"]},
    ),
    ToolSpec(
        "simulate_skill",
        "Simulate adding one skill to the profile (optionally for one job) and report the ranking change.",
        {
            "type": "object",
            "properties": {"skill": {"type": "string"}, "job_id": {"type": "integer"}},
            "required": ["skill"],
        },
    ),
    ToolSpec(
        "search_jobs",
        "Hybrid (semantic + keyword) search over open jobs. Use for roles or topics outside the profile ranking.",
        {
            "type": "object",
            "properties": {"query": {"type": "string"}, "remote_only": {"type": "boolean"}},
            "required": ["query"],
        },
    ),
]


def run_tool(ctx: AgentContext, name: str, args: dict) -> dict:
    fn = TOOLS.get(name)
    if fn is None:
        return {"error": f"unknown tool {name}"}
    allowed = {
        "rank_jobs": {"limit"},
        "best_skills": set(),
        "explain_job": {"job_id"},
        "simulate_skill": {"skill", "job_id"},
        "search_jobs": {"query", "remote_only"},
    }[name]
    try:
        return fn(ctx, **{k: v for k, v in (args or {}).items() if k in allowed})
    except (TypeError, ValueError):
        return {"error": f"bad arguments for {name}"}
