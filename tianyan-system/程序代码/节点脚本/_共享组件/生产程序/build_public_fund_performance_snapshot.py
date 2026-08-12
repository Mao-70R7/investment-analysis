from __future__ import annotations

import argparse
import gzip
import json
import math
import os
import shutil
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import stdev
from typing import Any

from benchmark_comparison_pool import build_comparison_pool


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "public_fund_performance_snapshot"
TABLE_NAME = "公募基金产品绩效快照"
REQUIRED_SNAPSHOT_COLUMNS = {
    "基金代码",
    "基准风险资产权重",
    "基准风险资产权重来源",
}
_BENCHMARK_CATALOG_CACHE: Any | None = None
_BENCHMARK_COMPUTE_FUNC: Any | None = None
_BENCHMARK_STATIC_FUNC: Any | None = None
_DYNAMIC_BENCHMARK_OVERRIDE_CACHE: dict[tuple[int, str], dict[str, Any]] | None = None
DYNAMIC_BENCHMARK_OVERRIDE_PATH = PROJECT_ROOT / "config" / "动态基准年度权重.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build all-market public fund performance snapshot into SQLite.")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--site-dir", type=Path, help="Optional formal basic_data directory to receive JS/JSON data packs.")
    parser.add_argument("--end-date", help="Optional YYYY-MM-DD performance cutoff. Defaults to latest local NAV date.")
    parser.add_argument("--chunk-size", type=int, default=600)
    return parser.parse_args()


