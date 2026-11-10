"""Run-level email logic: baseline, dedup of read emails, unreadable emails, cross-source fingerprints."""
from jobalert.config import CompanyConfig
from jobalert.dedup import fingerprint
from jobalert.gmail.collect import collect_emails, to_job
from jobalert.gmail.imap import MailMessage
from jobalert.gmail.parsers import EmailJob

COMPANIES = [CompanyConfig("mobileye", "Mobileye", "lever", {}, aliases=("mobileye",))]
SRC = {"key": "gmail_linkedin", "name": "LinkedIn", "query": "from:x", "parser": "linkedin"}

LI_EMAIL = (
    "Your job alert for Junior in Jerusalem\n"
    "Data Analytics Project Manager\nMobileye\nJerusalem District, Israel\n\n1 connection\n"
    "View job: https://www.linkedin.com/comm/jobs/view/111/?trk=a\n"
    "---------------------------------------------------------\n"
    "Data Analyst\nAcme Ltd\nTel Aviv-Yafo\n"
    "View job: https://www.linkedin.com/comm/jobs/view/222/?trk=a\n"
)


def _msg(gid: str, text: str) -> MailMessage:
    return MailMessage(gmail_id=gid, sender="jobalerts-noreply@linkedin.com", subject=f"alert {gid}",
                       received_at=None, text=text, html="", raw=b"")


class FakeReader:
    def __init__(self, messages: dict[str, MailMessage]):
        self.messages = messages        # uid -> message
        self.fetched: list[str] = []

    def search(self, q):
        return list(self.messages)

    def gmail_ids(self, uids):
        return {u: self.messages[u].gmail_id for u in uids}

    def fetch(self, uid):
        self.fetched.append(uid)
        return self.messages[uid]

    def close(self):
        pass


class FakeDb:
    def __init__(self):
        self.state, self.jobs, self.processed = {}, {}, set()

    def get_source_state(self, key):
        return self.state.get(key)

    def record_source_success(self, key, count, meta):
        self.state[key] = {"last_success_at": "now", "meta": meta, "consecutive_failures": 0}

    def record_source_failure(self, key, error, meta):
        s = self.state.setdefault(key, {"last_success_at": None, "consecutive_failures": 0})
        s["consecutive_failures"] += 1
        s["meta"] = meta
        return s["consecutive_failures"]

    def processed_email_ids(self, ids):
        return {i for i in ids if i in self.processed}

    def mark_emails_processed(self, rows):
        self.processed.update(r[0] for r in rows)

    def upsert_jobs(self, rows, status):
        new = 0
        for job, fp in rows:
            if job.job_key not in self.jobs:
                self.jobs[job.job_key] = {"job": job, "fp": fp, "notify_status": status}
                new += 1
        return new


class FakeTg:
    def __init__(self):
        self.messages = []

    def send_message(self, text):
        self.messages.append(text)
        return 1


def _run(db, reader, settings, tg=None):
    s = settings.__class__(**{**settings.__dict__, "email": {"enabled": True, "lookback_days": 3, "sources": [SRC]}})
    return collect_emails(db, tg or FakeTg(), s, COMPANIES, reader_factory=lambda: reader)


def test_first_run_is_baseline_then_new_jobs_are_pending(settings):
    db = FakeDb()
    stats = _run(db, FakeReader({"1": _msg("g1", LI_EMAIL)}), settings)
    assert stats["gmail_linkedin"]["baseline"] is True
    assert {j["notify_status"] for j in db.jobs.values()} == {"baseline"}

    later = LI_EMAIL.replace("/222/", "/333/")
    reader = FakeReader({"1": _msg("g1", LI_EMAIL), "2": _msg("g2", later)})
    stats = _run(db, reader, settings)
    assert reader.fetched == ["2"]                    # an email already read is not downloaded again
    assert stats["gmail_linkedin"]["new"] == 1
    assert db.jobs["gmail_linkedin:acme ltd:333"]["notify_status"] == "pending"


def test_unreadable_email_is_retried_and_warns_after_three_runs(settings):
    db, tg = FakeDb(), FakeTg()
    _run(db, FakeReader({"0": _msg("g0", LI_EMAIL)}), settings, tg)        # baseline run
    broken = _msg("g9", "View job: https://www.linkedin.com/comm/jobs/view/9/\n")
    for _ in range(3):
        reader = FakeReader({"9": broken})
        stats = _run(db, reader, settings, tg)
        assert reader.fetched == ["9"]                # never marked as read
    assert stats["gmail_linkedin"]["unreadable"] == 1
    assert len(tg.messages) == 1 and "LinkedIn" in tg.messages[0]
    assert db.state["gmail_linkedin"]["meta"]["baselined"] is True   # stays baselined while failing


def test_login_failure_does_not_raise(settings):
    db, tg = FakeDb(), FakeTg()

    def boom():
        raise RuntimeError("Gmail login failed")

    s = settings.__class__(**{**settings.__dict__, "email": {"enabled": True, "sources": [SRC]}})
    stats = collect_emails(db, tg, s, COMPANIES, reader_factory=boom)
    assert "error" in stats["gmail"]
    assert db.state["gmail"]["consecutive_failures"] == 1


def test_email_job_fingerprint_matches_company_board():
    job, fp = to_job("gmail_linkedin", EmailJob("111", "Data Analytics Project Manager", "Mobileye",
                                                "Jerusalem District, Israel", "u"), {"mobileye": "mobileye"})
    assert job.company_slug == "mobileye"
    assert fp == fingerprint("mobileye", "Data Analytics Project Manager", "Jerusalem")


def test_jobify_ids_come_from_fingerprint():
    a, _ = to_job("gmail_jobify", EmailJob(None, "Data Analyst", "G-Stat", "רחובות", "https://j/n/A"), {})
    b, _ = to_job("gmail_jobify", EmailJob(None, "Data Analyst", "G-Stat", "רחובות", "https://j/n/B"), {})
    assert a.job_key == b.job_key        # same job in two weekly emails, different short links
