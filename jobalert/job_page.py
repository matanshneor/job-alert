"""Read the description of an email job from its own job page (the link in the email).

Supported links:
- LinkedIn   /jobs/view/<id>     -> the public "guest" job posting (no login)
- Jobify     jobify360.co.il/n/  -> redirects to the job page; text in .content-text-main
Other links are not opened. AllJobs pages sit behind a bot wall (Radware) that blocks automated
reads after the first request, so they are not tried; the digest's tracking links would also
count as a click in the user's account.

Any failure (blocked, login wall, layout change) returns None: the caller then falls back to
the company's board lookup and, failing that, to a title-only score.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from bs4 import BeautifulSoup

from .text import html_to_text

log = logging.getLogger(__name__)

MIN_LENGTH = 300   # shorter text is a teaser or a login/bot wall, not a description

_LINKEDIN_ID = re.compile(r"linkedin\.com/(?:comm/)?jobs/view/(\d+)")
_JOBIFY = re.compile(r"^https://(www\.)?jobify360\.co\.il/")


def supported(url: str) -> bool:
    return bool(_LINKEDIN_ID.search(url) or _JOBIFY.match(url))


def _ld_json_description(soup: BeautifulSoup) -> str | None:
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except ValueError:
            continue
        for item in data if isinstance(data, list) else [data]:
            if isinstance(item, dict) and item.get("@type") == "JobPosting" and item.get("description"):
                return html_to_text(item["description"])
    return None


def _text_of(soup: BeautifulSoup, css: str) -> str | None:
    """Text of the longest element matching `css` (pages reuse classes for promo boxes)."""
    els = soup.select(css)
    if not els:
        return None
    return html_to_text(str(max(els, key=lambda e: len(e.get_text()))))


def extract_description(url: str, page_html: str) -> str | None:
    soup = BeautifulSoup(page_html or "", "html.parser")
    if _LINKEDIN_ID.search(url):
        text = _text_of(soup, ".show-more-less-html__markup") or _text_of(soup, ".description__text")
    else:
        text = _text_of(soup, ".content-text-main") or _ld_json_description(soup)
    if not text:
        return None
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text if len(text) >= MIN_LENGTH else None


def fetch_description(http: Any, url: str) -> str | None:
    """The job's description from its page, or None."""
    if not supported(url):
        return None
    m = _LINKEDIN_ID.search(url)
    page_url = f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{m.group(1)}" if m else url
    try:
        page = http.get_text(page_url)
    except Exception as exc:  # noqa: BLE001 - a blocked page just means "not found"
        log.info("Could not read job page %s: %s", page_url, exc)
        return None
    return extract_description(url, page)
