"""Cross-source duplicate detection.

The same job can arrive from two sources (e.g. Wix's own board and a LinkedIn email).
Each job gets a fingerprint built from the normalized company, title and city.
Before alerting, we skip a job if an alert with the same fingerprint was already sent from a
*different* source. Two postings in the same source are different jobs, even with the same title.
"""
from __future__ import annotations

import re
import unicodedata

from .config import CompanyConfig

CITY_ALIASES = {
    "tel aviv yafo": "tel aviv",
    "tel aviv jaffa": "tel aviv",
    "tlv": "tel aviv",
    "petach tikva": "petah tikva",
    "petah tiqva": "petah tikva",
    "petach tiqva": "petah tikva",
    "herzliya pituach": "herzliya",
    "herzelia": "herzliya",
    "raanana": "raanana",
    "beersheba": "beer sheva",
    "beer sheba": "beer sheva",
}

# Noise removed from titles: requisition numbers, gender markers, work-mode tags.
_TITLE_NOISE = [
    r"\(\s*\d{4,}\s*\)",                 # "(20001596)"
    r"\b[mfwdx]\s*/\s*[mfwdx](\s*/\s*[mfwdx])?\b",  # "m/f", "f/m/d"
    r"\((hybrid|remote|on[\s-]?site)\)",
]


def _basic(text: str) -> str:
    # Drop accents/niqqud but keep Hebrew letters (Jobify emails are often in Hebrew).
    text = "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))
    text = text.lower().replace("'", "").replace("’", "")
    text = re.sub(r"[^a-z0-9֐-׿]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_title(title: str) -> str:
    t = title.lower()
    for pattern in _TITLE_NOISE:
        t = re.sub(pattern, " ", t)
    return _basic(t)


def normalize_city(location: str | None) -> str:
    if not location:
        return ""
    location = re.sub(r"^[A-Z]{2}-", "", location.strip())   # Comeet style "IL-Haifa"
    first = re.split(r"[,/|(;]", location)[0]
    city = _basic(first)
    city = re.sub(r"\b(district|area|metropolitan|israel)\b", " ", city)
    city = re.sub(r"\s+", " ", city).strip()
    return CITY_ALIASES.get(city, city)


def build_alias_map(companies: list[CompanyConfig]) -> dict[str, str]:
    """Map every normalized alias (and name/slug) to the company slug."""
    alias_map: dict[str, str] = {}
    for c in companies:
        for alias in (c.slug, c.name, *c.aliases):
            alias_map[_basic(alias)] = c.slug
    return alias_map


def canonical_company(name: str, alias_map: dict[str, str]) -> str:
    key = _basic(name)
    return alias_map.get(key, key)


def fingerprint(company_slug: str, title: str, location: str | None) -> str:
    return f"{company_slug}|{normalize_title(title)}|{normalize_city(location)}"
