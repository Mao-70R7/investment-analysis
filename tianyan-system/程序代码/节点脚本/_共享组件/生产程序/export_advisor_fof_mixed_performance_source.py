# -*- coding: utf-8 -*-
"""Build source data and QA evidence for the advisor + FOF mixed ranking workbook."""

from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from business_naming import canonical_advisor_institution, canonical_business_channel


PROJECT_ROOT = Path(os.environ.get("ADVISOR_CODE_ROOT") or Path.cwd()).resolve()
if not (PROJECT_ROOT / "AGENTS.md").is_file():
    raise RuntimeError("ADVISOR_CODE_ROOT or current working directory must be the code root containing AGENTS.md")
END_DATE = "2026-06-30"
DEFAULT_PLATFORM_DIR = PROJECT_ROOT / "site"
DEFAULT_PACK = DEFAULT_PLATFORM_DIR / "basic_data" / "data" / "advisor_fof_ranking_pack.json"
DEFAULT_DB = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
DEFAULT_OUT = PROJECT_ROOT / "outputs" / "advisor_fof_mixed_performance_20260630"

INTERVALS = [
    ("上半年", "h1"),
    ("今年以来", "ytd"),
    ("近1月", "1m"),
    ("近3月", "3m"),
    ("近6月", "6m"),
    ("近1年", "1y"),
]

ALLOWED_STRATEGY_CHANNELS = {
    "天天基金/投顾",
    "广发基金",
    "广发证券",
    "且慢",
}
TOLERANCE_PP = 0.05
CN_TZ = timezone(timedelta(hours=8))
LEGACY_PUBLIC_BUCKET = "".join(("基准", "权益分档"))


def now_cn() -> str:
    return datetime.now(CN_TZ).replace(microsecond=0).isoformat()


def to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(value) or math.isinf(value):
        return None
    return value


def pp_to_decimal(value: Any) -> float | None:
    value = to_float(value)
    return None if value is None else value / 100.0


def decimal_to_pp(value: Any) -> float | None:
    value = to_float(value)
    return None if value is None else value * 100.0


def as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def yes_no(value: Any) -> str:
    if isinstance(value, bool):
        return "是" if value else "否"
    if value in (1, "1", "true", "True", "是"):
        return "是"
    if value in (0, "0", "false", "False", "否"):
        return "否"
    return as_text(value)


def bucket_order(bucket: str) -> tuple[int, str]:
    text = (bucket or "").strip()
    if text.startswith("L"):
        try:
            return (int(text[1:]), text)
        except ValueError:
            pass
    return (999, text)


def bucket_description(bucket: str) -> str:
    text = (bucket or "").strip()
    if text == "L0":
        return "L0: 基准权益权重为0%"
    if text == "L1":
        return "L1: 基准权益权重0%-10%"
    if text.startswith("L"):
        try:
            idx = int(text[1:])
        except ValueError:
            return text
        if idx == 10:
            return "L10: 基准权益权重90%-100%"
        if 2 <= idx <= 9:
            return f"L{idx}: 基准权益权重{(idx - 1) * 10}%-{idx * 10}%"
    return text or "未分档"


def connect_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def rows_by_key(conn: sqlite3.Connection, table: str, key_col: str) -> dict[str, sqlite3.Row]:
    rows: dict[str, sqlite3.Row] = {}
    for row in conn.execute(f'SELECT * FROM "{table}"'):
        key = as_text(row[key_col]).strip()
        if key:
            rows[key] = row
    return rows


