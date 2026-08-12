from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
DEFAULT_ALGORITHM_VERSION = "rebalance_asset_fee_dual_nav_v9_ttfund_rules_cifm_overseas_placeholder_20260527"
CHANNEL_ID = "ttfund"

GENERATED_VERSION_TABLES = [
    "策略模拟净值",
    "策略模拟净值区间",
    "策略模拟净值质量",
    "策略模拟净值校验",
    "策略官方偏差分析",
    "渠道官方偏差分析",
    "策略官方算法候选评估",
    "渠道官方算法候选评估",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="稽核天天基金投顾最终入库数据、计算依赖和生成结果完整性。")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--algorithm-version", default=DEFAULT_ALGORITHM_VERSION)
    parser.add_argument("--output-dir", type=Path)
    return parser.parse_args()


def now_id() -> str:
    return datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z")


def default_output_dir() -> Path:
    today = datetime.now().astimezone().strftime("%Y-%m-%d")
    return PROJECT_ROOT / "outputs" / "ttfund_final_integrity" / today / now_id()


def fetch_dicts(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, params)]


def fetch_one(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any]:
    row = conn.execute(sql, params).fetchone()
    return dict(row) if row else {}


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
            (table_name,),
        ).fetchone()
    )


def table_columns(conn: sqlite3.Connection, table_name: str) -> list[str]:
    if not table_exists(conn, table_name):
        return []
    return [row[1] for row in conn.execute(f'PRAGMA table_info("{table_name}")')]


def to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if isinstance(value, str) and value.strip() in {"", "--", "null", "None"}:
            return None
        result = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(result) or math.isinf(result):
        return None
    return result


