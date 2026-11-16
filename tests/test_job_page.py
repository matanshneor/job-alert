"""Reading an email job's description from its own page (real, trimmed pages)."""
from jobalert.job_page import MIN_LENGTH, extract_description, fetch_description, supported
from tests.conftest import load_fixture

LI = "https://www.linkedin.com/jobs/view/4491262314/"
JF = "https://jobify360.co.il/n/mVodg"
AJ = "https://www.alljobs.co.il/Search/UploadSingle.aspx?JobID=8900246"


def test_supported_links():
    assert supported(LI) and supported(JF)
    assert not supported(AJ)                                  # bot wall: not tried
    assert not supported("https://trailer.web-view.net/Links/c.htm?p=abc")   # digest click tracker
    assert not supported("https://example.com/job/1")


def test_linkedin_page():
    text = extract_description(LI, load_fixture("jobpage_linkedin.html"))
    assert " ".join(text.split()).startswith("We are looking for the next Data Analyst")


def test_jobify_page_uses_full_text_not_the_short_json():
    text = extract_description(JF, load_fixture("jobpage_jobify.html"))
    assert "Key Responsibilities" in text and len(text) > 500


def test_wall_or_teaser_is_not_a_description():
    assert extract_description(LI, "<html><body>Sign in to view</body></html>") is None
    short = '<div class="description__text">' + "x" * (MIN_LENGTH - 1) + "</div>"
    assert extract_description(LI, short) is None


class FakeHttp:
    def __init__(self, page=None, error=None):
        self.page, self.error, self.urls = page, error, []

    def get_text(self, url):
        self.urls.append(url)
        if self.error:
            raise self.error
        return self.page


def test_fetch_uses_linkedin_guest_url_and_never_raises():
    http = FakeHttp(load_fixture("jobpage_linkedin.html"))
    assert fetch_description(http, LI)
    assert http.urls == ["https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/4491262314"]
    assert fetch_description(FakeHttp(error=RuntimeError("HTTP 999")), LI) is None
    http = FakeHttp("x")
    assert fetch_description(http, "https://trailer.web-view.net/Links/c.htm?p=abc") is None
    assert http.urls == []                                   # unsupported links are not opened
