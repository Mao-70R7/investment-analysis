from __future__ import annotations

import hashlib
import json
import math
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from http.client import IncompleteRead
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from advisor_monitor.models import RawSnapshot
from advisor_monitor.storage import write_jsonl


CHANNEL_ID = "zocaifu"
CHANNEL_NAME = "中欧财富/中欧钱滚滚"
API_BASE = "https://mobile.qiangungun.com"
USER_AGENT = "Mozilla/5.0 advisor-monitor/0.1"


@dataclass(frozen=True)
class RawResponse:
    json_data: dict[str, Any] | None
    text: str
    snapshot: dict[str, Any]
    raw_path: Path


def now_local() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def safe_name(value: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+', "_", value).strip(" .")
    return (cleaned or "unnamed")[:60].rstrip(" .")


def parse_yyyymmdd(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text == "--":
        return None
    if re.fullmatch(r"\d{8}", text):
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text
    if re.fullmatch(r"\d{14}", text):
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return text


def to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and math.isnan(value):
            return None
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text or text == "--":
        return None
    if text.endswith("%"):
        text = text[:-1]
    try:
        return float(text)
    except ValueError:
        return None


def split_tags(*values: Any) -> list[str]:
    tags: list[str] = []
    for value in values:
        if value is None:
            continue
        for item in re.split(r"[,，、\r\n]+", str(value)):
            item = item.strip()
            if item and item != "--" and item not in tags:
                tags.append(item)
    return tags


def extract_advisory_fee_rate(text: Any) -> str | None:
    if not text:
        return None
    match = re.search(r"投顾服务费率\s*([0-9.]+%\s*/\s*年)", str(text))
    if match:
        return match.group(1).replace(" ", "")
    match = re.search(r"([0-9.]+%\s*/\s*年)", str(text))
    return match.group(1).replace(" ", "") if match else None


def normalize_fund_name(value: Any) -> str:
    text = str(value or "")
    return re.sub(r"\s+", "", text).replace("（", "(").replace("）", ")")


def classify_action(before_weight: float | None, after_weight: float | None) -> str:
    before = before_weight if before_weight is not None else 0.0
    after = after_weight if after_weight is not None else 0.0
    if before == 0 and after > 0:
        return "buy"
    if before > 0 and after == 0:
        return "sell"
    if after > before:
        return "increase"
    if after < before:
        return "decrease"
    if after == before:
        return "keep"
    return "unknown"


class ZocaifuPublicCollector:
    def __init__(
        self,
        project_root: Path,
        *,
        page_size: int = 1000,
        max_workers: int = 8,
        limit: int | None = None,
        collect_fund_nav: bool = True,
    ) -> None:
        self.project_root = project_root
        self.page_size = page_size
        self.max_workers = max_workers
        self.limit = limit
        self.collect_fund_nav = collect_fund_nav
        self.run_at = now_local()
        self.day = self.run_at.strftime("%Y-%m-%d")
        self.run_id = self.run_at.strftime("%Y%m%dT%H%M%S%z")
        self.captured_at = self.run_at.isoformat(timespec="seconds")
        self.raw_base_dir = (
            project_root / "data" / "raw" / CHANNEL_ID / "public_api" / self.day / self.run_id
        )
        self.normalized_base_dir = (
            project_root / "data" / "normalized" / CHANNEL_ID
        )
        self.raw_snapshots: list[dict[str, Any]] = []
        self._snapshot_lock = threading.Lock()

    def post_json(
        self,
        endpoint: str,
        body: dict[str, Any],
        *,
        collector_name: str,
        raw_relative_path: Path,
        parse_status: str = "success",
    ) -> RawResponse:
        url = f"{API_BASE}{endpoint}"
        payload = compact_json(body).encode("utf-8")
        status: int | None = None
        content_type: str | None = None
        raw_bytes = b""
        json_data: dict[str, Any] | None = None
        final_parse_status = parse_status

        for attempt in range(1, 4):
            attempt_parse_status = parse_status
            request = Request(
                url,
                data=payload,
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": USER_AGENT,
                    "Accept-Encoding": "identity",
                    "Connection": "close",
                },
            )
            try:
                with urlopen(request, timeout=30) as response:
                    status = response.status
                    content_type = response.headers.get("Content-Type")
                    try:
                        raw_bytes = response.read()
                    except IncompleteRead as error:
                        raw_bytes = error.partial
                        attempt_parse_status = "partial"
                    except (TimeoutError, OSError) as error:
                        raw_bytes = json.dumps(
                            {"transport_error": str(error), "endpoint": endpoint, "body": body},
                            ensure_ascii=False,
                        ).encode("utf-8")
                        attempt_parse_status = "failed"
            except HTTPError as error:
                raw_bytes = error.read()
                status = error.code
                content_type = error.headers.get("Content-Type") if error.headers else None
                attempt_parse_status = "failed"
            except URLError as error:
                raw_bytes = json.dumps(
                    {"transport_error": str(error.reason), "endpoint": endpoint, "body": body},
                    ensure_ascii=False,
                ).encode("utf-8")
                attempt_parse_status = "failed"

            text = raw_bytes.decode("utf-8", errors="replace")
            try:
                decoded = json.loads(text)
                json_data = decoded if isinstance(decoded, dict) else {"data": decoded}
                final_parse_status = attempt_parse_status
                if attempt_parse_status != "failed" or attempt == 3:
                    break
            except json.JSONDecodeError:
                json_data = None
                final_parse_status = "failed"
                if attempt == 3:
                    break

        raw_path = self.raw_base_dir / raw_relative_path
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_bytes(raw_bytes)
        text = raw_bytes.decode("utf-8", errors="replace")
        content_hash = hashlib.sha256(raw_bytes).hexdigest()
        snapshot = RawSnapshot(
            snapshot_id=f"{CHANNEL_ID}-{collector_name}-{content_hash[:16]}",
            channel_id=CHANNEL_ID,
            collector_name=collector_name,
            access_level="public",
            captured_at=self.captured_at,
            source_url=url,
            http_status=status,
            raw_path=str(raw_path),
            content_type=content_type,
            content_hash=content_hash,
            parse_status=final_parse_status,
        ).to_dict()

        with self._snapshot_lock:
            self.raw_snapshots.append(snapshot)
        return RawResponse(json_data=json_data, text=text, snapshot=snapshot, raw_path=raw_path)

    def collect(self) -> dict[str, Any]:
        self.raw_base_dir.mkdir(parents=True, exist_ok=True)
        index_response = self.post_json(
            "/v1/fof/queryAdPageStrategyInfo",
            {"appRecommend": True, "investTypeCodeList": ["00", "04"]},
            collector_name="strategy_index",
            raw_relative_path=Path("index") / "queryAdPageStrategyInfo.json",
        )
        strategies = self.extract_strategies(index_response.json_data or {})
        if self.limit and self.limit > 0:
            strategies = strategies[: self.limit]

        products: dict[str, dict[str, Any]] = {}
        rebalance_by_strategy: dict[str, dict[str, Any]] = {}
        daily_by_strategy: dict[str, list[dict[str, Any]]] = {}
        nav_by_strategy: dict[str, dict[str, Any]] = {}

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = [
                executor.submit(self.collect_strategy_payloads, strategy)
                for strategy in strategies
            ]
            for index, future in enumerate(as_completed(futures), start=1):
                sid, product_payload, rebalance_payload, nav_payload, daily_rows = future.result()
                products[sid] = product_payload
                rebalance_by_strategy[sid] = rebalance_payload
                nav_by_strategy[sid] = nav_payload
                daily_by_strategy[sid] = daily_rows
                strategy_name = next(
                    (item["strategy_name"] for item in strategies if item["source_strategy_id"] == sid),
                    sid,
                )
                print(
                    f"{index}/{len(strategies)} {sid} {strategy_name} daily={len(daily_rows)}",
                    flush=True,
                )

        fund_nav_by_product_id = (
            self.collect_fund_navs(products) if self.collect_fund_nav else {}
        )
        normalized = self.normalize(
            strategies,
            products,
            rebalance_by_strategy,
            daily_by_strategy,
            nav_by_strategy,
            fund_nav_by_product_id,
        )
        self.write_normalized(normalized)
        summary = self.build_summary(strategies, products, rebalance_by_strategy, daily_by_strategy, fund_nav_by_product_id)
        self.write_run_manifest(summary)
        return summary

    def collect_strategy_payloads(
        self,
        strategy: dict[str, Any],
    ) -> tuple[str, dict[str, Any], dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
        source_strategy_id = strategy["source_strategy_id"]
        strategy_name = strategy["strategy_name"]
        product_dir = Path("products") / f"{source_strategy_id}_{safe_name(strategy_name)}"

        detail_response = self.post_json(
            "/v2/product/detail",
            {"productId": source_strategy_id},
            collector_name="product_detail",
            raw_relative_path=product_dir / "productDetailV2.json",
        )

        rebalance_response = self.post_json(
            "/v1/product/queryFofRebalanceInfo",
            {"fofId": source_strategy_id},
            collector_name="rebalance",
            raw_relative_path=product_dir / "queryFofRebalanceInfo.json",
        )

        nav_response = self.post_json(
            "/v1/fof/queryFofNav",
            {
                "productId": source_strategy_id,
                "startDate": "",
                "endDate": "",
                "fofRateInterval": "total",
                "hasTradeFlag": True,
            },
            collector_name="daily_nav",
            raw_relative_path=product_dir / "queryFofNav_total.json",
        )

        daily_rows = self.collect_daily_pages(source_strategy_id, product_dir)
        return (
            source_strategy_id,
            detail_response.json_data or {},
            rebalance_response.json_data or {},
            nav_response.json_data or {},
            daily_rows,
        )

    def collect_daily_pages(self, source_strategy_id: str, product_dir: Path) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        page_no = 1
        total_item: int | None = None
        while True:
            response = self.post_json(
                "/v1/fof/listDailyRiseAndFall",
                {"fofId": source_strategy_id, "pageNo": page_no, "pageSize": self.page_size},
                collector_name="daily_performance",
                raw_relative_path=product_dir / "daily" / f"page_{page_no:04d}.json",
            )
            data = (response.json_data or {}).get("data") or {}
            page_rows = data.get("list") or []
            if isinstance(data.get("totalItem"), int):
                total_item = data.get("totalItem")
            rows.extend(page_rows)
            if not page_rows:
                break
            if total_item is not None and len(rows) >= total_item:
                break
            if len(page_rows) < self.page_size:
                break
            page_no += 1
        return rows

    def collect_fund_navs(self, products: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        product_map: dict[str, dict[str, Any]] = {}
        for product_payload in products.values():
            detail = product_payload.get("data") or {}
            for item in detail.get("subProductList") or []:
                product_id = str(item.get("productId") or "").strip()
                if not product_id:
                    continue
                product_map[product_id] = {
                    "internal_product_id": product_id,
                    "fund_code": item.get("fundId"),
                    "fund_name": item.get("productName"),
                }

        results: dict[str, dict[str, Any]] = {}
        if not product_map:
            return results

        def fetch(item: dict[str, Any]) -> tuple[str, dict[str, Any]]:
            product_id = item["internal_product_id"]
            response = self.post_json(
                "/v1/product/nav/page",
                {
                    "productId": product_id,
                    "pageNo": 1,
                    "pageSize": 1,
                    "startDate": None,
                    "endDate": None,
                },
                collector_name="underlying_fund_nav",
                raw_relative_path=Path("fund_nav") / f"{product_id}.json",
            )
            return product_id, {
                "request_meta": item,
                "response": response.json_data or {},
                "snapshot_id": response.snapshot["snapshot_id"],
            }

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = [executor.submit(fetch, item) for item in product_map.values()]
            for future in as_completed(futures):
                try:
                    product_id, payload = future.result()
                except Exception as exc:  # Individual fund NAV endpoints can timeout; keep the strategy batch usable.
                    print(f"fund_nav_fetch_failed: {exc}")
                    continue
                results[product_id] = payload
        return results

    def extract_strategies(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        strategies: list[dict[str, Any]] = []
        seen: set[str] = set()

        def walk(value: Any) -> None:
            if isinstance(value, list):
                for item in value:
                    walk(item)
                return
            if not isinstance(value, dict):
                return
            if value.get("fofId") and value.get("fofName"):
                source_strategy_id = str(value["fofId"])
                if source_strategy_id not in seen:
                    seen.add(source_strategy_id)
                    strategies.append(
                        {
                            "channel_id": CHANNEL_ID,
                            "source_strategy_id": source_strategy_id,
                            "strategy_name": value.get("fofName"),
                            "strategy_type": (value.get("fofStrategy2Vo") or {}).get("fofStrategyTypeDesc"),
                            "risk_level": value.get("riskDesc"),
                            "minimum_amount": to_float(value.get("minPurAmount")),
                            "suggested_holding_period": value.get("holdingYear"),
                            "benchmark": value.get("standardDesc"),
                            "tags": split_tags(
                                (value.get("fofStrategy2Vo") or {}).get("fofStrategyTypeDesc"),
                                value.get("choiceDesc"),
                                value.get("recommendTag"),
                            ),
                            "index_raw": value,
                        }
                    )
            for child in value.values():
                walk(child)

        walk(payload.get("data"))
        return strategies

    def normalize(
        self,
        strategies: list[dict[str, Any]],
        products: dict[str, dict[str, Any]],
        rebalance_by_strategy: dict[str, dict[str, Any]],
        daily_by_strategy: dict[str, list[dict[str, Any]]],
        nav_by_strategy: dict[str, dict[str, Any]],
        fund_nav_by_product_id: dict[str, dict[str, Any]],
    ) -> dict[str, list[dict[str, Any]]]:
        fund_name_map = self.build_fund_name_map(products)

        strategy_master: list[dict[str, Any]] = []
        performance_daily: list[dict[str, Any]] = []
        fund_snapshot: list[dict[str, Any]] = []
        rebalance_events: list[dict[str, Any]] = []
        rebalance_deltas: list[dict[str, Any]] = []
        fund_public_dim: dict[str, dict[str, Any]] = {}

        for strategy in strategies:
            sid = strategy["source_strategy_id"]
            product_payload = products.get(sid) or {}
            detail = product_payload.get("data") or {}
            index_raw = strategy.get("index_raw") or {}
            latest_position_date = parse_yyyymmdd(
                detail.get("leastDisclosureDay")
                or (detail.get("fundRebalanceInfo") or {}).get("transferDate")
                or detail.get("navDate")
                or index_raw.get("navDate")
            )
            detail_snapshot_id = self.find_snapshot_id_for_file(
                Path("productDetailV2.json"),
                sid,
            )

            strategy_master.append(
                {
                    "channel_id": CHANNEL_ID,
                    "source_strategy_id": sid,
                    "strategy_name": detail.get("fofName") or strategy.get("strategy_name"),
                    "advisor_name": CHANNEL_NAME,
                    "strategy_type": (detail.get("fofStrategy2Vo") or {}).get("fofStrategyTypeDesc")
                    or strategy.get("strategy_type"),
                    "risk_level": detail.get("riskDesc") or strategy.get("risk_level"),
                    "launch_date": parse_yyyymmdd(detail.get("fofPublish")),
                    "suggested_holding_period": detail.get("holdingYear") or strategy.get("suggested_holding_period"),
                    "minimum_amount": to_float(detail.get("minPurAmount") or strategy.get("minimum_amount")),
                    "advisory_fee_rate": extract_advisory_fee_rate(detail.get("adFeeDesc")),
                    "benchmark": detail.get("standardDesc") or strategy.get("benchmark"),
                    "tags": split_tags(
                        (detail.get("fofStrategy2Vo") or {}).get("fofStrategyTypeDesc"),
                        detail.get("choiceDesc"),
                        detail.get("recommendTag"),
                        detail.get("riskDesc"),
                    ),
                    "strategy_description": detail.get("strategyConcept")
                    or detail.get("strategyPosition")
                    or detail.get("recommendDesc"),
                    "status": detail.get("fofTargetStrategyStatusDesc") or None,
                    "source_url": f"{API_BASE}/v2/product/detail",
                    "first_seen_at": self.captured_at,
                    "last_seen_at": self.captured_at,
                    "run_id": self.run_id,
                    "source_snapshot_id": detail_snapshot_id,
                    "extra": {
                        "fof_strategy_type_code": detail.get("fofStrategyTypeCode"),
                        "fof_sub_strategy_type_code": detail.get("fofSubStrategyTypeCode"),
                        "deadline_desc": detail.get("deadlineDesc"),
                        "suggest_holding_day": detail.get("suggestHoldingDay"),
                        "nav_date": parse_yyyymmdd(detail.get("navDate")),
                        "least_disclosure_day": latest_position_date,
                        "latest_transfer_date": parse_yyyymmdd(
                            (detail.get("fundRebalanceInfo") or {}).get("transferDate")
                        ),
                        "advisory_fee_description": detail.get("adFeeDesc"),
                        "standard_desc": detail.get("standardDesc"),
                    },
                }
            )

            nav_map = self.build_nav_map(nav_by_strategy.get(sid) or {})
            for daily in daily_by_strategy.get(sid) or []:
                trade_date = parse_yyyymmdd(daily.get("date") or daily.get("key"))
                nav_row = nav_map.get(trade_date or "")
                performance_daily.append(
                    {
                        "channel_id": CHANNEL_ID,
                        "source_strategy_id": sid,
                        "trade_date": trade_date,
                        "nav": to_float((nav_row or {}).get("nav")),
                        # `value` in this endpoint is not stable enough to treat as daily return.
                        "daily_return": to_float(daily.get("dailyRate")),
                        "cumulative_return": to_float(daily.get("totalRate")),
                        "benchmark_return": None,
                        "index_return": None,
                        "max_drawdown": None,
                        "source_snapshot_id": self.find_snapshot_id_for_file(Path("daily"), sid),
                        "nav_source_snapshot_id": self.find_snapshot_id_for_file(Path("queryFofNav_total.json"), sid),
                        "run_id": self.run_id,
                        "raw": {
                            "daily_key": daily.get("key"),
                            "nav_yield": (nav_row or {}).get("yield"),
                            "standard_nav": (nav_row or {}).get("standardNav"),
                            "index_yield": (nav_row or {}).get("indexYield"),
                        },
                    }
                )

            snapshot_id = f"{CHANNEL_ID}-{sid}-holding-{latest_position_date or self.day}-{self.run_id}"
            for item in detail.get("subProductList") or []:
                internal_product_id = str(item.get("productId") or "")
                fund_nav = fund_nav_by_product_id.get(internal_product_id) or {}
                fund_nav_row = self.first_nav_row(fund_nav.get("response") or {})
                fund_code = item.get("fundId")
                if fund_code:
                    fund_public_dim[fund_code] = {
                        "fund_code": fund_code,
                        "fund_name": item.get("productName"),
                        "fund_company": None,
                        "fund_type": item.get("detailTypeDesc"),
                        "tracking_index": None,
                        "theme_tags": json.dumps(split_tags(item.get("pcsIndustryTheme")), ensure_ascii=False),
                        "latest_nav": to_float(fund_nav_row.get("nav") if fund_nav_row else None),
                        "latest_nav_date": parse_yyyymmdd(fund_nav_row.get("navDate") if fund_nav_row else None),
                        "status": None,
                        "source": "zocaifu_public_api",
                        "updated_at": self.captured_at,
                        "run_id": self.run_id,
                        "internal_product_id": internal_product_id or None,
                    }

                fund_snapshot.append(
                    {
                        "snapshot_id": snapshot_id,
                        "channel_id": CHANNEL_ID,
                        "source_strategy_id": sid,
                        "position_date": latest_position_date,
                        "disclosure_date": latest_position_date,
                        "fund_code": fund_code,
                        "fund_name": item.get("productName"),
                        "fund_asset_type": item.get("assetTypeDesc"),
                        "fund_group_name": item.get("detailTypeDesc"),
                        "fund_weight": to_float(item.get("ratio")),
                        "fund_nav": to_float(fund_nav_row.get("nav") if fund_nav_row else None),
                        "fund_nav_date": parse_yyyymmdd(fund_nav_row.get("navDate") if fund_nav_row else None),
                        "is_precise_weight": item.get("ratio") not in (None, "", "--"),
                        "is_login_required": False,
                        "source_url": f"{API_BASE}/v2/product/detail",
                        "raw_record_hash": hashlib.sha256(compact_json(item).encode("utf-8")).hexdigest(),
                        "confidence_level": "official_exact",
                        "access_level": "public",
                        "run_id": self.run_id,
                        "internal_product_id": internal_product_id or None,
                        "latest_fund_daily_rate": to_float(fund_nav_row.get("dailyRate") if fund_nav_row else None),
                    }
                )

            events = (rebalance_by_strategy.get(sid) or {}).get("data", {}).get("fofRebalanceList") or []
            sorted_events = sorted(events, key=lambda event: str(event.get("rebalanceDate") or ""))
            previous_date: str | None = None
            for event in sorted_events:
                rebalance_date = parse_yyyymmdd(event.get("rebalanceDate"))
                event_hash = hashlib.sha256(compact_json(event).encode("utf-8")).hexdigest()[:12]
                event_id = f"{CHANNEL_ID}-{sid}-{rebalance_date or 'unknown'}-{event_hash}"
                rebalance_events.append(
                    {
                        "rebalance_event_id": event_id,
                        "channel_id": CHANNEL_ID,
                        "source_strategy_id": sid,
                        "rebalance_date": rebalance_date,
                        "previous_position_date": previous_date,
                        "new_position_date": rebalance_date,
                        "disclosure_date": rebalance_date,
                        "event_title": f"{detail.get('fofName') or strategy.get('strategy_name')} 调仓",
                        "event_reason": event.get("rebalanceDesc"),
                        "source_url": f"{API_BASE}/v1/product/queryFofRebalanceInfo",
                        "source_snapshot_id": self.find_snapshot_id_for_file(Path("queryFofRebalanceInfo.json"), sid),
                        "confidence_level": "official_partial",
                        "run_id": self.run_id,
                        "previous_position_date_is_inferred": previous_date is not None,
                    }
                )
                for fund in event.get("fundRebalanceList") or []:
                    before_weight = to_float(fund.get("ratio"))
                    after_weight = to_float(fund.get("targetRatio"))
                    resolved = self.resolve_fund_code(fund.get("fundName"), fund_name_map)
                    rebalance_deltas.append(
                        {
                            "rebalance_event_id": event_id,
                            "fund_code": resolved.get("fund_code"),
                            "fund_name": resolved.get("fund_name") or fund.get("fundName"),
                            "before_weight": before_weight,
                            "after_weight": after_weight,
                            "weight_delta": None
                            if before_weight is None or after_weight is None
                            else after_weight - before_weight,
                            "action_type": classify_action(before_weight, after_weight),
                            "run_id": self.run_id,
                            "fund_name_raw": fund.get("fundName"),
                            "fund_code_resolve_status": resolved.get("status"),
                        }
                    )
                previous_date = rebalance_date

        return {
            "strategy_master": strategy_master,
            "strategy_performance_daily": performance_daily,
            "strategy_fund_snapshot": fund_snapshot,
            "strategy_rebalance_event": rebalance_events,
            "strategy_rebalance_fund_delta": rebalance_deltas,
            "fund_public_dim": list(fund_public_dim.values()),
        }

    def build_nav_map(self, nav_payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
        rows = ((nav_payload.get("data") or {}).get("list")) or []
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            trade_date = parse_yyyymmdd(row.get("navDate"))
            if trade_date:
                result[trade_date] = row
        return result

    def first_nav_row(self, nav_payload: dict[str, Any]) -> dict[str, Any] | None:
        data = nav_payload.get("data")
        if isinstance(data, list) and data:
            return data[0]
        return None

    def build_fund_name_map(self, products: dict[str, dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        result: dict[str, list[dict[str, Any]]] = {}
        for product_payload in products.values():
            detail = product_payload.get("data") or {}
            for item in detail.get("subProductList") or []:
                key = normalize_fund_name(item.get("productName"))
                if not key:
                    continue
                result.setdefault(key, []).append(
                    {
                        "fund_code": item.get("fundId"),
                        "fund_name": item.get("productName"),
                        "internal_product_id": item.get("productId"),
                    }
                )
        return result

    def resolve_fund_code(self, fund_name: Any, fund_name_map: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
        matches = fund_name_map.get(normalize_fund_name(fund_name)) or []
        unique_codes = {match.get("fund_code") for match in matches if match.get("fund_code")}
        if len(unique_codes) == 1:
            first = matches[0]
            return {"status": "exact", "fund_code": first.get("fund_code"), "fund_name": first.get("fund_name")}
        if len(unique_codes) > 1:
            return {"status": "ambiguous", "fund_code": None, "fund_name": fund_name}
        return {"status": "missing", "fund_code": None, "fund_name": fund_name}

    def find_snapshot_id_for_file(self, filename: Path, source_strategy_id: str) -> str | None:
        needle = str(filename)
        for snapshot in reversed(self.raw_snapshots):
            raw_path = snapshot.get("raw_path") or ""
            if source_strategy_id in raw_path and needle in raw_path:
                return snapshot.get("snapshot_id")
        return None

    def write_normalized(self, normalized: dict[str, list[dict[str, Any]]]) -> None:
        for entity, rows in normalized.items():
            output_path = (
                self.normalized_base_dir
                / entity
                / self.day
                / f"{self.run_id}.jsonl"
            )
            write_jsonl(output_path, rows)

    def write_run_manifest(self, summary: dict[str, Any]) -> None:
        manifest = {
            "summary": summary,
            "raw_snapshots": self.raw_snapshots,
        }
        (self.raw_base_dir / "_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        summary_dir = self.normalized_base_dir / "collection_summary" / self.day
        summary_dir.mkdir(parents=True, exist_ok=True)
        (summary_dir / f"{self.run_id}.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def build_summary(
        self,
        strategies: list[dict[str, Any]],
        products: dict[str, dict[str, Any]],
        rebalance_by_strategy: dict[str, dict[str, Any]],
        daily_by_strategy: dict[str, list[dict[str, Any]]],
        fund_nav_by_product_id: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        product_details = [(payload.get("data") or {}) for payload in products.values()]
        sub_counts = [len(detail.get("subProductList") or []) for detail in product_details]
        rebalance_events = [
            len(((payload.get("data") or {}).get("fofRebalanceList")) or [])
            for payload in rebalance_by_strategy.values()
        ]
        daily_counts = [len(rows) for rows in daily_by_strategy.values()]
        fund_nav_rows = [self.first_nav_row(payload.get("response") or {}) for payload in fund_nav_by_product_id.values()]
        return {
            "channel_id": CHANNEL_ID,
            "channel_name": CHANNEL_NAME,
            "run_id": self.run_id,
            "captured_at": self.captured_at,
            "raw_dir": str(self.raw_base_dir),
            "normalized_dir": str(self.normalized_base_dir),
            "strategy_total": len(strategies),
            "product_detail_ok": sum(1 for payload in products.values() if payload.get("code") == "000000"),
            "current_holdings_non_empty": sum(1 for count in sub_counts if count > 0),
            "current_holding_rows": sum(sub_counts),
            "rebalance_ok": sum(1 for payload in rebalance_by_strategy.values() if payload.get("code") == "000000"),
            "rebalance_events_non_empty": sum(1 for count in rebalance_events if count > 0),
            "rebalance_event_total": sum(rebalance_events),
            "daily_non_empty": sum(1 for count in daily_counts if count > 0),
            "daily_rows_total": sum(daily_counts),
            "daily_rows_min": min(daily_counts) if daily_counts else 0,
            "daily_rows_max": max(daily_counts) if daily_counts else 0,
            "fund_nav_product_total": len(fund_nav_by_product_id),
            "fund_nav_with_latest_nav": sum(1 for row in fund_nav_rows if row and row.get("navDate") and row.get("nav")),
            "raw_snapshot_total": len(self.raw_snapshots),
        }


def collect_zocaifu_public(
    project_root: Path,
    *,
    page_size: int = 1000,
    max_workers: int = 8,
    limit: int | None = None,
    collect_fund_nav: bool = True,
) -> dict[str, Any]:
    collector = ZocaifuPublicCollector(
        project_root,
        page_size=page_size,
        max_workers=max_workers,
        limit=limit,
        collect_fund_nav=collect_fund_nav,
    )
    return collector.collect()
