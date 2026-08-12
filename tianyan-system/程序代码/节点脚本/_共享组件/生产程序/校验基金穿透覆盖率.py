from __future__ import annotations

import argparse
import json
from pathlib import Path

from fund_lookthrough_common import DEFAULT_DB_PATH, DEFAULT_OUTPUT_ROOT, connect_db, now_cn, run_id, write_csv


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="校验当前策略持仓基金的季报穿透覆盖率。")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def current_holding_fund_rows(conn):
    return conn.execute(
        """
        WITH current_dates AS (
          SELECT "统一策略ID", MAX("持仓日期") AS latest_date
          FROM "策略当前持仓"
          GROUP BY "统一策略ID"
        )
        SELECT h."基金代码", MAX(h."基金名称") AS "基金名称",
               SUM(COALESCE(h."基金权重_百分比",0)) AS "全市场持仓权重",
               COUNT(DISTINCT h."统一策略ID") AS "持仓策略数"
        FROM "策略当前持仓" h
        JOIN current_dates d
          ON d."统一策略ID" = h."统一策略ID" AND d.latest_date = h."持仓日期"
        WHERE h."基金代码" GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
          AND COALESCE(h."基金权重_百分比",0) > 0
        GROUP BY h."基金代码"
        """
    ).fetchall()


def main() -> None:
    args = parse_args()
    this_run = run_id()
    args.output_root.mkdir(parents=True, exist_ok=True)
    with connect_db(args.db_path) as conn:
        holding_rows = current_holding_fund_rows(conn)
        snapshot_by_code = {
            row["基金代码"]: row
            for row in conn.execute(
                """
                WITH latest AS (
                  SELECT "基金代码", MAX("报告期") AS latest_report
                  FROM "基金分类快照"
                  GROUP BY "基金代码"
                )
                SELECT s.*
                FROM "基金分类快照" s
                JOIN latest l ON l."基金代码"=s."基金代码" AND l.latest_report=s."报告期"
                """
            ).fetchall()
        }
        total_funds = len(holding_rows)
        total_weight = sum(float(row["全市场持仓权重"] or 0) for row in holding_rows)
        covered_rows = []
        missing_rows = []
        stale_rows = []
        for row in holding_rows:
            code = row["基金代码"]
            snap = snapshot_by_code.get(code)
            out = {
                "基金代码": code,
                "基金名称": row["基金名称"],
                "全市场持仓权重": round(float(row["全市场持仓权重"] or 0), 4),
                "持仓策略数": row["持仓策略数"],
                "报告期": snap["报告期"] if snap else "",
                "覆盖状态": snap["覆盖状态"] if snap else "missing",
            }
            if snap:
                covered_rows.append(out)
                if str(snap["报告期"] or "") < "2025-01-01":
                    stale_rows.append(out)
            else:
                missing_rows.append(out)
        covered_weight = sum(row["全市场持仓权重"] for row in covered_rows)
        metrics = {
            "运行ID": this_run,
            "生成时间": now_cn(),
            "当前持仓基金数": total_funds,
            "已穿透基金数": len(covered_rows),
            "缺失基金数": len(missing_rows),
            "过旧基金数": len(stale_rows),
            "基金数量覆盖率": round(len(covered_rows) / total_funds * 100, 4) if total_funds else 0,
            "仓位加权覆盖率": round(covered_weight / total_weight * 100, 4) if total_weight else 0,
            "总持仓权重": round(total_weight, 4),
            "已覆盖持仓权重": round(covered_weight, 4),
        }
        if args.dry_run:
            print(json.dumps({"状态": "dry_run", **metrics}, ensure_ascii=False, indent=2))
            return
        for key, value in metrics.items():
            if key in {"运行ID", "生成时间"}:
                continue
            conn.execute(
                """
                INSERT OR REPLACE INTO "基金穿透数据质量"
                ("运行ID","指标名","指标值","指标文本","生成时间")
                VALUES (?,?,?,?,?)
                """,
                (
                    this_run,
                    key,
                    float(value) if isinstance(value, (int, float)) else None,
                    str(value),
                    metrics["生成时间"],
                ),
            )
        conn.commit()

    coverage_path = args.output_root / "覆盖率报告.json"
    missing_path = args.output_root / "缺失基金清单.csv"
    stale_path = args.output_root / "过旧基金清单.csv"
    coverage_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    fields = ["基金代码", "基金名称", "全市场持仓权重", "持仓策略数", "报告期", "覆盖状态"]
    write_csv(missing_path, missing_rows, fields)
    write_csv(stale_path, stale_rows, fields)
    print(json.dumps({"状态": "completed", **metrics, "缺失基金清单": str(missing_path)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
