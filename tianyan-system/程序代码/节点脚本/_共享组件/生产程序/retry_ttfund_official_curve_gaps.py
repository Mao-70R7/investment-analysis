from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DRIVER = PROJECT_ROOT / "节点脚本" / "_共享组件" / "生产程序" / "drive_ttfund_app.py"
COLLECTOR = PROJECT_ROOT / "节点脚本" / "_共享组件" / "生产程序" / "collect_ttfund_official_performance_curve.py"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Retry active official-curve gaps after targeted App visits.")
    parser.add_argument("--summary-path", type=Path, required=True)
    parser.add_argument("--db-path", type=Path, default=PROJECT_ROOT / "data" / "analysis_zh_current.sqlite")
    parser.add_argument("--adb-path", required=True)
    parser.add_argument("--primary-device-id", required=True)
    parser.add_argument("--fallback-device-id")
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--python-exe", default=sys.executable)
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root is not an object: {path}")
    return payload


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def write_csv_rows(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    with temp.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    temp.replace(path)


def strategy_id(row: dict[str, Any]) -> str:
    return str(row.get("渠道策略ID") or row.get("source_strategy_id") or row.get("strategy_id") or "").strip()


def active_missing_ids(rows: list[dict[str, str]]) -> list[str]:
    return sorted(
        {
            strategy_id(row)
            for row in rows
            if strategy_id(row) and str(row.get("quality_scope") or "active").strip().lower() == "active"
        }
    )


def missing_gap_types(rows: list[dict[str, str]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for row in rows:
        source_id = strategy_id(row)
        if not source_id:
            continue
        result[source_id] = str(row.get("缺口类型") or "策略曲线缺失").strip() or "策略曲线缺失"
    return result


def recovered_ids_for_gaps(
    rows: list[dict[str, Any]],
    gap_types: dict[str, str],
) -> set[str]:
    by_id: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        source_id = str(row.get("source_strategy_id") or "").strip()
        if source_id in gap_types:
            by_id.setdefault(source_id, []).append(row)
    recovered: set[str] = set()
    for source_id, gap_type in gap_types.items():
        strategy_rows = by_id.get(source_id) or []
        if not strategy_rows:
            continue
        if not gap_type.startswith("基准曲线"):
            recovered.add(source_id)
            continue
        latest_strategy_date = max(str(row.get("trade_date") or "") for row in strategy_rows)
        benchmark_dates = [
            str(row.get("trade_date") or "")
            for row in strategy_rows
            if row.get("benchmark_return") is not None
        ]
        if benchmark_dates and max(benchmark_dates) >= latest_strategy_date:
            recovered.add(source_id)
    return recovered


def probe_adb_device(adb_path: str, device_id: str) -> dict[str, Any]:
    if not device_id:
        return {"state": "not_configured", "ready": False, "device_id": ""}
    command = [adb_path, "-s", device_id, "get-state"]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
        )
        detail = (completed.stdout + completed.stderr).strip()
        ready = completed.returncode == 0 and completed.stdout.strip() == "device"
        return {
            "state": "ready" if ready else "unavailable",
            "ready": ready,
            "device_id": device_id,
            "returncode": int(completed.returncode),
            "detail": detail,
        }
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "state": "unavailable",
            "ready": False,
            "device_id": device_id,
            "error": f"{type(exc).__name__}: {exc}",
        }


def run_device_visit(args: argparse.Namespace, *, device_id: str, ids: list[str], label: str) -> dict[str, Any]:
    if not ids:
        return {"state": "skipped", "target_total": 0, "success_total": 0, "failed_ids": []}
    device_dir = args.run_dir / label
    strategy_file = device_dir / "strategy_ids.txt"
    device_dir.mkdir(parents=True, exist_ok=True)
    strategy_file.write_text("\n".join(ids) + "\n", encoding="utf-8")
    command = [
        args.python_exe,
        "-X",
        "utf8",
        str(DRIVER),
        "--adb-path",
        args.adb_path,
        "--device-id",
        device_id,
        "--strategy-file",
        str(strategy_file),
        "--run-dir",
        str(device_dir),
        "--skip-history",
        "--detail-scan-swipes",
        "1",
        "--max-attempts",
        "2",
        "--retry-wait-ms",
        "5000",
        "--capture-failures",
        "--keep-run-cache",
    ]
    print(f"[官方曲线][{label}] App 定向刷新 {len(ids)} 个策略，设备={device_id}", flush=True)
    completed = subprocess.run(command, cwd=PROJECT_ROOT, check=False)
    results_path = device_dir / "results.json"
    rows = []
    if results_path.exists():
        try:
            payload = json.loads(results_path.read_text(encoding="utf-8-sig"))
            rows = payload if isinstance(payload, list) else []
        except (OSError, json.JSONDecodeError):
            rows = []
    success_ids = {
        str(row.get("strategy_id") or "").strip()
        for row in rows
        if isinstance(row, dict) and row.get("detail_ok")
    }
    failed_ids = sorted(set(ids) - success_ids)
    return {
        "state": "completed" if completed.returncode == 0 else "failed_nonblocking",
        "device_id": device_id,
        "target_total": len(ids),
        "success_total": len(success_ids),
        "failed_total": len(failed_ids),
        "failed_ids": failed_ids,
        "returncode": int(completed.returncode),
        "results_path": str(results_path),
    }


def run_curve_retry(
    args: argparse.Namespace,
    *,
    ids: list[str],
    label: str,
    parent_run_id: str,
    gap_types: dict[str, str],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not ids:
        return {"state": "skipped", "target_total": 0, "recovered_ids": []}, []
    run_id = f"{parent_run_id}_{label}_{datetime.now().strftime('%H%M%S')}"
    command = [
        args.python_exe,
        "-X",
        "utf8",
        str(COLLECTOR),
        "--db-path",
        str(args.db_path.resolve()),
        "--strategy-ids",
        *ids,
        "--workers",
        "2",
        "--timeout",
        "30",
        "--retries",
        "4",
        "--retry-failed-rounds",
        "1",
        "--run-id",
        run_id,
        "--min-success-ratio",
        "0",
        "--min-latest-ratio",
        "0",
        "--min-benchmark-latest-ratio",
        "0",
        "--max-source-lag-business-days",
        "9999",
    ]
    print(f"[官方曲线][{label}] 低并发接口复核 {len(ids)} 个策略。", flush=True)
    completed = subprocess.run(command, cwd=PROJECT_ROOT, check=False)
    matches = sorted(
        (PROJECT_ROOT / "outputs" / "ttfund_official_performance_curve").glob(f"*/{run_id}/official_curve_summary.json"),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    retry_summary = read_json(matches[0]) if matches else {}
    normalized_path = Path(str(retry_summary.get("normalized_strategy_performance_daily") or ""))
    rows: list[dict[str, Any]] = []
    if normalized_path.is_file():
        with normalized_path.open("r", encoding="utf-8-sig") as handle:
            rows = [json.loads(line) for line in handle if line.strip()]
    recovered_ids = sorted(recovered_ids_for_gaps(rows, gap_types))
    result = {
        "state": "completed" if completed.returncode == 0 else "failed_nonblocking",
        "target_total": len(ids),
        "recovered_total": len(recovered_ids),
        "recovered_ids": recovered_ids,
        "returncode": int(completed.returncode),
        "summary_path": str(matches[0]) if matches else None,
        "normalized_path": str(normalized_path) if normalized_path.is_file() else None,
    }
    return result, rows


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    with temp.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    temp.replace(path)


def merge_rows(existing: list[dict[str, Any]], recovered: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged = {
        (str(row.get("source_strategy_id") or ""), str(row.get("trade_date") or "")): dict(row)
        for row in existing
        if row.get("source_strategy_id") and row.get("trade_date")
    }
    for row in recovered:
        key = (str(row.get("source_strategy_id") or ""), str(row.get("trade_date") or ""))
        if all(key):
            merged[key] = dict(row)
    return [merged[key] for key in sorted(merged)]


def main() -> int:
    args = parse_args()
    args.run_dir = args.run_dir.resolve()
    args.run_dir.mkdir(parents=True, exist_ok=True)
    summary = read_json(args.summary_path.resolve())
    missing_path = Path(str(summary.get("missing_csv") or ""))
    missing_rows = read_csv_rows(missing_path)
    initial_ids = active_missing_ids(missing_rows)
    initial_gap_types = missing_gap_types(missing_rows)
    retry_summary_path = args.run_dir / "official_curve_device_retry_summary.json"
    if not initial_ids:
        payload = {
            "state": "not_needed",
            "initial_active_missing_total": 0,
            "final_active_missing_total": 0,
            "final_active_missing_ids": [],
        }
        atomic_write_json(retry_summary_path, payload)
        summary["device_retry"] = payload
        summary["device_retry_summary_path"] = str(retry_summary_path)
        atomic_write_json(args.summary_path.resolve(), summary)
        return 0

    parent_run_id = str(summary.get("run_id") or "official_curve")
    primary_visit = run_device_visit(
        args,
        device_id=args.primary_device_id,
        ids=initial_ids,
        label="primary_device_retry",
    )
    primary_curve, primary_rows = run_curve_retry(
        args,
        ids=initial_ids,
        label="primary_retry",
        parent_run_id=parent_run_id,
        gap_types={source_id: initial_gap_types[source_id] for source_id in initial_ids},
    )
    recovered_ids = set(primary_curve.get("recovered_ids") or [])
    remaining = sorted(set(initial_ids) - recovered_ids)

    fallback_visit: dict[str, Any] = {"state": "not_configured", "target_total": 0}
    fallback_probe: dict[str, Any] = {"state": "not_configured", "ready": False}
    fallback_curve: dict[str, Any] = {"state": "not_needed", "target_total": 0, "recovered_ids": []}
    fallback_rows: list[dict[str, Any]] = []
    fallback_id = str(args.fallback_device_id or "").strip()
    if remaining and fallback_id and fallback_id != args.primary_device_id:
        fallback_probe = probe_adb_device(args.adb_path, fallback_id)
        if fallback_probe.get("ready"):
            fallback_visit = run_device_visit(
                args,
                device_id=fallback_id,
                ids=remaining,
                label="physical_device_retry",
            )
        else:
            fallback_visit = {
                "state": "unavailable",
                "device_id": fallback_id,
                "target_total": len(remaining),
                "success_total": 0,
                "failed_total": len(remaining),
                "failed_ids": remaining,
                "detail": fallback_probe.get("detail") or fallback_probe.get("error"),
            }
            print(
                f"[官方曲线][physical_device_retry] 实体手机 {fallback_id} 当前不可用，"
                f"跳过 {len(remaining)} 个策略的设备访问并保留缺口。",
                flush=True,
            )
        fallback_curve, fallback_rows = run_curve_retry(
            args,
            ids=remaining,
            label="physical_retry",
            parent_run_id=parent_run_id,
            gap_types={source_id: initial_gap_types[source_id] for source_id in remaining},
        )
        recovered_ids.update(fallback_curve.get("recovered_ids") or [])
        remaining = sorted(set(initial_ids) - recovered_ids)

    normalized_path = Path(str(summary.get("normalized_strategy_performance_daily") or ""))
    merged_total: int | None = None
    if recovered_ids and normalized_path:
        merged_rows = merge_rows(read_jsonl(normalized_path), primary_rows + fallback_rows)
        write_jsonl(normalized_path, merged_rows)
        merged_total = len(merged_rows)

    if missing_rows:
        fieldnames = list(missing_rows[0])
        write_csv_rows(
            missing_path,
            [row for row in missing_rows if strategy_id(row) not in recovered_ids],
            fieldnames,
        )

    recovered_total = len(recovered_ids)
    recovered_rows = primary_rows + fallback_rows
    recovered_point_counts: dict[str, int] = {}
    recovered_latest_dates: dict[str, str] = {}
    recovered_benchmark_point_counts: dict[str, int] = {}
    recovered_latest_benchmark_dates: dict[str, str] = {}
    for row in recovered_rows:
        source_id = str(row.get("source_strategy_id") or "").strip()
        trade_date = str(row.get("trade_date") or "").strip()
        if source_id not in recovered_ids:
            continue
        recovered_point_counts[source_id] = recovered_point_counts.get(source_id, 0) + 1
        if trade_date and trade_date > recovered_latest_dates.get(source_id, ""):
            recovered_latest_dates[source_id] = trade_date
        if row.get("benchmark_return") is not None:
            recovered_benchmark_point_counts[source_id] = recovered_benchmark_point_counts.get(source_id, 0) + 1
            if trade_date and trade_date > recovered_latest_benchmark_dates.get(source_id, ""):
                recovered_latest_benchmark_dates[source_id] = trade_date
    coverage_path = Path(str(summary.get("coverage_csv") or ""))
    coverage_rows = read_csv_rows(coverage_path)
    if coverage_rows:
        for row in coverage_rows:
            source_id = strategy_id(row)
            if source_id not in recovered_ids:
                continue
            row["采集成功"] = "1"
            row["官方曲线点数"] = str(recovered_point_counts.get(source_id, 0))
            row["官方曲线最晚日期"] = recovered_latest_dates.get(source_id, "")
            row["基准曲线点数"] = str(recovered_benchmark_point_counts.get(source_id, 0))
            row["基准曲线最晚日期"] = recovered_latest_benchmark_dates.get(source_id, "")
            row["缺口类型"] = ""
            row["失败原因"] = ""
        write_csv_rows(coverage_path, coverage_rows, list(coverage_rows[0]))

    curve_recovered_ids = {
        source_id for source_id in recovered_ids if not initial_gap_types.get(source_id, "").startswith("基准曲线")
    }
    benchmark_recovered_ids = set(recovered_ids) - curve_recovered_ids
    benchmark_complete_recovered_ids = {
        source_id
        for source_id in recovered_ids
        if recovered_latest_benchmark_dates.get(source_id, "") >= recovered_latest_dates.get(source_id, "")
    }
    summary["curve_strategy_total"] = int(summary.get("curve_strategy_total") or 0) + len(curve_recovered_ids)
    summary["missing_strategy_total"] = max(0, int(summary.get("missing_strategy_total") or 0) - recovered_total)
    summary["curve_missing_strategy_total"] = max(
        0, int(summary.get("curve_missing_strategy_total") or 0) - len(curve_recovered_ids)
    )
    summary["benchmark_gap_strategy_total"] = max(
        0, int(summary.get("benchmark_gap_strategy_total") or 0) - len(benchmark_recovered_ids)
    )
    summary["eligible_success_total"] = int(summary.get("eligible_success_total") or 0) + len(curve_recovered_ids)
    summary["latest_strategy_total"] = int(summary.get("latest_strategy_total") or 0) + len(curve_recovered_ids)
    summary["latest_benchmark_total"] = int(summary.get("latest_benchmark_total") or 0) + len(
        benchmark_complete_recovered_ids
    )
    summary["daily_rows_total"] = int(summary.get("daily_rows_total") or 0) + len(recovered_rows)
    summary["fetched_daily_rows_total"] = int(summary.get("fetched_daily_rows_total") or 0) + len(recovered_rows)
    if merged_total is not None:
        summary["merged_daily_rows_total"] = merged_total
    eligible_total = int(summary.get("quality_eligible_strategy_total") or 0)
    if eligible_total:
        summary["success_ratio"] = min(1.0, summary["eligible_success_total"] / eligible_total)
        summary["latest_ratio"] = min(1.0, summary["latest_strategy_total"] / eligible_total)
        summary["benchmark_latest_ratio"] = min(1.0, summary["latest_benchmark_total"] / eligible_total)
    payload = {
        "state": "completed_with_gaps" if remaining else "completed",
        "initial_active_missing_total": len(initial_ids),
        "initial_active_missing_ids": initial_ids,
        "primary_device_visit": primary_visit,
        "primary_curve_retry": primary_curve,
        "fallback_device_probe": fallback_probe,
        "fallback_device_visit": fallback_visit,
        "fallback_curve_retry": fallback_curve,
        "recovered_total": recovered_total,
        "recovered_ids": sorted(recovered_ids),
        "final_active_missing_total": len(remaining),
        "final_active_missing_ids": remaining,
        "normalized_path": str(normalized_path),
    }
    atomic_write_json(retry_summary_path, payload)
    summary["device_retry"] = payload
    summary["device_retry_summary_path"] = str(retry_summary_path)
    summary["final_active_missing_total"] = len(remaining)
    summary["final_active_missing_ids"] = remaining
    atomic_write_json(args.summary_path.resolve(), summary)
    collection_matches = list(
        (PROJECT_ROOT / "data" / "normalized" / "ttfund" / "collection_summary").glob(
            f"*/{parent_run_id}.json"
        )
    )
    for collection_path in collection_matches:
        try:
            collection_summary = read_json(collection_path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        collection_summary["official_curve"] = summary
        atomic_write_json(collection_path, collection_summary)
    print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
