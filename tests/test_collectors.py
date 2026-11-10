import httpx
import pytest

from jobalert.collectors.ashby import AshbyCollector
from jobalert.collectors.comeet import ComeetCollector
from jobalert.collectors.eightfold import EightfoldCollector
from jobalert.collectors.greenhouse import GreenhouseCollector
from jobalert.collectors.lever import LeverCollector
from jobalert.collectors.mizrahi import MizrahiCollector
from jobalert.collectors.oracle_hcm import OracleHcmCollector
from jobalert.collectors.smartrecruiters import SmartRecruitersCollector
from jobalert.collectors.workday import WorkdayCollector
from jobalert.config import CompanyConfig
from jobalert.location import LocationFilter

from .conftest import FakeHttp, load_fixture

MONDAY = CompanyConfig("monday", "monday.com", "ashby", {"board": "monday.com"})
MOBILEYE = CompanyConfig("mobileye", "Mobileye", "lever", {"site": "mobileye", "region": "eu"})
PAYONEER = CompanyConfig("payoneer", "Payoneer", "greenhouse", {"board": "payoneer"})
AMDOCS = CompanyConfig("amdocs", "Amdocs", "eightfold",
                       {"host": "jobs.amdocs.com", "domain": "amdocs.com", "location": "Israel"})
PANW = CompanyConfig("paloalto", "Palo Alto Networks", "workday", {
    "host": "paloaltonetworks.wd5.myworkdayjobs.com", "tenant": "paloaltonetworks",
    "site": "panwexternalcareers", "country": "IL", "facets": {"locations": ["abc"]}})
DISCOUNT = CompanyConfig("discount", "Bank Discount", "oracle_hcm",
                         {"host": "ehsb.fa.em2.oraclecloud.com", "site_number": "CX_3001"})
MIZRAHI = CompanyConfig("mizrahi", "Mizrahi-Tefahot", "mizrahi", {})
WIX = CompanyConfig("wix", "Wix", "smartrecruiters", {"company_id": "Wix2", "country": "il"})
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


class WorkdayHttp(FakeHttp):
    def post_json(self, url, payload, **kwargs):
        self.calls.append(url)
        self.posts = getattr(self, "posts", []) + [payload]
        return load_fixture("workday_panw_p1.json" if payload["offset"] == 0 else "workday_panw_p2.json")


def test_workday_pages_uses_facets_and_fetches_details(settings, monkeypatch):
    monkeypatch.setattr("jobalert.collectors.workday.PAGE_SIZE", 2)
    http = WorkdayHttp({"/wday/cxs/paloaltonetworks/panwexternalcareers/job/": load_fixture("workday_panw_details.json")})
    col = WorkdayCollector(PANW, http, {})
    jobs = col.list_jobs()
    # total comes from the first page only (later pages report 0)
    assert [j.job_key for j in jobs] == ["workday:paloalto:JR-1", "workday:paloalto:JR-2", "workday:paloalto:JR-3"]
    assert all(p["appliedFacets"] == {"locations": ["abc"]} for p in http.posts)
    assert jobs[0].url == ("https://paloaltonetworks.wd5.myworkdayjobs.com/panwexternalcareers"
                           "/job/Office---Israel---Tel-Aviv/Senior-Backend-Engineer--Cortex-_JR-1")
    loc = LocationFilter(settings.location)
    assert all(loc.passes(j) for j in jobs)        # "2 Locations" passes thanks to country: IL
    col.fetch_details(jobs[0])
    assert "Your Impact" in jobs[0].description and "Go" in jobs[0].description


def test_oracle_hcm_parses_and_fetches_details(settings):
    http = FakeHttp({
        "recruitingCEJobRequisitionDetails": load_fixture("oracle_discount_details.json"),
        "recruitingCEJobRequisitions": load_fixture("oracle_discount.json"),
    })
    col = OracleHcmCollector(DISCOUNT, http, {})
    jobs = col.list_jobs()
    assert len(http.calls) == 1                      # total 2 < page size: one page
    assert "siteNumber=CX_3001,limit=25,offset=0" in http.calls[0]
    teller, analyst = jobs
    assert teller.job_key == "oracle_hcm:discount:5794" and teller.country == "IL"
    assert teller.url.endswith("/hcmUI/CandidateExperience/he/sites/CX_3001/job/5794")
    assert analyst.all_locations == ["תל אביב, ישראל", "ירושלים, ישראל"]
    loc = LocationFilter(settings.location)
    assert all(loc.passes(j) for j in jobs)
    col.fetch_details(teller)
    assert 'Id="5794"' in http.calls[-1]
    assert teller.description.startswith("ניהול הקשר עם הלקוחות")
    assert "תואר ראשון" in teller.description


