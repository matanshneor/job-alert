"""Hourly entrypoint.

    uv run python -m jobalert.run                 # normal run (what Railway's cron executes)
    uv run python -m jobalert.run --dry-run       # fetch + filter + print; no DB, no Telegram
    uv run python -m jobalert.run --sample-alerts 3   # also send 3 test alerts from stored jobs
    uv run python -m jobalert.run --only monday,upwind   # "gmail" = the email sources
"""
from __future__ import annotations

import argparse
import html
import logging
import sys
import time
from typing import Any

from .ai.evaluate import TITLE_ONLY_MODE, evaluate_job, evaluate_title_only
from .ai.llm import LLM, TransientAIError
from .ai.profile import load_db_profile
from .collectors.registry import get_collector_class
from .config import CompanyConfig, Settings, load_companies, load_settings, require_env
from .db import Database
from .dedup import build_alias_map, fingerprint
from .enrich import BoardFinder
from .gmail.collect import PARSERS, collect_emails
from .gmail.imap import GmailReader
from .http import PoliteClient
from .location import LocationFilter
from .notify.telegram import AlertContent, TelegramClient
from .quiet import is_quiet, load_held, save_held

log = logging.getLogger("jobalert")


def setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # httpx logs full request URLs at INFO, and Telegram URLs contain the bot token.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Check job sources and send Telegram alerts.")
    p.add_argument("--dry-run", action="store_true", help="fetch and filter only; no DB, no Telegram")
    p.add_argument("--sample-alerts", type=int, default=0, help="send N test alerts from stored jobs")
    p.add_argument("--only", default="", help="comma-separated company slugs to run ('gmail' = emails)")
    return p.parse_args(argv)


def _wanted(only: str) -> set[str]:
    return {s.strip() for s in only.split(",") if s.strip()}


def emails_wanted(only: str) -> bool:
    wanted = _wanted(only)
    return not wanted or "gmail" in wanted


def active_companies(companies: list[CompanyConfig], only: str) -> list[CompanyConfig]:
    wanted = _wanted(only)
    result = []
    for c in companies:
        if wanted and c.slug not in wanted:
            continue
        if not c.enabled:
            continue
        if get_collector_class(c.ats) is None:
            log.info("Skipping %s: no '%s' collector yet", c.slug, c.ats)
            continue
        result.append(c)
    return result


def make_http(settings: Settings) -> PoliteClient:
    h = settings.http
    return PoliteClient(h["user_agent"], h["min_delay_seconds"], h["timeout_seconds"], h["retries"])


# ---------------------------------------------------------------- dry run

def dry_run_emails(settings: Settings) -> None:
    cfg = settings.email
    reader = GmailReader(require_env("GMAIL_ADDRESS"), require_env("GMAIL_APP_PASSWORD"))
    try:
        for src in cfg.get("sources") or []:
            uids = reader.search(f"{src['query']} newer_than:{int(cfg.get('lookback_days', 3))}d")
            jobs = []
            for uid in uids:
                jobs.extend(PARSERS[src["parser"]](reader.fetch(uid)))
            print(f"\n[{src['name']} emails] {len(uids)} emails, {len(jobs)} jobs (incl. repeats)")
            for j in jobs[:5]:
                print(f"   - {j.title} | {j.company} | {j.location}")
                print(f"     {j.url}")
    finally:
        reader.close()


def dry_run(companies: list[CompanyConfig], settings: Settings, include_email: bool = False) -> int:
    http = make_http(settings)
    loc = LocationFilter(settings.location)
    for company in companies:
        collector = get_collector_class(company.ats)(company, http, {})
        try:
            jobs = collector.list_jobs()
        except Exception as exc:  # noqa: BLE001 - report and continue with other sources
            print(f"\n[{company.name}] FAILED: {exc}")
            continue
        kept = [j for j in jobs if loc.passes(j)]
        print(f"\n[{company.name}] ({company.ats}) fetched {len(jobs)}, kept {len(kept)} (Israel/remote)")
        for j in kept[:5]:
            desc = f"{len(j.description or '')} chars"
            print(f"   - {j.title} | {j.location} | {j.workplace_type} | desc {desc}")
            print(f"     {j.url}")
    http.close()
    if include_email and settings.email.get("enabled"):
        try:
            dry_run_emails(settings)
        except Exception as exc:  # noqa: BLE001
            print(f"\n[emails] FAILED: {exc}")
    return 0


