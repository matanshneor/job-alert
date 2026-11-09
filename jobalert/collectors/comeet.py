"""Comeet careers API.

Endpoint: GET https://www.comeet.co/careers-api/2.0/company/{uid}/positions?token={token}&details=true
The token is public: it is embedded in the company's Comeet careers page,
https://www.comeet.com/jobs/{comeet_slug}/{uid}. We read it from there and cache it.
params:
  comeet_slug: the company slug in the Comeet URL
  company_uid: the company uid in the Comeet URL, e.g. "49.004"
"""
from __future__ import annotations

import logging
import re
from datetime import datetime

import httpx

from .base import Collector
from ..models import Job
from ..text import html_to_text

log = logging.getLogger(__name__)

TOKEN_RE = re.compile(r'"token":\s*"([0-9A-Fa-f]+)"')
WORKPLACE = {"Remote": "remote", "Hybrid": "hybrid", "On-site": "onsite"}


class ComeetCollector(Collector):
    ats = "comeet"

    def _page_url(self) -> str:
        return f"https://www.comeet.com/jobs/{self.param('comeet_slug')}/{self.param('company_uid')}"

    def _fetch_token(self) -> str:
        html = self.http.get_text(self._page_url())
        match = TOKEN_RE.search(html)
        if not match:
            raise RuntimeError(f"Comeet token not found on {self._page_url()}")
        self.state["token"] = match.group(1)
        return match.group(1)

    def _positions(self, token: str) -> list[dict]:
        uid = self.param("company_uid")
        url = f"https://www.comeet.co/careers-api/2.0/company/{uid}/positions"
        return self.http.get_json(url, params={"token": token, "details": "true"})

    def list_jobs(self) -> list[Job]:
        token = self.state.get("token") or self._fetch_token()
        try:
            positions = self._positions(token)
        except httpx.HTTPStatusError:
            # The cached token may have been rotated; read a fresh one once.
            log.info("Comeet API failed for %s, refreshing token", self.company.slug)
            positions = self._positions(self._fetch_token())
        return [self._to_job(p) for p in positions]

    def _to_job(self, p: dict) -> Job:
        loc = p.get("location") or {}
        workplace = WORKPLACE.get(p.get("workplace_type") or "")
        is_remote = bool(loc.get("is_remote")) or workplace == "remote"
        description = "\n\n".join(
            f"{d.get('name', '')}\n{html_to_text(d.get('value'))}" for d in (p.get("details") or [])
        )
        return self.make_job(
            external_id=p["uid"],
            title=p["name"].strip(),
            url=p.get("url_active_page") or p.get("url_comeet_hosted_page") or self._page_url(),
            location=loc.get("name") or loc.get("city"),
            country=(loc.get("country") or "").upper() or None,
            is_remote=is_remote,
            workplace_type=workplace or ("remote" if is_remote else None),
            all_locations=[t for t in (loc.get("name"), loc.get("city")) if t],
            description=description or None,
            posted_at=_parse_dt(p.get("time_updated")),
            raw={k: p.get(k) for k in ("department", "employment_type", "experience_level", "workplace_type")},
        )


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
