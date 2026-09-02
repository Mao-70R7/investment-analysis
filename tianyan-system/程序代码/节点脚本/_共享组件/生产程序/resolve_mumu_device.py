from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
TTFUND_PACKAGE = "com.eastmoney.android.fund"
REMOTE_TTFUND_CACHE_DIR = f"/sdcard/Android/data/{TTFUND_PACKAGE}/files/.ttjj_cache"


def now_local() -> datetime:
    return datetime.now().astimezone()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Resolve a running MuMu emulator as an ADB device for advisor incremental updates."
    )
    parser.add_argument("--mumu-root", type=Path, default=None)
    parser.add_argument("--mumu-cli", type=Path, default=None)
    parser.add_argument("--adb-path", type=Path, default=None)
    parser.add_argument("--vmindex", default="0")
    parser.add_argument("--wait-seconds", type=int, default=120)
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--launch-if-needed", action="store_true")
    parser.add_argument("--launch-ttfund", action="store_true")
    parser.add_argument(
        "--prepare-ttfund-app",
        action="store_true",
        help="Grant TTFund runtime permissions and dismiss the first-run consent dialog when it is visible.",
    )
    parser.add_argument(
        "--ttfund-apk",
        type=Path,
        default=None,
        help="Optional local TTFund APK/APKS/XAPK path. The resolver installs it only when the app is missing.",
    )
    parser.add_argument("--require-ttfund-app", action="store_true")
    parser.add_argument("--require-ttfund-cache", action="store_true")
    parser.add_argument("--allow-missing-ttfund", action="store_true")
    parser.add_argument("--cleanup-offline-localhost", action="store_true")
    parser.add_argument("--output-json", type=Path, default=None)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "data" / "raw" / "ttfund" / "mumu_device_resolve",
    )
    return parser.parse_args()


