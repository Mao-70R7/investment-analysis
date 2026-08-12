from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from probe_qieman_device import active_locks, now_local, write_json


PROBE_ROOT = Path(__file__).resolve().parent
DOCS_URL = "https://stargate.yingmi.com/api/docs.json"
DEFAULT_SERVER_URL = "https://stargate.yingmi.com/api"
API_KEY_ENV = "QIEMAN_STARGATE_API_KEY"
TARGET_OPERATIONS = (
    "SearchPortfolioStrategies",
    "StrategySearchByKeyword",
    "GetStrategyDetails",
    "BatchGetStrategiesComposition",
    "GetStrategyNavHistory",
    "GetStrategyBenchmark",
    "GetStrategyAdjustments",
)
HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


def request_json(
    method: str,
    url: str,
    *,
    body: Any = None,
    api_key: str | None = None,
    timeout: int = 30,
) -> tuple[int | None, Any, str | None]:
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "qieman-stargate-readonly-probe/1.0",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method.upper(), headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(32 * 1024 * 1024)
            status = response.status
    except urllib.error.HTTPError as exc:
        raw = exc.read(1024 * 1024)
        status = exc.code
    except Exception as exc:
        return None, None, str(exc)
    if not raw:
        return status, None, None
    try:
        return status, json.loads(raw.decode("utf-8")), None
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return status, None, str(exc)


def fetch_openapi_document() -> dict[str, Any]:
    status, payload, error = request_json("GET", DOCS_URL)
    if status != 200 or not isinstance(payload, dict):
        raise RuntimeError(f"StarGate OpenAPI document unavailable: status={status}, error={error}")
    return payload


def iter_operations(document: dict[str, Any]) -> Iterable[dict[str, Any]]:
    for path, path_item in (document.get("paths") or {}).items():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method.lower() not in HTTP_METHODS or not isinstance(operation, dict):
                continue
            yield {"method": method.upper(), "path": path, **operation}


def target_operation_inventory(document: dict[str, Any]) -> list[dict[str, Any]]:
    by_name: dict[str, dict[str, Any]] = {}
    for operation in iter_operations(document):
        name = operation.get("operationId")
        if name not in TARGET_OPERATIONS:
            continue
        # The OpenAPI catalog currently contains both POST and GET variants of
        # GetStrategyAdjustments. Prefer the OAP batch POST for the first entry.
        if name in by_name and by_name[name]["method"] == "POST":
            continue
        by_name[name] = {
            "operation_id": name,
            "method": operation["method"],
            "path": operation["path"],
            "summary": operation.get("summary"),
            "description": operation.get("description"),
            "parameters": operation.get("parameters") or [],
            "request_body": operation.get("requestBody"),
            "responses": operation.get("responses") or {},
        }
    return [by_name[name] for name in TARGET_OPERATIONS if name in by_name]


