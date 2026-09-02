from __future__ import annotations

import argparse
import concurrent.futures
import json
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
sys.path.insert(0, str(PROJECT_ROOT / "节点脚本" / "_共享组件" / "python_src"))

from advisor_monitor.collectors.ttfund_fund_nav import (  # noqa: E402
    CHANNEL_ID,
    CHANNEL_NAME,
    ENTITY_COLLECTION_SUMMARY,
    ENTITY_HISTORY_DAILY,
    ENTITY_HISTORY_META,
    TTFundFundNavCollector,
)
from advisor_monitor.storage import write_jsonl  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect NAV history for the full FOF universe in 基金标准分类字典.",
    )
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--start-date", default="2025-06-01")
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--per-page", type=int, default=2000)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--only-missing-nav",
        action="store_true",
        help="Only collect FOF funds with no rows in 基金日度净值.",
    )
    parser.add_argument(
        "--stale-before",
        default=None,
        help="Only collect funds whose latest local NAV date is before this YYYY-MM-DD date.",
    )
    parser.add_argument(
        "--min-total-rows",
        type=int,
        default=0,
        help="Only collect funds whose total local NAV rows are below this threshold.",
    )
    parser.add_argument(
        "--min-1y-rows",
        type=int,
        default=0,
        help="Only collect funds whose local NAV rows since --one-year-start are below this threshold.",
    )
    parser.add_argument(
        "--one-year-start",
        default="2025-06-30",
        help="Start date used for --min-1y-rows filtering.",
    )
    parser.add_argument("--codes", default="", help="Comma-separated fund codes for targeted repair.")
    parser.add_argument("--progress-every", type=int, default=25)
    return parser.parse_args()


def clean(value: Any) -> str:
    text = str(value or "").strip()
    return "" if text in {"None", "nan", "null", "--", "-"} else text


def load_targets(args: argparse.Namespace) -> list[dict[str, Any]]:
    conn = sqlite3.connect(args.db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = [
            dict(row)
            for row in conn.execute(
                """
                WITH nav AS (
                  SELECT "基金代码",
                         COUNT(*) AS nav_rows,
                         MAX("交易日期") AS latest_nav_date,
                         SUM(CASE WHEN "交易日期" >= ? THEN 1 ELSE 0 END) AS nav_rows_1y
                  FROM "基金日度净值"
                  GROUP BY "基金代码"
                )
                SELECT d."基金代码", d."标准基金名称", d."天天基金细分类", d."基金公司",
                       nav.nav_rows, nav.latest_nav_date, nav.nav_rows_1y
                FROM "基金标准分类字典" d
                LEFT JOIN nav ON nav."基金代码" = d."基金代码"
                WHERE d."是否FOF" = 1
                ORDER BY d."基金代码"
                """,
                (args.one_year_start,),
            )
        ]
    finally:
        conn.close()

    code_filter = {
        clean(code).zfill(6)
        for code in str(args.codes or "").split(",")
        if clean(code)
    }
    targets: list[dict[str, Any]] = []
    for row in rows:
        code = clean(row.get("基金代码"))
        if code_filter and code not in code_filter:
            continue
        if args.only_missing_nav and int(row.get("nav_rows") or 0) > 0:
            continue
        if args.stale_before and clean(row.get("latest_nav_date")) >= args.stale_before:
            continue
        if args.min_total_rows and int(row.get("nav_rows") or 0) >= args.min_total_rows:
            continue
        if args.min_1y_rows and int(row.get("nav_rows_1y") or 0) >= args.min_1y_rows:
            continue
        targets.append(row)
    if args.limit and args.limit > 0:
        targets = targets[: args.limit]
    return targets


def metadata_from_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "fund_code": clean(row.get("基金代码")),
        "fund_name": clean(row.get("标准基金名称")) or None,
        "fund_type": clean(row.get("天天基金细分类")) or None,
        "fund_company": clean(row.get("基金公司")) or None,
        "source_channels": ["基金标准分类字典"],
        "source_entities": ["fof_universe"],
    }


