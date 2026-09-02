from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
sys.path.insert(0, str(PROJECT_ROOT / "节点脚本" / "_共享组件" / "python_src"))

from advisor_monitor.collectors.ttfund_loggedin import (  # noqa: E402
    CHANNEL_ID,
    CHANNEL_NAME,
    TTFundLoggedInCollector,
    collect_ttfund_loggedin,
)
from advisor_monitor.storage import init_sqlite, upsert_channel, upsert_raw_snapshot  # noqa: E402


DB_PATH = PROJECT_ROOT / "data" / "advisor_monitor.sqlite"
ANALYSIS_DB_PATH = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
SCHEMA_PATH = PROJECT_ROOT / "schemas" / "sqlite.sql"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect full strategy snapshots from TTFund logged-in app cache."
    )
    parser.add_argument("--device-id", type=str, default=None, help="ADB device serial.")
    parser.add_argument(
        "--input-cache-dir",
        type=Path,
        default=None,
        help="Local cache directory fallback. Defaults to data/raw/device_cache when device sync is disabled.",
    )
    parser.add_argument(
        "--no-sync-device-cache",
        action="store_true",
        help="Do not pull .ttjj_cache from the connected Android device.",
    )
    parser.add_argument("--limit", type=int, default=None, help="Collect only the first N strategies.")
    parser.add_argument(
        "--quote-batch-size",
        type=int,
        default=200,
        help="Batch size for getTGQuoteByFavor requests.",
    )
    parser.add_argument(
        "--adb-path",
        type=str,
        default="adb",
        help="Path to adb executable when device cache sync is enabled.",
    )
    parser.add_argument(
        "--skip-public-quote",
        action="store_true",
        help="Skip public quote enrichment from getTGQuoteByFavor.",
    )
    parser.add_argument(
        "--skip-db",
        action="store_true",
        help="Do not initialize/update the local SQLite channel/raw_snapshot tables.",
    )
    parser.add_argument(
        "--strategy-id",
        action="append",
        default=[],
        help="Collect only this strategy id. Repeat for multiple new strategies.",
    )
    parser.add_argument("--run-id", help="Explicit child run id for exact batch provenance.")
    return parser.parse_args()


def load_strategy_ids_from_analysis_db() -> list[str]:
    if not ANALYSIS_DB_PATH.exists():
        return []
    conn = sqlite3.connect(ANALYSIS_DB_PATH)
    try:
        rows = conn.execute(
            'SELECT "渠道策略ID" FROM "策略信息" WHERE "渠道ID" = ? ORDER BY "渠道策略ID"',
            (CHANNEL_ID,),
        )
        return [str(row[0]).strip() for row in rows if str(row[0]).strip()]
    finally:
        conn.close()


def latest_jsonl(entity: str) -> Path | None:
    root = PROJECT_ROOT / "data" / "normalized" / CHANNEL_ID / entity
    if not root.exists():
        return None
    candidates = sorted(root.glob("*/*.jsonl"))
    return candidates[-1] if candidates else None


def load_strategy_ids_from_latest_master() -> list[str]:
    path = latest_jsonl("strategy_master")
    if path is None:
        return []
    strategy_ids: list[str] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            strategy_id = str(row.get("source_strategy_id") or "").strip()
            if strategy_id and strategy_id not in strategy_ids:
                strategy_ids.append(strategy_id)
    return strategy_ids


