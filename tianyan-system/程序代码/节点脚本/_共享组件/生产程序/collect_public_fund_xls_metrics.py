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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any
from urllib.parse import urlencode

import requests
from requests.adapters import HTTPAdapter

from backfill_fund_history_analysis_sqlite import (
    epoch_millis_to_ymd,
    parse_f10_payload,
    parse_js_array,
    parse_table_fragment,
    row_as_map,
    to_float,
    value_type_from_headers,
)
from build_public_fund_performance_snapshot import calc_return, calc_risk, interval_definitions


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "public_fund_xls_metrics"
TABLE_NAME = "公募基金区间绩效补齐"
USER_AGENT = "Mozilla/5.0 advisor-monitor/0.1"
CN_TZ = timezone(timedelta(hours=8))
XH5_URL = "https://finance.sina.com.cn/fund/api/xh5Fund/nav/{fund_code}.js"
PINGZHONG_URL = "https://fund.eastmoney.com/pingzhongdata/{fund_code}.js"
F10_URL = "https://fundf10.eastmoney.com/F10DataApi.aspx"
INTERVALS = ["上半年", "今年以来", "近1周", "近1月", "近3月", "近6月", "近1年"]
HTTP_LOCAL = threading.local()


@dataclass(frozen=True)
class FundTarget:
    fund_code: str
    nav_code: str
    fund_name: str
    fund_type: str
    fund_company: str
    is_money: bool
    bucket: str
    mapping_note: str


class NoDataBeforeCutoff(ValueError):
    pass


class SourceNotFound(NoDataBeforeCutoff):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect the minimum public-fund NAV window required by the mixed ranking XLS.")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--end-date", default="2026-06-30")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--timeout-sec", type=int, default=25)
    parser.add_argument("--retries", type=int, default=3, help="Total attempts per request.")
    parser.add_argument("--commit-every", type=int, default=100)
    parser.add_argument("--raw-batch-size", type=int, default=500)
    parser.add_argument("--task-batch-size", type=int, default=100, help="Commit only after a complete network batch has finished.")
    parser.add_argument("--progress-every", type=int, default=50)
    parser.add_argument("--status-interval-sec", type=int, default=300, help="Periodic progress output interval.")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--fund-code", action="append", default=[])
    parser.add_argument("--fund-code-file", type=Path, help="UTF-8 text file with one fund code per line.")
    parser.add_argument("--preflight", action="store_true", help="Run a stratified sample instead of the full missing-NAV target set.")
    parser.add_argument("--preflight-per-bucket", type=int, default=3)
    parser.add_argument("--refresh", action="store_true", help="Refresh rows already collected successfully for this end date.")
    parser.add_argument("--retry-failed-only", action="store_true", help="Only retry rows that failed or had transient no-data errors for this end date.")
    parser.add_argument("--refresh-success-older-than-days", type=int, help="In non-refresh mode, refresh successful rows older than this many days.")
    parser.add_argument("--skip-f10", action="store_true", help="Skip the slow paginated F10 fallback in the primary pass.")
    parser.add_argument("--no-raw", action="store_true", help="Skip compressed raw response archives.")
    parser.add_argument("--no-lock", action="store_true", help="Allow concurrent collectors. Use only for controlled debugging.")
    parser.add_argument("--lock-file", type=Path, help="Collector lock file. Defaults to <output-root>/collect_public_fund_xls_metrics.lock.")
    parser.add_argument("--lock-stale-hours", type=float, default=12.0, help="Treat an existing collector lock older than this as stale.")
    return parser.parse_args()


def now_cn() -> datetime:
    return datetime.now(CN_TZ)


