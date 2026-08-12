from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from drive_ttfund_app import summarize as summarize_app_results


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
sys.path.insert(0, str(PROJECT_ROOT / "节点脚本" / "_共享组件" / "python_src"))

from advisor_monitor.progress import ConsoleProgress  # noqa: E402


DRIVER = PROJECT_ROOT / "节点脚本" / "_共享组件" / "生产程序" / "drive_ttfund_app.py"
DEFAULT_ADB = PROJECT_ROOT / "tools" / "platform-tools" / "adb.exe"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Shard TTFund current-holding App capture across independent ADB devices."
    )
    parser.add_argument("--adb-path", type=Path, default=DEFAULT_ADB)
    parser.add_argument("--device", action="append", required=True, dest="devices")
    parser.add_argument("--strategy-file", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--python-exe", default=sys.executable)
    parser.add_argument("--detail-scan-swipes", type=int, default=1)
    parser.add_argument("--max-attempts", type=int, default=1)
    parser.add_argument("--retry-wait-ms", type=int, default=2500)
    parser.add_argument(
        "--soft-circuit-break-consecutive-incomplete-detail",
        type=int,
        default=0,
    )
    parser.add_argument("--soft-circuit-break-max-recoveries", type=int, default=2)
    parser.add_argument("--soft-circuit-recovery-wait-ms", type=int, default=8000)
    parser.add_argument("--progress-interval-sec", type=int, default=15)
    parser.add_argument("--capture-failures", action="store_true")
    parser.add_argument("--skip-existing-results", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def read_strategy_ids(path: Path) -> list[str]:
    strategy_ids: list[str] = []
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        strategy_id = raw_line.strip()
        if not strategy_id or strategy_id.startswith("#"):
            continue
        if not re.fullmatch(r"[A-Za-z0-9]+", strategy_id):
            raise ValueError(f"invalid strategy id: {strategy_id}")
        if strategy_id not in strategy_ids:
            strategy_ids.append(strategy_id)
    if not strategy_ids:
        raise ValueError("strategy file contains no ids")
    return strategy_ids


def unique_devices(values: list[str]) -> list[str]:
    devices: list[str] = []
    for value in values:
        device = value.strip()
        if device and device not in devices:
            devices.append(device)
    if not devices:
        raise ValueError("no devices provided")
    return devices


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._") or "device"


def shard_ids(strategy_ids: list[str], devices: list[str]) -> dict[str, list[str]]:
    active_devices = devices[: min(len(devices), len(strategy_ids))]
    shards = {device: [] for device in active_devices}
    for index, strategy_id in enumerate(strategy_ids):
        shards[active_devices[index % len(active_devices)]].append(strategy_id)
    return shards


def read_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None


def build_child_command(args: argparse.Namespace, device: str, shard_file: Path, shard_dir: Path) -> list[str]:
    command = [
        str(args.python_exe),
        "-u",
        str(DRIVER),
        "--adb-path",
        str(args.adb_path),
        "--device-id",
        device,
        "--strategy-file",
        str(shard_file),
        "--run-dir",
        str(shard_dir),
        "--skip-history",
        "--current-holding-fast",
        "--detail-scan-swipes",
        str(max(args.detail_scan_swipes, 1)),
        "--max-attempts",
        str(max(args.max_attempts, 1)),
        "--retry-wait-ms",
        str(max(args.retry_wait_ms, 0)),
        "--soft-circuit-break-consecutive-incomplete-detail",
        str(max(args.soft_circuit_break_consecutive_incomplete_detail, 0)),
        "--soft-circuit-break-max-recoveries",
        str(max(args.soft_circuit_break_max_recoveries, 0)),
        "--soft-circuit-recovery-wait-ms",
        str(max(args.soft_circuit_recovery_wait_ms, 0)),
    ]
    if args.capture_failures:
        command.append("--capture-failures")
    if args.skip_existing_results:
        command.append("--skip-existing-results")
    return command


def aggregate_progress(shard_rows: list[dict[str, Any]], requested_total: int) -> dict[str, Any]:
    completed = 0
    failures = 0
    fast_path = 0
    fallbacks = 0
    elapsed_weighted = 0.0
    for shard in shard_rows:
        summary = read_json(shard["run_dir"] / "summary.json") or {}
        count = int(summary.get("strategy_total") or 0)
        completed += count
        failures += int(summary.get("current_holding_missing_total") or 0)
        fast_path += int(summary.get("fast_path_ok_total") or 0)
        fallbacks += int(summary.get("full_fallback_total") or 0)
        elapsed_weighted += float(summary.get("avg_elapsed_sec") or 0.0) * count
    avg_elapsed = elapsed_weighted / completed if completed else 0.0
    device_total = max(len(shard_rows), 1)
    eta_seconds = max(requested_total - completed, 0) * avg_elapsed / device_total if avg_elapsed else None
    return {
        "event": "sharded_progress",
        "progress": f"{completed}/{requested_total}",
        "device_total": len(shard_rows),
        "failure_total": failures,
        "fast_path_ok_total": fast_path,
        "full_fallback_total": fallbacks,
        "avg_elapsed_sec": round(avg_elapsed, 2),
        "eta_sec": round(eta_seconds, 1) if eta_seconds is not None else None,
    }


def summarize_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    summary = summarize_app_results(results)
    summary["requested_total"] = len(results)
    return summary


def main() -> None:
    args = parse_args()
    strategy_ids = read_strategy_ids(args.strategy_file)
    devices = unique_devices(args.devices)
    shards = shard_ids(strategy_ids, devices)
    progress = ConsoleProgress("天天投顾当前仓位并行采集", len(strategy_ids))
    progress.emit(0, success=0, failed=0, extra=f"设备数 {len(shards)}")
    args.run_dir.mkdir(parents=True, exist_ok=True)
    shard_root = args.run_dir / "shards"
    shard_root.mkdir(parents=True, exist_ok=True)

    shard_rows: list[dict[str, Any]] = []
    for index, (device, ids) in enumerate(shards.items(), start=1):
        shard_dir = shard_root / f"{index:02d}_{safe_name(device)}"
        shard_dir.mkdir(parents=True, exist_ok=True)
        shard_file = shard_dir / "strategy_ids.txt"
        shard_file.write_text("\n".join(ids) + "\n", encoding="utf-8")
        shard_rows.append(
            {
                "index": index,
                "device_id": device,
                "strategy_total": len(ids),
                "strategy_ids": ids,
                "strategy_file": shard_file,
                "run_dir": shard_dir,
                "stdout_log": shard_dir / "stdout.log",
                "stderr_log": shard_dir / "stderr.log",
            }
        )

    plan = {
        "state": "dry_run" if args.dry_run else "ready",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "strategy_total": len(strategy_ids),
        "device_total": len(shard_rows),
        "adb_path": str(args.adb_path),
        "run_dir": str(args.run_dir),
        "shards": [
            {
                "index": row["index"],
                "device_id": row["device_id"],
                "strategy_total": row["strategy_total"],
                "strategy_file": str(row["strategy_file"]),
                "run_dir": str(row["run_dir"]),
            }
            for row in shard_rows
        ],
    }
    (args.run_dir / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.dry_run:
        print(json.dumps(plan, ensure_ascii=False, indent=2), flush=True)
        return

    running: list[dict[str, Any]] = []
    try:
        for row in shard_rows:
            command = build_child_command(args, row["device_id"], row["strategy_file"], row["run_dir"])
            stdout_handle = row["stdout_log"].open("w", encoding="utf-8")
            stderr_handle = row["stderr_log"].open("w", encoding="utf-8")
            process = subprocess.Popen(command, stdout=stdout_handle, stderr=stderr_handle)
            running.append(
                {
                    **row,
                    "command": command,
                    "process": process,
                    "stdout_handle": stdout_handle,
                    "stderr_handle": stderr_handle,
                }
            )

        interval = max(args.progress_interval_sec, 1)
        while any(row["process"].poll() is None for row in running):
            progress_row = aggregate_progress(shard_rows, len(strategy_ids))
            completed = int(str(progress_row["progress"]).split("/", 1)[0])
            failure_total = int(progress_row["failure_total"])
            progress.emit(
                completed,
                success=max(0, completed - failure_total),
                failed=failure_total,
                extra=(
                    f"设备数 {progress_row['device_total']} | "
                    f"快速采集 {progress_row['fast_path_ok_total']} | "
                    f"完整页面兜底 {progress_row['full_fallback_total']}"
                ),
            )
            time.sleep(interval)
    except KeyboardInterrupt:
        for row in running:
            if row["process"].poll() is None:
                row["process"].terminate()
        raise
    except Exception:
        for row in running:
            if row["process"].poll() is None:
                row["process"].terminate()
        raise
    finally:
        for row in running:
            row["process"].wait()
            row["stdout_handle"].close()
            row["stderr_handle"].close()

    merged_by_id: dict[str, dict[str, Any]] = {}
    shard_status: list[dict[str, Any]] = []
    for row in running:
        child_results = read_json(row["run_dir"] / "results.json") or []
        expected_ids = set(row["strategy_ids"])
        for result in child_results if isinstance(child_results, list) else []:
            strategy_id = str(result.get("strategy_id") or "")
            if strategy_id not in expected_ids:
                continue
            if strategy_id in merged_by_id:
                raise RuntimeError(f"duplicate sharded result: {strategy_id}")
            merged_by_id[strategy_id] = result
        shard_status.append(
            {
                "index": row["index"],
                "device_id": row["device_id"],
                "strategy_total": row["strategy_total"],
                "returncode": row["process"].returncode,
                "result_total": len(child_results) if isinstance(child_results, list) else 0,
                "run_dir": str(row["run_dir"]),
                "stdout_log": str(row["stdout_log"]),
                "stderr_log": str(row["stderr_log"]),
            }
        )

    missing_ids = [strategy_id for strategy_id in strategy_ids if strategy_id not in merged_by_id]
    results = [merged_by_id[strategy_id] for strategy_id in strategy_ids if strategy_id in merged_by_id]
    summary = summarize_results(results)
    summary.update(
        {
            "state": "completed" if not missing_ids and all(row["returncode"] == 0 for row in shard_status) else "failed",
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "requested_total": len(strategy_ids),
            "merged_result_total": len(results),
            "missing_result_total": len(missing_ids),
            "missing_result_ids": missing_ids,
            "device_total": len(shard_rows),
            "device_ids": [row["device_id"] for row in shard_rows],
            "shards": shard_status,
            "run_dir": str(args.run_dir),
        }
    )
    progress.emit(
        len(results),
        success=int(summary.get("holding_info_ok_total") or 0),
        failed=int(summary.get("current_holding_missing_total") or 0) + len(missing_ids),
        extra=f"设备数 {len(shard_status)} | 缺失结果 {len(missing_ids)}",
    )
    (args.run_dir / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.run_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    if summary["state"] != "completed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
