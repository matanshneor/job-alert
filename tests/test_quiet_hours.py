from datetime import datetime, timezone

from jobalert.notify.telegram import TelegramClient
from jobalert.quiet import is_quiet, load_held, save_held
from jobalert.run import send_held_messages

CFG = {"enabled": True, "start": "00:00", "end": "09:00", "timezone": "Asia/Jerusalem"}


def utc(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=timezone.utc)


def test_quiet_window_in_israel_time_winter_and_summer():
    # Winter: Israel = UTC+2
    assert not is_quiet(CFG, utc(2026, 11, 11, 21, 59))   # 23:59 Israel
    assert is_quiet(CFG, utc(2026, 11, 11, 22, 0))        # 00:00 Israel
    assert is_quiet(CFG, utc(2026, 11, 12, 6, 59))        # 08:59 Israel
    assert not is_quiet(CFG, utc(2026, 11, 12, 7, 0))     # 09:00 Israel
    # Summer: Israel = UTC+3
    assert is_quiet(CFG, utc(2026, 7, 1, 5, 30))          # 08:30 Israel
    assert not is_quiet(CFG, utc(2026, 7, 1, 6, 0))       # 09:00 Israel


def test_quiet_window_crossing_midnight_and_disabled():
    cfg = {**CFG, "start": "23:00", "end": "07:00"}
    assert is_quiet(cfg, utc(2026, 11, 11, 21, 30))       # 23:30 Israel
    assert is_quiet(cfg, utc(2026, 11, 12, 4, 0))         # 06:00 Israel
    assert not is_quiet(cfg, utc(2026, 11, 12, 10, 0))    # 12:00 Israel
    assert not is_quiet({**CFG, "enabled": False}, utc(2026, 11, 11, 23, 0))
    assert not is_quiet(None, utc(2026, 11, 11, 23, 0))


class FakeDb:
    def __init__(self):
        self.state = {}

    def get_source_state(self, key):
        return self.state.get(key)

    def record_source_success(self, key, count, meta):
        self.state[key] = {"last_count": count, "meta": meta}


class RecordingTelegram(TelegramClient):
    def __init__(self, quiet):
        super().__init__("t", "c", delay_seconds=0, quiet=quiet)
        self.sent = []

    def _call(self, method, payload):
        self.sent.append(payload["text"])
        return {"message_id": len(self.sent)}


def test_quiet_client_holds_messages_and_morning_run_sends_them():
    db = FakeDb()
    night = RecordingTelegram(quiet=True)
    assert night.send_message("⚠️ warning 1") == 0
    night.send_message("⚠️ warning 2")
    assert night.sent == [] and night.held == ["⚠️ warning 1", "⚠️ warning 2"]
    save_held(db, load_held(db) + night.held)
    save_held(db, load_held(db) + ["⚠️ warning 3"])        # a later quiet run adds to them

    morning = RecordingTelegram(quiet=False)
    send_held_messages(db, morning)
    assert morning.sent[1:] == ["⚠️ warning 1", "⚠️ warning 2", "⚠️ warning 3"]
    assert "3" in morning.sent[0]                            # "3 messages from the night" header
    assert load_held(db) == []

    send_held_messages(db, morning)                          # nothing held: sends nothing
    assert len(morning.sent) == 4


# --- delivery times (summary at set times, never on Shabbat) ---
from jobalert.delivery import in_shabbat, load_last_delivery, save_last_delivery, should_deliver  # noqa: E402

DELIVERY = {"enabled": True, "times": ["09:00", "17:00"], "timezone": "Asia/Jerusalem",
            "shabbat": {"enabled": True, "start": "fri 15:00", "end": "sat 20:30"}}


def test_delivers_once_per_delivery_time():
    # Wed 2026-11-25, Israel = UTC+2
    assert not should_deliver(DELIVERY, utc(2026, 11, 25, 7, 0), utc(2026, 11, 25, 11, 0))   # 13:00, sent at 09:00
    assert should_deliver(DELIVERY, utc(2026, 11, 25, 7, 0), utc(2026, 11, 25, 15, 0))       # 17:00 run
    assert not should_deliver(DELIVERY, utc(2026, 11, 25, 15, 0), utc(2026, 11, 25, 19, 0))  # 21:00
    assert not should_deliver(DELIVERY, utc(2026, 11, 25, 15, 0), utc(2026, 11, 25, 23, 0))  # 01:00
    assert not should_deliver(DELIVERY, utc(2026, 11, 25, 15, 0), utc(2026, 11, 26, 3, 0))   # 05:00
    assert should_deliver(DELIVERY, utc(2026, 11, 25, 15, 0), utc(2026, 11, 26, 7, 0))       # 09:00 next day
    assert not should_deliver(DELIVERY, None, utc(2026, 11, 25, 23, 0))                     # first run: wait for next time


def test_summer_time_runs_an_hour_later_still_deliver():
    # July: Israel = UTC+3, the 07:00 UTC run is at 10:00
    assert should_deliver(DELIVERY, utc(2026, 7, 1, 15, 0), utc(2026, 7, 2, 7, 0))


def test_shabbat_holds_until_saturday_night():
    last = utc(2026, 11, 27, 7, 0)                                                  # Fri 09:00 summary
    assert in_shabbat(DELIVERY, utc(2026, 11, 27, 15, 0).astimezone(__import__("zoneinfo").ZoneInfo("Asia/Jerusalem")))
    assert not should_deliver(DELIVERY, last, utc(2026, 11, 27, 15, 0))           # Fri 17:00
    assert not should_deliver(DELIVERY, last, utc(2026, 11, 28, 7, 0))            # Sat 09:00
    assert not should_deliver(DELIVERY, last, utc(2026, 11, 28, 15, 0))           # Sat 17:00
    assert should_deliver(DELIVERY, last, utc(2026, 11, 28, 19, 0))               # Sat 21:00
    assert not should_deliver(DELIVERY, utc(2026, 11, 28, 19, 0), utc(2026, 11, 29, 3, 0))  # Sun 05:00


def test_disabled_delivery_sends_every_run():
    assert should_deliver({**DELIVERY, "enabled": False}, utc(2026, 11, 25, 11, 0), utc(2026, 11, 25, 11, 5))
    assert should_deliver(None, None, utc(2026, 11, 28, 9, 0))


def test_last_delivery_round_trip():
    db = FakeDb()
    assert load_last_delivery(db) is None
    save_last_delivery(db, utc(2026, 11, 25, 7, 0))
    assert load_last_delivery(db) == utc(2026, 11, 25, 7, 0)


def test_digest_header_then_silent_alerts():
    from jobalert.run import send_pending_alerts

    class Db:
        def pending_alerts(self, limit, require_evaluation, no_ai):
            return [{"id": i, "source": "lever", "company": "Mobileye", "title": f"Job {i}", "location": "Ramat Gan",
                     "url": "https://x", "fingerprint": f"f{i}", "workplace_type": None, "score": 70,
                     "decision": "alert", "reason_he": None, "gaps_he": None, "eval_mode": None} for i in (1, 2)]
        def claim_alert(self, *a): return 1
        def complete_alert(self, *a): pass

    class Tg(RecordingTelegram):
        def _call(self, method, payload):
            self.sent.append((payload["text"], payload.get("disable_notification", False)))
            return {"message_id": len(self.sent)}

    tg = Tg(quiet=False)
    assert send_pending_alerts(Db(), tg, 40, True, digest=True) == 2
    assert "2" in tg.sent[0][0] and tg.sent[0][1] is False
    assert [silent for _, silent in tg.sent[1:]] == [True, True]
