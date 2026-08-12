from __future__ import annotations

import argparse
import json
from functools import partial

from fund_lookthrough_common import (
    add_common_args,
    build_targets_from_args,
    cleanup_impossible_bond_holding_weights,
    collect_with_workers,
    fetch_archive_holdings,
    write_run_summary,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="增量采集当前投顾持仓基金的东财F10股票/债券持仓明细。")
    add_common_args(parser)
    parser.add_argument("--top-line", type=int, default=50, help="股票持仓接口请求的最大行数；季报通常只披露前十大。")
    parser.add_argument("--skip-stock", action="store_true")
    parser.add_argument("--skip-bond", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    targets = build_targets_from_args(args)
    if args.dry_run:
        print(json.dumps({"状态": "dry_run", "目标基金数": len(targets), "基金代码": [t.fund_code for t in targets[:50]]}, ensure_ascii=False, indent=2))
        return

    all_results = []
    quality_checks = {}
    if not args.skip_stock:
        stock_results = collect_with_workers(
            targets,
            args,
            partial(fetch_archive_holdings, data_type="stock_holding"),
            "基金季报股票持仓",
        )
        all_results.extend(stock_results)
        write_run_summary(args.output_root, "采集基金季报股票持仓", stock_results)
    if not args.skip_bond:
        bond_results = collect_with_workers(
            targets,
            args,
            partial(fetch_archive_holdings, data_type="bond_holding"),
            "基金季报债券持仓",
        )
        all_results.extend(bond_results)
        write_run_summary(args.output_root, "采集基金季报债券持仓", bond_results)
        quality_checks["债券持仓异常占比清理"] = cleanup_impossible_bond_holding_weights(args.db_path)

    summary_path = write_run_summary(
        args.output_root,
        "采集基金季报持仓明细",
        all_results,
        {"质量检查": quality_checks} if quality_checks else None,
    )
    print(json.dumps({"状态": "completed", "目标基金数": len(targets), "summary": str(summary_path)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