def clean(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text in {"None", "null", "nan", "--", "-"} else text


def parse_ymd(value: Any) -> str | None:
    text = clean(value)[:10].replace("/", "-")
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


def positive_float(value: Any) -> float | None:
    number = to_float(value)
    return number if number is not None and number > 0 and math.isfinite(number) else None


def parse_iso_datetime(value: Any) -> datetime | None:
    text = clean(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=CN_TZ)
    except ValueError:
        return None


def is_transient_error(value: Any) -> bool:
    error = clean(value).lower()
    retry_markers = (
        "timed out",
        "timeout",
        "http 429",
        "http 5",
        "ssl",
        "connection",
        "failed to resolve",
        "getaddrinfo",
        "name resolution",
        "remote end closed",
    )
    return any(marker in error for marker in retry_markers)


def target_bucket(row: sqlite3.Row) -> str:
    if int(row["是否货币基金"] or 0):
        return "货币"
    if int(row["是否REITs"] or 0):
        return "REITs"
    if int(row["是否FOF"] or 0):
        return "FOF"
    if int(row["是否QDII"] or 0):
        return "QDII"
    if int(row["是否ETF"] or 0):
        return "ETF"
    if int(row["是否LOF"] or 0):
        return "LOF"
    if int(row["是否债券基金"] or 0):
        return "债券"
    if int(row["是否权益基金"] or 0):
        return "权益"
    if int(row["是否混合基金"] or 0):
        return "混合"
    return clean(row["标准资产大类"]) or "其他"


def ensure_table(conn: sqlite3.Connection) -> None:
    metric_defs: list[str] = []
    for label in INTERVALS:
        metric_defs.extend(
            [
                f'"{label}收益率_百分比" REAL',
                f'"{label}最大回撤_百分比" REAL',
                f'"{label}年化波动率_百分比" REAL',
                f'"{label}风险净值点数" INTEGER',
                f'"{label}区间" TEXT',
            ]
        )
    conn.execute(
        f'''
        CREATE TABLE IF NOT EXISTS "{TABLE_NAME}" (
          "基金代码" TEXT NOT NULL,
          "绩效截止日期" TEXT NOT NULL,
          "基金名称" TEXT,
          "基金类型" TEXT,
          "基金公司" TEXT,
          "基金分类桶" TEXT,
          "是否货币基金" INTEGER NOT NULL DEFAULT 0,
          "净值映射基金代码" TEXT,
          "净值映射说明" TEXT,
          "采集状态" TEXT NOT NULL,
          "采集来源" TEXT,
          "来源URL" TEXT,
          "失败原因" TEXT,
          "请求尝试次数" INTEGER,
          "请求耗时秒" REAL,
          "原始响应字节数" INTEGER,
          "原始响应SHA256" TEXT,
          "原始证据包路径" TEXT,
          "源端净值点数" INTEGER,
          "复权净值点数" INTEGER,
          "复权口径" TEXT,
          "窗口净值点数" INTEGER,
          "窗口起始日期" TEXT,
          "窗口截止日期" TEXT,
          "最新单位净值" REAL,
          "最新累计净值" REAL,
          "披露频率" TEXT,
          "披露中位间隔天数" REAL,
          "披露最大间隔天数" INTEGER,
          "区间状态JSON" TEXT,
          {", ".join(metric_defs)},
          "run_id" TEXT NOT NULL,
          "更新时间" TEXT NOT NULL,
          PRIMARY KEY ("基金代码", "绩效截止日期")
        )
        '''
    )
    existing_columns = {row[1] for row in conn.execute(f'PRAGMA table_info("{TABLE_NAME}")')}
    added_columns = {
        "净值映射基金代码": "TEXT",
        "净值映射说明": "TEXT",
        "复权净值点数": "INTEGER",
        "复权口径": "TEXT",
    }
    for column, column_type in added_columns.items():
        if column not in existing_columns:
            conn.execute(f'ALTER TABLE "{TABLE_NAME}" ADD COLUMN "{column}" {column_type}')


def discover_targets(
    conn: sqlite3.Connection,
    end_date: str,
    refresh: bool,
    *,
    retry_failed_only: bool = False,
    refresh_success_older_than_days: int | None = None,
) -> list[FundTarget]:
    rows = conn.execute(
        f'''
        SELECT
          d."基金代码",
          COALESCE(NULLIF(TRIM(d."标准基金名称"), ''), d."基金代码") AS "基金名称",
          COALESCE(NULLIF(TRIM(d."天天基金细分类"), ''), NULLIF(TRIM(d."标准资产细类"), ''), '') AS "基金类型",
          COALESCE(NULLIF(TRIM(d."基金公司"), ''), '') AS "基金公司",
          d."是否货币基金", d."是否债券基金", d."是否权益基金", d."是否混合基金",
          d."是否ETF", d."是否QDII", d."是否FOF", d."是否LOF", d."是否REITs",
          d."标准资产大类",
          COALESCE(s."本地净值记录数", 0) AS "本地净值记录数",
          c."采集状态" AS "已有采集状态",
          c."复权口径" AS "已有复权口径",
          c."失败原因" AS "已有失败原因",
          c."更新时间" AS "已有更新时间"
        FROM "基金标准分类字典" d
        LEFT JOIN "公募基金产品绩效快照" s ON s."基金代码" = d."基金代码"
        LEFT JOIN "{TABLE_NAME}" c ON c."基金代码" = d."基金代码" AND c."绩效截止日期" = ?
        WHERE COALESCE(s."本地净值记录数", 0) = 0
        ORDER BY d."基金代码"
        ''',
        (end_date,),
    ).fetchall()
    name_rows = conn.execute(
        'SELECT "基金代码", "标准基金名称", "是否当前库使用" FROM "基金标准分类字典" WHERE "基金代码" IS NOT NULL'
    ).fetchall()
    exact_names: dict[str, list[tuple[str, int]]] = {}
    for item in name_rows:
        name = clean(item["标准基金名称"])
        if name:
            exact_names.setdefault(name, []).append((clean(item["基金代码"]), int(item["是否当前库使用"] or 0)))
    targets: list[FundTarget] = []
    stale_success_cutoff = (
        now_cn() - timedelta(days=max(0, refresh_success_older_than_days))
        if refresh_success_older_than_days is not None
        else None
    )
    for row in rows:
        existing_status = clean(row["已有采集状态"])
        existing_error = clean(row["已有失败原因"])
        existing_is_transient_no_data = existing_status == "截止日无数据" and is_transient_error(existing_error)
        existing_is_retryable = existing_status == "失败" or existing_is_transient_no_data
        if retry_failed_only and not existing_is_retryable:
            continue
        if not refresh:
            if existing_status == "截止日无数据":
                if not existing_is_transient_no_data:
                    continue
            if existing_status == "成功" and clean(row["已有复权口径"]):
                if stale_success_cutoff is None:
                    continue
                updated_at = parse_iso_datetime(row["已有更新时间"])
                if updated_at is not None and updated_at >= stale_success_cutoff:
                    continue
        fund_code = clean(row["基金代码"])
        fund_name = clean(row["基金名称"])
        nav_code = fund_code
        mapping_note = ""
        if re.search(r"[（(]后端[）)]", fund_name):
            base_name = re.sub(r"[（(]后端[）)]", "", fund_name).strip()
            candidates = [item for item in exact_names.get(base_name, []) if item[0] != fund_code]
            if candidates:
                candidates.sort(key=lambda item: (-item[1], item[0]))
                nav_code = candidates[0][0]
                mapping_note = f"历史后端收费代码；净值严格映射到同名普通份额{nav_code}"
        targets.append(
            FundTarget(
                fund_code=fund_code,
                nav_code=nav_code,
                fund_name=fund_name,
                fund_type=clean(row["基金类型"]),
                fund_company=clean(row["基金公司"]),
                is_money=bool(int(row["是否货币基金"] or 0)),
                bucket=target_bucket(row),
                mapping_note=mapping_note,
            )
        )
    return targets


def discover_targets_by_codes(conn: sqlite3.Connection, codes: set[str]) -> list[FundTarget]:
    cleaned_codes = sorted({clean(code) for code in codes if clean(code)})
    if not cleaned_codes:
        return []
    placeholders = ",".join("?" for _ in cleaned_codes)
    rows = conn.execute(
        f'''
        SELECT
          d."基金代码",
          COALESCE(NULLIF(TRIM(d."标准基金名称"), ''), d."基金代码") AS "基金名称",
          COALESCE(NULLIF(TRIM(d."天天基金细分类"), ''), NULLIF(TRIM(d."标准资产细类"), ''), '') AS "基金类型",
          COALESCE(NULLIF(TRIM(d."基金公司"), ''), '') AS "基金公司",
          d."是否货币基金", d."是否债券基金", d."是否权益基金", d."是否混合基金",
          d."是否ETF", d."是否QDII", d."是否FOF", d."是否LOF", d."是否REITs",
          d."标准资产大类"
        FROM "基金标准分类字典" d
        WHERE d."基金代码" IN ({placeholders})
        ORDER BY d."基金代码"
        ''',
        cleaned_codes,
    ).fetchall()
    name_rows = conn.execute(
        'SELECT "基金代码", "标准基金名称", "是否当前库使用" FROM "基金标准分类字典" WHERE "基金代码" IS NOT NULL'
    ).fetchall()
    exact_names: dict[str, list[tuple[str, int]]] = {}
    for item in name_rows:
        name = clean(item["标准基金名称"])
        if name:
            exact_names.setdefault(name, []).append((clean(item["基金代码"]), int(item["是否当前库使用"] or 0)))
    targets: list[FundTarget] = []
    found_codes: set[str] = set()
    for row in rows:
        fund_code = clean(row["基金代码"])
        found_codes.add(fund_code)
        fund_name = clean(row["基金名称"])
        nav_code = fund_code
        mapping_note = ""
        if re.search(r"[（(]后端[）)]", fund_name):
            base_name = re.sub(r"[（(]后端[）)]", "", fund_name).strip()
            candidates = [item for item in exact_names.get(base_name, []) if item[0] != fund_code]
            if candidates:
                candidates.sort(key=lambda item: (-item[1], item[0]))
                nav_code = candidates[0][0]
                mapping_note = f"历史后端收费代码；净值严格映射到同名普通份额{nav_code}"
        targets.append(
            FundTarget(
                fund_code=fund_code,
                nav_code=nav_code,
                fund_name=fund_name,
                fund_type=clean(row["基金类型"]),
                fund_company=clean(row["基金公司"]),
                is_money=bool(int(row["是否货币基金"] or 0)),
                bucket=target_bucket(row),
                mapping_note=mapping_note,
            )
        )
    for missing_code in sorted(set(cleaned_codes) - found_codes):
        targets.append(FundTarget(missing_code, missing_code, missing_code, "", "", False, "指定样本", "基金标准分类字典未找到"))
    return targets


def stratified_targets(targets: list[FundTarget], per_bucket: int) -> list[FundTarget]:
    grouped: dict[str, list[FundTarget]] = {}
    for target in targets:
        grouped.setdefault(target.bucket, []).append(target)
    selected: list[FundTarget] = []
    for bucket in sorted(grouped):
        items = grouped[bucket]
        if len(items) <= per_bucket:
            selected.extend(items)
            continue
        if per_bucket == 1:
            indexes = [len(items) // 2]
        else:
            indexes = sorted({round(index * (len(items) - 1) / (per_bucket - 1)) for index in range(per_bucket)})
        selected.extend(items[index] for index in indexes)
    return selected


def http_session() -> requests.Session:
    session = getattr(HTTP_LOCAL, "session", None)
    if session is None:
        session = requests.Session()
        adapter = HTTPAdapter(pool_connections=4, pool_maxsize=4, max_retries=0, pool_block=True)
        session.mount("https://", adapter)
        session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept-Encoding": "gzip, deflate",
                "Accept": "*/*",
            }
        )
        HTTP_LOCAL.session = session
    return session


