from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qs, urlparse


APP_PACKAGE = "com.cgbchina.xpt"
REMOTE_DUMP = "/sdcard/gfbank_authenticated_window.xml"
STRATEGY_ENTRIES = ("理财组合", "超级定投", "目标盈")
INTERVAL_TABS = (
    ("1m", ("近1个月", "近1月")),
    ("6m", ("近6个月", "近6月")),
    ("1y", ("近1年", "近一年")),
    ("since_inception", ("成立以来",)),
)
BOUNDS_PATTERN = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")
FULL_DATE_PATTERN = re.compile(r"(\d{4})[./-](\d{2})[./-](\d{2})")
PERCENT_PATTERN = re.compile(r"([+-]?\d+(?:\.\d+)?)\s*%")
OCR_PERCENT_PATTERN = re.compile(r"([+-]?\d+\.\d{2})\s*%")
ADB_TRANSIENT_ERROR_MARKERS = (
    "device not found",
    "device offline",
    "no devices/emulators found",
    "closed",
    "cannot connect",
    "connection reset",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture authenticated GF Bank strategy details and disclosed interval performance from a physical device."
    )
    parser.add_argument("--workspace-root", type=Path, default=Path.cwd().parent)
    parser.add_argument("--code-root", type=Path, default=Path.cwd())
    parser.add_argument("--device-id", default=os.environ.get("ADVISOR_DEVICE_ID"))
    parser.add_argument("--adb", default=os.environ.get("ADVISOR_ADB_PATH") or "adb")
    parser.add_argument(
        "--adb-startup-wait-seconds",
        type=float,
        default=90.0,
        help="Wait this long for a temporarily disconnected physical device before giving up.",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-list-views", type=int, default=6)
    parser.add_argument("--max-products", type=int, default=0)
    parser.add_argument("--wait-seconds", type=float, default=1.6)
    parser.add_argument(
        "--strategy-entry",
        action="append",
        choices=STRATEGY_ENTRIES,
        default=[],
        help=(
            "Authenticated 广发智投 section to enumerate. Repeat for multiple sections. "
            "Defaults to 理财组合 for backward compatibility."
        ),
    )
    parser.add_argument(
        "--strategy-name",
        action="append",
        default=[],
        help="Only capture a named strategy; repeat to capture several named strategies.",
    )
    parser.add_argument(
        "--curve-scan-step-px",
        type=int,
        default=3,
        help="Initial horizontal scan step. Dense curves automatically receive the missing pixel-offset passes.",
    )
    parser.add_argument(
        "--curve-scan-scope",
        choices=("auto", "full", "recent"),
        default="auto",
        help=(
            "Scan the full chart, only its recent right edge, or choose automatically. "
            "Auto uses recent mode only after a validated multi-date curve exists in the authenticated cache."
        ),
    )
    parser.add_argument(
        "--curve-recent-width-px",
        type=int,
        default=180,
        help="Horizontal chart width scanned in recent mode; the rightmost point is always included.",
    )
    parser.add_argument("--curve-wait-seconds", type=float, default=0.08)
    parser.add_argument(
        "--curve-read-mode",
        choices=("auto", "exact_ui", "verified_ocr", "recorded_ocr"),
        default="auto",
        help="Prefer recorded OCR with automatic exact-UI fallback, force exact UI, or use a specific OCR transport.",
    )
    parser.add_argument(
        "--curve-ocr-verify-every",
        type=int,
        default=12,
        help="In verified_ocr mode, compare every Nth OCR point with exact UI text; use 1 for a full dual-read pilot.",
    )
    parser.add_argument(
        "--skip-curve-dense-refinement",
        action="store_true",
        help="Pilot-only: keep the coarse scan and do not fill unsampled pixels even when the curve is dense.",
    )
    parser.add_argument("--skip-curve-points", action="store_true")
    return parser.parse_args()


def run_adb(
    adb: str,
    device_id: str,
    *arguments: str,
    timeout: int = 30,
    binary: bool = False,
    transient_retries: int | None = None,
    transient_retry_delay: float = 5.0,
) -> subprocess.CompletedProcess[Any]:
    command = [adb, "-s", device_id, *arguments]
    safe_to_retry = not (
        len(arguments) >= 2
        and arguments[0] == "shell"
        and arguments[1] in {"input", "monkey", "am"}
    )
    retries = (12 if safe_to_retry else 0) if transient_retries is None else max(0, transient_retries)
    last_error = ""
    for attempt in range(retries + 1):
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=not binary,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            if attempt < retries and safe_to_retry:
                time.sleep(max(0.0, transient_retry_delay))
                continue
            raise RuntimeError(f"ADB command timed out after {timeout}s: {' '.join(command)}") from exc
        if completed.returncode == 0:
            return completed
        stderr = completed.stderr if isinstance(completed.stderr, str) else completed.stderr.decode("utf-8", errors="replace")
        stdout = completed.stdout if isinstance(completed.stdout, str) else completed.stdout.decode("utf-8", errors="replace")
        last_error = f"ADB command failed ({completed.returncode}): {' '.join(command)}\n{stdout}\n{stderr}".strip()
        error_text = f"{stdout}\n{stderr}".lower()
        transient = any(marker in error_text for marker in ADB_TRANSIENT_ERROR_MARKERS) or bool(
            re.search(r"device\s+.+?\s+not found", error_text)
        )
        if attempt >= retries or not safe_to_retry or not transient:
            raise RuntimeError(last_error)
        time.sleep(max(0.0, transient_retry_delay))
    raise RuntimeError(last_error or f"ADB command failed: {' '.join(command)}")


def ensure_device(adb: str, device_id: str | None, startup_wait_seconds: float = 90.0) -> str:
    if not device_id:
        raise RuntimeError("device id is required; pass --device-id or set ADVISOR_DEVICE_ID")
    deadline = time.monotonic() + max(0.0, startup_wait_seconds)
    last_error = ""
    state = ""
    while True:
        try:
            state = run_adb(
                adb,
                device_id,
                "get-state",
                timeout=15,
                transient_retries=0,
            ).stdout.strip()
            if state == "device":
                break
            last_error = f"state={state!r}"
        except RuntimeError as exc:
            last_error = str(exc)
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"ADB device did not become ready within {startup_wait_seconds:.0f}s: {device_id}; {last_error}"
            )
        time.sleep(min(5.0, max(0.1, deadline - time.monotonic())))
    if state != "device":
        raise RuntimeError(f"ADB device is not ready: {device_id}, state={state!r}")
    package = run_adb(adb, device_id, "shell", "pm", "path", APP_PACKAGE, timeout=20).stdout.strip()
    if not package.startswith("package:"):
        raise RuntimeError(f"GF Bank app is not installed on {device_id}")
    return device_id