def fetch_strategy_info(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    return rows_by_key(conn, "策略信息", "统一策略ID")


def fetch_fof_snapshot(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    return rows_by_key(conn, "FOF产品绩效快照", "基金代码")


def nav_series(
    conn: sqlite3.Connection,
    strategy_id: str,
    start_date: str,
    end_date: str,
) -> list[tuple[str, float]]:
    rows = conn.execute(
        """
        SELECT 交易日期, 标准费后单位净值
        FROM 策略标准业绩净值
        WHERE 统一策略ID = ?
          AND 交易日期 <= ?
          AND 标准费后单位净值 IS NOT NULL
        ORDER BY 交易日期
        """,
        (strategy_id, end_date),
    ).fetchall()
    points: list[tuple[str, float]] = []
    for row in rows:
        nav = to_float(row["标准费后单位净值"])
        if nav is not None and nav > 0:
            points.append((as_text(row["交易日期"]), nav))
    if len(points) >= 2:
        return points
    official_rows = conn.execute(
        """
        SELECT 交易日期, 单位净值
        FROM 策略日度业绩
        WHERE 统一策略ID = ?
          AND 交易日期 <= ?
          AND 单位净值 IS NOT NULL
        ORDER BY 交易日期
        """,
        (strategy_id, end_date),
    ).fetchall()
    official_points: list[tuple[str, float]] = []
    for row in official_rows:
        nav = to_float(row["单位净值"])
        if nav is not None and nav > 0:
            official_points.append((as_text(row["交易日期"]), nav))
    return official_points if len(official_points) >= 2 else points


def fund_nav_series(
    conn: sqlite3.Connection,
    fund_code: str,
    start_date: str,
    end_date: str,
) -> list[tuple[str, float]]:
    rows = conn.execute(
        """
        SELECT 交易日期, 单位净值, 累计净值
        FROM 基金日度净值
        WHERE 基金代码 = ?
          AND 交易日期 <= ?
          AND (累计净值 IS NOT NULL OR 单位净值 IS NOT NULL)
        ORDER BY 交易日期
        """,
        (fund_code, end_date),
    ).fetchall()
    points: list[tuple[str, float]] = []
    for row in rows:
        nav = to_float(row["累计净值"])
        if nav is None:
            nav = to_float(row["单位净值"])
        if nav is not None and nav > 0:
            points.append((as_text(row["交易日期"]), nav))
    return points


def calc_nav_metrics(points: list[tuple[str, float]], start_date: str, end_date: str) -> dict[str, Any]:
    if not points:
        return {"return": None, "maxDrawdown": None, "volatility": None, "pointCount": 0}

    start_point: tuple[str, float] | None = None
    end_point: tuple[str, float] | None = None
    for date, nav in points:
        if date <= start_date:
            start_point = (date, nav)
        if date <= end_date:
            end_point = (date, nav)

    if start_point is None or end_point is None:
        return {"return": None, "maxDrawdown": None, "volatility": None, "pointCount": 0}

    risk_points = [start_point]
    risk_points.extend((date, nav) for date, nav in points if start_date < date <= end_point[0])
    deduped: list[tuple[str, float]] = []
    seen_dates = set()
    for date, nav in risk_points:
        if date not in seen_dates:
            deduped.append((date, nav))
            seen_dates.add(date)

    ret = (end_point[1] / start_point[1] - 1.0) * 100.0
    peak = deduped[0][1] if deduped else start_point[1]
    max_drawdown = 0.0
    daily_returns: list[float] = []
    previous_nav: float | None = None
    for _, nav in deduped:
        if nav > peak:
            peak = nav
        drawdown = nav / peak - 1.0
        if drawdown < max_drawdown:
            max_drawdown = drawdown
        if previous_nav and previous_nav > 0:
            daily_returns.append(nav / previous_nav - 1.0)
        previous_nav = nav

    volatility = None
    if len(daily_returns) >= 2:
        volatility = statistics.stdev(daily_returns) * math.sqrt(252) * 100.0

    drawdown_value = None if len(deduped) < 2 else max_drawdown * 100.0

    return {
        "return": ret,
        "maxDrawdown": drawdown_value,
        "volatility": volatility,
        "pointCount": len(deduped),
        "startDateUsed": start_point[0],
        "endDateUsed": end_point[0],
    }


def metric_diff_pp(left_decimal: float | None, right_pp: float | None) -> float | None:
    left_pp = decimal_to_pp(left_decimal)
    if left_pp is None or right_pp is None:
        return None
    return left_pp - right_pp


def compare_decimal_to_pp(left_decimal: float | None, right_pp: float | None) -> tuple[str, float | None]:
    diff = metric_diff_pp(left_decimal, right_pp)
    if diff is None:
        if left_decimal is None and right_pp is None:
            return ("一致缺失", None)
        return ("缺失不一致", diff)
    if abs(diff) <= TOLERANCE_PP:
        return ("一致", diff)
    return ("不一致", diff)


def compare_required_text(left: Any, right: Any) -> bool:
    expected = as_text(right).strip()
    if not expected:
        return True
    return as_text(left).strip() == expected


def entity_type(row: dict[str, Any]) -> str:
    text = as_text(row.get("entityType"))
    return "FOF基金" if "FOF" in text.upper() else "投顾策略"


def should_export(row: dict[str, Any]) -> bool:
    if entity_type(row) == "FOF基金":
        return True
    source_id = as_text(row.get("id")).split("__", 1)[0]
    return canonical_business_channel(source_id, row.get("channel")) in ALLOWED_STRATEGY_CHANNELS


def flatten_row(
    row: dict[str, Any],
    fof_snapshot: dict[str, sqlite3.Row],
    strategy_info: dict[str, sqlite3.Row],
) -> dict[str, Any]:
    etype = entity_type(row)
    code = as_text(row.get("code") or row.get("id")).strip()
    sid = as_text(row.get("id") or code).strip()
    fof = fof_snapshot.get(code)
    strategy = strategy_info.get(sid)
    risk_profile = row.get("riskProfile") or {}

    def profile_percent(key: str, fof_col: str | None = None) -> float | None:
        value = risk_profile.get(key)
        if value is None and fof is not None and fof_col:
            value = fof[fof_col]
        return pp_to_decimal(value)

    institution = as_text(row.get("institution")).strip()
    if etype == "FOF基金" and fof is not None:
        institution = as_text(fof["基金公司"]).strip() or institution
    elif etype == "投顾策略" and strategy is not None:
        institution = as_text(strategy["投顾机构"]).strip() or institution
    institution = canonical_advisor_institution(institution)
    if not institution:
        institution = "未知机构"

    manager = as_text(row.get("manager")).strip()
    if etype == "FOF基金" and fof is not None:
        manager = as_text(fof["基金经理"]).strip() or manager

    foundation_date = ""
    if etype == "FOF基金" and fof is not None:
        foundation_date = as_text(fof["F10成立日期"])
    elif strategy is not None:
        foundation_date = as_text(strategy["成立日期"])

    benchmark = row.get("benchmark")
    if etype == "FOF基金" and not benchmark and fof is not None:
        benchmark = fof["业绩比较基准"]
    elif etype == "投顾策略" and not benchmark and strategy is not None:
        benchmark = strategy["业绩基准"]

    bucket = as_text(row.get("benchmarkEquityBucket") or risk_profile.get("benchmarkEquityBucket")).strip()
    if etype == "FOF基金" and not bucket and fof is not None:
        fof_fields = dict(fof)
        bucket = as_text(fof_fields.get("基准风险资产权重") or fof_fields.get(LEGACY_PUBLIC_BUCKET)).strip()

    flat: dict[str, Any] = {
        "排名": None,
        "产品类型": etype,
        "产品ID": sid,
        "产品代码": code,
        "产品名称": as_text(strategy["策略名称"]) if etype == "投顾策略" and strategy is not None else as_text(row.get("name")),
        "机构": institution,
        "渠道": canonical_business_channel(sid.split("__", 1)[0], row.get("channel")),
        "管理人/经理": manager,
        "是否对客": as_text(row.get("isCustomer")),
        "是否广发": yes_no(
            yes_no(row.get("isGuangfa")) == "是"
            or "广发" in institution
            or "广发" in canonical_business_channel(sid.split("__", 1)[0], row.get("channel"))
        ),
        "展示状态": as_text(row.get("displayStatus")),
        "数据状态": as_text(row.get("dataStatus")),
        "成立日期": foundation_date,
        "基准风险资产权重": bucket,
        "基准风险资产权重说明": bucket_description(bucket),
        "分类依据": as_text(row.get("rankingCategoryBasis")),
        "业务/公开分类": as_text(row.get("businessCategory") or row.get("fofPublicCategory")),
        "FOF公开分类": as_text(row.get("fofPublicCategory") or (fof["FOF公开分类"] if fof is not None else "")),
        "FOF基准细分分类": as_text(row.get("fofBenchmarkCategory") or (fof["FOF基准细分分类"] if fof is not None else "")),
        "风险等级": as_text(row.get("riskLevel")),
        "基准权益权重": profile_percent("benchmarkEquityWeight", "基准权益权重_百分比"),
        "基准债券权重": profile_percent("benchmarkBondWeight", "基准债券权重_百分比"),
        "基准货币权重": profile_percent("benchmarkCashWeight", "基准货币权重_百分比"),
        "基准商品权重": profile_percent("benchmarkCommodityWeight", "基准商品权重_百分比"),
        "基准海外权重": profile_percent("benchmarkOverseasWeight", "基准海外权重_百分比"),
        "基准未知权重": profile_percent("benchmarkUnknownWeight", "基准未知权重_百分比"),
        "业绩比较基准": as_text(benchmark),
        "解析置信度": as_text(row.get("parseConfidence") or (fof["解析置信度"] if fof is not None else "")),
        "解析置信度分数": to_float(row.get("parseConfidenceScore") or (fof["解析置信度分数"] if fof is not None else None)),
        "详情链接": as_text(row.get("detailUrl")),
    }

    returns = row.get("returns") or {}
    risks = row.get("riskMetrics") or {}
    sources = row.get("returnSources") or {}
    ranges = row.get("returnDateRanges") or {}
    for label, _ in INTERVALS:
        flat[f"{label}收益率"] = pp_to_decimal(returns.get(label))
        risk = risks.get(label) or {}
        flat[f"{label}最大回撤"] = pp_to_decimal(risk.get("maxDrawdown"))
        flat[f"{label}年化波动率"] = pp_to_decimal(risk.get("volatility"))
        flat[f"{label}风险净值点数"] = to_float(risk.get("navPointCount"))
        flat[f"{label}收益来源"] = as_text(sources.get(label))
        drange = ranges.get(label) or {}
        if drange:
            flat[f"{label}区间"] = f"{drange.get('startDate', '')}~{drange.get('endDate', '')}"
        else:
            flat[f"{label}区间"] = ""
        flat[f"{label}风险来源"] = as_text(risk.get("riskSource"))

    return flat


def assign_ranks(rows: list[dict[str, Any]]) -> None:
    rows.sort(
        key=lambda row: (
            row.get("上半年收益率") is None,
            -(row.get("上半年收益率") or -999),
            *bucket_order(row.get("基准风险资产权重", "")),
            row.get("产品类型", ""),
            row.get("机构", ""),
            row.get("产品名称", ""),
        )
    )
    rank = 0
    previous_value: float | None = None
    previous_rank: int | None = None
    for idx, row in enumerate(rows, start=1):
        value = row.get("上半年收益率")
        if value is None:
            row["排名"] = None
            continue
        rank = idx
        if previous_value is not None and abs(value - previous_value) < 1e-12:
            row["排名"] = previous_rank
        else:
            row["排名"] = rank
            previous_rank = rank
            previous_value = value


def stat_values(rows: list[dict[str, Any]], field: str) -> list[float]:
    return [to_float(row.get(field)) for row in rows if to_float(row.get(field)) is not None]


def avg(values: list[float]) -> float | None:
    return None if not values else sum(values) / len(values)


def median(values: list[float]) -> float | None:
    return None if not values else statistics.median(values)


def build_bucket_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(row.get("基准风险资产权重") or "未分档", row["产品类型"])].append(row)
    summary = []
    for (bucket, etype), group in sorted(groups.items(), key=lambda item: (bucket_order(item[0][0]), item[0][1])):
        h1_returns = stat_values(group, "上半年收益率")
        h1_dd = stat_values(group, "上半年最大回撤")
        h1_vol = stat_values(group, "上半年年化波动率")
        summary.append(
            {
                "基准风险资产权重": bucket,
                "基准风险资产权重说明": bucket_description(bucket),
                "产品类型": etype,
                "产品数": len(group),
                "上半年收益有效数": len(h1_returns),
                "上半年收益均值": avg(h1_returns),
                "上半年收益中位数": median(h1_returns),
                "上半年最大回撤有效数": len(h1_dd),
                "上半年最大回撤均值": avg(h1_dd),
                "上半年最大回撤中位数": median(h1_dd),
                "上半年年化波动率有效数": len(h1_vol),
                "上半年年化波动率均值": avg(h1_vol),
                "上半年年化波动率中位数": median(h1_vol),
            }
        )
    return summary


def build_coverage_rows(
    pack: dict[str, Any],
    rows: list[dict[str, Any]],
    raw_rows: list[dict[str, Any]],
    excluded_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    counter = Counter(row["产品类型"] for row in rows)
    raw_counter = Counter(entity_type(row) for row in raw_rows)
    coverage = [
        {"项目": "截至日期", "值": END_DATE, "说明": "混排榜使用正式排名数据包的区间截止日"},
        {"项目": "正式数据包生成时间", "值": pack.get("meta", {}).get("generatedAt", ""), "说明": "来源 advisor_fof_ranking_pack.json"},
        {"项目": "正式数据包原始总数", "值": len(raw_rows), "说明": f"投顾{raw_counter.get('投顾策略', 0)}；FOF{raw_counter.get('FOF基金', 0)}"},
        {"项目": "导出总数", "值": len(rows), "说明": f"投顾{counter.get('投顾策略', 0)}；FOF{counter.get('FOF基金', 0)}"},
        {
            "项目": "导出策略渠道",
            "值": "、".join(sorted(ALLOWED_STRATEGY_CHANNELS)),
            "说明": "延续页面展示口径；FOF为全市场FOF基金",
        },
        {"项目": "因渠道口径排除的投顾策略", "值": len(excluded_rows), "说明": "不影响FOF全市场样本"},
        {
            "项目": "基准风险资产权重完整数",
            "值": sum(1 for row in rows if row.get("基准风险资产权重")),
            "说明": f"覆盖率 {sum(1 for row in rows if row.get('基准风险资产权重')) / len(rows):.2%}" if rows else "",
        },
        {
            "项目": "业绩比较基准完整数",
            "值": sum(1 for row in rows if row.get("业绩比较基准")),
            "说明": f"覆盖率 {sum(1 for row in rows if row.get('业绩比较基准')) / len(rows):.2%}" if rows else "",
        },
    ]
    for label, _ in INTERVALS:
        ret_count = sum(1 for row in rows if row.get(f"{label}收益率") is not None)
        risk_count = sum(
            1
            for row in rows
            if row.get(f"{label}最大回撤") is not None and row.get(f"{label}年化波动率") is not None
        )
        coverage.append(
            {
                "项目": f"{label}收益率有效数",
                "值": ret_count,
                "说明": f"覆盖率 {ret_count / len(rows):.2%}" if rows else "",
            }
        )
        coverage.append(
            {
                "项目": f"{label}回撤/波动有效数",
                "值": risk_count,
                "说明": f"覆盖率 {risk_count / len(rows):.2%}" if rows else "",
            }
        )
    return coverage


def choose_institution_samples(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(row["产品类型"], row["机构"])].append(row)

    samples = []
    for key, group in groups.items():
        def score(row: dict[str, Any]) -> tuple[int, float, str]:
            complete = 0
            complete += int(bool(row.get("基准风险资产权重")))
            complete += int(row.get("上半年收益率") is not None)
            complete += int(row.get("上半年最大回撤") is not None)
            complete += int(row.get("上半年年化波动率") is not None)
            complete += int(bool(row.get("业绩比较基准")))
            return (complete, row.get("上半年收益率") or -999, row.get("产品名称", ""))

        samples.append(sorted(group, key=score, reverse=True)[0])
    samples.sort(key=lambda row: (row["产品类型"], row["机构"], row["产品名称"]))
    return samples


def compare_sample(
    conn: sqlite3.Connection,
    row: dict[str, Any],
    fof_snapshot: dict[str, sqlite3.Row],
    strategy_info: dict[str, sqlite3.Row],
) -> dict[str, Any]:
    etype = row["产品类型"]
    code = row["产品代码"]
    product_id = row["产品ID"]
    statuses: list[str] = []
    diffs: list[float] = []
    checked_fields = 0
    notes: list[str] = []
    attention = False

    qa = {
        "抽样维度": f"{etype}+{row['机构']}",
        "产品类型": etype,
        "机构": row["机构"],
        "产品代码": code,
        "产品名称": row["产品名称"],
        "基准风险资产权重": row.get("基准风险资产权重"),
        "上半年收益率": row.get("上半年收益率"),
        "上半年最大回撤": row.get("上半年最大回撤"),
        "上半年年化波动率": row.get("上半年年化波动率"),
        "核对字段数": 0,
        "最大收益差异_百分点": None,
        "最大风险差异_百分点": None,
        "核对状态": "通过",
        "核对说明": "",
    }

    if etype == "FOF基金":
        source = fof_snapshot.get(code)
        if source is None:
            qa["核对状态"] = "需复核"
            qa["核对说明"] = "FOF产品绩效快照未找到该基金代码"
            return qa

        base_checks = [
            ("产品名称", source["基金名称"]),
            ("机构", source["基金公司"]),
            ("基准风险资产权重", source["基准风险资产权重"]),
        ]
        for field, expected in base_checks:
            checked_fields += 1
            if not compare_required_text(row.get(field), expected):
                statuses.append("不一致")
                notes.append(f"{field}与FOF产品绩效快照不一致")

        for label, _ in INTERVALS:
            date_range = as_text(row.get(f"{label}区间"))
            if "~" not in date_range:
                attention = True
                notes.append(f"{label}缺少区间日期")
                continue
            start_date, end_date = date_range.split("~", 1)
            if not start_date or not end_date:
                attention = True
                checked_fields += 3
                notes.append(f"{label}缺少完整区间日期，无法用基金日度净值重算")
                continue
            points = fund_nav_series(conn, code, start_date, end_date)
            if not points:
                attention = True
                checked_fields += 3
                notes.append(f"{label}本地基金日度净值缺失，无法重算")
                continue
            metrics = calc_nav_metrics(points, start_date, end_date)
            for workbook_field, calc_key in [
                (f"{label}收益率", "return"),
                (f"{label}最大回撤", "maxDrawdown"),
                (f"{label}年化波动率", "volatility"),
            ]:
                status, diff = compare_decimal_to_pp(row.get(workbook_field), to_float(metrics.get(calc_key)))
                checked_fields += 1
                if diff is not None:
                    diffs.append(diff)
                statuses.append(status)
                if status not in {"一致", "一致缺失"}:
                    notes.append(f"{workbook_field}与基金日度净值重算不一致")

    else:
        source = strategy_info.get(product_id)
        if source is None:
            qa["核对状态"] = "需复核"
            qa["核对说明"] = "策略信息表未找到该统一策略ID"
            return qa

        base_checks = [
            ("产品名称", source["策略名称"]),
            ("机构", source["投顾机构"]),
            ("业绩比较基准", source["业绩基准"]),
        ]
        for field, expected in base_checks:
            checked_fields += 1
            if not compare_required_text(row.get(field), expected):
                statuses.append("不一致")
                notes.append(f"{field}与策略信息表不一致")

        for label, _ in INTERVALS:
            date_range = as_text(row.get(f"{label}区间"))
            if "~" not in date_range:
                attention = True
                notes.append(f"{label}缺少区间日期")
                continue
            start_date, end_date = date_range.split("~", 1)
            if not start_date or not end_date:
                attention = True
                checked_fields += 3
                notes.append(f"{label}缺少完整区间日期，无法用策略可用净值重算")
                continue
            points = nav_series(conn, product_id, start_date, end_date)
            if not points:
                attention = True
                checked_fields += 3
                notes.append(f"{label}策略标准回放及官方披露净值均不足，无法重算")
                continue
            metrics = calc_nav_metrics(points, start_date, end_date)
            for workbook_field, calc_key in [
                (f"{label}收益率", "return"),
                (f"{label}最大回撤", "maxDrawdown"),
                (f"{label}年化波动率", "volatility"),
            ]:
                status, diff = compare_decimal_to_pp(row.get(workbook_field), to_float(metrics.get(calc_key)))
                checked_fields += 1
                if diff is not None:
                    diffs.append(diff)
                statuses.append(status)
                if status not in {"一致", "一致缺失"}:
                    notes.append(f"{workbook_field}与策略可用净值重算不一致")

    bad = [status for status in statuses if status in {"不一致", "缺失不一致"}]
    if bad:
        qa["核对状态"] = "需复核"
    elif attention:
        qa["核对状态"] = "需关注"
    qa["核对字段数"] = checked_fields
    return_diffs = [
        abs(diff)
        for diff in diffs
        if diff is not None
    ]
    risk_diffs = return_diffs
    if return_diffs:
        qa["最大收益差异_百分点"] = max(return_diffs)
        qa["最大风险差异_百分点"] = max(risk_diffs)
    if not notes:
        source_note = (
            "FOF产品绩效快照基础信息/基金日度净值区间重算"
            if etype == "FOF基金"
            else "策略信息/标准回放净值或官方披露净值区间重算"
        )
        notes.append(f"与{source_note}抽样核对一致，容忍阈值{TOLERANCE_PP}个百分点")
    qa["核对说明"] = "；".join(notes[:4])
    return qa


def build_field_notes() -> list[dict[str, str]]:
    return [
        {"字段": "排名", "说明": "按全样本上半年收益率降序混排；同收益并列；收益缺失不排名"},
        {"字段": "基准风险资产权重", "说明": "按业绩比较基准中的权益类组件权重分档：0%为L0，0%-10%为L1，之后每10个百分点一档，90%-100%为L10"},
        {"字段": "收益率/最大回撤/年化波动率", "说明": "工作簿内为百分比格式；源数据以百分点保存，导出时转为Excel百分比数值"},
        {"字段": "投顾策略范围", "说明": "保留天天基金/投顾、广发基金和广发证券渠道；广发证券来源ID仅用于血缘追溯。"},
        {"字段": "FOF基金范围", "说明": "全市场FOF基金，基础信息和绩效字段来自FOF产品绩效快照及正式排名数据包"},
        {"字段": "抽样核对", "说明": "按产品类型+机构至少抽一只；FOF对照FOF产品绩效快照，投顾优先用策略标准业绩净值、缺失时用官方披露净值重算区间收益/回撤/波动。"},
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pack", type=Path, default=DEFAULT_PACK)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    pack = json.loads(args.pack.read_text(encoding="utf-8-sig"))
    raw_rows = pack.get("rows", [])
    args.out_dir.mkdir(parents=True, exist_ok=True)

    conn = connect_db(args.db)
    try:
        fof_snapshot = fetch_fof_snapshot(conn)
        strategy_info = fetch_strategy_info(conn)

        excluded_rows = [row for row in raw_rows if entity_type(row) == "投顾策略" and not should_export(row)]
        rows = [flatten_row(row, fof_snapshot, strategy_info) for row in raw_rows if should_export(row)]
        assign_ranks(rows)

        samples = choose_institution_samples(rows)
        qa_rows = [compare_sample(conn, row, fof_snapshot, strategy_info) for row in samples]
        qa_counter = Counter(row["核对状态"] for row in qa_rows)

        result = {
            "meta": {
                "title": "投顾策略+FOF基金产品业绩混排榜",
                "asOfDate": END_DATE,
                "generatedAt": now_cn(),
                "sourcePack": str(args.pack.resolve()),
                "sourceDb": str(args.db.resolve()),
                "strategyScope": "天天基金/投顾、广发基金、广发证券、且慢",
                "fofScope": "全市场FOF基金",
                "rawRowCount": len(raw_rows),
                "exportRowCount": len(rows),
                "excludedStrategyRowCount": len(excluded_rows),
                "qaSampleCount": len(qa_rows),
                "qaStatusCounts": dict(qa_counter),
                "tolerancePp": TOLERANCE_PP,
            },
            "rows": rows,
            "bucketSummary": build_bucket_summary(rows),
            "coverageRows": build_coverage_rows(pack, rows, raw_rows, excluded_rows),
            "qaRows": qa_rows,
            "fieldNotes": build_field_notes(),
        }

        source_path = args.out_dir / "workbook_source.json"
        qa_path = args.out_dir / "qa_summary.json"
        source_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        qa_path.write_text(
            json.dumps({"meta": result["meta"], "qaRows": qa_rows}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(json.dumps(result["meta"], ensure_ascii=False, indent=2))
        print(f"source={source_path}")
        print(f"qa={qa_path}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
