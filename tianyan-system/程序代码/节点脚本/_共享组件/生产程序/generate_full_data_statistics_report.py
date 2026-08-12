from __future__ import annotations

import argparse
import html
import json
import math
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from business_naming import canonical_advisor_institution


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
DEFAULT_RAW_INDEX_DB = PROJECT_ROOT / "data" / "advisor_monitor.sqlite"
DEFAULT_ALGORITHM_VERSION = "rebalance_asset_fee_dual_nav_v9_ttfund_rules_cifm_overseas_placeholder_20260527"
DEFAULT_SITE_DIR = PROJECT_ROOT / "site" / "full_data_statistics_report"

GENERATED_TABLES = [
    "策略模拟净值",
    "策略模拟净值区间",
    "策略模拟净值质量",
    "策略模拟净值校验",
    "策略官方偏差分析",
    "渠道官方偏差分析",
    "策略官方算法候选评估",
    "渠道官方算法候选评估",
]

CORE_TABLES = [
    "渠道信息",
    "策略信息",
    "策略调仓事件",
    "策略调仓明细",
    "策略当前持仓",
    "策略当前持仓分组",
    "策略日度业绩",
    "策略区间业绩",
    "策略披露风险指标",
    "基金信息",
    "基金日度净值",
    "基金净值概况",
    "基金分红送配",
    "基金名称映射",
    "数据来源清单",
    "策略模拟净值",
    "策略模拟净值区间",
    "策略模拟净值质量",
    "策略官方偏差分析",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成原始采集、加工入库和全量统计的独立页面报告。")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--raw-index-db", type=Path, default=DEFAULT_RAW_INDEX_DB)
    parser.add_argument("--algorithm-version", default=DEFAULT_ALGORITHM_VERSION)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--site-dir", type=Path, default=DEFAULT_SITE_DIR)
    return parser.parse_args()


def now_id() -> str:
    return datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z")


def default_output_dir() -> Path:
    return PROJECT_ROOT / "outputs" / "full_data_statistics_report" / datetime.now().astimezone().strftime("%Y-%m-%d") / now_id()


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def write_text_if_changed(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            if path.read_text(encoding="utf-8") == text:
                return
        except UnicodeDecodeError:
            pass
    path.write_text(text, encoding="utf-8")


def fetch_all(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, params)]


def fetch_one(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any]:
    row = conn.execute(sql, params).fetchone()
    return dict(row) if row else {}


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    if not table_exists(conn, table):
        return []
    return [row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')]


def safe_count(conn: sqlite3.Connection, table: str) -> int:
    if not table_exists(conn, table):
        return 0
    return int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] or 0)


def to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def pct(numerator: int | float | None, denominator: int | float | None) -> float | None:
    if denominator in (None, 0):
        return None
    return round(float(numerator or 0) / float(denominator) * 100.0, 4)


def fmt_int(value: Any) -> str:
    if value is None:
        return "-"
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return str(value)


def fmt_num(value: Any, digits: int = 4) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):,.{digits}f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return str(value)


def percentile(sorted_values: list[float], ratio: float) -> float | None:
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = (len(sorted_values) - 1) * ratio
    low = math.floor(pos)
    high = math.ceil(pos)
    if low == high:
        return sorted_values[low]
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * (pos - low)


def describe(values: list[float]) -> dict[str, Any]:
    clean = sorted(v for v in values if v is not None and math.isfinite(v))
    if not clean:
        return {"样本数": 0}
    return {
        "样本数": len(clean),
        "最小值": round(clean[0], 6),
        "中位数": round(percentile(clean, 0.5) or 0.0, 6),
        "P75": round(percentile(clean, 0.75) or 0.0, 6),
        "P90": round(percentile(clean, 0.9) or 0.0, 6),
        "P95": round(percentile(clean, 0.95) or 0.0, 6),
        "最大值": round(clean[-1], 6),
        "平均值": round(sum(clean) / len(clean), 6),
    }


def dir_stats(path: Path, max_depth: int = 1) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    result: list[dict[str, Any]] = []
    for child in sorted((item for item in path.iterdir() if item.is_dir()), key=lambda p: p.name):
        file_count = 0
        byte_count = 0
        latest: float | None = None
        for file_path in child.rglob("*"):
            if file_path.is_file():
                file_count += 1
                stat = file_path.stat()
                byte_count += stat.st_size
                latest = max(latest or stat.st_mtime, stat.st_mtime)
        result.append(
            {
                "目录": child.relative_to(PROJECT_ROOT).as_posix(),
                "文件数": file_count,
                "大小_MB": round(byte_count / 1024 / 1024, 3),
                "最近修改时间": datetime.fromtimestamp(latest).isoformat(timespec="seconds") if latest else None,
            }
        )
    return sorted(result, key=lambda row: row["大小_MB"], reverse=True)


def data_source_file_check(conn: sqlite3.Connection) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    rows = fetch_all(
        conn,
        """
        SELECT "渠道ID", "文件类型", "采集批次ID", "文件路径", COUNT(*) AS "引用记录数"
        FROM "数据来源清单"
        GROUP BY "渠道ID", "文件类型", "采集批次ID", "文件路径"
        ORDER BY "渠道ID", "文件类型", "采集批次ID"
        """,
    )
    missing: list[dict[str, Any]] = []
    existing_files = 0
    existing_bytes = 0
    for row in rows:
        path = Path(str(row["文件路径"]))
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        if path.exists():
            existing_files += 1
            existing_bytes += path.stat().st_size
        else:
            missing.append(row)
    summary = {
        "数据来源引用文件数": len(rows),
        "存在文件数": existing_files,
        "缺失文件数": len(missing),
        "引用文件大小_MB": round(existing_bytes / 1024 / 1024, 3),
    }
    return rows, missing, summary


