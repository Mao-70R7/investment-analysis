from __future__ import annotations

import argparse
import json
from pathlib import Path

from fund_lookthrough_common import (
    DEFAULT_DB_PATH,
    DEFAULT_OUTPUT_ROOT,
    connect_db,
    repair_stock_industry_fields,
    write_run_summary,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="回填基金季度股票持仓的行业映射，避免空行业覆盖历史有效映射。")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--fetch-missing", action="store_true", help="对仍缺行业的A股代码调用东财stock/get接口补充。")
    parser.add_argument("--timeout-sec", type=int, default=20)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--limit", type=int, help="限制接口补充的股票数量，排障时使用。")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with connect_db(args.db_path) as conn:
        before = {
            "股票持仓行数": conn.execute('SELECT COUNT(*) FROM "基金季度股票持仓"').fetchone()[0],
            "空行业持仓行数": conn.execute(
                'SELECT COUNT(*) FROM "基金季度股票持仓" WHERE COALESCE("行业一级",\'\') IN (\'\',\'未识别\')'
            ).fetchone()[0],
            "有效映射股票数": conn.execute(
                'SELECT COUNT(*) FROM "股票行业映射" WHERE COALESCE("行业一级",\'\') NOT IN (\'\',\'未识别\')'
            ).fetchone()[0],
        }
        if args.dry_run:
            print(json.dumps({"状态": "dry_run", "修复前": before}, ensure_ascii=False, indent=2))
            return
        result = repair_stock_industry_fields(
            conn,
            fetch_missing=args.fetch_missing,
            timeout_sec=max(5, args.timeout_sec),
            workers=max(1, args.workers),
            limit=args.limit,
        )
        after = {
            "股票持仓行数": conn.execute('SELECT COUNT(*) FROM "基金季度股票持仓"').fetchone()[0],
            "空行业持仓行数": conn.execute(
                'SELECT COUNT(*) FROM "基金季度股票持仓" WHERE COALESCE("行业一级",\'\') IN (\'\',\'未识别\')'
            ).fetchone()[0],
            "有效映射股票数": conn.execute(
                'SELECT COUNT(*) FROM "股票行业映射" WHERE COALESCE("行业一级",\'\') NOT IN (\'\',\'未识别\')'
            ).fetchone()[0],
        }
    payload = {"状态": "completed", "修复前": before, "修复后": after, "结果": result}
    summary_path = write_run_summary(args.output_root, "回填股票行业映射", [payload])
    payload["summary"] = str(summary_path)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
