"""Run-level logic of collect_company with an in-memory DB that mirrors the SQL semantics."""
from jobalert.collectors import registry
from jobalert.collectors.base import Collector
from jobalert.config import CompanyConfig
from jobalert.location import LocationFilter
from jobalert.run import collect_company

COMPANY = CompanyConfig("acme", "Acme", "fake", {})


class FakeCollector(Collector):
    ats = "fake"
    listing: list = []
    error: Exception | None = None

    def list_jobs(self):
        if FakeCollector.error:
            raise FakeCollector.error
        return [self.make_job(external_id=i, title=f"Job {i}", url=f"https://acme/{i}", location="Tel Aviv")
                for i in FakeCollector.listing]


class FakeDb:
    def __init__(self):
        self.state: dict = {}
        self.jobs: dict[str, dict] = {}
        self.alerts: dict[str, int] = {}   # job_key -> telegram message id

    def get_source_state(self, key):
        return self.state.get(key)

    def record_source_success(self, key, count, meta):
        self.state[key] = {"last_success_at": "now", "last_count": count, "meta": meta,
                           "consecutive_failures": 0}

    def record_source_failure(self, key, error, meta):
        s = self.state.setdefault(key, {"last_success_at": None, "last_count": None, "meta": meta,
                                        "consecutive_failures": 0})
        s["consecutive_failures"] += 1
        return s["consecutive_failures"]

    def existing_keys(self, source, slug):
        return set(self.jobs)

    def upsert_jobs(self, rows, status):
        new = 0
        for job, fp in rows:
            if job.job_key in self.jobs:
                self.jobs[job.job_key].update(missed=0, status="open")
            else:
                self.jobs[job.job_key] = {"missed": 0, "status": "open", "notify": status}
                new += 1
        return new

    def mark_missing(self, source, slug, seen, n):
        closed = []
        for key, j in self.jobs.items():
            if j["status"] == "open" and key not in seen:
                j["missed"] += 1
                if j["missed"] >= n:
                    j["status"] = "closed"
                    closed.append({"job_id": key, "telegram_message_id": self.alerts.get(key),
                                   "message_text": "alert text"})
        return closed


class FakeTelegram:
    def __init__(self):
        self.messages, self.closed = [], []

    def send_message(self, text, reply_markup=None):
        self.messages.append(text)
        return 1

    def mark_closed(self, message_id, text):
        self.closed.append(message_id)


def setup(monkeypatch):
    monkeypatch.setitem(registry.COLLECTORS, "fake", FakeCollector)
    FakeCollector.error = None
    return FakeDb(), FakeTelegram()


def run_once(db, tg, settings, listing):
    FakeCollector.listing = listing
    return collect_company(COMPANY, db, None, LocationFilter(settings.location), tg, settings)


def test_first_run_is_baseline_then_new_jobs_are_pending(monkeypatch, settings):
    db, tg = setup(monkeypatch)
    stats = run_once(db, tg, settings, ["1", "2"])
    assert stats["baseline"] and stats["new"] == 2
    assert {j["notify"] for j in db.jobs.values()} == {"baseline"}
    stats = run_once(db, tg, settings, ["1", "2", "3"])
    assert not stats["baseline"] and stats["new"] == 1
    assert db.jobs["fake:acme:3"]["notify"] == "pending"


def test_job_closes_after_two_missed_runs_and_alert_is_edited(monkeypatch, settings):
    db, tg = setup(monkeypatch)
    run_once(db, tg, settings, ["1", "2"])
    db.alerts["fake:acme:2"] = 555
    run_once(db, tg, settings, ["1"])
    assert db.jobs["fake:acme:2"]["status"] == "open"          # one miss is not enough
    run_once(db, tg, settings, ["1"])
    assert db.jobs["fake:acme:2"]["status"] == "closed"
    assert tg.closed == [555]


def test_job_that_reappears_resets_its_miss_count(monkeypatch, settings):
    db, tg = setup(monkeypatch)
    run_once(db, tg, settings, ["1", "2"])
    run_once(db, tg, settings, ["1"])
    run_once(db, tg, settings, ["1", "2"])
    run_once(db, tg, settings, ["1"])
    assert db.jobs["fake:acme:2"]["status"] == "open"


def test_sudden_empty_listing_closes_nothing(monkeypatch, settings):
    db, tg = setup(monkeypatch)
    run_once(db, tg, settings, [str(i) for i in range(10)])
    for _ in range(3):
        stats = run_once(db, tg, settings, [])
        assert "warning" in stats
    assert all(j["status"] == "open" for j in db.jobs.values())
    assert len(tg.messages) == 1 and "0 משרות" in tg.messages[0]   # warned once, at 3 failures


def test_failing_source_warns_once_after_three_failures(monkeypatch, settings):
    db, tg = setup(monkeypatch)
    FakeCollector.error = RuntimeError("HTTP 500")
    for _ in range(4):
        stats = run_once(db, tg, settings, [])
        assert "error" in stats
    assert len(tg.messages) == 1 and "נכשל 3 ריצות" in tg.messages[0]
