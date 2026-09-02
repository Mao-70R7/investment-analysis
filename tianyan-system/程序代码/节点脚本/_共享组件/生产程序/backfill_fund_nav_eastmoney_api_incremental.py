from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
SCRIPT_DIR = PROJECT_ROOT / "节点脚本" / "_共享组件" / "生产程序"
sys.path.insert(0, str(SCRIPT_DIR))

import backfill_fund_history_analysis_sqlite as base  # noqa: E402


DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
DEFAULT_SCHEMA_PATH = PROJECT_ROOT / "schemas" / "analysis_zh_current.sql"
NORMALIZED_ROOT = PROJECT_ROOT / "data" / "normalized" / "ttfund_fund_nav"
RAW_ROOT = PROJECT_ROOT / "data" / "raw" / "ttfund_fund_nav" / "eastmoney_api"
API_URL = "https://api.fund.eastmoney.com/f10/lsjz"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
CN_TZ = timezone(timedelta(hours=8))


K_FUND_CODE = "\u57fa\u91d1\u4ee3\u7801"
K_FUND_NAME = "\u57fa\u91d1\u540d\u79f0"
K_FUND_TYPE = "\u57fa\u91d1\u7c7b\u578b"
K_FUND_COMPANY = "\u57fa\u91d1\u516c\u53f8"
K_TRADE_DATE = "\u4ea4\u6613\u65e5\u671f"
K_NAV_TYPE = "\u51c0\u503c\u53e3\u5f84"
K_UNIT_NAV = "\u5355\u4f4d\u51c0\u503c"
K_ACC_NAV = "\u7d2f\u8ba1\u51c0\u503c"
K_DAILY_RET = "\u65e5\u6536\u76ca\u7387_\u767e\u5206\u6bd4"
K_PER_10K = "\u6bcf\u4e07\u4efd\u6536\u76ca"
K_7D_ANNUAL = "\u4e03\u65e5\u5e74\u5316\u6536\u76ca\u7387_\u767e\u5206\u6bd4"
K_DIV_HINT = "\u51c0\u503c\u56fe\u5206\u7ea2\u9001\u914d"
K_IS_MONEY = "\u662f\u5426\u8d27\u5e01\u57fa\u91d1"
K_SOURCE = "\u6570\u636e\u6765\u6e90"
K_SNAPSHOT_ID = "\u539f\u59cb\u51c0\u503c\u5feb\u7167ID"
K_CAPTURED_AT = "\u91c7\u96c6\u65f6\u95f4"
K_START_DATE = "\u5386\u53f2\u8d77\u59cb\u65e5\u671f"
K_END_DATE = "\u5386\u53f2\u7ed3\u675f\u65e5\u671f"
K_RECORD_COUNT = "\u5386\u53f2\u8bb0\u5f55\u6570"
K_DIV_COUNT = "\u5206\u7ea2\u4e8b\u4ef6\u6570"
K_LATEST_UNIT_NAV = "\u6700\u65b0\u5355\u4f4d\u51c0\u503c"
K_LATEST_ACC_NAV = "\u6700\u65b0\u7d2f\u8ba1\u51c0\u503c"
K_LATEST_DAILY_RET = "\u6700\u65b0\u65e5\u6536\u76ca\u7387_\u767e\u5206\u6bd4"
K_LATEST_PER_10K = "\u6700\u65b0\u6bcf\u4e07\u4efd\u6536\u76ca"
K_LATEST_7D_ANNUAL = "\u6700\u65b0\u4e03\u65e5\u5e74\u5316\u6536\u76ca\u7387_\u767e\u5206\u6bd4"
K_DIV_SNAPSHOT_ID = "\u539f\u59cb\u5206\u7ea2\u5feb\u7167ID"
K_RECENT_CAPTURED_AT = "\u6700\u8fd1\u91c7\u96c6\u65f6\u95f4"
T_NAV_META = "\u57fa\u91d1\u51c0\u503c\u6982\u51b5"


