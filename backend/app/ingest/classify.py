"""Rule-based job classification (no LLM at ingest: deterministic, free, testable).

seniority: intern | new_grad | entry | mid | senior | unknown
"""

from __future__ import annotations

import re

_INTERN = re.compile(r"\b(intern|internship|co-?op|trainee|apprentice(ship)?|summer analyst)\b", re.I)
_NEWGRAD = re.compile(r"\b(new grad(uate)?|graduate|university|campus|fresher|early[- ]career|entry[- ]level)\b", re.I)
_ENTRY = re.compile(r"\b(junior|jr\.?|associate)\b|\b(engineer|developer|scientist|analyst)\s+(i|1)\b", re.I)
_SENIOR = re.compile(
    r"\b(senior|sr\.?|staff|principal|lead|manager|director|head|vp|vice president|architect|distinguished)\b"
    r"|\b(engineer|developer|scientist)\s+(iii|iv|v|3|4|5)\b",
    re.I,
)
_MID = re.compile(r"\b(engineer|developer|scientist|analyst)\s+(ii|2)\b", re.I)
_YEARS = re.compile(r"(\d{1,2})\s*(?:\+|plus)?\s*(?:-|to|–)?\s*(\d{1,2})?\s*\+?\s*years?", re.I)

_TECH = re.compile(
    r"\b(engineer(ing)?|developer|software|sde|swe|data|machine learning|ml|ai|scientist|research(er)?|"
    r"sre|devops|backend|back[- ]end|frontend|front[- ]end|full[- ]?stack|platform|infrastructure|"
    r"security|qa|sdet|test|mobile|ios|android|analyst|analytics|cloud|programmer|architect)\b",
    re.I,
)
_NON_TECH = re.compile(
    r"\b(account (executive|manager)|recruit(er|ing)|sales(?! engineer)|marketing|legal|counsel|"
    r"finance|accountant|payroll|hr\b|people partner|customer success|support specialist)\b",
    re.I,
)

INDIA_PLACES = (
    "india",
    "bengaluru",
    "bangalore",
    "hyderabad",
    "pune",
    "mumbai",
    "delhi",
    "gurgaon",
    "gurugram",
    "noida",
    "chennai",
    "kolkata",
    "ahmedabad",
    "kochi",
    "trivandrum",
    "thiruvananthapuram",
    "jaipur",
)
_REMOTE = re.compile(r"\b(remote|anywhere|work from home|wfh|distributed)\b", re.I)


def seniority(title: str, text: str = "") -> str:
    if _INTERN.search(title):
        return "intern"
    if _NEWGRAD.search(title):
        return "new_grad"
    if _SENIOR.search(title):
        return "senior"
    if _ENTRY.search(title):
        return "entry"
    if _MID.search(title):
        return "mid"
    # Fall back to the smallest "N years" requirement in the text.
    years = [int(m.group(1)) for m in _YEARS.finditer(text[:4000]) if int(m.group(1)) <= 20]
    if years:
        y = min(years)
        if y <= 1:
            return "entry"
        if y <= 4:
            return "mid"
        return "senior"
    return "unknown"


def is_remote(title: str, location: str, workplace_type: str | None = None) -> bool:
    if workplace_type and workplace_type.lower() == "remote":
        return True
    return bool(_REMOTE.search(location or "") or _REMOTE.search(title or ""))


def in_india(location: str) -> bool:
    loc = (location or "").lower()
    return any(p in loc for p in INDIA_PLACES)


def is_tech(title: str) -> bool:
    return bool(_TECH.search(title)) and not _NON_TECH.search(title)


def priority(title: str, location: str, remote: bool, level: str) -> int:
    """Higher = indexed first when we hit MAX_ACTIVE_JOBS. Matches the target audience (students, India/remote)."""
    p = 0
    if is_tech(title):
        p += 4
    if level in ("intern", "new_grad"):
        p += 4
    elif level == "entry":
        p += 3
    elif level in ("mid", "unknown"):
        p += 1
    if in_india(location):
        p += 2
    if remote:
        p += 1
    return p
