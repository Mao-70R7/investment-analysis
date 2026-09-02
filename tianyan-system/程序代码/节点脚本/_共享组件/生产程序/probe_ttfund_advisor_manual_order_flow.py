from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_ADB = PROJECT_ROOT / "tools" / "platform-tools" / "adb.exe"
APP_ID = "funda91a99886abf7e"
APP_PACKAGE = "com.eastmoney.android.fund"
PDF_CACHE_DIR = "/sdcard/Android/data/com.eastmoney.android.fund/cache/fund_attachments"
REMOTE_XML = "/sdcard/ttfund_advisor_manual_uidump.xml"


def now_stamp() -> str:
    return datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z")


def adb_run(adb: Path, device: str, *args: str, timeout: int = 30, binary: bool = False) -> subprocess.CompletedProcess[Any]:
    return subprocess.run(
        [str(adb), "-s", device, *args],
        capture_output=True,
        text=not binary,
        encoding=None if binary else "utf-8",
        errors=None if binary else "replace",
        timeout=timeout,
        check=False,
    )


def adb_shell(adb: Path, device: str, command: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return adb_run(adb, device, "shell", command, timeout=timeout)


def build_strategy_url(strategy_id: str) -> str:
    link_to = f"fund://mp.1234567.com.cn/weex/{APP_ID}/pages/strategyDetail/index?id={strategy_id}&showKycPopup=1"
    wrapper = {
        "LinkTo": link_to,
        "LinkType": 2,
        "AdId": "0",
        "IsVerifyLogin": False,
        "CloseWeex": False,
    }
    encoded = urllib.parse.quote(json.dumps(wrapper, ensure_ascii=False, separators=(",", ":")))
    return f"eastmoneyjijin://startapp/toPage?type=8&linkto={encoded}"


def parse_bounds(value: str) -> tuple[int, int, int, int] | None:
    match = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", value or "")
    if not match:
        return None
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def center(bounds: tuple[int, int, int, int]) -> tuple[int, int]:
    left, top, right, bottom = bounds
    return ((left + right) // 2, (top + bottom) // 2)


def flatten_nodes(xml_path: Path) -> list[dict[str, Any]]:
    root = ET.fromstring(xml_path.read_text(encoding="utf-8", errors="replace"))
    nodes: list[dict[str, Any]] = []
    for elem in root.iter("node"):
        text = elem.attrib.get("text") or elem.attrib.get("content-desc") or ""
        bounds_text = elem.attrib.get("bounds") or ""
        bounds = parse_bounds(bounds_text)
        row: dict[str, Any] = {
            "text": text,
            "bounds": bounds_text,
            "class": elem.attrib.get("class"),
            "clickable": elem.attrib.get("clickable"),
        }
        if bounds:
            row["center"] = center(bounds)
        nodes.append(row)
    return nodes


def screen_size(adb: Path, device: str) -> tuple[int, int]:
    result = adb_shell(adb, device, "wm size", timeout=10)
    match = re.search(r"(\d+)x(\d+)", result.stdout)
    if not match:
        return (1080, 2400)
    return int(match.group(1)), int(match.group(2))


def snapshot(adb: Path, device: str, out_dir: Path, label: str) -> list[dict[str, Any]]:
    dump = adb_shell(adb, device, f"uiautomator dump {REMOTE_XML}", timeout=30)
    (out_dir / f"{label}.dump.txt").write_text(dump.stdout + dump.stderr, encoding="utf-8")
    adb_run(adb, device, "pull", REMOTE_XML, str(out_dir / f"{label}.xml"), timeout=30)
    shot = adb_run(adb, device, "exec-out", "screencap", "-p", timeout=30, binary=True)
    if shot.stdout:
        (out_dir / f"{label}.png").write_bytes(shot.stdout)
    nodes = flatten_nodes(out_dir / f"{label}.xml")
    (out_dir / f"{label}.nodes.json").write_text(json.dumps(nodes, ensure_ascii=False, indent=2), encoding="utf-8")
    texts = [str(node.get("text") or "") for node in nodes if node.get("text")]
    (out_dir / f"{label}.texts.txt").write_text("\n".join(texts), encoding="utf-8")
    return nodes


def tap(adb: Path, device: str, x: int, y: int, wait: float = 1.0) -> None:
    adb_shell(adb, device, f"input tap {x} {y}", timeout=15)
    time.sleep(wait)


def back(adb: Path, device: str, wait: float = 1.0) -> None:
    adb_shell(adb, device, "input keyevent 4", timeout=15)
    time.sleep(wait)


def node_texts(nodes: list[dict[str, Any]]) -> list[str]:
    return [str(node.get("text") or "") for node in nodes if node.get("text")]


def find_nodes(nodes: list[dict[str, Any]], terms: tuple[str, ...]) -> list[dict[str, Any]]:
    found = []
    for node in nodes:
        text = str(node.get("text") or "")
        if any(term in text for term in terms):
            found.append(node)
            continue
        is_protocol_lookup = any(term in ui_terms("已仔细阅读并同意") for term in terms) if "ui_terms" in globals() else False
        if is_protocol_lookup and (
            contains_ui_text(text, "点击确认即表示已仔细阅读") or contains_ui_text(text, "策略说明书")
        ):
            found.append(node)
    return found


def garbled_ui_text(value: str) -> str:
    try:
        return value.encode("utf-8").decode("gbk", errors="replace")
    except UnicodeError:
        return value


def ui_terms(*values: str) -> tuple[str, ...]:
    terms: list[str] = []
    for value in values:
        for term in (value, garbled_ui_text(value)):
            if term and term not in terms:
                terms.append(term)
    return tuple(terms)


def contains_ui_text(texts: str, value: str) -> bool:
    return any(term in texts for term in ui_terms(value))


def find_ui_nodes(nodes: list[dict[str, Any]], *values: str) -> list[dict[str, Any]]:
    return find_nodes(nodes, ui_terms(*values))


def is_order_page(nodes: list[dict[str, Any]]) -> bool:
    texts = "\n".join(node_texts(nodes))
    return "转入投顾账户" in texts and "转入金额" in texts and "已仔细阅读并同意" in texts


def is_order_page(nodes: list[dict[str, Any]]) -> bool:
    texts = "\n".join(node_texts(nodes))
    has_order_frame = any(
        contains_ui_text(texts, term)
        for term in (
            "转入投顾账户",
            "转入金额",
            "每份投资金额",
            "建立底仓",
        )
    )
    has_action_or_protocol = any(
        contains_ui_text(texts, term)
        for term in (
            "已仔细阅读并同意",
            "点击确认即表示已仔细阅读",
            "确定转入",
            "开启跟车",
            "待支付",
        )
    )
    return has_order_frame and has_action_or_protocol


def looks_like_order_shell(nodes: list[dict[str, Any]]) -> bool:
    texts = "\n".join(node_texts(nodes))
    return any(
        contains_ui_text(texts, term)
        for term in (
            "转入投顾账户",
            "转入金额",
            "每份投资金额",
            "确定转入",
            "开启跟车",
        )
    )


def is_numeric_keyboard_open(nodes: list[dict[str, Any]]) -> bool:
    texts = set(node_texts(nodes))
    digit_count = sum(1 for key in ("1", "2", "3", "4", "5", "6", "7", "8", "9", "0") if key in texts)
    return digit_count >= 8 and ("完成" in texts or "00" in texts)


def synthesize_protocol_node(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    texts = [str(node.get("text") or "") for node in nodes if node.get("text")]
    compact_text = "".join(texts)
    has_protocol_text = (
        ("策略说明书" in compact_text or "服务协议" in compact_text)
        and ("已仔细阅读" in compact_text or "点击确认" in compact_text)
    )
    if not has_protocol_text:
        return []
    protocol_chars = set("已仔细阅读并同意点击确认即表示策略说明书风险揭示服务协议业务规则基金投资组合目标盈系列中欧财富《》（）")
    bounds_rows: list[tuple[int, int, int, int]] = []
    for node in nodes:
        text = str(node.get("text") or "").strip()
        bounds = parse_bounds(str(node.get("bounds") or ""))
        if not text or not bounds:
            continue
        left, top, right, bottom = bounds
        if top < 1500:
            continue
        if len(text) == 1 and text in protocol_chars:
            bounds_rows.append(bounds)
        elif any(term in text for term in ("已仔细阅读", "点击确认", "策略说明书", "服务协议", "风险揭示书", "业务规则")):
            bounds_rows.append(bounds)
    if not bounds_rows:
        return []
    left = min(row[0] for row in bounds_rows)
    top = min(row[1] for row in bounds_rows)
    right = max(row[2] for row in bounds_rows)
    bottom = max(row[3] for row in bounds_rows)
    return [
        {
            "text": compact_text,
            "bounds": f"[{left},{top}][{right},{bottom}]",
            "class": "synthetic_protocol",
            "clickable": "false",
            "center": center((left, top, right, bottom)),
        }
    ]


def find_protocol_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    direct_nodes = find_ui_nodes(nodes, "已仔细阅读并同意", "点击确认即表示已仔细阅读", "策略说明书")
    return direct_nodes or synthesize_protocol_node(nodes)


def is_login_page(nodes: list[dict[str, Any]]) -> bool:
    texts = node_texts(nodes)
    joined = "\n".join(texts)
    masked_phone = re.search(r"\d{3}\*{4}\d{4}", joined) is not None
    text_set = set(texts)
    letter_key_count = sum(1 for key in "qwertyuiopasdfghjklzxcvbnm" if key in text_set)
    digit_key_count = sum(1 for key in "1234567890" if key in text_set)
    has_password_length_hint = "8-20" in joined
    return (masked_phone and (has_password_length_hint or letter_key_count >= 10)) or (
        letter_key_count >= 20 and digit_key_count >= 8
    )


def is_pdf_viewer(nodes: list[dict[str, Any]]) -> bool:
    texts = "\n".join(node_texts(nodes))
    if is_risk_disclosure_modal(nodes) or is_order_page(nodes):
        return False
    return "附件" in texts or "基金投资组合策略说明书" in texts or "策略说明书" in texts or "pdfread2" in texts


def is_risk_disclosure_modal(nodes: list[dict[str, Any]]) -> bool:
    texts = "\n".join(node_texts(nodes))
    return "风险揭示书" in texts and (
        "我已阅读并同意风险揭示书" in texts
        or "截屏方式留存" in texts
        or "尊敬的投资者" in texts
    )


def dismiss_risk_disclosure_modal(
    adb: Path,
    device: str,
    strategy_dir: Path,
    nodes: list[dict[str, Any]],
    result: dict[str, Any],
    label_prefix: str,
) -> list[dict[str, Any]]:
    risk_nodes = find_nodes(nodes, ("我已阅读并同意风险揭示书",))
    if not risk_nodes or not risk_nodes[0].get("center"):
        return nodes
    rx, ry = risk_nodes[0]["center"]
    tap(adb, device, rx, ry, wait=3)
    next_nodes = snapshot(adb, device, strategy_dir, f"{label_prefix}_after_risk_agree")
    result["events"].append({"step": f"{label_prefix}_risk_agree", "coord": [rx, ry], "texts_head": node_texts(next_nodes)[:60]})
    return next_nodes


def list_pdf_cache(adb: Path, device: str) -> dict[str, dict[str, Any]]:
    command = (
        f"find {PDF_CACHE_DIR} -type f -name '*.pdf' "
        "-exec stat -c '%Y|%s|%n' {} \\; 2>/dev/null"
    )
    result = adb_shell(adb, device, command, timeout=30)
    files: dict[str, dict[str, Any]] = {}
    for line in result.stdout.splitlines():
        parts = line.strip().split("|", 2)
        if len(parts) != 3:
            continue
        try:
            mtime = int(parts[0])
            size = int(parts[1])
        except ValueError:
            continue
        files[parts[2]] = {"mtime": mtime, "size": size, "path": parts[2]}
    return files


def new_or_changed_pdfs(before: dict[str, dict[str, Any]], after: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    changed = []
    for path, meta in after.items():
        if path not in before or before[path].get("mtime") != meta.get("mtime") or before[path].get("size") != meta.get("size"):
            changed.append(meta)
    return sorted(changed, key=lambda row: (row.get("mtime", 0), row.get("size", 0)), reverse=True)


def pull_pdf(adb: Path, device: str, remote: str, out_dir: Path, strategy_id: str) -> Path | None:
    safe_name = Path(remote).name or f"{strategy_id}_strategy_manual.pdf"
    local = out_dir / f"{strategy_id}_{safe_name}"
    result = adb_run(adb, device, "pull", remote, str(local), timeout=60)
    (out_dir / f"pull_{safe_name}.txt").write_text(result.stdout + result.stderr, encoding="utf-8")
    if local.exists() and local.stat().st_size > 0:
        return local
    return None


def guess_pdf_urls(remote: str) -> list[str]:
    name = Path(remote).name
    guesses = []
    if re.match(r"pdf_\d+\.pdf$", name):
        guesses.append(f"https://img.1234567.com.cn/pdf/{name}")
    return guesses


def extract_pdf_text(pdf_path: Path, max_pages: int = 8) -> tuple[str, str]:
    try:
        import pdfplumber  # type: ignore[import-not-found]

        parts = []
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages[:max_pages]:
                parts.append(page.extract_text() or "")
        return "\n".join(parts), "pdfplumber"
    except Exception as first_error:
        try:
            from pypdf import PdfReader  # type: ignore[import-not-found]

            reader = PdfReader(str(pdf_path))
            parts = []
            for page in reader.pages[:max_pages]:
                parts.append(page.extract_text() or "")
            return "\n".join(parts), "pypdf"
        except Exception as second_error:
            return "", f"extract_failed: {first_error!r}; {second_error!r}"


def classify_pdf(local: Path) -> dict[str, Any]:
    text, extractor = extract_pdf_text(local)
    text_path = local.with_suffix(".txt")
    if text:
        text_path.write_text(text, encoding="utf-8", errors="replace")
    head = re.sub(r"\s+", " ", text[:800]).strip()
    head_window = text[:2000]
    manual_title = "\u57fa\u91d1\u6295\u8d44\u7ec4\u5408\u7b56\u7565\u8bf4\u660e\u4e66"
    manual_short = "\u7b56\u7565\u8bf4\u660e\u4e66"
    this_manual = "\u672c\u8bf4\u660e\u4e66"
    service_agreement = "\u57fa\u91d1\u6295\u8d44\u987e\u95ee\u670d\u52a1\u534f\u8bae"
    risk_disclosure = "\u98ce\u9669\u63ed\u793a\u4e66"
    business_rule = "\u4e1a\u52a1\u89c4\u5219"
    is_strategy_manual = manual_title in head_window or (manual_short in head_window and this_manual in head_window)
    return {
        "extractor": extractor,
        "text_path": str(text_path) if text else None,
        "text_chars": len(text),
        "text_head": head,
        "is_strategy_manual": is_strategy_manual,
        "is_service_agreement": service_agreement in head_window,
        "is_risk_disclosure": risk_disclosure in head_window and not is_strategy_manual,
        "is_business_rule": business_rule in head_window and not is_strategy_manual,
    }


def pull_and_classify_pdfs(
    adb: Path,
    device: str,
    changed: list[dict[str, Any]],
    strategy_dir: Path,
    strategy_id: str,
) -> list[dict[str, Any]]:
    pulled = []
    for meta in changed[:4]:
        remote = str(meta["path"])
        local = pull_pdf(adb, device, remote, strategy_dir, strategy_id)
        if not local:
            continue
        pulled.append(
            {
                "remote": remote,
                "local": str(local),
                "size": local.stat().st_size,
                "direct_url_guesses": guess_pdf_urls(remote),
                "classification": classify_pdf(local),
            }
        )
    return pulled


def logcat_hits(text: str) -> list[str]:
    patterns = [
        r"https?://[^\s\"']*(?:lookpdf|pdfread2|pdf_|fund_attachments)[^\s\"']*",
        r"pdfread2/api\?f=\d+",
        r"lookpdf\.html\?code=[A-Za-z0-9+/=]+[^\s\"']*",
        r"pdf_\d+\.pdf",
    ]
    hits: list[str] = []
    for pattern in patterns:
        for match in re.findall(pattern, text):
            if match not in hits:
                hits.append(match)
    return hits


def collect_runtime_context(
    adb: Path,
    device: str,
    result: dict[str, Any],
    before_pdfs: dict[str, dict[str, Any]],
    strategy_dir: Path,
) -> None:
    after_pdfs = list_pdf_cache(adb, device)
    result["pdf_cache_after_count"] = len(after_pdfs)
    result["pdf_cache_new_or_changed"] = new_or_changed_pdfs(before_pdfs, after_pdfs)
    logcat = adb_shell(adb, device, "logcat -d", timeout=30).stdout
    (strategy_dir / "logcat.txt").write_text(logcat, encoding="utf-8", errors="replace")
    result["logcat_hits"] = logcat_hits(logcat)


def scroll_to_protocol(
    adb: Path,
    device: str,
    strategy_dir: Path,
    nodes: list[dict[str, Any]],
    result: dict[str, Any],
    width: int,
    height: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    protocol_nodes = find_protocol_nodes(nodes)
    if protocol_nodes:
        return nodes, protocol_nodes
    for index in range(1, 4):
        start_y = int(height * 0.76)
        end_y = int(height * 0.42)
        adb_shell(adb, device, f"input swipe {width // 2} {start_y} {width // 2} {end_y} 700", timeout=15)
        time.sleep(2)
        nodes = snapshot(adb, device, strategy_dir, f"04_scroll_to_protocol_{index}")
        result["events"].append({"step": f"scroll_to_protocol_{index}", "texts_head": node_texts(nodes)[:50]})
        protocol_nodes = find_protocol_nodes(nodes)
        if protocol_nodes:
            return nodes, protocol_nodes
    return nodes, protocol_nodes


def protocol_tap_points(protocol_node: dict[str, Any]) -> list[tuple[int, int]]:
    bounds = parse_bounds(str(protocol_node.get("bounds") or ""))
    if not bounds:
        return [(520, 2085), (430, 2085), (650, 2085), (520, 2140)]
    left, top, right, bottom = bounds
    width = right - left
    # The first blue link starts after "已仔细阅读并同意 ". Try the first two rendered lines.
    raw_points = []
    for y_frac in (0.24, 0.45, 0.67, 0.86):
        for x_frac in (0.18, 0.30, 0.42, 0.54, 0.66, 0.78, 0.90):
            raw_points.append((left + int(width * x_frac), top + int((bottom - top) * y_frac)))
    return [(min(max(x, left + 8), right - 8), min(max(y, top + 8), bottom - 8)) for x, y in raw_points]


def probe_strategy(adb: Path, device: str, strategy_id: str, out_dir: Path) -> dict[str, Any]:
    strategy_dir = out_dir / strategy_id
    strategy_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {
        "strategy_id": strategy_id,
        "startedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "events": [],
        "success": False,
        "reason": None,
    }
    width, height = screen_size(adb, device)
    result["screen"] = {"width": width, "height": height}
    before_pdfs = list_pdf_cache(adb, device)
    result["pdf_cache_before_count"] = len(before_pdfs)
    adb_shell(adb, device, "logcat -c", timeout=10)
    adb_shell(adb, device, "input keyevent 3", timeout=10)
    time.sleep(1)

    url = build_strategy_url(strategy_id)
    launch = adb_shell(adb, device, f"am start -a android.intent.action.VIEW -d '{url}'", timeout=30)
    result["launch"] = {"stdout": launch.stdout, "stderr": launch.stderr, "returncode": launch.returncode, "url": url}
    time.sleep(8)
    nodes = snapshot(adb, device, strategy_dir, "01_after_launch")
    result["events"].append({"step": "after_launch", "texts_head": node_texts(nodes)[:40]})

    confirm_nodes = find_nodes(nodes, ("确认查看", "确认选择"))
    if confirm_nodes and confirm_nodes[0].get("center"):
        x, y = confirm_nodes[0]["center"]
        tap(adb, device, x, y, wait=3)
        nodes = snapshot(adb, device, strategy_dir, "02_after_confirm_view")
        result["events"].append({"step": "tap_confirm_view", "coord": [x, y], "texts_head": node_texts(nodes)[:40]})

    # Detail page bottom action bar sits below the fee/rule strip. The first
    # probe used y=2200~2240 and landed on the transaction-rules area instead
    # of the orange advisor transfer button on 1080x2400 devices.
    transfer_y = min(height - 128, 2272)
    transfer_points = [
        (int(width * 0.84), transfer_y),
        (int(width * 0.91), transfer_y),
        (min(width - 120, 900), transfer_y),
    ]
    order_nodes: list[dict[str, Any]] | None = None
    for index, (x, y) in enumerate(transfer_points, start=1):
        tap(adb, device, x, y, wait=7)
        nodes = snapshot(adb, device, strategy_dir, f"03_after_transfer_tap_{index}")
        result["events"].append({"step": f"transfer_tap_{index}", "coord": [x, y], "texts_head": node_texts(nodes)[:60]})
        if is_login_page(nodes):
            result["reason"] = "login_required_before_order_page"
            result["login_detected_after"] = f"transfer_tap_{index}"
            collect_runtime_context(adb, device, result, before_pdfs, strategy_dir)
            return result
        if is_risk_disclosure_modal(nodes):
            nodes = dismiss_risk_disclosure_modal(adb, device, strategy_dir, nodes, result, f"03_transfer_tap_{index}")
        if is_order_page(nodes):
            order_nodes = nodes
            break
        # If a non-order page was opened, go back before trying another transfer point.
        if "转入投顾账户" not in "\n".join(node_texts(nodes)):
            back(adb, device, wait=2)

    if not order_nodes:
        result["reason"] = "order_page_not_reached"
        collect_runtime_context(adb, device, result, before_pdfs, strategy_dir)
        return result

    if is_numeric_keyboard_open(order_nodes):
        back(adb, device, wait=2)
        order_nodes = snapshot(adb, device, strategy_dir, "03_after_hide_numeric_keyboard")
        result["events"].append({"step": "hide_numeric_keyboard", "texts_head": node_texts(order_nodes)[:50]})

    protocol_nodes = find_nodes(order_nodes, ("已仔细阅读并同意",))
    if not protocol_nodes:
        order_nodes, protocol_nodes = scroll_to_protocol(adb, device, strategy_dir, order_nodes, result, width, height)
    result["protocol_texts"] = [str(node.get("text") or "") for node in protocol_nodes]
    if not protocol_nodes:
        result["reason"] = "protocol_text_not_found"
        return result

    protocol_node = protocol_nodes[0]
    nodes = snapshot(adb, device, strategy_dir, "04_before_manual_link_scan")
    if is_risk_disclosure_modal(nodes):
        nodes = dismiss_risk_disclosure_modal(adb, device, strategy_dir, nodes, result, "04_before_manual_link_scan")
    protocol_nodes = find_nodes(nodes, ("已仔细阅读并同意",))
    if protocol_nodes:
        protocol_node = protocol_nodes[0]
    points = protocol_tap_points(protocol_node)
    result["manual_link_points"] = points
    for index, (x, y) in enumerate(points, start=1):
        tap(adb, device, x, y, wait=6)
        nodes = snapshot(adb, device, strategy_dir, f"05_after_manual_link_tap_{index}")
        result["events"].append({"step": f"manual_link_tap_{index}", "coord": [x, y], "texts_head": node_texts(nodes)[:40]})
        if is_risk_disclosure_modal(nodes):
            nodes = dismiss_risk_disclosure_modal(adb, device, strategy_dir, nodes, result, f"05_manual_link_tap_{index}")
        after_pdfs = list_pdf_cache(adb, device)
        changed = new_or_changed_pdfs(before_pdfs, after_pdfs)
        pulled_now = pull_and_classify_pdfs(adb, device, changed, strategy_dir, strategy_id) if changed else []
        if pulled_now:
            result.setdefault("pulled_pdfs", []).extend(pulled_now)
        valid_manuals = [
            row for row in pulled_now if row.get("classification", {}).get("is_strategy_manual")
        ]
        if valid_manuals:
            result["success"] = True
            result["reason"] = "strategy_manual_pdf_confirmed"
            result["pdf_cache_new_or_changed"] = changed
            break
        if pulled_now:
            result.setdefault("rejected_pdfs", []).extend(pulled_now)
            before_pdfs = after_pdfs
            if is_pdf_viewer(nodes) or not looks_like_order_shell(nodes):
                back(adb, device, wait=2)
                snapshot(adb, device, strategy_dir, f"05_back_after_rejected_pdf_{index}")
            continue
        if is_pdf_viewer(nodes) and not changed:
            result["success"] = True
            result["reason"] = "manual_link_opened_pdf_viewer_without_cache"
            result["pdf_cache_new_or_changed"] = changed
            break
        # Stay on the order page for the next scan point.
        texts = "\n".join(node_texts(nodes))
        if "转入投顾账户" not in texts and not is_pdf_viewer(nodes):
            back(adb, device, wait=2)
            snapshot(adb, device, strategy_dir, f"05_back_after_non_order_{index}")

    if not result.get("success") and not result.get("reason"):
        result["reason"] = "manual_link_scan_exhausted"

    after_pdfs = list_pdf_cache(adb, device)
    changed = result.get("pdf_cache_new_or_changed") or new_or_changed_pdfs(before_pdfs, after_pdfs)
    result["pdf_cache_after_count"] = len(after_pdfs)
    result["pdf_cache_new_or_changed"] = changed
    logcat = adb_shell(adb, device, "logcat -d", timeout=30).stdout
    (strategy_dir / "logcat.txt").write_text(logcat, encoding="utf-8", errors="replace")
    result["logcat_hits"] = logcat_hits(logcat)
    pull_candidates = changed
    pulled = result.setdefault("pulled_pdfs", [])
    known_remotes = {str(row.get("remote")) for row in pulled}
    missing_candidates = [meta for meta in pull_candidates if str(meta.get("path")) not in known_remotes]
    pulled.extend(pull_and_classify_pdfs(adb, device, missing_candidates[:2], strategy_dir, strategy_id))
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe TTFund advisor strategy manual PDFs through the order-page protocol links.")
    parser.add_argument("--adb-path", type=Path, default=DEFAULT_ADB)
    parser.add_argument("--device-id", required=True)
    parser.add_argument("--strategy-id", action="append", required=True)
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=PROJECT_ROOT / "data/raw/ttfund/order_protocol_probe/2026-06-25/advisor_manual_order_flow_probe",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    adb = args.adb_path
    if not adb.exists():
        found = shutil.which(str(adb))
        if not found:
            raise FileNotFoundError(f"adb not found: {adb}")
        adb = Path(found)
    run_dir = args.out_dir / now_stamp()
    run_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for strategy_id in args.strategy_id:
        print(f"[probe] {strategy_id}", flush=True)
        results.append(probe_strategy(adb, args.device_id, strategy_id, run_dir))
        (run_dir / "summary.json").write_text(json.dumps({"run_dir": str(run_dir), "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"run_dir": str(run_dir), "strategies": args.strategy_id}, ensure_ascii=False))


if __name__ == "__main__":
    main()