def clean(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text in {"None", "nan", "null", "--", "-"} else text


def as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    try:
        number = float(str(value).replace("%", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def round_or_none(value: Any, digits: int = 6) -> float | None:
    number = as_float(value)
    return round(number, digits) if number is not None else None


def date_or_none(value: Any) -> date | None:
    text = clean(value)
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    return row is not None


def load_external_fund_audit(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    if not table_exists(conn, "外部基金0630核对"):
        return {}
    return {
        clean(row["基金代码"]): dict(row)
        for row in conn.execute('SELECT * FROM "外部基金0630核对"')
        if clean(row["基金代码"])
    }


def load_supplemental_metrics(conn: sqlite3.Connection, end_date: date) -> dict[str, dict[str, Any]]:
    if not table_exists(conn, "公募基金区间绩效补齐"):
        return {}
    return {
        clean(row["基金代码"]): dict(row)
        for row in conn.execute(
            '''
            SELECT *
            FROM "公募基金区间绩效补齐"
            WHERE "绩效截止日期" = ? AND "采集状态" = '成功'
            ''',
            (end_date.isoformat(),),
        )
        if clean(row["基金代码"])
    }


def apply_supplemental_metrics(output: dict[str, Any], supplemental: dict[str, Any] | None) -> None:
    output["收益确认来源"] = clean(output.get("收益确认来源")) or "本地复权净值"
    if not supplemental:
        output["源端复爬收益核对状态"] = "未复爬"
        return
    source = clean(supplemental.get("采集来源"))
    method = clean(supplemental.get("复权口径"))
    if not source or not method:
        output["源端复爬收益核对状态"] = "复爬缺复权口径"
        return
    output["源端复爬收益来源"] = source
    output["源端复爬复权口径"] = method
    output["源端复爬窗口起始日"] = clean(supplemental.get("窗口起始日期"))
    output["源端复爬窗口截止日"] = clean(supplemental.get("窗口截止日期"))
    output["源端复爬净值点数"] = supplemental.get("窗口净值点数")
    output["源端复爬收益核对状态"] = "已复爬"
    for label in ["上半年", "今年以来", "近1周", "近1月", "近3月", "近6月", "近1年"]:
        ret = round_or_none(supplemental.get(f"{label}收益率_百分比"))
        dd = round_or_none(supplemental.get(f"{label}最大回撤_百分比"))
        vol = round_or_none(supplemental.get(f"{label}年化波动率_百分比"))
        points = supplemental.get(f"{label}风险净值点数")
        interval = clean(supplemental.get(f"{label}区间"))
        if ret is not None:
            output[f"源端复爬{label}收益率_百分比"] = ret
            local_ret = as_float(output.get(f"{label}收益率_百分比"))
            if local_ret is not None:
                output[f"源端复爬{label}本地收益差异_百分点"] = round(ret - local_ret, 6)
        if ret is not None and dd is not None:
            output[f"源端复爬{label}最大回撤_百分比"] = dd
        if ret is not None and vol is not None:
            output[f"源端复爬{label}年化波动率_百分比"] = vol
        if ret is not None and (dd is not None or vol is not None):
            output[f"源端复爬{label}风险来源"] = f"{source}/{method}"
        if ret is not None and points is not None:
            output[f"源端复爬{label}风险净值点数"] = points
        if interval:
            output[f"源端复爬{label}区间"] = interval
    output["源端复爬上半年收益率_百分比"] = round_or_none(supplemental.get("上半年收益率_百分比"))
    output["源端复爬最大回撤_百分比"] = round_or_none(supplemental.get("上半年最大回撤_百分比"))
    output["源端复爬年化波动率_百分比"] = round_or_none(supplemental.get("上半年年化波动率_百分比"))
    if output.get("源端复爬上半年收益率_百分比") is not None:
        output["收益确认来源"] = "本地复权净值；源端复爬仅作核对"


def apply_external_fund_audit(output: dict[str, Any], external: dict[str, Any] | None) -> None:
    if not external:
        output["外部收益核对状态"] = "外部表无该代码"
        return
    local_return = as_float(output.get("本地上半年收益率_百分比"))
    if local_return is None:
        local_return = as_float(output.get("上半年收益率_百分比"))
    external_return = as_float(external.get("上半年复权收益率_百分比"))
    supplemental_return = as_float(output.get("源端复爬上半年收益率_百分比"))
    output["本地上半年收益率_百分比"] = local_return
    output["外部上半年收益率_百分比"] = external_return
    output["外部基金名称"] = clean(external.get("基金名称"))
    output["外部业绩比较基准"] = clean(external.get("业绩比较基准"))
    if external_return is None:
        output["外部收益核对状态"] = "外部收益缺失"
        return
    difference = None if local_return is None else round(local_return - external_return, 6)
    output["外部收益差异_百分点"] = difference
    if supplemental_return is not None:
        output["源端复爬外部收益差异_百分点"] = round(supplemental_return - external_return, 6)
        output["源端复爬本地收益差异_百分点"] = None if local_return is None else round(supplemental_return - local_return, 6)
        if abs(output["源端复爬外部收益差异_百分点"]) <= 0.05:
            output["外部收益核对状态"] = "源端复爬与外部通过"
        else:
            output["外部收益核对状态"] = "源端复爬与外部差异超0.05个百分点"
        return
    output["收益确认来源"] = clean(output.get("收益确认来源")) or "本地复权净值"
    if difference is None:
        output["外部收益核对状态"] = "仅外部收益，不覆盖主指标"
        return
    if abs(difference) <= 0.05:
        output["外部收益核对状态"] = "通过"
        return
    output["外部收益核对状态"] = "差异超0.05个百分点"


def chunked(values: list[str], size: int) -> list[list[str]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def latest_nav_date(conn: sqlite3.Connection) -> date:
    value = conn.execute('SELECT MAX("交易日期") FROM "基金日度净值"').fetchone()[0]
    parsed = date_or_none(value)
    if not parsed:
        raise RuntimeError("基金日度净值缺少有效交易日期，无法构建公募基金绩效快照。")
    return parsed


def interval_definitions(end_date: date) -> list[dict[str, Any]]:
    h1_end = date(end_date.year, 6, 30)
    if end_date < h1_end:
        h1_end = end_date
    return [
        {"label": "上半年", "key": "h1", "start": date(end_date.year - 1, 12, 31), "end": h1_end},
        {"label": "今年以来", "key": "ytd", "start": date(end_date.year - 1, 12, 31), "end": end_date},
        {"label": "近1周", "key": "1w", "start": end_date - timedelta(days=7), "end": end_date},
        {"label": "近1月", "key": "1m", "start": end_date - timedelta(days=30), "end": end_date},
        {"label": "近3月", "key": "3m", "start": end_date - timedelta(days=90), "end": end_date},
        {"label": "近6月", "key": "6m", "start": end_date - timedelta(days=183), "end": end_date},
        {"label": "近1年", "key": "1y", "start": end_date - timedelta(days=365), "end": end_date},
    ]


def load_fund_universe(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    query = (
        """
        SELECT
          d."基金代码",
          COALESCE(NULLIF(TRIM(i."基金名称"), ''), NULLIF(TRIM(d."标准基金名称"), ''), d."基金代码") AS "基金名称",
          COALESCE(NULLIF(TRIM(i."基金公司"), ''), NULLIF(TRIM(d."基金公司"), '')) AS "基金公司",
          COALESCE(NULLIF(TRIM(i."基金类型"), ''), NULLIF(TRIM(d."天天基金细分类"), ''), NULLIF(TRIM(d."标准资产细类"), ''), NULLIF(TRIM(d."标准资产大类"), '')) AS "基金类型",
          d."天天基金细分类",
          d."天天基金大类",
          d."天天基金二级分类",
          d."基金经理",
          d."标准资产大类",
          d."标准资产细类",
          d."市场地域标签",
          d."主动被动标签",
          d."跟踪指数_名称推断",
          d."是否当前库使用",
          d."是否货币基金",
          d."是否债券基金",
          d."是否权益基金",
          d."是否混合基金",
          d."是否指数基金",
          d."是否ETF",
          d."是否ETF联接",
          d."是否QDII",
          d."是否FOF",
          d."是否LOF",
          d."是否REITs",
          d."是否商品黄金",
          m."历史起始日期",
          m."历史结束日期",
          m."历史记录数",
          m."最新单位净值",
          m."最新累计净值",
          m."最新日收益率_百分比",
          i."最新净值",
          i."最新净值日期",
          COALESCE(g."业绩比较基准", fb."业绩比较基准", fbc."业绩比较基准") AS "业绩比较基准",
          COALESCE(g."跟踪标的", fb."跟踪标的", d."跟踪指数_名称推断") AS "跟踪标的",
          COALESCE(g."F10基金类型", fb."F10基金类型", fbc."F10基金类型") AS "F10基金类型",
          COALESCE(g."F10成立日期", fb."F10成立日期") AS "F10成立日期",
          COALESCE(g."采集状态", fb."采集状态", fbc."采集状态") AS "F10采集状态",
          COALESCE(g."HTTP状态", fb."HTTP状态") AS "F10_HTTP状态",
          COALESCE(g."错误信息", fb."错误信息") AS "F10错误信息",
          COALESCE(g."F10页面URL", fb."F10页面URL") AS "F10页面URL",
          COALESCE(g."采集批次", fb."采集批次", fbc."采集批次") AS "F10采集批次",
          fbc."基准权益权重_百分比",
          fbc."基准债券权重_百分比",
          fbc."基准货币权重_百分比",
          fbc."基准商品权重_百分比",
          fbc."基准海外权重_百分比",
          fbc."基准未知权重_百分比",
          fbc."基准权重合计_百分比",
          __FOF_RISK_BUCKET__,
          fbc."基准解析说明" AS "FOF基准解析说明",
          fbc."解析置信度" AS "FOF基准解析置信度",
          fbc."采集状态" AS "FOF基准采集状态"
        FROM "基金标准分类字典" d
        LEFT JOIN "基金信息" i ON i."基金代码" = d."基金代码"
        LEFT JOIN "基金净值概况" m ON m."基金代码" = d."基金代码"
        LEFT JOIN "基金F10基准" g ON g."基金代码" = d."基金代码"
        LEFT JOIN "FOF基金F10基准" fb ON fb."基金代码" = d."基金代码"
        LEFT JOIN "FOF基准细分分类" fbc ON fbc."基金代码" = d."基金代码"
        WHERE d."基金代码" IS NOT NULL AND TRIM(d."基金代码") <> ''
        ORDER BY d."基金代码"
        """
        if table_exists(conn, "基金F10基准")
        else """
        SELECT
          d."基金代码",
          COALESCE(NULLIF(TRIM(i."基金名称"), ''), NULLIF(TRIM(d."标准基金名称"), ''), d."基金代码") AS "基金名称",
          COALESCE(NULLIF(TRIM(i."基金公司"), ''), NULLIF(TRIM(d."基金公司"), '')) AS "基金公司",
          COALESCE(NULLIF(TRIM(i."基金类型"), ''), NULLIF(TRIM(d."天天基金细分类"), ''), NULLIF(TRIM(d."标准资产细类"), ''), NULLIF(TRIM(d."标准资产大类"), '')) AS "基金类型",
          d."天天基金细分类",
          d."天天基金大类",
          d."天天基金二级分类",
          d."基金经理",
          d."标准资产大类",
          d."标准资产细类",
          d."市场地域标签",
          d."主动被动标签",
          d."跟踪指数_名称推断",
          d."是否当前库使用",
          d."是否货币基金",
          d."是否债券基金",
          d."是否权益基金",
          d."是否混合基金",
          d."是否指数基金",
          d."是否ETF",
          d."是否ETF联接",
          d."是否QDII",
          d."是否FOF",
          d."是否LOF",
          d."是否REITs",
          d."是否商品黄金",
          m."历史起始日期",
          m."历史结束日期",
          m."历史记录数",
          m."最新单位净值",
          m."最新累计净值",
          m."最新日收益率_百分比",
          i."最新净值",
          i."最新净值日期",
          COALESCE(fb."业绩比较基准", fbc."业绩比较基准") AS "业绩比较基准",
          COALESCE(fb."跟踪标的", d."跟踪指数_名称推断") AS "跟踪标的",
          COALESCE(fb."F10基金类型", fbc."F10基金类型") AS "F10基金类型",
          fb."F10成立日期",
          COALESCE(fb."采集状态", fbc."采集状态") AS "F10采集状态",
          fb."HTTP状态" AS "F10_HTTP状态",
          fb."错误信息" AS "F10错误信息",
          fb."F10页面URL" AS "F10页面URL",
          COALESCE(fb."采集批次", fbc."采集批次") AS "F10采集批次",
          fbc."基准权益权重_百分比",
          fbc."基准债券权重_百分比",
          fbc."基准货币权重_百分比",
          fbc."基准商品权重_百分比",
          fbc."基准海外权重_百分比",
          fbc."基准未知权重_百分比",
          fbc."基准权重合计_百分比",
          __FOF_RISK_BUCKET__,
          fbc."基准解析说明" AS "FOF基准解析说明",
          fbc."解析置信度" AS "FOF基准解析置信度",
          fbc."采集状态" AS "FOF基准采集状态"
        FROM "基金标准分类字典" d
        LEFT JOIN "基金信息" i ON i."基金代码" = d."基金代码"
        LEFT JOIN "基金净值概况" m ON m."基金代码" = d."基金代码"
        LEFT JOIN "FOF基金F10基准" fb ON fb."基金代码" = d."基金代码"
        LEFT JOIN "FOF基准细分分类" fbc ON fbc."基金代码" = d."基金代码"
        WHERE d."基金代码" IS NOT NULL AND TRIM(d."基金代码") <> ''
        ORDER BY d."基金代码"
        """
    )
    fbc_columns = {row[1] for row in conn.execute('PRAGMA table_info("FOF基准细分分类")').fetchall()}
    legacy_bucket_column = "".join(("基准", "权益分档"))
    bucket_column = "基准风险资产权重" if "基准风险资产权重" in fbc_columns else legacy_bucket_column
    query = query.replace("__FOF_RISK_BUCKET__", f'fbc."{bucket_column}" AS "FOF基准风险资产权重"')
    rows = conn.execute(query).fetchall()
    return [dict(row) for row in rows]


def load_nav_series(conn: sqlite3.Connection, codes: list[str], start_date: date, end_date: date) -> dict[str, list[tuple[str, float]]]:
    if not codes:
        return {}
    placeholders = ",".join("?" for _ in codes)
    columns = {row[1] for row in conn.execute('PRAGMA table_info("基金日度净值")')}
    adjusted_expr = '"复权净值"' if "复权净值" in columns else 'NULL AS "复权净值"'
    rows = conn.execute(
        f"""
        SELECT "基金代码", "交易日期", "单位净值", "累计净值", "日收益率_百分比", "是否货币基金", {adjusted_expr}
        FROM "基金日度净值"
        WHERE "基金代码" IN ({placeholders})
          AND "交易日期" >= ?
          AND "交易日期" <= ?
        ORDER BY "基金代码", "交易日期"
        """,
        [*codes, start_date.isoformat(), end_date.isoformat()],
    )
    grouped: dict[str, list[tuple[str, float]]] = {}
    synthetic_by_code: dict[str, float] = {}
    previous_proxy_by_code: dict[str, float] = {}
    for row in rows:
        code = clean(row["基金代码"])
        trade_date = clean(row["交易日期"])[:10]
        if not code or not trade_date:
            continue
        adjusted_nav = as_float(row["复权净值"])
        proxy = as_float(row["累计净值"])
        if proxy is None:
            proxy = as_float(row["单位净值"])
        daily_pct = as_float(row["日收益率_百分比"])
        is_money = int(as_float(row["是否货币基金"]) or 0) == 1
        if adjusted_nav is not None:
            nav = adjusted_nav
            synthetic_by_code[code] = nav
        else:
            previous = synthetic_by_code.get(code)
            if previous is None:
                nav = 100.0
            elif is_money and daily_pct is not None and math.isfinite(daily_pct) and 1.0 + daily_pct / 100.0 > 0:
                nav = previous * (1.0 + daily_pct / 100.0)
            else:
                previous_proxy = previous_proxy_by_code.get(code)
                nav = previous * (proxy / previous_proxy) if proxy and previous_proxy else previous
            synthetic_by_code[code] = nav
        if proxy is not None:
            previous_proxy_by_code[code] = proxy
        if nav is not None and nav > 0:
            grouped.setdefault(code, []).append((trade_date, nav))
    return grouped


def load_nav_close_rows(conn: sqlite3.Connection, codes: list[str], end_date: date) -> dict[str, dict[str, Any]]:
    if not codes:
        return {}
    placeholders = ",".join("?" for _ in codes)
    rows = conn.execute(
        f'''
        SELECT n."基金代码", n."交易日期", n."单位净值", n."累计净值"
        FROM "基金日度净值" n
        JOIN (
          SELECT "基金代码", MAX("交易日期") AS max_date
          FROM "基金日度净值"
          WHERE "基金代码" IN ({placeholders}) AND "交易日期" <= ?
          GROUP BY "基金代码"
        ) latest
          ON latest."基金代码" = n."基金代码" AND latest.max_date = n."交易日期"
        ''',
        [*codes, end_date.isoformat()],
    )
    return {clean(row["基金代码"]): dict(row) for row in rows}


def value_on_or_before(series: list[tuple[str, float]], target: date) -> tuple[str, float] | None:
    selected: tuple[str, float] | None = None
    target_text = target.isoformat()
    for trade_date, nav in series:
        if trade_date <= target_text:
            selected = (trade_date, nav)
        else:
            break
    return selected


def calc_return(series: list[tuple[str, float]], start_date: date, end_date: date) -> tuple[float | None, dict[str, Any]]:
    if not series:
        return None, {"status": "无本地净值", "startDate": "", "endDate": ""}
    first_date = series[0][0]
    start = value_on_or_before(series, start_date)
    end = value_on_or_before(series, end_date)
    if not end:
        return None, {"status": "区间截止日前无净值", "startDate": "", "endDate": ""}
    if not start:
        return None, {"status": "净值起点晚于区间起点", "startDate": first_date, "endDate": end[0]}
    if end[0] <= start[0] or start[1] <= 0 or end[1] <= 0:
        return None, {"status": "区间净值点不足", "startDate": start[0], "endDate": end[0]}
    return round((end[1] / start[1] - 1.0) * 100.0, 6), {"status": "完整区间", "startDate": start[0], "endDate": end[0]}


def calc_risk(series: list[tuple[str, float]], start_date: date, end_date: date) -> dict[str, Any]:
    if not series:
        return {"maxDrawdown": None, "volatility": None, "navPointCount": 0, "status": "无本地净值", "startDate": "", "endDate": ""}
    start = value_on_or_before(series, start_date)
    status = "完整区间"
    if not start:
        start = next((item for item in series if item[0] <= end_date.isoformat()), None)
        status = "净值起点晚于区间起点"
    if not start:
        return {"maxDrawdown": None, "volatility": None, "navPointCount": 0, "status": "区间内无净值", "startDate": "", "endDate": ""}
    points = [item for item in series if start[0] <= item[0] <= end_date.isoformat() and item[1] > 0]
    dedup = sorted({trade_date: nav for trade_date, nav in points}.items())
    if len(dedup) < 2:
        return {"maxDrawdown": None, "volatility": None, "navPointCount": len(dedup), "status": "区间净值点不足", "startDate": start[0], "endDate": dedup[-1][0] if dedup else ""}
    peak = dedup[0][1]
    max_drawdown = 0.0
    observed_returns: list[float] = []
    business_day_gaps: list[int] = []
    calendar_day_gaps: list[int] = []
    previous_date = date.fromisoformat(dedup[0][0])
    previous = dedup[0][1]
    for trade_date, nav in dedup[1:]:
        if peak > 0:
            max_drawdown = min(max_drawdown, nav / peak - 1.0)
        peak = max(peak, nav)
        if previous > 0:
            observed_returns.append(nav / previous - 1.0)
            current_date = date.fromisoformat(trade_date)
            calendar_gap = max(1, (current_date - previous_date).days)
            business_gap = sum(
                1
                for offset in range(1, calendar_gap + 1)
                if (previous_date + timedelta(days=offset)).weekday() < 5
            )
            business_day_gaps.append(max(1, business_gap))
            calendar_day_gaps.append(calendar_gap)
            previous_date = current_date
        previous = nav
    volatility = None
    annualization_factor = None
    if len(observed_returns) >= 2:
        business_span = max(1, sum(business_day_gaps))
        observations_per_year = len(observed_returns) * 252.0 / business_span
        annualization_factor = math.sqrt(observations_per_year)
        volatility = stdev(observed_returns) * annualization_factor * 100.0
    median_gap = None
    disclosure_frequency = "点数不足"
    if calendar_day_gaps:
        ordered_gaps = sorted(calendar_day_gaps)
        middle = len(ordered_gaps) // 2
        median_gap = (
            float(ordered_gaps[middle])
            if len(ordered_gaps) % 2
            else (ordered_gaps[middle - 1] + ordered_gaps[middle]) / 2.0
        )
        if median_gap <= 4:
            disclosure_frequency = "日频"
        elif median_gap <= 10:
            disclosure_frequency = "周频/低频"
        elif median_gap <= 45:
            disclosure_frequency = "月频/低频"
        else:
            disclosure_frequency = "不定期/低频"
    return {
        "maxDrawdown": round(max_drawdown * 100.0, 6),
        "volatility": round(volatility, 6) if volatility is not None else None,
        "navPointCount": len(dedup),
        "status": status,
        "startDate": dedup[0][0],
        "endDate": dedup[-1][0],
        "disclosureFrequency": disclosure_frequency,
        "medianGapDays": median_gap,
        "maxGapDays": max(calendar_day_gaps) if calendar_day_gaps else None,
        "annualizationFactor": round(annualization_factor, 8) if annualization_factor is not None else None,
    }


def risk_asset_bucket_from_pct(value: Any) -> str:
    pct = as_float(value)
    if pct is None:
        return ""
    if pct <= 0:
        return "L0"
    return f"L{min(10, max(1, math.ceil(pct / 10.0)))}"


def benchmark_mix(benchmark_text: str) -> dict[str, Any]:
    global _BENCHMARK_CATALOG_CACHE, _BENCHMARK_COMPUTE_FUNC, _BENCHMARK_STATIC_FUNC
    if not benchmark_text:
        return {}
    try:
        if _BENCHMARK_COMPUTE_FUNC is None:
            from benchmark_asset_classification import compute_benchmark_asset_mix, is_static_benchmark_formula, load_benchmark_catalog

            _BENCHMARK_COMPUTE_FUNC = compute_benchmark_asset_mix
            _BENCHMARK_STATIC_FUNC = is_static_benchmark_formula
            _BENCHMARK_CATALOG_CACHE = load_benchmark_catalog()
    except Exception:
        return {}
    try:
        return _BENCHMARK_COMPUTE_FUNC(benchmark_text, _BENCHMARK_CATALOG_CACHE)
    except Exception:
        return {}


def benchmark_formula_is_static(benchmark_text: str) -> bool:
    global _BENCHMARK_STATIC_FUNC
    if _BENCHMARK_STATIC_FUNC is None:
        benchmark_mix(benchmark_text)
    return bool(_BENCHMARK_STATIC_FUNC and _BENCHMARK_STATIC_FUNC(benchmark_text))


def load_dynamic_benchmark_overrides() -> dict[tuple[int, str], dict[str, Any]]:
    global _DYNAMIC_BENCHMARK_OVERRIDE_CACHE
    if _DYNAMIC_BENCHMARK_OVERRIDE_CACHE is not None:
        return _DYNAMIC_BENCHMARK_OVERRIDE_CACHE
    overrides: dict[tuple[int, str], dict[str, Any]] = {}
    try:
        payload = json.loads(DYNAMIC_BENCHMARK_OVERRIDE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        _DYNAMIC_BENCHMARK_OVERRIDE_CACHE = overrides
        return overrides
    for item in payload.get("年度权重") or []:
        year = int(as_float(item.get("年份")) or 0)
        for code in item.get("基金代码") or []:
            fund_code = clean(code).zfill(6)
            if year and fund_code:
                overrides[(year, fund_code)] = dict(item)
    _DYNAMIC_BENCHMARK_OVERRIDE_CACHE = overrides
    return overrides


def audited_dynamic_benchmark(row: dict[str, Any]) -> dict[str, Any] | None:
    year = datetime.now().year
    code = clean(row.get("基金代码")).zfill(6)
    item = load_dynamic_benchmark_overrides().get((year, code))
    if not item:
        return None
    equity = round_or_none(item.get("权益权重_百分比"))
    bond = round_or_none(item.get("债券权重_百分比"))
    cash = round_or_none(item.get("现金权重_百分比")) or 0.0
    commodity = round_or_none(item.get("商品权重_百分比")) or 0.0
    alternative = round_or_none(item.get("另类权重_百分比")) or 0.0
    unknown = round_or_none(item.get("未知权重_百分比")) or 0.0
    if equity is None or bond is None or abs(equity + bond + cash + commodity + alternative + unknown - 100.0) > 0.01:
        return None
    a_share_ratio = round_or_none(item.get("A股占权益_百分比"))
    hk_ratio = round_or_none(item.get("港股占权益_百分比"))
    overseas_equity = round_or_none(item.get("海外权益权重_百分比")) or 0.0
    if a_share_ratio is None or hk_ratio is None or overseas_equity > equity + 0.01:
        return None
    hk_equity = round_or_none(equity * hk_ratio / 100.0)
    a_share_equity = round_or_none(equity * a_share_ratio / 100.0)
    if abs((a_share_equity or 0.0) + (hk_equity or 0.0) + overseas_equity - equity) > 0.02:
        return None
    risk_asset_weight = round_or_none(equity + commodity + alternative)
    bucket = risk_asset_bucket_from_pct(risk_asset_weight) if unknown <= 0.01 else ""
    comparison = build_comparison_pool(
        bucket=bucket,
        equity=equity,
        bond=bond,
        cash=cash,
        commodity=commodity,
        alternative=alternative,
        unknown=unknown,
    )
    source_name = clean(item.get("来源名称"))
    source_date = clean(item.get("来源日期"))
    source_url = clean(item.get("来源URL"))
    return {
        "基准权益权重_百分比": equity,
        "基准风险资产权重": bucket,
        "基准风险资产权重_百分比": risk_asset_weight,
        "基准风险资产权重来源": "年度披露权重核验",
        "基准映射置信度": "高" if unknown <= 0.01 else "中",
        "基准债券权重_百分比": bond,
        "基准货币权重_百分比": cash,
        "基准商品权重_百分比": commodity,
        "基准另类权重_百分比": alternative,
        "基准港股权益权重_百分比": hk_equity,
        "基准海外权益权重_百分比": overseas_equity,
        "基准海外权重_百分比": round_or_none((hk_equity or 0.0) + overseas_equity),
        "基准未知权重_百分比": unknown,
        "基准权重合计_百分比": 100.0,
        **comparison,
        "基准解析说明": (
            f"按{year}年公开披露权重核验：权益{equity:.2f}%、债券{bond:.2f}%、现金{cash:.2f}%、"
            f"商品{commodity:.2f}%、另类{alternative:.2f}%、未知{unknown:.2f}%；"
            f"来源={source_name}（{source_date}）；{source_url}"
        ),
        "业绩基准获取状态": "已取得" if unknown <= 0.01 else "已取得未完整解析",
        "是否使用分类兜底": 0,
    }


def enrich_benchmark(row: dict[str, Any]) -> dict[str, Any]:
    benchmark_text = clean(row.get("业绩比较基准"))
    mix = benchmark_mix(benchmark_text)
    unified_bucket = clean(mix.get("基准风险资产权重"))
    unified_confidence = clean(mix.get("基准映射置信度"))
    unified_unknown = round_or_none(mix.get("基准资产大类-其他") or mix.get("基准资产未映射权重"))
    unified_vector = [
        round_or_none(mix.get(field))
        for field in [
            "基准资产大类-权益",
            "基准资产大类-债券",
            "基准资产大类-现金",
            "基准资产大类-商品",
            "基准资产大类-另类",
            "基准资产大类-其他",
        ]
    ]
    unified_total = round_or_none(sum(value for value in unified_vector if value is not None))
    unified_is_valid = (
        unified_bucket in {f"L{index}" for index in range(11)}
        and unified_confidence in {"高", "中"}
        and (unified_unknown is None or unified_unknown <= 0.01)
        and unified_total is not None
        and abs(unified_total - 100.0) <= 0.01
    )
    existing_bucket = clean(row.get("FOF基准风险资产权重"))
    existing_equity = round_or_none(row.get("基准权益权重_百分比"))
    existing_unknown = round_or_none(row.get("基准未知权重_百分比"))
    existing_total = round_or_none(row.get("基准权重合计_百分比"))
    existing_confidence = clean(row.get("FOF基准解析置信度"))
    existing_note = clean(row.get("FOF基准解析说明"))
    existing_is_valid = (
        existing_bucket in {f"L{index}" for index in range(11)}
        and existing_equity is not None
        and existing_confidence in {"高", "中"}
        and (existing_unknown is None or existing_unknown <= 0.01)
        and existing_total is not None
        and abs(existing_total - 100.0) <= 0.01
    )
    # The legacy FOF parser contains category heuristics. For literal, static
    # benchmark formulas the unified index catalog is the more direct source of
    # truth; keep the FOF-specific result only for dynamic/glide-path formulas.
    if existing_is_valid and not unified_is_valid and not benchmark_formula_is_static(benchmark_text) and "估算" not in existing_note:
        existing_bond = round_or_none(row.get("基准债券权重_百分比"))
        existing_cash = round_or_none(row.get("基准货币权重_百分比"))
        existing_commodity = round_or_none(row.get("基准商品权重_百分比"))
        existing_alternative = round_or_none(row.get("基准另类权重_百分比")) or 0.0
        existing_risk_weight = round_or_none((existing_equity or 0.0) + (existing_commodity or 0.0) + existing_alternative)
        existing_bucket = risk_asset_bucket_from_pct(existing_risk_weight)
        comparison = build_comparison_pool(
            bucket=existing_bucket,
            equity=existing_equity,
            bond=existing_bond,
            cash=existing_cash,
            commodity=existing_commodity,
            alternative=0.0,
            unknown=existing_unknown,
        )
        return {
            "基准权益权重_百分比": existing_equity,
            "基准风险资产权重": existing_bucket,
            "基准风险资产权重_百分比": existing_risk_weight,
            "基准风险资产权重来源": "FOF基准解析",
            "基准映射置信度": existing_confidence,
            "基准债券权重_百分比": existing_bond,
            "基准货币权重_百分比": existing_cash,
            "基准商品权重_百分比": existing_commodity,
            "基准另类权重_百分比": existing_alternative,
            **comparison,
            "基准解析说明": existing_note,
            "业绩基准获取状态": "已取得",
            "是否使用分类兜底": 0,
        }
    if benchmark_text and ("未披露" in benchmark_text or "暂无" in benchmark_text):
        return {
            "基准权益权重_百分比": None,
            "基准风险资产权重": "",
            "基准风险资产权重_百分比": None,
            "基准风险资产权重来源": "未披露业绩基准",
            "基准映射置信度": "未披露",
            "基准解析说明": f"F10返回：{benchmark_text[:180]}",
            "业绩基准获取状态": "未披露",
            "是否使用分类兜底": 0,
        }
    parsed_bucket = clean(mix.get("基准风险资产权重"))
    parsed_equity = round_or_none(mix.get("基准资产大类-权益"))
    if not parsed_bucket:
        audited_dynamic = audited_dynamic_benchmark(row)
        if audited_dynamic:
            return audited_dynamic
    if parsed_bucket or parsed_equity is not None:
        parsed_bond = round_or_none(mix.get("基准资产类别-债券"))
        parsed_cash = round_or_none(mix.get("基准资产类别-现金"))
        parsed_commodity = round_or_none(as_float(mix.get("基准资产大类-商品")) or 0.0)
        parsed_alternative = round_or_none(as_float(mix.get("基准资产大类-另类")) or 0.0)
        parsed_hk_equity = round_or_none(as_float(mix.get("基准资产类别-港股")) or 0.0)
        parsed_overseas_equity = round_or_none(as_float(mix.get("基准资产类别-海外权益")) or 0.0)
        parsed_overseas = round_or_none((parsed_hk_equity or 0.0) + (parsed_overseas_equity or 0.0))
        parsed_unknown = round_or_none(mix.get("基准资产大类-其他") or mix.get("基准资产未映射权重"))
        weight_values = [parsed_equity, parsed_bond, parsed_cash, parsed_commodity, parsed_alternative, parsed_unknown]
        parsed_total = round_or_none(sum(value for value in weight_values if value is not None))
        parsed_confidence = clean(mix.get("基准映射置信度"))
        parsed_risk_weight = round_or_none((parsed_equity or 0.0) + (parsed_commodity or 0.0) + (parsed_alternative or 0.0))
        valid_bucket = (
            parsed_bucket in {f"L{index}" for index in range(11)}
            and parsed_confidence in {"高", "中"}
            and (parsed_unknown is None or parsed_unknown <= 0.01)
            and parsed_total is not None
            and abs(parsed_total - 100.0) <= 0.01
        )
        bucket = parsed_bucket if valid_bucket else ""
        parsed_source = "F10基准解析" if valid_bucket else "业绩基准未解析"
        parsed_status = "已取得" if valid_bucket else "已取得未解析"
        parsed_note = clean(mix.get("基准公式解析"))
        invalid_total = parsed_total is None or parsed_total <= 0.0001 or parsed_total > 100.0001
        if not valid_bucket:
            parsed_note = f"{parsed_note}；未通过确定分档门槛（置信度={parsed_confidence or '空'}，权重合计={parsed_total}）".strip("；")
        if invalid_total:
            parsed_equity = None
            parsed_bond = None
            parsed_cash = None
            parsed_commodity = None
            parsed_alternative = None
            parsed_hk_equity = None
            parsed_overseas_equity = None
            parsed_overseas = None
            parsed_unknown = 100.0
            parsed_total = 100.0
            parsed_risk_weight = None
        comparison = build_comparison_pool(
            bucket=bucket,
            equity=parsed_equity,
            bond=parsed_bond,
            cash=parsed_cash,
            commodity=parsed_commodity,
            alternative=parsed_alternative,
            unknown=parsed_unknown,
        )
        return {
            "基准权益权重_百分比": parsed_equity,
            "基准风险资产权重": bucket,
            "基准风险资产权重_百分比": parsed_risk_weight,
            "基准风险资产权重来源": parsed_source,
            "基准映射置信度": parsed_confidence,
            "基准债券权重_百分比": parsed_bond,
            "基准货币权重_百分比": parsed_cash,
            "基准商品权重_百分比": parsed_commodity,
            "基准另类权重_百分比": parsed_alternative,
            "基准港股权益权重_百分比": parsed_hk_equity,
            "基准海外权益权重_百分比": parsed_overseas_equity,
            "基准海外权重_百分比": parsed_overseas,
            "基准未知权重_百分比": parsed_unknown,
            "基准权重合计_百分比": parsed_total,
            **comparison,
            "基准解析说明": parsed_note,
            "业绩基准获取状态": parsed_status,
            "是否使用分类兜底": 0,
        }
    if benchmark_text:
        missing = mix.get("基准缺失组件") if isinstance(mix, dict) else None
        suffix = ""
        if missing:
            suffix = "；" + "、".join(str(item) for item in list(missing)[:3])
        return {
            "基准权益权重_百分比": None,
            "基准风险资产权重": "",
            "基准风险资产权重_百分比": None,
            "基准风险资产权重来源": "业绩基准未解析",
            "基准映射置信度": clean(mix.get("基准映射置信度")) or "未解析",
            "基准解析说明": f"已取得业绩比较基准但未能映射为资产权重{suffix}",
            "业绩基准获取状态": "已取得未解析",
            "是否使用分类兜底": 0,
        }
    status = clean(row.get("F10采集状态")) or clean(row.get("FOF基准采集状态")) or "未采集"
    error = clean(row.get("F10错误信息"))
    reason = f"F10/FOF F10未取得业绩比较基准；采集状态={status}"
    if error:
        reason = f"{reason}；错误={error[:180]}"
    return {
        "基准权益权重_百分比": None,
        "基准风险资产权重": "",
        "基准风险资产权重_百分比": None,
        "基准风险资产权重来源": "未取得业绩基准",
        "基准映射置信度": "未披露",
        "基准解析说明": reason,
        "业绩基准获取状态": "未取得",
        "是否使用分类兜底": 0,
    }


def build_row(
    row: dict[str, Any],
    series: list[tuple[str, float]],
    intervals: list[dict[str, Any]],
    generated_at: str,
    end_date: date,
    nav_close: dict[str, Any] | None = None,
) -> dict[str, Any]:
    latest_point = value_on_or_before(series, end_date) if series else None
    first_point = series[0] if series else None
    sample_return = None
    sample_risk = {"maxDrawdown": None, "volatility": None, "navPointCount": 0}
    if first_point and latest_point and latest_point[0] > first_point[0] and first_point[1] > 0:
        sample_return = round((latest_point[1] / first_point[1] - 1.0) * 100.0, 6)
        sample_risk = calc_risk(series, date.fromisoformat(first_point[0]), end_date)
    benchmark = enrich_benchmark(row)
    full_nav_count = int(as_float(row.get("历史记录数")) or 0)
    full_nav_start = clean(row.get("历史起始日期"))
    full_nav_end = clean(row.get("历史结束日期"))
    output = {
        "基金代码": clean(row.get("基金代码")),
        "基金名称": clean(row.get("基金名称")),
        "基金公司": clean(row.get("基金公司")),
        "基金经理": clean(row.get("基金经理")),
        "基金类型": clean(row.get("基金类型")),
        "天天基金细分类": clean(row.get("天天基金细分类")),
        "天天基金大类": clean(row.get("天天基金大类")),
        "天天基金二级分类": clean(row.get("天天基金二级分类")),
        "标准资产大类": clean(row.get("标准资产大类")),
        "标准资产细类": clean(row.get("标准资产细类")),
        "市场地域标签": clean(row.get("市场地域标签")),
        "主动被动标签": clean(row.get("主动被动标签")),
        "跟踪指数_名称推断": clean(row.get("跟踪指数_名称推断")),
        "是否当前库使用": int(as_float(row.get("是否当前库使用")) or 0),
        "是否货币基金": int(as_float(row.get("是否货币基金")) or 0),
        "是否债券基金": int(as_float(row.get("是否债券基金")) or 0),
        "是否权益基金": int(as_float(row.get("是否权益基金")) or 0),
        "是否混合基金": int(as_float(row.get("是否混合基金")) or 0),
        "是否指数基金": int(as_float(row.get("是否指数基金")) or 0),
        "是否ETF": int(as_float(row.get("是否ETF")) or 0),
        "是否ETF联接": int(as_float(row.get("是否ETF联接")) or 0),
        "是否QDII": int(as_float(row.get("是否QDII")) or 0),
        "是否FOF": int(as_float(row.get("是否FOF")) or 0),
        "是否LOF": int(as_float(row.get("是否LOF")) or 0),
        "是否REITs": int(as_float(row.get("是否REITs")) or 0),
        "是否商品黄金": int(as_float(row.get("是否商品黄金")) or 0),
        "业绩比较基准": clean(row.get("业绩比较基准")),
        "跟踪标的": clean(row.get("跟踪标的")),
        "F10基金类型": clean(row.get("F10基金类型")),
        "F10成立日期": clean(row.get("F10成立日期")),
        "F10采集状态": clean(row.get("F10采集状态")),
        "F10_HTTP状态": round_or_none(row.get("F10_HTTP状态")),
        "F10错误信息": clean(row.get("F10错误信息")),
        "F10页面URL": clean(row.get("F10页面URL")),
        "F10采集批次": clean(row.get("F10采集批次")),
        **benchmark,
        "基准债券权重_百分比": benchmark.get("基准债券权重_百分比") if benchmark.get("基准债券权重_百分比") is not None else round_or_none(row.get("基准债券权重_百分比")),
        "基准货币权重_百分比": benchmark.get("基准货币权重_百分比") if benchmark.get("基准货币权重_百分比") is not None else round_or_none(row.get("基准货币权重_百分比")),
        "基准商品权重_百分比": benchmark.get("基准商品权重_百分比") if benchmark.get("基准商品权重_百分比") is not None else round_or_none(row.get("基准商品权重_百分比")),
        "基准另类权重_百分比": benchmark.get("基准另类权重_百分比") if benchmark.get("基准另类权重_百分比") is not None else 0.0,
        "基准港股权益权重_百分比": benchmark.get("基准港股权益权重_百分比"),
        "基准海外权益权重_百分比": benchmark.get("基准海外权益权重_百分比"),
        "基准海外权重_百分比": benchmark.get("基准海外权重_百分比") if benchmark.get("基准海外权重_百分比") is not None else round_or_none(row.get("基准海外权重_百分比")),
        "基准未知权重_百分比": benchmark.get("基准未知权重_百分比") if benchmark.get("基准未知权重_百分比") is not None else round_or_none(row.get("基准未知权重_百分比")),
        "基准权重合计_百分比": benchmark.get("基准权重合计_百分比") if benchmark.get("基准权重合计_百分比") is not None else round_or_none(row.get("基准权重合计_百分比")),
        "本地净值记录数": max(full_nav_count, len(series)),
        "本地净值起始日": full_nav_start or (first_point[0] if first_point else ""),
        "本地净值截止日": full_nav_end or (latest_point[0] if latest_point else ""),
        "绩效样本净值记录数": len(series),
        "绩效样本起始日": first_point[0] if first_point else "",
        "绩效样本截止日": latest_point[0] if latest_point else "",
        "净值日期": clean((nav_close or {}).get("交易日期")) or (latest_point[0] if latest_point else ""),
        "单位净值": round_or_none((nav_close or {}).get("单位净值")),
        "累计净值": round_or_none((nav_close or {}).get("累计净值")),
        "绩效样本收益率_百分比": sample_return,
        "绩效样本最大回撤_百分比": sample_risk.get("maxDrawdown"),
        "绩效样本年化波动率_百分比": sample_risk.get("volatility"),
        "绩效截止日期": end_date.isoformat(),
        "区间可计算状态JSON": "",
        "收益数据状态": "缺本地净值",
        "风险数据状态": "缺本地净值",
        "数据来源": "基金标准分类字典+基金信息+基金日度净值+F10业绩比较基准",
        "更新时间": generated_at,
    }
    statuses: dict[str, Any] = {}
    return_ok = 0
    risk_ok = 0
    for item in intervals:
        label = item["label"]
        start = item["start"]
        interval_end = item["end"]
        interval_return, return_status = calc_return(series, start, interval_end)
        risk = calc_risk(series, start, interval_end)
        complete_interval = interval_return is not None and return_status.get("status") == "完整区间"
        output[f"{label}收益率_百分比"] = interval_return
        output[f"{label}最大回撤_百分比"] = risk.get("maxDrawdown") if complete_interval else None
        output[f"{label}年化波动率_百分比"] = risk.get("volatility") if complete_interval else None
        statuses[label] = {
            "收益状态": return_status,
            "风险状态": {
                "status": risk.get("status"),
                "startDate": risk.get("startDate"),
                "endDate": risk.get("endDate"),
                "navPointCount": risk.get("navPointCount"),
            },
        }
        if interval_return is not None:
            return_ok += 1
        if complete_interval and (risk.get("maxDrawdown") is not None or risk.get("volatility") is not None):
            risk_ok += 1
    output["区间可计算状态JSON"] = json.dumps(statuses, ensure_ascii=False, separators=(",", ":"))
    if return_ok:
        output["收益数据状态"] = "有完整区间收益"
    elif series:
        output["收益数据状态"] = "净值起点晚于区间起点或点数不足"
    if risk_ok:
        output["风险数据状态"] = "有历史净值风险指标"
    elif series:
        output["风险数据状态"] = "净值起点晚于区间起点或点数不足"
    return output


def sqlite_type(column: str) -> str:
    if column == "基金代码":
        return "TEXT PRIMARY KEY"
    if column.startswith("是否") or column == "本地净值记录数":
        return "INTEGER"
    if column.endswith("_百分比") or column in {"单位净值", "累计净值"}:
        return "REAL"
    return "TEXT"


def write_snapshot(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise RuntimeError("公募基金产品绩效快照重建结果为空，保留原表并中止提交。")
    columns: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                columns.append(key)
    missing_columns = sorted(REQUIRED_SNAPSHOT_COLUMNS - seen)
    if missing_columns:
        raise RuntimeError(
            "公募基金产品绩效快照缺少必需字段，保留原表并中止提交："
            + "、".join(missing_columns)
        )
    conn.execute(f'DROP TABLE IF EXISTS "{TABLE_NAME}"')
    definitions = ", ".join(f'"{column}" {sqlite_type(column)}' for column in columns)
    conn.execute(f'CREATE TABLE "{TABLE_NAME}" ({definitions})')
    placeholders = ",".join("?" for _ in columns)
    quoted = ",".join(f'"{column}"' for column in columns)
    conn.executemany(
        f'INSERT INTO "{TABLE_NAME}" ({quoted}) VALUES ({placeholders})',
        [[row.get(column) for column in columns] for row in rows],
    )
    actual_columns = {
        row[1] for row in conn.execute(f'PRAGMA table_info("{TABLE_NAME}")').fetchall()
    }
    missing_after_write = sorted(REQUIRED_SNAPSHOT_COLUMNS - actual_columns)
    actual_rows = int(conn.execute(f'SELECT COUNT(*) FROM "{TABLE_NAME}"').fetchone()[0] or 0)
    if missing_after_write or actual_rows != len(rows):
        raise RuntimeError(
            "公募基金产品绩效快照写入后校验失败："
            f"missing_columns={missing_after_write}, expected_rows={len(rows)}, actual_rows={actual_rows}"
        )


def write_site_pack(site_dir: Path, rows: list[dict[str, Any]], summary: dict[str, Any]) -> dict[str, str]:
    data_dir = site_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "meta": {
            key: value
            for key, value in summary.items()
            if key not in {"snapshot_json", "output_dir"}
        },
        "rows": rows,
    }
    json_path = data_dir / "public_fund_performance_snapshot.json"
    js_path = data_dir / "public_fund_performance_snapshot.js"
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    json_path.write_text(encoded, encoding="utf-8")
    js_path.write_text(f"window.__PUBLIC_FUND_PERFORMANCE_SNAPSHOT__ = {encoded};\n", encoding="utf-8")
    return {"site_json": str(json_path), "site_js": str(js_path)}


def prepare_snapshot_artifacts(
    output_root: Path,
    rows: list[dict[str, Any]],
    summary: dict[str, Any],
) -> tuple[Path, Path, Path]:
    """Write and verify node artifacts before the database table is replaced.

    The daily node run directory is already unique, so compact names avoid the
    legacy Windows MAX_PATH boundary without losing traceability.  Artifact
    preparation deliberately precedes ``BEGIN IMMEDIATE``: an unwritable or
    overlong output path must leave the existing snapshot table untouched.
    """

    token = datetime.now().strftime("%Y%m%dT%H%M%S%f")
    output_dir = output_root / token
    snapshot_json = output_dir / "snapshot.json.gz"
    snapshot_temp = output_dir / "snapshot.json.gz.tmp"
    summary_json = output_dir / "summary.json"
    summary_temp = output_dir / "summary.json.tmp"
    candidate_paths = (snapshot_json, snapshot_temp, summary_json, summary_temp)
    if os.name == "nt" and max(len(str(path.resolve())) for path in candidate_paths) >= 248:
        raise RuntimeError(
            "公募基金绩效快照产物路径过长，数据库未修改："
            f"output_root={output_root}。请缩短节点输出目录。"
        )
    output_dir.mkdir(parents=True, exist_ok=False)
    try:
        with gzip.open(snapshot_temp, "wt", encoding="utf-8", compresslevel=6) as handle:
            json.dump(rows, handle, ensure_ascii=False, separators=(",", ":"))
        os.replace(snapshot_temp, snapshot_json)
        uncompressed_bytes = 0
        with gzip.open(snapshot_json, "rb") as handle:
            while chunk := handle.read(1024 * 1024):
                uncompressed_bytes += len(chunk)
        if uncompressed_bytes <= 2:
            raise RuntimeError("公募基金绩效快照压缩包内容为空，数据库未修改。")
        summary["output_dir"] = str(output_dir)
        summary["snapshot_json"] = str(snapshot_json)
        summary["snapshot_encoding"] = "gzip"
        summary["snapshot_compressed_bytes"] = snapshot_json.stat().st_size
        summary["snapshot_uncompressed_bytes"] = uncompressed_bytes
        summary_temp.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(summary_temp, summary_json)
        if not snapshot_json.is_file() or snapshot_json.stat().st_size <= 2:
            raise RuntimeError("公募基金绩效快照 GZIP 未完整落盘，数据库未修改。")
        if not summary_json.is_file() or summary_json.stat().st_size <= 2:
            raise RuntimeError("公募基金绩效快照摘要未完整落盘，数据库未修改。")
    except Exception:
        shutil.rmtree(output_dir, ignore_errors=True)
        raise
    return output_dir, snapshot_json, summary_json


def build_snapshot(args: argparse.Namespace) -> dict[str, Any]:
    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    conn = sqlite3.connect(args.db_path)
    conn.row_factory = sqlite3.Row
    rows_out: list[dict[str, Any]] = []
    output_dir: Path | None = None
    summary_json: Path | None = None
    try:
        end_date = date_or_none(args.end_date) or latest_nav_date(conn)
        intervals = interval_definitions(end_date)
        lower_bound = min(item["start"] for item in intervals) - timedelta(days=10)
        universe = load_fund_universe(conn)
        external_audit = load_external_fund_audit(conn)
        supplemental_metrics = load_supplemental_metrics(conn, end_date)
        for item in universe:
            code = clean(item.get("基金代码"))
            external = external_audit.get(code)
            external_benchmark = clean((external or {}).get("业绩比较基准"))
            if external_benchmark and external_benchmark != "无":
                item["业绩比较基准"] = external_benchmark
                item["业绩基准原始来源"] = "外部全部基金0630"
        codes = [clean(row.get("基金代码")) for row in universe if clean(row.get("基金代码"))]
        by_code = {clean(row.get("基金代码")): row for row in universe}
        for group in chunked(codes, max(1, args.chunk_size)):
            series_by_code = load_nav_series(conn, group, lower_bound, end_date)
            close_by_code = load_nav_close_rows(conn, group, end_date)
            for code in group:
                output = build_row(by_code[code], series_by_code.get(code, []), intervals, generated_at, end_date, close_by_code.get(code))
                output["业绩基准原始来源"] = clean(by_code[code].get("业绩基准原始来源")) or "F10/FOF F10"
                output["本地上半年收益率_百分比"] = output.get("上半年收益率_百分比")
                apply_supplemental_metrics(output, supplemental_metrics.get(code))
                apply_external_fund_audit(output, external_audit.get(code))
                rows_out.append(output)
        rows_out.sort(key=lambda item: item.get("基金代码") or "")
        summary = {
            "generated_at": generated_at,
            "db_path": str(args.db_path),
            "table": TABLE_NAME,
            "fund_count": len(rows_out),
            "nav_any_count": sum(1 for row in rows_out if int(row.get("本地净值记录数") or 0) > 0),
            "return_any_count": sum(1 for row in rows_out if row.get("收益数据状态") == "有完整区间收益"),
            "risk_any_count": sum(1 for row in rows_out if row.get("风险数据状态") == "有历史净值风险指标"),
            "benchmark_text_count": sum(1 for row in rows_out if clean(row.get("业绩比较基准"))),
            "benchmark_equity_bucket_count": sum(1 for row in rows_out if clean(row.get("基准风险资产权重"))),
            "benchmark_equity_bucket_source_counts": {},
        }
        source_counts: dict[str, int] = {}
        for row in rows_out:
            source = clean(row.get("基准风险资产权重来源")) or "缺失"
            source_counts[source] = source_counts.get(source, 0) + 1
        summary["benchmark_equity_bucket_source_counts"] = dict(sorted(source_counts.items()))
        output_dir, _snapshot_json, summary_json = prepare_snapshot_artifacts(
            args.output_root,
            rows_out,
            summary,
        )

        try:
            conn.execute("BEGIN IMMEDIATE")
            write_snapshot(conn, rows_out)
            conn.commit()
        except Exception:
            if conn.in_transaction:
                conn.rollback()
            shutil.rmtree(output_dir, ignore_errors=True)
            raise
    finally:
        conn.close()
    assert output_dir is not None and summary_json is not None
    if args.site_dir:
        summary.update(write_site_pack(args.site_dir, rows_out, summary))
        summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    args = parse_args()
    summary = build_snapshot(args)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
