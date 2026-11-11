"""Phase 3: read job-alert emails (LinkedIn, Jobify) and store their jobs.

Email jobs differ from company-board jobs:
- only the area exclusion of the location filter (the alerts are already set to Israel, and
  Jobify writes city names in Hebrew, so the "is it in Israel" check is skipped),
- no description: the AI step looks the job up on the company's board (jobalert/enrich.py)
  and, if it isn't there, scores it from the title only,
- never closed (there is no listing to compare against),
- still deduplicated: the same job from a company board and from an email is alerted once.
"""
from __future__ import annotations

import hashlib
import html
import logging
from typing import Any, Callable, Protocol

from ..config import CompanyConfig, Settings, require_env
from ..dedup import build_alias_map, canonical_company, fingerprint
from ..location import LocationFilter
from ..models import Job
from ..text import html_to_text
from .imap import GmailReader, MailMessage
from .parsers import EmailJob, ParseError, parse_jobify, parse_linkedin

log = logging.getLogger(__name__)

LOGIN_KEY = "gmail"   # source_state key for login / connection health

PARSERS: dict[str, Callable[[MailMessage], list[EmailJob]]] = {
    "linkedin": lambda m: parse_linkedin(m.text or html_to_text(m.html)),
    "jobify": lambda m: parse_jobify(m.html),
}


class Reader(Protocol):
    def search(self, gmail_query: str) -> list[str]: ...
    def gmail_ids(self, uids: list[str]) -> dict[str, str]: ...
    def fetch(self, uid: str) -> MailMessage: ...
    def close(self) -> None: ...


class Notifier(Protocol):
    def send_message(self, text: str) -> int: ...


def to_job(source_key: str, ej: EmailJob, alias_map: dict[str, str]) -> tuple[Job, str]:
    slug = canonical_company(ej.company, alias_map)
    fp = fingerprint(slug, ej.title, ej.location)
    # Without a stable id from the source, the fingerprint identifies the job.
    external_id = ej.external_id or hashlib.sha1(fp.encode()).hexdigest()[:16]
    job = Job(
        source=source_key, company_slug=slug, company=ej.company, external_id=external_id,
        title=ej.title, url=ej.url, location=ej.location, posted_at=ej.posted_at,
        raw={"from_email": True, **ej.extra},
    )
    return job, fp


def _warn_on_threshold(
    db: Any, tg: Notifier, settings: Settings, key: str, name: str, error: str,
    meta: dict[str, Any] | None = None,
) -> None:
    if meta is None:
        meta = (db.get_source_state(key) or {}).get("meta") or {}
    failures = db.record_source_failure(key, error, meta)
    if failures == settings.closing["warn_after_failures"]:
        tg.send_message(
            f"⚠️ המקור <b>{html.escape(name)}</b> נכשל {failures} ריצות ברצף.\n"
            f"<code>{html.escape(error[:300])}</code>"
        )


def collect_email_source(
    reader: Reader, src: dict[str, Any], db: Any, tg: Notifier, settings: Settings,
    alias_map: dict[str, str],
) -> dict[str, Any]:
    key, name = src["key"], src["name"]
    parser = PARSERS[src["parser"]]
    loc = LocationFilter(settings.location)
    state = db.get_source_state(key) or {}
    meta = dict(state.get("meta") or {})
    # The first run stores what is already in the inbox without alerting (like company boards).
    first_time = not meta.get("baselined")

    query = f"{src['query']} newer_than:{int(settings.email.get('lookback_days', 3))}d"
    ids = reader.gmail_ids(reader.search(query))
    done = db.processed_email_ids(list(ids.values()))

    rows: list[tuple[Job, str]] = []
    processed: list[tuple[str, str, str, Any, int]] = []
    unreadable: list[str] = []
    for uid, gmail_id in ids.items():
        if gmail_id in done:
            continue
        msg = reader.fetch(uid)
        try:
            found = parser(msg)
        except ParseError as exc:
            log.warning("%s: %s (%s)", key, exc, msg.subject[:80])
            unreadable.append(msg.subject)
            continue
        jobs = [to_job(key, ej, alias_map) for ej in found]
        rows.extend((j, fp) for j, fp in jobs if not loc.excluded(j))
        processed.append((gmail_id, key, msg.subject[:300], msg.received_at, len(found)))

    new = db.upsert_jobs(rows, "baseline" if first_time else "pending")
    db.mark_emails_processed(processed)   # only after the jobs are saved
    meta["baselined"] = True

    stats: dict[str, Any] = {"name": name, "emails": len(processed), "jobs": len(rows), "new": new,
                             "baseline": first_time}
    if unreadable:
        # Unreadable emails aren't marked processed, so every run retries them and the failure
        # count keeps growing until the parser is fixed (or they leave the look-back window).
        stats["unreadable"] = len(unreadable)
        _warn_on_threshold(db, tg, settings, key, name,
                           f"could not read {len(unreadable)} email(s), layout may have changed: "
                           f"{unreadable[0][:120]}", meta)
    else:
        db.record_source_success(key, len(rows), meta)
    log.info("%s: %d new emails, %d jobs, %d new%s%s", key, len(processed), len(rows), new,
             " (baseline)" if first_time else "", f", {len(unreadable)} unreadable" if unreadable else "")
    return stats


def collect_emails(
    db: Any, tg: Notifier, settings: Settings, companies: list[CompanyConfig],
    reader_factory: Callable[[], Reader] | None = None,
) -> dict[str, dict[str, Any]]:
    cfg = settings.email
    if not cfg.get("enabled"):
        return {}
    alias_map = build_alias_map(companies)
    try:
        reader = (reader_factory or _default_reader)()
    except Exception as exc:  # noqa: BLE001 - email trouble must not stop the company boards
        log.exception("Gmail connection failed")
        _warn_on_threshold(db, tg, settings, LOGIN_KEY, "Gmail", f"{type(exc).__name__}: {exc}")
        return {LOGIN_KEY: {"error": str(exc)[:300]}}
    db.record_source_success(LOGIN_KEY, 0, {})

    stats: dict[str, dict[str, Any]] = {}
    try:
        for src in cfg.get("sources") or []:
            try:
                stats[src["key"]] = collect_email_source(reader, src, db, tg, settings, alias_map)
            except Exception as exc:  # noqa: BLE001 - one source must not stop the other
                log.exception("Email source %s failed", src["key"])
                _warn_on_threshold(db, tg, settings, src["key"], src["name"], f"{type(exc).__name__}: {exc}")
                stats[src["key"]] = {"name": src["name"], "error": str(exc)[:300]}
    finally:
        reader.close()
    return stats


def _default_reader() -> GmailReader:
    return GmailReader(require_env("GMAIL_ADDRESS"), require_env("GMAIL_APP_PASSWORD"))
