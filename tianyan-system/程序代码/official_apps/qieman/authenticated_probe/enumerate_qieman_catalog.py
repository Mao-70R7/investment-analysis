from __future__ import annotations

import argparse
import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any

from probe_qieman_device import (
    active_locks,
    acquire_device_lock,
    capture_ui,
    now_local,
    release_device_lock,
    run_adb,
    write_json,
)


PROBE_ROOT = Path(__file__).resolve().parent
PACKAGE_NAME = "cn.yingmi.qieman.hermione"
CATALOG_TITLE = "严选策略"
CATALOG_TAB_NAMES = {"短期稳健", "长期投资"}
STRATEGY_CODE_RE = re.compile(r"/(?:alfa/)?portfolio/((?:ZH|SI)\d+)(?:/|\?|$)")
BOUNDS_RE = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")


def parse_bounds(value: str | None) -> tuple[int, int, int, int] | None:
    match = BOUNDS_RE.fullmatch(value or "")
    if not match:
        return None
    bounds = tuple(int(item) for item in match.groups())
    if bounds[0] >= bounds[2] or bounds[1] >= bounds[3]:
        return None
    return bounds


def parse_catalog_xml(path: Path) -> dict[str, Any]:
    root = ET.parse(path).getroot()
    texts = [node.attrib.get("text", "") for node in root.iter("node") if node.attrib.get("text")]
    tabs: list[dict[str, Any]] = []
    products: list[dict[str, Any]] = []
    for node in root.iter("node"):
        resource_id = node.attrib.get("resource-id", "")
        text = node.attrib.get("text", "")
        bounds = parse_bounds(node.attrib.get("bounds"))
        if resource_id.endswith(":id/tvTab") and text in CATALOG_TAB_NAMES:
            tabs.append(
                {
                    "name": text,
                    "bounds": bounds,
                    "selected": node.attrib.get("selected") == "true",
                }
            )
        if resource_id.endswith(":id/tvName") and text and bounds:
            products.append({"name": text, "bounds": bounds})
    selected_tab = next((item["name"] for item in tabs if item["selected"]), None)
    return {
        "is_catalog": CATALOG_TITLE in texts and {item["name"] for item in tabs} == CATALOG_TAB_NAMES,
        "texts": texts,
        "tabs": tabs,
        "selected_tab": selected_tab,
        "products": products,
    }


def parse_strategy_codes(path: Path) -> list[str]:
    root = ET.parse(path).getroot()
    codes: set[str] = set()
    for node in root.iter("node"):
        resource_id = node.attrib.get("resource-id", "")
        codes.update(STRATEGY_CODE_RE.findall(resource_id))
    return sorted(codes)


def center(bounds: tuple[int, int, int, int]) -> tuple[int, int]:
    left, top, right, bottom = bounds
    return (left + right) // 2, (top + bottom) // 2


def ensure_catalog(adb: str, device_id: str, output_dir: Path, max_backs: int = 5) -> dict[str, Any]:
    for attempt in range(max_backs + 1):
        xml_path = output_dir / f"catalog_check_{attempt:02d}.xml"
        ui = capture_ui(adb, device_id, xml_path)
        if ui.get("status") == "captured_redacted":
            parsed = parse_catalog_xml(xml_path)
            if parsed["is_catalog"]:
                return {"xml_path": xml_path, **parsed}
        if attempt < max_backs:
            run_adb(adb, device_id, "shell", "input", "keyevent", "4", timeout=20)
            time.sleep(2)
    raise RuntimeError("could not return to the Qieman curated-strategy catalog")


def tap_tab(adb: str, device_id: str, catalog: dict[str, Any], tab_name: str) -> None:
    tab = next((item for item in catalog["tabs"] if item["name"] == tab_name), None)
    if not tab or not tab.get("bounds"):
        raise RuntimeError(f"catalog tab not found: {tab_name}")
    x, y = center(tab["bounds"])
    result = run_adb(adb, device_id, "shell", "input", "tap", str(x), str(y), timeout=20)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout or f"failed to tap catalog tab {tab_name}")
    time.sleep(3)


