from __future__ import annotations

import argparse
import html
import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

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
DEFAULT_QUERIES = ("组", "基金", "米", "新", "启", "搬砖", "足")
BOUNDS_RE = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")
RISK_HOLDING_RE = re.compile(r"本策略为(?P<risk>[^，,\n]+)[，,]适合持有(?P<holding>[^\n]+)")
MAX_DRAWDOWN_RE = re.compile(r"历史最大回撤\s*(?P<value>-?\d+(?:\.\d+)?)%")
ANNUAL_RETURN_RE = re.compile(r"年化收益\s*(?P<value>-?\d+(?:\.\d+)?)%")


def parse_bounds(value: str | None) -> tuple[int, int, int, int] | None:
    match = BOUNDS_RE.fullmatch(value or "")
    if not match:
        return None
    bounds = tuple(int(item) for item in match.groups())
    if bounds[0] >= bounds[2] or bounds[1] >= bounds[3]:
        return None
    return bounds


def center(bounds: tuple[int, int, int, int]) -> tuple[int, int]:
    left, top, right, bottom = bounds
    return (left + right) // 2, (top + bottom) // 2


def clean_text(value: str | None) -> str:
    return html.unescape(value or "").replace("\r", "").strip()


def parse_search_result_xml(path: Path) -> dict[str, Any]:
    root = ET.parse(path).getroot()
    texts = [clean_text(node.attrib.get("text")) for node in root.iter("node")]
    texts = [text for text in texts if text]
    rows: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for node in root.iter("node"):
        text = clean_text(node.attrib.get("text"))
        resource_id = node.attrib.get("resource-id", "")
        if resource_id.endswith(":id/tvName") and text:
            if current:
                rows.append(current)
            current = {
                "strategy_name": text,
                "advisor_name": None,
                "run_duration_text": None,
                "risk_level": None,
                "suggested_holding_period": None,
                "historical_max_drawdown": None,
                "historical_annualized_return": None,
                "visible_summary_text": None,
            }
            continue
        if current is None or not text:
            continue
        if resource_id.endswith(":id/tvTag") and not current["advisor_name"]:
            current["advisor_name"] = text.removesuffix("V") or None
        elif text.startswith("已运行") and not current["run_duration_text"]:
            current["run_duration_text"] = text
        elif "本策略为" in text:
            current["visible_summary_text"] = text
            risk_match = RISK_HOLDING_RE.search(text)
            if risk_match:
                current["risk_level"] = risk_match.group("risk")
                current["suggested_holding_period"] = risk_match.group("holding")
            drawdown_match = MAX_DRAWDOWN_RE.search(text)
            if drawdown_match:
                current["historical_max_drawdown"] = float(drawdown_match.group("value")) / 100
            return_match = ANNUAL_RETURN_RE.search(text)
            if return_match:
                current["historical_annualized_return"] = float(return_match.group("value")) / 100
    if current:
        rows.append(current)
    return {
        "is_more_strategy_page": "更多策略" in texts,
        "texts": texts,
        "rows": rows,
        "visible_signature": tuple(row["strategy_name"] for row in rows),
    }


def root_bottom(path: Path) -> int:
    root = ET.parse(path).getroot()
    first = next(root.iter("node"), None)
    bounds = parse_bounds(first.attrib.get("bounds")) if first is not None else None
    return bounds[3] if bounds else 0


def find_node_bounds(path: Path, *, text: str | None = None, resource_suffix: str | None = None) -> tuple[int, int, int, int] | None:
    root = ET.parse(path).getroot()
    for node in root.iter("node"):
        if text is not None and clean_text(node.attrib.get("text")) != text:
            continue
        if resource_suffix is not None and not node.attrib.get("resource-id", "").endswith(resource_suffix):
            continue
        bounds = parse_bounds(node.attrib.get("bounds"))
        if bounds:
            return bounds
    return None


def find_strategy_more_bounds(path: Path) -> tuple[int, int, int, int] | None:
    root = ET.parse(path).getroot()
    strategy_y: int | None = None
    candidates: list[tuple[int, tuple[int, int, int, int]]] = []
    for node in root.iter("node"):
        text = clean_text(node.attrib.get("text"))
        bounds = parse_bounds(node.attrib.get("bounds"))
        resource_id = node.attrib.get("resource-id", "")
        if text == "策略" and resource_id.endswith(":id/tvTitle") and bounds:
            strategy_y = bounds[1]
        if text == "更多" and resource_id.endswith(":id/tvMore") and bounds:
            candidates.append((bounds[1], bounds))
    if not candidates:
        return None
    if strategy_y is None:
        return candidates[0][1]
    return min(candidates, key=lambda item: abs(item[0] - strategy_y))[1]