# ---------------------------------------------------------------- real run

def collect_company(
    company: CompanyConfig, db: Database, http: PoliteClient, loc: LocationFilter,
    tg: TelegramClient, settings: Settings,
) -> dict[str, Any]:
    key = company.source_key
    state = db.get_source_state(key) or {}
    meta = dict(state.get("meta") or {})
    collector = get_collector_class(company.ats)(company, http, meta)
    closing = settings.closing

    try:
        jobs = collector.list_jobs()
    except Exception as exc:  # noqa: BLE001 - one broken source must not stop the others
        log.exception("Source %s failed", key)
        failures = db.record_source_failure(key, f"{type(exc).__name__}: {exc}", meta)
        if failures == closing["warn_after_failures"]:
            tg.send_message(
                f"⚠️ המקור <b>{html.escape(company.name)}</b> נכשל {failures} ריצות ברצף.\n"
                f"<code>{html.escape(str(exc)[:300])}</code>"
            )
        return {"error": str(exc)[:300]}

    kept = [j for j in jobs if loc.passes(j)]
    previous = state.get("last_count") or 0
    if not kept and previous > closing["min_previous_count"]:
        # Suspicious: a source that had jobs suddenly has none. Don't close anything.
        msg = f"returned 0 relevant jobs (had {previous}); not closing anything"
        log.warning("%s %s", key, msg)
        failures = db.record_source_failure(key, msg, meta)
        if failures == closing["warn_after_failures"]:
            tg.send_message(f"⚠️ המקור <b>{html.escape(company.name)}</b> מחזיר 0 משרות כבר {failures} ריצות.")
        return {"fetched": len(jobs), "kept": 0, "warning": msg}

    known = db.existing_keys(collector.ats, company.slug)
    for job in kept:
        if job.job_key not in known and not job.description:
            collector.fetch_details(job)

    # A source seen for the first time is baselined: its existing jobs are stored without alerts.
    first_time = state.get("last_success_at") is None
    rows = [(j, fingerprint(company.slug, j.title, j.location)) for j in kept]
    new = db.upsert_jobs(rows, "baseline" if first_time else "pending")

    closed = []
    if collector.complete_listing:
        closed = db.mark_missing(collector.ats, company.slug, [j.job_key for j in kept],
                                 closing["missed_runs_to_close"])
        for c in closed:
            if c["telegram_message_id"]:
                tg.mark_closed(c["telegram_message_id"], c["message_text"] or "")

    db.record_source_success(key, len(kept), meta)
    log.info("%s: fetched %d, kept %d, new %d, closed %d%s", key, len(jobs), len(kept), new,
             len(closed), " (baseline)" if first_time else "")
    return {"fetched": len(jobs), "kept": len(kept), "new": new, "closed": len(closed),
            "baseline": first_time}


def unscored_sources(settings: Settings) -> list[str]:
    """Sources whose jobs skip the AI. Email jobs are scored unless email.score_with_ai is false."""
    if settings.email.get("score_with_ai", True):
        return []
    return list(settings.email_sources)


