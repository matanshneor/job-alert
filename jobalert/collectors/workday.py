"""Workday career sites (*.myworkdayjobs.com).

Listing: POST https://{host}/wday/cxs/{tenant}/{site}/jobs
         body {"limit": 20, "offset": n, "searchText": "", "appliedFacets": facets}
         (data.total is only reliable on the first page)
Details: GET  https://{host}/wday/cxs/{tenant}/{site}{externalPath}
params:
  host:    e.g. paloaltonetworks.wd5.myworkdayjobs.com
  tenant:  e.g. paloaltonetworks
  site:    e.g. panwexternalcareers
  facets:  Workday facet filters, e.g. {locations: [<ids>]} (ids from the site's facet list)
  country: optional ISO-2 code; set it when the facets already limit the search to one country,
           because the listing often says only "2 Locations" instead of the place names.
"""
from __future__ import annotations

from .base import Collector
from ..models import Job
from ..text import html_to_text

PAGE_SIZE = 20
MAX_PAGES = 50  # safety cap: 1000 postings


class WorkdayCollector(Collector):
    ats = "workday"

    def _base(self) -> str:
        return f"https://{self.param('host')}/wday/cxs/{self.param('tenant')}/{self.param('site')}"

    def list_jobs(self) -> list[Job]:
        facets = self.company.params.get("facets") or {}
        jobs: list[Job] = []
        total = None
        for page in range(MAX_PAGES):
            data = self.http.post_json(
                f"{self._base()}/jobs",
                {"limit": PAGE_SIZE, "offset": page * PAGE_SIZE, "searchText": "", "appliedFacets": facets},
            )
            if total is None:
                total = data.get("total") or 0
            postings = data.get("jobPostings") or []
            jobs.extend(self._to_job(p) for p in postings if p.get("externalPath"))
            if not postings or (page + 1) * PAGE_SIZE >= total:
                break
        return jobs

    def fetch_details(self, job: Job) -> Job:
        info = self.http.get_json(f"{self._base()}{job.raw['path']}").get("jobPostingInfo") or {}
        job.description = html_to_text(info.get("jobDescription")) or None
        if (info.get("remoteType") or "").lower() in ("remote", "fully remote"):
            job.is_remote, job.workplace_type = True, "remote"
        return job

    def _to_job(self, p: dict) -> Job:
        path = p["externalPath"]
        # ".../Senior-Engineer_JR-023791" -> "JR-023791"; fall back to the whole path.
        external_id = path.rsplit("_", 1)[-1] if "_" in path else path
        country = self.company.params.get("country")
        return self.make_job(
            external_id=external_id,
            title=p["title"].strip(),
            url=f"https://{self.param('host')}/{self.param('site')}{path}",
            location=p.get("locationsText"),
            country=country.upper() if country else None,
            all_locations=[p.get("locationsText") or ""],
            raw={"path": path, "time_type": p.get("timeType")},
        )
