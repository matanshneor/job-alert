"""SmartRecruiters public postings API.

Listing: GET https://api.smartrecruiters.com/v1/companies/{company_id}/postings?country={cc}&limit=100&offset={n}
         (totalFound is the total)
Details: GET https://api.smartrecruiters.com/v1/companies/{company_id}/postings/{id}
params:
  company_id: as in https://jobs.smartrecruiters.com/{company_id}
  country:    optional ISO-2 filter sent to the API (e.g. "il"); the listing is complete for it.
"""
from __future__ import annotations

from datetime import datetime

from .base import Collector
from ..models import Job
from ..text import html_to_text

PAGE_SIZE = 100
MAX_PAGES = 20  # safety cap: 2000 postings
SECTIONS = ("jobDescription", "qualifications", "additionalInformation")


class SmartRecruitersCollector(Collector):
    ats = "smartrecruiters"

    def _base(self) -> str:
        return f"https://api.smartrecruiters.com/v1/companies/{self.param('company_id')}/postings"

    def list_jobs(self) -> list[Job]:
        params = {"limit": PAGE_SIZE}
        if self.company.params.get("country"):
            params["country"] = self.company.params["country"].lower()
        jobs: list[Job] = []
        for page in range(MAX_PAGES):
            data = self.http.get_json(self._base(), params={**params, "offset": page * PAGE_SIZE})
            content = data.get("content") or []
            jobs.extend(self._to_job(p) for p in content)
            if not content or (page + 1) * PAGE_SIZE >= (data.get("totalFound") or 0):
                break
        return jobs

    def fetch_details(self, job: Job) -> Job:
        data = self.http.get_json(f"{self._base()}/{job.external_id}")
        sections = (data.get("jobAd") or {}).get("sections") or {}
        parts = []
        for key in SECTIONS:
            section = sections.get(key) or {}
            text = html_to_text(section.get("text"))
            if text:
                parts.append(f"{section.get('title') or ''}\n{text}".strip())
        # The URL is not replaced with data["postingUrl"]: jobs already stored are not re-fetched,
        # and the DB overwrites url each run, so it must not depend on whether details were fetched.
        job.description = "\n\n".join(parts) or None
        return job

    def _to_job(self, p: dict) -> Job:
        loc = p.get("location") or {}
        workplace = "remote" if loc.get("remote") else "hybrid" if loc.get("hybrid") else "onsite"
        return self.make_job(
            external_id=str(p["id"]),
            title=p["name"].strip(),
            url=f"https://jobs.smartrecruiters.com/{self.param('company_id')}/{p['id']}",
            location=loc.get("fullLocation") or loc.get("city"),
            country=(loc.get("country") or "").upper() or None,
            is_remote=bool(loc.get("remote")),
            workplace_type=workplace,
            all_locations=[t for t in (loc.get("fullLocation"), loc.get("city")) if t],
            posted_at=_parse_dt(p.get("releasedDate")),
            raw={"function": (p.get("function") or {}).get("label"),
                 "experience": (p.get("experienceLevel") or {}).get("label")},
        )


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
