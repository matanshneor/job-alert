"""Read-only Gmail access over IMAP, using an app password.

Read-only by design: the mailbox is opened with EXAMINE (readonly=True) and bodies are fetched
with BODY.PEEK, so nothing is marked as read, moved or deleted.
Gmail-specific IMAP extensions used: X-GM-RAW (Gmail search syntax) and X-GM-MSGID (stable id).
"""
from __future__ import annotations

import email
import email.policy
import email.utils
import imaplib
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage

log = logging.getLogger(__name__)

IMAP_HOST = "imap.gmail.com"
_LIST_RE = re.compile(rb'^\((?P<flags>[^)]*)\) (?:"[^"]*"|NIL) (?P<name>.+)$')
_MSGID_RE = re.compile(rb"X-GM-MSGID (\d+)")
_UID_RE = re.compile(rb"UID (\d+)")


@dataclass
class MailMessage:
    gmail_id: str               # X-GM-MSGID, stable across folders
    sender: str
    subject: str
    received_at: datetime | None
    text: str                   # text/plain body ("" if the email has none)
    html: str                   # text/html body ("" if the email has none)
    raw: bytes


class GmailReader:
    def __init__(self, address: str, app_password: str, timeout: float = 60) -> None:
        self._imap = imaplib.IMAP4_SSL(IMAP_HOST, timeout=timeout)
        try:
            self._imap.login(address, app_password.replace(" ", ""))
        except imaplib.IMAP4.error:
            # Don't chain the original error: keep credentials-related details out of logs.
            raise RuntimeError(
                "Gmail login failed. Check GMAIL_ADDRESS and GMAIL_APP_PASSWORD "
                "(a 16-letter app password, not the normal Google password)."
            ) from None
        mailbox = self._all_mail_name()
        typ, _ = self._imap.select(mailbox, readonly=True)
        if typ != "OK":
            raise RuntimeError(f"Could not open Gmail mailbox {mailbox!r}")

    def _all_mail_name(self) -> str:
        """Find "All Mail" by its \\All flag (its display name depends on the Gmail UI language)."""
        typ, lines = self._imap.list()
        if typ == "OK":
            for line in lines:
                m = _LIST_RE.match(line or b"")
                if m and b"\\All" in m.group("flags").split():
                    return m.group("name").decode()
        log.warning("No \\All mailbox found; falling back to INBOX")
        return "INBOX"

    def search(self, gmail_query: str) -> list[str]:
        """UIDs matching a Gmail search query (same syntax as the Gmail search box)."""
        if '"' in gmail_query or "\\" in gmail_query:
            raise ValueError("Gmail query must not contain quotes or backslashes")
        typ, data = self._imap.uid("SEARCH", "X-GM-RAW", f'"{gmail_query}"')
        if typ != "OK":
            raise RuntimeError(f"Gmail search failed: {data!r}")
        return [u.decode() for u in (data[0] or b"").split()]

    def gmail_ids(self, uids: list[str]) -> dict[str, str]:
        """Map UID -> X-GM-MSGID without downloading bodies."""
        if not uids:
            return {}
        typ, data = self._imap.uid("FETCH", ",".join(uids), "(X-GM-MSGID)")
        if typ != "OK":
            raise RuntimeError(f"Gmail fetch failed: {data!r}")
        result: dict[str, str] = {}
        for item in data:
            line = item[0] if isinstance(item, tuple) else item
            if not line:
                continue
            uid, msgid = _UID_RE.search(line), _MSGID_RE.search(line)
            if uid and msgid:
                result[uid.group(1).decode()] = msgid.group(1).decode()
        return result

    def fetch(self, uid: str) -> MailMessage:
        typ, data = self._imap.uid("FETCH", uid, "(X-GM-MSGID BODY.PEEK[])")
        if typ != "OK":
            raise RuntimeError(f"Gmail fetch failed: {data!r}")
        part = next((d for d in data if isinstance(d, tuple)), None)
        if part is None:
            raise RuntimeError(f"Gmail message {uid} not found")
        header, raw = part
        msgid = _MSGID_RE.search(header)
        return parse_message(raw, msgid.group(1).decode() if msgid else f"uid:{uid}")

    def close(self) -> None:
        try:
            self._imap.close()
            self._imap.logout()
        except (imaplib.IMAP4.error, OSError):
            pass


def parse_message(raw: bytes, gmail_id: str) -> MailMessage:
    msg: EmailMessage = email.message_from_bytes(raw, policy=email.policy.default)  # type: ignore[assignment]
    received_at = None
    if msg["Date"]:
        try:
            received_at = email.utils.parsedate_to_datetime(str(msg["Date"]))
        except (TypeError, ValueError):
            pass
    return MailMessage(
        gmail_id=gmail_id,
        sender=email.utils.parseaddr(str(msg["From"] or ""))[1].lower(),
        subject=str(msg["Subject"] or ""),
        received_at=received_at,
        text=_body(msg, "plain"),
        html=_body(msg, "html"),
        raw=raw,
    )


def _body(msg: EmailMessage, subtype: str) -> str:
    part = msg.get_body(preferencelist=(subtype,))
    if part is None or part.get_content_subtype() != subtype:
        return ""
    try:
        return part.get_content()
    except (LookupError, UnicodeDecodeError):
        payload = part.get_payload(decode=True) or b""
        return payload.decode("utf-8", errors="replace")
