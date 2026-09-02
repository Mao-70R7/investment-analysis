import argparse
import csv
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from benchmark_asset_classification import (
    build_strategy_benchmark_asset_rows,
    export_rows_csv,
    write_strategy_benchmark_asset_table,
)


ROOT = Path(os.environ.get("ADVISOR_CODE_ROOT") or Path.cwd()).resolve()
if not (ROOT / "AGENTS.md").is_file():
    raise RuntimeError("ADVISOR_CODE_ROOT or current working directory must be the code root containing AGENTS.md")
DEFAULT_DB_PATH = Path(os.environ.get("ADVISOR_DATABASE_ROOT") or ROOT / "data") / "analysis_zh_current.sqlite"
DEFAULT_OUTPUT_ROOT = Path(os.environ.get("ADVISOR_OUTPUT_ROOT") or ROOT / "outputs")
TABLE_NAME = "策略基准费率状态"


def norm_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def parse_fee_rate_pct(value: Any) -> float | None:
    text = norm_text(value)
    if not text:
        return None
    lowered = text.lower()
    if lowered in {"0", "0.0", "0%", "免费", "免投顾费", "无", "不收取"}:
        return 0.0
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*[%％]", text)
    if match:
        return float(match.group(1))
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)", text)
    if not match:
        return None
    value_num = float(match.group(1))
    return value_num if value_num > 1 else value_num * 100


