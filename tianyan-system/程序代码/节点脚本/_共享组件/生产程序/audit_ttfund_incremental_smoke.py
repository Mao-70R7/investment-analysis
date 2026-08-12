from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
DEFAULT_NORMALIZED_ROOT = PROJECT_ROOT / "data" / "normalized" / "ttfund"
CHANNEL_ID = "ttfund"

K_CHANNEL_ID = "\u6e20\u9053ID"
K_UNIFIED_ID = "\u7edf\u4e00\u7b56\u7565ID"
K_TRADE_DATE = "\u4ea4\u6613\u65e5\u671f"
K_STAT_DATE = "\u7edf\u8ba1\u65e5\u671f"

T_STRATEGY_DAILY = "\u7b56\u7565\u65e5\u5ea6\u4e1a\u7ee9"
T_STRATEGY_INTERVAL = "\u7b56\u7565\u533a\u95f4\u4e1a\u7ee9"
T_FUND_NAV = "\u57fa\u91d1\u65e5\u5ea6\u51c0\u503c"
T_INDEX_QUOTE = "\u6307\u6570\u65e5\u5ea6\u884c\u60c5"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke audit for a TTFund incremental run.")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--normalized-root", type=Path, default=DEFAULT_NORMALIZED_ROOT)
    parser.add_argument("--target-trade-date", help="Expected latest strategy/fund NAV trade date, YYYY-MM-DD.")
    parser.add_argument("--run-id", help="Collection run id used for the incremental load.")
    parser.add_argument("--allow-fund-nav-lag-days", type=int, default=1)
    return parser.parse_args()


def scalar(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> Any:
    return conn.execute(sql, params).fetchone()[0]


def latest_date(conn: sqlite3.Connection, table: str, date_col: str, where: str = "", params: tuple[Any, ...] = ()) -> str | None:
    sql = f'SELECT MAX("{date_col}") FROM "{table}"'
    if where:
        sql += f" WHERE {where}"
    return scalar(conn, sql, params)


def count_rows(conn: sqlite3.Connection, table: str, where: str = "", params: tuple[Any, ...] = ()) -> int:
    sql = f'SELECT COUNT(*) FROM "{table}"'
    if where:
        sql += f" WHERE {where}"
    return int(scalar(conn, sql, params))


def count_distinct(conn: sqlite3.Connection, table: str, col: str, where: str = "", params: tuple[Any, ...] = ()) -> int:
    sql = f'SELECT COUNT(DISTINCT "{col}") FROM "{table}"'
    if where:
        sql += f" WHERE {where}"
    return int(scalar(conn, sql, params))


def find_run_summary(normalized_root: Path, run_id: str | None) -> dict[str, Any] | None:
    if not run_id:
        return None
    matches = sorted((normalized_root / "collection_summary").glob(f"*/{run_id}.json"))
    if not matches:
        return None
    payload = json.loads(matches[-1].read_text(encoding="utf-8-sig"))
    payload["_path"] = str(matches[-1].resolve())
    return payload


def date_lag_days(latest: str | None, target: str | None) -> int | None:
    if not latest or not target:
        return None
    try:
        return (datetime.fromisoformat(target) - datetime.fromisoformat(latest)).days
    except ValueError:
        return None


def fund_nav_lag_is_unacceptable(lag_days: int | None, allowed_lag_days: int) -> bool:
    """Return true only when NAV data is missing or genuinely older than allowed.

    A negative lag means the NAV source is newer than the strategy target date and
    must not be treated as stale.
    """
    return lag_days is None or lag_days > max(0, allowed_lag_days)


def main() -> None:
    args = parse_args()
    failures: list[str] = []
    warnings: list[str] = []

    conn = sqlite3.connect(args.db_path)
    try:
        target = args.target_trade_date
        channel_where = f'"{K_CHANNEL_ID}"=?'
        target_daily_where = f'"{K_CHANNEL_ID}"=? AND "{K_TRADE_DATE}"=?'
        target_interval_where = f'"{K_CHANNEL_ID}"=? AND "{K_STAT_DATE}"=?'
        target_fund_nav_where = f'"{K_TRADE_DATE}"=?'

        result = {
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "target_trade_date": target,
            "run_id": args.run_id,
            "run_summary": find_run_summary(args.normalized_root, args.run_id),
            "ttfund_strategy_daily_latest_date": latest_date(conn, T_STRATEGY_DAILY, K_TRADE_DATE, channel_where, (CHANNEL_ID,)),
            "ttfund_strategy_daily_rows": count_rows(conn, T_STRATEGY_DAILY, channel_where, (CHANNEL_ID,)),
            "ttfund_strategy_daily_target_rows": count_rows(conn, T_STRATEGY_DAILY, target_daily_where, (CHANNEL_ID, target)) if target else None,
            "ttfund_strategy_daily_target_strategies": count_distinct(conn, T_STRATEGY_DAILY, K_UNIFIED_ID, target_daily_where, (CHANNEL_ID, target)) if target else None,
            "ttfund_strategy_interval_latest_date": latest_date(conn, T_STRATEGY_INTERVAL, K_STAT_DATE, channel_where, (CHANNEL_ID,)),
            "ttfund_strategy_interval_target_rows": count_rows(conn, T_STRATEGY_INTERVAL, target_interval_where, (CHANNEL_ID, target)) if target else None,
            "fund_nav_latest_date": latest_date(conn, T_FUND_NAV, K_TRADE_DATE),
            "fund_nav_target_rows": count_rows(conn, T_FUND_NAV, target_fund_nav_where, (target,)) if target else None,
            "index_quote_latest_date": latest_date(conn, T_INDEX_QUOTE, K_TRADE_DATE),
        }
    finally:
        conn.close()

    if target:
        if not result["ttfund_strategy_daily_latest_date"] or str(result["ttfund_strategy_daily_latest_date"]) < target:
            failures.append("ttfund strategy daily latest date is older than target")
        if not result["ttfund_strategy_daily_target_rows"]:
            failures.append("no ttfund strategy daily rows on target trade date")
        fund_nav_lag_days = date_lag_days(result.get("fund_nav_latest_date"), target)
        result["fund_nav_lag_days"] = fund_nav_lag_days
        if fund_nav_lag_is_unacceptable(fund_nav_lag_days, args.allow_fund_nav_lag_days):
            failures.append("fund NAV latest date is older than target")
        if not result["fund_nav_target_rows"]:
            warnings.append("no fund NAV rows on target trade date")

    run_summary = result.get("run_summary") or {}
    if run_summary.get("interval_rows_total") and not result.get("ttfund_strategy_interval_target_rows"):
        failures.append("incremental run had interval rows but DB has no target interval rows")

    result["status"] = "passed" if not failures else "failed"
    result["failures"] = failures
    result["warnings"] = warnings
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
