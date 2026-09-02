from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
import urllib.parse
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
sys.path.insert(0, str(PROJECT_ROOT / "节点脚本" / "_共享组件" / "python_src"))

from advisor_monitor.progress import ConsoleProgress  # noqa: E402


DEVICE_CACHE_DIR = "/sdcard/Android/data/com.eastmoney.android.fund/files/.ttjj_cache"
DEFAULT_ADB = PROJECT_ROOT / "tools" / "platform-tools" / "adb.exe"
DATA_ROOT = Path(os.environ.get("ADVISOR_DATABASE_ROOT") or PROJECT_ROOT / "data").resolve()
RAW_ROOT = Path(os.environ.get("ADVISOR_RAW_ROOT") or DATA_ROOT / "raw").resolve()
NORMALIZED_ROOT = Path(os.environ.get("ADVISOR_NORMALIZED_ROOT") or DATA_ROOT / "normalized").resolve()
TTFUND_PACKAGE = "com.eastmoney.android.fund"
APP_ID = "funda91a99886abf7e"
LABEL_CONFIRM_VIEW_VARIANTS = ("\u786e\u8ba4\u67e5\u770b", "纭鏌ョ湅")
LABEL_CONFIRM_SELECT_VARIANTS = ("\u786e\u8ba4\u9009\u62e9", "纭閫夋嫨")
LABEL_SERVICE_FEE_VARIANTS = ("\u6295\u987e\u670d\u52a1\u8d39\u7387", "鎶曢【鏈嶅姟璐圭巼")
LABEL_DYNAMIC_TAB_VARIANTS = ("\u8c03\u4ed3\u52a8\u6001", "璋冧粨鍔ㄦ€")
LABEL_HISTORY_ENTRY_VARIANTS = ("\u8c03\u4ed3\u5386\u53f2", "璋冧粨鍘嗗彶")
LABEL_HISTORY_TITLE_VARIANTS = ("\u603b\u8ba1\u8c03\u4ed3\u8bb0\u5f55", "鎬昏璋冧粨璁板綍")
LABEL_RULES_TITLE_VARIANTS = ("\u4ea4\u6613\u89c4\u5219",)
LABEL_LOAD_FAILED_VARIANTS = (
    "\u6570\u636e\u52a0\u8f7d\u5931\u8d25",
    "\u70b9\u51fb\u91cd\u8bd5",
    "鏁版嵁鍔犺浇澶辫触",
    "鐐瑰嚮閲嶈瘯",
    "2001",
    "001",
)
LABEL_LOADING_VARIANTS = (
    "\u6b63\u5728\u52a0\u8f7d",
    "\u8bf7\u7a0d\u5019",
    "99%",
)
LABEL_PERFORMANCE_SECTION_VARIANTS = (
    "\u4e1a\u7ee9\u8868\u73b0",
    "\u7d2f\u8ba1\u6536\u76ca\u7387",
    "\u57fa\u51c6\u6da8\u8dcc\u5e45",
)
LABEL_BENCHMARK_DROPDOWN_VARIANTS = (
    "\u57fa\u51c6\u6da8\u8dcc\u5e45",
    "\u4e1a\u7ee9\u6bd4\u8f83\u57fa\u51c6",
    "\u4e1a\u7ee9\u57fa\u51c6",
)
LABEL_BENCHMARK_TEXT_VARIANTS = (
    "\u4e1a\u7ee9\u6bd4\u8f83\u57fa\u51c6",
    "\u4e1a\u7ee9\u57fa\u51c6",
)
BENCHMARK_FORMULA_HINTS = (
    "\u6307\u6570",
    "\u4e2d\u8bc1",
    "\u6caa\u6df1",
    "\u4e0a\u8bc1",
    "\u56fd\u503a",
    "\u8d27\u5e01\u57fa\u91d1",
    "MSCI",
    "\u521b\u4e1a\u677f",
    "\u6052\u751f",
    "\u6807\u666e",
)


def now_local() -> datetime:
    return datetime.now().astimezone()


def load_latest_strategy_ids(limit: int | None = None, offset: int = 0) -> list[str]:
    summary_dir = NORMALIZED_ROOT / "ttfund" / "strategy_master"
    candidates = sorted(summary_dir.rglob("*.jsonl"))
    if not candidates:
        raise FileNotFoundError("no normalized strategy_master jsonl found")
    latest = candidates[-1]
    ids: list[str] = []
    with latest.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            strategy_id = str(row.get("source_strategy_id") or "").strip()
            if strategy_id and strategy_id not in ids:
                ids.append(strategy_id)
    if offset > 0:
        ids = ids[offset:]
    if limit is not None and limit > 0:
        ids = ids[:limit]
    return ids


def load_strategy_ids_from_file(path: Path) -> list[str]:
    ids: list[str] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            strategy_id = line.strip().lstrip("\ufeff")
            if strategy_id and not strategy_id.startswith("#"):
                ids.append(strategy_id)
    return ids


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Drive the TTFund Android app to open strategy detail/history pages and pull cache files."
    )
    parser.add_argument("--adb-path", type=Path, default=DEFAULT_ADB, help="Path to adb executable.")
    parser.add_argument("--device-id", type=str, required=True, help="ADB device serial.")
    parser.add_argument(
        "--strategy-id",
        action="append",
        dest="strategy_ids",
        default=[],
        help="Strategy id to process. Repeat for multiple ids.",
    )
    parser.add_argument(
        "--strategy-file",
        type=Path,
        default=None,
        help="Text file with one strategy id per line. Lines starting with # are ignored.",
    )
    parser.add_argument(
        "--work-bundle-file",
        type=Path,
        default=None,
        help=(
            "JSON strategy work bundles with per-field requirements. When no strategy ids are supplied, "
            "all bundle ids are processed once with the strongest required capture profile."
        ),
    )
    parser.add_argument(
        "--use-latest-master",
        action="store_true",
        help="Load strategy ids from the latest normalized strategy_master jsonl.",
    )
    parser.add_argument("--offset", type=int, default=0, help="Start position when using --use-latest-master.")
    parser.add_argument("--limit", type=int, default=None, help="Optional cap when using --use-latest-master.")
    parser.add_argument(
        "--deeplink-mode",
        choices=("guodu", "show_kyc", "plain"),
        default="guodu",
        help="Strategy detail deeplink query variant.",
    )
    parser.add_argument(
        "--missing-scope",
        choices=("detail", "latest_adjustment", "history_adjustment"),
        default=None,
        help="Only process strategy ids that do not yet have this cache type on the device.",
    )
    parser.add_argument(
        "--selection-only",
        action="store_true",
        help="Print the filtered strategy id selection and exit without driving the app.",
    )
    parser.add_argument("--max-swipes", type=int, default=5, help="Maximum swipe attempts to reach strategy section.")
    parser.add_argument("--swipe-wait-ms", type=int, default=1200, help="Wait after each swipe/tap in milliseconds.")
    parser.add_argument(
        "--detail-scan-swipes",
        type=int,
        default=0,
        help="Swipe the detail page before pulling cache to trigger lazy-loaded detail sections.",
    )
    parser.add_argument(
        "--skip-history",
        action="store_true",
        help="Only trigger detail cache, do not try to enter 调仓历史.",
    )
    parser.add_argument(
        "--current-holding-fast",
        action="store_true",
        help=(
            "Try a no-swipe current-holding capture first. Automatically run the full detail scan "
            "when the fast result does not contain a valid holdWareHouseInfo payload."
        ),
    )
    parser.add_argument(
        "--keep-run-cache",
        action="store_true",
        help="Store pulled cache files under this run directory in addition to device_cache mirror.",
    )
    parser.add_argument(
        "--capture-failures",
        action="store_true",
        help="Capture screenshot and XML when history page is not reached.",
    )
    parser.add_argument(
        "--run-dir",
        type=Path,
        default=None,
        help="Reuse an existing run directory to resume or continue writing results.",
    )
    parser.add_argument(
        "--skip-existing-results",
        action="store_true",
        help="When --run-dir is provided, skip strategies that already have result.json.",
    )
    parser.add_argument(
        "--require-benchmark-text",
        action="store_true",
        help="Treat a strategy as incomplete unless detail cache or visible UI contains benchmark text.",
    )
    parser.add_argument(
        "--fail-on-required-field-missing",
        action="store_true",
        help="Exit non-zero when a required field is still missing after retries.",
    )
    parser.add_argument(
        "--fail-on-incomplete-detail",
        action="store_true",
        help="Exit non-zero when any requested strategy does not produce a detail cache.",
    )
    parser.add_argument(
        "--max-consecutive-incomplete-detail",
        type=int,
        default=3,
        help="Abort early after this many consecutive strategies fail to produce detail cache.",
    )
    parser.add_argument(
        "--soft-circuit-break-consecutive-incomplete-detail",
        type=int,
        default=0,
        help=(
            "Stop this device cleanly after this many consecutive device/page failures. "
            "Unprocessed strategy ids stay in summary.json and can be retried by a fallback device."
        ),
    )
    parser.add_argument(
        "--soft-circuit-break-max-recoveries",
        type=int,
        default=2,
        help=(
            "Before deferring the remaining strategies, allow this many controlled App "
            "restarts after a real device/page failure circuit is reached."
        ),
    )
    parser.add_argument(
        "--soft-circuit-recovery-wait-ms",
        type=int,
        default=8000,
        help="Wait after a controlled soft-circuit App restart before continuing.",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=2,
        help="Maximum attempts per strategy before recording failure.",
    )
    parser.add_argument(
        "--retry-wait-ms",
        type=int,
        default=2500,
        help="Wait time between retry attempts in milliseconds.",
    )
    return parser.parse_args()


