from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Emit environment assignments from the latest completed TTFund incremental summary."
    )
    parser.add_argument("--project-root", type=Path, default=next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file()))
    parser.add_argument("--device-id")
    parser.add_argument("--history-mode")
    parser.add_argument("--format", choices=("batch", "json"), default="batch")
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None


def iter_summary_paths(project_root: Path) -> list[Path]:
    root = project_root / "data" / "raw" / "ttfund" / "incremental_update_runs"
    if not root.exists():
        return []
    return sorted(root.glob("*/*/summary.json"), key=lambda path: path.stat().st_mtime, reverse=True)


def clean_batch_value(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).replace("\r", " ").replace("\n", " ").replace('"', "")
    return text


def batch_set(name: str, value: Any) -> str:
    return f'set "{name}={clean_batch_value(value)}"'


def main() -> int:
    args = parse_args()
    project_root = args.project_root.resolve()
    selected_path: Path | None = None
    selected_payload: dict[str, Any] | None = None

    for path in iter_summary_paths(project_root):
        payload = read_json(path)
        if not payload:
            continue
        if payload.get("state") != "completed":
            continue
        if args.device_id and payload.get("device_id") and str(payload.get("device_id")) != args.device_id:
            continue
        if args.history_mode and payload.get("history_mode") and str(payload.get("history_mode")) != args.history_mode:
            continue
        selected_path = path
        selected_payload = payload
        break

    if not selected_path or not selected_payload:
        result = {"found": False}
        if args.format == "json":
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(batch_set("TTFUND_SUMMARY_FOUND", "0"))
        return 0

    target_trade_date = (
        selected_payload.get("target_trade_date")
        or selected_payload.get("remote_max_trade_date")
        or selected_payload.get("latest_local_trade_date")
    )
    collect_run_id = selected_payload.get("collect_run_id")
    fast_post_eligible = bool(collect_run_id and target_trade_date)
    result = {
        "found": True,
        "summary_path": str(selected_path),
        "job_root": str(selected_path.parent),
        "incremental_job_id": selected_path.parent.name,
        "state": selected_payload.get("state"),
        "finished_at": selected_payload.get("finished_at"),
        "device_id": selected_payload.get("device_id"),
        "history_mode": selected_payload.get("history_mode"),
        "target_trade_date": target_trade_date,
        "latest_local_trade_date": selected_payload.get("latest_local_trade_date"),
        "remote_max_trade_date": selected_payload.get("remote_max_trade_date"),
        "collect_run_id": collect_run_id,
        "should_collect": selected_payload.get("should_collect"),
        "deploy_needs_export": selected_payload.get("deploy_needs_export"),
        "no_new_trade_date": selected_payload.get("no_new_trade_date"),
        "fast_post_eligible": fast_post_eligible,
    }

    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    print(batch_set("TTFUND_SUMMARY_FOUND", "1"))
    print(batch_set("TTFUND_SUMMARY_PATH", result["summary_path"]))
    print(batch_set("TTFUND_INCREMENTAL_JOB_ROOT", result["job_root"]))
    print(batch_set("TTFUND_INCREMENTAL_JOB_ID", result["incremental_job_id"]))
    print(batch_set("TTFUND_SUMMARY_STATE", result["state"]))
    print(batch_set("TTFUND_SUMMARY_FINISHED_AT", result["finished_at"]))
    print(batch_set("TTFUND_TARGET_TRADE_DATE", result["target_trade_date"]))
    print(batch_set("TTFUND_LATEST_LOCAL_TRADE_DATE", result["latest_local_trade_date"]))
    print(batch_set("TTFUND_REMOTE_MAX_TRADE_DATE", result["remote_max_trade_date"]))
    print(batch_set("TTFUND_COLLECT_RUN_ID", result["collect_run_id"]))
    print(batch_set("TTFUND_SHOULD_COLLECT", result["should_collect"]))
    print(batch_set("TTFUND_DEPLOY_NEEDS_EXPORT", result["deploy_needs_export"]))
    print(batch_set("TTFUND_NO_NEW_TRADE_DATE", result["no_new_trade_date"]))
    print(batch_set("TTFUND_FAST_POST_ELIGIBLE", "1" if fast_post_eligible else "0"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
