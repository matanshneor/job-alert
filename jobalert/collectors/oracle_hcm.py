"""Oracle Recruiting Cloud (Oracle HCM "Candidate Experience") sites.

Listing: GET https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions
             ?onlyData=true&expand=requisitionList.secondaryLocations
             &finder=findReqs;siteNumber={site_number},limit=25,offset={n}
         (items[0].TotalJobsCount is the total)
Details: GET https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails
             ?onlyData=true&expand=all&finder=ById;Id="{id}",siteNumber={site_number}
params:
  host:        e.g. ehsb.fa.em2.oraclecloud.com
  site_number: e.g. CX_3001 (from the careers URL ".../sites/CX_3001")
  language:    UI language for job links (default "he")
"""
from __future__ import annotations

from datetime import datetime

from .base import Collector
from ..models import Job
from ..text import html_to_text

PAGE_SIZE = 25
MAX_PAGES = 40  # safety cap: 1000 requisitions
DETAIL_FIELDS = ("ExternalDescriptionStr", "ExternalResponsibilitiesStr", "ExternalQualificationsStr")


class OracleHcmCollector(Collector):
    ats = "oracle_hcm"

    def _api(self) -> str:
        return f"https://{self.param('host')}/hcmRestApi/resources/latest"

    def list_jobs(self) -> list[Job]:
        site = self.param("site_number")
        jobs: list[Job] = []
        for page in range(MAX_PAGES):
            # Oracle's finder syntax uses ';' and ',' literally, so the query is built by hand.
            url = (f"{self._api()}/recruitingCEJobRequisitions?onlyData=true"
                   f"&expand=requisitionList.secondaryLocations"
                   f"&finder=findReqs;siteNumber={site},limit={PAGE_SIZE},offset={page * PAGE_SIZE}")
            items = self.http.get_json(url).get("items") or [{}]
            total = items[0].get("TotalJobsCount") or 0
            reqs = items[0].get("requisitionList") or []
            jobs.extend(self._to_job(r) for r in reqs)
            if not reqs or (page + 1) * PAGE_SIZE >= total:
                break
        return jobs

    def fetch_details(self, job: Job) -> Job:
        url = (f"{self._api()}/recruitingCEJobRequisitionDetails?onlyData=true&expand=all"
               f'&finder=ById;Id="{job.external_id}",siteNumber={self.param("site_number")}')
        items = self.http.get_json(url).get("items") or [{}]
        parts = [job.raw.get("short_description") or ""]
        parts += [html_to_text(items[0].get(f)) for f in DETAIL_FIELDS]
        job.description = "\n\n".join(p.strip() for p in parts if p and p.strip()) or None
        return job

    def _to_job(self, r: dict) -> Job:
        secondary = [s.get("Name") or "" for s in r.get("secondaryLocations") or []]
        workplace = (r.get("WorkplaceTypeCode") or "").lower()
        workplace = {"remote": "remote", "hybrid": "hybrid", "on_site": "onsite", "onsite": "onsite"}.get(workplace)
        lang = self.company.params.get("language", "he")
        return self.make_job(
            external_id=str(r["Id"]),
            title=r["Title"].strip(),
            url=(f"https://{self.param('host')}/hcmUI/CandidateExperience/{lang}/sites/"
                 f"{self.param('site_number')}/job/{r['Id']}"),
            location=r.get("PrimaryLocation"),
            country=(r.get("PrimaryLocationCountry") or "").upper() or None,
            is_remote=workplace == "remote",
            workplace_type=workplace,
            all_locations=[t for t in [r.get("PrimaryLocation") or "", *secondary] if t],
            posted_at=_parse_date(r.get("PostedDate")),
            raw={"short_description": r.get("ShortDescriptionStr")},
        )


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
