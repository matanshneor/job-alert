"""Greenhouse public job board API.

Endpoint: GET https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true
params:
  board: the Greenhouse board token, as in https://boards.greenhouse.io/{board}
"""
from __future__ import annotations

import html
from datetime import datetime

from .base import Collector
from ..models import Job
from ..text import html_to_text


class GreenhouseCollector(Collector):
    ats = "greenhouse"

    def list_jobs(self) -> list[Job]:
        board = self.param("board")
        data = self.http.get_json(
            f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs", params={"content": "true"}
        )
        return [self._to_job(p, board) for p in data.get("jobs", [])]

    def _to_job(self, p: dict, board: str) -> Job:
        location = ((p.get("location") or {}).get("name") or "").strip()
        offices = p.get("offices") or []
        texts = [location] + [o.get("location") or "" for o in offices] + [o.get("name") or "" for o in offices]
        is_remote = "remote" in location.lower()
        # Greenhouse returns the description HTML-escaped (&lt;p&gt;...).
        description = html_to_text(html.unescape(p.get("content") or ""))
        return self.make_job(
            external_id=str(p["id"]),
            title=p["title"].strip(),
            url=p.get("absolute_url") or f"https://boards.greenhouse.io/{board}/jobs/{p['id']}",
            location=location or None,
            is_remote=is_remote,
            workplace_type="remote" if is_remote else None,
            all_locations=[t for t in texts if t],
            description=description or None,
            posted_at=_parse_dt(p.get("first_published") or p.get("updated_at")),
            raw={"departments": [d.get("name") for d in p.get("departments") or []]},
        )


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
