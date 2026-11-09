"""Lever public postings API.

Endpoint: GET https://api.lever.co/v0/postings/{site}?mode=json  (EU accounts: api.eu.lever.co)
params:
  site:   the Lever site name, as in https://jobs.lever.co/{site}
  region: "eu" for EU-hosted accounts (jobs.eu.lever.co), omit otherwise
"""
from __future__ import annotations

from datetime import datetime, timezone

from .base import Collector
from ..models import Job
from ..text import html_to_text


class LeverCollector(Collector):
    ats = "lever"

    def list_jobs(self) -> list[Job]:
        site = self.param("site")
        host = "api.eu.lever.co" if self.company.params.get("region") == "eu" else "api.lever.co"
        data = self.http.get_json(f"https://{host}/v0/postings/{site}?mode=json")
        jobs = []
        for p in data:
            cats = p.get("categories") or {}
            locations = cats.get("allLocations") or [cats.get("location") or ""]
            workplace = (p.get("workplaceType") or "").lower() or None
            jobs.append(
                self.make_job(
                    external_id=p["id"],
                    title=p["text"].strip(),
                    url=p.get("hostedUrl") or f"https://jobs.lever.co/{site}/{p['id']}",
                    location=cats.get("location"),
                    country=(p.get("country") or "").upper() or None,
                    is_remote=workplace == "remote",
                    workplace_type=workplace,
                    all_locations=[t for t in locations if t],
                    description=_description(p),
                    posted_at=_from_millis(p.get("createdAt")),
                    raw={"team": cats.get("team"), "department": cats.get("department"),
                         "commitment": cats.get("commitment")},
                )
            )
        return jobs


def _description(p: dict) -> str:
    parts = [p.get("descriptionPlain") or ""]
    for section in p.get("lists") or []:
        parts.append(f"{section.get('text', '')}\n{html_to_text(section.get('content'))}")
    parts.append(p.get("additionalPlain") or "")
    return "\n\n".join(x.strip() for x in parts if x and x.strip())


def _from_millis(value: int | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
