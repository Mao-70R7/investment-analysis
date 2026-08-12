from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
sys.path.insert(0, str(PROJECT_ROOT / "节点脚本" / "_共享组件" / "python_src"))

from advisor_monitor.progress import ConsoleProgress  # noqa: E402


DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
CHANNEL_ID = "ttfund"
CHANNEL_NAME = "天天基金/投顾"
CURVE_API_URL = "https://ibgmarket.tiantianfunds.com/combine/investAdviserInfo/fundIAAIChartPro"
DEFAULT_RANGE = "ln"
USER_AGENT = "ttjj/6.6.19 Android advisor-monitor/0.1"


def zh(value: str) -> str:
    return json.loads(f'"{value}"')


T_STRATEGY = zh(r"\u7b56\u7565\u4fe1\u606f")
T_DAILY = zh(r"\u7b56\u7565\u65e5\u5ea6\u4e1a\u7ee9")
C_UNIFIED_ID = zh(r"\u7edf\u4e00\u7b56\u7565ID")
C_CHANNEL_ID = zh(r"\u6e20\u9053ID")
C_SOURCE_ID = zh(r"\u6e20\u9053\u7b56\u7565ID")
C_TRADE_DATE = zh(r"\u4ea4\u6613\u65e5\u671f")
C_NAME = zh(r"\u7b56\u7565\u540d\u79f0")
C_ADVISOR = zh(r"\u6295\u987e\u673a\u6784")
C_ESTABLISHED = zh(r"\u6210\u7acb\u65e5\u671f")
C_STATUS = zh(r"\u7b56\u7565\u72b6\u6001")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="采集天天基金 App 披露的投顾策略官方业绩曲线。")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--strategy-ids", nargs="*", help="只采集指定天天策略 ID。默认采集数据库中全部天天策略。")
    parser.add_argument(
        "--catalog-manifest-path",
        type=Path,
        help="本批目录发现清单；未显式指定策略时，与数据库策略取并集，确保新策略在入库前先采集官方曲线。",
    )
    parser.add_argument("--limit", type=int, help="最多采集多少个策略，用于探测。")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--range", default=DEFAULT_RANGE, dest="range_code", help="fundIAAIChartPro RANGE 参数，成立以来使用 ln。")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--run-id", help="自定义采集批次 ID。")
    parser.add_argument("--merge-existing-run", action="store_true")
    parser.add_argument(
        "--auto-incremental",
        action="store_true",
        help="已有曲线只取最新点；新策略或断档策略自动切换成立以来曲线并回补缺口。",
    )
    parser.add_argument("--overlap-days", type=int, default=3, help="断档回补时保留的重叠自然日数。")
    parser.add_argument(
        "--full-history-gap-days",
        type=int,
        default=4,
        help="本地最新日期距离目标日期超过该自然日数时，自动抓成立以来曲线。",
    )
    parser.add_argument("--expected-latest-date")
    parser.add_argument("--min-success-ratio", type=float, default=0.98)
    parser.add_argument("--min-latest-ratio", type=float, default=0.98)
    parser.add_argument("--min-benchmark-latest-ratio", type=float, default=0.95)
    parser.add_argument(
        "--max-source-lag-business-days",
        type=int,
        default=1,
        help="Maximum accepted business-day lag between quote data and the official curve source.",
    )
    parser.add_argument("--retry-failed-rounds", type=int, default=1)
    return parser.parse_args()


def now_local() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(str(value).strip().replace("%", ""))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result):
        return None
    return result


def load_catalog_manifest(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {"state": "not_configured", "catalog_strategy_ids": [], "catalog_rows": []}
    resolved = path.resolve()
    if not resolved.is_file():
        return {
            "state": "missing",
            "manifest_path": str(resolved),
            "catalog_strategy_ids": [],
            "catalog_rows": [],
        }
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "state": "invalid",
            "manifest_path": str(resolved),
            "load_error": f"{type(exc).__name__}: {exc}",
            "catalog_strategy_ids": [],
            "catalog_rows": [],
        }
    if not isinstance(payload, dict):
        return {
            "state": "invalid",
            "manifest_path": str(resolved),
            "load_error": "JSON root is not an object",
            "catalog_strategy_ids": [],
            "catalog_rows": [],
        }
    payload = dict(payload)
    payload["manifest_path"] = str(resolved)
    return payload


