"""Turn job-alert emails into job records.

Each parser returns [] for an email that isn't a job list, and raises ParseError when the email
clearly lists jobs but none could be read (the email layout probably changed).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from bs4 import BeautifulSoup, Tag


class ParseError(Exception):
    pass


@dataclass
class EmailJob:
    external_id: str | None      # the source's stable job id, when the email has one
    title: str
    company: str
    location: str | None
    url: str
    posted_at: datetime | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


# ------------------------------------------------------------------ LinkedIn
# Uses the email's text/plain part. Each job is a block of lines ending in "View job: <url>":
#   <title> / <company> / <location> / optional notes ("1 connection", "Apply with resume & profile")

_LI_VIEW = re.compile(r"^\s*View job:\s*(\S+)", re.M)
_LI_JOB_ID = re.compile(r"/jobs/view/(\d+)")
_LI_SEPARATOR = re.compile(r"^-{5,}$")
_LI_HEADER = re.compile(r"^(your job alert for\b|\d+\+? new jobs? match)", re.I)
_LI_NOTE = re.compile(
    r"^(\d+ (connections?|school alum(ni|nus|na)?|company alum(ni|nus|na)?)\b"
    r"|this company is actively hiring|apply with resume|easy apply|actively recruiting"
    r"|be an early applicant|promoted)",
    re.I,
)


def parse_linkedin(text: str) -> list[EmailJob]:
    jobs: list[EmailJob] = []
    seen: set[str] = set()
    start = 0
    matches = list(_LI_VIEW.finditer(text))
    for m in matches:
        block = text[start:m.start()]
        start = m.end()
        lines = [ln.strip() for ln in block.splitlines()]
        last_sep = max((i for i, ln in enumerate(lines) if _LI_SEPARATOR.match(ln)), default=-1)
        lines = [ln for ln in lines[last_sep + 1:] if ln and not _LI_HEADER.match(ln)]
        job_id = _LI_JOB_ID.search(m.group(1))
        if not job_id or len(lines) < 2 or job_id.group(1) in seen:
            continue
        seen.add(job_id.group(1))
        location = lines[2] if len(lines) > 2 and not _LI_NOTE.match(lines[2]) else None
        jobs.append(EmailJob(
            external_id=job_id.group(1),
            title=_clean(lines[0]),
            company=_clean(lines[1]),
            location=location,
            url=f"https://www.linkedin.com/jobs/view/{job_id.group(1)}/",
        ))
    if not jobs and (matches or "/jobs/view/" in text):
        raise ParseError("LinkedIn email has job links but no job could be read")
    return jobs


# ------------------------------------------------------------------ Jobify
# HTML only. Every job card has exactly one short link "jobify360.co.il/n/<code>".
# Weekly layout (since late Oct 2026):  [NN%] [התאמה] [● badges] <title> [NN%] "<company> · 📍 <city>"
# Legacy layout:                        <title> <company> <dd/mm/yyyy> <city>
# "Opportunity" cards (roles people with a similar background moved to) have only a title: skipped.

_JOBIFY_LINK = re.compile(r"jobify360\.co\.il/n/([A-Za-z0-9]+)")
_DATE = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")
_PERCENT = re.compile(r"^(\d{1,3})%$")
_NO_LOCATION = {"מיקום לא צוין", ""}


def _card(link: Tag) -> Tag:
    """The largest ancestor that still contains only this one job link."""
    card = link
    while card.parent is not None and len(card.parent.find_all("a", href=_JOBIFY_LINK)) == 1:
        card = card.parent
    return card


def _is_badge(s: str) -> bool:
    return "●" in s or s == "התאמה" or bool(_PERCENT.match(s)) or s.startswith("להגשה")


def _jobify_card(parts: list[str]) -> tuple[str, str, str | None, datetime | None] | None:
    # Weekly layout: "<company> · 📍 <city>"
    for i, s in enumerate(parts):
        if " · " in s and not _is_badge(s) and not s.startswith("₪"):
            company, _, city = s.partition(" · ")
            title = next((p for p in reversed(parts[:i]) if not _is_badge(p)), None)
            if title:
                city = city.replace("📍", "").strip()
                return title, company.strip(), (None if city in _NO_LOCATION else city), None
    # Legacy layout: title, company, date, city
    for i, s in enumerate(parts):
        d = _DATE.match(s)
        if d and i >= 2:
            city = parts[i + 1] if i + 1 < len(parts) else ""
            posted = datetime(int(d.group(3)), int(d.group(2)), int(d.group(1)))
            return parts[i - 2], parts[i - 1], (None if city in _NO_LOCATION else city), posted
    return None


def parse_jobify(html: str) -> list[EmailJob]:
    soup = BeautifulSoup(html or "", "html.parser")
    links = soup.find_all("a", href=_JOBIFY_LINK)
    jobs: list[EmailJob] = []
    for link in links:
        code = _JOBIFY_LINK.search(link["href"]).group(1)  # type: ignore[union-attr]
        parts = [_clean(s) for s in _card(link).stripped_strings]
        parsed = _jobify_card([p for p in parts if p])
        if parsed is None:
            continue
        title, company, location, posted = parsed
        match = next((int(m.group(1)) for p in parts if (m := _PERCENT.match(p))), None)
        jobs.append(EmailJob(
            external_id=None,   # short links differ per email, so they can't identify a job
            title=title,
            company=company,
            location=location,
            url=f"https://jobify360.co.il/n/{code}",
            posted_at=posted,
            extra={"jobify_match": match} if match is not None else {},
        ))
    if links and not jobs:
        raise ParseError("Jobify email has job links but no job could be read")
    return jobs
