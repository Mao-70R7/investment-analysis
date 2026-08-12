from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
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

K_UNIFIED_ID = "\u7edf\u4e00\u7b56\u7565ID"
K_CHANNEL_ID = "\u6e20\u9053ID"
K_CHANNEL_STRATEGY_ID = "\u6e20\u9053\u7b56\u7565ID"
K_TRADE_DATE = "\u4ea4\u6613\u65e5\u671f"
K_NAV = "\u5355\u4f4d\u51c0\u503c"
K_DAILY_RETURN = "\u65e5\u6536\u76ca\u7387_\u767e\u5206\u6bd4"
K_CUMULATIVE_RETURN = "\u7d2f\u8ba1\u6536\u76ca\u7387_\u767e\u5206\u6bd4"
K_BENCHMARK_RETURN = "\u57fa\u51c6\u6536\u76ca\u7387_\u767e\u5206\u6bd4"
K_INDEX_RETURN = "\u6307\u6570\u6536\u76ca\u7387_\u767e\u5206\u6bd4"
K_MAX_DRAWDOWN = "\u6700\u5927\u56de\u64a4_\u767e\u5206\u6bd4"
K_SECTION_NAME = "\u4e1a\u7ee9\u533a\u6bb5\u540d\u79f0"
K_SECTION_TYPE = "\u4e1a\u7ee9\u533a\u6bb5\u7c7b\u578b"
K_SNAPSHOT_ID = "\u539f\u59cb\u5feb\u7167ID"
K_STAT_DATE = "\u7edf\u8ba1\u65e5\u671f"
K_INTERVAL_CODE = "\u533a\u95f4\u4ee3\u7801"
K_INTERVAL_NAME = "\u533a\u95f4\u540d\u79f0"
K_STRATEGY_RETURN = "\u7b56\u7565\u6536\u76ca\u7387_\u767e\u5206\u6bd4"
K_FILE_TYPE = "\u6587\u4ef6\u7c7b\u578b"
K_FILE_PATH = "\u6587\u4ef6\u8def\u5f84"
K_RUN_ID = "\u91c7\u96c6\u6279\u6b21ID"
K_CAPTURED_AT = "\u91c7\u96c6\u65f6\u95f4"

T_DAILY = "\u7b56\u7565\u65e5\u5ea6\u4e1a\u7ee9"
T_INTERVAL = "\u7b56\u7565\u533a\u95f4\u4e1a\u7ee9"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Upsert one TTFund incremental performance run into analysis DB.")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--schema-path", type=Path, default=DEFAULT_SCHEMA_PATH)
    parser.add_argument("--normalized-root", type=Path, default=DEFAULT_NORMALIZED_ROOT)
    parser.add_argument("--run-id", help="Collection run id, for example 20260529T162451+0800.")
    parser.add_argument("--target-trade-date", help="Expected latest trade date, YYYY-MM-DD.")
    return parser.parse_args()


def run_day(run_id: str) -> str:
    return f"{run_id[0:4]}-{run_id[4:6]}-{run_id[6:8]}"


def find_latest_summary(normalized_root: Path) -> Path:
    summaries = sorted((normalized_root / "collection_summary").glob("*/*.json"), key=lambda path: path.stat().st_mtime)
    if not summaries:
        raise SystemExit(f"no collection summary under {normalized_root / 'collection_summary'}")
    return summaries[-1]


def find_run_summary(normalized_root: Path, run_id: str | None) -> Path:
    if run_id:
        matches = sorted((normalized_root / "collection_summary").glob(f"*/{run_id}.json"))
        if not matches:
            raise SystemExit(f"collection summary not found for run_id={run_id}")
        return matches[-1]
    return find_latest_summary(normalized_root)


def run_file(normalized_root: Path, entity: str, run_id: str) -> Path | None:
    matches = sorted((normalized_root / entity).glob(f"*/{run_id}.jsonl"))
    return matches[-1] if matches else None


def source_row(strategy_id: Any, unified_id: str, file_type: str, path: Path, run_id: str, captured_at: str | None) -> dict[str, Any]:
    return {
        K_UNIFIED_ID: unified_id,
        K_CHANNEL_ID: CHANNEL_ID,
        K_CHANNEL_STRATEGY_ID: strategy_id,
        K_FILE_TYPE: file_type,
        K_FILE_PATH: loader.normalize_path(path),
        K_RUN_ID: run_id,
        K_CAPTURED_AT: captured_at,
    }