def fetch_text(url: str, *, timeout: int, attempts: int, referer: str) -> tuple[str, int, int, float]:
    last_error: Exception | None = None
    elapsed = 0.0
    for attempt in range(1, max(1, attempts) + 1):
        started = time.perf_counter()
        try:
            response = http_session().get(
                url,
                headers={
                    "Referer": referer,
                },
                timeout=(min(5, max(1, timeout)), max(1, timeout)),
            )
            status = int(response.status_code)
            if status in {404, 410}:
                raise SourceNotFound(f"source HTTP {status}")
            if status != 200:
                raise RuntimeError(f"HTTP {status}")
            raw = response.content
            if len(raw) < 20:
                raise RuntimeError(f"response too short: {len(raw)} bytes")
            elapsed += time.perf_counter() - started
            return raw.decode("utf-8", errors="replace"), status, attempt, elapsed
        except SourceNotFound:
            raise
        except Exception as error:  # pragma: no cover - network dependent
            elapsed += time.perf_counter() - started
            last_error = error
            if attempt < attempts:
                time.sleep(min(4.0, 0.6 * (2 ** (attempt - 1))) + random.uniform(0.0, 0.5))
    raise RuntimeError(f"request failed after {attempts} attempts: {last_error}")


def trim_series(series: list[tuple[str, float]], lower_bound: date, end_date: date) -> list[tuple[str, float]]:
    dedup = sorted({trade_date: nav for trade_date, nav in series if nav > 0 and trade_date <= end_date.isoformat()}.items())
    if not dedup:
        return []
    anchor: tuple[str, float] | None = None
    selected: list[tuple[str, float]] = []
    for item in dedup:
        if item[0] < lower_bound.isoformat():
            anchor = item
        else:
            selected.append(item)
    if anchor:
        selected.insert(0, anchor)
    return selected


def has_window_observation(series: list[tuple[str, float]], lower_bound: date) -> bool:
    return any(trade_date >= lower_bound.isoformat() for trade_date, _nav in series)


