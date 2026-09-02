from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from report_periods import (
    monthly_rebalance_asset_directory,
    monthly_rebalance_report_page,
    monthly_rebalance_snapshot_name,
    previous_completed_month,
)


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_DEPLOY_DIR = PROJECT_ROOT / "site"
MONTHLY_REBALANCE_REPORT_MONTH = previous_completed_month()
MONTHLY_REBALANCE_REPORT_PAGE = monthly_rebalance_report_page(MONTHLY_REBALANCE_REPORT_MONTH)
MONTHLY_REBALANCE_REPORT_ASSET_DIRECTORY = monthly_rebalance_asset_directory(MONTHLY_REBALANCE_REPORT_MONTH)
MONTHLY_REBALANCE_SNAPSHOT_NAME = monthly_rebalance_snapshot_name(MONTHLY_REBALANCE_REPORT_MONTH)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Write and validate the deploy manifest for the analysis platform.")
    parser.add_argument("--deploy-dir", type=Path, default=DEFAULT_DEPLOY_DIR)
    parser.add_argument(
        "--page-set",
        choices=("minimal_publish",),
        default="minimal_publish",
    )
    return parser.parse_args()


def dir_stats(path: Path) -> dict[str, Any]:
    files = [item for item in path.rglob("*") if item.is_file()] if path.exists() else []
    return {
        "exists": path.exists(),
        "files": len(files),
        "bytes": sum(item.stat().st_size for item in files),
    }


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_text_if_changed(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            if path.read_text(encoding="utf-8") == text:
                return
        except UnicodeDecodeError:
            pass
    path.write_text(text, encoding="utf-8")


def main() -> None:
    args = parse_args()
    deploy_dir = args.deploy_dir.resolve()
    required = {
        "root_index": deploy_dir / "index.html",
        "basic_data_index": deploy_dir / "basic_data" / "index.html",
        "basic_data_strategies": deploy_dir / "basic_data" / "strategies.html",
        "basic_data_insights": deploy_dir / "basic_data" / "insights.html",
        "basic_data_ai_strategy": deploy_dir / "basic_data" / "ai-strategy.html",
        "basic_data_topic_analysis": deploy_dir / "basic_data" / "topic-analysis.html",
        "basic_data_gf_rebalance_monitor": deploy_dir / "basic_data" / "gf-rebalance-monitor.html",
        "basic_data_monthly_rebalance_report": deploy_dir / "basic_data" / MONTHLY_REBALANCE_REPORT_PAGE,
        "basic_data_monthly_rebalance_snapshot": deploy_dir / "basic_data" / "reports" / MONTHLY_REBALANCE_SNAPSHOT_NAME,
        "basic_data_qd_fund_report": deploy_dir / "basic_data" / "qd-fund-report.html",
        "basic_data_qd_fund_detail": deploy_dir / "basic_data" / "qd-fund-detail.html",
        "basic_data_qd_fund_detail_xlsx": deploy_dir / "basic_data" / "qd-fund-detail.xlsx",
        "basic_data_data_quality": deploy_dir / "basic_data" / "data-quality.html",
        "basic_data_strategy_detail": deploy_dir / "basic_data" / "strategy.html",
        "basic_data_fund_detail": deploy_dir / "basic_data" / "fund.html",
        "basic_data_summary": deploy_dir / "basic_data" / "data" / "basic_summary.js",
        "basic_data_ai_semantic_index": deploy_dir / "basic_data" / "data" / "ai_semantic_index.js",
        "basic_data_standard_entity_dictionary": deploy_dir / "basic_data" / "data" / "standard_entity_dictionary.js",
        "basic_data_data_quality_pack": deploy_dir / "basic_data" / "data" / "data_quality_pack.js",
        "basic_data_data_pack_manifest": deploy_dir / "basic_data" / "data" / "data_pack_manifest.js",
        "basic_data_topic_analysis_pack": deploy_dir / "basic_data" / "data" / "topic_analysis_pack.js",
        "basic_data_holding_snapshot_pack": deploy_dir / "basic_data" / "data" / "holding_snapshot_pack.json",
        "basic_data_holding_snapshot_pack_js": deploy_dir / "basic_data" / "data" / "holding_snapshot_pack.js",
        "basic_data_rebalance_fund_category_pack": deploy_dir / "basic_data" / "data" / "rebalance_fund_category_pack.json",
        "basic_data_rebalance_fund_category_pack_js": deploy_dir / "basic_data" / "data" / "rebalance_fund_category_pack.js",
        "basic_data_fund_detail_pack": deploy_dir / "basic_data" / "data" / "fund_detail_pack.js",
        "basic_data_fund_economic_exposure_pack": deploy_dir / "basic_data" / "data" / "fund_economic_exposure_pack.js",
        "basic_data_fund_details_manifest": deploy_dir / "basic_data" / "data" / "fund_details" / "_manifest.js",
        "basic_data_common_js": deploy_dir / "basic_data" / "assets" / "basic-common.js",
        "basic_data_css": deploy_dir / "basic_data" / "assets" / "basic.css",
        "basic_data_overview_js": deploy_dir / "basic_data" / "assets" / "overview.js",
        "basic_data_strategies_js": deploy_dir / "basic_data" / "assets" / "strategies.js",
        "basic_data_insights_js": deploy_dir / "basic_data" / "assets" / "insights.js",
        "basic_data_ai_strategy_js": deploy_dir / "basic_data" / "assets" / "ai-strategy.js",
        "basic_data_data_quality_js": deploy_dir / "basic_data" / "assets" / "data-quality.js",
        "basic_data_topic_analysis_js": deploy_dir / "basic_data" / "assets" / "topic-analysis.js",
        "basic_data_mixed_performance_scatter_js": deploy_dir / "basic_data" / "assets" / "mixed-performance-scatter.js",
        "basic_data_ai_strategy_config_js": deploy_dir / "basic_data" / "assets" / "ai-strategy-config.js",
        "basic_data_model_service_config_js": deploy_dir / "basic_data" / "config" / "模型服务配置.js",
        "basic_data_ai_strategy_local_config_js": deploy_dir / "basic_data" / "config" / "ai-strategy-local-config.js",
        "basic_data_ai_strategy_local_config_doc": deploy_dir / "basic_data" / "config" / "模型配置说明.md",
        "basic_data_strategy_detail_js": deploy_dir / "basic_data" / "assets" / "strategy-detail.js",
        "basic_data_fund_detail_js": deploy_dir / "basic_data" / "assets" / "fund-detail.js",
        "ai_strategy_codex_proxy": deploy_dir / "scripts" / "ai_strategy_codex_proxy.mjs",
        "ai_strategy_codex_proxy_start": deploy_dir / "scripts" / "start_ai_strategy_codex_proxy.ps1",
        "ai_strategy_codex_proxy_start_cmd": deploy_dir / "scripts" / "start_ai_strategy_codex_proxy.cmd",
        "ai_strategy_codex_proxy_startup_install": deploy_dir / "scripts" / "install_ai_strategy_codex_proxy_startup.ps1",
        "ai_strategy_codex_proxy_startup_install_cmd": deploy_dir / "scripts" / "install_ai_strategy_codex_proxy_startup.cmd",
        "ai_strategy_codex_proxy_startup_uninstall": deploy_dir / "scripts" / "uninstall_ai_strategy_codex_proxy_startup.ps1",
        "ai_strategy_codex_proxy_startup_uninstall_cmd": deploy_dir / "scripts" / "uninstall_ai_strategy_codex_proxy_startup.cmd",
    }
    if args.page_set == "minimal_publish":
        required = {
            "root_index": deploy_dir / "index.html",
            "basic_data_index": deploy_dir / "basic_data" / "index.html",
            "basic_data_strategies": deploy_dir / "basic_data" / "strategies.html",
            "basic_data_institutions": deploy_dir / "basic_data" / "institutions.html",
            "basic_data_strategy_detail": deploy_dir / "basic_data" / "strategy.html",
            "basic_data_compare": deploy_dir / "basic_data" / "compare.html",
            "basic_data_mixed_performance_scatter": deploy_dir / "basic_data" / "mixed-performance-scatter.html",
            "basic_data_fund_detail": deploy_dir / "basic_data" / "fund.html",
            "basic_data_ai_strategy": deploy_dir / "basic_data" / "ai-strategy.html",
            "basic_data_basic_summary_core": deploy_dir / "basic_data" / "data" / "basic_summary_core.js",
            "basic_data_fund_detail_pack": deploy_dir / "basic_data" / "data" / "fund_detail_pack.js",
            "basic_data_fund_economic_exposure_pack": deploy_dir / "basic_data" / "data" / "fund_economic_exposure_pack.js",
            "basic_data_ai_semantic_index": deploy_dir / "basic_data" / "data" / "ai_semantic_index.js",
            "basic_data_ai_topic_evidence_pack": deploy_dir / "basic_data" / "data" / "ai_topic_evidence_pack.js",
            "basic_data_mixed_performance_scatter_pack": deploy_dir / "basic_data" / "data" / "mixed_performance_scatter_pack.js",
            "basic_data_holding_snapshot_pack_js": deploy_dir / "basic_data" / "data" / "holding_snapshot_pack.js",
            "basic_data_data_quality_pack": deploy_dir / "basic_data" / "data" / "data_quality_pack.js",
            "basic_data_standard_entity_dictionary": deploy_dir / "basic_data" / "data" / "standard_entity_dictionary.js",
            "basic_data_strategy_details": deploy_dir / "basic_data" / "data" / "details",
            "basic_data_common_js": deploy_dir / "basic_data" / "assets" / "basic-common.js",
            "basic_data_css": deploy_dir / "basic_data" / "assets" / "basic.css",
            "basic_data_strategies_js": deploy_dir / "basic_data" / "assets" / "strategies.js",
            "basic_data_institutions_js": deploy_dir / "basic_data" / "assets" / "institutions.js",
            "basic_data_strategy_detail_js": deploy_dir / "basic_data" / "assets" / "strategy-detail.js",
            "basic_data_insights_js": deploy_dir / "basic_data" / "assets" / "insights.js",
            "basic_data_mixed_performance_scatter_js": deploy_dir / "basic_data" / "assets" / "mixed-performance-scatter.js",
            "basic_data_fund_detail_js": deploy_dir / "basic_data" / "assets" / "fund-detail.js",
            "basic_data_ai_strategy_config_js": deploy_dir / "basic_data" / "assets" / "ai-strategy-config.js",
            "basic_data_ai_strategy_js": deploy_dir / "basic_data" / "assets" / "ai-strategy.js",
            "basic_data_model_service_config_js": deploy_dir / "basic_data" / "config" / "模型服务配置.js",
            "basic_data_ai_strategy_local_config_js": deploy_dir / "basic_data" / "config" / "ai-strategy-local-config.js",
        }
    strategy_manifest: dict[str, Any] = {}
    if args.page_set == "all":
        strategy_manifest = load_json(deploy_dir / "strategy_center" / "manifest.json")
        required.update(
            {
                "strategy_center_index": deploy_dir / "strategy_center" / "index.html",
                "strategy_center_summary": deploy_dir / "strategy_center" / "data" / "summary.js",
                "strategy_center_quality": deploy_dir / "strategy_center" / "data" / "quality.js",
                "full_data_statistics_report": deploy_dir / "full_data_statistics_report" / "index.html",
            }
        )
    missing = [name for name, path in required.items() if not path.exists()]
    detail_files = strategy_manifest.get("detailFiles") or {}
    manifest = {
        "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "deployDir": str(deploy_dir),
        "pageSet": args.page_set,
        "defaultPage": "basic_data/index.html",
        "status": "ready" if not missing else "missing_files",
        "missing": missing,
        "strategyCenter": {
            **dir_stats(deploy_dir / "strategy_center"),
            "latestNavDate": strategy_manifest.get("latestNavDate"),
            "detailFileCount": len(detail_files) if isinstance(detail_files, dict) else 0,
        },
        "basicData": dir_stats(deploy_dir / "basic_data"),
        "fullDataStatisticsReport": dir_stats(deploy_dir / "full_data_statistics_report"),
    }
    text = json.dumps(manifest, ensure_ascii=False, indent=2)
    write_text_if_changed(deploy_dir / "deployment_manifest.json", text)
    print(text)
    if missing:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
