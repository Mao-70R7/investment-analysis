from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from business_naming import canonical_advisor_institution, canonical_business_channel


PROJECT_ROOT = Path(os.environ.get("ADVISOR_CODE_ROOT") or Path.cwd()).resolve()
if not (PROJECT_ROOT / "AGENTS.md").is_file():
    raise RuntimeError("ADVISOR_CODE_ROOT or current working directory must be the code root containing AGENTS.md")
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
DEFAULT_SITE_DIR = PROJECT_ROOT / "site" / "basic_data"
OUTPUT_BASENAME = "advisor_fof_ranking_pack"
SUPPLEMENTAL_STRATEGY_CHANNELS = ("gfsec_fima",)


INTERVAL_DEFINITIONS = [
    ("上半年", "上半年收益", "h1"),
    ("今年以来", "年初以来收益", "ytd"),
    ("近1月", "近一个月收益", "1m"),
    ("近3月", "近三个月收益", "3m"),
    ("近6月", "近六个月收益", "6m"),
    ("近1年", "近一年收益", "1y"),
]


def clean_text(value: Any, fallback: str = "") -> str:
    if value is None:
        return fallback
    text = str(value).strip()
    return text if text else fallback


def as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    try:
        number = float(str(value).replace("%", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def round_or_none(value: Any, digits: int = 4) -> float | None:
    number = as_float(value)
    return round(number, digits) if number is not None else None


def benchmark_equity_bucket(value: Any) -> str:
    number = as_float(value)
    if number is None:
        return ""
    if number <= 0:
        return "L0"
    return f"L{min(10, max(1, math.ceil(number / 10)))}"


def benchmark_broad_equity_bucket(equity: Any, commodity: Any, alternative: Any, unknown: Any) -> tuple[float | None, str, str]:
    unknown_number = as_float(unknown) or 0.0
    if unknown_number > 0.01:
        return None, "", "基准未知权重超过0.01%，基准风险资产不硬分档。"
    numbers = [as_float(equity), as_float(commodity), as_float(alternative)]
    if all(value is None for value in numbers):
        return None, "", "缺少权益、商品、另类权重，无法计算基准风险资产权重。"
    broad = max(0.0, min(100.0, sum(value or 0.0 for value in numbers)))
    return round_or_none(broad), benchmark_equity_bucket(broad), "基准风险资产=基准权益+基准商品+基准另类；港股/海外权益是权益子项，不重复计入。"


def parse_date(value: Any) -> date | None:
    text = clean_text(value)
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def latest_source_json() -> Path:
    candidates = list((PROJECT_ROOT / "outputs" / "fof_benchmark_ranking").glob("*/fof_benchmark_classified_ranking_data.json"))
    if not candidates:
        raise FileNotFoundError("missing FOF benchmark ranking source JSON")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def write_js_assignment(path: Path, lhs: str, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{lhs} = {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))};\n", encoding="utf-8")


def chunked(values: list[str], size: int = 800) -> list[list[str]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def sum_numbers(*values: Any) -> float | None:
    numbers = [as_float(value) for value in values if as_float(value) is not None]
    if not numbers:
        return None
    return sum(numbers)


def confidence_score(label: Any) -> float | None:
    text = clean_text(label)
    return {
        "高": 0.9,
        "中": 0.6,
        "低": 0.35,
        "未解析": 0.0,
    }.get(text)


def fetch_strategy_asset_overrides(
    conn: sqlite3.Connection,
    strategy_ids: set[str],
) -> dict[str, dict[str, Any]]:
    if not strategy_ids:
        return {}
    output: dict[str, dict[str, Any]] = {}
    ordered_ids = sorted(str(item) for item in strategy_ids if clean_text(item))
    for group in chunked(ordered_ids):
        placeholders = ",".join("?" for _ in group)
        rows = conn.execute(
            f'''
            SELECT *
            FROM "策略基准资产配置"
            WHERE "统一策略ID" IN ({placeholders})
            ''',
            group,
        ).fetchall()
        for row in rows:
            item = dict(row)
            sid = clean_text(item.get("统一策略ID"))
            if not sid:
                continue
            equity = round_or_none(item.get("基准资产大类-权益"))
            bond = round_or_none(item.get("基准资产类别-债券"))
            cash = round_or_none(item.get("基准资产类别-现金"))
            commodity = round_or_none(item.get("基准资产类别-商品"))
            overseas = round_or_none(sum_numbers(item.get("基准资产类别-港股"), item.get("基准资产类别-海外权益")))
            unknown = round_or_none(first_present(item.get("基准资产大类-其他"), item.get("基准资产未映射权重")))
            bucket = clean_text(item.get("基准风险资产权重")) or benchmark_equity_bucket(equity)
            confidence = clean_text(item.get("基准映射置信度"), "未解析")
            output[sid] = {
                "策略基准风险资产权重": bucket,
                "基准风险资产权重": bucket,
                "策略基准权益权重_百分比": equity,
                "策略基准债券权重_百分比": bond,
                "策略基准货币权重_百分比": cash,
                "策略基准商品权重_百分比": commodity,
                "策略基准海外权重_百分比": overseas,
                "策略基准未知权重_百分比": unknown,
                "策略基准解析置信度": confidence,
                "策略基准解析置信度分数": confidence_score(confidence),
                "策略基准解析说明": clean_text(item.get("基准公式解析")),
            }
    return output


def apply_strategy_asset_overrides(
    rows: list[dict[str, Any]],
    overrides: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    updated_rows: list[dict[str, Any]] = []
    updated = 0
    for row in rows:
        sid = clean_text(row.get("统一策略ID"))
        override = overrides.get(sid)
        if override:
            next_row = {**row, **override}
            updated += 1
        else:
            next_row = dict(row)
        updated_rows.append(next_row)
    return updated_rows, updated


def fetch_supplemental_strategy_rows(
    conn: sqlite3.Connection,
    existing_ids: set[str],
) -> list[dict[str, Any]]:
    placeholders = ",".join("?" for _ in SUPPLEMENTAL_STRATEGY_CHANNELS)
    rows = conn.execute(
        f'''
        SELECT s."统一策略ID", s."渠道策略ID" AS "策略代码", s."策略名称",
               s."投顾机构", c."渠道名称" AS "渠道", s."风险等级",
               s."策略类型" AS "业务分类", s."业绩基准", s."策略状态",
               g."是否纳入常规排名", g."治理状态", g."分析分组",
               g."是否已停止", g."是否业绩异常"
        FROM "策略信息" s
        LEFT JOIN "渠道信息" c ON c."渠道ID" = s."渠道ID"
        LEFT JOIN "策略治理标签" g ON g."统一策略ID" = s."统一策略ID"
        WHERE s."渠道ID" IN ({placeholders})
        ORDER BY s."渠道ID", s."统一策略ID"
        ''',
        SUPPLEMENTAL_STRATEGY_CHANNELS,
    ).fetchall()
    result: list[dict[str, Any]] = []
    for source in rows:
        row = dict(source)
        strategy_id = clean_text(row.get("统一策略ID"))
        if not strategy_id or strategy_id in existing_ids:
            continue
        status = clean_text(row.get("策略状态")).lower()
        rankable = int(as_float(row.get("是否纳入常规排名")) or 0) == 1
        stopped = int(as_float(row.get("是否已停止")) or 0) == 1
        abnormal = int(as_float(row.get("是否业绩异常")) or 0) == 1
        if not rankable or status in {"terminated", "closed", "offline", "delisted"} or stopped or abnormal:
            continue
        row["是否对客"] = "是" if status in {"public", "active"} else "未披露"
        row["天天展示状态"] = clean_text(row.get("治理状态"), clean_text(row.get("策略状态")))
        result.append(row)
    return result


def fetch_nav_series(
    conn: sqlite3.Connection,
    *,
    table: str,
    id_col: str,
    date_col: str,
    nav_expr: str,
    ids: set[str],
    lower_bound: date,
    end_date: date,
) -> dict[str, list[tuple[str, float]]]:
    if not ids:
        return {}
    output: dict[str, dict[str, float]] = defaultdict(dict)
    ordered_ids = sorted(str(item) for item in ids if clean_text(item))
    for group in chunked(ordered_ids):
        placeholders = ",".join("?" for _ in group)
        sql = f"""
            SELECT entity_id, trade_date, MAX(nav) AS nav
            FROM (
                SELECT "{id_col}" AS entity_id,
                       "{date_col}" AS trade_date,
                       {nav_expr} AS nav
                FROM "{table}"
                WHERE "{id_col}" IN ({placeholders})
                  AND "{date_col}" >= ?
                  AND "{date_col}" <= ?
            )
            WHERE nav IS NOT NULL AND nav > 0
            GROUP BY entity_id, trade_date
            ORDER BY entity_id, trade_date
        """
        params = [*group, lower_bound.isoformat(), end_date.isoformat()]
        for entity_id, trade_date, nav in conn.execute(sql, params):
            nav_value = as_float(nav)
            if clean_text(entity_id) and clean_text(trade_date) and nav_value and nav_value > 0:
                output[str(entity_id)][str(trade_date)[:10]] = nav_value
    return {key: sorted(values.items()) for key, values in output.items()}


def value_on_or_before(series: list[tuple[str, float]], target: date) -> tuple[str, float] | None:
    target_text = target.isoformat()
    selected: tuple[str, float] | None = None
    for date_text, nav in series:
        if date_text <= target_text:
            selected = (date_text, nav)
        else:
            break
    return selected


def calc_interval_return(
    series: list[tuple[str, float]] | None,
    start_date: date,
    end_date: date,
) -> tuple[float | None, dict[str, str]]:
    if not series:
        return None, {"startDate": "", "endDate": ""}
    start = value_on_or_before(series, start_date)
    end = value_on_or_before(series, end_date)
    if not start or not end:
        return None, {"startDate": start[0] if start else "", "endDate": end[0] if end else ""}
    if start[1] <= 0 or end[1] <= 0 or end[0] < start[0]:
        return None, {"startDate": start[0], "endDate": end[0]}
    return round((end[1] / start[1] - 1.0) * 100.0, 4), {"startDate": start[0], "endDate": end[0]}


def calc_interval_risk(
    series: list[tuple[str, float]] | None,
    start_date: date,
    end_date: date,
) -> dict[str, Any]:
    empty = {"maxDrawdown": None, "volatility": None, "navPointCount": 0, "riskSource": "缺净值"}
    if not series:
        return empty
    start = value_on_or_before(series, start_date)
    if not start:
        return empty
    points: list[tuple[str, float]] = [start]
    for date_text, nav in series:
        if start[0] < date_text <= end_date.isoformat() and nav > 0:
            points.append((date_text, nav))
    dedup: dict[str, float] = {}
    for date_text, nav in points:
        if nav > 0:
            dedup[date_text] = nav
    ordered = sorted(dedup.items())
    if len(ordered) < 2:
        return {**empty, "navPointCount": len(ordered)}
    peak = ordered[0][1]
    max_drawdown = 0.0
    daily_returns: list[float] = []
    prev = ordered[0][1]
    for _, nav in ordered[1:]:
        if peak > 0:
            max_drawdown = min(max_drawdown, nav / peak - 1.0)
        peak = max(peak, nav)
        if prev > 0:
            daily_returns.append(nav / prev - 1.0)
        prev = nav
    volatility = None
    if len(daily_returns) >= 2:
        mean = sum(daily_returns) / len(daily_returns)
        variance = sum((value - mean) ** 2 for value in daily_returns) / (len(daily_returns) - 1)
        volatility = math.sqrt(variance) * math.sqrt(252) * 100.0
    return {
        "maxDrawdown": round(max_drawdown * 100.0, 4),
        "volatility": round(volatility, 4) if volatility is not None else None,
        "navPointCount": len(ordered),
        "riskSource": "本地净值",
    }


def interval_windows(end_date: date, h1_start: date) -> dict[str, tuple[date, date]]:
    h1_end = min(end_date, date(end_date.year, 6, 30))
    return {
        "上半年": (h1_start, h1_end),
        "今年以来": (date(end_date.year - 1, 12, 31), end_date),
        "近1月": (end_date - timedelta(days=30), end_date),
        "近3月": (end_date - timedelta(days=90), end_date),
        "近6月": (end_date - timedelta(days=183), end_date),
        "近1年": (end_date - timedelta(days=365), end_date),
    }


def table_has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    try:
        return column in {str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")')}
    except sqlite3.OperationalError:
        return False


def max_date_value(conn: sqlite3.Connection, table: str, column: str) -> date | None:
    if not table_has_column(conn, table, column):
        return None
    value = conn.execute(f'SELECT MAX("{column}") FROM "{table}"').fetchone()[0]
    return parse_date(value)


def resolve_end_date(conn: sqlite3.Connection, explicit_end_date: date | None = None) -> date:
    if explicit_end_date is not None:
        return explicit_end_date

    strategy_date = max_date_value(conn, "策略标准业绩净值", "交易日期")
    if strategy_date is None:
        strategy_date = max_date_value(conn, "策略日度业绩", "交易日期")
    # 排名收益和风险会直接按基金日度净值重算，因此应优先使用本次已刷新的
    # 净值水位。公募基金产品绩效快照可能按较低频率刷新，只能在日度净值
    # 完全不可用时作为兜底，否则会造成净值曲线已更新、区间收益仍停留在旧日。
    fund_date = max_date_value(conn, "基金日度净值", "交易日期")
    if fund_date is None:
        fund_date = max_date_value(conn, "公募基金产品绩效快照", "绩效截止日期")
    candidates = [value for value in (strategy_date, fund_date) if value is not None]
    if not candidates:
        raise RuntimeError("策略和基金均缺少有效业绩截止日，无法生成排名数据包。")
    return min(candidates)


def first_present(*values: Any, fallback: str = "") -> str:
    for value in values:
        text = clean_text(value)
        if text:
            return text
    return fallback


def is_guangfa(*values: Any) -> bool:
    return "广发" in " ".join(clean_text(value) for value in values)


def build_returns(
    series: list[tuple[str, float]] | None,
    windows: dict[str, tuple[date, date]],
    fallback_returns: dict[str, Any] | None = None,
    primary_source: str = "本地净值",
) -> tuple[dict[str, float | None], dict[str, dict[str, str]], dict[str, str]]:
    returns: dict[str, float | None] = {}
    ranges: dict[str, dict[str, str]] = {}
    sources: dict[str, str] = {}
    fallback_returns = fallback_returns or {}
    for label, _desc, _key in INTERVAL_DEFINITIONS:
        start_date, end_date = windows[label]
        value, date_range = calc_interval_return(series, start_date, end_date)
        source = primary_source
        fallback = round_or_none(fallback_returns.get(label))
        if value is None and fallback is not None:
            value = fallback
            source = "上游排行"
        returns[label] = value
        ranges[label] = date_range
        sources[label] = source if value is not None else "缺净值"
    return returns, ranges, sources


def build_risk_metrics(
    series: list[tuple[str, float]] | None,
    windows: dict[str, tuple[date, date]],
) -> dict[str, dict[str, Any]]:
    return {
        label: calc_interval_risk(series, windows[label][0], windows[label][1])
        for label, _desc, _key in INTERVAL_DEFINITIONS
    }


def build_risk_profile(row: dict[str, Any], *, prefix: str = "基准") -> dict[str, float | None]:
    return {
        "benchmarkEquityWeight": round_or_none(row.get(f"{prefix}权益权重_百分比")),
        "benchmarkBondWeight": round_or_none(row.get(f"{prefix}债券权重_百分比")),
        "benchmarkCashWeight": round_or_none(row.get(f"{prefix}货币权重_百分比")),
        "benchmarkCommodityWeight": round_or_none(row.get(f"{prefix}商品权重_百分比")),
        "benchmarkAlternativeWeight": round_or_none(row.get(f"{prefix}另类权重_百分比")),
        "benchmarkOverseasWeight": round_or_none(row.get(f"{prefix}海外权重_百分比")),
        "benchmarkUnknownWeight": round_or_none(row.get(f"{prefix}未知权重_百分比")),
    }


def row_valid_count(row: dict[str, Any]) -> int:
    returns = row.get("returns") or {}
    return sum(1 for label, _desc, _key in INTERVAL_DEFINITIONS if as_float(returns.get(label)) is not None)


def row_risk_valid_count(row: dict[str, Any]) -> int:
    risk_metrics = row.get("riskMetrics") or {}
    return sum(
        1
        for label, _desc, _key in INTERVAL_DEFINITIONS
        if as_float((risk_metrics.get(label) or {}).get("maxDrawdown")) is not None
    )


def build_strategy_row(
    row: dict[str, Any],
    nav_series: dict[str, list[tuple[str, float]]],
    windows: dict[str, tuple[date, date]],
    nav_sources: dict[str, str] | None = None,
) -> dict[str, Any]:
    strategy_id = clean_text(row.get("统一策略ID"))
    fallback_returns = {
        "上半年": row.get("策略H1收益率_百分比"),
    }
    series = nav_series.get(strategy_id)
    returns, ranges, sources = build_returns(
        series,
        windows,
        fallback_returns,
        (nav_sources or {}).get(strategy_id, "标准回放净值"),
    )
    risk_metrics = build_risk_metrics(series, windows)
    source_channel_id = strategy_id.split("__", 1)[0]
    institution = canonical_advisor_institution(row.get("投顾机构"))
    channel = canonical_business_channel(source_channel_id, row.get("渠道"))
    name = clean_text(row.get("策略名称"), strategy_id)
    benchmark_bucket = first_present(
        row.get("策略基准风险资产权重"),
        row.get("基准风险资产权重"),
        benchmark_equity_bucket(row.get("策略基准权益权重_百分比")),
        fallback="未分档",
    )
    risk_profile = build_risk_profile(row, prefix="策略基准")
    risk_profile["benchmarkEquityBucket"] = benchmark_bucket
    broad_weight, broad_bucket, broad_note = benchmark_broad_equity_bucket(
        risk_profile.get("benchmarkEquityWeight"),
        risk_profile.get("benchmarkCommodityWeight"),
        risk_profile.get("benchmarkAlternativeWeight"),
        risk_profile.get("benchmarkUnknownWeight"),
    )
    risk_profile["broadEquityWeight"] = broad_weight
    risk_profile["broadEquityBucket"] = broad_bucket
    risk_profile["broadEquityNote"] = broad_note
    return {
        "id": strategy_id,
        "entityType": "投顾策略",
        "code": clean_text(row.get("策略代码"), strategy_id),
        "name": name,
        "institution": institution,
        "channel": channel,
        "manager": "",
        "isCustomer": clean_text(row.get("是否对客"), "未披露"),
        "displayStatus": clean_text(row.get("天天展示状态")),
        "isGuangfa": is_guangfa(institution, channel, name),
        "rankingCategory": benchmark_bucket,
        "rankingCategoryBasis": "基准风险资产权重",
        "benchmarkEquityBucket": benchmark_bucket,
        "broadEquityBucket": broad_bucket,
        "broadEquityWeight": broad_weight,
        "broadEquityNote": broad_note,
        "fofPublicCategory": clean_text(row.get("FOF可比分类")),
        "fofBenchmarkCategory": clean_text(row.get("策略基准细分分类")),
        "parseConfidence": clean_text(row.get("策略基准解析置信度"), "未解析"),
        "parseConfidenceScore": round_or_none(row.get("策略基准解析置信度分数")),
        "riskLevel": clean_text(row.get("风险等级")),
        "businessCategory": clean_text(row.get("业务分类")),
        "benchmark": clean_text(row.get("业绩基准")),
        "returns": returns,
        "riskMetrics": risk_metrics,
        "riskProfile": risk_profile,
        "returnDateRanges": ranges,
        "returnSources": sources,
        "dataStatus": "可排名" if any(value is not None for value in returns.values()) else "缺收益区间",
        "detailUrl": f"./strategy.html?id={strategy_id}",
    }


def build_fof_row(
    row: dict[str, Any],
    nav_series: dict[str, list[tuple[str, float]]],
    windows: dict[str, tuple[date, date]],
) -> dict[str, Any]:
    fund_code = clean_text(row.get("基金代码"))
    fallback_returns = {
        "上半年": row.get("上半年收益率_百分比"),
        "今年以来": row.get("今年以来收益率_百分比"),
        "近1月": row.get("近1月收益率_百分比"),
        "近3月": row.get("近3月收益率_百分比"),
        "近6月": row.get("近6月收益率_百分比"),
        "近1年": row.get("近1年收益率_百分比"),
    }
    series = nav_series.get(fund_code)
    returns, ranges, sources = build_returns(series, windows, fallback_returns)
    risk_metrics = build_risk_metrics(series, windows)
    company = clean_text(row.get("基金公司"))
    name = clean_text(row.get("基金名称"), fund_code)
    benchmark_category = clean_text(row.get("FOF基准细分分类"))
    benchmark_bucket = first_present(
        row.get("基准风险资产权重"),
        row.get("FOF基准风险资产权重"),
        benchmark_equity_bucket(row.get("基准权益权重_百分比")),
        fallback="未分档",
    )
    risk_profile = build_risk_profile(row, prefix="基准")
    risk_profile["benchmarkEquityBucket"] = benchmark_bucket
    broad_weight, broad_bucket, broad_note = benchmark_broad_equity_bucket(
        risk_profile.get("benchmarkEquityWeight"),
        risk_profile.get("benchmarkCommodityWeight"),
        risk_profile.get("benchmarkAlternativeWeight"),
        risk_profile.get("benchmarkUnknownWeight"),
    )
    risk_profile["broadEquityWeight"] = broad_weight
    risk_profile["broadEquityBucket"] = broad_bucket
    risk_profile["broadEquityNote"] = broad_note
    return {
        "id": fund_code,
        "entityType": "FOF基金",
        "code": fund_code,
        "name": name,
        "institution": company,
        "channel": "天天基金FOF",
        "manager": clean_text(row.get("基金经理")),
        "isCustomer": "不适用",
        "displayStatus": clean_text(row.get("数据状态")),
        "isGuangfa": is_guangfa(company, name),
        "rankingCategory": benchmark_bucket,
        "rankingCategoryBasis": "基准风险资产权重",
        "benchmarkEquityBucket": benchmark_bucket,
        "broadEquityBucket": broad_bucket,
        "broadEquityWeight": broad_weight,
        "broadEquityNote": broad_note,
        "fofPublicCategory": clean_text(row.get("FOF公开分类"), clean_text(row.get("FOF可比分类"))),
        "fofBenchmarkCategory": benchmark_category,
        "parseConfidence": clean_text(row.get("解析置信度"), "未解析"),
        "parseConfidenceScore": round_or_none(row.get("解析置信度分数")),
        "riskLevel": "",
        "businessCategory": clean_text(row.get("天天基金细分类")),
        "benchmark": clean_text(row.get("业绩比较基准")),
        "returns": returns,
        "riskMetrics": risk_metrics,
        "riskProfile": risk_profile,
        "returnDateRanges": ranges,
        "returnSources": sources,
        "dataStatus": "可排名" if any(value is not None for value in returns.values()) else "缺收益区间",
        "detailUrl": f"./fund.html?code={fund_code}",
    }


def summarize_categories(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[clean_text(row.get("rankingCategory"), "未分类")].append(row)
    output: list[dict[str, Any]] = []
    for category, items in groups.items():
        output.append(
            {
                "分类": category,
                "产品数": len(items),
                "投顾策略数": sum(1 for row in items if row.get("entityType") == "投顾策略"),
                "FOF基金数": sum(1 for row in items if row.get("entityType") == "FOF基金"),
                "广发产品数": sum(1 for row in items if row.get("isGuangfa")),
                "有任一区间收益数": sum(1 for row in items if row_valid_count(row)),
                "有任一区间风险数": sum(1 for row in items if row_risk_valid_count(row)),
            }
        )
    return sorted(output, key=lambda row: (-row["产品数"], row["分类"]))


def interval_coverage(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for label, desc, key in INTERVAL_DEFINITIONS:
        valid_rows = [row for row in rows if as_float((row.get("returns") or {}).get(label)) is not None]
        output.append(
            {
                "key": key,
                "label": label,
                "description": desc,
                "validCount": len(valid_rows),
                "strategyValidCount": sum(1 for row in valid_rows if row.get("entityType") == "投顾策略"),
                "fofValidCount": sum(1 for row in valid_rows if row.get("entityType") == "FOF基金"),
            }
        )
    return output


def risk_coverage(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for label, desc, key in INTERVAL_DEFINITIONS:
        valid_rows = [
            row
            for row in rows
            if as_float(((row.get("riskMetrics") or {}).get(label) or {}).get("maxDrawdown")) is not None
        ]
        output.append(
            {
                "key": key,
                "label": label,
                "description": f"{desc}对应区间风险",
                "validCount": len(valid_rows),
                "strategyValidCount": sum(1 for row in valid_rows if row.get("entityType") == "投顾策略"),
                "fofValidCount": sum(1 for row in valid_rows if row.get("entityType") == "FOF基金"),
            }
        )
    return output


def build_pack(db_path: Path, source_json: Path, explicit_end_date: date | None = None) -> dict[str, Any]:
    source = load_json(source_json)
    meta = source.get("meta") or {}
    strategy_rows = source.get("strategyRows") or []
    fof_rows = source.get("fofRows") or []
    strategy_ids = {clean_text(row.get("统一策略ID")) for row in strategy_rows if clean_text(row.get("统一策略ID"))}
    fund_codes = {clean_text(row.get("基金代码")) for row in fof_rows if clean_text(row.get("基金代码"))}
    strategy_asset_override_count = 0
    supplemental_strategy_count = 0
    official_nav_fallback_count = 0
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        end_date = resolve_end_date(conn, explicit_end_date)
        source_h1_start = parse_date(meta.get("策略收益起始锚点")) or parse_date(meta.get("基金收益起始锚点"))
        h1_start = source_h1_start if source_h1_start and source_h1_start.year == end_date.year - 1 else date(end_date.year - 1, 12, 31)
        windows = interval_windows(end_date, h1_start)
        lower_bound = min(start for start, _ in windows.values()) - timedelta(days=45)
        supplemental_rows = fetch_supplemental_strategy_rows(conn, strategy_ids)
        if supplemental_rows:
            strategy_rows = [*strategy_rows, *supplemental_rows]
            supplemental_strategy_count = len(supplemental_rows)
            strategy_ids.update(
                clean_text(row.get("统一策略ID"))
                for row in supplemental_rows
                if clean_text(row.get("统一策略ID"))
            )
        strategy_asset_overrides = fetch_strategy_asset_overrides(conn, strategy_ids)
        strategy_rows, strategy_asset_override_count = apply_strategy_asset_overrides(strategy_rows, strategy_asset_overrides)
        standard_strategy_nav = fetch_nav_series(
            conn,
            table="策略标准业绩净值",
            id_col="统一策略ID",
            date_col="交易日期",
            nav_expr='"标准费后单位净值"',
            ids=strategy_ids,
            lower_bound=lower_bound,
            end_date=end_date,
        )
        official_strategy_nav = fetch_nav_series(
            conn,
            table="策略日度业绩",
            id_col="统一策略ID",
            date_col="交易日期",
            nav_expr='"单位净值"',
            ids=strategy_ids,
            lower_bound=lower_bound,
            end_date=end_date,
        )
        strategy_nav: dict[str, list[tuple[str, float]]] = {}
        strategy_nav_sources: dict[str, str] = {}
        for strategy_id in strategy_ids:
            standard_series = standard_strategy_nav.get(strategy_id) or []
            official_series = official_strategy_nav.get(strategy_id) or []
            if len(standard_series) >= 2:
                strategy_nav[strategy_id] = standard_series
                strategy_nav_sources[strategy_id] = "标准回放净值"
            elif len(official_series) >= 2:
                strategy_nav[strategy_id] = official_series
                strategy_nav_sources[strategy_id] = "官方披露净值"
                official_nav_fallback_count += 1
            elif standard_series:
                strategy_nav[strategy_id] = standard_series
                strategy_nav_sources[strategy_id] = "标准回放净值"
            elif official_series:
                strategy_nav[strategy_id] = official_series
                strategy_nav_sources[strategy_id] = "官方披露净值"
                official_nav_fallback_count += 1
        fof_nav = fetch_nav_series(
            conn,
            table="基金日度净值",
            id_col="基金代码",
            date_col="交易日期",
            nav_expr='COALESCE("累计净值","单位净值")',
            ids=fund_codes,
            lower_bound=lower_bound,
            end_date=end_date,
        )
    rows = [
        *(
            build_strategy_row(row, strategy_nav, windows, strategy_nav_sources)
            for row in strategy_rows
        ),
        *(build_fof_row(row, fof_nav, windows) for row in fof_rows),
    ]
    rows = sorted(rows, key=lambda row: (row["entityType"], row["rankingCategory"], row["institution"], row["name"], row["id"]))
    category_rows = summarize_categories(rows)
    coverage = interval_coverage(rows)
    risk_cov = risk_coverage(rows)
    type_options = [row["分类"] for row in category_rows]
    return {
        "meta": {
            "title": "投顾-FOF排名",
            "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
            "sourceJson": str(source_json),
            "dataUpdatedTo": end_date.isoformat(),
            "returnAnchorStart": h1_start.isoformat(),
            "intervalAsOfDates": {label: interval_end.isoformat() for label, (_start, interval_end) in windows.items()},
            "strategyCount": sum(1 for row in rows if row["entityType"] == "投顾策略"),
            "fofCount": sum(1 for row in rows if row["entityType"] == "FOF基金"),
            "totalCount": len(rows),
            "guangfaCount": sum(1 for row in rows if row["isGuangfa"]),
            "customerStrategyCount": sum(1 for row in rows if row["entityType"] == "投顾策略" and row["isCustomer"] == "是"),
            "categoryCount": len(category_rows),
            "strategyBenchmarkOverrideCount": strategy_asset_override_count,
            "supplementalStrategyCount": supplemental_strategy_count,
            "officialStrategyNavFallbackCount": official_nav_fallback_count,
            "intervals": [{"key": key, "label": label, "description": desc} for label, desc, key in INTERVAL_DEFINITIONS],
            "intervalCoverage": coverage,
            "riskCoverage": risk_cov,
            "classificationNote": (
                "投顾策略和FOF基金优先按业绩基准解析出的基准风险资产权重混排；"
                "策略标准回放净值缺失时，仅回退渠道官方披露净值，不使用推荐基金清单推算业绩。"
            ),
        },
        "typeOptions": type_options,
        "categoryRows": category_rows,
        "rows": rows,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build mixed advisor strategy and FOF ranking page pack.")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--source-json", type=Path, default=None)
    parser.add_argument("--site-dir", type=Path, default=DEFAULT_SITE_DIR)
    parser.add_argument("--end-date", type=date.fromisoformat, default=None, help="Optional common comparison cutoff; defaults to the latest common strategy/fund date.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    source_json = args.source_json or latest_source_json()
    pack = build_pack(args.db_path, source_json, args.end_date)
    data_dir = args.site_dir / "data"
    write_json(data_dir / f"{OUTPUT_BASENAME}.json", pack)
    write_js_assignment(data_dir / f"{OUTPUT_BASENAME}.js", "window.__ADVISOR_FOF_RANKING_PACK__", pack)
    print(
        json.dumps(
            {
                "输出目录": str(data_dir),
                "数据截至": pack["meta"]["dataUpdatedTo"],
                "投顾策略数": pack["meta"]["strategyCount"],
                "FOF基金数": pack["meta"]["fofCount"],
                "广发产品数": pack["meta"]["guangfaCount"],
                "分类数": pack["meta"]["categoryCount"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
