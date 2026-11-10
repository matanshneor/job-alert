"""Email parsers on real (sanitized) LinkedIn and Jobify alert emails."""
from datetime import datetime

import pytest

from jobalert.gmail.parsers import ParseError, parse_jobify, parse_linkedin
from tests.conftest import load_fixture


def test_linkedin_digest_with_hebrew_titles_and_notes():
    jobs = parse_linkedin(load_fixture("linkedin_jerusalem.txt"))
    assert len(jobs) == 6
    first = jobs[0]
    assert (first.title, first.company, first.location) == (
        "Data Analytics Project Manager", "Mobileye", "Jerusalem District, Israel")
    assert first.external_id == "4487632697"
    assert first.url == "https://www.linkedin.com/jobs/view/4487632697/"   # no tracking params
    hebrew = jobs[2]
    assert hebrew.title == "דרוש.ה תומכ/ת מערכות מידע ו- IT"
    assert (hebrew.company, hebrew.location) == ("Orr Shalom", "Beit Shemesh")
    # header lines ("Your job alert for ...", "7 new jobs match ...") never become jobs
    assert all(not j.title.startswith(("Your job alert", "7 new")) for j in jobs)


def test_linkedin_second_digest():
    jobs = parse_linkedin(load_fixture("linkedin_center.txt"))
    assert [j.company for j in jobs] == ["comblack", "abra", "abra", "LabOS", "comblack", "Priority Software"]
    assert jobs[5].title == "CRM Product Manager"        # leading space trimmed
    assert jobs[3].location == "Petah Tikva"


def test_linkedin_missing_location_is_not_a_note():
    text = "Some Role\nAcme\n\nThis company is actively hiring\nView job: https://www.linkedin.com/comm/jobs/view/123/?x=1\n"
    [job] = parse_linkedin(text)
    assert (job.title, job.company, job.location) == ("Some Role", "Acme", None)


def test_linkedin_non_alert_email_and_broken_layout():
    assert parse_linkedin("Your job alert has been created.") == []
    with pytest.raises(ParseError):
        parse_linkedin("View job: https://www.linkedin.com/comm/jobs/view/123/\n")


def test_jobify_weekly_layout_skips_opportunities():
    jobs = parse_jobify(load_fixture("jobify_weekly.html"))
    assert len(jobs) == 7          # the 5 "opportunity" cards (title only) are skipped
    top = jobs[0]
    assert (top.title, top.company, top.location) == ("AI Builder", "matrix (מוצרים)", "תל אביב - יפו")
    assert top.extra == {"jobify_match": 100}
    assert top.url == "https://jobify360.co.il/n/4mpEIQ"
    assert top.external_id is None   # short links differ per email
    assert (jobs[1].title, jobs[1].company, jobs[1].location) == (
        "Data Analyst במחלקת אסטרטגיה", "G-Stat", "רחובות")


def test_jobify_first_weekly_layout_percent_after_title():
    jobs = parse_jobify(load_fixture("jobify_weekly_v1.html"))
    assert len(jobs) == 6
    assert (jobs[0].title, jobs[0].company, jobs[0].location) == ("מפתח.ת תוכנה רמה א' במשטרה", "Octally", "ירושלים")
    assert jobs[3].company == "חברה בתחום הייטק / חומרה / תוכנה / סייבר"   # salary line isn't the company


def test_jobify_legacy_layout_with_dates():
    jobs = parse_jobify(load_fixture("jobify_legacy.html"))
    assert len(jobs) == 12
    first = jobs[0]
    assert (first.title, first.company) == ("רכז /ת תפעול למפעל קייטרינג - איש שטח", "humanlink")
    assert first.location is None                    # "מיקום לא צוין"
    assert first.posted_at == datetime(2026, 10, 8)
    assert jobs[1].location == "רמת גן"


def test_jobify_broken_layout_raises():
    html = '<table><tr><td><a href="https://jobify360.co.il/n/abc">להגשה</a></td></tr></table>'
    with pytest.raises(ParseError):
        parse_jobify(html)
    assert parse_jobify("<p>מיליון הגשות. וואו.</p>") == []
