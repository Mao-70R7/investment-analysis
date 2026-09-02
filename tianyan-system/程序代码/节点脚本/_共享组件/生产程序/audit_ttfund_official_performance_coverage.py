from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
DEFAULT_ALGORITHM_VERSION = "rebalance_asset_fee_dual_nav_v9_ttfund_rules_cifm_overseas_placeholder_20260527"
CHANNEL_ID = "ttfund"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="评估天天基金 App/接口官方业绩数据覆盖和曲线可用性。")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--algorithm-version", default=DEFAULT_ALGORITHM_VERSION)
    parser.add_argument("--output-dir", type=Path)
    return parser.parse_args()


def default_output_dir() -> Path:
    day = datetime.now().astimezone().strftime("%Y-%m-%d")
    run_id = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z")
    return PROJECT_ROOT / "outputs" / "ttfund_official_performance_coverage" / day / run_id


def fetch_dicts(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, params)]


def fetch_one(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any]:
    row = conn.execute(sql, params).fetchone()
    return dict(row) if row else {}


def to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result):
        return None
    return result


def pct(num: int | float, den: int | float) -> float | None:
    if not den:
        return None
    return round(float(num) * 100.0 / float(den), 4)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    columns: list[str] = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def build_strategy_rows(conn: sqlite3.Connection, algorithm_version: str) -> list[dict[str, Any]]:
    rows = fetch_dicts(
        conn,
        """
        WITH daily AS (
            SELECT
                "统一策略ID",
                COUNT(DISTINCT "交易日期") AS "官方日度点数",
                MIN("交易日期") AS "官方日度最早日期",
                MAX("交易日期") AS "官方日度最晚日期",
                SUM(CASE WHEN "单位净值" IS NOT NULL THEN 1 ELSE 0 END) AS "官方净值点数",
                SUM(CASE WHEN "日收益率_百分比" IS NOT NULL THEN 1 ELSE 0 END) AS "官方日收益点数",
                SUM(CASE WHEN "累计收益率_百分比" IS NOT NULL THEN 1 ELSE 0 END) AS "官方累计收益点数"
            FROM "策略日度业绩"
            WHERE "渠道ID" = ?
              AND "业绩区段类型" = 'official_app_curve'
            GROUP BY "统一策略ID"
        ),
        interval_perf AS (
            SELECT
                "统一策略ID",
                COUNT("区间代码") AS "官方区间记录数",
                COUNT(DISTINCT "区间代码") AS "官方区间口径数",
                SUM(CASE WHEN "区间代码" = 'since_inception' THEN 1 ELSE 0 END) AS "成立来区间记录数",
                MAX(CASE WHEN "区间代码" = 'since_inception' THEN "统计日期" ELSE NULL END) AS "成立来区间最新日期"
            FROM "策略区间业绩"
            WHERE "渠道ID" = ?
            GROUP BY "统一策略ID"
        )
        SELECT
            s."统一策略ID",
            s."渠道策略ID",
            s."策略名称",
            s."投顾机构",
            s."策略类型",
            s."成立日期",
            s."策略状态",
            COALESCE(d."官方日度点数", 0) AS "官方日度点数",
            d."官方日度最早日期",
            d."官方日度最晚日期",
            COALESCE(d."官方净值点数", 0) AS "官方净值点数",
            COALESCE(d."官方日收益点数", 0) AS "官方日收益点数",
            COALESCE(d."官方累计收益点数", 0) AS "官方累计收益点数",
            COALESCE(i."官方区间记录数", 0) AS "官方区间记录数",
            COALESCE(i."官方区间口径数", 0) AS "官方区间口径数",
            COALESCE(i."成立来区间记录数", 0) AS "成立来区间记录数",
            i."成立来区间最新日期",
            q."是否纳入模拟" AS "是否纳入自算回放",
            q."模拟交易日数" AS "自算回放交易日数",
            q."模拟起始日期" AS "自算回放起始日期",
            q."模拟结束日期" AS "自算回放结束日期",
            q."模拟单位净值_期末" AS "自算期末单位净值",
            q."模拟累计收益率_百分比" AS "自算累计收益率_百分比",
            q."官方可比记录数" AS "官方可比记录数",
            COALESCE(q."App展示官方收益差_百分点", q."模拟费前官方收益差_百分点", q."模拟官方收益差_百分点") AS "自算对官方偏差_百分点"
        FROM "策略信息" s
        LEFT JOIN daily d
          ON d."统一策略ID" = s."统一策略ID"
        LEFT JOIN interval_perf i
          ON i."统一策略ID" = s."统一策略ID"
        LEFT JOIN "策略模拟净值质量" q
          ON q."统一策略ID" = s."统一策略ID"
         AND q."算法版本" = ?
         AND q."渠道ID" = ?
        WHERE s."渠道ID" = ?
        ORDER BY s."统一策略ID"
        """,
        (CHANNEL_ID, CHANNEL_ID, algorithm_version, CHANNEL_ID, CHANNEL_ID),
    )
    latest_daily_date = fetch_one(
        conn,
        """
        SELECT MAX("交易日期") AS "最新官方日度日期"
        FROM "策略日度业绩"
        WHERE "渠道ID" = ?
          AND "业绩区段类型" = 'official_app_curve'
        """,
        (CHANNEL_ID,),
    ).get("最新官方日度日期")
    for row in rows:
        daily_points = int(row.get("官方日度点数") or 0)
        row["是否有官方单日业绩点"] = 1 if daily_points > 0 else 0
        row["是否有官方区间业绩"] = 1 if int(row.get("官方区间记录数") or 0) > 0 else 0
        row["是否有成立来收益"] = 1 if int(row.get("成立来区间记录数") or 0) > 0 else 0
        row["是否覆盖最新官方日度日期"] = 1 if latest_daily_date and row.get("官方日度最晚日期") == latest_daily_date else 0
        row["是否可直接绘制创设以来官方曲线"] = 1 if daily_points >= 30 else 0
        row["官方曲线可用性说明"] = (
            "官方日度点过少，仅能作为离散快照/累计收益点"
            if daily_points > 0 and daily_points < 30
            else ("缺少官方日度业绩点" if daily_points == 0 else "具备较多官方日度点，可进一步检查连续性")
        )
    return rows


