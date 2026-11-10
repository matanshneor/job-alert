import httpx

from jobalert.collectors.ashby import AshbyCollector
from jobalert.collectors.comeet import ComeetCollector
from jobalert.collectors.eightfold import EightfoldCollector
from jobalert.collectors.greenhouse import GreenhouseCollector
from jobalert.collectors.lever import LeverCollector
from jobalert.config import CompanyConfig
from jobalert.location import LocationFilter

from .conftest import FakeHttp, load_fixture

MONDAY = CompanyConfig("monday", "monday.com", "ashby", {"board": "monday.com"})
MOBILEYE = CompanyConfig("mobileye", "Mobileye", "lever", {"site": "mobileye", "region": "eu"})
PAYONEER = CompanyConfig("payoneer", "Payoneer", "greenhouse", {"board": "payoneer"})
AMDOCS = CompanyConfig("amdocs", "Amdocs", "eightfold",
                       {"host": "jobs.amdocs.com", "domain": "amdocs.com", "location": "Israel"})
UPWIND = CompanyConfig("upwind", "Upwind", "comeet", {"comeet_slug": "upwind", "company_uid": "49.004"})


def test_ashby_parses_and_filters(settings):
    http = FakeHttp({"ashbyhq.com": load_fixture("ashby_monday.json")})
    jobs = AshbyCollector(MONDAY, http, {}).list_jobs()
    assert len(jobs) == 3
    tlv, us_remote, zurich = jobs
    assert tlv.job_key.startswith("ashby:monday:")
    assert tlv.company == "monday.com" and tlv.url.startswith("https://jobs.ashbyhq.com/monday.com/")
    assert tlv.description
    loc = LocationFilter(settings.location)
    assert loc.passes(tlv)
    assert not loc.passes(us_remote)
    assert not loc.passes(zurich)


def test_lever_eu_parses_and_filters(settings):
    http = FakeHttp({"api.eu.lever.co/v0/postings/mobileye": load_fixture("lever_mobileye.json")})
    jobs = LeverCollector(MOBILEYE, http, {}).list_jobs()
    assert len(jobs) == 3
    assert all(j.job_key.startswith("lever:mobileye:") for j in jobs)
    assert jobs[0].country == "IL" and jobs[2].country == "CN"
    assert "All you need is" in jobs[0].description or jobs[0].description
    loc = LocationFilter(settings.location)
    assert [loc.passes(j) for j in jobs] == [True, True, False]


def test_comeet_reads_token_from_page_and_caches_it(settings):
    http = FakeHttp({
        "comeet.com/jobs/upwind/49.004": load_fixture("comeet_page_snippet.html"),
        "careers-api/2.0/company/49.004/positions": load_fixture("comeet_upwind.json"),
    })
    state: dict = {}
    jobs = ComeetCollector(UPWIND, http, state).list_jobs()
    assert state["token"] == "ABCDEF0123456789ABCDEF012345"
    assert len(jobs) == 3
    loc = LocationFilter(settings.location)
    assert [loc.passes(j) for j in jobs] == [True, True, False]   # 2x Israel, 1x US (Remote)
    assert "Requirements" in (jobs[0].description or "")
    # Second run uses the cached token: no page fetch.
    http.calls.clear()
    ComeetCollector(UPWIND, http, state).list_jobs()
    assert not any("comeet.com/jobs" in c for c in http.calls)


def test_comeet_refreshes_token_when_api_rejects_it():
    request = httpx.Request("GET", "https://www.comeet.co/x")
    rejected = httpx.HTTPStatusError("401", request=request, response=httpx.Response(401, request=request))

    class RotatingHttp(FakeHttp):
        def get_json(self, url, **kwargs):
            self.calls.append(url)
            if kwargs["params"]["token"] == "OLD":
                raise rejected
            return load_fixture("comeet_upwind.json")

    http = RotatingHttp({"comeet.com/jobs/upwind": load_fixture("comeet_page_snippet.html")})
    state = {"token": "OLD"}
    jobs = ComeetCollector(UPWIND, http, state).list_jobs()
    assert len(jobs) == 3 and state["token"] == "ABCDEF0123456789ABCDEF012345"


def test_greenhouse_parses_unescapes_and_filters(settings):
    http = FakeHttp({"boards-api.greenhouse.io/v1/boards/payoneer/jobs": load_fixture("greenhouse_payoneer.json")})
    jobs = GreenhouseCollector(PAYONEER, http, {}).list_jobs()
    assert [j.job_key for j in jobs] == ["greenhouse:payoneer:101", "greenhouse:payoneer:102",
                                         "greenhouse:payoneer:103"]
    il, us_remote, london = jobs
    assert il.title == "Backend Engineer"
    assert "Requirements" in il.description and "Python" in il.description
    assert "&lt;" not in il.description
    assert us_remote.is_remote and us_remote.workplace_type == "remote"
    loc = LocationFilter(settings.location)
    assert [loc.passes(j) for j in jobs] == [True, False, False]


class PagedHttp(FakeHttp):
    """Eightfold: route by the 'start' query param and the endpoint."""

    def get_json(self, url, **kwargs):
        self.calls.append(url)
        params = kwargs.get("params") or {}
        if url.endswith("/position_details"):
            assert params["position_id"] == "1"
            return load_fixture("eightfold_amdocs_details.json")
        assert url == "https://jobs.amdocs.com/api/pcsx/search" and params["location"] == "Israel"
        return load_fixture("eightfold_amdocs_p1.json" if params["start"] == 0 else "eightfold_amdocs_p2.json")


def test_eightfold_pages_filters_and_fetches_details(settings):
    http = PagedHttp({})
    col = EightfoldCollector(AMDOCS, http, {})
    jobs = col.list_jobs()
    assert len(jobs) == 3 and len(http.calls) == 2          # two pages, stops at data.count
    il, cyprus, remote = jobs
    assert il.job_key == "eightfold:amdocs:1" and il.country == "IL" and il.workplace_type == "hybrid"
    assert il.url == "https://jobs.amdocs.com/careers/job/1"
    assert cyprus.country == "CY"
    loc = LocationFilter(settings.location)
    assert [loc.passes(j) for j in jobs] == [True, False, True]   # plain "Remote" is kept
    col.fetch_details(il)
    assert "Who are we?" in il.description and "SQL" in il.description