@dataclass(frozen=True)
class FetchResult:
    fund_code: str
    fund_name: str | None
    status: str
    start_date: str | None
    end_date: str | None
    rows: list[dict[str, Any]]
    meta_row: dict[str, Any] | None
    raw_snapshot: dict[str, Any] | None
    raw_path: str | None
    error: str | None = None


def now_cn() -> datetime:
    return datetime.now(CN_TZ)


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp_path.replace(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Incrementally update fund NAV rows with Eastmoney f10 JSON API."
    )
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--schema-path", type=Path, default=DEFAULT_SCHEMA_PATH)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--timeout-sec", type=int, default=15)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--page-size", type=int, default=200)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--fund-code", action="append", default=[])
    parser.add_argument("--fund-code-file", action="append", type=Path, default=[])
    parser.add_argument(
        "--target-source",
        choices=["positioned", "current-dict", "all-dict"],
        default="all-dict",
    )
    parser.add_argument(
        "--only-nav-before",
        help="Only update funds whose local latest NAV date is missing or earlier than this YYYY-MM-DD date.",
    )
    parser.add_argument(
        "--only-captured-before",
        help="Resume mode: only update funds never captured or last captured before this ISO timestamp.",
    )
    parser.add_argument(
        "--end-date",
        default=now_cn().date().isoformat(),
        help="API end date. Default: today in Asia/Shanghai.",
    )
    parser.add_argument(
        "--missing-start-date",
        default="1990-01-01",
        help="Start date for funds without local NAV history. Defaults to a full-history lower bound.",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=2,
        help="Fetch this many extra calendar days before the local latest date for overlap validation.",
    )
    parser.add_argument("--commit-every", type=int, default=100)
    parser.add_argument("--progress-every", type=int, default=200)
    parser.add_argument("--no-output-files", action="store_true")
    parser.add_argument("--no-raw-files", action="store_true")
    return parser.parse_args()


def normalize_code(value: str | None) -> str | None:
    if value is None:
        return None
    text = "".join(ch for ch in str(value).strip() if ch.isdigit())
    if not text:
        return None
    return text.zfill(6)


def to_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "").replace("%", "")
    if not text or text in {"--", "-", "null", "None"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def add_days(date_text: str, days: int) -> str:
    return (datetime.strptime(date_text, "%Y-%m-%d").date() + timedelta(days=days)).isoformat()


def selected_codes_from_args(args: argparse.Namespace) -> set[str]:
    codes: set[str] = set()
    for raw in args.fund_code:
        code = normalize_code(raw)
        if code:
            codes.add(code)
    for path in args.fund_code_file:
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            code = normalize_code(line.split("#", 1)[0])
            if code:
                codes.add(code)
    return codes


def discover_targets(conn: sqlite3.Connection, args: argparse.Namespace) -> list[base.FundTarget]:
    if args.target_source == "all-dict":
        targets = base.discover_dict_fund_targets(conn, current_only=False)
    elif args.target_source == "current-dict":
        targets = base.discover_dict_fund_targets(conn, current_only=True)
    else:
        targets = base.discover_fund_targets(conn)

    selected = selected_codes_from_args(args)
    if selected:
        target_map = {target.fund_code: target for target in targets}
        targets = [target_map.get(code, base.FundTarget(code, None, None, None)) for code in sorted(selected)]

    latest_dates = base.discover_existing_latest_nav_dates(conn)
    if args.only_nav_before:
        cutoff = args.only_nav_before
        targets = [target for target in targets if (latest_dates.get(target.fund_code) or "") < cutoff]
    if args.only_captured_before:
        recent_capture_by_code = {
            str(code): str(captured_at)
            for code, captured_at in conn.execute(
                f'SELECT "{K_FUND_CODE}", "{K_RECENT_CAPTURED_AT}" FROM "{T_NAV_META}"'
            )
            if code and captured_at
        }
        cutoff = args.only_captured_before
        targets = [
            target
            for target in targets
            if (recent_capture_by_code.get(target.fund_code) or "") < cutoff
        ]
    if args.limit and args.limit > 0:
        targets = targets[: args.limit]
    return targets


def api_url(
    fund_code: str,
    start_date: str | None,
    end_date: str | None,
    page_size: int,
    page_index: int = 1,
) -> str:
    params = {
        "fundCode": fund_code,
        "pageIndex": max(1, page_index),
        "pageSize": max(1, min(page_size, 5000)),
    }
    if start_date:
        params["startDate"] = start_date
    if end_date:
        params["endDate"] = end_date
    return f"{API_URL}?{urlencode(params)}"


def fetch_json(url: str, *, timeout_sec: int, retries: int) -> bytes:
    last_error: Exception | None = None
    for attempt in range(1, max(1, retries + 1) + 1):
        try:
            req = Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Referer": "https://fundf10.eastmoney.com/",
                    "Accept": "application/json,text/plain,*/*",
                },
            )
            with urlopen(req, timeout=timeout_sec) as response:
                return response.read()
        except Exception as error:  # noqa: BLE001 - keep collector robust and summarize failures.
            last_error = error
            if attempt <= retries:
                time.sleep(0.6 * attempt)
    raise RuntimeError(str(last_error))


