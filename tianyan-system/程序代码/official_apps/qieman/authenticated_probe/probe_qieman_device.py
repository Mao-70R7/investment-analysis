from __future__ import annotations

import atexit
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


PROBE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = next(parent for parent in PROBE_ROOT.parents if (parent / "AGENTS.md").is_file())
WORKSPACE_ROOT = PROJECT_ROOT.parent
LOCK_ROOT = WORKSPACE_ROOT / "运行状态" / "locks"
DEFAULT_ADB = PROJECT_ROOT / "tools" / "platform-tools" / "adb.exe"
ACTIVE_LOCK_NAMES = (
    "daily_update.lock",
    "device.lock",
    "main_db_write.lock",
    "publish_repo.lock",
)
PACKAGE_HINTS = ("qieman", "yingmi", "ymfund", "hermione")
SYSTEM_PACKAGE_PREFIXES = (
    "android",
    "com.android.",
    "com.google.android.",
    "com.miui.",
    "com.huawei.",
    "com.hihonor.",
    "com.oplus.",
    "com.coloros.",
    "com.vivo.",
)
URL_RE = re.compile(rb"https?://[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]{4,500}")
PATH_RE = re.compile(
    rb"/(?:api|gateway|v[0-9]+|fund|portfolio|strategy|advisor|invest|position|holding|nav|benchmark|rebalance|adjust)[A-Za-z0-9._~/?#\[\]@!$&'()*+,;=%:-]{2,300}",
    re.IGNORECASE,
)
BUSINESS_TERMS = (
    "基金投顾",
    "投资顾问",
    "组合",
    "策略",
    "业绩基准",
    "基准",
    "净值",
    "收益率",
    "持仓",
    "仓位",
    "调仓",
    "成分基金",
)
SENSITIVE_SCREEN_TERMS = (
    "默认账户",
    "待办事项",
    "自动调仓失败",
    "最新收益",
    "资产总额",
    "总资产",
    "持有收益",
)


def now_local() -> datetime:
    return datetime.now().astimezone()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def redact_sensitive_text(value: str) -> str:
    patterns = (
        (r"(?i)(authorization\s*[:=]\s*)([^\s&\"']+)", r"\1<redacted>"),
        (r"(?i)((?:token|access[_-]?token|refresh[_-]?token|session(?:id)?|cookie|ticket)\s*[:=]\s*)([^\s&\"']+)", r"\1<redacted>"),
        (r"(?i)([?&](?:token|access_token|session|ticket|sign|signature)=)[^&\s\"']+", r"\1<redacted>"),
        (r"(?<!\d)1[3-9]\d{9}(?!\d)", "<redacted_mobile>"),
        (r"(?<!\d)\d{17}[0-9Xx](?!\d)", "<redacted_id>"),
        (r"(?<!\d)\d{16,19}(?!\d)", "<redacted_long_number>"),
        (r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "<redacted_email>"),
    )
    redacted = value
    for pattern, replacement in patterns:
        redacted = re.sub(pattern, replacement, redacted)
    return redacted


def sanitize_url(value: str) -> str:
    value = redact_sensitive_text(value.strip("\x00\r\n\t '[](){}<>\""))
    if "?" not in value:
        return value
    base, query = value.split("?", 1)
    safe_parts = []
    for item in query.split("&"):
        key = item.split("=", 1)[0]
        if key and re.fullmatch(r"[A-Za-z0-9_.-]{1,50}", key):
            safe_parts.append(f"{key}=<redacted>")
    return base + ("?" + "&".join(safe_parts) if safe_parts else "")


def redact_ui_xml(value: str) -> tuple[str, bool]:
    redacted = redact_sensitive_text(value)
    sensitive_screen = any(term in redacted for term in SENSITIVE_SCREEN_TERMS)
    if sensitive_screen:
        redacted = re.sub(
            r'(?P<attribute>text|content-desc)="[^"]*"',
            lambda match: f'{match.group("attribute")}="<redacted_screen_text>"',
            redacted,
        )
    return redacted, sensitive_screen


