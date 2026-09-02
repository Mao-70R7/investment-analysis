from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
CURRENT_ALGORITHM_VERSION = "standard_rebalance_asset_dual_nav_v10_all_channels_20260528"

ANALYSIS_DB = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
RAW_INDEX_DB = PROJECT_ROOT / "data" / "advisor_monitor.sqlite"

KEEP_SCRIPTS = {
    "analyze_official_deviation.py",
    "audit_current_holding_projection.py",
    "audit_ttfund_final_data_integrity.py",
    "audit_ttfund_official_performance_coverage.py",
    "backfill_cifm_mutual_fund_nav.py",
    "backfill_dividends_from_nav_hints.py",
    "backfill_fund_history_analysis_sqlite.py",
    "backfill_sina_fund_nav_analysis_sqlite.py",
    "backfill_ttfund_overseas_nav.py",
    "build_rebalance_quality_analysis.py",
    "build_ttfund_incremental_plan.py",
    "cleanup_current_project_state.py",
    "collect_official_apps_public.py",
    "collect_ttfund_direct_interfaces.py",
    "collect_ttfund_fund_nav.py",
    "collect_ttfund_loggedin.py",
    "collect_ttfund_official_performance_curve.py",
    "diagnose_ttfund_top_deviation_fund_gaps.py",
    "drive_ttfund_app.py",
    "enrich_fund_name_aliases.py",
    "ensure_analysis_performance_indexes.py",
    "evaluate_gffunds_rebalance_trade_delay_algorithm.py",
    "export_strategy_dashboard_data.py",
    "govern_performance_data.py",
    "load_analysis_zh_current_sqlite.py",
    "load_fund_history_from_normalized.py",
    "normalize_southern_live_artifact.py",
    "rebalance_snapshot_repairs.py",
    "rebuild_fund_nav_raw_manifests.py",
    "reconstruct_strategy_nav.py",
    "run_southern_live_collect.js",
    "run_ttfund_full_capture.ps1",
    "run_ttfund_incremental_history_repair.ps1",
    "run_ttfund_incremental_update.ps1",
    "run_ttfund_post_update_quality.py",
    "serve_strategy_center.py",
    "southern_utils.js",
    "start_ttfund_lan_capture.ps1",
    "summarize_ttfund_channel_quality.py",
    "summarize_ttfund_optimized_quality.py",
    "sync_raw_snapshot_manifests.py",
    "validate_ttfund_interface_mode.py",
}

KEEP_DOCS = {
    "analysis_zh_current_model.md",
    "data_model.md",
    "official_app_collection_2026-05-23.md",
    "official_strategy_governance_2026-05-23.md",
    "ttfund_interface_inventory_2026-05-14.md",
    "ttfund_loggedin_interface_inventory_2026-05-14.md",
}

KEEP_OUTPUT_PREFIXES = [
    "outputs/current_cleanup",
    "outputs/official_deviation_analysis_ttfund_appbasis_target_window_20260527",
    "outputs/strategy_nav_reconstruction_ttfund_rules_cifm_overseas_placeholder_20260527_appbasis_target_window",
    "outputs/ttfund_appbasis_quality_summary/2026-05-27/target_window",
    "outputs/ttfund_final_integrity/2026-05-27/appbasis_target_window_final",
    "outputs/ttfund_final_integrity/2026-05-28/post_cleanup_final",
    "outputs/ttfund_guangfa_trade_delay_appbasis_20260527",
    "outputs/ttfund_official_performance_coverage/2026-05-27/after_appbasis_target_window",
    "outputs/ttfund_top_deviation_fund_gaps/2026-05-27/appbasis_target_window_top100",
]

KEEP_RAW_PREFIXES = [
    "data/raw/ttfund/loggedin_cache/2026-05-27/20260527T224756+0800",
    "data/raw/ttfund/official_performance_curve/2026-05-27/20260527T230144+0800",
    "data/raw/ttfund/official_performance_curve/2026-05-27/20260527T230720+0800",
]

KEEP_NORMALIZED_PREFIXES = [
    "data/normalized/cifm_mutual_fund_nav",
    "data/normalized/ttfund_fund_nav",
    "data/normalized/ttfund_overseas_nav",
]

