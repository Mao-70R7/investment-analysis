from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
SCRIPT_DIR = PROJECT_ROOT / "节点脚本" / "_共享组件" / "生产程序"
DEFAULT_ALGORITHM_VERSION = "standard_rebalance_asset_dual_nav_v10_all_channels_20260528"
STEP_ALIASES = {
    "04_backfill_cifm_mutual_nav": "05_backfill_cifm_mutual_nav",
    "05_backfill_sina_xh5_nav": "06_backfill_sina_xh5_nav",
    "06_backfill_overseas_nav": "07_backfill_overseas_nav",
    "07_backfill_dividends_from_nav_hints": "08_backfill_dividends_from_nav_hints",
    "08_reconstruct_strategy_nav": "10_reconstruct_strategy_nav",
    "08b_build_rebalance_quality_analysis": "10b_build_rebalance_quality_analysis",
    "09_analyze_official_deviation": "11_analyze_official_deviation",
    "10_govern_performance_data": "12_govern_performance_data",
    "11_audit_current_holding_projection": "13_audit_current_holding_projection",
    "12_evaluate_guangfa_trade_delay": "14_evaluate_guangfa_trade_delay",
    "13_diagnose_top_deviation_fund_gaps": "15_diagnose_top_deviation_fund_gaps",
    "14_summarize_optimized_quality": "16_summarize_optimized_quality",
    "15_summarize_channel_quality": "17_summarize_channel_quality",
    "16_final_integrity_audit": "18_final_integrity_audit",
    "17_audit_official_performance_coverage": "19_audit_official_performance_coverage",
    "18_generate_full_data_statistics_report": "20_generate_full_data_statistics_report",
    "19_export_basic_data_pages": "21_export_basic_data_pages",
    "20_build_basic_data_report_packs": "22_build_basic_data_report_packs",
    "20_export_strategy_dashboard_data": "22_export_strategy_dashboard_data",
    "repair_positive_stock_holding_gaps_before_outputs": "19b_repair_positive_stock_holding_gaps_before_outputs",
}
ANALYSIS_DB_PATH = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
FUND_NAV_TABLE = "\u57fa\u91d1\u65e5\u5ea6\u51c0\u503c"
TRADE_DATE_COLUMN = "\u4ea4\u6613\u65e5\u671f"
FUND_NAV_NORMALIZED_ROOT = PROJECT_ROOT / "data" / "normalized" / "ttfund_fund_nav"
FUND_HISTORY_LOAD_SUMMARY = PROJECT_ROOT / "outputs" / "fund_history_from_normalized" / "summary.json"
TTFUND_INCREMENTAL_RUN_ROOT = PROJECT_ROOT / "data" / "raw" / "ttfund" / "incremental_update_runs"
AUTO_FAST_INCREMENTAL_MAX_AGE_SECONDS = 8 * 60 * 60


def normalize_trade_date(value: str | None) -> str | None:
    if not value:
        return None
    match = re.search(r"([12]\d{3})[-/.]?([01]\d)[-/.]?([0-3]\d)", str(value))
    if not match:
        return str(value).strip() or None
    return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"


def latest_db_date(table: str, column: str) -> str | None:
    if not ANALYSIS_DB_PATH.exists():
        return None
    try:
        with sqlite3.connect(ANALYSIS_DB_PATH) as conn:
            return conn.execute(f'SELECT MAX("{column}") FROM "{table}"').fetchone()[0]
    except sqlite3.Error:
        return None


def table_row_count(table: str) -> int | None:
    if not ANALYSIS_DB_PATH.exists():
        return None
    try:
        with sqlite3.connect(ANALYSIS_DB_PATH) as conn:
            return int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] or 0)
    except sqlite3.Error:
        return None


def latest_jsonl_mtime(root: Path) -> float | None:
    if not root.exists():
        return None
    latest: float | None = None
    for path in root.rglob("*.jsonl"):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        latest = mtime if latest is None else max(latest, mtime)
    return latest


