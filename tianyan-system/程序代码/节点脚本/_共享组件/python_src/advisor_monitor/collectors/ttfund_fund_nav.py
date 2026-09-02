from __future__ import annotations

import hashlib
import html
import json
import re
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from advisor_monitor.models import RawSnapshot
from advisor_monitor.storage import write_jsonl


CHANNEL_ID = "ttfund_fund_nav"
CHANNEL_NAME = "天天基金/基金历史净值"
API_URL = "https://fundf10.eastmoney.com/F10DataApi.aspx"
USER_AGENT = "Mozilla/5.0 advisor-monitor/0.1"
ENTITY_HISTORY_DAILY = "fund_nav_history_daily"
ENTITY_HISTORY_META = "fund_nav_history_meta"
ENTITY_COLLECTION_SUMMARY = "collection_summary"

ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
CELL_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.IGNORECASE | re.DOTALL)
TAG_RE = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class RawResponse:
    text: str
    snapshot: dict[str, Any]
    raw_path: Path


def now_local() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def build_snapshot_id(channel_id: str, collector_name: str, raw_bytes: bytes, unique_hint: str) -> str:
    content_hash = hashlib.sha256(raw_bytes).hexdigest()
    hint_hash = hashlib.sha1(unique_hint.encode("utf-8")).hexdigest()
    return f"{channel_id}-{collector_name}-{content_hash[:12]}-{hint_hash[:6]}"


def sanitize_fund_code(value: Any) -> str:
    text = str(value or "").strip()
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) == 6:
        return digits
    if digits and len(digits) < 6:
        return digits.zfill(6)
    return text


def to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
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