def load_work_bundles(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(payload, dict):
        payload = payload.get("strategy_work_bundles") or payload.get("bundles") or []
    if not isinstance(payload, list):
        raise ValueError(f"work bundle payload must be an array: {path}")
    bundles: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in payload:
        if not isinstance(row, dict):
            raise ValueError(f"work bundle row must be an object: {path}")
        strategy_id = str(row.get("strategy_id") or "").strip()
        if not strategy_id:
            raise ValueError(f"work bundle row is missing strategy_id: {path}")
        if strategy_id in seen:
            raise ValueError(f"duplicate strategy_id in work bundle: {strategy_id}")
        seen.add(strategy_id)
        required = row.get("required_fields")
        if not isinstance(required, dict):
            required = {}
        normalized_required = {
            "detail": bool(required.get("detail", True)),
            "benchmark_text": bool(required.get("benchmark_text")),
            "current_holding": bool(required.get("current_holding")),
            "rebalance_history": bool(required.get("rebalance_history")),
        }
        bundles.append(
            {
                **row,
                "strategy_id": strategy_id,
                "required_fields": normalized_required,
            }
        )
    return bundles


def default_required_fields(
    *,
    missing_scope: str | None,
    skip_history: bool,
    require_benchmark_text: bool,
    require_current_holding: bool,
) -> dict[str, bool]:
    return {
        "detail": True,
        "benchmark_text": bool(require_benchmark_text),
        "current_holding": bool(require_current_holding),
        "rebalance_history": bool(
            not skip_history and missing_scope not in {"detail", "latest_adjustment"}
        ),
    }


def field_completion(result: dict[str, Any], required_fields: dict[str, Any]) -> dict[str, bool]:
    detail_ok = bool(result.get("detail_ok"))
    return {
        "detail": detail_ok,
        "benchmark_text": bool(result.get("benchmark_text_ok")),
        "current_holding": bool(detail_ok and result.get("holding_info_ok")),
        "rebalance_history": bool(
            detail_ok and (result.get("history_adjustment_ok") or result.get("history_page_seen"))
        ),
    }


def apply_work_bundle_status(
    result: dict[str, Any],
    required_fields: dict[str, Any],
    *,
    fail_on_missing: bool = False,
) -> dict[str, Any]:
    completed = field_completion(result, required_fields)
    pending = [
        field
        for field, required in required_fields.items()
        if bool(required) and not completed.get(field, False)
    ]
    result["required_fields"] = {field: bool(value) for field, value in required_fields.items()}
    result["completed_fields"] = completed
    result["pending_fields"] = pending
    result["required_fields_ok"] = not pending
    if pending:
        result["incomplete_reason"] = ",".join(f"{field}_missing" for field in pending)
        if fail_on_missing and not result.get("error"):
            result["error"] = result["incomplete_reason"]
    else:
        result.pop("incomplete_reason", None)
        if result.get("error"):
            # A retry may fail on the page it was probing while the checkpoint
            # merge has already restored every required business field.  Keep
            # that attempt diagnostic without misclassifying the completed
            # strategy bundle as a final collection failure.
            result["last_attempt_error"] = result["error"]
            result["error"] = None
    return result


def pending_required_fields(
    result: dict[str, Any] | None,
    required_fields: dict[str, Any],
) -> dict[str, bool]:
    completed = field_completion(result or {}, required_fields)
    return {
        field: bool(required) and not completed.get(field, False)
        for field, required in required_fields.items()
    }


def merge_checkpoint_result(
    existing: dict[str, Any] | None,
    candidate: dict[str, Any],
) -> dict[str, Any]:
    """Preserve fields already validated in the same run when a retry only fills gaps."""

    if not existing:
        return candidate
    merged = dict(existing)
    merged.update(candidate)
    groups: tuple[tuple[Any, tuple[str, ...]], ...] = (
        (
            lambda row: bool(row.get("detail_ok")),
            (
                "detail_ok",
                "detail_size",
                "detail_source_file",
                "detail_field_presence",
                "strategy_name",
                "advisor_institution",
                "risk_level",
                "service_fee_ok",
                "service_fee_text",
                "performance_stage_ok",
            ),
        ),
        (
            lambda row: bool(row.get("benchmark_text_ok")),
            ("benchmark_text_ok", "benchmark_text", "benchmark_text_source", "benchmark_ui_text_ok", "benchmark_ui_text"),
        ),
        (
            lambda row: bool(row.get("detail_ok") and row.get("holding_info_ok")),
            ("holding_info_ok", "holding_date", "holding_fund_count"),
        ),
        (
            lambda row: bool(row.get("latest_adjustment_ok")),
            ("latest_adjustment_ok", "latest_size"),
        ),
        (
            lambda row: bool(row.get("history_adjustment_ok") or row.get("history_page_seen")),
            (
                "history_adjustment_ok",
                "history_page_seen",
                "history_size",
                "history_event_count",
                "history_delta_count",
            ),
        ),
    )
    restored_fields: list[str] = []
    for predicate, keys in groups:
        if predicate(existing) and not predicate(candidate):
            for key in keys:
                if key in existing:
                    merged[key] = existing[key]
                    restored_fields.append(key)
    merged["checkpoint_merged"] = True
    merged["checkpoint_restored_fields"] = sorted(set(restored_fields))
    merged["app_open_total"] = int(existing.get("app_open_total") or 0) + int(candidate.get("app_open_total") or 0)
    return merged


def write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def resolve_adb_path(adb_path: Path) -> Path:
    if adb_path.exists():
        return adb_path
    found = shutil.which(str(adb_path))
    if found:
        return Path(found)
    raise FileNotFoundError(f"adb not found: {adb_path}")


def adb_run(adb_path: Path, device_id: str, *args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(adb_path), "-s", device_id, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def adb_shell(adb_path: Path, device_id: str, command: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return adb_run(adb_path, device_id, "shell", command, timeout=timeout)


def list_remote_cache_files(adb_path: Path, device_id: str) -> list[str]:
    completed = adb_shell(adb_path, device_id, f"ls -1 {DEVICE_CACHE_DIR}", timeout=30)
    if completed.returncode != 0:
        return []
    return [line.strip() for line in completed.stdout.splitlines() if line.strip().endswith(".0")]


def list_remote_cache_files_for_strategy(adb_path: Path, device_id: str, strategy_id: str) -> list[str]:
    if not re.fullmatch(r"[A-Za-z0-9]+", strategy_id):
        raise ValueError(f"invalid strategy id: {strategy_id}")
    patterns = (
        f"{DEVICE_CACHE_DIR}/strategyDetailPageData{strategy_id}_*.0",
        f"{DEVICE_CACHE_DIR}/adjuseHouseList{strategy_id}_*.0",
        f"{DEVICE_CACHE_DIR}/adjuseHouseListHis{strategy_id}_*.0",
        f"{DEVICE_CACHE_DIR}/ttfund-layout-cache-advicer-strategy-detail-matter-{strategy_id}-*.0",
    )
    command = "for f in {patterns}; do [ -f \"$f\" ] && basename \"$f\"; done".format(
        patterns=" ".join(patterns)
    )
    completed = adb_shell(adb_path, device_id, command, timeout=30)
    if completed.returncode != 0:
        return [name for name in list_remote_cache_files(adb_path, device_id) if strategy_id in name]
    return list(
        dict.fromkeys(
            line.strip()
            for line in completed.stdout.splitlines()
            if line.strip().endswith(".0") and strategy_id in line
        )
    )


def list_remote_cache_entries(adb_path: Path, device_id: str) -> list[tuple[str, int | None]]:
    command = (
        f"for f in {DEVICE_CACHE_DIR}/*.0; do "
        'if [ -f "$f" ]; then '
        'b=$(basename "$f"); '
        's=$(wc -c < "$f" 2>/dev/null); '
        'echo "$b|$s"; '
        "fi; "
        "done"
    )
    completed = adb_shell(adb_path, device_id, command, timeout=45)
    if completed.returncode != 0:
        return [(name, None) for name in list_remote_cache_files(adb_path, device_id)]
    entries: list[tuple[str, int | None]] = []
    for line in completed.stdout.splitlines():
        line = line.strip()
        if not line or "|" not in line:
            continue
        name, size_text = line.rsplit("|", 1)
        name = name.strip()
        if not name.endswith(".0"):
            continue
        try:
            size = int(size_text.strip())
        except ValueError:
            size = None
        entries.append((name, size))
    return entries


def build_remote_cache_index(names: list[str] | list[tuple[str, int | None]]) -> dict[str, set[str]]:
    index: dict[str, set[str]] = {
        "detail": set(),
        "latest_adjustment": set(),
        "history_adjustment": set(),
    }
    for entry in names:
        if isinstance(entry, tuple):
            name, size = entry
        else:
            name, size = entry, None
        if size is not None and size <= 8:
            continue
        if name.startswith("strategyDetailPageData"):
            match = re.search(r"strategyDetailPageData([A-Za-z0-9]+)_", name)
            if match:
                index["detail"].add(match.group(1))
            continue
        if name.startswith("adjuseHouseListHis"):
            match = re.search(r"adjuseHouseListHis([A-Za-z0-9]+)_", name)
            if match:
                index["history_adjustment"].add(match.group(1))
            continue
        if name.startswith("adjuseHouseList"):
            match = re.search(r"adjuseHouseList([A-Za-z0-9]+)_", name)
            if match:
                index["latest_adjustment"].add(match.group(1))
    return index


def build_strategy_url(strategy_id: str, deeplink_mode: str = "guodu") -> str:
    query = {"id": strategy_id}
    if deeplink_mode == "guodu":
        query["fromOutStrategy"] = "toGuoduChoose"
    elif deeplink_mode == "show_kyc":
        query["showKycPopup"] = "1"
    inner = (
        f"fund://mp.1234567.com.cn/weex/{APP_ID}/pages/strategyDetail/index?"
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


def launch_strategy(
    adb_path: Path,
    device_id: str,
    strategy_id: str,
    deeplink_mode: str = "guodu",
) -> subprocess.CompletedProcess[str]:
    url = build_strategy_url(strategy_id, deeplink_mode)
    command = f"am start -a android.intent.action.VIEW -d '{url}'"
    return adb_shell(adb_path, device_id, command, timeout=40)


def parse_bounds(text: str) -> tuple[int, int, int, int]:
    left, top, right, bottom = text.replace("][", ",").replace("[", "").replace("]", "").split(",")
    return int(left), int(top), int(right), int(bottom)


@dataclass
class UiNode:
    text: str
    clickable: bool
    bounds: tuple[int, int, int, int]

    @property
    def center(self) -> tuple[int, int]:
        left, top, right, bottom = self.bounds
        return (left + right) // 2, (top + bottom) // 2


class DeviceUi:
    def __init__(self, adb_path: Path, device_id: str, run_dir: Path) -> None:
        self.adb_path = adb_path
        self.device_id = device_id
        self.run_dir = run_dir
        self.temp_dump_remote = "/sdcard/ttfund_uidump.xml"
        self.temp_dump_local = run_dir / "last_uidump.xml"

    def dump(self) -> list[UiNode]:
        last_error = ""
        for attempt in range(1, 4):
            dump_result = adb_shell(
                self.adb_path,
                self.device_id,
                f"uiautomator dump {self.temp_dump_remote}",
                timeout=30,
            )
            dumped = "dumped to:" in f"{dump_result.stdout}\n{dump_result.stderr}".lower()
            if dump_result.returncode == 0 or dumped:
                pull_result = adb_run(
                    self.adb_path,
                    self.device_id,
                    "pull",
                    self.temp_dump_remote,
                    str(self.temp_dump_local),
                    timeout=30,
                )
                if pull_result.returncode == 0 and self.temp_dump_local.exists():
                    break
                last_error = (
                    f"uidump pull failed: {pull_result.stdout.strip()} {pull_result.stderr.strip()}".strip()
                )
            else:
                last_error = (
                    f"uiautomator dump failed: {dump_result.stdout.strip()} {dump_result.stderr.strip()}".strip()
                )
            if attempt < 3:
                adb_shell(self.adb_path, self.device_id, "input keyevent 224", timeout=10)
                # Android may keep UiAutomationService registered briefly after a failed dump.
                time.sleep(5.0 * attempt)
        else:
            raise RuntimeError(last_error or "uiautomator dump failed after retries")
        xml_text = self.temp_dump_local.read_text(encoding="utf-8", errors="replace")
        if not xml_text.strip():
            raise RuntimeError("uidump pull produced empty XML")
        root = ET.fromstring(xml_text)
        nodes: list[UiNode] = []
        for element in root.iter():
            desc = element.attrib.get("content-desc", "")
            text_attr = element.attrib.get("text", "")
            display_text = desc or text_attr
            if not display_text:
                continue
            bounds_text = element.attrib.get("bounds")
            if not bounds_text:
                continue
            nodes.append(
                UiNode(
                    text=display_text,
                    clickable=element.attrib.get("clickable") == "true",
                    bounds=parse_bounds(bounds_text),
                )
            )
        return nodes

    def find_contains(self, nodes: list[UiNode], keyword: str) -> list[UiNode]:
        return [node for node in nodes if keyword in node.text]

    def find_preferred(self, nodes: list[UiNode], keywords: str | tuple[str, ...]) -> list[UiNode]:
        if isinstance(keywords, str):
            keyword_list = (keywords,)
        else:
            keyword_list = tuple(keyword for keyword in keywords if keyword)
        exact = [node for node in nodes if any(node.text == keyword for keyword in keyword_list)]
        fuzzy = [
            node
            for node in nodes
            if any(keyword in node.text for keyword in keyword_list) and node not in exact
        ]
        ranked = exact + fuzzy
        ranked.sort(
            key=lambda node: (
                0 if node.clickable else 1,
                (node.bounds[2] - node.bounds[0]) * (node.bounds[3] - node.bounds[1]),
                node.bounds[1],
                node.bounds[0],
            )
        )
        return ranked

    def screenshot(self, path: Path) -> None:
        completed = subprocess.run(
            [str(self.adb_path), "-s", self.device_id, "exec-out", "screencap", "-p"],
            capture_output=True,
            timeout=30,
            check=False,
        )
        path.write_bytes(completed.stdout)


class CacheMirror:
    def __init__(self, adb_path: Path, device_id: str, run_dir: Path, keep_run_cache: bool) -> None:
        self.adb_path = adb_path
        self.device_id = device_id
        self.run_dir = run_dir
        self.keep_run_cache = keep_run_cache
        self.mirror_root = RAW_ROOT / "device_cache"

    def list_remote_cache_files(self) -> list[str]:
        return list_remote_cache_files(self.adb_path, self.device_id)

    def pull_for_strategy(self, strategy_id: str) -> dict[str, Path]:
        names = list_remote_cache_files_for_strategy(self.adb_path, self.device_id, strategy_id)
        wanted = [
            name for name in names
            if strategy_id in name and (
                name.startswith("strategyDetailPageData")
                or name.startswith("adjuseHouseList")
                or name.startswith("ttfund-layout-cache-advicer-strategy-detail-matter-")
            )
        ]
        pulled: dict[str, Path] = {}
        for name in wanted:
            remote = f"{DEVICE_CACHE_DIR}/{name}"
            target_dir = self.mirror_root / strategy_id
            target_dir.mkdir(parents=True, exist_ok=True)
            local_path = target_dir / name
            completed = adb_run(self.adb_path, self.device_id, "pull", remote, str(local_path), timeout=40)
            if completed.returncode == 0:
                pulled[name] = local_path
                if self.keep_run_cache:
                    run_cache_dir = self.run_dir / "pulled_cache" / strategy_id
                    run_cache_dir.mkdir(parents=True, exist_ok=True)
                    retained_name = f"{hashlib.sha256(name.encode('utf-8')).hexdigest()[:20]}.cache"
                    retained_path = run_cache_dir / retained_name
                    shutil.copy2(local_path, retained_path)
                    manifest_path = run_cache_dir / "manifest.json"
                    manifest = load_json(manifest_path)
                    if not isinstance(manifest, dict):
                        manifest = {}
                    manifest[retained_name] = {
                        "source_name": name,
                        "mirror_path": str(local_path),
                    }
                    manifest_path.write_text(
                        json.dumps(manifest, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
        return pulled


def load_json(path: Path) -> Any | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        obj = json.loads(text)
        if isinstance(obj, str):
            return json.loads(obj)
        return obj
    except Exception:
        return None


def norm_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text in {"--", "null", "None"}:
        return None
    return text


def format_service_fee(rate: Any, provision_type: Any) -> str | None:
    rate_text = norm_text(rate)
    unit = norm_text(provision_type) or ""
    if not rate_text and not unit:
        return None
    if not rate_text:
        return unit or None
    if rate_text.endswith("%"):
        return f"{rate_text}{unit}" if unit else rate_text
    return f"{rate_text}%{unit}" if unit else f"{rate_text}%"


def validate_pulled_cache(strategy_id: str, pulled: dict[str, Path]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "detail_ok": False,
        "benchmark_text_ok": False,
        "benchmark_text": None,
        "service_fee_ok": False,
        "service_fee_text": None,
        "strategy_name": None,
        "advisor_institution": None,
        "risk_level": None,
        "holding_info_ok": False,
        "holding_date": None,
        "holding_fund_count": 0,
        "performance_stage_ok": False,
        "detail_source_file": None,
        "detail_field_presence": {},
        "latest_adjustment_ok": False,
        "history_adjustment_ok": False,
        "detail_size": None,
        "history_size": None,
        "latest_size": None,
        "history_event_count": 0,
        "history_delta_count": 0,
    }
    for name, path in pulled.items():
        payload = load_json(path)
        size = path.stat().st_size
        if name.startswith("strategyDetailPageData"):
            if isinstance(payload, dict) and payload.get("tgExtendInfo"):
                extend_info = payload.get("tgExtendInfo") or {}
                cfh_info = payload.get("cfhInfo") or {}
                holding_info = payload.get("holdWareHouseInfo") or {}
                result["detail_ok"] = True
                result["detail_source_file"] = name
                result["strategy_name"] = norm_text(extend_info.get("tgName") or extend_info.get("name"))
                result["advisor_institution"] = norm_text(extend_info.get("logoName") or cfh_info.get("fortuneName"))
                result["risk_level"] = norm_text(extend_info.get("risk"))
                result["service_fee_text"] = format_service_fee(
                    extend_info.get("strategyRate"),
                    extend_info.get("provisionType"),
                )
                result["service_fee_ok"] = bool(result["service_fee_text"])
                result["holding_info_ok"] = bool(holding_info.get("holdTypeList") or [])
                result["holding_date"] = norm_text(holding_info.get("date"))
                result["holding_fund_count"] = sum(
                    len(group.get("fundList") or group.get("fundsList") or [])
                    for group in holding_info.get("holdTypeList") or []
                    if isinstance(group, dict)
                )
                result["performance_stage_ok"] = bool(extend_info.get("stageListAll") or [])
                result["detail_field_presence"] = {
                    "tgName": bool(result["strategy_name"]),
                    "logoName": bool(result["advisor_institution"]),
                    "cfhInfo.fortuneName": bool(norm_text(cfh_info.get("fortuneName"))),
                    "risk": bool(result["risk_level"]),
                    "investTerm": bool(norm_text(extend_info.get("investTerm"))),
                    "minBuy": bool(norm_text(extend_info.get("minBuy"))),
                    "strategyRate": bool(norm_text(extend_info.get("strategyRate"))),
                    "provisionType": bool(norm_text(extend_info.get("provisionType"))),
                    "basicCalFormulaRemark": bool(norm_text(extend_info.get("basicCalFormulaRemark"))),
                    "stageListAll": result["performance_stage_ok"],
                    "holdWareHouseInfo": result["holding_info_ok"],
                }
                benchmark_text = norm_text(extend_info.get("basicCalFormulaRemark"))
                if benchmark_text:
                    result["benchmark_text_ok"] = True
                    result["benchmark_text"] = benchmark_text
            result["detail_size"] = size
        elif name.startswith(f"adjuseHouseListHis{strategy_id}"):
            if isinstance(payload, dict) and isinstance(payload.get("adjustList"), list):
                result["history_adjustment_ok"] = True
                result["history_event_count"] = len(payload.get("adjustList") or [])
                delta_count = 0
                for event in payload.get("adjustList") or []:
                    for group in event.get("arr") or []:
                        delta_count += len(group.get("changeList") or [])
                result["history_delta_count"] = delta_count
            result["history_size"] = size
        elif name.startswith(f"adjuseHouseList{strategy_id}"):
            if isinstance(payload, dict) and (
                payload.get("dateStr") or payload.get("reason") or payload.get("adjustList")
            ):
                result["latest_adjustment_ok"] = True
            result["latest_size"] = size
    return result


def tap(adb_path: Path, device_id: str, point: tuple[int, int]) -> None:
    x, y = point
    adb_shell(adb_path, device_id, f"input tap {x} {y}", timeout=25)


def swipe_up(adb_path: Path, device_id: str) -> None:
    adb_shell(adb_path, device_id, "input swipe 540 1880 540 820 280", timeout=30)


def back(adb_path: Path, device_id: str) -> None:
    adb_shell(adb_path, device_id, "input keyevent 4", timeout=20)


def wake_device(adb_path: Path, device_id: str) -> None:
    adb_shell(adb_path, device_id, "input keyevent 224", timeout=20)


def force_stop_ttfund(adb_path: Path, device_id: str) -> None:
    adb_shell(adb_path, device_id, f"am force-stop {TTFUND_PACKAGE}", timeout=20)


def wait_ms(ms: int) -> None:
    time.sleep(ms / 1000)


def capture_failure(ui: DeviceUi, strategy_dir: Path, label: str) -> None:
    ui.screenshot(strategy_dir / f"{label}.png")
    shutil.copy2(ui.temp_dump_local, strategy_dir / f"{label}.xml")


def any_node_matches(nodes: list[UiNode], keywords: str | tuple[str, ...]) -> bool:
    if isinstance(keywords, str):
        keyword_list = (keywords,)
    else:
        keyword_list = tuple(keyword for keyword in keywords if keyword)
    return any(any(keyword in node.text for keyword in keyword_list) for node in nodes)


def is_load_failure_page(nodes: list[UiNode]) -> bool:
    return any_node_matches(nodes, LABEL_LOAD_FAILED_VARIANTS)


def is_loading_page(nodes: list[UiNode]) -> bool:
    return any_node_matches(nodes, LABEL_LOADING_VARIANTS)


def is_blank_page(nodes: list[UiNode]) -> bool:
    return not nodes


def is_device_degradation_failure(result: dict[str, Any]) -> bool:
    error_text = str(result.get("error") or "")
    device_markers = {
        "detail_page_blank",
        "detail_page_load_failed_2001",
        "detail_page_loading_timeout",
        "device_unavailable",
    }
    if any(
        error_text == marker
        or error_text.startswith(f"{marker}:")
        or f": {marker}" in error_text
        for marker in device_markers
    ):
        return True
    if not (
        error_text == "detail_cache_missing"
        or error_text.startswith("detail_cache_missing:")
        or ": detail_cache_missing" in error_text
    ):
        return False
    # A cache miss with the expected detail activity still open is normally a
    # strategy/source-specific miss. It must not poison the device-level
    # consecutive failure counter and defer hundreds of unrelated strategies.
    return bool(
        result.get("activity_ok") is False
        or result.get("blank_page_unresolved")
        or result.get("load_failure_unresolved")
    )


def should_soft_circuit_break(
    *,
    consecutive_device_failures: int,
    threshold: int,
) -> bool:
    return threshold > 0 and consecutive_device_failures >= threshold


def should_attempt_soft_circuit_recovery(
    *,
    recovery_total: int,
    max_recoveries: int,
) -> bool:
    return max_recoveries > 0 and recovery_total < max_recoveries


def wait_until_not_loading(
    ui: DeviceUi,
    nodes: list[UiNode],
    *,
    wait_ms_each: int,
    max_wait_ms: int = 15000,
) -> tuple[list[UiNode], bool]:
    current_nodes = nodes
    waited_ms = 0
    while is_loading_page(current_nodes) and waited_ms < max_wait_ms:
        wait_ms(wait_ms_each)
        waited_ms += wait_ms_each
        current_nodes = ui.dump()
    return current_nodes, not is_loading_page(current_nodes)


def norm_visible_text(text: Any) -> str:
    return re.sub(r"\s+", "", str(text or "")).strip()


def strip_benchmark_label(text: str) -> str:
    value = norm_visible_text(text)
    for label in LABEL_BENCHMARK_TEXT_VARIANTS:
        value = value.replace(label, "")
    value = value.replace("\u57fa\u51c6\u8bf4\u660e", "")
    value = value.strip("\uff1a:;；，,。 ")
    return value


def looks_like_benchmark_formula(text: str) -> bool:
    value = norm_visible_text(text)
    if len(value) < 6 or len(value) > 220:
        return False
    if not any(hint in value for hint in BENCHMARK_FORMULA_HINTS):
        return False
    if "%" not in value and not any(token in value for token in ("+", "\uff0b", "*", "\u00d7", "x", "X")):
        return False
    if any(token in value for token in ("\u6295\u987e\u670d\u52a1\u8d39\u7387", "\u5143\u8d77", "\u67e5\u770b\u5168\u90e8")):
        return False
    return True


def extract_benchmark_text_from_nodes(nodes: list[UiNode]) -> str | None:
    texts: list[str] = []
    seen: set[str] = set()
    for node in nodes:
        text = norm_visible_text(node.text)
        if not text or text in seen:
            continue
        seen.add(text)
        texts.append(text)

    for index, text in enumerate(texts):
        stripped = strip_benchmark_label(text)
        if looks_like_benchmark_formula(stripped):
            return stripped
        if any(label in text for label in LABEL_BENCHMARK_TEXT_VARIANTS):
            combined = strip_benchmark_label("".join(texts[index : index + 6]))
            if looks_like_benchmark_formula(combined):
                return combined
    return None


def tap_first_keyword(
    ui: DeviceUi,
    adb_path: Path,
    device_id: str,
    nodes: list[UiNode],
    keyword: str | tuple[str, ...],
    wait_after_ms: int,
) -> tuple[bool, list[UiNode]]:
    matches = ui.find_preferred(nodes, keyword)
    if not matches:
        return False, nodes
    tap(adb_path, device_id, matches[0].center)
    wait_ms(wait_after_ms)
    return True, ui.dump()


def recover_load_failure_page(
    ui: DeviceUi,
    adb_path: Path,
    device_id: str,
    nodes: list[UiNode],
    *,
    retry_wait_ms: int,
    max_retries: int = 3,
) -> tuple[list[UiNode], dict[str, Any]]:
    status: dict[str, Any] = {
        "load_failure_seen": False,
        "load_failure_retry_total": 0,
        "load_failure_recovered": False,
        "load_failure_unresolved": False,
    }
    current_nodes = nodes
    if not is_load_failure_page(current_nodes):
        return current_nodes, status

    status["load_failure_seen"] = True
    for retry_index in range(max(max_retries, 0)):
        tapped, current_nodes = tap_first_keyword(
            ui,
            adb_path,
            device_id,
            current_nodes,
            LABEL_LOAD_FAILED_VARIANTS,
            max(retry_wait_ms, 3500),
        )
        if not tapped:
            # The error text is sometimes not marked clickable, but tapping the
            # center of the visible error row still triggers the app retry.
            matches = ui.find_preferred(current_nodes, LABEL_LOAD_FAILED_VARIANTS)
            if matches:
                tap(adb_path, device_id, matches[0].center)
                wait_ms(max(retry_wait_ms, 3500))
                current_nodes = ui.dump()
        status["load_failure_retry_total"] = retry_index + 1
        if not is_load_failure_page(current_nodes):
            status["load_failure_recovered"] = True
            return current_nodes, status

    status["load_failure_unresolved"] = is_load_failure_page(current_nodes)
    return current_nodes, status


def merge_load_failure_status(result: dict[str, Any], status: dict[str, Any]) -> None:
    result["load_failure_seen"] = bool(result.get("load_failure_seen") or status.get("load_failure_seen"))
    result["load_failure_retry_total"] = int(result.get("load_failure_retry_total") or 0) + int(
        status.get("load_failure_retry_total") or 0
    )
    result["load_failure_recovered"] = bool(
        result.get("load_failure_recovered") or status.get("load_failure_recovered")
    )
    if status.get("load_failure_seen"):
        result["load_failure_unresolved"] = bool(status.get("load_failure_unresolved"))


def scan_detail_page(
    ui: DeviceUi,
    adb_path: Path,
    device_id: str,
    nodes: list[UiNode],
    *,
    detail_scan_swipes: int,
    swipe_wait_ms: int,
    snapshot_dir: Path | None = None,
) -> tuple[list[UiNode], dict[str, Any]]:
    current_nodes = nodes
    status: dict[str, Any] = {
        "detail_scan_swipe_count": 0,
        "performance_section_seen": any_node_matches(current_nodes, LABEL_PERFORMANCE_SECTION_VARIANTS),
        "benchmark_dropdown_seen": any_node_matches(current_nodes, LABEL_BENCHMARK_DROPDOWN_VARIANTS),
        "benchmark_dropdown_tapped": False,
        "benchmark_ui_text_ok": False,
        "benchmark_ui_text": None,
    }
    for scan_index in range(max(detail_scan_swipes, 0)):
        ui_benchmark_text = extract_benchmark_text_from_nodes(current_nodes)
        if ui_benchmark_text:
            status["benchmark_ui_text_ok"] = True
            status["benchmark_ui_text"] = ui_benchmark_text
        if any_node_matches(current_nodes, LABEL_PERFORMANCE_SECTION_VARIANTS):
            status["performance_section_seen"] = True
        if any_node_matches(current_nodes, LABEL_BENCHMARK_DROPDOWN_VARIANTS):
            status["benchmark_dropdown_seen"] = True
            tapped, current_nodes = tap_first_keyword(
                ui,
                adb_path,
                device_id,
                current_nodes,
                LABEL_BENCHMARK_DROPDOWN_VARIANTS,
                max(swipe_wait_ms, 1500),
            )
            status["benchmark_dropdown_tapped"] = bool(status["benchmark_dropdown_tapped"] or tapped)
            if tapped:
                if snapshot_dir is not None:
                    shutil.copy2(ui.temp_dump_local, snapshot_dir / f"benchmark_dropdown_{scan_index + 1}.xml")
                ui_benchmark_text = extract_benchmark_text_from_nodes(current_nodes)
                if ui_benchmark_text:
                    status["benchmark_ui_text_ok"] = True
                    status["benchmark_ui_text"] = ui_benchmark_text

        swipe_up(adb_path, device_id)
        status["detail_scan_swipe_count"] = scan_index + 1
        wait_ms(swipe_wait_ms)
        current_nodes = ui.dump()
        _, current_nodes = tap_first_keyword(
            ui,
            adb_path,
            device_id,
            current_nodes,
            LABEL_CONFIRM_VIEW_VARIANTS,
            max(swipe_wait_ms, 1500),
        )

    if any_node_matches(current_nodes, LABEL_PERFORMANCE_SECTION_VARIANTS):
        status["performance_section_seen"] = True
    if any_node_matches(current_nodes, LABEL_BENCHMARK_DROPDOWN_VARIANTS):
        status["benchmark_dropdown_seen"] = True
    ui_benchmark_text = extract_benchmark_text_from_nodes(current_nodes)
    if ui_benchmark_text:
        status["benchmark_ui_text_ok"] = True
        status["benchmark_ui_text"] = ui_benchmark_text
    return current_nodes, status


def drive_one_strategy(
    strategy_id: str,
    adb_path: Path,
    device_id: str,
    run_dir: Path,
    deeplink_mode: str,
    max_swipes: int,
    swipe_wait_ms: int,
    detail_scan_swipes: int,
    skip_history: bool,
    keep_run_cache: bool,
    capture_failures: bool,
    persist_result: bool = True,
) -> dict[str, Any]:
    strategy_dir = run_dir / strategy_id
    strategy_dir.mkdir(parents=True, exist_ok=True)
    ui = DeviceUi(adb_path, device_id, strategy_dir)
    mirror = CacheMirror(adb_path, device_id, run_dir, keep_run_cache)
    started_at = time.perf_counter()
    result: dict[str, Any] = {
        "strategy_id": strategy_id,
        "app_open_total": 1,
        "launch_ok": False,
        "activity_ok": False,
        "detail_ui_seen": False,
        "dynamic_tab_seen": False,
        "dynamic_tab_tapped": False,
        "history_entry_seen": False,
        "history_entry_tapped": False,
        "history_page_seen": False,
        "swipe_count": 0,
        "detail_scan_swipe_count": 0,
        "performance_section_seen": False,
        "benchmark_dropdown_seen": False,
        "benchmark_dropdown_tapped": False,
        "confirm_view_tapped": False,
        "load_failure_seen": False,
        "load_failure_retry_total": 0,
        "load_failure_recovered": False,
        "load_failure_unresolved": False,
        "blank_page_seen": False,
        "blank_page_unresolved": False,
        "error": None,
    }

    wake_device(adb_path, device_id)
    wait_ms(500)
    launch = launch_strategy(adb_path, device_id, strategy_id, deeplink_mode)
    result["launch_stdout"] = launch.stdout.strip()
    result["launch_stderr"] = launch.stderr.strip()
    result["launch_ok"] = launch.returncode == 0 and "Error:" not in launch.stdout
    wait_ms(max(swipe_wait_ms, 1800))

    top = adb_shell(adb_path, device_id, "dumpsys activity top", timeout=30)
    result["activity_ok"] = "FundWeexActivity" in top.stdout
    nodes = ui.dump()
    result["blank_page_seen"] = is_blank_page(nodes)
    result["confirm_view_tapped"], nodes = tap_first_keyword(
        ui,
        adb_path,
        device_id,
        nodes,
        LABEL_CONFIRM_VIEW_VARIANTS,
        max(swipe_wait_ms, 1800),
    )
    nodes, load_status = recover_load_failure_page(
        ui,
        adb_path,
        device_id,
        nodes,
        retry_wait_ms=max(swipe_wait_ms, 2500),
    )
    result.update(load_status)
    if result.get("load_failure_unresolved"):
        force_stop_ttfund(adb_path, device_id)
        wait_ms(max(swipe_wait_ms, 2500))
        launch = launch_strategy(adb_path, device_id, strategy_id, deeplink_mode)
        result["relaunched_after_load_failure"] = True
        result["relaunch_stdout"] = launch.stdout.strip()
        result["relaunch_stderr"] = launch.stderr.strip()
        wait_ms(max(swipe_wait_ms, 3500))
        nodes = ui.dump()
        tapped_confirm, nodes = tap_first_keyword(
            ui,
            adb_path,
            device_id,
            nodes,
            LABEL_CONFIRM_VIEW_VARIANTS,
            max(swipe_wait_ms, 1800),
        )
        result["confirm_view_tapped"] = bool(result["confirm_view_tapped"] or tapped_confirm)
        nodes, second_load_status = recover_load_failure_page(
            ui,
            adb_path,
            device_id,
            nodes,
            retry_wait_ms=max(swipe_wait_ms, 3000),
        )
        result["load_failure_retry_total"] = int(result.get("load_failure_retry_total") or 0) + int(
            second_load_status.get("load_failure_retry_total") or 0
        )
        result["load_failure_recovered"] = bool(
            result.get("load_failure_recovered") or second_load_status.get("load_failure_recovered")
        )
        result["load_failure_unresolved"] = bool(second_load_status.get("load_failure_unresolved"))
    nodes, loading_completed = wait_until_not_loading(
        ui,
        nodes,
        wait_ms_each=max(swipe_wait_ms, 1500),
    )
    result["initial_loading_completed"] = loading_completed
    result["blank_page_seen"] = bool(result["blank_page_seen"] or is_blank_page(nodes))
    if result.get("load_failure_unresolved"):
        result["error"] = "detail_page_load_failed_2001"
    result["detail_ui_seen"] = any_node_matches(nodes, LABEL_CONFIRM_SELECT_VARIANTS) or any_node_matches(
        nodes, LABEL_SERVICE_FEE_VARIANTS
    )
    if detail_scan_swipes > 0:
        nodes, detail_scan_status = scan_detail_page(
            ui,
            adb_path,
            device_id,
            nodes,
            detail_scan_swipes=detail_scan_swipes,
            swipe_wait_ms=swipe_wait_ms,
            snapshot_dir=strategy_dir,
        )
        result.update(detail_scan_status)
        if is_load_failure_page(nodes):
            nodes, late_load_status = recover_load_failure_page(
                ui,
                adb_path,
                device_id,
                nodes,
                retry_wait_ms=max(swipe_wait_ms, 3000),
            )
            merge_load_failure_status(result, late_load_status)
            if result.get("load_failure_unresolved"):
                result["error"] = "detail_page_load_failed_2001"
                force_stop_ttfund(adb_path, device_id)
                wait_ms(max(swipe_wait_ms, 2500))
        nodes, loading_completed = wait_until_not_loading(
            ui,
            nodes,
            wait_ms_each=max(swipe_wait_ms, 1500),
        )
        result["post_scan_loading_completed"] = loading_completed
        result["blank_page_seen"] = bool(result["blank_page_seen"] or is_blank_page(nodes))

    if not skip_history:
        dynamic_phase_nodes = nodes
        for swipe_index in range(max_swipes + 1):
            if swipe_index > 0:
                swipe_up(adb_path, device_id)
                result["swipe_count"] = swipe_index
                wait_ms(swipe_wait_ms)
                dynamic_phase_nodes = ui.dump()
                _, dynamic_phase_nodes = tap_first_keyword(
                    ui,
                    adb_path,
                    device_id,
                    dynamic_phase_nodes,
                    LABEL_CONFIRM_VIEW_VARIANTS,
                    max(swipe_wait_ms, 1800),
                )

            dynamic_nodes = ui.find_preferred(dynamic_phase_nodes, LABEL_DYNAMIC_TAB_VARIANTS)
            if dynamic_nodes:
                result["dynamic_tab_seen"] = True
                tap(adb_path, device_id, dynamic_nodes[0].center)
                result["dynamic_tab_tapped"] = True
                wait_ms(swipe_wait_ms)
                dynamic_phase_nodes = ui.dump()
                _, dynamic_phase_nodes = tap_first_keyword(
                    ui,
                    adb_path,
                    device_id,
                    dynamic_phase_nodes,
                    LABEL_CONFIRM_VIEW_VARIANTS,
                    max(swipe_wait_ms, 1800),
                )
                break

        if result["dynamic_tab_tapped"]:
            nodes = dynamic_phase_nodes
            for history_attempt in range(3):
                history_nodes = ui.find_preferred(nodes, LABEL_HISTORY_ENTRY_VARIANTS)
                if history_nodes:
                    result["history_entry_seen"] = True
                    for candidate in history_nodes:
                        tap(adb_path, device_id, candidate.center)
                        result["history_entry_tapped"] = True
                        wait_ms(max(swipe_wait_ms, 1800))
                        nodes = ui.dump()
                        _, nodes = tap_first_keyword(
                            ui,
                            adb_path,
                            device_id,
                            nodes,
                            LABEL_CONFIRM_VIEW_VARIANTS,
                            max(swipe_wait_ms, 1800),
                        )
                        if any_node_matches(nodes, LABEL_HISTORY_TITLE_VARIANTS) or any_node_matches(
                            nodes, LABEL_HISTORY_ENTRY_VARIANTS
                        ):
                            result["history_page_seen"] = True
                            break
                        if any_node_matches(nodes, LABEL_RULES_TITLE_VARIANTS):
                            back(adb_path, device_id)
                            wait_ms(max(swipe_wait_ms, 1800))
                            nodes = ui.dump()
                            _, nodes = tap_first_keyword(
                                ui,
                                adb_path,
                                device_id,
                                nodes,
                                LABEL_CONFIRM_VIEW_VARIANTS,
                                max(swipe_wait_ms, 1800),
                            )
                    if result["history_page_seen"]:
                        break
                    break
                if history_attempt < 2:
                    swipe_up(adb_path, device_id)
                    wait_ms(swipe_wait_ms)
                    nodes = ui.dump()
                    _, nodes = tap_first_keyword(
                        ui,
                        adb_path,
                        device_id,
                        nodes,
                        LABEL_CONFIRM_VIEW_VARIANTS,
                        max(swipe_wait_ms, 1800),
                    )
        if capture_failures and not result["history_page_seen"]:
            capture_failure(ui, strategy_dir, "history_not_reached")

    pulled = mirror.pull_for_strategy(strategy_id)
    result["pulled_files"] = sorted(pulled)
    result.update(validate_pulled_cache(strategy_id, pulled))
    result["blank_page_unresolved"] = bool(is_blank_page(nodes) and not result.get("detail_ok"))
    if result.get("load_failure_unresolved"):
        result["error"] = "detail_page_load_failed_2001"
    if result.get("blank_page_unresolved") and not result.get("detail_ok"):
        result["error"] = "detail_page_blank"
    if is_loading_page(nodes) and not result.get("detail_ok") and not result.get("error"):
        result["error"] = "detail_page_loading_timeout"
    if not result.get("detail_ok") and not result.get("error"):
        result["error"] = "detail_cache_missing"
    if capture_failures and not result.get("detail_ok"):
        capture_failure(ui, strategy_dir, "detail_not_reached")
    if (not result.get("benchmark_text_ok")) and result.get("benchmark_ui_text_ok"):
        result["benchmark_text_ok"] = True
        result["benchmark_text"] = result.get("benchmark_ui_text")
        result["benchmark_text_source"] = "visible_ui"
    result["elapsed_sec"] = round(time.perf_counter() - started_at, 2)

    if persist_result:
        write_json_atomic(strategy_dir / "result.json", result)
    return result


def build_failed_result(
    strategy_id: str,
    error: Exception,
    *,
    elapsed_sec: float = 0.0,
) -> dict[str, Any]:
    trace_text = traceback.format_exc(limit=8)
    result = {
        "strategy_id": strategy_id,
        "app_open_total": 0,
        "launch_ok": False,
        "activity_ok": False,
        "detail_ui_seen": False,
        "dynamic_tab_seen": False,
        "dynamic_tab_tapped": False,
        "history_entry_seen": False,
        "history_entry_tapped": False,
        "history_page_seen": False,
        "swipe_count": 0,
        "confirm_view_tapped": False,
        "load_failure_seen": False,
        "load_failure_retry_total": 0,
        "load_failure_recovered": False,
        "load_failure_unresolved": False,
        "detail_ok": False,
        "benchmark_text_ok": False,
        "benchmark_text": None,
        "service_fee_ok": False,
        "service_fee_text": None,
        "holding_info_ok": False,
        "holding_date": None,
        "holding_fund_count": 0,
        "latest_adjustment_ok": False,
        "history_adjustment_ok": False,
        "detail_size": None,
        "history_size": None,
        "latest_size": None,
        "history_event_count": 0,
        "history_delta_count": 0,
        "pulled_files": [],
        "elapsed_sec": round(elapsed_sec, 2),
        "error_type": type(error).__name__,
        "error": f"{type(error).__name__}: {error}",
    }
    if trace_text and trace_text.strip() != "NoneType: None":
        result["traceback"] = trace_text.strip()
    return result


def check_device_ready(
    adb_path: Path,
    device_id: str,
    *,
    require_package: bool = True,
) -> tuple[bool, str]:
    state = adb_run(adb_path, device_id, "get-state", timeout=10)
    if state.returncode != 0 or state.stdout.strip() != "device":
        message = state.stderr.strip() or state.stdout.strip() or "get-state failed"
        return False, message
    if require_package:
        package = adb_shell(adb_path, device_id, f"pm path {TTFUND_PACKAGE}", timeout=10)
        if package.returncode != 0 or not package.stdout.strip().startswith("package:"):
            message = package.stderr.strip() or package.stdout.strip() or f"{TTFUND_PACKAGE} not installed"
            return False, message
    return True, ""


def is_strategy_complete(
    result: dict[str, Any],
    missing_scope: str | None,
    skip_history: bool,
    *,
    require_benchmark_text: bool = False,
    require_current_holding: bool = False,
) -> bool:
    if require_benchmark_text and not result.get("benchmark_text_ok"):
        return False
    if require_current_holding:
        return bool(result.get("detail_ok") and result.get("holding_info_ok"))
    if missing_scope == "detail" or skip_history:
        return bool(result.get("detail_ok"))
    if missing_scope == "latest_adjustment":
        return bool(result.get("detail_ok") and result.get("latest_adjustment_ok"))
    if missing_scope == "history_adjustment":
        return bool(result.get("detail_ok") and result.get("history_adjustment_ok"))
    if skip_history:
        return bool(result.get("detail_ok"))
    return bool(result.get("detail_ok") and result.get("history_adjustment_ok"))


def apply_required_field_status(
    result: dict[str, Any],
    *,
    require_benchmark_text: bool = False,
    fail_on_missing: bool = False,
) -> dict[str, Any]:
    missing: list[str] = []
    if not result.get("detail_ok"):
        missing.append("detail")
    if require_benchmark_text and not result.get("benchmark_text_ok"):
        missing.append("benchmark_text")

    result["required_fields_ok"] = not missing
    if missing:
        result["incomplete_reason"] = ",".join(f"{item}_missing" for item in missing)
        if fail_on_missing and not result.get("error"):
            result["error"] = result["incomplete_reason"]
    return result


def load_existing_result(run_dir: Path, strategy_id: str) -> dict[str, Any] | None:
    path = run_dir / strategy_id / "result.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter()
    error_by_type: Counter[str] = Counter()
    detail_missing_ids: list[str] = []
    current_holding_missing_ids: list[str] = []
    source_unavailable_ids: list[str] = []
    source_unavailable_errors = {
        "detail_cache_missing",
        "detail_page_blank",
        "detail_page_loading_timeout",
        "detail_page_load_failed_2001",
    }
    total_elapsed = 0.0
    pending_field_counts: Counter[str] = Counter()
    for row in results:
        total_elapsed += row.get("elapsed_sec") or 0.0
        for key in (
            "launch_ok",
            "activity_ok",
            "detail_ok",
            "benchmark_text_ok",
            "service_fee_ok",
            "holding_info_ok",
            "required_fields_ok",
            "latest_adjustment_ok",
            "history_adjustment_ok",
            "history_page_seen",
        ):
            if row.get(key):
                counts[key] += 1
        if row.get("error"):
            counts["error"] += 1
            error_text = str(row.get("error") or "unknown_error")
            error_by_type[error_text] += 1
            if error_text in source_unavailable_errors:
                source_unavailable_ids.append(str(row.get("strategy_id") or ""))
        if not row.get("detail_ok"):
            detail_missing_ids.append(str(row.get("strategy_id") or ""))
        if not (row.get("detail_ok") and row.get("holding_info_ok")):
            current_holding_missing_ids.append(str(row.get("strategy_id") or ""))
        for field in row.get("pending_fields") or []:
            pending_field_counts[str(field)] += 1
    total = len(results)
    return {
        "strategy_total": total,
        "launch_ok_total": counts["launch_ok"],
        "activity_ok_total": counts["activity_ok"],
        "detail_ok_total": counts["detail_ok"],
        "benchmark_text_ok_total": counts["benchmark_text_ok"],
        "benchmark_text_missing_total": max(total - counts["benchmark_text_ok"], 0),
        "service_fee_ok_total": counts["service_fee_ok"],
        "holding_info_ok_total": counts["holding_info_ok"],
        "holding_info_missing_total": max(total - counts["holding_info_ok"], 0),
        "current_holding_missing_total": len([item for item in current_holding_missing_ids if item]),
        "current_holding_missing_ids": [item for item in current_holding_missing_ids if item],
        "holding_fund_total": sum(int(row.get("holding_fund_count") or 0) for row in results),
        "fast_path_ok_total": sum(1 for row in results if row.get("capture_mode") == "current_holding_fast"),
        "full_fallback_total": sum(
            1 for row in results if row.get("capture_mode") == "current_holding_full_fallback"
        ),
        "required_fields_ok_total": counts["required_fields_ok"],
        "required_fields_missing_total": max(total - counts["required_fields_ok"], 0),
        "latest_adjustment_ok_total": counts["latest_adjustment_ok"],
        "history_page_seen_total": counts["history_page_seen"],
        "history_adjustment_ok_total": counts["history_adjustment_ok"],
        "error_total": counts["error"],
        "error_by_type": dict(sorted(error_by_type.items())),
        "detail_missing_total": len([item for item in detail_missing_ids if item]),
        "detail_missing_ids": [item for item in detail_missing_ids if item],
        "source_unavailable_total": len([item for item in source_unavailable_ids if item]),
        "source_unavailable_ids": [item for item in source_unavailable_ids if item],
        "pending_field_counts": dict(sorted(pending_field_counts.items())),
        "reused_checkpoint_total": sum(1 for row in results if row.get("reused_from_existing")),
        "app_open_total": sum(int(row.get("app_open_total") or 0) for row in results),
        "strategy_reopen_total": sum(max(int(row.get("app_open_total") or 0) - 1, 0) for row in results),
        "avg_elapsed_sec": round(total_elapsed / total, 2) if total else 0.0,
        "max_elapsed_sec": max((row.get("elapsed_sec") or 0.0 for row in results), default=0.0),
    }


def write_run_outputs(
    *,
    run_dir: Path,
    results: list[dict[str, Any]],
    run_id: str,
    run_at: datetime,
    strategy_ids: list[str],
    requested_total: int,
    missing_scope: str | None,
    cache_index: dict[str, set[str]] | None,
) -> dict[str, Any]:
    summary = summarize(results)
    summary["run_id"] = run_id
    summary["captured_at"] = run_at.isoformat(timespec="seconds")
    summary["run_dir"] = str(run_dir)
    summary["strategy_ids"] = strategy_ids
    summary["requested_total"] = requested_total
    summary["missing_scope"] = missing_scope
    if cache_index and missing_scope:
        summary["cache_scope_covered_total"] = len(cache_index[missing_scope])
        summary["cache_scope_missing_total"] = max(requested_total - len(cache_index[missing_scope]), 0)

    write_json_atomic(run_dir / "summary.json", summary)
    write_json_atomic(run_dir / "results.json", results)
    return summary


def build_selection_summary(
    *,
    requested_total: int,
    selected_ids: list[str],
    missing_scope: str | None,
    cache_index: dict[str, set[str]] | None,
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "requested_total": requested_total,
        "selected_total": len(selected_ids),
        "selected_ids": selected_ids,
    }
    if missing_scope and cache_index is not None:
        covered_ids = cache_index[missing_scope]
        summary.update(
            {
                "missing_scope": missing_scope,
                "cache_scope_covered_total": len(covered_ids),
                "cache_scope_missing_total": max(requested_total - len(covered_ids), 0),
            }
        )
    return summary


def capture_current_holding_strategy(
    *,
    drive_kwargs: dict[str, Any],
    detail_scan_swipes: int,
) -> dict[str, Any]:
    fast_candidate = drive_one_strategy(
        **drive_kwargs,
        detail_scan_swipes=0,
    )
    fast_path_ok = bool(
        fast_candidate.get("launch_ok")
        and fast_candidate.get("detail_ok")
        and fast_candidate.get("holding_info_ok")
        and not fast_candidate.get("error")
    )
    if fast_path_ok:
        fast_candidate["capture_mode"] = "current_holding_fast"
        fast_candidate["fast_path_ok"] = True
        fast_candidate["app_open_total"] = 1
        return fast_candidate

    fast_elapsed_sec = float(fast_candidate.get("elapsed_sec") or 0.0)
    candidate = drive_one_strategy(
        **drive_kwargs,
        detail_scan_swipes=max(detail_scan_swipes, 1),
    )
    candidate["capture_mode"] = "current_holding_full_fallback"
    candidate["app_open_total"] = 2
    candidate["fast_path_ok"] = False
    candidate["fast_path_elapsed_sec"] = fast_elapsed_sec
    candidate["fast_path_error"] = fast_candidate.get("error")
    candidate["fast_path_detail_ok"] = bool(fast_candidate.get("detail_ok"))
    candidate["fast_path_holding_info_ok"] = bool(fast_candidate.get("holding_info_ok"))
    candidate["elapsed_sec"] = round(
        fast_elapsed_sec + float(candidate.get("elapsed_sec") or 0.0),
        2,
    )
    return candidate


def main() -> None:
    args = parse_args()
    adb_path = resolve_adb_path(args.adb_path)
    work_bundles = load_work_bundles(args.work_bundle_file)
    work_bundle_by_id = {row["strategy_id"]: row for row in work_bundles}

    strategy_ids = list(args.strategy_ids)
    if args.strategy_file:
        strategy_ids.extend(load_strategy_ids_from_file(args.strategy_file))
    if args.use_latest_master:
        strategy_ids.extend(load_latest_strategy_ids(limit=None, offset=0))
    if work_bundles and not strategy_ids:
        strategy_ids.extend(row["strategy_id"] for row in work_bundles)
    strategy_ids = list(dict.fromkeys(strategy_ids))
    if work_bundles:
        missing_bundle_ids = [strategy_id for strategy_id in strategy_ids if strategy_id not in work_bundle_by_id]
        if missing_bundle_ids:
            raise ValueError(
                "work bundle is missing selected strategy ids: " + ",".join(missing_bundle_ids[:20])
            )

    device_ready, device_error = check_device_ready(adb_path, args.device_id)
    if not device_ready:
        raise RuntimeError(f"device not ready: {device_error}")

    requested_total = len(strategy_ids)
    cache_index: dict[str, set[str]] | None = None
    if args.missing_scope:
        cache_index = build_remote_cache_index(list_remote_cache_entries(adb_path, args.device_id))
        strategy_ids = [sid for sid in strategy_ids if sid not in cache_index[args.missing_scope]]

    if args.offset > 0:
        strategy_ids = strategy_ids[args.offset:]
    if args.limit is not None and args.limit > 0:
        strategy_ids = strategy_ids[:args.limit]
    results: list[dict[str, Any]] = []
    selection_summary = build_selection_summary(
        requested_total=requested_total,
        selected_ids=strategy_ids,
        missing_scope=args.missing_scope,
        cache_index=cache_index,
    )

    if args.selection_only:
        print(json.dumps(selection_summary, ensure_ascii=False, indent=2))
        return
    if not strategy_ids:
        raise SystemExit("no strategy ids selected")

    run_at = now_local()
    run_id = run_at.strftime("%Y%m%dT%H%M%S%z")
    run_dir = args.run_dir or (
        RAW_ROOT / "ttfund" / "app_drive" / run_at.strftime("%Y-%m-%d") / run_id
    )
    run_dir.mkdir(parents=True, exist_ok=True)
    consecutive_incomplete_detail = 0
    consecutive_device_failures = 0
    soft_circuit_break_triggered = False
    soft_circuit_break_after_strategy_id: str | None = None
    soft_circuit_recovery_total = 0
    soft_circuit_recovery_errors: list[str] = []
    deferred_strategy_ids: list[str] = []
    if args.current_holding_fast:
        progress_label = "天天投顾当前仓位采集"
    elif work_bundles:
        progress_label = "天天投顾单策略任务包采集"
    elif args.skip_history:
        progress_label = "天天投顾策略详情采集"
    else:
        progress_label = "天天投顾历史调仓采集"
    progress = ConsoleProgress(progress_label, len(strategy_ids))
    progress.emit(0, success=0, failed=0, extra=f"设备 {args.device_id}")

    def required_fields_for(strategy_id: str) -> dict[str, bool]:
        bundle = work_bundle_by_id.get(strategy_id)
        if bundle:
            return dict(bundle["required_fields"])
        return default_required_fields(
            missing_scope=args.missing_scope,
            skip_history=args.skip_history,
            require_benchmark_text=args.require_benchmark_text,
            require_current_holding=args.current_holding_fast,
        )

    def complete_for_strategy(strategy_id: str, result: dict[str, Any]) -> bool:
        if work_bundles:
            required = required_fields_for(strategy_id)
            return all(
                not bool(required_field) or field_completion(result, required).get(field, False)
                for field, required_field in required.items()
            )
        return is_strategy_complete(
            result,
            args.missing_scope,
            args.skip_history,
            require_benchmark_text=args.require_benchmark_text,
            require_current_holding=args.current_holding_fast,
        )

    def apply_completion_status(strategy_id: str, result: dict[str, Any]) -> dict[str, Any]:
        if work_bundles:
            bundle = work_bundle_by_id[strategy_id]
            result["work_bundle"] = bundle
            return apply_work_bundle_status(
                result,
                required_fields_for(strategy_id),
                fail_on_missing=args.fail_on_required_field_missing,
            )
        return apply_required_field_status(
            result,
            require_benchmark_text=args.require_benchmark_text,
            fail_on_missing=args.fail_on_required_field_missing,
        )

    def report_progress(strategy_id: str, result: dict[str, Any]) -> dict[str, Any]:
        current_summary = write_run_outputs(
            run_dir=run_dir,
            results=results,
            run_id=run_id,
            run_at=run_at,
            strategy_ids=strategy_ids,
            requested_total=requested_total,
            missing_scope=args.missing_scope,
            cache_index=cache_index,
        )
        success_total = sum(
            1
            for row in results
            if complete_for_strategy(str(row.get("strategy_id") or ""), row)
        )
        failure_total = max(len(results) - success_total, 0)
        progress.emit(
            len(results),
            success=success_total,
            failed=failure_total,
            current=strategy_id,
            extra=(
                f"详情完整 {current_summary.get('detail_ok_total', 0)} | "
                f"当前仓位 {current_summary.get('holding_info_ok_total', 0)} | "
                f"调仓历史 {current_summary.get('history_adjustment_ok_total', 0)} | "
                f"采集方式 {result.get('capture_mode') or ('复用已有结果' if result.get('reused_from_existing') else '-')}"
            ),
        )
        return current_summary

    existing_results: dict[str, dict[str, Any]] = {}
    if args.skip_existing_results:
        for strategy_id in strategy_ids:
            existing = load_existing_result(run_dir, strategy_id)
            if existing:
                existing_results[strategy_id] = existing

    for strategy_index, strategy_id in enumerate(strategy_ids):
        if strategy_id in existing_results and complete_for_strategy(
            strategy_id,
            existing_results[strategy_id],
        ):
            reused = dict(existing_results[strategy_id])
            reused["reused_from_existing"] = True
            reused = apply_completion_status(strategy_id, reused)
            results.append(reused)
            report_progress(strategy_id, reused)
            consecutive_incomplete_detail = 0
            consecutive_device_failures = 0
            continue

        checkpoint_result = existing_results.get(strategy_id)
        best_result: dict[str, Any] | None = None
        for attempt in range(1, max(args.max_attempts, 1) + 1):
            required_fields = required_fields_for(strategy_id)
            capture_fields = pending_required_fields(checkpoint_result, required_fields)
            progress.emit(
                len(results),
                current=strategy_id,
                extra=f"正在处理第 {len(results) + 1}/{len(strategy_ids)} 个策略 | 尝试 {attempt}/{max(args.max_attempts, 1)}",
            )
            attempt_started = time.perf_counter()
            try:
                device_ready, device_error = check_device_ready(
                    adb_path,
                    args.device_id,
                    require_package=False,
                )
                if not device_ready:
                    raise RuntimeError(f"device_unavailable: {device_error}")
                drive_kwargs = {
                    "strategy_id": strategy_id,
                    "adb_path": adb_path,
                    "device_id": args.device_id,
                    "run_dir": run_dir,
                    "deeplink_mode": args.deeplink_mode,
                    "max_swipes": args.max_swipes,
                    "swipe_wait_ms": args.swipe_wait_ms,
                    "skip_history": not bool(capture_fields.get("rebalance_history")),
                    "keep_run_cache": args.keep_run_cache,
                    "capture_failures": args.capture_failures,
                    "persist_result": not bool(work_bundles),
                }
                bundle = work_bundle_by_id.get(strategy_id) or {}
                needs_deep_detail = bool(bundle.get("deep_detail_refresh")) and bool(
                    capture_fields.get("detail") or capture_fields.get("benchmark_text")
                )
                if (
                    capture_fields.get("current_holding")
                    and not capture_fields.get("rebalance_history")
                    and not needs_deep_detail
                ):
                    candidate = capture_current_holding_strategy(
                        drive_kwargs=drive_kwargs,
                        detail_scan_swipes=args.detail_scan_swipes,
                    )
                else:
                    candidate = drive_one_strategy(
                        **drive_kwargs,
                        detail_scan_swipes=args.detail_scan_swipes,
                    )
                    candidate["capture_mode"] = (
                        "history_bundle"
                        if capture_fields.get("rebalance_history")
                        else "detail_bundle"
                    )
            except Exception as error:
                candidate = build_failed_result(
                    strategy_id,
                    error,
                    elapsed_sec=time.perf_counter() - attempt_started,
                )
                strategy_result_dir = run_dir / strategy_id
                strategy_result_dir.mkdir(parents=True, exist_ok=True)
                if not work_bundles:
                    write_json_atomic(strategy_result_dir / "result.json", candidate)

            candidate = merge_checkpoint_result(checkpoint_result, candidate)
            candidate["attempt"] = attempt
            candidate["checkpoint_pending_fields_before"] = [
                field for field, required in capture_fields.items() if required
            ]
            candidate = apply_completion_status(strategy_id, candidate)
            best_result = candidate
            checkpoint_result = candidate
            write_json_atomic(run_dir / strategy_id / "result.json", candidate)
            if complete_for_strategy(strategy_id, candidate):
                break
            if attempt < max(args.max_attempts, 1):
                wait_ms(args.retry_wait_ms)

        if best_result is not None:
            best_result = apply_completion_status(strategy_id, best_result)
            strategy_result_dir = run_dir / str(best_result.get("strategy_id") or strategy_id)
            strategy_result_dir.mkdir(parents=True, exist_ok=True)
            write_json_atomic(strategy_result_dir / "result.json", best_result)
        results.append(
            best_result or {
                "strategy_id": strategy_id,
                "error": "unknown_failure",
                "required_fields_ok": False,
            }
        )
        report_progress(strategy_id, best_result or {})
        if (best_result or {}).get("detail_ok"):
            consecutive_incomplete_detail = 0
        else:
            consecutive_incomplete_detail += 1
        if is_device_degradation_failure(best_result or {}):
            consecutive_device_failures += 1
        else:
            consecutive_device_failures = 0
        if (
            args.fail_on_incomplete_detail
            and consecutive_incomplete_detail >= max(args.max_consecutive_incomplete_detail, 1)
        ):
            raise SystemExit(3)
        if should_soft_circuit_break(
            consecutive_device_failures=consecutive_device_failures,
            threshold=args.soft_circuit_break_consecutive_incomplete_detail,
        ):
            if should_attempt_soft_circuit_recovery(
                recovery_total=soft_circuit_recovery_total,
                max_recoveries=args.soft_circuit_break_max_recoveries,
            ):
                soft_circuit_recovery_total += 1
                print(
                    "[CIRCUIT_RECOVERY] real device/page failures stayed consecutive; "
                    f"device={args.device_id} failures={consecutive_device_failures} "
                    f"recovery={soft_circuit_recovery_total}/"
                    f"{max(args.soft_circuit_break_max_recoveries, 0)}",
                    flush=True,
                )
                try:
                    force_stop_ttfund(adb_path, args.device_id)
                except Exception as error:
                    recovery_error = f"{type(error).__name__}: {error}"
                    soft_circuit_recovery_errors.append(recovery_error)
                    print(
                        "[CIRCUIT_RECOVERY_WARN] controlled App restart failed; "
                        f"device={args.device_id} error={recovery_error}",
                        flush=True,
                    )
                wait_ms(max(args.soft_circuit_recovery_wait_ms, args.retry_wait_ms, 0))
                consecutive_device_failures = 0
                continue
            soft_circuit_break_triggered = True
            soft_circuit_break_after_strategy_id = strategy_id
            deferred_strategy_ids = strategy_ids[strategy_index + 1 :]
            print(
                "[CIRCUIT_BREAK] device/page failures stayed consecutive; "
                f"device={args.device_id} failures={consecutive_device_failures} "
                f"deferred={len(deferred_strategy_ids)}",
                flush=True,
            )
            break

    summary = write_run_outputs(
        run_dir=run_dir,
        results=results,
        run_id=run_id,
        run_at=run_at,
        strategy_ids=strategy_ids,
        requested_total=requested_total,
        missing_scope=args.missing_scope,
        cache_index=cache_index,
    )
    summary.update(
        {
            "soft_circuit_break_triggered": soft_circuit_break_triggered,
            "soft_circuit_break_threshold": args.soft_circuit_break_consecutive_incomplete_detail,
            "soft_circuit_break_after_strategy_id": soft_circuit_break_after_strategy_id,
            "soft_circuit_recovery_limit": max(args.soft_circuit_break_max_recoveries, 0),
            "soft_circuit_recovery_total": soft_circuit_recovery_total,
            "soft_circuit_recovery_errors": soft_circuit_recovery_errors,
            "deferred_strategy_total": len(deferred_strategy_ids),
            "deferred_strategy_ids": deferred_strategy_ids,
            "work_bundle_mode": bool(work_bundles),
            "work_bundle_file": str(args.work_bundle_file) if args.work_bundle_file else None,
            "work_bundle_total": len(work_bundles),
        }
    )
    write_json_atomic(run_dir / "summary.json", summary)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.fail_on_required_field_missing and summary.get("required_fields_missing_total", 0) > 0:
        raise SystemExit(2)
    if args.fail_on_incomplete_detail and summary.get("detail_ok_total", 0) < summary.get("strategy_total", 0):
        raise SystemExit(3)


if __name__ == "__main__":
    main()
