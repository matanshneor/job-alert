"""Check Gmail access and optionally save sample alert emails (for building parsers).

    uv run python -m jobalert.tools.gmail_check                    # login + count matching emails
    uv run python -m jobalert.tools.gmail_check --save DIR --limit 3

Read-only: nothing in the mailbox is changed or marked as read.
Saved samples contain personal tracking links; keep them out of git (sanitize before using as fixtures).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..config import require_env
from ..gmail.imap import GmailReader

QUERIES = {
    "linkedin": "from:jobalerts-noreply@linkedin.com newer_than:14d",
    "jobify": "from:info@jobify360.co.il newer_than:60d",
}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--save", type=Path, help="directory to save sample emails into")
    p.add_argument("--limit", type=int, default=3, help="newest N emails per source to save")
    args = p.parse_args(argv)

    reader = GmailReader(require_env("GMAIL_ADDRESS"), require_env("GMAIL_APP_PASSWORD"))
    try:
        print("Gmail login OK (read-only).")
        for name, query in QUERIES.items():
            uids = reader.search(query)
            print(f"{name}: {len(uids)} emails match '{query}'")
            if not args.save:
                continue
            out = args.save / name
            out.mkdir(parents=True, exist_ok=True)
            for uid in uids[-args.limit:]:
                m = reader.fetch(uid)
                stem = out / m.gmail_id
                stem.with_suffix(".eml").write_bytes(m.raw)
                print(f"  saved {stem.name}: {m.subject[:70]!r} text={len(m.text)} html={len(m.html)}")
    finally:
        reader.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