def parse_xh5(text: str, lower_bound: date, end_date: date) -> tuple[list[tuple[str, float]], dict[str, Any]]:
    match = re.search(r"xh5Fund\((\{.*?\})\)", text, flags=re.S)
    if not match:
        raise ValueError("新浪 xh5 响应无法解析")
    payload = json.loads(match.group(1))
    series: list[tuple[str, float]] = []
    latest_unit: float | None = None
    latest_acc: float | None = None
    latest_date = ""
    source_count = 0
    adjusted_count = 0
    for item in str(payload.get("data") or "").split("#"):
        parts = item.split(",")
        if len(parts) < 4 or not re.fullmatch(r"\d{8}", parts[0]):
            continue
        trade_date = f"{parts[0][:4]}-{parts[0][4:6]}-{parts[0][6:8]}"
        unit_nav = positive_float(parts[1])
        accumulated_nav = positive_float(parts[2] if len(parts) > 2 else None)
        adjusted_nav = positive_float(parts[3] if len(parts) > 3 else None)
        if unit_nav is None and accumulated_nav is None and adjusted_nav is None:
            continue
        source_count += 1
        adjusted_count += int(adjusted_nav is not None)
        if trade_date <= end_date.isoformat():
            if adjusted_nav is not None:
                series.append((trade_date, adjusted_nav))
            if trade_date >= latest_date:
                latest_date = trade_date
                latest_unit = unit_nav
                latest_acc = accumulated_nav
    if source_count and adjusted_count == 0:
        raise ValueError("新浪 xh5 响应没有复权净值列")
    trimmed = trim_series(series, lower_bound, end_date)
    if not trimmed or not has_window_observation(trimmed, lower_bound):
        raise NoDataBeforeCutoff("新浪 xh5 目标窗口内无有效净值")
    return trimmed, {
        "sourcePointCount": source_count,
        "adjustedPointCount": adjusted_count,
        "adjustmentMethod": "新浪_xh5_直接复权净值",
        "latestUnitNav": latest_unit,
        "latestAccumulatedNav": latest_acc,
    }


def build_money_unit_delta_series(
    records: list[tuple[str, float | None, float | None, float | None]],
    lower_bound: date,
    end_date: date,
) -> tuple[list[tuple[str, float]], dict[str, Any]]:
    unit_values = [float(unit_nav) for _date, unit_nav, _acc, _ret in records if unit_nav is not None]
    denominator = 100.0 if unit_values and median(unit_values) > 10 else 1.0
    adjusted_index = 100.0
    full_series: list[tuple[str, float]] = []
    previous: tuple[str, float, float | None] | None = None
    reset_count = 0
    for trade_date, unit_nav, accumulated_nav, _daily_return in records:
        if unit_nav is None:
            continue
        if previous is None:
            full_series.append((trade_date, adjusted_index))
            previous = (trade_date, unit_nav, accumulated_nav)
            continue
        _previous_date, previous_unit, previous_acc = previous
        delta = unit_nav - previous_unit
        if delta >= 0:
            period_return = delta / denominator
        elif accumulated_nav is not None and previous_acc is not None and accumulated_nav - previous_acc > 0:
            period_return = (accumulated_nav - previous_acc) / denominator
            reset_count += 1
        else:
            period_return = 0.0
            reset_count += 1
        if math.isfinite(period_return) and 1.0 + period_return > 0:
            adjusted_index *= 1.0 + period_return
            full_series.append((trade_date, adjusted_index))
        previous = (trade_date, unit_nav, accumulated_nav)
    trimmed = trim_series(full_series, lower_bound, end_date)
    if not trimmed or not has_window_observation(trimmed, lower_bound):
        raise NoDataBeforeCutoff("天天 pingzhongdata 货币基金净值差额序列目标窗口内无有效净值")
    return trimmed, {
        "sourcePointCount": len(records),
        "adjustedPointCount": len(full_series),
        "adjustmentMethod": f"货币基金万份收益为空，改用单位净值日差额复利指数(reset={reset_count},base={denominator:g})",
    }


def parse_pingzhong(
    text: str,
    *,
    is_money: bool,
    lower_bound: date,
    end_date: date,
) -> tuple[list[tuple[str, float]], dict[str, Any]]:
    money_fallback_reason = ""
    if is_money:
        incomes = parse_js_array(text, "Data_millionCopiesIncome") or []
        raw: list[tuple[str, float]] = []
        for item in incomes:
            if not isinstance(item, list) or len(item) < 2:
                continue
            trade_date = epoch_millis_to_ymd(item[0])
            per_10k = to_float(item[1])
            if trade_date and per_10k is not None and trade_date <= end_date.isoformat():
                raw.append((trade_date, per_10k))
        dedup = sorted({trade_date: value for trade_date, value in raw}.items())
        anchor_index = 0
        for index, (trade_date, _value) in enumerate(dedup):
            if trade_date < lower_bound.isoformat():
                anchor_index = index
            else:
                break
        window = dedup[anchor_index:]
        if any(trade_date >= lower_bound.isoformat() for trade_date, _value in window):
            synthetic = 100.0
            series: list[tuple[str, float]] = []
            for trade_date, per_10k in window:
                synthetic *= 1.0 + per_10k / 10000.0
                series.append((trade_date, synthetic))
            if series:
                return series, {
                    "sourcePointCount": len(dedup),
                    "adjustedPointCount": len(series),
                    "adjustmentMethod": "万份收益复利指数",
                    "latestUnitNav": None,
                    "latestAccumulatedNav": None,
                }
        money_fallback_reason = "货币基金万份收益为空，改用净值增长率复利指数"

    nav_series = parse_js_array(text, "Data_netWorthTrend") or []
    accumulated_series = parse_js_array(text, "Data_ACWorthTrend") or []
    accumulated_map = {
        epoch_millis_to_ymd(item[0]): positive_float(item[1])
        for item in accumulated_series
        if isinstance(item, list) and len(item) >= 2
    }
    records: list[tuple[str, float | None, float | None, float | None]] = []
    latest_unit: float | None = None
    latest_acc: float | None = None
    latest_date = ""
    for item in nav_series:
        if not isinstance(item, dict):
            continue
        trade_date = epoch_millis_to_ymd(item.get("x"))
        unit_nav = positive_float(item.get("y"))
        accumulated_nav = accumulated_map.get(trade_date)
        daily_return = to_float(item.get("equityReturn"))
        if not trade_date or unit_nav is None or trade_date > end_date.isoformat():
            continue
        records.append((trade_date, unit_nav, accumulated_nav, daily_return))
        if trade_date >= latest_date:
            latest_date = trade_date
            latest_unit = unit_nav
            latest_acc = accumulated_nav
    records.sort(key=lambda item: item[0])
    if is_money and money_fallback_reason:
        series, money_meta = build_money_unit_delta_series(records, lower_bound, end_date)
        money_meta["latestUnitNav"] = latest_unit
        money_meta["latestAccumulatedNav"] = latest_acc
        return series, money_meta
    adjusted_index = 100.0
    series: list[tuple[str, float]] = []
    for index, (trade_date, _unit_nav, _accumulated_nav, daily_return) in enumerate(records):
        if index == 0:
            series.append((trade_date, adjusted_index))
            continue
        if daily_return is None or not math.isfinite(daily_return) or 1.0 + daily_return / 100.0 <= 0:
            continue
        adjusted_index *= 1.0 + daily_return / 100.0
        series.append((trade_date, adjusted_index))
    trimmed = trim_series(series, lower_bound, end_date)
    if not trimmed or not has_window_observation(trimmed, lower_bound):
        raise NoDataBeforeCutoff("天天 pingzhongdata 目标窗口内无有效净值")
    return trimmed, {
        "sourcePointCount": len(records),
        "adjustedPointCount": len(series),
        "adjustmentMethod": money_fallback_reason or "天天_日增长率复利指数",
        "latestUnitNav": latest_unit,
        "latestAccumulatedNav": latest_acc,
    }


