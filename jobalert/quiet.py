"""Quiet hours: no Telegram messages during the night (Israel time).

During quiet hours the run still collects and evaluates jobs. New alerts stay pending and
other messages (warnings, summaries) are held in the database; both go out on the first
run after quiet hours end. Editing an existing message (a job closed) is silent, so it is
still allowed.
"""
from __future__ import annotations

from datetime import datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

HELD_KEY = "telegram:held"


def _parse(value: str) -> time:
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))


def is_quiet(cfg: dict[str, Any] | None, now: datetime | None = None) -> bool:
    """True if `now` (default: current time) falls in the configured quiet window."""
    if not cfg or not cfg.get("enabled", True):
        return False
    now = now or datetime.now(timezone.utc)
    local = now.astimezone(ZoneInfo(cfg.get("timezone", "Asia/Jerusalem"))).time()
    start, end = _parse(cfg["start"]), _parse(cfg["end"])
    if start <= end:
        return start <= local < end
    return local >= start or local < end   # window crosses midnight, e.g. 23:00-07:00


def load_held(db: Any) -> list[str]:
    """Messages held during earlier quiet runs, oldest first."""
    row = db.get_source_state(HELD_KEY)
    return list(((row or {}).get("meta") or {}).get("messages") or [])


def save_held(db: Any, messages: list[str]) -> None:
    db.record_source_success(HELD_KEY, len(messages), {"messages": messages})
