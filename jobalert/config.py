"""Load YAML config files and environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = PROJECT_ROOT / "config"

# Local development reads ".env"; on Railway the variables come from the service settings.
load_dotenv(PROJECT_ROOT / ".env")


@dataclass(frozen=True)
class CompanyConfig:
    slug: str
    name: str
    ats: str
    params: dict[str, Any] = field(default_factory=dict)
    aliases: tuple[str, ...] = ()
    enabled: bool = True

    @property
    def source_key(self) -> str:
        return f"{self.ats}:{self.slug}"


@dataclass(frozen=True)
class Settings:
    http: dict[str, Any]
    location: dict[str, Any]
    alerts: dict[str, Any]
    closing: dict[str, Any]
    ai: dict[str, Any]
    email: dict[str, Any] = field(default_factory=dict)

    @property
    def email_sources(self) -> dict[str, str]:
        """Email source key -> display name. These jobs skip the AI (emails have no description)."""
        return {s["key"]: s["name"] for s in (self.email.get("sources") or [])}


def load_companies(path: Path | None = None) -> list[CompanyConfig]:
    data = yaml.safe_load((path or CONFIG_DIR / "companies.yaml").read_text(encoding="utf-8"))
    companies = []
    seen: set[str] = set()
    for entry in data.get("companies", []):
        slug = entry["slug"]
        if slug in seen:
            raise ValueError(f"Duplicate company slug in companies.yaml: {slug}")
        seen.add(slug)
        companies.append(
            CompanyConfig(
                slug=slug,
                name=entry.get("name", slug),
                ats=entry["ats"],
                params=entry.get("params") or {},
                aliases=tuple(entry.get("aliases") or ()),
                enabled=entry.get("enabled", True),
            )
        )
    return companies


def load_settings(path: Path | None = None) -> Settings:
    data = yaml.safe_load((path or CONFIG_DIR / "settings.yaml").read_text(encoding="utf-8"))
    return Settings(
        http=data["http"],
        location=data["location"],
        alerts=data["alerts"],
        closing=data["closing"],
        ai=data.get("ai") or {"enabled": False},
        email=data.get("email") or {"enabled": False},
    )


def require_env(name: str) -> str:
    """Return an environment variable or fail with a clear message (never prints the value)."""
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(
            f"Missing environment variable {name}. "
            f"Locally: add it to the .env file. On Railway: add it under the service's Variables tab."
        )
    return value