def tap_bounds(adb: str, device_id: str, bounds: tuple[int, int, int, int]) -> None:
    x, y = center(bounds)
    result = run_adb(adb, device_id, "shell", "input", "tap", str(x), str(y), timeout=20)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout or f"tap failed at {x},{y}")


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def merge_candidate(
    by_name: dict[str, dict[str, Any]],
    row: dict[str, Any],
    *,
    query: str,
    page_index: int,
    captured_at: str,
    run_id: str,
) -> None:
    name = str(row["strategy_name"])
    current = by_name.get(name)
    if current is None:
        current = {
            "channel_id": "qieman",
            "source_strategy_id": None,
            "strategy_name": name,
            "advisor_name": row.get("advisor_name"),
            "strategy_type": None,
            "risk_level": row.get("risk_level"),
            "launch_date": None,
            "suggested_holding_period": row.get("suggested_holding_period"),
            "minimum_amount": None,
            "advisory_fee_rate": None,
            "benchmark": None,
            "tags": [],
            "strategy_description": None,
            "status": "authenticated_search_candidate",
            "source_url": None,
            "first_seen_at": captured_at,
            "last_seen_at": captured_at,
            "run_id": run_id,
            "confidence_level": "authenticated_search_candidate_without_strategy_id",
            "extra": {
                "search_queries": [],
                "search_pages": [],
                "run_duration_text": row.get("run_duration_text"),
                "visible_summary_text": row.get("visible_summary_text"),
                "historical_max_drawdown": row.get("historical_max_drawdown"),
                "historical_annualized_return": row.get("historical_annualized_return"),
                "metrics_are_search_snapshot_not_daily_performance": True,
                "launch_date_not_inferred_from_run_duration": True,
            },
        }
        by_name[name] = current
    current["last_seen_at"] = captured_at
    if not current.get("advisor_name") and row.get("advisor_name"):
        current["advisor_name"] = row["advisor_name"]
    if not current.get("risk_level") and row.get("risk_level"):
        current["risk_level"] = row["risk_level"]
    if not current.get("suggested_holding_period") and row.get("suggested_holding_period"):
        current["suggested_holding_period"] = row["suggested_holding_period"]
    extra = current["extra"]
    if query not in extra["search_queries"]:
        extra["search_queries"].append(query)
    page_key = f"{query}:{page_index}"
    if page_key not in extra["search_pages"]:
        extra["search_pages"].append(page_key)


def ensure_search_screen(adb: str, device_id: str, output_dir: Path, max_backs: int = 3) -> Path:
    for attempt in range(max_backs + 1):
        xml_path = output_dir / f"search_check_{attempt:02d}.xml"
        ui = capture_ui(adb, device_id, xml_path)
        if ui.get("status") == "captured_redacted" and find_node_bounds(xml_path, resource_suffix=":id/etSearch"):
            return xml_path
        if attempt < max_backs:
            run_adb(adb, device_id, "shell", "input", "keyevent", "4", timeout=20)
            time.sleep(2)
    raise RuntimeError("could not return to Qieman global search")