def run_command(args: list[str], timeout: int = 60, binary: bool = False) -> subprocess.CompletedProcess[Any]:
    kwargs: dict[str, Any] = {
        "capture_output": True,
        "timeout": timeout,
        "check": False,
    }
    if not binary:
        kwargs.update({"text": True, "encoding": "utf-8", "errors": "replace"})
    return subprocess.run(args, **kwargs)


def run_adb(adb: str, device_id: str | None, *args: str, timeout: int = 60, binary: bool = False) -> subprocess.CompletedProcess[Any]:
    command = [adb]
    if device_id:
        command.extend(["-s", device_id])
    command.extend(args)
    return run_command(command, timeout=timeout, binary=binary)


def parse_adb_devices(output: str) -> list[dict[str, str]]:
    devices: list[dict[str, str]] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("List of devices attached") or line.startswith("*"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        row = {"serial": parts[0], "state": parts[1]}
        for item in parts[2:]:
            if ":" in item:
                key, value = item.split(":", 1)
                row[key] = value
        devices.append(row)
    return devices


def active_locks(lock_root: Path = LOCK_ROOT) -> list[str]:
    """Return locks that make an isolated Qieman probe unsafe.

    Production Qieman nodes run inside the normal daily orchestrator, so the
    orchestrator's own ``daily_update.lock`` is expected.  They also use only
    read-only HTTP interfaces and may run while the separate TTFund branch owns
    ``device.lock``.  The opt-in environment values below are set only by the
    formal Qieman node and are verified against the lock payload; ad-hoc probes
    keep the original fail-closed behaviour.
    """

    allowed_daily_run_id = str(os.environ.get("QIEMAN_ALLOWED_DAILY_RUN_ID") or "").strip()
    allow_device_lock = str(os.environ.get("QIEMAN_ALLOW_DEVICE_LOCK") or "").strip() == "1"
    blocked: list[str] = []
    for name in ACTIVE_LOCK_NAMES:
        path = lock_root / name
        if not path.is_file():
            continue
        if name == "device.lock" and allow_device_lock:
            continue
        if name == "daily_update.lock" and allowed_daily_run_id:
            try:
                payload = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError):
                payload = {}
            if str(payload.get("runId") or "").strip() == allowed_daily_run_id:
                continue
        blocked.append(str(path))
    return blocked