def capture_screenshot(adb: str, device_id: str, path: Path) -> str | None:
    screenshot = run_adb(adb, device_id, "exec-out", "screencap", "-p", timeout=30, binary=True)
    if screenshot.returncode != 0:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(screenshot.stdout)
    return str(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only enumeration of Qieman's curated strategy catalog.")
    parser.add_argument("--device-id", required=True)
    parser.add_argument("--adb-path", default="adb")
    parser.add_argument("--output-root", type=Path, default=PROBE_ROOT / "runs")
    parser.add_argument("--max-pages", type=int, default=20)
    parser.add_argument("--max-items", type=int, default=200)
    parser.add_argument("--detail-wait-sec", type=float, default=6.0)
    parser.add_argument("--return-wait-sec", type=float, default=2.0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if active_locks():
        raise SystemExit("active production lock; catalog enumeration aborted")
    started = now_local()
    run_id = started.strftime("%Y%m%dT%H%M%S%z") + "-catalog-enumeration"
    run_dir = args.output_root.resolve() / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    lock_path, lock_token = acquire_device_lock(run_id)
    mappings: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    page_rows: list[dict[str, Any]] = []
    stable_pages = 0
    previous_signature: tuple[tuple[str, tuple[int, int, int, int]], ...] | None = None
    try:
        catalog = ensure_catalog(args.adb_path, args.device_id, run_dir / "navigation")
        tap_tab(args.adb_path, args.device_id, catalog, "短期稳健")
        for page_index in range(args.max_pages):
            catalog = ensure_catalog(args.adb_path, args.device_id, run_dir / "navigation" / f"page_{page_index:02d}")
            products = [
                item
                for item in catalog["products"]
                if 350 <= item["bounds"][1] <= 2180 and item["name"] not in seen_names
            ]
            signature = tuple((item["name"], tuple(item["bounds"])) for item in catalog["products"])
            stable_pages = stable_pages + 1 if signature == previous_signature else 0
            previous_signature = signature
            page_row = {
                "page_index": page_index,
                "selected_tab": catalog.get("selected_tab"),
                "visible_products": [item["name"] for item in catalog["products"]],
                "new_products": [item["name"] for item in products],
                "stable_after": stable_pages,
            }
            page_rows.append(page_row)
            print(json.dumps(page_row, ensure_ascii=False), flush=True)

            for item in products:
                if len(mappings) >= args.max_items:
                    break
                name = item["name"]
                seen_names.add(name)
                x, y = center(tuple(item["bounds"]))
                tapped = run_adb(args.adb_path, args.device_id, "shell", "input", "tap", str(x), str(y), timeout=20)
                if tapped.returncode != 0:
                    mappings.append({"strategy_name": name, "status": "tap_failed", "codes": []})
                    continue
                time.sleep(max(2.0, args.detail_wait_sec))
                safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_") or f"item_{len(mappings):03d}"
                detail_dir = run_dir / "details" / f"{len(mappings):03d}_{safe_name}"
                detail_xml = detail_dir / "window.xml"
                ui = capture_ui(args.adb_path, args.device_id, detail_xml)
                codes = parse_strategy_codes(detail_xml) if ui.get("status") == "captured_redacted" else []
                screenshot_path = capture_screenshot(args.adb_path, args.device_id, detail_dir / "screen.png")
                row = {
                    "strategy_name": name,
                    "catalog_tab_at_click": catalog.get("selected_tab"),
                    "codes": codes,
                    "source_strategy_id": codes[0] if len(codes) == 1 else None,
                    "status": "mapped" if len(codes) == 1 else "ambiguous_or_missing_route",
                    "detail_ui_status": ui.get("status"),
                    "detail_text_sample": [
                        value
                        for value in ui.get("text_sample", [])
                        if not value.startswith("svg+xml;base64,") and len(value) <= 200
                    ][:80],
                    "detail_xml": str(detail_xml) if detail_xml.is_file() else None,
                    "screenshot": screenshot_path,
                }
                mappings.append(row)
                print(json.dumps({k: row[k] for k in ("strategy_name", "source_strategy_id", "status")}, ensure_ascii=False), flush=True)
                time.sleep(0.5)
                catalog = ensure_catalog(args.adb_path, args.device_id, detail_dir / "return")
                time.sleep(max(0.5, args.return_wait_sec))

            if len(mappings) >= args.max_items or stable_pages >= 2:
                break
            swipe = run_adb(
                args.adb_path,
                args.device_id,
                "shell",
                "input",
                "swipe",
                "540",
                "1900",
                "540",
                "730",
                "550",
                timeout=20,
            )
            if swipe.returncode != 0:
                raise RuntimeError(swipe.stderr or swipe.stdout or "catalog swipe failed")
            time.sleep(2)
    finally:
        release_device_lock(lock_path, lock_token)

    summary = {
        "state": "qieman_curated_catalog_enumerated",
        "run_id": run_id,
        "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "catalog_unique_name_count": len(seen_names),
        "mapped_single_code_count": sum(row["status"] == "mapped" for row in mappings),
        "end_reached": stable_pages >= 2,
        "mappings": mappings,
        "pages": page_rows,
        "safety": {
            "read_only_navigation": True,
            "allowed_actions": ["catalog tab tap", "strategy card tap", "back", "vertical scroll", "UI dump", "screenshot"],
            "trade_actions": False,
        },
    }
    write_json(run_dir / "catalog_enumeration.json", summary)
    print(json.dumps({key: value for key, value in summary.items() if key not in {"mappings", "pages"}}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
