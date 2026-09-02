from __future__ import annotations

import csv
import concurrent.futures
import hashlib
import html
import json
import math
import re
import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http.client import IncompleteRead
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import Request, urlopen

from advisor_monitor.collectors.gffunds_public import collect_gffunds_public
from advisor_monitor.collectors.zocaifu_public import collect_zocaifu_public
from advisor_monitor.gffunds_public_jobs import find_latest_discovered_strategy_file, load_strategy_ids
from advisor_monitor.models import RawSnapshot
from advisor_monitor.strategy_catalog import load_local_strategy_ids, reconcile_catalog_batch


USER_AGENT = "Mozilla/5.0 advisor-monitor/0.1"
DEFAULT_ENTITIES = [
    "strategy_master",
    "strategy_performance_daily",
    "strategy_performance_interval",
    "strategy_fund_snapshot",
    "strategy_rebalance_event",
    "strategy_rebalance_fund_delta",
    "fund_public_dim",
    "app_public_entry",
    "strategy_disclosure_event",
]


def load_gffunds_strategy_ids_from_analysis_db(project_root: Path) -> list[str]:
    db_path = project_root / "data" / "analysis_zh_current.sqlite"
    if not db_path.exists():
        return []

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            'SELECT DISTINCT "渠道策略ID" FROM "策略信息" '
            'WHERE "渠道ID" = ? AND "渠道策略ID" IS NOT NULL',
            ("gffunds",),
        ).fetchall()
    finally:
        conn.close()

    strategy_ids: list[str] = []
    for (value,) in rows:
        strategy_id = str(value or "").strip()
        if re.fullmatch(r"GFJJ\d{6}", strategy_id) and strategy_id not in strategy_ids:
            strategy_ids.append(strategy_id)
    return sorted(strategy_ids)


@dataclass(frozen=True)
class RawResponse:
    raw_path: Path
    snapshot: dict[str, Any]
    text: str
    json_data: Any | None
    final_url: str


def now_local() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def parse_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and math.isnan(value):
            return None
        return float(value)
    text = strip_html(str(value)).replace(",", "").strip()
    if not text or text in {"--", "-", "暂无数据"}:
        return None
    if text.endswith("%"):
        text = text[:-1]
    try:
        return float(text)
    except ValueError:
        return None


def pct_decimal_to_percent(value: Any) -> float | None:
    parsed = parse_float(value)
    return None if parsed is None else round(parsed * 100, 6)


def gfsec_fima_daily_curve_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert official daily returns into a rebased, drawable NAV series.

    The GFsec field is named ``portfolioDayYield`` and contains a daily return,
    not a cumulative return.  The first available observation is used as the
    1.0 base point; subsequent observations are compounded chronologically.
    """

    source_rows: list[tuple[str, dict[str, Any]]] = []
    for source in object_rows(payload.get("data")):
        trade_date = gfsec_fima_curve_date(source.get("busiDate"))
        if trade_date:
            source_rows.append((trade_date, source))
    source_rows.sort(key=lambda item: item[0])

    result: list[dict[str, Any]] = []
    strategy_nav: float | None = None
    benchmark_nav: float | None = None
    for trade_date, source in source_rows:
        strategy_daily = parse_float(source.get("portfolioDayYield"))
        benchmark_daily = parse_float(source.get("indexDayYield"))
        if strategy_daily is not None:
            strategy_nav = 1.0 if strategy_nav is None else strategy_nav * (1.0 + strategy_daily)
        if benchmark_daily is not None:
            benchmark_nav = 1.0 if benchmark_nav is None else benchmark_nav * (1.0 + benchmark_daily)
        if strategy_nav is None:
            continue
        result.append(
            {
                "trade_date": trade_date,
                "nav": round(strategy_nav, 8),
                "daily_return_pct": pct_decimal_to_percent(strategy_daily),
                "cumulative_return_pct": round((strategy_nav - 1.0) * 100.0, 8),
                "benchmark_cumulative_return_pct": (
                    round((benchmark_nav - 1.0) * 100.0, 8)
                    if benchmark_daily is not None and benchmark_nav is not None
                    else None
                ),
                "source_snapshot_id": source.get("__source_snapshot_id"),
            }
        )
    return result


def gfsec_fima_curve_date(value: Any) -> str | None:
    """Parse Wealth Manager chart epochs as China-local calendar dates."""

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        if number > 10_000_000_000:
            return (datetime.fromtimestamp(number / 1000, timezone.utc) + timedelta(hours=8)).date().isoformat()
        if number > 1_000_000_000:
            return (datetime.fromtimestamp(number, timezone.utc) + timedelta(hours=8)).date().isoformat()
    return parse_flexible_date(value)


def gfsec_fima_official_curve_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize GFsec's official cumulative strategy and benchmark curves.

    ``portfolioAndIndexYield`` is the data source used by the Wealth Manager
    chart.  Its ``totalYield`` values are already cumulative decimals and must
    not be reconstructed by compounding the rounded ``dayYieldList`` values.
    Benchmark values are joined only on their exact disclosed dates; a lagging
    benchmark series is deliberately not forward-filled.
    """

    benchmark_by_date: dict[str, float] = {}
    for source in object_rows(payload.get("indexYields")):
        trade_date = gfsec_fima_curve_date(source.get("busiDate"))
        total_yield = parse_float(source.get("totalYield"))
        if trade_date and total_yield is not None:
            benchmark_by_date[trade_date] = total_yield

    portfolio_by_date: dict[str, dict[str, Any]] = {}
    for source in object_rows(payload.get("portfolioYields")):
        trade_date = gfsec_fima_curve_date(source.get("busiDate"))
        total_yield = parse_float(source.get("totalYield"))
        if trade_date and total_yield is not None:
            portfolio_by_date[trade_date] = source

    result: list[dict[str, Any]] = []
    previous_total_yield: float | None = None
    for trade_date in sorted(portfolio_by_date):
        source = portfolio_by_date[trade_date]
        total_yield = parse_float(source.get("totalYield"))
        if total_yield is None:
            continue
        daily_return_pct = None
        if previous_total_yield is not None and 1.0 + previous_total_yield != 0:
            daily_return_pct = round(
                ((1.0 + total_yield) / (1.0 + previous_total_yield) - 1.0) * 100.0,
                8,
            )
        benchmark_total_yield = benchmark_by_date.get(trade_date)
        result.append(
            {
                "trade_date": trade_date,
                "nav": round(1.0 + total_yield, 8),
                "daily_return_pct": daily_return_pct,
                "cumulative_return_pct": round(total_yield * 100.0, 8),
                "benchmark_cumulative_return_pct": (
                    round(benchmark_total_yield * 100.0, 8)
                    if benchmark_total_yield is not None
                    else None
                ),
                "source_snapshot_id": source.get("__source_snapshot_id"),
            }
        )
        previous_total_yield = total_yield
    return result


def gfsec_robot_curve_payload_rows(payload: Any) -> list[dict[str, Any]]:
    """Return the point list from the legacy Robot curve response.

    The old 1.0.0 and newer 2.0.0 deployments used slightly different
    envelopes.  Keep the accepted shapes narrow and require object rows so an
    HTML error page or an unrelated object cannot be mistaken for a curve.
    """

    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("data", "rows", "list", "result"):
        value = payload.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
        if isinstance(value, dict):
            for nested_key in ("data", "rows", "list", "result"):
                nested = value.get(nested_key)
                if isinstance(nested, list):
                    return [row for row in nested if isinstance(row, dict)]
    return []


def gfsec_robot_curve_rows(
    channel_id: str,
    strategy_id: str,
    payload: Any,
    source_snapshot_id: str | None,
) -> list[dict[str, Any]]:
    """Normalize an official Robot cumulative-return curve without interpolation."""

    source_rows: list[tuple[str, dict[str, Any]]] = []
    for item in gfsec_robot_curve_payload_rows(payload):
        trade_date = (
            parse_flexible_date(item.get("date"))
            or parse_flexible_date(item.get("tradeDate"))
            or parse_flexible_date(item.get("busiDate"))
        )
        if trade_date:
            source_rows.append((trade_date, item))
    source_rows.sort(key=lambda value: value[0])

    rows: list[dict[str, Any]] = []
    previous_total: float | None = None
    for trade_date, item in source_rows:
        total_yield = next(
            (
                parse_float(item.get(key))
                for key in ("totalYield", "cumReturn", "cumulativeReturn", "modelYield", "yield")
                if parse_float(item.get(key)) is not None
            ),
            None,
        )
        if total_yield is None:
            continue
        baseline_yield = next(
            (
                parse_float(item.get(key))
                for key in ("baselineYield", "benchmarkYield", "indexYield")
                if parse_float(item.get(key)) is not None
            ),
            None,
        )
        daily_return = None
        if previous_total is not None and 1.0 + previous_total != 0:
            daily_return = (1.0 + total_yield) / (1.0 + previous_total) - 1.0
        rows.append(
            {
                "channel_id": channel_id,
                "source_strategy_id": strategy_id,
                "trade_date": trade_date,
                "nav": round(1.0 + total_yield, 8),
                "daily_return": pct_decimal_to_percent(daily_return),
                "cumulative_return": pct_decimal_to_percent(total_yield),
                "benchmark_return": pct_decimal_to_percent(baseline_yield),
                "index_return": None,
                "max_drawdown": None,
                "section_name": "官方累计收益曲线",
                "section_type": "official_strategy_model_yield",
                "source_snapshot_id": source_snapshot_id,
            }
        )
        previous_total = total_yield
    return rows


def gfsec_robot_curve_candidates(detail: dict[str, Any], end_date: str) -> list[dict[str, str]]:
    """Build official curve requests, preferring the URL disclosed by the strategy detail."""

    strategy_id = str(detail.get("id") or "").strip()
    if not strategy_id or strategy_id == "moneyfund":
        return []
    others = detail.get("others") if isinstance(detail.get("others"), dict) else {}
    performance = others.get("performance") if isinstance(others.get("performance"), dict) else {}
    start_date = (
        parse_flexible_date(others.get("createDate"))
        or parse_flexible_date(detail.get("createAt"))
        or "2016-06-20"
    )
    end_date = parse_flexible_date(end_date) or end_date
    candidates: list[dict[str, str]] = []

    template = str(performance.get("url") or "").strip()
    if template:
        rendered = template.replace("${startDate}", start_date).replace("${endDate}", end_date)
        official_url = urljoin("https://robot.gf.com.cn", rendered)
        parsed = urlparse(official_url)
        if parsed.hostname == "robot.gf.com.cn" and parsed.path.startswith("/api/robot/"):
            candidates.append({"kind": "detail_disclosed_url", "url": official_url})

    query = urlencode(
        {
            "strategyType": strategy_id,
            "modelIdentifier": strategy_id,
            "startDate": start_date,
            "endDate": end_date,
        }
    )
    production_query = urlencode(
        {
            "strategyType": strategy_id,
            "modelIdentifier": strategy_id,
            # The current Guangfa Securities 13.3.5 Android bundle sends compact
            # yyyyMMdd dates to the production (versionless) assetallocation route.
            "startDate": start_date.replace("-", ""),
            "endDate": end_date.replace("-", ""),
        }
    )
    candidates.append(
        {
            "kind": "production_apk_centerproxy_model_yield",
            "url": (
                "https://info.gf.com.cn/api/1.0.0/centerproxy/ytj/"
                f"assetallocation/strategy/model/yield?{production_query}"
            ),
        }
    )
    candidates.append(
        {
            "kind": "current_frontend_model_yield",
            "url": f"https://robot.gf.com.cn/api/robot/assetallocation/2.0.0/strategy/model/yield?{query}",
        }
    )
    legacy_query = urlencode(
        {
            "strategyType": strategy_id,
            "startDate": start_date,
            "endDate": end_date,
        }
    )
    candidates.append(
        {
            "kind": "legacy_model_yield_fallback",
            "url": f"https://robot.gf.com.cn/api/robot/assetallocation/1.0.0/strategy/model/yield?{legacy_query}",
        }
    )
    unique: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for candidate in candidates:
        if candidate["url"] not in seen_urls:
            seen_urls.add(candidate["url"])
            unique.append(candidate)
    return unique


def gfsec_robot_curve_disclosure_match(
    detail: dict[str, Any], rows: list[dict[str, Any]], *, require_exact_point: bool
) -> tuple[bool, str]:
    """Guard generated endpoints against assigning a shared model curve to the wrong strategy."""

    others = detail.get("others") if isinstance(detail.get("others"), dict) else {}
    performance = others.get("performance") if isinstance(others.get("performance"), dict) else {}
    disclosed_date = parse_flexible_date(performance.get("busiDate"))
    disclosed_return = pct_decimal_to_percent(performance.get("yield"))
    if disclosed_date is None or disclosed_return is None:
        return (not require_exact_point, "detail_has_no_comparable_disclosure")
    exact = next((row for row in rows if row.get("trade_date") == disclosed_date), None)
    if exact is None:
        return (not require_exact_point, f"curve_has_no_point_on_{disclosed_date}")
    curve_return = parse_float(exact.get("cumulative_return"))
    if curve_return is None:
        return False, f"curve_point_on_{disclosed_date}_has_no_return"
    difference = abs(curve_return - disclosed_return)
    if difference > 0.1:
        return False, f"curve_disclosure_difference_pct={difference:.6f}"
    return True, "matched_detail_disclosure"