def evaluate_pending(
    db: Database, tg: TelegramClient, settings: Settings,
    http: PoliteClient | None = None, companies: list[CompanyConfig] | None = None,
) -> dict[str, Any]:
    """Run the AI filter + score on new jobs. Rejected or low-score jobs are marked skipped.

    Email jobs have no description: we look for the same job on the company's own board and
    score it normally if found, otherwise we estimate the score from the title alone.
    """
    ai = settings.ai
    stats = {"evaluated": 0, "rejected": 0, "below_min_score": 0, "duplicates": 0, "waiting": 0,
             "email_from_board": 0, "email_title_only": 0}
    skip_sources = unscored_sources(settings)
    rows = db.jobs_to_evaluate("pending", ai["max_evaluations_per_run"], skip_sources)
    if not rows:
        return stats
    email_sources = set(settings.email_sources)
    finder = None
    if http is not None and companies is not None and any(r["source"] in email_sources for r in rows):
        finder = BoardFinder(db, http, companies, build_alias_map(companies),
                             LocationFilter(settings.location), settings.email.get("board_lookup") or {})
    try:
        profile = load_db_profile(db)
        llm = LLM(require_env("ANTHROPIC_API_KEY"))
        for row in rows:
            if db.is_cross_source_duplicate(row["fingerprint"], row["source"]):
                db.set_notify_status(row["id"], "skipped")
                stats["duplicates"] += 1
                continue
            if row["source"] in email_sources and not row.get("description"):
                found = finder.find(row) if finder else None
                if found:
                    db.set_description(row["id"], found.description, found.board_url)
                    row = {**row, "description": found.description}
                    stats["email_from_board"] += 1
                    ev = evaluate_job(llm, profile, row, ai)
                else:
                    stats["email_title_only"] += 1
                    ev = evaluate_title_only(llm, profile, row, ai)
            else:
                ev = evaluate_job(llm, profile, row, ai)
            new_status = None
            if ev.decision == "reject":
                new_status = "skipped"
                stats["rejected"] += 1
            elif ev.score is not None and ev.score < ai.get("min_score_to_alert", 0):
                new_status = "skipped"
                stats["below_min_score"] += 1
            db.save_evaluation(row["id"], ev, new_status)
            stats["evaluated"] += 1
            log.info("AI: %s / %s -> %s %s%s", row["company"], row["title"], ev.decision,
                     ev.score if ev.score is not None else ev.reject_reason or "",
                     " (title only)" if ev.models.get("mode") == TITLE_ONLY_MODE else "")
    except TransientAIError as exc:
        # Temporary API trouble: the remaining jobs stay pending and are retried next run.
        log.warning("AI temporarily unavailable: %s", exc)
        stats["error"] = str(exc)[:300]
    except Exception as exc:  # noqa: BLE001 - e.g. missing key / profile / bad request
        log.exception("AI evaluation failed")
        stats["error"] = f"{type(exc).__name__}: {exc}"[:300]
    if "error" in stats:
        failures = db.record_source_failure("ai:evaluate", stats["error"], {})
        if failures == settings.closing["warn_after_failures"]:
            tg.send_message(f"⚠️ שלב ה-AI נכשל {failures} ריצות ברצף. משרות חדשות ממתינות.\n"
                            f"<code>{html.escape(stats['error'])}</code>")
    else:
        db.record_source_success("ai:evaluate", stats["evaluated"], {})
    stats["waiting"] = db.count_to_evaluate("pending", skip_sources)
    return stats


def send_pending_alerts(
    db: Database, tg: TelegramClient, limit: int, require_evaluation: bool,
    email_sources: dict[str, str] | None = None, no_ai_sources: list[str] | None = None,
) -> int:
    email_sources = email_sources or {}
    sent = 0
    for row in db.pending_alerts(limit, require_evaluation, no_ai_sources or []):
        borderline = row.get("decision") == "borderline"
        alert_id = db.claim_alert(row["id"], row["source"], row["fingerprint"], borderline)
        if alert_id is None:
            log.info("Skipping duplicate: %s / %s", row["company"], row["title"])
            continue
        content = AlertContent(
            job_id=row["id"], company=row["company"], title=row["title"],
            location=row["location"], workplace_type=row["workplace_type"], url=row["url"],
            score=row.get("score"), borderline=borderline, borderline_gaps=row.get("gaps_he"),
            reason_he=row.get("reason_he"), email_source=email_sources.get(row["source"]),
            estimated=row.get("eval_mode") == TITLE_ONLY_MODE,
        )
        try:
            message_id, text = tg.send_alert(content)
        except Exception:  # noqa: BLE001
            log.exception("Telegram send failed for job %s; will retry next run", row["id"])
            db.release_alert(alert_id)
            continue
        db.complete_alert(alert_id, row["id"], message_id, text)
        sent += 1
    return sent