def fetch_f10_page(
    code: str,
    page: int,
    *,
    start_date: str,
    end_date: str,
    timeout: int,
    attempts: int,
) -> tuple[dict[str, Any], list[str], list[list[str]], dict[str, Any]]:
    params = {"type": "lsjz", "code": code, "page": page, "per": 2000, "sdate": start_date, "edate": end_date}
    url = f"{F10_URL}?{urlencode(params)}"
    text, status, used_attempts, elapsed = fetch_text(
        url,
        timeout=timeout,
        attempts=attempts,
        referer=f"https://fundf10.eastmoney.com/jjjz_{code}.html",
    )
    payload = parse_f10_payload(text)
    headers, rows = parse_table_fragment(payload.get("content") or "")
    return payload, headers, rows, {"url": url, "text": text, "status": status, "attempts": used_attempts, "elapsed": elapsed}


def collect_f10(
    target: FundTarget,
    *,
    lower_bound: date,
    end_date: date,
    timeout: int,
    attempts: int,
) -> tuple[list[tuple[str, float]], dict[str, Any], list[dict[str, Any]]]:
    first, headers, rows, first_raw = fetch_f10_page(
        target.nav_code,
        1,
        start_date=lower_bound.isoformat(),
        end_date=end_date.isoformat(),
        timeout=timeout,
        attempts=attempts,
    )
    all_rows = list(rows)
    raw_docs = [first_raw]
    pages = int(first.get("pages") or 1)
    for page in range(2, pages + 1):
        _payload, page_headers, page_rows, raw_doc = fetch_f10_page(
            target.nav_code,
            page,
            start_date=lower_bound.isoformat(),
            end_date=end_date.isoformat(),
            timeout=timeout,
            attempts=attempts,
        )
        if page_headers:
            headers = page_headers
        all_rows.extend(page_rows)
        raw_docs.append(raw_doc)
    value_type = value_type_from_headers(headers)
    is_money = value_type == "money_market" or target.is_money
    if is_money:
        raw_income: list[tuple[str, float]] = []
        for cells in all_rows:
            record = row_as_map(headers, cells)
            trade_date = parse_ymd(record.get("净值日期"))
            per_10k = to_float(record.get("每万份收益"))
            if trade_date and per_10k is not None:
                raw_income.append((trade_date, per_10k))
        synthetic = 100.0
        series: list[tuple[str, float]] = []
        for trade_date, per_10k in sorted(dict(raw_income).items()):
            synthetic *= 1.0 + per_10k / 10000.0
            series.append((trade_date, synthetic))
        if not series:
            raise NoDataBeforeCutoff("天天 F10 货币基金截止日前区间数据为空")
        return series, {
            "sourcePointCount": len(series),
            "adjustedPointCount": len(series),
            "adjustmentMethod": "万份收益复利指数",
            "latestUnitNav": None,
            "latestAccumulatedNav": None,
        }, raw_docs

    records: list[tuple[str, float, float | None, float | None]] = []
    latest_unit: float | None = None
    latest_acc: float | None = None
    latest_date = ""
    for cells in all_rows:
        record = row_as_map(headers, cells)
        trade_date = parse_ymd(record.get("净值日期"))
        unit_nav = positive_float(record.get("单位净值"))
        accumulated_nav = positive_float(record.get("累计净值"))
        daily_return = to_float(record.get("日增长率"))
        if not trade_date or unit_nav is None:
            continue
        records.append((trade_date, unit_nav, accumulated_nav, daily_return))
        if trade_date >= latest_date:
            latest_date = trade_date
            latest_unit = unit_nav
            latest_acc = accumulated_nav
    records.sort(key=lambda item: item[0])
    adjusted_index = 100.0
    full_series: list[tuple[str, float]] = []
    for index, (trade_date, _unit_nav, _accumulated_nav, daily_return) in enumerate(records):
        if index == 0:
            full_series.append((trade_date, adjusted_index))
            continue
        if daily_return is None or not math.isfinite(daily_return) or 1.0 + daily_return / 100.0 <= 0:
            continue
        adjusted_index *= 1.0 + daily_return / 100.0
        full_series.append((trade_date, adjusted_index))
    series = trim_series(full_series, lower_bound, end_date)
    if not series:
        raise NoDataBeforeCutoff("天天 F10 截止日前区间数据为空")
    return series, {
        "sourcePointCount": len(records),
        "adjustedPointCount": len(full_series),
        "adjustmentMethod": "天天_F10日增长率复利指数",
        "latestUnitNav": latest_unit,
        "latestAccumulatedNav": latest_acc,
    }, raw_docs


def frequency_stats(series: list[tuple[str, float]], end_date: date) -> dict[str, Any]:
    one_year_start = end_date - timedelta(days=365)
    dates = [date.fromisoformat(trade_date) for trade_date, _nav in series if one_year_start <= date.fromisoformat(trade_date) <= end_date]
    dates = sorted(set(dates))
    gaps = [(right - left).days for left, right in zip(dates, dates[1:]) if right > left]
    if not gaps:
        return {"label": "点数不足", "medianGap": None, "maxGap": None}
    med = float(median(gaps))
    if med <= 4:
        label = "日频"
    elif med <= 10:
        label = "周频/低频"
    elif med <= 45:
        label = "月频/低频"
    else:
        label = "不定期/低频"
    return {"label": label, "medianGap": round(med, 4), "maxGap": max(gaps)}


