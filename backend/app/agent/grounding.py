"""Grounding check for the agent's narrative. Pure functions, no I/O (property-tested).

Every tool result is turned into short numbered facts ("F3: Job 12 ... ranks #4 of 200 ..."). The model must cite
fact ids for every sentence it writes. A claim survives only if:
  1. it cites at least one fact, and every cited id exists;
  2. every number in it appears in the cited facts (so no invented ranks, hours or counts);
  3. every skill it names (taxonomy match) appears in the cited facts;
  4. it has no URL and is at most MAX_CLAIM_CHARS long.
A next step additionally needs its skill to be a taxonomy skill that the cited facts mention.
Failing claims are dropped, never repaired: the user sees fewer sentences, not wrong ones.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field

MAX_CLAIM_CHARS = 300
MAX_POINTS = 4
MAX_STEPS = 3
_NUM = re.compile(r"\d+(?:\.\d+)?")
_URL = re.compile(r"https?://|www\.", re.I)

NARRATIVE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "headline": {"type": "string", "description": "One sentence answering the question."},
        "headline_facts": {"type": "array", "items": {"type": "string"}, "description": "Fact ids, e.g. F2"},
        "points": {
            "type": "array",
            "maxItems": MAX_POINTS,
            "items": {
                "type": "object",
                "properties": {"text": {"type": "string"}, "facts": {"type": "array", "items": {"type": "string"}}},
                "required": ["text", "facts"],
            },
        },
        "next_steps": {
            "type": "array",
            "maxItems": MAX_STEPS,
            "items": {
                "type": "object",
                "properties": {
                    "skill": {"type": "string"},
                    "why": {"type": "string"},
                    "steps": {
                        "type": "array",
                        "maxItems": 3,
                        "items": {"type": "string"},
                        "description": "Concrete practice steps; no links, no numbers unless from the facts.",
                    },
                    "facts": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["skill", "why", "facts"],
            },
        },
    },
    "required": ["headline", "headline_facts", "points", "next_steps"],
}


def numbers(text: str) -> set[str]:
    """Canonical number tokens: '1,000' -> '1000', '0.60' -> '0.6', '#4' -> '4'."""
    text = re.sub(r"(?<=\d),(?=\d{3}\b)", "", text)
    out = set()
    for n in _NUM.findall(text):
        whole, _, frac = n.partition(".")
        whole, frac = whole.lstrip("0") or "0", frac.rstrip("0")
        out.add(f"{whole}.{frac}" if frac else whole)
    return out


@dataclass
class Claim:
    text: str
    facts: list[str]


@dataclass
class Step:
    skill: str
    why: str
    facts: list[str]
    steps: list[str] = field(default_factory=list)


@dataclass
class Narrative:
    headline: Claim | None
    points: list[Claim] = field(default_factory=list)
    next_steps: list[Step] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "headline": None if self.headline is None else vars(self.headline),
            "points": [vars(p) for p in self.points],
            "next_steps": [vars(s) for s in self.next_steps],
        }


@dataclass
class GroundingReport:
    narrative: Narrative
    dropped: list[dict]  # {"text", "reason"}

    @property
    def kept(self) -> int:
        n = self.narrative
        return (n.headline is not None) + len(n.points) + len(n.next_steps)


SkillFinder = Callable[[str], Iterable[str]]
Canonical = Callable[[str], "str | None"]


def check_claim(text: str, cited: list[str], facts: Mapping[str, str], find_skills: SkillFinder) -> str | None:
    """None if grounded, else the reason it is not."""
    if not isinstance(text, str) or not text.strip():
        return "empty"
    if len(text) > MAX_CLAIM_CHARS:
        return "too long"
    if _URL.search(text):
        return "contains a link"
    if not cited:
        return "cites no fact"
    missing = [f for f in cited if f not in facts]
    if missing:
        return f"cites unknown fact {missing[0]}"
    source = " ".join(facts[f] for f in cited)
    extra = numbers(text) - numbers(source)
    if extra:
        return f"number {sorted(extra)[0]} not in cited facts"
    have = set(find_skills(source))
    for s in find_skills(text):
        if s not in have:
            return f"skill {s} not in cited facts"
    return None


def _ids(v) -> list[str]:
    return [str(x).strip() for x in v][:6] if isinstance(v, list) else []


def ground(
    raw: Mapping,
    facts: Mapping[str, str],
    find_skills: SkillFinder,
    canonical: Canonical,
    always: tuple[str, ...] = (),
) -> GroundingReport:
    """Validate a model narrative (already JSON-decoded) against the fact table.

    `always`: fact ids every practice step may draw on implicitly (the profile fact), besides its own skill.
    """
    dropped: list[dict] = []

    def keep(text, cited) -> bool:
        reason = check_claim(text, cited, facts, find_skills)
        if reason:
            dropped.append({"text": str(text)[:200], "reason": reason})
        return reason is None

    head_text, head_ids = raw.get("headline"), _ids(raw.get("headline_facts"))
    headline = Claim(head_text.strip(), head_ids) if keep(head_text, head_ids) else None

    points = []
    for p in (raw.get("points") or [])[:MAX_POINTS]:
        if not isinstance(p, Mapping):
            continue
        text, ids = p.get("text"), _ids(p.get("facts"))
        if keep(text, ids):
            points.append(Claim(text.strip(), ids))

    steps, seen = [], set()
    for s in (raw.get("next_steps") or [])[:MAX_STEPS]:
        if not isinstance(s, Mapping):
            continue
        ids = _ids(s.get("facts"))
        skill = canonical(str(s.get("skill") or ""))
        if skill is None:
            dropped.append({"text": str(s.get("skill"))[:60], "reason": "not a known skill"})
            continue
        if skill in seen:
            continue
        cited_src = " ".join(facts.get(f, "") for f in ids)
        if skill not in set(find_skills(cited_src)):
            dropped.append({"text": skill, "reason": "skill not in cited facts"})
            continue
        if keep(s.get("why"), ids):
            seen.add(skill)
            ctx_facts = {**facts, "_skill": skill}
            cited = [*ids, *[a for a in always if a in facts], "_skill"]
            practice = []
            for line in (s.get("steps") or [])[:3] if isinstance(s.get("steps"), list) else []:
                reason = check_claim(line, cited, ctx_facts, find_skills)
                if reason:
                    dropped.append({"text": str(line)[:200], "reason": f"step: {reason}"})
                else:
                    practice.append(line.strip())
            steps.append(Step(skill, s["why"].strip(), ids, practice))

    if headline is None and points:  # promote the first grounded point rather than show no headline
        headline = points.pop(0)
    return GroundingReport(Narrative(headline, points, steps), dropped)
