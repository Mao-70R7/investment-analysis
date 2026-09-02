from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
TTFUND_PACKAGE = "com.eastmoney.android.fund"
REMOTE_CACHE_DIR = f"/sdcard/Android/data/{TTFUND_PACKAGE}/files/.ttjj_cache"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "raw" / "device_cache"
APP_ID = "funda91a99886abf7e"

DETAIL_PATTERNS = (
    re.compile(r"strategyDetailPageData(?P<sid>[A-Za-z0-9]+)_"),
    re.compile(r"ttfund-layout-cache-advicer-strategy-detail-matter-(?P<sid>[A-Za-z0-9]+)-"),
)
ADJUSTMENT_PATTERNS = (
    re.compile(r"adjuseHouseListHis(?P<sid>[A-Za-z0-9]+)_"),
    re.compile(r"adjuseHouseList(?P<sid>[A-Za-z0-9]+)_"),
)
HOME_PREFIXES = (
    "layout_tougu-scroll-view",
    "saveAllAdvisersInfokey",
    "home-vuex_",
    "EFAppHomeConfigData",
)


def now_local() -> datetime:
    return datetime.now().astimezone()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Lightly sync TTFund Android .ttjj_cache into the local device_cache mirror."
    )
    parser.add_argument("--device-id", required=True)
    parser.add_argument("--adb-path", default="adb")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--run-dir", type=Path, default=None)
    parser.add_argument("--warmup-sec", type=int, default=6)
    parser.add_argument("--skip-launch-app", action="store_true")
    parser.add_argument(
        "--allow-missing-device",
        action="store_true",
        help="Return success with a skipped summary when the ADB device is unavailable.",
    )
    return parser.parse_args()


def run_adb(adb_path: str, device_id: str, *args: str, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [adb_path, "-s", device_id, *args],
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


def strategy_id_from_name(name: str) -> str | None:
    for pattern in DETAIL_PATTERNS + ADJUSTMENT_PATTERNS:
        match = pattern.search(name)
        if match:
            return match.group("sid")
    return None


def is_home_cache(name: str) -> bool:
    return name.startswith(HOME_PREFIXES)


def iter_pulled_files(root: Path) -> list[Path]:
    candidates = [path for path in root.rglob("*") if path.is_file()]
    if not candidates:
        return []
    return sorted(candidates)


def copy_into_mirror(source: Path, output_dir: Path) -> tuple[Path, bool]:
    sid = strategy_id_from_name(source.name)
    if sid:
        target = output_dir / sid / source.name
    elif is_home_cache(source.name):
        target = output_dir / source.name
    else:
        target = output_dir / "_misc" / source.name
    target.parent.mkdir(parents=True, exist_ok=True)
    changed = True
    if target.exists() and target.stat().st_size == source.stat().st_size:
        try:
            changed = target.read_bytes() != source.read_bytes()
        except OSError:
            changed = True
    if changed:
        shutil.copy2(source, target)
    return target, changed


def skipped_summary(args: argparse.Namespace, run_at: datetime, state: str, reason: str) -> dict[str, Any]:
    return {
        "state": state,
        "reason": reason,
        "captured_at": run_at.isoformat(timespec="seconds"),
        "device_id": args.device_id,
        "remote_cache_dir": REMOTE_CACHE_DIR,
        "output_dir": str(args.output_dir),
        "file_total": 0,
        "changed_total": 0,
        "home_cache_total": 0,
        "strategy_cache_total": 0,
    }


def main() -> None:
    args = parse_args()
    run_at = now_local()
    run_id = run_at.strftime("%Y%m%dT%H%M%S%z")
    run_dir = args.run_dir or (
        PROJECT_ROOT / "data" / "raw" / "ttfund" / "device_cache_sync" / run_at.strftime("%Y-%m-%d") / run_id
    )
    run_dir.mkdir(parents=True, exist_ok=True)

    state = run_adb(args.adb_path, args.device_id, "get-state", timeout=15)
    if state.returncode != 0 or state.stdout.strip() != "device":
        summary = skipped_summary(args, run_at, "skipped_device_unavailable", state.stderr.strip() or state.stdout.strip())
        write_json(run_dir / "summary.json", summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        if args.allow_missing_device:
            return
        raise SystemExit(2)

    if not args.skip_launch_app:
        run_adb(args.adb_path, args.device_id, "shell", "monkey", "-p", TTFUND_PACKAGE, "1", timeout=30)
        if args.warmup_sec > 0:
            time.sleep(args.warmup_sec)

    temp_root = Path(tempfile.mkdtemp(prefix="ttfund_device_cache_"))
    try:
        pull = run_adb(args.adb_path, args.device_id, "pull", REMOTE_CACHE_DIR, str(temp_root), timeout=180)
        (run_dir / "adb_pull.stdout.log").write_text(pull.stdout, encoding="utf-8")
        (run_dir / "adb_pull.stderr.log").write_text(pull.stderr, encoding="utf-8")
        if pull.returncode != 0:
            summary = skipped_summary(args, run_at, "skipped_pull_failed", pull.stderr.strip() or pull.stdout.strip())
            write_json(run_dir / "summary.json", summary)
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            if args.allow_missing_device:
                return
            raise SystemExit(pull.returncode)

        files = iter_pulled_files(temp_root)
        copied: list[dict[str, Any]] = []
        strategy_ids: set[str] = set()
        home_total = 0
        changed_total = 0
        for source in files:
            target, changed = copy_into_mirror(source, args.output_dir)
            sid = strategy_id_from_name(source.name)
            if sid:
                strategy_ids.add(sid)
            if is_home_cache(source.name):
                home_total += 1
            if changed:
                changed_total += 1
            copied.append(
                {
                    "source": str(source),
                    "target": str(target),
                    "strategy_id": sid,
                    "changed": changed,
                }
            )

        summary = {
            "state": "ready",
            "captured_at": run_at.isoformat(timespec="seconds"),
            "run_id": run_id,
            "device_id": args.device_id,
            "remote_cache_dir": REMOTE_CACHE_DIR,
            "output_dir": str(args.output_dir),
            "run_dir": str(run_dir),
            "file_total": len(files),
            "changed_total": changed_total,
            "home_cache_total": home_total,
            "strategy_cache_total": len(strategy_ids),
            "strategy_ids_sample": sorted(strategy_ids)[:30],
        }
        write_json(run_dir / "summary.json", summary)
        write_json(run_dir / "copied_files.json", copied)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