REMOVE_DIRS = [
    ".venv-mitm",
    "deploy",
    "data/api_probe",
    "data/apk",
    "data/device_pull",
    "data/integrated_zh",
    "data/protocol_pdfs",
    "data/quality",
    "data/quarantine",
    "data/screenshots",
    "data/state",
    "data/raw/cifm_mutual_fund_nav",
    "data/raw/sina_fund_nav",
    "data/raw/ttfund_fund_nav",
    "data/raw/ttfund_overseas_nav",
    "国泰海通证券",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="清理项目历史无效数据，只保留当前最新全量数据和当前生产链路。")
    parser.add_argument("--execute", action="store_true", help="实际执行删除；未指定时只生成 dry-run 清单。")
    parser.add_argument("--skip-vacuum", action="store_true", help="跳过 SQLite VACUUM。")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument(
        "--allow-legacy-cleanup",
        action="store_true",
        help="显式允许执行旧版深度清理逻辑。日常请改用 cleanup_sync_artifacts.py。",
    )
    return parser.parse_args()


def rel(path: Path) -> str:
    return path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()


def safe_path(path: Path) -> Path:
    resolved = path.resolve()
    root = PROJECT_ROOT.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"path escapes project root: {path}")
    return resolved


def path_under(path: Path, prefixes: set[Path]) -> bool:
    resolved = path.resolve()
    for prefix in prefixes:
        prefix_resolved = prefix.resolve()
        if resolved == prefix_resolved or prefix_resolved in resolved.parents:
            return True
    return False


def remove_file(path: Path, execute: bool, actions: list[dict[str, Any]], reason: str) -> None:
    safe_path(path)
    if not path.exists() or not path.is_file():
        return
    actions.append({"action": "delete_file", "path": rel(path), "bytes": path.stat().st_size, "reason": reason})
    if execute:
        path.unlink()


def remove_dir(path: Path, execute: bool, actions: list[dict[str, Any]], reason: str) -> None:
    safe_path(path)
    if not path.exists() or not path.is_dir():
        return
    size = sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
    actions.append({"action": "delete_dir", "path": rel(path), "bytes": size, "reason": reason})
    if execute:
        shutil.rmtree(path)


def prune_tree(
    root: Path,
    keep_prefixes: set[Path],
    keep_files: set[Path],
    execute: bool,
    actions: list[dict[str, Any]],
    reason: str,
) -> None:
    root = safe_path(root)
    if not root.exists():
        return
    for file_path in sorted((item for item in root.rglob("*") if item.is_file()), reverse=True):
        resolved = file_path.resolve()
        if resolved in keep_files or path_under(file_path, keep_prefixes):
            continue
        remove_file(file_path, execute, actions, reason)
    if execute:
        for dir_path in sorted((item for item in root.rglob("*") if item.is_dir()), key=lambda p: len(p.parts), reverse=True):
            if not path_under(dir_path, keep_prefixes):
                try:
                    dir_path.rmdir()
                except OSError:
                    pass


def fetch_data_source_keep_files() -> tuple[set[Path], set[tuple[str, str]]]:
    keep_files: set[Path] = set()
    keep_channel_batches: set[tuple[str, str]] = set()
    if not ANALYSIS_DB.exists():
        return keep_files, keep_channel_batches
    conn = sqlite3.connect(ANALYSIS_DB)
    conn.row_factory = sqlite3.Row
    for row in conn.execute('SELECT "渠道ID", "采集批次ID", "文件路径" FROM "数据来源清单"'):
        raw_path = row["文件路径"]
        if raw_path:
            path = Path(str(raw_path))
            if not path.is_absolute():
                path = PROJECT_ROOT / path
            if PROJECT_ROOT.resolve() in path.resolve().parents:
                keep_files.add(path.resolve())
        if row["渠道ID"] and row["采集批次ID"]:
            keep_channel_batches.add((str(row["渠道ID"]), str(row["采集批次ID"])))
    conn.close()
    return keep_files, keep_channel_batches


