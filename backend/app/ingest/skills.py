"""Rule-based skill extraction from sectioned job text. No LLM: deterministic, free, re-runnable.

Rules (documented because the eval measures them):
1. Every alias is matched on custom word boundaries that understand C++, C#, .NET, Node.js.
2. Overlapping matches -> the longest wins ("Objective-C" beats "C", "React Native" beats "React").
3. A match inside a `block` phrase is dropped ("Series C" is not the C language).
4. Section -> bucket:
     required sections            -> required
     preferred / nice-to-have     -> preferred
     ignore (about us, benefits)  -> dropped (company boilerplate)
     responsibilities / other     -> required if the posting has NO required section, else preferred
   A skill in both buckets is required.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from app.config import DATA_DIR

TAXONOMY_PATH = DATA_DIR / "skills_taxonomy.yaml"

_LEFT = r"(?<![\w+#.])"
_RIGHT = r"(?![\w+#&]|\.\w)"


@dataclass(frozen=True)
class Mention:
    skill: str
    text: str
    start: int
    end: int


@dataclass
class Extraction:
    required: list[str]
    preferred: list[str]
    mentions: list[tuple[str, str, str]]  # (skill, section_kind, matched_text) for the eval


class Taxonomy:
    def __init__(self, entries: list[dict]):
        self.names = [e["name"] for e in entries]
        self._ci: dict[str, str] = {}  # lowercased alias -> skill
        self._cs: dict[str, str] = {}  # exact alias -> skill
        self._blocks: dict[str, list[re.Pattern]] = {}
        for e in entries:
            name = e["name"]
            ci = list(e.get("aliases") or [])
            cs = list(e.get("case_aliases") or [])
            (cs if e.get("case") else ci).append(name)
            for alias in ci:
                self._ci[alias.lower()] = name
            for alias in cs:
                self._cs[alias] = name
            if e.get("block"):
                self._blocks[name] = [re.compile(re.escape(b), re.I) for b in e["block"]]
        # One alternation per case mode, longest alias first: a single regex pass per text instead of
        # ~800 (65 ms -> ~1 ms per job). If the longest alternative fails the right boundary, the engine
        # backtracks to shorter ones at the same position, so "React Native" still beats "React".
        self._pats = [
            (self._ci, self._alternation(self._ci, re.I)),
            (self._cs, self._alternation(self._cs, 0)),
        ]

    @staticmethod
    def _alternation(aliases: dict[str, str], flags: int) -> re.Pattern:
        alts = "|".join(re.escape(a) for a in sorted(aliases, key=len, reverse=True))
        return re.compile(f"{_LEFT}(?:{alts}){_RIGHT}", flags)

    def canonical(self, text: str) -> str | None:
        """Map a user-typed chip ("js", "k8s", "Postgres") to the taxonomy name, or None if unknown."""
        t = " ".join(text.split())
        if not t:
            return None
        if t in self._cs:
            return self._cs[t]
        if t.lower() in self._ci:
            return self._ci[t.lower()]
        for n in self.names:  # case-sensitive names typed in another case ("REACT" -> React)
            if n.lower() == t.lower():
                return n
        return None

    @property
    def pattern_count(self) -> int:
        return len(self._ci) + len(self._cs)

    def find(self, text: str) -> list[Mention]:
        raw: list[Mention] = []
        for lookup, pat in self._pats:
            ci = lookup is self._ci
            for m in pat.finditer(text):
                skill = lookup[m.group(0).lower() if ci else m.group(0)]
                raw.append(Mention(skill, m.group(0), m.start(), m.end()))
        # longest-first, then leftmost; keep non-overlapping
        raw.sort(key=lambda m: (-(m.end - m.start), m.start))
        taken: list[Mention] = []
        for m in raw:
            if any(m.start < t.end and t.start < m.end for t in taken):
                continue
            if self._blocked(m, text):
                continue
            taken.append(m)
        taken.sort(key=lambda m: m.start)
        return taken

    def _blocked(self, m: Mention, text: str) -> bool:
        for bp in self._blocks.get(m.skill, []):
            lo = max(0, m.start - 40)
            for b in bp.finditer(text, lo, min(len(text), m.end + 40)):
                if b.start() <= m.start and m.end <= b.end():
                    return True
        return False


@lru_cache
def load_taxonomy(path: Path = TAXONOMY_PATH) -> Taxonomy:
    return Taxonomy(yaml.safe_load(path.read_text(encoding="utf-8"))["skills"])


def extract(secs: list[tuple[str, str, str]], tax: Taxonomy | None = None) -> Extraction:
    tax = tax or load_taxonomy()
    has_required = any(k == "required" for k, _, _ in secs)
    req: dict[str, None] = {}
    pref: dict[str, None] = {}
    mentions: list[tuple[str, str, str]] = []
    for kind, _heading, text in secs:
        if kind == "ignore":
            continue
        bucket = {
            "required": req,
            "preferred": pref,
        }.get(kind, pref if has_required else req)
        for m in tax.find(text):
            bucket[m.skill] = None
            mentions.append((m.skill, kind, m.text))
    preferred = [s for s in pref if s not in req]
    return Extraction(required=list(req), preferred=preferred, mentions=mentions)
