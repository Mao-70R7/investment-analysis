from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from probe_qieman_device import active_locks, write_json


PROBE_ROOT = Path(__file__).resolve().parent
BASE_URL = "https://qieman.com"
PUBLIC_CATALOG_PATH = "/pmdj/v2/m4"
PUBLIC_CATALOG_PATHS = (
    PUBLIC_CATALOG_PATH,
    "/pmdj/v2/m4/hand-picked",
)
PORTFOLIO_CODE_RE = re.compile(r"^(?:(?:ZH|SI)\d+|J\d+)$")
DEFAULT_SAMPLE_CODES = ("ZH012636", "ZH099893", "ZH032680", "ZH035411")
PROTECTED_ENDPOINTS = (
    "/pmdj/v1/pomodels/{code}",
    "/pmdj/v1/pomodels/{code}/nav-history",
    "/pmdj/v1/pomodels/{code}/fund-invest-type-dist",
    "/pmdj/v1/pomodels/{code}/composition-deviation",
    "/pmdj/v1/pomodels/{code}/adjustments?page=0&size=5&format=openapi&isDesc=true",
    "/pmdj/v1/pomodels/{code}/candidate-funds",
)


def now_local() -> datetime:
    return datetime.now().astimezone()


def qieman_headers(path: str) -> dict[str, str]:
    now_ms = int(time.time() * 1000)
    anonymous_id = "qieman-probe-" + hashlib.md5(str(random.random()).encode()).hexdigest()[:16]
    sign_tail = hashlib.sha256(str(int(1.01 * now_ms)).encode()).hexdigest().upper()[:32]
    request_seed = str(random.random()) + str(now_ms) + path + anonymous_id
    request_id = "albus." + hashlib.md5(request_seed.encode()).hexdigest().upper()[-20:]
    return {
        "Accept": "application/json",
        "Cache-Control": "no-store",
        "User-Agent": "Mozilla/5.0 qieman-readonly-probe/1.0",
        "x-request-id": request_id,
        "x-sign": str(now_ms) + sign_tail,
        "sensors-anonymous-id": anonymous_id,
    }