def add_sibling_normalized_keep_files(keep_files: set[Path], keep_channel_batches: set[tuple[str, str]]) -> None:
    for channel_id, batch_id in keep_channel_batches:
        channel_root = PROJECT_ROOT / "data" / "normalized" / channel_id
        if not channel_root.exists():
            continue
        for entity_dir in channel_root.iterdir():
            if not entity_dir.is_dir():
                continue
            for file_path in entity_dir.rglob(f"{batch_id}.*"):
                if file_path.is_file():
                    keep_files.add(file_path.resolve())


def clean_database(execute: bool, skip_vacuum: bool) -> dict[str, Any]:
    result: dict[str, Any] = {"analysis": {}, "raw_index": {}}
    if ANALYSIS_DB.exists():
        conn = sqlite3.connect(ANALYSIS_DB)
        conn.row_factory = sqlite3.Row
        table_deletes: dict[str, int] = {}
        tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        for table in tables:
            cols = [row["name"] for row in conn.execute(f'PRAGMA table_info("{table}")')]
            if "算法版本" not in cols:
                continue
            count = int(
                conn.execute(f'SELECT COUNT(*) FROM "{table}" WHERE "算法版本" <> ?', [CURRENT_ALGORITHM_VERSION]).fetchone()[0]
            )
            table_deletes[table] = count
            if execute and count:
                conn.execute(f'DELETE FROM "{table}" WHERE "算法版本" <> ?', [CURRENT_ALGORITHM_VERSION])
        result["analysis"]["obsolete_algorithm_rows"] = table_deletes
        result["analysis"]["size_before"] = ANALYSIS_DB.stat().st_size
        if execute:
            conn.commit()
            if not skip_vacuum and sum(table_deletes.values()) > 0:
                conn.execute("VACUUM")
                conn.execute("PRAGMA optimize")
        conn.close()
        result["analysis"]["size_after"] = ANALYSIS_DB.stat().st_size

    if RAW_INDEX_DB.exists():
        conn = sqlite3.connect(RAW_INDEX_DB)
        keep_prefixes = {safe_path(PROJECT_ROOT / prefix) for prefix in KEEP_RAW_PREFIXES}
        rows = conn.execute("SELECT snapshot_id, raw_path FROM raw_snapshot").fetchall()
        delete_ids: list[str] = []
        for snapshot_id, raw_path in rows:
            if not raw_path:
                delete_ids.append(snapshot_id)
                continue
            path = Path(str(raw_path))
            if not path.is_absolute():
                path = PROJECT_ROOT / path
            try:
                keep = path.exists() and path_under(path, keep_prefixes)
            except ValueError:
                keep = False
            if not keep:
                delete_ids.append(snapshot_id)
        result["raw_index"]["raw_snapshot_rows_before"] = len(rows)
        result["raw_index"]["raw_snapshot_rows_to_delete"] = len(delete_ids)
        result["raw_index"]["size_before"] = RAW_INDEX_DB.stat().st_size
        if execute and delete_ids:
            conn.executemany("DELETE FROM raw_snapshot WHERE snapshot_id = ?", [(item,) for item in delete_ids])
            conn.commit()
            if not skip_vacuum:
                conn.execute("VACUUM")
                conn.execute("PRAGMA optimize")
        conn.close()
        result["raw_index"]["size_after"] = RAW_INDEX_DB.stat().st_size
    return result