def fund_history_load_is_current() -> tuple[bool, str]:
    if not FUND_HISTORY_LOAD_SUMMARY.exists():
        return False, "fund_history_summary_missing"
    try:
        summary = json.loads(FUND_HISTORY_LOAD_SUMMARY.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False, "fund_history_summary_unreadable"
    latest_input_mtime = latest_jsonl_mtime(FUND_NAV_NORMALIZED_ROOT)
    if latest_input_mtime is None:
        return False, "fund_history_normalized_jsonl_missing"
    try:
        summary_mtime = FUND_HISTORY_LOAD_SUMMARY.stat().st_mtime
    except OSError:
        return False, "fund_history_summary_unreadable"
    if summary_mtime < latest_input_mtime:
        input_time = datetime.fromtimestamp(latest_input_mtime).isoformat(timespec="seconds")
        summary_time = datetime.fromtimestamp(summary_mtime).isoformat(timespec="seconds")
        return False, f"fund_history_input_newer_than_summary input={input_time} summary={summary_time}"
    row_count = table_row_count(FUND_NAV_TABLE)
    if not row_count:
        return False, "fund_nav_table_empty"
    expected_count = ((summary.get("table_counts") or {}).get(FUND_NAV_TABLE) or 0)
    if expected_count and row_count < int(expected_count):
        return False, f"fund_nav_table_rows_below_summary rows={row_count} summary_rows={expected_count}"
    summary_latest_date = normalize_trade_date(summary.get("latest_fund_nav_date"))
    db_latest_date = normalize_trade_date(latest_db_date(FUND_NAV_TABLE, TRADE_DATE_COLUMN))
    if summary_latest_date and (not db_latest_date or db_latest_date < summary_latest_date):
        return False, f"fund_nav_latest_date_below_summary db={db_latest_date} summary={summary_latest_date}"
    return True, f"fund_history_summary_current rows={row_count} latest={db_latest_date}"


def read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None


def find_recent_completed_ttfund_summary(max_age_seconds: int = AUTO_FAST_INCREMENTAL_MAX_AGE_SECONDS) -> tuple[Path, dict] | None:
    if not TTFUND_INCREMENTAL_RUN_ROOT.exists():
        return None
    now_ts = datetime.now().timestamp()
    summaries = sorted(
        TTFUND_INCREMENTAL_RUN_ROOT.glob("*/*/summary.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for path in summaries:
        try:
            age_seconds = now_ts - path.stat().st_mtime
        except OSError:
            continue
        if age_seconds > max_age_seconds:
            return None
        payload = read_json(path)
        if not payload:
            continue
        if payload.get("state") == "completed" and payload.get("collect_run_id"):
            return path, payload
    return None


@dataclass
class StepResult:
    step: str
    command: list[str]
    returncode: int
    elapsed_seconds: float
    log_path: str


def atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp_path.replace(path)


def terminate_process_tree(proc: subprocess.Popen[object]) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        proc.kill()
    proc.wait(timeout=30)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="天天基金投顾增量后处理：入库、依赖补齐、全量回放、质检和稽核。")
    parser.add_argument("--algorithm-version", default=DEFAULT_ALGORITHM_VERSION)
    parser.add_argument("--output-root", type=Path, default=PROJECT_ROOT / "outputs" / "ttfund_post_update_quality")
    parser.add_argument("--skip-load-analysis", action="store_true")
    parser.add_argument("--skip-dashboard-export", action="store_true")
    parser.add_argument("--skip-guangfa-trade-delay", action="store_true")
    parser.add_argument("--skip-fund-nav-load", action="store_true")
    parser.add_argument("--skip-fund-nav-refresh", action="store_true")
    parser.add_argument("--skip-fund-lookthrough-update", action="store_true")
    parser.add_argument(
        "--skip-performance-governance-backup",
        action="store_true",
        help="跳过业绩治理步骤自身的数据库快照；仅用于后续已有正式一致性备份节点的 DAG。",
    )
    parser.add_argument("--fund-nav-workers", type=int, default=12)
    parser.add_argument("--fund-nav-incremental-days", type=int, default=3)
    parser.add_argument("--fund-lookthrough-workers", type=int, default=8)
    parser.add_argument("--fund-lookthrough-stale-days", type=int, default=30)
    parser.add_argument("--fund-lookthrough-limit", type=int)
    parser.add_argument("--full-fund-nav-refresh", action="store_true")
    parser.add_argument("--skip-index-quote-update", action="store_true")
    parser.add_argument("--index-quote-lookback-days", type=int, default=10)
    parser.add_argument("--fast-incremental", action="store_true")
    parser.add_argument("--lightweight", action="store_true")
    parser.add_argument("--disable-auto-fast-incremental", action="store_true")
    parser.add_argument("--incremental-run-id")
    parser.add_argument("--target-trade-date")
    parser.add_argument("--deploy-site-dir", type=Path)
    parser.add_argument("--deploy-page-set", choices=("basic_data", "all"), default="basic_data")
    parser.add_argument("--skip-deploy-export", action="store_true")
    parser.add_argument("--skip-basic-data-pack-build", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--start-at-step", help="从指定步骤名开始执行，用于后处理失败后的快速续跑。")
    parser.add_argument("--stop-after-step", help="执行完指定步骤后停止，用于节点化调度拆分后处理范围。")
    parser.add_argument("--timeout", type=int, default=3600, help="单步骤超时时间，单位秒。")
    return parser.parse_args()


def run_step(step: str, args: list[str], log_dir: Path, timeout: int) -> StepResult:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{step}.log"
    command = [sys.executable, "-X", "utf8", *args]
    started = datetime.now()
    started_monotonic = time.monotonic()
    with log_path.open("w", encoding="utf-8", errors="replace") as log_handle:
        proc = subprocess.Popen(
            command,
            cwd=PROJECT_ROOT,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        )
        returncode: int | None = None
        while returncode is None:
            elapsed = time.monotonic() - started_monotonic
            remaining = max(0.0, float(timeout) - elapsed)
            if remaining <= 0:
                terminate_process_tree(proc)
                log_handle.write(f"\n[TIMEOUT] Step exceeded timeout={timeout}s and was stopped.\n")
                log_handle.flush()
                return StepResult(step, command, 124, round(elapsed, 3), str(log_path))
            try:
                returncode = proc.wait(timeout=min(30.0, remaining))
            except subprocess.TimeoutExpired:
                print(
                    f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                    f"HEARTBEAT {step}: elapsed={round(elapsed + min(30.0, remaining), 1)}s, log={log_path}",
                    flush=True,
                )
    elapsed = (datetime.now() - started).total_seconds()
    return StepResult(step, command, int(returncode), round(elapsed, 3), str(log_path))


def main() -> None:
    args = parse_args()
    day = datetime.now().astimezone().strftime("%Y-%m-%d")
    run_id = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z")
    output_dir = args.output_root / day / run_id
    log_dir = output_dir / "logs"

    steps: list[tuple[str, list[str]]] = [
        ("00_sync_raw_snapshot_manifests", [str(SCRIPT_DIR / "sync_raw_snapshot_manifests.py")]),
        ("01_ensure_indexes", [str(SCRIPT_DIR / "ensure_analysis_performance_indexes.py")]),
    ]
    skipped_steps: list[dict[str, str | None]] = []
    target_trade_date = normalize_trade_date(args.target_trade_date)
    auto_fast_incremental: dict[str, str | bool | None] = {"enabled": False}
    auto_fast_allowed = (
        not args.disable_auto_fast_incremental
        and os.environ.get("ADVISOR_AUTO_FAST_INCREMENTAL_POST", "1") != "0"
        and not args.fast_incremental
        and not args.incremental_run_id
        and not args.skip_load_analysis
        and not args.start_at_step
    )
    if auto_fast_allowed:
        recent_summary = find_recent_completed_ttfund_summary()
        if recent_summary:
            summary_path, summary_payload = recent_summary
            summary_target_trade_date = normalize_trade_date(
                summary_payload.get("target_trade_date")
                or summary_payload.get("remote_max_trade_date")
                or summary_payload.get("latest_local_trade_date")
            )
            if (not target_trade_date or target_trade_date == summary_target_trade_date) and summary_payload.get(
                "collect_run_id"
            ):
                args.fast_incremental = True
                args.incremental_run_id = str(summary_payload.get("collect_run_id"))
                target_trade_date = target_trade_date or summary_target_trade_date
                auto_fast_incremental = {
                    "enabled": True,
                    "summary_path": str(summary_path),
                    "incremental_run_id": args.incremental_run_id,
                    "target_trade_date": target_trade_date,
                }
    deploy_site_dir = args.deploy_site_dir.resolve() if args.deploy_site_dir else None
    strategy_site_dir = (
        deploy_site_dir / "strategy_center" if deploy_site_dir else PROJECT_ROOT / "site" / "strategy_center"
    )
    basic_data_site_dir = deploy_site_dir / "basic_data" if deploy_site_dir else PROJECT_ROOT / "site" / "basic_data"
    full_statistics_site_dir = (
        deploy_site_dir / "full_data_statistics_report"
        if deploy_site_dir
        else PROJECT_ROOT / "site" / "full_data_statistics_report"
    )
    latest_fund_nav_date = latest_db_date(FUND_NAV_TABLE, TRADE_DATE_COLUMN)
    fund_history_current, fund_history_current_reason = fund_history_load_is_current()
    fund_nav_is_current = bool(
        target_trade_date
        and latest_fund_nav_date
        and normalize_trade_date(latest_fund_nav_date) >= target_trade_date
    )
    if args.fast_incremental and not args.skip_load_analysis and not args.incremental_run_id:
        raise SystemExit("--fast-incremental requires --incremental-run-id unless --skip-load-analysis is set")

    def skip_step(step: str, reason: str) -> None:
        skipped_steps.append({"step": step, "reason": reason})

    def deploy_export_steps(prefix: str) -> list[tuple[str, list[str]]]:
        if not deploy_site_dir or args.skip_deploy_export:
            if args.skip_deploy_export:
                skip_step(f"{prefix}_deploy_export", "skip_deploy_export")
            return []
        output_steps: list[tuple[str, list[str]]] = [
            (
                f"{prefix}_prepare_deploy_site",
                [
                    str(SCRIPT_DIR / "prepare_analysis_platform_deploy.py"),
                    "--deploy-dir",
                    str(deploy_site_dir),
                    "--page-set",
                    args.deploy_page_set,
                ],
            ),
            (
                f"{prefix}_export_basic_data_pages",
                [
                    str(SCRIPT_DIR / "export_basic_data_pages.py"),
                    "--algorithm-version",
                    args.algorithm_version,
                    "--site-dir",
                    str(basic_data_site_dir),
                ],
            ),
        ]
        if not args.skip_basic_data_pack_build:
            output_steps.append(
                (
                    f"{prefix}_build_basic_data_report_packs",
                    [
                        str(SCRIPT_DIR / "build_basic_data_report_packs.py"),
                        "--report-root",
                        str(deploy_site_dir),
                    ],
                )
            )
            output_steps.append(
                (
                    f"{prefix}_audit_basic_data_deploy_integrity",
                    [
                        str(SCRIPT_DIR / "audit_basic_data_deploy_integrity.py"),
                        "--report-root",
                        str(deploy_site_dir),
                    ],
                )
            )
        else:
            skip_step(f"{prefix}_build_basic_data_report_packs", "skip_basic_data_pack_build")
            skip_step(f"{prefix}_audit_basic_data_deploy_integrity", "skip_basic_data_pack_build")
        if args.deploy_page_set == "all":
            output_steps.extend(
                [
                    (
                f"{prefix}_export_strategy_dashboard_data",
                [
                    str(SCRIPT_DIR / "export_strategy_dashboard_data.py"),
                    "--algorithm-version",
                    args.algorithm_version,
                    "--site-dir",
                    str(strategy_site_dir),
                ],
                    ),
                    (
                f"{prefix}_generate_full_data_statistics_report",
                [
                    str(SCRIPT_DIR / "generate_full_data_statistics_report.py"),
                    "--algorithm-version",
                    args.algorithm_version,
                    "--output-dir",
                    str(output_dir / "full_data_statistics_report"),
                    "--site-dir",
                    str(full_statistics_site_dir),
                ],
                    ),
                ]
            )
        else:
            skip_step(f"{prefix}_export_strategy_dashboard_data", "deploy_page_set=basic_data")
            skip_step(f"{prefix}_generate_full_data_statistics_report", "deploy_page_set=basic_data")
        output_steps.append(
            (
                f"{prefix}_write_deploy_manifest",
                [
                    str(SCRIPT_DIR / "write_analysis_platform_deploy_manifest.py"),
                    "--deploy-dir",
                    str(deploy_site_dir),
                    "--page-set",
                    args.deploy_page_set,
                ],
            ),
        )
        return output_steps

    if not args.skip_load_analysis:
        if args.fast_incremental:
            load_args = [str(SCRIPT_DIR / "load_ttfund_incremental_analysis.py")]
            if args.incremental_run_id:
                load_args.extend(["--run-id", args.incremental_run_id])
            if target_trade_date:
                load_args.extend(["--target-trade-date", target_trade_date])
            steps.append(("02_load_ttfund_incremental_analysis", load_args))
        else:
            steps.append(("02_load_analysis_db", [str(SCRIPT_DIR / "load_analysis_zh_current_sqlite.py"), "--keep-existing-db"]))
        if args.skip_fund_nav_load:
            skip_step("03_load_fund_history", "skip_fund_nav_load")
        elif args.fast_incremental:
            skip_step("03_load_fund_history", "fast_incremental")
        elif fund_nav_is_current:
            skip_step("03_load_fund_history", f"fund_nav_latest_date={latest_fund_nav_date}")
        elif fund_history_current:
            skip_step("03_load_fund_history", fund_history_current_reason)
        else:
            steps.append(("03_load_fund_history", [str(SCRIPT_DIR / "load_fund_history_from_normalized.py"), "--append"]))
    else:
        skip_step("02_load_analysis_db", "skip_load_analysis")
        skip_step("03_load_fund_history", "skip_load_analysis")

    steps.append(("02b_backfill_ttfund_benchmark_from_detail_cache", [str(SCRIPT_DIR / "backfill_ttfund_benchmark_from_detail_cache.py")]))
    steps.append(("02c_build_strategy_benchmark_fee_status", [str(SCRIPT_DIR / "build_strategy_benchmark_fee_status.py")]))
    # Governance is a post-load step, but it must remain independently runnable.
    # The daily DAG deliberately invokes it with --skip-load-analysis after the
    # process-load node has committed the database changes.
    steps.append(("02d_govern_strategy_lifecycle_and_rebalance", [str(SCRIPT_DIR / "治理策略生命周期和调仓去重.py")]))
    steps.append(
        (
            "02e_detect_strategy_parent_child_relationships",
            [
                str(SCRIPT_DIR / "识别策略母子关系.py"),
                "--result-path",
                str(output_dir / "strategy_parent_child_relationships.json"),
            ],
        )
    )

    if args.skip_fund_nav_refresh:
        skip_step("04_refresh_fund_nav_public", "skip_fund_nav_refresh")
    else:
        if args.full_fund_nav_refresh:
            fund_nav_command = [
                str(SCRIPT_DIR / "backfill_fund_history_analysis_sqlite.py"),
                "--refresh",
                "--workers",
                str(max(1, args.fund_nav_workers)),
                "--no-output-files",
                "--skip-raw-index-sync",
            ]
        else:
            fund_nav_command = [
                str(SCRIPT_DIR / "run_resilient_fund_nav_refresh.py"),
                "--target-source",
                "all-dict",
                "--workers",
                str(max(1, args.fund_nav_workers)),
                "--lookback-days",
                str(max(1, args.fund_nav_incremental_days)),
            ]
            if target_trade_date:
                fund_nav_command.extend(
                    [
                        "--only-nav-before",
                        target_trade_date,
                        "--end-date",
                        target_trade_date,
                    ]
                )
        steps.append(
            (
                "04_refresh_fund_nav_public",
                fund_nav_command,
            )
        )
    if args.skip_fund_lookthrough_update:
        skip_step("04b_fund_lookthrough_update", "skip_fund_lookthrough_update")
    else:
        lookthrough_common_args = [
            "--workers",
            str(max(1, args.fund_lookthrough_workers)),
            "--stale-days",
            str(max(0, args.fund_lookthrough_stale_days)),
        ]
        if args.fund_lookthrough_limit:
            lookthrough_common_args.extend(["--limit", str(max(1, args.fund_lookthrough_limit))])
        steps.extend(
            [
                (
                    "04b_collect_fund_quarterly_asset_allocation",
                    [str(SCRIPT_DIR / "采集基金季报资产配置.py"), *lookthrough_common_args],
                ),
                (
                    "04c_collect_fund_quarterly_holdings",
                    [str(SCRIPT_DIR / "采集基金季报持仓明细.py"), *lookthrough_common_args],
                ),
                (
                    "04c2_repair_positive_stock_holding_gaps",
                    [
                        str(SCRIPT_DIR / "repair_latest_positive_stock_holding_gaps.py"),
                        "--workers",
                        str(max(1, args.fund_lookthrough_workers)),
                        "--output-root",
                        # Keep the nested output name short.  The caller output directory already
                        # contains the run/node/attempt/postprocess hierarchy, and the repair script
                        # appends another timestamp.  A descriptive long suffix can otherwise cross
                        # the legacy Windows MAX_PATH boundary for perfectly valid custom run IDs.
                        str(output_dir / "gap_repair"),
                    ],
                ),
                ("04d_normalize_fund_lookthrough", [str(SCRIPT_DIR / "规范化基金穿透数据.py")]),
                ("04e_build_fund_classification_snapshot", [str(SCRIPT_DIR / "构建基金分类快照.py")]),
                ("04e2_build_fund_economic_exposure", [str(SCRIPT_DIR / "构建基金经济暴露快照.py")]),
                ("04f_audit_fund_lookthrough_coverage", [str(SCRIPT_DIR / "校验基金穿透覆盖率.py")]),
            ]
        )
    if args.skip_index_quote_update:
        skip_step("09_update_index_daily_quotes", "skip_index_quote_update")
        skip_step("09b_update_ttfund_global_index_quotes", "skip_index_quote_update")
    else:
        steps.extend(
            [
                (
                    "09_update_index_daily_quotes",
                    [
                        str(SCRIPT_DIR / "update_index_daily_quotes.py"),
                        "--all",
                        "--incremental",
                        "--lookback-days",
                        str(max(0, args.index_quote_lookback_days)),
                    ],
                ),
                (
                    "09b_update_ttfund_global_index_quotes",
                    [
                        str(SCRIPT_DIR / "update_ttfund_global_index_quotes.py"),
                        "--all",
                    ],
                ),
            ]
        )
    heavy_steps: list[tuple[str, list[str]]] = [
            ("05_backfill_cifm_mutual_nav", [str(SCRIPT_DIR / "backfill_cifm_mutual_fund_nav.py")]),
            ("06_backfill_sina_xh5_nav", [str(SCRIPT_DIR / "backfill_sina_xh5_fund_nav.py")]),
            ("07_backfill_overseas_nav", [str(SCRIPT_DIR / "backfill_ttfund_overseas_nav.py")]),
            ("08_backfill_dividends_from_nav_hints", [str(SCRIPT_DIR / "backfill_dividends_from_nav_hints.py")]),
            (
                "10_reconstruct_strategy_nav",
                [
                    str(SCRIPT_DIR / "reconstruct_strategy_nav.py"),
                    "--algorithm-version",
                    args.algorithm_version,
                    "--output-dir",
                    str(output_dir / "strategy_nav_reconstruction"),
                ],
            ),
            (
                "10b_build_rebalance_quality_analysis",
                [
                    str(SCRIPT_DIR / "build_rebalance_quality_analysis.py"),
                    "--algorithm-version",
                    args.algorithm_version,
                    "--output-json",
                    str(output_dir / "rebalance_quality_analysis" / "summary.json"),
                ],
            ),
            (
                "11_analyze_official_deviation",
                [str(SCRIPT_DIR / "analyze_official_deviation.py"), "--algorithm-version", args.algorithm_version],
            ),
            (
                "12_govern_performance_data",
                [
                    str(SCRIPT_DIR / "govern_performance_data.py"),
                    "--standard-algorithm-version",
                    args.algorithm_version,
                    "--skip-vacuum",
                    *(["--no-backup"] if args.skip_performance_governance_backup else []),
                ],
            ),
            (
                "13_audit_current_holding_projection",
                [str(SCRIPT_DIR / "audit_current_holding_projection.py"), "--write-db"],
            ),
            (
                "13b_build_signal_strategy_events",
                [str(SCRIPT_DIR / "构建信号类策略事件.py")],
            ),
            (
                "15_diagnose_top_deviation_fund_gaps",
                [
                    str(SCRIPT_DIR / "diagnose_ttfund_top_deviation_fund_gaps.py"),
                    "--algorithm-version",
                    args.algorithm_version,
                    "--top-n",
                    "100",
                    "--output-dir",
                    str(output_dir / "top_deviation_fund_gaps"),
                ],
            ),
            (
                "16_summarize_optimized_quality",
                [str(SCRIPT_DIR / "summarize_ttfund_optimized_quality.py"), "--new-algorithm-version", args.algorithm_version],
            ),
            (
                "17_summarize_channel_quality",
                [str(SCRIPT_DIR / "summarize_ttfund_channel_quality.py"), "--algorithm-version", args.algorithm_version],
            ),
            (
                "18_final_integrity_audit",
                [
                    str(SCRIPT_DIR / "audit_ttfund_final_data_integrity.py"),
                    "--algorithm-version",
                    args.algorithm_version,
                    "--output-dir",
                    str(output_dir / "final_integrity"),
                ],
            ),
            (
                "19_audit_official_performance_coverage",
                [
                    str(SCRIPT_DIR / "audit_ttfund_official_performance_coverage.py"),
                    "--algorithm-version",
                    args.algorithm_version,
                    "--output-dir",
                    str(output_dir / "official_performance_coverage"),
                ],
            ),
            (
                "21_export_basic_data_pages",
                [
                    str(SCRIPT_DIR / "export_basic_data_pages.py"),
                    "--algorithm-version",
                    args.algorithm_version,
                    "--site-dir",
                    str(basic_data_site_dir),
                ],
            ),
        ]
    if not args.skip_fund_lookthrough_update:
        output_repair_index = next(index for index, (step, _) in enumerate(heavy_steps) if step == "21_export_basic_data_pages")
        heavy_steps[output_repair_index:output_repair_index] = [
            (
                "19b_repair_positive_stock_holding_gaps_before_outputs",
                [
                    str(SCRIPT_DIR / "repair_latest_positive_stock_holding_gaps.py"),
                    "--workers",
                    str(max(1, args.fund_lookthrough_workers)),
                    "--output-root",
                    # Keep nested timestamped repair output below the legacy
                    # Windows MAX_PATH limit even when the scheduler runId is long.
                    str(output_dir / "gap_final"),
                    "--fail-on-unrepaired",
                ],
            ),
            ("19c_normalize_fund_lookthrough_after_gap_repair", [str(SCRIPT_DIR / "规范化基金穿透数据.py")]),
            ("19d_build_fund_classification_snapshot_after_gap_repair", [str(SCRIPT_DIR / "构建基金分类快照.py")]),
            ("19e_build_fund_economic_exposure_after_gap_repair", [str(SCRIPT_DIR / "构建基金经济暴露快照.py")]),
            ("19f_audit_fund_lookthrough_coverage_after_gap_repair", [str(SCRIPT_DIR / "校验基金穿透覆盖率.py")]),
        ]
    if not args.lightweight:
        skip_step("20_generate_full_data_statistics_report", "旧全量发布包已停止维护；仅生成最小发布集页面源")
    if not args.skip_basic_data_pack_build:
        basic_pack_report_root = deploy_site_dir if deploy_site_dir else PROJECT_ROOT / "site"
        basic_export_index = next(index for index, (step, _) in enumerate(heavy_steps) if step == "21_export_basic_data_pages")
        heavy_steps.insert(
            basic_export_index + 1,
            (
                "22_build_basic_data_report_packs",
                [
                    str(SCRIPT_DIR / "build_basic_data_report_packs.py"),
                    "--report-root",
                    str(basic_pack_report_root),
                    "--minimal-publish-only",
                ],
            ),
        )
        heavy_steps.insert(
            basic_export_index + 2,
            (
                "22b_audit_basic_data_deploy_integrity",
                [
                    str(SCRIPT_DIR / "audit_basic_data_deploy_integrity.py"),
                    "--report-root",
                    str(basic_pack_report_root),
                ],
            ),
        )
    else:
        skip_step("22_build_basic_data_report_packs", "skip_basic_data_pack_build")
        skip_step("22b_audit_basic_data_deploy_integrity", "skip_basic_data_pack_build")
    if not args.skip_guangfa_trade_delay and (SCRIPT_DIR / "tune_ttfund_current_position_algorithms.py").exists():
        heavy_steps.insert(
            next(index for index, (step, _) in enumerate(heavy_steps) if step == "15_diagnose_top_deviation_fund_gaps"),
            (
                "14_evaluate_guangfa_trade_delay",
                [
                    str(SCRIPT_DIR / "evaluate_gffunds_rebalance_trade_delay_algorithm.py"),
                    "--channel-id",
                    "ttfund",
                    "--output-root",
                    str(output_dir / "guangfa_trade_delay_algorithm"),
                ],
            ),
        )
    if not args.skip_dashboard_export and (not deploy_site_dir or args.deploy_page_set == "all"):
        heavy_steps.append(
            (
                "22_export_strategy_dashboard_data",
                [
                    str(SCRIPT_DIR / "export_strategy_dashboard_data.py"),
                    "--algorithm-version",
                    args.algorithm_version,
                    "--site-dir",
                    str(strategy_site_dir),
                ],
            )
        )
    elif deploy_site_dir and args.deploy_page_set == "basic_data" and not args.lightweight:
        skip_step("22_export_strategy_dashboard_data", "deploy_page_set=basic_data")
    if deploy_site_dir and not args.skip_deploy_export:
        heavy_steps = [
            (
                "04_prepare_deploy_site",
                [
                    str(SCRIPT_DIR / "prepare_analysis_platform_deploy.py"),
                    "--deploy-dir",
                    str(deploy_site_dir),
                    "--page-set",
                    args.deploy_page_set,
                ],
            ),
            *heavy_steps,
            (
                "23_write_deploy_manifest",
                [
                    str(SCRIPT_DIR / "write_analysis_platform_deploy_manifest.py"),
                    "--deploy-dir",
                    str(deploy_site_dir),
                    "--page-set",
                    args.deploy_page_set,
                ],
            ),
        ]
    if args.lightweight:
        audit_args = [str(SCRIPT_DIR / "audit_ttfund_incremental_smoke.py")]
        if args.incremental_run_id:
            audit_args.extend(["--run-id", args.incremental_run_id])
        if target_trade_date:
            audit_args.extend(["--target-trade-date", target_trade_date])
        if args.skip_fund_nav_refresh:
            audit_args.extend(["--allow-fund-nav-lag-days", "7"])
        steps.append(("05_audit_ttfund_incremental_smoke", audit_args))
        steps.extend(deploy_export_steps("06"))
        skip_step("full_quality_replay", "lightweight_incremental")
    else:
        steps.extend(heavy_steps)
    if args.start_at_step:
        step_names = [step for step, _ in steps]
        start_step = STEP_ALIASES.get(args.start_at_step, args.start_at_step)
        if start_step not in step_names:
            raise SystemExit(f"unknown start step: {args.start_at_step}; available: {', '.join(step_names)}")
        start_index = step_names.index(start_step)
        steps = steps[start_index:]
    if args.stop_after_step:
        step_names = [step for step, _ in steps]
        stop_step = STEP_ALIASES.get(args.stop_after_step, args.stop_after_step)
        if stop_step not in step_names:
            raise SystemExit(f"unknown stop step: {args.stop_after_step}; available: {', '.join(step_names)}")
        stop_index = step_names.index(stop_step)
        steps = steps[: stop_index + 1]

    if args.dry_run:
        print(
            json.dumps(
                {
                    "状态": "dry_run",
                    "算法版本": args.algorithm_version,
                    "target_trade_date": target_trade_date,
                    "latest_fund_nav_date": latest_fund_nav_date,
                    "deploy_site_dir": str(deploy_site_dir) if deploy_site_dir else None,
                    "deploy_page_set": args.deploy_page_set,
                    "步骤数": len(steps),
                    "步骤": [{"step": step, "command": command_args} for step, command_args in steps],
                    "skipped_steps": skipped_steps,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    output_dir.mkdir(parents=True, exist_ok=True)
    results: list[StepResult] = []
    checkpoint_path = output_dir / "post_update_quality_checkpoint.json"
    optional_failure_steps = {
        "05_backfill_cifm_mutual_nav",
        "06_backfill_sina_xh5_nav",
        "07_backfill_overseas_nav",
    }
    optional_failures: list[dict[str, str | int]] = []

    def write_checkpoint(state: str, current_step: str | None = None, error: str | None = None) -> None:
        atomic_write_json(
            checkpoint_path,
            {
                "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "state": state,
                "current_step": current_step,
                "target_trade_date": target_trade_date,
                "completed_steps": [result.__dict__ for result in results],
                "optional_failures": optional_failures,
                "skipped_steps": skipped_steps,
                "error": error,
            },
        )

    write_checkpoint("running")
    try:
        total_steps = len(steps)
        for index, (step, command_args) in enumerate(steps, start=1):
            write_checkpoint("running", step)
            print(
                f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                f"[{index}/{total_steps}] START {step}: {' '.join(command_args)}",
                flush=True,
            )
            result = run_step(step, command_args, log_dir, args.timeout)
            results.append(result)
            write_checkpoint("running", step)
            print(
                f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                f"[{index}/{total_steps}] DONE  {step}: exit={result.returncode}, "
                f"elapsed={result.elapsed_seconds}s, log={result.log_path}",
                flush=True,
            )
            if result.returncode != 0:
                if step in optional_failure_steps:
                    optional_failures.append(
                        {"step": step, "returncode": result.returncode, "log_path": result.log_path}
                    )
                    continue
                raise RuntimeError(f"{step} failed, see {result.log_path}")
        state = "completed"
        error = None
    except Exception as exc:
        state = "failed"
        error = str(exc)
        write_checkpoint(state, results[-1].step if results else None, error)

    summary = {
        "生成时间": datetime.now().astimezone().isoformat(timespec="seconds"),
        "状态": state,
        "算法版本": args.algorithm_version,
        "输出目录": str(output_dir),
        "target_trade_date": target_trade_date,
        "latest_fund_nav_date_before": latest_fund_nav_date,
        "auto_fast_incremental": auto_fast_incremental,
        "deploy_site_dir": str(deploy_site_dir) if deploy_site_dir else None,
        "deploy_page_set": args.deploy_page_set,
        "步骤数": len(results),
        "失败信息": error,
        "步骤": [result.__dict__ for result in results],
        "optional_failures": optional_failures,
        "skipped_steps": skipped_steps,
        "checkpoint_path": str(checkpoint_path),
    }
    summary_path = output_dir / "post_update_quality_summary.json"
    atomic_write_json(summary_path, summary)
    write_checkpoint(state, error=error)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if state != "completed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