def build_summary(conn: sqlite3.Connection, rows: list[dict[str, Any]], algorithm_version: str) -> dict[str, Any]:
    strategy_total = len(rows)
    daily_summary = fetch_one(
        conn,
        """
        SELECT COUNT(*) AS "行数",
               COUNT(DISTINCT "统一策略ID") AS "策略数",
               COUNT(DISTINCT "交易日期") AS "日期数",
               MIN("交易日期") AS "最早日期",
               MAX("交易日期") AS "最晚日期",
               SUM(CASE WHEN "单位净值" IS NOT NULL THEN 1 ELSE 0 END) AS "单位净值行数",
               SUM(CASE WHEN "累计收益率_百分比" IS NOT NULL THEN 1 ELSE 0 END) AS "累计收益行数",
               SUM(CASE WHEN "日收益率_百分比" IS NOT NULL THEN 1 ELSE 0 END) AS "日收益行数"
        FROM "策略日度业绩"
        WHERE "渠道ID" = ?
          AND "业绩区段类型" = 'official_app_curve'
        """,
        (CHANNEL_ID,),
    )
    interval_summary = fetch_one(
        conn,
        """
        SELECT COUNT(*) AS "行数",
               COUNT(DISTINCT "统一策略ID") AS "策略数",
               COUNT(DISTINCT "区间代码") AS "区间口径数",
               COUNT(DISTINCT "统计日期") AS "统计日期数",
               MIN("统计日期") AS "最早统计日期",
               MAX("统计日期") AS "最晚统计日期"
        FROM "策略区间业绩"
        WHERE "渠道ID" = ?
        """,
        (CHANNEL_ID,),
    )
    by_interval = fetch_dicts(
        conn,
        """
        SELECT "区间代码", "区间名称", COUNT(*) AS "行数", COUNT(DISTINCT "统一策略ID") AS "策略数",
               MIN("统计日期") AS "最早统计日期", MAX("统计日期") AS "最晚统计日期"
        FROM "策略区间业绩"
        WHERE "渠道ID" = ?
        GROUP BY "区间代码", "区间名称"
        ORDER BY "行数" DESC, "区间代码"
        """,
        (CHANNEL_ID,),
    )
    daily_points_dist = dict(Counter(int(row.get("官方日度点数") or 0) for row in rows))
    interval_codes_dist = dict(Counter(int(row.get("官方区间口径数") or 0) for row in rows))
    simulated_included = sum(1 for row in rows if int(row.get("是否纳入自算回放") or 0) == 1)
    official_daily = sum(1 for row in rows if int(row.get("是否有官方单日业绩点") or 0) == 1)
    official_interval = sum(1 for row in rows if int(row.get("是否有官方区间业绩") or 0) == 1)
    official_since_inception = sum(1 for row in rows if int(row.get("是否有成立来收益") or 0) == 1)
    curve_direct = sum(1 for row in rows if int(row.get("是否可直接绘制创设以来官方曲线") or 0) == 1)
    comparable_with_self = sum(
        1
        for row in rows
        if int(row.get("是否纳入自算回放") or 0) == 1 and int(row.get("是否有官方单日业绩点") or 0) == 1
    )
    latest_daily = str(daily_summary.get("最晚日期") or "")
    latest_daily_count = sum(1 for row in rows if row.get("官方日度最晚日期") == latest_daily)
    if curve_direct:
        curve_judgement = (
            f"可以。当前已有 {curve_direct} 个策略具备不少于 30 个官方日度点，可用于绘制 App 披露口径的成立以来业绩曲线。"
        )
        compare_judgement = "可以。自算净值曲线与 App 官方曲线、官方单日累计收益点和区间收益均可做交叉对比。"
    else:
        curve_judgement = "当前不能。天天已接入口径只提供若干披露日的快照点和区间收益，不是从创设日至今的每日官方净值序列。"
        compare_judgement = "可以。自算净值用于完整曲线，官方单日累计收益点和区间收益用于锚点/区间偏差比对。"
    return {
        "算法版本": algorithm_version,
        "天天策略数": strategy_total,
        "有官方单日业绩点策略数": official_daily,
        "有官方单日业绩点占比_百分比": pct(official_daily, strategy_total),
        "覆盖最新官方日度日期策略数": latest_daily_count,
        "有官方区间业绩策略数": official_interval,
        "有官方成立来收益策略数": official_since_inception,
        "有官方成立来收益占比_百分比": pct(official_since_inception, strategy_total),
        "可直接绘制创设以来官方曲线策略数": curve_direct,
        "自算回放纳入策略数": simulated_included,
        "自算回放且有官方单日点策略数": comparable_with_self,
        "官方日度业绩汇总": daily_summary,
        "官方区间业绩汇总": interval_summary,
        "官方区间口径覆盖": by_interval,
        "每策略官方日度点数分布": daily_points_dist,
        "每策略官方区间口径数分布": interval_codes_dist,
        "判断": {
            "能否保存官方原始业绩": "可以。当前已保存 raw quote 批次、官方曲线接口原始响应、normalized JSONL，并入库到策略日度业绩/策略区间业绩。",
            "能否直接画官方创设以来日度曲线": curve_judgement,
            "能否做自算与官方对比": compare_judgement,
        },
    }