def clean_files(execute: bool, output_dir: Path) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    output_dir.mkdir(parents=True, exist_ok=True)

    keep_normalized_files, keep_channel_batches = fetch_data_source_keep_files()
    add_sibling_normalized_keep_files(keep_normalized_files, keep_channel_batches)

    keep_output_prefixes = {safe_path(PROJECT_ROOT / prefix) for prefix in KEEP_OUTPUT_PREFIXES}
    keep_output_prefixes.add(output_dir.resolve())
    keep_raw_prefixes = {safe_path(PROJECT_ROOT / prefix) for prefix in KEEP_RAW_PREFIXES}
    keep_normalized_prefixes = {safe_path(PROJECT_ROOT / prefix) for prefix in KEEP_NORMALIZED_PREFIXES}

    remove_file(PROJECT_ROOT / "analysis_zh_current.sqlite", execute, actions, "根目录零字节旧数据库占位文件")

    for pycache in PROJECT_ROOT.rglob("__pycache__"):
        remove_dir(pycache, execute, actions, "Python 缓存目录")
    for pyc in PROJECT_ROOT.rglob("*.pyc"):
        remove_file(pyc, execute, actions, "Python 编译缓存")

    scripts_dir = PROJECT_ROOT / "节点脚本" / "_共享组件" / "生产程序"
    if scripts_dir.exists():
        for file_path in scripts_dir.iterdir():
            if file_path.is_file() and file_path.name not in KEEP_SCRIPTS:
                remove_file(file_path, execute, actions, "历史探测/烟测/已弃用脚本")

    docs_dir = PROJECT_ROOT / "文档" / "项目规范"
    if docs_dir.exists():
        for file_path in docs_dir.iterdir():
            if file_path.is_file() and file_path.name not in KEEP_DOCS:
                remove_file(file_path, execute, actions, "历史调研文档")

    for remove_rel in REMOVE_DIRS:
        remove_dir(PROJECT_ROOT / remove_rel, execute, actions, "历史运行环境/缓存/部署包")

    for app_output in (PROJECT_ROOT / "official_apps").glob("**/outputs"):
        remove_dir(app_output, execute, actions, "官方 App 采集重复输出，已入库到 data/normalized")

    prune_tree(PROJECT_ROOT / "outputs", keep_output_prefixes, set(), execute, actions, "历史分析输出")
    prune_tree(PROJECT_ROOT / "data" / "raw", keep_raw_prefixes, set(), execute, actions, "历史原始缓存/抓包/探测数据")
    prune_tree(
        PROJECT_ROOT / "data" / "normalized",
        keep_normalized_prefixes,
        keep_normalized_files,
        execute,
        actions,
        "历史 normalized 批次，未被当前数据来源清单引用",
    )
    return actions


def summarize_actions(actions: list[dict[str, Any]]) -> dict[str, Any]:
    by_reason: Counter[str] = Counter()
    bytes_by_reason: Counter[str] = Counter()
    for action in actions:
        reason = str(action["reason"])
        by_reason[reason] += 1
        bytes_by_reason[reason] += int(action.get("bytes") or 0)
    return {
        "action_count": len(actions),
        "bytes_total": sum(int(action.get("bytes") or 0) for action in actions),
        "mb_total": round(sum(int(action.get("bytes") or 0) for action in actions) / 1024 / 1024, 3),
        "count_by_reason": dict(by_reason),
        "mb_by_reason": {key: round(value / 1024 / 1024, 3) for key, value in bytes_by_reason.items()},
    }


def main() -> None:
    args = parse_args()
    if not args.allow_legacy_cleanup:
        raise SystemExit(
            "cleanup_current_project_state.py is disabled by default because its allow-list is outdated. "
            "Use 节点脚本/_共享组件/生产程序/cleanup_sync_artifacts.py for conservative cleanup, or pass --allow-legacy-cleanup "
            "only after reviewing the legacy deletion list."
        )
    now = datetime.now().astimezone()
    output_dir = args.output_dir or PROJECT_ROOT / "outputs" / "current_cleanup" / now.strftime("%Y-%m-%d") / now.strftime(
        "%Y%m%dT%H%M%S%z"
    )
    output_dir = safe_path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    file_actions = clean_files(args.execute, output_dir)
    db_result = clean_database(args.execute, args.skip_vacuum)
    summary = {
        "generated_at": now.isoformat(timespec="seconds"),
        "mode": "execute" if args.execute else "dry_run",
        "project_root": str(PROJECT_ROOT),
        "current_algorithm_version": CURRENT_ALGORITHM_VERSION,
        "file_cleanup": summarize_actions(file_actions),
        "database_cleanup": db_result,
        "output_dir": str(output_dir),
    }
    (output_dir / "cleanup_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "cleanup_actions.jsonl").write_text(
        "\n".join(json.dumps(action, ensure_ascii=False) for action in file_actions),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
