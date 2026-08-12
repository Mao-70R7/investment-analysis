from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Any


def init_sqlite(db_path: Path, schema_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(schema_path.read_text(encoding="utf-8"))


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def upsert_raw_snapshot(db_path: Path, snapshot: dict[str, Any]) -> None:
    keys = [
        "snapshot_id",
        "channel_id",
        "collector_name",
        "access_level",
        "captured_at",
        "source_url",
        "http_status",
        "raw_path",
        "content_type",
        "content_hash",
        "parse_status",
    ]
    values = [snapshot.get(key) for key in keys]
    placeholders = ",".join("?" for _ in keys)
    assignments = ",".join(f"{key}=excluded.{key}" for key in keys[1:])
    sql = (
        f"INSERT INTO raw_snapshot ({','.join(keys)}) VALUES ({placeholders}) "
        f"ON CONFLICT(snapshot_id) DO UPDATE SET {assignments}"
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(sql, values)


def upsert_channel(db_path: Path, channel: dict[str, Any]) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO channel (
                channel_id, channel_name, provider_type, official_site_url, login_required_level
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(channel_id) DO UPDATE SET
                channel_name=excluded.channel_name,
                provider_type=excluded.provider_type,
                official_site_url=excluded.official_site_url,
                login_required_level=excluded.login_required_level,
                updated_at=CURRENT_TIMESTAMP
            """,
            [
                channel["channel_id"],
                channel["channel_name"],
                channel["provider_type"],
                channel["official_site_url"],
                channel["login_required_level"],
            ],
        )

