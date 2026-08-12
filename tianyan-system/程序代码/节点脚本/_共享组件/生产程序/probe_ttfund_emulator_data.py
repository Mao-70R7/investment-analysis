from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_ADB = PROJECT_ROOT / "tools" / "platform-tools" / "adb.exe"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "data" / "raw" / "ttfund" / "emulator_probe"
TTFUND_PACKAGE = "com.eastmoney.android.fund"
TTFUND_APP_ID = "funda91a99886abf7e"
REMOTE_CACHE_DIR = f"/sdcard/Android/data/{TTFUND_PACKAGE}/files/.ttjj_cache"

DETAIL_PATTERNS = (
    re.compile(r"^strategyDetailPageData(?P<sid>[A-Za-z0-9]+)_"),
    re.compile(r"^ttfund-layout-cache-advicer-strategy-detail-matter-(?P<sid>[A-Za-z0-9]+)-"),
)
LATEST_REBALANCE_PATTERN = re.compile(r"^adjuseHouseList(?P<sid>[A-Za-z0-9]+)_")
HISTORY_REBALANCE_PATTERN = re.compile(r"^adjuseHouseListHis(?P<sid>[A-Za-z0-9]+)_")

EMULATOR_MODEL_HINTS = (
    "sdk",
    "emulator",
    "android sdk",
    "mumu",
    "nox",
    "ldplayer",
    "genymotion",
    "bluestacks",
    "vbox",
    "virtual",
)


@dataclass
class AdbDevice:
    serial: str
    state: str
    detail: str
    props: dict[str, str]
    is_emulator: bool


def now_local() -> datetime:
    return datetime.now().astimezone()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Probe TTFund advisor data collection through an Android emulator. "
            "This script writes only to data/raw/ttfund/emulator_probe and never updates "
            "the production device_cache mirror or SQLite databases."
        )
    )
    parser.add_argument("--adb-path", type=Path, default=DEFAULT_ADB)
    parser.add_argument("--device-id", type=str, default=None)
    parser.add_argument("--list-devices", action="store_true")
    parser.add_argument(
        "--allow-non-emulator",
        action="store_true",
        help="Allow a physical Android device. Disabled by default so the probe does not accidentally use the production phone.",
    )
    parser.add_argument("--strategy-id", action="append", default=[], dest="strategy_ids")
    parser.add_argument("--strategy-file", type=Path, default=None)
    parser.add_argument("--sample-size", type=int, default=3)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--launch-warmup-sec", type=int, default=8)
    parser.add_argument("--detail-scan-swipes", type=int, default=1)
    parser.add_argument(
        "--deeplink-mode",
        choices=("guodu", "show_kyc", "plain"),
        default="guodu",
    )
    parser.add_argument(
        "--skip-launch",
        action="store_true",
        help="Do not open strategy pages; only pull and validate existing remote cache files.",
    )
    parser.add_argument(
        "--fail-on-invalid",
        action="store_true",
        help="Exit non-zero when no selected strategy validates successfully.",
    )
    return parser.parse_args()


def resolve_adb_path(path: Path) -> Path:
    if path.exists():
        return path
    found = shutil.which(str(path))
    if found:
        return Path(found)
    raise FileNotFoundError(f"adb not found: {path}")


