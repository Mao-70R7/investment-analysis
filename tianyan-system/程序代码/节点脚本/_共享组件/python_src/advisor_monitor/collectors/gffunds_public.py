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
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from advisor_monitor.models import RawSnapshot
from advisor_monitor.progress import ConsoleProgress
from advisor_monitor.storage import write_jsonl
from advisor_monitor.strategy_catalog import (
    catalog_diff,
    load_local_strategy_ids,
    reconcile_catalog_batch,
)

try:
    import pdfplumber
except ImportError:  # pragma: no cover
    pdfplumber = None


CHANNEL_ID = "gffunds"
CHANNEL_NAME = "广发基金"
API_BASE = "https://gfwx.gffunds.com.cn/mapi"
H5_BASE = "https://gfwx.gffunds.com.cn/html5app"
USER_AGENT = "Mozilla/5.0 advisor-monitor/0.1"
COMMON_PARAMS = {
    "market": "GffundsHtml5",
    "app_version": "7.7.0",
    "app_channel": "NETNO_HTML5",
    "device_info": "App",
}
SECTION_TYPE_MAP = {
    "0": "近1月",
    "1": "近3月",
    "2": "近6月",
    "3": "近1年",
    "4": "近3年",
    "5": "今年以来",
    "6": "近1周",
    "7": "成立以来",
}
FUND_TYPE_MAP = {
    "0": "货币类",
    "1": "固收类",
    "2": "权益类",
}


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
    return re.sub(r'[\\/:*?"<>|]+', "_", value)[:80]


