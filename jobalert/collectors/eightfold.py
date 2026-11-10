"""Eightfold careers sites (e.g. jobs.amdocs.com).

Listing:  GET https://{host}/api/pcsx/search?domain={domain}&location={location}&start={n}
          (10 positions per page; data.count is the total)
Details:  GET https://{host}/api/pcsx/position_details?position_id={id}&domain={domain}
params:
  host:     the careers site host, e.g. jobs.amdocs.com
  domain:   the Eightfold domain, e.g. amdocs.com
  location: search location sent to the site (default "Israel"); it also returns remote jobs.
            The listing is complete for this location, so closing logic still works.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .base import Collector
from ..models import Job
from ..text import html_to_text

MAX_PAGES = 50  # safety cap: 500 positions


class EightfoldCollector(Collector):
    ats = "eightfold"

    def _base(self) -> str:
        return f"https://{self.param('host')}/api/pcsx"

    def list_jobs(self) -> list[Job]:
        domain = self.param("domain")
        location = self.company.params.get("location", "Israel")
        jobs: list[Job] = []
        start = 0
        for _ in range(MAX_PAGES):
            data = self.http.get_json(
                f"{self._base()}/search",
                params={"domain": domain, "location": location, "start": start},
            )["data"]
            positions = data.get("positions") or []
            jobs.extend(self._to_job(p) for p in positions)
            start += len(positions)
            if not positions or start >= (data.get("count") or 0):
                break
        return jobs

    def fetch_details(self, job: Job) -> Job:
        data = self.http.get_json(
            f"{self._base()}/position_details",
            params={"position_id": job.external_id, "domain": self.param("domain")},
        )["data"]
        job.description = html_to_text(data.get("jobDescription")) or None
        return job

    def _to_job(self, p: dict) -> Job:
        standardized = p.get("standardizedLocations") or []
        countries = sorted({s.rsplit(",", 1)[-1].strip().upper() for s in standardized if "," in s})
        workplace = (p.get("workLocationOption") or "").lower() or None
        if workplace not in ("remote", "hybrid", "onsite"):
            workplace = None
        locations = p.get("locations") or []
        return self.make_job(
            external_id=str(p["id"]),
            title=p["name"].strip(),
            url=f"https://{self.param('host')}{p.get('positionUrl') or '/careers/job/' + str(p['id'])}",
            location=locations[0] if locations else None,
            country=countries[0] if len(countries) == 1 else None,
            is_remote=workplace == "remote",
            workplace_type=workplace,
            all_locations=[t for t in locations + standardized if t],
            all_countries=countries,
            posted_at=_from_seconds(p.get("postedTs")),
            raw={"department": p.get("department"), "display_job_id": p.get("displayJobId")},
        )


def _from_seconds(value: int | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromtimestamp(value, tz=timezone.utc)
