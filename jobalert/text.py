"""Small text helpers."""
from __future__ import annotations

import re

from bs4 import BeautifulSoup


def html_to_text(html: str | None) -> str:
    """Convert job-description HTML to readable plain text."""
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for li in soup.find_all("li"):
        li.insert_before("\n• ")
    text = soup.get_text(separator="\n")
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()
