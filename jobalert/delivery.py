"""Delivery times: Telegram messages go out only at set times of day, and never on Shabbat.

Runs still happen every 4 hours (collect + evaluate). A run sends only when a delivery time has
passed since the last delivery, so at most one batch per delivery time, whatever the run schedule
or daylight saving time. During Shabbat nothing is sent; the batch waits for the first run after
Shabbat ends. Between deliveries new alerts stay pending and other messages are held
(see quiet.py), exactly like quiet hours.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

STATE_KEY = "telegram:delivery"
WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def _hm(value: str) -> tuple[int, int]:
    hours, minutes = value.split(":")
    return int(hours), int(minutes)


def _week_minute(value: str) -> int:
    """'fri 15:00' -> minutes since Monday 00:00."""
    day, clock = value.lower().split()
    h, m = _hm(clock)
    return WEEKDAYS[day[:3]] * 1440 + h * 60 + m


def in_shabbat(cfg: dict[str, Any], local: datetime) -> bool:
    window = cfg.get("shabbat")
    if not window or not window.get("enabled", True):
        return False
    start, end = _week_minute(window["start"]), _week_minute(window["end"])
    now = local.weekday() * 1440 + local.hour * 60 + local.minute
    if start <= end:
        return start <= now < end
    return now >= start or now < end


def in_night(cfg: dict[str, Any], local: datetime) -> bool:
    """No summary at night, even if one is due (e.g. the batch held over Shabbat)."""
    window = cfg.get("night")
    if not window:
        return False
    start, end = _hm(window["start"]), _hm(window["end"])
    now = (local.hour, local.minute)
    if start <= end:
        return start <= now < end
    return now >= start or now < end


def latest_slot(cfg: dict[str, Any], local: datetime) -> datetime:
    """The most recent delivery time at or before `local`."""
    times = sorted(_hm(t) for t in cfg["times"])
    for days_back in range(0, 8):
        day = (local - timedelta(days=days_back)).date()
        for h, m in reversed(times):
            slot = datetime(day.year, day.month, day.day, h, m, tzinfo=local.tzinfo)
            if slot <= local:
                return slot
    raise ValueError("delivery.times is empty")


def should_deliver(cfg: dict[str, Any] | None, last_delivery: datetime | None,
                   now: datetime | None = None) -> bool:
    """True if this run should send to Telegram."""
    if not cfg or not cfg.get("enabled", True):
        return True
    now = now or datetime.now(timezone.utc)
    local = now.astimezone(ZoneInfo(cfg.get("timezone", "Asia/Jerusalem")))
    if in_shabbat(cfg, local) or in_night(cfg, local):
        return False
    if last_delivery is None:
        return False   # first run with delivery times: start counting from now (no 1 a.m. summary)
    return last_delivery < latest_slot(cfg, local)


def load_last_delivery(db: Any) -> datetime | None:
    row = db.get_source_state(STATE_KEY)
    value = ((row or {}).get("meta") or {}).get("last")
    return datetime.fromisoformat(value) if value else None


def save_last_delivery(db: Any, when: datetime | None = None) -> None:
    when = when or datetime.now(timezone.utc)
    db.record_source_success(STATE_KEY, 1, {"last": when.isoformat()})