def run_command(args: list[str], timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_jsonish(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            value = json.loads(raw[start : end + 1])
            return value if isinstance(value, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def first_existing(paths: list[Path]) -> Path | None:
    for path in paths:
        if path and path.exists():
            return path
    return None


def common_mumu_roots() -> list[Path]:
    roots: list[Path] = []
    for env_name in ("MUMU_ROOT", "MUMU_INSTALL_ROOT", "ADVISOR_MUMU_ROOT"):
        value = os.environ.get(env_name)
        if value:
            roots.append(Path(value))
    roots.extend(
        [
            Path(r"E:\app_emulator\MuMu"),
            Path(r"C:\Program Files\Netease\MuMuPlayer-12.0"),
            Path(r"C:\Program Files\Netease\MuMu Player 12"),
            Path(r"C:\Program Files (x86)\Netease\MuMuPlayer-12.0"),
        ]
    )
    deduped: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root).lower()
        if key not in seen:
            seen.add(key)
            deduped.append(root)
    return deduped


def resolve_mumu_root(cli: Path | None, requested_root: Path | None) -> Path | None:
    if requested_root and requested_root.exists():
        return requested_root
    if cli and cli.exists():
        # Typical layout: <root>/nx_main/mumu-cli.exe
        if cli.parent.name.lower() == "nx_main":
            return cli.parent.parent
        return cli.parent
    return first_existing(common_mumu_roots())


def resolve_mumu_cli(requested_cli: Path | None, root: Path | None) -> Path:
    candidates: list[Path] = []
    env_cli = os.environ.get("MUMU_CLI_EXE") or os.environ.get("ADVISOR_MUMU_CLI")
    if requested_cli:
        candidates.append(requested_cli)
    if env_cli:
        candidates.append(Path(env_cli))
    if root:
        candidates.extend(
            [
                root / "nx_main" / "mumu-cli.exe",
                root / "nx_main" / "MuMuManager.exe",
            ]
        )
    found = first_existing(candidates)
    if found:
        return found
    which = shutil.which("mumu-cli.exe") or shutil.which("MuMuManager.exe")
    if which:
        return Path(which)
    raise FileNotFoundError("MuMu CLI not found. Pass --mumu-cli or set MUMU_CLI_EXE.")


def resolve_adb_path(requested_adb: Path | None, root: Path | None, cli: Path) -> Path:
    candidates: list[Path] = []
    env_adb = os.environ.get("MUMU_ADB_EXE") or os.environ.get("ADVISOR_MUMU_ADB_EXE")
    if requested_adb:
        candidates.append(requested_adb)
    if env_adb:
        candidates.append(Path(env_adb))
    candidates.append(cli.parent / "adb.exe")
    if root:
        candidates.extend(
            [
                root / "nx_main" / "adb.exe",
                root / "nx_device" / "15.0" / "shell" / "adb.exe",
                root / "nx_device" / "12.0" / "shell" / "adb.exe",
            ]
        )
    candidates.append(PROJECT_ROOT / "tools" / "platform-tools" / "adb.exe")
    found = first_existing(candidates)
    if found:
        return found
    which = shutil.which("adb")
    if which:
        return Path(which)
    raise FileNotFoundError("adb.exe not found. Pass --adb-path or set MUMU_ADB_EXE.")


def mumu_cli_json(cli: Path, *args: str, timeout: int = 30) -> tuple[dict[str, Any] | None, subprocess.CompletedProcess[str]]:
    completed = run_command([str(cli), *args], timeout=timeout)
    return parse_jsonish(completed.stdout + completed.stderr), completed


def adb_run(adb: Path, *args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return run_command([str(adb), *args], timeout=timeout)


def adb_device_run(adb: Path, serial: str, *args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return adb_run(adb, "-s", serial, *args, timeout=timeout)


def read_vm_config_adb_port(root: Path | None, vmindex: str) -> int | None:
    if not root:
        return None
    vms_root = root / "vms"
    if not vms_root.exists():
        return None
    pattern = f"MuMuPlayer-*-{vmindex}"
    candidates = sorted(vms_root.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    for vm_dir in candidates:
        config = vm_dir / "configs" / "vm_config.json"
        if not config.exists():
            continue
        try:
            payload = json.loads(config.read_text(encoding="utf-8"))
            value = payload["vm"]["nat"]["port_forward"]["adb"]["host_port"]
            return int(str(value))
        except Exception:
            continue
    return None


def cleanup_offline_localhost(adb: Path, keep_serial: str) -> list[str]:
    completed = adb_run(adb, "devices", "-l", timeout=20)
    disconnected: list[str] = []
    for line in completed.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, state = parts[0], parts[1]
        if serial == keep_serial:
            continue
        if state == "offline" and (serial.startswith("127.0.0.1:") or serial.startswith("localhost:")):
            adb_run(adb, "disconnect", serial, timeout=10)
            disconnected.append(serial)
    return disconnected


def connect_mumu(cli: Path, adb: Path, root: Path | None, vmindex: str) -> dict[str, Any]:
    payload, completed = mumu_cli_json(cli, "adb", "--vmindex", vmindex, "--cmd", "connect", timeout=40)
    result: dict[str, Any] = {
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
        "payload": payload,
    }
    host = None
    port = None
    if payload:
        host = payload.get("adb_host")
        port = payload.get("adb_port")
    if not host:
        host = "127.0.0.1"
    if not port:
        port = read_vm_config_adb_port(root, vmindex)
    if port:
        serial = f"{host}:{port}"
        result["serial"] = serial
        state = adb_device_run(adb, serial, "get-state", timeout=20)
        result["state_stdout"] = state.stdout.strip()
        result["state_stderr"] = state.stderr.strip()
        result["state_returncode"] = state.returncode
        if state.returncode != 0 or state.stdout.strip() != "device":
            direct = adb_run(adb, "connect", serial, timeout=20)
            result["direct_connect_stdout"] = direct.stdout.strip()
            result["direct_connect_stderr"] = direct.stderr.strip()
            state = adb_device_run(adb, serial, "get-state", timeout=20)
            result["state_stdout"] = state.stdout.strip()
            result["state_stderr"] = state.stderr.strip()
            result["state_returncode"] = state.returncode
    return result


def get_prop(adb: Path, serial: str, prop: str) -> str:
    completed = adb_device_run(adb, serial, "shell", f"getprop {prop}", timeout=10)
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def is_package_installed(adb: Path, serial: str, package_name: str) -> bool:
    completed = adb_device_run(adb, serial, "shell", f"pm path {package_name}", timeout=20)
    return completed.returncode == 0 and bool(completed.stdout.strip())


def install_ttfund_if_needed(cli: Path, adb: Path, serial: str, vmindex: str, apk_path: Path | None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "requested": bool(apk_path),
        "installed_before": is_package_installed(adb, serial, TTFUND_PACKAGE),
        "attempted": False,
    }
    if not apk_path or result["installed_before"]:
        result["installed_after"] = result["installed_before"]
        return result
    if not apk_path.exists():
        result["error"] = f"APK not found: {apk_path}"
        result["installed_after"] = False
        return result
    completed = run_command(
        [
            str(cli),
            "control",
            "--vmindex",
            vmindex,
            "app",
            "install",
            "--apk",
            str(apk_path),
        ],
        timeout=180,
    )
    result.update(
        {
            "attempted": True,
            "apk_path": str(apk_path),
            "returncode": completed.returncode,
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
            "payload": parse_jsonish(completed.stdout + completed.stderr),
        }
    )
    time.sleep(5)
    result["installed_after"] = is_package_installed(adb, serial, TTFUND_PACKAGE)
    return result


def grant_ttfund_permissions(adb: Path, serial: str) -> dict[str, Any]:
    permissions = (
        "android.permission.POST_NOTIFICATIONS",
        "android.permission.READ_EXTERNAL_STORAGE",
        "android.permission.READ_PHONE_STATE",
        "android.permission.READ_MEDIA_IMAGES",
        "android.permission.CAMERA",
        "android.permission.READ_MEDIA_AUDIO",
        "android.permission.READ_MEDIA_VIDEO",
        "android.permission.RECORD_AUDIO",
        "android.permission.BLUETOOTH_CONNECT",
        "android.permission.BLUETOOTH_ADVERTISE",
        "android.permission.BLUETOOTH_SCAN",
    )
    granted: list[str] = []
    skipped: list[dict[str, str]] = []
    for permission in permissions:
        completed = adb_device_run(
            adb,
            serial,
            "shell",
            f"pm grant {TTFUND_PACKAGE} {permission}",
            timeout=15,
        )
        if completed.returncode == 0:
            granted.append(permission)
        else:
            skipped.append(
                {
                    "permission": permission,
                    "error": completed.stderr.strip() or completed.stdout.strip(),
                }
            )
    return {"granted": granted, "skipped": skipped}


def parse_bounds(bounds: str) -> tuple[int, int, int, int] | None:
    match = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds or "")
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


def tap_center(adb: Path, serial: str, bounds: str) -> bool:
    parsed = parse_bounds(bounds)
    if not parsed:
        return False
    left, top, right, bottom = parsed
    x = int((left + right) / 2)
    y = int((top + bottom) / 2)
    completed = adb_device_run(adb, serial, "shell", f"input tap {x} {y}", timeout=15)
    return completed.returncode == 0


def dump_ttfund_ui(adb: Path, serial: str, remote_path: str = "/sdcard/ttfund_mumu_prepare.xml") -> tuple[bool, str]:
    dump = adb_device_run(adb, serial, "shell", f"uiautomator dump {remote_path}", timeout=30)
    dumped = "dumped to:" in f"{dump.stdout}\n{dump.stderr}".lower()
    if dump.returncode != 0 and not dumped:
        return False, dump.stderr.strip() or dump.stdout.strip()
    cat = adb_device_run(adb, serial, "shell", f"cat {remote_path}", timeout=30)
    if cat.returncode != 0:
        return False, cat.stderr.strip() or cat.stdout.strip()
    return True, cat.stdout


def maybe_accept_ttfund_consent(adb: Path, serial: str) -> dict[str, Any]:
    result: dict[str, Any] = {"attempted": False, "accepted": False}
    ok, xml_text = dump_ttfund_ui(adb, serial)
    if not ok:
        result["error"] = xml_text
        return result
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        result["error"] = f"ui_xml_parse_failed: {exc}"
        return result

    candidates: list[str] = []
    for node in root.iter("node"):
        if node.attrib.get("package") != TTFUND_PACKAGE:
            continue
        if node.attrib.get("clickable") != "true":
            continue
        bounds = node.attrib.get("bounds") or ""
        parsed = parse_bounds(bounds)
        if not parsed:
            continue
        left, top, right, bottom = parsed
        width = right - left
        height = bottom - top
        if top >= 1000 and left >= 300 and width >= 180 and height >= 60:
            candidates.append(bounds)

    if not candidates:
        return result
    result["attempted"] = True
    result["candidate_bounds"] = candidates
    result["accepted"] = tap_center(adb, serial, candidates[0])
    if result["accepted"]:
        time.sleep(4)
    return result


def prepare_ttfund_app(adb: Path, serial: str, launch_ttfund: bool) -> dict[str, Any]:
    result: dict[str, Any] = {"requested": True}
    if not is_package_installed(adb, serial, TTFUND_PACKAGE):
        result["skipped"] = "ttfund_app_not_installed"
        return result
    if launch_ttfund:
        launch = adb_device_run(adb, serial, "shell", "monkey", "-p", TTFUND_PACKAGE, "1", timeout=30)
        result["launch_returncode"] = launch.returncode
        time.sleep(5)
    result["permissions"] = grant_ttfund_permissions(adb, serial)
    result["consent"] = maybe_accept_ttfund_consent(adb, serial)
    return result


def inspect_device(adb: Path, serial: str, launch_ttfund: bool) -> dict[str, Any]:
    state = adb_device_run(adb, serial, "get-state", timeout=15)
    props = {
        prop: get_prop(adb, serial, prop)
        for prop in (
            "ro.kernel.qemu",
            "ro.boot.qemu",
            "ro.product.manufacturer",
            "ro.product.brand",
            "ro.product.model",
            "ro.hardware",
            "ro.build.characteristics",
        )
    }
    app = adb_device_run(adb, serial, "shell", f"pm path {TTFUND_PACKAGE}", timeout=20)
    app_installed = app.returncode == 0 and bool(app.stdout.strip())
    launched = False
    if launch_ttfund and app_installed:
        launch = adb_device_run(adb, serial, "shell", "monkey", "-p", TTFUND_PACKAGE, "1", timeout=30)
        launched = launch.returncode == 0
        time.sleep(5)
    cache = adb_device_run(adb, serial, "shell", f"ls -d {REMOTE_TTFUND_CACHE_DIR}", timeout=20)
    wm_size = adb_device_run(adb, serial, "shell", "wm size", timeout=10)
    return {
        "serial": serial,
        "state_ok": state.returncode == 0 and state.stdout.strip() == "device",
        "state_stdout": state.stdout.strip(),
        "state_stderr": state.stderr.strip(),
        "props": props,
        "ttfund_app_installed": app_installed,
        "ttfund_app_path": app.stdout.strip(),
        "ttfund_app_error": app.stderr.strip(),
        "ttfund_launched": launched,
        "ttfund_cache_accessible": cache.returncode == 0,
        "ttfund_cache_path": cache.stdout.strip(),
        "ttfund_cache_error": cache.stderr.strip() or cache.stdout.strip(),
        "wm_size": wm_size.stdout.strip(),
    }


def main() -> None:
    args = parse_args()
    run_at = now_local()
    run_id = run_at.strftime("%Y%m%dT%H%M%S%z")
    output_json = args.output_json or (
        args.output_dir / run_at.strftime("%Y-%m-%d") / run_id / "summary.json"
    )
    summary: dict[str, Any] = {
        "state": "running",
        "run_id": run_id,
        "captured_at": run_at.isoformat(timespec="seconds"),
        "vmindex": str(args.vmindex),
        "ttfund_package": TTFUND_PACKAGE,
        "ttfund_cache_dir": REMOTE_TTFUND_CACHE_DIR,
        "output_json": str(output_json),
    }
    exit_code = 0
    try:
        root = resolve_mumu_root(args.mumu_cli, args.mumu_root)
        cli = resolve_mumu_cli(args.mumu_cli, root)
        root = resolve_mumu_root(cli, root)
        adb = resolve_adb_path(args.adb_path, root, cli)
        summary.update({"mumu_root": str(root) if root else None, "mumu_cli": str(cli), "adb_path": str(adb)})

        info_payload, info_completed = mumu_cli_json(cli, "info", "--vmindex", str(args.vmindex), timeout=30)
        summary["initial_info"] = info_payload or {
            "returncode": info_completed.returncode,
            "stdout": info_completed.stdout.strip(),
            "stderr": info_completed.stderr.strip(),
        }
        if info_payload and not info_payload.get("is_process_started") and args.launch_if_needed:
            launch_payload, launch_completed = mumu_cli_json(
                cli, "control", "--vmindex", str(args.vmindex), "launch", timeout=60
            )
            summary["launch"] = launch_payload or {
                "returncode": launch_completed.returncode,
                "stdout": launch_completed.stdout.strip(),
                "stderr": launch_completed.stderr.strip(),
            }

        deadline = time.monotonic() + max(args.wait_seconds, 1)
        connect_result: dict[str, Any] = {}
        serial = None
        polls: list[dict[str, Any]] = []
        while time.monotonic() <= deadline:
            info_payload, _ = mumu_cli_json(cli, "info", "--vmindex", str(args.vmindex), timeout=30)
            connect_result = connect_mumu(cli, adb, root, str(args.vmindex))
            serial = connect_result.get("serial")
            poll = {
                "time": now_local().isoformat(timespec="seconds"),
                "info": info_payload,
                "connect": connect_result,
            }
            polls.append(poll)
            if serial and connect_result.get("state_stdout") == "device":
                break
            time.sleep(max(args.poll_seconds, 1.0))
        summary["polls_tail"] = polls[-6:]
        summary["connect"] = connect_result
        if not serial or connect_result.get("state_stdout") != "device":
            raise RuntimeError("MuMu ADB device is not ready.")

        if args.cleanup_offline_localhost:
            summary["offline_localhost_disconnected"] = cleanup_offline_localhost(adb, serial)

        install_result = install_ttfund_if_needed(cli, adb, serial, str(args.vmindex), args.ttfund_apk)
        summary["ttfund_install"] = install_result
        if args.ttfund_apk and not install_result.get("installed_after"):
            raise RuntimeError(f"TTFund install did not complete: {install_result.get('error') or install_result}")

        if args.prepare_ttfund_app:
            summary["ttfund_prepare"] = prepare_ttfund_app(adb, serial, args.launch_ttfund)

        health = inspect_device(adb, serial, args.launch_ttfund)
        summary["device_id"] = serial
        summary["device_health"] = health
        blocked_reasons: list[str] = []
        if not health["state_ok"]:
            blocked_reasons.append("device_state_not_ready")
        if args.require_ttfund_app and not health["ttfund_app_installed"]:
            blocked_reasons.append("ttfund_app_not_installed")
        if args.require_ttfund_cache and not health["ttfund_cache_accessible"]:
            blocked_reasons.append("ttfund_cache_not_accessible")
        if blocked_reasons and not args.allow_missing_ttfund:
            summary["state"] = "blocked"
            summary["blocked_reasons"] = blocked_reasons
            exit_code = 3
        else:
            summary["state"] = "ready"
            summary["blocked_reasons"] = blocked_reasons
    except Exception as exc:
        summary["state"] = "failed"
        summary["error"] = str(exc)
        exit_code = 1
    finally:
        write_json(output_json, summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
