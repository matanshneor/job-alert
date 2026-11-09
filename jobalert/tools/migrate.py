"""Apply SQL files from migrations/ in order, once each.

Usage: uv run python -m jobalert.tools.migrate
"""
from __future__ import annotations

from ..config import PROJECT_ROOT, require_env
from ..db import Database


def main() -> None:
    db = Database(require_env("DATABASE_URL"))
    db.conn.execute(
        """
        create table if not exists schema_migrations (
          filename text primary key, applied_at timestamptz not null default now()
        );
        alter table schema_migrations enable row level security;
        """
    )
    applied = {r["filename"] for r in db.conn.execute("select filename from schema_migrations").fetchall()}
    for path in sorted((PROJECT_ROOT / "migrations").glob("*.sql")):
        if path.name in applied:
            print(f"already applied: {path.name}")
            continue
        with db.conn.transaction():
            db.conn.execute(path.read_text(encoding="utf-8"))
            db.conn.execute("insert into schema_migrations (filename) values (%s)", (path.name,))
        print(f"applied: {path.name}")
    db.close()


if __name__ == "__main__":
    main()
