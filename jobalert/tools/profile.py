"""Upload or show the candidate profile used by the AI.

    uv run python -m jobalert.tools.profile --upload   # cv/profile.yaml + cv/cv.txt -> database
    uv run python -m jobalert.tools.profile --show
"""
from __future__ import annotations

import argparse

from ..ai.profile import load_local_profile
from ..config import require_env
from ..db import Database


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--upload", action="store_true")
    p.add_argument("--show", action="store_true")
    args = p.parse_args()
    db = Database(require_env("DATABASE_URL"))
    if args.upload:
        profile = load_local_profile()
        db.set_profile(profile.yaml_text, profile.cv_text)
        print("Profile uploaded. Areas and years:")
        for area, years in profile.area_years.items():
            print(f"  {area:32s} {years:g}")
    if args.show or not args.upload:
        row = db.get_profile()
        print(f"Stored profile updated at {row['updated_at']}" if row else "No profile stored yet.")
    db.close()


if __name__ == "__main__":
    main()
