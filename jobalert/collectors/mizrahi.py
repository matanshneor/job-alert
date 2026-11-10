"""Mizrahi-Tefahot bank: jobs listed directly in the HTML of its open-jobs page.

Listing: GET https://www.mizrahi-tefahot.co.il/about-mizrahi-tefahot-he/career/open-jobs/
         each job is a <div class="job filterItem"> with the title in <h3> and a link slug.
Details: GET {listing url}{slug}/  (the job's own page, with the full description)
params:
  url: the open-jobs page (optional, defaults to the URL above)
All jobs are in Israel; the region is in Hebrew (e.g. "שרון"), so country is set to IL.
"""
from __future__ import annotations

from bs4 import BeautifulSoup

from .base import Collector
from ..models import Job

DEFAULT_URL = "https://www.mizrahi-tefahot.co.il/about-mizrahi-tefahot-he/career/open-jobs/"


class MizrahiCollector(Collector):
    ats = "mizrahi"

    def _url(self) -> str:
        url = self.company.params.get("url") or DEFAULT_URL
        return url if url.endswith("/") else url + "/"

    def list_jobs(self) -> list[Job]:
        soup = BeautifulSoup(self.http.get_text(self._url()), "html.parser")
        items = soup.select("div.job.filterItem")
        if not items:
            # The page layout changed: fail loudly instead of reporting 0 jobs.
            raise RuntimeError(f"No job items found on {self._url()}")
        return [self._to_job(item) for item in items]

    def fetch_details(self, job: Job) -> Job:
        soup = BeautifulSoup(self.http.get_text(job.url), "html.parser")
        main = soup.select_one("main") or soup.body
        job.description = main.get_text("\n", strip=True) if main else None
        return job

    def _to_job(self, item) -> Job:
        slug = item.select_one("a.toggler")["href"].strip("/")
        region = (item.get("data-jobregions") or "").split("|")[0].strip()
        short = item.select_one(".jobDescription")
        return self.make_job(
            external_id=slug,
            title=item.select_one("h3").get_text(strip=True),
            url=f"{self._url()}{slug}/",
            location=region or None,
            country="IL",
            all_locations=[region] if region else [],
            raw={"field": item.get("data-jobfields"), "type": item.get("data-jobtypes"),
                 "short_description": short.get_text("\n", strip=True) if short else None},
        )
