from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_REPORT_ROOT = PROJECT_ROOT / "site"
SCRIPT_PATH = PROJECT_ROOT / "节点脚本" / "_共享组件" / "生产程序" / "apply_field_renames_and_build_insights.js"
FUND_ENRICHMENT_SCRIPT_PATH = PROJECT_ROOT / "节点脚本" / "_共享组件" / "生产程序" / "build_fund_page_enrichment_pack.py"
FUND_ECONOMIC_EXPOSURE_SCRIPT_PATH = PROJECT_ROOT / "节点脚本" / "_共享组件" / "生产程序" / "同步基金经济暴露到页面包.py"
QUALITY_GATE_SCRIPT_PATH = PROJECT_ROOT / "节点脚本" / "_共享组件" / "生产程序" / "构建基础数据质量包.py"
FIELD_DICTIONARY_SCRIPT_PATH = PROJECT_ROOT / "节点脚本" / "_共享组件" / "生产程序" / "构建字段字典与指标口径.py"
DATA_AUDIT_SCRIPT_PATH = PROJECT_ROOT / "节点脚本" / "_共享组件" / "生产程序" / "标准化数据稽核.py"
DEFAULT_DB_PATH = Path(os.environ.get("ADVISOR_DATABASE_ROOT") or PROJECT_ROOT / "data") / "analysis_zh_current.sqlite"
ADVISOR_FOF_RANKING_SCRIPT_PATH = PROJECT_ROOT / "节点脚本" / "_共享组件" / "生产程序" / "build_advisor_fof_ranking_pack.py"
FOF_H1_SOURCE_SCRIPT_PATH = PROJECT_ROOT / "节点脚本" / "_共享组件" / "生产程序" / "generate_fof_h1_strategy_rank_data.py"
FOF_BENCHMARK_ENRICH_SCRIPT_PATH = PROJECT_ROOT / "节点脚本" / "_共享组件" / "生产程序" / "enrich_fof_benchmark_classification_and_rerank.py"
FOF_BENCHMARK_DATA_PATH = PROJECT_ROOT / "data" / "raw" / "fof_f10_benchmark" / "latest_fof_f10_benchmarks.json"
FOF_H1_OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "fof_h1_strategy_ranking"
FOF_BENCHMARK_OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "fof_benchmark_ranking"
MIXED_PERFORMANCE_SOURCE_SCRIPT_PATH = PROJECT_ROOT / "节点脚本" / "_共享组件" / "生产程序" / "export_advisor_public_fund_mixed_performance_source.py"
MIXED_PERFORMANCE_PACK_SCRIPT_PATH = PROJECT_ROOT / "节点脚本" / "_共享组件" / "生产程序" / "build_mixed_performance_scatter_pack.py"
MONTHLY_REBALANCE_REPORT_SCRIPT_PATH = PROJECT_ROOT / "节点脚本" / "_共享组件" / "生产程序" / "build_monthly_rebalance_research_report.py"
GF_REBALANCE_MONITOR_SCRIPT_PATH = PROJECT_ROOT / "节点脚本" / "_共享组件" / "生产程序" / (
    "".join(chr(code) for code in (0x751F, 0x6210, 0x5E7F, 0x53D1, 0x57FA, 0x91D1, 0x8C03, 0x4ED3, 0x76D1, 0x63A7, 0x4E13, 0x9898, 0x9875))
    + ".py"
)
QD_FUND_REPORT_SCRIPT_PATH = PROJECT_ROOT / "节点脚本" / "_共享组件" / "生产程序" / (
    "".join(chr(code) for code in (0x751F, 0x6210, 0x51, 0x44, 0x57FA, 0x91D1, 0x914D, 0x7F6E, 0x4E13, 0x9898, 0x9875))
    + ".py"
)


