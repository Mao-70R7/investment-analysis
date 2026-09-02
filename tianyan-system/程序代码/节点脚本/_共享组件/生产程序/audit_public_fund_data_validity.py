#!/usr/bin/env python3
"""Audit public-fund benchmark and performance data used by mixed ranking outputs."""

from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import stdev
from typing import Any, Iterable


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_DB = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "public_fund_data_validity_audit"
CN_TZ = timezone(timedelta(hours=8))
BUCKETS = {f"L{index}" for index in range(11)}
ALLOWED_BUCKET_SOURCES = {
    "F10基准解析",
    "FOF基准解析",
    "年度披露权重核验",
    "未披露业绩基准",
    "未取得业绩基准",
    "业绩基准未解析",
}


def interval_ranges(end_date: date) -> dict[str, tuple[date, date]]:
    first_half_end = min(end_date, date(end_date.year, 6, 30))
    return {
        "上半年": (date(first_half_end.year - 1, 12, 31), first_half_end),
        "今年以来": (date(end_date.year - 1, 12, 31), end_date),
        "近1周": (end_date - timedelta(days=7), end_date),
        "近1月": (end_date - timedelta(days=30), end_date),
        "近3月": (end_date - timedelta(days=90), end_date),
        "近6月": (end_date - timedelta(days=183), end_date),
        "近1年": (end_date - timedelta(days=365), end_date),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit all-market public-fund mixed-ranking data validity.")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument(
        "--end-date",
        type=date.fromisoformat,
        default=None,
        help="Override the snapshot performance end date. Default reads MAX(绩效截止日期) from the database.",
    )
    parser.add_argument("--sample-per-company", type=int, default=1)
    parser.add_argument("--fail-on-error", action="store_true")
    return parser.parse_args()


def clean(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text in {"", "None", "null", "nan", "-", "--"} else text


def as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def expected_bucket(equity_pct: Any) -> str:
    value = as_float(equity_pct)
    if value is None:
        return ""
    if value <= 0:
        return "L0"
    return f"L{min(10, max(1, math.ceil(value / 10.0)))}"


def fund_main_type(row: sqlite3.Row | dict[str, Any]) -> str:
    if int(as_float(row["是否FOF"]) or 0):
        return "FOF"
    if int(as_float(row["是否QDII"]) or 0):
        return "QDII"
    if int(as_float(row["是否REITs"]) or 0):
        return "REITs"
    if int(as_float(row["是否商品黄金"]) or 0):
        return "商品黄金"
    if int(as_float(row["是否ETF"]) or 0):
        return "ETF"
    if int(as_float(row["是否LOF"]) or 0):
        return "LOF"
    if int(as_float(row["是否货币基金"]) or 0):
        return "货币"
    if int(as_float(row["是否债券基金"]) or 0):
        return "债券"
    if int(as_float(row["是否权益基金"]) or 0):
        return "权益"
    if int(as_float(row["是否混合基金"]) or 0):
        return "混合"
    return clean(row["标准资产大类"]) or clean(row["基金类型"]) or "其他"


def make_issue(
    issues: list[dict[str, Any]],
    rule_id: str,
    severity: str,
    count: int,
    detail: str,
    samples: Iterable[dict[str, Any]] = (),
) -> None:
    if count <= 0:
        return
    issues.append(
        {
            "ruleId": rule_id,
            "severity": severity,
            "count": count,
            "detail": detail,
            "samples": list(samples)[:50],
        }
    )


def query_rows(conn: sqlite3.Connection, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, tuple(params)).fetchall()]


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}