def parse_dividend_amount(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text in {"--", "无"}:
        return None
    if "拆分" in text or "折算" in text:
        return None
    match = re.search(r"(-?\d+(?:\.\d+)?)", text)
    if not match:
        return None
    amount = float(match.group(1))
    if re.search(r"每\s*10\s*份|10\s*份", text):
        amount /= 10.0
    return amount


def percentile(sorted_values: list[float], pct: float) -> float | None:
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = (len(sorted_values) - 1) * pct
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return sorted_values[lo]
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def metric(values: list[float]) -> dict[str, Any]:
    clean = sorted(v for v in values if v is not None and not math.isnan(v) and not math.isinf(v))
    if not clean:
        return {"样本数": 0}
    return {
        "样本数": len(clean),
        "最小值": round(clean[0], 6),
        "中位数": round(percentile(clean, 0.5) or 0.0, 6),
        "P90": round(percentile(clean, 0.9) or 0.0, 6),
        "P95": round(percentile(clean, 0.95) or 0.0, 6),
        "最大值": round(clean[-1], 6),
        "平均值": round(sum(clean) / len(clean), 6),
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns: list[str] = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def strategy_counts(conn: sqlite3.Connection, algorithm_version: str) -> dict[str, Any]:
    base = fetch_one(
        conn,
        """
        SELECT
            COUNT(*) AS "策略总数",
            SUM(CASE WHEN "渠道ID" = ? THEN 1 ELSE 0 END) AS "天天策略数"
        FROM "策略信息"
        """,
        (CHANNEL_ID,),
    )
    source_counts = fetch_dicts(
        conn,
        """
        SELECT "文件类型", COUNT(*) AS "记录数", COUNT(DISTINCT "采集批次ID") AS "采集批次数"
        FROM "数据来源清单"
        WHERE "渠道ID" = ?
        GROUP BY "文件类型"
        ORDER BY "文件类型"
        """,
        (CHANNEL_ID,),
    )
    table_rows = {}
    for table in ["策略信息", "策略调仓事件", "策略调仓明细", "策略当前持仓", "策略日度业绩", "策略区间业绩"]:
        table_rows[table] = fetch_one(
            conn,
            f'SELECT COUNT(*) AS "行数", COUNT(DISTINCT "统一策略ID") AS "策略数" FROM "{table}" WHERE "渠道ID" = ?',
            (CHANNEL_ID,),
        )
    quality = fetch_one(
        conn,
        """
        SELECT
            COUNT(*) AS "策略数",
            SUM(CASE WHEN "是否纳入模拟" = 1 THEN 1 ELSE 0 END) AS "纳入模拟策略数",
            SUM(CASE WHEN "是否纳入模拟" <> 1 THEN 1 ELSE 0 END) AS "未纳入模拟策略数",
            MIN("模拟起始日期") AS "模拟最早日期",
            MAX("模拟结束日期") AS "模拟最晚日期",
            SUM("模拟交易日数") AS "模拟交易日合计"
        FROM "策略模拟净值质量"
        WHERE "算法版本" = ? AND "渠道ID" = ?
        """,
        (algorithm_version, CHANNEL_ID),
    )
    return {
        "基础策略": base,
        "基础表行数": table_rows,
        "数据来源清单": source_counts,
        "模拟质量汇总": quality,
    }


def generated_versions(conn: sqlite3.Connection, algorithm_version: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    for table in GENERATED_VERSION_TABLES:
        if "算法版本" not in table_columns(conn, table):
            continue
        for row in fetch_dicts(
            conn,
            f'SELECT "算法版本", COUNT(*) AS "行数" FROM "{table}" GROUP BY "算法版本" ORDER BY "行数" DESC',
        ):
            row["表名"] = table
            row["是否当前算法"] = 1 if row["算法版本"] == algorithm_version else 0
            rows.append(row)
    old_rows = [row for row in rows if row["算法版本"] != algorithm_version]
    summary = {
        "含算法版本表数": len({row["表名"] for row in rows}),
        "算法版本行组数": len(rows),
        "旧算法行组数": len(old_rows),
        "旧算法总行数": sum(int(row["行数"] or 0) for row in old_rows),
    }
    return summary, rows


def fund_dependency_coverage(conn: sqlite3.Connection) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    refs = fetch_dicts(
        conn,
        """
        SELECT "统一策略ID", "基金代码", "基金名称", "调仓日期" AS "引用日期", '历史调仓' AS "引用来源"
        FROM "策略调仓明细"
        WHERE "渠道ID" = ? AND "基金代码" IS NOT NULL AND TRIM("基金代码") <> ''
        UNION ALL
        SELECT "统一策略ID", "基金代码", "基金名称", "持仓日期" AS "引用日期", '当前持仓' AS "引用来源"
        FROM "策略当前持仓"
        WHERE "渠道ID" = ? AND "基金代码" IS NOT NULL AND TRIM("基金代码") <> ''
        """,
        (CHANNEL_ID, CHANNEL_ID),
    )
    by_code: dict[str, dict[str, Any]] = {}
    for row in refs:
        code = str(row["基金代码"]).strip()
        if not code:
            continue
        item = by_code.setdefault(
            code,
            {
                "基金代码": code,
                "基金名称": row.get("基金名称"),
                "涉及策略集合": set(),
                "历史调仓引用次数": 0,
                "当前持仓引用次数": 0,
                "最早引用日期": row.get("引用日期"),
                "最晚引用日期": row.get("引用日期"),
            },
        )
        if row.get("基金名称"):
            item["基金名称"] = row.get("基金名称")
        item["涉及策略集合"].add(row["统一策略ID"])
        if row["引用来源"] == "历史调仓":
            item["历史调仓引用次数"] += 1
        else:
            item["当前持仓引用次数"] += 1
        ref_date = row.get("引用日期")
        if ref_date:
            if not item["最早引用日期"] or ref_date < item["最早引用日期"]:
                item["最早引用日期"] = ref_date
            if not item["最晚引用日期"] or ref_date > item["最晚引用日期"]:
                item["最晚引用日期"] = ref_date

    nav_profiles = {
        row["基金代码"]: row
        for row in fetch_dicts(
            conn,
            """
            SELECT
                "基金代码",
                MIN("交易日期") AS "净值起始日",
                MAX("交易日期") AS "净值结束日",
                COUNT(*) AS "净值记录数",
                SUM(CASE WHEN "单位净值" IS NOT NULL THEN 1 ELSE 0 END) AS "单位净值记录数",
                SUM(CASE WHEN "累计净值" IS NOT NULL THEN 1 ELSE 0 END) AS "累计净值记录数",
                SUM(CASE WHEN "日收益率_百分比" IS NOT NULL THEN 1 ELSE 0 END) AS "日收益率记录数",
                SUM(CASE WHEN "每万份收益" IS NOT NULL OR "七日年化收益率_百分比" IS NOT NULL THEN 1 ELSE 0 END) AS "货币收益记录数",
                MAX("是否货币基金") AS "是否货币基金",
                MAX("基金类型") AS "基金类型",
                MAX("基金公司") AS "基金公司"
            FROM "基金日度净值"
            GROUP BY "基金代码"
            """
        )
    }
    dividend_profiles = {
        row["基金代码"]: row
        for row in fetch_dicts(
            conn,
            """
            SELECT
                "基金代码",
                COUNT(*) AS "分红事件数",
                MIN(COALESCE("除息日", "权益登记日")) AS "最早分红日",
                MAX(COALESCE("除息日", "权益登记日")) AS "最晚分红日"
            FROM "基金分红送配"
            GROUP BY "基金代码"
            """
        )
    }
    meta_profiles = {
        row["基金代码"]: row
        for row in fetch_dicts(
            conn,
            """
            SELECT "基金代码", "基金名称", "基金公司", "基金类型", "分红事件数" AS "概况分红事件数",
                   "历史起始日期" AS "概况净值起始日", "历史结束日期" AS "概况净值结束日", "历史记录数" AS "概况净值记录数"
            FROM "基金净值概况"
            """
        )
    }

    rows: list[dict[str, Any]] = []
    for code in sorted(by_code):
        item = by_code[code]
        nav = nav_profiles.get(code) or {}
        div = dividend_profiles.get(code) or {}
        meta = meta_profiles.get(code) or {}
        nav_count = int(nav.get("净值记录数") or 0)
        return_count = sum(
            int(nav.get(key) or 0)
            for key in ["单位净值记录数", "累计净值记录数", "日收益率记录数", "货币收益记录数"]
        )
        div_count = int(div.get("分红事件数") or 0)
        meta_div_count = meta.get("概况分红事件数")
        issues: list[str] = []
        hard_gap = 0
        if nav_count == 0:
            issues.append("无基金净值记录")
            hard_gap = 1
        if nav_count > 0 and return_count == 0:
            issues.append("无可推导收益口径")
            hard_gap = 1
        if meta_div_count is not None and int(meta_div_count or 0) != div_count:
            issues.append("基金净值概况分红数与分红明细不一致")
        rows.append(
            {
                "基金代码": code,
                "基金名称": item.get("基金名称") or meta.get("基金名称"),
                "基金公司": meta.get("基金公司") or nav.get("基金公司"),
                "基金类型": meta.get("基金类型") or nav.get("基金类型"),
                "涉及策略数": len(item["涉及策略集合"]),
                "历史调仓引用次数": item["历史调仓引用次数"],
                "当前持仓引用次数": item["当前持仓引用次数"],
                "最早引用日期": item["最早引用日期"],
                "最晚引用日期": item["最晚引用日期"],
                "净值起始日": nav.get("净值起始日"),
                "净值结束日": nav.get("净值结束日"),
                "净值记录数": nav_count,
                "可推导收益记录口径数": return_count,
                "是否货币基金": int(nav.get("是否货币基金") or 0),
                "分红事件数": div_count,
                "概况分红事件数": meta_div_count,
                "最早分红日": div.get("最早分红日"),
                "最晚分红日": div.get("最晚分红日"),
                "是否计算硬缺口": hard_gap,
                "覆盖问题": "；".join(issues) if issues else "可用于计算",
            }
        )
    gaps = [row for row in rows if row["覆盖问题"] != "可用于计算"]
    hard_gaps = [row for row in rows if int(row["是否计算硬缺口"]) == 1]
    summary = {
        "涉及基金数": len(rows),
        "有净值基金数": sum(1 for row in rows if int(row["净值记录数"] or 0) > 0),
        "无净值基金数": sum(1 for row in rows if int(row["净值记录数"] or 0) == 0),
        "计算硬缺口基金数": len(hard_gaps),
        "有分红记录基金数": sum(1 for row in rows if int(row["分红事件数"] or 0) > 0),
        "分红事件数": sum(int(row["分红事件数"] or 0) for row in rows),
        "分红概况不一致基金数": sum(1 for row in rows if "分红数与分红明细不一致" in row["覆盖问题"]),
    }
    return summary, rows, gaps


def dividend_quality(conn: sqlite3.Connection) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    dividend_rows = fetch_dicts(
        conn,
        """
        SELECT "基金代码", "权益登记日", "除息日", COALESCE("除息日", "权益登记日") AS "分红确认日",
               "基金名称", "每份分红", "数据来源"
        FROM "基金分红送配"
        """
    )
    invalid: list[dict[str, Any]] = []
    split_or_conversion_rows = 0
    dividend_key_amounts: dict[tuple[str, str], list[float]] = {}
    for row in dividend_rows:
        dividend_text = str(row.get("每份分红") or "")
        if "拆分" in dividend_text or "折算" in dividend_text:
            split_or_conversion_rows += 1
            continue
        amount = parse_dividend_amount(row.get("每份分红"))
        date = row.get("分红确认日")
        code = row.get("基金代码")
        if not code or not date or amount is None:
            item = dict(row)
            item["问题"] = "缺少基金代码/分红日期或每份分红不可解析"
            invalid.append(item)
            continue
        dividend_key_amounts.setdefault((str(code), str(date)), []).append(amount)

    hint_rows = fetch_dicts(
        conn,
        """
        SELECT "基金代码", "交易日期", "基金名称", "净值图分红送配"
        FROM "基金日度净值"
        WHERE "净值图分红送配" IS NOT NULL
          AND TRIM("净值图分红送配") <> ''
          AND TRIM("净值图分红送配") <> '--'
        """
    )
    unmatched_hints: list[dict[str, Any]] = []
    for row in hint_rows:
        hint_text = str(row.get("净值图分红送配") or "")
        if "拆分" in hint_text or "折算" in hint_text:
            continue
        amount = parse_dividend_amount(row.get("净值图分红送配"))
        key = (str(row["基金代码"]), str(row["交易日期"]))
        candidates = dividend_key_amounts.get(key) or []
        matched = False
        if amount is not None:
            matched = any(abs(amount - candidate) <= 0.00005 for candidate in candidates)
        else:
            matched = bool(candidates)
        if not matched:
            item = dict(row)
            item["净值图解析每份分红"] = amount
            item["问题"] = "净值图分红提示未匹配到分红送配明细"
            unmatched_hints.append(item)

    dividend_without_nav = fetch_dicts(
        conn,
        """
        SELECT d."基金代码", d."权益登记日", d."除息日", COALESCE(d."除息日", d."权益登记日") AS "分红确认日",
               d."基金名称", d."每份分红", d."数据来源"
        FROM "基金分红送配" d
        LEFT JOIN "基金日度净值" n
          ON n."基金代码" = d."基金代码"
         AND n."交易日期" = COALESCE(d."除息日", d."权益登记日")
        WHERE n."基金代码" IS NULL
          AND d."每份分红" NOT LIKE '%拆分%'
          AND d."每份分红" NOT LIKE '%折算%'
        LIMIT 10000
        """
    )
    summary = {
        "分红明细事件数": len(dividend_rows),
        "分红基金数": len({row["基金代码"] for row in dividend_rows if row.get("基金代码")}),
        "拆分折算事件数": split_or_conversion_rows,
        "分红金额不可解析行数": len(invalid),
        "净值图分红提示行数": len(hint_rows),
        "净值图提示未匹配分红明细行数": len(unmatched_hints),
        "分红确认日无同日净值行数": len(dividend_without_nav),
    }
    gaps = invalid + unmatched_hints[:10000] + [dict(row, 问题="分红确认日无同日净值") for row in dividend_without_nav]
    return summary, gaps, unmatched_hints


def dividend_no_nav_impact(
    conn: sqlite3.Connection,
    algorithm_version: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    no_nav_rows = fetch_dicts(
        conn,
        """
        SELECT d."基金代码" AS "基金代码",
               COALESCE(d."除息日", d."权益登记日") AS "分红确认日",
               d."基金名称" AS "基金名称",
               d."每份分红" AS "每份分红"
        FROM "基金分红送配" d
        LEFT JOIN "基金日度净值" n
          ON n."基金代码" = d."基金代码"
         AND n."交易日期" = COALESCE(d."除息日", d."权益登记日")
        WHERE n."基金代码" IS NULL
          AND d."每份分红" NOT LIKE '%拆分%'
          AND d."每份分红" NOT LIKE '%折算%'
        """
    )
    held_by_fund: dict[str, list[dict[str, Any]]] = {}
    held_rows = fetch_dicts(
        conn,
        """
        SELECT seg."统一策略ID",
               seg."策略名称",
               seg."区间开始日期",
               seg."区间结束日期",
               d."基金代码",
               d."调后权重_百分比"
        FROM "策略模拟净值区间" seg
        JOIN "策略调仓明细" d INDEXED BY "idx_策略调仓明细_事件_基金"
          ON d."调仓事件ID" = seg."调仓事件ID"
        WHERE seg."算法版本" = ?
          AND seg."渠道ID" = ?
          AND seg."是否纳入模拟" = 1
          AND seg."区间是否有效" = 1
          AND d."基金代码" IS NOT NULL
          AND d."调后权重_百分比" > 0
        """,
        (algorithm_version, CHANNEL_ID),
    )
    for row in held_rows:
        held_by_fund.setdefault(str(row["基金代码"]), []).append(row)

    affected: list[dict[str, Any]] = []
    affected_strategy_ids: set[str] = set()
    for row in no_nav_rows:
        div_date = row.get("分红确认日")
        if not div_date:
            continue
        hits = []
        for holding in held_by_fund.get(str(row.get("基金代码")), []):
            start_date = holding.get("区间开始日期")
            end_date = holding.get("区间结束日期")
            if start_date and end_date and div_date > start_date and div_date <= end_date:
                hits.append(holding)
        if not hits:
            continue
        strategy_ids = sorted({str(hit["统一策略ID"]) for hit in hits})
        affected_strategy_ids.update(strategy_ids)
        affected.append(
            {
                **row,
                "影响策略数": len(strategy_ids),
                "影响策略ID示例": ",".join(strategy_ids[:20]),
            }
        )
    return (
        {
            "分红确认日无同日净值行数": len(no_nav_rows),
            "命中持有区间分红行数": len(affected),
            "影响策略数": len(affected_strategy_ids),
        },
        affected,
    )


def nav_reconstruction_quality(conn: sqlite3.Connection, algorithm_version: str) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    quality_rows = fetch_dicts(
        conn,
        """
        SELECT *
        FROM "策略模拟净值质量"
        WHERE "算法版本" = ? AND "渠道ID" = ?
        """,
        (algorithm_version, CHANNEL_ID),
    )
    not_included = [row for row in quality_rows if int(row.get("是否纳入模拟") or 0) != 1]
    included = [row for row in quality_rows if int(row.get("是否纳入模拟") or 0) == 1]
    segment_summary = fetch_one(
        conn,
        """
        SELECT
            COUNT(*) AS "区间数",
            SUM(CASE WHEN "区间是否有效" = 1 THEN 1 ELSE 0 END) AS "有效区间数",
            SUM(CASE WHEN "区间是否有效" <> 1 THEN 1 ELSE 0 END) AS "无效区间数",
            SUM(COALESCE("缺净值基金数", 0)) AS "缺净值基金数合计",
            SUM(COALESCE("起始覆盖不足基金数", 0)) AS "起始覆盖不足基金数合计",
            SUM(COALESCE("结束覆盖不足基金数", 0)) AS "结束覆盖不足基金数合计",
            SUM(COALESCE("缺失日收益填补点数", 0)) AS "缺失日收益填补点数合计"
        FROM "策略模拟净值区间"
        WHERE "算法版本" = ? AND "渠道ID" = ?
        """,
        (algorithm_version, CHANNEL_ID),
    )
    nav_count = fetch_one(
        conn,
        """
        SELECT COUNT(*) AS "模拟净值行数", COUNT(DISTINCT "统一策略ID") AS "模拟净值策略数",
               MIN("交易日期") AS "最早交易日", MAX("交易日期") AS "最晚交易日"
        FROM "策略模拟净值"
        WHERE "算法版本" = ? AND "渠道ID" = ?
        """,
        (algorithm_version, CHANNEL_ID),
    )
    reason_counts = Counter(str(row.get("首个问题类型") or "未标注") for row in not_included)
    repair_counts = Counter()
    for row in quality_rows:
        repairs = str(row.get("修复说明") or "")
        if "清盘/止盈空仓" in repairs:
            repair_counts["清盘/止盈空仓"] += 1
        if "调前权重缺失" in repairs:
            repair_counts["调前权重缺失占位行剔除"] += 1
        if "同日重复事件" in repairs:
            repair_counts["同日重复事件折叠"] += 1
        if int(row.get("缺失投顾费率按0处理") or 0) == 1:
            repair_counts["缺投顾费率按0处理"] += 1
    examples = sorted(
        [
            {
                "统一策略ID": row.get("统一策略ID"),
                "渠道策略ID": row.get("渠道策略ID"),
                "策略名称": row.get("策略名称"),
                "投顾机构": row.get("投顾机构"),
                "首个问题日期": row.get("首个问题日期"),
                "首个问题类型": row.get("首个问题类型"),
                "问题说明": row.get("问题说明"),
                "修复说明": row.get("修复说明"),
            }
            for row in not_included
        ],
        key=lambda row: (str(row.get("首个问题类型") or ""), str(row.get("统一策略ID") or "")),
    )
    summary = {
        "算法版本": algorithm_version,
        "质量记录策略数": len(quality_rows),
        "纳入模拟策略数": len(included),
        "未纳入模拟策略数": len(not_included),
        "未纳入原因分布": dict(reason_counts),
        "修复策略数分布": dict(repair_counts),
        "模拟净值": nav_count,
        "区间质量": segment_summary,
    }
    return summary, examples, quality_rows


def current_projection_quality(conn: sqlite3.Connection) -> dict[str, Any]:
    if not table_exists(conn, "最新持仓推算稽核策略汇总"):
        return {"可用": False, "说明": "未生成最新持仓推算稽核表"}
    rows = fetch_dicts(
        conn,
        'SELECT * FROM "最新持仓推算稽核策略汇总" WHERE "渠道ID" = ?',
        (CHANNEL_ID,),
    )
    comparable = [
        row
        for row in rows
        if row.get("最大绝对差_百分点") is not None
        and row.get("稽核状态") in {"通过", "小额差异", "需复核", "结构不一致"}
    ]
    comparable_no_mismatch = [row for row in comparable if row.get("稽核状态") != "结构不一致"]
    return {
        "可用": True,
        "策略数": len(rows),
        "状态分布": dict(Counter(str(row.get("稽核状态") or "未标注") for row in rows)),
        "归因分布": dict(Counter(str(row.get("归因分类") or "未标注") for row in rows)),
        "可推算补齐策略数": sum(int(row.get("是否可推算补齐") or 0) for row in rows),
        "缺净值影响策略数": sum(1 for row in rows if int(row.get("缺净值基金数") or 0) > 0),
        "可比策略数": len(comparable),
        "可比最大绝对差统计": metric([to_float(row.get("最大绝对差_百分点")) or 0.0 for row in comparable]),
        "剔除结构不一致最大绝对差统计": metric(
            [to_float(row.get("最大绝对差_百分点")) or 0.0 for row in comparable_no_mismatch]
        ),
    }


def official_deviation_quality(conn: sqlite3.Connection, algorithm_version: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows = fetch_dicts(
        conn,
        """
        SELECT *
        FROM "策略官方偏差分析"
        WHERE "算法版本" = ? AND "渠道ID" = ?
        """,
        (algorithm_version, CHANNEL_ID),
    )
    net_abs = [abs(to_float(row.get("费后官方偏差_百分点")) or 0.0) for row in rows]
    gross_abs = [abs(to_float(row.get("费前官方偏差_百分点")) or 0.0) for row in rows]
    best_abs = [min(a, b) for a, b in zip(net_abs, gross_abs)]
    worst = sorted(
        [
            {
                "统一策略ID": row.get("统一策略ID"),
                "渠道策略ID": row.get("渠道策略ID"),
                "策略名称": row.get("策略名称"),
                "质量等级": row.get("质量等级"),
                "官方可比记录数": row.get("官方可比记录数"),
                "官方起始日期": row.get("官方起始日期"),
                "官方结束日期": row.get("官方结束日期"),
                "官方区间收益率_百分比": row.get("官方区间收益率_百分比"),
                "费后模拟同区间收益率_百分比": row.get("费后模拟同区间收益率_百分比"),
                "费前模拟同区间收益率_百分比": row.get("费前模拟同区间收益率_百分比"),
                "费后官方偏差_百分点": row.get("费后官方偏差_百分点"),
                "费前官方偏差_百分点": row.get("费前官方偏差_百分点"),
                "最优绝对偏差_百分点": min(
                    abs(to_float(row.get("费后官方偏差_百分点")) or 0.0),
                    abs(to_float(row.get("费前官方偏差_百分点")) or 0.0),
                ),
                "官方更接近口径": row.get("官方更接近口径"),
                "推断原因": row.get("推断原因"),
            }
            for row in rows
        ],
        key=lambda item: to_float(item["最优绝对偏差_百分点"]) or 0.0,
        reverse=True,
    )
    return (
        {
            "官方可比策略数": len(rows),
            "费后绝对偏差统计": metric(net_abs),
            "费前绝对偏差统计": metric(gross_abs),
            "最优口径绝对偏差统计": metric(best_abs),
            "官方更接近口径分布": dict(Counter(str(row.get("官方更接近口径") or "未标注") for row in rows)),
        },
        worst[:100],
    )


def raw_process_checks(conn: sqlite3.Connection) -> dict[str, Any]:
    source_missing: dict[str, Any] = {}
    for table, source_col in [
        ("策略调仓事件", "原始快照ID"),
        ("策略调仓明细", "原始快照ID"),
        ("策略当前持仓", "原始快照ID"),
        ("策略日度业绩", "原始快照ID"),
        ("策略区间业绩", "原始快照ID"),
    ]:
        source_missing[table] = fetch_one(
            conn,
            f"""
            SELECT COUNT(*) AS "行数",
                   SUM(CASE WHEN "{source_col}" IS NULL OR TRIM("{source_col}") = '' THEN 1 ELSE 0 END) AS "缺原始快照ID行数",
                   COUNT(DISTINCT "统一策略ID") AS "策略数"
            FROM "{table}"
            WHERE "渠道ID" = ?
            """,
            (CHANNEL_ID,),
        )
    duplicate_keys: dict[str, int] = {}
    duplicate_specs = {
        "策略调仓事件": '"调仓事件ID"',
        "策略调仓明细": '"调仓明细ID"',
        "策略当前持仓": '"统一策略ID", "持仓日期", "基金名称"',
        "策略日度业绩": '"统一策略ID", "交易日期"',
        "策略区间业绩": '"统一策略ID", "统计日期", "区间代码"',
    }
    for table, keys in duplicate_specs.items():
        duplicate_keys[table] = int(
            fetch_one(
                conn,
                f"""
                SELECT COUNT(*) AS "重复键组数"
                FROM (
                    SELECT {keys}, COUNT(*) AS c
                    FROM "{table}"
                    WHERE "渠道ID" = ?
                    GROUP BY {keys}
                    HAVING c > 1
                )
                """,
                (CHANNEL_ID,),
            ).get("重复键组数")
            or 0
        )
    return {"原始快照ID覆盖": source_missing, "入库主键重复组数": duplicate_keys}


def write_report(output_dir: Path, payload: dict[str, Any], paths: dict[str, str]) -> Path:
    s = payload
    lines = [
        "# 天天基金投顾最终数据完整性稽核报告",
        "",
        f"- 生成时间：{s['生成时间']}",
        f"- 数据库：`{s['数据库']}`",
        f"- 算法版本：`{s['算法版本']}`",
        "",
        "## 结论",
    ]
    fund_summary = s["基金依赖覆盖"]
    dividend_summary = s["分红质量"]
    dividend_impact = s.get("分红无同日净值影响", {})
    nav_summary = s["策略净值回放"]
    projection_summary = s["最新持仓推算稽核"]
    old_summary = s["旧算法产物"]
    hard_ok = int(fund_summary.get("计算硬缺口基金数") or 0) == 0
    dividend_ok = (
        int(dividend_summary.get("分红金额不可解析行数") or 0) == 0
        and int(dividend_summary.get("净值图提示未匹配分红明细行数") or 0) == 0
    )
    lines.extend(
        [
            f"- 基金净值依赖：{'完整' if hard_ok else '存在硬缺口'}。涉及基金 {fund_summary.get('涉及基金数')} 只，"
            f"无净值基金 {fund_summary.get('无净值基金数')} 只，计算硬缺口 {fund_summary.get('计算硬缺口基金数')} 只。",
            f"- 分红依赖：{'自洽' if dividend_ok else '存在需复核项'}。分红明细 {dividend_summary.get('分红明细事件数')} 条，"
            f"净值图分红提示未匹配 {dividend_summary.get('净值图提示未匹配分红明细行数')} 条，"
            f"不可解析 {dividend_summary.get('分红金额不可解析行数')} 条；"
            f"无同日净值分红命中持有区间 {dividend_impact.get('命中持有区间分红行数')} 条，"
            f"影响策略 {dividend_impact.get('影响策略数')} 个。",
            f"- 策略净值：纳入模拟 {nav_summary.get('纳入模拟策略数')}/{nav_summary.get('质量记录策略数')}，"
            f"未纳入 {nav_summary.get('未纳入模拟策略数')}。",
            f"- 最新持仓：可推算补齐 {projection_summary.get('可推算补齐策略数')} 个策略，"
            f"缺净值影响策略 {projection_summary.get('缺净值影响策略数')} 个。",
            f"- 旧算法产物：旧算法行组 {old_summary.get('旧算法行组数')}，旧算法行数 {old_summary.get('旧算法总行数')}。",
        ]
    )
    lines.extend(
        [
            "",
            "## 核心数据量",
            f"- 天天策略数：{s['策略与来源']['基础策略'].get('天天策略数')}",
            f"- 调仓事件：{s['策略与来源']['基础表行数']['策略调仓事件'].get('行数')} 行，"
            f"调仓明细：{s['策略与来源']['基础表行数']['策略调仓明细'].get('行数')} 行。",
            f"- 当前持仓：{s['策略与来源']['基础表行数']['策略当前持仓'].get('行数')} 行。",
            f"- 官方日度业绩：{s['策略与来源']['基础表行数']['策略日度业绩'].get('行数')} 行，"
            f"官方区间业绩：{s['策略与来源']['基础表行数']['策略区间业绩'].get('行数')} 行。",
            "",
            "## 未纳入模拟原因",
        ]
    )
    for reason, count in sorted(nav_summary.get("未纳入原因分布", {}).items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"- {reason}：{count} 个策略")
    lines.extend(["", "## 当前持仓推算比对"])
    lines.append(f"- 状态分布：{json.dumps(projection_summary.get('状态分布', {}), ensure_ascii=False)}")
    lines.append(f"- 归因分布：{json.dumps(projection_summary.get('归因分布', {}), ensure_ascii=False)}")
    lines.append(f"- 剔除结构不一致最大绝对差统计：{json.dumps(projection_summary.get('剔除结构不一致最大绝对差统计', {}), ensure_ascii=False)}")
    lines.extend(["", "## 官方业绩偏差"])
    lines.append(f"- 官方可比策略数：{s['官方业绩偏差'].get('官方可比策略数')}")
    lines.append(f"- 最优口径绝对偏差统计：{json.dumps(s['官方业绩偏差'].get('最优口径绝对偏差统计', {}), ensure_ascii=False)}")
    lines.extend(["", "## 输出文件"])
    for name, path in paths.items():
        lines.append(f"- {name}：`{path}`")
    report_path = output_dir / "final_integrity_report.md"
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_path


def main() -> None:
    args = parse_args()
    output_dir = args.output_dir or default_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(args.db_path)
    conn.row_factory = sqlite3.Row
    try:
        old_summary, old_rows = generated_versions(conn, args.algorithm_version)
        strategy_summary = strategy_counts(conn, args.algorithm_version)
        fund_summary, fund_rows, fund_gaps = fund_dependency_coverage(conn)
        dividend_summary, dividend_gaps, unmatched_hints = dividend_quality(conn)
        dividend_impact_summary, dividend_impact_rows = dividend_no_nav_impact(conn, args.algorithm_version)
        nav_summary, not_included_rows, _quality_rows = nav_reconstruction_quality(conn, args.algorithm_version)
        projection_summary = current_projection_quality(conn)
        official_summary, official_worst = official_deviation_quality(conn, args.algorithm_version)
        raw_checks = raw_process_checks(conn)
    finally:
        conn.close()

    paths: dict[str, str] = {}
    csv_outputs = {
        "基金依赖覆盖明细": ("fund_dependency_coverage.csv", fund_rows),
        "基金依赖问题": ("fund_dependency_gaps.csv", fund_gaps),
        "分红问题": ("dividend_gaps.csv", dividend_gaps),
        "净值图分红未匹配": ("dividend_hint_unmatched.csv", unmatched_hints[:10000]),
        "未纳入模拟策略": ("nav_not_included_strategies.csv", not_included_rows),
        "官方偏差最大策略": ("official_deviation_worst.csv", official_worst),
        "算法版本产物": ("generated_algorithm_versions.csv", old_rows),
        "无同日净值分红影响策略": ("dividend_no_nav_impact.csv", dividend_impact_rows),
    }
    for label, (file_name, rows) in csv_outputs.items():
        path = output_dir / file_name
        write_csv(path, rows)
        paths[label] = str(path)

    payload = {
        "生成时间": datetime.now().astimezone().isoformat(timespec="seconds"),
        "数据库": str(args.db_path.resolve()),
        "算法版本": args.algorithm_version,
        "策略与来源": strategy_summary,
        "旧算法产物": old_summary,
        "基金依赖覆盖": fund_summary,
        "分红质量": dividend_summary,
        "分红无同日净值影响": dividend_impact_summary,
        "策略净值回放": nav_summary,
        "最新持仓推算稽核": projection_summary,
        "官方业绩偏差": official_summary,
        "原始数据与入库过程检查": raw_checks,
        "输出文件": paths,
    }
    summary_path = output_dir / "final_integrity_summary.json"
    summary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    paths["汇总JSON"] = str(summary_path)
    report_path = write_report(output_dir, payload, paths)
    payload["输出文件"]["报告"] = str(report_path)
    summary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