def build_metric_result(
    target: FundTarget,
    *,
    end_date: date,
    series: list[tuple[str, float]],
    meta: dict[str, Any],
    source: str,
    source_url: str,
    raw_docs: list[dict[str, Any]],
    run_id: str,
) -> dict[str, Any]:
    statuses: dict[str, Any] = {}
    output: dict[str, Any] = {
        "基金代码": target.fund_code,
        "绩效截止日期": end_date.isoformat(),
        "基金名称": target.fund_name,
        "基金类型": target.fund_type,
        "基金公司": target.fund_company,
        "基金分类桶": target.bucket,
        "是否货币基金": 1 if target.is_money else 0,
        "净值映射基金代码": target.nav_code,
        "净值映射说明": target.mapping_note,
        "采集状态": "成功",
        "采集来源": source,
        "来源URL": source_url,
        "失败原因": "",
        "请求尝试次数": sum(int(doc.get("attempts") or 0) for doc in raw_docs),
        "请求耗时秒": round(sum(float(doc.get("elapsed") or 0) for doc in raw_docs), 4),
        "原始响应字节数": sum(len(str(doc.get("text") or "").encode("utf-8")) for doc in raw_docs),
        "原始响应SHA256": hashlib.sha256("\n".join(str(doc.get("text") or "") for doc in raw_docs).encode("utf-8")).hexdigest(),
        "原始证据包路径": "",
        "源端净值点数": int(meta.get("sourcePointCount") or 0),
        "复权净值点数": int(meta.get("adjustedPointCount") or 0),
        "复权口径": clean(meta.get("adjustmentMethod")),
        "窗口净值点数": len(series),
        "窗口起始日期": series[0][0] if series else "",
        "窗口截止日期": series[-1][0] if series else "",
        "最新单位净值": meta.get("latestUnitNav"),
        "最新累计净值": meta.get("latestAccumulatedNav"),
        "区间状态JSON": "",
        "run_id": run_id,
        "更新时间": now_cn().isoformat(timespec="seconds"),
    }
    freq = frequency_stats(series, end_date)
    output["披露频率"] = freq["label"]
    output["披露中位间隔天数"] = freq["medianGap"]
    output["披露最大间隔天数"] = freq["maxGap"]
    for item in interval_definitions(end_date):
        label = item["label"]
        ret, ret_status = calc_return(series, item["start"], item["end"])
        risk = calc_risk(series, item["start"], item["end"])
        output[f"{label}收益率_百分比"] = ret
        output[f"{label}最大回撤_百分比"] = risk.get("maxDrawdown")
        output[f"{label}年化波动率_百分比"] = risk.get("volatility")
        output[f"{label}风险净值点数"] = int(risk.get("navPointCount") or 0)
        start = (ret_status or {}).get("startDate") or risk.get("startDate")
        finish = (ret_status or {}).get("endDate") or risk.get("endDate")
        output[f"{label}区间"] = f"{start}~{finish}" if start and finish else ""
        statuses[label] = {"收益状态": ret_status, "风险状态": risk}
    output["区间状态JSON"] = json.dumps(statuses, ensure_ascii=False, separators=(",", ":"))
    output["_raw_docs"] = raw_docs
    return output


def collect_one(
    target: FundTarget,
    *,
    lower_bound: date,
    end_date: date,
    timeout: int,
    attempts: int,
    run_id: str,
    skip_f10: bool,
) -> dict[str, Any]:
    errors: list[str] = []
    no_data_count = 0
    routes = ["pingzhong"] if target.is_money else ["xh5", "pingzhong"]
    if not skip_f10:
        routes.append("f10")
    for route in routes:
        try:
            if route == "xh5":
                url = XH5_URL.format(fund_code=target.nav_code)
                text, status, used_attempts, elapsed = fetch_text(
                    url,
                    timeout=timeout,
                    attempts=attempts,
                    referer=f"https://finance.sina.com.cn/fund/quotes/{target.nav_code}/bc.shtml",
                )
                series, meta = parse_xh5(text, lower_bound, end_date)
                raw_docs = [{"url": url, "text": text, "status": status, "attempts": used_attempts, "elapsed": elapsed}]
                return build_metric_result(
                    target,
                    end_date=end_date,
                    series=series,
                    meta=meta,
                    source="新浪财经_xh5Fund_复权净值",
                    source_url=url,
                    raw_docs=raw_docs,
                    run_id=run_id,
                )
            if route == "pingzhong":
                url = PINGZHONG_URL.format(fund_code=target.nav_code)
                text, status, used_attempts, elapsed = fetch_text(
                    url,
                    timeout=timeout,
                    attempts=attempts,
                    referer=f"https://fund.eastmoney.com/{target.nav_code}.html",
                )
                series, meta = parse_pingzhong(text, is_money=target.is_money, lower_bound=lower_bound, end_date=end_date)
                raw_docs = [{"url": url, "text": text, "status": status, "attempts": used_attempts, "elapsed": elapsed}]
                return build_metric_result(
                    target,
                    end_date=end_date,
                    series=series,
                    meta=meta,
                    source="天天基金_pingzhongdata_复权指数",
                    source_url=url,
                    raw_docs=raw_docs,
                    run_id=run_id,
                )
            series, meta, raw_docs = collect_f10(
                target,
                lower_bound=lower_bound,
                end_date=end_date,
                timeout=timeout,
                attempts=attempts,
            )
            return build_metric_result(
                target,
                end_date=end_date,
                series=series,
                meta=meta,
                source="天天基金_F10DataApi_lsjz",
                source_url=raw_docs[0]["url"] if raw_docs else F10_URL,
                raw_docs=raw_docs,
                run_id=run_id,
            )
        except NoDataBeforeCutoff as error:
            no_data_count += 1
            errors.append(f"{route}:{error}")
        except Exception as error:  # pragma: no cover - network dependent
            errors.append(f"{route}:{error}")
    return {
        "基金代码": target.fund_code,
        "绩效截止日期": end_date.isoformat(),
        "基金名称": target.fund_name,
        "基金类型": target.fund_type,
        "基金公司": target.fund_company,
        "基金分类桶": target.bucket,
        "是否货币基金": 1 if target.is_money else 0,
        "净值映射基金代码": target.nav_code,
        "净值映射说明": target.mapping_note,
        "采集状态": "截止日无数据" if no_data_count == len(routes) else "失败",
        "采集来源": "",
        "来源URL": "",
        "失败原因": "；".join(errors)[:4000],
        "请求尝试次数": 0,
        "请求耗时秒": None,
        "原始响应字节数": 0,
        "原始响应SHA256": "",
        "原始证据包路径": "",
        "源端净值点数": 0,
        "复权净值点数": 0,
        "复权口径": "",
        "窗口净值点数": 0,
        "窗口起始日期": "",
        "窗口截止日期": "",
        "最新单位净值": None,
        "最新累计净值": None,
        "披露频率": "",
        "披露中位间隔天数": None,
        "披露最大间隔天数": None,
        "区间状态JSON": "{}",
        "run_id": run_id,
        "更新时间": now_cn().isoformat(timespec="seconds"),
        "_raw_docs": [],
    }


