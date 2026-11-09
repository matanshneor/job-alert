"""Ashby public job board API.

Endpoint: GET https://api.ashbyhq.com/posting-api/job-board/{board}
params:
  board: the Ashby board name, as in https://jobs.ashbyhq.com/{board}
"""
from __future__ import annotations

from datetime import datetime

from .base import Collector
from ..models import Job

WORKPLACE = {"Remote": "remote", "Hybrid": "hybrid", "OnSite": "onsite"}


def _country_names(address: dict | None) -> str:
    return ((address or {}).get("postalAddress") or {}).get("addressCountry") or ""


class AshbyCollector(Collector):
    ats = "ashby"

    def list_jobs(self) -> list[Job]:
        board = self.param("board")
        data = self.http.get_json(f"https://api.ashbyhq.com/posting-api/job-board/{board}")
        jobs = []
        for p in data.get("jobs", []):
            if p.get("isListed") is False:
                continue
            secondary = p.get("secondaryLocations") or []
            locations = [p.get("location") or ""] + [s.get("location") or "" for s in secondary]
            # Ashby gives country names ("Israel"), not codes; the location filter matches the text.
            country_texts = [_country_names(p.get("address"))] + [
                _country_names(s.get("address")) for s in secondary
            ]
            workplace = WORKPLACE.get(p.get("workplaceType") or "")
            jobs.append(
                self.make_job(
                    external_id=p["id"],
                    title=p["title"].strip(),
                    url=p.get("jobUrl") or f"https://jobs.ashbyhq.com/{board}/{p['id']}",
                    location=p.get("location"),
                    country="IL" if "Israel" in country_texts[:1] else None,
                    is_remote=workplace == "remote",
                    workplace_type=workplace,
                    all_locations=[t for t in locations + country_texts if t],
                    description=p.get("descriptionPlain"),
                    posted_at=_parse_dt(p.get("publishedAt")),
                    raw={k: p.get(k) for k in ("department", "team", "employmentType", "workplaceType")},
                )
            )
        return jobs


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
