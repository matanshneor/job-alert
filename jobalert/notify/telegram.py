"""Telegram Bot API: send alerts, edit them when a job closes, send warnings."""
from __future__ import annotations

import html
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

log = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/{method}"
TAILOR_BUTTON_TEXT = "התאם קו״ח"
CLOSED_LINE = "\n\n🔴 <b>המשרה נסגרה</b>"
WORKPLACE_HE = {"remote": "Remote", "hybrid": "Hybrid", "onsite": "On-site"}


@dataclass
class AlertContent:
    """What goes into one alert. Score/reason come from the AI in phase 2."""
    job_id: int
    company: str
    title: str
    location: str | None
    workplace_type: str | None
    url: str
    score: int | None = None
    borderline: bool = False
    borderline_gaps: str | None = None   # Hebrew, e.g. "חסרות 2 שנות ניסיון ב-SQL"
    reason_he: str | None = None
    is_test: bool = False
    email_source: str | None = None      # e.g. "LinkedIn" for jobs from alert emails
    estimated: bool = False              # score from the title only (no description was found)


def format_alert(a: AlertContent) -> str:
    e = html.escape
    location = a.location or "לא צוין"
    if a.workplace_type in WORKPLACE_HE:
        location = f"{location} · {WORKPLACE_HE[a.workplace_type]}"
    lines = []
    if a.is_test:
        lines.append("🧪 <i>הודעת בדיקה</i>")
    if a.score is not None and a.estimated:
        score_line = f"⭐ ציון משוער: {a.score}/100 (לפי הכותרת בלבד)"
    elif a.score is not None:
        score_line = f"⭐ ציון: {a.score}/100"
    elif a.email_source:
        score_line = f"📬 ממייל של {e(a.email_source)} · בלי ציון AI"
    else:
        score_line = "⭐ ציון: — (עדיין בלי AI)"
    lines += [
        f"🏢 <b>{e(a.company)}</b>",
        f"💼 <b>{e(a.title)}</b>",
        f"📍 {e(location)}",
        score_line,
    ]
    if a.email_source and a.score is not None:
        lines.append(f"📬 ממייל של {e(a.email_source)}")
    if a.borderline:
        gaps = f": {e(a.borderline_gaps)}" if a.borderline_gaps else ""
        lines.append(f"⚠️ <b>Borderline</b>{gaps}")
    if a.reason_he:
        lines.append(f"📝 {e(a.reason_he)}")
    lines.append(f'🔗 <a href="{e(a.url, quote=True)}">למשרה</a>')
    return "\n".join(lines)


def tailor_keyboard(job_id: int) -> dict[str, Any]:
    # The button is handled by the bot service added in phase 5.
    return {"inline_keyboard": [[{"text": TAILOR_BUTTON_TEXT, "callback_data": f"tailor:{job_id}"}]]}


class TelegramClient:
    def __init__(self, token: str, chat_id: str, delay_seconds: float = 1.1,
                 tailor_button: bool = False, quiet: bool = False) -> None:
        self._token = token
        # Quiet hours: send_message keeps the text in self.held instead of sending it.
        self.quiet = quiet
        self.held: list[str] = []
        self._chat_id = chat_id
        self._delay = delay_seconds
        self._tailor_button = tailor_button
        self._client = httpx.Client(timeout=30)
        self._last_send = 0.0

    def _call(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = API.format(token=self._token, method=method)
        for attempt in range(4):
            wait = self._delay - (time.monotonic() - self._last_send)
            if wait > 0:
                time.sleep(wait)
            self._last_send = time.monotonic()
            try:
                response = self._client.post(url, json=payload)
            except httpx.TransportError as exc:
                # Never log the URL: it contains the bot token.
                log.warning("Telegram network error (attempt %d): %s", attempt + 1, type(exc).__name__)
                time.sleep(2 ** attempt)
                continue
            data = response.json()
            if data.get("ok"):
                return data["result"]
            if response.status_code == 429:
                time.sleep(int((data.get("parameters") or {}).get("retry_after", 5)) + 1)
                continue
            raise RuntimeError(f"Telegram {method} failed: {data.get('description')}")
        raise RuntimeError(f"Telegram {method} failed after retries")

    def send_message(self, text: str, reply_markup: dict[str, Any] | None = None) -> int:
        if self.quiet:
            self.held.append(text)
            log.info("Quiet hours: message held for the morning")
            return 0
        payload: dict[str, Any] = {
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        return self._call("sendMessage", payload)["message_id"]

    def send_alert(self, content: AlertContent) -> tuple[int, str]:
        text = format_alert(content)
        keyboard = tailor_keyboard(content.job_id) if self._tailor_button else None
        message_id = self.send_message(text, keyboard)
        return message_id, text

    def mark_closed(self, message_id: int, original_text: str) -> None:
        """Append the 'closed' line and remove the tailoring button."""
        if CLOSED_LINE.strip() in original_text:
            return
        try:
            self._call("editMessageText", {
                "chat_id": self._chat_id,
                "message_id": message_id,
                "text": original_text + CLOSED_LINE,
                "parse_mode": "HTML",
                "link_preview_options": {"is_disabled": True},
            })
        except RuntimeError as exc:
            # Old or deleted messages can't be edited; the DB still marks the job closed.
            log.warning("Could not edit message %s: %s", message_id, exc)

    def get_updates(self) -> list[dict[str, Any]]:
        return self._call("getUpdates", {"timeout": 0})

    def close(self) -> None:
        self._client.close()
