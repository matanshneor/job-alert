"""Find the description of an email job (LinkedIn / Jobify) on the company's own job board.

Emails give only title, company and location. Many companies post the same job on their own
board, where the full description is. For each email job:

1. Find the company's board:
   - a company from companies.yaml (matched by alias) uses its configured board;
   - otherwise probe the public ATS APIs that need only a name (Greenhouse, Ashby, Lever,
     Lever EU) with a few spellings of the company name. The result, found or not, is cached
     in source_state ("board:<company>") so each company is probed at most once per
     `recheck_days`.
2. Look for a job with the same title there (normalized; near-identical titles also match),
   preferring one in the alert area. A wrong board (another company with the same name)
   almost never has an open job with the same title in Israel, so the title match also
   guards against that.
3. Return its description (fetched through the board's collector).

If nothing is found, the caller scores the job from its title alone.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from typing import Any

import httpx

from .collectors.registry import get_collector_class
from .config import CompanyConfig
from .dedup import _basic, normalize_title
from .location import LocationFilter
from .models import Job

log = logging.getLogger(__name__)

STATE_PREFIX = "board:"
TITLE_MATCH_RATIO = 0.9
# Words that are often part of the company's name but not of its board slug.
LEGAL_WORDS = {"ltd", "inc", "llc", "corp", "corporation", "co", "the"}
DESCRIPTIVE_WORDS = {"group", "israel", "il", "technologies", "technology", "tech", "software",
                     "systems", "solutions"}

# (ats, params builder, probe URL builder). Only ATSs whose public API is addressed by name.
PROBES = [
    ("greenhouse", lambda s: {"board": s}, lambda s: f"https://boards-api.greenhouse.io/v1/boards/{s}/jobs"),
    ("ashby", lambda s: {"board": s}, lambda s: f"https://api.ashbyhq.com/posting-api/job-board/{s}"),
    ("lever", lambda s: {"site": s}, lambda s: f"https://api.lever.co/v0/postings/{s}?mode=json&limit=1"),
    ("lever", lambda s: {"site": s, "region": "eu"},
     lambda s: f"https://api.eu.lever.co/v0/postings/{s}?mode=json&limit=1"),
]


@dataclass
class Found:
    description: str
    board_url: str
    ats: str


def slug_candidates(company: str) -> list[str]:
    """'Priority Software Ltd.' -> ['priority', 'prioritysoftware', 'priority-software']."""
    words = _basic(company).split()
    if not words or not all(w.isascii() for w in words):
        return []                      # Hebrew-only names can't be an ATS slug
    named = [w for w in words if w not in LEGAL_WORDS] or words            # "priority software"
    core = [w for w in named if w not in DESCRIPTIVE_WORDS] or named       # "priority"
    candidates = ["".join(core), "-".join(core), "".join(named), "-".join(named)]
    if len(core) > 1 and len(core[0]) >= 4:
        candidates.append(core[0])
    seen: list[str] = []
    for c in candidates:
        if len(c) >= 3 and c not in seen:
            seen.append(c)
    return seen


def titles_match(a: str, b: str) -> bool:
    na, nb = normalize_title(a), normalize_title(b)
    if not na or not nb:
        return False
    return na == nb or SequenceMatcher(None, na, nb).ratio() >= TITLE_MATCH_RATIO


class BoardFinder:
    def __init__(self, db: Any, http: Any, companies: list[CompanyConfig], alias_map: dict[str, str],
                 loc: LocationFilter, cfg: dict[str, Any]) -> None:
        self.db = db
        self.http = http
        self.by_slug = {c.slug: c for c in companies if c.enabled}
        self.alias_map = alias_map
        self.loc = loc
        self.recheck = timedelta(days=int(cfg.get("recheck_days", 14)))
        self.probe_budget = int(cfg.get("max_new_companies_per_run", 15))
        self._listings: dict[str, list[Job]] = {}   # board key -> jobs, cached for this run

    # ---------------------------------------------------------------- board lookup

    def board_for(self, company: str, company_slug: str) -> CompanyConfig | None:
        if company_slug in self.by_slug:
            return self.by_slug[company_slug]
        key = STATE_PREFIX + company_slug
        meta = (self.db.get_source_state(key) or {}).get("meta") or {}
        checked = meta.get("checked_at")
        fresh = checked and datetime.now(timezone.utc) - datetime.fromisoformat(checked) < self.recheck
        if meta.get("board"):
            b = meta["board"]
            return CompanyConfig(company_slug, company, b["ats"], b["params"])
        if fresh:
            return None                      # probed recently, no board found
        if self.probe_budget <= 0:
            return None                      # try again on a later run
        self.probe_budget -= 1
        board = self._probe(company)
        self.db.record_source_success(key, 1 if board else 0, {
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "board": {"ats": board[0], "params": board[1]} if board else None,
        })
        if board:
            log.info("Board found for %s: %s %s", company, board[0], board[1])
            return CompanyConfig(company_slug, company, board[0], board[1])
        return None

    def _probe(self, company: str) -> tuple[str, dict[str, Any]] | None:
        for slug in slug_candidates(company):
            for ats, params, url in PROBES:
                try:
                    data = self.http.get_json(url(slug))
                except (httpx.HTTPError, ValueError):   # 404 = no such board
                    continue
                jobs = data if isinstance(data, list) else (data or {}).get("jobs")
                if jobs:
                    return ats, params(slug)
        return None

    # ---------------------------------------------------------------- job lookup

    def _listing(self, board: CompanyConfig) -> list[Job]:
        key = f"{board.ats}:{sorted(board.params.items())}"
        if key not in self._listings:
            collector = get_collector_class(board.ats)(board, self.http, {})
            self._listings[key] = collector.list_jobs()
        return self._listings[key]

    def find(self, row: dict[str, Any]) -> Found | None:
        board = self.board_for(row["company"], row["company_slug"])
        if board is None or get_collector_class(board.ats) is None:
            return None
        try:
            listing = self._listing(board)
        except Exception as exc:  # noqa: BLE001 - a broken board just means "not found"
            log.warning("Could not read board of %s (%s): %s", row["company"], board.ats, exc)
            return None
        matches = [j for j in listing if titles_match(j.title, row["title"])]
        if not matches:
            return None
        in_area = [j for j in matches if self.loc.passes(j) and not self.loc.excluded(j)]
        job = (in_area or matches)[0]
        if not in_area and board.slug not in self.by_slug:
            return None                      # a probed board with no Israeli match: likely another company
        if not job.description:
            get_collector_class(board.ats)(board, self.http, {}).fetch_details(job)
        if not job.description:
            return None
        return Found(job.description, job.url, board.ats)