def request_endpoint(path: str, timeout: int = 20, max_bytes: int = 8 * 1024 * 1024) -> dict[str, Any]:
    request = urllib.request.Request(BASE_URL + path, headers=qieman_headers(path), method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(max_bytes)
            status = response.status
            content_type = response.headers.get("Content-Type")
    except urllib.error.HTTPError as exc:
        body = exc.read(min(max_bytes, 256 * 1024))
        status = exc.code
        content_type = exc.headers.get("Content-Type")
    except Exception as exc:
        return {
            "path": path,
            "status": None,
            "content_type": None,
            "body_bytes": 0,
            "json": None,
            "error": str(exc),
        }
    text = body.decode("utf-8", errors="replace")
    parsed: Any = None
    parse_error = None
    if text:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            parse_error = str(exc)
    return {
        "path": path,
        "status": status,
        "content_type": content_type,
        "body_bytes": len(body),
        "json": parsed,
        "error": parse_error,
    }


def iter_recommendations(value: Any, group: dict[str, Any] | None = None) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        current_group = group
        if value.get("group") and value.get("name"):
            current_group = {
                "group": value.get("group"),
                "group_name": value.get("name"),
                "group_desc": value.get("desc"),
            }
        if value.get("recCode"):
            yield {**value, "_group": current_group or {}}
        for child in value.values():
            yield from iter_recommendations(child, current_group)
    elif isinstance(value, list):
        for child in value:
            yield from iter_recommendations(child, group)


def extract_portfolio_candidates(catalog: Any) -> list[dict[str, Any]]:
    by_code: dict[str, dict[str, Any]] = {}
    for record in iter_recommendations(catalog):
        code = str(record.get("recCode") or "")
        if PORTFOLIO_CODE_RE.fullmatch(code):
            by_code.setdefault(code, record)
    return [by_code[code] for code in sorted(by_code)]


def merge_portfolio_candidates(catalogs: Iterable[tuple[str, Any]]) -> list[dict[str, Any]]:
    by_code: dict[str, dict[str, Any]] = {}
    for source_path, catalog in catalogs:
        for record in extract_portfolio_candidates(catalog):
            code = str(record["recCode"])
            existing = by_code.get(code)
            if existing is None:
                existing = dict(record)
                existing["_source_catalog_paths"] = []
                existing["_discovery_groups"] = []
                by_code[code] = existing
            if source_path not in existing["_source_catalog_paths"]:
                existing["_source_catalog_paths"].append(source_path)
            group = record.get("_group") or {}
            group_key = {
                "group": group.get("group"),
                "group_name": group.get("group_name"),
            }
            if group_key not in existing["_discovery_groups"]:
                existing["_discovery_groups"].append(group_key)
    return [by_code[code] for code in sorted(by_code)]


def card_classification(record: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    risk_level = None
    strategy_type = None
    availability_text = None
    for item in record.get("data") or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        text = str(item.get("text") or "").strip()
        for candidate in (key, text):
            if "｜" not in candidate:
                continue
            left, right = candidate.split("｜", 1)
            if "风险" in left:
                risk_level = left
                if "基金" in right:
                    strategy_type = right
                else:
                    availability_text = right or None
        if "｜" not in text and "基金" in text and not strategy_type:
            strategy_type = text
    return risk_level, strategy_type, availability_text


def normalized_master_candidate(record: dict[str, Any], captured_at: str, run_id: str) -> dict[str, Any]:
    risk_level, strategy_type, availability_text = card_classification(record)
    group = record.get("_group") or {}
    tags = [value for value in (group.get("group_name"), strategy_type) if value]
    return {
        "channel_id": "qieman",
        "source_strategy_id": record.get("recCode"),
        "strategy_name": record.get("recName"),
        "advisor_name": record.get("author"),
        "strategy_type": strategy_type,
        "risk_level": risk_level,
        "launch_date": None,
        "suggested_holding_period": None,
        "minimum_amount": None,
        "advisory_fee_rate": None,
        "benchmark": None,
        "tags": tags,
        "strategy_description": record.get("tips"),
        "status": "public_curated_recommendation",
        "source_url": record.get("url"),
        "first_seen_at": captured_at,
        "last_seen_at": captured_at,
        "run_id": run_id,
        "confidence_level": "public_curated_entry_not_complete_catalog",
        "extra": {
            "m4_group": group.get("group"),
            "m4_group_name": group.get("group_name"),
            "m4_group_description": group.get("group_desc"),
            "public_card_metrics": record.get("data") or [],
            "public_card_metrics_are_not_daily_performance": True,
            "availability_text": availability_text,
            "source_catalog_paths": record.get("_source_catalog_paths") or [PUBLIC_CATALOG_PATH],
            "discovery_groups": record.get("_discovery_groups") or [],
        },
    }


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def endpoint_entity(path: str) -> str:
    if path.endswith("/nav-history"):
        return "strategy_performance_daily"
    if path.endswith("/fund-invest-type-dist"):
        return "strategy_position_group_history"
    if path.endswith("/composition-deviation") or path.endswith("/candidate-funds"):
        return "strategy_fund_snapshot_candidate"
    if "/adjustments?" in path:
        return "strategy_rebalance_event_and_delta"
    return "strategy_master_and_current_composition"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe Qieman public catalog and protected portfolio interfaces.")
    parser.add_argument("--output-root", type=Path, default=PROBE_ROOT / "runs")
    parser.add_argument("--sample-codes", default=",".join(DEFAULT_SAMPLE_CODES))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    locks = active_locks()
    if locks:
        raise SystemExit("active production lock; probe aborted: " + ", ".join(locks))
    started = now_local()
    captured_at = started.isoformat(timespec="seconds")
    run_id = started.strftime("%Y%m%dT%H%M%S%z") + "-public-api"
    run_dir = args.output_root.resolve() / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    catalog_payloads: list[tuple[str, dict[str, Any]]] = []
    catalog_counts: dict[str, int] = {}
    for catalog_path in PUBLIC_CATALOG_PATHS:
        catalog_response = request_endpoint(catalog_path)
        catalog = catalog_response.get("json")
        if catalog_response.get("status") != 200 or not isinstance(catalog, dict):
            write_json(
                run_dir / "api_probe_summary.json",
                {"state": "public_catalog_failed", "catalog_path": catalog_path, **catalog_response},
            )
            raise SystemExit(2)
        raw_name = "m4.json" if catalog_path == PUBLIC_CATALOG_PATH else "m4_hand_picked.json"
        write_json(run_dir / "raw" / raw_name, catalog)
        catalog_payloads.append((catalog_path, catalog))
        catalog_counts[catalog_path] = len(extract_portfolio_candidates(catalog))

    candidates = merge_portfolio_candidates(catalog_payloads)
    normalized = [normalized_master_candidate(record, captured_at, run_id) for record in candidates]
    write_jsonl(run_dir / "normalized" / "strategy_master_candidates.jsonl", normalized)

    available_codes = {row["source_strategy_id"] for row in normalized}
    requested_codes = [item.strip() for item in args.sample_codes.split(",") if item.strip()]
    sample_codes = [code for code in requested_codes if code in available_codes]
    endpoint_rows: list[dict[str, Any]] = []
    nonempty_responses = 0
    tasks: list[tuple[str, str]] = []
    for code in sample_codes:
        tasks.extend(
            (code, template.format(code=urllib.parse.quote(code, safe=""))) for template in PROTECTED_ENDPOINTS
        )
    completed: list[tuple[str, str, dict[str, Any]]] = []
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(tasks)))) as executor:
        futures = {executor.submit(request_endpoint, path): (code, path) for code, path in tasks}
        for future in as_completed(futures):
            code, path = futures[future]
            completed.append((code, path, future.result()))
    for code, path, response in sorted(completed, key=lambda item: (item[0], item[1])):
        parsed = response.pop("json")
        nonempty = response.get("body_bytes", 0) > 0
        row = {
            "source_strategy_id": code,
            "entity": endpoint_entity(path),
            **response,
            "nonempty_response": nonempty,
            "access_level": "anonymous_probe",
        }
        endpoint_rows.append(row)
        if nonempty:
            nonempty_responses += 1
            write_json(run_dir / "raw" / code / (hashlib.sha256(path.encode()).hexdigest()[:16] + ".json"), parsed)

    write_json(run_dir / "endpoint_assessment.json", {"endpoints": endpoint_rows})
    coverage = {
        "channel_id": "qieman",
        "run_id": run_id,
        "captured_at": captured_at,
        "catalog_sources": list(PUBLIC_CATALOG_PATHS),
        "catalog_candidate_counts": catalog_counts,
        "public_curated_portfolio_candidates": len(normalized),
        "sample_strategy_count": len(sample_codes),
        "sample_strategy_ids": sample_codes,
        "protected_endpoint_probe_count": len(endpoint_rows),
        "protected_endpoint_nonempty_count": nonempty_responses,
        "entities": {
            "strategy_master": {
                "status": "partial_public_curated_and_handpicked_entries_only",
                "rows": len(normalized),
                "complete_catalog": False,
                "benchmark_complete": False,
                "launch_date_complete": False,
            },
            "strategy_performance_daily": {"status": "blocked_login_token_required", "rows": 0},
            "strategy_performance_interval": {
                "status": "public_card_metrics_not_formal_interval_performance",
                "rows": 0,
            },
            "strategy_fund_snapshot": {"status": "blocked_login_token_required", "rows": 0},
            "strategy_rebalance_event": {"status": "blocked_login_token_required", "rows": 0},
            "strategy_rebalance_fund_delta": {"status": "blocked_login_token_required", "rows": 0},
        },
        "direct_interface_assessment": (
            "public catalog interface works; detail/nav/position/rebalance paths are confirmed but return empty without an authenticated token"
        ),
        "quality_note": (
            "M4 and hand-picked cards are curated recommendation subsets, not the complete Qieman portfolio catalog. "
            "Card metrics without an as-of date and interval definition are not written as strategy performance."
        ),
    }
    write_json(run_dir / "coverage_assessment.json", coverage)
    summary = {
        "state": "public_api_probe_complete",
        "run_id": run_id,
        "captured_at": captured_at,
        "public_curated_portfolio_candidates": len(normalized),
        "catalog_candidate_counts": catalog_counts,
        "sample_strategy_count": len(sample_codes),
        "protected_endpoint_probe_count": len(endpoint_rows),
        "protected_endpoint_nonempty_count": nonempty_responses,
        "next_step": "connect and authorize the physical ADB device, keep Qieman foreground, then recover the in-app authenticated request context without persisting tokens",
        "run_dir": str(run_dir),
    }
    write_json(run_dir / "api_probe_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
