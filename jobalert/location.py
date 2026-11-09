"""Location filter: Israel, plus remote jobs that are open to Israel."""
from __future__ import annotations

from typing import Any

from .models import Job


class LocationFilter:
    def __init__(self, cfg: dict[str, Any]) -> None:
        self.countries = {c.upper() for c in cfg.get("countries", [])}
        self.israel_keywords = [k.lower() for k in cfg.get("israel_keywords", [])]
        self.remote_ok_keywords = [k.lower() for k in cfg.get("remote_ok_keywords", [])]

    def _texts(self, job: Job) -> list[str]:
        texts = [job.location or "", *job.all_locations]
        return [t.lower() for t in texts if t]

    def passes(self, job: Job) -> bool:
        countries = {c.upper() for c in [job.country or "", *job.all_countries] if c}
        if countries & self.countries:
            return True
        texts = self._texts(job)
        if any(k in t for t in texts for k in self.israel_keywords):
            return True
        if job.workplace_type == "remote" or job.is_remote:
            if any(k in t for t in texts for k in self.remote_ok_keywords):
                return True
            # Plain "Remote" with no region and no country: could be open to Israel, keep it.
            stripped = [t.replace("remote", "").strip(" -,()") for t in texts]
            if not countries and all(s == "" for s in stripped):
                return True
        return False
