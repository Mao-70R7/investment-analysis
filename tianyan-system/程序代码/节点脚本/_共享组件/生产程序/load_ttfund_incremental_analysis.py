from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
SCRIPT_DIR = PROJECT_ROOT / "节点脚本" / "_共享组件" / "生产程序"
sys.path.insert(0, str(SCRIPT_DIR))

import load_analysis_zh_current_sqlite as loader  # noqa: E402


DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
DEFAULT_SCHEMA_PATH = PROJECT_ROOT / "schemas" / "analysis_zh_current.sql"
DEFAULT_NORMALIZED_ROOT = PROJECT_ROOT / "data" / "normalized" / "ttfund"
CHANNEL_ID = "ttfund"
STRATEGY_DAILY_TABLE = "\u7b56\u7565\u65e5\u5ea6\u4e1a\u7ee9"
CHANNEL_COLUMN = "\u6e20\u9053ID"
TRADE_DATE_COLUMN = "\u4ea4\u6613\u65e5\u671f"


def quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Upsert one normalized TTFund run into analysis DB.")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--schema-path", type=Path, default=DEFAULT_SCHEMA_PATH)
    parser.add_argument("--normalized-root", type=Path, default=DEFAULT_NORMALIZED_ROOT)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--target-trade-date")
    parser.add_argument("--result-path", type=Path)
    return parser.parse_args()


def find_run_summary(normalized_root: Path, run_id: str) -> Path:
    matches = sorted((normalized_root / "collection_summary").glob(f"*/{run_id}.json"))
    if not matches:
        raise SystemExit(f"collection summary not found for run_id={run_id}")
    return matches[-1]


def run_files(normalized_root: Path, run_id: str) -> dict[str, list[Path]]:
    result: dict[str, list[Path]] = {}
    for entity_dir in sorted(path for path in normalized_root.iterdir() if path.is_dir()):
        matches = sorted(entity_dir.glob(f"*/{run_id}.jsonl"))
        if matches:
            result[entity_dir.name] = matches
    return result


def count_jsonl(path: Path) -> int:
    with path.open("r", encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def scalar(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> Any:
    return conn.execute(sql, params).fetchone()[0]


def strategy_daily_latest_date_sql() -> str:
    return (
        f"SELECT MAX({quote_identifier(TRADE_DATE_COLUMN)}) "
        f"FROM {quote_identifier(STRATEGY_DAILY_TABLE)} "
        f"WHERE {quote_identifier(CHANNEL_COLUMN)}=?"
    )


def strategy_daily_target_rows_sql() -> str:
    return (
        f"SELECT COUNT(*) FROM {quote_identifier(STRATEGY_DAILY_TABLE)} "
        f"WHERE {quote_identifier(CHANNEL_COLUMN)}=? "
        f"AND {quote_identifier(TRADE_DATE_COLUMN)}=?"
    )


def main() -> None:
    args = parse_args()
    summary_path = find_run_summary(args.normalized_root, args.run_id)
    summary_payload = json.loads(summary_path.read_text(encoding="utf-8-sig"))
    files_by_entity = run_files(args.normalized_root, args.run_id)
    if not files_by_entity:
        raise SystemExit(f"no normalized entity files found for run_id={args.run_id}")

    original_entity_files = loader.entity_files
    original_summary_files = loader.summary_files

    def only_current_run_files(channel_id: str, entity_name: str) -> list[Path]:
        if channel_id != CHANNEL_ID:
            return []
        return files_by_entity.get(entity_name, [])

    def only_current_run_summary(channel_id: str) -> dict[str, dict[str, Any]]:
        if channel_id != CHANNEL_ID:
            return {}
        return {args.run_id: summary_payload}

    loader.entity_files = only_current_run_files
    loader.summary_files = only_current_run_summary
    try:
        conn = loader.init_db(args.db_path, args.schema_path, keep_existing_db=True)
        try:
            if conn.in_transaction:
                conn.commit()
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=120000")
            conn.execute("BEGIN IMMEDIATE")
            try:
                counters = loader.import_channels(conn, [CHANNEL_ID])
                latest_daily_date = scalar(conn, strategy_daily_latest_date_sql(), (CHANNEL_ID,))
                target_daily_rows = (
                    scalar(conn, strategy_daily_target_rows_sql(), (CHANNEL_ID, args.target_trade_date))
                    if args.target_trade_date
                    else None
                )
                if args.target_trade_date and int(target_daily_rows or 0) <= 0:
                    raise RuntimeError(
                        "incremental load wrote zero strategy daily rows for "
                        f"target_trade_date={args.target_trade_date}; run_id={args.run_id}"
                    )

                result = {
                    "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "run_id": args.run_id,
                    "target_trade_date": args.target_trade_date,
                    "summary_path": str(summary_path.resolve()),
                    "input_files": {
                        entity: [{"path": str(path.resolve()), "rows": count_jsonl(path)} for path in paths]
                        for entity, paths in sorted(files_by_entity.items())
                    },
                    "counters": dict(sorted(counters.items())),
                    "db_latest_strategy_daily_date": latest_daily_date,
                    "db_target_strategy_daily_rows": target_daily_rows,
                }
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
        finally:
            conn.close()
    finally:
        loader.entity_files = original_entity_files
        loader.summary_files = original_summary_files

    if args.result_path:
        args.result_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.result_path.with_suffix(args.result_path.suffix + ".tmp")
        temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, args.result_path)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
