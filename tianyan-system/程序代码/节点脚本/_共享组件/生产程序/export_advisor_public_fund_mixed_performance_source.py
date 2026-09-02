# -*- coding: utf-8 -*-
"""Build source data and QA evidence for the advisor + all public fund mixed ranking workbook."""

from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import statistics
from collections import Counter, defaultdict
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from benchmark_asset_classification import compute_benchmark_asset_mix, load_benchmark_catalog
from benchmark_comparison_pool import build_comparison_pool
from build_public_fund_performance_snapshot import calc_return, calc_risk, interval_definitions
from export_advisor_fof_mixed_performance_source import (
    ALLOWED_STRATEGY_CHANNELS,
    TOLERANCE_PP,
    as_text,
    calc_nav_metrics,
    compare_decimal_to_pp,
    compare_required_text,
    connect_db,
    decimal_to_pp,
    entity_type,
    flatten_row,
    fund_nav_series,
    nav_series,
    pp_to_decimal,
    should_export,
    to_float,
)


PROJECT_ROOT = Path(os.environ.get("ADVISOR_CODE_ROOT") or Path.cwd()).resolve()
if not (PROJECT_ROOT / "AGENTS.md").is_file():
    raise RuntimeError("ADVISOR_CODE_ROOT or current working directory must be the code root containing AGENTS.md")
END_DATE = ""
DEFAULT_PLATFORM_DIR = PROJECT_ROOT / "site"
DEFAULT_PACK = DEFAULT_PLATFORM_DIR / "basic_data" / "data" / "advisor_fof_ranking_pack.json"
DEFAULT_SUMMARY_CORE = DEFAULT_PLATFORM_DIR / "basic_data" / "data" / "basic_summary_core.js"
DEFAULT_DB = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
DEFAULT_OUT = DEFAULT_PLATFORM_DIR / "reports" / "advisor_public_fund_mixed_performance_latest"
LOOKBACK_BUFFER_DAYS = 45
MAX_OFFICIAL_PERFORMANCE_AGE_DAYS = 31
PEER_POOL_MIN_SIZE = 5
INTERVAL_LABELS = ["上半年", "今年以来", "近1月", "近3月", "近6月", "近1年"]
# Transitional read aliases for the currently deployed database. New tables and all outputs use the canonical names.
LEGACY_PUBLIC_BUCKET_SOURCE = "".join(("基准", "权益分档来源"))
LEGACY_STRATEGY_BUCKET = "".join(("基准", "权益分类档"))


def now_cn() -> str:
    return datetime.now(timezone(timedelta(hours=8))).replace(microsecond=0).isoformat()


def safe_div(left: float | None, right: float | None) -> float | None:
    if left is None or right in (None, 0):
        return None
    return left / right


def yes_no(value: Any) -> str:
    return "是" if int(to_float(value) or 0) == 1 else "否"


def confidence_score(label: Any, source: str = "") -> float | None:
    text = as_text(label).strip()
    if text == "高":
        return 0.9
    if text == "中":
        return 0.6
    if text == "低":
        return 0.35
    if source == "业绩基准未解析":
        return 0.1
    return None


def benchmark_equity_bucket(value: Any) -> str:
    number = to_float(value)
    if number is None:
        return ""
    if number <= 0:
        return "L0"
    return f"L{min(10, max(1, math.ceil(number / 10.0)))}"


def bucket_sort_key(bucket: str) -> tuple[float, str]:
    text = as_text(bucket).strip()
    if text.startswith("L"):
        try:
            return (float(text[1:]), text)
        except ValueError:
            pass
    match = __import__("re").match(r"^(\d+(?:\.\d+)?)\s*[-~至]", text)
    if match:
        return (float(match.group(1)) / 10.0, text)
    if text == "未分档":
        return (999.0, text)
    return (998.0, text)


def bucket_description(bucket: str) -> str:
    text = as_text(bucket).strip()
    if not text:
        return "未分档"
    if text == "L0":
        return "L0: 基准风险资产权重为0%"
    if text == "L1":
        return "L1: 基准风险资产权重0%-10%"
    if text.startswith("L"):
        try:
            idx = int(text[1:])
        except ValueError:
            return text
        if idx == 10:
            return "L10: 基准风险资产权重90%-100%"
        if 2 <= idx <= 9:
            return f"L{idx}: 基准风险资产权重{(idx - 1) * 10}%-{idx * 10}%"
    return f"{text}: 基准风险资产权重"


def fund_main_type(row: sqlite3.Row | dict[str, Any]) -> str:
    if int(to_float(row["是否FOF"]) or 0) == 1:
        return "FOF"
    if int(to_float(row["是否QDII"]) or 0) == 1:
        return "QDII"
    if int(to_float(row["是否REITs"]) or 0) == 1:
        return "REITs"
    if int(to_float(row["是否商品黄金"]) or 0) == 1:
        return "商品黄金"
    if int(to_float(row["是否ETF"]) or 0) == 1:
        return "ETF"
    if int(to_float(row["是否LOF"]) or 0) == 1:
        return "LOF"
    asset = as_text(row["标准资产大类"]).strip()
    if asset:
        return asset
    ftype = as_text(row["基金类型"]).strip()
    return ftype or "未分类"


def fund_type_tags(row: sqlite3.Row | dict[str, Any]) -> str:
    tags: list[str] = []
    for col, label in [
        ("是否FOF", "FOF"),
        ("是否QDII", "QDII"),
        ("是否ETF", "ETF"),
        ("是否ETF联接", "ETF联接"),
        ("是否LOF", "LOF"),
        ("是否REITs", "REITs"),
        ("是否商品黄金", "商品黄金"),
        ("是否货币基金", "货币"),
        ("是否债券基金", "债券"),
        ("是否权益基金", "权益"),
        ("是否混合基金", "混合"),
        ("是否指数基金", "指数"),
    ]:
        if int(to_float(row[col]) or 0) == 1:
            tags.append(label)
    return "、".join(tags) if tags else fund_main_type(row)


