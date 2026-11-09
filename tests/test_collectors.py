import httpx

from jobalert.collectors.ashby import AshbyCollector
from jobalert.collectors.comeet import ComeetCollector
from jobalert.collectors.lever import LeverCollector
from jobalert.config import CompanyConfig
from jobalert.location import LocationFilter

from .conftest import FakeHttp, load_fixture

MONDAY = CompanyConfig("monday", "monday.com", "ashby", {"board": "monday.com"})
MOBILEYE = CompanyConfig("mobileye", "Mobileye", "lever", {"site": "mobileye", "region": "eu"})
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
