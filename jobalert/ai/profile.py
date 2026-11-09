"""Candidate profile: structured background (YAML) + CV text."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ..config import PROJECT_ROOT

CV_DIR = PROJECT_ROOT / "cv"


@dataclass(frozen=True)
class Profile:
    yaml_text: str
    cv_text: str

    @property
    def data(self) -> dict[str, Any]:
        return yaml.safe_load(self.yaml_text) or {}

    @property
    def area_years(self) -> dict[str, float]:
        """Years of experience per area, e.g. {"people_management": 3.0}."""
        areas = self.data.get("experience_areas") or {}
        return {name: float(info.get("years") or 0) for name, info in areas.items()}

    def prompt_block(self) -> str:
        """Stable text placed in the system prompt (cached across calls)."""
        return (
            "<candidate_profile>\n" + self.yaml_text.strip() + "\n</candidate_profile>\n\n"
            "<candidate_cv>\n" + self.cv_text.strip() + "\n</candidate_cv>"
        )


def load_local_profile(cv_dir: Path = CV_DIR) -> Profile:
    profile_path, cv_path = cv_dir / "profile.yaml", cv_dir / "cv.txt"
    if not profile_path.exists() or not cv_path.exists():
        raise FileNotFoundError(f"Expected {profile_path} and {cv_path}")
    profile = Profile(profile_path.read_text(encoding="utf-8"), cv_path.read_text(encoding="utf-8"))
    validate_profile(profile)
    return profile


def validate_profile(profile: Profile) -> None:
    years = profile.area_years
    if not years:
        raise ValueError("profile.yaml has no experience_areas")
    for name in years:
        if not name.replace("_", "").isalnum():
            raise ValueError(f"Area names must be snake_case identifiers: {name!r}")


def load_db_profile(db) -> Profile:
    row = db.get_profile()
    if not row:
        raise RuntimeError(
            "No profile in the database. Upload it with: uv run python -m jobalert.tools.profile --upload"
        )
    profile = Profile(row["profile_yaml"], row["cv_text"])
    validate_profile(profile)
    return profile
