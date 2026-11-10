"""Postgres (Supabase) access."""
from __future__ import annotations

from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .models import Job


class Database:
    def __init__(self, url: str) -> None:
        # prepare_threshold=None keeps this working through Supabase's connection pooler.
        self.conn = psycopg.connect(url, autocommit=True, prepare_threshold=None, row_factory=dict_row)

    def close(self) -> None:
        self.conn.close()

    # ---------- runs ----------

    def start_run(self) -> int:
        return self.conn.execute("insert into runs default values returning id").fetchone()["id"]

    def finish_run(self, run_id: int, stats: dict[str, Any], error: str | None = None) -> None:
        self.conn.execute(
            "update runs set finished_at = now(), stats = %s, error = %s where id = %s",
            (Jsonb(stats), error, run_id),
        )

    # ---------- source health ----------

    def get_source_state(self, source_key: str) -> dict[str, Any] | None:
        return self.conn.execute(
            "select * from source_state where source_key = %s", (source_key,)
        ).fetchone()

    def record_source_success(self, source_key: str, count: int, meta: dict[str, Any]) -> None:
        self.conn.execute(
            """
            insert into source_state (source_key, last_success_at, last_count, consecutive_failures, last_error, meta)
            values (%s, now(), %s, 0, null, %s)
            on conflict (source_key) do update set
              last_success_at = now(), last_count = excluded.last_count,
              consecutive_failures = 0, last_error = null, meta = excluded.meta
            """,
            (source_key, count, Jsonb(meta)),
        )

    def record_source_failure(self, source_key: str, error: str, meta: dict[str, Any]) -> int:
        """Returns the number of consecutive failures, including this one."""
        row = self.conn.execute(
            """
            insert into source_state (source_key, consecutive_failures, last_error, meta)
            values (%s, 1, %s, %s)
            on conflict (source_key) do update set
              consecutive_failures = source_state.consecutive_failures + 1,
              last_error = excluded.last_error, meta = excluded.meta
            returning consecutive_failures
            """,
            (source_key, error[:1000], Jsonb(meta)),
        ).fetchone()
        return row["consecutive_failures"]

    # ---------- jobs ----------

    def existing_keys(self, source: str, company_slug: str) -> set[str]:
        rows = self.conn.execute(
            "select job_key from jobs where source = %s and company_slug = %s", (source, company_slug)
        ).fetchall()
        return {r["job_key"] for r in rows}

    def upsert_jobs(self, jobs: list[tuple[Job, str]], notify_status_for_new: str) -> int:
        """Insert new jobs, refresh known ones. `jobs` is a list of (job, fingerprint).

        Returns how many jobs were new. notify_status is only set on insert.
        """
        if not jobs:
            return 0
        params = [
            (
                job.job_key, fp, job.source, job.company_slug, job.company, job.title, job.location,
                job.country, job.is_remote, job.url, job.description, job.posted_at, notify_status_for_new,
                Jsonb({**job.raw, "workplace_type": job.workplace_type, "all_locations": job.all_locations}),
            )
            for job, fp in jobs
        ]
        new = 0
        with self.conn.transaction(), self.conn.cursor() as cur:
            cur.executemany(
                """
                insert into jobs (job_key, fingerprint, source, company_slug, company, title, location,
                                  country, is_remote, url, description, posted_at, notify_status, raw)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                on conflict (job_key) do update set
                  last_seen_at = now(),
                  missed_runs  = 0,
                  status       = 'open',
                  closed_at    = null,
                  fingerprint  = excluded.fingerprint,
                  company      = excluded.company,
                  title        = excluded.title,
                  location     = excluded.location,
                  country      = excluded.country,
                  is_remote    = excluded.is_remote,
                  url          = excluded.url,
                  description  = coalesce(excluded.description, jobs.description),
                  posted_at    = coalesce(excluded.posted_at, jobs.posted_at),
                  raw          = excluded.raw
                returning (xmax = 0) as inserted
                """,
                params,
                returning=True,
            )
            while True:
                row = cur.fetchone()
                if row and row["inserted"]:
                    new += 1
                if not cur.nextset():
                    break
        return new

    def mark_missing(
        self, source: str, company_slug: str, seen_keys: list[str], missed_runs_to_close: int
    ) -> list[dict[str, Any]]:
        """Count a miss for open jobs not in this run's listing; close those missed often enough.

        Returns every closed job: [{job_id, telegram_message_id, message_text}].
        telegram_message_id is None when no alert was sent for that job.
        """
        with self.conn.transaction():
            self.conn.execute(
                """
                update jobs set missed_runs = missed_runs + 1
                where source = %s and company_slug = %s and status = 'open'
                  and not (job_key = any(%s))
                """,
                (source, company_slug, seen_keys),
            )
            closed = self.conn.execute(
                """
                update jobs set status = 'closed', closed_at = now()
                where source = %s and company_slug = %s and status = 'open' and missed_runs >= %s
                returning id
                """,
                (source, company_slug, missed_runs_to_close),
            ).fetchall()
        ids = [r["id"] for r in closed]
        if not ids:
            return []
        return self.conn.execute(
            """
            select j.id as job_id, a.telegram_message_id, a.message_text
            from jobs j left join alerts a on a.job_id = j.id
            where j.id = any(%s)
            """,
            (ids,),
        ).fetchall()

    # ---------- alerts ----------

    def pending_alerts(
        self, limit: int, require_evaluation: bool, no_ai_sources: list[str] | None = None
    ) -> list[dict[str, Any]]:
        """Jobs waiting for an alert, best score first. With AI on, only evaluated jobs are sent,
        except jobs from no_ai_sources (email alerts), which are sent without an evaluation."""
        return self.conn.execute(
            """
            select j.id, j.source, j.company, j.title, j.location, j.url, j.fingerprint,
                   j.raw->>'workplace_type' as workplace_type,
                   e.score, e.decision, e.reason_he, e.gaps_he
            from jobs j left join evaluations e on e.job_id = j.id
            where j.notify_status = 'pending' and j.status = 'open'
              and (not %s or e.id is not null or j.source = any(%s))
            order by e.score desc nulls last, j.first_seen_at, j.id
            limit %s
            """,
            (require_evaluation, list(no_ai_sources or []), limit),
        ).fetchall()

    def pending_count(self) -> int:
        return self.conn.execute(
            "select count(*) as n from jobs where notify_status = 'pending' and status = 'open'"
        ).fetchone()["n"]

    def sample_baseline_jobs(self, n: int) -> list[dict[str, Any]]:
        return self.conn.execute(
            """
            select id, company, title, location, url, raw->>'workplace_type' as workplace_type
            from jobs where notify_status = 'baseline' and status = 'open'
            order by random() limit %s
            """,
            (n,),
        ).fetchall()

    def claim_alert(self, job_id: int, source: str, fingerprint: str, borderline: bool = False) -> int | None:
        """Reserve the right to alert on this job, before sending.

        Returns the alert id, or None if this job was already alerted, or the same job
        (same fingerprint) was already alerted from a different source. In that case the
        job is marked so it is not retried. Runs are never concurrent (hourly cron),
        so check-then-insert is safe here.
        """
        with self.conn.transaction():
            dup = self.conn.execute(
                """
                select 1 from alerts a join jobs j on j.id = a.job_id
                where a.fingerprint = %s and j.source <> %s limit 1
                """,
                (fingerprint, source),
            ).fetchone()
            if dup:
                self.set_notify_status(job_id, "skipped")
                return None
            row = self.conn.execute(
                """
                insert into alerts (job_id, fingerprint, borderline) values (%s, %s, %s)
                on conflict (job_id) do nothing returning id
                """,
                (job_id, fingerprint, borderline),
            ).fetchone()
            if row is None:
                self.set_notify_status(job_id, "sent")
                return None
            return row["id"]

    def complete_alert(self, alert_id: int, job_id: int, message_id: int, text: str) -> None:
        with self.conn.transaction():
            self.conn.execute(
                "update alerts set telegram_message_id = %s, message_text = %s, sent_at = now() where id = %s",
                (message_id, text, alert_id),
            )
            self.set_notify_status(job_id, "sent")

    def release_alert(self, alert_id: int) -> None:
        """Sending failed: drop the reservation so the next run retries."""
        self.conn.execute("delete from alerts where id = %s", (alert_id,))

    def set_notify_status(self, job_id: int, status: str) -> None:
        self.conn.execute("update jobs set notify_status = %s where id = %s", (status, job_id))

    # ---------- AI evaluation ----------

    def jobs_to_evaluate(
        self, notify_status: str, limit: int, exclude_sources: list[str] | None = None
    ) -> list[dict[str, Any]]:
        """Open jobs in the given notify_status that have no evaluation yet, oldest first.
        exclude_sources: sources that are never evaluated (email alerts)."""
        return self.conn.execute(
            """
            select j.id, j.source, j.company, j.title, j.location, j.description, j.fingerprint
            from jobs j left join evaluations e on e.job_id = j.id
            where j.notify_status = %s and j.status = 'open' and e.id is null
              and not (j.source = any(%s))
            order by j.first_seen_at, j.id limit %s
            """,
            (notify_status, list(exclude_sources or []), limit),
        ).fetchall()

    def count_to_evaluate(self, notify_status: str, exclude_sources: list[str] | None = None) -> int:
        return self.conn.execute(
            """
            select count(*) as n from jobs j left join evaluations e on e.job_id = j.id
            where j.notify_status = %s and j.status = 'open' and e.id is null
              and not (j.source = any(%s))
            """,
            (notify_status, list(exclude_sources or [])),
        ).fetchone()["n"]

    def is_cross_source_duplicate(self, fingerprint: str, source: str) -> bool:
        """Already alerted from a different source (so there's no need to spend AI on it)."""
        return self.conn.execute(
            """
            select 1 from alerts a join jobs j on j.id = a.job_id
            where a.fingerprint = %s and j.source <> %s limit 1
            """,
            (fingerprint, source),
        ).fetchone() is not None

    def save_evaluation(self, job_id: int, ev: Any, new_notify_status: str | None) -> None:
        with self.conn.transaction():
            self.conn.execute(
                """
                insert into evaluations (job_id, decision, reject_reason, requirements, score, rubric,
                                         reason_he, gaps_he, models)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                on conflict (job_id) do update set
                  decision = excluded.decision, reject_reason = excluded.reject_reason,
                  requirements = excluded.requirements, score = excluded.score, rubric = excluded.rubric,
                  reason_he = excluded.reason_he, gaps_he = excluded.gaps_he, models = excluded.models,
                  created_at = now()
                """,
                (job_id, ev.decision, ev.reject_reason, Jsonb(ev.requirements), ev.score,
                 Jsonb(ev.rubric) if ev.rubric is not None else None, ev.reason_he, ev.gaps_he,
                 Jsonb(ev.models)),
            )
            if new_notify_status:
                self.set_notify_status(job_id, new_notify_status)

    def baseline_summary(self) -> list[dict[str, Any]]:
        return self.conn.execute(
            """
            select e.decision,
                   count(*) as n,
                   count(*) filter (where e.score >= 70) as s70,
                   count(*) filter (where e.score >= 50 and e.score < 70) as s50,
                   count(*) filter (where e.score < 50) as below50
            from jobs j join evaluations e on e.job_id = j.id
            where j.notify_status = 'baseline' and j.status = 'open'
            group by e.decision order by e.decision
            """
        ).fetchall()

    def release_baseline(self, min_score: int) -> tuple[int, int]:
        """Evaluated baseline jobs: relevant ones with score >= min_score become pending (alerted
        on the next runs, best first); the rest become skipped. Returns (released, skipped)."""
        with self.conn.transaction():
            released = self.conn.execute(
                """
                update jobs j set notify_status = 'pending'
                from evaluations e
                where e.job_id = j.id and j.notify_status = 'baseline' and j.status = 'open'
                  and e.decision in ('alert', 'borderline') and coalesce(e.score, 100) >= %s
                """,
                (min_score,),
            ).rowcount
            skipped = self.conn.execute(
                """
                update jobs j set notify_status = 'skipped'
                from evaluations e
                where e.job_id = j.id and j.notify_status = 'baseline' and j.status = 'open'
                """
            ).rowcount
        return released, skipped

    # ---------- emails (phase 3) ----------

    def processed_email_ids(self, gmail_ids: list[str]) -> set[str]:
        if not gmail_ids:
            return set()
        rows = self.conn.execute(
            "select gmail_id from processed_emails where gmail_id = any(%s)", (gmail_ids,)
        ).fetchall()
        return {r["gmail_id"] for r in rows}

    def mark_emails_processed(self, rows: list[tuple[str, str, str, Any, int]]) -> None:
        """rows: (gmail_id, source, subject, received_at, jobs_found)."""
        if not rows:
            return
        with self.conn.transaction(), self.conn.cursor() as cur:
            cur.executemany(
                """
                insert into processed_emails (gmail_id, source, subject, received_at, jobs_found)
                values (%s, %s, %s, %s, %s) on conflict (gmail_id) do nothing
                """,
                rows,
            )

    # ---------- profile ----------

    def get_profile(self) -> dict[str, Any] | None:
        return self.conn.execute(
            "select profile_yaml, cv_text, updated_at from profile where key = 'default'"
        ).fetchone()

    def set_profile(self, profile_yaml: str, cv_text: str) -> None:
        self.conn.execute(
            """
            insert into profile (key, profile_yaml, cv_text) values ('default', %s, %s)
            on conflict (key) do update set profile_yaml = excluded.profile_yaml,
              cv_text = excluded.cv_text, updated_at = now()
            """,
            (profile_yaml, cv_text),
        )