def build_raw_snapshot(
    *,
    fund_code: str,
    url: str,
    raw_bytes: bytes,
    raw_path: Path | None,
    captured_at: str,
) -> dict[str, Any]:
    unique_hint = f"{url}|{fund_code}|{captured_at}"
    snapshot_id = base.build_snapshot_id("ttfund_fund_nav", "eastmoney_f10_lsjz_api", raw_bytes, unique_hint)
    return {
        "snapshot_id": snapshot_id,
        "channel_id": "ttfund_fund_nav",
        "collector_name": "eastmoney_f10_lsjz_api",
        "access_level": "public",
        "captured_at": captured_at,
        "source_url": url,
        "http_status": 200,
        "raw_path": str(raw_path.resolve()) if raw_path else None,
        "content_type": "application/json",
        "content_hash": hashlib.sha256(raw_bytes).hexdigest(),
        "parse_status": "success",
    }


def parse_payload(raw_bytes: bytes) -> dict[str, Any]:
    text = raw_bytes.decode("utf-8-sig", errors="replace").strip()
    if text.startswith("jQuery(") and text.endswith(")"):
        text = text[len("jQuery(") : -1]
    payload = json.loads(text)
    if int(payload.get("ErrCode") or 0) != 0:
        raise ValueError(f"api ErrCode={payload.get('ErrCode')} ErrMsg={payload.get('ErrMsg')}")
    return payload