def run_command(args: list[str], timeout: int = 30, binary: bool = False) -> subprocess.CompletedProcess[Any]:
    if binary:
        return subprocess.run(args, capture_output=True, timeout=timeout, check=False)
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def adb_run(adb: Path, device_id: str, *args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return run_command([str(adb), "-s", device_id, *args], timeout=timeout)


def adb_shell(adb: Path, device_id: str, command: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return adb_run(adb, device_id, "shell", command, timeout=timeout)


def adb_no_device(adb: Path, *args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return run_command([str(adb), *args], timeout=timeout)


def get_prop(adb: Path, serial: str, name: str) -> str:
    completed = adb_shell(adb, serial, f"getprop {name}", timeout=10)
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def classify_emulator(serial: str, detail: str, props: dict[str, str]) -> bool:
    if serial.startswith("emulator-") or serial.startswith("127.0.0.1:") or serial.startswith("localhost:"):
        return True
    if props.get("ro.kernel.qemu") == "1" or props.get("ro.boot.qemu") == "1":
        return True
    haystack = " ".join([serial, detail, *props.values()]).lower()
    return any(hint in haystack for hint in EMULATOR_MODEL_HINTS)


def list_adb_devices(adb: Path) -> list[AdbDevice]:
    completed = adb_no_device(adb, "devices", "-l", timeout=20)
    devices: list[AdbDevice] = []
    for line in completed.stdout.splitlines()[1:]:
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        serial = parts[0]
        state = parts[1]
        detail = " ".join(parts[2:])
        props: dict[str, str] = {}
        if state == "device":
            for prop in (
                "ro.kernel.qemu",
                "ro.boot.qemu",
                "ro.product.manufacturer",
                "ro.product.brand",
                "ro.product.model",
                "ro.hardware",
                "ro.build.characteristics",
            ):
                props[prop] = get_prop(adb, serial, prop)
        devices.append(
            AdbDevice(
                serial=serial,
                state=state,
                detail=detail,
                props=props,
                is_emulator=classify_emulator(serial, detail, props),
            )
        )
    return devices


def select_device(adb: Path, requested: str | None, allow_non_emulator: bool) -> AdbDevice:
    devices = list_adb_devices(adb)
    ready = [device for device in devices if device.state == "device"]
    if requested:
        matches = [device for device in ready if device.serial == requested]
        if not matches:
            raise RuntimeError(f"requested ADB device is not ready: {requested}")
        selected = matches[0]
    else:
        emulators = [device for device in ready if device.is_emulator]
        if not emulators:
            raise RuntimeError("no ready emulator device detected; run with --list-devices to inspect current ADB devices")
        if len(emulators) > 1:
            serials = ", ".join(device.serial for device in emulators)
            raise RuntimeError(f"multiple emulator devices detected, please pass --device-id: {serials}")
        selected = emulators[0]
    if not selected.is_emulator and not allow_non_emulator:
        raise RuntimeError(
            f"selected device {selected.serial} does not look like an emulator; "
            "pass --allow-non-emulator only for isolated script validation"
        )
    return selected


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_json(path: Path) -> Any | None:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
        value = json.loads(raw)
        if isinstance(value, str):
            inner = value.strip()
            if inner and inner[0] in "[{":
                return json.loads(inner)
        return value
    except Exception:
        return None


def norm_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text in {"--", "null", "None"}:
        return None
    return text


def strategy_id_from_filename(name: str) -> str | None:
    for pattern in (*DETAIL_PATTERNS, HISTORY_REBALANCE_PATTERN, LATEST_REBALANCE_PATTERN):
        match = pattern.search(name)
        if match:
            return match.group("sid")
    return None


def is_strategy_cache_file(name: str, strategy_id: str) -> bool:
    sid = strategy_id_from_filename(name)
    if sid != strategy_id:
        return False
    return (
        name.startswith("strategyDetailPageData")
        or name.startswith("ttfund-layout-cache-advicer-strategy-detail-matter-")
        or name.startswith("adjuseHouseList")
    )


def sample_ids_from_file(path: Path) -> list[str]:
    ids: list[str] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        value = line.strip()
        if value and not value.startswith("#") and value not in ids:
            ids.append(value)
    return ids


def sample_ids_from_sqlite(limit: int) -> list[str]:
    db_path = PROJECT_ROOT / "data" / "advisor_monitor.sqlite"
    if not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute(
            """
            SELECT source_strategy_id
            FROM strategy_master
            WHERE channel_id = 'ttfund'
              AND source_strategy_id IS NOT NULL
              AND source_strategy_id <> ''
            ORDER BY last_seen_at DESC, source_strategy_id
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [str(row[0]).strip() for row in rows if str(row[0]).strip()]
    except sqlite3.Error:
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


def sample_ids_from_local_cache(limit: int) -> list[str]:
    root = PROJECT_ROOT / "data" / "raw" / "device_cache"
    if not root.exists():
        return []
    candidates = sorted(
        [path for path in root.iterdir() if path.is_dir()],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    ids: list[str] = []
    for path in candidates:
        sid = path.name
        if not re.fullmatch(r"[A-Za-z0-9]+", sid):
            continue
        detail_files = [
            file
            for file in path.glob("*.0")
            if is_strategy_cache_file(file.name, sid)
            and file.stat().st_size > 8
            and (
                file.name.startswith("strategyDetailPageData")
                or file.name.startswith("ttfund-layout-cache-advicer-strategy-detail-matter-")
            )
        ]
        if detail_files and sid not in ids:
            ids.append(sid)
        if len(ids) >= limit:
            break
    return ids


def choose_strategy_ids(args: argparse.Namespace) -> tuple[list[str], str]:
    ids: list[str] = []
    for strategy_id in args.strategy_ids:
        for part in str(strategy_id).split(","):
            value = part.strip()
            if value and value not in ids:
                ids.append(value)
    if args.strategy_file:
        for value in sample_ids_from_file(args.strategy_file):
            if value not in ids:
                ids.append(value)
    if ids:
        return ids[: args.sample_size or len(ids)], "explicit"
    db_ids = sample_ids_from_sqlite(args.sample_size)
    if db_ids:
        return db_ids, "advisor_monitor.sqlite"
    cache_ids = sample_ids_from_local_cache(args.sample_size)
    if cache_ids:
        return cache_ids, "local_device_cache"
    raise FileNotFoundError("no strategy ids supplied and no local strategy sample source found")


def build_strategy_url(strategy_id: str, deeplink_mode: str) -> str:
    query = {"id": strategy_id}
    if deeplink_mode == "guodu":
        query["fromOutStrategy"] = "toGuoduChoose"
    elif deeplink_mode == "show_kyc":
        query["showKycPopup"] = "1"
    inner = (
        f"fund://mp.1234567.com.cn/weex/{TTFUND_APP_ID}/pages/strategyDetail/index?"
        f"{urllib.parse.urlencode(query)}"
    )
    wrapper = {
        "LinkTo": inner,
        "LinkType": 2,
        "AdId": "0",
        "IsVerifyLogin": False,
        "CloseWeex": False,
    }
    encoded = urllib.parse.quote(json.dumps(wrapper, ensure_ascii=False, separators=(",", ":")))
    return f"eastmoneyjijin://startapp/toPage?type=8&linkto={encoded}"


def launch_strategy(adb: Path, device_id: str, strategy_id: str, deeplink_mode: str) -> dict[str, Any]:
    url = build_strategy_url(strategy_id, deeplink_mode)
    completed = adb_shell(
        adb,
        device_id,
        f"am start -a android.intent.action.VIEW -d '{url}'",
        timeout=45,
    )
    return {
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }


def list_remote_cache_entries(adb: Path, device_id: str, strategy_id: str | None = None) -> list[dict[str, Any]]:
    glob_pattern = "*.0"
    if strategy_id:
        if not re.fullmatch(r"[A-Za-z0-9]+", strategy_id):
            raise ValueError(f"invalid strategy id for remote glob: {strategy_id}")
        glob_pattern = f"*{strategy_id}*.0"
    command = (
        f"for f in {REMOTE_CACHE_DIR}/{glob_pattern}; do "
        'if [ -f "$f" ]; then '
        'b=$(basename "$f"); '
        's=$(wc -c < "$f" 2>/dev/null); '
        'echo "$b|$s"; '
        "fi; "
        "done"
    )
    completed = adb_shell(adb, device_id, command, timeout=60)
    entries: list[dict[str, Any]] = []
    if completed.returncode != 0:
        return entries
    for line in completed.stdout.splitlines():
        line = line.strip()
        if not line or "|" not in line:
            continue
        name, size_text = line.rsplit("|", 1)
        try:
            size = int(size_text.strip())
        except ValueError:
            size = None
        entries.append({"name": name.strip(), "size": size})
    return entries


def pull_strategy_files(adb: Path, device_id: str, strategy_id: str, entries: list[dict[str, Any]], target_dir: Path) -> dict[str, str]:
    target_dir.mkdir(parents=True, exist_ok=True)
    pulled: dict[str, str] = {}
    for entry in entries:
        name = str(entry.get("name") or "")
        if not is_strategy_cache_file(name, strategy_id):
            continue
        remote = f"{REMOTE_CACHE_DIR}/{name}"
        local = target_dir / name
        completed = adb_run(adb, device_id, "pull", remote, str(local), timeout=60)
        if completed.returncode == 0 and local.exists():
            pulled[name] = str(local)
    return pulled


def count_holding_funds(holding_info: dict[str, Any]) -> int:
    total = 0
    for group in holding_info.get("holdTypeList") or []:
        if isinstance(group, dict):
            total += len(group.get("fundList") or [])
    return total


def validate_strategy_cache(strategy_id: str, pulled: dict[str, str]) -> dict[str, Any]:
    detail_candidates: list[tuple[int, str, Path, dict[str, Any]]] = []
    latest_adjustment_ok = False
    history_adjustment_ok = False
    history_event_count = 0
    history_delta_count = 0
    latest_adjustment_size: int | None = None
    history_adjustment_size: int | None = None

    for name, path_text in pulled.items():
        path = Path(path_text)
        payload = load_json(path)
        size = path.stat().st_size if path.exists() else None
        if (
            name.startswith("strategyDetailPageData")
            or name.startswith("ttfund-layout-cache-advicer-strategy-detail-matter-")
        ) and isinstance(payload, dict):
            if payload.get("tgExtendInfo"):
                priority = 2 if name.startswith("strategyDetailPageData") else 1
                detail_candidates.append((priority, name, path, payload))
        elif name.startswith(f"adjuseHouseListHis{strategy_id}"):
            history_adjustment_size = size
            if isinstance(payload, dict) and isinstance(payload.get("adjustList"), list):
                history_adjustment_ok = True
                events = payload.get("adjustList") or []
                history_event_count = len(events)
                for event in events:
                    for group in event.get("arr") or []:
                        history_delta_count += len(group.get("changeList") or [])
        elif name.startswith(f"adjuseHouseList{strategy_id}"):
            latest_adjustment_size = size
            if isinstance(payload, dict) and (payload.get("dateStr") or payload.get("reason") or payload.get("adjustList")):
                latest_adjustment_ok = True

    detail_candidates.sort(key=lambda row: row[0], reverse=True)
    result: dict[str, Any] = {
        "strategy_id": strategy_id,
        "detail_ok": False,
        "detail_source_file": None,
        "detail_size": None,
        "strategy_name": None,
        "advisor_institution": None,
        "risk_level": None,
        "benchmark_text_ok": False,
        "benchmark_text": None,
        "holding_info_ok": False,
        "holding_group_count": 0,
        "holding_fund_count": 0,
        "performance_stage_ok": False,
        "performance_stage_count": 0,
        "latest_adjustment_ok": latest_adjustment_ok,
        "history_adjustment_ok": history_adjustment_ok,
        "latest_adjustment_size": latest_adjustment_size,
        "history_adjustment_size": history_adjustment_size,
        "history_event_count": history_event_count,
        "history_delta_count": history_delta_count,
        "pulled_file_total": len(pulled),
    }
    if not detail_candidates:
        return result

    _, name, path, payload = detail_candidates[0]
    extend_info = payload.get("tgExtendInfo") or {}
    cfh_info = payload.get("cfhInfo") or {}
    holding_info = payload.get("holdWareHouseInfo") or {}
    stage_list = extend_info.get("stageListAll") or []
    benchmark_text = norm_text(extend_info.get("basicCalFormulaRemark"))
    result.update(
        {
            "detail_ok": True,
            "detail_source_file": name,
            "detail_size": path.stat().st_size,
            "strategy_name": norm_text(extend_info.get("tgName") or extend_info.get("name")),
            "advisor_institution": norm_text(extend_info.get("logoName") or cfh_info.get("fortuneName")),
            "risk_level": norm_text(extend_info.get("risk")),
            "benchmark_text_ok": bool(benchmark_text),
            "benchmark_text": benchmark_text,
            "holding_info_ok": bool(holding_info.get("holdTypeList")),
            "holding_group_count": len(holding_info.get("holdTypeList") or []),
            "holding_fund_count": count_holding_funds(holding_info),
            "performance_stage_ok": bool(stage_list),
            "performance_stage_count": len(stage_list),
        }
    )
    return result


def dump_ui(adb: Path, device_id: str, out_path: Path) -> dict[str, Any]:
    remote = "/sdcard/ttfund_emulator_probe_uidump.xml"
    result = adb_shell(adb, device_id, f"uiautomator dump {remote}", timeout=30)
    dumped = "dumped to:" in f"{result.stdout}\n{result.stderr}".lower()
    if result.returncode != 0 and not dumped:
        return {"ok": False, "error": result.stderr.strip() or result.stdout.strip()}
    pulled = adb_run(adb, device_id, "pull", remote, str(out_path), timeout=30)
    if pulled.returncode != 0 or not out_path.exists():
        return {"ok": False, "error": pulled.stderr.strip() or pulled.stdout.strip()}
    try:
        root = ET.fromstring(out_path.read_text(encoding="utf-8", errors="replace"))
        visible_texts = []
        for element in root.iter():
            text = element.attrib.get("content-desc") or element.attrib.get("text") or ""
            text = text.strip()
            if text and text not in visible_texts:
                visible_texts.append(text)
            if len(visible_texts) >= 20:
                break
        return {"ok": True, "visible_text_sample": visible_texts}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def device_health(adb: Path, device: AdbDevice) -> dict[str, Any]:
    state = adb_run(adb, device.serial, "get-state", timeout=10)
    app = adb_shell(adb, device.serial, f"pm path {TTFUND_PACKAGE}", timeout=15)
    cache = adb_shell(adb, device.serial, f"ls -d {REMOTE_CACHE_DIR}", timeout=15)
    wm_size = adb_shell(adb, device.serial, "wm size", timeout=10)
    return {
        "state_ok": state.returncode == 0 and state.stdout.strip() == "device",
        "app_installed": app.returncode == 0 and bool(app.stdout.strip()),
        "app_path": app.stdout.strip(),
        "cache_dir_accessible": cache.returncode == 0,
        "cache_dir_error": cache.stderr.strip() if cache.returncode != 0 else None,
        "wm_size": wm_size.stdout.strip(),
    }


def probe_strategy(adb: Path, device_id: str, strategy_id: str, args: argparse.Namespace, run_dir: Path) -> dict[str, Any]:
    strategy_dir = run_dir / "strategies" / strategy_id
    strategy_dir.mkdir(parents=True, exist_ok=True)
    before_entries = list_remote_cache_entries(adb, device_id, strategy_id)
    launch_result: dict[str, Any] | None = None
    ui_result: dict[str, Any] | None = None

    if not args.skip_launch:
        adb_shell(adb, device_id, "input keyevent 224", timeout=10)
        adb_shell(adb, device_id, f"monkey -p {TTFUND_PACKAGE} 1", timeout=30)
        time.sleep(1)
        launch_result = launch_strategy(adb, device_id, strategy_id, args.deeplink_mode)
        time.sleep(max(args.launch_warmup_sec, 0))
        for _ in range(max(args.detail_scan_swipes, 0)):
            adb_shell(adb, device_id, "input swipe 540 1800 540 820 350", timeout=20)
            time.sleep(1)
        ui_result = dump_ui(adb, device_id, strategy_dir / "uidump.xml")

    after_entries = list_remote_cache_entries(adb, device_id, strategy_id)
    pulled = pull_strategy_files(adb, device_id, strategy_id, after_entries, strategy_dir / "cache")
    validation = validate_strategy_cache(strategy_id, pulled)
    before_names = {entry["name"] for entry in before_entries if is_strategy_cache_file(str(entry.get("name") or ""), strategy_id)}
    after_names = {entry["name"] for entry in after_entries if is_strategy_cache_file(str(entry.get("name") or ""), strategy_id)}
    result = {
        "strategy_id": strategy_id,
        "launch": launch_result,
        "ui": ui_result,
        "remote_cache_before_total": len(before_names),
        "remote_cache_after_total": len(after_names),
        "remote_cache_new_files": sorted(after_names - before_names),
        "pulled_files": pulled,
        "validation": validation,
        "ok": bool(validation.get("detail_ok") and (validation.get("holding_info_ok") or validation.get("performance_stage_ok"))),
    }
    write_json(strategy_dir / "result.json", result)
    return result


def main() -> None:
    args = parse_args()
    adb = resolve_adb_path(args.adb_path)

    if args.list_devices:
        devices = list_adb_devices(adb)
        payload = {
            "adb_path": str(adb),
            "devices": [
                {
                    "serial": device.serial,
                    "state": device.state,
                    "is_emulator": device.is_emulator,
                    "detail": device.detail,
                    "props": device.props,
                }
                for device in devices
            ],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    run_at = now_local()
    run_id = run_at.strftime("%Y%m%dT%H%M%S%z")
    run_dir = args.output_root / run_at.strftime("%Y-%m-%d") / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    summary: dict[str, Any] = {
        "state": "running",
        "run_id": run_id,
        "captured_at": run_at.isoformat(timespec="seconds"),
        "adb_path": str(adb),
        "run_dir": str(run_dir),
        "remote_cache_dir": REMOTE_CACHE_DIR,
        "writes_production_cache": False,
        "writes_sqlite": False,
    }
    try:
        device = select_device(adb, args.device_id, args.allow_non_emulator)
        strategy_ids, source = choose_strategy_ids(args)
        write_json(run_dir / "strategy_ids.json", {"source": source, "strategy_ids": strategy_ids})

        health = device_health(adb, device)
        summary.update(
            {
                "device": {
                    "serial": device.serial,
                    "is_emulator": device.is_emulator,
                    "state": device.state,
                    "detail": device.detail,
                    "props": device.props,
                },
                "strategy_id_source": source,
                "strategy_ids": strategy_ids,
                "device_health": health,
            }
        )
        if not health["state_ok"]:
            raise RuntimeError("selected device is not in adb device state")
        if not health["app_installed"]:
            raise RuntimeError(f"{TTFUND_PACKAGE} is not installed on selected device")
        if not health["cache_dir_accessible"]:
            raise RuntimeError(f"remote cache directory is not accessible: {REMOTE_CACHE_DIR}")

        results = [probe_strategy(adb, device.serial, strategy_id, args, run_dir) for strategy_id in strategy_ids]
        ok_total = sum(1 for row in results if row.get("ok"))
        detail_ok_total = sum(1 for row in results if row.get("validation", {}).get("detail_ok"))
        holding_ok_total = sum(1 for row in results if row.get("validation", {}).get("holding_info_ok"))
        summary.update(
            {
                "state": "completed" if ok_total else "completed_no_valid_strategy",
                "strategy_total": len(strategy_ids),
                "ok_total": ok_total,
                "detail_ok_total": detail_ok_total,
                "holding_ok_total": holding_ok_total,
                "results": results,
            }
        )
        if args.fail_on_invalid and ok_total == 0:
            summary["state"] = "failed_no_valid_strategy"
            write_json(run_dir / "summary.json", summary)
            raise SystemExit(2)
    except Exception as exc:
        summary["state"] = "failed"
        summary["error"] = str(exc)
        summary["devices"] = [
            {
                "serial": device.serial,
                "state": device.state,
                "is_emulator": device.is_emulator,
                "detail": device.detail,
                "props": device.props,
            }
            for device in list_adb_devices(adb)
        ]
        write_json(run_dir / "summary.json", summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        raise SystemExit(1)

    write_json(run_dir / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