def prepare_history(adb: str, device_id: str, run_dir: Path, query: str) -> Path:
    screen = ensure_search_screen(adb, device_id, run_dir / "navigation")
    clear_bounds = find_node_bounds(screen, resource_suffix=":id/ivClear")
    if clear_bounds:
        tap_bounds(adb, device_id, clear_bounds)
        time.sleep(1)
    history_path = run_dir / "history.xml"
    capture_ui(adb, device_id, history_path)
    if root_bottom(history_path) < 2000:
        run_adb(adb, device_id, "shell", "input", "keyevent", "4", timeout=20)
        time.sleep(2)
        capture_ui(adb, device_id, history_path)
    if not find_node_bounds(history_path, text=query):
        raise RuntimeError(f"search history query unavailable: {query}")
    return history_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Enumerate Qieman advisor candidates through authenticated search history.")
    parser.add_argument("--device-id", required=True)
    parser.add_argument("--adb-path", default="adb")
    parser.add_argument("--queries", default=",".join(DEFAULT_QUERIES))
    parser.add_argument("--output-root", type=Path, default=PROBE_ROOT / "runs")
    parser.add_argument("--max-pages", type=int, default=40)
    parser.add_argument("--query-wait-sec", type=float, default=5.0)
    parser.add_argument("--page-wait-sec", type=float, default=2.0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if active_locks():
        raise SystemExit("active production lock; search enumeration aborted")
    queries = list(dict.fromkeys(item.strip() for item in args.queries.split(",") if item.strip()))
    started = now_local()
    captured_at = started.isoformat(timespec="seconds")
    run_id = started.strftime("%Y%m%dT%H%M%S%z") + "-search-catalog"
    run_dir = args.output_root.resolve() / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    lock_path, lock_token = acquire_device_lock(run_id)
    candidates: dict[str, dict[str, Any]] = {}
    query_results: list[dict[str, Any]] = []
    try:
        for query in queries:
            query_slug = re.sub(r"[^A-Za-z0-9._-]+", "_", query).strip("_")
            if not query_slug:
                unicode_key = "-".join(f"u{ord(char):04x}" for char in query)
                query_slug = f"query_{len(query_results):02d}_{unicode_key}"
            query_dir = run_dir / "queries" / query_slug
            try:
                history_path = prepare_history(args.adb_path, args.device_id, query_dir, query)
                query_bounds = find_node_bounds(history_path, text=query)
                if not query_bounds:
                    raise RuntimeError(f"query node disappeared: {query}")
                tap_bounds(args.adb_path, args.device_id, query_bounds)
                time.sleep(max(2.0, args.query_wait_sec))
                result_path = query_dir / "result.xml"
                capture_ui(args.adb_path, args.device_id, result_path)
                more_bounds = find_strategy_more_bounds(result_path)
                if not more_bounds:
                    raise RuntimeError(f"strategy more entry unavailable for query: {query}")
                tap_bounds(args.adb_path, args.device_id, more_bounds)
                time.sleep(max(2.0, args.query_wait_sec))

                seen_for_query: set[str] = set()
                page_rows: list[dict[str, Any]] = []
                stable_pages = 0
                previous_signature: tuple[str, ...] | None = None
                for page_index in range(args.max_pages):
                    page_path = query_dir / "pages" / f"page_{page_index:03d}.xml"
                    ui = capture_ui(args.adb_path, args.device_id, page_path)
                    if ui.get("status") != "captured_redacted":
                        raise RuntimeError(f"UI capture failed for query {query}, page {page_index}")
                    parsed = parse_search_result_xml(page_path)
                    if not parsed["is_more_strategy_page"]:
                        raise RuntimeError(f"unexpected page while enumerating query: {query}")
                    signature = parsed["visible_signature"]
                    stable_pages = stable_pages + 1 if signature == previous_signature else 0
                    previous_signature = signature
                    new_names: list[str] = []
                    for row in parsed["rows"]:
                        name = str(row["strategy_name"])
                        if name not in seen_for_query:
                            new_names.append(name)
                            seen_for_query.add(name)
                        merge_candidate(
                            candidates,
                            row,
                            query=query,
                            page_index=page_index,
                            captured_at=captured_at,
                            run_id=run_id,
                        )
                    page_row = {
                        "page_index": page_index,
                        "visible_names": list(signature),
                        "new_names": new_names,
                        "stable_after": stable_pages,
                    }
                    page_rows.append(page_row)
                    print(json.dumps({"query": query, **page_row}, ensure_ascii=False), flush=True)
                    if stable_pages >= 2:
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
                        raise RuntimeError(swipe.stderr or swipe.stdout or "search result swipe failed")
                    time.sleep(max(1.0, args.page_wait_sec))
                query_results.append(
                    {
                        "query": query,
                        "status": "complete" if stable_pages >= 2 else "max_pages_reached",
                        "unique_strategy_name_count": len(seen_for_query),
                        "end_reached": stable_pages >= 2,
                        "pages": page_rows,
                    }
                )
                run_adb(args.adb_path, args.device_id, "shell", "input", "keyevent", "4", timeout=20)
                time.sleep(2)
            except Exception as exc:
                query_results.append(
                    {
                        "query": query,
                        "status": "failed",
                        "unique_strategy_name_count": 0,
                        "end_reached": False,
                        "error": str(exc),
                        "pages": [],
                    }
                )
                print(json.dumps(query_results[-1], ensure_ascii=False), flush=True)
    finally:
        release_device_lock(lock_path, lock_token)

    candidate_rows = [candidates[name] for name in sorted(candidates)]
    write_jsonl(run_dir / "normalized" / "strategy_master_candidates.jsonl", candidate_rows)
    summary = {
        "state": "qieman_authenticated_search_catalog_enumerated",
        "run_id": run_id,
        "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "queries": queries,
        "query_results": query_results,
        "unique_strategy_name_count": len(candidate_rows),
        "source_strategy_id_count": 0,
        "complete_catalog": False,
        "quality_note": (
            "登录态搜索并集用于发现策略候选；搜索结果没有策略代码，不能单独形成主库业务键，"
            "卡片年化收益和最大回撤也不是日度业绩序列。"
        ),
        "next_step": "用官方 StarGate API Key 调用 StrategySearch/GetStrategyDetails，补齐代码并验证搜索总数。",
        "run_dir": str(run_dir),
    }
    write_json(run_dir / "search_catalog.json", summary)
    print(json.dumps({key: value for key, value in summary.items() if key != "query_results"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
