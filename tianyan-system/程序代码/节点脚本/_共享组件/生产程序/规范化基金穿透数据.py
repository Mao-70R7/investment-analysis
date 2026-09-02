from __future__ import annotations

import argparse
import json
from pathlib import Path

from fund_lookthrough_common import (
    DEFAULT_DB_PATH,
    DEFAULT_OUTPUT_ROOT,
    connect_db,
    rebuild_industry_allocation,
    repair_stock_industry_fields,
    write_run_summary,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="从基金季报股票持仓明细推导基金季度行业配置。")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with connect_db(args.db_path) as conn:
        pending = conn.execute('SELECT COUNT(*) FROM "基金季度股票持仓"').fetchone()[0]
        if args.dry_run:
            print(json.dumps({"状态": "dry_run", "股票持仓行数": pending}, ensure_ascii=False, indent=2))
            return
        repair_result = repair_stock_industry_fields(conn, fetch_missing=False)
        row_count = rebuild_industry_allocation(conn)
    result = [{"状态": "parsed", "生成行业配置行数": row_count, "股票持仓行数": pending, "行业映射回填": repair_result}]
    summary_path = write_run_summary(args.output_root, "规范化基金穿透数据", result)
    print(json.dumps({"状态": "completed", "生成行业配置行数": row_count, "summary": str(summary_path)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
