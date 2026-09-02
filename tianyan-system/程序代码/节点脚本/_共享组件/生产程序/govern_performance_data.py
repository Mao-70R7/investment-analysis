from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "outputs" / "performance_data_governance"
DEFAULT_STANDARD_ALGORITHM_VERSION = "standard_rebalance_asset_dual_nav_v10_all_channels_20260528"
DEFAULT_DISCLOSURE_VERSION = "app_disclosure_nav_v1_20260528"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="治理策略业绩数据，生成 App 披露净值、标准回放净值和两者对比。")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--standard-algorithm-version", default=DEFAULT_STANDARD_ALGORITHM_VERSION)
    parser.add_argument("--disclosure-version", default=DEFAULT_DISCLOSURE_VERSION)
    parser.add_argument("--no-backup", action="store_true", help="跳过数据库备份。")
    parser.add_argument("--skip-vacuum", action="store_true", help="清理旧算法产物后不执行 VACUUM。")
    return parser.parse_args()


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


def round_or_none(value: float | None, digits: int = 8) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(value, digits)


def fetch_dicts(conn: sqlite3.Connection, sql: str, params: list[Any] | tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    return [dict(row) for row in conn.execute(sql, params)]


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}


def create_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS "策略产品披露净值" (
            "统一策略ID" TEXT NOT NULL,
            "披露口径版本" TEXT NOT NULL,
            "渠道ID" TEXT NOT NULL,
            "渠道策略ID" TEXT,
            "策略名称" TEXT,
            "投顾机构" TEXT,
            "交易日期" TEXT NOT NULL,
            "披露单位净值" REAL,
            "披露累计收益率_百分比" REAL,
            "披露日收益率_百分比" REAL,
            "原始单位净值" REAL,
            "原始累计收益率_百分比" REAL,
            "原始日收益率_百分比" REAL,
            "基准收益率_百分比" REAL,
            "最大回撤_百分比" REAL,
            "业绩区段名称" TEXT,
            "业绩区段类型" TEXT,
            "净值来源" TEXT NOT NULL,
            "是否可画曲线" INTEGER NOT NULL,
            "问题说明" TEXT,
            "原始快照ID" TEXT,
            "生成时间" TEXT NOT NULL,
            PRIMARY KEY ("统一策略ID", "披露口径版本", "交易日期")
        );

        CREATE INDEX IF NOT EXISTS "idx_策略产品披露净值_渠道_日期"
        ON "策略产品披露净值"("渠道ID", "交易日期");

        CREATE TABLE IF NOT EXISTS "策略标准业绩净值" (
            "统一策略ID" TEXT NOT NULL,
            "算法版本" TEXT NOT NULL,
            "渠道ID" TEXT NOT NULL,
            "渠道策略ID" TEXT,
            "策略名称" TEXT,
            "投顾机构" TEXT,
            "交易日期" TEXT NOT NULL,
            "标准费后单位净值" REAL,
            "标准费前单位净值" REAL,
            "标准费后累计收益率_百分比" REAL,
            "标准费前累计收益率_百分比" REAL,
            "标准费后日收益率_百分比" REAL,
            "标准费前日收益率_百分比" REAL,
            "当日投顾费_元" REAL,
            "累计投顾费_元" REAL,
            "调仓日期" TEXT,
            "调仓事件ID" TEXT,
            "质量等级" TEXT,
            "口径说明" TEXT NOT NULL,
            "生成时间" TEXT NOT NULL,
            PRIMARY KEY ("统一策略ID", "算法版本", "交易日期")
        );

        CREATE INDEX IF NOT EXISTS "idx_策略标准业绩净值_渠道_日期"
        ON "策略标准业绩净值"("渠道ID", "交易日期");

        CREATE TABLE IF NOT EXISTS "策略业绩口径对比" (
            "统一策略ID" TEXT NOT NULL,
            "披露口径版本" TEXT NOT NULL,
            "算法版本" TEXT NOT NULL,
            "渠道ID" TEXT NOT NULL,
            "渠道策略ID" TEXT,
            "策略名称" TEXT,
            "投顾机构" TEXT,
            "对比状态" TEXT NOT NULL,
            "官方披露记录数" INTEGER NOT NULL,
            "标准回放记录数" INTEGER NOT NULL,
            "共同交易日数" INTEGER NOT NULL,
            "披露起始日期" TEXT,
            "披露结束日期" TEXT,
            "标准起始日期" TEXT,
            "标准结束日期" TEXT,
            "对比起始日期" TEXT,
            "对比结束日期" TEXT,
            "产品披露区间收益率_百分比" REAL,
            "标准费前区间收益率_百分比" REAL,
            "标准费后区间收益率_百分比" REAL,
            "标准费前相对披露偏差_百分点" REAL,
            "标准费后相对披露偏差_百分点" REAL,
            "标准费前绝对偏差_百分点" REAL,
            "标准费后绝对偏差_百分点" REAL,
            "更接近披露口径" TEXT,
            "问题说明" TEXT,
            "生成时间" TEXT NOT NULL,
            PRIMARY KEY ("统一策略ID", "披露口径版本", "算法版本")
        );

        CREATE INDEX IF NOT EXISTS "idx_策略业绩口径对比_渠道"
        ON "策略业绩口径对比"("渠道ID", "对比状态");

        CREATE TABLE IF NOT EXISTS "业绩数据治理记录" (
            "记录ID" TEXT PRIMARY KEY,
            "治理批次" TEXT NOT NULL,
            "对象类型" TEXT NOT NULL,
            "对象名称" TEXT NOT NULL,
            "处理动作" TEXT NOT NULL,
            "处理行数" INTEGER NOT NULL,
            "处理说明" TEXT,
            "生成时间" TEXT NOT NULL
        );
        """
    )


def backup_database(db_path: Path, generated_at: str) -> Path | None:
    if not db_path.exists():
        return None
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = generated_at.replace("-", "").replace(":", "").replace("+", "").replace("T", "T")
    backup_path = backup_dir / f"{db_path.stem}_before_performance_governance_{stamp}{db_path.suffix}"
    shutil.copy2(db_path, backup_path)
    return backup_path


def clean_generated_data(
    conn: sqlite3.Connection,
    standard_algorithm_version: str,
    disclosure_version: str,
    generated_at: str,
    skip_vacuum: bool,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []

    def add_record(object_type: str, object_name: str, action: str, count: int, note: str) -> None:
        records.append(
            {
                "记录ID": f"{generated_at}-{len(records)+1:04d}",
                "治理批次": generated_at,
                "对象类型": object_type,
                "对象名称": object_name,
                "处理动作": action,
                "处理行数": int(count),
                "处理说明": note,
                "生成时间": generated_at,
            }
        )

    # 删除旧算法产物，只保留本次统一标准算法版本。
    for table in [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]:
        cols = table_columns(conn, table)
        if "算法版本" not in cols:
            continue
        count = int(conn.execute(f'SELECT COUNT(*) FROM "{table}" WHERE "算法版本" <> ?', [standard_algorithm_version]).fetchone()[0])
        if count:
            conn.execute(f'DELETE FROM "{table}" WHERE "算法版本" <> ?', [standard_algorithm_version])
        add_record("算法产物表", table, "删除旧算法版本", count, f"仅保留算法版本 {standard_algorithm_version}")

    # 本次衍生表按版本重建，避免重复和历史脏数据。
    versioned_tables = [
        ("策略产品披露净值", "披露口径版本", disclosure_version),
        ("策略标准业绩净值", "算法版本", standard_algorithm_version),
        ("策略业绩口径对比", "披露口径版本", disclosure_version),
    ]
    for table, column, version in versioned_tables:
        if table not in [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]:
            continue
        count = int(conn.execute(f'SELECT COUNT(*) FROM "{table}" WHERE "{column}" = ?', [version]).fetchone()[0])
        if count:
            conn.execute(f'DELETE FROM "{table}" WHERE "{column}" = ?', [version])
        add_record("业绩口径衍生表", table, "重建当前版本", count, f"清理当前版本旧记录：{version}")

    conn.execute('DELETE FROM "业绩数据治理记录" WHERE "治理批次" = ?', [generated_at])
    if not skip_vacuum:
        conn.commit()
        conn.execute("VACUUM")
    return records


def derive_disclosure_rows(conn: sqlite3.Connection, disclosure_version: str, generated_at: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows = fetch_dicts(
        conn,
        """
        SELECT
            p.*,
            s."策略名称",
            s."投顾机构"
        FROM "策略日度业绩" p
        LEFT JOIN "策略信息" s
          ON p."统一策略ID" = s."统一策略ID"
        WHERE COALESCE(p."业绩区段类型", '') <> 'public_quote'
        ORDER BY p."统一策略ID", p."交易日期"
        """,
    )
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["统一策略ID"])].append(row)

    output: list[dict[str, Any]] = []
    issue_rows: list[dict[str, Any]] = []
    for strategy_id, items in grouped.items():
        previous_nav: float | None = None
        first_nav: float | None = None
        for idx, row in enumerate(items):
            original_nav = to_float(row.get("单位净值"))
            original_cum = to_float(row.get("累计收益率_百分比"))
            original_daily = to_float(row.get("日收益率_百分比"))
            nav: float | None = None
            source = "不可反推"
            issue: str | None = None

            if original_nav is not None and original_nav > 0:
                nav = original_nav
                source = "App单位净值"
            elif original_cum is not None:
                nav = 1.0 + original_cum / 100.0
                source = "App累计收益率反推"
            elif original_daily is not None:
                if previous_nav is None:
                    previous_nav = 1.0
                    source = "App日收益率连乘_起点1"
                else:
                    source = "App日收益率连乘"
                nav = previous_nav * (1.0 + original_daily / 100.0)
            else:
                issue = "单位净值、累计收益率、日收益率均缺失，无法反推披露净值"

            if nav is not None and nav <= 0:
                issue = "反推披露单位净值非正"
                nav = None

            if nav is not None:
                if first_nav is None:
                    first_nav = nav
                if original_cum is not None:
                    disclosure_cum = original_cum
                elif first_nav not in (None, 0):
                    disclosure_cum = (nav / first_nav - 1.0) * 100.0
                else:
                    disclosure_cum = None
                if original_daily is not None:
                    disclosure_daily = original_daily
                elif previous_nav not in (None, 0) and idx > 0:
                    disclosure_daily = (nav / previous_nav - 1.0) * 100.0
                else:
                    disclosure_daily = 0.0
                previous_nav = nav
                curve_ok = 1
            else:
                disclosure_cum = None
                disclosure_daily = None
                curve_ok = 0

            output.append(
                {
                    "统一策略ID": strategy_id,
                    "披露口径版本": disclosure_version,
                    "渠道ID": row.get("渠道ID"),
                    "渠道策略ID": row.get("渠道策略ID"),
                    "策略名称": row.get("策略名称"),
                    "投顾机构": row.get("投顾机构"),
                    "交易日期": row.get("交易日期"),
                    "披露单位净值": round_or_none(nav, 8),
                    "披露累计收益率_百分比": round_or_none(disclosure_cum, 8),
                    "披露日收益率_百分比": round_or_none(disclosure_daily, 8),
                    "原始单位净值": round_or_none(original_nav, 8),
                    "原始累计收益率_百分比": round_or_none(original_cum, 8),
                    "原始日收益率_百分比": round_or_none(original_daily, 8),
                    "基准收益率_百分比": round_or_none(to_float(row.get("基准收益率_百分比")), 8),
                    "最大回撤_百分比": round_or_none(to_float(row.get("最大回撤_百分比")), 8),
                    "业绩区段名称": row.get("业绩区段名称"),
                    "业绩区段类型": row.get("业绩区段类型"),
                    "净值来源": source,
                    "是否可画曲线": curve_ok,
                    "问题说明": issue,
                    "原始快照ID": row.get("原始快照ID"),
                    "生成时间": generated_at,
                }
            )
            if issue:
                issue_rows.append(output[-1])

    return output, issue_rows


def derive_standard_rows(conn: sqlite3.Connection, standard_algorithm_version: str, generated_at: str) -> list[dict[str, Any]]:
    return fetch_dicts(
        conn,
        """
        SELECT
            n."统一策略ID",
            n."算法版本",
            n."渠道ID",
            n."渠道策略ID",
            n."策略名称",
            s."投顾机构",
            n."交易日期",
            n."模拟单位净值" AS "标准费后单位净值",
            n."费前单位净值" AS "标准费前单位净值",
            n."累计收益率_百分比" AS "标准费后累计收益率_百分比",
            n."费前累计收益率_百分比" AS "标准费前累计收益率_百分比",
            n."日收益率_百分比" AS "标准费后日收益率_百分比",
            n."费前日收益率_百分比" AS "标准费前日收益率_百分比",
            n."当日投顾费_元",
            n."累计投顾费_元",
            n."调仓日期",
            n."调仓事件ID",
            n."质量等级"
        FROM "策略模拟净值" n
        LEFT JOIN "策略信息" s
          ON n."统一策略ID" = s."统一策略ID"
        WHERE n."算法版本" = ?
        ORDER BY n."渠道ID", n."统一策略ID", n."交易日期"
        """,
        [standard_algorithm_version],
    )


def with_standard_note(rows: list[dict[str, Any]], generated_at: str) -> list[dict[str, Any]]:
    note = "统一标准回放口径：基于历史调仓后的基金仓位占比、基金净值和分红复投推算；同时保留费前和费后净值。"
    output: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["口径说明"] = note
        item["生成时间"] = generated_at
        output.append(item)
    return output


def build_comparison_rows(
    conn: sqlite3.Connection,
    disclosure_version: str,
    standard_algorithm_version: str,
    generated_at: str,
) -> list[dict[str, Any]]:
    strategies = fetch_dicts(
        conn,
        """
        SELECT "统一策略ID", "渠道ID", "渠道策略ID", "策略名称", "投顾机构"
        FROM "策略信息"
        ORDER BY "渠道ID", "统一策略ID"
        """,
    )
    disclosure = fetch_dicts(
        conn,
        """
        SELECT "统一策略ID", "交易日期", "披露单位净值"
        FROM "策略产品披露净值"
        WHERE "披露口径版本" = ? AND "是否可画曲线" = 1 AND "披露单位净值" IS NOT NULL
        ORDER BY "统一策略ID", "交易日期"
        """,
        [disclosure_version],
    )
    standard = fetch_dicts(
        conn,
        """
        SELECT "统一策略ID", "交易日期", "标准费前单位净值", "标准费后单位净值"
        FROM "策略标准业绩净值"
        WHERE "算法版本" = ?
        ORDER BY "统一策略ID", "交易日期"
        """,
        [standard_algorithm_version],
    )
    disclosed_by_strategy: dict[str, dict[str, float]] = defaultdict(dict)
    standard_by_strategy: dict[str, dict[str, tuple[float | None, float | None]]] = defaultdict(dict)
    for row in disclosure:
        nav = to_float(row["披露单位净值"])
        if nav is not None:
            disclosed_by_strategy[str(row["统一策略ID"])][str(row["交易日期"])] = nav
    for row in standard:
        standard_by_strategy[str(row["统一策略ID"])][str(row["交易日期"])] = (
            to_float(row["标准费前单位净值"]),
            to_float(row["标准费后单位净值"]),
        )

    output: list[dict[str, Any]] = []
    for strategy in strategies:
        strategy_id = str(strategy["统一策略ID"])
        dmap = disclosed_by_strategy.get(strategy_id, {})
        smap = standard_by_strategy.get(strategy_id, {})
        common_dates = sorted(set(dmap).intersection(smap))
        d_dates = sorted(dmap)
        s_dates = sorted(smap)

        status = "可对比"
        issue = None
        if not d_dates:
            status = "无官方披露曲线"
            issue = "当前未从 App/官方渠道取得可反推每日净值的业绩曲线。"
        elif not s_dates:
            status = "无标准回放净值"
            issue = "当前标准算法未能基于仓位和基金净值生成该策略回放净值。"
        elif len(common_dates) < 2:
            status = "共同交易日不足"
            issue = "官方披露净值与标准回放净值共同日期少于2个，无法计算同区间收益。"

        disclosure_return = gross_return = net_return = gross_diff = net_diff = gross_abs = net_abs = None
        basis = None
        compare_start = common_dates[0] if len(common_dates) >= 2 else None
        compare_end = common_dates[-1] if len(common_dates) >= 2 else None
        if compare_start and compare_end:
            start_disclosure = dmap[compare_start]
            end_disclosure = dmap[compare_end]
            start_gross, start_net = smap[compare_start]
            end_gross, end_net = smap[compare_end]
            if start_disclosure:
                disclosure_return = (end_disclosure / start_disclosure - 1.0) * 100.0
            if start_gross:
                gross_return = (end_gross / start_gross - 1.0) * 100.0 if end_gross is not None else None
            if start_net:
                net_return = (end_net / start_net - 1.0) * 100.0 if end_net is not None else None
            if disclosure_return is not None and gross_return is not None:
                gross_diff = gross_return - disclosure_return
                gross_abs = abs(gross_diff)
            if disclosure_return is not None and net_return is not None:
                net_diff = net_return - disclosure_return
                net_abs = abs(net_diff)
            if gross_abs is not None and net_abs is not None:
                if abs(gross_abs - net_abs) <= 0.05:
                    basis = "费前费后接近"
                elif gross_abs < net_abs:
                    basis = "标准费前"
                else:
                    basis = "标准费后"

        output.append(
            {
                "统一策略ID": strategy_id,
                "披露口径版本": disclosure_version,
                "算法版本": standard_algorithm_version,
                "渠道ID": strategy.get("渠道ID"),
                "渠道策略ID": strategy.get("渠道策略ID"),
                "策略名称": strategy.get("策略名称"),
                "投顾机构": strategy.get("投顾机构"),
                "对比状态": status,
                "官方披露记录数": len(d_dates),
                "标准回放记录数": len(s_dates),
                "共同交易日数": len(common_dates),
                "披露起始日期": d_dates[0] if d_dates else None,
                "披露结束日期": d_dates[-1] if d_dates else None,
                "标准起始日期": s_dates[0] if s_dates else None,
                "标准结束日期": s_dates[-1] if s_dates else None,
                "对比起始日期": compare_start,
                "对比结束日期": compare_end,
                "产品披露区间收益率_百分比": round_or_none(disclosure_return, 6),
                "标准费前区间收益率_百分比": round_or_none(gross_return, 6),
                "标准费后区间收益率_百分比": round_or_none(net_return, 6),
                "标准费前相对披露偏差_百分点": round_or_none(gross_diff, 6),
                "标准费后相对披露偏差_百分点": round_or_none(net_diff, 6),
                "标准费前绝对偏差_百分点": round_or_none(gross_abs, 6),
                "标准费后绝对偏差_百分点": round_or_none(net_abs, 6),
                "更接近披露口径": basis,
                "问题说明": issue,
                "生成时间": generated_at,
            }
        )
    return output


def insert_rows(conn: sqlite3.Connection, table: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    columns = list(rows[0].keys())
    col_sql = ", ".join(f'"{column}"' for column in columns)
    placeholders = ", ".join("?" for _ in columns)
    conn.executemany(
        f'INSERT OR REPLACE INTO "{table}" ({col_sql}) VALUES ({placeholders})',
        [[row.get(column) for column in columns] for row in rows],
    )


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    columns = list(rows[0].keys())
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def summarize(
    disclosure_rows: list[dict[str, Any]],
    standard_rows: list[dict[str, Any]],
    comparison_rows: list[dict[str, Any]],
    governance_records: list[dict[str, Any]],
    backup_path: Path | None,
    standard_algorithm_version: str,
    disclosure_version: str,
    generated_at: str,
) -> dict[str, Any]:
    by_channel: dict[str, Counter[str]] = defaultdict(Counter)
    for row in comparison_rows:
        by_channel[str(row["渠道ID"])][str(row["对比状态"])] += 1
    comparable = [row for row in comparison_rows if row["对比状态"] == "可对比"]
    gross_abs = [float(row["标准费前绝对偏差_百分点"]) for row in comparable if row["标准费前绝对偏差_百分点"] is not None]
    net_abs = [float(row["标准费后绝对偏差_百分点"]) for row in comparable if row["标准费后绝对偏差_百分点"] is not None]

    def median(values: list[float]) -> float | None:
        if not values:
            return None
        values = sorted(values)
        mid = len(values) // 2
        if len(values) % 2:
            return values[mid]
        return (values[mid - 1] + values[mid]) / 2.0

    return {
        "生成时间": generated_at,
        "数据库备份": str(backup_path) if backup_path else None,
        "披露口径版本": disclosure_version,
        "标准算法版本": standard_algorithm_version,
        "治理记录数": len(governance_records),
        "旧数据清理行数": sum(int(row["处理行数"]) for row in governance_records),
        "产品披露净值行数": len(disclosure_rows),
        "产品披露净值策略数": len({row["统一策略ID"] for row in disclosure_rows if row["是否可画曲线"] == 1}),
        "标准业绩净值行数": len(standard_rows),
        "标准业绩净值策略数": len({row["统一策略ID"] for row in standard_rows}),
        "业绩口径对比策略数": len(comparison_rows),
        "可对比策略数": len(comparable),
        "按渠道对比状态": {channel: dict(counter) for channel, counter in sorted(by_channel.items())},
        "标准费前相对披露绝对偏差中位数_百分点": round_or_none(median(gross_abs), 6),
        "标准费后相对披露绝对偏差中位数_百分点": round_or_none(median(net_abs), 6),
    }


def main() -> None:
    args = parse_args()
    generated_at = datetime.now().isoformat(timespec="seconds")
    output_dir = args.output_dir / generated_at.replace(":", "").replace("-", "")
    output_dir.mkdir(parents=True, exist_ok=True)

    backup_path = None if args.no_backup else backup_database(args.db_path, generated_at)
    conn = sqlite3.connect(args.db_path)
    try:
        create_tables(conn)
        governance_records = clean_generated_data(
            conn,
            args.standard_algorithm_version,
            args.disclosure_version,
            generated_at,
            args.skip_vacuum,
        )
        disclosure_rows, disclosure_issues = derive_disclosure_rows(conn, args.disclosure_version, generated_at)
        standard_rows = with_standard_note(derive_standard_rows(conn, args.standard_algorithm_version, generated_at), generated_at)
        insert_rows(conn, "策略产品披露净值", disclosure_rows)
        insert_rows(conn, "策略标准业绩净值", standard_rows)
        comparison_rows = build_comparison_rows(
            conn,
            args.disclosure_version,
            args.standard_algorithm_version,
            generated_at,
        )
        insert_rows(conn, "策略业绩口径对比", comparison_rows)
        insert_rows(conn, "业绩数据治理记录", governance_records)
        conn.commit()
        conn.execute("PRAGMA optimize")
    finally:
        conn.close()

    summary = summarize(
        disclosure_rows,
        standard_rows,
        comparison_rows,
        governance_records,
        backup_path,
        args.standard_algorithm_version,
        args.disclosure_version,
        generated_at,
    )
    write_csv(output_dir / "performance_governance_records.csv", governance_records)
    write_csv(output_dir / "disclosure_nav_issues.csv", disclosure_issues)
    write_csv(output_dir / "performance_basis_comparison.csv", comparison_rows)
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "performance_governance_report.md").write_text(
        "\n".join(
            [
                "# 投顾策略业绩口径治理报告",
                "",
                f"- 生成时间：{generated_at}",
                f"- 披露口径版本：`{args.disclosure_version}`",
                f"- 标准算法版本：`{args.standard_algorithm_version}`",
                f"- 数据库备份：`{backup_path}`" if backup_path else "- 数据库备份：未执行",
                f"- 产品披露净值：{summary['产品披露净值行数']} 行，{summary['产品披露净值策略数']} 个策略",
                f"- 标准业绩净值：{summary['标准业绩净值行数']} 行，{summary['标准业绩净值策略数']} 个策略",
                f"- 可对比策略：{summary['可对比策略数']} / {summary['业绩口径对比策略数']}",
                f"- 标准费前相对披露绝对偏差中位数：{summary['标准费前相对披露绝对偏差中位数_百分点']} 个百分点",
                f"- 标准费后相对披露绝对偏差中位数：{summary['标准费后相对披露绝对偏差中位数_百分点']} 个百分点",
                "",
                "## 说明",
                "",
                "- 产品披露净值只来自 App/官方披露业绩曲线，不使用基金仓位反推。",
                "- 标准业绩净值来自统一标准算法，用历史调仓基金仓位、基金净值和分红复投回放。",
                "- 两套净值并存，差异在 `策略业绩口径对比` 表中保存。",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