def catalog_strategy_seeds(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows_by_id: dict[str, dict[str, Any]] = {}
    for row in manifest.get("catalog_rows") or []:
        if not isinstance(row, dict):
            continue
        source_id = str(
            row.get("source_strategy_id")
            or row.get("channel_strategy_id")
            or row.get("strategy_id")
            or ""
        ).strip()
        if source_id:
            rows_by_id[source_id] = row
    ids = {
        str(value or "").strip()
        for value in manifest.get("catalog_strategy_ids") or []
        if str(value or "").strip()
    }
    ids.update(rows_by_id)
    seeds: dict[str, dict[str, Any]] = {}
    for source_id in ids:
        row = rows_by_id.get(source_id) or {}
        seeds[source_id] = {
            "unified_strategy_id": f"{CHANNEL_ID}__{source_id}",
            "source_strategy_id": source_id,
            "strategy_name": row.get("strategy_name"),
            "advisor_name": row.get("advisor_name"),
            "established_date": row.get("established_date") or row.get("launch_date"),
            "strategy_status": row.get("strategy_status"),
            "inventory_source": "catalog_manifest",
        }
    return seeds


def load_strategies(
    db_path: Path,
    strategy_ids: list[str] | None = None,
    limit: int | None = None,
    catalog_manifest_path: Path | None = None,
) -> list[dict[str, Any]]:
    where = [f'"{C_CHANNEL_ID}" = ?']
    params: list[Any] = [CHANNEL_ID]
    if strategy_ids:
        placeholders = ",".join("?" for _ in strategy_ids)
        where.append(f'"{C_SOURCE_ID}" IN ({placeholders})')
        params.extend(strategy_ids)
    sql = f"""
        SELECT
            "{C_UNIFIED_ID}" AS unified_strategy_id,
            "{C_SOURCE_ID}" AS source_strategy_id,
            "{C_NAME}" AS strategy_name,
            "{C_ADVISOR}" AS advisor_name,
            "{C_ESTABLISHED}" AS established_date
            ,"{C_STATUS}" AS strategy_status
        FROM "{T_STRATEGY}"
        WHERE {" AND ".join(where)}
        ORDER BY "{C_SOURCE_ID}"
    """
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        rows = [{**dict(row), "inventory_source": "database"} for row in conn.execute(sql, params)]
    finally:
        conn.close()
    manifest = load_catalog_manifest(catalog_manifest_path)
    catalog_seeds = catalog_strategy_seeds(manifest)
    requested_ids = (
        [str(value or "").strip() for value in strategy_ids if str(value or "").strip()]
        if strategy_ids
        else sorted(catalog_seeds)
    )
    known = {str(row.get("source_strategy_id") or ""): row for row in rows}
    for source_id in requested_ids:
        if source_id in known:
            seed = catalog_seeds.get(source_id) or {}
            for field in ("strategy_name", "advisor_name", "established_date", "strategy_status"):
                if not known[source_id].get(field) and seed.get(field):
                    known[source_id][field] = seed[field]
            if seed:
                known[source_id]["inventory_source"] = "database+catalog_manifest"
            continue
        seed = catalog_seeds.get(source_id) or {
            "unified_strategy_id": f"{CHANNEL_ID}__{source_id}",
            "source_strategy_id": source_id,
            "strategy_name": None,
            "advisor_name": None,
            "established_date": None,
            "strategy_status": None,
            "inventory_source": "explicit_selection",
        }
        rows.append(dict(seed))
        known[source_id] = rows[-1]
    rows.sort(key=lambda row: str(row.get("source_strategy_id") or ""))
    if limit:
        rows = rows[: int(limit)]
    return rows


def classify_quality_scope(strategy: dict[str, Any]) -> str:
    status = str(strategy.get("strategy_status") or "").strip().lower()
    name = str(strategy.get("strategy_name") or "").strip()
    if status == "stopped":
        return "stopped"
    if "测试" in name or "test" in name.lower():
        return "test"
    return "active"


def disclosure_date_at_coverage(
    latest_dates: dict[str, str], eligible_ids: set[str], min_ratio: float
) -> str | None:
    values = sorted(
        str(value)
        for strategy_id, value in latest_dates.items()
        if strategy_id in eligible_ids and value
    )
    if not values:
        return None
    required = max(1, math.ceil(len(eligible_ids) * max(0.0, min(1.0, min_ratio))))
    candidates = sorted(set(values), reverse=True)
    for candidate in candidates:
        if sum(1 for value in values if value >= candidate) >= required:
            return candidate
    return min(values)


def business_day_lag(source_date: str | None, target_date: str | None) -> int | None:
    source = parse_iso_date(source_date)
    target = parse_iso_date(target_date)
    if source is None or target is None:
        return None
    if source >= target:
        return 0
    lag = 0
    cursor = source + timedelta(days=1)
    while cursor <= target:
        if cursor.weekday() < 5:
            lag += 1
        cursor += timedelta(days=1)
    return lag


def curve_gap_type(
    rows: list[dict[str, Any]],
    max_benchmark_lag_business_days: int,
) -> tuple[str, int | None]:
    if not rows:
        return "策略曲线缺失", None
    strategy_dates = [str(row.get("trade_date") or "") for row in rows if row.get("trade_date")]
    benchmark_dates = [
        str(row.get("trade_date") or "")
        for row in rows
        if row.get("trade_date") and row.get("benchmark_return") is not None
    ]
    if not benchmark_dates:
        return "基准曲线缺失", None
    lag = business_day_lag(max(benchmark_dates), max(strategy_dates)) if strategy_dates else None
    if lag is None or lag > max(0, int(max_benchmark_lag_business_days)):
        return "基准曲线滞后", lag
    return "", lag


def load_latest_dates(db_path: Path) -> dict[str, str]:
    sql = f'''
        SELECT "{C_SOURCE_ID}" AS source_strategy_id, MAX("{C_TRADE_DATE}") AS latest_date
        FROM "{T_DAILY}"
        WHERE "{C_CHANNEL_ID}" = ?
        GROUP BY "{C_SOURCE_ID}"
    '''
    with sqlite3.connect(db_path) as conn:
        return {
            str(source_id): str(latest_date)
            for source_id, latest_date in conn.execute(sql, (CHANNEL_ID,))
            if source_id and latest_date
        }


def parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def choose_range_code(
    *,
    configured_range: str,
    auto_incremental: bool,
    local_latest_date: str | None,
    expected_latest_date: str | None,
    full_history_gap_days: int,
) -> str:
    if not auto_incremental:
        return configured_range
    # Other RANGE values return a rebased interval point (often 0/0 on the
    # latest date), not the strategy's since-inception cumulative curve.
    # Fetch the authoritative `ln` curve and trim it locally to the overlap.
    return DEFAULT_RANGE


def filter_incremental_rows(
    rows: list[dict[str, Any]], *, local_latest_date: str | None, overlap_days: int
) -> list[dict[str, Any]]:
    local_date = parse_iso_date(local_latest_date)
    if local_date is None:
        return rows
    cutoff = (local_date - timedelta(days=max(0, overlap_days))).isoformat()
    return [row for row in rows if str(row.get("trade_date") or "") >= cutoff]


def curve_params(strategy_id: str, range_code: str) -> dict[str, str]:
    return {
        "product": "Fund",
        "appVersion": "6.6.19",
        "serverversion": "6.6.19",
        "version": "6.6.19",
        "plat": "Android",
        "indexCode": "",
        "CODE": strategy_id,
        "RANGE": range_code,
    }


def fetch_curve(
    session: requests.Session,
    strategy: dict[str, Any],
    *,
    range_code: str,
    timeout: float,
    retries: int,
) -> dict[str, Any]:
    strategy_id = str(strategy["source_strategy_id"])
    params = curve_params(strategy_id, range_code)
    url = requests.Request("GET", CURVE_API_URL, params=params).prepare().url or CURVE_API_URL
    last_error = ""
    last_result: dict[str, Any] | None = None
    for attempt in range(1, retries + 1):
        try:
            response = session.get(CURVE_API_URL, params=params, timeout=timeout)
            text = response.text.lstrip("\ufeff")
            parsed = response.json() if not response.text.startswith("\ufeff") else json.loads(text)
            data = parsed.get("data") or parsed.get("Data")
            result = {
                "strategy": strategy,
                "url": url,
                "status_code": response.status_code,
                "text": text,
                "payload": parsed,
                "data": data if isinstance(data, list) else [],
                "ok": response.status_code == 200 and isinstance(data, list) and len(data) > 0,
                "attempts": attempt,
                "error": None,
            }
            if result["ok"]:
                return result
            last_error = f"empty_or_invalid_curve:http={response.status_code}"
            result["error"] = last_error
            last_result = result
            if attempt < retries:
                time.sleep(0.4 * attempt)
        except Exception as exc:  # noqa: BLE001 - keep collection resilient.
            last_error = f"{type(exc).__name__}: {str(exc)[:300]}"
            if attempt < retries:
                time.sleep(0.4 * attempt)
    if last_result is not None:
        last_result["attempts"] = retries
        last_result["error"] = last_error
        return last_result
    return {
        "strategy": strategy,
        "url": url,
        "status_code": None,
        "text": "",
        "payload": None,
        "data": [],
        "ok": False,
        "attempts": retries,
        "error": last_error,
    }


def snapshot_id(strategy_id: str, text: str) -> str:
    digest = hashlib.sha256((strategy_id + "\n" + text).encode("utf-8")).hexdigest()
    return f"ttfund-official_curve-{strategy_id}-{digest[:16]}"


def build_daily_rows(result: dict[str, Any], run_id: str, source_snapshot_id: str) -> list[dict[str, Any]]:
    strategy_id = str(result["strategy"]["source_strategy_id"])
    rows: list[dict[str, Any]] = []
    previous_nav: float | None = None
    for item in result["data"]:
        if not isinstance(item, dict):
            continue
        trade_date = str(item.get("PDATE") or "").strip()
        cumulative_return = to_float(item.get("SE"))
        benchmark_return = to_float(item.get("BENCH_SE"))
        index_return = to_float(item.get("indexSe"))
        if not trade_date or cumulative_return is None:
            continue
        nav = round(1.0 + cumulative_return / 100.0, 8) if cumulative_return is not None else None
        daily_return = None
        if nav is not None and previous_nav not in (None, 0):
            daily_return = round((nav / previous_nav - 1.0) * 100.0, 8)
        elif nav is not None:
            daily_return = 0.0
        if nav is not None:
            previous_nav = nav
        rows.append(
            {
                "channel_id": CHANNEL_ID,
                "source_strategy_id": strategy_id,
                "trade_date": trade_date,
                "nav": nav,
                "daily_return": daily_return,
                "cumulative_return": cumulative_return,
                "benchmark_return": benchmark_return,
                "index_return": index_return,
                "max_drawdown": None,
                "section_name": "成立以来官方业绩曲线",
                "section_type": "official_app_curve",
                "source_snapshot_id": source_snapshot_id,
                "run_id": run_id,
                "source_type": "official_app_curve",
            }
        )
    return rows


def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temp_path.write_text(text, encoding=encoding)
    temp_path.replace(path)


def write_json(path: Path, payload: Any) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    atomic_write_text(path, "".join(compact_json(row) + "\n" for row in rows))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8-sig") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def merge_daily_rows(
    existing_rows: list[dict[str, Any]], official_rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for row in existing_rows:
        key = (str(row.get("source_strategy_id") or ""), str(row.get("trade_date") or ""))
        if all(key):
            merged[key] = dict(row)
    for row in official_rows:
        key = (str(row.get("source_strategy_id") or ""), str(row.get("trade_date") or ""))
        if not all(key):
            continue
        current = merged.get(key, {})
        official_is_rebased_zero = (
            row.get("source_type") == "official_app_curve"
            and float(row.get("nav") or 0.0) == 1.0
            and float(row.get("cumulative_return") or 0.0) == 0.0
            and float(row.get("daily_return") or 0.0) == 0.0
            and float(row.get("benchmark_return") or 0.0) == 0.0
        )
        current_has_nonzero_quote = (
            current.get("source_type") != "official_app_curve"
            and (
                abs(float(current.get("cumulative_return") or 0.0)) > 1e-12
                or abs(float(current.get("nav") or 1.0) - 1.0) > 1e-12
            )
        )
        if official_is_rebased_zero and current_has_nonzero_quote:
            continue
        current.pop("provenance_role", None)
        current.pop("official_source_effective_date", None)
        for field, value in row.items():
            if value not in (None, ""):
                current[field] = value
        merged[key] = current
    return sorted(merged.values(), key=lambda row: (str(row["source_strategy_id"]), str(row["trade_date"])))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns: list[str] = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    temp_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    with temp_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    temp_path.replace(path)


def collect(args: argparse.Namespace) -> dict[str, Any]:
    run_at = now_local()
    run_id = args.run_id or run_at.strftime("%Y%m%dT%H%M%S%z")
    day = run_at.strftime("%Y-%m-%d")
    raw_dir = PROJECT_ROOT / "data" / "raw" / "ttfund" / "official_performance_curve" / day / run_id
    normalized_path = (
        PROJECT_ROOT / "data" / "normalized" / "ttfund" / "strategy_performance_daily" / day / f"{run_id}.jsonl"
    )
    summary_path = PROJECT_ROOT / "data" / "normalized" / "ttfund" / "collection_summary" / day / f"{run_id}.json"
    report_dir = PROJECT_ROOT / "outputs" / "ttfund_official_performance_curve" / day / run_id

    catalog_manifest = load_catalog_manifest(args.catalog_manifest_path)
    strategies = load_strategies(
        args.db_path,
        args.strategy_ids,
        args.limit,
        args.catalog_manifest_path,
    )
    local_latest_dates = load_latest_dates(args.db_path) if args.auto_incremental else {}
    all_daily_rows: list[dict[str, Any]] = []
    fetched_daily_rows_total = 0
    coverage_rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    raw_snapshots: list[dict[str, Any]] = []
    latest_date_by_strategy: dict[str, str] = {}
    latest_benchmark_date_by_strategy: dict[str, str] = {}
    range_counts: dict[str, int] = {}
    progress = ConsoleProgress("天天投顾官方业绩曲线更新", len(strategies))
    progress.emit(0, success=0, failed=0, extra=f"并发数 {max(1, args.workers)}")

    def worker(strategy: dict[str, Any]) -> dict[str, Any]:
        session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
        strategy_id = str(strategy["source_strategy_id"])
        requested_range = choose_range_code(
            configured_range=args.range_code,
            auto_incremental=args.auto_incremental,
            local_latest_date=local_latest_dates.get(strategy_id),
            expected_latest_date=args.expected_latest_date,
            full_history_gap_days=args.full_history_gap_days,
        )
        result = fetch_curve(
            session,
            strategy,
            range_code=requested_range,
            timeout=args.timeout,
            retries=args.retries,
        )
        result["requested_range"] = requested_range
        return result

    raw_curve_dir = raw_dir / "curves"
    raw_curve_dir.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = [executor.submit(worker, strategy) for strategy in strategies]
        for idx, future in enumerate(as_completed(futures), start=1):
            result = future.result()
            if not result["ok"]:
                for _ in range(max(0, args.retry_failed_rounds)):
                    retry_session = requests.Session()
                    retry_session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
                    requested_range = str(result.get("requested_range") or args.range_code)
                    result = fetch_curve(
                        retry_session,
                        result["strategy"],
                        range_code=requested_range,
                        timeout=args.timeout,
                        retries=args.retries,
                    )
                    result["requested_range"] = requested_range
                    if result["ok"]:
                        break
            strategy = result["strategy"]
            strategy_id = str(strategy["source_strategy_id"])
            requested_range = str(result.get("requested_range") or args.range_code)
            range_counts[requested_range] = range_counts.get(requested_range, 0) + 1
            sid_snapshot_id = snapshot_id(strategy_id, result["text"])
            raw_path = raw_curve_dir / f"{strategy_id}.json"
            raw_text = result["text"] or compact_json(
                {
                    "request_url": result["url"],
                    "status_code": result["status_code"],
                    "attempts": result["attempts"],
                    "error": result["error"],
                    "strategy": strategy,
                    "payload": result["payload"],
                }
            )
            write_json(
                raw_path,
                {
                    "request_url": result["url"],
                    "status_code": result["status_code"],
                    "attempts": result["attempts"],
                    "error": result["error"],
                    "strategy": strategy,
                    "payload": result["payload"],
                },
            )
            raw_snapshots.append(
                {
                    "snapshot_id": sid_snapshot_id,
                    "channel_id": CHANNEL_ID,
                    "collector_name": "ttfund_official_performance_curve",
                    "access_level": "public",
                    "captured_at": run_at.isoformat(timespec="seconds"),
                    "source_url": result["url"],
                    "http_status": result["status_code"],
                    "raw_path": str(raw_path.relative_to(raw_dir)),
                    "content_type": "application/json",
                    "content_hash": hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
                    "parse_status": "success" if result["ok"] else "error",
                }
            )
            fetched_daily_rows = build_daily_rows(result, run_id, sid_snapshot_id) if result["ok"] else []
            fetched_daily_rows_total += len(fetched_daily_rows)
            daily_rows = (
                filter_incremental_rows(
                    fetched_daily_rows,
                    local_latest_date=local_latest_dates.get(strategy_id),
                    overlap_days=args.overlap_days,
                )
                if args.auto_incremental and requested_range == DEFAULT_RANGE
                else fetched_daily_rows
            )
            all_daily_rows.extend(daily_rows)
            first_date = fetched_daily_rows[0]["trade_date"] if fetched_daily_rows else None
            last_date = fetched_daily_rows[-1]["trade_date"] if fetched_daily_rows else None
            last_return = fetched_daily_rows[-1]["cumulative_return"] if fetched_daily_rows else None
            if last_date:
                latest_date_by_strategy[strategy_id] = str(last_date)
            benchmark_dates = [
                str(row["trade_date"])
                for row in fetched_daily_rows
                if row.get("benchmark_return") is not None
            ]
            if benchmark_dates:
                latest_benchmark_date_by_strategy[strategy_id] = max(benchmark_dates)
            benchmark_point_count = len(benchmark_dates)
            gap_type, benchmark_lag_business_days = curve_gap_type(
                fetched_daily_rows,
                args.max_source_lag_business_days,
            )
            coverage = {
                "统一策略ID": strategy.get("unified_strategy_id"),
                "渠道策略ID": strategy_id,
                "策略名称": strategy.get("strategy_name"),
                "投顾机构": strategy.get("advisor_name"),
                "成立日期": strategy.get("established_date"),
                "采集成功": 1 if fetched_daily_rows else 0,
                "请求范围": requested_range,
                "官方曲线点数": len(fetched_daily_rows),
                "本批写入点数": len(daily_rows),
                "官方曲线最早日期": first_date,
                "官方曲线最晚日期": last_date,
                "官方曲线末值累计收益率_百分比": last_return,
                "基准曲线点数": benchmark_point_count,
                "基准曲线最晚日期": max(benchmark_dates) if benchmark_dates else None,
                "基准滞后工作日": benchmark_lag_business_days,
                "缺口类型": gap_type,
                "HTTP状态码": result["status_code"],
                "尝试次数": result["attempts"],
                "失败原因": result["error"] or (
                    "接口未返回 data 数组或曲线为空" if not fetched_daily_rows else (
                        "官方曲线未返回 BENCH_SE" if gap_type == "基准曲线缺失" else (
                            "官方基准曲线日期落后于策略曲线" if gap_type == "基准曲线滞后" else ""
                        )
                    )
                ),
                "原始文件": str(raw_path),
                "strategy_status": strategy.get("strategy_status"),
                "quality_scope": classify_quality_scope(strategy),
                "inventory_source": strategy.get("inventory_source"),
            }
            coverage_rows.append(coverage)
            if gap_type:
                errors.append(coverage)
            if idx == 1 or idx % 10 == 0 or idx == len(strategies):
                progress.emit(
                    idx,
                    success=idx - len(errors),
                    failed=len(errors),
                    current=str(result["strategy"].get("source_strategy_id") or ""),
                    extra=f"本批曲线点 {len(all_daily_rows)} | 缺失 {len(errors)}",
                )

    all_daily_rows.sort(key=lambda row: (str(row["source_strategy_id"]), str(row["trade_date"])))
    coverage_rows.sort(key=lambda row: str(row["渠道策略ID"]))
    existing_daily_rows = read_jsonl(normalized_path) if args.merge_existing_run else []
    normalized_rows = merge_daily_rows(existing_daily_rows, all_daily_rows)
    write_csv(report_dir / "official_curve_coverage.csv", coverage_rows)
    write_csv(report_dir / "official_curve_missing.csv", errors)
    raw_manifest_path = raw_dir / "_manifest.json"
    write_json(
        raw_manifest_path,
        {
            "channel_id": CHANNEL_ID,
            "collector_name": "ttfund_official_performance_curve",
            "run_id": run_id,
            "captured_at": run_at.isoformat(timespec="seconds"),
            "raw_snapshots": raw_snapshots,
        },
    )

    curve_strategy_total = sum(1 for row in coverage_rows if row["采集成功"])
    curve_missing_strategy_total = sum(1 for row in coverage_rows if row.get("缺口类型") == "策略曲线缺失")
    benchmark_gap_strategy_total = sum(
        1 for row in coverage_rows if str(row.get("缺口类型") or "").startswith("基准曲线")
    )
    point_counts = [int(row["官方曲线点数"] or 0) for row in coverage_rows]
    strategy_total = len(strategies)
    quality_scope_by_id = {
        str(strategy["source_strategy_id"]): classify_quality_scope(strategy) for strategy in strategies
    }
    eligible_ids = {
        strategy_id for strategy_id, scope in quality_scope_by_id.items() if scope == "active"
    }
    excluded_stopped_ids = sorted(
        strategy_id for strategy_id, scope in quality_scope_by_id.items() if scope == "stopped"
    )
    excluded_test_ids = sorted(
        strategy_id for strategy_id, scope in quality_scope_by_id.items() if scope == "test"
    )
    eligible_success_total = sum(1 for strategy_id in eligible_ids if strategy_id in latest_date_by_strategy)
    success_ratio = eligible_success_total / len(eligible_ids) if eligible_ids else 1.0
    source_effective_date = disclosure_date_at_coverage(
        latest_date_by_strategy,
        eligible_ids,
        args.min_latest_ratio,
    )
    latest_strategy_total = sum(
        1
        for strategy_id, value in latest_date_by_strategy.items()
        if strategy_id in eligible_ids and (not source_effective_date or value >= source_effective_date)
    )
    latest_ratio = latest_strategy_total / len(eligible_ids) if eligible_ids else 1.0
    latest_benchmark_total = sum(
        1
        for strategy_id, value in latest_benchmark_date_by_strategy.items()
        if strategy_id in eligible_ids and (not source_effective_date or value >= source_effective_date)
    )
    benchmark_latest_ratio = latest_benchmark_total / len(eligible_ids) if eligible_ids else 1.0
    source_lag_business_days = business_day_lag(source_effective_date, args.expected_latest_date)
    quote_fallback_ids = sorted(
        {
            str(row.get("source_strategy_id") or "")
            for row in existing_daily_rows
            if str(row.get("source_strategy_id") or "") in eligible_ids
            and args.expected_latest_date
            and str(row.get("trade_date") or "") >= args.expected_latest_date
            and latest_date_by_strategy.get(str(row.get("source_strategy_id") or ""), "")
            < args.expected_latest_date
        }
    )
    if source_lag_business_days:
        for row in normalized_rows:
            strategy_id = str(row.get("source_strategy_id") or "")
            if (
                strategy_id in eligible_ids
                and str(row.get("trade_date") or "") > str(source_effective_date or "")
                and row.get("source_type") != "official_app_curve"
            ):
                row["provenance_role"] = "quote_calculated_fallback"
                row["official_source_effective_date"] = source_effective_date
    quality_errors: list[str] = []
    quality_warnings: list[str] = []
    failure_class: str | None = None
    if success_ratio < args.min_success_ratio:
        quality_errors.append(f"success_ratio={success_ratio:.6f} < {args.min_success_ratio:.6f}")
        failure_class = "data_quality"
    if source_effective_date and latest_ratio < args.min_latest_ratio:
        quality_errors.append(f"latest_ratio={latest_ratio:.6f} < {args.min_latest_ratio:.6f}")
        failure_class = failure_class or "data_quality"
    if source_effective_date and benchmark_latest_ratio < args.min_benchmark_latest_ratio:
        quality_errors.append(
            f"benchmark_latest_ratio={benchmark_latest_ratio:.6f} < {args.min_benchmark_latest_ratio:.6f}"
        )
        failure_class = failure_class or "data_quality"
    if source_effective_date is None:
        quality_errors.append("source_effective_date is unavailable")
        failure_class = "source_unavailable"
    elif source_lag_business_days is not None and source_lag_business_days > args.max_source_lag_business_days:
        quality_errors.append(
            "source_lag_business_days="
            f"{source_lag_business_days} > {args.max_source_lag_business_days}"
        )
        failure_class = "source_not_ready"
    elif source_lag_business_days:
        quality_warnings.append(
            f"official curve lags quote target by {source_lag_business_days} business day(s); "
            "quote rows are retained as calculated fallback"
        )
    summary = {
        "channel_id": CHANNEL_ID,
        "channel_name": CHANNEL_NAME,
        "collector_name": "ttfund_official_performance_curve",
        "run_id": run_id,
        "captured_at": run_at.isoformat(timespec="seconds"),
        "range_code": args.range_code,
        "auto_incremental": bool(args.auto_incremental),
        "range_counts": range_counts,
        "source_url": CURVE_API_URL,
        "raw_dir": str(raw_dir),
        "normalized_dir": str(PROJECT_ROOT / "data" / "normalized" / "ttfund"),
        "strategy_total": len(strategies),
        "database_inventory_total": sum(
            1 for strategy in strategies if str(strategy.get("inventory_source") or "").startswith("database")
        ),
        "catalog_manifest_path": catalog_manifest.get("manifest_path"),
        "catalog_manifest_state": catalog_manifest.get("state"),
        "catalog_inventory_total": len(catalog_strategy_seeds(catalog_manifest)),
        "catalog_only_strategy_total": sum(
            1 for strategy in strategies if strategy.get("inventory_source") == "catalog_manifest"
        ),
        "catalog_only_strategy_ids": sorted(
            str(strategy.get("source_strategy_id") or "")
            for strategy in strategies
            if strategy.get("inventory_source") == "catalog_manifest"
        ),
        "curve_strategy_total": curve_strategy_total,
        "missing_strategy_total": len(errors),
        "curve_missing_strategy_total": curve_missing_strategy_total,
        "benchmark_gap_strategy_total": benchmark_gap_strategy_total,
        "daily_rows_total": len(all_daily_rows),
        "fetched_daily_rows_total": fetched_daily_rows_total,
        "min_points_per_strategy": min(point_counts) if point_counts else 0,
        "max_points_per_strategy": max(point_counts) if point_counts else 0,
        "normalized_strategy_performance_daily": str(normalized_path),
        "raw_manifest": str(raw_manifest_path),
        "raw_snapshot_total": len(raw_snapshots),
        "coverage_csv": str(report_dir / "official_curve_coverage.csv"),
        "missing_csv": str(report_dir / "official_curve_missing.csv"),
        "state": "failed_quality_gate" if quality_errors else (
            "ready_with_quote_fallback" if source_lag_business_days else "ready"
        ),
        "failure_class": failure_class,
        "expected_latest_date": args.expected_latest_date,
        "source_effective_date": source_effective_date,
        "source_lag_business_days": source_lag_business_days,
        "max_source_lag_business_days": args.max_source_lag_business_days,
        "quality_eligible_strategy_total": len(eligible_ids),
        "quality_excluded_stopped_total": len(excluded_stopped_ids),
        "quality_excluded_stopped_strategy_ids": excluded_stopped_ids,
        "quality_excluded_test_total": len(excluded_test_ids),
        "quality_excluded_test_strategy_ids": excluded_test_ids,
        "eligible_success_total": eligible_success_total,
        "quote_fallback_strategy_total": len(quote_fallback_ids),
        "quote_fallback_strategy_ids": quote_fallback_ids,
        "success_ratio": success_ratio,
        "latest_strategy_total": latest_strategy_total,
        "latest_ratio": latest_ratio,
        "latest_benchmark_total": latest_benchmark_total,
        "benchmark_latest_ratio": benchmark_latest_ratio,
        "quality_errors": quality_errors,
        "quality_warnings": quality_warnings,
        "existing_daily_rows_total": len(existing_daily_rows),
        "merged_daily_rows_total": len(normalized_rows),
    }
    write_json(report_dir / "official_curve_summary.json", summary)
    existing_summary: dict[str, Any] = {}
    if args.merge_existing_run and summary_path.exists():
        try:
            existing_summary = json.loads(summary_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            existing_summary = {}
    merged_summary = dict(existing_summary)
    merged_summary.update(
        {
            "batch_state": "failed_quality_gate" if quality_errors else "ready",
            "official_curve": summary,
            "captured_at": summary["captured_at"],
        }
    )
    if quality_errors:
        write_json(summary_path, merged_summary or summary)
        raise RuntimeError("official curve quality gate failed: " + "; ".join(quality_errors))
    write_jsonl(normalized_path, normalized_rows)
    write_json(summary_path, merged_summary or summary)
    report_lines = [
        "# 天天基金 App 官方业绩曲线采集报告",
        "",
        f"- 采集批次：`{run_id}`",
        f"- 策略总数：{len(strategies)}",
        f"- 成功拿到成立以来曲线：{curve_strategy_total}",
        f"- 未拿到曲线：{len(errors)}",
        f"- 官方日度曲线行数：{len(all_daily_rows)}",
        f"- 输出文件：`{normalized_path}`",
        "",
        "## 字段口径",
        "",
        "- `PDATE` 标准化为交易日期。",
        "- `SE` 标准化为策略成立以来累计收益率_百分比，并派生单位净值 `1 + SE / 100`。",
        "- `BENCH_SE` 标准化为基准收益率_百分比。",
        "- `indexSe` 标准化为指数收益率_百分比。",
        "- 日收益率由相邻官方净值点反推，首日置为 0。",
    ]
    (report_dir / "official_curve_report.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    return summary


def main() -> None:
    args = parse_args()
    summary = collect(args)
    console_summary = {
        key: summary.get(key)
        for key in (
            "state",
            "run_id",
            "strategy_total",
            "catalog_only_strategy_total",
            "curve_strategy_total",
            "curve_missing_strategy_total",
            "benchmark_gap_strategy_total",
            "source_effective_date",
            "source_lag_business_days",
            "success_ratio",
            "latest_ratio",
            "benchmark_latest_ratio",
            "quality_errors",
            "quality_warnings",
            "coverage_csv",
            "missing_csv",
        )
    }
    print(compact_json(console_summary))


if __name__ == "__main__":
    main()
