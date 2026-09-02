# -*- coding: utf-8 -*-
"""Sync full public-fund NAV/performance history into analysis SQLite.

The script writes the existing canonical tables:
- 基金日度净值
- 基金净值概况
- 基金信息

It prefers local compounding when daily returns already exist and only fetches
Eastmoney pingzhongdata when source data is missing/stale or --force-fetch is set.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import random
import re
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests
from requests.adapters import HTTPAdapter

from backfill_fund_history_analysis_sqlite import (
    epoch_millis_to_ymd,
    parse_f10_payload,
    parse_js_array,
    parse_js_bool,
    parse_quoted_js_string,
    parse_table_fragment,
    parse_ymd,
    row_as_map,
    to_float,
)


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "public_fund_full_history_sync"
DEFAULT_RAW_ROOT = PROJECT_ROOT / "data" / "raw" / "public_fund_full_history"
PINGZHONG_URL = "https://fund.eastmoney.com/pingzhongdata/{fund_code}.js"
F10_API_URL = "https://fundf10.eastmoney.com/F10DataApi.aspx"
USER_AGENT = "Mozilla/5.0 advisor-monitor/0.1"
CN_TZ = timezone(timedelta(hours=8))
HTTP_LOCAL = threading.local()
DB_WRITE_LOCK = threading.Lock()


@dataclass(frozen=True)
class FundTarget:
    fund_code: str
    fund_name: str
    fund_type: str
    fund_company: str
    is_money: bool
    local_rows: int
    local_adjusted_rows: int
    local_daily_rows: int
    local_first_date: str
    local_latest_date: str


@dataclass
class SyncResult:
    fund_code: str
    fund_name: str
    status: str
    method: str
    rows_parsed: int = 0
    rows_written: int = 0
    first_date: str = ""
    latest_date: str = ""
    adjusted_rows_after: int = 0
    total_rows_after: int = 0
    source_url: str = ""
    raw_path: str = ""
    elapsed_sec: float = 0.0
    error: str = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--raw-root", type=Path, default=DEFAULT_RAW_ROOT)
    parser.add_argument("--target-source", choices=["all-dict", "current-dict"], default="all-dict")
    parser.add_argument("--fund-code", action="append", default=[])
    parser.add_argument("--fund-code-file", type=Path)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--sample-size", type=int, help="Deterministically sample N targets after discovery.")
    parser.add_argument("--sample-seed", type=int, default=20260711)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--timeout-sec", type=int, default=20)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--f10-per-page", type=int, default=2000)
    parser.add_argument("--commit-every", type=int, default=20)
    parser.add_argument("--progress-every", type=int, default=20)
    parser.add_argument("--status-interval-sec", type=int, default=300)
    parser.add_argument("--min-latest-date", help="Only include funds whose latest local NAV date is missing or before this YYYY-MM-DD.")
    parser.add_argument("--refresh", action="store_true", help="Process every discovered target.")
    parser.add_argument("--force-fetch", action="store_true", help="Fetch source pingzhongdata even when local rows can be compounded.")
    parser.add_argument("--no-network", action="store_true", help="Only compound existing local daily returns; do not fetch source data.")
    parser.add_argument("--no-raw", action="store_true", help="Do not archive source JavaScript.")
    parser.add_argument("--audit-sample-size", type=int, default=100)
    parser.add_argument("--audit-max-return-diff-pp", type=float, default=0.05)
    parser.add_argument("--lock-file", type=Path)
    parser.add_argument("--no-lock", action="store_true")
    parser.add_argument("--lock-stale-hours", type=float, default=12.0)
    return parser.parse_args()


def now_cn() -> datetime:
    return datetime.now(CN_TZ)


def clean(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"none", "nan", "null", "--", "-"} else text


def normalize_code(value: Any) -> str:
    text = clean(value)
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits.zfill(6) if digits and len(digits) <= 6 else text


def parse_iso_datetime(value: Any) -> datetime | None:
    text = clean(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=CN_TZ)
    except ValueError:
        return None


def positive(value: Any) -> float | None:
    number = to_float(value)
    return number if number is not None and number > 0 and math.isfinite(number) else None


def ensure_columns(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute('PRAGMA table_info("基金日度净值")')}
    for column, column_type in [("复权净值", "REAL"), ("复权来源", "TEXT")]:
        if column not in columns:
            conn.execute(f'ALTER TABLE "基金日度净值" ADD COLUMN "{column}" {column_type}')
    conn.execute('CREATE INDEX IF NOT EXISTS "idx_基金日度净值_基金代码日期" ON "基金日度净值"("基金代码", "交易日期")')
    conn.commit()


def http_session() -> requests.Session:
    session = getattr(HTTP_LOCAL, "session", None)
    if session is None:
        session = requests.Session()
        adapter = HTTPAdapter(pool_connections=4, pool_maxsize=4, max_retries=0, pool_block=True)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Referer": "https://fund.eastmoney.com/",
                "Accept": "*/*",
                "Accept-Language": "zh-CN,zh;q=0.9",
            }
        )
        HTTP_LOCAL.session = session
    return session


def fetch_text(url: str, *, timeout: int, attempts: int) -> tuple[str, int, float]:
    last_error: Exception | None = None
    started = time.perf_counter()
    for attempt in range(1, attempts + 1):
        try:
            response = http_session().get(url, timeout=timeout)
            status = int(response.status_code)
            if status == 404:
                raise FileNotFoundError("source HTTP 404")
            if status != 200:
                raise RuntimeError(f"HTTP {status}")
            raw = response.content
            return raw.decode("utf-8", errors="replace"), attempt, time.perf_counter() - started
        except Exception as error:  # pragma: no cover - network dependent
            last_error = error
            if attempt < attempts:
                time.sleep(min(8.0, 0.8 * attempt + random.random() * 0.6))
    raise RuntimeError(str(last_error))


class CollectorLock:
    def __init__(self, path: Path, *, stale_hours: float, enabled: bool) -> None:
        self.path = path
        self.stale_hours = max(0.1, stale_hours)
        self.enabled = enabled
        self.token = hashlib.sha1(f"{os.getpid()}:{time.time_ns()}".encode("ascii")).hexdigest()
        self.acquired = False

    def acquire(self, run_id: str) -> None:
        if not self.enabled:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "pid": os.getpid(),
            "runId": run_id,
            "token": self.token,
            "startedAt": now_cn().isoformat(timespec="seconds"),
        }
        while True:
            try:
                with self.path.open("x", encoding="utf-8") as handle:
                    json.dump(payload, handle, ensure_ascii=False, indent=2)
                self.acquired = True
                return
            except FileExistsError as error:
                existing: dict[str, Any] = {}
                stale = False
                try:
                    existing = json.loads(self.path.read_text(encoding="utf-8"))
                    started_at = parse_iso_datetime(existing.get("startedAt"))
                    stale = started_at is not None and (now_cn() - started_at).total_seconds() > self.stale_hours * 3600
                except Exception:
                    try:
                        stale = time.time() - self.path.stat().st_mtime > self.stale_hours * 3600
                    except OSError:
                        stale = False
                if stale:
                    stale_path = self.path.with_suffix(self.path.suffix + f".stale.{now_cn().strftime('%Y%m%dT%H%M%S')}")
                    self.path.replace(stale_path)
                    continue
                raise RuntimeError(f"collector lock exists: {self.path} {existing}") from error

    def release(self) -> None:
        if not self.enabled or not self.acquired:
            return
        try:
            existing = json.loads(self.path.read_text(encoding="utf-8"))
            if existing.get("token") == self.token:
                self.path.unlink()
        except FileNotFoundError:
            pass
        finally:
            self.acquired = False


def load_selected_codes(args: argparse.Namespace) -> set[str]:
    selected = {normalize_code(code) for code in args.fund_code if normalize_code(code)}
    if args.fund_code_file:
        for line in args.fund_code_file.read_text(encoding="utf-8-sig").splitlines():
            code = normalize_code(line.split(",", 1)[0])
            if code and not code.startswith("#"):
                selected.add(code)
    return selected


def discover_targets(conn: sqlite3.Connection, args: argparse.Namespace) -> list[FundTarget]:
    selected = load_selected_codes(args)
    predicates = ['d."基金代码" IS NOT NULL']
    params: list[Any] = []
    if args.target_source == "current-dict":
        predicates.append('COALESCE(d."是否当前库使用", 0) = 1')
    if selected:
        placeholders = ",".join("?" for _ in selected)
        predicates.append(f'd."基金代码" IN ({placeholders})')
        params.extend(sorted(selected))
    where_sql = " AND ".join(predicates)
    rows = conn.execute(
        f'''
        WITH nav AS (
          SELECT
            "基金代码",
            COUNT(*) AS local_rows,
            SUM(CASE WHEN "复权净值" IS NOT NULL THEN 1 ELSE 0 END) AS local_adjusted_rows,
            SUM(CASE WHEN "日收益率_百分比" IS NOT NULL OR "每万份收益" IS NOT NULL THEN 1 ELSE 0 END) AS local_daily_rows,
            MIN("交易日期") AS local_first_date,
            MAX("交易日期") AS local_latest_date
          FROM "基金日度净值"
          GROUP BY "基金代码"
        )
        SELECT
          d."基金代码",
          COALESCE(NULLIF(TRIM(d."标准基金名称"), ''), d."基金代码") AS "基金名称",
          COALESCE(NULLIF(TRIM(d."天天基金细分类"), ''), NULLIF(TRIM(d."标准资产细类"), ''), '') AS "基金类型",
          COALESCE(NULLIF(TRIM(d."基金公司"), ''), '') AS "基金公司",
          COALESCE(d."是否货币基金", 0) AS "是否货币基金",
          COALESCE(nav.local_rows, 0) AS local_rows,
          COALESCE(nav.local_adjusted_rows, 0) AS local_adjusted_rows,
          COALESCE(nav.local_daily_rows, 0) AS local_daily_rows,
          COALESCE(nav.local_first_date, '') AS local_first_date,
          COALESCE(nav.local_latest_date, '') AS local_latest_date
        FROM "基金标准分类字典" d
        LEFT JOIN nav ON nav."基金代码" = d."基金代码"
        WHERE {where_sql}
        ORDER BY d."基金代码"
        ''',
        params,
    ).fetchall()
    targets = [
        FundTarget(
            fund_code=clean(row["基金代码"]),
            fund_name=clean(row["基金名称"]),
            fund_type=clean(row["基金类型"]),
            fund_company=clean(row["基金公司"]),
            is_money=bool(int(row["是否货币基金"] or 0)),
            local_rows=int(row["local_rows"] or 0),
            local_adjusted_rows=int(row["local_adjusted_rows"] or 0),
            local_daily_rows=int(row["local_daily_rows"] or 0),
            local_first_date=clean(row["local_first_date"]),
            local_latest_date=clean(row["local_latest_date"]),
        )
        for row in rows
    ]
    if not args.refresh and not selected:
        cutoff = clean(args.min_latest_date)
        targets = [
            target
            for target in targets
            if target.local_rows == 0
            or target.local_adjusted_rows < target.local_rows
            or (cutoff and (not target.local_latest_date or target.local_latest_date < cutoff))
        ]
    if args.sample_size and args.sample_size > 0 and args.sample_size < len(targets):
        rng = random.Random(args.sample_seed)
        targets = rng.sample(targets, args.sample_size)
    else:
        targets.sort(key=lambda target: hashlib.sha1(target.fund_code.encode("ascii", errors="ignore")).hexdigest())
    if args.limit and args.limit > 0:
        targets = targets[: args.limit]
    return targets


def parse_pingzhong_full(text: str, target: FundTarget) -> tuple[list[dict[str, Any]], str, bool, str]:
    parsed_name = clean(parse_quoted_js_string(text, "fS_name")) or target.fund_name
    is_money = bool(parse_js_bool(text, "ishb")) or target.is_money
    rows: list[dict[str, Any]] = []
    if is_money:
        incomes = parse_js_array(text, "Data_millionCopiesIncome") or []
        annualized = parse_js_array(text, "Data_sevenDaysYearIncome") or []
        annualized_map = {
            epoch_millis_to_ymd(item[0]): to_float(item[1])
            for item in annualized
            if isinstance(item, list) and len(item) >= 2 and epoch_millis_to_ymd(item[0])
        }
        adjusted = 100.0
        for item in sorted(incomes, key=lambda value: value[0] if isinstance(value, list) and value else 0):
            if not isinstance(item, list) or len(item) < 2:
                continue
            trade_date = epoch_millis_to_ymd(item[0])
            per_10k = to_float(item[1])
            if not trade_date or per_10k is None:
                continue
            adjusted *= max(0.000001, 1.0 + per_10k / 10_000.0)
            rows.append(
                {
                    "date": trade_date,
                    "unit": None,
                    "accum": None,
                    "daily": per_10k / 100.0,
                    "per10k": per_10k,
                    "seven_day": annualized_map.get(trade_date),
                    "event": "",
                    "adjusted": adjusted,
                    "source": "天天基金_pingzhongdata_万份收益复利指数",
                }
            )
        if rows:
            return rows, parsed_name, True, "货币基金收益"

    nav_series = parse_js_array(text, "Data_netWorthTrend") or []
    accumulated_series = parse_js_array(text, "Data_ACWorthTrend") or []
    accumulated_map = {
        epoch_millis_to_ymd(item[0]): positive(item[1])
        for item in accumulated_series
        if isinstance(item, list) and len(item) >= 2 and epoch_millis_to_ymd(item[0])
    }
    records: list[dict[str, Any]] = []
    for item in nav_series:
        if not isinstance(item, dict):
            continue
        trade_date = epoch_millis_to_ymd(item.get("x"))
        unit = positive(item.get("y"))
        if not trade_date or unit is None:
            continue
        records.append(
            {
                "date": trade_date,
                "unit": unit,
                "accum": accumulated_map.get(trade_date),
                "daily": to_float(item.get("equityReturn")),
                "event": clean(item.get("unitMoney")),
            }
        )
    records.sort(key=lambda row: row["date"])
    adjusted = 100.0
    previous_unit: float | None = None
    for index, item in enumerate(records):
        daily = item.get("daily")
        if index > 0:
            if daily is None and previous_unit not in (None, 0) and item.get("unit") is not None:
                daily = (item["unit"] / previous_unit - 1.0) * 100.0
            if daily is not None and math.isfinite(daily) and 1.0 + daily / 100.0 > 0:
                adjusted *= 1.0 + daily / 100.0
        previous_unit = item.get("unit")
        rows.append(
            {
                "date": item["date"],
                "unit": item.get("unit"),
                "accum": item.get("accum"),
                "daily": daily,
                "per10k": None,
                "seven_day": None,
                "event": item.get("event") or "",
                "adjusted": adjusted,
                "source": "天天基金_pingzhongdata_日增长率复利指数",
            }
        )
    return rows, parsed_name, False, "单位净值"


def record_value(record: dict[str, Any], *keywords: str) -> Any:
    for key, value in record.items():
        key_text = clean(key)
        if key_text and all(keyword in key_text for keyword in keywords):
            return value
    return None


def parse_f10_full(text: str, target: FundTarget, *, source_label: str) -> tuple[list[dict[str, Any]], bool, str]:
    payload = parse_f10_payload(text)
    headers, table_rows = parse_table_fragment(payload["content"])
    is_money = any("每万份" in clean(header) for header in headers)
    records: list[dict[str, Any]] = []
    for cells in table_rows:
        record = row_as_map(headers, cells)
        trade_date = parse_ymd(record_value(record, "净值", "日期"))
        if not trade_date:
            continue
        unit = positive(record_value(record, "单位", "净值"))
        accum = positive(record_value(record, "累计", "净值"))
        daily = to_float(record_value(record, "日增长率"))
        per_10k = to_float(record_value(record, "每万份"))
        seven_day = to_float(record_value(record, "7日", "年化"))
        if seven_day is None:
            seven_day = to_float(record_value(record, "七日", "年化"))
        records.append(
            {
                "date": trade_date,
                "unit": unit,
                "accum": accum,
                "daily": per_10k / 100.0 if is_money and per_10k is not None else daily,
                "per10k": per_10k,
                "seven_day": seven_day,
                "event": clean(record_value(record, "分红")),
            }
        )

    records.sort(key=lambda row: row["date"])
    rows: list[dict[str, Any]] = []
    adjusted = 100.0
    previous_unit: float | None = None
    for index, item in enumerate(records):
        daily = item.get("daily")
        if is_money:
            per_10k = item.get("per10k")
            if per_10k is not None:
                adjusted *= max(0.000001, 1.0 + per_10k / 10_000.0)
            elif index > 0 and daily is not None and math.isfinite(daily) and 1.0 + daily / 100.0 > 0:
                adjusted *= 1.0 + daily / 100.0
            source = f"{source_label}_万份收益复利指数"
        else:
            unit = item.get("unit")
            if index > 0:
                if daily is None and previous_unit not in (None, 0) and unit is not None:
                    daily = (unit / previous_unit - 1.0) * 100.0
                if daily is not None and math.isfinite(daily) and 1.0 + daily / 100.0 > 0:
                    adjusted *= 1.0 + daily / 100.0
            previous_unit = unit
            source = f"{source_label}_日增长率复利指数"
        rows.append(
            {
                "date": item["date"],
                "unit": item.get("unit"),
                "accum": item.get("accum"),
                "daily": daily,
                "per10k": item.get("per10k"),
                "seven_day": item.get("seven_day"),
                "event": item.get("event") or "",
                "adjusted": adjusted,
                "source": source,
            }
        )
    return rows, is_money, "货币基金收益" if is_money else "单位净值"


def fetch_f10_rows(target: FundTarget, *, args: argparse.Namespace, raw_dir: Path) -> tuple[list[dict[str, Any]], bool, str, str, str, float]:
    started = time.perf_counter()
    all_rows: list[dict[str, Any]] = []
    raw_paths: list[str] = []
    first_url = ""
    pages_total = 1
    page_no = 1
    per_page = max(1, min(int(args.f10_per_page or 2000), 2000))
    is_money = False
    nav_type = "单位净值"
    while page_no <= pages_total:
        params = {
            "type": "lsjz",
            "code": target.fund_code,
            "page": page_no,
            "per": per_page,
        }
        url = f"{F10_API_URL}?{urlencode(params)}"
        if page_no == 1:
            first_url = url
        text, _attempts, _elapsed = fetch_text(url, timeout=max(1, args.timeout_sec), attempts=max(1, args.retries))
        if not args.no_raw:
            fund_raw_dir = raw_dir / target.fund_code
            fund_raw_dir.mkdir(parents=True, exist_ok=True)
            path = fund_raw_dir / f"f10_page_{page_no:04d}.js.gz"
            with gzip.open(path, "wt", encoding="utf-8", compresslevel=5) as handle:
                handle.write(text)
            raw_paths.append(str(path.resolve()))
        payload = parse_f10_payload(text)
        pages_total = max(1, int(payload.get("pages") or 1))
        page_rows, page_is_money, page_nav_type = parse_f10_full(text, target, source_label="天天基金_lsjz")
        if page_rows:
            is_money = page_is_money
            nav_type = page_nav_type
            all_rows.extend(page_rows)
        page_no += 1

    merged: dict[str, dict[str, Any]] = {}
    for row in all_rows:
        merged[row["date"]] = row
    rows = [merged[date] for date in sorted(merged)]
    return rows, is_money, nav_type, first_url, ";".join(raw_paths), time.perf_counter() - started


def fetch_source_rows(target: FundTarget, *, args: argparse.Namespace, run_id: str, raw_dir: Path) -> tuple[list[dict[str, Any]], str, bool, str, str, str, str, float]:
    url = PINGZHONG_URL.format(fund_code=target.fund_code)
    started = time.perf_counter()
    text, attempts, elapsed = fetch_text(url, timeout=max(1, args.timeout_sec), attempts=max(1, args.retries))
    raw_path = ""
    if not args.no_raw:
        raw_dir.mkdir(parents=True, exist_ok=True)
        path = raw_dir / f"{target.fund_code}.js.gz"
        with gzip.open(path, "wt", encoding="utf-8", compresslevel=5) as handle:
            handle.write(text)
        raw_path = str(path.resolve())
    rows, parsed_name, is_money, nav_type = parse_pingzhong_full(text, target)
    method = "source_pingzhongdata"
    if not rows:
        rows, is_money, nav_type, url, raw_path, f10_elapsed = fetch_f10_rows(target, args=args, raw_dir=raw_dir)
        method = "source_f10_lsjz"
        elapsed = max(elapsed, f10_elapsed)
    # Touch elapsed and attempts in the source string through the snapshot id, but keep rows simple.
    _ = attempts
    return rows, parsed_name, is_money, nav_type, url, raw_path, method, max(elapsed, time.perf_counter() - started)


def load_local_rows_for_adjustment(conn: sqlite3.Connection, target: FundTarget) -> tuple[list[dict[str, Any]], bool, str]:
    rows = conn.execute(
        '''
        SELECT "交易日期", "单位净值", "累计净值", "日收益率_百分比", "每万份收益",
               "七日年化收益率_百分比", "净值图分红送配", "是否货币基金"
        FROM "基金日度净值"
        WHERE "基金代码" = ?
        ORDER BY "交易日期"
        ''',
        [target.fund_code],
    ).fetchall()
    output: list[dict[str, Any]] = []
    adjusted = 100.0
    previous_unit: float | None = None
    is_money = target.is_money
    for index, row in enumerate(rows):
        trade_date = clean(row["交易日期"])
        if not trade_date:
            continue
        unit = positive(row["单位净值"])
        daily = to_float(row["日收益率_百分比"])
        per_10k = to_float(row["每万份收益"])
        row_is_money = bool(int(row["是否货币基金"] or 0)) or per_10k is not None or (is_money and unit is None)
        if row_is_money:
            is_money = True
            if per_10k is not None:
                adjusted *= max(0.000001, 1.0 + per_10k / 10_000.0)
            elif index > 0 and daily is not None and 1.0 + daily / 100.0 > 0:
                adjusted *= 1.0 + daily / 100.0
            source = "本地基金日度净值_万份收益复利指数"
        else:
            if index > 0:
                if daily is None and previous_unit not in (None, 0) and unit is not None:
                    daily = (unit / previous_unit - 1.0) * 100.0
                if daily is not None and math.isfinite(daily) and 1.0 + daily / 100.0 > 0:
                    adjusted *= 1.0 + daily / 100.0
            previous_unit = unit
            source = "本地基金日度净值_日增长率复利指数"
        output.append(
            {
                "date": trade_date,
                "unit": unit,
                "accum": positive(row["累计净值"]),
                "daily": daily,
                "per10k": per_10k,
                "seven_day": to_float(row["七日年化收益率_百分比"]),
                "event": clean(row["净值图分红送配"]),
                "adjusted": adjusted,
                "source": source,
            }
        )
    has_unit_nav = any(row.get("unit") is not None for row in output)
    has_money_income = any(row.get("per10k") is not None for row in output)
    inferred_is_money = bool(output) and not has_unit_nav and (has_money_income or is_money)
    return output, inferred_is_money, "货币基金收益" if inferred_is_money else "单位净值"


def upsert_history_rows(
    conn: sqlite3.Connection,
    target: FundTarget,
    rows: list[dict[str, Any]],
    *,
    parsed_name: str,
    is_money: bool,
    nav_type: str,
    captured_at: str,
    run_id: str,
    source_url: str,
) -> int:
    if not rows:
        return 0
    sql = '''
        INSERT INTO "基金日度净值" (
          "基金代码", "交易日期", "基金名称", "基金类型", "基金公司", "净值口径",
          "单位净值", "累计净值", "日收益率_百分比", "每万份收益", "七日年化收益率_百分比",
          "净值图分红送配", "是否货币基金", "数据来源", "原始净值快照ID", "采集时间",
          "复权净值", "复权来源"
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT("基金代码", "交易日期") DO UPDATE SET
          "基金名称"=COALESCE(excluded."基金名称", "基金日度净值"."基金名称"),
          "基金类型"=COALESCE(excluded."基金类型", "基金日度净值"."基金类型"),
          "基金公司"=COALESCE(excluded."基金公司", "基金日度净值"."基金公司"),
          "净值口径"=excluded."净值口径",
          "单位净值"=COALESCE(excluded."单位净值", "基金日度净值"."单位净值"),
          "累计净值"=COALESCE(excluded."累计净值", "基金日度净值"."累计净值"),
          "日收益率_百分比"=COALESCE(excluded."日收益率_百分比", "基金日度净值"."日收益率_百分比"),
          "每万份收益"=COALESCE(excluded."每万份收益", "基金日度净值"."每万份收益"),
          "七日年化收益率_百分比"=COALESCE(excluded."七日年化收益率_百分比", "基金日度净值"."七日年化收益率_百分比"),
          "净值图分红送配"=COALESCE(NULLIF(excluded."净值图分红送配", ''), "基金日度净值"."净值图分红送配"),
          "是否货币基金"=excluded."是否货币基金",
          "数据来源"=excluded."数据来源",
          "原始净值快照ID"=excluded."原始净值快照ID",
          "采集时间"=excluded."采集时间",
          "复权净值"=excluded."复权净值",
          "复权来源"=excluded."复权来源"
    '''
    name = parsed_name or target.fund_name
    values = [
        (
            target.fund_code,
            row["date"],
            name,
            target.fund_type,
            target.fund_company,
            nav_type,
            row.get("unit"),
            row.get("accum"),
            row.get("daily"),
            row.get("per10k"),
            row.get("seven_day"),
            row.get("event"),
            1 if is_money else 0,
            row.get("source") or "公募基金全历史同步",
            f"public_fund_full_history::{run_id}::{target.fund_code}",
            captured_at,
            row.get("adjusted"),
            row.get("source") or "公募基金全历史同步",
        )
        for row in rows
        if row.get("date") and row.get("adjusted") is not None
    ]
    conn.executemany(sql, values)
    update_overview(conn, target, parsed_name=name, is_money=is_money, nav_type=nav_type, captured_at=captured_at, run_id=run_id, source_url=source_url)
    return len(values)


def update_overview(
    conn: sqlite3.Connection,
    target: FundTarget,
    *,
    parsed_name: str,
    is_money: bool,
    nav_type: str,
    captured_at: str,
    run_id: str,
    source_url: str,
) -> None:
    stats = conn.execute(
        '''
        SELECT MIN("交易日期") AS first_date, MAX("交易日期") AS latest_date, COUNT(*) AS row_count,
               SUM(CASE WHEN COALESCE("净值图分红送配", '') <> '' THEN 1 ELSE 0 END) AS dividend_count,
               SUM(CASE WHEN "复权净值" IS NOT NULL THEN 1 ELSE 0 END) AS adjusted_count
        FROM "基金日度净值"
        WHERE "基金代码" = ?
        ''',
        [target.fund_code],
    ).fetchone()
    if not stats or not stats["row_count"]:
        return
    latest = conn.execute(
        '''
        SELECT *
        FROM "基金日度净值"
        WHERE "基金代码" = ? AND "交易日期" = ?
        LIMIT 1
        ''',
        [target.fund_code, stats["latest_date"]],
    ).fetchone()
    if latest is None:
        return
    name = parsed_name or target.fund_name
    source = latest["复权来源"] or latest["数据来源"] or "公募基金全历史同步"
    snapshot_id = f"public_fund_full_history::{run_id}::{target.fund_code}"
    conn.execute(
        '''
        INSERT INTO "基金净值概况" (
          "基金代码", "基金名称", "基金类型", "基金公司", "净值口径", "是否货币基金",
          "历史起始日期", "历史结束日期", "历史记录数", "分红事件数", "最新单位净值",
          "最新累计净值", "最新日收益率_百分比", "最新每万份收益", "最新七日年化收益率_百分比",
          "数据来源", "原始净值快照ID", "原始分红快照ID", "最近采集时间"
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT("基金代码") DO UPDATE SET
          "基金名称"=COALESCE(excluded."基金名称", "基金净值概况"."基金名称"),
          "基金类型"=COALESCE(excluded."基金类型", "基金净值概况"."基金类型"),
          "基金公司"=COALESCE(excluded."基金公司", "基金净值概况"."基金公司"),
          "净值口径"=excluded."净值口径",
          "是否货币基金"=excluded."是否货币基金",
          "历史起始日期"=excluded."历史起始日期",
          "历史结束日期"=excluded."历史结束日期",
          "历史记录数"=excluded."历史记录数",
          "分红事件数"=excluded."分红事件数",
          "最新单位净值"=excluded."最新单位净值",
          "最新累计净值"=excluded."最新累计净值",
          "最新日收益率_百分比"=excluded."最新日收益率_百分比",
          "最新每万份收益"=excluded."最新每万份收益",
          "最新七日年化收益率_百分比"=excluded."最新七日年化收益率_百分比",
          "数据来源"=excluded."数据来源",
          "原始净值快照ID"=excluded."原始净值快照ID",
          "最近采集时间"=excluded."最近采集时间"
        ''',
        (
            target.fund_code,
            name,
            target.fund_type,
            target.fund_company,
            "复权总回报" if stats["adjusted_count"] else nav_type,
            1 if is_money else 0,
            stats["first_date"],
            stats["latest_date"],
            int(stats["row_count"] or 0),
            int(stats["dividend_count"] or 0),
            latest["单位净值"],
            latest["累计净值"],
            latest["日收益率_百分比"],
            latest["每万份收益"],
            latest["七日年化收益率_百分比"],
            source,
            snapshot_id,
            None,
            captured_at,
        ),
    )
    conn.execute(
        '''
        INSERT INTO "基金信息" ("基金代码", "基金名称", "基金公司", "基金类型", "最新净值", "最新净值日期", "数据来源")
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT("基金代码") DO UPDATE SET
          "基金名称"=COALESCE(excluded."基金名称", "基金信息"."基金名称"),
          "基金公司"=COALESCE(excluded."基金公司", "基金信息"."基金公司"),
          "基金类型"=COALESCE(excluded."基金类型", "基金信息"."基金类型"),
          "最新净值"=COALESCE(excluded."最新净值", "基金信息"."最新净值"),
          "最新净值日期"=COALESCE(excluded."最新净值日期", "基金信息"."最新净值日期"),
          "数据来源"=COALESCE(excluded."数据来源", "基金信息"."数据来源"),
          "最近更新时间"=CURRENT_TIMESTAMP
        ''',
        (
            target.fund_code,
            name,
            target.fund_company,
            target.fund_type,
            latest["单位净值"],
            stats["latest_date"],
            source_url or source,
        ),
    )


def after_stats(conn: sqlite3.Connection, fund_code: str) -> tuple[int, int, str, str]:
    row = conn.execute(
        '''
        SELECT COUNT(*) AS total_rows,
               SUM(CASE WHEN "复权净值" IS NOT NULL THEN 1 ELSE 0 END) AS adjusted_rows,
               MIN("交易日期") AS first_date,
               MAX("交易日期") AS latest_date
        FROM "基金日度净值"
        WHERE "基金代码" = ?
        ''',
        [fund_code],
    ).fetchone()
    if not row:
        return 0, 0, "", ""
    return int(row["total_rows"] or 0), int(row["adjusted_rows"] or 0), clean(row["first_date"]), clean(row["latest_date"])


def delete_history_rows(conn: sqlite3.Connection, fund_code: str) -> None:
    conn.execute(
        '''
        DELETE FROM "基金日度净值"
        WHERE "基金代码" = ?
        ''',
        [fund_code],
    )


def process_target(target: FundTarget, *, args: argparse.Namespace, run_id: str, raw_dir: Path) -> SyncResult:
    started = time.perf_counter()
    result = SyncResult(fund_code=target.fund_code, fund_name=target.fund_name, status="running", method="")
    try:
        source_url = ""
        raw_path = ""
        parsed_name = target.fund_name
        is_money = target.is_money
        nav_type = "货币基金收益" if target.is_money else "单位净值"
        if not args.force_fetch and target.local_rows > 0 and target.local_daily_rows > 0:
            conn = sqlite3.connect(args.db_path)
            conn.row_factory = sqlite3.Row
            try:
                rows, is_money, nav_type = load_local_rows_for_adjustment(conn, target)
            finally:
                conn.close()
            method = "local_compound"
        else:
            if args.no_network:
                raise RuntimeError("no local daily rows available and --no-network is set")
            rows, parsed_name, is_money, nav_type, source_url, raw_path, method, _elapsed = fetch_source_rows(target, args=args, run_id=run_id, raw_dir=raw_dir)
        with DB_WRITE_LOCK:
            conn = sqlite3.connect(args.db_path)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=30000")
            try:
                conn.execute("BEGIN")
                if method.startswith("source_") and rows:
                    delete_history_rows(conn, target.fund_code)
                written = upsert_history_rows(
                    conn,
                    target,
                    rows,
                    parsed_name=parsed_name,
                    is_money=is_money,
                    nav_type=nav_type,
                    captured_at=now_cn().isoformat(timespec="seconds"),
                    run_id=run_id,
                    source_url=source_url,
                )
                conn.commit()
                total, adjusted, first_date, latest_date = after_stats(conn, target.fund_code)
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
        result.status = "success" if written else "no_data"
        result.method = method
        result.rows_parsed = len(rows)
        result.rows_written = written
        result.first_date = first_date
        result.latest_date = latest_date
        result.adjusted_rows_after = adjusted
        result.total_rows_after = total
        result.source_url = source_url
        result.raw_path = raw_path
    except Exception as error:
        result.status = "failed"
        result.error = str(error)[:1000]
    result.elapsed_sec = round(time.perf_counter() - started, 3)
    return result


def audit_codes(conn: sqlite3.Connection, codes: list[str], *, max_return_diff_pp: float) -> dict[str, Any]:
    rows_out: list[dict[str, Any]] = []
    status_counts: dict[str, int] = {}
    for code in codes:
        rows = conn.execute(
            '''
            SELECT "基金代码", "交易日期", "单位净值", "日收益率_百分比", "每万份收益",
                   "是否货币基金", "复权净值", "复权来源"
            FROM "基金日度净值"
            WHERE "基金代码" = ?
            ORDER BY "交易日期"
            ''',
            [code],
        ).fetchall()
        total = len(rows)
        adjusted_rows = sum(1 for row in rows if row["复权净值"] is not None)
        positive_adjusted = sum(1 for row in rows if positive(row["复权净值"]) is not None)
        first_date = clean(rows[0]["交易日期"]) if rows else ""
        latest_date = clean(rows[-1]["交易日期"]) if rows else ""
        max_diff = 0.0
        checked = 0
        previous_adjusted: float | None = None
        for row in rows:
            adjusted = positive(row["复权净值"])
            if adjusted is None:
                previous_adjusted = None
                continue
            if previous_adjusted not in (None, 0):
                observed = (adjusted / previous_adjusted - 1.0) * 100.0
                expected = to_float(row["日收益率_百分比"])
                if expected is None:
                    per_10k = to_float(row["每万份收益"])
                    expected = per_10k / 100.0 if per_10k is not None else None
                if expected is not None and math.isfinite(expected):
                    max_diff = max(max_diff, abs(observed - expected))
                    checked += 1
            previous_adjusted = adjusted
        if total == 0:
            status = "error_no_rows"
        elif adjusted_rows < total:
            status = "error_adjusted_incomplete"
        elif positive_adjusted < adjusted_rows:
            status = "error_nonpositive_adjusted"
        elif checked and max_diff > max_return_diff_pp:
            status = "warn_return_diff"
        else:
            status = "pass"
        status_counts[status] = status_counts.get(status, 0) + 1
        rows_out.append(
            {
                "基金代码": code,
                "记录数": total,
                "复权记录数": adjusted_rows,
                "起始日期": first_date,
                "最新日期": latest_date,
                "收益一致性检查点数": checked,
                "最大收益差异_百分点": round(max_diff, 8),
                "稽核状态": status,
                "复权来源样例": clean(rows[-1]["复权来源"]) if rows else "",
            }
        )
    return {
        "status": "pass" if set(status_counts) <= {"pass"} else ("warn" if "warn_return_diff" in status_counts and not any(key.startswith("error") for key in status_counts) else "error"),
        "statusCounts": status_counts,
        "rows": rows_out,
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    args = parse_args()
    run_at = now_cn()
    run_id = run_at.strftime("%Y%m%dT%H%M%S%z")
    run_dir = args.output_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = args.raw_root / run_id
    summary_path = run_dir / "summary.json"
    lock_path = args.lock_file or (args.output_root / "sync_public_fund_full_history.lock")
    lock = CollectorLock(lock_path, stale_hours=float(args.lock_stale_hours), enabled=not args.no_lock)

    conn = sqlite3.connect(args.db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA journal_mode=WAL")
    ensure_columns(conn)
    targets = discover_targets(conn, args)
    conn.close()

    summary: dict[str, Any] = {
        "runId": run_id,
        "startedAt": run_at.isoformat(timespec="seconds"),
        "status": "running",
        "dbPath": str(args.db_path.resolve()),
        "targetSource": args.target_source,
        "targets": len(targets),
        "workers": max(1, args.workers),
        "forceFetch": bool(args.force_fetch),
        "noNetwork": bool(args.no_network),
        "minLatestDate": clean(args.min_latest_date),
        "completed": 0,
        "success": 0,
        "noData": 0,
        "failed": 0,
        "methodCounts": {},
        "totalRowsWritten": 0,
        "failures": [],
    }
    write_json(summary_path, summary)
    if not targets:
        summary["status"] = "completed"
        summary["finishedAt"] = now_cn().isoformat(timespec="seconds")
        write_json(summary_path, summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
        return 0

    started = time.perf_counter()
    last_status_at = started
    results: list[SyncResult] = []
    exit_code = 0
    lock.acquire(run_id)
    try:
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
            future_map = {
                executor.submit(process_target, target, args=args, run_id=run_id, raw_dir=raw_dir): target
                for target in targets
            }
            for completed, future in enumerate(as_completed(future_map), start=1):
                target = future_map[future]
                result = future.result()
                results.append(result)
                summary["completed"] = completed
                summary["success"] += int(result.status == "success")
                summary["noData"] += int(result.status == "no_data")
                summary["failed"] += int(result.status == "failed")
                summary["totalRowsWritten"] += int(result.rows_written or 0)
                summary["methodCounts"][result.method or result.status] = int(summary["methodCounts"].get(result.method or result.status, 0)) + 1
                if result.status == "failed":
                    summary["failures"].append({"基金代码": target.fund_code, "基金名称": target.fund_name, "错误": result.error})
                now = time.perf_counter()
                if (
                    completed % max(1, args.progress_every) == 0
                    or completed == len(targets)
                    or result.status == "failed"
                    or now - last_status_at >= max(1, args.status_interval_sec)
                ):
                    elapsed = max(0.001, now - started)
                    rate = completed / elapsed * 60.0
                    eta = (len(targets) - completed) / rate if rate > 0 else None
                    print(
                        f"[sync {completed}/{len(targets)}] success={summary['success']} no_data={summary['noData']} "
                        f"failed={summary['failed']} rows={summary['totalRowsWritten']} rate={rate:.2f}/min "
                        f"eta_min={eta:.1f} last={target.fund_code} status={result.status} method={result.method}",
                        flush=True,
                    )
                    last_status_at = now
                if completed % max(1, args.commit_every) == 0:
                    summary["elapsedSeconds"] = round(time.perf_counter() - started, 3)
                    write_json(summary_path, summary)
    except KeyboardInterrupt:
        summary["status"] = "interrupted"
        exit_code = 130
    finally:
        lock.release()

    result_rows = [result.__dict__ for result in sorted(results, key=lambda item: item.fund_code)]
    write_json(run_dir / "sync_results.json", {"runId": run_id, "rows": result_rows})
    processed_codes = [result.fund_code for result in results if result.status == "success"]
    audit_codes_list = processed_codes[: max(0, args.audit_sample_size)]
    conn = sqlite3.connect(args.db_path)
    conn.row_factory = sqlite3.Row
    try:
        audit = audit_codes(conn, audit_codes_list, max_return_diff_pp=max(0.0, args.audit_max_return_diff_pp))
    finally:
        conn.close()
    write_json(run_dir / "sample_audit.json", audit)

    elapsed = max(0.001, time.perf_counter() - started)
    summary["status"] = summary.get("status") if summary.get("status") == "interrupted" else "completed"
    summary["finishedAt"] = now_cn().isoformat(timespec="seconds")
    summary["elapsedSeconds"] = round(elapsed, 3)
    summary["ratePerMinute"] = round(len(results) / elapsed * 60.0, 3)
    summary["auditStatus"] = audit["status"]
    summary["auditStatusCounts"] = audit["statusCounts"]
    summary["summaryPath"] = str(summary_path.resolve())
    summary["resultPath"] = str((run_dir / "sync_results.json").resolve())
    summary["auditPath"] = str((run_dir / "sample_audit.json").resolve())
    write_json(summary_path, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    if exit_code:
        return exit_code
    if summary["failed"]:
        return 2
    return 0 if audit["status"] in {"pass", "warn"} else 3


if __name__ == "__main__":
    raise SystemExit(main())