def test_mizrahi_parses_page_and_fetches_job_page(settings):
    http = FakeHttp({
        "open-jobs/java-developer/": load_fixture("mizrahi_job_page_snippet.html"),
        "open-jobs/": load_fixture("mizrahi_page_snippet.html"),
    })
    col = MizrahiCollector(MIZRAHI, http, {})
    jobs = col.list_jobs()
    # The site's own ids repeat (JB-11 twice), so the link slug is the id.
    assert [j.job_key for j in jobs] == ["mizrahi:mizrahi:lodseller", "mizrahi:mizrahi:java-developer"]
    assert jobs[1].title == "מפתח/ת Java" and jobs[1].location == "מרכז"
    assert jobs[1].url.endswith("/career/open-jobs/java-developer/")
    loc = LocationFilter(settings.location)
    assert all(loc.passes(j) for j in jobs)
    col.fetch_details(jobs[1])
    assert "Java, Spring" in jobs[1].description


def test_mizrahi_fails_loudly_when_layout_changes():
    http = FakeHttp({"open-jobs/": "<html><body>new design</body></html>"})
    with pytest.raises(RuntimeError):
        MizrahiCollector(MIZRAHI, http, {}).list_jobs()


def test_smartrecruiters_parses_and_fetches_details(settings):
    http = FakeHttp({
        "postings/111": load_fixture("smartrecruiters_wix_details.json"),
        "companies/Wix2/postings": load_fixture("smartrecruiters_wix.json"),
    })
    col = SmartRecruitersCollector(WIX, http, {})
    jobs = col.list_jobs()
    assert len(http.calls) == 1                      # totalFound 2 < page size
    dev, care = jobs
    assert dev.job_key == "smartrecruiters:wix:111" and dev.title == "Backend Developer"
    assert dev.country == "IL" and dev.workplace_type == "hybrid" and care.workplace_type == "onsite"
    loc = LocationFilter(settings.location)
    assert loc.passes(dev) and not loc.passes(care)   # Tel Aviv kept, Be'er Sheva outside the area
    col.fetch_details(dev)
    assert "Build APIs" in dev.description and "Node.js" in dev.description
    assert "About Wix" not in dev.description        # boilerplate company text is skipped
    assert dev.url == "https://jobs.smartrecruiters.com/Wix2/111"   # stable, same as in the listing


def test_only_center_and_jerusalem(settings):
    from jobalert.models import Job
    loc = LocationFilter(settings.location)

    def job(location, all_locations=()):
        return Job("x", "c", "C", "1", "t", "u", location=location, country="IL", all_locations=list(all_locations))

    assert not loc.passes(job("אילת, דרום, ישראל", ["אילת, דרום, ישראל"]))
    assert not loc.passes(job("IL-Haifa", ["IL-Haifa", "Haifa"]))
    assert not loc.passes(job("ישראל", ["ישראל", "נשר, חיפה, ישראל"]))      # generic "Israel" ignored
    assert not loc.passes(job("Israel, Yokneam"))
    assert not loc.passes(job("צפון", ["צפון"]))
    assert loc.passes(job("Tel Aviv", ["Tel Aviv", "Israel"]))
    assert loc.passes(job("ישראל", ["ישראל", "ירושלים, ישראל"]))
    assert loc.passes(job("Israel"))                                       # unknown area: keep
    assert loc.passes(job("Herzliya", ["Haifa", "Herzliya"]))              # one location in the area is enough
    assert loc.passes(job("צפון תל אביב"))
    assert loc.passes(job("רמת השרון, דן , ישראל"))
    assert loc.passes(job("Jerusalem District, Israel"))
    assert loc.excluded(job("Beer Sheva")) and not loc.excluded(job("רחובות"))


def test_ashdod_is_in_the_area(settings):
    from jobalert.models import Job
    loc = LocationFilter(settings.location)
    for place in ("Ashdod", "אשדוד, דרום, ישראל"):
        assert loc.passes(Job("x", "c", "C", "1", "t", "u", location=place, country="IL", all_locations=[place]))