def audit_universe(conn: sqlite3.Connection, issues: list[dict[str, Any]]) -> dict[str, Any]:
    universe = query_rows(conn, 'SELECT * FROM "基金标准分类字典" ORDER BY "基金代码"')
    snapshot = query_rows(conn, 'SELECT * FROM "公募基金产品绩效快照" ORDER BY "基金代码"')
    universe_codes = [clean(row["基金代码"]) for row in universe]
    snapshot_codes = [clean(row["基金代码"]) for row in snapshot]
    universe_counter = Counter(universe_codes)
    snapshot_counter = Counter(snapshot_codes)
    universe_dupes = [code for code, count in universe_counter.items() if code and count > 1]
    snapshot_dupes = [code for code, count in snapshot_counter.items() if code and count > 1]
    missing_snapshot = sorted(set(universe_codes) - set(snapshot_codes))
    outside_universe = sorted(set(snapshot_codes) - set(universe_codes))
    missing_company = [row for row in universe if not clean(row["基金公司"])]
    invalid_code = [row for row in universe if len(clean(row["基金代码"])) != 6 or not clean(row["基金代码"]).isdigit()]
    make_issue(issues, "PUBLIC_FUND_UNIVERSE_DUPLICATE", "error", len(universe_dupes), "基金标准分类字典基金代码不唯一。", ({"基金代码": code} for code in universe_dupes))
    make_issue(issues, "PUBLIC_FUND_SNAPSHOT_DUPLICATE", "error", len(snapshot_dupes), "公募基金绩效快照基金代码不唯一。", ({"基金代码": code} for code in snapshot_dupes))
    make_issue(issues, "PUBLIC_FUND_SNAPSHOT_MISSING", "error", len(missing_snapshot), "标准基金母集中的基金未进入绩效快照。", ({"基金代码": code} for code in missing_snapshot))
    make_issue(issues, "PUBLIC_FUND_SNAPSHOT_OUTSIDE_UNIVERSE", "error", len(outside_universe), "绩效快照含标准基金母集之外的代码。", ({"基金代码": code} for code in outside_universe))
    make_issue(issues, "PUBLIC_FUND_COMPANY_MISSING", "error", len(missing_company), "标准基金母集仍有基金公司缺失。", missing_company)
    make_issue(issues, "PUBLIC_FUND_CODE_INVALID", "error", len(invalid_code), "标准基金代码不是六位数字。", invalid_code)
    return {
        "universeCount": len(universe),
        "universeDistinctCodeCount": len(set(universe_codes)),
        "snapshotCount": len(snapshot),
        "snapshotDistinctCodeCount": len(set(snapshot_codes)),
        "missingSnapshotCount": len(missing_snapshot),
        "outsideUniverseCount": len(outside_universe),
        "companyCoveredCount": len(universe) - len(missing_company),
    }


