from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
sys.path.insert(0, str(PROJECT_ROOT / "节点脚本" / "_共享组件" / "python_src"))

from advisor_monitor.collectors.ttfund_fund_nav import (  # noqa: E402
    CHANNEL_ID,
    CHANNEL_NAME,
    collect_ttfund_fund_nav,
    discover_existing_fund_codes,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect historical fund NAV series from TTFund/Eastmoney F10DataApi."
    )
    parser.add_argument(
        "--fund-code",
        action="append",
        default=[],
        help="Fund code to collect. Can be passed multiple times.",
    )
    parser.add_argument(
        "--use-existing-funds",
        action="store_true",
        help="Collect from all distinct fund codes already discovered in the current project.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional limit after fund-code discovery.",
    )
    parser.add_argument(
        "--start-date",
        type=str,
        default=None,
        help="Optional start date in YYYY-MM-DD.",
    )
    parser.add_argument(
        "--end-date",
        type=str,
        default=None,
        help="Optional end date in YYYY-MM-DD.",
    )
    parser.add_argument(
        "--per-page",
        type=int,
        default=2000,
        help="Records per request page. Eastmoney currently supports up to 2000.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    fund_codes = list(args.fund_code)
    if args.use_existing_funds:
        fund_codes.extend(discover_existing_fund_codes(PROJECT_ROOT))
    deduped: list[str] = []
    seen: set[str] = set()
    for code in fund_codes:
        text = str(code or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        deduped.append(text)
    if args.limit and args.limit > 0:
        deduped = deduped[: args.limit]
    if not deduped:
        raise SystemExit("No fund codes specified. Use --fund-code or --use-existing-funds.")

    summary = collect_ttfund_fund_nav(
        PROJECT_ROOT,
        fund_codes=deduped,
        start_date=args.start_date,
        end_date=args.end_date,
        per_page=args.per_page,
    )

    print("collection complete")
    print(f"channel={CHANNEL_ID}")
    print(f"channel_name={CHANNEL_NAME}")
    print(f"run_id={summary['run_id']}")
    print(f"raw_dir={summary['raw_dir']}")
    print(f"normalized_dir={summary['normalized_dir']}")
    print(
        "coverage="
        f"targets:{summary['target_fund_total']} "
        f"success:{summary['successful_fund_total']} "
        f"failed:{summary['failed_fund_total']} "
        f"history_rows:{summary['history_row_total']}"
    )
    if summary["failed_funds"]:
        print(f"failed_funds={summary['failed_funds']}")


if __name__ == "__main__":
    main()