def normalize_search_product(product: dict[str, Any], captured_at: str, run_id: str) -> dict[str, Any]:
    risk_value = product.get("risk5Level")
    risk_level = f"R{risk_value}" if risk_value is not None else None
    highlights = product.get("highlights") if isinstance(product.get("highlights"), list) else []
    texts = product.get("texts") if isinstance(product.get("texts"), list) else []
    return {
        "channel_id": "qieman",
        "source_strategy_id": product.get("prodCode"),
        "strategy_name": product.get("prodName"),
        "advisor_name": product.get("poManagerName"),
        "strategy_type": product.get("productType"),
        "risk_level": risk_level,
        "launch_date": product.get("establishedOn"),
        "suggested_holding_period": product.get("holdingDurationDesc"),
        "minimum_amount": None,
        "advisory_fee_rate": None,
        "benchmark": None,
        "tags": [value for value in (product.get("m4Type"), product.get("investDistrict")) if value],
        "strategy_description": product.get("poPhilosophy") or (texts[0] if texts else None),
        "status": "stargate_catalog_discovered",
        "source_url": product.get("url"),
        "first_seen_at": captured_at,
        "last_seen_at": captured_at,
        "run_id": run_id,
        "confidence_level": "official_stargate_structured_catalog",
        "extra": {
            "annual_compounded_return": product.get("annualCompoundedReturn"),
            "max_drawdown": product.get("maxDrawdown"),
            "volatility": product.get("volatility"),
            "nav": product.get("nav"),
            "highlights": highlights,
            "texts": texts,
            "invest_range": product.get("investRange"),
            "invest_strategy": product.get("investStrategy"),
            "product_features": product.get("productFeatures"),
            "catalog_metrics_are_not_daily_performance": True,
        },
    }


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def server_url(document: dict[str, Any]) -> str:
    servers = document.get("servers")
    if isinstance(servers, list) and servers and isinstance(servers[0], dict) and servers[0].get("url"):
        return str(servers[0]["url"]).rstrip("/")
    return DEFAULT_SERVER_URL


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe the official Yingmi StarGate advisor API without persisting API keys.")
    parser.add_argument("--output-root", type=Path, default=PROBE_ROOT / "runs")
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--max-pages", type=int, default=100)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if active_locks():
        raise SystemExit("active production lock; StarGate probe aborted")
    started = now_local()
    captured_at = started.isoformat(timespec="seconds")
    run_id = started.strftime("%Y%m%dT%H%M%S%z") + "-stargate-api"
    run_dir = args.output_root.resolve() / run_id
    run_dir.mkdir(parents=True, exist_ok=False)

    document = fetch_openapi_document()
    operations = target_operation_inventory(document)
    schema_inventory = {
        "captured_at": captured_at,
        "docs_url": DOCS_URL,
        "server_url": server_url(document),
        "all_operation_count": sum(1 for _ in iter_operations(document)),
        "target_operations": operations,
        "capability_assessment": {
            "catalog": "SearchPortfolioStrategies returns total and paged products with code/name/manager/establishedOn",
            "details": "GetStrategyDetails returns structured strategy detail rows",
            "performance": "GetStrategyNavHistory returns dated strategy NAV history",
            "benchmark": "GetStrategyBenchmark returns index code/name/weight split",
            "holdings": "BatchGetStrategiesComposition returns fund code/name/percent/updatedAt",
            "rebalance": "GetStrategyAdjustments returns dates, comments, before and after fund weights",
        },
    }
    write_json(run_dir / "stargate_schema_inventory.json", schema_inventory)

    api_key = os.environ.get(API_KEY_ENV, "").strip()
    if not api_key:
        search = next((item for item in operations if item["operation_id"] == "SearchPortfolioStrategies"), None)
        status = None
        response_message = None
        if search:
            status, response, error = request_json(
                search["method"],
                server_url(document) + search["path"],
                body={"broker": "0008", "page": 1, "size": min(100, args.page_size)},
            )
            if isinstance(response, dict):
                response_message = response.get("message") or response.get("errorMessage")
            response_message = response_message or error
        summary = {
            "state": "blocked_api_key_required",
            "run_id": run_id,
            "captured_at": captured_at,
            "api_key_persisted": False,
            "unauthenticated_search_status": status,
            "unauthenticated_search_message": response_message,
            "target_operation_count": len(operations),
            "strategy_total": None,
            "complete_catalog": False,
            "next_step": (
                "Apply once through qieman-mcp-cli phone verification, then set QIEMAN_STARGATE_API_KEY "
                "for this process only and rerun."
            ),
            "run_dir": str(run_dir),
        }
        write_json(run_dir / "summary.json", summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return

    search = next((item for item in operations if item["operation_id"] == "SearchPortfolioStrategies"), None)
    if not search:
        raise RuntimeError("SearchPortfolioStrategies is missing from the current OpenAPI document")
    page_size = max(1, min(100, args.page_size))
    products_by_code: dict[str, dict[str, Any]] = {}
    reported_total: int | None = None
    page_summaries: list[dict[str, Any]] = []
    for page in range(1, args.max_pages + 1):
        status, payload, error = request_json(
            search["method"],
            server_url(document) + search["path"],
            body={"broker": "0008", "page": page, "size": page_size},
            api_key=api_key,
        )
        if status != 200 or not isinstance(payload, dict):
            raise RuntimeError(f"StarGate catalog page failed: page={page}, status={status}, error={error}")
        write_json(run_dir / "raw" / f"search_page_{page:04d}.json", payload)
        products = payload.get("products") if isinstance(payload.get("products"), list) else []
        pagination = (payload.get("metadata") or {}).get("pagination") or {}
        if pagination.get("total") is not None:
            reported_total = int(pagination["total"])
        for product in products:
            if not isinstance(product, dict) or not product.get("prodCode"):
                continue
            products_by_code[str(product["prodCode"])] = product
        page_summaries.append(
            {
                "page": page,
                "row_count": len(products),
                "unique_count_after": len(products_by_code),
                "reported_total": reported_total,
            }
        )
        if not products or (reported_total is not None and len(products_by_code) >= reported_total):
            break

    normalized = [normalize_search_product(products_by_code[code], captured_at, run_id) for code in sorted(products_by_code)]
    write_jsonl(run_dir / "normalized" / "strategy_master.jsonl", normalized)
    complete_catalog = reported_total is not None and len(normalized) >= reported_total
    summary = {
        "state": "stargate_catalog_collected" if complete_catalog else "stargate_catalog_partial",
        "run_id": run_id,
        "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "api_key_persisted": False,
        "strategy_total": len(normalized),
        "reported_total": reported_total,
        "complete_catalog": complete_catalog,
        "page_summaries": page_summaries,
        "next_step": "Use the discovered codes with details, nav-history, benchmark, composition and adjustments operations.",
        "run_dir": str(run_dir),
    }
    write_json(run_dir / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
