from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
sys.path.insert(0, str(PROJECT_ROOT / "节点脚本" / "_共享组件" / "python_src"))

from advisor_monitor.collectors.official_apps_public import collect_official_apps_public  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect public investment-advisory data from selected official apps/sites."
    )
    parser.add_argument(
        "--apps",
        default="huaxia_tougu,zocaifu,harvestwm,southern,cmfchina,efundcf,gffunds,gfsec_fima,gfsec_robot,fullgoal,fund99,qieman",
        help=(
            "Comma-separated app ids: huaxia_tougu,zocaifu,harvestwm,southern,cmfchina,"
            "efundcf,gffunds,gfsec_fima,gfsec_robot,fullgoal,fund99,qieman. "
            "gfbank_cgb is archival and must be selected explicitly."
        ),
    )
    parser.add_argument(
        "--harvest-pages",
        type=int,
        default=None,
        help="Limit Harvest notice pages for a test run. Default collects all pages reported by the site.",
    )
    parser.add_argument("--workers", type=int, default=8, help="Worker count passed to ZOCAIFU collector.")
    parser.add_argument("--zocaifu-limit", type=int, default=None, help="Limit ZOCAIFU strategies for a test run.")
    parser.add_argument(
        "--zocaifu-skip-fund-nav",
        action="store_true",
        help="Skip ZOCAIFU underlying fund latest NAV calls. Holdings and rebalance data are still collected.",
    )
    parser.add_argument("--gffunds-limit", type=int, default=None, help="Limit GFFunds strategies for a test run.")
    parser.add_argument(
        "--gffunds-skip-fund-nav",
        action="store_true",
        help="Skip GFFunds underlying fund latest NAV calls. Holdings and rebalance data are still collected.",
    )
    parser.add_argument(
        "--gffunds-skip-protocol-pdf",
        action="store_true",
        help="Skip GFFunds strategy protocol PDF download/parsing for faster repeat runs.",
    )
    parser.add_argument("--run-id", help="Explicit run id for exact batch provenance.")
    parser.add_argument("--result-summary-path", type=Path, help="Write selected app summaries to this JSON file.")
    parser.add_argument(
        "--gffunds-latest-adjustment-refresh-days",
        type=int,
        default=1,
        help="Reuse validated historical adjustment details; refresh the latest detail daily by default (0 means every run).",
    )
    parser.add_argument(
        "--gfsec-fima-daily-page-size",
        type=int,
        default=400,
        help="Latest official cumulative-yield points requested per FIMA underlying portfolio.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    apps = [item.strip() for item in args.apps.split(",") if item.strip()]
    results = collect_official_apps_public(
        PROJECT_ROOT,
        apps=apps,
        harvest_pages=args.harvest_pages,
        workers=args.workers,
        zocaifu_limit=args.zocaifu_limit,
        zocaifu_skip_fund_nav=args.zocaifu_skip_fund_nav,
        gffunds_limit=args.gffunds_limit,
        gffunds_skip_fund_nav=args.gffunds_skip_fund_nav,
        gffunds_skip_protocol_pdf=args.gffunds_skip_protocol_pdf,
        gffunds_latest_adjustment_refresh_days=args.gffunds_latest_adjustment_refresh_days,
        gfsec_fima_daily_page_size=args.gfsec_fima_daily_page_size,
        run_id=args.run_id,
    )
    if args.result_summary_path:
        args.result_summary_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.result_summary_path.with_suffix(args.result_summary_path.suffix + ".tmp")
        temporary.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(args.result_summary_path)
    print("collection complete")
    for app, summary in results.items():
        print(
            f"{app}: status={summary.get('collection_status')} "
            f"strategies={summary.get('strategy_total', 0)} "
            f"holdings={summary.get('current_holding_rows', 0)} "
            f"rebalance_events={summary.get('rebalance_event_total', 0)}"
        )
    print(
        json.dumps(
            {
                app: {
                    "summary": summary.get("output_paths", {}).get("summary"),
                    "coverage": summary.get("output_paths", {}).get("coverage"),
                }
                for app, summary in results.items()
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