def send_samples(db: Database, tg: TelegramClient, n: int) -> None:
    # Test messages only: not recorded as alerts, so phase 2 still evaluates these jobs.
    for row in db.sample_baseline_jobs(n):
        tg.send_alert(AlertContent(
            job_id=row["id"], company=row["company"], title=row["title"], location=row["location"],
            workplace_type=row["workplace_type"], url=row["url"], is_test=True,
        ))


def baseline_summary(stats: dict[str, Any], companies: list[CompanyConfig]) -> str | None:
    names = {c.source_key: c.name for c in companies}
    lines = [f"• {html.escape(s.get('name') or names.get(k, k))}: {s['new']} משרות"
             for k, s in stats.items() if s.get("baseline")]
    if not lines:
        return None
    return (
        "📥 <b>התחלתי לעקוב אחרי:</b>\n" + "\n".join(lines) +
        "\n\nהמשרות הקיימות נשמרו בלי התראות. מעכשיו תקבל התראה על כל משרה חדשה ורלוונטית."
    )


def send_held_messages(db: Database, tg: TelegramClient) -> None:
    """Send the messages held during quiet hours, then clear them."""
    held = load_held(db)
    if not held:
        return
    tg.send_message(f"🌙 <b>{len(held)} הודעות מהלילה:</b>")
    for text in held:
        tg.send_message(text)
    save_held(db, [])


def real_run(companies: list[CompanyConfig], settings: Settings, sample_alerts: int,
             include_email: bool = True) -> int:
    db = Database(require_env("DATABASE_URL"))
    tg = TelegramClient(require_env("TELEGRAM_BOT_TOKEN"), require_env("TELEGRAM_CHAT_ID"),
                        settings.alerts["telegram_delay_seconds"],
                        tailor_button=bool(settings.alerts.get("tailor_button", False)),
                        quiet=is_quiet(settings.alerts.get("quiet_hours")))
    http = make_http(settings)
    loc = LocationFilter(settings.location)
    run_id = db.start_run()
    started = time.monotonic()
    stats: dict[str, Any] = {}
    try:
        if not tg.quiet:
            send_held_messages(db, tg)
        for company in companies:
            stats[company.source_key] = collect_company(company, db, http, loc, tg, settings)
        if include_email:
            stats.update(collect_emails(db, tg, settings, load_companies()))
        summary = baseline_summary(stats, companies)
        if summary:
            tg.send_message(summary)
        if sample_alerts and not tg.quiet:
            send_samples(db, tg, sample_alerts)
        ai_on = bool(settings.ai.get("enabled"))
        if ai_on:
            stats["_ai"] = evaluate_pending(db, tg, settings, http, load_companies())
        sent = 0
        if tg.quiet:
            log.info("Quiet hours: alerts stay pending until the morning")
        else:
            sent = send_pending_alerts(db, tg, settings.alerts["max_per_run"], require_evaluation=ai_on,
                                       email_sources=settings.email_sources,
                                       no_ai_sources=unscored_sources(settings))
        waiting = db.pending_count()
        stats["_alerts"] = {"sent": sent, "still_pending": waiting}
        stats["_seconds"] = round(time.monotonic() - started, 1)
        log.info("Done: %d alerts sent, %d pending for next run, %.1fs", sent, waiting, stats["_seconds"])
        db.finish_run(run_id, stats)
        return 0
    except Exception as exc:
        log.exception("Run failed")
        db.finish_run(run_id, stats, f"{type(exc).__name__}: {exc}"[:1000])
        return 1
    finally:
        if tg.held:
            try:
                save_held(db, load_held(db) + tg.held)
            except Exception:  # noqa: BLE001 - never hide the run's own error
                log.exception("Could not save %d held messages", len(tg.held))
        http.close()
        tg.close()
        db.close()


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    args = parse_args(argv)
    settings = load_settings()
    companies = active_companies(load_companies(), args.only)
    include_email = emails_wanted(args.only)
    if args.dry_run:
        return dry_run(companies, settings, include_email)
    return real_run(companies, settings, args.sample_alerts, include_email)


if __name__ == "__main__":
    sys.exit(main())
