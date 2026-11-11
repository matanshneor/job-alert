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