def strip_html(value: str) -> str:
    text = re.sub(r"(?is)<script.*?</script>", " ", value)
    text = re.sub(r"(?is)<style.*?</style>", " ", text)
    text = re.sub(r"(?is)<br\s*/?>", "\n", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def safe_name(value: str) -> str:
    text = re.sub(r'[\\/:*?"<>|]+', "_", value).strip()
    return text[:80] or "unnamed"


def stable_id(prefix: str, value: str) -> str:
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


def parse_title(text: str) -> str | None:
    match = re.search(r"(?is)<title>(.*?)</title>", text)
    return strip_html(match.group(1)) if match else None


def parse_date_yyyymmdd(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", text):
        y, m, d = text.split("-")
        return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
    if re.fullmatch(r"\d{8}", text):
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return text


def parse_millis_date(value: Any) -> str | None:
    parsed = parse_float(value)
    if parsed is None:
        return None
    return datetime.fromtimestamp(parsed / 1000, timezone.utc).date().isoformat()


def parse_flexible_date(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        if number > 10_000_000_000:
            return parse_millis_date(number)
        if number > 1_000_000_000:
            return datetime.fromtimestamp(number, timezone.utc).date().isoformat()
    text = str(value).strip()
    match = re.match(r"^(\d{4})[-/]?(\d{2})[-/]?(\d{2})", text)
    if match:
        return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
    return None


def object_rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    if isinstance(value, dict):
        return [value]
    return []


def catalog_page_closed(
    payload: dict[str, Any],
    rows: list[dict[str, Any]],
    requested_size: int,
) -> bool:
    """Return true only when a one-page catalog response proves exhaustion."""

    for key in ("total", "totalCount", "totalElements", "recordTotal"):
        raw_total = payload.get(key)
        if raw_total in (None, ""):
            continue
        try:
            return len(rows) >= int(raw_total)
        except (TypeError, ValueError):
            return False
    return len(rows) < requested_size


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


class OfficialAppsPublicCollector:
    def __init__(
        self,
        project_root: Path,
        *,
        run_id: str | None = None,
        harvest_pages: int | None = None,
        workers: int = 8,
        zocaifu_limit: int | None = None,
        zocaifu_skip_fund_nav: bool = False,
        gffunds_limit: int | None = None,
        gffunds_skip_fund_nav: bool = False,
        gffunds_skip_protocol_pdf: bool = False,
        gffunds_latest_adjustment_refresh_days: int = 1,
        gfsec_fima_daily_page_size: int = 200,
    ) -> None:
        self.project_root = project_root
        self.run_at = now_local()
        self.day = self.run_at.strftime("%Y-%m-%d")
        self.run_id = run_id or self.run_at.strftime("%Y%m%dT%H%M%S%z")
        self.captured_at = self.run_at.isoformat(timespec="seconds")
        self.harvest_pages = harvest_pages
        self.workers = workers
        self.zocaifu_limit = zocaifu_limit
        self.zocaifu_skip_fund_nav = zocaifu_skip_fund_nav
        self.gffunds_limit = gffunds_limit
        self.gffunds_skip_fund_nav = gffunds_skip_fund_nav
        self.gffunds_skip_protocol_pdf = gffunds_skip_protocol_pdf
        self.gffunds_latest_adjustment_refresh_days = max(
            0,
            int(gffunds_latest_adjustment_refresh_days),
        )
        self.gfsec_fima_daily_page_size = max(1, int(gfsec_fima_daily_page_size))
        self.snapshots: dict[str, list[dict[str, Any]]] = {}

    def collect(self, apps: list[str] | None = None) -> dict[str, Any]:
        selected = apps or [
            "huaxia_tougu",
            "zocaifu",
            "harvestwm",
            "southern",
            "cmfchina",
            "efundcf",
            "gffunds",
            "gfsec_fima",
            "gfsec_robot",
            "gfbank_cgb",
            "fullgoal",
            "fund99",
            "qieman",
        ]
        results: dict[str, Any] = {}
        for app in selected:
            if app == "huaxia_tougu":
                results[app] = self.collect_huaxia()
            elif app == "zocaifu":
                results[app] = self.collect_zocaifu()
            elif app == "efundcf":
                results[app] = self.collect_efundcf()
            elif app == "gffunds":
                results[app] = self.collect_gffunds()
            elif app == "gfsec_fima":
                results[app] = self.collect_gfsec_fima()
            elif app == "gfsec_robot":
                results[app] = self.collect_gfsec_robot()
            elif app == "gfbank_cgb":
                results[app] = self.collect_gfbank_cgb()
            elif app == "fullgoal":
                results[app] = self.collect_fullgoal()
            elif app == "fund99":
                results[app] = self.collect_fund99()
            elif app == "qieman":
                results[app] = self.collect_qieman()
            elif app == "harvestwm":
                results[app] = self.collect_harvestwm()
            elif app == "southern":
                results[app] = self.collect_southern()
            elif app == "cmfchina":
                results[app] = self.collect_cmfchina()
            else:
                raise ValueError(f"unknown app: {app}")
        self.write_overall_summary(results)
        return results

    def fetch(
        self,
        channel_id: str,
        collector_name: str,
        url: str,
        raw_relative_path: str | Path,
        *,
        method: str = "GET",
        data: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: int = 45,
        parse_json: bool = False,
        encoding_hint: str | None = None,
    ) -> RawResponse:
        raw_dir = self.project_root / "data" / "raw" / channel_id / collector_name / self.day / self.run_id
        raw_path = raw_dir / raw_relative_path
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        request_headers = {
            "User-Agent": USER_AGENT,
            "Accept": "*/*",
            "Accept-Encoding": "identity",
        }
        if headers:
            request_headers.update(headers)

        body: bytes | None = None
        if data is not None:
            body = urlencode(data).encode("utf-8")
            request_headers.setdefault(
                "Content-Type", "application/x-www-form-urlencoded; charset=UTF-8"
            )

        status: int | None = None
        content_type: str | None = None
        final_url = url
        raw_bytes = b""
        parse_status = "success"
        for attempt in range(1, 4):
            try:
                parse_status = "success"
                request = Request(url, data=body, method=method, headers=request_headers)
                with urlopen(request, timeout=timeout) as response:
                    status = response.status
                    content_type = response.headers.get("Content-Type")
                    final_url = response.geturl()
                    try:
                        raw_bytes = response.read()
                    except IncompleteRead as error:
                        raw_bytes = error.partial
                        parse_status = "partial"
                        if attempt < 3:
                            time.sleep(1.0 * attempt)
                            continue
                if parse_json:
                    try:
                        json.loads(self.decode(raw_bytes, content_type, encoding_hint))
                    except json.JSONDecodeError:
                        parse_status = "failed"
                        if attempt < 3:
                            time.sleep(1.0 * attempt)
                            continue
                break
            except HTTPError as error:
                raw_bytes = error.read()
                status = error.code
                content_type = error.headers.get("Content-Type") if error.headers else None
                final_url = error.url or url
                parse_status = "failed"
                break
            except (TimeoutError, URLError, OSError) as error:
                if attempt == 3:
                    raw_bytes = json.dumps(
                        {"transport_error": str(error), "url": url, "method": method},
                        ensure_ascii=False,
                    ).encode("utf-8")
                    parse_status = "failed"
                else:
                    time.sleep(1.0 * attempt)

        raw_path.write_bytes(raw_bytes)
        text = self.decode(raw_bytes, content_type, encoding_hint)
        json_data = None
        if parse_json:
            try:
                json_data = json.loads(text)
            except json.JSONDecodeError:
                parse_status = "failed"
        content_hash = hashlib.sha256(raw_bytes).hexdigest()
        snapshot = RawSnapshot(
            snapshot_id=f"{channel_id}-{collector_name}-{content_hash[:16]}",
            channel_id=channel_id,
            collector_name=collector_name,
            access_level="public",
            captured_at=self.captured_at,
            source_url=final_url,
            http_status=status,
            raw_path=str(raw_path),
            content_type=content_type,
            content_hash=content_hash,
            parse_status=parse_status,
        ).to_dict()
        self.snapshots.setdefault(channel_id, []).append(snapshot)
        return RawResponse(raw_path, snapshot, text, json_data, final_url)

    @staticmethod
    def decode(raw_bytes: bytes, content_type: str | None, encoding_hint: str | None) -> str:
        candidates: list[str] = []
        if encoding_hint:
            candidates.append(encoding_hint)
        if content_type:
            match = re.search(r"charset=([A-Za-z0-9_-]+)", content_type, flags=re.I)
            if match:
                candidates.append(match.group(1))
        candidates.extend(["utf-8", "gb18030", "gbk"])
        seen: set[str] = set()
        for encoding in candidates:
            key = encoding.lower()
            if key in seen:
                continue
            seen.add(key)
            try:
                return raw_bytes.decode(encoding)
            except UnicodeDecodeError:
                continue
        return raw_bytes.decode("utf-8", errors="replace")

    def write_entity(self, channel_id: str, entity: str, rows: list[dict[str, Any]]) -> Path:
        output_path = (
            self.project_root
            / "data"
            / "normalized"
            / channel_id
            / entity
            / self.day
            / f"{self.run_id}.jsonl"
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        return output_path

    def write_app_outputs(
        self,
        channel_id: str,
        normalized: dict[str, list[dict[str, Any]]],
        summary: dict[str, Any],
        inventory: dict[str, Any],
    ) -> dict[str, str]:
        app_dir = self.project_root / "official_apps" / channel_id / "outputs"
        app_dir.mkdir(parents=True, exist_ok=True)
        output_paths: dict[str, str] = {}
        for entity in DEFAULT_ENTITIES:
            rows = normalized.get(entity, [])
            jsonl_path = app_dir / f"{entity}.jsonl"
            csv_path = app_dir / f"{entity}.csv"
            with jsonl_path.open("w", encoding="utf-8") as fh:
                for row in rows:
                    fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            self.write_csv(csv_path, rows)
            output_paths[f"{entity}_jsonl"] = str(jsonl_path)
            output_paths[f"{entity}_csv"] = str(csv_path)

        coverage = self.build_coverage(channel_id, normalized, summary)
        summary_path = app_dir / "latest_summary.json"
        coverage_path = app_dir / "coverage_check.json"
        inventory_path = app_dir / "source_inventory.json"
        manifest_path = app_dir / "raw_manifest.json"
        normalized_summary_path = (
            self.project_root
            / "data"
            / "normalized"
            / channel_id
            / "collection_summary"
            / self.day
            / f"{summary.get('run_id', self.run_id)}.json"
        )
        normalized_summary_path.parent.mkdir(parents=True, exist_ok=True)
        exact_coverage_path = normalized_summary_path.with_name(
            normalized_summary_path.stem + ".coverage.json"
        )
        exact_inventory_path = normalized_summary_path.with_name(
            normalized_summary_path.stem + ".inventory.json"
        )
        output_paths.update(
            {
                "summary": str(summary_path),
                "coverage": str(coverage_path),
                "inventory": str(inventory_path),
                "raw_manifest": str(manifest_path),
                "normalized_summary": str(normalized_summary_path),
                "exact_coverage": str(exact_coverage_path),
                "exact_inventory": str(exact_inventory_path),
            }
        )
        summary["output_paths"] = dict(output_paths)
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        normalized_summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        coverage_path.write_text(json.dumps(coverage, ensure_ascii=False, indent=2), encoding="utf-8")
        exact_coverage_path.write_text(json.dumps(coverage, ensure_ascii=False, indent=2), encoding="utf-8")
        inventory_path.write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8")
        exact_inventory_path.write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8")
        manifest_path.write_text(
            json.dumps({"raw_snapshots": self.snapshots.get(channel_id, [])}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return output_paths

    @staticmethod
    def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
        if not rows:
            path.write_text("", encoding="utf-8")
            return
        fieldnames: list[str] = []
        for row in rows:
            for key in row:
                if key not in fieldnames:
                    fieldnames.append(key)
        with path.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow(
                    {
                        key: compact_json(value) if isinstance(value, (dict, list)) else value
                        for key, value in row.items()
                    }
                )

    def build_coverage(
        self,
        channel_id: str,
        normalized: dict[str, list[dict[str, Any]]],
        summary: dict[str, Any],
    ) -> dict[str, Any]:
        counts = {entity: len(normalized.get(entity, [])) for entity in DEFAULT_ENTITIES}
        def ok_flag(name: str, fallback: bool) -> bool:
            value = summary.get(name)
            return fallback if value is None else bool(value)

        return {
            "channel_id": channel_id,
            "run_id": summary.get("run_id", self.run_id),
            "captured_at": self.captured_at,
            "entity_counts": counts,
            "strategy_master_ok": ok_flag("strategy_master_ok", counts["strategy_master"] > 0),
            "daily_performance_ok": ok_flag("daily_performance_ok", counts["strategy_performance_daily"] > 0),
            "fund_level_position_ok": ok_flag("fund_level_position_ok", counts["strategy_fund_snapshot"] > 0),
            "recommendation_fund_list_ok": bool(summary.get("recommendation_fund_list_ok", False)),
            "rebalance_event_ok": ok_flag("rebalance_event_ok", counts["strategy_rebalance_event"] > 0),
            "rebalance_fund_delta_ok": ok_flag("rebalance_fund_delta_ok", counts["strategy_rebalance_fund_delta"] > 0),
            "holding_penetration_status": summary.get("holding_penetration_status"),
            "collection_status": summary.get("collection_status"),
            "known_gap": summary.get("known_gap"),
        }

    def read_normalized_run_rows(self, channel_id: str, entity: str, run_id: str) -> list[dict[str, Any]]:
        root = self.project_root / "data" / "normalized" / channel_id / entity
        paths = sorted(root.glob(f"*/*{run_id}.jsonl"))
        if not paths:
            return []
        rows: list[dict[str, Any]] = []
        with paths[-1].open("r", encoding="utf-8") as fh:
            for line in fh:
                text = line.strip()
                if text:
                    rows.append(json.loads(text))
        return rows

    def write_normalized_entities(self, channel_id: str, normalized: dict[str, list[dict[str, Any]]]) -> None:
        for entity in DEFAULT_ENTITIES:
            self.write_entity(channel_id, entity, normalized.get(entity, []))

    @staticmethod
    def attr_value(tag: str, attr: str) -> str | None:
        match = re.search(rf"{attr}\s*=\s*(['\"])(.*?)\1", tag, flags=re.I | re.S)
        return html.unescape(match.group(2)).strip() if match else None

    @staticmethod
    def extract_section(text: str, start: str, *ends: str) -> str | None:
        pattern = re.escape(start) + r"\s*(.*?)\s*(?:" + "|".join(re.escape(end) for end in ends) + r"|$)"
        match = re.search(pattern, text, flags=re.S)
        return re.sub(r"\s+", " ", match.group(1)).strip(" ：:") if match else None

    @staticmethod
    def risk_level_from_text(text: str | None) -> str | None:
        if not text:
            return None
        match = re.search(
            r"风险等级(?:为|是)?\s*([Rr]\d(?:-\d)?(?:[—-][^，。；;]+)?(?:[，,][^，。；;]*风险)?)",
            text,
        )
        if not match:
            match = re.search(r"风险等级(?:为|是)?\s*([^，。；;]+)", text)
        return match.group(1).strip() if match else None

    @staticmethod
    def fee_rate_from_text(text: str | None) -> str | None:
        if not text:
            return None
        match = re.search(r"(\d+(?:\.\d+)?\s*%/?年)", text)
        return match.group(1).replace(" ", "") if match else None

    @staticmethod
    def asset_range_text(scope: str | None) -> list[str]:
        if not scope:
            return []
        ranges: list[str] = []
        for match in re.finditer(
            r"((?:权益类基金|固定收益类基金|货币基金类仓位|货币基金)[^。；;]*?\d+(?:\.\d+)?%-\d+(?:\.\d+)?%[^。；;]*)",
            scope,
        ):
            text = re.sub(r"\s+", " ", match.group(1)).strip()
            if text and text not in ranges:
                ranges.append(text)
        return ranges

    def collect_huaxia(self) -> dict[str, Any]:
        channel_id = "huaxia_tougu"
        channel_name = "华夏投顾/华夏财富查理智投"
        fund_list = self.fetch(
            channel_id,
            "public_site",
            "https://www.amcfortune.com/superfund/fundList.shtml",
            "fundList.shtml",
            encoding_hint="gb18030",
        )
        questionnaire = self.fetch(
            channel_id,
            "public_api",
            "https://www.amcfortune.com/hxcf/cf/kyc/questionnaire",
            "kyc_questionnaire.json",
            parse_json=True,
        )
        star = self.fetch(
            channel_id,
            "public_api",
            "https://www.amcfortune.com/hxcf/cf/queryStarProduct",
            "queryStarProduct.json",
            parse_json=True,
        )

        list_items = self.parse_huaxia_fund_list(fund_list.text)
        details: dict[str, dict[str, Any]] = {}
        nav_rows: list[dict[str, Any]] = []
        snapshots: list[dict[str, Any]] = []
        events: list[dict[str, Any]] = []
        deltas: list[dict[str, Any]] = []
        fund_dim: dict[str, dict[str, Any]] = {}

        for item in list_items:
            code = item["source_strategy_id"]
            detail_url = f"https://www.amcfortune.com/funds/superfund/{code}/index.shtml"
            detail_response = self.fetch(
                channel_id,
                "strategy_detail",
                detail_url,
                Path("details") / code / "index.shtml",
                encoding_hint="gb18030",
            )
            detail = self.parse_huaxia_detail(detail_response.text, code)
            merged = {**item, **detail, "detail_source_snapshot_id": detail_response.snapshot["snapshot_id"]}
            details[code] = merged

            strategy_nav_rows = self.collect_huaxia_nav(code)
            nav_rows.extend(
                [
                    {
                        "channel_id": channel_id,
                        "source_strategy_id": code,
                        "trade_date": row.get("navActualDate"),
                        "nav": None,
                        "daily_return": parse_float(row.get("dayGains")),
                        "cumulative_return": parse_float(row.get("gains")),
                        "benchmark_return": None,
                        "index_return": None,
                        "max_drawdown": None,
                        "source_snapshot_id": row.get("_snapshot_id"),
                        "run_id": self.run_id,
                    }
                    for row in strategy_nav_rows
                ]
            )

            strategy_events, strategy_deltas, latest_rows = self.collect_huaxia_adjustments(
                code,
                merged.get("strategy_name") or code,
                detail.get("version_ids") or [],
            )
            events.extend(strategy_events)
            deltas.extend(strategy_deltas)
            if latest_rows:
                snapshot_id = f"{channel_id}-{code}-holding-{latest_rows[0].get('position_date')}-{self.run_id}"
                for row in latest_rows:
                    fund_code = row.get("fund_code")
                    snapshots.append({**row, "snapshot_id": snapshot_id})
                    if fund_code:
                        fund_dim[fund_code] = {
                            "fund_code": fund_code,
                            "fund_name": row.get("fund_name"),
                            "fund_company": None,
                            "fund_type": None,
                            "tracking_index": None,
                            "theme_tags": None,
                            "latest_nav": None,
                            "latest_nav_date": None,
                            "status": None,
                            "source": "huaxia_tougu_public_sfAdjustVersion",
                            "updated_at": self.captured_at,
                            "run_id": self.run_id,
                        }

        strategy_master = [
            self.huxia_strategy_master_row(channel_id, channel_name, item)
            for item in details.values()
        ]
        app_entry = [
            {
                "channel_id": channel_id,
                "channel_name": channel_name,
                "source_url": "https://www.amcfortune.com/superfund/fundList.shtml",
                "title": parse_title(fund_list.text),
                "run_id": self.run_id,
                "captured_at": self.captured_at,
                "strategy_total": len(strategy_master),
                "star_product": (star.json_data or {}).get("superFundIncome") if isinstance(star.json_data, dict) else None,
                "kyc_questionnaire": (questionnaire.json_data or {}).get("content") if isinstance(questionnaire.json_data, dict) else None,
                "available_entities": [
                    "strategy_master",
                    "strategy_performance_daily",
                    "strategy_fund_snapshot",
                    "strategy_rebalance_event",
                    "strategy_rebalance_fund_delta",
                ],
            }
        ]
        normalized = {
            "strategy_master": strategy_master,
            "strategy_performance_daily": nav_rows,
            "strategy_fund_snapshot": snapshots,
            "strategy_rebalance_event": events,
            "strategy_rebalance_fund_delta": deltas,
            "fund_public_dim": list(fund_dim.values()),
            "app_public_entry": app_entry,
            "strategy_disclosure_event": [],
        }
        for entity, rows in normalized.items():
            self.write_entity(channel_id, entity, rows)
        summary = {
            "channel_id": channel_id,
            "channel_name": channel_name,
            "run_id": self.run_id,
            "captured_at": self.captured_at,
            "collection_status": "success",
            "holding_penetration_status": "fund_weight_exact_public",
            "strategy_total": len(strategy_master),
            "daily_rows_total": len(nav_rows),
            "current_holding_rows": len(snapshots),
            "rebalance_event_total": len(events),
            "rebalance_fund_delta_total": len(deltas),
            "fund_dim_total": len(fund_dim),
            "known_gap": "公开页面给出调仓前后基金代码、基金名称和权重；未见单独的组合净值字段，历史接口返回累计收益和日涨跌幅。",
        }
        inventory = {
            "primary_sources": [
                "https://www.amcfortune.com/superfund/fundList.shtml",
                "https://www.amcfortune.com/funds/superfund/{source_strategy_id}/index.shtml",
                "https://www.amcfortune.com/hxcf/cf/sfAdjustVersion",
                "https://www.amcfortune.com/hxcf/cf/sfNavPage",
            ],
            "method": "解析公开 HTML 内嵌策略列表和版本 ID；POST sfAdjustVersion 获取每次调仓的 fundCode/fundName/beforeRate/afterRate；POST sfNavPage 获取历史涨跌幅。",
            "raw_snapshots": self.snapshots.get(channel_id, []),
        }
        summary["output_paths"] = self.write_app_outputs(channel_id, normalized, summary, inventory)
        return summary

    def parse_huaxia_fund_list(self, text: str) -> list[dict[str, Any]]:
        by_code: dict[str, dict[str, Any]] = {}
        for block in re.findall(r"(?is)<tr\s+class\s*=\s*['\"]superFundShow['\"][^>]*>(.*?)</tr>", text):
            click = re.search(
                r"clSmartPageClick\('(?P<code>SF[^']*)','(?P<name>[^']*)','(?P<series>[^']*)','(?P<display>[^']*)'\)",
                block,
            )
            link = re.search(r"gotoSuperFundDetail\('(?P<code>SF[^']*)'\).*?>(?P<name>.*?)</a>", block, re.S)
            if not click and not link:
                continue
            code = (click or link).group("code")
            name = strip_html((click.group("name") if click else link.group("name")) or code)
            series = strip_html(click.group("series")) if click else None
            desc_match = re.search(r"</a>\s*</div>\s*<span>(?P<desc>.*?)</span>", block, re.S)
            target_match = re.search(r"<div class=\"h5\">\s*(?P<target>.*?)\s*</div>\s*<span>投资目标", block, re.S)
            return_match = re.search(r"<h3 class=\"h3[^\"]*\">\s*(?P<value>.*?)\s*</h3>\s*<span>(?P<label>.*?)</span>", block, re.S)
            status_match = re.search(r"<h3 class=\"h5\">\s*(?P<status>.*?)\s*</h3>\s*<span>交易状态", block, re.S)
            by_code[code] = {
                "channel_id": "huaxia_tougu",
                "source_strategy_id": code,
                "strategy_name": name,
                "strategy_type": series,
                "strategy_description": strip_html(desc_match.group("desc")) if desc_match else None,
                "risk_level": strip_html(target_match.group("target")) if target_match else None,
                "status": strip_html(status_match.group("status")) if status_match else None,
                "list_return": parse_float(return_match.group("value")) if return_match else None,
                "list_return_label": strip_html(return_match.group("label")) if return_match else None,
            }
        for match in re.finditer(r"gotoSuperFundDetail\('(?P<code>SF[^']*)'\).*?>(?P<name>.*?)</a>", text, re.S):
            code = match.group("code")
            by_code.setdefault(
                code,
                {
                    "channel_id": "huaxia_tougu",
                    "source_strategy_id": code,
                    "strategy_name": strip_html(match.group("name")),
                    "strategy_type": None,
                    "strategy_description": None,
                    "risk_level": None,
                    "status": None,
                    "list_return": None,
                    "list_return_label": None,
                },
            )
        return list(by_code.values())

    def parse_huaxia_detail(self, text: str, code: str) -> dict[str, Any]:
        title = parse_title(text)
        detail: dict[str, Any] = {
            "strategy_name": title,
            "source_url": f"https://www.amcfortune.com/funds/superfund/{code}/index.shtml",
        }
        for item in re.findall(r"(?is)<div class=\"li\">(.*?)</div>", text):
            item_text = strip_html(item)
            if "：" not in item_text:
                continue
            key, value = item_text.split("：", 1)
            key = key.strip()
            value = value.strip()
            if key == "组合代码":
                detail["source_strategy_id"] = value
            elif key == "成立时间":
                detail["launch_date"] = parse_date_yyyymmdd(value)
            elif key == "风险等级":
                detail["risk_level"] = value
            elif key == "适合人群":
                detail["suitable_investor"] = value
            elif key == "起投金额":
                detail["minimum_amount_text"] = value
                amount = re.search(r"([0-9][0-9,]*(?:\.\d+)?)", value)
                detail["minimum_amount"] = parse_float(amount.group(1)) if amount else None
            elif key == "赎回时间":
                detail["redeem_time"] = value
            elif key == "申购费用":
                detail["purchase_fee"] = value
            elif key == "分红方式":
                detail["dividend_method"] = value
        for label, field in [
            ("日涨跌幅", "latest_daily_return_text"),
            ("成立以来年化", "annualized_return_since_launch"),
            ("近一年最大回撤率", "one_year_max_drawdown"),
        ]:
            pattern = rf"(?is)<div class=\"h3[^>]*>\s*(.*?)\s*</div>\s*<div class=\"p\">{label}</div>"
            match = re.search(pattern, text)
            if match:
                detail[field] = parse_float(match.group(1))
        date_match = re.search(r'<span class="time">\((\d{4}-\d{2}-\d{2})\)</span>', text)
        if date_match:
            detail["latest_daily_return_date"] = date_match.group(1)
        for label, field in [
            ("投资目标", "investment_objective"),
            ("适合人群", "suitable_people_description"),
            ("组合策略", "strategy_description"),
            ("费用结构", "fee_description"),
        ]:
            match = re.search(
                rf"(?is)<div class=\"h3\">{label}</div>\s*<div class=\"p\">(?P<value>.*?)</div>",
                text,
            )
            if match:
                detail[field] = strip_html(match.group("value"))
        fee_match = re.search(r"([0-9.]+%（年化）)", detail.get("fee_description") or "")
        if fee_match:
            detail["advisory_fee_rate"] = fee_match.group(1)
        allocation = []
        alloc_block = re.search(r"(?is)<th>大类资产名称</th>\s*<th>资产占比</th>(?P<body>.*?)</table>", text)
        if alloc_block:
            for asset, weight in re.findall(r"(?is)<tr>\s*<td>(.*?)</td>\s*<td>(.*?)</td>\s*</tr>", alloc_block.group("body")):
                allocation.append({"asset_type": strip_html(asset), "weight": parse_float(weight)})
        detail["asset_allocation"] = allocation
        version_match = re.search(r"var\s+versionIds\s*=\s*\[(?P<body>.*?)\];", text, re.S)
        if version_match:
            detail["version_ids"] = re.findall(r'"([^"]+)"', version_match.group("body"))
        else:
            detail["version_ids"] = []
        return detail

    def huxia_strategy_master_row(
        self,
        channel_id: str,
        channel_name: str,
        item: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "channel_id": channel_id,
            "source_strategy_id": item["source_strategy_id"],
            "strategy_name": item.get("strategy_name"),
            "advisor_name": channel_name,
            "strategy_type": item.get("strategy_type"),
            "risk_level": item.get("risk_level"),
            "launch_date": item.get("launch_date"),
            "suggested_holding_period": None,
            "minimum_amount": item.get("minimum_amount"),
            "advisory_fee_rate": item.get("advisory_fee_rate"),
            "benchmark": None,
            "tags": [
                tag
                for tag in [
                    "查理智投",
                    item.get("strategy_type"),
                    item.get("suitable_investor"),
                ]
                if tag
            ],
            "strategy_description": item.get("strategy_description"),
            "status": item.get("status"),
            "source_url": item.get("source_url"),
            "first_seen_at": self.captured_at,
            "last_seen_at": self.captured_at,
            "run_id": self.run_id,
            "source_snapshot_id": item.get("detail_source_snapshot_id"),
            "extra": {
                "investment_objective": item.get("investment_objective"),
                "suitable_people_description": item.get("suitable_people_description"),
                "asset_allocation": item.get("asset_allocation"),
                "annualized_return_since_launch": item.get("annualized_return_since_launch"),
                "one_year_max_drawdown": item.get("one_year_max_drawdown"),
                "latest_daily_return_text": item.get("latest_daily_return_text"),
                "latest_daily_return_date": item.get("latest_daily_return_date"),
                "minimum_amount_text": item.get("minimum_amount_text"),
                "purchase_fee": item.get("purchase_fee"),
                "redeem_time": item.get("redeem_time"),
                "dividend_method": item.get("dividend_method"),
                "version_total": len(item.get("version_ids") or []),
            },
        }

    def collect_huaxia_nav(self, code: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        page_index = 1
        page_size = 1000
        while True:
            response = self.fetch(
                "huaxia_tougu",
                "strategy_nav",
                "https://www.amcfortune.com/hxcf/cf/sfNavPage",
                Path("nav") / code / f"page_{page_index:04d}.json",
                method="POST",
                data={"fundCode": code, "pageIndex": page_index, "pageSize": page_size},
                parse_json=True,
            )
            payload = response.json_data if isinstance(response.json_data, dict) else {}
            nav_list = payload.get("navList") or {}
            page_rows = nav_list.get("records") or []
            for row in page_rows:
                if isinstance(row, dict):
                    row["_snapshot_id"] = response.snapshot["snapshot_id"]
            rows.extend(page_rows)
            pagination = nav_list.get("pagination") or {}
            record_count = int(pagination.get("recordCount") or len(rows))
            if not page_rows or len(rows) >= record_count:
                break
            page_index += 1
        return rows

    def collect_huaxia_adjustments(
        self,
        code: str,
        strategy_name: str,
        version_ids: list[str],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        events: list[dict[str, Any]] = []
        deltas: list[dict[str, Any]] = []
        latest_snapshot_rows: list[dict[str, Any]] = []
        for index, version_id in enumerate(version_ids):
            response = self.fetch(
                "huaxia_tougu",
                "strategy_adjustment",
                "https://www.amcfortune.com/hxcf/cf/sfAdjustVersion",
                Path("adjustments") / code / f"{index + 1:04d}_{version_id}.json",
                method="POST",
                data={"versionId": version_id},
                parse_json=True,
            )
            payload = response.json_data if isinstance(response.json_data, dict) else {}
            detail = payload.get("versionDetail") or {}
            if not detail:
                continue
            rebalance_date = parse_millis_date(detail.get("effectDate"))
            event_id = f"huaxia_tougu-{code}-{rebalance_date or index}-{version_id[:12]}"
            events.append(
                {
                    "rebalance_event_id": event_id,
                    "channel_id": "huaxia_tougu",
                    "source_strategy_id": code,
                    "rebalance_date": rebalance_date,
                    "previous_position_date": None,
                    "new_position_date": rebalance_date,
                    "disclosure_date": rebalance_date,
                    "event_title": f"{strategy_name} 调仓",
                    "event_reason": None,
                    "source_url": "https://www.amcfortune.com/hxcf/cf/sfAdjustVersion",
                    "source_snapshot_id": response.snapshot["snapshot_id"],
                    "confidence_level": "official_exact",
                    "run_id": self.run_id,
                    "version_id": version_id,
                }
            )
            item_rows = detail.get("itemVersionList") or []
            for fund in item_rows:
                before_weight = pct_decimal_to_percent(fund.get("beforeRate"))
                after_weight = pct_decimal_to_percent(fund.get("afterRate"))
                deltas.append(
                    {
                        "rebalance_event_id": event_id,
                        "fund_code": fund.get("fundCode"),
                        "fund_name": fund.get("fundName"),
                        "before_weight": before_weight,
                        "after_weight": after_weight,
                        "weight_delta": None
                        if before_weight is None or after_weight is None
                        else round(after_weight - before_weight, 6),
                        "action_type": classify_action(before_weight, after_weight),
                        "run_id": self.run_id,
                        "fund_code_resolve_status": "official",
                    }
                )
            if index == len(version_ids) - 1:
                latest_snapshot_rows = [
                    {
                        "channel_id": "huaxia_tougu",
                        "source_strategy_id": code,
                        "position_date": rebalance_date,
                        "disclosure_date": rebalance_date,
                        "fund_code": fund.get("fundCode"),
                        "fund_name": fund.get("fundName"),
                        "fund_asset_type": None,
                        "fund_group_name": None,
                        "fund_weight": pct_decimal_to_percent(fund.get("afterRate")),
                        "fund_nav": None,
                        "fund_nav_date": None,
                        "is_precise_weight": fund.get("afterRate") is not None,
                        "is_login_required": False,
                        "source_url": "https://www.amcfortune.com/hxcf/cf/sfAdjustVersion",
                        "raw_record_hash": hashlib.sha256(compact_json(fund).encode("utf-8")).hexdigest(),
                        "confidence_level": "official_exact",
                        "access_level": "public",
                        "source_snapshot_id": response.snapshot["snapshot_id"],
                        "run_id": self.run_id,
                        "version_id": version_id,
                    }
                    for fund in item_rows
                    if pct_decimal_to_percent(fund.get("afterRate")) not in (None, 0.0)
                ]
        return events, deltas, latest_snapshot_rows

    def collect_zocaifu(self) -> dict[str, Any]:
        channel_id = "zocaifu"
        summary = collect_zocaifu_public(
            self.project_root,
            page_size=1000,
            max_workers=self.workers,
            limit=self.zocaifu_limit,
            collect_fund_nav=not self.zocaifu_skip_fund_nav,
        )
        z_run_id = summary["run_id"]
        z_day = datetime.fromisoformat(summary["captured_at"]).strftime("%Y-%m-%d")
        normalized: dict[str, list[dict[str, Any]]] = {}
        for entity in DEFAULT_ENTITIES:
            path = self.project_root / "data" / "normalized" / channel_id / entity / z_day / f"{z_run_id}.jsonl"
            normalized[entity] = self.read_jsonl(path) if path.exists() else []
        normalized["app_public_entry"] = [
            {
                "channel_id": channel_id,
                "channel_name": summary.get("channel_name"),
                "source_url": "https://mobile.qiangungun.com/v1/fof/queryAdPageStrategyInfo",
                "run_id": z_run_id,
                "captured_at": summary.get("captured_at"),
                "available_entities": [
                    "strategy_master",
                    "strategy_performance_daily",
                    "strategy_fund_snapshot",
                    "strategy_rebalance_event",
                    "strategy_rebalance_fund_delta",
                ],
            }
        ]
        summary = {
            **summary,
            "collection_status": "success",
            "holding_penetration_status": "fund_weight_exact_public",
            "known_gap": "公开 API 可获取基金级当前持仓和官方调仓明细；App 自动化仍可作为后续交叉验证。",
        }
        inventory = {
            "primary_sources": [
                "https://mobile.qiangungun.com/v1/fof/queryAdPageStrategyInfo",
                "https://mobile.qiangungun.com/v2/product/detail",
                "https://mobile.qiangungun.com/v1/product/queryFofRebalanceInfo",
                "https://mobile.qiangungun.com/v1/fof/listDailyRiseAndFall",
            ],
            "method": "复用项目已有中欧财富公共 API 采集器。",
            "raw_dir": summary.get("raw_dir"),
        }
        summary["output_paths"] = self.write_app_outputs(channel_id, normalized, summary, inventory)
        return summary

    @staticmethod
    def read_jsonl(path: Path) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        return rows

    def collect_efundcf(self) -> dict[str, Any]:
        channel_id = "efundcf"
        channel_name = "易方达财富/e钱包"
        source_url = "https://www.efundcf.com.cn/lm/tgfw/tgcl/"
        page = self.fetch(channel_id, "public_site", source_url, "tgcl.html")
        titles = [
            strip_html(item)
            for item in re.findall(
                r"(?is)<div[^>]+class=['\"][^'\"]*\btittle\b[^'\"]*['\"][^>]*>(.*?)</div>",
                page.text,
            )
        ]
        descriptions = [
            strip_html(item)
            for item in re.findall(
                r"(?is)<div[^>]+class=['\"][^'\"]*\bstrategy-item-desc\b[^'\"]*['\"][^>]*>(.*?)</div>",
                page.text,
            )
        ]
        plain = strip_html(page.text)
        fallback_pairs = [
            ("资产增值", "多元资产配置，力争长期回报"),
            ("现金管理", "闲钱理财，取用灵活"),
            ("养老规划", "为幸福养老提前储备"),
            ("子女教育", "为孩子的未来规划教育金"),
        ]
        if not titles:
            titles = [title for title, _ in fallback_pairs if title in plain]
            descriptions = [desc for title, desc in fallback_pairs if title in plain]

        strategy_master: list[dict[str, Any]] = []
        for index, title in enumerate(titles):
            if not title:
                continue
            description = descriptions[index] if index < len(descriptions) else None
            source_strategy_id = stable_id("efundcf", title)
            strategy_master.append(
                {
                    "channel_id": channel_id,
                    "source_strategy_id": source_strategy_id,
                    "strategy_name": title,
                    "advisor_name": channel_name,
                    "strategy_type": "投顾策略分类",
                    "risk_level": None,
                    "launch_date": None,
                    "suggested_holding_period": None,
                    "minimum_amount": None,
                    "advisory_fee_rate": None,
                    "benchmark": None,
                    "tags": ["易方达财富", "e钱包", "投顾策略"],
                    "status": "public_strategy_category",
                    "strategy_description": description,
                    "source_url": source_url,
                    "source_snapshot_id": page.snapshot["snapshot_id"],
                    "first_seen_at": self.captured_at,
                    "last_seen_at": self.captured_at,
                    "run_id": self.run_id,
                    "extra": {
                        "source_order": index,
                        "public_page_title": parse_title(page.text),
                    },
                }
            )

        normalized = {
            "strategy_master": strategy_master,
            "strategy_performance_daily": [],
            "strategy_fund_snapshot": [],
            "strategy_rebalance_event": [],
            "strategy_rebalance_fund_delta": [],
            "fund_public_dim": [],
            "app_public_entry": [
                {
                    "channel_id": channel_id,
                    "channel_name": channel_name,
                    "title": parse_title(page.text),
                    "source_url": source_url,
                    "run_id": self.run_id,
                    "captured_at": self.captured_at,
                    "strategy_total": len(strategy_master),
                    "available_entities": ["strategy_master", "app_public_entry"],
                    "missing_entities": [
                        "strategy_performance_daily",
                        "strategy_fund_snapshot",
                        "strategy_rebalance_event",
                        "strategy_rebalance_fund_delta",
                    ],
                    "login_hint": "公开官网仅展示投顾策略分类；具体投顾产品、基金仓位、业绩和调仓需进入 e 钱包/交易端。",
                }
            ],
            "strategy_disclosure_event": [],
        }
        self.write_normalized_entities(channel_id, normalized)
        summary = {
            "channel_id": channel_id,
            "channel_name": channel_name,
            "run_id": self.run_id,
            "captured_at": self.captured_at,
            "collection_status": "success_public_strategy_categories",
            "holding_penetration_status": "blocked_app_or_login_required",
            "strategy_total": len(strategy_master),
            "daily_rows_total": 0,
            "current_holding_rows": 0,
            "rebalance_event_total": 0,
            "rebalance_fund_delta_total": 0,
            "fund_dim_total": 0,
            "known_gap": "易方达财富公开页只披露策略分类入口，未公开基金级持仓、历史调仓和日度业绩。",
        }
        inventory = {
            "primary_sources": [source_url],
            "method": "抓取易方达财富投顾策略公开页，解析策略分类标题和说明；缺失项在 coverage_check 中标记。",
            "raw_snapshots": self.snapshots.get(channel_id, []),
        }
        summary["output_paths"] = self.write_app_outputs(channel_id, normalized, summary, inventory)
        return summary

    def collect_gffunds(self) -> dict[str, Any]:
        channel_id = "gffunds"
        strategy_id_file = find_latest_discovered_strategy_file(self.project_root)
        extra_strategy_ids = load_strategy_ids(strategy_id_file) if strategy_id_file else []
        extra_strategy_source = str(strategy_id_file) if strategy_id_file else None
        if not extra_strategy_ids:
            extra_strategy_ids = load_gffunds_strategy_ids_from_analysis_db(self.project_root)
            extra_strategy_source = "analysis_zh_current.sqlite:策略信息" if extra_strategy_ids else None
        summary = collect_gffunds_public(
            self.project_root,
            max_workers=self.workers,
            limit=self.gffunds_limit,
            collect_fund_nav=not self.gffunds_skip_fund_nav,
            collect_protocol_pdf=not self.gffunds_skip_protocol_pdf,
            extra_strategy_ids=extra_strategy_ids,
            run_id=self.run_id,
            latest_adjustment_refresh_days=self.gffunds_latest_adjustment_refresh_days,
        )
        summary["strategy_id_file"] = str(strategy_id_file) if strategy_id_file else None
        summary["extra_strategy_id_source"] = extra_strategy_source
        summary["extra_strategy_id_total"] = len(extra_strategy_ids)
        run_id = str(summary["run_id"])
        normalized: dict[str, list[dict[str, Any]]] = {
            entity: self.read_normalized_run_rows(channel_id, entity, run_id) for entity in DEFAULT_ENTITIES
        }
        normalized["app_public_entry"] = [
            {
                "channel_id": channel_id,
                "channel_name": summary.get("channel_name"),
                "source_url": "https://gfwx.gffunds.com.cn/html5app/invest-advisor",
                "run_id": run_id,
                "captured_at": summary.get("captured_at"),
                "strategy_total": summary.get("strategy_total"),
                "available_entities": [
                    "strategy_master",
                    "strategy_performance_daily",
                    "strategy_fund_snapshot",
                    "strategy_rebalance_event",
                    "strategy_rebalance_fund_delta",
                    "fund_public_dim",
                ],
                "missing_entities": [],
                "partial_entities": {
                    "strategy_performance_daily": "target_profit_issue_parent_curve_excluded",
                    "strategy_fund_snapshot": "target_profit_issue_holding_login_required",
                    "strategy_rebalance_event": "target_profit_issue_parent_rebalance_excluded",
                    "strategy_rebalance_fund_delta": "target_profit_issue_parent_rebalance_excluded",
                },
            }
        ]
        normalized["strategy_disclosure_event"] = []

        raw_manifest_path = Path(str(summary.get("raw_dir") or "")) / "_manifest.json"
        if raw_manifest_path.exists():
            try:
                raw_manifest = json.loads(raw_manifest_path.read_text(encoding="utf-8"))
                self.snapshots[channel_id] = raw_manifest.get("raw_snapshots") or []
            except json.JSONDecodeError:
                self.snapshots[channel_id] = []

        summary = {
            **summary,
            "collection_status": "success",
            "holding_penetration_status": (
                "core_strategy_fund_weight_exact_profit_issue_holding_login_required"
            ),
            "current_holding_rows": summary.get("latest_snapshot_rows_total")
            or summary.get("public_snapshot_rows", 0),
            "fund_dim_total": len(normalized.get("fund_public_dim", [])),
            "known_gap": (
                "普通 GFJJ 策略可由公开接口取得曲线、基金级当前仓位和调仓前后权重；"
                "目标盈 ZY 期次仅公开目录、基础配置和协议，期次自身详情与仓位要求登录。"
                "匿名曲线和调仓接口返回母策略数据，采集器明确排除，避免错记为期次业绩、仓位或调仓。"
            ),
        }
        inventory = {
            "primary_sources": [
                "https://gfwx.gffunds.com.cn/html5app/invest-advisor",
                "https://gfwx.gffunds.com.cn/mapi/get_invest_advisor_config",
                "https://gfwx.gffunds.com.cn/mapi/get_investadvisor_operate_config_list",
                "https://gfwx.gffunds.com.cn/mapi/get_profit_investadvisor_list",
                "https://gfwx.gffunds.com.cn/mapi/get_investadvisor_operate_config_byids",
                "https://gfwx.gffunds.com.cn/mapi/get_investadvisor_protocol_list",
                "https://gfwx.gffunds.com.cn/mapi/get_investadvisor_adjustment_record",
                "https://gfwx.gffunds.com.cn/mapi/get_investadvisor_yield_trend",
            ],
            "method": (
                "合并普通策略目录与目标盈期次目录；普通策略采集公开曲线、仓位和调仓，"
                "目标盈期次仅采集期次自身公开元数据与协议，并执行母策略数据血缘隔离。"
            ),
            "raw_dir": summary.get("raw_dir"),
            "raw_snapshots": self.snapshots.get(channel_id, []),
        }
        summary["output_paths"] = self.write_app_outputs(channel_id, normalized, summary, inventory)
        return summary

    def gfsec_fima_fetch(
        self,
        endpoint: str,
        params: dict[str, Any],
        raw_name: str,
    ) -> dict[str, Any]:
        base = "https://robot.gf.com.cn/api/robot"
        clean_params = {key: value for key, value in params.items() if value not in (None, "")}
        url = f"{base}/{endpoint.lstrip('/')}"
        if clean_params:
            url += "?" + urlencode(clean_params)
        response: RawResponse | None = None
        ok = False
        status = 0
        for outer_attempt in range(1, 3):
            attempt_raw_name = Path(raw_name)
            if outer_attempt > 1:
                attempt_raw_name = attempt_raw_name.with_name(
                    f"{attempt_raw_name.stem}_retry_{outer_attempt:02d}{attempt_raw_name.suffix}"
                )
            response = self.fetch(
                "gfsec_fima",
                "public_api",
                url,
                attempt_raw_name,
                parse_json=True,
                timeout=35,
            )
            status = int(response.snapshot.get("http_status") or 0)
            ok = status == 200 and response.snapshot.get("parse_status") in {"success", "partial"}
            if ok:
                break
        assert response is not None
        return {
            "ok": ok,
            "payload": response.json_data,
            "snapshot_id": response.snapshot.get("snapshot_id"),
            "source_url": response.final_url,
            "http_status": status,
            "error": None if ok else f"http_status={status}, parse_status={response.snapshot.get('parse_status')}",
        }

    def gfsec_fima_fetch_daily_performance(self, portfolio_code: str) -> dict[str, Any]:
        safe_code = safe_name(portfolio_code)
        # Responses above roughly 30 KB are intermittently truncated by the
        # upstream service. Keep pages bounded and page until the advertised
        # total is fully covered.
        page_size = min(self.gfsec_fima_daily_page_size, 200)
        combined_by_date: dict[str, dict[str, Any]] = {}
        page_snapshot_ids: list[str] = []
        first_source_url: str | None = None
        expected_total: int | None = None
        error: str | None = None
        completed = False
        for page_num in range(1, 101):
            page_result = self.gfsec_fima_fetch(
                "aggregate/1.0.0/strategy/performance/dayYieldList",
                {
                    "portfolioCode": portfolio_code,
                    "pageNum": page_num,
                    "pageSize": page_size,
                },
                Path("portfolio")
                / safe_code
                / "daily_performance"
                / f"page_{page_num:04d}.json",
            )
            if first_source_url is None:
                first_source_url = page_result.get("source_url")
            if not page_result.get("ok"):
                error = f"page={page_num}, {page_result.get('error')}"
                break
            snapshot_id = str(page_result.get("snapshot_id") or "")
            if snapshot_id:
                page_snapshot_ids.append(snapshot_id)
            payload = page_result.get("payload")
            payload = payload if isinstance(payload, dict) else {}
            page_rows = object_rows(payload.get("data"))
            total_value = parse_float(payload.get("total"))
            if total_value is not None:
                expected_total = max(expected_total or 0, int(total_value))
            for source_row in page_rows:
                busi_date = gfsec_fima_curve_date(source_row.get("busiDate"))
                if not busi_date:
                    continue
                normalized_source = dict(source_row)
                normalized_source["__source_snapshot_id"] = snapshot_id or None
                combined_by_date[busi_date] = normalized_source
            if (
                (expected_total is not None and len(combined_by_date) >= expected_total)
                or len(page_rows) < page_size
            ):
                completed = True
                break
        if not completed and error is None:
            error = "page_limit_exceeded"
        combined_rows = sorted(
            combined_by_date.values(),
            key=lambda row: str(row.get("busiDate") or ""),
            reverse=True,
        )
        return {
            "ok": completed and error is None,
            "payload": {
                "total": expected_total if expected_total is not None else len(combined_rows),
                "data": combined_rows,
            },
            "snapshot_id": page_snapshot_ids[0] if page_snapshot_ids else None,
            "snapshot_ids": page_snapshot_ids,
            "source_url": first_source_url,
            "http_status": 200 if completed and error is None else 0,
            "error": error,
            "page_count": len(page_snapshot_ids),
            "row_count": len(combined_rows),
        }

    def gfsec_fima_fetch_official_curve(
        self,
        portfolio_code: str,
        start_date: str,
    ) -> dict[str, Any]:
        """Fetch the exact chart curve, splitting only after a large response fails.

        The upstream occasionally closes long JSON responses before the final
        byte.  Most portfolios therefore keep the single-request fast path;
        only a failed long response is retried in bounded calendar-year
        windows and merged by disclosed date.
        """

        safe_code = safe_name(portfolio_code)
        endpoint = (
            "fimaassetallocation/1.0.0/strategy/portfolioAndIndexYield/"
            f"{portfolio_code}"
        )
        initial = self.gfsec_fima_fetch(
            endpoint,
            {"startDate": start_date, "endDate": self.day},
            Path("portfolio") / safe_code / "official_curve.json",
        )
        if initial.get("ok"):
            initial["windowed_fallback"] = False
            return initial

        parsed_start = parse_flexible_date(start_date) or "2000-01-01"
        parsed_end = parse_flexible_date(self.day) or datetime.now(timezone.utc).date().isoformat()
        start_year = int(parsed_start[:4])
        end_year = int(parsed_end[:4])
        portfolio_by_date: dict[str, dict[str, Any]] = {}
        benchmark_by_date: dict[str, dict[str, Any]] = {}
        snapshot_ids: list[str] = []
        source_urls: list[str] = []
        errors: list[str] = []
        index_code: Any = None
        index_name: Any = None
        for year in range(start_year, end_year + 1):
            window_start = max(parsed_start, f"{year:04d}-01-01")
            window_end = min(parsed_end, f"{year:04d}-12-31")
            if window_start > window_end:
                continue
            result = self.gfsec_fima_fetch(
                endpoint,
                {"startDate": window_start, "endDate": window_end},
                Path("portfolio")
                / safe_code
                / "official_curve_windows"
                / f"{year:04d}.json",
            )
            if not result.get("ok"):
                errors.append(f"{window_start}..{window_end}: {result.get('error')}")
                continue
            snapshot_id = str(result.get("snapshot_id") or "")
            if snapshot_id:
                snapshot_ids.append(snapshot_id)
            if result.get("source_url"):
                source_urls.append(str(result["source_url"]))
            payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
            index_code = index_code or payload.get("indexCode")
            index_name = index_name or payload.get("indexName")
            for source in object_rows(payload.get("portfolioYields")):
                trade_date = gfsec_fima_curve_date(source.get("busiDate"))
                if trade_date:
                    row = dict(source)
                    row["__source_snapshot_id"] = snapshot_id or None
                    portfolio_by_date[trade_date] = row
            for source in object_rows(payload.get("indexYields")):
                trade_date = gfsec_fima_curve_date(source.get("busiDate"))
                if trade_date:
                    row = dict(source)
                    row["__source_snapshot_id"] = snapshot_id or None
                    benchmark_by_date[trade_date] = row

        complete = not errors
        return {
            "ok": complete,
            "payload": {
                "indexCode": index_code,
                "indexName": index_name,
                "portfolioYields": [portfolio_by_date[key] for key in sorted(portfolio_by_date)],
                "indexYields": [benchmark_by_date[key] for key in sorted(benchmark_by_date)],
            },
            "snapshot_id": snapshot_ids[0] if snapshot_ids else initial.get("snapshot_id"),
            "snapshot_ids": snapshot_ids,
            "source_url": source_urls[0] if source_urls else initial.get("source_url"),
            "http_status": 200 if complete else 0,
            "error": "; ".join(errors) if errors else None,
            "windowed_fallback": True,
            "window_count": end_year - start_year + 1,
            "row_count": len(portfolio_by_date),
        }

    def collect_gfsec_fima_portfolio(self, portfolio_code: str) -> dict[str, Any]:
        safe_code = safe_name(portfolio_code)
        endpoint_defs = {
            "portfolio_mix": (
                "aggregate/1.0.0/strategy/portfolioMixInfo",
                {"portfolioCode": portfolio_code},
            ),
            "current_allocation": (
                "aggregate/1.0.0/strategy/productAllocationWithAdjust",
                {"portfolioCode": portfolio_code},
            ),
            "rebalances": (
                "aggregate/1.0.0/strategy/rebalance/list",
                {"portfolioCode": portfolio_code, "pageNum": 1, "pageSize": 9999},
            ),
            "period_performance": (
                "aggregate/1.0.0/strategy/performance/periodPerf",
                {"portfolioCode": portfolio_code},
            ),
        }
        endpoints: dict[str, Any] = {}
        for name, (endpoint, params) in endpoint_defs.items():
            endpoints[name] = self.gfsec_fima_fetch(
                endpoint,
                params,
                Path("portfolio") / safe_code / f"{name}.json",
            )
        mix_payload = (
            endpoints["portfolio_mix"].get("payload")
            if isinstance(endpoints["portfolio_mix"].get("payload"), dict)
            else {}
        )
        curve_start_date = gfsec_fima_curve_date(
            mix_payload.get("setupDateStr") or mix_payload.get("setupDate")
        ) or "2000-01-01"
        endpoints["official_curve"] = self.gfsec_fima_fetch_official_curve(
            portfolio_code,
            curve_start_date,
        )
        endpoints["daily_performance"] = self.gfsec_fima_fetch_daily_performance(portfolio_code)
        official_curve_payload = endpoints["official_curve"].get("payload")
        official_curve_rows = (
            gfsec_fima_official_curve_rows(
                official_curve_payload if isinstance(official_curve_payload, dict) else {}
            )
            if endpoints["official_curve"].get("ok")
            else []
        )
        legacy_daily_payload = endpoints["daily_performance"].get("payload")
        legacy_daily_rows = gfsec_fima_daily_curve_rows(
            legacy_daily_payload if isinstance(legacy_daily_payload, dict) else {}
        )
        period_ok = bool(endpoints["period_performance"].get("ok"))
        core_metadata_ok = bool(
            endpoints["portfolio_mix"].get("ok") or endpoints["period_performance"].get("ok")
        )
        performance_ok = bool(official_curve_rows or legacy_daily_rows or period_ok)
        return {
            "portfolio_code": portfolio_code,
            "endpoints": endpoints,
            # Holdings and rebalance endpoints are independently audited.  A
            # transient ancillary failure must not discard otherwise complete
            # product metadata and performance data.
            "success": core_metadata_ok and performance_ok,
            "official_curve_row_count": len(official_curve_rows),
            "legacy_daily_curve_row_count": len(legacy_daily_rows),
            "performance_source": (
                "official_cumulative_curve"
                if official_curve_rows
                else "legacy_compounded_daily_fallback"
                if legacy_daily_rows
                else "period_performance_only"
                if period_ok
                else None
            ),
        }

    @staticmethod
    def gfsec_fima_fee_text(mix: dict[str, Any]) -> str | None:
        fee_rows = object_rows(mix.get("fees"))
        fee = next(
            (
                row
                for row in fee_rows
                if str(row.get("feeType")) == "10" and str(row.get("status", 0)) == "0"
            ),
            None,
        )
        if not fee:
            return None
        ratio = parse_float(fee.get("discountFeeRatio"))
        if ratio is None:
            ratio = parse_float(fee.get("feeRatio"))
        if ratio is None:
            return None
        return f"{ratio * 100:g}%/年"

    @staticmethod
    def gfsec_fima_source_strategy_id(owner: dict[str, Any]) -> str:
        product_type = str(owner.get("product_type") or "regular")
        partner_id = str(owner.get("partner_id") or "unknown")
        product_id = str(owner.get("product_id") or owner.get("strategy_code") or owner.get("portfolio_code"))
        prefix = "target" if product_type == "target_period" else "regular"
        return f"{prefix}:{partner_id}:{product_id}"

    def gfsec_fima_master_row(
        self,
        owner: dict[str, Any],
        portfolio_result: dict[str, Any],
        company_by_partner: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        endpoints = portfolio_result.get("endpoints") or {}
        mix_response = endpoints.get("portfolio_mix") or {}
        period_response = endpoints.get("period_performance") or {}
        official_curve_response = endpoints.get("official_curve") or {}
        mix = mix_response.get("payload") if isinstance(mix_response.get("payload"), dict) else {}
        period_payload = (
            period_response.get("payload")
            if isinstance(period_response.get("payload"), dict)
            else {}
        )
        performance = period_payload.get("portfolioPerf")
        if not isinstance(performance, dict):
            performance = mix.get("perf") if isinstance(mix.get("perf"), dict) else {}
        official_curve_payload = (
            official_curve_response.get("payload")
            if isinstance(official_curve_response.get("payload"), dict)
            else {}
        )
        company = company_by_partner.get(str(owner.get("partner_id") or ""), {})
        is_target = owner.get("product_type") == "target_period"
        target = owner.get("target") if isinstance(owner.get("target"), dict) else {}
        catalog = owner.get("catalog") if isinstance(owner.get("catalog"), dict) else {}
        product_name = (
            target.get("targetProfitName")
            if is_target
            else catalog.get("displayName") or catalog.get("portfolioName") or catalog.get("strategyName")
        )
        product_name = product_name or mix.get("portfolioName") or mix.get("strategyName") or owner["portfolio_code"]
        launch_date = (
            gfsec_fima_curve_date(target.get("participationStartDate"))
            if is_target
            else gfsec_fima_curve_date(
                mix.get("setupDateStr") or mix.get("setupDate") or catalog.get("setupDateStr")
            )
        )
        source_strategy_id = self.gfsec_fima_source_strategy_id(owner)
        raw_tags = mix.get("tags") if isinstance(mix.get("tags"), list) else []
        tags = [
            "广发证券财富管家",
            "目标盈期次" if is_target else str(mix.get("strategyTypeName") or "常规策略"),
            str(company.get("brandName") or company.get("chiName") or owner.get("partner_id") or ""),
            *[str(tag) for tag in raw_tags],
        ]
        tags = [tag for index, tag in enumerate(tags) if tag and tag not in tags[:index]]
        benchmark = (
            strip_html(str(mix.get("remark") or ""))
            or mix.get("baselineIndexName")
            or official_curve_payload.get("indexName")
        )
        return {
            "channel_id": "gfsec_fima",
            "source_strategy_id": source_strategy_id,
            "strategy_name": str(product_name),
            "advisor_name": mix.get("advisorOrgName") or company.get("chiName") or company.get("brandName"),
            "strategy_type": "目标盈期次" if is_target else mix.get("strategyTypeName"),
            "risk_level": mix.get("riskLevelName") or (f"R{target.get('riskLevel')}" if target.get("riskLevel") else None),
            "launch_date": launch_date,
            "suggested_holding_period": mix.get("targetHoldingPeriod") or mix.get("holdingPeriodStr"),
            "minimum_amount": parse_float(mix.get("firstMinAmount")),
            "advisory_fee_rate": self.gfsec_fima_fee_text(mix),
            "benchmark": benchmark,
            "tags": tags,
            "status": "public",
            "strategy_description": mix.get("strategyIllustration") or mix.get("strategyIntroduce") or mix.get("strategyTarget"),
            "source_url": mix_response.get("source_url"),
            "source_snapshot_id": mix_response.get("snapshot_id"),
            "first_seen_at": self.captured_at,
            "last_seen_at": self.captured_at,
            "extra": {
                "product_type": owner.get("product_type"),
                "underlying_portfolio_code": owner.get("portfolio_code"),
                "partner_id": owner.get("partner_id"),
                "strategy_code": owner.get("strategy_code"),
                "product_id": owner.get("product_id"),
                "target_period_status": target.get("status") if is_target else None,
                "target_period": target if is_target else None,
                "strategy_target": mix.get("strategyTarget"),
                "investment_scope": mix.get("investScope"),
                "investment_target_scope": mix.get("investTargetScope"),
                "risk_feature": mix.get("riskFeature"),
                "primary_manager": mix.get("primaryManager"),
                "sub_manager": mix.get("subManager"),
                "asset_configs": mix.get("assetConfigs"),
                "performance": performance,
                "performance_source": "gfsec_fima.periodPerf.portfolioPerf",
                "official_curve_index_code": official_curve_payload.get("indexCode"),
                "official_curve_index_name": official_curve_payload.get("indexName"),
                "current_position_semantics": "official_current_model_allocation_not_customer_actual_holding",
            },
            "run_id": self.run_id,
        }

    def gfsec_fima_normalize_rebalances(
        self,
        owner: dict[str, Any],
        portfolio_result: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
        response = (portfolio_result.get("endpoints") or {}).get("rebalances") or {}
        payload = response.get("payload") if isinstance(response.get("payload"), dict) else {}
        raw_events = object_rows(payload.get("data"))
        event_rows: list[dict[str, Any]] = []
        delta_rows: list[dict[str, Any]] = []
        invalid_date_total = 0
        strategy_id = self.gfsec_fima_source_strategy_id(owner)
        for event_order, event in enumerate(raw_events, 1):
            event_date = next(
                (
                    parsed
                    for key in ("rebalanceDate", "effectiveDate", "tradeDate", "date", "createAt", "updateAt")
                    if (parsed := gfsec_fima_curve_date(event.get(key)))
                ),
                None,
            )
            if not event_date:
                invalid_date_total += 1
                continue
            raw_event_id = str(event.get("id") or event.get("rebalanceId") or compact_json(event))
            event_id = stable_id("gfsec_fima_rebalance", f"{strategy_id}|{raw_event_id}")
            event_rows.append(
                {
                    "channel_id": "gfsec_fima",
                    "source_strategy_id": strategy_id,
                    "rebalance_event_id": event_id,
                    "rebalance_date": event_date,
                    "previous_position_date": gfsec_fima_curve_date(event.get("previousEffectiveDate")),
                    "new_position_date": gfsec_fima_curve_date(event.get("effectiveDate")) or event_date,
                    "disclosure_date": gfsec_fima_curve_date(event.get("createAt") or event.get("updateAt")),
                    "event_title": event.get("title") or event.get("rebalanceName") or "官方调仓",
                    "event_reason": event.get("reason") or event.get("rebalanceReason"),
                    "event_sequence": event_order,
                    "confidence_level": "official_exact",
                    "source_snapshot_id": response.get("snapshot_id"),
                    "source_url": response.get("source_url"),
                    "run_id": self.run_id,
                }
            )
            for asset in object_rows(event.get("assetChangeList")):
                group_name = asset.get("assetName") or asset.get("parentAssetName")
                for product in object_rows(asset.get("productChangeList")):
                    before = parse_float(
                        product.get("configRatioBefore")
                        if product.get("configRatioBefore") is not None
                        else product.get("beforeRatio")
                    )
                    after = parse_float(
                        product.get("configRatioAfter")
                        if product.get("configRatioAfter") is not None
                        else product.get("configRatio")
                        if product.get("configRatio") is not None
                        else product.get("afterRatio")
                    )
                    before_pct = pct_decimal_to_percent(before)
                    after_pct = pct_decimal_to_percent(after)
                    delta_rows.append(
                        {
                            "rebalance_event_id": event_id,
                            "fund_code": product.get("productCode") or product.get("fundCode"),
                            "fund_name": product.get("productName") or product.get("fundName"),
                            "fund_group_name": group_name,
                            "before_weight": before_pct,
                            "after_weight": after_pct,
                            "weight_delta": (
                                round(after_pct - before_pct, 6)
                                if before_pct is not None and after_pct is not None
                                else None
                            ),
                            "action_type": classify_action(before_pct, after_pct),
                            "source_snapshot_id": response.get("snapshot_id"),
                            "run_id": self.run_id,
                        }
                    )
        return event_rows, delta_rows, invalid_date_total

    def collect_gfsec_fima(self) -> dict[str, Any]:
        channel_id = "gfsec_fima"
        channel_name = "广发证券"
        companies_response = self.gfsec_fima_fetch(
            "fimaassetallocation/1.0.0/content/fimaFundCompany/search",
            {"from": 0, "size": 999},
            "catalog/companies.json",
        )
        strategies_response = self.gfsec_fima_fetch(
            "fimaassetallocation/1.0.0/content/fimaStrategy/search",
            {"from": 0, "size": 9999, "status": "normal"},
            "catalog/strategies.json",
        )
        companies_payload = companies_response.get("payload") if isinstance(companies_response.get("payload"), dict) else {}
        strategies_payload = strategies_response.get("payload") if isinstance(strategies_response.get("payload"), dict) else {}
        companies = object_rows(companies_payload.get("data"))
        companies_catalog_closed = bool(companies_response.get("ok")) and catalog_page_closed(
            companies_payload,
            companies,
            999,
        )
        active_companies = [row for row in companies if str(row.get("status")) == "normal"]
        company_by_partner = {str(row.get("partnerId")): row for row in active_companies if row.get("partnerId")}
        raw_strategies = object_rows(strategies_payload.get("data"))
        strategies_catalog_closed = bool(strategies_response.get("ok")) and catalog_page_closed(
            strategies_payload,
            raw_strategies,
            9999,
        )
        strategies = [
            row
            for row in raw_strategies
            if str(row.get("partnerId") or "") in company_by_partner
        ]
        excluded_test_rows = [
            row
            for row in strategies
            if "UAT" in str(row.get("strategyCode") or "").upper()
            or str(row.get("portfolioCode") or "").upper().startswith("TEST")
            or "测试" in str(row.get("strategyName") or "")
        ]
        business_strategies = [row for row in strategies if row not in excluded_test_rows]
        regular_strategies = [row for row in business_strategies if str(row.get("businessClass")) != "7"]
        target_templates = [row for row in business_strategies if str(row.get("businessClass")) == "7"]
        templates_by_pair = {
            (str(row.get("partnerId")), str(row.get("strategyCode"))): row
            for row in target_templates
            if row.get("partnerId") and row.get("strategyCode")
        }
        target_periods: list[dict[str, Any]] = []
        period_query_ok = 0
        for partner_id, strategy_code in sorted(templates_by_pair):
            period_response = self.gfsec_fima_fetch(
                "aggregate/1.0.0/strategy/mby/periodList",
                {
                    "partnerId": partner_id,
                    "strategyCode": strategy_code,
                    "page": 1,
                    "pageSize": 9999,
                },
                Path("catalog") / "target_periods" / f"{safe_name(partner_id)}_{safe_name(strategy_code)}.json",
            )
            payload = (
                period_response.get("payload")
                if isinstance(period_response.get("payload"), dict)
                else {}
            )
            period_rows = object_rows(payload.get("data"))
            if period_response.get("ok") and catalog_page_closed(payload, period_rows, 9999):
                period_query_ok += 1
            for row in period_rows:
                item = dict(row)
                item["_query_partner_id"] = partner_id
                item["_query_strategy_code"] = strategy_code
                target_periods.append(item)
        deduped_periods: dict[str, dict[str, Any]] = {}
        for row in target_periods:
            key = str(
                row.get("id")
                or f"{row.get('_query_partner_id')}|{row.get('_query_strategy_code')}|{row.get('portfolioCodeDate')}"
            )
            deduped_periods[key] = row
        target_periods = list(deduped_periods.values())

        owner_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in regular_strategies:
            portfolio_code = str(row.get("portfolioCode") or "").strip()
            if not portfolio_code:
                continue
            owner_map[portfolio_code].append(
                {
                    "product_type": "regular_strategy",
                    "partner_id": row.get("partnerId"),
                    "strategy_code": row.get("strategyCode"),
                    "product_id": row.get("_id") or row.get("id") or row.get("strategyCode"),
                    "portfolio_code": portfolio_code,
                    "catalog": row,
                }
            )
        for row in target_periods:
            portfolio_code = str(row.get("portfolioCode") or "").strip()
            if not portfolio_code:
                continue
            partner_id = str(row.get("_query_partner_id") or row.get("partnerId") or "")
            strategy_code = str(row.get("_query_strategy_code") or row.get("strategyCode") or "")
            owner_map[portfolio_code].append(
                {
                    "product_type": "target_period",
                    "partner_id": partner_id,
                    "strategy_code": strategy_code,
                    "product_id": row.get("id") or row.get("portfolioCodeDate") or portfolio_code,
                    "portfolio_code": portfolio_code,
                    "catalog": templates_by_pair.get((partner_id, strategy_code), {}),
                    "target": row,
                }
            )

        portfolio_codes = sorted(owner_map)
        product_total = sum(len(owners) for owners in owner_map.values())
        catalog_strategy_ids = sorted(
            {
                self.gfsec_fima_source_strategy_id(owner)
                for owners in owner_map.values()
                for owner in owners
            }
        )
        local_strategy_ids = load_local_strategy_ids(self.project_root, channel_id)
        catalog_complete = companies_catalog_closed and strategies_catalog_closed and (
            period_query_ok == len(templates_by_pair)
        )
        planned = len(portfolio_codes)
        print(
            f"[PLAN] GFSEC_FIMA planned_products={product_total} planned_portfolios={planned}",
            flush=True,
        )
        print(
            "PROGRESS "
            + json.dumps(
                {
                    "completed": 0,
                    "total": planned,
                    "unit": "个底层组合",
                    "message": f"计划产品={product_total} 成功=0 失败=0",
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        portfolio_results: list[dict[str, Any]] = []
        success_total = 0
        failure_total = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, self.workers)) as executor:
            futures = {
                executor.submit(self.collect_gfsec_fima_portfolio, code): code
                for code in portfolio_codes
            }
            for processed, future in enumerate(concurrent.futures.as_completed(futures), 1):
                code = futures[future]
                try:
                    result = future.result()
                except Exception as exc:  # noqa: BLE001 - isolated portfolio failure is recorded.
                    result = {
                        "portfolio_code": code,
                        "success": False,
                        "fatal_error": f"{type(exc).__name__}: {exc}",
                        "endpoints": {},
                    }
                portfolio_results.append(result)
                if result.get("success"):
                    success_total += 1
                else:
                    failure_total += 1
                print(
                    "PROGRESS "
                    + json.dumps(
                        {
                            "completed": processed,
                            "total": planned,
                            "unit": "个底层组合",
                            "message": f"当前={code} 成功={success_total} 失败={failure_total}",
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
        portfolio_results.sort(key=lambda item: str(item.get("portfolio_code")))
        result_by_code = {str(row.get("portfolio_code")): row for row in portfolio_results}

        normalized = {entity: [] for entity in DEFAULT_ENTITIES}
        fund_dim: dict[str, dict[str, Any]] = {}
        invalid_rebalance_date_total = 0
        allocation_portfolio_ok = 0
        allocation_weight_closed_total = 0
        rebalance_endpoint_ok_total = 0
        daily_endpoint_ok_total = 0
        official_curve_endpoint_ok_total = 0
        official_curve_data_total = 0
        usable_daily_curve_total = 0
        daily_curve_fallback_total = 0
        period_endpoint_ok_total = 0
        successful_product_total = 0
        alternative_fund_excluded_underlying_total = 0
        for portfolio_code, owners in sorted(owner_map.items()):
            portfolio_result = result_by_code.get(portfolio_code, {"endpoints": {}})
            endpoints = portfolio_result.get("endpoints") or {}
            allocation_response = endpoints.get("current_allocation") or {}
            allocation = allocation_response.get("payload") if isinstance(allocation_response.get("payload"), dict) else {}
            groups = object_rows(allocation.get("productAllocations"))
            main_funds = [
                (group, fund)
                for group in groups
                for fund in object_rows(group.get("mainProducts"))
            ]
            alternative_fund_excluded_underlying_total += sum(
                len(object_rows(fund.get("alternativeProducts")))
                for _, fund in main_funds
            )
            weights = [parse_float(fund.get("configRatio")) for _, fund in main_funds]
            valid_weights = [value for value in weights if value is not None]
            weight_closed = bool(valid_weights) and 0.995 <= sum(valid_weights) <= 1.005
            if allocation_response.get("ok") and main_funds:
                allocation_portfolio_ok += 1
            if weight_closed:
                allocation_weight_closed_total += 1
            if (endpoints.get("rebalances") or {}).get("ok"):
                rebalance_endpoint_ok_total += 1
            if (endpoints.get("daily_performance") or {}).get("ok"):
                daily_endpoint_ok_total += 1
            official_curve_response = endpoints.get("official_curve") or {}
            official_curve_payload = (
                official_curve_response.get("payload")
                if isinstance(official_curve_response.get("payload"), dict)
                else {}
            )
            official_curve_rows = (
                gfsec_fima_official_curve_rows(official_curve_payload)
                if official_curve_response.get("ok")
                else []
            )
            if official_curve_response.get("ok"):
                official_curve_endpoint_ok_total += 1
            if official_curve_rows:
                official_curve_data_total += 1
                usable_daily_curve_total += 1
            else:
                legacy_daily_response = endpoints.get("daily_performance") or {}
                legacy_daily_payload = (
                    legacy_daily_response.get("payload")
                    if isinstance(legacy_daily_response.get("payload"), dict)
                    else {}
                )
                if gfsec_fima_daily_curve_rows(legacy_daily_payload):
                    daily_curve_fallback_total += 1
                    usable_daily_curve_total += 1
            if (endpoints.get("period_performance") or {}).get("ok"):
                period_endpoint_ok_total += 1
            if portfolio_result.get("success"):
                successful_product_total += len(owners)

            for owner in owners:
                strategy_id = self.gfsec_fima_source_strategy_id(owner)
                normalized["strategy_master"].append(
                    self.gfsec_fima_master_row(owner, portfolio_result, company_by_partner)
                )
                position_hash = hashlib.sha256(compact_json(allocation).encode("utf-8")).hexdigest()
                for group, fund in main_funds:
                    fund_code = str(fund.get("productCode") or "").strip()
                    fund_name = str(fund.get("productName") or "").strip()
                    group_name = group.get("assetName") or group.get("parentAssetName")
                    normalized["strategy_fund_snapshot"].append(
                        {
                            "channel_id": channel_id,
                            "source_strategy_id": strategy_id,
                            "snapshot_id": f"gfsec_fima-{stable_id('position', strategy_id)}-{self.day}",
                            "position_date": self.day,
                            "disclosure_date": None,
                            "fund_code": fund_code,
                            "fund_name": fund_name,
                            "fund_asset_type": group_name,
                            "fund_group_name": group_name,
                            "fund_weight": pct_decimal_to_percent(fund.get("configRatio")),
                            "group_weight": pct_decimal_to_percent(
                                group.get("configRatio")
                                if group.get("configRatio") is not None
                                else group.get("productTotalRatio")
                            ),
                            "is_precise_weight": True,
                            "confidence_level": "official_current_model_observed",
                            "access_level": "public",
                            "is_login_required": False,
                            "source_snapshot_id": allocation_response.get("snapshot_id"),
                            "source_url": allocation_response.get("source_url"),
                            "raw_record_hash": position_hash,
                            "run_id": self.run_id,
                            "extra": {
                                "underlying_portfolio_code": portfolio_code,
                                "action_name_not_used_as_rebalance": fund.get("actionName"),
                                "alternative_products_excluded": len(object_rows(fund.get("alternativeProducts"))),
                            },
                        }
                    )
                    if fund_code:
                        fund_dim[fund_code] = {
                            "fund_code": fund_code,
                            "fund_name": fund_name or fund_code,
                            "fund_company": None,
                            "fund_type": group_name,
                            "tracking_index": None,
                            "theme_tags": [group_name] if group_name else [],
                            "latest_nav": parse_float(fund.get("newestNetVal")),
                            "latest_nav_date": None,
                            "status": "active",
                            "source": "gfsec_fima_public_current_model_allocation",
                            "updated_at": self.captured_at,
                            "run_id": self.run_id,
                        }

                official_curve_response = endpoints.get("official_curve") or {}
                official_curve_payload = (
                    official_curve_response.get("payload")
                    if isinstance(official_curve_response.get("payload"), dict)
                    else {}
                )
                daily_rows = (
                    gfsec_fima_official_curve_rows(official_curve_payload)
                    if official_curve_response.get("ok")
                    else []
                )
                daily_response = official_curve_response
                section_name = "官方组合及基准累计收益曲线"
                section_type = "gfsec_fima_portfolioAndIndexYield"
                if not daily_rows:
                    daily_response = endpoints.get("daily_performance") or {}
                    daily_payload = (
                        daily_response.get("payload")
                        if isinstance(daily_response.get("payload"), dict)
                        else {}
                    )
                    daily_rows = gfsec_fima_daily_curve_rows(daily_payload)
                    section_name = "官方单日收益复利净值曲线（累计曲线兜底）"
                    section_type = "gfsec_fima_dayYieldList_fallback"
                for daily in daily_rows:
                    normalized["strategy_performance_daily"].append(
                        {
                            "channel_id": channel_id,
                            "source_strategy_id": strategy_id,
                            "trade_date": daily["trade_date"],
                            "nav": daily["nav"],
                            "daily_return": daily["daily_return_pct"],
                            "cumulative_return": daily["cumulative_return_pct"],
                            "benchmark_return": daily["benchmark_cumulative_return_pct"],
                            "index_return": None,
                            "max_drawdown": None,
                            "section_name": section_name,
                            "section_type": section_type,
                            "source_snapshot_id": daily.get("source_snapshot_id")
                            or daily_response.get("snapshot_id"),
                            "run_id": self.run_id,
                        }
                    )

                period_response = endpoints.get("period_performance") or {}
                period_payload = period_response.get("payload") if isinstance(period_response.get("payload"), dict) else {}
                portfolio_perf = period_payload.get("portfolioPerf") if isinstance(period_payload.get("portfolioPerf"), dict) else {}
                index_perf = period_payload.get("indexPerf") if isinstance(period_payload.get("indexPerf"), dict) else {}
                as_of_date = gfsec_fima_curve_date(portfolio_perf.get("busiDate")) or self.day
                interval_defs = [
                    ("1w", "近1周", "yield1w"),
                    ("1m", "近1月", "yield1m"),
                    ("3m", "近3月", "yield3m"),
                    ("6m", "近6月", "yield6m"),
                    ("1y", "近1年", "yield1y"),
                    ("2y", "近2年", "yield2y"),
                    ("3y", "近3年", "yield3y"),
                    ("5y", "近5年", "yield5y"),
                    ("ytd", "今年以来", "yieldTy"),
                    ("since_inception", "成立以来", "totalYield"),
                    ("annualized", "年化收益", "annualReturn"),
                ]
                for interval_code, interval_label, field in interval_defs:
                    value = parse_float(portfolio_perf.get(field))
                    benchmark_value = parse_float(index_perf.get(field))
                    if value is None and benchmark_value is None:
                        continue
                    normalized["strategy_performance_interval"].append(
                        {
                            "channel_id": channel_id,
                            "source_strategy_id": strategy_id,
                            "as_of_date": as_of_date,
                            "interval_code": interval_code,
                            "interval_label": interval_label,
                            "return_value": pct_decimal_to_percent(value),
                            "benchmark_return": pct_decimal_to_percent(benchmark_value),
                            "source_snapshot_id": period_response.get("snapshot_id"),
                            "run_id": self.run_id,
                        }
                    )

                event_rows, delta_rows, invalid_total = self.gfsec_fima_normalize_rebalances(
                    owner,
                    portfolio_result,
                )
                normalized["strategy_rebalance_event"].extend(event_rows)
                normalized["strategy_rebalance_fund_delta"].extend(delta_rows)
                invalid_rebalance_date_total += invalid_total

        normalized["fund_public_dim"] = sorted(fund_dim.values(), key=lambda row: row["fund_code"])
        normalized["app_public_entry"] = [
            {
                "channel_id": channel_id,
                "channel_name": channel_name,
                "source_url": "https://robot.gf.com.cn/api/robot",
                "run_id": self.run_id,
                "captured_at": self.captured_at,
                "strategy_total": len(normalized["strategy_master"]),
                "underlying_portfolio_total": planned,
                "available_entities": [
                    "strategy_master",
                    "strategy_performance_daily",
                    "strategy_performance_interval",
                    "strategy_fund_snapshot",
                    "fund_public_dim",
                    "app_public_entry",
                ],
                "missing_entities": (
                    []
                    if normalized["strategy_rebalance_event"]
                    else ["strategy_rebalance_event", "strategy_rebalance_fund_delta"]
                ),
                "access_note": "匿名公开接口；仓位为官方当前模型配置，不是客户账户实际持仓。调仓接口已逐组合检查。",
            }
        ]
        self.write_normalized_entities(channel_id, normalized)

        ratio = lambda value: (value / planned if planned else 0.0)
        catalog_reconciliation = reconcile_catalog_batch(
            catalog_strategy_ids,
            (
                row.get("source_strategy_id")
                for row in normalized["strategy_master"]
                if isinstance(row, dict)
            ),
            local_strategy_ids,
        )
        summary = {
            "channel_id": channel_id,
            "channel_name": channel_name,
            "run_id": self.run_id,
            "captured_at": self.captured_at,
            "collection_status": "success" if failure_total == 0 and product_total > 0 else "partial",
            "holding_penetration_status": "official_current_model_fund_weight_exact_public",
            "strategy_master_ok": product_total > 0 and len(normalized["strategy_master"]) == product_total,
            "daily_performance_ok": ratio(usable_daily_curve_total) >= 0.95,
            "fund_level_position_ok": ratio(allocation_portfolio_ok) >= 0.95 and ratio(allocation_weight_closed_total) >= 0.95,
            "rebalance_event_ok": ratio(rebalance_endpoint_ok_total) >= 0.95,
            "rebalance_fund_delta_ok": ratio(rebalance_endpoint_ok_total) >= 0.95,
            "rebalance_endpoint_ok": ratio(rebalance_endpoint_ok_total) >= 0.95,
            "official_rebalance_history_status": (
                "official_endpoint_checked_no_events"
                if rebalance_endpoint_ok_total == planned and not normalized["strategy_rebalance_event"]
                else "official_events_collected"
                if normalized["strategy_rebalance_event"]
                else "partial_endpoint_failure"
            ),
            "strategy_total": len(normalized["strategy_master"]),
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
            "catalog_complete": catalog_complete,
            "catalog_source": "gfsec_fima.search_companies_and_strategies",
            "catalog_company_total": len(companies),
            "catalog_active_company_total": len(active_companies),
            "catalog_raw_strategy_total": len(raw_strategies),
            "catalog_companies_page_closed": companies_catalog_closed,
            "catalog_strategies_page_closed": strategies_catalog_closed,
            "catalog_stop_reason": (
                "catalog_and_target_period_queries_ok"
                if catalog_complete
                else "catalog_or_target_period_query_incomplete"
            ),
            "regular_product_total": len(regular_strategies),
            "target_period_product_total": len(target_periods),
            "underlying_portfolio_total": planned,
            "planned_portfolio_total": planned,
            "processed_portfolio_total": len(portfolio_results),
            "success_portfolio_total": success_total,
            "failure_portfolio_total": failure_total,
            "planned_product_total": product_total,
            "processed_product_total": product_total,
            "success_product_total": successful_product_total,
            "failure_product_total": product_total - successful_product_total,
            "allocation_portfolio_ok_total": allocation_portfolio_ok,
            "allocation_weight_closed_total": allocation_weight_closed_total,
            "daily_endpoint_ok_total": daily_endpoint_ok_total,
            "official_curve_endpoint_ok_total": official_curve_endpoint_ok_total,
            "official_curve_data_total": official_curve_data_total,
            "usable_daily_curve_total": usable_daily_curve_total,
            "daily_curve_fallback_total": daily_curve_fallback_total,
            "period_endpoint_ok_total": period_endpoint_ok_total,
            "rebalance_endpoint_ok_total": rebalance_endpoint_ok_total,
            "current_holding_rows": len(normalized["strategy_fund_snapshot"]),
            "current_holding_strategy_total": len(
                {row["source_strategy_id"] for row in normalized["strategy_fund_snapshot"]}
            ),
            "daily_performance_rows": len(normalized["strategy_performance_daily"]),
            "daily_performance_strategy_total": len(
                {row["source_strategy_id"] for row in normalized["strategy_performance_daily"]}
            ),
            "interval_performance_rows": len(normalized["strategy_performance_interval"]),
            "rebalance_event_total": len(normalized["strategy_rebalance_event"]),
            "rebalance_fund_delta_total": len(normalized["strategy_rebalance_fund_delta"]),
            "invalid_rebalance_date_total": invalid_rebalance_date_total,
            "alternative_fund_excluded_total": alternative_fund_excluded_underlying_total,
            "alternative_fund_excluded_product_mapped_total": sum(
                int((row.get("extra") or {}).get("alternative_products_excluded") or 0)
                for row in normalized["strategy_fund_snapshot"]
            ),
            "fund_dim_total": len(normalized["fund_public_dim"]),
            "active_company_total": len(active_companies),
            "target_period_query_total": len(templates_by_pair),
            "target_period_query_success_total": period_query_ok,
            "excluded_test_strategy_total": len(excluded_test_rows),
            "known_gap": "官方当前模型仓位无有效日期字段，持仓日期使用采集日、披露日期留空；官方调仓列表当前逐组合返回0条，未构造调仓事件；备选基金不作为持仓；官方基准曲线可能晚于策略曲线更新，未披露日期不做前向填充。",
        }
        inventory = {
            "primary_sources": [
                "https://robot.gf.com.cn/api/robot/fimaassetallocation/1.0.0/content/fimaFundCompany/search",
                "https://robot.gf.com.cn/api/robot/fimaassetallocation/1.0.0/content/fimaStrategy/search",
                "https://robot.gf.com.cn/api/robot/aggregate/1.0.0/strategy/portfolioMixInfo",
                "https://robot.gf.com.cn/api/robot/aggregate/1.0.0/strategy/productAllocationWithAdjust",
                "https://robot.gf.com.cn/api/robot/fimaassetallocation/1.0.0/strategy/portfolioAndIndexYield/{portfolioCode}",
                "https://robot.gf.com.cn/api/robot/aggregate/1.0.0/strategy/rebalance/list",
            ],
            "method": "官方 HTTP 接口按产品目录发现、按底层 portfolioCode 并发采集；业绩图优先使用 App 同源累计收益与基准曲线，旧日收益复算仅作兜底。",
            "login_required": False,
            "product_instance_total": product_total,
            "underlying_portfolio_total": planned,
            "catalog_strategy_total": int(catalog_reconciliation.get("catalog_strategy_total") or 0),
            "catalog_strategy_ids": list(catalog_reconciliation.get("catalog_strategy_ids") or []),
            "catalog_new_strategy_total": int(catalog_reconciliation.get("new_strategy_total") or 0),
            "catalog_new_strategy_ids": list(catalog_reconciliation.get("new_strategy_ids") or []),
            "catalog_batch_missing_strategy_ids": list(
                catalog_reconciliation.get("catalog_batch_missing_strategy_ids") or []
            ),
            "catalog_batch_closed": bool(catalog_reconciliation.get("catalog_batch_closed")),
            "catalog_complete": catalog_complete,
            "excluded_test_strategy_total": len(excluded_test_rows),
            "raw_snapshots": self.snapshots.get(channel_id, []),
        }
        summary["output_paths"] = self.write_app_outputs(channel_id, normalized, summary, inventory)
        print(
            f"[SUMMARY] GFSEC_FIMA planned_portfolios={planned} processed_portfolios={len(portfolio_results)} "
            f"success_portfolios={success_total} failure_portfolios={failure_total} "
            f"planned_products={product_total} processed_products={product_total} "
            f"success_products={successful_product_total} failure_products={product_total - successful_product_total}",
            flush=True,
        )
        return summary

    def collect_gfsec_robot(self) -> dict[str, Any]:
        channel_id = "gfsec_robot"
        channel_name = "广发证券"
        api_base = "https://robot.gf.com.cn/api/robot"
        investment_base = f"{api_base}/investment/2.0.0"
        asset_base = f"{api_base}/assetallocation/2.0.0"
        landing_url = "https://robot.gf.com.cn/asset/#/moneystrategy?channel=ytjapp"
        list_urls = [
            ("allocation_list_gfit2_normal", f"{investment_base}/strategy/allocation/list?status=normal&channel=gfit2&from=0&size=999"),
            ("allocation_list_gfit2_all", f"{investment_base}/strategy/allocation/list?channel=gfit2&from=0&size=999"),
            ("allocation_list_gfit_normal", f"{investment_base}/strategy/allocation/list?status=normal&channel=gfit&from=0&size=999"),
            ("allocation_list_gfit_all", f"{investment_base}/strategy/allocation/list?channel=gfit&from=0&size=999"),
        ]
        seed_strategy_ids = [
            "moneyfund",
            "t0moneyfund",
            "shortbond",
            "riskparity",
            "riskparityplus",
            "taa",
            "bigsmallcap",
            "fixedincomeplus",
            "zostar",
            "surfing",
            "theme",
            "gffund",
            "jsfund",
            "contmarketing",
            "allocation",
            "allocation.risk1p6",
            "allocation.risk1p12",
            "allocation.risk1p36",
            "allocation.risk2p6",
            "allocation.risk2p12",
            "allocation.risk2p36",
            "allocation.risk3p6",
            "allocation.risk3p12",
            "allocation.risk3p36",
            "allocation.risk4p6",
            "allocation.risk4p12",
            "allocation.risk4p36",
            "allocation.risk5p6",
            "allocation.risk5p12",
            "allocation.risk5p36",
            "smartfund",
            "wmadvisor",
            "insurance",
            "blmodel",
        ]

        discovered_ids: list[str] = []
        endpoint_status: list[dict[str, Any]] = []
        for key, url in list_urls:
            response = self.fetch(channel_id, "public_robot_api", url, Path("strategy_lists") / f"{key}.json", parse_json=True)
            data = response.json_data.get("data") if isinstance(response.json_data, dict) else None
            if isinstance(data, list):
                for item in data:
                    strategy_id = raw_id = str((item or {}).get("id") or "").strip()
                    if raw_id and strategy_id not in discovered_ids:
                        discovered_ids.append(strategy_id)
            endpoint_status.append(
                {
                    "name": key,
                    "url": response.final_url,
                    "http_status": response.snapshot.get("http_status"),
                    "parse_status": response.snapshot.get("parse_status"),
                    "bytes": response.raw_path.stat().st_size if response.raw_path.exists() else 0,
                    "total": response.json_data.get("total") if isinstance(response.json_data, dict) else None,
                    "count": len(data) if isinstance(data, list) else 0,
                    "page_closed": (
                        catalog_page_closed(response.json_data, data, 999)
                        if isinstance(response.json_data, dict) and isinstance(data, list)
                        else False
                    ),
                }
            )

        local_strategy_ids = load_local_strategy_ids(self.project_root, channel_id)
        catalog_complete = bool(endpoint_status) and all(
            item.get("http_status") == 200
            and item.get("parse_status") in {"success", "partial"}
            and item.get("page_closed") is True
            for item in endpoint_status
        )

        detail_ids: list[str] = []
        for strategy_id in [*discovered_ids, *seed_strategy_ids]:
            if strategy_id and strategy_id not in detail_ids:
                detail_ids.append(strategy_id)

        details: list[dict[str, Any]] = []
        missing_details: list[str] = []
        detail_responses: dict[str, RawResponse] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, self.workers)) as executor:
            futures = {
                executor.submit(
                    self.fetch,
                    channel_id,
                    "public_robot_api",
                    f"{investment_base}/strategy/{strategy_id}",
                    Path("strategy_details") / f"{safe_name(strategy_id)}.json",
                    parse_json=True,
                    timeout=20,
                ): strategy_id
                for strategy_id in detail_ids
            }
            for future in concurrent.futures.as_completed(futures):
                strategy_id = futures[future]
                try:
                    detail_responses[strategy_id] = future.result()
                except Exception:  # noqa: BLE001 - one strategy must not abort the channel batch.
                    missing_details.append(strategy_id)
        for strategy_id in detail_ids:
            response = detail_responses.get(strategy_id)
            if response is None:
                if strategy_id not in missing_details:
                    missing_details.append(strategy_id)
                continue
            if isinstance(response.json_data, dict) and response.json_data.get("id"):
                details.append(
                    {
                        **response.json_data,
                        "_source_snapshot_id": response.snapshot["snapshot_id"],
                        "_source_url": response.final_url,
                    }
                )
            else:
                missing_details.append(strategy_id)

        baseline_infos: dict[str, dict[str, Any]] = {}
        poster_infos: dict[str, dict[str, Any]] = {}
        baseline_status: list[dict[str, Any]] = []
        poster_status: list[dict[str, Any]] = []
        ancillary_responses: dict[tuple[str, str], RawResponse] = {}
        ancillary_errors: dict[tuple[str, str], str] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, self.workers)) as executor:
            futures: dict[concurrent.futures.Future[RawResponse], tuple[str, str]] = {}
            for detail in details:
                strategy_id = str(detail.get("id") or "").strip()
                if not strategy_id:
                    continue
                futures[
                    executor.submit(
                        self.fetch,
                        channel_id,
                        "public_robot_api",
                        f"{asset_base}/models/{strategy_id}/baselineCode",
                        Path("strategy_baselines") / f"{safe_name(strategy_id)}.json",
                        parse_json=True,
                        timeout=10,
                    )
                ] = ("baseline", strategy_id)
                futures[
                    executor.submit(
                        self.fetch,
                        channel_id,
                        "public_robot_api",
                        f"{asset_base}/content/strategyPosterInfo/{strategy_id}",
                        Path("strategy_posters") / f"{safe_name(strategy_id)}.json",
                        parse_json=True,
                        timeout=10,
                    )
                ] = ("poster", strategy_id)
            for future in concurrent.futures.as_completed(futures):
                key = futures[future]
                try:
                    ancillary_responses[key] = future.result()
                except Exception as exc:  # noqa: BLE001 - preserve the other independent responses.
                    ancillary_errors[key] = f"{type(exc).__name__}: {exc}"
        for detail in details:
            strategy_id = str(detail.get("id") or "").strip()
            if not strategy_id:
                continue
            baseline_response = ancillary_responses.get(("baseline", strategy_id))
            if baseline_response is None:
                baseline_status.append(
                    {
                        "strategy_id": strategy_id,
                        "status": "request_failed",
                        "error": ancillary_errors.get(("baseline", strategy_id), "missing_response"),
                        "has_baseline_index": False,
                    }
                )
            else:
                if isinstance(baseline_response.json_data, dict) and baseline_response.json_data.get("baselineIndex"):
                    baseline_infos[strategy_id] = {
                        **baseline_response.json_data,
                        "_source_snapshot_id": baseline_response.snapshot["snapshot_id"],
                        "_source_url": baseline_response.final_url,
                    }
                baseline_status.append(
                    {
                        "strategy_id": strategy_id,
                        "url": baseline_response.final_url,
                        "http_status": baseline_response.snapshot.get("http_status"),
                        "parse_status": baseline_response.snapshot.get("parse_status"),
                        "has_baseline_index": strategy_id in baseline_infos,
                    }
                )

            poster_response = ancillary_responses.get(("poster", strategy_id))
            if poster_response is None:
                poster_status.append(
                    {
                        "strategy_id": strategy_id,
                        "status": "request_failed",
                        "error": ancillary_errors.get(("poster", strategy_id), "missing_response"),
                        "has_contents": False,
                    }
                )
            else:
                if isinstance(poster_response.json_data, dict) and poster_response.json_data.get("contents"):
                    poster_infos[strategy_id] = {
                        **poster_response.json_data,
                        "_source_snapshot_id": poster_response.snapshot["snapshot_id"],
                        "_source_url": poster_response.final_url,
                    }
                poster_status.append(
                    {
                        "strategy_id": strategy_id,
                        "url": poster_response.final_url,
                        "http_status": poster_response.snapshot.get("http_status"),
                        "parse_status": poster_response.snapshot.get("parse_status"),
                        "has_contents": strategy_id in poster_infos,
                    }
                )

        money_total_yield_response = self.fetch(
            channel_id,
            "public_robot_api",
            f"{asset_base}/strategy/moneyfund/totalYield?startDate=20170101&endDate={self.run_at.strftime('%Y%m%d')}",
            Path("performance") / "moneyfund_totalYield.json",
            parse_json=True,
            timeout=60,
        )
        money_daily_rows = self.gfsec_robot_money_daily_rows(
            channel_id,
            "moneyfund",
            money_total_yield_response.json_data,
            money_total_yield_response.snapshot["snapshot_id"],
        )

        model_daily_rows: list[dict[str, Any]] = []
        model_curve_status: list[dict[str, Any]] = []
        curve_requests: list[tuple[dict[str, Any], dict[str, str]]] = []
        for detail in details:
            for candidate in gfsec_robot_curve_candidates(detail, self.day):
                curve_requests.append((detail, candidate))
        curve_requests.sort(key=lambda item: item[1]["kind"] != "detail_disclosed_url")

        unavailable_routes: dict[str, str] = {}
        successful_curve_strategy_ids: set[str] = set()
        for detail, candidate in curve_requests:
            strategy_id = str(detail.get("id") or "").strip()
            if not strategy_id or strategy_id in successful_curve_strategy_ids:
                continue
            candidate_url = candidate["url"]
            parsed_url = urlparse(candidate_url)
            route_key = f"{parsed_url.scheme}://{parsed_url.netloc}{parsed_url.path}"
            if route_key in unavailable_routes:
                model_curve_status.append(
                    {
                        "strategy_id": strategy_id,
                        "kind": candidate["kind"],
                        "url": candidate_url,
                        "status": "skipped_route_unavailable",
                        "route_failure": unavailable_routes[route_key],
                        "row_count": 0,
                    }
                )
                continue
            response = self.fetch(
                channel_id,
                "public_robot_api",
                candidate_url,
                Path("performance")
                / f"{safe_name(strategy_id)}_{safe_name(candidate['kind'])}.json",
                parse_json=True,
                timeout=20,
            )
            curve_rows = gfsec_robot_curve_rows(
                channel_id,
                strategy_id,
                response.json_data,
                response.snapshot.get("snapshot_id"),
            )
            matched, match_note = gfsec_robot_curve_disclosure_match(
                detail,
                curve_rows,
                require_exact_point=candidate["kind"] != "detail_disclosed_url",
            )
            http_status = response.snapshot.get("http_status")
            accepted_rows = curve_rows if curve_rows and matched else []
            model_curve_status.append(
                {
                    "strategy_id": strategy_id,
                    "kind": candidate["kind"],
                    "url": response.final_url,
                    "http_status": http_status,
                    "parse_status": response.snapshot.get("parse_status"),
                    "status": "accepted" if accepted_rows else "no_usable_curve",
                    "row_count": len(curve_rows),
                    "accepted_row_count": len(accepted_rows),
                    "disclosure_match": matched,
                    "disclosure_match_note": match_note,
                }
            )
            if accepted_rows:
                model_daily_rows.extend(accepted_rows)
                successful_curve_strategy_ids.add(strategy_id)
            if http_status in {404, 410, 502, 503}:
                unavailable_routes[route_key] = f"http_{http_status}"

        all_daily_rows = sorted(
            [*money_daily_rows, *model_daily_rows],
            key=lambda row: (str(row.get("source_strategy_id") or ""), str(row.get("trade_date") or "")),
        )

        recommendation_sources = [
            ("moneyfund_recommend_v2", f"{asset_base}/strategy/moneyfund/v2/recommend", "moneyfund", "场外货币基金优选清单", "货币基金"),
            ("moneyfund_recommend", f"{asset_base}/strategy/moneyfund/recommend", "moneyfund", "场外货币基金当前推荐", "货币基金"),
            ("moneyfund_high_level", f"{asset_base}/strategy/moneyfund/highLevelRecommend", "moneyfund", "高收益货币基金推荐", "货币基金"),
            ("moneyfund_all_yield_rates", f"{asset_base}/strategy/moneyfund/allYieldRates", "moneyfund", "场外货币基金收益清单", "货币基金"),
            ("onsite2_all", f"{asset_base}/strategy/onsite2/all", "t0moneyfund", "场内货币基金清单", "场内货币基金"),
            ("onsite_all", f"{asset_base}/strategy/onsite/all", "t0moneyfund", "场内货币基金清单", "场内货币基金"),
            ("shortbond2_products", f"{asset_base}/strategy/shortbond2/products", "shortbond", "短债产品清单", "短债基金"),
            ("shortbond_products", f"{asset_base}/strategy/shortbond/products", "shortbond", "短债产品清单", "短债基金"),
        ]
        fund_snapshots: list[dict[str, Any]] = []
        fund_dim: dict[str, dict[str, Any]] = {}
        recommendation_status: list[dict[str, Any]] = []
        for key, url, strategy_id, group_name, asset_type in recommendation_sources:
            response = self.fetch(
                channel_id,
                "public_robot_api",
                url,
                Path("recommendations") / f"{key}.json",
                parse_json=True,
            )
            items = self.gfsec_robot_recommendation_items(response.json_data)
            recommendation_status.append(
                {
                    "name": key,
                    "strategy_id": strategy_id,
                    "url": response.final_url,
                    "http_status": response.snapshot.get("http_status"),
                    "parse_status": response.snapshot.get("parse_status"),
                    "count": len(items),
                }
            )
            snapshot_id = f"{channel_id}-{strategy_id}-{key}-{self.run_id}"
            for rank, item in enumerate(items, start=1):
                row = self.gfsec_robot_fund_snapshot_row(
                    channel_id,
                    strategy_id,
                    snapshot_id,
                    item,
                    group_name,
                    asset_type,
                    rank,
                    response.final_url,
                )
                if not row:
                    continue
                row["source_snapshot_id"] = response.snapshot["snapshot_id"]
                row["recommendation_endpoint"] = key
                fund_snapshots.append(row)
                fund_code = row["fund_code"]
                fund_dim.setdefault(
                    fund_code,
                    {
                        "fund_code": fund_code,
                        "fund_name": row.get("fund_name"),
                        "fund_company": None,
                        "fund_type": row.get("fund_asset_type"),
                        "tracking_index": None,
                        "theme_tags": [row.get("fund_group_name")],
                        "source": "gfsec_robot_public_recommendation_list_not_holding",
                        "source_url": row.get("source_url"),
                        "run_id": self.run_id,
                    },
                )

        strategy_master = [
            self.gfsec_robot_strategy_master_row(
                channel_id,
                channel_name,
                detail,
                investment_base,
                baseline_infos.get(str(detail.get("id") or "").strip()),
                poster_infos.get(str(detail.get("id") or "").strip()),
            )
            for detail in details
        ]
        interval_rows = [
            row
            for detail in details
            for row in self.gfsec_robot_performance_interval_rows(channel_id, detail)
        ]
        status_counts: dict[str, int] = {}
        channel_counts: dict[str, int] = {}
        for detail in details:
            status = str(detail.get("status") or "unknown")
            channel = str(detail.get("channel") or "unknown")
            status_counts[status] = status_counts.get(status, 0) + 1
            channel_counts[channel] = channel_counts.get(channel, 0) + 1
        normal_strategy_ids = {
            str(detail.get("id") or "").strip()
            for detail in details
            if str(detail.get("status") or "").strip().lower() == "normal"
        }
        daily_strategy_ids = {
            str(row.get("source_strategy_id") or "").strip()
            for row in all_daily_rows
            if str(row.get("source_strategy_id") or "").strip()
        }
        normal_daily_strategy_ids = normal_strategy_ids & daily_strategy_ids
        daily_strategy_coverage_ratio = (
            len(normal_daily_strategy_ids) / len(normal_strategy_ids)
            if normal_strategy_ids
            else 0.0
        )

        normalized = {entity: [] for entity in DEFAULT_ENTITIES}
        normalized.update(
            {
                "strategy_master": strategy_master,
                "strategy_performance_daily": all_daily_rows,
                "strategy_performance_interval": interval_rows,
                "strategy_fund_snapshot": fund_snapshots,
                "fund_public_dim": list(fund_dim.values()),
                "app_public_entry": [
                    {
                        "channel_id": channel_id,
                        "channel_name": channel_name,
                        "source_url": landing_url,
                        "run_id": self.run_id,
                        "captured_at": self.captured_at,
                        "strategy_total": len(strategy_master),
                        "normal_strategy_total": status_counts.get("normal", 0),
                        "available_entities": [
                            "strategy_master",
                            "strategy_performance_daily",
                            "strategy_performance_interval",
                            "strategy_fund_snapshot",
                            "fund_public_dim",
                            "app_public_entry",
                        ],
                        "missing_entities": ["strategy_rebalance_event", "strategy_rebalance_fund_delta"],
                        "access_note": "匿名公开 robot 接口可取策略主数据、策略详情披露收益风险、仍可用的官方日度收益和产品推荐清单；逐策略曲线必须通过详情披露值核对，公开推荐清单不等同于精确持仓。",
                    }
                ],
            }
        )
        self.write_normalized_entities(channel_id, normalized)
        catalog_reconciliation = reconcile_catalog_batch(
            discovered_ids,
            (
                row.get("source_strategy_id")
                for row in strategy_master
                if isinstance(row, dict)
            ),
            local_strategy_ids,
        )
        summary = {
            "channel_id": channel_id,
            "channel_name": channel_name,
            "run_id": self.run_id,
            "captured_at": self.captured_at,
            "collection_status": "success_public_strategy_and_partial_performance",
            "holding_penetration_status": "public_recommendation_list_only_not_precise_holding",
            "strategy_master_ok": len(strategy_master) > 0,
            "daily_performance_ok": bool(normal_strategy_ids) and daily_strategy_coverage_ratio >= 0.95,
            "fund_level_position_ok": False,
            "recommendation_fund_list_ok": len(fund_snapshots) > 0,
            "strategy_total": len(strategy_master),
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
            "catalog_complete": catalog_complete,
            "catalog_source": "gfsec_robot.allocation_list_endpoints",
            "normal_strategy_total": status_counts.get("normal", 0),
            "deleted_strategy_total": status_counts.get("delete", 0),
            "current_holding_rows": 0,
            "daily_performance_rows": len(all_daily_rows),
            "daily_performance_strategy_total": len(daily_strategy_ids),
            "normal_daily_performance_strategy_total": len(normal_daily_strategy_ids),
            "normal_daily_performance_strategy_coverage_ratio": round(daily_strategy_coverage_ratio, 6),
            "normal_daily_performance_missing_strategy_ids": sorted(normal_strategy_ids - daily_strategy_ids),
            "model_curve_strategy_total": len(successful_curve_strategy_ids),
            "model_curve_route_failures": unavailable_routes,
            "interval_performance_rows": len(interval_rows),
            "recommendation_fund_rows": len(fund_snapshots),
            "fund_dim_total": len(fund_dim),
            "rebalance_event_total": 0,
            "baseline_code_total": len(baseline_infos),
            "strategy_poster_total": len(poster_infos),
            "status_counts": status_counts,
            "api_channel_counts": channel_counts,
            "missing_detail_ids": missing_details,
            "known_gap": "广发证券易淘金内贝塔牛理财的策略详情仍披露截至 2023-08-01 的区间收益、回撤和基准，但当前模型曲线路由已返回 404/502；采集器已按详情 URL、易淘金 13.3.5 APK 的 info.gf.com.cn 生产代理、线上 H5 2.0.0 接口和旧版接口依次验证，并对生成式候选执行披露值核对，未通过时不伪造曲线。匿名接口仍未披露精确持仓、真实计划持仓和历史调仓。",
        }
        inventory = {
            "primary_sources": [
                landing_url,
                f"{investment_base}/strategy/{{strategy_id}}",
                f"{asset_base}/models/{{strategy_id}}/baselineCode",
                f"{asset_base}/content/strategyPosterInfo/{{strategy_id}}",
                f"{asset_base}/strategy/moneyfund/totalYield",
                "https://info.gf.com.cn/api/1.0.0/centerproxy/ytj/assetallocation/strategy/model/yield",
                f"{asset_base}/strategy/moneyfund/v2/recommend",
                f"{asset_base}/strategy/shortbond/products",
                f"{asset_base}/strategy/onsite/all",
            ],
            "method": "从广发证券易淘金 13.3.5 APK 与当前线上 asset H5 静态资源交叉定位生产代理、2.0.0 与详情披露接口，并用匿名公开 API 采集策略详情、区间业绩和推荐基金清单；曲线必须通过同日官方披露值核对。",
            "list_endpoints": endpoint_status,
            "baseline_endpoints": baseline_status,
            "poster_endpoints": poster_status,
            "performance_endpoints": [
                {
                    "name": "moneyfund_totalYield",
                    "url": money_total_yield_response.final_url,
                    "http_status": money_total_yield_response.snapshot.get("http_status"),
                    "parse_status": money_total_yield_response.snapshot.get("parse_status"),
                    "row_count": len(money_daily_rows),
                }
            ]
            + model_curve_status,
            "recommendation_endpoints": recommendation_status,
            "seed_strategy_ids": seed_strategy_ids,
            "catalog_strategy_total": int(catalog_reconciliation.get("catalog_strategy_total") or 0),
            "catalog_strategy_ids": list(catalog_reconciliation.get("catalog_strategy_ids") or []),
            "catalog_new_strategy_total": int(catalog_reconciliation.get("new_strategy_total") or 0),
            "catalog_new_strategy_ids": list(catalog_reconciliation.get("new_strategy_ids") or []),
            "catalog_batch_missing_strategy_ids": list(
                catalog_reconciliation.get("catalog_batch_missing_strategy_ids") or []
            ),
            "catalog_batch_closed": bool(catalog_reconciliation.get("catalog_batch_closed")),
            "catalog_complete": catalog_complete,
            "raw_snapshots": self.snapshots.get(channel_id, []),
        }
        summary["output_paths"] = self.write_app_outputs(channel_id, normalized, summary, inventory)
        return summary

    @staticmethod
    def gfsec_robot_recommendation_items(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if not isinstance(payload, dict):
            return []
        data = payload.get("data")
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            return [data]
        for key in ["result", "rows", "list"]:
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return []

    @staticmethod
    def gfsec_robot_fund_snapshot_row(
        channel_id: str,
        strategy_id: str,
        snapshot_id: str,
        item: dict[str, Any],
        group_name: str,
        asset_type: str,
        rank: int,
        source_url: str,
    ) -> dict[str, Any] | None:
        fund_code = str(item.get("productCode") or item.get("fundCode") or item.get("tradingCode") or item.get("code") or "").strip()
        fund_name = str(item.get("productName") or item.get("fundName") or item.get("name") or item.get("shortName") or "").strip()
        if not fund_code or not fund_name:
            return None
        disclosure_date = (
            parse_date_yyyymmdd(item.get("date"))
            or parse_millis_date(item.get("dateTime"))
            or parse_millis_date(item.get("yieldRateUpdateTime"))
        )
        return {
            "snapshot_id": snapshot_id,
            "channel_id": channel_id,
            "source_strategy_id": strategy_id,
            "position_date": now_local().date().isoformat(),
            "disclosure_date": disclosure_date,
            "fund_code": fund_code,
            "fund_name": fund_name,
            "fund_asset_type": asset_type,
            "fund_group_name": group_name,
            "fund_weight": None,
            "fund_nav": None,
            "fund_nav_date": None,
            "is_precise_weight": False,
            "is_login_required": False,
            "source_url": source_url,
            "raw_record_hash": hashlib.sha1(compact_json(item).encode("utf-8")).hexdigest(),
            "confidence_level": "public_recommendation_list_not_holding",
            "access_level": "public",
            "recommend_rank": rank,
            "risk_level": item.get("riskCode") or item.get("riskLevel"),
            "annual_yield": parse_float(item.get("annuYield") or item.get("annualYield")),
            "yield_1m": parse_float(item.get("yield1m")),
            "yield_3m": parse_float(item.get("yield3m")),
            "yield_6m": parse_float(item.get("yield6m")),
            "yield_1y": parse_float(item.get("yield1y")),
            "available": item.get("available"),
            "gf_selling": item.get("gfSelling"),
            "fund_level": item.get("fundLevel"),
        }

    @staticmethod
    def gfsec_robot_money_daily_rows(
        channel_id: str,
        strategy_id: str,
        payload: Any,
        source_snapshot_id: str | None,
    ) -> list[dict[str, Any]]:
        rows = gfsec_robot_curve_rows(channel_id, strategy_id, payload, source_snapshot_id)
        for row in rows:
            row["section_name"] = "货币策略指数"
            row["section_type"] = "official_totalYield"
        return rows

    @staticmethod
    def gfsec_robot_performance_interval_rows(channel_id: str, detail: dict[str, Any]) -> list[dict[str, Any]]:
        others = detail.get("others") if isinstance(detail.get("others"), dict) else {}
        performance = others.get("performance") if isinstance(others.get("performance"), dict) else {}
        if not performance:
            return []
        strategy_id = str(detail.get("id") or "").strip()
        if not strategy_id:
            return []
        as_of_date = parse_millis_date(performance.get("busiDate")) or now_local().date().isoformat()
        interval_map = [
            ("1d", "近1日", "yield1d"),
            ("1m", "近1月", "yield1m"),
            ("3m", "近3月", "yield3m"),
            ("6m", "近6月", "yield6m"),
            ("1y", "近1年", "yield1y"),
            ("2y", "近2年", "yield2y"),
            ("3y", "近3年", "yield3y"),
            ("std", "官方披露累计", "yield"),
        ]
        rows: list[dict[str, Any]] = []
        for interval_code, interval_label, field in interval_map:
            value = pct_decimal_to_percent(performance.get(field))
            if value is None:
                continue
            rows.append(
                {
                    "channel_id": channel_id,
                    "source_strategy_id": strategy_id,
                    "as_of_date": as_of_date,
                    "interval_code": interval_code,
                    "interval_label": interval_label,
                    "return_value": value,
                    "benchmark_return": None,
                    "source_snapshot_id": detail.get("_source_snapshot_id"),
                    "source_field": f"others.performance.{field}",
                }
            )
        return rows

    @staticmethod
    def gfsec_robot_strategy_master_row(
        channel_id: str,
        channel_name: str,
        detail: dict[str, Any],
        investment_base: str,
        baseline_info: dict[str, Any] | None = None,
        poster_info: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        others = detail.get("others") if isinstance(detail.get("others"), dict) else {}
        performance = others.get("performance") if isinstance(others.get("performance"), dict) else {}
        settings = others.get("settings") if isinstance(others.get("settings"), dict) else {}
        user_define = others.get("userDefine") if isinstance(others.get("userDefine"), dict) else {}
        strategy_id = str(detail.get("id") or "").strip()
        tags: list[str] = []
        for value in [
            "广发证券",
            "易淘金",
            "贝塔牛理财",
            detail.get("status"),
            detail.get("channel"),
            detail.get("type"),
            others.get("category"),
            others.get("subType"),
            *(others.get("tags") if isinstance(others.get("tags"), list) else []),
        ]:
            text = str(value or "").strip()
            if text and text not in tags:
                tags.append(text)
        desc_parts = [strip_html(str(detail.get("desc") or "")), strip_html(str(user_define.get("slogan") or ""))]
        description = "；".join(part for part in desc_parts if part)
        min_money = parse_float(settings.get("leastMoney"))
        return {
            "channel_id": channel_id,
            "source_strategy_id": strategy_id,
            "strategy_name": str(detail.get("name") or strategy_id),
            "advisor_name": channel_name,
            "strategy_type": str(detail.get("type") or "allocation"),
            "risk_level": f"R{detail.get('riskLevel')}" if detail.get("riskLevel") else None,
            "launch_date": parse_date_yyyymmdd(others.get("createDate")),
            "suggested_holding_period": None,
            "minimum_amount": min_money,
            "advisory_fee_rate": None,
            # baseLine is often only the generic label "业绩基准"; baseText carries
            # the actual disclosed formula (for example 30% equity + 70% bond).
            "benchmark": performance.get("baseText") or performance.get("baseLine"),
            "tags": tags,
            "strategy_description": description or None,
            "status": "stopped" if str(detail.get("status") or "").strip().lower() == "delete" else detail.get("status"),
            "source_url": detail.get("_source_url") or f"{investment_base}/strategy/{strategy_id}",
            "first_seen_at": parse_date_yyyymmdd(others.get("createDate")) or now_local().date().isoformat(),
            "last_seen_at": now_local().date().isoformat(),
            "api_channel": detail.get("channel"),
            "api_update_at": detail.get("updateAt"),
            "source_snapshot_id": detail.get("_source_snapshot_id"),
            "performance_yield": parse_float(performance.get("yield")),
            "performance_yield_1m": parse_float(performance.get("yield1m")),
            "performance_yield_1d": parse_float(performance.get("yield1d")),
            "performance_busi_date": parse_millis_date(performance.get("busiDate")),
            "performance_desc": strip_html(str(performance.get("desc") or "")) or None,
            "product_code": (others.get("product") or {}).get("code") if isinstance(others.get("product"), dict) else None,
            "baseline_index_code": (baseline_info or {}).get("baselineIndex"),
            "strategy_poster_url": (poster_info or {}).get("_source_url"),
            "strategy_poster_text": strip_html(str((poster_info or {}).get("contents") or "")) or None,
            "extra": {
                "order": detail.get("order"),
                "source_status": detail.get("status"),
                "like": others.get("like"),
                "settings": settings,
                "userDefine": user_define,
                "maxDown": others.get("maxDown"),
                "overview": others.get("overview"),
                "product": others.get("product"),
                "category": others.get("category"),
                "subType": others.get("subType"),
                "performance": performance,
                "baseline": baseline_info,
                "poster": poster_info,
            },
            "raw_extra": {
                "order": detail.get("order"),
                "like": others.get("like"),
                "settings": settings,
                "userDefine": user_define,
                "maxDown": others.get("maxDown"),
                "performance": performance,
                "baseline": baseline_info,
                "poster": poster_info,
            },
        }

    def collect_gfbank_cgb(self) -> dict[str, Any]:
        channel_id = "gfbank_cgb"
        channel_name = "广发银行发现精彩"
        entry_urls = [
            ("fund_home", "https://wap.cgbchina.com.cn/cgb_fund/index/fund_home_page.html", "基金频道"),
            ("invest_precinct", "https://wap.cgbchina.com.cn/cgb_invest_precinct/invest_precinct/index.html", "定投专区"),
            ("gfzt_content", "https://wap.cgbchina.com.cn/cgb_content/index/index.html?pageCode=A200017400", "广发智投"),
            ("fund_schedule_content", "https://wap.cgbchina.com.cn/cgb_content/index/index.html?pageCode=A300900990", "基金定投内容页"),
            ("asset_allocation", "https://wap.cgbchina.com.cn/cgb_asset_allocation/asset_allocation/index.html", "360资产配置"),
            ("discount_fund", "https://wap.cgbchina.com.cn/cgb_discount_fund/discount_fund/index.html", "基金折扣专区"),
            ("fund_schedule_rank", "https://wap.cgbchina.com.cn/cgb_fund/performance_rank/index.html?param=first", "基金定投排行"),
            ("wealth_diagnose", "https://wap.cgbchina.com.cn/wxWealthDiagnose.do", "财富诊断"),
            ("wealth_num", "https://wap.cgbchina.com.cn/mdp/hmba/wealthNum/page/index?srcChannel=CS&src_channel=CS", "财富号"),
        ]
        keyword_patterns = ["投顾", "智能投顾", "基金", "定投", "组合", "策略", "gdb_zhinengtg", "gdb_fund", "advisor", "invest"]
        app_entries: list[dict[str, Any]] = []
        fetched_script_urls: set[str] = set()
        script_hits: list[dict[str, Any]] = []
        for key, url, label in entry_urls:
            response = self.fetch(channel_id, "public_h5_entry", url, Path("entries") / f"{key}.html")
            page_hits = self.text_keyword_hits(response.text, keyword_patterns)
            script_urls = self.extract_asset_urls(response.text, response.final_url, "script")
            app_entries.append(
                {
                    "channel_id": channel_id,
                    "channel_name": channel_name,
                    "source_url": response.final_url,
                    "entry_key": key,
                    "entry_label": label,
                    "http_status": response.snapshot.get("http_status"),
                    "parse_status": response.snapshot.get("parse_status"),
                    "title": parse_title(response.text),
                    "keyword_hits": page_hits,
                    "script_count": len(script_urls),
                    "source_snapshot_id": response.snapshot["snapshot_id"],
                    "run_id": self.run_id,
                    "captured_at": self.captured_at,
                }
            )
            for index, script_url in enumerate(script_urls[:20], start=1):
                if script_url in fetched_script_urls:
                    continue
                fetched_script_urls.add(script_url)
                script_response = self.fetch(
                    channel_id,
                    "public_h5_entry",
                    script_url,
                    Path("scripts") / f"{safe_name(key)}_{index:02d}_{safe_name(Path(urlparse(script_url).path).name)}",
                )
                hits = self.text_keyword_hits(script_response.text, keyword_patterns)
                if hits:
                    script_hits.append(
                        {
                            "entry_key": key,
                            "script_url": script_response.final_url,
                            "http_status": script_response.snapshot.get("http_status"),
                            "keyword_hits": hits,
                            "source_snapshot_id": script_response.snapshot["snapshot_id"],
                        }
                    )

        static_evidence = self.gfbank_static_evidence()
        normalized = self.load_gfbank_authenticated_cache()
        authenticated_summary = normalized.pop("_authenticated_summary", {})
        authenticated_entries = normalized.get("app_public_entry", [])
        normalized["app_public_entry"] = [*app_entries, *authenticated_entries]
        self.write_normalized_entities(channel_id, normalized)
        strategy_like_hits = [
            entry
            for entry in [*app_entries, *script_hits]
            if any(keyword in compact_json(entry.get("keyword_hits") or []) for keyword in ["投顾", "智能投顾", "gdb_zhinengtg", "advisor"])
        ]
        authenticated_strategy_total = len(normalized.get("strategy_master", []))
        authenticated_daily_total = len(normalized.get("strategy_performance_daily", []))
        authenticated_interval_total = len(normalized.get("strategy_performance_interval", []))
        has_authenticated_cache = authenticated_strategy_total > 0
        summary = {
            "channel_id": channel_id,
            "channel_name": channel_name,
            "run_id": self.run_id,
            "captured_at": self.captured_at,
            "collection_status": (
                "success_public_entries_and_authenticated_cache"
                if has_authenticated_cache
                else "success_public_entries_only"
            ),
            "holding_penetration_status": (
                "authenticated_strategy_master_no_fund_holdings"
                if has_authenticated_cache
                else "not_available_public_h5_or_login_required"
            ),
            "strategy_total": authenticated_strategy_total,
            "current_holding_rows": 0,
            "rebalance_event_total": 0,
            "daily_performance_rows": authenticated_daily_total,
            "interval_performance_rows": authenticated_interval_total,
            "strategy_master_ok": has_authenticated_cache,
            "daily_performance_ok": authenticated_daily_total > 0,
            "interval_performance_ok": authenticated_interval_total > 0,
            "detail_coverage_ratio": authenticated_summary.get("detail_coverage_ratio"),
            "interval_strategy_coverage_ratio": authenticated_summary.get(
                "interval_strategy_coverage_ratio"
            ),
            "fund_level_position_ok": False,
            "rebalance_event_ok": False,
            "rebalance_fund_delta_ok": False,
            "public_entry_total": len(app_entries),
            "script_keyword_hit_total": len(script_hits),
            "strategy_like_public_hit_total": len(strategy_like_hits),
            "authenticated_cache_run_id": authenticated_summary.get("run_id"),
            "authenticated_cache_captured_at": authenticated_summary.get("captured_at"),
            "known_gap": authenticated_summary.get("known_gap")
            or (
                "公开 H5 未发现可匿名返回投顾策略主数据、持仓或调仓的接口；"
                "尚未取得渠道官方策略ID、完整日度曲线、基金代码与权重、调仓事件和调仓明细。"
            ),
        }
        inventory = {
            "primary_sources": [url for _, url, _ in entry_urls],
            "method": "从广发银行发现精彩 APK 静态配置提取基金/智能投顾相关 H5 入口，再匿名抓取入口页及脚本并扫描投顾/基金/策略关键词。",
            "public_h5_entries": app_entries,
            "script_keyword_hits": script_hits,
            "apk_static_evidence": static_evidence,
            "raw_snapshots": self.snapshots.get(channel_id, []),
        }
        summary["output_paths"] = self.write_app_outputs(channel_id, normalized, summary, inventory)
        return summary

    def load_gfbank_authenticated_cache(self) -> dict[str, Any]:
        cache_dir = self.project_root / "official_apps" / "gfbank_cgb" / "authenticated_cache"
        normalized: dict[str, Any] = {entity: [] for entity in DEFAULT_ENTITIES}
        for entity in DEFAULT_ENTITIES:
            path = cache_dir / f"{entity}.jsonl"
            if not path.is_file():
                continue
            rows: list[dict[str, Any]] = []
            with path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    text = line.strip()
                    if text:
                        rows.append(json.loads(text))
            normalized[entity] = rows
        summary_path = cache_dir / "latest_summary.json"
        normalized["_authenticated_summary"] = (
            json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.is_file() else {}
        )
        return normalized

    @staticmethod
    def extract_asset_urls(text: str, base_url: str, tag: str) -> list[str]:
        attr = "src" if tag == "script" else "href"
        urls: list[str] = []
        for match in re.finditer(rf"<{tag}\b[^>]*>", text, flags=re.I):
            value = OfficialAppsPublicCollector.attr_value(match.group(0), attr)
            if not value:
                continue
            absolute = urljoin(base_url, value)
            if absolute not in urls:
                urls.append(absolute)
        return urls

    @staticmethod
    def text_keyword_hits(text: str, keywords: list[str]) -> list[str]:
        low_text = text.lower()
        hits: list[str] = []
        for keyword in keywords:
            if (keyword.lower() if keyword.isascii() else keyword) in (low_text if keyword.isascii() else text):
                hits.append(keyword)
        return hits

    def gfbank_static_evidence(self) -> dict[str, Any]:
        root = self.project_root / "outputs" / "gf_channel_probe" / "apk_static" / "gfbank_extract"
        evidence: dict[str, Any] = {"static_extract_dir": str(root), "exists": root.exists(), "files": []}
        jump_links: list[dict[str, Any]] = []
        relevant_codes = [
            "gdb_fund",
            "gdb_gfzt",
            "gdb_fund_schedule",
            "gdb_zhinengtg_dt_area",
            "cgb_asset_allocation",
            "CGB_DISCOUNT_FUND",
        ]
        for name in [
            "assets__appThirdUrls.json",
            "assets__appUnwantedLoginIds.json",
            "assets__flutter_assets__json__A139000006.json",
            "res__raw__a139000005.json",
            "_json_search_summary.json",
        ]:
            path = root / name
            item: dict[str, Any] = {"path": str(path), "exists": path.exists()}
            if path.exists():
                text = path.read_text(encoding="utf-8-sig", errors="replace")
                clean_text = html.unescape(text).replace("\\/", "/").replace('\\"', '"')
                item["size"] = len(text.encode("utf-8"))
                item["keyword_hits"] = self.text_keyword_hits(
                    clean_text,
                    ["gdb_zhinengtg", "gdb_zhinengtg_dt", "gdb_zhinengtg_zt", "gdb_fund", "gdb_fund_schedule", "cgb_financeTA_list", "invest", "fund", "wealth"],
                )
                for code in relevant_codes:
                    idx = clean_text.find(f'"jumpLinkConfigCode":"{code}"')
                    if idx < 0:
                        continue
                    window = clean_text[idx : idx + 2400]
                    link_match = re.search(r'"jumpLink":"([^"]+)"', window)
                    name_match = re.search(r'"name":"([^"]*)"', window)
                    login_match = re.search(r'loginedFlag["\\: ]+([01])', window)
                    jump_link = {
                        "source_file": name,
                        "jumpLinkConfigCode": code,
                        "name": name_match.group(1) if name_match else None,
                        "jumpLink": link_match.group(1) if link_match else None,
                        "loginedFlag": login_match.group(1) if login_match else None,
                    }
                    if jump_link not in jump_links:
                        jump_links.append(jump_link)
            evidence["files"].append(item)
        evidence["jump_links"] = jump_links
        return evidence

    def collect_fullgoal(self) -> dict[str, Any]:
        channel_id = "fullgoal"
        channel_name = "富国基金/富钱包星投顾"
        landing_url = "https://www.fullgoal.com.cn/mobile/tougu/"
        doc_urls = [
            "https://www.fullgoal.com.cn/mobile/tougu/shouhuxing.html",
            "https://www.fullgoal.com.cn/mobile/tougu/qimingxing.html",
            "https://www.fullgoal.com.cn/mobile/tougu/shuangzixing.html",
            "https://www.fullgoal.com.cn/mobile/tougu/mantianxing.html",
        ]
        landing = self.fetch(channel_id, "public_site", landing_url, "index.html")
        self.fetch(channel_id, "public_site", urljoin(landing_url, "index1.html"), "index1.html")

        label_by_slug = {
            "shouhuxing": "守护星稳健理财系列",
            "qimingxing": "启明星理财升级系列",
            "shuangzixing": "双子星股债均衡系列",
            "mantianxing": "满天星追求收益系列",
        }
        strategy_master: list[dict[str, Any]] = []
        fetched_docs: list[dict[str, Any]] = []
        for order, url in enumerate(doc_urls):
            slug = Path(urlparse(url).path).stem
            response = self.fetch(channel_id, "strategy_doc", url, f"{slug}.html")
            if response.snapshot.get("http_status") != 200:
                continue
            plain = strip_html(response.text)
            name = self.extract_section(plain, "一、组合名称", "二、投资目标")
            target = self.extract_section(plain, "二、投资目标", "三、业绩基准")
            benchmark = self.extract_section(plain, "三、业绩基准", "四、风险特征")
            risk_text = self.extract_section(plain, "四、风险特征", "五、适合投资者范围")
            investor_scope = self.extract_section(plain, "五、适合投资者范围（根据组合风险特征决定）", "六、投资范围及限制")
            investment_scope = self.extract_section(plain, "六、投资范围及限制", "七、备选基金产品评估情况")
            fee_text = self.extract_section(plain, "十、投资顾问服务费率", "投顾组合策略", "跟投服务规则说明")
            strategy_name = name or label_by_slug.get(slug) or slug
            strategy_master.append(
                {
                    "channel_id": channel_id,
                    "source_strategy_id": slug,
                    "strategy_name": strategy_name,
                    "advisor_name": channel_name,
                    "strategy_type": label_by_slug.get(slug),
                    "risk_level": self.risk_level_from_text(risk_text),
                    "launch_date": None,
                    "suggested_holding_period": None,
                    "minimum_amount": None,
                    "advisory_fee_rate": self.fee_rate_from_text(fee_text),
                    "benchmark": benchmark,
                    "tags": ["富国星投顾", "富钱包", label_by_slug.get(slug) or slug],
                    "status": "public_strategy_doc",
                    "strategy_description": target,
                    "source_url": url,
                    "source_snapshot_id": response.snapshot["snapshot_id"],
                    "first_seen_at": self.captured_at,
                    "last_seen_at": self.captured_at,
                    "run_id": self.run_id,
                    "extra": {
                        "source_order": order,
                        "investor_scope": investor_scope,
                        "investment_scope": investment_scope,
                        "asset_range_text": self.asset_range_text(investment_scope),
                        "fee_section": fee_text,
                    },
                }
            )
            fetched_docs.append(
                {
                    "url": url,
                    "snapshot_id": response.snapshot["snapshot_id"],
                    "strategy_name": strategy_name,
                }
            )

        normalized = {
            "strategy_master": strategy_master,
            "strategy_performance_daily": [],
            "strategy_fund_snapshot": [],
            "strategy_rebalance_event": [],
            "strategy_rebalance_fund_delta": [],
            "fund_public_dim": [],
            "app_public_entry": [
                {
                    "channel_id": channel_id,
                    "channel_name": channel_name,
                    "title": parse_title(landing.text),
                    "source_url": landing_url,
                    "run_id": self.run_id,
                    "captured_at": self.captured_at,
                    "strategy_total": len(strategy_master),
                    "strategy_doc_urls": fetched_docs,
                    "available_entities": ["strategy_master", "app_public_entry"],
                    "missing_entities": [
                        "strategy_performance_daily",
                        "strategy_fund_snapshot",
                        "strategy_rebalance_event",
                        "strategy_rebalance_fund_delta",
                    ],
                    "login_hint": "公开策略说明书披露投资范围、风险、业绩基准和费率；实际基金持仓、业绩和调仓明细需富钱包 App/交易端。",
                }
            ],
            "strategy_disclosure_event": [],
        }
        self.write_normalized_entities(channel_id, normalized)
        summary = {
            "channel_id": channel_id,
            "channel_name": channel_name,
            "run_id": self.run_id,
            "captured_at": self.captured_at,
            "collection_status": "success_public_strategy_docs",
            "holding_penetration_status": "blocked_app_or_login_required",
            "strategy_total": len(strategy_master),
            "daily_rows_total": 0,
            "current_holding_rows": 0,
            "rebalance_event_total": 0,
            "rebalance_fund_delta_total": 0,
            "fund_dim_total": 0,
            "known_gap": "富国公开策略说明书可取得资产类别仓位区间、风险、基准和费率，但未披露具体基金占比和每次调仓基金明细。",
        }
        inventory = {
            "primary_sources": [landing_url, *doc_urls],
            "method": "抓取富国星投顾 H5 入口及四个公开策略说明书，按章节解析组合名称、目标、基准、风险、投资范围和费率。",
            "raw_snapshots": self.snapshots.get(channel_id, []),
        }
        summary["output_paths"] = self.write_app_outputs(channel_id, normalized, summary, inventory)
        return summary

    def collect_fund99(self) -> dict[str, Any]:
        channel_id = "fund99"
        channel_name = "汇添富基金/现金宝投顾"
        source_url = "https://qy.99fund.com/info/investment_adviser.htm"
        page = self.fetch(channel_id, "public_site", source_url, "investment_adviser.html")
        strategy_master: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in re.findall(r"(?is)<tr[^>]*>(.*?)</tr>", page.text):
            cells = re.findall(r"(?is)<td[^>]*>(.*?)</td>", row)
            if len(cells) < 2:
                continue
            names = [strip_html(item) for item in re.findall(r"(?is)<p[^>]*>(.*?)</p>", cells[0])]
            if not names:
                names = [strip_html(cells[0])]
            fee_rate = strip_html(cells[1])
            for name in names:
                if not name or name in {"策略名称", "投顾策略", "组合名称"} or name in seen:
                    continue
                seen.add(name)
                strategy_master.append(
                    {
                        "channel_id": channel_id,
                        "source_strategy_id": stable_id("fund99", name),
                        "strategy_name": name,
                        "advisor_name": channel_name,
                        "strategy_type": "投顾服务",
                        "risk_level": None,
                        "launch_date": None,
                        "suggested_holding_period": None,
                        "minimum_amount": None,
                        "advisory_fee_rate": fee_rate,
                        "benchmark": None,
                        "tags": ["汇添富投顾", "现金宝", "公开费率表"],
                        "status": "public_fee_list",
                        "strategy_description": None,
                        "source_url": source_url,
                        "source_snapshot_id": page.snapshot["snapshot_id"],
                        "first_seen_at": self.captured_at,
                        "last_seen_at": self.captured_at,
                        "run_id": self.run_id,
                        "extra": {"public_fee_rate": fee_rate},
                    }
                )

        normalized = {
            "strategy_master": strategy_master,
            "strategy_performance_daily": [],
            "strategy_fund_snapshot": [],
            "strategy_rebalance_event": [],
            "strategy_rebalance_fund_delta": [],
            "fund_public_dim": [],
            "app_public_entry": [
                {
                    "channel_id": channel_id,
                    "channel_name": channel_name,
                    "title": parse_title(page.text),
                    "source_url": source_url,
                    "run_id": self.run_id,
                    "captured_at": self.captured_at,
                    "strategy_total": len(strategy_master),
                    "available_entities": ["strategy_master", "app_public_entry"],
                    "missing_entities": [
                        "strategy_performance_daily",
                        "strategy_fund_snapshot",
                        "strategy_rebalance_event",
                        "strategy_rebalance_fund_delta",
                    ],
                    "login_hint": "企业版公开帮助页仅披露投顾服务费率；投顾资产、交易记录、持仓和调仓需要进入“我的投顾”登录态。",
                }
            ],
            "strategy_disclosure_event": [],
        }
        self.write_normalized_entities(channel_id, normalized)
        summary = {
            "channel_id": channel_id,
            "channel_name": channel_name,
            "run_id": self.run_id,
            "captured_at": self.captured_at,
            "collection_status": "success_public_fee_list",
            "holding_penetration_status": "blocked_app_or_login_required",
            "strategy_total": len(strategy_master),
            "daily_rows_total": 0,
            "current_holding_rows": 0,
            "rebalance_event_total": 0,
            "rebalance_fund_delta_total": 0,
            "fund_dim_total": 0,
            "known_gap": "汇添富公开页可解析策略名称和投顾服务费率，未公开基金级持仓、每次调仓和业绩明细。",
        }
        inventory = {
            "primary_sources": [source_url],
            "method": "抓取汇添富企业版投顾服务帮助页，解析策略费率表并标准化为 strategy_master。",
            "raw_snapshots": self.snapshots.get(channel_id, []),
        }
        summary["output_paths"] = self.write_app_outputs(channel_id, normalized, summary, inventory)
        return summary

    def collect_qieman(self) -> dict[str, Any]:
        channel_id = "qieman"
        channel_name = "且慢/盈米基金"
        source_url = "https://qieman.com/app"
        page = self.fetch(channel_id, "public_site", source_url, "app.html")
        meta: dict[str, str] = {}
        for tag in re.findall(r"(?is)<meta[^>]+>", page.text):
            key = self.attr_value(tag, "name") or self.attr_value(tag, "property")
            content = self.attr_value(tag, "content")
            if key and content:
                meta[key] = content
        script_urls = [
            urljoin(source_url, src)
            for src in re.findall(r"(?is)<script[^>]+src=['\"]([^'\"]+)['\"]", page.text)
        ]
        observed_paths: set[str] = set()
        fetched_scripts: list[str] = []
        for script_url in script_urls:
            if not script_url.endswith(".js"):
                continue
            script_name = safe_name(Path(urlparse(script_url).path).name)
            response = self.fetch(channel_id, "spa_static", script_url, Path("static") / script_name)
            fetched_scripts.append(script_url)
            for match in re.findall(
                r"""['"]([^'"]{0,180}(?:advisor|portfolio|strategy|fund|composition|rebalance|holding)[^'"]{0,180})['"]""",
                response.text,
                flags=re.I,
            ):
                text = match.strip()
                looks_like_path = text.startswith(("/", "http")) or bool(
                    re.fullmatch(r"[A-Za-z0-9_./:@?&=%-]{3,180}", text)
                )
                if 2 <= len(text) <= 220 and "\n" not in text and looks_like_path:
                    observed_paths.add(text)

        normalized = {
            "strategy_master": [],
            "strategy_performance_daily": [],
            "strategy_fund_snapshot": [],
            "strategy_rebalance_event": [],
            "strategy_rebalance_fund_delta": [],
            "fund_public_dim": [],
            "app_public_entry": [
                {
                    "channel_id": channel_id,
                    "channel_name": channel_name,
                    "title": parse_title(page.text),
                    "source_url": source_url,
                    "run_id": self.run_id,
                    "captured_at": self.captured_at,
                    "available_entities": ["app_public_entry", "spa_static_assets"],
                    "missing_entities": [
                        "strategy_master",
                        "strategy_performance_daily",
                        "strategy_fund_snapshot",
                        "strategy_rebalance_event",
                        "strategy_rebalance_fund_delta",
                    ],
                    "meta": meta,
                    "script_urls": fetched_scripts,
                    "observed_keyword_paths": sorted(observed_paths)[:100],
                    "login_hint": "且慢公开入口为 SPA/下载页；静态资源中可见投顾相关前端路径词，但产品、仓位、业绩和调仓数据未在公开 HTML 中直接披露。",
                }
            ],
            "strategy_disclosure_event": [],
        }
        self.write_normalized_entities(channel_id, normalized)
        summary = {
            "channel_id": channel_id,
            "channel_name": channel_name,
            "run_id": self.run_id,
            "captured_at": self.captured_at,
            "collection_status": "success_spa_entry_only",
            "holding_penetration_status": "blocked_app_or_auth_required",
            "strategy_total": 0,
            "daily_rows_total": 0,
            "current_holding_rows": 0,
            "rebalance_event_total": 0,
            "rebalance_fund_delta_total": 0,
            "fund_dim_total": 0,
            "observed_keyword_path_total": len(observed_paths),
            "known_gap": "且慢公开入口未直接披露策略清单、基金级持仓、业绩或调仓；后续需要 App/H5 登录态或授权接口。",
        }
        inventory = {
            "primary_sources": [source_url, *fetched_scripts],
            "method": "抓取且慢公开入口 HTML 和关联 JS 静态资源，保存原始快照并提取投顾相关前端路径关键词作为后续接口探测线索。",
            "raw_snapshots": self.snapshots.get(channel_id, []),
        }
        summary["output_paths"] = self.write_app_outputs(channel_id, normalized, summary, inventory)
        return summary

    def collect_harvestwm(self) -> dict[str, Any]:
        channel_id = "harvestwm"
        channel_name = "嘉实财富"
        product_page = self.fetch(
            channel_id,
            "public_site",
            "https://www.harvestwm.cn/product/customize_acco",
            "product_customize_acco.html",
        )
        first_page = self.fetch(
            channel_id,
            "notice_index",
            "https://www.harvestwm.cn/about/notice",
            Path("notice_pages") / "page_0001.html",
        )
        page_count_match = re.search(r"pageCount\s*:\s*(\d+)", first_page.text)
        page_count = int(page_count_match.group(1)) if page_count_match else 1
        if self.harvest_pages:
            page_count = min(page_count, self.harvest_pages)
        notices = self.parse_harvest_notices(first_page.text)
        for page_num in range(2, page_count + 1):
            page = self.fetch(
                channel_id,
                "notice_index",
                f"https://www.harvestwm.cn/about/notice?page={page_num}",
                Path("notice_pages") / f"page_{page_num:04d}.html",
            )
            notices.extend(self.parse_harvest_notices(page.text))

        relevant = [
            notice
            for notice in notices
            if any(key in notice["title"] for key in ["组合方案说明书", "信息披露提醒", "投顾"])
        ]
        disclosure_events: list[dict[str, Any]] = []
        strategy_map: dict[str, dict[str, Any]] = {}
        for notice in relevant:
            notice_id = notice["href"].rstrip("/").split("/")[-1]
            detail = self.fetch(
                channel_id,
                "notice_detail",
                urljoin("https://www.harvestwm.cn", notice["href"]),
                Path("notice_details") / f"{notice_id}.html",
            )
            text = self.extract_harvest_article_text(detail.text)
            links = self.extract_links(detail.text, urljoin("https://www.harvestwm.cn", notice["href"]))
            pdf_links = [link for link in links if urlparse(link).path.lower().endswith(".pdf")]
            image_links = [
                link
                for link in re.findall(r'(?is)<img[^>]+src="([^"]+)"', detail.text)
                if link
            ]
            image_links = [urljoin("https://www.harvestwm.cn", link) for link in image_links]
            for pdf_url in pdf_links:
                parsed = urlparse(pdf_url)
                self.fetch(
                    channel_id,
                    "notice_attachment",
                    pdf_url,
                    Path("notice_attachments") / notice_id / safe_name(Path(parsed.path).name),
                )
            strategy_name = self.extract_harvest_strategy_name(notice["title"], text)
            risk_change = self.extract_risk_change(text)
            strategy_id = stable_id("harvest", strategy_name) if strategy_name else None
            disclosure_events.append(
                {
                    "channel_id": channel_id,
                    "source_strategy_id": strategy_id,
                    "strategy_name": strategy_name,
                    "event_id": stable_id("harvest_notice", notice["href"]),
                    "event_date": parse_date_yyyymmdd(notice["date"]),
                    "event_title": notice["title"],
                    "event_type": "scheme_update"
                    if "组合方案说明书" in notice["title"]
                    else "information_disclosure_reminder",
                    "source_url": urljoin("https://www.harvestwm.cn", notice["href"]),
                    "source_snapshot_id": detail.snapshot["snapshot_id"],
                    "pdf_links": pdf_links,
                    "image_links": image_links,
                    "risk_change": risk_change,
                    "run_id": self.run_id,
                }
            )
            if strategy_name and strategy_id:
                current = strategy_map.setdefault(
                    strategy_id,
                    {
                        "channel_id": channel_id,
                        "source_strategy_id": strategy_id,
                        "strategy_name": strategy_name,
                        "advisor_name": channel_name,
                        "strategy_type": "公募基金投顾组合",
                        "risk_level": None,
                        "launch_date": None,
                        "suggested_holding_period": None,
                        "minimum_amount": None,
                        "advisory_fee_rate": None,
                        "benchmark": None,
                        "tags": ["嘉实财富", "投顾服务"],
                        "strategy_description": None,
                        "status": "public_disclosure_only",
                        "source_url": urljoin("https://www.harvestwm.cn", notice["href"]),
                        "first_seen_at": self.captured_at,
                        "last_seen_at": self.captured_at,
                        "run_id": self.run_id,
                        "source_snapshot_id": detail.snapshot["snapshot_id"],
                        "extra": {"disclosure_notice_total": 0, "pdf_links": []},
                    },
                )
                current["last_seen_at"] = self.captured_at
                current["source_url"] = urljoin("https://www.harvestwm.cn", notice["href"])
                current["source_snapshot_id"] = detail.snapshot["snapshot_id"]
                current["extra"]["disclosure_notice_total"] += 1
                current["extra"]["pdf_links"] = sorted(set(current["extra"]["pdf_links"] + pdf_links))
                if risk_change and risk_change.get("new_risk_level"):
                    current["risk_level"] = risk_change["new_risk_level"]

        normalized = {
            "strategy_master": list(strategy_map.values()),
            "strategy_performance_daily": [],
            "strategy_fund_snapshot": [],
            "strategy_rebalance_event": [],
            "strategy_rebalance_fund_delta": [],
            "fund_public_dim": [],
            "strategy_disclosure_event": disclosure_events,
            "app_public_entry": [
                {
                    "channel_id": channel_id,
                    "channel_name": channel_name,
                    "source_url": "https://www.harvestwm.cn/product/customize_acco",
                    "title": parse_title(product_page.text),
                    "run_id": self.run_id,
                    "captured_at": self.captured_at,
                    "notice_page_total": page_count,
                    "notice_total": len(notices),
                    "relevant_notice_total": len(relevant),
                    "available_entities": ["strategy_master", "strategy_disclosure_event"],
                    "missing_entities": [
                        "strategy_performance_daily",
                        "strategy_fund_snapshot",
                        "strategy_rebalance_event",
                    ],
                    "login_hint": "公告正文提示请登录会员中心或嘉实财富 APP 查看部分产品信息。",
                }
            ],
        }
        for entity, rows in normalized.items():
            self.write_entity(channel_id, entity, rows)
        summary = {
            "channel_id": channel_id,
            "channel_name": channel_name,
            "run_id": self.run_id,
            "captured_at": self.captured_at,
            "collection_status": "success_public_disclosure_only",
            "holding_penetration_status": "blocked_login_or_app_required",
            "strategy_total": len(strategy_map),
            "disclosure_event_total": len(disclosure_events),
            "current_holding_rows": 0,
            "rebalance_event_total": 0,
            "known_gap": "公开官网只披露投顾服务页、公告、方案说明书 PDF；公告正文显示部分产品信息需登录会员中心或嘉实财富 App 查看。",
        }
        inventory = {
            "primary_sources": [
                "https://www.harvestwm.cn/product/customize_acco",
                "https://www.harvestwm.cn/about/notice?page={page}",
                "https://www.harvestwm.cn/about/notice/{notice_id}",
            ],
            "method": "分页抓取官网公告，抽取组合方案说明书公告、信息披露提醒、PDF/图片附件和风险等级变更。",
            "raw_snapshots": self.snapshots.get(channel_id, []),
        }
        summary["output_paths"] = self.write_app_outputs(channel_id, normalized, summary, inventory)
        return summary

    @staticmethod
    def parse_harvest_notices(text: str) -> list[dict[str, str]]:
        notices: list[dict[str, str]] = []
        pattern = (
            r'<a href="(?P<href>/about/notice/[^"]+)">\s*'
            r'<li class="col-md-10">(?P<title>.*?)</li>\s*'
            r'<li class="col-md-2 text-right">(?P<date>.*?)</li>'
        )
        for match in re.finditer(pattern, text, flags=re.S):
            notices.append(
                {
                    "href": match.group("href").strip(),
                    "title": strip_html(match.group("title")),
                    "date": strip_html(match.group("date")),
                }
            )
        return notices

    @staticmethod
    def extract_harvest_article_text(text: str) -> str:
        match = re.search(r'(?is)<div class="article_content">(.*?)</div>\s*</div>\s*</article>', text)
        return strip_html(match.group(1)) if match else strip_html(text)

    @staticmethod
    def extract_links(text: str, base_url: str) -> list[str]:
        links: list[str] = []
        for href in re.findall(r'(?is)<a[^>]+href="([^"]+)"', text):
            links.append(urljoin(base_url, html.unescape(href)))
        return sorted(set(links))

    @staticmethod
    def extract_harvest_strategy_name(title: str, body_text: str) -> str | None:
        match = re.search(r"关于调整(.+?)组合方案说明书的公告", title)
        if match:
            return f"{match.group(1).strip()}组合"
        match = re.search(r"([^\s，。；;]+组合)的方案说明书", body_text)
        return match.group(1).strip() if match else None

    @staticmethod
    def extract_risk_change(text: str) -> dict[str, str] | None:
        match = re.search(r"风险等级由(?P<old>[^，。]+?)调整为(?P<new>[^，。]+)", text)
        if not match:
            return None
        return {"old_risk_level": match.group("old"), "new_risk_level": match.group("new")}

    def collect_southern(self) -> dict[str, Any]:
        channel_id = "southern"
        channel_name = "南方基金/司南投顾"
        response = self.fetch(
            channel_id,
            "public_index",
            "https://www.nffund.com/new/snzt/index.html",
            "index.html",
            encoding_hint="gb18030",
        )
        links = sorted(set(re.findall(r'(?is)<a[^>]+href=["\']([^"\']+)["\']', response.text)))
        links = [urljoin(response.final_url, link) for link in links]
        images = sorted(set(re.findall(r'(?is)<img[^>]+src=["\']([^"\']+)["\']', response.text)))
        images = [urljoin(response.final_url, image) for image in images]
        login_urls = [
            link for link in links if "account/login" in link or "iainvest" in link or "trade.southernfund" in link
        ]
        normalized = {
            "strategy_master": [],
            "strategy_performance_daily": [],
            "strategy_fund_snapshot": [],
            "strategy_rebalance_event": [],
            "strategy_rebalance_fund_delta": [],
            "fund_public_dim": [],
            "strategy_disclosure_event": [],
            "app_public_entry": [
                {
                    "channel_id": channel_id,
                    "channel_name": channel_name,
                    "source_url": response.final_url,
                    "title": parse_title(response.text),
                    "run_id": self.run_id,
                    "captured_at": self.captured_at,
                    "login_urls": login_urls,
                    "image_assets": images,
                    "available_entities": ["channel_landing", "login_entry"],
                    "missing_entities": [
                        "strategy_master",
                        "strategy_performance_daily",
                        "strategy_fund_snapshot",
                        "strategy_rebalance_event",
                    ],
                }
            ],
        }
        for entity, rows in normalized.items():
            self.write_entity(channel_id, entity, rows)
        summary = {
            "channel_id": channel_id,
            "channel_name": channel_name,
            "run_id": self.run_id,
            "captured_at": self.captured_at,
            "collection_status": "success_landing_only",
            "holding_penetration_status": "blocked_login_required",
            "strategy_total": 0,
            "current_holding_rows": 0,
            "rebalance_event_total": 0,
            "login_url_total": len(login_urls),
            "known_gap": "公开页面是司南投顾入口；策略列表、仓位和业绩位于登录后的 iainvest 交易系统。",
        }
        inventory = {
            "primary_sources": ["https://www.nffund.com/new/snzt/index.html"],
            "auth_source": "https://trade.southernfund.com/new/account/login/init?from=web&url=%2Fiainvest%2Finit%3FmenuId%3D80000",
            "method": "保存公开入口 HTML、图片和登录 URL；未使用用户登录态。",
            "raw_snapshots": self.snapshots.get(channel_id, []),
        }
        summary["output_paths"] = self.write_app_outputs(channel_id, normalized, summary, inventory)
        return summary

    def collect_cmfchina(self) -> dict[str, Any]:
        channel_id = "cmfchina"
        channel_name = "招商基金/招财乐投顾"
        response = self.fetch(
            channel_id,
            "public_site",
            "https://www.cmfchina.com/web/investmentadvisory/index.html",
            "investmentadvisory.html",
        )
        products = self.parse_cmf_products(response.text)
        metrics = self.parse_cmf_metrics(response.text)
        faqs = self.parse_cmf_faqs(response.text)
        strategy_master = [
            {
                "channel_id": channel_id,
                "source_strategy_id": str(product.get("productId")),
                "strategy_name": product.get("productName"),
                "advisor_name": channel_name,
                "strategy_type": product.get("incomeCalMethod"),
                "risk_level": None,
                "launch_date": None,
                "suggested_holding_period": product.get("productTerm"),
                "minimum_amount": None,
                "advisory_fee_rate": None,
                "benchmark": product.get("rightsCenter"),
                "tags": ["招财乐投顾", product.get("incomeCalMethod")],
                "strategy_description": None,
                "status": "public_selected_strategy",
                "source_url": response.final_url,
                "first_seen_at": self.captured_at,
                "last_seen_at": self.captured_at,
                "run_id": self.run_id,
                "source_snapshot_id": response.snapshot["snapshot_id"],
                "extra": product,
            }
            for product in products
        ]
        normalized = {
            "strategy_master": strategy_master,
            "strategy_performance_daily": [],
            "strategy_fund_snapshot": [],
            "strategy_rebalance_event": [],
            "strategy_rebalance_fund_delta": [],
            "fund_public_dim": [],
            "strategy_disclosure_event": [],
            "app_public_entry": [
                {
                    "channel_id": channel_id,
                    "channel_name": channel_name,
                    "source_url": response.final_url,
                    "title": parse_title(response.text),
                    "run_id": self.run_id,
                    "captured_at": self.captured_at,
                    "metrics": metrics,
                    "faq_total": len(faqs),
                    "available_entities": ["strategy_master"],
                    "missing_entities": [
                        "strategy_performance_daily",
                        "strategy_fund_snapshot",
                        "strategy_rebalance_event",
                    ],
                }
            ],
        }
        for entity, rows in normalized.items():
            self.write_entity(channel_id, entity, rows)
        summary = {
            "channel_id": channel_id,
            "channel_name": channel_name,
            "run_id": self.run_id,
            "captured_at": self.captured_at,
            "collection_status": "success_public_selected_strategies",
            "holding_penetration_status": "blocked_app_or_encrypted_api_required",
            "strategy_total": len(strategy_master),
            "current_holding_rows": 0,
            "rebalance_event_total": 0,
            "known_gap": "官网 SSR 数据只含精选策略卡片、FAQ 和服务规模；未公开基金级持仓、业绩曲线和调仓记录。",
        }
        inventory = {
            "primary_sources": ["https://www.cmfchina.com/web/investmentadvisory/index.html"],
            "observed_frontend_calls": [
                "/ws-business-server/otherBusin/getTrustProductList",
                "/ws-base-server/publish/publish/getArticleContentList",
                "/ws-business-server/common/picList",
            ],
            "method": "从官网 Nuxt SSR 的 window.__NUXT__ 预渲染数据抽取 K 精选策略、D 指标和 F 常见问题。",
            "raw_snapshots": self.snapshots.get(channel_id, []),
        }
        summary["output_paths"] = self.write_app_outputs(channel_id, normalized, summary, inventory)
        return summary

    @staticmethod
    def js_string(value: str) -> str:
        try:
            return json.loads(f'"{value}"')
        except json.JSONDecodeError:
            return html.unescape(value)

    def parse_cmf_products(self, text: str) -> list[dict[str, Any]]:
        variable_values = {"a": None, "b": 0, "e": 3, "q": 4, "k": "admin"}
        products: list[dict[str, Any]] = []
        for match in re.finditer(r"K\[(?P<idx>\d+)\]=\{(?P<body>.*?)\};", text, flags=re.S):
            body = match.group("body")
            product: dict[str, Any] = {}
            for key in [
                "productId",
                "productCode",
                "productName",
                "riskLevel",
                "productTerm",
                "incomeCalMethod",
                "rightsCenter",
                "createDate",
                "updateDate",
            ]:
                value_match = re.search(rf"{key}:(?P<value>\"(?:\\.|[^\"])*\"|[^,}}]+)", body)
                if not value_match:
                    continue
                raw_value = value_match.group("value").strip()
                if raw_value.startswith('"'):
                    product[key] = self.js_string(raw_value[1:-1])
                else:
                    product[key] = variable_values.get(raw_value, parse_float(raw_value) if raw_value else raw_value)
            product["source_order"] = int(match.group("idx"))
            products.append(product)
        return products

    def parse_cmf_metrics(self, text: str) -> list[dict[str, Any]]:
        metrics: list[dict[str, Any]] = []
        for match in re.finditer(r"D\[(?P<idx>\d+)\]=\{(?P<body>.*?)\};", text, flags=re.S):
            body = match.group("body")
            metric: dict[str, Any] = {"source_order": int(match.group("idx"))}
            for key in ["dataId", "name", "dataValue", "unit", "updateDate"]:
                value_match = re.search(rf"{key}:(?P<value>\"(?:\\.|[^\"])*\"|[^,}}]+)", body)
                if value_match:
                    raw_value = value_match.group("value").strip()
                    metric[key] = self.js_string(raw_value[1:-1]) if raw_value.startswith('"') else parse_float(raw_value)
            metrics.append(metric)
        return metrics

    def parse_cmf_faqs(self, text: str) -> list[dict[str, Any]]:
        faqs: list[dict[str, Any]] = []
        for match in re.finditer(r"F\[(?P<idx>\d+)\]=\{(?P<body>.*?)\};", text, flags=re.S):
            body = match.group("body")
            item: dict[str, Any] = {"source_order": int(match.group("idx"))}
            for key in ["articleId", "title", "content", "publishDate"]:
                value_match = re.search(rf"{key}:(?P<value>\"(?:\\.|[^\"])*\"|[^,}}]+)", body)
                if value_match:
                    raw_value = value_match.group("value").strip()
                    item[key] = strip_html(self.js_string(raw_value[1:-1])) if raw_value.startswith('"') else raw_value
            faqs.append(item)
        return faqs

    def write_overall_summary(self, results: dict[str, Any]) -> None:
        output_dir = self.project_root / "official_apps" / "outputs"
        output_dir.mkdir(parents=True, exist_ok=True)
        channels: dict[str, Any] = {}
        for summary_path in sorted((self.project_root / "official_apps").glob("*/outputs/latest_summary.json")):
            channel_id = summary_path.parent.parent.name
            try:
                channels[channel_id] = json.loads(summary_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
        channels.update(results)
        payload = {
            "run_id": self.run_id,
            "captured_at": self.captured_at,
            "channels": channels,
        }
        (output_dir / "latest_overall_summary.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def collect_official_apps_public(
    project_root: Path,
    *,
    apps: list[str] | None = None,
    harvest_pages: int | None = None,
    workers: int = 8,
    zocaifu_limit: int | None = None,
    zocaifu_skip_fund_nav: bool = False,
    gffunds_limit: int | None = None,
    gffunds_skip_fund_nav: bool = False,
    gffunds_skip_protocol_pdf: bool = False,
    gffunds_latest_adjustment_refresh_days: int = 1,
    gfsec_fima_daily_page_size: int = 200,
    run_id: str | None = None,
) -> dict[str, Any]:
    collector = OfficialAppsPublicCollector(
        project_root,
        run_id=run_id,
        harvest_pages=harvest_pages,
        workers=workers,
        zocaifu_limit=zocaifu_limit,
        zocaifu_skip_fund_nav=zocaifu_skip_fund_nav,
        gffunds_limit=gffunds_limit,
        gffunds_skip_fund_nav=gffunds_skip_fund_nav,
        gffunds_skip_protocol_pdf=gffunds_skip_protocol_pdf,
        gffunds_latest_adjustment_refresh_days=gffunds_latest_adjustment_refresh_days,
        gfsec_fima_daily_page_size=gfsec_fima_daily_page_size,
    )
    return collector.collect(apps)