def upsert_result(conn: sqlite3.Connection, result: dict[str, Any]) -> None:
    columns = [key for key in result if not key.startswith("_")]
    placeholders = ",".join("?" for _ in columns)
    quoted = ",".join(f'"{column}"' for column in columns)
    updates = ",".join(
        f'"{column}"=excluded."{column}"'
        for column in columns
        if column not in {"基金代码", "绩效截止日期"}
    )
    conn.execute(
        f'INSERT INTO "{TABLE_NAME}" ({quoted}) VALUES ({placeholders}) '
        f'ON CONFLICT("基金代码", "绩效截止日期") DO UPDATE SET {updates}',
        [result.get(column) for column in columns],
    )


class RawBatchWriter:
    def __init__(self, output_dir: Path, batch_size: int, enabled: bool) -> None:
        self.output_dir = output_dir
        self.batch_size = max(1, batch_size)
        self.enabled = enabled
        self.handle: Any | None = None
        self.batch_index = 0

    def write(self, completed: int, result: dict[str, Any]) -> str:
        if not self.enabled or not result.get("_raw_docs"):
            return ""
        target_batch = (completed - 1) // self.batch_size + 1
        if target_batch != self.batch_index:
            self.close()
            self.batch_index = target_batch
            path = self.output_dir / f"raw_batch_{target_batch:04d}.jsonl.gz"
            self.handle = gzip.open(path, "at", encoding="utf-8", compresslevel=3)
        record = {
            "fundCode": result.get("基金代码"),
            "capturedAt": result.get("更新时间"),
            "source": result.get("采集来源"),
            "documents": result.get("_raw_docs"),
        }
        self.handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        return str((self.output_dir / f"raw_batch_{target_batch:04d}.jsonl.gz").resolve())

    def close(self) -> None:
        if self.handle is not None:
            self.handle.close()
            self.handle = None


