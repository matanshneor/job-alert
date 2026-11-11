"""Finding an email job's description on the company's own board."""
import httpx

from jobalert.config import CompanyConfig
from jobalert.enrich import STATE_PREFIX, BoardFinder, slug_candidates, titles_match
from jobalert.location import LocationFilter

from .conftest import load_fixture


def test_slug_candidates():
    assert slug_candidates("Priority Software Ltd.") == ["priority", "prioritysoftware", "priority-software"]
    assert slug_candidates("abra")[0] == "abra"
    assert slug_candidates("Cato Networks")[:2] == ["catonetworks", "cato-networks"]
    assert slug_candidates("בנק הפועלים") == []          # Hebrew names can't be a slug


def test_titles_match():
    assert titles_match("Senior Backend Engineer (Hybrid)", "Senior Backend Engineer")
    assert titles_match("Data Analyst - Payments", "Data Analyst – Payments")
    assert not titles_match("Data Analyst", "Senior Data Engineer")


class FakeDb:
    def __init__(self):
        self.state = {}

    def get_source_state(self, key):
        return self.state.get(key)

    def record_source_success(self, key, count, meta):
        self.state[key] = {"meta": meta}


class FakeHttp:
    """Greenhouse board 'payoneer' exists; every other probe is a 404."""

    def __init__(self):
        self.calls = []

    def get_json(self, url, **kwargs):
        self.calls.append(url)
        if "boards-api.greenhouse.io/v1/boards/payoneer/jobs" in url:
            return load_fixture("greenhouse_payoneer.json")
        request = httpx.Request("GET", url)
        raise httpx.HTTPStatusError("404", request=request, response=httpx.Response(404, request=request))


def finder(db, http, settings, companies=()):
    return BoardFinder(db, http, list(companies), {}, LocationFilter(settings.location),
                       {"max_new_companies_per_run": 5, "recheck_days": 14})


def row(company, slug, title):
    return {"id": 1, "company": company, "company_slug": slug, "title": title, "location": "Center"}


def test_probes_board_finds_job_and_caches_board(settings):
    db, http = FakeDb(), FakeHttp()
    found = finder(db, http, settings).find(row("Payoneer", "payoneer", "Backend Developer"))
    assert found is None                                    # title not on the board
    found = finder(db, http, settings).find(row("Payoneer", "payoneer", "Backend Engineer"))
    assert found and "Python" in found.description and found.ats == "greenhouse"
    assert db.state[STATE_PREFIX + "payoneer"]["meta"]["board"] == {"ats": "greenhouse",
                                                                   "params": {"board": "payoneer"}}
    http.calls.clear()
    finder(db, http, settings).find(row("Payoneer", "payoneer", "Backend Engineer"))
    assert not any("ashbyhq" in c or "lever" in c for c in http.calls)   # cached: no new probing


def test_job_outside_israel_on_probed_board_is_not_used(settings):
    # "Sales Manager" exists on the board but only in London: probably not the LinkedIn job.
    db, http = FakeDb(), FakeHttp()
    assert finder(db, http, settings).find(row("Payoneer", "payoneer", "Sales Manager")) is None


def test_company_without_board_is_cached_and_not_probed_again(settings):
    db, http = FakeDb(), FakeHttp()
    assert finder(db, http, settings).find(row("abra", "abra", "Financial Systems Analyst")) is None
    assert db.state[STATE_PREFIX + "abra"]["meta"]["board"] is None
    probes = len(http.calls)
    assert probes >= 4                                      # greenhouse, ashby, lever, lever eu
    finder(db, http, settings).find(row("abra", "abra", "Another Job"))
    assert len(http.calls) == probes                        # cached "no board" for recheck_days


def test_probe_budget_limits_new_companies_per_run(settings):
    db, http = FakeDb(), FakeHttp()
    f = BoardFinder(db, http, [], {}, LocationFilter(settings.location),
                    {"max_new_companies_per_run": 1})
    f.find(row("abra", "abra", "X"))
    calls = len(http.calls)
    assert f.find(row("comblack", "comblack", "Y")) is None
    assert len(http.calls) == calls and STATE_PREFIX + "comblack" not in db.state   # waits for next run


def test_tracked_company_uses_its_configured_board(settings):
    db, http = FakeDb(), FakeHttp()
    tracked = CompanyConfig("pay", "Payoneer", "greenhouse", {"board": "payoneer"})
    found = finder(db, http, settings, [tracked]).find(row("Payoneer Inc", "pay", "Backend Engineer"))
    assert found and len(http.calls) == 1 and not db.state             # no probing, nothing cached