def acquire_device_lock(run_id: str, lock_root: Path = LOCK_ROOT) -> tuple[Path, str]:
    lock_root.mkdir(parents=True, exist_ok=True)
    path = lock_root / "device.lock"
    token = hashlib.sha256(f"{os.getpid()}|{run_id}|{datetime.now().timestamp()}".encode("utf-8")).hexdigest()
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise RuntimeError(f"device resource lock is active: {path}") from exc
    try:
        payload = {
            "pid": os.getpid(),
            "runId": run_id,
            "nodeId": "qieman_authenticated_probe",
            "token": token,
            "acquiredAt": now_local().isoformat(timespec="seconds"),
        }
        os.write(descriptor, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    finally:
        os.close(descriptor)
    return path, token


def release_device_lock(path: Path, token: str) -> None:
    try:
        current = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        current = {}
    if current.get("token") == token:
        path.unlink(missing_ok=True)


def parse_front_component(text: str) -> tuple[str | None, str | None]:
    patterns = (
        r"mResumedActivity.*?\s([A-Za-z0-9._]+)/([A-Za-z0-9._$]+)",
        r"topResumedActivity=.*?\s([A-Za-z0-9._]+)/([A-Za-z0-9._$]+)",
        r"mCurrentFocus=.*?\s([A-Za-z0-9._]+)/([A-Za-z0-9._$]+)",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1), match.group(2)
    return None, None


def is_system_package(package_name: str) -> bool:
    return any(package_name == prefix or package_name.startswith(prefix) for prefix in SYSTEM_PACKAGE_PREFIXES)


def choose_package(explicit: str | None, front_package: str | None, installed: Iterable[str]) -> tuple[str | None, str]:
    if explicit:
        return explicit, "explicit"
    if front_package and not is_system_package(front_package):
        return front_package, "foreground"
    candidates = sorted({pkg for pkg in installed if any(hint in pkg.lower() for hint in PACKAGE_HINTS)})
    if len(candidates) == 1:
        return candidates[0], "package_name_hint"
    if candidates:
        return None, "multiple_package_name_hints:" + ",".join(candidates)
    return None, "qieman_package_not_identified_keep_app_foreground"


def parse_package_paths(output: str) -> list[str]:
    return [line.split("package:", 1)[1].strip() for line in output.splitlines() if line.strip().startswith("package:")]


def parse_package_metadata(text: str) -> dict[str, Any]:
    patterns = {
        "version_name": r"\bversionName=([^\s]+)",
        "version_code": r"\bversionCode=(\d+)",
        "target_sdk": r"\btargetSdk=(\d+)",
        "min_sdk": r"\bminSdk=(\d+)",
        "first_install_time": r"\bfirstInstallTime=(.+)",
        "last_update_time": r"\blastUpdateTime=(.+)",
        "installer_package_name": r"\binstallerPackageName=([^\s]+)",
    }
    result: dict[str, Any] = {}
    for key, pattern in patterns.items():
        match = re.search(pattern, text)
        result[key] = match.group(1).strip() if match else None
    return result


def iter_binary_chunks(path: Path, chunk_size: int = 1024 * 1024, overlap: int = 1024) -> Iterable[bytes]:
    with path.open("rb") as handle:
        tail = b""
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                if tail:
                    yield tail
                break
            merged = tail + chunk
            yield merged
            tail = merged[-overlap:]


def inventory_apk(path: Path, max_scan_bytes: int = 512 * 1024 * 1024) -> dict[str, Any]:
    urls: set[str] = set()
    paths: set[str] = set()
    term_counts: Counter[str] = Counter()
    hosts: Counter[str] = Counter()
    scanned_bytes = 0
    zip_entries: list[str] = []
    errors: list[str] = []

    try:
        with zipfile.ZipFile(path) as archive:
            zip_entries = archive.namelist()
            selected = [
                name
                for name in zip_entries
                if name.endswith((".dex", ".js", ".json", ".html", ".xml", ".txt", ".so"))
                or name.startswith(("assets/", "res/raw/"))
            ]
            for name in selected:
                if scanned_bytes >= max_scan_bytes:
                    break
                try:
                    with archive.open(name) as handle:
                        tail = b""
                        while scanned_bytes < max_scan_bytes:
                            chunk = handle.read(min(1024 * 1024, max_scan_bytes - scanned_bytes))
                            if not chunk:
                                break
                            scanned_bytes += len(chunk)
                            data = tail + chunk
                            collect_binary_clues(data, urls, paths, term_counts, hosts)
                            tail = data[-1024:]
                except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
                    errors.append(f"{name}: {exc}")
    except zipfile.BadZipFile:
        for chunk in iter_binary_chunks(path):
            scanned_bytes += len(chunk)
            collect_binary_clues(chunk, urls, paths, term_counts, hosts)
            if scanned_bytes >= max_scan_bytes:
                break

    return {
        "apk_path": str(path),
        "size": path.stat().st_size,
        "sha256": sha256_file(path),
        "scanned_bytes": scanned_bytes,
        "zip_entry_count": len(zip_entries),
        "zip_entry_sample": zip_entries[:200],
        "urls": sorted(urls)[:1000],
        "hosts": [{"host": host, "hits": count} for host, count in hosts.most_common(300)],
        "api_path_candidates": sorted(paths)[:2000],
        "business_term_hits": dict(sorted(term_counts.items())),
        "errors": errors[:100],
    }


def collect_binary_clues(
    data: bytes,
    urls: set[str],
    paths: set[str],
    term_counts: Counter[str],
    hosts: Counter[str],
) -> None:
    for match in URL_RE.finditer(data):
        raw = match.group(0).decode("utf-8", errors="ignore")
        url = sanitize_url(raw)
        if url:
            urls.add(url)
            host_match = re.match(r"https?://([^/:?#]+)", url, re.IGNORECASE)
            if host_match:
                hosts[host_match.group(1).lower()] += 1
    for match in PATH_RE.finditer(data):
        candidate = match.group(0).decode("utf-8", errors="ignore")
        candidate = sanitize_url(candidate)
        if 4 <= len(candidate) <= 320:
            paths.add(candidate)
    for term in BUSINESS_TERMS:
        count = data.count(term.encode("utf-8"))
        if count:
            term_counts[term] += count


def capture_ui(adb: str, device_id: str, output_path: Path) -> dict[str, Any]:
    remote = "/sdcard/Download/qieman_probe_window.xml"
    dump = run_adb(adb, device_id, "shell", "uiautomator", "dump", remote, timeout=30)
    if dump.returncode != 0:
        return {"status": "failed", "error": redact_sensitive_text(dump.stderr or dump.stdout)}
    pulled = run_adb(adb, device_id, "shell", "cat", remote, timeout=30)
    run_adb(adb, device_id, "shell", "rm", remote, timeout=15)
    if pulled.returncode != 0:
        return {"status": "failed", "error": redact_sensitive_text(pulled.stderr or pulled.stdout)}
    redacted, sensitive_screen = redact_ui_xml(pulled.stdout)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(redacted, encoding="utf-8")
    text_values = [redact_sensitive_text(value) for value in re.findall(r'text="([^"]+)"', redacted) if value]
    resource_ids = sorted(set(re.findall(r'resource-id="([^"]+)"', redacted)))
    packages = sorted(set(re.findall(r'package="([^"]+)"', redacted)))
    return {
        "status": "captured_redacted",
        "path": str(output_path),
        "text_node_count": len(text_values),
        "text_sample": text_values[:200],
        "resource_id_count": len(resource_ids),
        "resource_id_sample": resource_ids[:300],
        "packages": packages,
        "sensitive_screen_text_fully_redacted": sensitive_screen,
    }


def webview_targets(adb: str, device_id: str) -> dict[str, Any]:
    sockets = run_adb(adb, device_id, "shell", "cat", "/proc/net/unix", timeout=20)
    names = sorted(set(re.findall(r"@(\S*webview_devtools_remote\S*)", sockets.stdout if sockets.returncode == 0 else "")))
    result: dict[str, Any] = {"socket_names": names, "targets": [], "errors": []}
    for name in names[:10]:
        forward = run_adb(adb, device_id, "forward", "tcp:0", f"localabstract:{name}", timeout=15)
        if forward.returncode != 0:
            result["errors"].append(redact_sensitive_text(forward.stderr or forward.stdout))
            continue
        port = forward.stdout.strip()
        if not port.isdigit():
            result["errors"].append(f"invalid_forward_port:{port}")
            continue
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=3) as response:
                payload = json.loads(response.read(2 * 1024 * 1024).decode("utf-8", errors="replace"))
            for target in payload if isinstance(payload, list) else []:
                if not isinstance(target, dict):
                    continue
                result["targets"].append(
                    {
                        "socket": name,
                        "type": target.get("type"),
                        "title": redact_sensitive_text(str(target.get("title") or "")),
                        "url": sanitize_url(str(target.get("url") or "")),
                    }
                )
        except (OSError, ValueError, urllib.error.URLError) as exc:
            result["errors"].append(f"{name}: {exc}")
        finally:
            run_adb(adb, device_id, "forward", "--remove", f"tcp:{port}", timeout=15)
    return result