def collect_one_with_retry(
    collector: TTFundFundNavCollector,
    metadata: dict[str, Any],
    retries: int,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any] | None]:
    last_error: Exception | None = None
    for attempt in range(1, max(1, retries + 1) + 1):
        try:
            history_rows, meta_row = collector.collect_one_fund(metadata)
            return history_rows, meta_row, None
        except Exception as exc:  # noqa: BLE001 - keep failed fund in summary.
            last_error = exc
            if attempt <= retries:
                time.sleep(min(2.0 * attempt, 8.0))
    return [], {}, {
        "fund_code": metadata.get("fund_code"),
        "fund_name": metadata.get("fund_name"),
        "error": f"{type(last_error).__name__}: {last_error}",
    }


def is_valid_history_row(row: dict[str, Any]) -> bool:
    if not clean(row.get("trade_date")):
        return False
    value_fields = (
        "nav",
        "accumulated_nav",
        "daily_return",
        "per_10k_yield",
        "seven_day_annualized",
    )
    return any(row.get(field) not in (None, "") for field in value_fields)


def main() -> None:
    args = parse_args()
    targets = load_targets(args)
    collector = TTFundFundNavCollector(
        PROJECT_ROOT,
        start_date=args.start_date,
        end_date=args.end_date,
        per_page=args.per_page,
    )
    for row in targets:
        metadata = metadata_from_row(row)
        collector.catalog[metadata["fund_code"]] = metadata

    print(
        json.dumps(
            {
                "channel": CHANNEL_ID,
                "target_fund_total": len(targets),
                "start_date": args.start_date,
                "end_date": args.end_date,
                "workers": args.workers,
                "run_id": collector.run_id,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

    history_rows: list[dict[str, Any]] = []
    meta_rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    invalid_history_row_total = 0
    started = datetime.now().astimezone().isoformat(timespec="seconds")

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        future_map = {
            executor.submit(collect_one_with_retry, collector, metadata_from_row(row), args.retries): row
            for row in targets
        }
        for done, future in enumerate(concurrent.futures.as_completed(future_map), start=1):
            rows, meta, failure = future.result()
            if failure:
                failures.append(failure)
            else:
                valid_rows = [row for row in rows if is_valid_history_row(row)]
                invalid_history_row_total += len(rows) - len(valid_rows)
                history_rows.extend(valid_rows)
                meta_rows.append(meta)
            if done == len(targets) or (args.progress_every > 0 and done % args.progress_every == 0):
                print(
                    f"[fof-nav] {done}/{len(targets)} success={len(meta_rows)} "
                    f"failed={len(failures)} history_rows={len(history_rows)} "
                    f"invalid_history_rows={invalid_history_row_total}",
                    flush=True,
                )

    day = collector.day
    run_id = collector.run_id
    normalized_base_dir = collector.normalized_base_dir
    history_path = normalized_base_dir / ENTITY_HISTORY_DAILY / day / f"{run_id}.jsonl"
    meta_path = normalized_base_dir / ENTITY_HISTORY_META / day / f"{run_id}.jsonl"
    summary_path = normalized_base_dir / ENTITY_COLLECTION_SUMMARY / day / f"{run_id}.json"
    write_jsonl(history_path, history_rows)
    write_jsonl(meta_path, meta_rows)

    manifest_path = collector.raw_base_dir / "_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(
            {
                "channel_id": CHANNEL_ID,
                "channel_name": CHANNEL_NAME,
                "run_id": run_id,
                "captured_at": collector.captured_at,
                "raw_snapshots": collector.raw_snapshots,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    summary = {
        "channel_id": CHANNEL_ID,
        "channel_name": CHANNEL_NAME,
        "run_id": run_id,
        "started_at": started,
        "captured_at": collector.captured_at,
        "start_date": args.start_date,
        "end_date": args.end_date,
        "raw_dir": str(collector.raw_base_dir.resolve()),
        "normalized_dir": str(normalized_base_dir.resolve()),
        "history_path": str(history_path.resolve()),
        "meta_path": str(meta_path.resolve()),
        "summary_path": str(summary_path.resolve()),
        "target_fund_total": len(targets),
        "successful_fund_total": len(meta_rows),
        "failed_fund_total": len(failures),
        "history_row_total": len(history_rows),
        "invalid_history_row_total": invalid_history_row_total,
        "failed_funds": failures,
        "targets": [
            {
                "fund_code": clean(row.get("基金代码")),
                "fund_name": clean(row.get("标准基金名称")),
                "latest_local_nav_date": clean(row.get("latest_nav_date")),
                "local_nav_rows": int(row.get("nav_rows") or 0),
                "local_nav_rows_1y": int(row.get("nav_rows_1y") or 0),
            }
            for row in targets
        ],
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