def load_daily(conn: sqlite3.Connection, path: Path | None, run_id: str, captured_at: str | None) -> dict[str, int]:
    counters: dict[str, int] = defaultdict(int)
    if path is None:
        return {"files": 0, "input_rows": 0, "upserted_rows": 0}

    rows_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for row in loader.load_jsonl(path):
        counters["input_rows"] += 1
        strategy_id = row.get("source_strategy_id")
        trade_date = loader.normalize_date_text(row.get("trade_date"))
        if not strategy_id or not trade_date:
            counters["skipped_rows"] += 1
            continue
        unified_id = loader.unified_strategy_id(CHANNEL_ID, strategy_id)
        mapped = {
            K_UNIFIED_ID: unified_id,
            K_CHANNEL_ID: CHANNEL_ID,
            K_CHANNEL_STRATEGY_ID: strategy_id,
            K_TRADE_DATE: trade_date,
            K_NAV: loader.to_float(row.get("nav")),
            K_DAILY_RETURN: loader.to_percent(CHANNEL_ID, row.get("daily_return")),
            K_CUMULATIVE_RETURN: loader.to_percent(CHANNEL_ID, row.get("cumulative_return")),
            K_BENCHMARK_RETURN: loader.to_percent(CHANNEL_ID, row.get("benchmark_return")),
            K_INDEX_RETURN: loader.to_percent(CHANNEL_ID, row.get("index_return")),
            K_MAX_DRAWDOWN: loader.to_percent(CHANNEL_ID, row.get("max_drawdown")),
            K_SECTION_NAME: row.get("section_name"),
            K_SECTION_TYPE: row.get("section_type") or row.get("source_type"),
            K_SNAPSHOT_ID: row.get("source_snapshot_id"),
        }
        key = (unified_id, trade_date)
        if key in rows_by_key:
            rows_by_key[key] = loader.merge_daily_row(rows_by_key[key], mapped)
            counters["merged_rows"] += 1
        else:
            rows_by_key[key] = mapped
        loader.upsert_source(conn, source_row(strategy_id, unified_id, "strategy_performance_daily", path, run_id, captured_at))

    for mapped in rows_by_key.values():
        loader.upsert_daily_performance(conn, mapped)
        counters["upserted_rows"] += 1
    counters["files"] = 1
    return dict(counters)


def load_interval(conn: sqlite3.Connection, path: Path | None, run_id: str, captured_at: str | None) -> dict[str, int]:
    counters: dict[str, int] = defaultdict(int)
    if path is None:
        return {"files": 0, "input_rows": 0, "upserted_rows": 0}

    for row in loader.load_jsonl(path):
        counters["input_rows"] += 1
        strategy_id = row.get("source_strategy_id")
        as_of_date = loader.normalize_date_text(row.get("as_of_date"))
        interval_code = row.get("interval_code")
        if not strategy_id or not as_of_date or not interval_code:
            counters["skipped_rows"] += 1
            continue
        unified_id = loader.unified_strategy_id(CHANNEL_ID, strategy_id)
        loader.upsert_interval_performance(
            conn,
            {
                K_UNIFIED_ID: unified_id,
                K_CHANNEL_ID: CHANNEL_ID,
                K_CHANNEL_STRATEGY_ID: strategy_id,
                K_STAT_DATE: as_of_date,
                K_INTERVAL_CODE: interval_code,
                K_INTERVAL_NAME: row.get("interval_label"),
                K_STRATEGY_RETURN: loader.to_percent(CHANNEL_ID, row.get("return_value")),
                K_BENCHMARK_RETURN: loader.to_percent(CHANNEL_ID, row.get("benchmark_return")),
                K_SNAPSHOT_ID: row.get("source_snapshot_id"),
            },
        )
        loader.upsert_source(conn, source_row(strategy_id, unified_id, "strategy_performance_interval", path, run_id, captured_at))
        counters["upserted_rows"] += 1
    counters["files"] = 1
    return dict(counters)


def query_latest(conn: sqlite3.Connection, table: str, date_col: str) -> str | None:
    return conn.execute(f'SELECT MAX("{date_col}") FROM "{table}" WHERE "\u6e20\u9053ID"=?', (CHANNEL_ID,)).fetchone()[0]


def query_target_count(conn: sqlite3.Connection, table: str, date_col: str, target: str | None) -> int | None:
    if not target:
        return None
    return int(
        conn.execute(
            f'SELECT COUNT(*) FROM "{table}" WHERE "\u6e20\u9053ID"=? AND "{date_col}"=?',
            (CHANNEL_ID, target),
        ).fetchone()[0]
    )


def configure_incremental_connection(conn: sqlite3.Connection) -> None:
    """Close schema setup work before applying write-connection PRAGMAs."""

    if conn.in_transaction:
        conn.commit()
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=120000")


def main() -> None:
    args = parse_args()
    summary_path = find_run_summary(args.normalized_root, args.run_id)
    summary = json.loads(summary_path.read_text(encoding="utf-8-sig"))
    run_id = str(summary.get("run_id") or summary_path.stem)
    captured_at = summary.get("captured_at")
    daily_path = run_file(args.normalized_root, "strategy_performance_daily", run_id)
    interval_path = run_file(args.normalized_root, "strategy_performance_interval", run_id)

    conn = loader.init_db(args.db_path, args.schema_path, keep_existing_db=True)
    try:
        configure_incremental_connection(conn)
        daily = load_daily(conn, daily_path, run_id, captured_at)
        interval = load_interval(conn, interval_path, run_id, captured_at)
        conn.commit()
        result = {
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "run_id": run_id,
            "target_trade_date": args.target_trade_date,
            "summary_path": str(summary_path.resolve()),
            "daily_path": str(daily_path.resolve()) if daily_path else None,
            "interval_path": str(interval_path.resolve()) if interval_path else None,
            "daily": daily,
            "interval": interval,
            "db_latest_strategy_daily_date": query_latest(conn, T_DAILY, K_TRADE_DATE),
            "db_latest_strategy_interval_date": query_latest(conn, T_INTERVAL, K_STAT_DATE),
            "target_daily_rows": query_target_count(conn, T_DAILY, K_TRADE_DATE, args.target_trade_date),
            "target_interval_rows": query_target_count(conn, T_INTERVAL, K_STAT_DATE, args.target_trade_date),
        }
    finally:
        conn.close()

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
