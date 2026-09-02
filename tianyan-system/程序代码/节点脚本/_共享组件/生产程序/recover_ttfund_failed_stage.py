from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
PROGRAM_ROOT = Path(__file__).resolve().parent
EXIT_SOURCE_NOT_READY = 20
EXIT_NOT_RECOVERABLE = 21
EXIT_RETRY_DEVICE = 22
EXIT_STAGE_RETRY_FAILED = 23


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Classify and recover the latest failed TTFund incremental stage.")
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--summary", type=Path)
    parser.add_argument("--python-exe", default=sys.executable)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--max-age-hours", type=int, default=8)
    parser.add_argument("--classify-only", action="store_true")
    return parser.parse_args()


def read_json(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def write_json(path: Path, payload: dict[str, Any]) -> None:
    temp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def latest_failed_summary(project_root: Path, max_age_hours: int) -> Path | None:
    root = project_root / "data" / "raw" / "ttfund" / "incremental_update_runs"
    if not root.exists():
        return None
    cutoff = datetime.now() - timedelta(hours=max(1, max_age_hours))
    candidates: list[Path] = []
    for path in root.glob("*/*/summary.json"):
        if datetime.fromtimestamp(path.stat().st_mtime) < cutoff:
            continue
        payload = read_json(path)
        if payload and payload.get("state") == "failed":
            candidates.append(path)
    return max(candidates, key=lambda item: item.stat().st_mtime_ns) if candidates else None


def classify(summary: dict[str, Any]) -> tuple[str, int]:
    failed_stage = str(summary.get("failed_stage") or "")
    failure_class = str(summary.get("failure_class") or "")
    if failed_stage == "03b_official_performance_curve" and not failure_class:
        failure_class = "legacy_official_curve_failure"
    if failure_class == "device_or_login":
        return "retry_full_after_device_preflight", EXIT_RETRY_DEVICE
    if failed_stage != "03b_official_performance_curve":
        return "stop_nonrecoverable_stage", EXIT_NOT_RECOVERABLE
    if failure_class == "source_not_ready":
        return "wait_for_source_disclosure", EXIT_SOURCE_NOT_READY
    if failure_class in {
        "data_quality",
        "source_unavailable",
        "upstream_or_collection",
        "legacy_official_curve_failure",
    }:
        return "retry_official_curve_stage", 0
    return "stop_nonrecoverable_stage", EXIT_NOT_RECOVERABLE


def update_recovered_summary(summary_path: Path, summary: dict[str, Any], official: dict[str, Any]) -> None:
    prior_failure = {
        "failed_stage": summary.get("failed_stage"),
        "failed_stage_exit_code": summary.get("failed_stage_exit_code"),
        "failure_class": summary.get("failure_class"),
        "error": summary.get("error"),
        "official_curve_state": summary.get("official_curve_state"),
    }
    summary.update(
        {
            "finished_at": datetime.now().isoformat(timespec="seconds"),
            "state": "completed",
            "recovery_state": "completed",
            "recovered_stage": "03b_official_performance_curve",
            "prior_failure": prior_failure,
            "failed_stage": None,
            "failed_stage_exit_code": None,
            "failure_class": None,
            "error": None,
            "error_detail": None,
            "official_curve_state": official.get("state"),
            "official_curve_source_effective_date": official.get("source_effective_date"),
            "official_curve_source_lag_business_days": official.get("source_lag_business_days"),
            "official_curve_quote_fallback_total": official.get("quote_fallback_strategy_total"),
        }
    )
    write_json(summary_path, summary)
    status_path = Path(str(summary.get("status_path") or ""))
    if status_path.is_file():
        status_payload = read_json(status_path) or {}
        status_payload.update(
            {
                "updated_at": datetime.now().isoformat(timespec="seconds"),
                "stage": "done",
                "state": "completed",
                "message": "official performance curve stage recovered without replaying prior stages",
            }
        )
        write_json(status_path, status_payload)


def main() -> int:
    args = parse_args()
    project_root = args.project_root.resolve()
    summary_path = args.summary
    if summary_path and not summary_path.is_absolute():
        summary_path = project_root / summary_path
    summary_path = summary_path or latest_failed_summary(project_root, args.max_age_hours)
    summary = read_json(summary_path)
    if summary_path is None or summary is None:
        print("[RECOVERY] action=stop reason=current_failed_ttfund_summary_not_found", flush=True)
        return EXIT_NOT_RECOVERABLE

    action, classification_exit = classify(summary)
    print(
        f"[RECOVERY] action={action} failure_class={summary.get('failure_class')} "
        f"failed_stage={summary.get('failed_stage')} summary={summary_path}",
        flush=True,
    )
    if args.classify_only or classification_exit != 0:
        return classification_exit

    collect_run_id = str(summary.get("collect_run_id") or "").strip()
    if not collect_run_id:
        print("[RECOVERY] action=stop reason=collect_run_id_missing", flush=True)
        return EXIT_NOT_RECOVERABLE
    command = [
        args.python_exe,
        "-X",
        "utf8",
        "-u",
        str(PROGRAM_ROOT / "collect_ttfund_official_performance_curve.py"),
        "--run-id",
        collect_run_id,
        "--workers",
        str(max(1, args.workers)),
        "--retries",
        str(max(0, args.retries)),
        "--retry-failed-rounds",
        "1",
        "--auto-incremental",
        "--overlap-days",
        "3",
        "--full-history-gap-days",
        "4",
        "--merge-existing-run",
    ]
    target_date = str(summary.get("target_trade_date") or "").strip()
    if target_date:
        command.extend(["--expected-latest-date", target_date])
    job_root = Path(str(summary.get("job_root") or summary_path.parent))
    log_path = job_root / "03b_official_performance_curve_recovery.log"
    print(f"[RECOVERY] retry_stage=03b_official_performance_curve log={log_path}", flush=True)
    with log_path.open("a", encoding="utf-8") as log_handle:
        process = subprocess.Popen(
            command,
            cwd=project_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            text = line.rstrip("\r\n")
            log_handle.write(text + "\n")
            log_handle.flush()
            if "[PROGRESS]" in text or "error" in text.lower() or "failed" in text.lower():
                print(text, flush=True)
        returncode = process.wait()

    official_path_text = str(summary.get("official_curve_summary_path") or "").strip()
    official_path = Path(official_path_text) if official_path_text else None
    if not official_path or not official_path.exists():
        matches = list((project_root / "outputs" / "ttfund_official_performance_curve").rglob("official_curve_summary.json"))
        matches = [path for path in matches if path.parent.name == collect_run_id]
        official_path = max(matches, key=lambda item: item.stat().st_mtime_ns) if matches else None
    official = read_json(official_path)
    if returncode != 0 or not official or not str(official.get("state") or "").startswith("ready"):
        failure_class = str((official or {}).get("failure_class") or "stage_retry_failed")
        print(
            f"[RECOVERY] failed exit_code={returncode} failure_class={failure_class} log={log_path}",
            flush=True,
        )
        return EXIT_SOURCE_NOT_READY if failure_class == "source_not_ready" else EXIT_STAGE_RETRY_FAILED

    summary["official_curve_summary_path"] = str(official_path)
    update_recovered_summary(summary_path, summary, official)
    print(
        f"[RECOVERY] completed source_effective_date={official.get('source_effective_date')} "
        f"source_lag_business_days={official.get('source_lag_business_days')}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