def normalize_fund_bucket(row: sqlite3.Row | dict[str, Any]) -> tuple[str, str]:
    item = dict(row)
    source = as_text(item.get("基准风险资产权重来源") or item.get(LEGACY_PUBLIC_BUCKET_SOURCE)).strip()
    unknown = to_float(row["基准未知权重_百分比"])
    if unknown is not None and unknown > 0.01:
        return "未分档", f"未分档；基准未知权重{unknown:.2f}%"
    if "兜底" in source:
        return "未分档", "未分档；旧分类兜底已停用"
    components = [
        to_float(item.get("基准权益权重_百分比")),
        to_float(item.get("基准商品权重_百分比")),
        to_float(item.get("基准另类权重_百分比")),
    ]
    bucket = benchmark_equity_bucket(sum(value or 0.0 for value in components)) if any(value is not None for value in components) else "未分档"
    return bucket, bucket_description(bucket) + (f"；来源={source}" if source else "")


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def load_basic_summary(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8-sig").strip()
    marker = "window.__BASIC_DATA__.summary = "
    if marker not in text:
        raise RuntimeError(f"基础数据摘要格式异常: {path}")
    return json.loads(text.split(marker, 1)[1].rstrip(";\r\n "))


def strategy_list_visible_ids(summary: dict[str, Any]) -> set[str]:
    visible: set[str] = set()
    for row in summary.get("strategies") or []:
        strategy_id = as_text(row.get("统一策略ID")).strip()
        if strategy_id:
            visible.add(strategy_id)
    return visible


def strategy_summary_rows_by_id(summary: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        strategy_id: row
        for row in summary.get("strategies") or []
        if (strategy_id := as_text(row.get("统一策略ID")).strip())
    }


def ranking_stub_from_summary(row: dict[str, Any]) -> dict[str, Any]:
    strategy_id = as_text(row.get("统一策略ID")).strip()
    stub = {
        "id": strategy_id,
        "code": strategy_id,
        "entityType": "投顾策略",
        "name": as_text(row.get("策略名称")),
        "institution": as_text(row.get("投顾机构")),
        "channel": as_text(row.get("渠道")),
        "有基准": as_text(row.get("有基准")),
        "有业绩走势": as_text(row.get("有业绩走势")),
        "有历史仓位": as_text(row.get("有历史仓位")),
        "对客未终止": as_text(row.get("对客未终止")),
    }
    if "是否纳入常规排名" in row:
        stub["pageRankable"] = row.get("是否纳入常规排名")
        stub["pageListOnly"] = row.get("仅列表展示")
        stub["pageLegacyArchive"] = row.get("是否历史接口留档")
        stub["pageGovernanceStatus"] = as_text(row.get("策略治理状态")).strip()
    return stub


def apply_page_governance_boundary(
    ranking_row: dict[str, Any],
    summary_row: dict[str, Any] | None,
) -> dict[str, Any]:
    result = dict(ranking_row)
    if not summary_row:
        return result
    summary_stub = ranking_stub_from_summary(summary_row)
    for key in (
        "pageRankable", "pageListOnly", "pageLegacyArchive", "pageGovernanceStatus",
        "有基准", "有业绩走势", "有历史仓位", "对客未终止",
    ):
        if key in summary_stub:
            result[key] = summary_stub[key]
    return result


def load_public_fund_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    if table_exists(conn, "基金主份额映射"):
        return conn.execute(
            '''
            SELECT s.*,
                   m."基金家族ID", m."主基金代码", m."主基金名称",
                   m."份额类别", m."计价币种", m."是否主份额",
                   m."合并份额代码JSON", m."合并份额数",
                   m."映射来源" AS "主份额映射来源",
                   m."映射置信度" AS "主份额映射置信度",
                   m."主份额选择规则"
            FROM "公募基金产品绩效快照" s
            JOIN "基金主份额映射" m ON m."基金代码" = s."基金代码"
            WHERE m."是否主份额" = 1
            ORDER BY s."基金代码"
            '''
        ).fetchall()
    return conn.execute(
        '''
        SELECT *, "基金代码" AS "主基金代码", "基金名称" AS "主基金名称",
               1 AS "是否主份额", 1 AS "合并份额数"
        FROM "公募基金产品绩效快照"
        ORDER BY "基金代码"
        '''
    ).fetchall()


def load_external_guangfa_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    if not table_exists(conn, "外部广发策略0630核对"):
        return []
    return [dict(row) for row in conn.execute('SELECT * FROM "外部广发策略0630核对" ORDER BY "策略代码"')]


def load_gffunds_strategy_refetch_rows(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    table = "广发策略源端收益复爬核对"
    if not table_exists(conn, table):
        return {}
    rows = conn.execute(f'SELECT * FROM "{table}" ORDER BY "策略代码"').fetchall()
    return {as_text(row["策略代码"]).strip().upper(): dict(row) for row in rows if as_text(row["策略代码"]).strip()}


def load_strategy_disclosed_nav(
    conn: sqlite3.Connection,
    strategy_codes: list[str],
    lower_bound: date,
    end_date: date,
) -> dict[str, list[tuple[str, float]]]:
    output: dict[str, list[tuple[str, float]]] = defaultdict(list)
    if not strategy_codes or not table_exists(conn, "策略产品披露净值"):
        return output
    for index in range(0, len(strategy_codes), 700):
        group = strategy_codes[index : index + 700]
        placeholders = ",".join("?" for _ in group)
        rows = conn.execute(
            f'''
            SELECT "渠道策略ID", "交易日期", "披露单位净值"
            FROM "策略产品披露净值"
            WHERE "渠道ID" = 'gffunds'
              AND "渠道策略ID" IN ({placeholders})
              AND "交易日期" >= ? AND "交易日期" <= ?
              AND "披露单位净值" IS NOT NULL
            ORDER BY "渠道策略ID", "交易日期"
            ''',
            [*group, lower_bound.isoformat(), end_date.isoformat()],
        ).fetchall()
        for row in rows:
            code = as_text(row["渠道策略ID"]).strip().upper()
            nav = to_float(row["披露单位净值"])
            if code and nav is not None and nav > 0:
                output[code].append((as_text(row["交易日期"])[:10], nav))
    return dict(output)


def disclosed_strategy_nav_series(
    conn: sqlite3.Connection,
    strategy_code: str,
    end_date: str,
) -> list[tuple[str, float]]:
    if not table_exists(conn, "策略产品披露净值"):
        return []
    rows = conn.execute(
        '''
        SELECT "交易日期", "披露单位净值"
        FROM "策略产品披露净值"
        WHERE "渠道ID" = 'gffunds' AND "渠道策略ID" = ?
          AND "交易日期" <= ? AND "披露单位净值" IS NOT NULL
        ORDER BY "交易日期"
        ''',
        (strategy_code, end_date),
    ).fetchall()
    return [
        (as_text(item["交易日期"])[:10], float(item["披露单位净值"]))
        for item in rows
        if to_float(item["披露单位净值"]) is not None and float(item["披露单位净值"]) > 0
    ]


def load_strategy_governance(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    try:
        rows = conn.execute('SELECT * FROM "策略治理标签"').fetchall()
    except sqlite3.OperationalError:
        return {}
    output: dict[str, dict[str, Any]] = {}
    for row in rows:
        item = dict(row)
        sid = as_text(item.get("统一策略ID")).strip()
        if sid:
            output[sid] = item
    return output


def load_strategy_official_performance_dates(conn: sqlite3.Connection) -> dict[str, str]:
    output: dict[str, str] = {}
    for table in ["策略日度业绩", "策略产品披露净值"]:
        try:
            rows = conn.execute(
                f'SELECT "统一策略ID", MAX("交易日期") AS max_date FROM "{table}" GROUP BY "统一策略ID"'
            ).fetchall()
        except sqlite3.OperationalError:
            continue
        for row in rows:
            sid = as_text(row["统一策略ID"]).strip()
            max_date = as_text(row["max_date"]).strip()[:10]
            if sid and max_date and max_date > output.get(sid, ""):
                output[sid] = max_date
    return output


def load_invalid_strategy_performance_ids(conn: sqlite3.Connection) -> set[str]:
    try:
        rows = conn.execute(
            '''
            SELECT DISTINCT "统一策略ID"
            FROM "策略日度业绩"
            WHERE "单位净值" <= 0 OR ABS("日收益率_百分比") > 50
            '''
        ).fetchall()
    except sqlite3.OperationalError:
        return set()
    return {as_text(row["统一策略ID"]).strip() for row in rows if as_text(row["统一策略ID"]).strip()}


def strategy_row_id(row: dict[str, Any]) -> str:
    return as_text(row.get("id") or row.get("code")).strip()


def strategy_exclusion_reason(
    row: dict[str, Any],
    strategy_governance: dict[str, dict[str, Any]],
    official_performance_dates: dict[str, str],
    invalid_performance_ids: set[str],
    end_date: date,
) -> str:
    if not should_export(row):
        return "渠道不在导出口径"
    sid = strategy_row_id(row)
    # The page summary is the final strategy-governance boundary.  A stub built
    # from that summary must preserve list-only / legacy exclusions even when
    # the older governance table has not yet been backfilled for the strategy.
    if "pageRankable" in row and int(to_float(row.get("pageRankable")) or 0) != 1:
        page_status = as_text(row.get("pageGovernanceStatus")).strip()
        if page_status:
            return page_status
        if int(to_float(row.get("pageLegacyArchive")) or 0) == 1:
            return "历史接口留档"
        if int(to_float(row.get("pageListOnly")) or 0) == 1:
            return "仅列表展示，不纳入常规排名"
        return "页面治理规则不纳入常规排名"
    if sid in invalid_performance_ids:
        return "官方业绩曲线异常；存在非正净值或绝对日收益超过50%的异常点"
    latest_official = official_performance_dates.get(sid, "")
    if not latest_official:
        return "无官方披露业绩"
    cutoff = end_date - timedelta(days=MAX_OFFICIAL_PERFORMANCE_AGE_DAYS)
    if date.fromisoformat(latest_official) < cutoff:
        return f"已清盘/业绩停更；官方最新业绩日期={latest_official}"
    governance = strategy_governance.get(sid)
    if governance and int(to_float(governance.get("是否纳入常规排名")) or 0) != 1:
        governance_status = as_text(governance.get("治理状态")).strip()
        return governance_status or "治理规则不纳入常规排名"
    return ""


def should_export_strategy(
    row: dict[str, Any],
    strategy_governance: dict[str, dict[str, Any]],
    official_performance_dates: dict[str, str],
    invalid_performance_ids: set[str],
    end_date: date,
) -> bool:
    return not strategy_exclusion_reason(row, strategy_governance, official_performance_dates, invalid_performance_ids, end_date)


def partition_missing_visible_strategies(
    missing_visible_ids: list[str],
    summary_strategy_by_id: dict[str, dict[str, Any]],
    strategy_governance: dict[str, dict[str, Any]],
    official_performance_dates: dict[str, str],
    invalid_performance_ids: set[str],
    end_date: date,
) -> tuple[list[dict[str, str]], list[str]]:
    expected_nonrankable: list[dict[str, str]] = []
    unexpected_rankable: list[str] = []
    for strategy_id in missing_visible_ids:
        summary_row = summary_strategy_by_id.get(strategy_id, {})
        stub = ranking_stub_from_summary(summary_row)
        reason = strategy_exclusion_reason(
            stub,
            strategy_governance,
            official_performance_dates,
            invalid_performance_ids,
            end_date,
        )
        if not reason:
            unexpected_rankable.append(strategy_id)
            continue
        expected_nonrankable.append(
            {
                "产品代码": strategy_id,
                "产品名称": as_text(summary_row.get("策略名称")),
                "机构": as_text(summary_row.get("投顾机构")),
                "渠道": as_text(summary_row.get("渠道")),
                "最新业绩日期": official_performance_dates.get(strategy_id, ""),
                "生命周期状态": as_text(strategy_governance.get(strategy_id, {}).get("治理状态")),
                "剔除原因": f"策略列表可见但不具备混排资格；{reason}",
            }
        )
    return expected_nonrankable, unexpected_rankable


def load_public_nav_series(
    conn: sqlite3.Connection,
    codes: list[str],
    lower_bound: date,
    end_date: date,
    chunk_size: int = 700,
) -> dict[str, list[tuple[str, float]]]:
    output: dict[str, list[tuple[str, float]]] = defaultdict(list)
    columns = {row[1] for row in conn.execute('PRAGMA table_info("基金日度净值")')}
    adjusted_expr = '"复权净值"' if "复权净值" in columns else 'NULL AS "复权净值"'
    for index in range(0, len(codes), chunk_size):
        group = codes[index : index + chunk_size]
        placeholders = ",".join("?" for _ in group)
        rows = conn.execute(
            f'''
            SELECT "基金代码", "交易日期", "单位净值", "累计净值", "日收益率_百分比", "是否货币基金", {adjusted_expr}
            FROM "基金日度净值"
            WHERE "基金代码" IN ({placeholders})
              AND "交易日期" >= ?
              AND "交易日期" <= ?
            ORDER BY "基金代码", "交易日期"
            ''',
            [*group, lower_bound.isoformat(), end_date.isoformat()],
        ).fetchall()
        synthetic: dict[str, float] = {}
        previous_proxy: dict[str, float] = {}
        for row in rows:
            code = as_text(row["基金代码"]).strip()
            adjusted = to_float(row["复权净值"])
            proxy = to_float(row["累计净值"])
            if proxy is None:
                proxy = to_float(row["单位净值"])
            daily = to_float(row["日收益率_百分比"])
            is_money = int(to_float(row["是否货币基金"]) or 0) == 1
            if adjusted is not None:
                nav = adjusted
                synthetic[code] = nav
            else:
                previous = synthetic.get(code)
                if previous is None:
                    nav = 100.0
                elif is_money and daily is not None and 1.0 + daily / 100.0 > 0:
                    nav = previous * (1.0 + daily / 100.0)
                else:
                    prior_proxy = previous_proxy.get(code)
                    nav = previous * (proxy / prior_proxy) if proxy and prior_proxy else previous
                synthetic[code] = nav
            if proxy is not None:
                previous_proxy[code] = proxy
            if nav is not None and nav > 0:
                output[code].append((as_text(row["交易日期"]), nav))
    return dict(output)


def build_interval_metrics(series: list[tuple[str, float]], end_date: date) -> dict[str, Any]:
    intervals = [item for item in interval_definitions(end_date) if item["label"] in INTERVAL_LABELS]
    metrics: dict[str, Any] = {}
    for item in intervals:
        label = item["label"]
        ret_pp, ret_status = calc_return(series, item["start"], item["end"])
        risk = calc_risk(series, item["start"], item["end"])
        complete_interval = ret_pp is not None and ret_status.get("status") == "完整区间"
        metrics[f"{label}收益率"] = pp_to_decimal(ret_pp)
        metrics[f"{label}最大回撤"] = pp_to_decimal(risk.get("maxDrawdown")) if complete_interval else None
        metrics[f"{label}年化波动率"] = pp_to_decimal(risk.get("volatility")) if complete_interval else None
        metrics[f"{label}风险净值点数"] = to_float(risk.get("navPointCount"))
        start = (ret_status or {}).get("startDate") or risk.get("startDate")
        end = (ret_status or {}).get("endDate") or risk.get("endDate")
        metrics[f"{label}区间"] = f"{start}~{end}" if start and end else ""
        metrics[f"{label}收益来源"] = "本地净值" if ret_pp is not None else "缺净值"
        metrics[f"{label}风险来源"] = "本地净值" if complete_interval and (risk.get("maxDrawdown") is not None or risk.get("volatility") is not None) else "缺完整区间"
    return metrics


def flatten_public_fund_row(row: sqlite3.Row, nav_series: list[tuple[str, float]], end_date: date) -> dict[str, Any]:
    item = dict(row)
    code = as_text(item.get("基金代码")).strip()
    bucket, bucket_desc = normalize_fund_bucket(item)
    source = as_text(item.get("基准风险资产权重来源") or item.get(LEGACY_PUBLIC_BUCKET_SOURCE)).strip()
    data_status = as_text(item.get("收益数据状态")).strip() or ("可排名" if nav_series else "缺本地净值")
    comparison = build_comparison_pool(
        bucket=bucket,
        equity=to_float(item.get("基准权益权重_百分比")),
        bond=to_float(item.get("基准债券权重_百分比")),
        cash=to_float(item.get("基准货币权重_百分比")),
        commodity=to_float(item.get("基准商品权重_百分比")),
        alternative=to_float(item.get("基准另类权重_百分比")),
        unknown=to_float(item.get("基准未知权重_百分比")),
    )
    output: dict[str, Any] = {
        "排名": None,
        "绝对收益排名": None,
        "同档混排排名": None,
        "同类可比排名": None,
        "同类可比样本数": None,
        "同类前25%": "",
        "产品类型": "公募基金",
        "基金主类型": fund_main_type(item),
        "基金类型标签": fund_type_tags(item),
        "产品ID": code,
        "产品代码": code,
        "产品名称": as_text(item.get("基金名称")),
        "机构": as_text(item.get("基金公司")) or "未知基金公司",
        "渠道": "天天基金/公募基金",
        "管理人/经理": as_text(item.get("基金经理")),
        "是否对客": "不适用",
        "是否广发": "是" if "广发" in as_text(item.get("基金公司")) else "否",
        "展示状态": "主份额基金",
        "数据状态": data_status,
        "基金家族ID": as_text(item.get("基金家族ID")),
        "主基金代码": as_text(item.get("主基金代码")) or code,
        "主基金名称": as_text(item.get("主基金名称")) or as_text(item.get("基金名称")),
        "份额类别": as_text(item.get("份额类别")),
        "计价币种": as_text(item.get("计价币种")),
        "是否主份额": yes_no(item.get("是否主份额", 1)),
        "合并份额数": to_float(item.get("合并份额数")) or 1,
        "合并份额代码JSON": as_text(item.get("合并份额代码JSON")),
        "主份额映射来源": as_text(item.get("主份额映射来源")),
        "主份额映射置信度": as_text(item.get("主份额映射置信度")),
        "成立日期": as_text(item.get("F10成立日期")),
        "是否FOF": yes_no(item.get("是否FOF")),
        "是否QDII": yes_no(item.get("是否QDII")),
        "是否ETF": yes_no(item.get("是否ETF")),
        "是否LOF": yes_no(item.get("是否LOF")),
        "是否REITs": yes_no(item.get("是否REITs")),
        "是否商品黄金": yes_no(item.get("是否商品黄金")),
        "标准资产大类": as_text(item.get("标准资产大类")),
        "标准资产细类": as_text(item.get("标准资产细类")),
        "基准风险资产权重": bucket,
        "基准风险资产权重_百分比": pp_to_decimal(
            sum(
                to_float(item.get(field)) or 0.0
                for field in ("基准权益权重_百分比", "基准商品权重_百分比", "基准另类权重_百分比")
            )
        ) if bucket != "未分档" else None,
        "基准风险资产权重说明": bucket_desc,
        "基准风险资产权重来源": source,
        **comparison,
        "分类依据": "基准风险资产权重" if bucket != "未分档" else "未分档",
        "业务/公开分类": as_text(item.get("基金类型")) or as_text(item.get("天天基金细分类")),
        "FOF公开分类": as_text(item.get("天天基金细分类")) if int(to_float(item.get("是否FOF")) or 0) == 1 else "",
        "FOF基准细分分类": "",
        "风险等级": "",
        "基准权益权重": pp_to_decimal(item.get("基准权益权重_百分比")),
        "基准债券权重": pp_to_decimal(item.get("基准债券权重_百分比")),
        "基准货币权重": pp_to_decimal(item.get("基准货币权重_百分比")),
        "基准商品权重": pp_to_decimal(item.get("基准商品权重_百分比")),
        "基准另类权重": pp_to_decimal(item.get("基准另类权重_百分比")),
        "基准港股权益权重": pp_to_decimal(item.get("基准港股权益权重_百分比")),
        "基准海外权益权重": pp_to_decimal(item.get("基准海外权益权重_百分比")),
        "基准海外权重": pp_to_decimal(item.get("基准海外权重_百分比")),
        "基准未知权重": pp_to_decimal(item.get("基准未知权重_百分比")),
        "业绩比较基准": as_text(item.get("业绩比较基准")),
        "业绩基准原始来源": as_text(item.get("业绩基准原始来源")),
        "业绩基准获取状态": as_text(item.get("业绩基准获取状态")),
        "基准解析说明": as_text(item.get("基准解析说明")),
        "是否使用分类兜底": yes_no(item.get("是否使用分类兜底")),
        "F10采集状态": as_text(item.get("F10采集状态")),
        "F10_HTTP状态": to_float(item.get("F10_HTTP状态")),
        "F10错误信息": as_text(item.get("F10错误信息")),
        "F10页面URL": as_text(item.get("F10页面URL")),
        "解析置信度": as_text(item.get("基准映射置信度")),
        "解析置信度分数": confidence_score(item.get("基准映射置信度"), source),
        "是否当前库使用": yes_no(item.get("是否当前库使用")),
        "本地净值记录数": to_float(item.get("本地净值记录数")),
        "本地净值起始日": as_text(item.get("本地净值起始日")),
        "本地净值截止日": as_text(item.get("本地净值截止日")),
        "外部上半年收益率": pp_to_decimal(item.get("外部上半年收益率_百分比")),
        "本地上半年收益率": pp_to_decimal(item.get("本地上半年收益率_百分比")),
        "源端复爬上半年收益率": pp_to_decimal(item.get("源端复爬上半年收益率_百分比")),
        "源端复爬最大回撤": pp_to_decimal(item.get("源端复爬最大回撤_百分比")),
        "源端复爬年化波动率": pp_to_decimal(item.get("源端复爬年化波动率_百分比")),
        "源端复爬收益来源": as_text(item.get("源端复爬收益来源")),
        "源端复爬复权口径": as_text(item.get("源端复爬复权口径")),
        "源端复爬窗口起始日": as_text(item.get("源端复爬窗口起始日")),
        "源端复爬窗口截止日": as_text(item.get("源端复爬窗口截止日")),
        "源端复爬净值点数": to_float(item.get("源端复爬净值点数")),
        "外部本地收益差异_百分点": to_float(item.get("外部收益差异_百分点")),
        "源端复爬外部收益差异_百分点": to_float(item.get("源端复爬外部收益差异_百分点")),
        "源端复爬本地收益差异_百分点": to_float(item.get("源端复爬本地收益差异_百分点")),
        "外部收益核对状态": as_text(item.get("外部收益核对状态")),
        "源端复爬收益核对状态": as_text(item.get("源端复爬收益核对状态")),
        "收益确认来源": as_text(item.get("收益确认来源")),
        "详情链接": f"./fund.html?code={code}",
    }
    metrics = build_interval_metrics(nav_series, end_date)
    for label in INTERVAL_LABELS:
        ret_pp = to_float(item.get(f"{label}收益率_百分比"))
        dd_pp = to_float(item.get(f"{label}最大回撤_百分比"))
        vol_pp = to_float(item.get(f"{label}年化波动率_百分比"))
        metrics[f"{label}收益率"] = pp_to_decimal(ret_pp)
        metrics[f"{label}最大回撤"] = pp_to_decimal(dd_pp)
        metrics[f"{label}年化波动率"] = pp_to_decimal(vol_pp)
    if item.get("源端复爬上半年收益率_百分比") is not None:
        metrics["上半年收益来源"] = as_text(item.get("上半年收益来源")) or "源端复爬复权收益"
        metrics["上半年风险来源"] = as_text(item.get("上半年风险来源")) or (
            "源端复爬复权净值" if metrics.get("上半年最大回撤") is not None else "源端复爬未形成风险指标"
        )
    elif item.get("外部上半年收益率_百分比") is not None:
        metrics["上半年收益来源"] = "外部全部基金0630复权收益"
        metrics["上半年风险来源"] = as_text(item.get("上半年风险来源")) or (
            "本地复权净值" if metrics.get("上半年最大回撤") is not None else "外部收益与本地净值不一致，风险指标留空"
        )
    output.update(metrics)
    return output


def fetch_strategy_assets(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    rows = conn.execute('SELECT * FROM "策略基准资产配置"').fetchall()
    output: dict[str, dict[str, Any]] = {}
    for row in rows:
        item = dict(row)
        sid = as_text(item.get("统一策略ID")).strip()
        if not sid:
            continue
        equity = to_float(item.get("基准资产大类-权益"))
        conf = as_text(item.get("基准映射置信度")).strip()
        bond = to_float(item.get("基准资产大类-债券") or item.get("基准资产类别-债券"))
        cash = to_float(item.get("基准资产大类-现金") or item.get("基准资产类别-现金"))
        commodity = to_float(item.get("基准资产大类-商品"))
        alternative = to_float(item.get("基准资产大类-另类"))
        hk_equity = to_float(item.get("基准港股权益权重") or item.get("基准资产类别-港股"))
        overseas_equity = to_float(item.get("基准海外权益权重") or item.get("基准资产类别-海外权益"))
        overseas = (hk_equity or 0.0) + (overseas_equity or 0.0)
        unknown = to_float(item.get("基准资产大类-其他") or item.get("基准资产未映射权重"))
        risk_asset_weight = None if unknown and unknown > 0.01 else (equity or 0.0) + (commodity or 0.0) + (alternative or 0.0)
        bucket = as_text(item.get("基准风险资产权重") or item.get(LEGACY_STRATEGY_BUCKET)).strip() or benchmark_equity_bucket(risk_asset_weight)
        comparison = build_comparison_pool(
            bucket=bucket,
            equity=equity,
            bond=bond,
            cash=cash,
            commodity=commodity,
            alternative=alternative,
            unknown=unknown,
        )
        output[sid] = {
            "基准风险资产权重": bucket or "未分档",
            "基准风险资产权重_百分比": pp_to_decimal(risk_asset_weight),
            "基准风险资产权重说明": bucket_description(bucket),
            "基准风险资产权重来源": "策略基准资产配置",
            "基准权益权重": pp_to_decimal(equity),
            "基准债券权重": pp_to_decimal(bond),
            "基准货币权重": pp_to_decimal(cash),
            "基准商品权重": pp_to_decimal(commodity),
            "基准另类权重": pp_to_decimal(alternative),
            "基准港股权益权重": pp_to_decimal(hk_equity),
            "基准海外权益权重": pp_to_decimal(overseas_equity),
            "基准海外权重": pp_to_decimal(overseas),
            "基准未知权重": pp_to_decimal(unknown),
            **comparison,
            "解析置信度": conf,
            "解析置信度分数": confidence_score(conf),
        }
    return output


def apply_strategy_assets(row: dict[str, Any], assets: dict[str, dict[str, Any]]) -> dict[str, Any]:
    sid = as_text(row.get("产品ID")).strip()
    enriched = dict(row)
    enriched.setdefault("基金主类型", "投顾策略")
    enriched.setdefault("基金类型标签", "投顾策略")
    for col in ["是否FOF", "是否QDII", "是否ETF", "是否LOF", "是否REITs", "标准资产大类", "标准资产细类"]:
        enriched.setdefault(col, "")
    if sid in assets:
        enriched.update(assets[sid])
    elif not enriched.get("基准风险资产权重"):
        enriched["基准风险资产权重"] = "未分档"
        enriched["基准风险资产权重说明"] = "未分档"
    if not enriched.get("非权益比较轨道"):
        comparison = build_comparison_pool(
            bucket=enriched.get("基准风险资产权重"),
            equity=(to_float(enriched.get("基准权益权重")) or 0.0) * 100.0,
            bond=(to_float(enriched.get("基准债券权重")) or 0.0) * 100.0,
            cash=(to_float(enriched.get("基准货币权重")) or 0.0) * 100.0,
            commodity=(to_float(enriched.get("基准商品权重")) or 0.0) * 100.0,
            alternative=(to_float(enriched.get("基准另类权重")) or 0.0) * 100.0,
            unknown=(to_float(enriched.get("基准未知权重")) or 0.0) * 100.0,
        )
        enriched.update(comparison)
    enriched.setdefault("绝对收益排名", None)
    enriched.setdefault("同档混排排名", None)
    enriched.setdefault("同类可比排名", None)
    enriched.setdefault("同类可比样本数", None)
    enriched.setdefault("同类前25%", "")
    return enriched


def apply_strategy_summary_business_facts(
    row: dict[str, Any],
    summary_row: dict[str, Any] | None,
) -> dict[str, Any]:
    """Use the strategy-list business facts as the cross-page filter contract.

    The strategy asset table intentionally records direct benchmark evidence and
    can therefore remain empty for a child strategy that inherits a verified
    parent benchmark.  The strategy-list summary is built after relationship and
    benchmark governance, so it is the canonical source shared by institution
    overview and mixed ranking.  Asset-table values may enrich that source, but
    must not overwrite its final bucket with an all-zero placeholder.
    """

    enriched = dict(row)
    if not summary_row:
        return enriched

    text_fields = {
        "产品名称": "策略名称",
        "机构": "投顾机构",
        "渠道": "渠道",
        "有基准": "有基准",
        "有业绩走势": "有业绩走势",
        "有历史仓位": "有历史仓位",
        "对客未终止": "对客未终止",
        "基准结构类型": "基准结构类型",
        "非权益比较轨道": "非权益比较轨道",
        "正式可比池": "正式可比池",
        "可比池样本资格": "可比池样本资格",
        "可比池说明": "可比池说明",
    }
    for output_field, summary_field in text_fields.items():
        if summary_field in summary_row:
            enriched[output_field] = as_text(summary_row.get(summary_field)).strip()

    if "基准风险资产权重" in summary_row:
        bucket = as_text(summary_row.get("基准风险资产权重")).strip()
        enriched["基准风险资产权重"] = bucket
        enriched["基准风险资产权重说明"] = (
            as_text(summary_row.get("基准风险资产权重说明")).strip()
            or bucket_description(bucket)
        )
        enriched["基准风险资产权重来源"] = (
            as_text(summary_row.get("基准风险资产权重来源")).strip()
            or "策略列表统一业务事实"
        )

    def first_summary_number(*fields: str) -> float | None:
        for field in fields:
            if field in summary_row:
                value = to_float(summary_row.get(field))
                if value is not None:
                    return value
        return None

    percentage_fields = {
        "基准风险资产权重_百分比": ("基准风险资产权重_百分比",),
        "基准权益权重": ("基准权益权重", "基准资产大类-权益"),
        "基准债券权重": ("基准债券权重", "基准资产大类-债券"),
        "基准货币权重": ("基准货币权重", "基准资产大类-现金"),
        "基准商品权重": ("基准商品权重", "基准资产大类-商品"),
        "基准另类权重": ("基准另类权重", "基准资产大类-另类"),
        "基准港股权益权重": ("基准港股权益权重", "基准资产类别-港股"),
        "基准海外权益权重": ("基准海外权益权重", "基准资产类别-海外权益"),
        "基准海外权重": ("基准海外权重",),
        "基准未知权重": ("基准未知权重", "基准资产大类-其他"),
    }
    for output_field, summary_fields in percentage_fields.items():
        value = first_summary_number(*summary_fields)
        if value is not None:
            enriched[output_field] = pp_to_decimal(value)

    if "基准互斥权重合计_百分比" in summary_row:
        enriched["基准互斥权重合计_百分比"] = to_float(
            summary_row.get("基准互斥权重合计_百分比")
        )

    benchmark = as_text(
        summary_row.get("业绩基准") or summary_row.get("业绩基准说明")
    ).strip()
    if benchmark:
        enriched["业绩比较基准"] = benchmark

    confidence = as_text(summary_row.get("基准映射置信度")).strip()
    if confidence:
        enriched["解析置信度"] = confidence
        enriched["解析置信度分数"] = confidence_score(confidence)
    return enriched


def strategy_channel_code(row: dict[str, Any]) -> str:
    value = as_text(row.get("产品代码") or row.get("产品ID")).strip().upper()
    return value.split("__", 1)[-1]


def benchmark_assets_from_text(benchmark: str, catalog: Any) -> dict[str, Any]:
    mix = compute_benchmark_asset_mix(benchmark, catalog)
    equity = to_float(mix.get("基准资产大类-权益"))
    bond = to_float(mix.get("基准资产大类-债券") or mix.get("基准资产类别-债券"))
    cash = to_float(mix.get("基准资产大类-现金") or mix.get("基准资产类别-现金"))
    commodity = to_float(mix.get("基准资产大类-商品"))
    alternative = to_float(mix.get("基准资产大类-另类"))
    unknown = to_float(mix.get("基准资产大类-其他") or mix.get("基准资产未映射权重"))
    hk_equity = to_float(mix.get("基准港股权益权重") or mix.get("基准资产类别-港股"))
    overseas_equity = to_float(mix.get("基准海外权益权重") or mix.get("基准资产类别-海外权益"))
    risk_asset_weight = (equity or 0.0) + (commodity or 0.0) + (alternative or 0.0)
    bucket = as_text(mix.get("基准风险资产权重") or mix.get(LEGACY_STRATEGY_BUCKET)).strip() or benchmark_equity_bucket(risk_asset_weight)
    comparison = build_comparison_pool(
        bucket=bucket,
        equity=equity,
        bond=bond,
        cash=cash,
        commodity=commodity,
        alternative=alternative,
        unknown=unknown,
    )
    return {
        "基准风险资产权重": bucket or "未分档",
        "基准风险资产权重_百分比": pp_to_decimal(risk_asset_weight),
        "基准风险资产权重说明": bucket_description(bucket),
        "基准风险资产权重来源": "外部广发策略基准解析",
        "基准权益权重": pp_to_decimal(equity),
        "基准债券权重": pp_to_decimal(bond),
        "基准货币权重": pp_to_decimal(cash),
        "基准商品权重": pp_to_decimal(commodity),
        "基准另类权重": pp_to_decimal(alternative),
        "基准港股权益权重": pp_to_decimal(hk_equity),
        "基准海外权益权重": pp_to_decimal(overseas_equity),
        "基准海外权重": pp_to_decimal((hk_equity or 0.0) + (overseas_equity or 0.0)),
        "基准未知权重": pp_to_decimal(unknown),
        "基准解析说明": as_text(mix.get("基准公式解析")),
        "解析置信度": as_text(mix.get("基准映射置信度")),
        "解析置信度分数": confidence_score(mix.get("基准映射置信度")),
        **comparison,
    }


def build_external_guangfa_strategy_rows(
    external_rows: list[dict[str, Any]],
    existing_rows: list[dict[str, Any]],
    disclosed_nav: dict[str, list[tuple[str, float]]],
    strategy_refetch: dict[str, dict[str, Any]],
    end_date: date,
) -> list[dict[str, Any]]:
    by_code = {strategy_channel_code(row): row for row in existing_rows if strategy_channel_code(row)}
    catalog = load_benchmark_catalog()
    output: list[dict[str, Any]] = []
    for external in external_rows:
        code = as_text(external.get("策略代码")).strip().upper()
        refetch = strategy_refetch.get(code, {})
        matched_existing = code in by_code
        base = dict(by_code.get(code) or {})
        product_id = f"gffunds__{code}"
        if not base:
            base = {
                "产品类型": "投顾策略",
                "基金主类型": "投顾策略",
                "基金类型标签": "投顾策略",
                "产品ID": product_id,
                "产品代码": code,
                "产品名称": as_text(external.get("策略名称")),
                "机构": "广发基金",
                "渠道": "广发基金",
                "管理人/经理": "",
                "是否对客": "是",
                "是否广发": "是",
                "展示状态": "外部广发151产品原始代码口径",
                "数据状态": "仅外部上半年收益" if not disclosed_nav.get(code) else "官方披露净值可重算",
                "成立日期": "",
                "详情链接": "",
            }
            for col in ["是否FOF", "是否QDII", "是否ETF", "是否LOF", "是否REITs", "是否商品黄金", "标准资产大类", "标准资产细类"]:
                base[col] = ""
        base["产品ID"] = product_id
        base["产品代码"] = code
        base["产品名称"] = as_text(external.get("策略名称")) or as_text(base.get("产品名称"))
        base["机构"] = "广发基金"
        base["渠道"] = "广发基金"
        base["是否广发"] = "是"
        base["本地策略匹配状态"] = "精确代码匹配" if matched_existing else "仅外部原始代码"
        base["展示状态"] = "外部广发151产品原始代码口径"
        benchmark = as_text(external.get("策略基准")) or as_text(base.get("业绩比较基准"))
        base["业绩比较基准"] = benchmark
        base.update(benchmark_assets_from_text(benchmark, catalog))
        series = disclosed_nav.get(code, [])
        official_metrics = build_interval_metrics(series, end_date) if series else {}
        if official_metrics:
            for key, value in official_metrics.items():
                base[key] = value
            base["最新业绩日期"] = series[-1][0]
        external_return_pp = to_float(external.get("上半年收益率_百分比"))
        official_return_pp = decimal_to_pp(base.get("上半年收益率"))
        refetch_return_pp = to_float(refetch.get("官方App上半年收益率_百分比"))
        refetch_benchmark_pp = to_float(refetch.get("官方App基准上半年收益率_百分比"))
        refetch_external_diff_pp = to_float(refetch.get("外部与官方App差异_百分点"))
        confirmed_return_pp = refetch_return_pp if refetch_return_pp is not None else external_return_pp
        confirmed_benchmark_pp = (
            refetch_benchmark_pp
            if refetch_benchmark_pp is not None
            else to_float(external.get("基准上半年收益率_百分比"))
        )
        difference_pp = None
        if external_return_pp is not None and official_return_pp is not None:
            difference_pp = official_return_pp - external_return_pp
        base["外部上半年收益率"] = pp_to_decimal(external_return_pp)
        base["官方净值重算上半年收益率"] = pp_to_decimal(official_return_pp)
        base["外部官方收益差异_百分点"] = difference_pp
        base["源端复爬上半年收益率"] = pp_to_decimal(refetch_return_pp)
        base["源端复爬基准上半年收益率"] = pp_to_decimal(refetch_benchmark_pp)
        base["源端复爬外部收益差异_百分点"] = refetch_external_diff_pp
        base["源端复爬收益来源"] = as_text(refetch.get("最终确认来源"))
        base["外部收益核对状态"] = (
            as_text(refetch.get("核对结论"))
            if refetch
            else (
            "一致"
            if difference_pp is not None and abs(difference_pp) <= 0.01
            else "缺官方净值"
            if official_return_pp is None
            else "不一致"
            )
        )
        if confirmed_return_pp is not None:
            base["上半年收益率"] = pp_to_decimal(confirmed_return_pp)
            base["上半年收益来源"] = (
                as_text(refetch.get("最终确认来源"))
                if refetch_return_pp is not None
                else "外部广发策略上半年业绩"
            )
        risk_difference_pp = None
        if confirmed_return_pp is not None and official_return_pp is not None:
            risk_difference_pp = official_return_pp - confirmed_return_pp
        if risk_difference_pp is None or abs(risk_difference_pp) > 0.05:
            base["上半年最大回撤"] = None
            base["上半年年化波动率"] = None
            base["上半年风险来源"] = "最终收益与官方披露净值无法一致核对，风险指标留空"
        else:
            base["上半年风险来源"] = "官方披露单位净值"
        base["基准上半年收益率"] = pp_to_decimal(confirmed_benchmark_pp)
        base["外部年化收益率"] = pp_to_decimal(external.get("年化收益率_百分比"))
        base["排名"] = None
        base["绝对收益排名"] = None
        base["同档混排排名"] = None
        base["同类可比排名"] = None
        base["同类可比样本数"] = None
        base["同类前25%"] = ""
        output.append(base)
    return output


def assign_ranks(rows: list[dict[str, Any]]) -> None:
    rows.sort(
        key=lambda row: (
            row.get("上半年收益率") is None,
            -(row.get("上半年收益率") or -999),
            *bucket_sort_key(row.get("基准风险资产权重", "")),
            row.get("产品类型", ""),
            row.get("基金主类型", ""),
            row.get("机构", ""),
            row.get("产品名称", ""),
        )
    )
    def rank_group(group: list[dict[str, Any]], field: str) -> None:
        ordered = sorted(
            (row for row in group if to_float(row.get("上半年收益率")) is not None),
            key=lambda row: (-float(row["上半年收益率"]), row.get("产品ID", "")),
        )
        previous_value: float | None = None
        previous_rank: int | None = None
        for idx, row in enumerate(ordered, start=1):
            value = float(row["上半年收益率"])
            rank = previous_rank if previous_value is not None and abs(value - previous_value) < 1e-12 else idx
            row[field] = rank
            previous_value = value
            previous_rank = rank

    for row in rows:
        row["排名"] = None
        row["绝对收益排名"] = None
        row["同档混排排名"] = None
        row["同类可比排名"] = None
        row["同类可比样本数"] = None
        row["同类前25%"] = ""
        row["同类收益中位数"] = None
        row["同类最大回撤中位数"] = None
        row["同类年化波动率中位数"] = None

    rank_group(rows, "绝对收益排名")
    bucket_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    pool_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        bucket = as_text(row.get("基准风险资产权重")).strip()
        if bucket and bucket != "未分档":
            bucket_groups[bucket].append(row)
        pool = as_text(row.get("正式可比池")).strip()
        if pool and as_text(row.get("可比池样本资格")) == "是":
            pool_groups[pool].append(row)
    for group in bucket_groups.values():
        rank_group(group, "同档混排排名")
    for group in pool_groups.values():
        valid = [row for row in group if to_float(row.get("上半年收益率")) is not None]
        if len(valid) < PEER_POOL_MIN_SIZE:
            for row in group:
                row["同类可比样本数"] = len(valid)
            continue
        rank_group(valid, "同类可比排名")
        return_values = stat_values(valid, "上半年收益率")
        dd_values = stat_values(valid, "上半年最大回撤")
        vol_values = stat_values(valid, "上半年年化波动率")
        top_count = max(1, math.ceil(len(valid) * 0.25))
        for row in group:
            row["同类可比样本数"] = len(valid)
            row["同类前25%"] = "是" if row.get("同类可比排名") and row["同类可比排名"] <= top_count else "否"
            row["同类收益中位数"] = statistics.median(return_values) if return_values else None
            row["同类最大回撤中位数"] = statistics.median(dd_values) if dd_values else None
            row["同类年化波动率中位数"] = statistics.median(vol_values) if vol_values else None
    for row in rows:
        row["排名"] = row.get("绝对收益排名")


def stat_values(rows: list[dict[str, Any]], field: str) -> list[float]:
    return [to_float(row.get(field)) for row in rows if to_float(row.get(field)) is not None]


def build_bucket_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        bucket = row.get("基准风险资产权重") or "未分档"
        track = row.get("非权益比较轨道") or "未形成轨道"
        pool = row.get("正式可比池") or "未进入正式可比池"
        groups[(bucket, track, pool)].append(row)
    summary = []
    for (bucket, track, pool), group in sorted(groups.items(), key=lambda item: (bucket_sort_key(item[0][0]), item[0][1], item[0][2])):
        h1_returns = stat_values(group, "上半年收益率")
        h1_dd = stat_values(group, "上半年最大回撤")
        h1_vol = stat_values(group, "上半年年化波动率")
        peer_valid = pool != "未进入正式可比池" and len(h1_returns) >= PEER_POOL_MIN_SIZE
        summary.append(
            {
                "基准风险资产权重": bucket,
                "基准风险资产权重说明": bucket_description(bucket),
                "非权益比较轨道": track,
                "正式可比池": pool,
                "产品类型分布": json.dumps(dict(Counter(row.get("产品类型") or "未标识" for row in group)), ensure_ascii=False),
                "基金主类型分布": json.dumps(dict(Counter(row.get("基金主类型") or "未标识" for row in group)), ensure_ascii=False),
                "产品数": len(group),
                "上半年收益有效数": len(h1_returns),
                "是否满足同类统计门槛": "是" if peer_valid else "否",
                "上半年收益均值": statistics.mean(h1_returns) if peer_valid else None,
                "上半年收益中位数": statistics.median(h1_returns) if peer_valid else None,
                "上半年最大回撤有效数": len(h1_dd),
                "上半年最大回撤均值": statistics.mean(h1_dd) if peer_valid and h1_dd else None,
                "上半年最大回撤中位数": statistics.median(h1_dd) if peer_valid and h1_dd else None,
                "上半年年化波动率有效数": len(h1_vol),
                "上半年年化波动率均值": statistics.mean(h1_vol) if peer_valid and h1_vol else None,
                "上半年年化波动率中位数": statistics.median(h1_vol) if peer_valid and h1_vol else None,
            }
        )
    return summary


def choose_samples(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}

    def score(row: dict[str, Any]) -> tuple[int, float, str]:
        complete = 0
        complete += 10 * int(row.get("本地策略匹配状态") == "精确代码匹配")
        complete += int(bool(row.get("基准风险资产权重")))
        complete += int(row.get("上半年收益率") is not None)
        complete += int(row.get("上半年最大回撤") is not None)
        complete += int(row.get("上半年年化波动率") is not None)
        complete += int(bool(row.get("业绩比较基准")))
        return (complete, row.get("上半年收益率") or -999, row.get("产品名称", ""))

    for name in {"司南科技基金精选", "中欧带你投硬科技"}:
        for row in rows:
            if row.get("产品名称") == name:
                selected[row["产品ID"]] = row

    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(row["产品类型"], row.get("机构") or "未知机构")].append(row)
    for group in groups.values():
        best = sorted(group, key=score, reverse=True)[0]
        selected.setdefault(best["产品ID"], best)

    for main_type in ["FOF", "QDII", "ETF", "REITs", "商品黄金", "债券", "货币", "权益", "混合"]:
        candidates = [row for row in rows if row.get("基金主类型") == main_type or main_type in as_text(row.get("基金类型标签"))]
        if candidates:
            best = sorted(candidates, key=score, reverse=True)[0]
            selected.setdefault(best["产品ID"], best)

    return sorted(selected.values(), key=lambda row: (row["产品类型"], row.get("机构") or "", row.get("基金主类型") or "", row["产品名称"]))


def compare_strategy_sample(
    conn: sqlite3.Connection,
    row: dict[str, Any],
    strategy_info: dict[str, sqlite3.Row],
    strategy_assets: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    product_id = row["产品ID"]
    qa = base_qa_row(row)
    source = strategy_info.get(product_id)
    if source is None:
        qa["核对状态"] = "需复核"
        qa["核对说明"] = "策略信息表未找到该统一策略ID"
        return qa
    notes: list[str] = []
    statuses: list[str] = []
    checked = 0
    expected_fields = [("产品名称", source["策略名称"]), ("机构", source["投顾机构"])]
    if as_text(row.get("渠道")) != "广发基金":
        expected_fields.append(("业绩比较基准", source["业绩基准"]))
    for field, expected in expected_fields:
        checked += 1
        if not compare_required_text(row.get(field), expected):
            statuses.append("不一致")
            notes.append(f"{field}与策略信息表不一致")
    asset = strategy_assets.get(product_id)
    if asset:
        checked += 2
        if row.get("基准风险资产权重") != asset.get("基准风险资产权重"):
            statuses.append("不一致")
            notes.append("基准风险资产权重与策略基准资产配置不一致")
        if abs((to_float(row.get("基准权益权重")) or 0) - (to_float(asset.get("基准权益权重")) or 0)) > 0.0005:
            statuses.append("不一致")
            notes.append("基准权益权重与策略基准资产配置不一致")
    diffs: list[float] = []
    attention = False
    for label in INTERVAL_LABELS:
        date_range = as_text(row.get(f"{label}区间"))
        if "~" not in date_range:
            attention = True
            notes.append(f"{label}缺少区间日期")
            continue
        start, end = date_range.split("~", 1)
        points = nav_series(conn, product_id, start, end)
        if not points:
            attention = True
            checked += 3
            notes.append(f"{label}策略标准业绩净值缺失，无法重算")
            continue
        metrics = calc_nav_metrics(points, start, end)
        for workbook_field, calc_key in [(f"{label}收益率", "return"), (f"{label}最大回撤", "maxDrawdown"), (f"{label}年化波动率", "volatility")]:
            status, diff = compare_decimal_to_pp(row.get(workbook_field), to_float(metrics.get(calc_key)))
            checked += 1
            if diff is not None:
                diffs.append(abs(diff))
            if status not in {"一致", "一致缺失"}:
                statuses.append(status)
                notes.append(f"{workbook_field}与策略标准业绩净值重算不一致")
    finish_qa(qa, checked, diffs, statuses, attention, notes, "策略信息/策略基准资产配置/策略标准业绩净值")
    return qa


def compare_fund_sample(
    conn: sqlite3.Connection,
    row: dict[str, Any],
    fund_snapshot: dict[str, sqlite3.Row],
    end_date: date,
) -> dict[str, Any]:
    code = row["产品代码"]
    qa = base_qa_row(row)
    source = fund_snapshot.get(code)
    if source is None:
        qa["核对状态"] = "需复核"
        qa["核对说明"] = "公募基金产品绩效快照未找到该基金代码"
        return qa
    notes: list[str] = []
    statuses: list[str] = []
    checked = 0
    source_fields = dict(source)
    for field, expected_col in [("产品名称", "基金名称"), ("机构", "基金公司"), ("基准风险资产权重来源", "基准风险资产权重来源")]:
        checked += 1
        expected_value = source_fields.get(expected_col)
        if expected_col == "基准风险资产权重来源" and expected_value in (None, ""):
            expected_value = source_fields.get(LEGACY_PUBLIC_BUCKET_SOURCE)
        if not compare_required_text(row.get(field), expected_value):
            statuses.append("不一致")
            notes.append(f"{field}与公募基金产品绩效快照不一致")
    diffs: list[float] = []
    attention = False
    intervals = [item for item in interval_definitions(end_date) if item["label"] in INTERVAL_LABELS]
    lower_bound = min(item["start"] for item in intervals) - timedelta(days=LOOKBACK_BUFFER_DAYS)
    series = load_public_nav_series(conn, [code], lower_bound, end_date).get(code, [])
    for item in intervals:
        label = item["label"]
        ret_pp, ret_status = calc_return(series, item["start"], item["end"])
        risk = calc_risk(series, item["start"], item["end"])
        complete_interval = ret_pp is not None and ret_status.get("status") == "完整区间"
        expected_start = (ret_status or {}).get("startDate") or risk.get("startDate")
        expected_end = (ret_status or {}).get("endDate") or risk.get("endDate")
        expected_range = f"{expected_start}~{expected_end}" if expected_start and expected_end else ""
        actual_range = as_text(row.get(f"{label}区间"))
        if actual_range != expected_range:
            if not actual_range and not expected_range:
                attention = True
                notes.append(f"{label}缺少本地净值区间，保留缺失标记")
            else:
                statuses.append("不一致")
                notes.append(f"{label}净值区间与基金日度净值重算不一致")
        for workbook_field, right_pp in [
            (f"{label}收益率", to_float(source[f"{label}收益率_百分比"]) if f"{label}收益率_百分比" in source.keys() else ret_pp),
            (
                f"{label}最大回撤",
                to_float(source[f"{label}最大回撤_百分比"])
                if f"{label}最大回撤_百分比" in source.keys()
                else risk.get("maxDrawdown") if complete_interval else None,
            ),
            (
                f"{label}年化波动率",
                to_float(source[f"{label}年化波动率_百分比"])
                if f"{label}年化波动率_百分比" in source.keys()
                else risk.get("volatility") if complete_interval else None,
            ),
        ]:
            status, diff = compare_decimal_to_pp(row.get(workbook_field), to_float(right_pp))
            checked += 1
            if diff is not None:
                diffs.append(abs(diff))
            if status not in {"一致", "一致缺失"}:
                statuses.append(status)
                notes.append(f"{workbook_field}与基金日度净值重算不一致")
    finish_qa(qa, checked, diffs, statuses, attention, notes, "公募基金产品绩效快照/基金日度净值（含日收益率合成）")
    return qa


def base_qa_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "抽样维度": f"{row['产品类型']}+{row.get('基金主类型') or ''}+{row['机构']}",
        "产品类型": row["产品类型"],
        "基金主类型": row.get("基金主类型"),
        "机构": row["机构"],
        "产品代码": row["产品代码"],
        "产品名称": row["产品名称"],
        "基准风险资产权重": row.get("基准风险资产权重"),
        "基准风险资产权重来源": row.get("基准风险资产权重来源"),
        "上半年收益率": row.get("上半年收益率"),
        "上半年最大回撤": row.get("上半年最大回撤"),
        "上半年年化波动率": row.get("上半年年化波动率"),
        "核对字段数": 0,
        "最大收益差异_百分点": None,
        "最大风险差异_百分点": None,
        "核对状态": "通过",
        "核对说明": "",
    }


def finish_qa(
    qa: dict[str, Any],
    checked: int,
    diffs: list[float],
    statuses: list[str],
    attention: bool,
    notes: list[str],
    source_note: str,
) -> None:
    qa["核对字段数"] = checked
    if diffs:
        qa["最大收益差异_百分点"] = max(diffs)
        qa["最大风险差异_百分点"] = max(diffs)
    if any(status in {"不一致", "缺失不一致"} for status in statuses):
        qa["核对状态"] = "需复核"
    elif attention:
        qa["核对状态"] = "需关注"
    if not notes:
        notes.append(f"与{source_note}抽样核对一致，容忍阈值{TOLERANCE_PP}个百分点")
    qa["核对说明"] = "；".join(notes[:5])


def build_coverage_rows(
    rows: list[dict[str, Any]],
    raw_strategy_count: int,
    source_excluded_strategy_count: int,
    visible_nonrankable_strategy_count: int,
    fund_snapshot_rows: list[sqlite3.Row],
    end_date: date,
) -> list[dict[str, Any]]:
    counter = Counter(row["产品类型"] for row in rows)
    fund_counter = Counter(row.get("基金主类型") or "" for row in rows if row["产品类型"] == "公募基金")
    fund_rows = [row for row in rows if row["产品类型"] == "公募基金"]
    strategy_rows = [row for row in rows if row["产品类型"] == "投顾策略"]
    source_counter = Counter(
        as_text(dict(row).get("基准风险资产权重来源") or dict(row).get(LEGACY_PUBLIC_BUCKET_SOURCE)) or "缺失"
        for row in fund_snapshot_rows
    )
    coverage = [
        {"项目": "截至日期", "值": end_date.isoformat(), "说明": "滚动收益、回撤和波动率统一按当前策略与基金的最新可比日期截断"},
        {"项目": "导出总数", "值": len(rows), "说明": f"投顾{counter.get('投顾策略', 0)}；公募基金{counter.get('公募基金', 0)}"},
        {
            "项目": "投顾策略范围",
            "值": "、".join(sorted(ALLOWED_STRATEGY_CHANNELS)),
            "说明": (
                f"列表展示与混排资格分层；排名源策略{raw_strategy_count}；"
                f"排名源中非当前列表对象{source_excluded_strategy_count}；"
                f"列表可见但无混排资格{visible_nonrankable_strategy_count}"
            ),
        },
        {"项目": "公募基金范围", "值": len(fund_rows), "说明": "全市场基金按F10基金全称+基金公司合并份额，只保留主份额；缺净值不剔除"},
        {"项目": "正式可比池有效产品数", "值": sum(1 for row in rows if row.get("正式可比池") and row.get("可比池样本资格") == "是"), "说明": "互斥资产向量合计100%±0.01%、未知权重不超过0.01%"},
        {"项目": "正式可比池分布", "值": json.dumps(dict(Counter(as_text(row.get("正式可比池")) or "未进入" for row in rows)), ensure_ascii=False), "说明": "正式可比池=基准风险资产权重+非权益比较轨道"},
        {"项目": "基金类型分布", "值": json.dumps(dict(fund_counter), ensure_ascii=False), "说明": "按基金主类型统计，便于筛选"},
        {"项目": "基金有本地净值数", "值": sum(1 for row in fund_rows if to_float(row.get("本地净值记录数"))), "说明": "有净值才可计算收益、回撤和波动"},
        {"项目": "基金上半年收益有效数", "值": sum(1 for row in fund_rows if row.get("上半年收益率") is not None), "说明": "缺净值或净值起点晚于区间起点则收益为空"},
        {"项目": "基金基准风险资产权重完整数", "值": sum(1 for row in fund_rows if row.get("基准风险资产权重") and row.get("基准风险资产权重") != "未分档"), "说明": "仅统计F10/FOF业绩比较基准解析结果，不含分类兜底"},
        {"项目": "基金基准风险资产权重来源", "值": json.dumps(dict(source_counter), ensure_ascii=False), "说明": "未取得或未解析业绩基准的基金保留为空并带原因"},
        {"项目": "投顾基准风险资产权重来源", "值": "策略基准资产配置", "说明": "导出阶段回查数据库覆盖旧排名源字段"},
    ]
    for label in INTERVAL_LABELS:
        all_valid = sum(1 for row in rows if row.get(f"{label}收益率") is not None)
        fund_valid = sum(1 for row in fund_rows if row.get(f"{label}收益率") is not None)
        strategy_valid = sum(1 for row in strategy_rows if row.get(f"{label}收益率") is not None)
        coverage.append({"项目": f"{label}收益率有效数", "值": all_valid, "说明": f"投顾{strategy_valid}；基金{fund_valid}"})
    return coverage


def build_field_notes() -> list[dict[str, str]]:
    return [
        {"字段": "非权益比较轨道/正式可比池", "说明": "非权益资产单项占非权益合计80%以上时按债券、货币、商品或另类主导，否则为多资产；非权益为0时为纯权益。"},
        {"字段": "产品类型", "说明": "投顾策略或公募基金。基金不再限定FOF，覆盖基金标准分类字典全量基金。"},
        {"字段": "主基金代码/合并份额数", "说明": "公募基金按F10基金全称+基金公司归并份额，优先人民币A；无法可靠识别家族时保留单份额。"},
        {"字段": "基金主类型/标准资产大类/标准资产细类", "说明": "基金筛选字段保留大类口径，细碎布尔标签不再作为主表列输出。"},
        {"字段": "基准风险资产权重", "说明": "基金只根据F10/FOF业绩比较基准解析资产权重后按L0-L10分档；未取得或未解析基准时为空并说明原因，不使用分类兜底。"},
        {"字段": "收益率/最大回撤/年化波动率", "说明": "工作簿内为百分比格式；基金上半年收益优先外部复权收益核对表，风险指标仅在本地复权净值收益与外部收益一致时保留。"},
        {"字段": "抽样核对", "说明": "投顾对照策略信息、策略基准资产配置和策略标准业绩净值；基金对照公募基金产品绩效快照和基金日度净值（含日收益率合成）。"},
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pack", type=Path, default=DEFAULT_PACK)
    parser.add_argument("--summary-core", type=Path, default=DEFAULT_SUMMARY_CORE)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--end-date", default=END_DATE)
    args = parser.parse_args()

    pack = json.loads(args.pack.read_text(encoding="utf-8-sig"))
    summary = load_basic_summary(args.summary_core)
    visible_strategy_ids = strategy_list_visible_ids(summary)
    summary_strategy_by_id = strategy_summary_rows_by_id(summary)
    end_date_text = args.end_date or as_text(pack.get("meta", {}).get("dataUpdatedTo")).strip()
    if not end_date_text:
        raise RuntimeError("排名数据包缺少最新可比截止日。")
    end_date = date.fromisoformat(end_date_text)
    intervals = interval_definitions(end_date)
    lower_bound = min(item["start"] for item in intervals if item["label"] in INTERVAL_LABELS) - timedelta(days=LOOKBACK_BUFFER_DAYS)
    raw_rows = pack.get("rows", [])
    args.out_dir.mkdir(parents=True, exist_ok=True)

    conn = connect_db(args.db)
    try:
        conn.row_factory = sqlite3.Row
        strategy_info = {as_text(row["统一策略ID"]).strip(): row for row in conn.execute('SELECT * FROM "策略信息"')}
        strategy_assets = fetch_strategy_assets(conn)
        strategy_governance = load_strategy_governance(conn)
        official_performance_dates = load_strategy_official_performance_dates(conn)
        invalid_performance_ids = load_invalid_strategy_performance_ids(conn)

        raw_strategy_rows = [
            apply_page_governance_boundary(row, summary_strategy_by_id.get(strategy_row_id(row)))
            for row in raw_rows
            if entity_type(row) == "投顾策略"
        ]
        ranking_strategy_by_id = {
            strategy_row_id(row): row
            for row in raw_strategy_rows
            if strategy_row_id(row)
            and should_export_strategy(
                row,
                strategy_governance,
                official_performance_dates,
                invalid_performance_ids,
                end_date,
            )
        }
        missing_visible_ids = sorted(visible_strategy_ids - set(ranking_strategy_by_id))
        visible_nonrankable_rows, unexpected_missing_visible_ids = partition_missing_visible_strategies(
            missing_visible_ids,
            summary_strategy_by_id,
            strategy_governance,
            official_performance_dates,
            invalid_performance_ids,
            end_date,
        )
        if unexpected_missing_visible_ids:
            raise RuntimeError(
                "排名源缺少具备混排资格的策略列表对象 "
                f"{len(unexpected_missing_visible_ids)} 个，示例={unexpected_missing_visible_ids[:10]}"
            )
        selected_strategy_ids = sorted(visible_strategy_ids & set(ranking_strategy_by_id))
        selected_raw_strategy_rows = [ranking_strategy_by_id[strategy_id] for strategy_id in selected_strategy_ids]
        source_excluded_strategy_rows = [
            {
                "产品代码": strategy_row_id(row),
                "产品名称": as_text(row.get("name")),
                "机构": as_text(row.get("institution")),
                "渠道": as_text(row.get("channel")),
                "最新业绩日期": official_performance_dates.get(strategy_row_id(row), ""),
                "生命周期状态": as_text(strategy_governance.get(strategy_row_id(row), {}).get("治理状态")),
                "剔除原因": "非策略列表当前可查询对象" if should_export(row) else "渠道不在页面展示口径",
            }
            for row in raw_strategy_rows
            if strategy_row_id(row) not in visible_strategy_ids
        ]
        excluded_strategy_rows = [*source_excluded_strategy_rows, *visible_nonrankable_rows]
        strategy_rows = [
            {
                **apply_strategy_summary_business_facts(
                    apply_strategy_assets(flatten_row(row, {}, strategy_info), strategy_assets),
                    summary_strategy_by_id.get(strategy_row_id(row)),
                ),
                "最新业绩日期": official_performance_dates.get(strategy_row_id(row), ""),
                "生命周期状态": as_text(strategy_governance.get(strategy_row_id(row), {}).get("治理状态")),
            }
            for row in selected_raw_strategy_rows
        ]

        fund_snapshot_rows = load_public_fund_rows(conn)
        fund_snapshot = {as_text(row["基金代码"]).strip(): row for row in fund_snapshot_rows}
        fund_codes = [as_text(row["基金代码"]).strip() for row in fund_snapshot_rows if as_text(row["基金代码"]).strip()]
        nav_by_code = load_public_nav_series(conn, fund_codes, lower_bound, end_date)
        fund_rows = [flatten_public_fund_row(row, nav_by_code.get(as_text(row["基金代码"]).strip(), []), end_date) for row in fund_snapshot_rows]

        rows = [*strategy_rows, *fund_rows]
        assign_ranks(rows)

        samples = choose_samples(rows)
        qa_rows = [
            compare_strategy_sample(conn, row, strategy_info, strategy_assets)
            if row["产品类型"] == "投顾策略"
            else compare_fund_sample(conn, row, fund_snapshot, end_date)
            for row in samples
        ]
        qa_counter = Counter(row["核对状态"] for row in qa_rows)

        result = {
            "meta": {
                "title": "投顾策略+全量公募基金产品业绩混排榜",
                "asOfDate": end_date.isoformat(),
                "intervalAsOfDates": {
                    item["label"]: item["end"].isoformat()
                    for item in intervals
                    if item["label"] in INTERVAL_LABELS
                },
                "generatedAt": now_cn(),
                "sourcePack": str(args.pack.resolve()),
                "sourceSummaryCore": str(args.summary_core.resolve()),
                "sourceDb": str(args.db.resolve()),
                "strategyScope": "天天基金/投顾、广发基金、广发证券、且慢",
                "strategySelectionPolicy": (
                    "策略列表展示与业绩混排资格分层：不以持仓/回放完整性预先删除策略；"
                    "列表可见但无有效官方业绩、业绩异常、"
                    "业绩停更或治理排除的策略不进入混排；具备混排资格的列表对象必须由排名源覆盖。"
                ),
                "fundScope": "基金标准分类字典全量公募基金",
                "rawStrategyRowCount": len(raw_strategy_rows),
                "strategyListVisibleRowCount": len(visible_strategy_ids),
                "strategyListRankingSourceCoveredCount": len(selected_strategy_ids),
                "strategyListNonrankableRowCount": len(visible_nonrankable_rows),
                "strategyListNonrankableReasonCounts": dict(Counter(row["剔除原因"] for row in visible_nonrankable_rows)),
                "strategyListMissingEligibleRowCount": len(unexpected_missing_visible_ids),
                "strategyRowCount": len(strategy_rows),
                "guangfaStrategyRowCount": sum(1 for row in strategy_rows if row.get("是否广发") == "是"),
                "publicFundRowCount": len(fund_rows),
                "publicFundPrimaryShareRowCount": len(fund_rows),
                "exportRowCount": len(rows),
                "excludedStrategyRowCount": len(excluded_strategy_rows),
                "channelExcludedStrategyRowCount": sum(1 for row in raw_strategy_rows if not should_export(row)),
                "strategyListExcludedStrategyRowCount": len(source_excluded_strategy_rows),
                "fundNavCoveredCount": sum(1 for row in fund_rows if to_float(row.get("本地净值记录数"))),
                "fundReturnCoveredCount": sum(1 for row in fund_rows if row.get("上半年收益率") is not None),
                "fundBenchmarkBucketCoveredCount": sum(1 for row in fund_rows if row.get("基准风险资产权重") and row.get("基准风险资产权重") != "未分档"),
                "qaSampleCount": len(qa_rows),
                "qaStatusCounts": dict(qa_counter),
                "tolerancePp": TOLERANCE_PP,
            },
            "rows": rows,
            "bucketSummary": build_bucket_summary(rows),
            "coverageRows": build_coverage_rows(
                rows,
                len(raw_strategy_rows),
                len(source_excluded_strategy_rows),
                len(visible_nonrankable_rows),
                fund_snapshot_rows,
                end_date,
            ),
            "qaRows": qa_rows,
            "fieldNotes": build_field_notes(),
            "excludedStrategyRows": excluded_strategy_rows,
        }

        source_path = args.out_dir / "workbook_source.json"
        qa_path = args.out_dir / "qa_summary.json"
        source_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        qa_path.write_text(json.dumps({"meta": result["meta"], "qaRows": qa_rows}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result["meta"], ensure_ascii=False, indent=2))
        print(f"source={source_path}")
        print(f"qa={qa_path}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
