"""HTML -> clean text split into headed sections.

Why sections: a skill under "Requirements" is a must-have, under "Nice to have" it's a bonus,
and under "About us"/"Benefits" it's usually boilerplate. The ranker weights these differently.
"""

from __future__ import annotations

import html as htmllib
import re

DESCRIPTION_CAP = 6000  # chars stored per job (Neon free = 0.5 GB)

_BLOCK = re.compile(r"</?(p|div|br|li|ul|ol|tr|table|section|article|h[1-6])\b[^>]*>", re.I)
_HEADING_OPEN = re.compile(r"<(h[1-6])\b[^>]*>", re.I)
_TAG = re.compile(r"<[^>]+>")
_SCRIPT = re.compile(r"<(script|style)\b.*?</\1>", re.I | re.S)
_WS = re.compile(r"[ \t ]+")

REQUIRED_H = re.compile(
    r"(requirement|qualification|must[- ]have|what you(['’]ll| will)? (need|bring)|you (have|bring|are)|"
    r"who you are|skills|experience|about you|what we(['’]re)? look(ing)? for|we(['’]d)? expect|ideal candidate)",
    re.I,
)
PREFERRED_H = re.compile(
    r"(nice[- ]to[- ]have|preferred|bonus|plus|desired|good to have|extra credit|would be great|stand out|"
    r"ideally)",
    re.I,
)
RESP_H = re.compile(
    r"(responsibilit|what you(['’]ll| will)? do|the role|your impact|day[- ]to[- ]day|in this role)", re.I
)
IGNORE_H = re.compile(
    r"(about (us|the company|[A-Z][a-z]+$)|benefit|perks|compensation|salary|pay (range|transparency)|"
    r"equal opportunity|eeo|diversity|accommodation|privacy|why join|our (mission|values)|life at)",
    re.I,
)


def html_to_lines(raw: str) -> list[str]:
    s = _SCRIPT.sub(" ", raw or "")
    s = _HEADING_OPEN.sub("\n## ", s)
    # <strong>/<b> at the start of a block often acts as a heading
    s = re.sub(r"(^|\n|<p[^>]*>|<div[^>]*>)\s*<(strong|b)\b[^>]*>", r"\1\n## ", s, flags=re.I)
    s = _BLOCK.sub("\n", s)
    s = _TAG.sub("", s)  # inline tags: no space, or "<b>C</b>ommunicate" splits into "C ommunicate"
    s = htmllib.unescape(s)
    lines = []
    for line in s.split("\n"):
        line = _WS.sub(" ", line).strip()
        if line and line != "##":
            lines.append(line)
    return lines


def _is_heading(line: str) -> bool:
    if line.startswith("## "):
        return len(line) < 100
    return len(line) < 70 and line.endswith(":")


def classify_heading(h: str) -> str:
    h = h.lstrip("# ").rstrip(":")
    if IGNORE_H.search(h):
        return "ignore"
    if PREFERRED_H.search(h):
        return "preferred"
    if RESP_H.search(h):
        return "responsibilities"
    if REQUIRED_H.search(h):
        return "required"
    return "other"


def sections(raw_html: str, extra: list[tuple[str, str]] | None = None) -> list[tuple[str, str, str]]:
    """Return [(kind, heading, text)] in document order."""
    out: list[tuple[str, str, str]] = []
    kind, heading, buf = "other", "", []

    def flush():
        if buf:
            out.append((kind, heading, "\n".join(buf)))

    for line in html_to_lines(raw_html):
        if _is_heading(line):
            # A heading that also carries content ("Bonus: Go experience") keeps the content.
            flush()
            heading = line.lstrip("# ").strip()
            kind = classify_heading(heading)
            buf = []
            if heading.endswith(":") is False and ":" in heading and len(heading.split(":", 1)[1]) > 3:
                buf.append(heading.split(":", 1)[1].strip())
            continue
        buf.append(line.lstrip("#").strip())
    flush()
    for h, body in extra or []:  # Lever "lists" are already headed
        text = "\n".join(html_to_lines(body))
        if text:
            out.append((classify_heading(h), h, text))
    return out


def plain_text(secs: list[tuple[str, str, str]], cap: int = DESCRIPTION_CAP) -> str:
    parts = []
    for _, h, t in secs:
        parts.append((h + "\n" if h else "") + t)
    return "\n\n".join(parts)[:cap]
