from __future__ import annotations

import argparse
import json

from fund_lookthrough_common import (
    add_common_args,
    build_targets_from_args,
    collect_with_workers,
    fetch_asset_allocation,
    write_run_summary,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="增量采集当前投顾持仓基金的东财F10季报资产配置。")
    add_common_args(parser)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    targets = build_targets_from_args(args)
    if args.dry_run:
        print(json.dumps({"状态": "dry_run", "目标基金数": len(targets), "基金代码": [t.fund_code for t in targets[:50]]}, ensure_ascii=False, indent=2))
        return
    results = collect_with_workers(targets, args, fetch_asset_allocation, "基金季报资产配置")
    summary_path = write_run_summary(args.output_root, "采集基金季报资产配置", results)
    print(json.dumps({"状态": "completed", "目标基金数": len(targets), "summary": str(summary_path)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
