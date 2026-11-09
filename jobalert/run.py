"""Hourly entrypoint.

    uv run python -m jobalert.run                 # normal run (what Railway's cron executes)
    uv run python -m jobalert.run --dry-run       # fetch + filter + print; no DB, no Telegram
    uv run python -m jobalert.run --sample-alerts 3   # also send 3 test alerts from stored jobs
    uv run python -m jobalert.run --only monday,upwind
"""
from __future__ import annotations

import argparse
import html
import logging
import sys
import time
from typing import Any

from .collectors.registry import get_collector_class
from .config import CompanyConfig, Settings, load_companies, load_settings, require_env
from .db import Database
from .dedup import fingerprint
from .http import PoliteClient
from .location import LocationFilter
from .notify.telegram import AlertContent, TelegramClient

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
    p.add_argument("--only", default="", help="comma-separated company slugs to run")
    return p.parse_args(argv)


def active_companies(companies: list[CompanyConfig], only: str) -> list[CompanyConfig]:
    wanted = {s.strip() for s in only.split(",") if s.strip()}
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

def dry_run(companies: list[CompanyConfig], settings: Settings) -> int:
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


def send_pending_alerts(db: Database, tg: TelegramClient, limit: int) -> int:
    # Phase 2 will insert the AI filter + score step here, before alerting.
    sent = 0
    for row in db.pending_alerts(limit):
        alert_id = db.claim_alert(row["id"], row["source"], row["fingerprint"])
        if alert_id is None:
            log.info("Skipping duplicate: %s / %s", row["company"], row["title"])
            continue
        content = AlertContent(
            job_id=row["id"], company=row["company"], title=row["title"],
            location=row["location"], workplace_type=row["workplace_type"], url=row["url"],
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
    lines = [f"• {html.escape(names[k])}: {s['new']} משרות" for k, s in stats.items() if s.get("baseline")]
    if not lines:
        return None
    return (
        "📥 <b>התחלתי לעקוב אחרי:</b>\n" + "\n".join(lines) +
        "\n\nהמשרות הקיימות נשמרו בלי התראות. מעכשיו תקבל התראה על כל משרה חדשה."
        "\nבשלב 2 ה-AI יעבור גם על המשרות הקיימות וישלח רק את הרלוונטיות."
    )


def real_run(companies: list[CompanyConfig], settings: Settings, sample_alerts: int) -> int:
    db = Database(require_env("DATABASE_URL"))
    tg = TelegramClient(require_env("TELEGRAM_BOT_TOKEN"), require_env("TELEGRAM_CHAT_ID"),
                        settings.alerts["telegram_delay_seconds"])
    http = make_http(settings)
    loc = LocationFilter(settings.location)
    run_id = db.start_run()
    started = time.monotonic()
    stats: dict[str, Any] = {}
    try:
        for company in companies:
            stats[company.source_key] = collect_company(company, db, http, loc, tg, settings)
        summary = baseline_summary(stats, companies)
        if summary:
            tg.send_message(summary)
        if sample_alerts:
            send_samples(db, tg, sample_alerts)
        sent = send_pending_alerts(db, tg, settings.alerts["max_per_run"])
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
        http.close()
        tg.close()
        db.close()


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    args = parse_args(argv)
    settings = load_settings()
    companies = active_companies(load_companies(), args.only)
    if args.dry_run:
        return dry_run(companies, settings)
    return real_run(companies, settings, args.sample_alerts)


if __name__ == "__main__":
    sys.exit(main())