def fetch_payload_pages(
    fund_code: str,
    start_date: str | None,
    end_date: str | None,
    *,
    page_size: int,
    timeout_sec: int,
    retries: int,
) -> tuple[str, bytes, dict[str, Any], int]:
    effective_page_size = max(1, min(page_size, 5000))
    first_url = ""
    first_payload: dict[str, Any] | None = None
    merged_items: list[dict[str, Any]] = []
    seen_rows: set[tuple[str, str, str]] = set()
    page_count = 0
    # The public endpoint currently caps each response at 20 rows even when a
    # larger pageSize is requested. New funds therefore need explicit paging.
    observed_page_capacity = min(effective_page_size, 20)
    for page_index in range(1, 1001):
        url = api_url(fund_code, start_date, end_date, effective_page_size, page_index)
        if not first_url:
            first_url = url
        raw_bytes = fetch_json(url, timeout_sec=timeout_sec, retries=retries)
        payload = parse_payload(raw_bytes)
        if first_payload is None:
            first_payload = payload
        data = payload.get("Data") or {}
        items = data.get("LSJZList") or []
        if not isinstance(items, list):
            items = []
        page_count += 1
        new_rows = 0
        for item in items:
            if not isinstance(item, dict):
                continue
            key = (
                str(item.get("FSRQ") or ""),
                str(item.get("DWJZ") or ""),
                str(item.get("LJJZ") or ""),
            )
            if key in seen_rows:
                continue
            seen_rows.add(key)
            merged_items.append(item)
            new_rows += 1
        if len(items) < observed_page_capacity or new_rows == 0:
            break
    if first_payload is None:
        first_payload = {"Data": {"LSJZList": []}}
    combined = dict(first_payload)
    combined_data = dict(combined.get("Data") or {})
    combined_data["LSJZList"] = merged_items
    combined["Data"] = combined_data
    combined["_collection"] = {"page_count": page_count, "row_count": len(merged_items)}
    combined_bytes = json.dumps(combined, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return first_url, combined_bytes, combined, page_count


def rows_from_payload(
    *,
    target: base.FundTarget,
    payload: dict[str, Any],
    captured_at: str,
    run_id: str,
    snapshot_id: str,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    data = payload.get("Data") or {}
    items = data.get("LSJZList") or []
    sy_type = str(data.get("SYType") or "")
    fund_type_code = str(data.get("FundType") or "")
    is_money = sy_type == "\u6bcf\u4e07\u4efd\u6536\u76ca" or fund_type_code == "005"
    nav_type = "\u8d27\u5e01\u57fa\u91d1\u6536\u76ca" if is_money else "\u5355\u4f4d\u51c0\u503c"
    source = "\u5929\u5929\u57fa\u91d1_f10_lsjz_api"

    rows: list[dict[str, Any]] = []
    for item in items:
        trade_date = str(item.get("FSRQ") or "").strip()
        if not trade_date:
            continue
        unit_or_income = to_float(item.get("DWJZ"))
        acc_or_annual = to_float(item.get("LJJZ"))
        if is_money:
            unit_nav = None
            acc_nav = None
            daily_ret = round(unit_or_income / 100, 8) if unit_or_income is not None else None
            per_10k = unit_or_income
            annual = acc_or_annual
        else:
            unit_nav = unit_or_income
            acc_nav = acc_or_annual
            daily_ret = to_float(item.get("JZZZL"))
            per_10k = None
            annual = None
        rows.append(
            {
                K_FUND_CODE: target.fund_code,
                K_TRADE_DATE: trade_date,
                K_FUND_NAME: target.fund_name,
                K_FUND_TYPE: target.fund_type,
                K_FUND_COMPANY: target.fund_company,
                K_NAV_TYPE: nav_type,
                K_UNIT_NAV: unit_nav,
                K_ACC_NAV: acc_nav,
                K_DAILY_RET: daily_ret,
                K_PER_10K: per_10k,
                K_7D_ANNUAL: annual,
                K_DIV_HINT: item.get("FHSP") or item.get("FHFCZ") or None,
                K_IS_MONEY: 1 if is_money else 0,
                K_SOURCE: source,
                K_SNAPSHOT_ID: snapshot_id,
                K_CAPTURED_AT: captured_at,
                "run_id": run_id,
            }
        )
    rows.sort(key=lambda row: row[K_TRADE_DATE] or "", reverse=True)
    if not rows:
        return rows, None

    latest = rows[0]
    trade_dates = [row[K_TRADE_DATE] for row in rows if row.get(K_TRADE_DATE)]
    meta_row = {
        K_FUND_CODE: target.fund_code,
        K_FUND_NAME: target.fund_name,
        K_FUND_TYPE: target.fund_type,
        K_FUND_COMPANY: target.fund_company,
        K_NAV_TYPE: nav_type,
        K_IS_MONEY: 1 if is_money else 0,
        K_START_DATE: min(trade_dates),
        K_END_DATE: max(trade_dates),
        K_RECORD_COUNT: len(rows),
        K_DIV_COUNT: 0,
        K_LATEST_UNIT_NAV: latest.get(K_UNIT_NAV),
        K_LATEST_ACC_NAV: latest.get(K_ACC_NAV),
        K_LATEST_DAILY_RET: latest.get(K_DAILY_RET),
        K_LATEST_PER_10K: latest.get(K_PER_10K),
        K_LATEST_7D_ANNUAL: latest.get(K_7D_ANNUAL),
        K_SOURCE: source,
        K_SNAPSHOT_ID: snapshot_id,
        K_DIV_SNAPSHOT_ID: None,
        K_RECENT_CAPTURED_AT: captured_at,
    }
    return rows, meta_row


def fetch_one(
    target: base.FundTarget,
    *,
    latest_date: str | None,
    args: argparse.Namespace,
    captured_at: str,
    run_id: str,
    raw_dir: Path,
) -> FetchResult:
    start_date = add_days(latest_date, -max(0, args.lookback_days)) if latest_date else args.missing_start_date
    end_date = args.end_date
    try:
        url, raw_bytes, payload, _ = fetch_payload_pages(
            target.fund_code,
            start_date,
            end_date,
            page_size=args.page_size,
            timeout_sec=max(1, args.timeout_sec),
            retries=max(0, args.retries),
        )
        raw_path = None
        if not args.no_raw_files:
            raw_path = raw_dir / "funds" / f"{target.fund_code}.json"
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_bytes(raw_bytes)
        snapshot = build_raw_snapshot(
            fund_code=target.fund_code,
            url=url,
            raw_bytes=raw_bytes,
            raw_path=raw_path,
            captured_at=captured_at,
        )
        rows, meta_row = rows_from_payload(
            target=target,
            payload=payload,
            captured_at=captured_at,
            run_id=run_id,
            snapshot_id=snapshot["snapshot_id"],
        )
        status = "success" if rows else "empty"
        return FetchResult(
            fund_code=target.fund_code,
            fund_name=target.fund_name,
            status=status,
            start_date=start_date,
            end_date=end_date,
            rows=rows,
            meta_row=meta_row,
            raw_snapshot=snapshot,
            raw_path=str(raw_path) if raw_path else None,
        )
    except Exception as error:  # noqa: BLE001
        return FetchResult(
            fund_code=target.fund_code,
            fund_name=target.fund_name,
            status="failed",
            start_date=start_date,
            end_date=end_date,
            rows=[],
            meta_row=None,
            raw_snapshot=None,
            raw_path=None,
            error=str(error),
        )


def main() -> None:
    args = parse_args()
    run_at = now_cn()
    day = run_at.date().isoformat()
    run_id = run_at.strftime("%Y%m%dT%H%M%S%z")
    captured_at = run_at.isoformat(timespec="seconds")
    raw_dir = RAW_ROOT / day / run_id
    normalized_daily_path = NORMALIZED_ROOT / "fund_nav_history_daily" / day / f"{run_id}.jsonl"
    normalized_meta_path = NORMALIZED_ROOT / "fund_nav_history_meta" / day / f"{run_id}.jsonl"
    summary_path = NORMALIZED_ROOT / "collection_summary" / day / f"{run_id}.json"
    progress_path = NORMALIZED_ROOT / "collection_summary" / day / f"{run_id}.progress.json"

    conn = sqlite3.connect(args.db_path)
    conn.row_factory = sqlite3.Row
    base.init_db(conn, args.schema_path)
    latest_dates = base.discover_existing_latest_nav_dates(conn)
    targets = discover_targets(conn, args)

    counters = {
        "targets": len(targets),
        "success": 0,
        "empty": 0,
        "failed": 0,
        "daily_rows": 0,
        "meta_rows": 0,
        "raw_snapshots": 0,
    }
    failures: list[dict[str, Any]] = []
    empty_funds: list[dict[str, Any]] = []
    latest_dates_after: dict[str, int] = {}
    success_since_commit = 0
    started = time.time()

    def write_progress(state: str, error: str | None = None) -> None:
        atomic_write_json(
            progress_path,
            {
                "run_id": run_id,
                "state": state,
                "updated_at": now_cn().isoformat(timespec="seconds"),
                "captured_at": captured_at,
                "target_source": args.target_source,
                "end_date": args.end_date,
                "counters": counters,
                "failures_sample": failures[-50:],
                "empty_funds_sample": empty_funds[-50:],
                "error": error,
                "elapsed_seconds": round(time.time() - started, 3),
            },
        )

    print(
        json.dumps(
            {
                "run_id": run_id,
                "targets": len(targets),
                "target_source": args.target_source,
                "only_nav_before": args.only_nav_before,
                "only_captured_before": args.only_captured_before,
                "end_date": args.end_date,
                "workers": args.workers,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

    try:
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
            future_map = {
                executor.submit(
                    fetch_one,
                    target,
                    latest_date=latest_dates.get(target.fund_code),
                    args=args,
                    captured_at=captured_at,
                    run_id=run_id,
                    raw_dir=raw_dir,
                ): target
                for target in targets
            }
            for index, future in enumerate(as_completed(future_map), start=1):
                target = future_map[future]
                result = future.result()
                if result.status == "success" and result.meta_row:
                    base.update_fund_info_from_meta(conn, result.meta_row)
                    base.upsert_daily_rows(conn, result.rows)
                    meta_to_upsert = base.rebuild_meta_row_from_db(conn, target.fund_code, result.meta_row)
                    base.update_fund_info_from_meta(conn, meta_to_upsert)
                    base.upsert_meta_row(conn, meta_to_upsert)
                    if result.raw_snapshot:
                        try:
                            base.upsert_raw_snapshot_index(conn, result.raw_snapshot)
                            counters["raw_snapshots"] += 1
                        except sqlite3.OperationalError as error:
                            if "raw_snapshot" not in str(error):
                                raise
                    if not args.no_output_files:
                        base.write_jsonl(normalized_daily_path, result.rows)
                        base.write_jsonl(normalized_meta_path, [meta_to_upsert])
                    counters["success"] += 1
                    counters["daily_rows"] += len(result.rows)
                    counters["meta_rows"] += 1
                    latest_end = meta_to_upsert.get(K_END_DATE)
                    if latest_end:
                        latest_dates_after[latest_end] = latest_dates_after.get(latest_end, 0) + 1
                    success_since_commit += 1
                elif result.status == "empty":
                    counters["empty"] += 1
                    empty_funds.append(
                        {
                            "fund_code": result.fund_code,
                            "fund_name": result.fund_name,
                            "start_date": result.start_date,
                            "end_date": result.end_date,
                        }
                    )
                else:
                    counters["failed"] += 1
                    failures.append(
                        {
                            "fund_code": result.fund_code,
                            "fund_name": result.fund_name,
                            "start_date": result.start_date,
                            "end_date": result.end_date,
                            "error": result.error,
                        }
                    )

                if success_since_commit >= max(1, args.commit_every):
                    conn.commit()
                    success_since_commit = 0
                    write_progress("running")
                if index == 1 or index == len(targets) or index % max(1, args.progress_every) == 0:
                    elapsed = time.time() - started
                    print(
                        f"[{index}/{len(targets)}] success={counters['success']} empty={counters['empty']} "
                        f"failed={counters['failed']} rows={counters['daily_rows']} elapsed={elapsed:.1f}s",
                        flush=True,
                    )
        conn.commit()
        write_progress("completed")
    except BaseException as exc:
        conn.rollback()
        write_progress("failed", f"{type(exc).__name__}: {exc}")
        raise
    finally:
        conn.close()

    summary = {
        "state": "completed",
        "run_id": run_id,
        "generated_at": now_cn().isoformat(timespec="seconds"),
        "captured_at": captured_at,
        "source": "eastmoney_f10_lsjz_api",
        "db_path": str(args.db_path.resolve()),
        "target_source": args.target_source,
        "only_nav_before": args.only_nav_before,
        "only_captured_before": args.only_captured_before,
        "end_date": args.end_date,
        "workers": args.workers,
        "counters": counters,
        "latest_dates_after": dict(sorted(latest_dates_after.items())),
        "empty_fund_codes": [item["fund_code"] for item in empty_funds],
        "empty_funds_sample": empty_funds[:200],
        "failures": failures,
        "failures_sample": failures[:200],
        "normalized_daily_path": str(normalized_daily_path.resolve()) if not args.no_output_files else None,
        "normalized_meta_path": str(normalized_meta_path.resolve()) if not args.no_output_files else None,
        "raw_dir": str(raw_dir.resolve()) if not args.no_raw_files else None,
        "progress_path": str(progress_path.resolve()),
        "elapsed_seconds": round(time.time() - started, 3),
    }
    atomic_write_json(summary_path, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
