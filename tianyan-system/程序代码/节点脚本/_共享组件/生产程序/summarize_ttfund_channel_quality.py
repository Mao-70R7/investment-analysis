from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "ttfund_channel_quality"
DEFAULT_ALGORITHM_VERSION = "rebalance_asset_fee_dual_nav_v4_ttfund_20260527"
CHANNEL_ID = "ttfund"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize Tiantian advisor strategy data quality.")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--algorithm-version", default=DEFAULT_ALGORITHM_VERSION)
    return parser.parse_args()


def fetch_dicts(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    return [dict(row) for row in conn.execute(sql, params)]


def fetch_one(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any]:
    rows = fetch_dicts(conn, sql, params)
    return rows[0] if rows else {}


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    return (
        conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name = ?",
            (table_name,),
        ).fetchone()[0]
        > 0
    )


def norm_code(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    digits = "".join(ch for ch in text if ch.isdigit())
    if digits and len(digits) <= 6:
        return digits.zfill(6)
    return text


def to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and math.isnan(value):
            return None
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    if text.endswith("%"):
        text = text[:-1]
    try:
        return float(text)
    except ValueError:
        return None


def ymd_to_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def percentile(values: list[float], p: float) -> float | None:
    clean = sorted(v for v in values if v is not None and math.isfinite(v))
    if not clean:
        return None
    if len(clean) == 1:
        return round(clean[0], 6)
    pos = (len(clean) - 1) * p
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return round(clean[lo], 6)
    weight = pos - lo
    return round(clean[lo] * (1 - weight) + clean[hi] * weight, 6)


def pct(numerator: int | float | None, denominator: int | float | None) -> float | None:
    if denominator in (None, 0) or numerator is None:
        return None
    return round(float(numerator) / float(denominator) * 100.0, 4)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    headers: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                headers.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def metric(values: list[float]) -> dict[str, Any]:
    clean = [v for v in values if v is not None and math.isfinite(v)]
    if not clean:
        return {"样本数": 0}
    return {
        "样本数": len(clean),
        "均值": round(sum(clean) / len(clean), 6),
        "中位数": percentile(clean, 0.5),
        "P75": percentile(clean, 0.75),
        "P90": percentile(clean, 0.9),
        "P95": percentile(clean, 0.95),
        "最大值": round(max(clean), 6),
    }


def issue_group(issue: str | None) -> str:
    text = issue or "未标明"
    if text.startswith("调后权重和不闭合"):
        return "调后权重和不闭合"
    if text.startswith("调仓日起始净值覆盖不足"):
        return "调仓日起始净值覆盖不足"
    if text.startswith("区间结束净值覆盖不足"):
        return "区间结束净值覆盖不足"
    if text.startswith("缺基金净值"):
        return "缺基金净值"
    return text


def is_qdii_like(row: dict[str, Any]) -> bool:
    text = " ".join(
        str(row.get(key) or "")
        for key in ["基金名称", "基金类型", "基金公司"]
    )
    keywords = [
        "QDII",
        "全球",
        "海外",
        "美元",
        "港股",
        "香港",
        "纳斯达克",
        "标普",
        "印度",
        "日本",
        "越南",
        "互认",
    ]
    return any(keyword.lower() in text.lower() for keyword in keywords)


def build_fund_coverage(conn: sqlite3.Connection, latest_nav_date: str | None) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    refs = fetch_dicts(
        conn,
        """
        SELECT "统一策略ID", "基金代码", "基金名称", "调仓日期" AS ref_date, '调仓明细' AS ref_type
        FROM "策略调仓明细"
        WHERE "渠道ID" = ? AND "基金代码" IS NOT NULL AND TRIM("基金代码") <> ''
        UNION ALL
        SELECT "统一策略ID", "基金代码", "基金名称", "持仓日期" AS ref_date, '当前持仓' AS ref_type
        FROM "策略当前持仓"
        WHERE "渠道ID" = ? AND "基金代码" IS NOT NULL AND TRIM("基金代码") <> ''
        """,
        (CHANNEL_ID, CHANNEL_ID),
    )
    by_code: dict[str, dict[str, Any]] = {}
    for row in refs:
        code = norm_code(row["基金代码"])
        if not code:
            continue
        item = by_code.setdefault(
            code,
            {
                "基金代码": code,
                "基金名称": row.get("基金名称"),
                "涉及策略集合": set(),
                "当前持仓引用次数": 0,
                "调仓明细引用次数": 0,
                "最早引用日期": row.get("ref_date"),
                "最晚引用日期": row.get("ref_date"),
            },
        )
        if row.get("基金名称"):
            item["基金名称"] = row.get("基金名称")
        item["涉及策略集合"].add(row["统一策略ID"])
        if row["ref_type"] == "当前持仓":
            item["当前持仓引用次数"] += 1
        else:
            item["调仓明细引用次数"] += 1
        ref_date = row.get("ref_date")
        if ref_date:
            if not item["最早引用日期"] or ref_date < item["最早引用日期"]:
                item["最早引用日期"] = ref_date
            if not item["最晚引用日期"] or ref_date > item["最晚引用日期"]:
                item["最晚引用日期"] = ref_date

    nav_profiles = {
        norm_code(row["基金代码"]): row
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
                MAX("基金公司") AS "基金公司",
                MAX("基金名称") AS "净值基金名称"
            FROM "基金日度净值"
            GROUP BY "基金代码"
            """,
        )
    }
    dividend_profiles = {
        norm_code(row["基金代码"]): row
        for row in fetch_dicts(
            conn,
            """
            SELECT
                "基金代码",
                COUNT(*) AS "分红事件数",
                MIN(COALESCE("除息日", "权益登记日")) AS "最早分红日期",
                MAX(COALESCE("除息日", "权益登记日")) AS "最晚分红日期"
            FROM "基金分红送配"
            GROUP BY "基金代码"
            """,
        )
    }
    info_profiles = {
        norm_code(row["基金代码"]): row
        for row in fetch_dicts(
            conn,
            """
            SELECT "基金代码", "基金名称", "基金公司", "基金类型", "基金状态", "数据来源"
            FROM "基金信息"
            """,
        )
    }

    global_latest = ymd_to_date(latest_nav_date)
    rows: list[dict[str, Any]] = []
    for code, item in sorted(by_code.items()):
        nav = nav_profiles.get(code) or {}
        div = dividend_profiles.get(code) or {}
        info = info_profiles.get(code) or {}
        name = item.get("基金名称") or info.get("基金名称") or nav.get("净值基金名称")
        fund_type = info.get("基金类型") or nav.get("基金类型")
        fund_company = info.get("基金公司") or nav.get("基金公司")
        nav_count = int(nav.get("净值记录数") or 0)
        unit_count = int(nav.get("单位净值记录数") or 0)
        accum_count = int(nav.get("累计净值记录数") or 0)
        daily_count = int(nav.get("日收益率记录数") or 0)
        money_count = int(nav.get("货币收益记录数") or 0)
        is_money = int(nav.get("是否货币基金") or 0)
        has_total_return = (money_count > 0 or daily_count > 0 or accum_count > 0 or unit_count > 0)
        max_nav = nav.get("净值结束日")
        max_nav_date = ymd_to_date(max_nav)
        lag_days = (global_latest - max_nav_date).days if global_latest and max_nav_date else None
        row = {
            "基金代码": code,
            "基金名称": name,
            "基金公司": fund_company,
            "基金类型": fund_type,
            "基金状态": info.get("基金状态"),
            "涉及策略数": len(item["涉及策略集合"]),
            "当前持仓引用次数": item["当前持仓引用次数"],
            "调仓明细引用次数": item["调仓明细引用次数"],
            "最早引用日期": item["最早引用日期"],
            "最晚引用日期": item["最晚引用日期"],
            "净值起始日": nav.get("净值起始日"),
            "净值结束日": max_nav,
            "净值记录数": nav_count,
            "单位净值记录数": unit_count,
            "累计净值记录数": accum_count,
            "日收益率记录数": daily_count,
            "货币收益记录数": money_count,
            "是否货币基金": is_money,
            "有可用总收益口径": 1 if has_total_return else 0,
            "分红事件数": int(div.get("分红事件数") or 0),
            "最早分红日期": div.get("最早分红日期"),
            "最晚分红日期": div.get("最晚分红日期"),
            "相对全库最新净值滞后天数": lag_days,
            "是否QDII或海外类": 1 if is_qdii_like({"基金名称": name, "基金类型": fund_type, "基金公司": fund_company}) else 0,
        }
        issues: list[str] = []
        hard_gap = False
        if nav_count == 0:
            issues.append("无净值")
            hard_gap = True
        if nav_count > 0 and not has_total_return:
            issues.append("无可用总收益口径")
            hard_gap = True
        if item["当前持仓引用次数"] > 0 and lag_days is not None and lag_days > 10:
            issues.append("当前持仓基金净值长期滞后")
            hard_gap = True
        if lag_days is not None and lag_days > 180:
            issues.append("历史净值长期停更")
        row["是否计算硬缺口"] = 1 if hard_gap else 0
        row["覆盖问题"] = "、".join(issues) if issues else "可用于计算"
        rows.append(row)

    gaps = [row for row in rows if row["覆盖问题"] != "可用于计算"]
    hard_gaps = [row for row in rows if int(row["是否计算硬缺口"] or 0) == 1]
    summary = {
        "涉及基金数": len(rows),
        "有净值基金数": sum(1 for row in rows if int(row["净值记录数"] or 0) > 0),
        "无净值基金数": sum(1 for row in rows if int(row["净值记录数"] or 0) == 0),
        "有可用总收益口径基金数": sum(1 for row in rows if int(row["有可用总收益口径"] or 0) == 1),
        "无可用总收益口径基金数": sum(1 for row in rows if int(row["有可用总收益口径"] or 0) == 0),
        "有分红记录基金数": sum(1 for row in rows if int(row["分红事件数"] or 0) > 0),
        "分红事件数": sum(int(row["分红事件数"] or 0) for row in rows),
        "QDII或海外类基金数": sum(int(row["是否QDII或海外类"] or 0) for row in rows),
        "计算硬缺口基金数": len(hard_gaps),
        "覆盖提示基金数": len(gaps),
        "仅历史净值长期停更基金数": sum(
            1
            for row in rows
            if int(row["是否计算硬缺口"] or 0) == 0 and "历史净值长期停更" in str(row["覆盖问题"])
        ),
    }
    return summary, rows, gaps


def summarize_projection(conn: sqlite3.Connection) -> dict[str, Any]:
    table = "最新持仓推算稽核策略汇总"
    if not table_exists(conn, table):
        return {"可用": False}
    rows = fetch_dicts(conn, f'SELECT * FROM "{table}" WHERE "渠道ID" = ?', (CHANNEL_ID,))
    comparable = [
        row
        for row in rows
        if row.get("最大绝对差_百分点") is not None
        and row.get("稽核状态") in ("通过", "小额差异", "需复核", "结构不一致")
    ]
    comparable_without_mismatch = [row for row in comparable if row.get("稽核状态") != "结构不一致"]
    max_diffs = [to_float(row.get("最大绝对差_百分点")) or 0.0 for row in comparable]
    max_diffs_no_mismatch = [to_float(row.get("最大绝对差_百分点")) or 0.0 for row in comparable_without_mismatch]
    status_counts = Counter(str(row.get("稽核状态") or "未标明") for row in rows)
    reason_counts = Counter(str(row.get("归因分类") or "未标明") for row in rows)
    inferred_strategy_count = sum(
        1
        for row in rows
        if str(row.get("稽核状态") or "") == "无当前披露持仓" and int(row.get("是否可推算补齐") or 0) == 1
    )
    if table_exists(conn, "策略当前持仓推算补齐"):
        inferred_strategy_count = int(
            conn.execute(
                'SELECT COUNT(DISTINCT "统一策略ID") FROM "策略当前持仓推算补齐" WHERE "渠道ID" = ?',
                (CHANNEL_ID,),
            ).fetchone()[0]
            or 0
        )
    return {
        "可用": True,
        "策略数": len(rows),
        "状态分布": dict(status_counts),
        "归因分布": dict(reason_counts),
        "可比策略数": len(comparable),
        "可比策略最大差统计": metric(max_diffs),
        "剔除结构不一致最大差统计": metric(max_diffs_no_mismatch),
        "可推算策略数": sum(int(row.get("是否可推算补齐") or 0) for row in rows),
        "当前缺失但可推算补齐策略数": inferred_strategy_count,
        "缺净值影响策略数": sum(1 for row in rows if int(row.get("缺净值基金数") or 0) > 0),
    }


def summarize_nav(conn: sqlite3.Connection, algorithm_version: str) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    rows = fetch_dicts(
        conn,
        """
        SELECT *
        FROM "策略模拟净值质量"
        WHERE "算法版本" = ? AND "渠道ID" = ?
        """,
        (algorithm_version, CHANNEL_ID),
    )
    included = [row for row in rows if int(row.get("是否纳入模拟") or 0) == 1]
    not_included = [row for row in rows if int(row.get("是否纳入模拟") or 0) != 1]
    comparable = [
        row
        for row in included
        if int(row.get("官方可比记录数") or 0) > 0
        and row.get("模拟官方收益差_百分点") is not None
        and row.get("模拟费前官方收益差_百分点") is not None
    ]
    net_abs = [abs(to_float(row.get("模拟官方收益差_百分点")) or 0.0) for row in comparable]
    gross_abs = [abs(to_float(row.get("模拟费前官方收益差_百分点")) or 0.0) for row in comparable]
    best_abs = [min(a, b) for a, b in zip(net_abs, gross_abs)]
    threshold_rows = []
    for threshold in [0.1, 0.3, 0.5, 1.0, 2.0, 3.0]:
        count = sum(1 for value in best_abs if value <= threshold)
        threshold_rows.append({"阈值_百分点": threshold, "策略数": count, "占比_百分比": pct(count, len(best_abs))})
    issue_counts = Counter(issue_group(row.get("首个问题类型")) for row in not_included)
    worst = sorted(
        [
            {
                "统一策略ID": row.get("统一策略ID"),
                "策略名称": row.get("策略名称"),
                "投顾机构": row.get("投顾机构"),
                "官方可比记录数": row.get("官方可比记录数"),
                "官方起始日期": row.get("官方起始日期"),
                "官方结束日期": row.get("官方结束日期"),
                "官方区间收益率_百分比": row.get("官方区间收益率_百分比"),
                "模拟同区间收益率_百分比": row.get("模拟同区间收益率_百分比"),
                "模拟官方收益差_百分点": row.get("模拟官方收益差_百分点"),
                "模拟费前官方收益差_百分点": row.get("模拟费前官方收益差_百分点"),
                "最优绝对偏差_百分点": min(
                    abs(to_float(row.get("模拟官方收益差_百分点")) or 0.0),
                    abs(to_float(row.get("模拟费前官方收益差_百分点")) or 0.0),
                ),
            }
            for row in comparable
        ],
        key=lambda row: to_float(row["最优绝对偏差_百分点"]) or 0.0,
        reverse=True,
    )
    summary = {
        "策略数": len(rows),
        "纳入模拟策略数": len(included),
        "未纳入模拟策略数": len(not_included),
        "纳入模拟占比_百分比": pct(len(included), len(rows)),
        "官方可比策略数": len(comparable),
        "费后绝对偏差统计": metric(net_abs),
        "费前绝对偏差统计": metric(gross_abs),
        "最优口径绝对偏差统计": metric(best_abs),
        "未纳入原因分布": dict(issue_counts),
        "偏差阈值分布": threshold_rows,
        "官方更接近口径分布": dict(Counter(str(row.get("官方更接近口径") or "未标明") for row in comparable)),
    }
    return summary, threshold_rows, worst


def summarize_raw_quality(conn: sqlite3.Connection) -> dict[str, Any]:
    strategy = fetch_one(
        conn,
        """
        SELECT
            COUNT(*) AS "策略数",
            COUNT(DISTINCT COALESCE("投顾机构", '未披露投顾机构')) AS "投顾机构数",
            MIN("成立日期") AS "最早成立日期",
            MAX("最近入库时间") AS "最近入库时间"
        FROM "策略信息"
        WHERE "渠道ID" = ?
        """,
        (CHANNEL_ID,),
    )
    official = fetch_one(
        conn,
        """
        SELECT
            COUNT(*) AS "策略日度业绩行数",
            COUNT(DISTINCT "统一策略ID") AS "有日度业绩策略数",
            MIN("交易日期") AS "业绩最早日期",
            MAX("交易日期") AS "业绩最新日期"
        FROM "策略日度业绩"
        WHERE "渠道ID" = ?
        """,
        (CHANNEL_ID,),
    )
    current = fetch_one(
        conn,
        """
        SELECT
            COUNT(*) AS "当前持仓行数",
            COUNT(DISTINCT "统一策略ID") AS "有当前持仓策略数",
            COUNT(DISTINCT CASE WHEN "基金权重_百分比" IS NOT NULL THEN "统一策略ID" END) AS "有当前基金权重策略数",
            COUNT(DISTINCT CASE WHEN "基金权重_百分比" IS NOT NULL AND "基金权重_百分比" > 0 THEN "统一策略ID" END) AS "有当前正权重策略数"
        FROM "策略当前持仓"
        WHERE "渠道ID" = ?
        """,
        (CHANNEL_ID,),
    )
    rebalance = fetch_one(
        conn,
        """
        SELECT
            COUNT(*) AS "调仓事件数",
            COUNT(DISTINCT "统一策略ID") AS "有调仓事件策略数",
            MIN("调仓日期") AS "调仓最早日期",
            MAX("调仓日期") AS "调仓最新日期"
        FROM "策略调仓事件"
        WHERE "渠道ID" = ?
        """,
        (CHANNEL_ID,),
    )
    detail = fetch_one(
        conn,
        """
        SELECT
            COUNT(*) AS "调仓基金明细行数",
            COUNT(DISTINCT "统一策略ID") AS "有调仓基金明细策略数",
            COUNT(DISTINCT CASE WHEN "调后权重_百分比" IS NOT NULL AND "调后权重_百分比" > 0 THEN "统一策略ID" END) AS "有调后正权重策略数"
        FROM "策略调仓明细"
        WHERE "渠道ID" = ?
        """,
        (CHANNEL_ID,),
    )
    latest_official_distribution = fetch_dicts(
        conn,
        """
        WITH latest AS (
            SELECT "统一策略ID", MAX("交易日期") AS "最新业绩日期"
            FROM "策略日度业绩"
            WHERE "渠道ID" = ?
            GROUP BY "统一策略ID"
        )
        SELECT "最新业绩日期", COUNT(*) AS "策略数"
        FROM latest
        GROUP BY "最新业绩日期"
        ORDER BY "最新业绩日期" DESC
        LIMIT 10
        """,
        (CHANNEL_ID,),
    )
    return {
        "策略": strategy,
        "官方业绩": official,
        "当前持仓": current,
        "历史调仓": rebalance,
        "调仓明细": detail,
        "最新业绩日期分布前10": latest_official_distribution,
    }


def summarize_replay_quality_from_csv() -> dict[str, Any]:
    output_dir = PROJECT_ROOT / "outputs" / "nav_reconstruction_quality"
    strategy_path = output_dir / "strategy_replay_quality.csv"
    event_path = output_dir / "rebalance_event_quality.csv"
    result: dict[str, Any] = {"可用": False}
    if not strategy_path.exists() or not event_path.exists():
        return result
    strategy_rows: list[dict[str, str]] = []
    with strategy_path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("渠道ID") == CHANNEL_ID:
                strategy_rows.append(row)
    event_rows: list[dict[str, str]] = []
    with event_path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("渠道ID") == CHANNEL_ID:
                event_rows.append(row)
    first_reason = Counter(row.get("首个不可回放原因") or "可回放" for row in strategy_rows)
    issue_counts = Counter()
    for row in event_rows:
        if row.get("调后完整快照可回放") == "1":
            continue
        if int(float(row.get("调后正权重行数") or 0)) == 0:
            issue_counts["无调后正权重"] += 1
        if row.get("调后权重严格闭合") != "1" and row.get("调后权重宽松可归一化") != "1":
            issue_counts["调后权重不闭合"] += 1
        for source, target in [
            ("调后正权重缺代码数", "调后正权重缺代码"),
            ("调后正权重重复基金数", "调后正权重重复基金"),
            ("调后正权重缺净值基金数", "调后正权重缺净值"),
            ("调仓日净值不可覆盖基金数", "调仓日净值不可覆盖"),
            ("区间结束净值不可覆盖基金数", "区间结束净值不可覆盖"),
            ("缺总收益口径基金数", "缺总收益口径"),
        ]:
            if int(float(row.get(source) or 0)) > 0:
                issue_counts[target] += 1
    full_chain = sum(1 for row in strategy_rows if row.get("全历史调仓链可回放") == "1")
    replayable_events = sum(1 for row in event_rows if row.get("调后完整快照可回放") == "1")
    result.update(
        {
            "可用": True,
            "有调仓链策略数": len(strategy_rows),
            "全历史调仓链可回放策略数": full_chain,
            "全历史调仓链可回放占比_百分比": pct(full_chain, len(strategy_rows)),
            "至少5次调仓且全链可回放策略数": sum(1 for row in strategy_rows if row.get("至少5次调仓且全链可回放") == "1"),
            "调仓事件数": len(event_rows),
            "调后完整快照可回放事件数": replayable_events,
            "调后完整快照可回放占比_百分比": pct(replayable_events, len(event_rows)),
            "策略首个不可回放原因": dict(first_reason),
            "不可回放事件问题分布": dict(issue_counts),
        }
    )
    return result


def render_report(output_dir: Path, payload: dict[str, Any]) -> None:
    raw = payload["原始数据"]
    projection = payload["最新仓位稽核"]
    nav = payload["策略净值回放"]
    replay = payload["历史调仓链路质量"]
    fund = payload["基金覆盖"]

    lines = [
        "# 天天基金投顾数据质量复核报告",
        "",
        f"- 生成时间：{payload['生成时间']}",
        f"- 分析库：`{payload['数据库']}`",
        f"- 净值回放算法版本：`{payload['净值回放算法版本']}`",
        "",
        "## 结论",
        "",
        f"- 天天渠道策略总数 {raw['策略'].get('策略数', 0)} 个；有历史调仓事件策略 {raw['历史调仓'].get('有调仓事件策略数', 0)} 个，调仓事件 {raw['历史调仓'].get('调仓事件数', 0)} 条，基金级调仓明细 {raw['调仓明细'].get('调仓基金明细行数', 0)} 行。",
        f"- 历史调仓基金仓位回放：调后完整快照事件 {replay.get('调后完整快照可回放事件数', 0)}/{replay.get('调仓事件数', 0)}，全历史调仓链可回放策略 {replay.get('全历史调仓链可回放策略数', 0)}/{replay.get('有调仓链策略数', 0)}。",
        f"- 最新仓位：有当前持仓明细策略 {raw['当前持仓'].get('有当前持仓策略数', 0)} 个，但有当前基金权重策略仅 {raw['当前持仓'].get('有当前基金权重策略数', 0)} 个；可由最后调仓向后推算 {projection.get('可推算策略数', 0)} 个，其中当前缺失但可推算补齐 {projection.get('当前缺失但可推算补齐策略数', 0)} 个。",
        f"- 基金依赖：天天涉及基金 {fund.get('涉及基金数', 0)} 只，有净值 {fund.get('有净值基金数', 0)} 只，无净值 {fund.get('无净值基金数', 0)} 只；有可用总收益口径 {fund.get('有可用总收益口径基金数', 0)} 只，分红事件 {fund.get('分红事件数', 0)} 条。",
        f"- 策略净值回放：纳入模拟 {nav.get('纳入模拟策略数', 0)}/{nav.get('策略数', 0)}；有官方业绩可比 {nav.get('官方可比策略数', 0)} 个，最优口径绝对偏差中位数 {nav.get('最优口径绝对偏差统计', {}).get('中位数')} pct，P90 {nav.get('最优口径绝对偏差统计', {}).get('P90')} pct。",
        "",
        "## 最新仓位对齐",
        "",
        f"- 可直接与天天当前基金权重比对的策略：{projection.get('可比策略数', 0)} 个。",
        f"- 状态分布：{json.dumps(projection.get('状态分布', {}), ensure_ascii=False)}",
        f"- 归因分布：{json.dumps(projection.get('归因分布', {}), ensure_ascii=False)}",
        f"- 可比策略最大绝对差统计：{json.dumps(projection.get('可比策略最大差统计', {}), ensure_ascii=False)}",
        f"- 剔除结构不一致后最大绝对差统计：{json.dumps(projection.get('剔除结构不一致最大差统计', {}), ensure_ascii=False)}",
        "",
        "## 历史调仓和基金依赖",
        "",
        f"- 调仓链路质量：{json.dumps(replay, ensure_ascii=False)}",
        f"- 基金覆盖摘要：{json.dumps(fund, ensure_ascii=False)}",
        "",
        "## 策略净值和真实业绩偏差",
        "",
        f"- 未纳入净值回放原因：{json.dumps(nav.get('未纳入原因分布', {}), ensure_ascii=False)}",
        f"- 费后绝对偏差统计：{json.dumps(nav.get('费后绝对偏差统计', {}), ensure_ascii=False)}",
        f"- 费前绝对偏差统计：{json.dumps(nav.get('费前绝对偏差统计', {}), ensure_ascii=False)}",
        f"- 最优口径阈值分布：{json.dumps(nav.get('偏差阈值分布', []), ensure_ascii=False)}",
        "",
        "## 输出文件",
        "",
        "- `ttfund_channel_quality_summary.json`：机器可读汇总。",
        "- `ttfund_fund_coverage.csv`：天天涉及基金净值、分红、引用情况。",
        "- `ttfund_fund_coverage_gaps.csv`：基金依赖缺口或长期滞后清单。",
        "- `ttfund_nav_worst_deviation.csv`：官方业绩偏差最大的策略清单。",
        "- `ttfund_nav_deviation_thresholds.csv`：官方业绩偏差阈值统计。",
    ]
    (output_dir / "ttfund_channel_quality_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    generated_at = datetime.now().isoformat(timespec="seconds")
    output_dir = args.output_root / datetime.now().strftime("%Y-%m-%d") / datetime.now().strftime("%Y%m%dT%H%M%S")
    output_dir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(args.db_path)
    conn.row_factory = sqlite3.Row

    latest_nav_date = fetch_one(conn, 'SELECT MAX("交易日期") AS latest_nav_date FROM "基金日度净值"').get("latest_nav_date")
    raw_summary = summarize_raw_quality(conn)
    replay_summary = summarize_replay_quality_from_csv()
    projection_summary = summarize_projection(conn)
    fund_summary, fund_rows, fund_gaps = build_fund_coverage(conn, latest_nav_date)
    nav_summary, nav_threshold_rows, nav_worst_rows = summarize_nav(conn, args.algorithm_version)

    payload = {
        "生成时间": generated_at,
        "数据库": str(args.db_path.resolve()),
        "净值回放算法版本": args.algorithm_version,
        "基金净值全库最新日期": latest_nav_date,
        "原始数据": raw_summary,
        "历史调仓链路质量": replay_summary,
        "最新仓位稽核": projection_summary,
        "基金覆盖": fund_summary,
        "策略净值回放": nav_summary,
        "输出目录": str(output_dir.resolve()),
    }

    write_csv(output_dir / "ttfund_fund_coverage.csv", fund_rows)
    write_csv(output_dir / "ttfund_fund_coverage_gaps.csv", fund_gaps)
    write_csv(output_dir / "ttfund_nav_deviation_thresholds.csv", nav_threshold_rows)
    write_csv(output_dir / "ttfund_nav_worst_deviation.csv", nav_worst_rows[:100])
    (output_dir / "ttfund_channel_quality_summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    render_report(output_dir, payload)
    conn.close()

    print(
        json.dumps(
            {
                "output_dir": str(output_dir.resolve()),
                "strategy_total": raw_summary["策略"].get("策略数"),
                "rebalance_full_chain": replay_summary.get("全历史调仓链可回放策略数"),
                "nav_included": nav_summary.get("纳入模拟策略数"),
                "official_comparable": nav_summary.get("官方可比策略数"),
                "fund_total": fund_summary.get("涉及基金数"),
                "fund_no_nav": fund_summary.get("无净值基金数"),
                "projection_comparable": projection_summary.get("可比策略数"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
