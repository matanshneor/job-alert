"""Normalized job record shared by all collectors."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class Job:
    source: str                      # ATS / source type, e.g. "ashby"
    company_slug: str                # from companies.yaml
    company: str                     # display name
    external_id: str                 # the source's own job id
    title: str
    url: str
    location: str | None = None      # main location text, e.g. "Tel Aviv"
    country: str | None = None       # ISO-2, upper case, e.g. "IL"
    is_remote: bool | None = None
    workplace_type: str | None = None  # "remote" | "hybrid" | "onsite" | None
    # Every location text / country the job lists (primary + secondary), used by the location filter.
    all_locations: list[str] = field(default_factory=list)
    all_countries: list[str] = field(default_factory=list)
    description: str | None = None
    posted_at: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def job_key(self) -> str:
        return f"{self.source}:{self.company_slug}:{self.external_id}"