def parse_ymd(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text == "--":
        return None
    if re.fullmatch(r"\d{8}", text):
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text
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


def parse_amount(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text or text == "--":
        return None
    match = re.search(r"(-?\d+(?:\.\d+)?)", text)
    if not match:
        return None
    amount = float(match.group(1))
    if "万" in text:
        amount *= 10000
    return amount


def split_tags(*values: Any) -> list[str]:
    tags: list[str] = []
    for value in values:
        if value is None:
            continue
        for item in re.split(r"[,，、\r\n]+", str(value)):
            text = item.strip()
            if text and text != "--" and text not in tags:
                tags.append(text)
    return tags


def md5_upper(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest().upper()


def build_sign(params: dict[str, Any]) -> dict[str, str]:
    merged = {**COMMON_PARAMS}
    for key, value in params.items():
        if value is None:
            continue
        merged[key] = value
    signed = {key: str(value) for key, value in merged.items()}
    raw = "&".join(f"{key}={signed[key]}" for key in sorted(signed)) + "&key=AP04"
    signed["sign"] = md5_upper(raw)
    return signed


def holding_period_text(value: Any, unit: Any) -> str | None:
    period = str(value or "").strip()
    unit_text = str(unit or "").strip().upper()
    if not period:
        return None
    if unit_text == "MONTH":
        return f"{period}个月"
    if unit_text == "YEAR":
        return f"{period}年"
    if unit_text == "DAY":
        return f"{period}天"
    return period


def recursive_nodes(value: Any):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from recursive_nodes(item)
    elif isinstance(value, list):
        for item in value:
            yield from recursive_nodes(item)


def extract_home_strategy_codes(payload: dict[str, Any]) -> list[str]:
    codes: list[str] = []
    for node in recursive_nodes(payload):
        prod_type = str(node.get("prod_type") or "").strip()
        prod_code = str(node.get("prod_code") or "").strip()
        if prod_type == "4" and re.fullmatch(r"GFJJ\d{6}", prod_code) and prod_code not in codes:
            codes.append(prod_code)
    return codes


def extract_line_field(text: str, label: str) -> str | None:
    pattern = rf"{re.escape(label)}\s*[：:]\s*([^\n]+)"
    match = re.search(pattern, text)
    if not match:
        return None
    value = match.group(1).strip()
    return re.sub(r"\s+", " ", value)


def extract_section(text: str, number: int, title: str, next_number: int | None) -> str | None:
    if next_number is None:
        pattern = rf"{number}\.\s*{re.escape(title)}\s*[：:]\s*(.+)$"
    else:
        pattern = rf"{number}\.\s*{re.escape(title)}\s*[：:]\s*(.*?)\s*{next_number}\."
    match = re.search(pattern, text, flags=re.S)
    if not match:
        return None
    value = re.sub(r"\s+", " ", match.group(1)).strip()
    return value or None


def clean_protocol_text(value: str) -> str:
    lines = []
    for raw_line in value.replace("\r", "").replace("\u3000", " ").splitlines():
        line = re.sub(r"[ \t]+", " ", raw_line).strip()
        if re.fullmatch(r"\d+", line):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def collapse_protocol_value(value: str | None) -> str | None:
    if not value:
        return None
    collapsed = re.sub(r"\s+", " ", value).strip()
    return collapsed or None


def normalize_protocol_title(value: str) -> str:
    return re.sub(r"\s+", "", value or "")


def extract_protocol_sections(text: str) -> dict[str, str]:
    heading_re = re.compile(
        r"(?m)^\s*(\d+)\.\s*([A-Za-z\u4e00-\u9fff][^\n：:]{0,40}?)(?:\s*[：:]\s*(.*))?$"
    )
    matches = list(heading_re.finditer(text))
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        title = normalize_protocol_title(match.group(2))
        inline_value = clean_protocol_text(match.group(3) or "")
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body_value = clean_protocol_text(text[start:end])
        if inline_value and body_value:
            value = f"{inline_value}\n{body_value}"
        else:
            value = inline_value or body_value
        cleaned = clean_protocol_text(value)
        if cleaned and title not in sections:
            sections[title] = cleaned
    return sections


def extract_section_by_titles(sections: dict[str, str], *titles: str) -> str | None:
    for title in titles:
        value = sections.get(normalize_protocol_title(title))
        if value:
            return value
    return None


def first_protocol_line(value: str | None) -> str | None:
    if not value:
        return None
    for line in value.splitlines():
        cleaned = collapse_protocol_value(line)
        if cleaned:
            return cleaned
    return None


def extract_fee_rate_value(*values: Any) -> str | None:
    for value in values:
        if value is None:
            continue
        match = re.search(r"([0-9]+(?:\.\d+)?)\s*%\s*/\s*每年", str(value))
        if match:
            return f"{match.group(1)}%/每年"
    return None


def extract_launch_date(yield_payload: dict[str, Any]) -> str | None:
    launch_date = parse_ymd(yield_payload.get("adv_setupdate"))
    if launch_date:
        return launch_date
    rows = yield_payload.get("adv_yield_trend_list") or []
    dates = [
        parsed
        for parsed in (parse_ymd(row.get("yield_date")) for row in rows)
        if parsed
    ]
    return min(dates) if dates else None


def extract_protocol_fields(text: str) -> dict[str, Any]:
    normalized = clean_protocol_text(text)
    sections = extract_protocol_sections(normalized)
    minimum_amount = (
        first_protocol_line(extract_section_by_titles(sections, "起投金额"))
        or first_protocol_line(extract_line_field(normalized, "起投金额"))
    )
    if not minimum_amount:
        transfer_match = re.search(r"转入金额为人民币\s*([0-9][0-9,]*(?:\.\d+)?)\s*元", normalized)
        if transfer_match:
            minimum_amount = f"{transfer_match.group(1)} 元"
    advisory_fee_rate = extract_fee_rate_value(
        extract_section_by_titles(sections, "投顾服务费率"),
        extract_line_field(normalized, "投顾服务费率"),
        normalized,
    )
    return {
        "protocol_strategy_name": first_protocol_line(
            extract_section_by_titles(sections, "策略名称")
        )
        or extract_line_field(normalized, "策略名称"),
        "benchmark": collapse_protocol_value(
            extract_section_by_titles(sections, "业绩基准", "业绩比较基准")
        )
        or extract_line_field(normalized, "业绩基准")
        or extract_line_field(normalized, "业绩比较基准"),
        "risk_level": first_protocol_line(extract_section_by_titles(sections, "风险等级"))
        or extract_line_field(normalized, "风险等级"),
        "suggested_holding_period": first_protocol_line(
            extract_section_by_titles(sections, "建议持有期")
        )
        or extract_line_field(normalized, "建议持有期"),
        "minimum_amount_text": minimum_amount,
        "advisory_fee_rate": advisory_fee_rate,
        "investment_scope": collapse_protocol_value(
            extract_section_by_titles(sections, "投资范围")
        )
        or extract_section(normalized, 8, "投资范围", 9),
        "strategy_idea": collapse_protocol_value(
            extract_section_by_titles(sections, "策略理念")
        )
        or extract_section(normalized, 9, "策略理念", 10),
        "strategy_description": collapse_protocol_value(
            extract_section_by_titles(sections, "策略描述")
        )
        or extract_section(normalized, 10, "策略描述", 11),
        "rebalance_frequency": collapse_protocol_value(
            extract_section_by_titles(sections, "调仓频率")
        )
        or extract_section(normalized, 7, "调仓频率", 8),
        "strategy_target": collapse_protocol_value(
            extract_section_by_titles(sections, "策略目标")
        )
        or extract_line_field(normalized, "策略目标"),
    }


class GFFundsPublicCollector:
    def __init__(
        self,
        project_root: Path,
        *,
        max_workers: int = 8,
        limit: int | None = None,
        collect_fund_nav: bool = True,
        collect_protocol_pdf: bool = True,
        extra_strategy_ids: list[str] | None = None,
        run_id: str | None = None,
        latest_adjustment_refresh_days: int = 1,
    ) -> None:
        self.project_root = project_root
        self.max_workers = max_workers
        self.limit = limit
        self.collect_fund_nav = collect_fund_nav
        self.collect_protocol_pdf = collect_protocol_pdf
        self.latest_adjustment_refresh_days = max(0, int(latest_adjustment_refresh_days))
        self.extra_strategy_ids = [
            strategy_id
            for strategy_id in (
                str(item).strip() for item in (extra_strategy_ids or [])
            )
            if re.fullmatch(r"(?:GFJJ\d{6}|ZY\d{8})", strategy_id)
        ]
        self.run_at = now_local()
        self.day = self.run_at.strftime("%Y-%m-%d")
        self.run_id = run_id or self.run_at.strftime("%Y%m%dT%H%M%S%z")
        self.captured_at = self.run_at.isoformat(timespec="seconds")
        self.raw_base_dir = (
            project_root / "data" / "raw" / CHANNEL_ID / "public_api" / self.day / self.run_id
        )
        self.normalized_base_dir = project_root / "data" / "normalized" / CHANNEL_ID
        self.raw_snapshots: list[dict[str, Any]] = []
        self._snapshot_lock = threading.Lock()
        self._protocol_meta_lock = threading.Lock()
        self._protocol_meta_by_url: dict[str, dict[str, Any]] = {}
        self._protocol_url_locks: dict[str, threading.Lock] = {}
        self._protocol_pdf_cache_index: dict[str, tuple[Path, dict[str, Any]]] = {}
        self.protocol_pdf_cache_fallback_total = 0
        self._adjustment_cache_index: dict[tuple[str, str], Path] = {}
        self.adjustment_cache_stats = {"reused": 0, "fetched": 0, "fallback": 0}

    @staticmethod
    def valid_adjustment_payload(payload: dict[str, Any] | None) -> bool:
        return bool(
            isinstance(payload, dict)
            and payload.get("RETCODE") == "0000"
            and isinstance(payload.get("advisor_comb_list"), list)
        )

    def build_adjustment_cache_index(self) -> None:
        cache_root = self.project_root / "data" / "raw" / CHANNEL_ID / "public_api"
        index: dict[tuple[str, str], Path] = {}
        if cache_root.exists():
            for path in cache_root.glob("*/*/products/*/adjustments/*.json"):
                try:
                    strategy_id = path.parents[1].name.split("_", 1)[0].strip().upper()
                    key = (strategy_id, path.stem.strip())
                    previous = index.get(key)
                    if previous is None or path.stat().st_mtime_ns > previous.stat().st_mtime_ns:
                        index[key] = path
                except OSError:
                    continue
        self._adjustment_cache_index = index

    def build_protocol_pdf_cache_index(self) -> None:
        """Index the latest successful official protocol PDF by source URL."""

        cache_root = self.project_root / "data" / "raw" / CHANNEL_ID / "public_api"
        latest: dict[str, tuple[str, Path, dict[str, Any]]] = {}
        if cache_root.exists():
            for manifest_path in cache_root.glob("*/*/_manifest.json"):
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                for snapshot in manifest.get("raw_snapshots") or []:
                    if not isinstance(snapshot, dict):
                        continue
                    if snapshot.get("collector_name") != "protocol_pdf":
                        continue
                    if snapshot.get("parse_status") != "success":
                        continue
                    source_url = str(snapshot.get("source_url") or "").strip()
                    raw_path = Path(str(snapshot.get("raw_path") or ""))
                    if not raw_path.is_absolute():
                        raw_path = self.project_root / raw_path
                    if not source_url or not raw_path.is_file():
                        continue
                    rank = str(snapshot.get("captured_at") or "")
                    previous = latest.get(source_url)
                    if previous is None or rank > previous[0]:
                        latest[source_url] = (rank, raw_path, snapshot)
        self._protocol_pdf_cache_index = {
            source_url: (item[1], item[2]) for source_url, item in latest.items()
        }

    def cached_adjustment_response(
        self,
        adv_id: str,
        raw_date: str,
        raw_relative_path: Path,
    ) -> tuple[RawResponse | None, float | None]:
        source = self._adjustment_cache_index.get((adv_id.strip().upper(), raw_date))
        if source is None or not source.is_file():
            return None, None
        try:
            raw_bytes = source.read_bytes()
            decoded = json.loads(raw_bytes.decode("utf-8-sig"))
            payload = decoded if isinstance(decoded, dict) else None
            if not self.valid_adjustment_payload(payload):
                return None, None
            age_days = max(0.0, (datetime.now().timestamp() - source.stat().st_mtime) / 86400)
            content_hash = hashlib.sha256(raw_bytes).hexdigest()
            snapshot = RawSnapshot(
                snapshot_id=f"{CHANNEL_ID}-adjustment_detail_cache-{content_hash[:16]}",
                channel_id=CHANNEL_ID,
                collector_name="adjustment_detail_cache",
                access_level="public",
                captured_at=datetime.fromtimestamp(
                    source.stat().st_mtime,
                    tz=timezone.utc,
                ).astimezone().isoformat(timespec="seconds"),
                source_url=f"{API_BASE}/get_investadvisor_adjustment_detail",
                http_status=200,
                raw_path=str(source),
                content_type="application/json",
                content_hash=content_hash,
                parse_status="success",
            ).to_dict()
            with self._snapshot_lock:
                self.raw_snapshots.append(snapshot)
            return RawResponse(
                json_data=payload,
                text=raw_bytes.decode("utf-8", errors="replace"),
                snapshot=snapshot,
                raw_path=source,
            ), age_days
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None, None

    def count_adjustment_cache(self, key: str) -> None:
        with self._snapshot_lock:
            self.adjustment_cache_stats[key] = int(self.adjustment_cache_stats.get(key) or 0) + 1

    def post_form(
        self,
        endpoint: str,
        body: dict[str, Any],
        *,
        collector_name: str,
        raw_relative_path: Path,
    ) -> RawResponse:
        url = f"{API_BASE}/{endpoint.lstrip('/')}"
        payload = urlencode(build_sign(body)).encode("utf-8")
        status: int | None = None
        content_type: str | None = None
        raw_bytes = b""
        parse_status = "failed"
        json_data: dict[str, Any] | None = None
        text = ""

        for attempt in range(1, 4):
            request = Request(
                url,
                data=payload,
                method="POST",
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "GFF-Charset": "UTF-8",
                    "User-Agent": USER_AGENT,
                    "Accept-Encoding": "identity",
                    "Connection": "close",
                },
            )
            try:
                with urlopen(request, timeout=45) as response:
                    status = response.status
                    content_type = response.headers.get("Content-Type")
                    try:
                        raw_bytes = response.read()
                        parse_status = "success"
                    except IncompleteRead as error:
                        raw_bytes = error.partial
                        parse_status = "partial"
            except HTTPError as error:
                status = error.code
                content_type = error.headers.get("Content-Type") if error.headers else None
                raw_bytes = error.read()
                parse_status = "failed"
            except URLError as error:
                raw_bytes = json.dumps(
                    {"transport_error": str(error.reason), "endpoint": endpoint, "body": body},
                    ensure_ascii=False,
                ).encode("utf-8")
                parse_status = "failed"
            except (TimeoutError, OSError) as error:
                raw_bytes = json.dumps(
                    {"transport_error": str(error), "endpoint": endpoint, "body": body},
                    ensure_ascii=False,
                ).encode("utf-8")
                parse_status = "failed"

            text = raw_bytes.decode("utf-8", errors="replace")
            try:
                decoded = json.loads(text)
                json_data = decoded if isinstance(decoded, dict) else {"data": decoded}
                if parse_status == "success":
                    break
            except json.JSONDecodeError:
                json_data = None
                parse_status = "failed"

            if attempt == 3:
                break

        raw_path = self.raw_base_dir / raw_relative_path
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_bytes(raw_bytes)
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
            parse_status=parse_status,
        ).to_dict()
        with self._snapshot_lock:
            self.raw_snapshots.append(snapshot)
        return RawResponse(json_data=json_data, text=text, snapshot=snapshot, raw_path=raw_path)

    def fetch_binary(
        self,
        url: str,
        *,
        collector_name: str,
        raw_relative_path: Path,
    ) -> tuple[bytes, dict[str, Any], Path]:
        status: int | None = None
        content_type: str | None = None
        raw_bytes = b""
        parse_status = "success"
        for attempt in range(1, 4):
            request = Request(
                url,
                method="GET",
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept-Encoding": "identity",
                    "Connection": "close",
                },
            )
            try:
                with urlopen(request, timeout=45) as response:
                    status = response.status
                    content_type = response.headers.get("Content-Type")
                    try:
                        raw_bytes = response.read()
                        parse_status = "success"
                    except IncompleteRead as error:
                        raw_bytes = error.partial
                        parse_status = "partial"
            except HTTPError as error:
                status = error.code
                content_type = error.headers.get("Content-Type") if error.headers else None
                raw_bytes = error.read()
                parse_status = "failed"
            except URLError as error:
                raw_bytes = json.dumps(
                    {"transport_error": str(error.reason), "url": url},
                    ensure_ascii=False,
                ).encode("utf-8")
                parse_status = "failed"
            except (TimeoutError, OSError) as error:
                raw_bytes = json.dumps(
                    {"transport_error": str(error), "url": url},
                    ensure_ascii=False,
                ).encode("utf-8")
                parse_status = "failed"
            if parse_status == "success" or attempt == 3:
                break

        raw_path = self.raw_base_dir / raw_relative_path
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_bytes(raw_bytes)
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
            parse_status=parse_status,
        ).to_dict()
        with self._snapshot_lock:
            self.raw_snapshots.append(snapshot)
        return raw_bytes, snapshot, raw_path

    def collect(self) -> dict[str, Any]:
        self.raw_base_dir.mkdir(parents=True, exist_ok=True)
        self.build_adjustment_cache_index()
        self.build_protocol_pdf_cache_index()
        strategy_list, standard_catalog_info = self.collect_strategy_catalog()
        profit_strategy_list, profit_catalog_info = self.collect_profit_strategy_catalog()
        local_strategy_ids = standard_catalog_info.get("local_strategy_ids") or []
        catalog_ids = sorted(
            {
                str(item.get("adv_id") or "").strip()
                for item in [*strategy_list, *profit_strategy_list]
                if str(item.get("adv_id") or "").strip()
            }
        )
        catalog_info = {
            **catalog_diff(catalog_ids, local_strategy_ids),
            "catalog_complete": bool(standard_catalog_info.get("catalog_complete"))
            and bool(profit_catalog_info.get("catalog_complete")),
            "catalog_pages": int(standard_catalog_info.get("catalog_pages") or 0)
            + int(profit_catalog_info.get("catalog_pages") or 0),
            "catalog_stop_reason": (
                f"standard={standard_catalog_info.get('catalog_stop_reason')};"
                f"profit={profit_catalog_info.get('catalog_stop_reason')}"
            ),
            "catalog_source": [
                standard_catalog_info.get("catalog_source"),
                profit_catalog_info.get("catalog_source"),
            ],
            "local_strategy_ids": local_strategy_ids,
            "standard_catalog_strategy_total": int(
                standard_catalog_info.get("catalog_strategy_total") or 0
            ),
            "standard_catalog_strategy_ids": list(
                standard_catalog_info.get("catalog_strategy_ids") or []
            ),
            "standard_catalog_complete": bool(
                standard_catalog_info.get("catalog_complete")
            ),
            "standard_catalog_pages": int(standard_catalog_info.get("catalog_pages") or 0),
            "standard_catalog_stop_reason": standard_catalog_info.get("catalog_stop_reason"),
            "profit_catalog_strategy_total": int(
                profit_catalog_info.get("catalog_strategy_total") or 0
            ),
            "profit_catalog_strategy_ids": list(
                profit_catalog_info.get("catalog_strategy_ids") or []
            ),
            "profit_catalog_complete": bool(profit_catalog_info.get("catalog_complete")),
            "profit_catalog_pages": int(profit_catalog_info.get("catalog_pages") or 0),
            "profit_catalog_stop_reason": profit_catalog_info.get("catalog_stop_reason"),
            "profit_catalog_excluded_local_ids": sorted(
                set(
                    strategy_id
                    for strategy_id in local_strategy_ids
                    if re.fullmatch(r"ZY\d{8}", strategy_id)
                )
                - set(profit_catalog_info.get("catalog_strategy_ids") or [])
            ),
        }
        home_response = self.post_form(
            "get_invest_advisor_config",
            {"session_id": ""},
            collector_name="home_config",
            raw_relative_path=Path("index") / "get_invest_advisor_config.json",
        )
        catalog_new_ids = set(catalog_info.get("new_strategy_ids") or [])
        strategy_map = {
            str(item.get("adv_id") or "").strip(): {
                **item,
                "_catalog_new": str(item.get("adv_id") or "").strip() in catalog_new_ids,
            }
            for item in strategy_list
            if str(item.get("adv_id") or "").strip()
        }
        for item in profit_strategy_list:
            adv_id = str(item.get("adv_id") or "").strip()
            if not adv_id:
                continue
            strategy_map[adv_id] = {
                **item,
                "_catalog_kind": "profit_issue",
                "_catalog_new": adv_id in catalog_new_ids,
            }
        for adv_id in extract_home_strategy_codes(home_response.json_data or {}):
            strategy_map.setdefault(adv_id, {"adv_id": adv_id})
        for adv_id in self.extra_strategy_ids:
            # Target-profit issues are sale/issue entities, not durable parent
            # strategies.  Keep delisted issues in the database as history, but
            # do not recreate them in the current batch from an ID-only seed.
            if re.fullmatch(r"ZY\d{8}", adv_id) and adv_id not in strategy_map:
                continue
            strategy_map.setdefault(adv_id, {"adv_id": adv_id})

        strategy_ids = list(strategy_map)
        if self.limit and self.limit > 0:
            strategy_ids = strategy_ids[: self.limit]

        payloads: dict[str, dict[str, Any]] = {}
        progress = ConsoleProgress("广发基金仓位及调仓采集", len(strategy_ids))
        progress.emit(0, success=0, failed=0, extra=f"并发数 {min(self.max_workers, max(len(strategy_ids), 1))}")
        with ThreadPoolExecutor(max_workers=min(self.max_workers, max(len(strategy_ids), 1))) as executor:
            future_map = {
                executor.submit(
                    self.collect_profit_issue_payloads
                    if re.fullmatch(r"ZY\d{8}", adv_id)
                    else self.collect_strategy_payloads,
                    adv_id,
                    strategy_map.get(adv_id) or {},
                ): adv_id
                for adv_id in strategy_ids
            }
            for index, future in enumerate(as_completed(future_map), start=1):
                adv_id, payload = future.result()
                payloads[adv_id] = payload
                print(
                    f"{index}/{len(strategy_ids)} {adv_id} "
                    f"{payload.get('strategy_name') or adv_id} "
                    f"events={len(payload.get('adjustments') or [])} "
                    f"scope={'profit_issue_metadata' if payload.get('profit_issue') else 'core_strategy'}",
                    flush=True,
                )
                progress.emit(
                    index,
                    success=index,
                    failed=0,
                    current=f"{adv_id} {payload.get('strategy_name') or ''}".strip(),
                    extra=f"调仓事件 {len(payload.get('adjustments') or [])}",
                )

        fund_codes = self.collect_fund_codes(payloads)
        fund_nav_by_code = self.collect_fund_navs(fund_codes) if self.collect_fund_nav else {}
        normalized = self.normalize(payloads, fund_nav_by_code)
        self.write_normalized(normalized)
        summary = self.build_summary(payloads, fund_nav_by_code, normalized, catalog_info)
        self.write_run_manifest(summary)
        return summary

    def collect_strategy_catalog(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Enumerate the public catalog until the endpoint proves exhaustion.

        The current H5 uses this endpoint for its ordinary-strategy shelf.  The
        endpoint does not expose a stable total field, so pagination is attempted
        with a duplicate-page guard; a truncated or non-paginating response is
        marked incomplete instead of silently being treated as a full catalog.
        """

        page_size = 200
        merged: dict[str, dict[str, Any]] = {}
        seen_page_ids: set[str] = set()
        page_total = 0
        complete = False
        stop_reason = "no_response"
        for page_no in range(1, 101):
            raw_name = (
                "get_investadvisor_operate_config_list.json"
                if page_no == 1
                else f"get_investadvisor_operate_config_list_page_{page_no:04d}.json"
            )
            response = self.post_form(
                "get_investadvisor_operate_config_list",
                {"session_id": "", "page_no": page_no, "page_size": page_size},
                collector_name="strategy_list",
                raw_relative_path=Path("index") / raw_name,
            )
            page_total += 1
            payload = response.json_data or {}
            rows = payload.get("config_list") if isinstance(payload, dict) else []
            rows = rows if isinstance(rows, list) else []
            page_ids = {
                str(row.get("adv_id") or "").strip()
                for row in rows
                if isinstance(row, dict) and str(row.get("adv_id") or "").strip()
            }
            if not rows:
                complete = response.snapshot.get("parse_status") == "success" and bool(payload)
                stop_reason = "empty_page"
                break
            if page_no > 1 and page_ids and page_ids.issubset(seen_page_ids):
                stop_reason = "duplicate_page_guard"
                break
            for row in rows:
                if not isinstance(row, dict):
                    continue
                strategy_id = str(row.get("adv_id") or "").strip()
                if strategy_id:
                    merged[strategy_id] = row
            seen_page_ids.update(page_ids)
            if len(rows) < page_size:
                complete = True
                stop_reason = "short_page"
                break
        else:
            stop_reason = "page_limit"
        catalog_ids = sorted(merged)
        local_ids = load_local_strategy_ids(self.project_root, CHANNEL_ID)
        diff = catalog_diff(catalog_ids, local_ids)
        return list(merged.values()), {
            **diff,
            "catalog_complete": complete,
            "catalog_pages": page_total,
            "catalog_page_size": page_size,
            "catalog_stop_reason": stop_reason,
            "catalog_source": f"{API_BASE}/get_investadvisor_operate_config_list",
            "local_strategy_ids": local_ids,
        }

    def collect_profit_strategy_catalog(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Enumerate the target-profit issue catalog exposed by the current H5.

        These ``ZY`` rows are individual target-profit issues.  Their anonymous
        list/config/protocol metadata is public, while issue-specific holdings
        and detail pages require a logged-in customer session.  They must not
        inherit the parent strategy curve, holdings or rebalance history.
        """

        page_size = 500
        merged: dict[str, dict[str, Any]] = {}
        seen_page_ids: set[str] = set()
        page_total = 0
        complete = False
        stop_reason = "no_response"
        for page_no in range(1, 101):
            raw_name = (
                "get_profit_investadvisor_list.json"
                if page_no == 1
                else f"get_profit_investadvisor_list_page_{page_no:04d}.json"
            )
            response = self.post_form(
                "get_profit_investadvisor_list",
                {"session_id": "", "page_no": page_no, "page_size": page_size},
                collector_name="profit_strategy_list",
                raw_relative_path=Path("index") / raw_name,
            )
            page_total += 1
            payload = response.json_data or {}
            rows = payload.get("invest_profit_list") if isinstance(payload, dict) else []
            rows = rows if isinstance(rows, list) else []
            page_ids = {
                str(row.get("adv_id") or "").strip()
                for row in rows
                if isinstance(row, dict)
                and re.fullmatch(r"ZY\d{8}", str(row.get("adv_id") or "").strip())
            }
            if not rows:
                complete = response.snapshot.get("parse_status") == "success" and bool(payload)
                stop_reason = "empty_page"
                break
            if page_no > 1 and page_ids and page_ids.issubset(seen_page_ids):
                stop_reason = "duplicate_page_guard"
                break
            for row in rows:
                if not isinstance(row, dict):
                    continue
                strategy_id = str(row.get("adv_id") or "").strip()
                if re.fullmatch(r"ZY\d{8}", strategy_id):
                    merged[strategy_id] = row
            seen_page_ids.update(page_ids)
            has_next = payload.get("is_has_next_page")
            has_next_false = has_next is False or str(has_next).strip().lower() in {
                "0",
                "false",
                "no",
            }
            if has_next_false or len(rows) < page_size:
                complete = True
                stop_reason = "has_next_false" if has_next_false else "short_page"
                break
        else:
            stop_reason = "page_limit"

        catalog_ids = sorted(merged)
        return list(merged.values()), {
            **catalog_diff(catalog_ids, []),
            "catalog_complete": complete,
            "catalog_pages": page_total,
            "catalog_page_size": page_size,
            "catalog_stop_reason": stop_reason,
            "catalog_source": f"{API_BASE}/get_profit_investadvisor_list",
        }

    def collect_profit_issue_payloads(
        self,
        adv_id: str,
        seed: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        """Collect only issue-scoped public metadata for a target-profit issue."""

        strategy_name = str(seed.get("adv_name") or adv_id)
        product_dir = Path("products") / f"{adv_id}_{safe_name(strategy_name)}"
        config_response = self.post_form(
            "get_investadvisor_operate_config_byids",
            {"session_id": "", "adv_ids": adv_id},
            collector_name="profit_issue_config",
            raw_relative_path=product_dir / "get_investadvisor_operate_config_byids.json",
        )
        config_list = (config_response.json_data or {}).get("config_list") or []
        config = config_list[0] if config_list else seed
        strategy_name = str(config.get("adv_name") or strategy_name)
        product_dir = Path("products") / f"{adv_id}_{safe_name(strategy_name)}"
        protocol_response = self.post_form(
            "get_investadvisor_protocol_list",
            {"session_id": "", "adv_id": adv_id},
            collector_name="profit_issue_protocol_list",
            raw_relative_path=product_dir / "get_investadvisor_protocol_list.json",
        )
        protocol_meta = self.collect_cached_profit_protocol_meta(
            adv_id=adv_id,
            strategy_name=strategy_name,
            protocol_payload=protocol_response.json_data or {},
            product_dir=product_dir,
            # The 75 current issues reuse only a few strategy documents.  Parse
            # every distinct URL once so all issues receive benchmark/fee/risk
            # metadata even when the daily node skips ordinary protocol PDFs.
            force_collect=True,
        )
        return adv_id, {
            "strategy_name": strategy_name,
            "seed": seed,
            "config": config,
            "config_response": config_response,
            "protocol_response": protocol_response,
            "protocol_meta": protocol_meta,
            "profit_issue": True,
            "yield_response": None,
            "adjustment_record_response": None,
            "adjustment_details": {},
            "alternate_fund_response": None,
            "adjustments": [],
        }

    def collect_strategy_payloads(
        self,
        adv_id: str,
        seed: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        strategy_name = str(seed.get("adv_name") or adv_id)
        product_dir = Path("products") / f"{adv_id}_{safe_name(strategy_name)}"

        config_response = self.post_form(
            "get_investadvisor_operate_config_byids",
            {"session_id": "", "adv_ids": adv_id},
            collector_name="strategy_config",
            raw_relative_path=product_dir / "get_investadvisor_operate_config_byids.json",
        )
        config_list = (config_response.json_data or {}).get("config_list") or []
        config = config_list[0] if config_list else seed
        strategy_name = str(config.get("adv_name") or strategy_name)
        product_dir = Path("products") / f"{adv_id}_{safe_name(strategy_name)}"

        yield_response = self.post_form(
            "get_investadvisor_yield_trend",
            {"session_id": "", "adv_id": adv_id, "section_type": "7", "from_page": "StrategyDetail"},
            collector_name="yield_since_inception",
            raw_relative_path=product_dir / "get_investadvisor_yield_trend_since_inception.json",
        )
        adjustment_record_response = self.post_form(
            "get_investadvisor_adjustment_record",
            {"session_id": "", "adv_id": adv_id, "page_no": 1, "page_size": 500},
            collector_name="adjustment_record",
            raw_relative_path=product_dir / "get_investadvisor_adjustment_record.json",
        )
        protocol_response = self.post_form(
            "get_investadvisor_protocol_list",
            {"session_id": "", "adv_id": adv_id},
            collector_name="protocol_list",
            raw_relative_path=product_dir / "get_investadvisor_protocol_list.json",
        )
        alternate_fund_response = self.post_form(
            "get_investadvisor_alternate_fund_list",
            {"session_id": "", "adv_id": adv_id},
            collector_name="alternate_fund_list",
            raw_relative_path=product_dir / "get_investadvisor_alternate_fund_list.json",
        )

        adjustment_details: dict[str, RawResponse] = {}
        adjustments = (adjustment_record_response.json_data or {}).get("adjustment_list") or []
        dated_adjustments = [
            str(item.get("adjustment_date") or "").strip()
            for item in adjustments
            if str(item.get("adjustment_date") or "").strip()
        ]
        latest_adjustment_date = max(dated_adjustments) if dated_adjustments else None
        for item in adjustments:
            raw_date = str(item.get("adjustment_date") or "").strip()
            if not raw_date:
                continue
            relative_path = product_dir / "adjustments" / f"{raw_date}.json"
            cached_response, cache_age_days = self.cached_adjustment_response(
                adv_id,
                raw_date,
                relative_path,
            )
            refresh_latest = bool(
                cached_response is not None
                and raw_date == latest_adjustment_date
                and (
                    self.latest_adjustment_refresh_days == 0
                    or (
                        cache_age_days is not None
                        and cache_age_days >= self.latest_adjustment_refresh_days
                    )
                )
            )
            if cached_response is not None and not refresh_latest:
                adjustment_details[raw_date] = cached_response
                self.count_adjustment_cache("reused")
                continue
            fetched = self.post_form(
                "get_investadvisor_adjustment_detail",
                {"session_id": "", "adv_id": adv_id, "adjustment_date": raw_date},
                collector_name="adjustment_detail",
                raw_relative_path=relative_path,
            )
            if self.valid_adjustment_payload(fetched.json_data):
                adjustment_details[raw_date] = fetched
                self.count_adjustment_cache("fetched")
            elif cached_response is not None:
                adjustment_details[raw_date] = cached_response
                self.count_adjustment_cache("fallback")
            else:
                adjustment_details[raw_date] = fetched
                self.count_adjustment_cache("fetched")

        protocol_meta = self.collect_protocol_meta(
            adv_id=adv_id,
            strategy_name=strategy_name,
            protocol_payload=protocol_response.json_data or {},
            product_dir=product_dir,
            force_collect=bool(seed.get("_catalog_new")),
        )

        return adv_id, {
            "strategy_name": strategy_name,
            "seed": seed,
            "config": config,
            "config_response": config_response,
            "yield_response": yield_response,
            "adjustment_record_response": adjustment_record_response,
            "adjustment_details": adjustment_details,
            "protocol_response": protocol_response,
            "alternate_fund_response": alternate_fund_response,
            "protocol_meta": protocol_meta,
            "adjustments": adjustments,
        }

    def collect_cached_profit_protocol_meta(
        self,
        *,
        adv_id: str,
        strategy_name: str,
        protocol_payload: dict[str, Any],
        product_dir: Path,
        force_collect: bool = False,
    ) -> dict[str, Any]:
        protocols = protocol_payload.get("protocol_list") or []
        pdf_item = next(
            (item for item in protocols if str(item.get("protocol_type")) == "3"),
            None,
        )
        protocol_url = str((pdf_item or {}).get("protocol_url") or "").strip()
        if not protocol_url:
            return self.collect_protocol_meta(
                adv_id=adv_id,
                strategy_name=strategy_name,
                protocol_payload=protocol_payload,
                product_dir=product_dir,
                force_collect=force_collect,
            )
        with self._protocol_meta_lock:
            cached = self._protocol_meta_by_url.get(protocol_url)
            if cached is not None:
                return dict(cached)
            url_lock = self._protocol_url_locks.setdefault(protocol_url, threading.Lock())
        # Serialize only identical protocol URLs.  Distinct PDFs may still be
        # downloaded and parsed in parallel with the strategy worker pool.
        with url_lock:
            with self._protocol_meta_lock:
                cached = self._protocol_meta_by_url.get(protocol_url)
                if cached is not None:
                    return dict(cached)
            meta = self.collect_protocol_meta(
                adv_id=adv_id,
                strategy_name=strategy_name,
                protocol_payload=protocol_payload,
                product_dir=product_dir,
                force_collect=force_collect,
            )
            with self._protocol_meta_lock:
                self._protocol_meta_by_url[protocol_url] = dict(meta)
            return meta

    def collect_protocol_meta(
        self,
        *,
        adv_id: str,
        strategy_name: str,
        protocol_payload: dict[str, Any],
        product_dir: Path,
        force_collect: bool = False,
    ) -> dict[str, Any]:
        protocols = protocol_payload.get("protocol_list") or []
        pdf_item = next((item for item in protocols if str(item.get("protocol_type")) == "3"), None)
        if not pdf_item:
            return {}
        meta = {
            "protocol_name": pdf_item.get("protocol_name"),
            "protocol_url": pdf_item.get("protocol_url"),
        }
        if (not self.collect_protocol_pdf and not force_collect) or not pdf_item.get("protocol_url"):
            return meta
        raw_bytes, snapshot, raw_path = self.fetch_binary(
            str(pdf_item.get("protocol_url")),
            collector_name="protocol_pdf",
            raw_relative_path=product_dir / "protocols" / f"{safe_name(strategy_name)}.pdf",
        )
        selected_snapshot = snapshot
        selected_raw_path = raw_path
        protocol_url = str(pdf_item.get("protocol_url"))
        if snapshot.get("parse_status") != "success" or not raw_bytes.startswith(b"%PDF"):
            cached = self._protocol_pdf_cache_index.get(protocol_url)
            if cached is not None:
                cached_path, cached_snapshot = cached
                try:
                    cached_bytes = cached_path.read_bytes()
                except OSError:
                    cached_bytes = b""
                if cached_bytes.startswith(b"%PDF"):
                    raw_bytes = cached_bytes
                    selected_raw_path = cached_path
                    selected_snapshot = cached_snapshot
                    meta["protocol_fetch_snapshot_id"] = snapshot["snapshot_id"]
                    meta["protocol_cache_fallback_path"] = str(cached_path)
                    with self._protocol_meta_lock:
                        self.protocol_pdf_cache_fallback_total += 1
        meta["protocol_snapshot_id"] = selected_snapshot["snapshot_id"]
        meta["protocol_raw_path"] = str(selected_raw_path)
        if pdfplumber is None:
            return meta
        try:
            pages: list[str] = []
            with pdfplumber.open(BytesIO(raw_bytes)) as pdf:
                for page in pdf.pages:
                    pages.append(page.extract_text() or "")
            full_text = "\n".join(pages)
            text_path = self.raw_base_dir / product_dir / "protocols" / f"{safe_name(strategy_name)}.txt"
            text_path.write_text(full_text, encoding="utf-8")
            meta["protocol_text_path"] = str(text_path)
            meta.update(extract_protocol_fields(full_text))
        except Exception as error:  # pragma: no cover
            meta["protocol_parse_error"] = str(error)
        return meta

    def collect_fund_codes(self, payloads: dict[str, dict[str, Any]]) -> list[str]:
        codes: list[str] = []
        for payload in payloads.values():
            for detail_response in payload.get("adjustment_details", {}).values():
                detail_json = detail_response.json_data or {}
                for group in detail_json.get("advisor_comb_list") or []:
                    for fund in group.get("comb_fund_list") or []:
                        code = str(fund.get("fund_code") or "").strip()
                        if code and code not in codes:
                            codes.append(code)
        return codes

    def collect_fund_navs(self, fund_codes: list[str]) -> dict[str, dict[str, Any]]:
        results: dict[str, dict[str, Any]] = {}

        def fetch(code: str) -> tuple[str, dict[str, Any]]:
            fund_dir = Path("funds") / code
            detail_response: RawResponse | None = None
            nav_response: RawResponse | None = None
            detail_json: dict[str, Any] = {}
            nav_json: dict[str, Any] = {}
            latest_nav: dict[str, Any] = {}

            for _ in range(3):
                detail_response = self.post_form(
                    "fund_info_detail",
                    {"session_id": "", "fund_code": code},
                    collector_name="fund_info_detail",
                    raw_relative_path=fund_dir / "fund_info_detail.json",
                )
                nav_response = self.post_form(
                    "fund_more_netvalue",
                    {"session_id": "", "fund_code": code, "page_no": 1, "page_size": 1},
                    collector_name="fund_more_netvalue",
                    raw_relative_path=fund_dir / "fund_more_netvalue_page_1.json",
                )
                detail_json = detail_response.json_data or {}
                nav_json = nav_response.json_data or {}
                nav_rows = nav_json.get("fund_net_values") or []
                latest_nav = nav_rows[0] if nav_rows else {}
                if detail_json.get("RETCODE") == "0000" and latest_nav.get("net_value") not in (None, ""):
                    break

            return code, {
                "fund_name": detail_json.get("fund_name"),
                "fund_type": detail_json.get("type_name"),
                "manager": detail_json.get("manager"),
                "latest_nav": to_float(latest_nav.get("net_value")),
                "latest_nav_date": parse_ymd(latest_nav.get("net_value_date") or latest_nav.get("netvalue_date")),
                "latest_day_growth": to_float(latest_nav.get("day_growth")),
                "detail_snapshot_id": detail_response.snapshot["snapshot_id"],
                "nav_snapshot_id": nav_response.snapshot["snapshot_id"],
                "detail_response": detail_json,
                "nav_response": nav_json,
            }

        with ThreadPoolExecutor(max_workers=min(self.max_workers, max(len(fund_codes), 1))) as executor:
            future_map = {executor.submit(fetch, code): code for code in fund_codes}
            for future in as_completed(future_map):
                code, payload = future.result()
                results[code] = payload
        return results

    def normalize(
        self,
        payloads: dict[str, dict[str, Any]],
        fund_nav_by_code: dict[str, dict[str, Any]],
    ) -> dict[str, list[dict[str, Any]]]:
        strategy_master: list[dict[str, Any]] = []
        performance_daily: list[dict[str, Any]] = []
        fund_snapshot: list[dict[str, Any]] = []
        rebalance_events: list[dict[str, Any]] = []
        rebalance_deltas: list[dict[str, Any]] = []
        fund_public_dim: dict[str, dict[str, Any]] = {}

        for adv_id, payload in payloads.items():
            config = payload.get("config") or {}
            seed = payload.get("seed") or {}
            protocol_meta = payload.get("protocol_meta") or {}
            is_profit_issue = bool(payload.get("profit_issue"))
            source_url = (
                f"{H5_BASE}/invest-advisor/stop-profit-strategy-detail"
                f"?showNavBar=true&advId={adv_id}"
                if is_profit_issue
                else (
                    f"{H5_BASE}/invest-advisor/strategy-detail"
                    f"?showNavBar=true&source_page=other_page&advId={adv_id}"
                )
            )
            strategy_master.append(
                {
                    "channel_id": CHANNEL_ID,
                    "source_strategy_id": adv_id,
                    "strategy_name": config.get("adv_name") or payload.get("strategy_name") or adv_id,
                    "advisor_name": CHANNEL_NAME,
                    "strategy_type": config.get("adv_type"),
                    "risk_level": protocol_meta.get("risk_level") or config.get("adv_risk_level"),
                    "launch_date": (
                        None
                        if is_profit_issue
                        else extract_launch_date(payload["yield_response"].json_data or {})
                    ),
                    "suggested_holding_period": protocol_meta.get("suggested_holding_period")
                    or holding_period_text(config.get("recommend_hold_time_value"), config.get("recommend_hold_time_unit")),
                    "minimum_amount": parse_amount(protocol_meta.get("minimum_amount_text")),
                    "advisory_fee_rate": protocol_meta.get("advisory_fee_rate"),
                    "benchmark": protocol_meta.get("benchmark"),
                    "tags": split_tags(
                        config.get("adv_type"),
                        config.get("adv_risk_level"),
                        protocol_meta.get("investment_scope"),
                        protocol_meta.get("strategy_target"),
                    ),
                    "strategy_description": protocol_meta.get("strategy_description")
                    or protocol_meta.get("strategy_idea")
                    or config.get("risk_perfer")
                    or config.get("adv_desc"),
                    "status": "public",
                    "source_url": source_url,
                    "first_seen_at": self.captured_at,
                    "last_seen_at": self.captured_at,
                    "run_id": self.run_id,
                    "source_snapshot_id": payload["config_response"].snapshot["snapshot_id"],
                    "extra": {
                        "ds_adv_type": config.get("ds_adv_type"),
                        "target_year_min": config.get("target_year_min"),
                        "target_year_max": config.get("target_year_max"),
                        "target_withdrawal": config.get("target_withdrawal"),
                        "risk_center": config.get("risk_center"),
                        "adv_risk_score": config.get("adv_risk_score"),
                        "protocol_name": protocol_meta.get("protocol_name"),
                        "protocol_url": protocol_meta.get("protocol_url"),
                        "protocol_snapshot_id": protocol_meta.get("protocol_snapshot_id"),
                        "protocol_text_path": protocol_meta.get("protocol_text_path"),
                        "protocol_raw_path": protocol_meta.get("protocol_raw_path"),
                        "protocol_fetch_snapshot_id": protocol_meta.get(
                            "protocol_fetch_snapshot_id"
                        ),
                        "protocol_cache_fallback_path": protocol_meta.get(
                            "protocol_cache_fallback_path"
                        ),
                        "protocol_parse_error": protocol_meta.get("protocol_parse_error"),
                        "rebalance_frequency": protocol_meta.get("rebalance_frequency"),
                        "strategy_target": protocol_meta.get("strategy_target"),
                        "investment_scope": protocol_meta.get("investment_scope"),
                        "catalog_kind": "profit_issue" if is_profit_issue else "core_strategy",
                        "adv_operate_state": seed.get("adv_operate_state"),
                        "adv_target_rate": seed.get("adv_target_rate"),
                        "adv_profit_standard": seed.get("adv_profit_standard"),
                        "operate_days": seed.get("operate_days"),
                        "entity_scope": "target_profit_issue" if is_profit_issue else "strategy",
                        "issue_specific_detail_access": (
                            "login_required" if is_profit_issue else "public"
                        ),
                        "issue_specific_holding_access": (
                            "login_required" if is_profit_issue else "public"
                        ),
                        "performance_lineage": (
                            "not_loaded_parent_strategy_curve_must_not_be_attributed_to_issue"
                            if is_profit_issue
                            else "official_strategy_curve"
                        ),
                        "rebalance_lineage": (
                            "not_loaded_parent_strategy_rebalance_must_not_be_attributed_to_issue"
                            if is_profit_issue
                            else "official_strategy_rebalance"
                        ),
                    },
                }
            )

            if is_profit_issue:
                continue

            yield_rows = (payload["yield_response"].json_data or {}).get("adv_yield_trend_list") or []
            for row in yield_rows:
                performance_daily.append(
                    {
                        "channel_id": CHANNEL_ID,
                        "source_strategy_id": adv_id,
                        "trade_date": parse_ymd(row.get("yield_date")),
                        "nav": None,
                        "daily_return": None,
                        "cumulative_return": to_float(row.get("yield_rate")),
                        "benchmark_return": to_float(row.get("base_yield_rate")),
                        "index_return": to_float(row.get("hs300_rate")),
                        "max_drawdown": None,
                        "source_snapshot_id": payload["yield_response"].snapshot["snapshot_id"],
                        "run_id": self.run_id,
                        "section_type": "7",
                        "section_name": SECTION_TYPE_MAP["7"],
                    }
                )

            adjustments = sorted(
                payload.get("adjustments") or [],
                key=lambda item: str(item.get("adjustment_date") or ""),
            )
            previous_position_date: str | None = None
            for item in adjustments:
                raw_date = str(item.get("adjustment_date") or "").strip()
                rebalance_date = parse_ymd(raw_date)
                detail_response = payload["adjustment_details"].get(raw_date)
                detail_json = (detail_response.json_data if detail_response else {}) or {}
                event_hash = hashlib.sha256(compact_json(item).encode("utf-8")).hexdigest()[:16]
                event_id = f"{CHANNEL_ID}-{adv_id}-{rebalance_date or 'unknown'}-{event_hash}"
                rebalance_events.append(
                    {
                        "rebalance_event_id": event_id,
                        "channel_id": CHANNEL_ID,
                        "source_strategy_id": adv_id,
                        "rebalance_date": rebalance_date,
                        "previous_position_date": previous_position_date,
                        "new_position_date": rebalance_date,
                        "disclosure_date": rebalance_date,
                        "event_title": f"{config.get('adv_name') or adv_id} 调仓",
                        "event_reason": item.get("adjustment_desc"),
                        "source_url": f"{API_BASE}/get_investadvisor_adjustment_detail",
                        "source_snapshot_id": (
                            detail_response.snapshot["snapshot_id"]
                            if detail_response
                            else payload["adjustment_record_response"].snapshot["snapshot_id"]
                        ),
                        "confidence_level": "official_exact",
                        "run_id": self.run_id,
                    }
                )

                snapshot_id = f"{CHANNEL_ID}-{adv_id}-{rebalance_date or self.day}-{self.run_id}"
                for group in detail_json.get("advisor_comb_list") or []:
                    fund_type_code = str(group.get("fund_type") or "").strip()
                    fund_type_name = FUND_TYPE_MAP.get(fund_type_code, fund_type_code or None)
                    group_name = fund_type_name
                    for fund in group.get("comb_fund_list") or []:
                        fund_code = str(fund.get("fund_code") or "").strip()
                        fund_name = fund.get("fund_name")
                        before_weight = to_float(fund.get("old_percent"))
                        after_weight = to_float(fund.get("new_percent"))
                        nav_info = fund_nav_by_code.get(fund_code) or {}
                        delta = None
                        if before_weight is not None or after_weight is not None:
                            delta = (after_weight or 0.0) - (before_weight or 0.0)
                        if before_weight in (None, 0.0) and (after_weight or 0.0) > 0:
                            action_type = "buy"
                        elif (before_weight or 0.0) > 0 and after_weight in (None, 0.0):
                            action_type = "sell"
                        elif before_weight is not None and after_weight is not None and after_weight > before_weight:
                            action_type = "increase"
                        elif before_weight is not None and after_weight is not None and after_weight < before_weight:
                            action_type = "decrease"
                        else:
                            action_type = "keep"

                        rebalance_deltas.append(
                            {
                                "rebalance_event_id": event_id,
                                "fund_code": fund_code,
                                "fund_name": fund_name,
                                "before_weight": before_weight,
                                "after_weight": after_weight,
                                "weight_delta": delta,
                                "action_type": action_type,
                                "run_id": self.run_id,
                                "fund_type_code": fund_type_code or None,
                                "fund_group_weight_before": to_float(group.get("old_percent")),
                                "fund_group_weight_after": to_float(group.get("new_percent")),
                                "source_snapshot_id": detail_response.snapshot["snapshot_id"] if detail_response else None,
                            }
                        )

                        fund_snapshot.append(
                            {
                                "snapshot_id": snapshot_id,
                                "channel_id": CHANNEL_ID,
                                "source_strategy_id": adv_id,
                                "position_date": rebalance_date,
                                "disclosure_date": rebalance_date,
                                "fund_code": fund_code,
                                "fund_name": fund_name,
                                "fund_asset_type": fund_type_name,
                                "fund_group_name": group_name,
                                "fund_weight": after_weight,
                                "fund_nav": nav_info.get("latest_nav"),
                                "fund_nav_date": nav_info.get("latest_nav_date"),
                                "is_precise_weight": after_weight is not None,
                                "is_login_required": False,
                                "source_url": f"{API_BASE}/get_investadvisor_adjustment_detail",
                                "raw_record_hash": hashlib.sha256(compact_json(fund).encode("utf-8")).hexdigest(),
                                "confidence_level": "official_exact",
                                "access_level": "public",
                                "run_id": self.run_id,
                                "source_snapshot_id": detail_response.snapshot["snapshot_id"] if detail_response else None,
                            }
                        )

                        if fund_code:
                            fund_public_dim[fund_code] = {
                                "fund_code": fund_code,
                                "fund_name": nav_info.get("fund_name") or fund_name,
                                "fund_company": None,
                                "fund_type": nav_info.get("fund_type") or fund_type_name,
                                "tracking_index": None,
                                "theme_tags": json.dumps(split_tags(fund_type_name), ensure_ascii=False),
                                "latest_nav": nav_info.get("latest_nav"),
                                "latest_nav_date": nav_info.get("latest_nav_date"),
                                "status": "active",
                                "source": "gffunds_public_api",
                                "updated_at": self.captured_at,
                                "run_id": self.run_id,
                            }
                previous_position_date = rebalance_date

        return {
            "strategy_master": strategy_master,
            "strategy_performance_daily": performance_daily,
            "strategy_fund_snapshot": fund_snapshot,
            "strategy_rebalance_event": rebalance_events,
            "strategy_rebalance_fund_delta": rebalance_deltas,
            "fund_public_dim": list(fund_public_dim.values()),
        }

    def write_normalized(self, normalized: dict[str, list[dict[str, Any]]]) -> None:
        for entity, rows in normalized.items():
            output_path = self.normalized_base_dir / entity / self.day / f"{self.run_id}.jsonl"
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
        payloads: dict[str, dict[str, Any]],
        fund_nav_by_code: dict[str, dict[str, Any]],
        normalized: dict[str, list[dict[str, Any]]],
        catalog_info: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        core_payloads = {
            strategy_id: payload
            for strategy_id, payload in payloads.items()
            if not payload.get("profit_issue")
        }
        profit_issue_payloads = {
            strategy_id: payload
            for strategy_id, payload in payloads.items()
            if payload.get("profit_issue")
        }
        adjustment_event_counts = [
            len(payload.get("adjustments") or []) for payload in core_payloads.values()
        ]
        latest_fund_counts: list[int] = []
        protocol_pdf_count = 0
        for payload in payloads.values():
            details = payload.get("adjustment_details") or {}
            if details:
                latest_key = sorted(details.keys())[-1]
                latest_json = (details[latest_key].json_data or {}).get("advisor_comb_list") or []
                latest_fund_counts.append(
                    sum(len(group.get("comb_fund_list") or []) for group in latest_json)
                )
            if (payload.get("protocol_meta") or {}).get("protocol_snapshot_id"):
                protocol_pdf_count += 1

        catalog_info = catalog_info or {}
        catalog_reconciliation = reconcile_catalog_batch(
            catalog_info.get("catalog_strategy_ids") or [],
            (
                row.get("source_strategy_id")
                for row in normalized.get("strategy_master", [])
                if isinstance(row, dict)
            ),
            catalog_info.get("local_strategy_ids") or [],
        )
        return {
            "channel_id": CHANNEL_ID,
            "channel_name": CHANNEL_NAME,
            "run_id": self.run_id,
            "captured_at": self.captured_at,
            "raw_dir": str(self.raw_base_dir),
            "normalized_dir": str(self.normalized_base_dir),
            "strategy_total": len(payloads),
            "core_strategy_total": len(core_payloads),
            "profit_issue_strategy_total": len(profit_issue_payloads),
            "profit_issue_public_metadata_total": sum(
                1
                for payload in profit_issue_payloads.values()
                if (payload["config_response"].json_data or {}).get("RETCODE") == "0000"
            ),
            "profit_issue_performance_status": (
                "not_loaded_parent_strategy_curve_not_issue_specific"
            ),
            "profit_issue_holding_status": "login_required_issue_specific",
            "profit_issue_rebalance_status": (
                "not_loaded_parent_strategy_rebalance_not_issue_specific"
            ),
            "catalog_strategy_total": int(catalog_reconciliation.get("catalog_strategy_total") or 0),
            "catalog_strategy_ids": list(catalog_reconciliation.get("catalog_strategy_ids") or []),
            "catalog_new_strategy_total": int(catalog_reconciliation.get("new_strategy_total") or 0),
            "catalog_new_strategy_ids": list(catalog_reconciliation.get("new_strategy_ids") or []),
            "catalog_batch_collected_strategy_total": int(
                catalog_reconciliation.get("catalog_batch_collected_strategy_total") or 0
            ),
            "catalog_batch_missing_strategy_total": int(
                catalog_reconciliation.get("catalog_batch_missing_strategy_total") or 0
            ),
            "catalog_batch_missing_strategy_ids": list(
                catalog_reconciliation.get("catalog_batch_missing_strategy_ids") or []
            ),
            "catalog_batch_closed": bool(catalog_reconciliation.get("catalog_batch_closed")),
            "catalog_new_strategy_collected_total": int(
                catalog_reconciliation.get("catalog_new_strategy_collected_total") or 0
            ),
            "catalog_new_strategy_collected_ids": list(
                catalog_reconciliation.get("catalog_new_strategy_collected_ids") or []
            ),
            "catalog_new_strategy_missing_total": int(
                catalog_reconciliation.get("catalog_new_strategy_missing_total") or 0
            ),
            "catalog_new_strategy_missing_ids": list(
                catalog_reconciliation.get("catalog_new_strategy_missing_ids") or []
            ),
            "catalog_complete": bool(catalog_info.get("catalog_complete")),
            "catalog_pages": int(catalog_info.get("catalog_pages") or 0),
            "catalog_stop_reason": catalog_info.get("catalog_stop_reason"),
            "catalog_source": catalog_info.get("catalog_source"),
            "standard_catalog_strategy_total": int(
                catalog_info.get("standard_catalog_strategy_total") or 0
            ),
            "standard_catalog_strategy_ids": list(
                catalog_info.get("standard_catalog_strategy_ids") or []
            ),
            "standard_catalog_complete": bool(catalog_info.get("standard_catalog_complete")),
            "standard_catalog_pages": int(catalog_info.get("standard_catalog_pages") or 0),
            "standard_catalog_stop_reason": catalog_info.get("standard_catalog_stop_reason"),
            "profit_catalog_strategy_total": int(
                catalog_info.get("profit_catalog_strategy_total") or 0
            ),
            "profit_catalog_strategy_ids": list(
                catalog_info.get("profit_catalog_strategy_ids") or []
            ),
            "profit_catalog_complete": bool(catalog_info.get("profit_catalog_complete")),
            "profit_catalog_pages": int(catalog_info.get("profit_catalog_pages") or 0),
            "profit_catalog_stop_reason": catalog_info.get("profit_catalog_stop_reason"),
            "profit_catalog_excluded_local_ids": list(
                catalog_info.get("profit_catalog_excluded_local_ids") or []
            ),
            "yield_ok": sum(
                1
                for payload in core_payloads.values()
                if (payload["yield_response"].json_data or {}).get("RETCODE") == "0000"
            ),
            "yield_non_empty": sum(
                1
                for payload in core_payloads.values()
                if (
                    (payload["yield_response"].json_data or {}).get("RETCODE") == "0000"
                    and bool(
                        (payload["yield_response"].json_data or {}).get(
                            "adv_yield_trend_list"
                        )
                    )
                )
            ),
            "rebalance_ok": sum(
                1
                for payload in core_payloads.values()
                if (payload["adjustment_record_response"].json_data or {}).get("RETCODE") == "0000"
            ),
            "rebalance_event_total": len(normalized["strategy_rebalance_event"]),
            "rebalance_fund_delta_total": len(normalized["strategy_rebalance_fund_delta"]),
            "public_snapshot_rows": len(normalized["strategy_fund_snapshot"]),
            "latest_snapshot_non_empty": sum(1 for count in latest_fund_counts if count > 0),
            "latest_snapshot_rows_total": sum(latest_fund_counts),
            "latest_snapshot_rows_min": min(latest_fund_counts) if latest_fund_counts else 0,
            "latest_snapshot_rows_max": max(latest_fund_counts) if latest_fund_counts else 0,
            "daily_rows_total": len(normalized["strategy_performance_daily"]),
            "fund_nav_total": len(fund_nav_by_code),
            "fund_nav_with_latest_nav": sum(
                1 for item in fund_nav_by_code.values() if item.get("latest_nav") is not None
            ),
            "protocol_pdf_total": protocol_pdf_count,
            "protocol_pdf_cache_fallback_total": self.protocol_pdf_cache_fallback_total,
            "raw_snapshot_total": len(self.raw_snapshots),
            "adjustment_events_non_empty": sum(1 for count in adjustment_event_counts if count > 0),
            "adjustment_detail_reused_total": self.adjustment_cache_stats["reused"],
            "adjustment_detail_fetched_total": self.adjustment_cache_stats["fetched"],
            "adjustment_detail_fallback_total": self.adjustment_cache_stats["fallback"],
            "adjustment_detail_cache_entries": len(self._adjustment_cache_index),
        }


def collect_gffunds_public(
    project_root: Path,
    *,
    max_workers: int = 8,
    limit: int | None = None,
    collect_fund_nav: bool = True,
    collect_protocol_pdf: bool = True,
    extra_strategy_ids: list[str] | None = None,
    run_id: str | None = None,
    latest_adjustment_refresh_days: int = 1,
) -> dict[str, Any]:
    collector = GFFundsPublicCollector(
        project_root,
        max_workers=max_workers,
        limit=limit,
        collect_fund_nav=collect_fund_nav,
        collect_protocol_pdf=collect_protocol_pdf,
        extra_strategy_ids=extra_strategy_ids,
        run_id=run_id,
        latest_adjustment_refresh_days=latest_adjustment_refresh_days,
    )
    return collector.collect()
