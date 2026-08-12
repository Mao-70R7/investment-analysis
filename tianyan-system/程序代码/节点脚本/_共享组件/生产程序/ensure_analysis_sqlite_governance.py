from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
ANALYSIS_SCHEMA_USER_VERSION = 20260712


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ensure analysis SQLite governance metadata is registered.")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.db_path.exists():
        raise SystemExit(f"missing db: {args.db_path}")

    with sqlite3.connect(args.db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(f"PRAGMA user_version = {ANALYSIS_SCHEMA_USER_VERSION}")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS "schema_migrations" (
              "version" INTEGER PRIMARY KEY,
              "name" TEXT NOT NULL,
              "applied_at" TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO "schema_migrations" ("version", "name", "applied_at")
            VALUES (?, ?, ?)
            """,
            (
                ANALYSIS_SCHEMA_USER_VERSION,
                "analysis_zh_current_governance_baseline",
                datetime.now().astimezone().isoformat(timespec="seconds"),
            ),
        )
        quick_check = conn.execute("PRAGMA quick_check").fetchone()[0]
        foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        user_version = conn.execute("PRAGMA user_version").fetchone()[0]
        conn.commit()

    print(
        {
            "status": "completed",
            "db_path": str(args.db_path),
            "quick_check": quick_check,
            "foreign_keys": foreign_keys,
            "user_version": user_version,
        }
    )


if __name__ == "__main__":
    main()
