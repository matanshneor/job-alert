"""Location filter: Israel (Center + Jerusalem only), plus remote jobs that are open to Israel."""
from __future__ import annotations

import re
from typing import Any

from .models import Job


class LocationFilter:
    def __init__(self, cfg: dict[str, Any]) -> None:
        self.countries = {c.upper() for c in cfg.get("countries", [])}
        self.israel_keywords = [k.lower() for k in cfg.get("israel_keywords", [])]
        self.remote_ok_keywords = [k.lower() for k in cfg.get("remote_ok_keywords", [])]
        self.exclude_keywords = [k.lower() for k in cfg.get("exclude_keywords", [])]

    def _texts(self, job: Job) -> list[str]:
        texts = [job.location or "", *job.all_locations]
        return [t.lower() for t in texts if t]

    def excluded(self, job: Job) -> bool:
        """True when every specific location of the job is outside the wanted area.

        Generic texts ("Israel", "ישראל", "IL") say nothing about the area and are ignored,
        so a job listing only "Israel" is kept, and a job in Haifa *or* Tel Aviv is kept.
        """
        specific = [t for t in self._texts(job) if not _is_generic(t)]
        if not specific or not self.exclude_keywords:
            return False
        return all(self._text_excluded(t) for t in specific)

    def _text_excluded(self, text: str) -> bool:
        # A named place (e.g. "haifa") excludes the text. A region word ("=דרום") excludes it only
        # when no other place is named: "אשדוד, דרום, ישראל" is kept if Ashdod isn't excluded.
        places = [k for k in self.exclude_keywords if not k.startswith("=")]
        if any(_matches(k, text) for k in places):
            return True
        regions = {k[1:] for k in self.exclude_keywords if k.startswith("=")}
        parts = [p.strip() for p in re.split(r"[,|/;]", text) if p.strip() and not _is_generic(p)]
        return bool(parts) and all(p in regions for p in parts)

    def passes(self, job: Job) -> bool:
        if self.excluded(job):
            return False
        return self._in_israel_or_open_remote(job)

    def _in_israel_or_open_remote(self, job: Job) -> bool:
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


_GENERIC = re.compile(r"^(israel|ישראל|il|remote|hybrid|\d+ locations?)$")


def _is_generic(text: str) -> bool:
    t = re.sub(r"[\s,()\-–]+", " ", text).strip()
    return not t or bool(_GENERIC.match(t))


def _matches(keyword: str, text: str) -> bool:
    """Whole-word match. A keyword starting with "=" must equal a whole comma-separated part."""
    if keyword.startswith("="):
        return keyword[1:] in {part.strip() for part in re.split(r"[,|/;]", text)}
    return re.search(rf"(?<!\w){re.escape(keyword)}(?!\w)", text) is not None