def project_arg(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(resolved)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build basic_data insight packs after basic_summary/details export.")
    parser.add_argument("--report-root", type=Path, default=DEFAULT_REPORT_ROOT)
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--node-exe", default=os.environ.get("ADVISOR_NODE_EXE") or os.environ.get("NODE_EXE") or "node")
    parser.add_argument(
        "--skip-fund-enrichment",
        action="store_true",
        help="Skip per-fund NAV/report holding files and the fund economic-exposure detail pack.",
    )
    parser.add_argument("--skip-gf-rebalance-monitor", action="store_true", help="Skip Guangfa fund rebalance monitor page.")
    parser.add_argument("--skip-qd-fund-report", action="store_true", help="Skip QD fund allocation report pages.")
    parser.add_argument("--skip-field-dictionary", action="store_true", help="Skip field dictionary data pack.")
    parser.add_argument("--skip-quality-gate", action="store_true", help="Skip data quality pack and acceptance gate.")
    parser.add_argument("--skip-data-audit", action="store_true", help="Skip standardized data audit.")
    parser.add_argument(
        "--minimal-publish-only",
        action="store_true",
        help="Build only packs required by the minimal publish package; skip standalone Guangfa and QD report pages.",
    )
    return parser.parse_args()


def should_build_fund_detail_artifacts(args: argparse.Namespace) -> bool:
    """Per-fund detail artifacts are outside the minimal publish contract."""
    return not (
        bool(getattr(args, "minimal_publish_only", False))
        or bool(getattr(args, "skip_fund_enrichment", False))
    )


def latest_fof_benchmark_source() -> Path | None:
    candidates = list(FOF_BENCHMARK_OUTPUT_ROOT.glob("*/fof_benchmark_classified_ranking_data.json"))
    latest_alias = FOF_BENCHMARK_OUTPUT_ROOT / "latest_fof_benchmark_classified_ranking_data.json"
    if latest_alias.is_file():
        candidates.append(latest_alias)
    return max(candidates, key=lambda path: path.stat().st_mtime_ns) if candidates else None


def read_basic_summary_watermarks(summary_path: Path) -> tuple[int | None, date | None]:
    if not summary_path.is_file():
        return None, None
    text = summary_path.read_text(encoding="utf-8")
    assignment = "window.__BASIC_DATA__.summary ="
    marker = text.find(assignment)
    start = text.find("{", marker + len(assignment)) if marker >= 0 else -1
    if start < 0:
        return None, None
    try:
        payload, _ = json.JSONDecoder().raw_decode(text[start:])
    except (json.JSONDecodeError, TypeError):
        return None, None
    overview = payload.get("overview") if isinstance(payload, dict) else {}
    strategies = payload.get("strategies") if isinstance(payload, dict) else []
    total_value = overview.get("策略总数") if isinstance(overview, dict) else None
    strategy_total = int(total_value) if total_value not in (None, "") else len(strategies or [])
    date_text = str(overview.get("数据更新至") or "") if isinstance(overview, dict) else ""
    try:
        data_date = date.fromisoformat(date_text[:10]) if date_text else None
    except ValueError:
        data_date = None
    return strategy_total, data_date


def fof_source_business_watermarks(source: Path) -> tuple[int | None, date | None]:
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, None
    meta = payload.get("meta") if isinstance(payload, dict) else {}
    if not isinstance(meta, dict):
        return None, None
    total_value = meta.get("策略总数")
    strategy_total = int(total_value) if total_value not in (None, "") else None
    date_text = str(meta.get("实际FOF净值最新日") or "")
    try:
        nav_date = date.fromisoformat(date_text[:10]) if date_text else None
    except ValueError:
        nav_date = None
    return strategy_total, nav_date


def fof_benchmark_source_is_fresh(
    source: Path,
    dependencies: list[Path],
    summary_path: Path | None = None,
    maximum_nav_lag_days: int = 5,
) -> bool:
    source_mtime = source.stat().st_mtime_ns
    existing_dependencies = [path for path in dependencies if path.is_file()]
    if existing_dependencies and any(path.stat().st_mtime_ns > source_mtime for path in existing_dependencies):
        return False
    if summary_path is None or not summary_path.is_file():
        return True
    current_total, current_date = read_basic_summary_watermarks(summary_path)
    source_total, source_nav_date = fof_source_business_watermarks(source)
    if current_total is None or source_total is None or current_total != source_total:
        return False
    if current_date is not None and source_nav_date is not None:
        if (current_date - source_nav_date).days > maximum_nav_lag_days:
            return False
    elif current_date is not None:
        return False
    return True


def ensure_fof_benchmark_source(report_root: Path, db_path: Path) -> Path:
    existing = latest_fof_benchmark_source()
    summary_path = report_root / "basic_data" / "data" / "basic_summary.js"
    h1_source = FOF_H1_OUTPUT_ROOT / "latest_fof_h1_strategy_ranking_data.json"
    dependencies = [h1_source, FOF_BENCHMARK_DATA_PATH]
    if existing is not None and fof_benchmark_source_is_fresh(existing, dependencies, summary_path):
        current_total, current_date = read_basic_summary_watermarks(summary_path)
        source_total, source_nav_date = fof_source_business_watermarks(existing)
        print(
            "[INFO] Reusing FOF benchmark ranking source with aligned business watermarks: "
            f"strategies={source_total or current_total or 'unknown'}, "
            f"source_nav={source_nav_date or 'unknown'}, report_data={current_date or 'unknown'}.",
            flush=True,
        )
        return existing

    required_paths = {
        "FOF H1 source builder": FOF_H1_SOURCE_SCRIPT_PATH,
        "FOF benchmark enrichment builder": FOF_BENCHMARK_ENRICH_SCRIPT_PATH,
        "FOF F10 benchmark input": FOF_BENCHMARK_DATA_PATH,
        "basic summary": summary_path,
    }
    missing = [f"{label}: {path}" for label, path in required_paths.items() if not path.is_file()]
    if missing:
        raise SystemExit("cannot bootstrap FOF benchmark ranking source; missing " + "; ".join(missing))

    refresh_reason = "missing" if existing is None else "stale"
    print(f"[INFO] FOF benchmark ranking source is {refresh_reason}; rebuilding it from current data.", flush=True)
    completed = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            project_arg(FOF_H1_SOURCE_SCRIPT_PATH),
            "--db-path",
            project_arg(db_path),
            "--summary-js",
            project_arg(summary_path),
            "--output-root",
            project_arg(FOF_H1_OUTPUT_ROOT),
        ],
        cwd=PROJECT_ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode:
        raise SystemExit(f"FOF ranking source bootstrap failed at H1 source build: exit={completed.returncode}")

    h1_source = FOF_H1_OUTPUT_ROOT / "latest_fof_h1_strategy_ranking_data.json"
    if not h1_source.is_file():
        raise SystemExit(f"FOF H1 source builder completed without output: {h1_source}")
    completed = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            project_arg(FOF_BENCHMARK_ENRICH_SCRIPT_PATH),
            "--input-data",
            project_arg(h1_source),
            "--benchmark-data",
            project_arg(FOF_BENCHMARK_DATA_PATH),
            "--output-root",
            project_arg(FOF_BENCHMARK_OUTPUT_ROOT),
            "--db-path",
            project_arg(db_path),
            "--skip-db",
        ],
        cwd=PROJECT_ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode:
        raise SystemExit(f"FOF ranking source bootstrap failed at benchmark enrichment: exit={completed.returncode}")
    generated = latest_fof_benchmark_source()
    if generated is None:
        raise SystemExit(f"FOF benchmark enrichment completed without output under: {FOF_BENCHMARK_OUTPUT_ROOT}")
    print(f"[INFO] FOF benchmark ranking source ready: {generated}", flush=True)
    return generated


