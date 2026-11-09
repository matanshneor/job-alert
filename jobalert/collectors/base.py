"""Collector interface."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..config import CompanyConfig
from ..http import PoliteClient
from ..models import Job


class Collector(ABC):
    #: The ATS name; also the "source" of every job this collector returns.
    ats: str = ""
    #: True if list_jobs() returns every open job, so a missing job means it closed.
    complete_listing: bool = True

    def __init__(self, company: CompanyConfig, http: PoliteClient, state: dict[str, Any]) -> None:
        self.company = company
        self.http = http
        # Persistent per-source scratch space (stored in source_state.meta), e.g. cached tokens.
        self.state = state

    @abstractmethod
    def list_jobs(self) -> list[Job]:
        """Return all currently open jobs (any location). Raise on failure."""

    def fetch_details(self, job: Job) -> Job:
        """Fill in the description for a new job if the listing didn't include it."""
        return job

    def param(self, name: str) -> Any:
        try:
            return self.company.params[name]
        except KeyError:
            raise ValueError(
                f"companies.yaml: '{self.company.slug}' ({self.ats}) is missing params.{name}"
            ) from None

    def make_job(self, **kwargs: Any) -> Job:
        return Job(source=self.ats, company_slug=self.company.slug, company=self.company.name, **kwargs)