def collect_quote_only_from_baseline(args: argparse.Namespace) -> dict[str, Any]:
    strategy_ids = list(dict.fromkeys(str(value).strip() for value in args.strategy_id if str(value).strip()))
    if not strategy_ids:
        strategy_ids = load_strategy_ids_from_analysis_db() or load_strategy_ids_from_latest_master()
    if args.limit and args.limit > 0:
        strategy_ids = strategy_ids[: args.limit]
    if not strategy_ids:
        raise FileNotFoundError("no strategy baseline found in analysis DB or normalized strategy_master")

    collector = TTFundLoggedInCollector(
        PROJECT_ROOT,
        device_id=args.device_id,
        sync_device_cache=False,
        strategy_ids=strategy_ids,
        quote_batch_size=args.quote_batch_size,
        fetch_public_quote=not args.skip_public_quote,
        adb_path=args.adb_path,
        run_id=args.run_id,
    )
    collector.raw_base_dir.mkdir(parents=True, exist_ok=True)
    quotes_by_strategy = (
        collector.collect_quote_snapshots(strategy_ids)
        if not args.skip_public_quote
        else {}
    )
    normalized = collector.normalize(strategy_ids, {}, {}, {}, quotes_by_strategy)
    quote_only_normalized = {
        "strategy_performance_daily": normalized["strategy_performance_daily"],
        "strategy_performance_interval": normalized["strategy_performance_interval"],
    }
    collector.write_normalized(quote_only_normalized)
    summary = {
        "channel_id": CHANNEL_ID,
        "channel_name": CHANNEL_NAME,
        "run_id": collector.run_id,
        "captured_at": collector.captured_at,
        "raw_dir": str(collector.raw_base_dir),
        "normalized_dir": str(collector.normalized_base_dir),
        "cache_root": None,
        "cache_source": "normalized_baseline_quote_only",
        "device_id": args.device_id,
        "strategy_total": len(strategy_ids),
        "home_strategy_total": 0,
        "quote_strategy_total": len(quotes_by_strategy),
        "detail_cache_strategy_total": 0,
        "adjustment_cache_strategy_total": 0,
        "adjustment_history_cache_strategy_total": 0,
        "detail_cache_with_holdings_total": 0,
        "strategy_master_rows": 0,
        "daily_rows_total": len(quote_only_normalized["strategy_performance_daily"]),
        "interval_rows_total": len(quote_only_normalized["strategy_performance_interval"]),
        "interval_with_benchmark_rows": 0,
        "holding_rows_total": 0,
        "fund_public_dim_total": 0,
        "rebalance_event_total": 0,
        "rebalance_delta_total": 0,
        "raw_snapshot_total": len(collector.raw_snapshots),
    }
    collector.write_run_manifest(summary)
    return summary


def main() -> None:
    args = parse_args()
    default_cache_dir = PROJECT_ROOT / "data" / "raw" / "device_cache"
    if args.no_sync_device_cache and args.input_cache_dir is None and not default_cache_dir.exists():
        summary = collect_quote_only_from_baseline(args)
    else:
        summary = collect_ttfund_loggedin(
            PROJECT_ROOT,
            device_id=args.device_id,
            sync_device_cache=not args.no_sync_device_cache,
            input_cache_dir=args.input_cache_dir,
            strategy_ids=args.strategy_id,
            limit=args.limit,
            quote_batch_size=args.quote_batch_size,
            fetch_public_quote=not args.skip_public_quote,
            adb_path=args.adb_path,
            run_id=args.run_id,
        )

    if not args.skip_db:
        init_sqlite(DB_PATH, SCHEMA_PATH)
        upsert_channel(
            DB_PATH,
            {
                "channel_id": CHANNEL_ID,
                "channel_name": CHANNEL_NAME,
                "provider_type": "third_party",
                "official_site_url": "https://fund.eastmoney.com/",
                "login_required_level": "partial",
            },
        )
        manifest_path = Path(summary["raw_dir"]) / "_manifest.json"
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            for snapshot in manifest.get("raw_snapshots", []):
                upsert_raw_snapshot(DB_PATH, snapshot)

    print("collection complete")
    print(f"channel={summary['channel_id']}")
    print(f"run_id={summary['run_id']}")
    print(f"raw_dir={summary['raw_dir']}")
    print(f"normalized_dir={summary['normalized_dir']}")
    print(
        "coverage="
        f"strategies:{summary['strategy_total']} "
        f"quote:{summary['quote_strategy_total']} "
        f"detail:{summary['detail_cache_strategy_total']} "
        f"daily:{summary['daily_rows_total']} "
        f"interval:{summary['interval_rows_total']} "
        f"holdings:{summary['holding_rows_total']} "
        f"rebalance_events:{summary['rebalance_event_total']} "
        f"rebalance_deltas:{summary['rebalance_delta_total']}"
    )


if __name__ == "__main__":
    main()