def audit_benchmarks(conn: sqlite3.Connection, issues: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows = query_rows(conn, 'SELECT * FROM "公募基金产品绩效快照" ORDER BY "基金代码"')
    invalid_bucket: list[dict[str, Any]] = []
    bucket_without_equity: list[dict[str, Any]] = []
    bucket_mismatch: list[dict[str, Any]] = []
    bucket_with_unknown: list[dict[str, Any]] = []
    bucket_without_benchmark: list[dict[str, Any]] = []
    fallback_rows: list[dict[str, Any]] = []
    invalid_source: list[dict[str, Any]] = []
    invalid_weight: list[dict[str, Any]] = []
    low_confidence_bucket: list[dict[str, Any]] = []
    text_count = bucket_count = unknown_count = 0
    source_counts: Counter[str] = Counter()
    confidence_counts: Counter[str] = Counter()
    unresolved_by_type: Counter[str] = Counter()
    unresolved_samples: list[dict[str, Any]] = []
    weight_fields = [
        "基准权益权重_百分比",
        "基准债券权重_百分比",
        "基准货币权重_百分比",
        "基准商品权重_百分比",
        "基准海外权重_百分比",
        "基准未知权重_百分比",
        "基准权重合计_百分比",
    ]
    for row in rows:
        code = clean(row["基金代码"])
        name = clean(row["基金名称"])
        benchmark = clean(row["业绩比较基准"])
        bucket = clean(row["基准风险资产权重"])
        source = clean(row["基准风险资产权重来源"])
        confidence = clean(row["基准映射置信度"])
        equity = as_float(row["基准权益权重_百分比"])
        unknown = as_float(row["基准未知权重_百分比"]) or 0.0
        sample = {
            "基金代码": code,
            "基金名称": name,
            "基金公司": clean(row["基金公司"]),
            "基金主类型": fund_main_type(row),
            "业绩比较基准": benchmark,
            "基准风险资产权重": bucket,
            "基准权益权重_百分比": equity,
            "基准未知权重_百分比": unknown,
            "基准风险资产权重来源": source,
            "基准映射置信度": confidence,
            "基准解析说明": clean(row["基准解析说明"]),
        }
        text_count += int(bool(benchmark))
        bucket_count += int(bool(bucket))
        unknown_count += int(unknown > 0.0001)
        source_counts[source or "<空>"] += 1
        confidence_counts[confidence or "<空>"] += 1
        if bucket and bucket not in BUCKETS:
            invalid_bucket.append(sample)
        if bucket and equity is None:
            bucket_without_equity.append(sample)
        if bucket and equity is not None and bucket != expected_bucket(equity):
            bucket_mismatch.append(sample)
        if bucket and unknown > 0.0001:
            bucket_with_unknown.append(sample)
        if bucket and not benchmark:
            bucket_without_benchmark.append(sample)
        if int(as_float(row["是否使用分类兜底"]) or 0) or "兜底" in source:
            fallback_rows.append(sample)
        if source not in ALLOWED_BUCKET_SOURCES:
            invalid_source.append(sample)
        if bucket and confidence in {"低", "未解析", "未披露", ""}:
            low_confidence_bucket.append(sample)
        for field in weight_fields:
            value = as_float(row[field])
            if value is not None and (value < -0.0001 or value > 100.0001):
                invalid_weight.append({**sample, "问题字段": field, "问题值": value})
        if not bucket:
            unresolved_by_type[fund_main_type(row)] += 1
            if len(unresolved_samples) < 200:
                unresolved_samples.append(sample)

    make_issue(issues, "PUBLIC_FUND_BUCKET_INVALID", "error", len(invalid_bucket), "基金基准风险资产权重必须为 L0-L10。", invalid_bucket)
    make_issue(issues, "PUBLIC_FUND_BUCKET_WITHOUT_EQUITY", "error", len(bucket_without_equity), "存在分档但没有可核验的基准权益权重。", bucket_without_equity)
    make_issue(issues, "PUBLIC_FUND_BUCKET_MISMATCH", "error", len(bucket_mismatch), "基准风险资产权重与权益权重不一致。", bucket_mismatch)
    make_issue(issues, "PUBLIC_FUND_BUCKET_WITH_UNKNOWN", "error", len(bucket_with_unknown), "基准仍有未知权重却输出了确定分档。", bucket_with_unknown)
    make_issue(issues, "PUBLIC_FUND_BUCKET_WITHOUT_BENCHMARK", "error", len(bucket_without_benchmark), "没有业绩比较基准原文却输出了分档。", bucket_without_benchmark)
    make_issue(issues, "PUBLIC_FUND_CLASSIFICATION_FALLBACK_USED", "error", len(fallback_rows), "基金类型或资产分类兜底被用于基准风险资产权重。", fallback_rows)
    make_issue(issues, "PUBLIC_FUND_BUCKET_SOURCE_INVALID", "error", len(invalid_source), "基准分档来源不在允许集合。", invalid_source)
    make_issue(issues, "PUBLIC_FUND_BENCHMARK_WEIGHT_INVALID", "error", len(invalid_weight), "基准资产权重超出 0%-100%。", invalid_weight)
    make_issue(issues, "PUBLIC_FUND_LOW_CONFIDENCE_BUCKET", "error", len(low_confidence_bucket), "低置信、未解析或未披露基准仍输出了确定分档。", low_confidence_bucket)
    make_issue(issues, "PUBLIC_FUND_BENCHMARK_UNRESOLVED", "warn", len(rows) - bucket_count, "未取得或未完整解析业绩基准的基金保持未分档，不进入分档比较。", unresolved_samples)
    return (
        {
            "benchmarkTextCoveredCount": text_count,
            "benchmarkTextCoverageRate": round(text_count / len(rows), 8) if rows else 0,
            "benchmarkBucketCoveredCount": bucket_count,
            "benchmarkBucketCoverageRate": round(bucket_count / len(rows), 8) if rows else 0,
            "benchmarkUnknownWeightCount": unknown_count,
            "classificationFallbackUsedCount": len(fallback_rows),
            "bucketSourceCounts": dict(source_counts),
            "benchmarkConfidenceCounts": dict(confidence_counts),
            "unresolvedByFundType": dict(unresolved_by_type),
        },
        unresolved_samples,
    )


def audit_nav_structure(conn: sqlite3.Connection, issues: list[dict[str, Any]]) -> dict[str, Any]:
    columns = table_columns(conn, "基金日度净值")
    has_adjusted = "复权净值" in columns
    row = conn.execute(
        f'''
        SELECT COUNT(*) AS row_count,
               COUNT(DISTINCT "基金代码") AS code_count,
               COUNT(DISTINCT CASE WHEN {('"复权净值" IS NOT NULL' if has_adjusted else '0')} THEN "基金代码" END) AS adjusted_code_count,
               SUM(CASE WHEN {('"复权净值" IS NOT NULL' if has_adjusted else '0')} THEN 1 ELSE 0 END) AS adjusted_row_count,
               SUM(CASE WHEN {('"复权净值" <= 0' if has_adjusted else '0')} THEN 1 ELSE 0 END) AS nonpositive_adjusted_count,
               MIN(CASE WHEN {('"复权净值" IS NOT NULL' if has_adjusted else '0')} THEN "交易日期" END) AS adjusted_start,
               MAX(CASE WHEN {('"复权净值" IS NOT NULL' if has_adjusted else '0')} THEN "交易日期" END) AS adjusted_end
        FROM "基金日度净值"
        '''
    ).fetchone()
    duplicates = query_rows(
        conn,
        '''
        SELECT "基金代码", "交易日期", COUNT(*) AS cnt
        FROM "基金日度净值"
        GROUP BY "基金代码", "交易日期"
        HAVING COUNT(*) > 1
        ORDER BY cnt DESC, "基金代码", "交易日期"
        ''',
    )
    nonpositive = query_rows(
        conn,
        '''
        SELECT "基金代码", "交易日期", "复权净值", "复权来源"
        FROM "基金日度净值"
        WHERE "复权净值" IS NOT NULL AND "复权净值" <= 0
        LIMIT 50
        ''',
    ) if has_adjusted else []
    make_issue(issues, "PUBLIC_FUND_NAV_DUPLICATE_DATE", "error", len(duplicates), "同一基金同一交易日存在多条净值，计算起点可能不确定。", duplicates)
    make_issue(issues, "PUBLIC_FUND_ADJUSTED_NAV_NONPOSITIVE", "error", int(row["nonpositive_adjusted_count"] or 0), "复权净值存在零值或负值。", nonpositive)

    basis_rows = query_rows(
        conn,
        '''
        SELECT d."基金代码", d."基金公司", d."标准资产大类", d."是否FOF", d."是否QDII", d."是否REITs",
               d."是否商品黄金", d."是否ETF", d."是否LOF", d."是否货币基金", d."是否债券基金",
               d."是否权益基金", d."是否混合基金",
               COUNT(n."交易日期") AS nav_rows,
               SUM(CASE WHEN n."复权净值" IS NOT NULL THEN 1 ELSE 0 END) AS adjusted_rows,
               SUM(CASE WHEN n."日收益率_百分比" IS NOT NULL THEN 1 ELSE 0 END) AS return_rows,
               SUM(CASE WHEN n."累计净值" IS NOT NULL OR n."单位净值" IS NOT NULL THEN 1 ELSE 0 END) AS proxy_rows
        FROM "基金标准分类字典" d
        LEFT JOIN "基金日度净值" n ON n."基金代码"=d."基金代码"
          AND n."交易日期">='2025-05-01' AND n."交易日期"<='2026-06-30'
        GROUP BY d."基金代码"
        ''',
    )
    basis_counts: Counter[str] = Counter()
    no_nav_samples: list[dict[str, Any]] = []
    for fund in basis_rows:
        adjusted_rows = int(fund["adjusted_rows"] or 0)
        return_rows = int(fund["return_rows"] or 0)
        proxy_rows = int(fund["proxy_rows"] or 0)
        if adjusted_rows > 0:
            basis = "直接复权净值"
        elif return_rows > 1:
            basis = "日收益率复合"
        elif proxy_rows > 1:
            basis = "累计/单位净值代理"
        else:
            basis = "无可用净值"
            if len(no_nav_samples) < 100:
                no_nav_samples.append({"基金代码": fund["基金代码"], "基金公司": fund["基金公司"], "基金主类型": fund_main_type(fund)})
        basis_counts[basis] += 1
    proxy_count = basis_counts["累计/单位净值代理"]
    make_issue(issues, "PUBLIC_FUND_NAV_PROXY_BASIS", "warn", proxy_count, "仅能使用累计/单位净值代理的基金需要避免把结果表述为严格复权收益。", (fund for fund in basis_rows if int(fund["adjusted_rows"] or 0) == 0 and int(fund["return_rows"] or 0) <= 1 and int(fund["proxy_rows"] or 0) > 1))
    make_issue(issues, "PUBLIC_FUND_NAV_UNAVAILABLE", "warn", basis_counts["无可用净值"], "目标区间没有可计算净值的基金保留空指标。", no_nav_samples)
    return {
        "navRowCount": int(row["row_count"] or 0),
        "navFundCount": int(row["code_count"] or 0),
        "adjustedNavFundCount": int(row["adjusted_code_count"] or 0),
        "adjustedNavRowCount": int(row["adjusted_row_count"] or 0),
        "adjustedNavStart": row["adjusted_start"],
        "adjustedNavEnd": row["adjusted_end"],
        "duplicateFundDateCount": len(duplicates),
        "performanceBasisCounts": dict(basis_counts),
    }


def select_accuracy_samples(conn: sqlite3.Connection, sample_per_company: int) -> list[dict[str, Any]]:
    rows = query_rows(
        conn,
        '''
        SELECT * FROM "公募基金产品绩效快照"
        WHERE "上半年收益率_百分比" IS NOT NULL
          AND "上半年最大回撤_百分比" IS NOT NULL
          AND "上半年年化波动率_百分比" IS NOT NULL
        ORDER BY "基金公司", "基金代码"
        ''',
    )
    selected: dict[str, dict[str, Any]] = {}
    company_counts: Counter[str] = Counter()
    type_seen: set[str] = set()
    source_seen: set[str] = set()
    for row in rows:
        code = clean(row["基金代码"])
        company = clean(row["基金公司"]) or "<未知机构>"
        main_type = fund_main_type(row)
        source = clean(row["基准风险资产权重来源"])
        if company_counts[company] < sample_per_company or main_type not in type_seen or source not in source_seen:
            selected[code] = row
            company_counts[company] += 1
            type_seen.add(main_type)
            source_seen.add(source)
    return list(selected.values())


def load_sample_nav(conn: sqlite3.Connection, codes: list[str], start_date: date, end_date: date) -> dict[str, list[tuple[str, float]]]:
    result: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for offset in range(0, len(codes), 400):
        chunk = codes[offset : offset + 400]
        placeholders = ",".join("?" for _ in chunk)
        rows = conn.execute(
            f'''
            SELECT "基金代码", "交易日期", "复权净值", "累计净值", "单位净值",
                   "日收益率_百分比", "是否货币基金"
            FROM "基金日度净值"
            WHERE "基金代码" IN ({placeholders})
              AND "交易日期">=? AND "交易日期"<=?
            ORDER BY "基金代码", "交易日期"
            ''',
            [*chunk, start_date.isoformat(), end_date.isoformat()],
        )
        synthetic: dict[str, float] = {}
        previous_proxy: dict[str, float] = {}
        for row in rows:
            code = clean(row["基金代码"])
            adjusted = as_float(row["复权净值"])
            proxy = as_float(row["累计净值"])
            if proxy is None:
                proxy = as_float(row["单位净值"])
            daily_return = as_float(row["日收益率_百分比"])
            is_money = int(as_float(row["是否货币基金"]) or 0) == 1
            if adjusted is not None:
                value = adjusted
            elif code not in synthetic:
                value = 100.0
            elif is_money and daily_return is not None and 1.0 + daily_return / 100.0 > 0:
                value = synthetic[code] * (1.0 + daily_return / 100.0)
            elif proxy is not None and previous_proxy.get(code):
                value = synthetic[code] * proxy / previous_proxy[code]
            else:
                value = synthetic[code]
            synthetic[code] = value
            if proxy is not None:
                previous_proxy[code] = proxy
            if value > 0:
                result[code].append((clean(row["交易日期"])[:10], value))
    return result


def value_on_or_before(series: list[tuple[str, float]], target: date) -> tuple[str, float] | None:
    target_text = target.isoformat()
    value = None
    for item in series:
        if item[0] <= target_text:
            value = item
        else:
            break
    return value


def independent_metrics(series: list[tuple[str, float]], start_date: date, end_date: date) -> tuple[float | None, float | None, float | None]:
    start = value_on_or_before(series, start_date)
    end = value_on_or_before(series, end_date)
    if not start or not end or end[0] <= start[0] or start[1] <= 0 or end[1] <= 0:
        return None, None, None
    dedup = sorted({trade_date: value for trade_date, value in series if start[0] <= trade_date <= end[0] and value > 0}.items())
    if len(dedup) < 2:
        return None, None, None
    ret = (end[1] / start[1] - 1.0) * 100.0
    peak = dedup[0][1]
    max_drawdown = 0.0
    daily_returns: list[float] = []
    business_span = 0
    previous_date = date.fromisoformat(dedup[0][0])
    previous_value = dedup[0][1]
    for trade_date, value in dedup[1:]:
        max_drawdown = min(max_drawdown, value / peak - 1.0)
        peak = max(peak, value)
        daily_returns.append(value / previous_value - 1.0)
        current_date = date.fromisoformat(trade_date)
        business_days = sum(1 for day in range(1, (current_date - previous_date).days + 1) if (previous_date + timedelta(days=day)).weekday() < 5)
        business_span += max(1, business_days)
        previous_date = current_date
        previous_value = value
    volatility = None
    if len(daily_returns) >= 2:
        volatility = stdev(daily_returns) * math.sqrt(len(daily_returns) * 252.0 / max(1, business_span)) * 100.0
    return ret, max_drawdown * 100.0, volatility


def snapshot_end_date(conn: sqlite3.Connection, requested: date | None) -> date:
    if requested is not None:
        return requested
    row = conn.execute(
        'SELECT MAX(substr("绩效截止日期",1,10)) FROM "公募基金产品绩效快照" '
        'WHERE "绩效截止日期" IS NOT NULL AND trim("绩效截止日期")<>""'
    ).fetchone()
    text = clean(row[0] if row else "")
    if not text:
        raise ValueError("公募基金产品绩效快照缺少可用绩效截止日期，无法执行独立重算")
    return date.fromisoformat(text[:10])


def row_snapshot_end_date(row: sqlite3.Row, fallback: date, *, use_row_date: bool) -> date:
    if not use_row_date:
        return fallback
    text = clean(row["绩效截止日期"])
    try:
        return date.fromisoformat(text[:10]) if text else fallback
    except ValueError:
        return fallback


def audit_metric_accuracy(
    conn: sqlite3.Connection,
    issues: list[dict[str, Any]],
    end_date: date,
    sample_per_company: int,
    *,
    use_row_end_date: bool = True,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    samples = select_accuracy_samples(conn, sample_per_company)
    codes = [clean(row["基金代码"]) for row in samples]
    earliest_start = min(start for start, _ in interval_ranges(end_date).values()) - timedelta(days=10)
    nav = load_sample_nav(conn, codes, earliest_start, end_date)
    qa_rows: list[dict[str, Any]] = []
    failures = 0
    for row in samples:
        code = clean(row["基金代码"])
        company = clean(row["基金公司"])
        main_type = fund_main_type(row)
        row_end_date = row_snapshot_end_date(row, end_date, use_row_date=use_row_end_date)
        for label, (start_date, interval_end_date) in interval_ranges(row_end_date).items():
            expected = independent_metrics(nav.get(code, []), start_date, interval_end_date)
            actual = (
                as_float(row[f"{label}收益率_百分比"]),
                as_float(row[f"{label}最大回撤_百分比"]),
                as_float(row[f"{label}年化波动率_百分比"]),
            )
            diffs = [None if left is None or right is None else abs(left - right) for left, right in zip(actual, expected)]
            passed = all(diff is None or diff <= 0.00001 for diff in diffs) and all((left is None) == (right is None) for left, right in zip(actual, expected))
            failures += int(not passed)
            qa_rows.append(
                {
                    "基金代码": code,
                    "基金名称": clean(row["基金名称"]),
                    "基金公司": company,
                    "基金主类型": main_type,
                    "区间": label,
                    "快照收益率_百分比": actual[0],
                    "重算收益率_百分比": expected[0],
                    "快照最大回撤_百分比": actual[1],
                    "重算最大回撤_百分比": expected[1],
                    "快照年化波动率_百分比": actual[2],
                    "重算年化波动率_百分比": expected[2],
                    "最大绝对差_百分点": max((diff for diff in diffs if diff is not None), default=0.0),
                    "核对结果": "通过" if passed else "失败",
                }
            )
    make_issue(issues, "PUBLIC_FUND_METRIC_RECALC_MISMATCH", "error", failures, "按机构、类型分层抽样后，快照指标与独立重算结果不一致。", (row for row in qa_rows if row["核对结果"] == "失败"))
    return (
        {
            "sampleFundCount": len(samples),
            "sampleCompanyCount": len({clean(row["基金公司"]) for row in samples}),
            "sampleFundTypeCount": len({fund_main_type(row) for row in samples}),
            "metricComparisonCount": len(qa_rows),
            "metricComparisonFailureCount": failures,
            "sampleFundTypes": dict(Counter(fund_main_type(row) for row in samples)),
        },
        qa_rows,
    )


def audit_snapshot_status(conn: sqlite3.Connection, issues: list[dict[str, Any]]) -> dict[str, Any]:
    rows = query_rows(conn, 'SELECT * FROM "公募基金产品绩效快照"')
    inconsistent: list[dict[str, Any]] = []
    outliers: list[dict[str, Any]] = []
    metric_fields = []
    for label in ["上半年", "今年以来", "近1周", "近1月", "近3月", "近6月", "近1年"]:
        metric_fields.extend([f"{label}收益率_百分比", f"{label}最大回撤_百分比", f"{label}年化波动率_百分比"])
    for row in rows:
        sample = {"基金代码": row["基金代码"], "基金名称": row["基金名称"], "基金公司": row["基金公司"], "基金主类型": fund_main_type(row)}
        h1_return = as_float(row["上半年收益率_百分比"])
        h1_drawdown = as_float(row["上半年最大回撤_百分比"])
        h1_vol = as_float(row["上半年年化波动率_百分比"])
        if h1_drawdown is not None and h1_drawdown > 0.000001:
            inconsistent.append({**sample, "问题": "最大回撤为正", "问题值": h1_drawdown})
        if h1_vol is not None and h1_vol < -0.000001:
            inconsistent.append({**sample, "问题": "波动率为负", "问题值": h1_vol})
        if h1_return is not None and clean(row["收益数据状态"]) == "缺本地净值":
            inconsistent.append({**sample, "问题": "缺本地净值但存在收益", "问题值": h1_return})
        for field in metric_fields:
            value = as_float(row[field])
            if value is None:
                continue
            if "收益率" in field and (value < -100.0001 or value > 1000):
                outliers.append({**sample, "问题字段": field, "问题值": value})
            elif "最大回撤" in field and (value < -100.0001 or value > 0.0001):
                outliers.append({**sample, "问题字段": field, "问题值": value})
            elif "波动率" in field and (value < -0.0001 or value > 500):
                outliers.append({**sample, "问题字段": field, "问题值": value})
    make_issue(issues, "PUBLIC_FUND_METRIC_STATUS_INCONSISTENT", "error", len(inconsistent), "指标符号或数据状态不一致。", inconsistent)
    make_issue(issues, "PUBLIC_FUND_METRIC_EXTREME_OUTLIER", "warn", len(outliers), "指标超出宽松合理范围，需要逐只复核净值或产品事件。", outliers)
    return {
        "returnAnyCount": sum(1 for row in rows if clean(row["收益数据状态"]) == "有完整区间收益"),
        "riskAnyCount": sum(1 for row in rows if clean(row["风险数据状态"]) == "有历史净值风险指标"),
        "h1ReturnCount": sum(1 for row in rows if row["上半年收益率_百分比"] is not None),
        "h1DrawdownCount": sum(1 for row in rows if row["上半年最大回撤_百分比"] is not None),
        "h1VolatilityCount": sum(1 for row in rows if row["上半年年化波动率_百分比"] is not None),
        "extremeOutlierCount": len(outliers),
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for field in row:
            if field not in seen:
                fields.append(field)
                seen.add(field)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_report(path: Path, summary: dict[str, Any]) -> None:
    coverage = summary["coverage"]
    benchmark = summary["benchmark"]
    nav = summary["nav"]
    metrics = summary["metrics"]
    qa = summary["accuracyQa"]
    lines = [
        "# 公募基金混排数据有效性专项稽核",
        "",
        f"- 生成时间：{summary['generatedAt']}",
        f"- 状态：{summary['status']}",
        f"- 错误：{summary['issueSummary']['error']}；告警：{summary['issueSummary']['warn']}",
        "",
        "## 覆盖率",
        "",
        f"- 标准基金母集：{coverage['universeCount']}；绩效快照：{coverage['snapshotCount']}；母集缺失：{coverage['missingSnapshotCount']}。",
        f"- 基金公司覆盖：{coverage['companyCoveredCount']}/{coverage['universeCount']}。",
        f"- 业绩基准文本：{benchmark['benchmarkTextCoveredCount']}/{coverage['universeCount']}（{benchmark['benchmarkTextCoverageRate']:.2%}）。",
        f"- 基准风险资产权重：{benchmark['benchmarkBucketCoveredCount']}/{coverage['universeCount']}（{benchmark['benchmarkBucketCoverageRate']:.2%}）。",
        f"- 有任一区间收益：{metrics['returnAnyCount']}；有风险指标：{metrics['riskAnyCount']}。",
        "",
        "## 口径",
        "",
        f"- 分类兜底用于分档：{benchmark['classificationFallbackUsedCount']}。",
        f"- 绩效基础：{json.dumps(nav['performanceBasisCounts'], ensure_ascii=False)}。",
        "- 收益使用目标期初日及以前最近一个净值点至期末日及以前最近一个净值点；风险在同一净值序列上计算最大回撤和按披露频率校正的年化波动率。",
        "",
        "## 准确性抽查",
        "",
        f"- 抽样基金：{qa['sampleFundCount']}；覆盖机构：{qa['sampleCompanyCount']}；覆盖基金类型：{qa['sampleFundTypeCount']}。",
        f"- 区间指标对比：{qa['metricComparisonCount']}；失败：{qa['metricComparisonFailureCount']}。",
        "",
        "## 问题",
        "",
    ]
    if summary["issues"]:
        for issue in summary["issues"]:
            lines.append(f"- [{issue['severity']}] {issue['ruleId']}：{issue['count']}；{issue['detail']}")
    else:
        lines.append("- 无问题。")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    run_id = datetime.now(CN_TZ).strftime("%Y%m%dT%H%M%S%z")
    output_dir = args.output_root / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    issues: list[dict[str, Any]] = []
    with sqlite3.connect(args.db_path) as conn:
        conn.row_factory = sqlite3.Row
        end_date = snapshot_end_date(conn, args.end_date)
        coverage = audit_universe(conn, issues)
        benchmark, unresolved_samples = audit_benchmarks(conn, issues)
        nav = audit_nav_structure(conn, issues)
        metrics = audit_snapshot_status(conn, issues)
        accuracy_qa, qa_rows = audit_metric_accuracy(
            conn,
            issues,
            end_date,
            args.sample_per_company,
            use_row_end_date=args.end_date is None,
        )
    issue_summary = Counter(issue["severity"] for issue in issues)
    status = "error" if issue_summary["error"] else "warn" if issue_summary["warn"] else "pass"
    summary = {
        "version": 1,
        "generatedAt": datetime.now(CN_TZ).replace(microsecond=0).isoformat(),
        "status": status,
        "dbPath": str(args.db_path),
        "endDate": end_date.isoformat(),
        "coverage": coverage,
        "benchmark": benchmark,
        "nav": nav,
        "metrics": metrics,
        "accuracyQa": accuracy_qa,
        "issueSummary": {"error": issue_summary["error"], "warn": issue_summary["warn"], "total": len(issues)},
        "issues": issues,
    }
    (output_dir / "audit_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report(output_dir / "audit_report.md", summary)
    write_csv(output_dir / "metric_recalculation_qa.csv", qa_rows)
    write_csv(output_dir / "unresolved_benchmark_samples.csv", unresolved_samples)
    issue_samples = [{"ruleId": issue["ruleId"], "severity": issue["severity"], **sample} for issue in issues for sample in issue["samples"]]
    write_csv(output_dir / "issue_samples.csv", issue_samples)
    print(json.dumps({"outputDir": str(output_dir), **summary}, ensure_ascii=False, indent=2))
    return 1 if args.fail_on_error and status == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