def raw_snapshot_check(raw_index_db: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not raw_index_db.exists():
        return [], {"原始快照索引库存在": "否", "原始快照记录数": 0, "原始文件缺失数": 0}
    conn = connect(raw_index_db)
    groups = fetch_all(
        conn,
        """
        SELECT channel_id AS "渠道ID", collector_name AS "采集器", COUNT(*) AS "快照数",
               MIN(captured_at) AS "最早采集时间", MAX(captured_at) AS "最晚采集时间"
        FROM raw_snapshot
        GROUP BY channel_id, collector_name
        ORDER BY "快照数" DESC
        """,
    )
    missing = 0
    total = 0
    for row in conn.execute("SELECT raw_path FROM raw_snapshot"):
        total += 1
        raw_path = row[0]
        if not raw_path or not Path(str(raw_path)).exists():
            missing += 1
    conn.close()
    return groups, {"原始快照索引库存在": "是", "原始快照记录数": total, "原始文件缺失数": missing}


def table_overview(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for table in CORE_TABLES:
        if not table_exists(conn, table):
            continue
        rows.append({"表名": table, "行数": safe_count(conn, table)})
    return rows


def channel_overview(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = fetch_all(
        conn,
        """
        SELECT s."渠道ID", COALESCE(c."渠道名称", s."渠道ID") AS "渠道名称",
               COUNT(*) AS "策略数",
               SUM(CASE WHEN s."策略状态" IS NULL OR s."策略状态" = '' THEN 1 ELSE 0 END) AS "状态缺失策略数"
        FROM "策略信息" s
        LEFT JOIN "渠道信息" c ON c."渠道ID" = s."渠道ID"
        GROUP BY s."渠道ID", COALESCE(c."渠道名称", s."渠道ID")
        ORDER BY "策略数" DESC
        """,
    )
    daily = {
        row["渠道ID"]: row
        for row in fetch_all(
            conn,
            """
            SELECT "渠道ID", COUNT(*) AS "日度业绩行数", COUNT(DISTINCT "统一策略ID") AS "有日度业绩策略数",
                   MIN("交易日期") AS "最早业绩日", MAX("交易日期") AS "最晚业绩日"
            FROM "策略日度业绩"
            GROUP BY "渠道ID"
            """,
        )
    }
    rebalance = {
        row["渠道ID"]: row
        for row in fetch_all(
            conn,
            """
            SELECT "渠道ID", COUNT(*) AS "调仓事件数", COUNT(DISTINCT "统一策略ID") AS "有调仓策略数"
            FROM "策略调仓事件"
            GROUP BY "渠道ID"
            """,
        )
    }
    holding = {
        row["渠道ID"]: row
        for row in fetch_all(
            conn,
            """
            SELECT "渠道ID", COUNT(*) AS "当前持仓行数", COUNT(DISTINCT "统一策略ID") AS "有当前持仓策略数"
            FROM "策略当前持仓"
            GROUP BY "渠道ID"
            """,
        )
    }
    for row in rows:
        channel_id = row["渠道ID"]
        row.update(daily.get(channel_id, {}))
        row.update(rebalance.get(channel_id, {}))
        row.update(holding.get(channel_id, {}))
    return rows


def source_overview(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return fetch_all(
        conn,
        """
        SELECT "渠道ID", "文件类型", COUNT(*) AS "策略引用数",
               COUNT(DISTINCT "采集批次ID") AS "采集批次数",
               COUNT(DISTINCT "文件路径") AS "文件数",
               MIN("采集时间") AS "最早采集时间",
               MAX("采集时间") AS "最晚采集时间"
        FROM "数据来源清单"
        GROUP BY "渠道ID", "文件类型"
        ORDER BY "渠道ID", "文件类型"
        """,
    )


def strategy_statistics(conn: sqlite3.Connection) -> dict[str, Any]:
    by_status = fetch_all(
        conn,
        """
        SELECT COALESCE("策略状态", "未披露") AS "策略状态", COUNT(*) AS "策略数"
        FROM "策略信息"
        GROUP BY COALESCE("策略状态", "未披露")
        ORDER BY "策略数" DESC
        """,
    )
    by_advisor = fetch_all(
        conn,
        """
        SELECT COALESCE("投顾机构", "未披露") AS "投顾机构", COUNT(*) AS "策略数"
        FROM "策略信息"
        GROUP BY COALESCE("投顾机构", "未披露")
        """,
    )
    ttfund_advisor = fetch_all(
        conn,
        """
        SELECT COALESCE("投顾机构", "未披露") AS "投顾机构", COUNT(*) AS "天天策略数"
        FROM "策略信息"
        WHERE "渠道ID" = 'ttfund'
        GROUP BY COALESCE("投顾机构", "未披露")
        """,
    )
    def canonical_top20(rows: list[dict[str, Any]], count_field: str) -> list[dict[str, Any]]:
        totals: Counter[str] = Counter()
        for row in rows:
            institution = canonical_advisor_institution(row.get("投顾机构")) or "未披露"
            totals[institution] += int(row.get(count_field) or 0)
        return [
            {"投顾机构": institution, count_field: count}
            for institution, count in sorted(totals.items(), key=lambda item: (-item[1], item[0]))[:20]
        ]

    by_advisor = canonical_top20(by_advisor, "策略数")
    ttfund_advisor = canonical_top20(ttfund_advisor, "天天策略数")
    return {"按状态": by_status, "按投顾机构Top20": by_advisor, "天天基金投顾机构Top20": ttfund_advisor}


def rebalance_statistics(conn: sqlite3.Connection) -> dict[str, Any]:
    event_summary = fetch_all(
        conn,
        """
        SELECT "渠道ID", COUNT(*) AS "调仓事件数", COUNT(DISTINCT "统一策略ID") AS "策略数",
               MIN("调仓日期") AS "最早调仓日", MAX("调仓日期") AS "最晚调仓日"
        FROM "策略调仓事件"
        GROUP BY "渠道ID"
        ORDER BY "调仓事件数" DESC
        """,
    )
    detail_summary = fetch_all(
        conn,
        """
        SELECT "渠道ID", COUNT(*) AS "调仓明细行数",
               SUM(CASE WHEN "基金代码" IS NULL OR TRIM("基金代码") = '' THEN 1 ELSE 0 END) AS "缺基金代码行数",
               COUNT(DISTINCT "基金代码") AS "涉及基金数"
        FROM "策略调仓明细"
        GROUP BY "渠道ID"
        ORDER BY "调仓明细行数" DESC
        """,
    )
    non_closed = fetch_all(
        conn,
        """
        WITH w AS (
            SELECT "渠道ID", "调仓事件ID", "统一策略ID",
                   SUM(CASE WHEN COALESCE("调后权重_百分比", 0) > 0 THEN "调后权重_百分比" ELSE 0 END) AS weight_sum
            FROM "策略调仓明细"
            GROUP BY "渠道ID", "调仓事件ID", "统一策略ID"
        )
        SELECT "渠道ID", COUNT(*) AS "调后权重不闭合事件数",
               COUNT(DISTINCT "统一策略ID") AS "影响策略数"
        FROM w
        WHERE weight_sum > 0 AND ABS(weight_sum - 100.0) > 1.0
        GROUP BY "渠道ID"
        ORDER BY "调后权重不闭合事件数" DESC
        """,
    )
    return {"事件统计": event_summary, "明细统计": detail_summary, "调后权重不闭合": non_closed}


def holding_statistics(conn: sqlite3.Connection) -> dict[str, Any]:
    summary = fetch_all(
        conn,
        """
        SELECT "渠道ID", COUNT(*) AS "当前持仓行数", COUNT(DISTINCT "统一策略ID") AS "策略数",
               MIN("持仓日期") AS "最早持仓日", MAX("持仓日期") AS "最晚持仓日",
               SUM(CASE WHEN "基金权重_百分比" IS NULL THEN 1 ELSE 0 END) AS "基金权重缺失行数"
        FROM "策略当前持仓"
        GROUP BY "渠道ID"
        ORDER BY "当前持仓行数" DESC
        """,
    )
    non_closed = fetch_all(
        conn,
        """
        WITH w AS (
            SELECT "渠道ID", "统一策略ID", "持仓日期",
                   SUM(COALESCE("基金权重_百分比", 0)) AS weight_sum,
                   SUM(CASE WHEN "基金权重_百分比" IS NOT NULL THEN 1 ELSE 0 END) AS weight_rows
            FROM "策略当前持仓"
            GROUP BY "渠道ID", "统一策略ID", "持仓日期"
        )
        SELECT "渠道ID", COUNT(*) AS "当前持仓权重不闭合组数", COUNT(DISTINCT "统一策略ID") AS "影响策略数"
        FROM w
        WHERE weight_rows > 0 AND weight_sum > 0 AND ABS(weight_sum - 100.0) > 1.0
        GROUP BY "渠道ID"
        ORDER BY "当前持仓权重不闭合组数" DESC
        """,
    )
    projection = []
    if table_exists(conn, "最新持仓推算稽核策略汇总"):
        projection = fetch_all(
            conn,
            """
            SELECT "稽核状态", COUNT(*) AS "策略数"
            FROM "最新持仓推算稽核策略汇总"
            GROUP BY "稽核状态"
            ORDER BY "策略数" DESC
            """,
        )
    return {"当前持仓统计": summary, "当前持仓权重不闭合": non_closed, "最新持仓推算稽核": projection}


def performance_statistics(conn: sqlite3.Connection) -> dict[str, Any]:
    daily = fetch_all(
        conn,
        """
        SELECT "渠道ID", COALESCE("业绩区段类型", "未披露") AS "业绩区段类型",
               COUNT(*) AS "行数", COUNT(DISTINCT "统一策略ID") AS "策略数",
               MIN("交易日期") AS "最早交易日", MAX("交易日期") AS "最晚交易日",
               SUM(CASE WHEN "单位净值" IS NULL THEN 1 ELSE 0 END) AS "单位净值缺失行数",
               SUM(CASE WHEN "累计收益率_百分比" IS NULL THEN 1 ELSE 0 END) AS "累计收益缺失行数"
        FROM "策略日度业绩"
        GROUP BY "渠道ID", COALESCE("业绩区段类型", "未披露")
        ORDER BY "行数" DESC
        """,
    )
    interval_rows = fetch_all(
        conn,
        """
        SELECT "渠道ID", "区间代码", "区间名称", COUNT(*) AS "行数", COUNT(DISTINCT "统一策略ID") AS "策略数",
               MIN("统计日期") AS "最早统计日", MAX("统计日期") AS "最晚统计日"
        FROM "策略区间业绩"
        GROUP BY "渠道ID", "区间代码", "区间名称"
        ORDER BY "渠道ID", "策略数" DESC
        """,
    )
    return {"日度业绩": daily, "区间业绩": interval_rows}


def fund_statistics(conn: sqlite3.Connection) -> dict[str, Any]:
    fund_summary = fetch_one(
        conn,
        """
        SELECT COUNT(*) AS "基金信息数",
               SUM(CASE WHEN "最新净值" IS NULL THEN 1 ELSE 0 END) AS "最新净值缺失基金数",
               MIN("最新净值日期") AS "最早最新净值日",
               MAX("最新净值日期") AS "最晚最新净值日"
        FROM "基金信息"
        """,
    )
    nav_summary = fetch_one(
        conn,
        """
        SELECT COUNT(*) AS "基金净值行数", COUNT(DISTINCT "基金代码") AS "有净值基金数",
               MIN("交易日期") AS "最早净值日", MAX("交易日期") AS "最晚净值日",
               SUM(CASE WHEN "单位净值" IS NULL AND "每万份收益" IS NULL THEN 1 ELSE 0 END) AS "净值收益均缺失行数"
        FROM "基金日度净值"
        """,
    )
    nav_source = fetch_all(
        conn,
        """
        SELECT COALESCE("数据来源", "未披露") AS "数据来源", COUNT(*) AS "行数", COUNT(DISTINCT "基金代码") AS "基金数"
        FROM "基金日度净值"
        GROUP BY COALESCE("数据来源", "未披露")
        ORDER BY "行数" DESC
        """,
    )
    dividend_summary = fetch_one(
        conn,
        """
        SELECT COUNT(*) AS "分红事件数", COUNT(DISTINCT "基金代码") AS "分红基金数",
               MIN(COALESCE("权益登记日", "除息日")) AS "最早分红日期",
               MAX(COALESCE("权益登记日", "除息日")) AS "最晚分红日期"
        FROM "基金分红送配"
        """,
    )
    used = fetch_one(
        conn,
        """
        WITH used AS (
            SELECT "基金代码" AS code FROM "策略调仓明细" WHERE "基金代码" IS NOT NULL AND TRIM("基金代码") <> ''
            UNION
            SELECT "基金代码" AS code FROM "策略当前持仓" WHERE "基金代码" IS NOT NULL AND TRIM("基金代码") <> ''
        ),
        nav AS (SELECT DISTINCT "基金代码" AS code FROM "基金日度净值")
        SELECT COUNT(*) AS "策略使用基金数",
               SUM(CASE WHEN f."基金代码" IS NULL THEN 1 ELSE 0 END) AS "基金信息缺失数",
               SUM(CASE WHEN nav.code IS NULL THEN 1 ELSE 0 END) AS "净值缺失基金数"
        FROM used
        LEFT JOIN "基金信息" f ON f."基金代码" = used.code
        LEFT JOIN nav ON nav.code = used.code
        """,
    )
    return {"基金概览": fund_summary, "基金净值": nav_summary, "净值来源": nav_source, "分红送配": dividend_summary, "策略基金依赖": used}


def simulation_statistics(conn: sqlite3.Connection, algorithm_version: str) -> dict[str, Any]:
    quality = fetch_one(
        conn,
        """
        SELECT COUNT(*) AS "质量记录策略数",
               SUM(CASE WHEN "是否纳入模拟" = 1 THEN 1 ELSE 0 END) AS "纳入模拟策略数",
               SUM(CASE WHEN "是否纳入模拟" <> 1 THEN 1 ELSE 0 END) AS "未纳入模拟策略数",
               MIN("模拟起始日期") AS "模拟最早日期",
               MAX("模拟结束日期") AS "模拟最晚日期"
        FROM "策略模拟净值质量"
        WHERE "算法版本" = ?
        """,
        (algorithm_version,),
    )
    grade = fetch_all(
        conn,
        """
        SELECT COALESCE("质量等级", "未披露") AS "质量等级", COUNT(*) AS "策略数"
        FROM "策略模拟净值质量"
        WHERE "算法版本" = ?
        GROUP BY COALESCE("质量等级", "未披露")
        ORDER BY "策略数" DESC
        """,
        (algorithm_version,),
    )
    rule = []
    if "官方对比区间规则" in table_columns(conn, "策略模拟净值质量"):
        rule = fetch_all(
            conn,
            """
            SELECT COALESCE("官方对比区间规则", "未披露") AS "官方对比区间规则", COUNT(*) AS "策略数"
            FROM "策略模拟净值质量"
            WHERE "算法版本" = ? AND "是否纳入模拟" = 1
            GROUP BY COALESCE("官方对比区间规则", "未披露")
            ORDER BY "策略数" DESC
            """,
            (algorithm_version,),
        )
    app_diffs = [
        to_float(row["App展示默认绝对偏差_百分点"])
        for row in fetch_all(
            conn,
            """
            SELECT "App展示默认绝对偏差_百分点"
            FROM "策略官方偏差分析"
            WHERE "算法版本" = ? AND "App展示默认绝对偏差_百分点" IS NOT NULL
            """,
            (algorithm_version,),
        )
    ]
    fee_after_diffs = [
        to_float(row["费后官方绝对偏差_百分点"])
        for row in fetch_all(
            conn,
            """
            SELECT "费后官方绝对偏差_百分点"
            FROM "策略官方偏差分析"
            WHERE "算法版本" = ? AND "费后官方绝对偏差_百分点" IS NOT NULL
            """,
            (algorithm_version,),
        )
    ]
    old_rows: list[dict[str, Any]] = []
    for table in GENERATED_TABLES:
        if "算法版本" not in table_columns(conn, table):
            continue
        count = int(conn.execute(f'SELECT COUNT(*) FROM "{table}" WHERE "算法版本" <> ?', (algorithm_version,)).fetchone()[0] or 0)
        old_rows.append({"表名": table, "旧算法行数": count})
    return {
        "模拟质量": quality,
        "质量等级": grade,
        "官方对比区间规则": rule,
        "App展示默认绝对偏差_百分点": describe([v for v in app_diffs if v is not None]),
        "费后内部口径绝对偏差_百分点": describe([v for v in fee_after_diffs if v is not None]),
        "旧算法产物": old_rows,
    }


def issue_checks(
    conn: sqlite3.Connection,
    raw_snapshot_summary: dict[str, Any],
    source_missing: list[dict[str, Any]],
    algorithm_version: str,
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    def add(name: str, scope: str, issue_count: int, level_when_issue: str, desc: str, action: str) -> None:
        if issue_count == 0:
            status = "通过"
        else:
            status = level_when_issue
        checks.append(
            {
                "检查项": name,
                "范围": scope,
                "状态": status,
                "问题数": issue_count,
                "检查说明": desc,
                "处理建议": action,
            }
        )

    add(
        "数据来源文件存在性",
        "数据来源清单",
        len(source_missing),
        "严重",
        "检查入库记录引用的 normalized 文件是否仍在本项目目录内。",
        "缺失时需要从当前采集批次恢复对应 normalized 文件，或重新运行采集和入库。",
    )
    add(
        "原始快照文件存在性",
        "advisor_monitor.raw_snapshot",
        int(raw_snapshot_summary.get("原始文件缺失数") or 0),
        "严重",
        "检查保留的原始采集快照索引是否都有可访问的 raw 文件。",
        "缺失时需要重新同步 raw_manifest，或重新采集对应批次。",
    )

    duplicate_groups = 0
    duplicate_specs = [
        ("策略信息", ['"渠道ID"', '"渠道策略ID"']),
        ("策略日度业绩", ['"统一策略ID"', '"交易日期"']),
        ("策略区间业绩", ['"统一策略ID"', '"统计日期"', '"区间代码"']),
        ("策略当前持仓", ['"统一策略ID"', '"持仓日期"', '"基金名称"']),
        ("策略调仓事件", ['"调仓事件ID"']),
        ("策略调仓明细", ['"调仓明细ID"']),
    ]
    for table, keys in duplicate_specs:
        key_sql = ", ".join(keys)
        duplicate_groups += int(
            conn.execute(
                f'SELECT COUNT(*) FROM (SELECT {key_sql}, COUNT(*) c FROM "{table}" GROUP BY {key_sql} HAVING c > 1)'
            ).fetchone()[0]
            or 0
        )
    add(
        "主键与业务键重复",
        "核心入库表",
        duplicate_groups,
        "严重",
        "检查策略、业绩、持仓、调仓事件和调仓明细是否存在重复业务键。",
        "重复时需要回到装载逻辑排查 ON CONFLICT 主键和去重口径。",
    )

    orphan_strategy = 0
    for table in ["策略日度业绩", "策略区间业绩", "策略当前持仓", "策略调仓事件", "策略调仓明细"]:
        orphan_strategy += int(
            conn.execute(
                f"""
                SELECT COUNT(*)
                FROM "{table}" t
                LEFT JOIN "策略信息" s ON s."统一策略ID" = t."统一策略ID"
                WHERE s."统一策略ID" IS NULL
                """
            ).fetchone()[0]
            or 0
        )
    orphan_event = int(
        conn.execute(
            """
            SELECT COUNT(*)
            FROM "策略调仓明细" d
            LEFT JOIN "策略调仓事件" e ON e."调仓事件ID" = d."调仓事件ID"
            WHERE e."调仓事件ID" IS NULL
            """
        ).fetchone()[0]
        or 0
    )
    add(
        "子表孤儿记录",
        "策略子表",
        orphan_strategy + orphan_event,
        "严重",
        "检查业绩、持仓、调仓子表是否能回连到策略主表和调仓事件主表。",
        "出现孤儿记录时需要清理对应装载批次并重跑入库。",
    )

    missing_snapshot = 0
    for table in ["策略信息", "策略日度业绩", "策略区间业绩", "策略当前持仓", "策略调仓事件", "策略调仓明细"]:
        if "原始快照ID" in table_columns(conn, table):
            missing_snapshot += int(
                conn.execute(f'SELECT COUNT(*) FROM "{table}" WHERE "原始快照ID" IS NULL OR TRIM("原始快照ID") = ""').fetchone()[0]
                or 0
            )
    add(
        "原始快照ID覆盖",
        "策略主表和子表",
        missing_snapshot,
        "关注",
        "检查入库记录是否保留原始快照ID，便于追溯采集响应。",
        "当前缺口多来自早期非天天渠道或公开页采集；新增采集应强制写入原始快照ID。",
    )

    used_no_nav = int(
        fetch_one(
            conn,
            """
            WITH used AS (
                SELECT "基金代码" AS code FROM "策略调仓明细" WHERE "基金代码" IS NOT NULL AND TRIM("基金代码") <> ''
                UNION
                SELECT "基金代码" AS code FROM "策略当前持仓" WHERE "基金代码" IS NOT NULL AND TRIM("基金代码") <> ''
            ),
            nav AS (SELECT DISTINCT "基金代码" AS code FROM "基金日度净值")
            SELECT SUM(CASE WHEN nav.code IS NULL THEN 1 ELSE 0 END) AS n
            FROM used
            LEFT JOIN nav ON nav.code = used.code
            """,
        ).get("n")
        or 0
    )
    add(
        "策略基金净值覆盖",
        "调仓和当前持仓涉及基金",
        used_no_nav,
        "严重",
        "检查策略出现过的基金代码是否都能在基金日度净值库中找到至少一条净值。",
        "缺失时优先补天天基金净值，互认/QD 基金再补基金公司或境外公开源。",
    )

    daily_null = int(
        conn.execute(
            """
            SELECT COUNT(*)
            FROM "策略日度业绩"
            WHERE "单位净值" IS NULL OR "累计收益率_百分比" IS NULL
            """
        ).fetchone()[0]
        or 0
    )
    add(
        "官方日度业绩核心字段",
        "策略日度业绩",
        daily_null,
        "关注",
        "检查官方日度曲线是否有单位净值和累计收益。",
        "缺失时需要补采 App 官方曲线或剔除不可画曲线的区段。",
    )

    rebalance_non_closed = int(
        conn.execute(
            """
            WITH w AS (
                SELECT "调仓事件ID",
                       SUM(CASE WHEN COALESCE("调后权重_百分比", 0) > 0 THEN "调后权重_百分比" ELSE 0 END) AS weight_sum
                FROM "策略调仓明细"
                GROUP BY "调仓事件ID"
            )
            SELECT COUNT(*) FROM w
            WHERE weight_sum > 0 AND ABS(weight_sum - 100.0) > 1.0
            """
        ).fetchone()[0]
        or 0
    )
    add(
        "调仓后权重闭合",
        "策略调仓明细",
        rebalance_non_closed,
        "关注",
        "检查调仓后正权重是否接近100%。目标盈/清盘空仓不计入该项。",
        "保留为问题策略清单，后续逐策略对比 App 页面或重采调仓明细。",
    )

    old_algorithm_rows = 0
    for table in GENERATED_TABLES:
        if "算法版本" in table_columns(conn, table):
            old_algorithm_rows += int(
                conn.execute(f'SELECT COUNT(*) FROM "{table}" WHERE "算法版本" <> ?', (algorithm_version,)).fetchone()[0]
                or 0
            )
    add(
        "旧算法产物残留",
        "回放和偏差分析结果表",
        old_algorithm_rows,
        "严重",
        "检查数据库是否仍保留非当前算法版本的回放/偏差产物。",
        "发现旧版本时执行清理脚本，仅保留当前算法版本。",
    )

    not_included = int(
        fetch_one(
            conn,
            """
            SELECT COUNT(*) AS n
            FROM "策略模拟净值质量"
            WHERE "算法版本" = ? AND "是否纳入模拟" <> 1
            """,
            (algorithm_version,),
        ).get("n")
        or 0
    )
    add(
        "策略净值回放纳入",
        "策略模拟净值质量",
        not_included,
        "关注",
        "检查策略是否能进入统一净值回放。",
        "未纳入策略多为未运作、缺历史调仓或调仓权重异常；报告中保留分类并不影响已纳入策略计算。",
    )

    return checks


def status_summary(checks: list[dict[str, Any]]) -> dict[str, Any]:
    counter = Counter(row["状态"] for row in checks)
    return {
        "检查项数": len(checks),
        "通过": counter.get("通过", 0),
        "关注": counter.get("关注", 0),
        "严重": counter.get("严重", 0),
    }


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def render_table(rows: list[dict[str, Any]], max_rows: int = 20) -> str:
    if not rows:
        return '<p class="muted">无数据</p>'
    columns: list[str] = []
    for row in rows[:max_rows]:
        for key in row:
            if key not in columns:
                columns.append(key)
    header = "".join(f"<th>{esc(col)}</th>" for col in columns)
    body = []
    for row in rows[:max_rows]:
        cells = "".join(f"<td>{esc(row.get(col))}</td>" for col in columns)
        body.append(f"<tr>{cells}</tr>")
    note = ""
    if len(rows) > max_rows:
        note = f'<p class="muted">仅展示前 {max_rows} 行，共 {len(rows)} 行。</p>'
    return f'<div class="table-wrap"><table><thead><tr>{header}</tr></thead><tbody>{"".join(body)}</tbody></table></div>{note}'


def render_metric_cards(cards: list[tuple[str, Any, str]]) -> str:
    return "".join(
        f'<div class="metric"><div class="metric-label">{esc(label)}</div><div class="metric-value">{esc(value)}</div><div class="metric-note">{esc(note)}</div></div>'
        for label, value, note in cards
    )


def render_check_table(checks: list[dict[str, Any]]) -> str:
    badge_class = {"通过": "ok", "关注": "warn", "严重": "bad"}
    rows = []
    for check in checks:
        status = str(check["状态"])
        rows.append(
            "<tr>"
            f"<td>{esc(check['检查项'])}</td>"
            f"<td>{esc(check['范围'])}</td>"
            f'<td><span class="badge {badge_class.get(status, "warn")}">{esc(status)}</span></td>'
            f"<td>{fmt_int(check['问题数'])}</td>"
            f"<td>{esc(check['检查说明'])}</td>"
            f"<td>{esc(check['处理建议'])}</td>"
            "</tr>"
        )
    return (
        '<div class="table-wrap"><table><thead><tr>'
        "<th>检查项</th><th>范围</th><th>状态</th><th>问题数</th><th>检查说明</th><th>处理建议</th>"
        f"</tr></thead><tbody>{''.join(rows)}</tbody></table></div>"
    )


def render_report(summary: dict[str, Any]) -> str:
    generated_at = summary["生成时间"]
    status = summary["质量检查汇总"]
    table_counts = {row["表名"]: row["行数"] for row in summary["入库表统计"]}
    sim = summary["回放与业绩对比"]["模拟质量"]
    app_diff = summary["回放与业绩对比"]["App展示默认绝对偏差_百分点"]
    fund_nav = summary["基金数据统计"]["基金净值"]
    source_summary = summary["采集加工文件检查"]["数据来源文件汇总"]
    cards = [
        ("策略总数", fmt_int(table_counts.get("策略信息")), "全渠道策略主表"),
        ("天天策略数", fmt_int(next((row["策略数"] for row in summary["渠道统计"] if row["渠道ID"] == "ttfund"), 0)), "天天基金渠道"),
        ("调仓明细", fmt_int(table_counts.get("策略调仓明细")), "历史调仓基金级明细"),
        ("官方日度业绩", fmt_int(table_counts.get("策略日度业绩")), "App/官方披露曲线"),
        ("基金净值", fmt_int(fund_nav.get("基金净值行数")), "底层基金日度净值"),
        ("纳入回放", fmt_int(sim.get("纳入模拟策略数")), "统一算法净值回放"),
        ("App偏差中位数", f'{fmt_num(app_diff.get("中位数"), 4)} pp', "费前展示口径"),
        ("检查通过/关注/严重", f"{status['通过']}/{status['关注']}/{status['严重']}", "数据质量检查"),
    ]
    css = """
    :root { color-scheme: light; --bg:#f7f8fa; --panel:#ffffff; --ink:#1f2933; --muted:#65758b; --line:#d9e0e8; --ok:#0f7b4f; --warn:#9a5b00; --bad:#b42318; --accent:#1f6feb; }
    * { box-sizing: border-box; }
    body { margin: 0; font-family: "Microsoft YaHei", "Segoe UI", Arial, sans-serif; background: var(--bg); color: var(--ink); }
    header { padding: 28px 36px 18px; border-bottom: 1px solid var(--line); background: #fff; }
    h1 { margin: 0 0 8px; font-size: 28px; letter-spacing: 0; }
    h2 { margin: 30px 0 12px; font-size: 20px; letter-spacing: 0; }
    h3 { margin: 22px 0 10px; font-size: 16px; letter-spacing: 0; }
    main { max-width: 1440px; margin: 0 auto; padding: 22px 28px 48px; }
    .muted { color: var(--muted); font-size: 13px; line-height: 1.6; }
    .grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
    .metric { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 14px 16px; min-height: 104px; }
    .metric-label { color: var(--muted); font-size: 13px; }
    .metric-value { margin-top: 8px; font-size: 25px; font-weight: 700; }
    .metric-note { margin-top: 8px; color: var(--muted); font-size: 12px; }
    .section { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 18px; margin-top: 16px; }
    .two-col { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 16px; }
    .table-wrap { overflow-x: auto; border: 1px solid var(--line); border-radius: 8px; }
    table { width: 100%; border-collapse: collapse; font-size: 13px; background: #fff; }
    th, td { padding: 9px 10px; border-bottom: 1px solid var(--line); vertical-align: top; white-space: nowrap; }
    th { text-align: left; background: #eef3f8; color: #253244; font-weight: 650; }
    tr:last-child td { border-bottom: 0; }
    .badge { display: inline-block; min-width: 42px; text-align: center; padding: 3px 8px; border-radius: 999px; font-weight: 650; }
    .badge.ok { background: #e6f4ee; color: var(--ok); }
    .badge.warn { background: #fff3d6; color: var(--warn); }
    .badge.bad { background: #fde8e6; color: var(--bad); }
    .summary-line { display: flex; flex-wrap: wrap; gap: 8px 18px; margin: 8px 0 0; color: var(--muted); font-size: 13px; }
    .summary-line span strong { color: var(--ink); }
    @media (max-width: 1100px) { .grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } .two-col { grid-template-columns: 1fr; } }
    @media (max-width: 640px) { header { padding: 22px 18px 14px; } main { padding: 16px; } .grid { grid-template-columns: 1fr; } .metric-value { font-size: 22px; } }
    """
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>投顾数据全量统计与质量检查报告</title>
  <style>{css}</style>
</head>
<body>
  <header>
    <h1>投顾数据全量统计与质量检查报告</h1>
    <div class="muted">生成时间：{esc(generated_at)}；算法版本：{esc(summary["算法版本"])}</div>
    <div class="summary-line">
      <span>数据来源文件：<strong>{fmt_int(source_summary["存在文件数"])}/{fmt_int(source_summary["数据来源引用文件数"])}</strong></span>
      <span>原始快照缺失：<strong>{fmt_int(summary["采集加工文件检查"]["原始快照汇总"]["原始文件缺失数"])}</strong></span>
      <span>策略基金净值缺失：<strong>{fmt_int(summary["基金数据统计"]["策略基金依赖"]["净值缺失基金数"])}</strong></span>
    </div>
  </header>
  <main>
    <div class="grid">{render_metric_cards(cards)}</div>

    <section class="section">
      <h2>一、质量检查结论</h2>
      <p class="muted">检查覆盖原始采集快照、normalized 加工文件、数据来源清单、核心入库表、基金净值依赖、回放产物和官方业绩对比。状态“关注”表示业务上可解释或需要继续跟踪，不等同于阻断入库。</p>
      {render_check_table(summary["质量检查明细"])}
    </section>

    <section class="section">
      <h2>二、原始采集与加工文件</h2>
      <div class="two-col">
        <div>
          <h3>原始快照索引</h3>
          {render_table(summary["采集加工文件检查"]["原始快照分布"], 12)}
        </div>
        <div>
          <h3>数据来源文件</h3>
          {render_table(summary["采集加工文件检查"]["数据来源分布"], 18)}
        </div>
      </div>
      <h3>项目数据目录体积</h3>
      {render_table(summary["采集加工文件检查"]["目录体积Top"], 18)}
    </section>

    <section class="section">
      <h2>三、入库与渠道统计</h2>
      <div class="two-col">
        <div>
          <h3>核心表行数</h3>
          {render_table(summary["入库表统计"], 18)}
        </div>
        <div>
          <h3>渠道覆盖</h3>
          {render_table(summary["渠道统计"], 14)}
        </div>
      </div>
    </section>

    <section class="section">
      <h2>四、策略、调仓、持仓和业绩</h2>
      <div class="two-col">
        <div>
          <h3>天天投顾机构 Top20</h3>
          {render_table(summary["策略数据统计"]["天天基金投顾机构Top20"], 20)}
        </div>
        <div>
          <h3>策略状态分布</h3>
          {render_table(summary["策略数据统计"]["按状态"], 12)}
        </div>
      </div>
      <h3>调仓事件统计</h3>
      {render_table(summary["调仓数据统计"]["事件统计"], 12)}
      <h3>当前持仓统计</h3>
      {render_table(summary["持仓数据统计"]["当前持仓统计"], 12)}
      <h3>官方日度业绩统计</h3>
      {render_table(summary["业绩数据统计"]["日度业绩"], 18)}
    </section>

    <section class="section">
      <h2>五、基金依赖、分红和净值</h2>
      <div class="two-col">
        <div>
          <h3>基金概览</h3>
          {render_table([summary["基金数据统计"]["基金概览"], summary["基金数据统计"]["基金净值"], summary["基金数据统计"]["分红送配"], summary["基金数据统计"]["策略基金依赖"]], 8)}
        </div>
        <div>
          <h3>基金净值来源</h3>
          {render_table(summary["基金数据统计"]["净值来源"], 12)}
        </div>
      </div>
    </section>

    <section class="section">
      <h2>六、净值回放和 App 对比</h2>
      <div class="two-col">
        <div>
          <h3>模拟质量</h3>
          {render_table([summary["回放与业绩对比"]["模拟质量"]], 4)}
          <h3>质量等级</h3>
          {render_table(summary["回放与业绩对比"]["质量等级"], 10)}
        </div>
        <div>
          <h3>官方对比区间规则</h3>
          {render_table(summary["回放与业绩对比"]["官方对比区间规则"], 10)}
          <h3>偏差统计</h3>
          {render_table([
              {"口径": "App展示默认费前口径", **summary["回放与业绩对比"]["App展示默认绝对偏差_百分点"]},
              {"口径": "费后内部资产口径", **summary["回放与业绩对比"]["费后内部口径绝对偏差_百分点"]},
          ], 4)}
        </div>
      </div>
    </section>
  </main>
</body>
</html>"""


def build_summary(args: argparse.Namespace) -> dict[str, Any]:
    conn = connect(args.db_path)
    source_rows, source_missing, source_summary = data_source_file_check(conn)
    raw_groups, raw_summary = raw_snapshot_check(args.raw_index_db)
    checks = issue_checks(conn, raw_summary, source_missing, args.algorithm_version)
    summary = {
        "生成时间": datetime.now().astimezone().isoformat(timespec="seconds"),
        "数据库": str(args.db_path),
        "算法版本": args.algorithm_version,
        "质量检查汇总": status_summary(checks),
        "质量检查明细": checks,
        "采集加工文件检查": {
            "数据来源文件汇总": source_summary,
            "数据来源缺失文件": source_missing[:200],
            "数据来源分布": source_overview(conn),
            "原始快照汇总": raw_summary,
            "原始快照分布": raw_groups,
            "目录体积Top": sorted(
                dir_stats(PROJECT_ROOT / "data" / "raw")
                + dir_stats(PROJECT_ROOT / "data" / "normalized")
                + dir_stats(PROJECT_ROOT / "outputs"),
                key=lambda row: row["大小_MB"],
                reverse=True,
            )[:30],
            "数据来源引用文件明细": source_rows,
        },
        "入库表统计": table_overview(conn),
        "渠道统计": channel_overview(conn),
        "策略数据统计": strategy_statistics(conn),
        "调仓数据统计": rebalance_statistics(conn),
        "持仓数据统计": holding_statistics(conn),
        "业绩数据统计": performance_statistics(conn),
        "基金数据统计": fund_statistics(conn),
        "回放与业绩对比": simulation_statistics(conn, args.algorithm_version),
    }
    conn.close()
    return summary


def main() -> None:
    args = parse_args()
    output_dir = args.output_dir or default_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    args.site_dir.mkdir(parents=True, exist_ok=True)
    summary = build_summary(args)
    table_count_map = {row["表名"]: row["行数"] for row in summary["入库表统计"]}
    html_text = render_report(summary)
    summary_path = output_dir / "full_data_statistics_summary.json"
    page_path = output_dir / "full_data_statistics_report.html"
    site_page_path = args.site_dir / "index.html"
    write_text_if_changed(summary_path, json.dumps(summary, ensure_ascii=False, indent=2))
    write_text_if_changed(page_path, html_text)
    write_text_if_changed(site_page_path, html_text)
    print(
        json.dumps(
            {
                "输出目录": str(output_dir),
                "页面报告": str(page_path),
                "站点页面": str(site_page_path),
                "质量检查汇总": summary["质量检查汇总"],
                "策略数": table_count_map.get("策略信息"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