def main() -> None:
    args = parse_args()
    # The old full package is retired. Every invocation now follows the minimal page contract.
    args.minimal_publish_only = True
    build_fund_detail_artifacts = should_build_fund_detail_artifacts(args)
    skip_gf_rebalance_monitor = True
    skip_qd_fund_report = True
    report_root = args.report_root.resolve()
    db_path = args.db_path.resolve()
    if not db_path.is_file():
        raise SystemExit(f"missing analysis database: {db_path}")
    summary_path = report_root / "basic_data" / "data" / "basic_summary.js"
    details_dir = report_root / "basic_data" / "data" / "details"
    if not SCRIPT_PATH.exists():
        raise SystemExit(f"missing report pack builder: {SCRIPT_PATH}")
    if not summary_path.exists():
        raise SystemExit(f"missing basic_summary.js: {summary_path}")
    if not details_dir.is_dir():
        raise SystemExit(f"missing details directory: {details_dir}")
    if not FUND_ENRICHMENT_SCRIPT_PATH.exists():
        raise SystemExit(f"missing fund enrichment builder: {FUND_ENRICHMENT_SCRIPT_PATH}")
    if not FUND_ECONOMIC_EXPOSURE_SCRIPT_PATH.exists():
        raise SystemExit(f"missing fund economic exposure sync script: {FUND_ECONOMIC_EXPOSURE_SCRIPT_PATH}")
    if not args.skip_field_dictionary and not FIELD_DICTIONARY_SCRIPT_PATH.exists():
        raise SystemExit(f"missing field dictionary builder: {FIELD_DICTIONARY_SCRIPT_PATH}")
    if not args.skip_quality_gate and not QUALITY_GATE_SCRIPT_PATH.exists():
        raise SystemExit(f"missing data quality gate builder: {QUALITY_GATE_SCRIPT_PATH}")
    if not args.skip_data_audit and not DATA_AUDIT_SCRIPT_PATH.exists():
        raise SystemExit(f"missing standardized data audit script: {DATA_AUDIT_SCRIPT_PATH}")
    if not ADVISOR_FOF_RANKING_SCRIPT_PATH.exists():
        raise SystemExit(f"missing advisor-FOF ranking pack builder: {ADVISOR_FOF_RANKING_SCRIPT_PATH}")
    if not MIXED_PERFORMANCE_SOURCE_SCRIPT_PATH.exists():
        raise SystemExit(f"missing mixed performance source builder: {MIXED_PERFORMANCE_SOURCE_SCRIPT_PATH}")
    if not MIXED_PERFORMANCE_PACK_SCRIPT_PATH.exists():
        raise SystemExit(f"missing mixed performance page pack builder: {MIXED_PERFORMANCE_PACK_SCRIPT_PATH}")
    if not skip_gf_rebalance_monitor and not GF_REBALANCE_MONITOR_SCRIPT_PATH.exists():
        raise SystemExit(f"missing Guangfa rebalance monitor builder: {GF_REBALANCE_MONITOR_SCRIPT_PATH}")
    if not skip_qd_fund_report and not QD_FUND_REPORT_SCRIPT_PATH.exists():
        raise SystemExit(f"missing QD fund report builder: {QD_FUND_REPORT_SCRIPT_PATH}")
    fof_benchmark_source = ensure_fof_benchmark_source(report_root, db_path)
    completed = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            project_arg(FUND_ECONOMIC_EXPOSURE_SCRIPT_PATH),
            "--db-path",
            project_arg(db_path),
            "--site-dir",
            project_arg(report_root / "basic_data"),
            "--skip-fund-detail",
        ],
        cwd=PROJECT_ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode:
        raise SystemExit(completed.returncode)
    completed = subprocess.run(
        [args.node_exe, project_arg(SCRIPT_PATH), "--report-root", project_arg(report_root)],
        cwd=PROJECT_ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode:
        raise SystemExit(completed.returncode)
    from export_basic_data_pages import write_shell_files, write_target_profit_analysis_pack, write_topic_analysis_pack

    write_shell_files(report_root / "basic_data")
    write_topic_analysis_pack(report_root / "basic_data", db_path)
    completed = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            project_arg(ADVISOR_FOF_RANKING_SCRIPT_PATH),
            "--db-path",
            project_arg(db_path),
            "--site-dir",
            project_arg(report_root / "basic_data"),
            "--source-json",
            project_arg(fof_benchmark_source),
        ],
        cwd=PROJECT_ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode:
        raise SystemExit(completed.returncode)
    mixed_source_dir = report_root / "reports" / "advisor_public_fund_mixed_performance_latest"
    completed = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            project_arg(MIXED_PERFORMANCE_SOURCE_SCRIPT_PATH),
            "--pack",
            project_arg(report_root / "basic_data" / "data" / "advisor_fof_ranking_pack.json"),
            "--summary-core",
            project_arg(report_root / "basic_data" / "data" / "basic_summary_core.js"),
            "--db",
            project_arg(db_path),
            "--out-dir",
            project_arg(mixed_source_dir),
        ],
        cwd=PROJECT_ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode:
        raise SystemExit(completed.returncode)
    completed = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            project_arg(MIXED_PERFORMANCE_PACK_SCRIPT_PATH),
            "--source",
            project_arg(mixed_source_dir / "workbook_source.json"),
            "--formal-root",
            project_arg(report_root),
            "--dev-root",
            project_arg(PROJECT_ROOT),
            "--no-dev-copy",
        ],
        cwd=PROJECT_ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode:
        raise SystemExit(completed.returncode)
    if not skip_gf_rebalance_monitor:
        completed = subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8",
                project_arg(GF_REBALANCE_MONITOR_SCRIPT_PATH),
                "--db-path",
                project_arg(db_path),
                "--site-dir",
                project_arg(report_root / "basic_data"),
            ],
            cwd=PROJECT_ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if completed.returncode:
            raise SystemExit(completed.returncode)
    if not skip_qd_fund_report:
        completed = subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8",
                project_arg(QD_FUND_REPORT_SCRIPT_PATH),
                "--db-path",
                project_arg(db_path),
                "--site-dir",
                project_arg(report_root / "basic_data"),
            ],
            cwd=PROJECT_ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if completed.returncode:
            raise SystemExit(completed.returncode)
    if build_fund_detail_artifacts:
        completed = subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8",
                project_arg(FUND_ENRICHMENT_SCRIPT_PATH),
                "--site-dir",
                project_arg(report_root / "basic_data"),
                "--fund-universe",
                "all-dict",
            ],
            cwd=PROJECT_ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if completed.returncode:
            raise SystemExit(completed.returncode)
        completed = subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8",
                project_arg(FUND_ECONOMIC_EXPOSURE_SCRIPT_PATH),
                "--db-path",
                project_arg(db_path),
                "--site-dir",
                project_arg(report_root / "basic_data"),
            ],
            cwd=PROJECT_ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if completed.returncode:
            raise SystemExit(completed.returncode)
    else:
        completed = subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8",
                project_arg(FUND_ECONOMIC_EXPOSURE_SCRIPT_PATH),
                "--db-path",
                project_arg(db_path),
                "--site-dir",
                project_arg(report_root / "basic_data"),
            ],
            cwd=PROJECT_ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if completed.returncode:
            raise SystemExit(completed.returncode)
        print(
            "[INFO] Minimal publish mode: skipped per-fund detail files and finalized the shared fund detail pack.",
            flush=True,
        )
    if not args.skip_field_dictionary:
        completed = subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8",
                project_arg(FIELD_DICTIONARY_SCRIPT_PATH),
                "--db",
                project_arg(db_path),
                "--site-data-dir",
                project_arg(report_root / "basic_data" / "data"),
            ],
            cwd=PROJECT_ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if completed.returncode:
            raise SystemExit(completed.returncode)
    if not args.skip_quality_gate:
        quality_command = [
            sys.executable,
            "-X",
            "utf8",
            project_arg(QUALITY_GATE_SCRIPT_PATH),
            "--db-path",
            project_arg(db_path),
            "--site-dir",
            project_arg(report_root / "basic_data"),
            "--fail-on-error",
        ]
        if args.minimal_publish_only:
            quality_command.append("--deployment-manifest-pending")
        completed = subprocess.run(
            quality_command,
            cwd=PROJECT_ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if completed.returncode:
            raise SystemExit(completed.returncode)
    if not args.skip_data_audit:
        completed = subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8",
                project_arg(DATA_AUDIT_SCRIPT_PATH),
                "--db-path",
                project_arg(db_path),
                "--site-dir",
                project_arg(report_root / "basic_data"),
                "--fail-on-error",
            ],
            cwd=PROJECT_ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if completed.returncode:
            raise SystemExit(completed.returncode)


if __name__ == "__main__":
    main()
