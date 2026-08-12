from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
sys.path.insert(0, str(PROJECT_ROOT / "节点脚本" / "_共享组件" / "生产程序"))

from runtime_workspace_cli import adb_device_health  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate the physical phone for the complete daily advisor update.")
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--adb-path", default="")
    parser.add_argument("--physical-device-id", default="")
    parser.add_argument("--device-type", choices=("auto", "physical"), default="physical")
    parser.add_argument("--preflight-attempts", type=int, default=3)
    parser.add_argument("--preflight-retry-seconds", type=float, default=5.0)
    parser.add_argument("--output-path", type=Path, required=True)
    return parser.parse_args()


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def resolve_adb(project_root: Path, requested: str) -> Path:
    candidates = [
        requested,
        os.environ.get("ADVISOR_ADB_EXE") or "",
        str(project_root / "tools" / "platform-tools" / "adb.exe"),
        shutil.which("adb") or "",
    ]
    for value in candidates:
        if not value:
            continue
        path = Path(value).expanduser()
        if path.is_file():
            return path.resolve()
    raise FileNotFoundError("ADB executable was not found in arguments, environment, project tools, or PATH")


def compact_attempt(attempt: dict[str, Any]) -> dict[str, Any]:
    return {
        "deviceType": attempt.get("deviceType"),
        "ready": bool(attempt.get("ready")),
        "deviceId": attempt.get("deviceId") or attempt.get("device_id"),
        "adbPath": attempt.get("adbPath") or attempt.get("adb_path"),
        "stateReady": attempt.get("stateReady"),
        "appInstalled": attempt.get("appInstalled"),
        "cacheAccessible": attempt.get("cacheAccessible"),
        "loginCacheEvidenceReady": attempt.get("loginCacheEvidenceReady"),
        "isEmulator": attempt.get("isEmulator"),
        "error": attempt.get("error"),
        "blockedReasons": attempt.get("blockedReasons") or attempt.get("blocked_reasons"),
    }


