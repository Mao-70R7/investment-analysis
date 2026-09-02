from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_RAW_ROOT = PROJECT_ROOT / "data" / "raw"
DEFAULT_RAW_INDEX_DB = PROJECT_ROOT / "data" / "advisor_monitor.sqlite"

RAW_SNAPSHOT_COLUMNS = [
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync raw snapshot manifests into advisor_monitor.sqlite.")
    parser.add_argument("--raw-root", type=Path, default=DEFAULT_RAW_ROOT, help="Root directory containing raw manifests.")
    parser.add_argument("--raw-index-db", type=Path, default=DEFAULT_RAW_INDEX_DB, help="advisor_monitor SQLite path.")
    parser.add_argument(
        "--channel",
        action="append",
        default=[],
        help="Only sync selected channel_id. Can be passed multiple times.",
    )
    return parser.parse_args()


def iter_manifest_paths(raw_root: Path) -> list[Path]:
    return sorted(path for path in raw_root.glob("**/_manifest.json") if path.is_file())


def normalize_raw_path(value: Any, manifest_path: Path) -> str:
    raw_text = str(value or "")
    if not raw_text:
        return raw_text
    raw_path = Path(raw_text)
    if not raw_path.is_absolute():
        raw_path = (manifest_path.parent / raw_path).resolve()
    return str(raw_path)


def normalize_snapshot(raw_row: dict[str, Any], manifest_path: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    summary = manifest.get("summary") if isinstance(manifest.get("summary"), dict) else {}
    return {
        "snapshot_id": raw_row["snapshot_id"],
        "channel_id": raw_row.get("channel_id") or manifest.get("channel_id") or summary.get("channel_id"),
        "collector_name": raw_row.get("collector_name") or "",
        "access_level": raw_row.get("access_level") or "public",
        "captured_at": raw_row.get("captured_at") or manifest.get("captured_at") or summary.get("captured_at") or "",
        "source_url": raw_row.get("source_url"),
        "http_status": raw_row.get("http_status"),
        "raw_path": normalize_raw_path(raw_row.get("raw_path"), manifest_path),
        "content_type": raw_row.get("content_type"),
        "content_hash": raw_row.get("content_hash") or "",
        "parse_status": raw_row.get("parse_status") or "success",
    }


def upsert_raw_snapshot(conn: sqlite3.Connection, row: dict[str, Any]) -> str:
    existing = conn.execute(
        "SELECT channel_id, collector_name, captured_at, raw_path, content_hash, parse_status "
        "FROM raw_snapshot WHERE snapshot_id = ?",
        [row["snapshot_id"]],
    ).fetchone()
    if existing is None:
        status = "inserted"
    elif tuple(existing) == (
        row["channel_id"],
        row["collector_name"],
        row["captured_at"],
        row["raw_path"],
        row["content_hash"],
        row["parse_status"],
    ):
        status = "unchanged"
    else:
        status = "updated"

    placeholders = ", ".join("?" for _ in RAW_SNAPSHOT_COLUMNS)
    column_sql = ", ".join(RAW_SNAPSHOT_COLUMNS)
    update_sql = ", ".join(f"{column}=excluded.{column}" for column in RAW_SNAPSHOT_COLUMNS[1:])
    conn.execute(
        f"""
        INSERT INTO raw_snapshot ({column_sql})
        VALUES ({placeholders})
        ON CONFLICT(snapshot_id) DO UPDATE SET
            {update_sql}
        """,
        [row.get(column) for column in RAW_SNAPSHOT_COLUMNS],
    )
    return status


def main() -> None:
    args = parse_args()
    selected_channels = set(args.channel)
    conn = sqlite3.connect(args.raw_index_db)
    counters = {
        "manifest_files": 0,
        "raw_rows": 0,
        "inserted": 0,
        "updated": 0,
        "unchanged": 0,
        "skipped_channel": 0,
        "missing_snapshot_id": 0,
    }

    try:
        for manifest_path in iter_manifest_paths(args.raw_root):
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            summary = manifest.get("summary") if isinstance(manifest.get("summary"), dict) else {}
            manifest_channel = manifest.get("channel_id") or summary.get("channel_id")
            if selected_channels and manifest_channel and manifest_channel not in selected_channels:
                counters["skipped_channel"] += 1
                continue
            counters["manifest_files"] += 1
            for raw_row in manifest.get("raw_snapshots") or []:
                if not raw_row.get("snapshot_id"):
                    counters["missing_snapshot_id"] += 1
                    continue
                row = normalize_snapshot(raw_row, manifest_path, manifest)
                if selected_channels and row["channel_id"] not in selected_channels:
                    counters["skipped_channel"] += 1
                    continue
                status = upsert_raw_snapshot(conn, row)
                counters[status] += 1
                counters["raw_rows"] += 1
        conn.commit()
    finally:
        conn.close()

    print(json.dumps(counters, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