def write_bytes_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temp.write_bytes(payload)
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def write_text_atomic(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temp.write_text(payload, encoding="utf-8")
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def extract_h5_route_metadata(payload: str) -> dict[str, Any]:
    """Return only non-sensitive identifiers from an mPaaS activity dump.

    ``dumpsys activity top`` exposes the current H5 URL, but it can also carry
    session and device context.  Never persist the raw dump.  The collector
    keeps only the app id, route path and the two official strategy identifiers
    needed to reproduce the MP8768/MP8769 lineage.
    """

    app_id_match = re.search(r"\bappId=([^,}\s]+)", payload)
    url_match = re.search(
        r"\burl=(https?://.*?)(?=,\s*[A-Za-z][A-Za-z0-9_]*=)",
        payload,
        flags=re.DOTALL,
    )
    route_url = re.sub(r"\s+", "", url_match.group(1)) if url_match else ""
    parsed = urlparse(route_url) if route_url else None
    query = parse_qs(parsed.query, keep_blank_values=True) if parsed else {}

    def identifier(name: str) -> str | None:
        values = query.get(name) or []
        if values and str(values[0]).strip():
            return str(values[0]).strip()
        patterns = (
            rf'["\']{name}["\']\s*:\s*["\']([^"\']+)',
            rf"\b{name}=([^&,}}\s]+)",
        )
        for pattern in patterns:
            match = re.search(pattern, payload)
            if match and match.group(1).strip():
                return match.group(1).strip()
        return None

    group_code = identifier("groupCode")
    sp_group_code = identifier("spGroupCode")
    return {
        "app_id": app_id_match.group(1).strip() if app_id_match else None,
        "h5_host": parsed.netloc if parsed else None,
        "h5_path": parsed.path if parsed else None,
        "group_code": group_code,
        "sp_group_code": sp_group_code,
        "evidence_source": "adb_dumpsys_activity_top_sanitized",
    }


def capture_h5_route_metadata(adb: str, device_id: str) -> dict[str, Any]:
    completed = run_adb(
        adb,
        device_id,
        "shell",
        "dumpsys",
        "activity",
        "top",
        timeout=30,
    )
    return extract_h5_route_metadata(completed.stdout)


def screenshot(adb: str, device_id: str, target: Path) -> None:
    write_bytes_atomic(target, screenshot_bytes(adb, device_id))


def screenshot_bytes(adb: str, device_id: str) -> bytes:
    completed = run_adb(adb, device_id, "exec-out", "screencap", "-p", timeout=30, binary=True)
    return bytes(completed.stdout)


def dump_ui(adb: str, device_id: str, target: Path) -> str:
    # ``uiautomator dump /dev/tty`` returns the hierarchy in the same ADB
    # request.  The older remote-file + cat path remains as a compatibility
    # fallback for Android builds that do not support the tty destination.
    completed = run_adb(
        adb,
        device_id,
        "exec-out",
        "uiautomator",
        "dump",
        "/dev/tty",
        timeout=35,
        binary=True,
    )
    payload = bytes(completed.stdout).decode("utf-8", errors="replace")
    hierarchy_end = payload.find("</hierarchy>")
    if hierarchy_end >= 0:
        payload = payload[: hierarchy_end + len("</hierarchy>")]
    if "<hierarchy" not in payload:
        run_adb(adb, device_id, "shell", "uiautomator", "dump", REMOTE_DUMP, timeout=35)
        completed = run_adb(adb, device_id, "exec-out", "cat", REMOTE_DUMP, timeout=20, binary=True)
        payload = bytes(completed.stdout).decode("utf-8", errors="replace")
    if "<hierarchy" not in payload:
        raise RuntimeError("UIAutomator dump does not contain a hierarchy")
    write_text_atomic(target, payload)
    return payload


def xml_nodes(payload: str) -> list[ET.Element]:
    return list(ET.fromstring(payload).iter("node"))


def node_text(node: ET.Element) -> str:
    return str(node.attrib.get("text") or node.attrib.get("content-desc") or "").strip()


def has_text(payload: str, value: str) -> bool:
    return any(value in node_text(node) for node in xml_nodes(payload))


def bounds_center(bounds: str | None) -> tuple[int, int] | None:
    match = BOUNDS_PATTERN.fullmatch(str(bounds or ""))
    if not match:
        return None
    left, top, right, bottom = (int(value) for value in match.groups())
    if right <= left or bottom <= top:
        return None
    return (left + right) // 2, (top + bottom) // 2


def bounds_rect(bounds: str | None) -> tuple[int, int, int, int] | None:
    match = BOUNDS_PATTERN.fullmatch(str(bounds or ""))
    if not match:
        return None
    left, top, right, bottom = (int(value) for value in match.groups())
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def find_text_center(payload: str, aliases: Iterable[str]) -> tuple[int, int] | None:
    alias_list = tuple(aliases)
    for exact in (True, False):
        for node in xml_nodes(payload):
            text = node_text(node)
            if not text:
                continue
            matched = any(text == alias if exact else alias in text for alias in alias_list)
            if matched:
                center = bounds_center(node.attrib.get("bounds"))
                if center:
                    return center
    return None


def find_resource_bounds(payload: str, resource_id: str) -> tuple[int, int, int, int] | None:
    for node in xml_nodes(payload):
        candidate = str(node.attrib.get("resource-id") or "")
        if candidate == resource_id or candidate.endswith(f"/{resource_id}"):
            rect = bounds_rect(node.attrib.get("bounds"))
            if rect:
                return rect
    return None


def find_clickable_after_text(payload: str, aliases: Iterable[str]) -> tuple[int, int] | None:
    """Find the small disclosure icon immediately to the right of a label."""
    nodes = xml_nodes(payload)
    labels = []
    for node in nodes:
        text = node_text(node)
        rect = bounds_rect(node.attrib.get("bounds"))
        if rect and any(alias in text for alias in aliases):
            labels.append(rect)
    candidates: list[tuple[int, int, int]] = []
    for left, top, right, bottom in labels:
        for node in nodes:
            if node.attrib.get("clickable") != "true":
                continue
            rect = bounds_rect(node.attrib.get("bounds"))
            center = bounds_center(node.attrib.get("bounds"))
            if not rect or not center:
                continue
            candidate_left, candidate_top, candidate_right, candidate_bottom = rect
            vertically_aligned = candidate_bottom >= top - 8 and candidate_top <= bottom + 8
            horizontal_gap = candidate_left - right
            if vertically_aligned and -8 <= horizontal_gap <= 120 and candidate_right > right:
                candidates.append((max(0, horizontal_gap), center[0], center[1]))
    if not candidates:
        return None
    _gap, x, y = min(candidates)
    return x, y


def strategy_card_centers_from_ui(payload: str) -> list[tuple[int, int]]:
    """Return visible clickable product-card centers without image OCR."""
    centers: list[tuple[int, int]] = []
    for node in xml_nodes(payload):
        if node.attrib.get("clickable") != "true" or node.attrib.get("class") != "android.view.View":
            continue
        rect = bounds_rect(node.attrib.get("bounds"))
        if not rect:
            continue
        left, top, right, bottom = rect
        height = bottom - top
        # The first full-width clickable block is the 广发智投 banner. Product
        # cards begin below it and are about 219 px high on the current device.
        if top < 470 or height < 140 or height > 300 or right - left < 700:
            continue
        centers.append(((left + right) // 2, (top + bottom) // 2))
    return centers


def target_profit_info_banner_center(payload: str) -> tuple[int, int] | None:
    """Locate the large official target-profit introduction banner.

    The provider cards are short rows below the banner.  Requiring a nearly
    full-width, tall clickable WebView child keeps this probe away from product
    cards and the native title bar.
    """
    candidates: list[tuple[int, int, int]] = []
    for node in xml_nodes(payload):
        if node.attrib.get("clickable") != "true" or node.attrib.get("class") != "android.view.View":
            continue
        rect = bounds_rect(node.attrib.get("bounds"))
        center = bounds_center(node.attrib.get("bounds"))
        if not rect or not center:
            continue
        left, top, right, bottom = rect
        if top < 200 or top > 500 or right - left < 900 or bottom - top < 500:
            continue
        candidates.append((top, center[0], center[1]))
    if not candidates:
        return None
    _top, x, y = min(candidates)
    return x, y


def looks_like_strategy_name(value: str) -> bool:
    text = re.sub(r"\s+", "", str(value or "")).strip("?？")
    if len(text) < 4 or len(text) > 50:
        return False
    if any(
        marker in text
        for marker in (
            "持有满",
            "建议持有",
            "投顾服务费",
            "组合涨跌幅",
            "基准涨跌幅",
            "成立以来收益率",
            "最新净值",
            "本策略方案由",
        )
    ):
        return False
    if re.search(r"\d+天$", text):
        return True
    if "目标盈" in text or ("目标" in text and re.search(r"\d+期$", text)):
        return True
    return "定投" in text and text not in {"超级定投", "定投专区", "基金定投"}


def detail_strategy_name_from_payload(payload: str) -> str | None:
    texts = [node_text(node) for node in xml_nodes(payload) if node_text(node)]
    return next((text for text in texts if looks_like_strategy_name(text)), None)


def capture_benchmark_disclosure(
    *,
    adb: str,
    device_id: str,
    output_dir: Path,
    strategy_name: str,
    initial_payload: str,
    wait_seconds: float,
) -> dict[str, Any]:
    center = find_clickable_after_text(initial_payload, ("基准涨跌幅",))
    if not center:
        return {
            "strategy_name": strategy_name,
            "status": "benchmark_disclosure_icon_missing",
            "source_file": None,
        }
    tap(adb, device_id, *center)
    time.sleep(max(0.5, wait_seconds))
    target = output_dir / f"detail_{safe_name(strategy_name)}_benchmark.xml"
    payload = ""
    try:
        for _attempt in range(8):
            payload = dump_ui(adb, device_id, target)
            if has_text(payload, "业绩基准"):
                return {
                    "strategy_name": strategy_name,
                    "status": "success",
                    "source_file": target.name,
                }
            time.sleep(0.6)
        target.unlink(missing_ok=True)
        return {
            "strategy_name": strategy_name,
            "status": "benchmark_disclosure_text_missing_after_click",
            "source_file": None,
        }
    finally:
        press_back(adb, device_id)
        time.sleep(0.8)


def card_matches_requested_strategy(card: dict[str, Any], requested_names: set[str]) -> bool:
    if not requested_names:
        return True
    candidates = {
        str(card.get("strategy_name") or "").strip(),
        str(card.get("product_card_text") or "").strip(),
    }
    candidates.discard("")
    return any(
        requested == candidate or requested in candidate or candidate in requested
        for requested in requested_names
        for candidate in candidates
    )


def performance_point_from_payload(payload: str) -> dict[str, Any] | None:
    """Read the exact values displayed above the draggable performance chart."""
    texts = [node_text(node) for node in xml_nodes(payload) if node_text(node)]
    try:
        start = texts.index("业绩表现")
    except ValueError:
        return None
    end_markers = ("每个投资者的投顾组合", "组合配置", "交易规则")
    end = next(
        (
            index
            for index, text in enumerate(texts[start + 1 :], start=start + 1)
            if any(text.startswith(marker) for marker in end_markers)
        ),
        len(texts),
    )
    section = texts[start:end]
    trade_date = None
    for text in section:
        match = FULL_DATE_PATTERN.search(text)
        if match:
            trade_date = f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
            break

    def percent_after(label: str) -> float | None:
        for index, text in enumerate(section):
            if label not in text:
                continue
            for candidate in section[index : index + 5]:
                match = PERCENT_PATTERN.search(candidate)
                if match:
                    return float(match.group(1))
        return None

    strategy_return = percent_after("组合涨跌幅")
    benchmark_return = percent_after("基准涨跌幅")
    if not trade_date or strategy_return is None or benchmark_return is None:
        return None
    return {
        "trade_date": trade_date,
        "cumulative_return": strategy_return,
        "benchmark_return": benchmark_return,
    }


def _ocr_single_line(engine: Any, image: Any, box: tuple[int, int, int, int]) -> tuple[str, float]:
    crop = image.crop(box)
    result, _elapsed = engine(crop, use_det=False, use_cls=False, use_rec=True)
    if not result or not result[0]:
        return "", 0.0
    return str(result[0][0] or "").strip(), float(result[0][1] or 0.0)


def performance_point_from_screenshot(
    screenshot_payload: bytes,
    chart_bounds: tuple[int, int, int, int],
    engine: Any,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Read the fixed tooltip summary row from a screenshot.

    This is a transport optimization only.  Accepted OCR values are periodically
    compared with UIAutomator text from the same snapped chart coordinate.
    """
    try:
        from PIL import Image  # type: ignore[import-not-found]
    except ModuleNotFoundError as exc:
        raise RuntimeError("Pillow is required for verified OCR curve capture") from exc
    image = Image.open(BytesIO(screenshot_payload)).convert("RGB")
    width, height = image.size
    left, top, right, _bottom = chart_bounds
    y1 = max(0, top - 69)
    y2 = min(height, top - 11)
    boxes = {
        "date": (max(0, left - 13), y1, min(width, left + 192), y2),
        "strategy": (max(0, left + 252), y1, min(width, left + 612), y2),
        "benchmark": (max(0, right - 322), y1, min(width, right + 68), y2),
    }
    recognized = {name: _ocr_single_line(engine, image, box) for name, box in boxes.items()}
    date_text, date_confidence = recognized["date"]
    strategy_text, strategy_confidence = recognized["strategy"]
    benchmark_text, benchmark_confidence = recognized["benchmark"]
    date_match = FULL_DATE_PATTERN.fullmatch(date_text.strip())
    # The GF Bank tooltip always renders two decimal places.  Requiring the
    # decimal point prevents high-confidence OCR errors such as 1.19 -> 119.
    strategy_match = OCR_PERCENT_PATTERN.search(strategy_text)
    benchmark_match = OCR_PERCENT_PATTERN.search(benchmark_text)
    diagnostics = {
        "recognized": {name: {"text": value[0], "confidence": value[1]} for name, value in recognized.items()},
        "minimum_confidence": min(value[1] for value in recognized.values()),
    }
    if (
        not date_match
        or not strategy_match
        or not benchmark_match
        or "组合涨跌幅" not in strategy_text
        or "基准涨跌幅" not in benchmark_text
        # Full-dual-read diagnostics showed that valid dates are often scored
        # around 0.87 even when every digit agrees with UIAutomator.  Exact
        # full-match syntax is a stronger guard than an unrealistically high
        # confidence cutoff, and periodic exact reads still detect drift.
        or date_confidence < 0.85
        or strategy_confidence < 0.90
        or benchmark_confidence < 0.90
    ):
        return None, diagnostics
    # A compressed video frame may cause the recognition model to drop the
    # first digit when the long Chinese label is included (for example 11.47
    # becoming 1.47).  Re-read the numeric tails independently and require
    # exact agreement.  Any disagreement is rejected and the caller falls back
    # to exact UI text instead of guessing.
    value_boxes = {
        "strategy_value": (max(0, left + 392), y1, min(width, left + 612), y2),
        "benchmark_value": (max(0, right - 202), y1, min(width, right + 68), y2),
    }
    value_recognized = {
        name: _ocr_single_line(engine, image, box) for name, box in value_boxes.items()
    }
    diagnostics["value_recognized"] = {
        name: {"text": value[0], "confidence": value[1]}
        for name, value in value_recognized.items()
    }
    strategy_value_match = OCR_PERCENT_PATTERN.search(value_recognized["strategy_value"][0])
    benchmark_value_match = OCR_PERCENT_PATTERN.search(value_recognized["benchmark_value"][0])
    if (
        not strategy_value_match
        or not benchmark_value_match
        or value_recognized["strategy_value"][1] < 0.80
        or value_recognized["benchmark_value"][1] < 0.80
        or float(strategy_match.group(1)) != float(strategy_value_match.group(1))
        or float(benchmark_match.group(1)) != float(benchmark_value_match.group(1))
    ):
        return None, diagnostics
    return (
        {
            "trade_date": f"{date_match.group(1)}-{date_match.group(2)}-{date_match.group(3)}",
            "cumulative_return": float(strategy_match.group(1)),
            "benchmark_return": float(benchmark_match.group(1)),
        },
        diagnostics,
    )


def build_ocr_curve_evidence_xml(
    strategy_name: str,
    point: dict[str, Any],
    *,
    x: int,
    minimum_confidence: float,
    source: str = "screen_ocr_periodically_verified_against_uiautomator",
) -> str:
    root = ET.Element(
        "hierarchy",
        {
            "source": source,
            "touch_x": str(x),
            "ocr_min_confidence": f"{minimum_confidence:.6f}",
        },
    )
    texts = (
        "投顾策略详情",
        strategy_name,
        "业绩表现",
        str(point["trade_date"]).replace("-", "."),
        "组合涨跌幅：",
        f"{float(point['cumulative_return']):+.2f}%",
        "基准涨跌幅",
        f"{float(point['benchmark_return']):+.2f}%",
        "每个投资者的投顾组合",
    )
    for text in texts:
        ET.SubElement(root, "node", {"text": text, "content-desc": ""})
    return ET.tostring(root, encoding="utf-8", xml_declaration=True).decode("utf-8")


def points_equal(first: dict[str, Any] | None, second: dict[str, Any] | None) -> bool:
    if not first or not second or first.get("trade_date") != second.get("trade_date"):
        return False
    for field in ("cumulative_return", "benchmark_return"):
        left = first.get(field)
        right = second.get(field)
        if left is None or right is None or abs(float(left) - float(right)) > 1e-9:
            return False
    return True


def tap(adb: str, device_id: str, x: int, y: int) -> None:
    run_adb(adb, device_id, "shell", "input", "tap", str(x), str(y), timeout=15)


def swipe(adb: str, device_id: str, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 650) -> None:
    run_adb(
        adb,
        device_id,
        "shell",
        "input",
        "swipe",
        str(x1),
        str(y1),
        str(x2),
        str(y2),
        str(duration_ms),
        timeout=20,
    )


def press_back(adb: str, device_id: str) -> None:
    run_adb(adb, device_id, "shell", "input", "keyevent", "KEYCODE_BACK", timeout=15)


def tap_top_bar_back(adb: str, device_id: str, payload: str | None = None) -> None:
    center = find_text_center(payload or "", ("返回",)) if payload else None
    if center:
        tap(adb, device_id, *center)
    else:
        # 广发银行 H5 对系统返回键偶尔只关闭浮层；标题栏返回更稳定。
        tap(adb, device_id, 55, 175)


def wait_for_text(
    adb: str,
    device_id: str,
    output_dir: Path,
    expected: str,
    *,
    attempts: int = 12,
    delay: float = 0.8,
) -> str:
    last_payload = ""
    for attempt in range(attempts):
        path = output_dir / f".wait_{os.getpid()}_{attempt}.xml"
        try:
            last_payload = dump_ui(adb, device_id, path)
        finally:
            path.unlink(missing_ok=True)
        if has_text(last_payload, expected):
            return last_payload
        time.sleep(delay)
    raise RuntimeError(f"page text did not appear: {expected}")


def is_strategy_list(payload: str, entry_label: str = "理财组合") -> bool:
    nodes = xml_nodes(payload)
    expected_titles = ("智投·目标盈", "目标盈") if entry_label == "目标盈" else (entry_label,)
    return (
        any(any(title in node_text(node) for title in expected_titles) for node in nodes)
        and any(
            node.attrib.get("scrollable") == "true"
            and any(title in node_text(node) for title in expected_titles)
            for node in nodes
        )
    )


def current_strategy_entry(payload: str) -> str | None:
    for entry_label in STRATEGY_ENTRIES:
        if is_strategy_list(payload, entry_label):
            return entry_label
    return None


def navigate_to_strategy_list(
    adb: str,
    device_id: str,
    output_dir: Path,
    entry_label: str = "理财组合",
) -> str:
    if entry_label not in STRATEGY_ENTRIES:
        raise ValueError(f"unsupported strategy entry: {entry_label}")
    run_adb(adb, device_id, "shell", "input", "keyevent", "KEYCODE_WAKEUP", timeout=15)
    wealth_tab_selected = False
    wealth_scrolls = 0
    for attempt in range(14):
        probe = output_dir / f".navigate_{attempt}.xml"
        payload = dump_ui(adb, device_id, probe)
        probe.unlink(missing_ok=True)
        if is_strategy_list(payload, entry_label):
            return payload
        if any(
            has_text(payload, marker)
            for marker in ("请输入登录密码", "立即登录", "更多登录方式")
        ):
            raise RuntimeError("GF Bank authenticated session is required")
        if has_text(payload, "投顾策略详情"):
            tap_top_bar_back(adb, device_id, payload)
            time.sleep(1.0)
            continue
        other_entry = current_strategy_entry(payload)
        if other_entry and other_entry != entry_label:
            tap_top_bar_back(adb, device_id, payload)
            time.sleep(1.0)
            continue
        if any(has_text(payload, marker) for marker in ("往期服务", "基金超级定投", "智投·目标盈")):
            tap_top_bar_back(adb, device_id, payload)
            time.sleep(1.0)
            continue
        if has_text(payload, "广发智投首页"):
            center = find_text_center(payload, (entry_label,))
            if center:
                tap(adb, device_id, *center)
                time.sleep(1.5)
                continue
        center = find_text_center(payload, ("乐享财富",))
        if center and not wealth_tab_selected:
            tap(adb, device_id, *center)
            wealth_tab_selected = True
            time.sleep(2.5)
            continue
        center = find_text_center(payload, ("广发智投",))
        if center:
            tap(adb, device_id, *center)
            time.sleep(1.8)
            continue
        if wealth_tab_selected and wealth_scrolls < 5:
            # 广发智投 sits below the first viewport of 乐享财富 on the current
            # physical-device layout.  Re-tapping the selected tab resets that
            # viewport, so scroll only after the one-time tab selection.
            swipe(adb, device_id, 540, 1860, 540, 620, 650)
            wealth_scrolls += 1
            time.sleep(1.2)
            continue
        if attempt == 0:
            run_adb(adb, device_id, "shell", "monkey", "-p", APP_PACKAGE, "-c", "android.intent.category.LAUNCHER", "1")
            time.sleep(2.0)
            continue
        raise RuntimeError(
            f"cannot navigate to 广发智投{entry_label} page; "
            f"keep the logged-in app on 广发智投首页 or {entry_label} and retry"
        )
    raise RuntimeError("navigation attempts exhausted")


def safe_name(value: str) -> str:
    clean = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "_", value).strip("_")[:36]
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
    return f"{clean or 'strategy'}_{digest}"


def acquire_lock_path(workspace_root: Path, run_id: str) -> tuple[Path, str]:
    lock_root = workspace_root / "运行状态" / "locks"
    lock_root.mkdir(parents=True, exist_ok=True)
    path = lock_root / "device.lock"
    token = hashlib.sha256(f"{os.getpid()}|{run_id}|{time.time()}".encode("utf-8")).hexdigest()
    for _attempt in range(2):
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                current = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError):
                current = {}
            pid = int(current.get("pid") or 0)
            if pid > 0:
                try:
                    os.kill(pid, 0)
                except OSError:
                    pass
                else:
                    raise RuntimeError(
                        f"device resource lock is active: run={current.get('runId')}, node={current.get('nodeId')}"
                    )
            path.unlink(missing_ok=True)
            continue
        try:
            payload = {
                "pid": os.getpid(),
                "runId": run_id,
                "nodeId": "manual_gfbank_authenticated_capture",
                "token": token,
                "acquiredAt": datetime.now().astimezone().isoformat(timespec="seconds"),
            }
            os.write(descriptor, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        finally:
            os.close(descriptor)
        return path, token
    raise RuntimeError("unable to acquire device resource lock")


def release_lock_path(path: Path, token: str) -> None:
    try:
        current = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        current = {}
    if current.get("token") == token:
        path.unlink(missing_ok=True)


@contextmanager
def device_lock(workspace_root: Path, run_id: str):
    path, token = acquire_lock_path(workspace_root, run_id)
    try:
        yield
    finally:
        release_lock_path(path, token)


def resolve_scrcpy(adb: str) -> str:
    adb_path = Path(adb)
    if adb_path.is_file():
        candidate = adb_path.with_name("scrcpy.exe")
        if candidate.is_file():
            return str(candidate)
    candidate = shutil.which("scrcpy")
    if candidate:
        return candidate
    raise RuntimeError("recorded_ocr requires scrcpy beside adb or available on PATH")


def read_jsonl_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            text = line.strip()
            if text:
                rows.append(json.loads(text))
    return rows


def load_cached_curve_state(code_root: Path) -> dict[str, dict[str, Any]]:
    """Return only validated curve history used to choose a safe scan scope.

    A latest-detail singleton is intentionally not treated as a historical
    curve.  If the cache cannot be parsed, callers fall back to a full scan.
    """
    cache_dir = code_root / "official_apps" / "gfbank_cgb" / "authenticated_cache"
    try:
        masters = read_jsonl_rows(cache_dir / "strategy_master.jsonl")
        daily_rows = read_jsonl_rows(cache_dir / "strategy_performance_daily.jsonl")
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return {}
    names_by_id = {
        str(row.get("source_strategy_id")): str(row.get("strategy_name"))
        for row in masters
        if row.get("source_strategy_id") and row.get("strategy_name")
    }
    points_by_name: dict[str, dict[str, dict[str, Any]]] = {}
    for row in daily_rows:
        if row.get("section_type") != "gfbank_authenticated_ui_curve_tooltip":
            continue
        strategy_name = names_by_id.get(str(row.get("source_strategy_id")))
        trade_date = str(row.get("trade_date") or "")
        if strategy_name and FULL_DATE_PATTERN.fullmatch(trade_date):
            points_by_name.setdefault(strategy_name, {})[trade_date] = row
    result: dict[str, dict[str, Any]] = {}
    for strategy_name, points_by_date in points_by_name.items():
        last_trade_date = max(points_by_date, default=None)
        latest = points_by_date.get(last_trade_date or "", {})
        result[strategy_name] = {
            "curve_point_total": len(points_by_date),
            "last_trade_date": last_trade_date,
            "cumulative_return": latest.get("cumulative_return"),
            "benchmark_return": latest.get("benchmark_return"),
            "nav": latest.get("nav"),
        }
    return result


def resolve_curve_scan_scope(
    requested_scope: str,
    strategy_name: str,
    cached_curve_state: dict[str, dict[str, Any]],
) -> tuple[str, dict[str, Any]]:
    state = dict(cached_curve_state.get(strategy_name) or {})
    if requested_scope != "auto":
        return requested_scope, state
    return (
        "recent" if int(state.get("curve_point_total") or 0) >= 2 else "full",
        state,
    )


def cached_curve_matches_current(
    cached_curve_state: dict[str, Any],
    current_point: dict[str, Any] | None,
) -> bool:
    if int(cached_curve_state.get("curve_point_total") or 0) < 2:
        return False
    return points_equal(
        current_point,
        {
            "trade_date": cached_curve_state.get("last_trade_date"),
            "cumulative_return": cached_curve_state.get("cumulative_return"),
            "benchmark_return": cached_curve_state.get("benchmark_return"),
        },
    )


def build_curve_scan_positions(
    scan_left: int,
    scan_right: int,
    step: int,
    *,
    scan_scope: str,
    recent_width_px: int,
) -> tuple[int, list[int]]:
    if scan_right < scan_left:
        raise ValueError("invalid curve scan bounds")
    effective_left = scan_left
    if scan_scope == "recent":
        effective_left = max(scan_left, scan_right - max(24, int(recent_width_px)) + 1)
    positions = list(range(effective_left, scan_right + 1, max(1, int(step))))
    if not positions or positions[-1] != scan_right:
        positions.append(scan_right)
    return effective_left, positions


def build_monkey_curve_script(
    positions: Iterable[int],
    *,
    scan_y: int,
    scan_right: int,
    dwell_milliseconds: int,
) -> str:
    values = list(positions)
    lines = [
        "type= user",
        f"count= {len(values) * 2}",
        "speed= 1.0",
        "start data >>",
    ]
    for x in values:
        lines.append(f"Drag({x},{scan_y},{min(scan_right, x + 2)},{scan_y},1)")
        lines.append(f"UserWait({max(80, int(dwell_milliseconds))})")
    return "\n".join(lines) + "\n"


def run_monkey_curve_chunk(
    *,
    adb: str,
    device_id: str,
    output_dir: Path,
    positions: list[int],
    scan_y: int,
    scan_right: int,
    dwell_milliseconds: int,
    chunk_index: int,
) -> dict[str, Any]:
    local_script = output_dir / f".curve_touch_{os.getpid()}_{chunk_index:03d}.monkey"
    remote_script = f"/data/local/tmp/gfbank_curve_touch_{os.getpid()}_{chunk_index:03d}.monkey"
    write_text_atomic(
        local_script,
        build_monkey_curve_script(
            positions,
            scan_y=scan_y,
            scan_right=scan_right,
            dwell_milliseconds=dwell_milliseconds,
        ),
    )
    run_adb(adb, device_id, "push", str(local_script), remote_script, timeout=30)
    started = time.monotonic()
    try:
        completed = run_adb(
            adb,
            device_id,
            "shell",
            "monkey",
            "-p",
            APP_PACKAGE,
            "-f",
            remote_script,
            "1",
            timeout=120,
            transient_retries=0,
        )
    finally:
        try:
            run_adb(
                adb,
                device_id,
                "shell",
                "rm",
                "-f",
                remote_script,
                timeout=10,
                transient_retries=0,
            )
        finally:
            local_script.unlink(missing_ok=True)
    return {
        "elapsed_seconds": time.monotonic() - started,
        "stdout_tail": str(completed.stdout or "")[-500:],
        "stderr_tail": str(completed.stderr or "")[-500:],
    }


def start_scrcpy_recording(
    *,
    adb: str,
    device_id: str,
    target: Path,
) -> tuple[subprocess.Popen[str], float]:
    scrcpy = resolve_scrcpy(adb)
    started = time.monotonic()
    process = subprocess.Popen(
        [
            scrcpy,
            "-s",
            device_id,
            "--no-window",
            "--no-audio",
            f"--record={target}",
            "--time-limit=300",
            "--max-fps=30",
            "--video-bit-rate=2M",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    time.sleep(2.5)
    if process.poll() is not None:
        output = process.communicate(timeout=5)[0]
        raise RuntimeError(f"scrcpy recording exited before curve scan: {output[-1000:]}")
    return process, time.monotonic() - started


def stop_scrcpy_recording(process: subprocess.Popen[str], target: Path) -> tuple[int, str]:
    if process.poll() is None:
        try:
            process.send_signal(getattr(signal, "CTRL_BREAK_EVENT", signal.SIGINT))
        except (OSError, ValueError):
            process.terminate()
    try:
        output = process.communicate(timeout=20)[0]
    except subprocess.TimeoutExpired as exc:
        process.kill()
        process.communicate(timeout=5)
        raise RuntimeError("scrcpy recording did not stop cleanly") from exc
    if process.returncode != 0:
        raise RuntimeError(f"scrcpy recording failed ({process.returncode}): {output[-1000:]}")
    video_bytes = target.stat().st_size if target.is_file() else 0
    if video_bytes < 10_000:
        raise RuntimeError(f"scrcpy recording is unexpectedly small: {video_bytes}")
    return video_bytes, output[-1000:]


def read_recorded_curve_points(
    *,
    video_path: Path,
    chart_bounds: tuple[int, int, int, int],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    try:
        import cv2  # type: ignore[import-not-found]
        import numpy as np  # type: ignore[import-not-found]
        from rapidocr_onnxruntime import RapidOCR  # type: ignore[import-not-found]
    except ModuleNotFoundError as exc:
        raise RuntimeError("recorded_ocr requires OpenCV, NumPy and RapidOCR") from exc
    reader = cv2.VideoCapture(str(video_path))
    if not reader.isOpened():
        raise RuntimeError(f"cannot open recorded curve video: {video_path}")
    engine = RapidOCR()
    observations: dict[str, set[tuple[float, float]]] = {}
    decoded_frame_total = 0
    candidate_frame_total = 0
    accepted_frame_total = 0
    previous_signature: Any | None = None
    representative_frame: Any | None = None

    def parse_representative(frame: Any) -> None:
        nonlocal candidate_frame_total, accepted_frame_total
        candidate_frame_total += 1
        encoded_ok, encoded = cv2.imencode(".png", frame)
        if not encoded_ok:
            return
        point, _diagnostics = performance_point_from_screenshot(
            bytes(encoded), chart_bounds, engine
        )
        if not point:
            return
        accepted_frame_total += 1
        observations.setdefault(str(point["trade_date"]), set()).add(
            (
                float(point["cumulative_return"]),
                float(point["benchmark_return"]),
            )
        )

    try:
        while True:
            ok, frame = reader.read()
            if not ok:
                break
            decoded_frame_total += 1
            left, top, right, _bottom = chart_bounds
            y1 = max(0, top - 69)
            y2 = min(frame.shape[0], top - 11)
            x1 = max(0, left - 13)
            x2 = min(frame.shape[1], right + 68)
            tooltip = cv2.cvtColor(frame[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)
            signature = cv2.resize(tooltip, (128, 16), interpolation=cv2.INTER_AREA)
            changed = (
                previous_signature is None
                or float(np.mean(cv2.absdiff(signature, previous_signature))) > 0.25
            )
            if changed and representative_frame is not None:
                parse_representative(representative_frame)
            representative_frame = frame
            previous_signature = signature
        if representative_frame is not None:
            parse_representative(representative_frame)
    finally:
        reader.release()
    conflicts = {
        date: sorted(values)
        for date, values in observations.items()
        if len(values) > 1
    }
    points = {
        date: {
            "trade_date": date,
            "cumulative_return": next(iter(values))[0],
            "benchmark_return": next(iter(values))[1],
        }
        for date, values in observations.items()
        if len(values) == 1
    }
    return points, {
        "decoded_frame_total": decoded_frame_total,
        "candidate_frame_total": candidate_frame_total,
        "accepted_frame_total": accepted_frame_total,
        "unique_curve_point_total": len(points),
        "value_conflict_total": len(conflicts),
        "value_conflicts": [
            {"trade_date": date, "values": values}
            for date, values in sorted(conflicts.items())[:30]
        ],
    }


def capture_since_inception_curve_points_recorded(
    *,
    adb: str,
    device_id: str,
    output_dir: Path,
    strategy_name: str,
    initial_payload: str,
    scan_step_px: int,
    ocr_verify_every: int,
    scan_scope: str,
    recent_width_px: int,
) -> dict[str, Any]:
    chart_bounds = find_resource_bounds(initial_payload, "echarts-div-line")
    if not chart_bounds:
        raise RuntimeError(f"performance chart is missing for {strategy_name}")
    left, top, right, bottom = chart_bounds
    horizontal_padding = max(3, int((right - left) * 0.01))
    scan_left = left + horizontal_padding
    scan_right = right - horizontal_padding
    scan_y = top + max(8, int((bottom - top) * 0.42))
    step = max(1, int(scan_step_px))
    effective_scan_left, positions = build_curve_scan_positions(
        scan_left,
        scan_right,
        step,
        scan_scope=scan_scope,
        recent_width_px=recent_width_px,
    )
    stem = safe_name(strategy_name)
    captured_by_date: dict[str, str] = {}
    exact_by_date: dict[str, dict[str, Any]] = {}
    failures: list[dict[str, Any]] = []
    verification_mismatches: list[dict[str, Any]] = []
    monkey_elapsed_seconds = 0.0
    verification_total = 0

    def retain_exact(payload: str, *, source: str) -> None:
        point = performance_point_from_payload(payload)
        if not point:
            failures.append({"source": source, "error": "tooltip_text_missing"})
            return
        trade_date = str(point["trade_date"])
        exact_by_date[trade_date] = point
        if trade_date in captured_by_date:
            return
        target = output_dir / f"curve_{stem}_since_inception_{trade_date.replace('-', '')}.xml"
        write_text_atomic(target, payload)
        captured_by_date[trade_date] = target.name

    retain_exact(initial_payload, source="selected_tab_initial")
    video_path = output_dir / f".curve_recording_{stem}_{os.getpid()}.mkv"
    recording_process: subprocess.Popen[str] | None = None
    video_bytes = 0
    scrcpy_start_seconds = 0.0
    scrcpy_output_tail = ""
    video_meta: dict[str, Any] = {}
    video_points: dict[str, dict[str, Any]] = {}
    # Fewer than 24 positions per chunk spends more time restarting Monkey and
    # dumping UI than reading the curve.  The exact point at the end of every
    # chunk remains a deterministic periodic verification anchor.
    verification_every = max(24, int(ocr_verify_every))
    try:
        recording_process, scrcpy_start_seconds = start_scrcpy_recording(
            adb=adb,
            device_id=device_id,
            target=video_path,
        )
        for chunk_index, start in enumerate(range(0, len(positions), verification_every)):
            chunk = positions[start : start + verification_every]
            result = run_monkey_curve_chunk(
                adb=adb,
                device_id=device_id,
                output_dir=output_dir,
                positions=chunk,
                scan_y=scan_y,
                scan_right=scan_right,
                dwell_milliseconds=350,
                chunk_index=chunk_index,
            )
            monkey_elapsed_seconds += float(result["elapsed_seconds"])
            verification_total += 1
            temp_path = output_dir / f".curve_verify_{os.getpid()}.xml"
            try:
                payload = dump_ui(adb, device_id, temp_path)
                retain_exact(payload, source=f"recorded_chunk_{chunk_index}_exact")
            finally:
                temp_path.unlink(missing_ok=True)
        video_bytes, scrcpy_output_tail = stop_scrcpy_recording(recording_process, video_path)
        recording_process = None
        video_points, video_meta = read_recorded_curve_points(
            video_path=video_path,
            chart_bounds=chart_bounds,
        )
        for trade_date, point in sorted(video_points.items()):
            exact_point = exact_by_date.get(trade_date)
            if exact_point is not None:
                if not points_equal(point, exact_point):
                    verification_mismatches.append(
                        {
                            "trade_date": trade_date,
                            "recorded": point,
                            "exact": exact_point,
                        }
                    )
                continue
            evidence = build_ocr_curve_evidence_xml(
                strategy_name,
                point,
                x=-1,
                minimum_confidence=0.80,
                source="screen_recording_ocr_periodically_verified_against_uiautomator",
            )
            target = output_dir / f"curve_{stem}_since_inception_{trade_date.replace('-', '')}.xml"
            write_text_atomic(target, evidence)
            captured_by_date[trade_date] = target.name
        if not video_points:
            failures.append({"source": "recorded_ocr", "error": "recorded_curve_no_points"})
        for item in video_meta.get("value_conflicts") or []:
            failures.append(
                {
                    "source": "recorded_ocr_value_conflict",
                    "trade_date": item.get("trade_date"),
                    "error": "same recorded date exposed more than one value pair",
                }
            )
    finally:
        if recording_process is not None and recording_process.poll() is None:
            try:
                stop_scrcpy_recording(recording_process, video_path)
            except Exception:  # noqa: BLE001 - preserve the original capture error.
                pass
        video_path.unlink(missing_ok=True)

    all_failures = failures + [
        {
            "source": "recorded_ocr_exact_verification",
            "trade_date": item["trade_date"],
            "error": "recorded OCR point disagrees with exact UI text",
        }
        for item in verification_mismatches
    ]
    manifest = {
        "strategy_name": strategy_name,
        "chart_bounds": list(chart_bounds),
        "scan_bounds": [effective_scan_left, scan_y, scan_right, scan_y],
        "full_chart_scan_bounds": [scan_left, scan_y, scan_right, scan_y],
        "scan_scope": scan_scope,
        "recent_width_px": int(recent_width_px) if scan_scope == "recent" else None,
        "initial_scan_step_px": step,
        "dense_refinement_executed": False,
        "dense_refinement_allowed": False,
        "touch_attempt_total": len(positions),
        "valid_tooltip_attempt_total": len(captured_by_date),
        "unique_curve_point_total": len(captured_by_date),
        "first_trade_date": min(captured_by_date, default=None),
        "last_trade_date": max(captured_by_date, default=None),
        "curve_files": [captured_by_date[key] for key in sorted(captured_by_date)],
        "failure_total": len(all_failures),
        "failures": all_failures[:50],
        "value_source": "screen_recording_ocr_periodically_verified_against_uiautomator",
        "ocr_verification_every": verification_every,
        "ocr_verification_total": verification_total,
        "ocr_verification_mismatch_total": len(verification_mismatches),
        "ocr_verification_mismatches": verification_mismatches[:20],
        "ocr_fallback_total": None,
        "ocr_min_confidence": 0.80,
        "video_bytes_before_cleanup": video_bytes,
        "video_transport_deleted": not video_path.exists(),
        "video_capture": video_meta,
        "monkey_elapsed_seconds": round(monkey_elapsed_seconds, 3),
        "scrcpy_start_seconds": round(scrcpy_start_seconds, 3),
        "scrcpy_output_tail": scrcpy_output_tail,
    }
    write_text_atomic(
        output_dir / f"curve_{stem}_since_inception_manifest.json",
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
    )
    return manifest


def capture_since_inception_curve_points(
    *,
    adb: str,
    device_id: str,
    output_dir: Path,
    strategy_name: str,
    initial_payload: str,
    scan_step_px: int,
    wait_seconds: float,
    allow_dense_refinement: bool,
    read_mode: str,
    ocr_verify_every: int,
    scan_scope: str,
    recent_width_px: int,
) -> dict[str, Any]:
    """Move across the ECharts plot and persist each distinct tooltip date.

    Exact mode reads every point from UIAutomator.  Verified OCR mode reads the
    fixed tooltip summary row from the screen and periodically compares all
    three fields with UIAutomator text from the same coordinate.
    """
    auto_fallback_error: str | None = None
    if read_mode in {"auto", "recorded_ocr"}:
        try:
            recorded_manifest = capture_since_inception_curve_points_recorded(
                adb=adb,
                device_id=device_id,
                output_dir=output_dir,
                strategy_name=strategy_name,
                initial_payload=initial_payload,
                scan_step_px=scan_step_px,
                ocr_verify_every=ocr_verify_every,
                scan_scope=scan_scope,
                recent_width_px=recent_width_px,
            )
            if read_mode == "auto" and int(recorded_manifest.get("failure_total") or 0) > 0:
                raise RuntimeError(
                    f"recorded_ocr manifest reports {recorded_manifest['failure_total']} failures"
                )
            return recorded_manifest
        except Exception as exc:
            if read_mode == "recorded_ocr":
                raise
            auto_fallback_error = f"{type(exc).__name__}: {exc}"
            stem = safe_name(strategy_name)
            for path in output_dir.glob(f"curve_{stem}_since_inception_*.xml"):
                path.unlink(missing_ok=True)
            (output_dir / f"curve_{stem}_since_inception_manifest.json").unlink(missing_ok=True)
            read_mode = "exact_ui"
    chart_bounds = find_resource_bounds(initial_payload, "echarts-div-line")
    if not chart_bounds:
        raise RuntimeError(f"performance chart is missing for {strategy_name}")
    left, top, right, bottom = chart_bounds
    horizontal_padding = max(3, int((right - left) * 0.01))
    scan_left = left + horizontal_padding
    scan_right = right - horizontal_padding
    scan_y = top + max(8, int((bottom - top) * 0.42))
    step = max(1, int(scan_step_px))
    effective_scan_left, coarse_positions = build_curve_scan_positions(
        scan_left,
        scan_right,
        step,
        scan_scope=scan_scope,
        recent_width_px=recent_width_px,
    )
    stem = safe_name(strategy_name)
    captured_by_date: dict[str, str] = {}
    attempts = 0
    valid_attempts = 0
    failed_attempts: list[dict[str, Any]] = []
    verification_total = 0
    verification_mismatches: list[dict[str, Any]] = []
    ocr_fallback_total = 0
    ocr_rejection_samples: list[dict[str, Any]] = []
    ocr_min_confidence = 1.0
    ocr_engine: Any | None = None
    if read_mode == "verified_ocr":
        try:
            from rapidocr_onnxruntime import RapidOCR  # type: ignore[import-not-found]
        except ModuleNotFoundError as exc:
            raise RuntimeError("rapidocr_onnxruntime is required for verified OCR curve capture") from exc
        ocr_engine = RapidOCR()

    def retain(payload: str, *, x: int | None, source: str, point: dict[str, Any] | None = None) -> None:
        nonlocal valid_attempts
        point = point or performance_point_from_payload(payload)
        if not point:
            failed_attempts.append({"x": x, "source": source, "error": "tooltip_text_missing"})
            return
        valid_attempts += 1
        trade_date = str(point["trade_date"])
        if trade_date in captured_by_date:
            return
        target = output_dir / f"curve_{stem}_since_inception_{trade_date.replace('-', '')}.xml"
        write_text_atomic(target, payload)
        captured_by_date[trade_date] = target.name

    retain(initial_payload, x=None, source="selected_tab_initial")
    sampled_x: set[int] = set()

    def scan_positions(positions: Iterable[int], pass_name: str) -> None:
        nonlocal attempts, verification_total, ocr_fallback_total, ocr_min_confidence
        consecutive_failures = 0
        for x in positions:
            if x in sampled_x:
                continue
            sampled_x.add(x)
            attempts += 1
            temp_path = output_dir / f".curve_probe_{os.getpid()}.xml"
            try:
                swipe(adb, device_id, x, scan_y, min(scan_right, x + 2), scan_y, 70)
                if wait_seconds > 0:
                    time.sleep(wait_seconds)
                before = len(captured_by_date)
                if read_mode == "exact_ui":
                    payload = dump_ui(adb, device_id, temp_path)
                    retain(payload, x=x, source=pass_name)
                else:
                    assert ocr_engine is not None
                    ocr_point, ocr_diagnostics = performance_point_from_screenshot(
                        screenshot_bytes(adb, device_id),
                        chart_bounds,
                        ocr_engine,
                    )
                    ocr_min_confidence = min(
                        ocr_min_confidence,
                        float(ocr_diagnostics.get("minimum_confidence") or 0.0),
                    )
                    verify = attempts == 1 or attempts % max(1, int(ocr_verify_every)) == 0 or ocr_point is None
                    exact_payload: str | None = None
                    exact_point: dict[str, Any] | None = None
                    if verify:
                        verification_total += 1
                        exact_payload = dump_ui(adb, device_id, temp_path)
                        exact_point = performance_point_from_payload(exact_payload)
                        if ocr_point is not None and not points_equal(ocr_point, exact_point):
                            verification_mismatches.append(
                                {
                                    "x": x,
                                    "pass": pass_name,
                                    "ocr": ocr_point,
                                    "exact": exact_point,
                                    "ocr_diagnostics": ocr_diagnostics,
                                }
                            )
                    if ocr_point is None:
                        ocr_fallback_total += 1
                        if len(ocr_rejection_samples) < 30:
                            ocr_rejection_samples.append(
                                {
                                    "x": x,
                                    "pass": pass_name,
                                    "diagnostics": ocr_diagnostics,
                                }
                            )
                        if exact_payload is None:
                            exact_payload = dump_ui(adb, device_id, temp_path)
                            exact_point = performance_point_from_payload(exact_payload)
                        retain(exact_payload, x=x, source=f"{pass_name}_ocr_fallback", point=exact_point)
                    elif exact_payload is not None:
                        retain(exact_payload, x=x, source=f"{pass_name}_ocr_verified", point=exact_point)
                    else:
                        evidence = build_ocr_curve_evidence_xml(
                            strategy_name,
                            ocr_point,
                            x=x,
                            minimum_confidence=float(ocr_diagnostics["minimum_confidence"]),
                        )
                        retain(evidence, x=x, source=f"{pass_name}_verified_ocr", point=ocr_point)
                consecutive_failures = 0 if len(captured_by_date) >= before else consecutive_failures + 1
            except Exception as exc:  # noqa: BLE001 - preserve other exact points and report the failed coordinate.
                consecutive_failures += 1
                failed_attempts.append({"x": x, "source": pass_name, "error": f"{type(exc).__name__}: {exc}"})
                if consecutive_failures >= 8:
                    break
            finally:
                temp_path.unlink(missing_ok=True)

    scan_positions(coarse_positions, "coarse")
    coarse_attempts = len(sampled_x)
    # If almost every coarse touch produced a different date, points are denser
    # than the coarse step.  Cover the unsampled pixels so dates are not silently
    # dropped.  Sparse curves do not pay this extra runtime cost.
    density = len(captured_by_date) / max(1, coarse_attempts)
    refined = allow_dense_refinement and step > 1 and density >= 0.70
    if refined:
        scan_positions(range(effective_scan_left, scan_right + 1), "dense_refinement")

    manifest = {
        "strategy_name": strategy_name,
        "chart_bounds": list(chart_bounds),
        "scan_bounds": [effective_scan_left, scan_y, scan_right, scan_y],
        "full_chart_scan_bounds": [scan_left, scan_y, scan_right, scan_y],
        "scan_scope": scan_scope,
        "recent_width_px": int(recent_width_px) if scan_scope == "recent" else None,
        "initial_scan_step_px": step,
        "dense_refinement_executed": refined,
        "dense_refinement_allowed": allow_dense_refinement,
        "touch_attempt_total": attempts,
        "valid_tooltip_attempt_total": valid_attempts,
        "unique_curve_point_total": len(captured_by_date),
        "first_trade_date": min(captured_by_date, default=None),
        "last_trade_date": max(captured_by_date, default=None),
        "curve_files": [captured_by_date[key] for key in sorted(captured_by_date)],
        "failure_total": len(failed_attempts) + len(verification_mismatches),
        "failures": (
            failed_attempts
            + [
                {
                    "x": item["x"],
                    "source": "ocr_exact_verification",
                    "error": "OCR point disagrees with exact UI text",
                }
                for item in verification_mismatches
            ]
        )[:50],
        "value_source": (
            "uiautomator_exact_tooltip_text_not_chart_pixels"
            if read_mode == "exact_ui"
            else "screen_ocr_periodically_verified_against_uiautomator"
        ),
        "ocr_verification_every": max(1, int(ocr_verify_every)) if read_mode == "verified_ocr" else None,
        "ocr_verification_total": verification_total,
        "ocr_verification_mismatch_total": len(verification_mismatches),
        "ocr_verification_mismatches": verification_mismatches[:20],
        "ocr_fallback_total": ocr_fallback_total,
        "ocr_rejection_samples": ocr_rejection_samples,
        "ocr_min_confidence": round(ocr_min_confidence, 6) if read_mode == "verified_ocr" else None,
        "auto_fallback_from": "recorded_ocr" if auto_fallback_error else None,
        "auto_fallback_reason": auto_fallback_error,
    }
    write_text_atomic(
        output_dir / f"curve_{stem}_since_inception_manifest.json",
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
    )
    return manifest


def capture_detail_intervals(
    *,
    adb: str,
    device_id: str,
    output_dir: Path,
    strategy_name: str,
    wait_seconds: float,
    curve_scan_step_px: int,
    curve_wait_seconds: float,
    capture_curve_points: bool,
    allow_dense_refinement: bool,
    curve_read_mode: str,
    curve_ocr_verify_every: int,
    curve_scan_scope: str,
    curve_recent_width_px: int,
    cached_curve_state: dict[str, Any],
) -> dict[str, Any]:
    captured: list[str] = []
    curve_manifest: dict[str, Any] | None = None
    curve_skip: dict[str, Any] | None = None
    benchmark_capture: dict[str, Any] | None = None
    stem = safe_name(strategy_name)
    for code, aliases in INTERVAL_TABS:
        current_probe = output_dir / f".detail_probe_{os.getpid()}.xml"
        center: tuple[int, int] | None = None
        payload = ""
        try:
            for attempt in range(7):
                payload = dump_ui(adb, device_id, current_probe)
                center = find_text_center(payload, aliases)
                if center:
                    break
                if attempt < 6:
                    time.sleep(0.8)
        finally:
            current_probe.unlink(missing_ok=True)
        if not center:
            raise RuntimeError(f"interval tab is missing for {strategy_name}: {code}")
        tap(adb, device_id, *center)
        time.sleep(wait_seconds)
        xml_path = output_dir / f"detail_{stem}_{code}.xml"
        payload = dump_ui(adb, device_id, xml_path)
        if not has_text(payload, strategy_name) and not has_text(payload, "投顾策略详情"):
            raise RuntimeError(f"detail page changed unexpectedly for {strategy_name}: {code}")
        if code == "1m":
            screenshot(adb, device_id, output_dir / f"detail_{stem}.png")
        captured.append(str(xml_path))
        if code == "since_inception" and capture_curve_points:
            current_point = performance_point_from_payload(payload)
            if curve_scan_scope == "recent" and cached_curve_matches_current(
                cached_curve_state,
                current_point,
            ):
                curve_skip = {
                    "strategy_name": strategy_name,
                    "scan_scope": "unchanged",
                    "scan_scope_requested": "auto_or_recent",
                    "value_source": "uiautomator_exact_latest_matches_authenticated_cache",
                    "cached_curve_point_total_before_capture": int(
                        cached_curve_state.get("curve_point_total") or 0
                    ),
                    "cached_last_trade_date_before_capture": cached_curve_state.get("last_trade_date"),
                    "detail_latest_point": current_point,
                    "touch_attempt_total": 0,
                    "unique_curve_point_total": 0,
                    "failure_total": 0,
                    "failures": [],
                    "video_transport_deleted": True,
                }
                write_text_atomic(
                    output_dir / f"curve_{stem}_since_inception_manifest.json",
                    json.dumps(curve_skip, ensure_ascii=False, indent=2) + "\n",
                )
            else:
                curve_manifest = capture_since_inception_curve_points(
                    adb=adb,
                    device_id=device_id,
                    output_dir=output_dir,
                    strategy_name=strategy_name,
                    initial_payload=payload,
                    scan_step_px=curve_scan_step_px,
                    wait_seconds=max(0.0, curve_wait_seconds),
                    allow_dense_refinement=allow_dense_refinement,
                    read_mode=curve_read_mode,
                    ocr_verify_every=curve_ocr_verify_every,
                    scan_scope=curve_scan_scope,
                    recent_width_px=curve_recent_width_px,
                )
    benchmark_capture = capture_benchmark_disclosure(
        adb=adb,
        device_id=device_id,
        output_dir=output_dir,
        strategy_name=strategy_name,
        initial_payload=payload,
        wait_seconds=wait_seconds,
    )
    if benchmark_capture.get("source_file"):
        captured.append(str(output_dir / str(benchmark_capture["source_file"])))
    return {
        "detail_files": captured,
        "curve_manifest": curve_manifest,
        "curve_skip": curve_skip,
        "benchmark_capture": benchmark_capture,
    }


def target_profit_strategy_names(payload: str) -> list[str]:
    names: list[str] = []
    for node in xml_nodes(payload):
        text = re.sub(r"^(?:南方|广发|招商|博时|景顺长城|鹏华)(?:基金)?\s*[-—]\s*", "", node_text(node)).strip()
        if "目标" in text and re.search(r"\d+期$", text):
            names.append(text)
    return list(dict.fromkeys(names))


def find_content_text_center(payload: str, value: str, *, min_y: int = 300) -> tuple[int, int] | None:
    candidates: list[tuple[int, int]] = []
    for node in xml_nodes(payload):
        if node_text(node) != value:
            continue
        center = bounds_center(node.attrib.get("bounds"))
        if center and center[1] >= min_y:
            candidates.append(center)
    return max(candidates, key=lambda item: item[1], default=None)


def wait_for_special_detail(
    adb: str,
    device_id: str,
    output_dir: Path,
    entry_label: str,
) -> str:
    last_payload = ""
    for attempt in range(15):
        path = output_dir / f".special_wait_{os.getpid()}_{attempt}.xml"
        try:
            last_payload = dump_ui(adb, device_id, path)
        finally:
            path.unlink(missing_ok=True)
        texts = [node_text(node) for node in xml_nodes(last_payload) if node_text(node)]
        if entry_label == "超级定投" and any("基金超级定投" in text for text in texts):
            return last_payload
        if entry_label == "目标盈" and any("往期服务" in text for text in texts):
            return last_payload
        time.sleep(0.8)
    raise RuntimeError(f"{entry_label} provider detail did not appear")


def capture_target_profit_info_banner(
    *,
    adb: str,
    device_id: str,
    output_dir: Path,
    initial_payload: str,
) -> dict[str, Any]:
    """Open the official target-profit introduction banner and record its route.

    The advertisement response itself is authenticated and signed.  This
    physical-device probe follows the same in-app click a user would make and
    persists only sanitized H5 identifiers; session, token, device and signing
    material never leave ``dumpsys`` output.
    """
    center = target_profit_info_banner_center(initial_payload)
    if not center:
        return {"status": "banner_bounds_missing", "destination_route": {}}
    source_route = capture_h5_route_metadata(adb, device_id)
    tap(adb, device_id, *center)
    time.sleep(2.0)
    destination_xml = output_dir / "special_目标盈_介绍页.xml"
    destination_png = output_dir / "special_目标盈_介绍页.png"
    destination_payload = dump_ui(adb, device_id, destination_xml)
    screenshot(adb, device_id, destination_png)
    destination_route = capture_h5_route_metadata(adb, device_id)
    remained_on_list = is_strategy_list(destination_payload, "目标盈")
    if not remained_on_list:
        press_back(adb, device_id)
        time.sleep(1.0)
        navigate_to_strategy_list(adb, device_id, output_dir, "目标盈")
    return {
        "status": "no_navigation" if remained_on_list else "success",
        "source_route": source_route,
        "destination_route": destination_route,
        "source_files": [destination_xml.name, destination_png.name],
    }


def capture_super_invest_entry(
    *,
    adb: str,
    device_id: str,
    output_dir: Path,
    provider_cards: list[dict[str, Any]],
) -> dict[str, Any]:
    captured: list[str] = []
    route_metadata: list[dict[str, Any]] = []
    for provider in provider_cards:
        navigate_to_strategy_list(adb, device_id, output_dir, "超级定投")
        tap(adb, device_id, int(provider["tap_x"]), int(provider["tap_y"]))
        payload = wait_for_special_detail(adb, device_id, output_dir, "超级定投")
        names = [
            node_text(node)
            for node in xml_nodes(payload)
            if re.fullmatch(r"(?:南方|广发|招商|博时|景顺长城|鹏华)基金超级定投", node_text(node))
        ]
        name = names[0] if names else f"{provider['provider_prefix']}基金超级定投"
        stem = safe_name(name)
        screenshot(adb, device_id, output_dir / f"special_超级定投_{stem}.png")
        dump_ui(adb, device_id, output_dir / f"special_超级定投_{stem}.xml")
        route = capture_h5_route_metadata(adb, device_id)
        route.update({"strategy_entry": "超级定投", "strategy_name": name})
        route_metadata.append(route)
        captured.append(name)
        tap_top_bar_back(adb, device_id, payload)
        time.sleep(1.2)
    return {
        "strategy_names": list(dict.fromkeys(captured)),
        "route_metadata": route_metadata,
    }


def capture_target_profit_entry(
    *,
    adb: str,
    device_id: str,
    output_dir: Path,
    provider_cards: list[dict[str, Any]],
    max_history_views: int,
    wait_seconds: float,
    curve_scan_step_px: int,
    curve_wait_seconds: float,
    capture_curve_points: bool,
    allow_dense_refinement: bool,
    curve_read_mode: str,
    curve_ocr_verify_every: int,
    curve_scan_scope_requested: str,
    curve_recent_width_px: int,
    cached_curve_state: dict[str, Any],
) -> dict[str, Any]:
    captured: list[str] = []
    detail_captures: list[dict[str, Any]] = []
    detail_failures: list[dict[str, str]] = []
    route_metadata: list[dict[str, Any]] = []
    for provider in provider_cards:
        navigate_to_strategy_list(adb, device_id, output_dir, "目标盈")
        tap(adb, device_id, int(provider["tap_x"]), int(provider["tap_y"]))
        payload = wait_for_special_detail(adb, device_id, output_dir, "目标盈")
        provider_prefix = str(provider["provider_prefix"])
        provider_stem = safe_name(provider_prefix)
        screenshot(adb, device_id, output_dir / f"special_目标盈_{provider_stem}_current.png")
        dump_ui(adb, device_id, output_dir / f"special_目标盈_{provider_stem}_current.xml")
        current_names = target_profit_strategy_names(payload)
        captured.extend(current_names)
        landing_route = capture_h5_route_metadata(adb, device_id)
        landing_route.update(
            {
                "strategy_entry": "目标盈",
                "strategy_name": current_names[0] if current_names else provider_prefix,
                "route_role": "target_profit_current_landing",
            }
        )
        route_metadata.append(landing_route)

        # The current target-profit card is an official navigation entry into
        # pro_detail.html.  Embedded H5 1.1.2.97 explicitly hides performance
        # while ``spGroupCode`` is present; newer online bundles are still
        # feature-detected at runtime.  The parent ``groupCode`` is retained so
        # a group-only parent detail can be opened later.  Historical cards are
        # deliberately never assigned the parent curve.
        current_name = current_names[0] if current_names else None
        current_center = (
            find_content_text_center(payload, current_name, min_y=300)
            if current_name
            else None
        )
        if current_name and current_center:
            entered_detail = False
            try:
                tap(adb, device_id, *current_center)
                detail_payload = wait_for_text(adb, device_id, output_dir, "投顾策略详情")
                entered_detail = True
                detail_name = detail_strategy_name_from_payload(detail_payload) or current_name
                detail_stem = safe_name(detail_name)
                screenshot(adb, device_id, output_dir / f"detail_{detail_stem}_base.png")
                detail_payload = dump_ui(
                    adb,
                    device_id,
                    output_dir / f"detail_{detail_stem}_base.xml",
                )
                detail_route = capture_h5_route_metadata(adb, device_id)
                detail_route.update(
                    {
                        "strategy_entry": "目标盈",
                        "strategy_name": detail_name,
                        "route_role": "target_profit_child_detail",
                    }
                )
                route_metadata.append(detail_route)
                effective_curve_scan_scope, cached_strategy_curve = resolve_curve_scan_scope(
                    curve_scan_scope_requested,
                    detail_name,
                    cached_curve_state,
                )
                performance_visible = has_text(detail_payload, "业绩表现") and has_text(
                    detail_payload,
                    "成立以来",
                )
                if performance_visible:
                    result = capture_detail_intervals(
                        adb=adb,
                        device_id=device_id,
                        output_dir=output_dir,
                        strategy_name=detail_name,
                        wait_seconds=wait_seconds,
                        curve_scan_step_px=curve_scan_step_px,
                        curve_wait_seconds=curve_wait_seconds,
                        capture_curve_points=capture_curve_points,
                        allow_dense_refinement=allow_dense_refinement,
                        curve_read_mode=curve_read_mode,
                        curve_ocr_verify_every=curve_ocr_verify_every,
                        curve_scan_scope=effective_curve_scan_scope,
                        curve_recent_width_px=curve_recent_width_px,
                        cached_curve_state=cached_strategy_curve,
                    )
                    disclosure_status = "official_child_detail_performance_visible"
                else:
                    result = {
                        "detail_files": [
                            str(output_dir / f"detail_{detail_stem}_base.png"),
                            str(output_dir / f"detail_{detail_stem}_base.xml"),
                        ],
                        "curve_manifest": None,
                        "curve_skip": {
                            "strategy_name": detail_name,
                            "reason": "official_child_detail_sp_group_code_hides_performance",
                            "group_code": detail_route.get("group_code"),
                            "sp_group_code": detail_route.get("sp_group_code"),
                        },
                        "benchmark_capture": {
                            "strategy_name": detail_name,
                            "status": "not_disclosed_on_target_child_detail",
                            "source_file": None,
                        },
                    }
                    disclosure_status = "official_child_detail_performance_hidden"
                detail_captures.append(
                    {
                        "strategy_name": detail_name,
                        "entry_strategy_name": current_name,
                        "result": result,
                        "curve_scan_scope_requested": curve_scan_scope_requested,
                        "cached_curve_point_total_before_capture": int(
                            cached_strategy_curve.get("curve_point_total") or 0
                        ),
                        "cached_last_trade_date_before_capture": cached_strategy_curve.get(
                            "last_trade_date"
                        ),
                        "performance_disclosure_status": disclosure_status,
                        "h5_route_metadata": detail_route,
                        # Reverse-verified from the official H5: MP8768 receives
                        # groupCode + spGroupCode, while MP8769 receives groupCode
                        # only.  Keep this lineage visible so a parent curve is
                        # never misrepresented as child-period-specific history.
                        "performance_entity_scope": "underlying_parent_group_code",
                        "performance_lineage_evidence": (
                            "official_h5_mp8768_group_and_sp_group_code_"
                            "mp8769_group_code_only"
                        ),
                    }
                )
                captured.append(detail_name)
            except Exception as exc:  # noqa: BLE001 - retain entry/history evidence and disclose detail gap.
                detail_failures.append(
                    {
                        "strategy_name": current_name,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
            finally:
                if entered_detail:
                    press_back(adb, device_id)
                    time.sleep(1.0)
                    payload = wait_for_special_detail(adb, device_id, output_dir, "目标盈")
        elif current_name:
            detail_failures.append(
                {
                    "strategy_name": current_name,
                    "error": "current_target_profit_card_bounds_missing",
                }
            )
        more_center = find_content_text_center(payload, "更多")
        if more_center:
            tap(adb, device_id, *more_center)
            history_payload = payload
            try:
                history_payload = wait_for_text(adb, device_id, output_dir, "往期服务")
                seen_names: set[str] = set()
                no_new_views = 0
                for view_index in range(max(1, max_history_views)):
                    xml_path = output_dir / f"special_目标盈_{provider_stem}_history_{view_index:02d}.xml"
                    image_path = output_dir / f"special_目标盈_{provider_stem}_history_{view_index:02d}.png"
                    screenshot(adb, device_id, image_path)
                    history_payload = dump_ui(adb, device_id, xml_path)
                    names = target_profit_strategy_names(history_payload)
                    new_names = set(names) - seen_names
                    captured.extend(names)
                    seen_names.update(names)
                    no_new_views = 0 if new_names else no_new_views + 1
                    if no_new_views >= 2 or has_text(history_payload, "没有更多"):
                        break
                    swipe(adb, device_id, 540, 2050, 540, 560, 700)
                    time.sleep(1.2)
            finally:
                tap_top_bar_back(adb, device_id, history_payload)
                time.sleep(1.0)
        tap_top_bar_back(adb, device_id, payload)
        time.sleep(1.2)
    return {
        "strategy_names": list(dict.fromkeys(captured)),
        "detail_captures": detail_captures,
        "detail_failures": detail_failures,
        "route_metadata": route_metadata,
    }


def main() -> int:
    args = parse_args()
    workspace_root = args.workspace_root.resolve()
    code_root = args.code_root.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    if (workspace_root / "运行状态" / "daily_update.lock").exists():
        raise RuntimeError("daily update lock exists; do not compete for the physical device")
    python_src = code_root / "节点脚本" / "_共享组件" / "python_src"
    if not python_src.is_dir():
        raise RuntimeError(f"invalid code root: {code_root}")
    sys.path.insert(0, str(python_src))
    from advisor_monitor.collectors.gfbank_authenticated_ui import (  # noqa: PLC0415
        ocr_entry_provider_cards,
        ocr_strategy_cards,
    )

    device_id = ensure_device(args.adb, args.device_id, args.adb_startup_wait_seconds)
    run_id = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z")
    captured_names: set[str] = set()
    entry_labels = list(dict.fromkeys(args.strategy_entry or ["理财组合"]))
    captured_names_by_entry: dict[str, list[str]] = {entry_label: [] for entry_label in entry_labels}
    requested_names = {str(value).strip() for value in args.strategy_name if str(value).strip()}
    cached_curve_state = load_cached_curve_state(code_root)
    curve_manifests: list[dict[str, Any]] = []
    curve_skips: list[dict[str, Any]] = []
    benchmark_captures: list[dict[str, Any]] = []
    performance_lineages: list[dict[str, Any]] = []
    h5_route_metadata: list[dict[str, Any]] = []
    route_discovery_probes: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    list_views = 0

    with device_lock(workspace_root, run_id):
        for entry_index, entry_label in enumerate(entry_labels):
            if args.max_products > 0 and len(captured_names) >= args.max_products:
                break
            navigate_to_strategy_list(args.adb, device_id, output_dir, entry_label)
            if entry_label in {"超级定投", "目标盈"}:
                entry_stem = safe_name(entry_label)
                image_path = output_dir / f"combo_{entry_stem}_00.png"
                xml_path = output_dir / f"combo_{entry_stem}_00.xml"
                screenshot(args.adb, device_id, image_path)
                payload = dump_ui(args.adb, device_id, xml_path)
                list_views += 1
                if entry_label == "目标盈":
                    try:
                        info_probe = capture_target_profit_info_banner(
                            adb=args.adb,
                            device_id=device_id,
                            output_dir=output_dir,
                            initial_payload=payload,
                        )
                    except Exception as exc:  # noqa: BLE001 - product capture can still continue.
                        info_probe = {
                            "status": "failed",
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    route_discovery_probes.append(
                        {
                            "strategy_entry": "目标盈",
                            "route_role": "target_profit_official_info_banner",
                            **info_probe,
                        }
                    )
                provider_cards = ocr_entry_provider_cards([image_path])
                if not provider_cards:
                    failures.append(
                        {
                            "strategy_entry": entry_label,
                            "strategy_name": "__provider_cards__",
                            "error": "provider_card_ocr_returned_empty",
                        }
                    )
                    continue
                try:
                    if entry_label == "超级定投":
                        super_capture = capture_super_invest_entry(
                            adb=args.adb,
                            device_id=device_id,
                            output_dir=output_dir,
                            provider_cards=provider_cards,
                        )
                        special_names = super_capture["strategy_names"]
                        h5_route_metadata.extend(super_capture["route_metadata"])
                    else:
                        special_capture = capture_target_profit_entry(
                            adb=args.adb,
                            device_id=device_id,
                            output_dir=output_dir,
                            provider_cards=provider_cards,
                            max_history_views=max(1, args.max_list_views),
                            wait_seconds=max(0.5, args.wait_seconds),
                            curve_scan_step_px=max(1, args.curve_scan_step_px),
                            curve_wait_seconds=max(0.0, args.curve_wait_seconds),
                            capture_curve_points=not args.skip_curve_points,
                            allow_dense_refinement=not args.skip_curve_dense_refinement,
                            curve_read_mode=args.curve_read_mode,
                            curve_ocr_verify_every=max(1, args.curve_ocr_verify_every),
                            curve_scan_scope_requested=args.curve_scan_scope,
                            curve_recent_width_px=max(24, args.curve_recent_width_px),
                            cached_curve_state=cached_curve_state,
                        )
                        special_names = special_capture["strategy_names"]
                        h5_route_metadata.extend(special_capture["route_metadata"])
                        for detail_capture in special_capture["detail_captures"]:
                            name = str(detail_capture["strategy_name"])
                            result = detail_capture["result"]
                            lineage = {
                                "strategy_entry": entry_label,
                                "strategy_name": name,
                                "entry_strategy_name": detail_capture["entry_strategy_name"],
                                "performance_disclosure_status": detail_capture[
                                    "performance_disclosure_status"
                                ],
                                "performance_entity_scope": detail_capture["performance_entity_scope"],
                                "performance_lineage_evidence": detail_capture[
                                    "performance_lineage_evidence"
                                ],
                                # Only persist route identifiers needed to reproduce
                                # the official request.  Session/device/signing fields
                                # are intentionally excluded by the sanitizer.
                                "h5_route_metadata": detail_capture["h5_route_metadata"],
                            }
                            performance_lineages.append(lineage)
                            if result.get("curve_manifest"):
                                curve_manifest = result["curve_manifest"]
                                curve_manifest.update(lineage)
                                curve_manifest["scan_scope_requested"] = detail_capture[
                                    "curve_scan_scope_requested"
                                ]
                                curve_manifest["cached_curve_point_total_before_capture"] = detail_capture[
                                    "cached_curve_point_total_before_capture"
                                ]
                                curve_manifest["cached_last_trade_date_before_capture"] = detail_capture[
                                    "cached_last_trade_date_before_capture"
                                ]
                                write_text_atomic(
                                    output_dir / f"curve_{safe_name(name)}_since_inception_manifest.json",
                                    json.dumps(curve_manifest, ensure_ascii=False, indent=2) + "\n",
                                )
                                curve_manifests.append(curve_manifest)
                            if result.get("curve_skip"):
                                result["curve_skip"].update(lineage)
                                curve_skips.append(result["curve_skip"])
                            if result.get("benchmark_capture"):
                                result["benchmark_capture"].update(lineage)
                                benchmark_captures.append(result["benchmark_capture"])
                        failures.extend(
                            {
                                "strategy_entry": entry_label,
                                "strategy_name": item["strategy_name"],
                                "error": item["error"],
                            }
                            for item in special_capture["detail_failures"]
                        )
                    captured_names.update(special_names)
                    captured_names_by_entry[entry_label].extend(special_names)
                except Exception as exc:  # noqa: BLE001
                    failures.append(
                        {
                            "strategy_entry": entry_label,
                            "strategy_name": "__special_entry__",
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )
                continue
            for _ in range(2):
                swipe(args.adb, device_id, 540, 650, 540, 2050, 500)
                time.sleep(0.8)
            no_new_views = 0
            for view_index in range(max(1, args.max_list_views)):
                list_views += 1
                entry_stem = safe_name(entry_label)
                image_path = output_dir / f"combo_{entry_stem}_{view_index:02d}.png"
                xml_path = output_dir / f"combo_{entry_stem}_{view_index:02d}.xml"
                screenshot(args.adb, device_id, image_path)
                payload = dump_ui(args.adb, device_id, xml_path)
                if not is_strategy_list(payload, entry_label):
                    raise RuntimeError(f"strategy list disappeared during capture: {entry_label}")
                try:
                    cards = ocr_strategy_cards([image_path])
                except RuntimeError:
                    cards = []
                if not cards:
                    cards = [
                        {
                            "strategy_name": f"__ui_card_{entry_index}_{view_index}_{index}_{center[0]}_{center[1]}",
                            "tap_x": center[0],
                            "tap_y": center[1],
                            "card_source": "clickable_ui_bounds",
                        }
                        for index, center in enumerate(strategy_card_centers_from_ui(payload))
                    ]
                new_cards = [
                    card
                    for card in cards
                    if card["strategy_name"] not in captured_names
                    and (
                        not requested_names
                        or str(card["strategy_name"]).startswith("__ui_card_")
                        or card_matches_requested_strategy(card, requested_names)
                    )
                ]
                if not new_cards and not requested_names:
                    no_new_views += 1
                else:
                    no_new_views = 0
                for card in new_cards:
                    if args.max_products > 0 and len(captured_names) >= args.max_products:
                        break
                    discovered_name = str(card["strategy_name"])
                    failure_name = discovered_name
                    try:
                        tap(args.adb, device_id, int(card["tap_x"]), int(card["tap_y"]))
                        detail_payload = wait_for_text(args.adb, device_id, output_dir, "投顾策略详情")
                        name = detail_strategy_name_from_payload(detail_payload) or discovered_name
                        failure_name = name
                        if name in captured_names:
                            continue
                        if requested_names and not any(
                            requested == name or requested in name or name in requested
                            for requested in requested_names
                        ):
                            continue
                        effective_curve_scan_scope, cached_strategy_curve = resolve_curve_scan_scope(
                            args.curve_scan_scope,
                            name,
                            cached_curve_state,
                        )
                        result = capture_detail_intervals(
                            adb=args.adb,
                            device_id=device_id,
                            output_dir=output_dir,
                            strategy_name=name,
                            wait_seconds=max(0.5, args.wait_seconds),
                            curve_scan_step_px=max(1, args.curve_scan_step_px),
                            curve_wait_seconds=max(0.0, args.curve_wait_seconds),
                            capture_curve_points=not args.skip_curve_points,
                            allow_dense_refinement=not args.skip_curve_dense_refinement,
                            curve_read_mode=args.curve_read_mode,
                            curve_ocr_verify_every=max(1, args.curve_ocr_verify_every),
                            curve_scan_scope=effective_curve_scan_scope,
                            curve_recent_width_px=max(24, args.curve_recent_width_px),
                            cached_curve_state=cached_strategy_curve,
                        )
                        if result.get("curve_manifest"):
                            curve_manifest = result["curve_manifest"]
                            curve_manifest["strategy_entry"] = entry_label
                            curve_manifest["scan_scope_requested"] = args.curve_scan_scope
                            curve_manifest["cached_curve_point_total_before_capture"] = int(
                                cached_strategy_curve.get("curve_point_total") or 0
                            )
                            curve_manifest["cached_last_trade_date_before_capture"] = cached_strategy_curve.get(
                                "last_trade_date"
                            )
                            write_text_atomic(
                                output_dir / f"curve_{safe_name(name)}_since_inception_manifest.json",
                                json.dumps(curve_manifest, ensure_ascii=False, indent=2) + "\n",
                            )
                            curve_manifests.append(curve_manifest)
                        if result.get("curve_skip"):
                            result["curve_skip"]["strategy_entry"] = entry_label
                            curve_skips.append(result["curve_skip"])
                        if result.get("benchmark_capture"):
                            result["benchmark_capture"]["strategy_entry"] = entry_label
                            benchmark_captures.append(result["benchmark_capture"])
                        captured_names.add(name)
                        captured_names_by_entry[entry_label].append(name)
                    except Exception as exc:  # noqa: BLE001 - continue other products and report exact failures.
                        failures.append(
                            {
                                "strategy_entry": entry_label,
                                "strategy_name": failure_name,
                                "error": f"{type(exc).__name__}: {exc}",
                            }
                        )
                    finally:
                        try:
                            press_back(args.adb, device_id)
                            time.sleep(1.0)
                            navigate_to_strategy_list(args.adb, device_id, output_dir, entry_label)
                        except Exception as exc:  # noqa: BLE001
                            failures.append(
                                {
                                    "strategy_entry": entry_label,
                                    "strategy_name": failure_name,
                                    "error": f"return_to_list_failed: {type(exc).__name__}: {exc}",
                                }
                            )
                            navigate_to_strategy_list(args.adb, device_id, output_dir, entry_label)
                    if requested_names and all(
                        any(
                            requested == captured or requested in captured or captured in requested
                            for captured in captured_names
                        )
                        for requested in requested_names
                    ):
                        break
                if args.max_products > 0 and len(captured_names) >= args.max_products:
                    break
                if requested_names and all(
                    any(
                        requested == captured or requested in captured or captured in requested
                        for captured in captured_names
                    )
                    for requested in requested_names
                ):
                    break
                if no_new_views >= 2:
                    break
                swipe(args.adb, device_id, 540, 1950, 540, 680, 700)
                time.sleep(1.2)

    missing_strategy_entries = [
        entry_label
        for entry_label in entry_labels
        if not captured_names_by_entry.get(entry_label)
    ]
    summary = {
        "status": (
            "success"
            if captured_names and not missing_strategy_entries
            else "partial_success"
            if captured_names
            else "failed"
        ),
        "run_id": run_id,
        "device_id": device_id,
        "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "list_views": list_views,
        "strategy_entries_requested": entry_labels,
        "captured_strategy_names_by_entry": captured_names_by_entry,
        "captured_strategy_counts_by_entry": {
            entry_label: len(names)
            for entry_label, names in captured_names_by_entry.items()
        },
        "missing_strategy_entries": missing_strategy_entries,
        "captured_strategy_total": len(captured_names),
        "captured_strategy_names": sorted(captured_names),
        "requested_strategy_names": sorted(requested_names),
        "missing_requested_strategy_names": sorted(
            requested
            for requested in requested_names
            if not any(requested == captured or requested in captured or captured in requested for captured in captured_names)
        ),
        "curve_strategy_total": len(curve_manifests),
        "curve_point_total": sum(int(item.get("unique_curve_point_total") or 0) for item in curve_manifests),
        "curve_manifests": curve_manifests,
        "curve_scan_skipped_unchanged_total": len(curve_skips),
        "curve_scan_skips": curve_skips,
        "benchmark_disclosure_total": len(benchmark_captures),
        "benchmark_disclosure_success_total": sum(
            1 for item in benchmark_captures if item.get("status") == "success"
        ),
        "benchmark_disclosures": benchmark_captures,
        "performance_lineages": performance_lineages,
        "h5_route_metadata_total": len(h5_route_metadata),
        "h5_route_metadata": h5_route_metadata,
        "route_discovery_probe_total": len(route_discovery_probes),
        "route_discovery_probes": route_discovery_probes,
        "curve_read_mode": args.curve_read_mode,
        "curve_scan_scope_requested": args.curve_scan_scope,
        "curve_scan_scope_counts": {
            scope: sum(1 for item in curve_manifests if item.get("scan_scope") == scope)
            for scope in ("full", "recent")
        },
        "cached_curve_strategy_total_before_capture": len(cached_curve_state),
        "curve_ocr_verify_every": max(1, args.curve_ocr_verify_every),
        "failure_total": len(failures),
        "failures": failures,
        "output_dir": str(output_dir),
    }
    write_text_atomic(output_dir / "capture_summary.json", json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if captured_names else 2


if __name__ == "__main__":
    raise SystemExit(main())
