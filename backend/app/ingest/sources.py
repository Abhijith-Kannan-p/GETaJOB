"""Fetchers for the two official public job-board APIs, normalised to RawJob.

- Greenhouse Job Board API: GET boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true
- Lever Postings API:       GET api.lever.co/v0/postings/{site}?mode=json

Why a polite client: these are free public endpoints. We send an identifying User-Agent,
sleep between calls, and back off on 429/5xx instead of hammering.
"""

from __future__ import annotations

import html
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx

from app.config import get_settings

log = logging.getLogger("getajob.sources")

GH_URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
GH_BOARD_URL = "https://boards-api.greenhouse.io/v1/boards/{token}"
LEVER_URL = "https://api.lever.co/v0/postings/{site}"


@dataclass
class RawJob:
    source: str
    external_id: str
    board: str
    company: str
    title: str
    location: str
    url: str
    html: str  # description HTML (unescaped)
    posted_at: datetime | None = None
    workplace_type: str | None = None  # lever only: remote/onsite/hybrid
    extra_sections: list[tuple[str, str]] = field(default_factory=list)  # lever "lists": (heading, html)


class BoardNotFound(Exception):
    pass


def make_client() -> httpx.Client:
    s = get_settings()
    return httpx.Client(timeout=30, headers={"User-Agent": s.user_agent, "Accept": "application/json"})


def _get_json(client: httpx.Client, url: str, params: dict | None = None):
    s = get_settings()
    for attempt in range(3):
        time.sleep(s.http_sleep_s)  # politeness delay before every call
        r = client.get(url, params=params)
        if r.status_code == 404:
            raise BoardNotFound(url)
        if r.status_code in (429, 500, 502, 503, 504):
            wait = 5 * (attempt + 1)
            log.warning("http %s on %s; backing off %ss", r.status_code, url, wait)
            time.sleep(wait)
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"giving up on {url}")


def _ts(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, int | float):  # lever: epoch millis
        return datetime.fromtimestamp(value / 1000, tz=UTC)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def greenhouse_board_name(client: httpx.Client, token: str) -> str:
    return _get_json(client, GH_BOARD_URL.format(token=token)).get("name") or token


def fetch_greenhouse(client: httpx.Client, token: str, company: str, content: bool = True) -> list[RawJob]:
    data = _get_json(client, GH_URL.format(token=token), {"content": "true" if content else "false"})
    out = []
    for j in data.get("jobs", []):
        out.append(
            RawJob(
                source="greenhouse",
                external_id=str(j["id"]),
                board=token,
                company=company,
                title=(j.get("title") or "").strip(),
                location=((j.get("location") or {}).get("name") or "").strip(),
                url=j.get("absolute_url") or "",
                # Greenhouse returns entity-escaped HTML in `content`
                html=html.unescape(j.get("content") or ""),
                posted_at=_ts(j.get("first_published") or j.get("updated_at")),
            )
        )
    return out


def fetch_lever(client: httpx.Client, site: str, company: str) -> list[RawJob]:
    data = _get_json(client, LEVER_URL.format(site=site), {"mode": "json"})
    if not isinstance(data, list):
        raise BoardNotFound(site)
    out = []
    for j in data:
        cats = j.get("categories") or {}
        loc = cats.get("location") or ", ".join(cats.get("allLocations") or [])
        sections = [(li.get("text") or "", li.get("content") or "") for li in (j.get("lists") or [])]
        out.append(
            RawJob(
                source="lever",
                external_id=str(j["id"]),
                board=site,
                company=company,
                title=(j.get("text") or "").strip(),
                location=(loc or "").strip(),
                url=j.get("hostedUrl") or "",
                html=(j.get("description") or "") + "\n" + (j.get("additional") or ""),
                posted_at=_ts(j.get("createdAt")),
                workplace_type=j.get("workplaceType"),
                extra_sections=sections,
            )
        )
    return out


def fetch_board(client: httpx.Client, source: str, slug: str, company: str) -> list[RawJob]:
    if source == "greenhouse":
        return fetch_greenhouse(client, slug, company)
    if source == "lever":
        return fetch_lever(client, slug, company)
    raise ValueError(source)