def parse_ymd(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text
    return None


def normalize_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def strip_html(value: str) -> str:
    return html.unescape(TAG_RE.sub("", value)).replace("\xa0", " ").strip()


def parse_apidata_payload(text: str) -> dict[str, Any]:
    marker = 'content:"'
    marker_index = text.find(marker)
    if marker_index < 0:
        raise ValueError("response missing content marker")
    start_index = marker_index + len(marker)
    end_marker = '",records:'
    end_index = text.find(end_marker, start_index)
    if end_index < 0:
        raise ValueError("response missing records marker")
    content = text[start_index:end_index]
    trailer = text[end_index + len('",') :]
    match = re.search(r"records:(?P<records>\d+),pages:(?P<pages>\d+),curpage:(?P<curpage>\d+)", trailer)
    if not match:
        raise ValueError("response missing records/pages metadata")
    return {
        "content": content.replace('\\"', '"').replace("\\/", "/"),
        "records": int(match.group("records")),
        "pages": int(match.group("pages")),
        "curpage": int(match.group("curpage")),
    }


def parse_table_html(table_html: str) -> tuple[list[str], list[list[str]]]:
    thead_match = re.search(r"<thead[^>]*>(.*?)</thead>", table_html, re.IGNORECASE | re.DOTALL)
    tbody_match = re.search(r"<tbody[^>]*>(.*?)</tbody>", table_html, re.IGNORECASE | re.DOTALL)
    if not thead_match or not tbody_match:
        raise ValueError("response content missing table head/body")
    headers = [strip_html(cell) for cell in CELL_RE.findall(thead_match.group(1))]
    rows: list[list[str]] = []
    for row_html in ROW_RE.findall(tbody_match.group(1)):
        cells = [strip_html(cell) for cell in CELL_RE.findall(row_html)]
        if cells:
            rows.append(cells)
    return headers, rows


def value_type_from_headers(headers: list[str]) -> str:
    if "单位净值" in headers:
        return "nav"
    if "每万份收益" in headers:
        return "money_market"
    return "unknown"


def row_as_map(headers: list[str], cells: list[str]) -> dict[str, str | None]:
    record: dict[str, str | None] = {}
    for index, header in enumerate(headers):
        record[header] = cells[index].strip() if index < len(cells) else None
    return record


def discover_existing_funds(project_root: Path) -> dict[str, dict[str, Any]]:
    normalized_root = project_root / "data" / "normalized"
    catalog: dict[str, dict[str, Any]] = {}
    counters: dict[str, Counter[str]] = defaultdict(Counter)
    source_priority = {
        "fund_public_dim": 1,
        "strategy_fund_snapshot": 2,
        "strategy_rebalance_fund_delta": 3,
    }
    entity_specs = [
        ("fund_public_dim", "fund_name", "fund_type", "fund_company"),
        ("strategy_fund_snapshot", "fund_name", "fund_asset_type", None),
        ("strategy_rebalance_fund_delta", "fund_name", None, None),
    ]

    for entity_name, name_key, type_key, company_key in entity_specs:
        for path in normalized_root.glob(f"*/{entity_name}/*/*.jsonl"):
            channel_id = path.parts[-4]
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    fund_code = sanitize_fund_code(row.get("fund_code"))
                    if not fund_code:
                        continue
                    info = catalog.setdefault(
                        fund_code,
                        {
                            "fund_code": fund_code,
                            "fund_name": None,
                            "fund_type": None,
                            "fund_company": None,
                            "source_channels": set(),
                            "source_entities": set(),
                            "_best_priority": 99,
                        },
                    )
                    info["source_channels"].add(channel_id)
                    info["source_entities"].add(entity_name)
                    name_value = normalize_text(row.get(name_key))
                    if name_value:
                        counters[f"{fund_code}:name"][name_value] += 1
                    type_value = normalize_text(row.get(type_key)) if type_key else None
                    if type_value:
                        counters[f"{fund_code}:type"][type_value] += 1
                    company_value = normalize_text(row.get(company_key)) if company_key else None
                    if company_value:
                        counters[f"{fund_code}:company"][company_value] += 1
                    priority = source_priority.get(entity_name, 99)
                    if priority < info["_best_priority"]:
                        info["_best_priority"] = priority
                        if name_value:
                            info["fund_name"] = name_value
                        if type_value:
                            info["fund_type"] = type_value
                        if company_value:
                            info["fund_company"] = company_value

    for fund_code, info in catalog.items():
        if not info.get("fund_name") and counters.get(f"{fund_code}:name"):
            info["fund_name"] = counters[f"{fund_code}:name"].most_common(1)[0][0]
        if not info.get("fund_type") and counters.get(f"{fund_code}:type"):
            info["fund_type"] = counters[f"{fund_code}:type"].most_common(1)[0][0]
        if not info.get("fund_company") and counters.get(f"{fund_code}:company"):
            info["fund_company"] = counters[f"{fund_code}:company"].most_common(1)[0][0]
        info["source_channels"] = sorted(info["source_channels"])
        info["source_entities"] = sorted(info["source_entities"])
        info.pop("_best_priority", None)
    return catalog


class TTFundFundNavCollector:
    def __init__(
        self,
        project_root: Path,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        per_page: int = 2000,
    ) -> None:
        self.project_root = project_root
        self.start_date = start_date
        self.end_date = end_date
        self.per_page = max(1, min(per_page, 2000))
        self.catalog = discover_existing_funds(project_root)
        self.run_at = now_local()
        self.day = self.run_at.strftime("%Y-%m-%d")
        self.run_id = self.run_at.strftime("%Y%m%dT%H%M%S%z")
        self.captured_at = self.run_at.isoformat(timespec="seconds")
        self.raw_base_dir = project_root / "data" / "raw" / CHANNEL_ID / "lsjz" / self.day / self.run_id
        self.normalized_base_dir = project_root / "data" / "normalized" / CHANNEL_ID
        self.raw_snapshots: list[dict[str, Any]] = []
        self._raw_snapshot_lock = Lock()

    def collect(self, fund_codes: list[str]) -> dict[str, Any]:
        self.raw_base_dir.mkdir(parents=True, exist_ok=True)
        target_codes = [sanitize_fund_code(code) for code in fund_codes if sanitize_fund_code(code)]
        unique_codes: list[str] = []
        seen: set[str] = set()
        for code in target_codes:
            if code in seen:
                continue
            seen.add(code)
            unique_codes.append(code)

        history_rows: list[dict[str, Any]] = []
        meta_rows: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []

        for fund_code in unique_codes:
            metadata = self.catalog.get(fund_code) or {"fund_code": fund_code}
            try:
                fund_rows, meta_row = self.collect_one_fund(metadata)
                history_rows.extend(fund_rows)
                meta_rows.append(meta_row)
            except Exception as error:  # pragma: no cover - network-dependent
                failures.append(
                    {
                        "fund_code": fund_code,
                        "fund_name": metadata.get("fund_name"),
                        "error": str(error),
                    }
                )

        history_path = self.normalized_base_dir / ENTITY_HISTORY_DAILY / self.day / f"{self.run_id}.jsonl"
        meta_path = self.normalized_base_dir / ENTITY_HISTORY_META / self.day / f"{self.run_id}.jsonl"
        summary_path = self.normalized_base_dir / ENTITY_COLLECTION_SUMMARY / self.day / f"{self.run_id}.json"
        write_jsonl(history_path, history_rows)
        write_jsonl(meta_path, meta_rows)

        manifest_path = self.raw_base_dir / "_manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "channel_id": CHANNEL_ID,
                    "channel_name": CHANNEL_NAME,
                    "run_id": self.run_id,
                    "captured_at": self.captured_at,
                    "raw_snapshots": self.raw_snapshots,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )

        summary = {
            "channel_id": CHANNEL_ID,
            "channel_name": CHANNEL_NAME,
            "run_id": self.run_id,
            "captured_at": self.captured_at,
            "raw_dir": str(self.raw_base_dir.resolve()),
            "normalized_dir": str(self.normalized_base_dir.resolve()),
            "history_path": str(history_path.resolve()),
            "meta_path": str(meta_path.resolve()),
            "target_fund_total": len(unique_codes),
            "successful_fund_total": len(meta_rows),
            "failed_fund_total": len(failures),
            "history_row_total": len(history_rows),
            "money_market_fund_total": sum(1 for row in meta_rows if row.get("value_type") == "money_market"),
            "nav_fund_total": sum(1 for row in meta_rows if row.get("value_type") == "nav"),
            "failed_funds": failures,
            "targets": [
                {
                    "fund_code": code,
                    "fund_name": (self.catalog.get(code) or {}).get("fund_name"),
                    "fund_type": (self.catalog.get(code) or {}).get("fund_type"),
                }
                for code in unique_codes
            ],
        }
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        return summary

    def collect_one_fund(self, metadata: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        fund_code = metadata["fund_code"]
        first_page = self.fetch_history_page_bundle(fund_code, 1, metadata)
        headers_seen = first_page["headers"]
        value_type = value_type_from_headers(headers_seen)
        pages_total = first_page["payload"]["pages"]
        records_total = first_page["payload"]["records"]
        all_rows: list[dict[str, Any]] = list(first_page["rows"])
        snapshot_ids: list[str] = [first_page["response"].snapshot["snapshot_id"]]

        if pages_total > 1:
            max_workers = min(8, pages_total - 1)
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_map = {
                    executor.submit(self.fetch_history_page_bundle, fund_code, page_no, metadata): page_no
                    for page_no in range(2, pages_total + 1)
                }
                for future in as_completed(future_map):
                    page_data = future.result()
                    all_rows.extend(page_data["rows"])
                    snapshot_ids.append(page_data["response"].snapshot["snapshot_id"])

        all_rows.sort(key=lambda row: row.get("trade_date") or "", reverse=True)

        trade_dates = [row["trade_date"] for row in all_rows if row.get("trade_date")]
        latest_row = all_rows[0] if all_rows else {}
        meta_row = {
            "fund_code": fund_code,
            "fund_name": metadata.get("fund_name"),
            "fund_type": metadata.get("fund_type"),
            "fund_company": metadata.get("fund_company"),
            "source_channels": metadata.get("source_channels"),
            "source_entities": metadata.get("source_entities"),
            "value_type": value_type,
            "records_total": records_total,
            "pages_total": pages_total,
            "row_total": len(all_rows),
            "first_trade_date": min(trade_dates) if trade_dates else None,
            "last_trade_date": max(trade_dates) if trade_dates else None,
            "latest_nav": latest_row.get("nav"),
            "latest_accumulated_nav": latest_row.get("accumulated_nav"),
            "latest_daily_return": latest_row.get("daily_return"),
            "latest_per_10k_yield": latest_row.get("per_10k_yield"),
            "latest_seven_day_annualized": latest_row.get("seven_day_annualized"),
            "latest_purchase_status": latest_row.get("purchase_status"),
            "latest_redemption_status": latest_row.get("redemption_status"),
            "headers": headers_seen,
            "source_snapshot_ids": snapshot_ids,
            "run_id": self.run_id,
            "captured_at": self.captured_at,
        }
        return all_rows, meta_row

    def fetch_history_page_bundle(
        self,
        fund_code: str,
        page_no: int,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        response = self.fetch_history_page(fund_code, page_no)
        payload = parse_apidata_payload(response.text)
        headers, rows = parse_table_html(payload["content"])
        normalized_rows = self.normalize_history_rows(
            fund_code=fund_code,
            fund_name=metadata.get("fund_name"),
            fund_type=metadata.get("fund_type"),
            fund_company=metadata.get("fund_company"),
            headers=headers,
            rows=rows,
            source_snapshot_id=response.snapshot["snapshot_id"],
            source_api_url=response.snapshot["source_url"],
        )
        return {
            "response": response,
            "payload": payload,
            "headers": headers,
            "rows": normalized_rows,
        }

    def fetch_history_page(self, fund_code: str, page_no: int) -> RawResponse:
        params = {
            "type": "lsjz",
            "code": fund_code,
            "page": page_no,
            "per": self.per_page,
        }
        if self.start_date:
            params["sdate"] = self.start_date
        if self.end_date:
            params["edate"] = self.end_date
        url = f"{API_URL}?{urlencode(params)}"
        request = Request(url, headers={"User-Agent": USER_AGENT, "Referer": f"https://fundf10.eastmoney.com/jjjz_{fund_code}.html"})
        with urlopen(request, timeout=30) as response:
            raw_bytes = response.read()
            raw_text = raw_bytes.decode("utf-8", errors="replace")
            snapshot_id = build_snapshot_id(CHANNEL_ID, "fund_history_lsjz", raw_bytes, f"{fund_code}-{page_no}")
            raw_relative_path = Path("funds") / fund_code / f"page_{page_no:04d}.js"
            raw_path = self.raw_base_dir / raw_relative_path
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_bytes(raw_bytes)
            snapshot = RawSnapshot(
                snapshot_id=snapshot_id,
                channel_id=CHANNEL_ID,
                collector_name="fund_history_lsjz",
                access_level="public",
                captured_at=self.captured_at,
                source_url=url,
                http_status=getattr(response, "status", None),
                raw_path=str(raw_path.resolve()),
                content_type=response.headers.get_content_type(),
                content_hash=hashlib.sha256(raw_bytes).hexdigest(),
                parse_status="parsed",
            ).to_dict()
            with self._raw_snapshot_lock:
                self.raw_snapshots.append(snapshot)
            return RawResponse(text=raw_text, snapshot=snapshot, raw_path=raw_path)

    def normalize_history_rows(
        self,
        *,
        fund_code: str,
        fund_name: str | None,
        fund_type: str | None,
        fund_company: str | None,
        headers: list[str],
        rows: list[list[str]],
        source_snapshot_id: str,
        source_api_url: str,
    ) -> list[dict[str, Any]]:
        value_type = value_type_from_headers(headers)
        normalized_rows: list[dict[str, Any]] = []
        for raw_cells in rows:
            record = row_as_map(headers, raw_cells)
            trade_date = parse_ymd(record.get("净值日期"))
            per_10k_yield = to_float(record.get("每万份收益"))
            normalized_rows.append(
                {
                    "fund_code": fund_code,
                    "fund_name": fund_name,
                    "fund_type": fund_type,
                    "fund_company": fund_company,
                    "trade_date": trade_date,
                    "nav": to_float(record.get("单位净值")),
                    "accumulated_nav": to_float(record.get("累计净值")),
                    "daily_return": self.resolve_daily_return(value_type, record, per_10k_yield),
                    "per_10k_yield": per_10k_yield,
                    "seven_day_annualized": to_float(record.get("7日年化收益率（%）")),
                    "purchase_status": normalize_text(record.get("申购状态")),
                    "redemption_status": normalize_text(record.get("赎回状态")),
                    "dividend_info": normalize_text(record.get("分红送配")),
                    "value_type": value_type,
                    "is_money_market": value_type == "money_market",
                    "source_api_url": source_api_url,
                    "source_snapshot_id": source_snapshot_id,
                    "run_id": self.run_id,
                    "captured_at": self.captured_at,
                }
            )
        return normalized_rows

    @staticmethod
    def resolve_daily_return(value_type: str, record: dict[str, Any], per_10k_yield: float | None) -> float | None:
        if value_type == "nav":
            return to_float(record.get("日增长率"))
        if value_type == "money_market" and per_10k_yield is not None:
            return round(per_10k_yield / 100, 8)
        return None


def collect_ttfund_fund_nav(
    project_root: Path,
    *,
    fund_codes: list[str],
    start_date: str | None = None,
    end_date: str | None = None,
    per_page: int = 2000,
) -> dict[str, Any]:
    collector = TTFundFundNavCollector(
        project_root,
        start_date=start_date,
        end_date=end_date,
        per_page=per_page,
    )
    return collector.collect(fund_codes)


def discover_existing_fund_codes(project_root: Path) -> list[str]:
    catalog = discover_existing_funds(project_root)
    return sorted(catalog)