def write_report(output_dir: Path, summary: dict[str, Any], paths: dict[str, str]) -> Path:
    lines = [
        "# 天天基金官方业绩数据覆盖评估",
        "",
        f"- 生成时间：{datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"- 算法版本：`{summary['算法版本']}`",
        "",
        "## 结论",
        f"- 天天策略数：{summary['天天策略数']}。",
        f"- 可从 App/接口拿到官方单日业绩点：{summary['有官方单日业绩点策略数']} 个策略，"
        f"占比 {summary['有官方单日业绩点占比_百分比']}%。",
        f"- 有官方区间业绩：{summary['有官方区间业绩策略数']} 个策略；有成立来收益：{summary['有官方成立来收益策略数']} 个策略。",
        f"- 可直接绘制创设以来官方日度曲线：{summary['可直接绘制创设以来官方曲线策略数']} 个策略。",
        f"- 自算回放纳入：{summary['自算回放纳入策略数']} 个策略；自算且有官方单日锚点：{summary['自算回放且有官方单日点策略数']} 个策略。",
        "",
        "## 官方日度业绩",
        f"- 行数：{summary['官方日度业绩汇总'].get('行数')}；策略数：{summary['官方日度业绩汇总'].get('策略数')}；"
        f"日期数：{summary['官方日度业绩汇总'].get('日期数')}；日期范围："
        f"{summary['官方日度业绩汇总'].get('最早日期')} 至 {summary['官方日度业绩汇总'].get('最晚日期')}。",
        f"- 每策略日度点数分布：{json.dumps(summary['每策略官方日度点数分布'], ensure_ascii=False)}。",
        "",
        "## 官方区间业绩",
        f"- 行数：{summary['官方区间业绩汇总'].get('行数')}；策略数：{summary['官方区间业绩汇总'].get('策略数')}；"
        f"区间口径数：{summary['官方区间业绩汇总'].get('区间口径数')}。",
    ]
    for row in summary["官方区间口径覆盖"]:
        lines.append(f"- {row['区间名称']}({row['区间代码']})：{row['策略数']} 个策略，{row['行数']} 行。")
    lines.extend(
        [
            "",
            "## 建议",
            "- 保留当前 `策略日度业绩` 与 `策略区间业绩`，作为 App 对客展示业绩的标准化事实表。",
            "- 对比口径使用：自算 `策略模拟净值` 画内部回放曲线；官方 `策略日度业绩` 的 `official_app_curve` 画 App 披露曲线；官方 `策略区间业绩` 做 1周/1月/成立来等区间偏差比对。",
            "- 对没有官方曲线的策略，保留 quote 快照和区间业绩作为披露锚点，并在缺失清单中单独标注。",
            "- 每日增量应同时更新官方曲线、quote 快照和区间收益，并把覆盖率、最新日期、曲线/区间差异稽核纳入增量质检。",
            "",
            "## 输出文件",
        ]
    )
    for label, path in paths.items():
        lines.append(f"- {label}：`{path}`")
    report_path = output_dir / "official_performance_coverage_report.md"
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_path


def main() -> None:
    args = parse_args()
    output_dir = args.output_dir or default_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(args.db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = build_strategy_rows(conn, args.algorithm_version)
        summary = build_summary(conn, rows, args.algorithm_version)
    finally:
        conn.close()

    paths: dict[str, str] = {}
    strategy_csv = output_dir / "strategy_official_performance_coverage.csv"
    write_csv(strategy_csv, rows)
    paths["策略覆盖明细"] = str(strategy_csv)

    missing_csv = output_dir / "strategy_missing_official_performance.csv"
    write_csv(
        missing_csv,
        [
            row
            for row in rows
            if int(row.get("是否有官方单日业绩点") or 0) == 0 or int(row.get("是否有官方区间业绩") or 0) == 0
        ],
    )
    paths["官方业绩缺失策略"] = str(missing_csv)

    summary_path = output_dir / "official_performance_coverage_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    paths["汇总JSON"] = str(summary_path)
    report_path = write_report(output_dir, summary, paths)
    summary["输出文件"] = {**paths, "报告": str(report_path)}
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