def initial_coverage() -> dict[str, Any]:
    return {
        "status": "entry_probe_only",
        "sample_strategy_count": 0,
        "entities": {
            "strategy_master": {"status": "not_yet_validated", "rows": 0},
            "strategy_performance_daily": {"status": "not_yet_validated", "rows": 0},
            "strategy_fund_snapshot": {"status": "not_yet_validated", "rows": 0},
            "strategy_rebalance_event": {"status": "not_yet_validated", "rows": 0},
            "strategy_rebalance_fund_delta": {"status": "not_yet_validated", "rows": 0},
        },
        "priority_fields": {
            "benchmark": "not_yet_validated",
            "launch_date": "not_yet_validated",
            "daily_performance_points": "not_yet_validated",
            "benchmark_performance_points": "not_yet_validated",
            "fund_code_and_weight": "not_yet_validated",
            "position_date": "not_yet_validated",
        },
        "quality_note": "入口、截图或 UI 文本不构成策略曲线、精确持仓或调仓明细证据。",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only authenticated entry probe for the Qieman Android app.")
    parser.add_argument("--adb-path", default=str(DEFAULT_ADB if DEFAULT_ADB.is_file() else "adb"))
    parser.add_argument("--device-id")
    parser.add_argument("--package")
    parser.add_argument("--output-root", type=Path, default=PROBE_ROOT / "runs")
    parser.add_argument("--allow-missing-device", action="store_true")
    parser.add_argument("--skip-apk-pull", action="store_true")
    parser.add_argument("--capture-screenshot", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    started = now_local()
    run_id = started.strftime("%Y%m%dT%H%M%S%z")
    run_dir = args.output_root.resolve() / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    locks = active_locks()
    if locks:
        summary = {
            "state": "blocked_active_production_lock",
            "captured_at": started.isoformat(timespec="seconds"),
            "active_locks": locks,
            "run_dir": str(run_dir),
        }
        write_json(run_dir / "summary.json", summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        raise SystemExit(3)

    lock_path, lock_token = acquire_device_lock(run_id)
    atexit.register(release_device_lock, lock_path, lock_token)

    devices_result = run_adb(args.adb_path, None, "devices", "-l", timeout=20)
    devices = parse_adb_devices(devices_result.stdout)
    usable = [row for row in devices if row.get("state") == "device"]
    selected = None
    if args.device_id:
        selected = next((row for row in usable if row.get("serial") == args.device_id), None)
    elif len(usable) == 1:
        selected = usable[0]

    if selected is None:
        if args.device_id and any(row.get("serial") == args.device_id for row in devices):
            reason = "requested_device_not_authorized"
        elif len(usable) > 1:
            reason = "multiple_devices_require_device_id"
        else:
            reason = "no_authorized_adb_device"
        summary = {
            "state": "blocked_device_unavailable",
            "reason": reason,
            "captured_at": started.isoformat(timespec="seconds"),
            "adb_path": args.adb_path,
            "devices": devices,
            "active_locks": locks,
            "run_dir": str(run_dir),
        }
        write_json(run_dir / "summary.json", summary)
        write_json(run_dir / "coverage_assessment.json", initial_coverage())
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        if args.allow_missing_device:
            return
        raise SystemExit(2)

    device_id = selected["serial"]
    activities = run_adb(args.adb_path, device_id, "shell", "dumpsys", "activity", "activities", timeout=30)
    windows = run_adb(args.adb_path, device_id, "shell", "dumpsys", "window", "windows", timeout=30)
    front_package, front_activity = parse_front_component(activities.stdout + "\n" + windows.stdout)
    packages_result = run_adb(args.adb_path, device_id, "shell", "pm", "list", "packages", "-3", timeout=30)
    installed = [line.split("package:", 1)[1].strip() for line in packages_result.stdout.splitlines() if line.startswith("package:")]
    package_name, package_reason = choose_package(args.package, front_package, installed)

    device_props = {}
    for key in ("ro.product.manufacturer", "ro.product.model", "ro.build.version.release", "ro.build.version.sdk"):
        prop = run_adb(args.adb_path, device_id, "shell", "getprop", key, timeout=15)
        device_props[key] = prop.stdout.strip() if prop.returncode == 0 else None

    evidence: dict[str, Any] = {
        "captured_at": started.isoformat(timespec="seconds"),
        "device": {**selected, **device_props},
        "foreground_package": front_package,
        "foreground_activity": front_activity,
        "package_selection_reason": package_reason,
        "third_party_package_count": len(installed),
        "package_hint_matches": sorted(pkg for pkg in installed if any(h in pkg.lower() for h in PACKAGE_HINTS)),
    }

    if not package_name:
        summary = {
            "state": "blocked_qieman_package_not_identified",
            "reason": package_reason,
            "captured_at": started.isoformat(timespec="seconds"),
            "device_id": device_id,
            "foreground_package": front_package,
            "foreground_activity": front_activity,
            "action": "keep Qieman in the foreground and rerun, or pass --package after verification",
            "run_dir": str(run_dir),
        }
        write_json(run_dir / "device_evidence.json", evidence)
        write_json(run_dir / "summary.json", summary)
        write_json(run_dir / "coverage_assessment.json", initial_coverage())
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        raise SystemExit(4)

    package_dump = run_adb(args.adb_path, device_id, "shell", "dumpsys", "package", package_name, timeout=60)
    package_paths_result = run_adb(args.adb_path, device_id, "shell", "pm", "path", package_name, timeout=30)
    package_paths = parse_package_paths(package_paths_result.stdout)
    launcher_result = run_adb(
        args.adb_path,
        device_id,
        "shell",
        "cmd",
        "package",
        "resolve-activity",
        "--brief",
        package_name,
        timeout=20,
    )
    package_metadata = {
        "package_name": package_name,
        "selection_reason": package_reason,
        "launcher_activity": launcher_result.stdout.strip() or None,
        "installed_apk_paths": package_paths,
        **parse_package_metadata(package_dump.stdout),
    }

    ui_summary = capture_ui(args.adb_path, device_id, run_dir / "ui" / "window.xml")
    evidence["ui"] = ui_summary
    evidence["webview"] = webview_targets(args.adb_path, device_id)

    apk_inventories: list[dict[str, Any]] = []
    if not args.skip_apk_pull:
        apk_dir = run_dir / "apk"
        apk_dir.mkdir(parents=True, exist_ok=True)
        for index, remote_path in enumerate(package_paths):
            local_name = Path(remote_path).name or f"split_{index}.apk"
            if any(item.name == local_name for item in apk_dir.iterdir()):
                local_name = f"{index}_{local_name}"
            local_path = apk_dir / local_name
            pull = run_adb(args.adb_path, device_id, "pull", remote_path, str(local_path), timeout=180)
            if pull.returncode == 0 and local_path.is_file():
                apk_inventories.append(inventory_apk(local_path))
            else:
                apk_inventories.append(
                    {
                        "remote_path": remote_path,
                        "pull_status": "failed",
                        "error": redact_sensitive_text(pull.stderr or pull.stdout),
                    }
                )

    if args.capture_screenshot:
        screenshot = run_adb(args.adb_path, device_id, "exec-out", "screencap", "-p", timeout=30, binary=True)
        if screenshot.returncode == 0 and screenshot.stdout:
            screenshot_path = run_dir / "ui" / "screen.png"
            screenshot_path.parent.mkdir(parents=True, exist_ok=True)
            screenshot_path.write_bytes(screenshot.stdout)
            evidence["screenshot"] = {"path": str(screenshot_path), "sha256": sha256_file(screenshot_path)}
        else:
            evidence["screenshot"] = {"status": "failed"}

    write_json(run_dir / "device_evidence.json", evidence)
    write_json(run_dir / "package_metadata.json", package_metadata)
    write_json(run_dir / "apk_inventory.json", {"packages": apk_inventories})
    write_json(run_dir / "coverage_assessment.json", initial_coverage())
    summary = {
        "state": "ready_entry_captured",
        "captured_at": started.isoformat(timespec="seconds"),
        "device_id": device_id,
        "package_name": package_name,
        "version_name": package_metadata.get("version_name"),
        "launcher_activity": package_metadata.get("launcher_activity"),
        "foreground_package": front_package,
        "foreground_activity": front_activity,
        "ui_status": ui_summary.get("status"),
        "webview_target_count": len(evidence["webview"].get("targets", [])),
        "apk_count": len(apk_inventories),
        "sample_strategy_count": 0,
        "next_step": "select 3-5 representative strategies and validate direct list/detail/performance/position interfaces",
        "run_dir": str(run_dir),
    }
    write_json(run_dir / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