def discover_online_device_ids(adb_path: Path) -> list[str]:
    completed = subprocess.run(
        [str(adb_path), "devices", "-l"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stdout + completed.stderr).strip()
        raise RuntimeError(f"adb devices failed: {detail or completed.returncode}")
    device_ids: list[str] = []
    for line in completed.stdout.splitlines()[1:]:
        parts = line.strip().split()
        if len(parts) < 2 or parts[1] != "device":
            continue
        serial = parts[0].strip()
        if serial and serial not in device_ids:
            device_ids.append(serial)
    return device_ids


def check_physical(
    adb_path: Path,
    serial: str,
    *,
    attempts: int = 3,
    retry_wait_seconds: float = 5.0,
) -> dict[str, Any]:
    if not serial:
        return {
            "deviceType": "physical",
            "ready": False,
            "error": "physical device ID is not configured",
            "preflightAttemptCount": 0,
        }

    attempt_total = max(1, attempts)
    history: list[dict[str, Any]] = []
    result: dict[str, Any] = {}
    for index in range(1, attempt_total + 1):
        # Selection is a read-only health check. The collection node is responsible
        # for launching the app, which avoids opening it once during every preflight.
        result = adb_device_health(adb_path, serial, launch=False)
        result["deviceType"] = "physical"
        history.append(compact_attempt(result))
        if result.get("ready") or result.get("isEmulator"):
            break
        if index < attempt_total and retry_wait_seconds > 0:
            time.sleep(retry_wait_seconds)

    result["preflightAttemptCount"] = len(history)
    result["preflightHistory"] = history
    return result


def select_device(
    adb_path: Path,
    *,
    physical_device_id: str,
    preflight_attempts: int = 3,
    preflight_retry_seconds: float = 5.0,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], list[str]]:
    priority = ["physical"]
    attempts: list[dict[str, Any]] = []

    if physical_device_id:
        print(f"[设备预检] 正在检查配置真机 {physical_device_id}...", flush=True)
        try:
            configured = check_physical(
                adb_path,
                physical_device_id,
                attempts=preflight_attempts,
                retry_wait_seconds=preflight_retry_seconds,
            )
        except (OSError, RuntimeError) as exc:
            configured = {
                "deviceType": "physical",
                "deviceId": physical_device_id,
                "ready": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
        configured["selectionMethod"] = "configured"
        attempts.append(configured)
        print(f"[设备预检] 配置真机: {'可用' if configured.get('ready') else '不可用'}", flush=True)
        if configured.get("ready"):
            return configured, attempts, priority
        print(json.dumps(compact_attempt(configured), ensure_ascii=False), flush=True)

    try:
        discovered_ids = [serial for serial in discover_online_device_ids(adb_path) if serial != physical_device_id]
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        attempts.append(
            {
                "deviceType": "physical",
                "ready": False,
                "selectionMethod": "auto_discovery",
                "error": f"{type(exc).__name__}: {exc}",
            }
        )
        return None, attempts, priority

    ready_candidates: list[dict[str, Any]] = []
    for serial in discovered_ids:
        print(f"[设备自动发现] 正在验证在线设备 {serial}...", flush=True)
        try:
            candidate = check_physical(
                adb_path,
                serial,
                attempts=1,
                retry_wait_seconds=0,
            )
        except (OSError, RuntimeError) as exc:
            candidate = {
                "deviceType": "physical",
                "deviceId": serial,
                "ready": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
        candidate["selectionMethod"] = "auto_discovery"
        attempts.append(candidate)
        if candidate.get("ready") and not candidate.get("isEmulator"):
            ready_candidates.append(candidate)
        else:
            print(json.dumps(compact_attempt(candidate), ensure_ascii=False), flush=True)

    if len(ready_candidates) == 1:
        return ready_candidates[0], attempts, priority
    if len(ready_candidates) > 1:
        attempts.append(
            {
                "deviceType": "physical",
                "ready": False,
                "selectionMethod": "auto_discovery",
                "error": "multiple ready physical devices; configure physicalDeviceId explicitly",
                "candidateDeviceIds": [item.get("deviceId") for item in ready_candidates],
            }
        )
    return None, attempts, priority


def main() -> int:
    args = parse_args()
    project_root = args.project_root.resolve()
    output_path = args.output_path
    if not output_path.is_absolute():
        output_path = project_root / output_path
    started_at = datetime.now().astimezone()
    attempts: list[dict[str, Any]] = []
    try:
        adb_path = resolve_adb(project_root, args.adb_path)
        physical_device_id = str(
            args.physical_device_id
            or os.environ.get("ADVISOR_PHYSICAL_DEVICE_ID")
            or os.environ.get("ADVISOR_DEVICE_ID")
            or os.environ.get("TTFUND_DEVICE_ID")
            or os.environ.get("GFFUNDS_DEVICE_ID")
            or ""
        ).strip()
        selected, attempts, priority = select_device(
            adb_path,
            physical_device_id=physical_device_id,
            preflight_attempts=args.preflight_attempts,
            preflight_retry_seconds=args.preflight_retry_seconds,
        )
        payload = {
            "version": 2,
            "status": "ready" if selected else "blocked",
            "deviceMode": "physical_only",
            "startedAt": started_at.isoformat(timespec="seconds"),
            "finishedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
            "priority": priority,
            "fallbackUsed": bool(selected and selected.get("selectionMethod") == "auto_discovery"),
            "configuredDeviceId": physical_device_id or None,
            "selected": selected,
            "fallback": None,
            "attempts": attempts,
        }
    except Exception as exc:  # noqa: BLE001 - always persist a selection failure summary.
        payload = {
            "version": 2,
            "status": "blocked",
            "deviceMode": "physical_only",
            "startedAt": started_at.isoformat(timespec="seconds"),
            "finishedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
            "selected": None,
            "fallback": None,
            "attempts": attempts,
            "error": f"{type(exc).__name__}: {exc}",
        }
    atomic_write_json(output_path, payload)
    payload["summaryPath"] = str(output_path)
    if payload["status"] != "ready":
        print(f"[设备预检失败] 没有唯一可用的实体真机，摘要：{output_path}", flush=True)
        return 2
    selected = payload["selected"] or {}
    print(
        f"[设备已选定] physical / {selected.get('deviceId') or selected.get('device_id')}，"
        "本批次所有 App 采集和补采固定使用该真机。",
        flush=True,
    )
    print(f"[设备预检摘要] {output_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