def write_summary(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


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
                stale = False
                existing: dict[str, Any] = {}
                try:
                    existing = json.loads(self.path.read_text(encoding="utf-8"))
                    started_at = parse_iso_datetime(existing.get("startedAt"))
                    stale = started_at is not None and (now_cn() - started_at).total_seconds() > self.stale_hours * 3600
                except Exception:
                    try:
                        stale = (time.time() - self.path.stat().st_mtime) > self.stale_hours * 3600
                    except OSError:
                        stale = False
                if stale:
                    stale_path = self.path.with_suffix(self.path.suffix + f".stale.{now_cn().strftime('%Y%m%dT%H%M%S')}")
                    try:
                        self.path.replace(stale_path)
                        continue
                    except OSError:
                        pass
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


def main() -> int:
    args = parse_args()
    end_date = date.fromisoformat(args.end_date)
    lower_bound = end_date - timedelta(days=396)
    run_at = now_cn()
    run_id = run_at.strftime("%Y%m%dT%H%M%S%z")
    run_dir = args.output_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    summary_path = run_dir / "summary.json"
    lock_path = args.lock_file or (args.output_root / "collect_public_fund_xls_metrics.lock")
    collector_lock = CollectorLock(lock_path, stale_hours=float(args.lock_stale_hours), enabled=not args.no_lock)

    conn = sqlite3.connect(args.db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    ensure_table(conn)
    conn.commit()
    targets = discover_targets(
        conn,
        args.end_date,
        args.refresh,
        retry_failed_only=bool(args.retry_failed_only),
        refresh_success_older_than_days=args.refresh_success_older_than_days,
    )
    selected_codes = {clean(code) for code in args.fund_code if clean(code)}
    if args.fund_code_file:
        for line in args.fund_code_file.read_text(encoding="utf-8-sig").splitlines():
            code = clean(line.split(",", 1)[0])
            if code and not code.startswith("#"):
                selected_codes.add(code)
    if selected_codes:
        selected_targets = discover_targets_by_codes(conn, selected_codes)
        target_map = {target.fund_code: target for target in selected_targets}
        targets = [target_map.get(code, FundTarget(code, code, code, "", "", False, "指定样本", "")) for code in sorted(selected_codes)]
    if args.preflight:
        targets = stratified_targets(targets, max(1, args.preflight_per_bucket))
    elif not selected_codes:
        targets.sort(key=lambda target: hashlib.sha1(target.fund_code.encode("ascii", errors="ignore")).hexdigest())
    if args.limit and args.limit > 0:
        targets = targets[: args.limit]

    summary: dict[str, Any] = {
        "runId": run_id,
        "startedAt": run_at.isoformat(timespec="seconds"),
        "status": "running",
        "dbPath": str(args.db_path.resolve()),
        "endDate": args.end_date,
        "lowerBound": lower_bound.isoformat(),
        "preflight": bool(args.preflight),
        "workers": max(1, args.workers),
        "taskBatchSize": max(1, min(args.task_batch_size, args.commit_every)),
        "rawBatchSize": max(1, args.raw_batch_size),
        "lockFile": str(lock_path.resolve()) if not args.no_lock else "",
        "targetPolicy": {
            "refresh": bool(args.refresh),
            "retryFailedOnly": bool(args.retry_failed_only),
            "refreshSuccessOlderThanDays": args.refresh_success_older_than_days,
            "skipF10": bool(args.skip_f10),
        },
        "targets": len(targets),
        "completed": 0,
        "committedBatches": 0,
        "success": 0,
        "noData": 0,
        "failed": 0,
        "sourceCounts": {},
        "bucketCounts": {},
        "failures": [],
    }
    write_summary(summary_path, summary)
    task_batch_size = max(1, min(args.task_batch_size, args.commit_every))
    writer = RawBatchWriter(run_dir, max(1, args.raw_batch_size), not args.no_raw)
    started = time.perf_counter()
    last_status_at = started
    exit_code = 0
    try:
        collector_lock.acquire(run_id)
        for batch_number, batch_start in enumerate(range(0, len(targets), task_batch_size), start=1):
            batch = targets[batch_start : batch_start + task_batch_size]
            summary["currentBatch"] = batch_number
            summary["currentBatchSize"] = len(batch)
            write_summary(summary_path, summary)
            batch_results: list[tuple[FundTarget, dict[str, Any]]] = []
            executor = ThreadPoolExecutor(max_workers=max(1, args.workers))
            future_map = {
                executor.submit(
                    collect_one,
                    target,
                    lower_bound=lower_bound,
                    end_date=end_date,
                    timeout=max(1, args.timeout_sec),
                    attempts=max(1, args.retries),
                    run_id=run_id,
                    skip_f10=bool(args.skip_f10),
                ): target
                for target in batch
            }
            try:
                for batch_completed, future in enumerate(as_completed(future_map), start=1):
                    target = future_map[future]
                    result = future.result()
                    batch_results.append((target, result))
                    observed = summary["completed"] + batch_completed
                    status = clean(result.get("采集状态"))
                    source = clean(result.get("采集来源")) or status or "失败"
                    now = time.perf_counter()
                    if (
                        observed % max(1, args.progress_every) == 0
                        or observed == len(targets)
                        or status not in {"成功", "截止日无数据"}
                        or now - last_status_at >= max(1, args.status_interval_sec)
                    ):
                        elapsed = max(0.001, now - started)
                        rate = observed / elapsed * 60.0
                        remaining = (len(targets) - observed) / rate if rate > 0 else None
                        print(
                            f"[network {observed}/{len(targets)}] committed={summary['completed']} "
                            f"rate={rate:.2f}/min eta_min={remaining:.1f} last={target.fund_code} source={source}",
                            flush=True,
                        )
                        last_status_at = now
            except KeyboardInterrupt:
                for future in future_map:
                    future.cancel()
                executor.shutdown(wait=False, cancel_futures=True)
                raise
            else:
                executor.shutdown(wait=True)

            for offset, (_target, result) in enumerate(batch_results, start=1):
                raw_path = writer.write(summary["completed"] + offset, result)
                if raw_path:
                    result["原始证据包路径"] = raw_path
            writer.close()

            try:
                conn.execute("BEGIN")
                for target, result in batch_results:
                    upsert_result(conn, result)
                conn.commit()
            except Exception:
                conn.rollback()
                raise

            for target, result in batch_results:
                status = clean(result.get("采集状态"))
                source = clean(result.get("采集来源")) or status or "失败"
                summary["success"] += int(status == "成功")
                summary["noData"] += int(status == "截止日无数据")
                summary["failed"] += int(status not in {"成功", "截止日无数据"})
                summary["sourceCounts"][source] = int(summary["sourceCounts"].get(source, 0)) + 1
                summary["bucketCounts"][target.bucket] = int(summary["bucketCounts"].get(target.bucket, 0)) + 1
                if status == "失败":
                    summary["failures"].append(
                        {"fundCode": target.fund_code, "fundName": target.fund_name, "bucket": target.bucket, "error": result.get("失败原因")}
                    )
            summary["completed"] += len(batch_results)
            summary["committedBatches"] = batch_number
            elapsed = max(0.001, time.perf_counter() - started)
            rate = summary["completed"] / elapsed * 60.0
            remaining = (len(targets) - summary["completed"]) / rate if rate > 0 else None
            summary["elapsedSeconds"] = round(elapsed, 3)
            summary["ratePerMinute"] = round(rate, 3)
            summary["etaMinutes"] = round(remaining, 3) if remaining is not None else None
            write_summary(summary_path, summary)
            print(
                f"[commit batch={batch_number}] completed={summary['completed']}/{len(targets)} "
                f"success={summary['success']} no_data={summary['noData']} failed={summary['failed']} "
                f"rate={rate:.2f}/min eta_min={remaining:.1f}",
                flush=True,
            )
        summary["status"] = "completed"
    except KeyboardInterrupt:
        conn.rollback()
        summary["status"] = "interrupted"
        summary["interruptedAt"] = now_cn().isoformat(timespec="seconds")
        summary["elapsedSeconds"] = round(time.perf_counter() - started, 3)
        write_summary(summary_path, summary)
        print(
            f"[interrupted] committed={summary['completed']}/{len(targets)}; current uncommitted batch will be retried",
            flush=True,
        )
        exit_code = 130
    finally:
        writer.close()
        conn.close()
        collector_lock.release()
    summary["finishedAt"] = now_cn().isoformat(timespec="seconds")
    summary["elapsedSeconds"] = round(time.perf_counter() - started, 3)
    summary["successRate"] = round(summary["success"] / len(targets), 6) if targets else 1.0
    summary["handledRate"] = round((summary["success"] + summary["noData"]) / len(targets), 6) if targets else 1.0
    summary["summaryPath"] = str(summary_path.resolve())
    write_summary(summary_path, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    if exit_code:
        return exit_code
    return 0 if summary["handledRate"] >= (0.9 if args.preflight else 0.0) else 2


if __name__ == "__main__":
    raise SystemExit(main())
