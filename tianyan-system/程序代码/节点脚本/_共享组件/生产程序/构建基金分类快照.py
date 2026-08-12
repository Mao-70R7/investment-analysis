from __future__ import annotations

import argparse
import json
from pathlib import Path

from fund_lookthrough_common import (
    DEFAULT_DB_PATH,
    DEFAULT_OUTPUT_ROOT,
    connect_db,
    build_classification_snapshots,
    write_run_summary,
    write_snapshot_exports,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="基于基金季度资产配置和行业配置生成基金分类快照。")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with connect_db(args.db_path) as conn:
        asset_count = conn.execute('SELECT COUNT(*) FROM "基金季度资产配置"').fetchone()[0]
        industry_count = conn.execute('SELECT COUNT(*) FROM "基金季度行业配置"').fetchone()[0]
        if args.dry_run:
            print(json.dumps({"状态": "dry_run", "资产配置行数": asset_count, "行业配置行数": industry_count}, ensure_ascii=False, indent=2))
            return
        rows = build_classification_snapshots(conn)
    export_paths = write_snapshot_exports(rows, args.output_root)
    summary_path = write_run_summary(
        args.output_root,
        "构建基金分类快照",
        [{"状态": "parsed", "资产配置行数": asset_count, "行业配置行数": industry_count, "快照基金数": len(rows)}],
        {"导出文件": {key: str(value) for key, value in export_paths.items()}},
    )
    print(json.dumps({"状态": "completed", "快照基金数": len(rows), "summary": str(summary_path)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