def init_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        f'''
        CREATE TABLE IF NOT EXISTS "{TABLE_NAME}" (
            "统一策略ID" TEXT PRIMARY KEY,
            "渠道ID" TEXT,
            "渠道名称" TEXT,
            "渠道策略ID" TEXT,
            "策略名称" TEXT,
            "投顾机构" TEXT,
            "投顾费率文本" TEXT,
            "年化投顾费率_百分比" REAL,
            "费率状态" TEXT,
            "业绩基准文本" TEXT,
            "基准文本状态" TEXT,
            "披露净值基准行数" INTEGER,
            "日度业绩基准行数" INTEGER,
            "区间基准行数" INTEGER,
            "基准曲线起始日期" TEXT,
            "基准曲线结束日期" TEXT,
            "基准曲线状态" TEXT,
            "基准可用状态" TEXT,
            "基础数据等级" TEXT,
            "建议补采动作" TEXT,
            "最近更新时间" TEXT
        )
        '''
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Build strategy benchmark, fee and benchmark-asset status tables.")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    args = parser.parse_args()
    db_path = args.db_path.resolve()
    if not db_path.is_file():
        raise SystemExit(f"missing analysis database: {db_path}")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    init_table(conn)

    benchmark_rows = {
        row["统一策略ID"]: dict(row)
        for row in conn.execute(
            '''
            WITH disclosed AS (
              SELECT "统一策略ID",
                     COUNT(*) AS disclosed_rows,
                     MIN("交易日期") AS disclosed_start,
                     MAX("交易日期") AS disclosed_end
              FROM "策略产品披露净值"
              WHERE "基准收益率_百分比" IS NOT NULL
              GROUP BY "统一策略ID"
            ),
            daily AS (
              SELECT "统一策略ID",
                     COUNT(*) AS daily_rows,
                     MIN("交易日期") AS daily_start,
                     MAX("交易日期") AS daily_end
              FROM "策略日度业绩"
              WHERE "基准收益率_百分比" IS NOT NULL
              GROUP BY "统一策略ID"
            ),
            interval_perf AS (
              SELECT "统一策略ID", COUNT(*) AS interval_rows
              FROM "策略区间业绩"
              WHERE "基准收益率_百分比" IS NOT NULL
              GROUP BY "统一策略ID"
            ),
            curve_dates AS (
              SELECT "统一策略ID", "交易日期"
              FROM "策略产品披露净值"
              WHERE "基准收益率_百分比" IS NOT NULL
              UNION
              SELECT "统一策略ID", "交易日期"
              FROM "策略日度业绩"
              WHERE "基准收益率_百分比" IS NOT NULL
            ),
            curve AS (
              SELECT "统一策略ID",
                     COUNT(DISTINCT "交易日期") AS curve_rows,
                     MIN("交易日期") AS curve_start,
                     MAX("交易日期") AS curve_end
              FROM curve_dates
              GROUP BY "统一策略ID"
            )
            SELECT
              s."统一策略ID",
              COALESCE(disclosed.disclosed_rows, 0) AS disclosed_rows,
              COALESCE(daily.daily_rows, 0) AS daily_rows,
              COALESCE(interval_perf.interval_rows, 0) AS interval_rows,
              COALESCE(curve.curve_rows, 0) AS curve_rows,
              curve.curve_start AS curve_start,
              curve.curve_end AS curve_end
            FROM "策略信息" s
            LEFT JOIN disclosed ON disclosed."统一策略ID" = s."统一策略ID"
            LEFT JOIN daily ON daily."统一策略ID" = s."统一策略ID"
            LEFT JOIN interval_perf ON interval_perf."统一策略ID" = s."统一策略ID"
            LEFT JOIN curve ON curve."统一策略ID" = s."统一策略ID"
            '''
        )
    }

    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    rows: list[dict[str, Any]] = []
    for row in conn.execute(
        '''
        SELECT s.*, c."渠道名称"
        FROM "策略信息" s
        LEFT JOIN "渠道信息" c ON c."渠道ID" = s."渠道ID"
        ORDER BY s."渠道ID", s."策略名称"
        '''
    ):
        strategy = dict(row)
        fee_text = norm_text(strategy.get("投顾费率"))
        fee_pct = parse_fee_rate_pct(fee_text)
        fee_status = "已披露可解析" if fee_pct is not None else "缺失"
        benchmark_text = norm_text(strategy.get("业绩基准"))
        b = benchmark_rows.get(strategy["统一策略ID"], {})
        disclosed_rows = int(b.get("disclosed_rows") or 0)
        daily_rows = int(b.get("daily_rows") or 0)
        interval_rows = int(b.get("interval_rows") or 0)
        curve_rows = int(b.get("curve_rows") or 0)
        has_curve = curve_rows >= 2
        benchmark_text_status = "已披露" if benchmark_text else "缺失"
        benchmark_curve_status = "有日度基准曲线" if has_curve else ("仅区间基准" if interval_rows > 0 else "缺失")
        if benchmark_text and has_curve:
            benchmark_status = "文本+曲线"
        elif has_curve:
            benchmark_status = "仅曲线"
        elif benchmark_text:
            benchmark_status = "仅文本"
        elif interval_rows:
            benchmark_status = "仅区间"
        else:
            benchmark_status = "缺失"

        if fee_pct is not None and (benchmark_text or has_curve):
            grade = "A"
        elif fee_pct is not None or benchmark_text or has_curve:
            grade = "B"
        else:
            grade = "C"

        actions = []
        if fee_pct is None:
            actions.append("补投顾费率")
        if not benchmark_text:
            if has_curve:
                actions.append("核验是否披露业绩基准文本（当前仅曲线）")
            elif interval_rows:
                actions.append("核验是否披露业绩基准文本（当前仅区间）")
            else:
                actions.append("补业绩基准文本")
        if not has_curve and interval_rows == 0:
            actions.append("补基准收益曲线或基准公式")
        action = "无需优先补采" if not actions else "；".join(actions)

        status_row = {
            "统一策略ID": strategy["统一策略ID"],
            "渠道ID": strategy["渠道ID"],
            "渠道名称": strategy.get("渠道名称"),
            "渠道策略ID": strategy.get("渠道策略ID"),
            "策略名称": strategy.get("策略名称"),
            "投顾机构": strategy.get("投顾机构"),
            "投顾费率文本": fee_text,
            "年化投顾费率_百分比": fee_pct,
            "费率状态": fee_status,
            "业绩基准文本": benchmark_text,
            "基准文本状态": benchmark_text_status,
            "披露净值基准行数": disclosed_rows,
            "日度业绩基准行数": daily_rows,
            "区间基准行数": interval_rows,
            "基准曲线起始日期": b.get("curve_start"),
            "基准曲线结束日期": b.get("curve_end"),
            "基准曲线状态": benchmark_curve_status,
            "基准可用状态": benchmark_status,
            "基础数据等级": grade,
            "建议补采动作": action,
            "最近更新时间": now,
        }
        rows.append(status_row)

    conn.execute(f'DELETE FROM "{TABLE_NAME}"')
    placeholders = ",".join(["?"] * len(rows[0])) if rows else ""
    if rows:
        columns = list(rows[0].keys())
        quoted_columns = ",".join(f'"{col}"' for col in columns)
        conn.executemany(
            f'INSERT INTO "{TABLE_NAME}" ({quoted_columns}) VALUES ({placeholders})',
            [[row[col] for col in columns] for row in rows],
        )
    asset_rows = build_strategy_benchmark_asset_rows(conn)
    asset_summary = write_strategy_benchmark_asset_table(conn, asset_rows)
    conn.commit()

    summary = {
        "total": len(rows),
        "fee_ready": sum(1 for row in rows if row["费率状态"] == "已披露可解析"),
        "benchmark_text_ready": sum(1 for row in rows if row["基准文本状态"] == "已披露"),
        "benchmark_curve_ready": sum(1 for row in rows if row["基准曲线状态"] == "有日度基准曲线"),
        "grade_counts": {},
    }
    for row in rows:
        summary["grade_counts"][row["基础数据等级"]] = summary["grade_counts"].get(row["基础数据等级"], 0) + 1

    output_dir = args.output_root.resolve() / "basic_data_readiness"
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "strategy_benchmark_fee_status.csv"
    if rows:
        with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
    summary_path = output_dir / "strategy_benchmark_fee_status_summary.json"
    asset_csv_path = output_dir / "strategy_benchmark_asset_mix.csv"
    export_rows_csv(asset_rows, asset_csv_path)
    summary.update(asset_summary)
    summary["asset_csv_path"] = str(asset_csv_path)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    conn.close()
    print(json.dumps({"csv_path": str(csv_path), "summary_path": str(summary_path), **summary}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
