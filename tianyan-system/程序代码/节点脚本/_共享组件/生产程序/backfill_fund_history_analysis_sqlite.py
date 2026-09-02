from __future__ import annotations

import argparse
import hashlib
import html
import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from http.client import IncompleteRead
import re


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
DEFAULT_SCHEMA_PATH = PROJECT_ROOT / "schemas" / "analysis_zh_current.sql"
DEFAULT_RAW_INDEX_DB = PROJECT_ROOT / "data" / "advisor_monitor.sqlite"
RAW_CHANNEL_ID = "ttfund_fund_nav"
RAW_CHANNEL_NAME = "天天基金/基金历史净值"
PINGZHONGDATA_URL = "http://fund.eastmoney.com/pingzhongdata/{fund_code}.js"
F10_API_URL = "http://fundf10.eastmoney.com/F10DataApi.aspx"
DIVIDEND_URL = "http://fundf10.eastmoney.com/fhsp_{fund_code}.html"
USER_AGENT = "Mozilla/5.0 advisor-monitor/0.1"
CN_TZ = timezone(timedelta(hours=8))

HOST_IP_FALLBACKS = {
    "fund.eastmoney.com": ["43.146.25.59", "43.168.116.56", "43.175.79.128", "43.175.186.85"],
    "fundf10.eastmoney.com": ["122.188.57.54", "36.249.93.227", "42.4.56.186", "218.61.165.129"],
}

ENTITY_HISTORY_DAILY = "fund_nav_history_daily"
ENTITY_DIVIDEND_EVENT = "fund_dividend_event"
ENTITY_HISTORY_META = "fund_nav_history_meta"
ENTITY_COLLECTION_SUMMARY = "collection_summary"

TABLE_ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
TABLE_CELL_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.IGNORECASE | re.DOTALL)
TAG_RE = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class RawSnapshot:
    snapshot_id: str
    channel_id: str
    collector_name: str
    access_level: str
    captured_at: str
    source_url: str
    http_status: int | None
    raw_path: str
    content_type: str | None
    content_hash: str
    parse_status: str = "parsed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "channel_id": self.channel_id,
            "collector_name": self.collector_name,
            "access_level": self.access_level,
            "captured_at": self.captured_at,
            "source_url": self.source_url,
            "http_status": self.http_status,
            "raw_path": self.raw_path,
            "content_type": self.content_type,
            "content_hash": self.content_hash,
            "parse_status": self.parse_status,
        }


@dataclass(frozen=True)
class FundTarget:
    fund_code: str
    fund_name: str | None
    fund_type: str | None
    fund_company: str | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill all positioned fund NAV/dividend history into analysis_zh_current.sqlite."
    )
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH, help="Target analysis SQLite path.")
    parser.add_argument(
        "--schema-path",
        type=Path,
        default=DEFAULT_SCHEMA_PATH,
        help="Schema SQL path used to create any missing analysis tables.",
    )
    parser.add_argument("--workers", type=int, default=12, help="Concurrent fund fetch workers.")
    parser.add_argument("--timeout-sec", type=int, default=15, help="Per-request timeout in seconds.")
    parser.add_argument("--retries", type=int, default=2, help="Per-request retry count.")
    parser.add_argument("--f10-per-page", type=int, default=2000, help="Rows per F10DataApi fallback request.")
    parser.add_argument("--limit", type=int, default=None, help="Optional limit for a dry run.")
    parser.add_argument(
        "--target-source",
        choices=["positioned", "current-dict", "all-dict"],
        default="positioned",
        help=(
            "Fund universe source. positioned uses strategy holdings/rebalance funds; "
            "current-dict uses 基金标准分类字典 是否当前库使用=1; all-dict uses the full public-fund dictionary."
        ),
    )
    parser.add_argument(
        "--fund-code",
        action="append",
        default=[],
        help="Only backfill selected fund code(s). Can be passed multiple times.",
    )
    parser.add_argument(
        "--fund-code-file",
        action="append",
        type=Path,
        default=[],
        help="Text file containing fund codes to backfill, one code per line. # comments are ignored.",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Refresh funds already present in 基金净值概况 instead of skipping them.",
    )
    parser.add_argument(
        "--skip-dividends",
        action="store_true",
        help="Skip separate dividend-page collection. NAV rows still keep dividend hints from pingzhongdata when available.",
    )
    parser.add_argument(
        "--incremental-days",
        type=int,
        default=None,
        help="For funds already present in the database, fetch only the latest N calendar days through F10DataApi.",
    )
    parser.add_argument(
        "--incremental-start-date",
        help="Optional YYYY-MM-DD start date for F10 incremental refresh. Overrides --incremental-days.",
    )
    parser.add_argument(
        "--incremental-from-existing",
        action="store_true",
        help="For existing funds, start each incremental refresh from that fund's own latest NAV date in SQLite.",
    )
    parser.add_argument(
        "--only-nav-before",
        help="Only process funds whose latest local NAV date is missing or earlier than this YYYY-MM-DD date.",
    )
    parser.add_argument(
        "--raw-run-dir",
        type=Path,
        default=None,
        help="Optional previously fetched raw run directory to ingest without network requests.",
    )
    parser.add_argument(
        "--commit-every",
        type=int,
        default=25,
        help="Commit every N successful funds.",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=20,
        help="Print one progress line every N completed funds, plus any failures.",
    )
    parser.add_argument(
        "--no-output-files",
        action="store_true",
        help="Only write the target SQLite database; do not append normalized files or raw manifests.",
    )
    parser.add_argument(
        "--raw-index-db",
        type=Path,
        default=DEFAULT_RAW_INDEX_DB,
        help="advisor_monitor SQLite path used to index raw snapshot manifests.",
    )
    parser.add_argument(
        "--skip-raw-index-sync",
        action="store_true",
        help="Do not sync generated raw snapshots into advisor_monitor.sqlite.",
    )
    return parser.parse_args()


def now_local() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def normalize_ymd(value: str | None) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt, width in (("%Y-%m-%d", 10), ("%Y/%m/%d", 10), ("%Y%m%d", 8)):
        try:
            return datetime.strptime(text[:width], fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def sanitize_fund_code(value: Any) -> str:
    text = str(value or "").strip()
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) == 6:
        return digits
    if digits and len(digits) < 6:
        return digits.zfill(6)
    return text


def normalize_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


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


def epoch_millis_to_ymd(value: Any) -> str | None:
    try:
        millis = int(value)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(millis / 1000, tz=CN_TZ).strftime("%Y-%m-%d")


def parse_quoted_js_string(text: str, variable_name: str) -> str | None:
    match = re.search(rf'var {re.escape(variable_name)}\s*=\s*"([^"]*)";', text)
    return html.unescape(match.group(1)).strip() if match else None


def parse_js_bool(text: str, variable_name: str) -> bool | None:
    match = re.search(rf"var {re.escape(variable_name)}\s*=\s*(true|false);", text)
    if not match:
        return None
    return match.group(1) == "true"


def parse_js_array(text: str, variable_name: str) -> Any:
    match = re.search(rf"var {re.escape(variable_name)}\s*=\s*(.*?);", text, re.DOTALL)
    if not match:
        return None
    return json.loads(match.group(1))


def strip_html(value: str) -> str:
    return html.unescape(TAG_RE.sub("", value)).replace("\xa0", " ").strip()


def parse_html_table(html_text: str, class_name: str) -> tuple[list[str], list[list[str]]]:
    match = re.search(
        rf"<table[^>]*class=['\"][^'\"]*{re.escape(class_name)}[^'\"]*['\"][^>]*>(.*?)</table>",
        html_text,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return [], []
    table_html = match.group(1)
    thead_match = re.search(r"<thead[^>]*>(.*?)</thead>", table_html, re.IGNORECASE | re.DOTALL)
    tbody_match = re.search(r"<tbody[^>]*>(.*?)</tbody>", table_html, re.IGNORECASE | re.DOTALL)
    headers = [strip_html(cell) for cell in TABLE_CELL_RE.findall(thead_match.group(1))] if thead_match else []
    rows: list[list[str]] = []
    if tbody_match:
        for row_html in TABLE_ROW_RE.findall(tbody_match.group(1)):
            cells = [strip_html(cell) for cell in TABLE_CELL_RE.findall(row_html)]
            if cells:
                rows.append(cells)
    return headers, rows


def parse_f10_payload(text: str) -> dict[str, Any]:
    marker = 'content:"'
    marker_index = text.find(marker)
    if marker_index < 0:
        raise ValueError("F10DataApi response missing content marker")
    start_index = marker_index + len(marker)
    end_marker = '",records:'
    end_index = text.find(end_marker, start_index)
    if end_index < 0:
        raise ValueError("F10DataApi response missing records marker")
    trailer = text[end_index + len('",') :]
    match = re.search(r"records:(?P<records>\d+),pages:(?P<pages>\d+),curpage:(?P<curpage>\d+)", trailer)
    if not match:
        raise ValueError("F10DataApi response missing records/pages metadata")
    return {
        "content": text[start_index:end_index].replace('\\"', '"').replace("\\/", "/"),
        "records": int(match.group("records")),
        "pages": int(match.group("pages")),
        "curpage": int(match.group("curpage")),
    }


def parse_table_fragment(table_html: str) -> tuple[list[str], list[list[str]]]:
    thead_match = re.search(r"<thead[^>]*>(.*?)</thead>", table_html, re.IGNORECASE | re.DOTALL)
    tbody_match = re.search(r"<tbody[^>]*>(.*?)</tbody>", table_html, re.IGNORECASE | re.DOTALL)
    if not thead_match or not tbody_match:
        return [], []
    headers = [strip_html(cell) for cell in TABLE_CELL_RE.findall(thead_match.group(1))]
    rows: list[list[str]] = []
    for row_html in TABLE_ROW_RE.findall(tbody_match.group(1)):
        cells = [strip_html(cell) for cell in TABLE_CELL_RE.findall(row_html)]
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
    return {header: cells[index].strip() if index < len(cells) else None for index, header in enumerate(headers)}


def parse_ymd(value: Any) -> str | None:
    text = normalize_text(value)
    if text and re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text
    return None


def build_snapshot_id(channel_id: str, collector_name: str, raw_bytes: bytes, unique_hint: str) -> str:
    content_hash = hashlib.sha256(raw_bytes).hexdigest()
    hint_hash = hashlib.sha1(unique_hint.encode("utf-8")).hexdigest()
    return f"{channel_id}-{collector_name}-{content_hash[:12]}-{hint_hash[:6]}"


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def request_candidates(url: str) -> list[tuple[str, dict[str, str]]]:
    parts = urlsplit(url)
    hostname = parts.hostname or ""
    candidates: list[tuple[str, dict[str, str]]] = [(url, {})]
    if parts.scheme != "http" or hostname not in HOST_IP_FALLBACKS:
        return candidates
    host_header = parts.netloc
    for ip_address in HOST_IP_FALLBACKS[hostname]:
        netloc = ip_address
        if parts.port:
            netloc = f"{ip_address}:{parts.port}"
        fallback_url = urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
        candidates.append((fallback_url, {"Host": host_header}))
    return candidates


def fetch_url(
    *,
    url: str,
    raw_path: Path,
    collector_name: str,
    captured_at: str,
    timeout: int = 30,
    retries: int = 3,
) -> tuple[str, dict[str, Any]]:
    last_error: Exception | None = None
    for request_url, extra_headers in request_candidates(url):
        headers = {
            "User-Agent": USER_AGENT,
            "Referer": "http://fund.eastmoney.com/",
            **extra_headers,
        }
        request = Request(request_url, headers=headers)
        attempt_total = max(1, retries + 1)
        for attempt in range(1, attempt_total + 1):
            try:
                with urlopen(request, timeout=timeout) as response:
                    chunks: list[bytes] = []
                    while True:
                        try:
                            chunk = response.read(65536)
                        except IncompleteRead as partial_error:
                            if partial_error.partial:
                                chunks.append(partial_error.partial)
                            break
                        if not chunk:
                            break
                        chunks.append(chunk)
                    raw_bytes = b"".join(chunks)
                    raw_path.parent.mkdir(parents=True, exist_ok=True)
                    raw_path.write_bytes(raw_bytes)
                    unique_hint = f"{request_url}|{raw_path.as_posix()}|{attempt}"
                    snapshot_id = build_snapshot_id(RAW_CHANNEL_ID, collector_name, raw_bytes, unique_hint)
                    snapshot = RawSnapshot(
                        snapshot_id=snapshot_id,
                        channel_id=RAW_CHANNEL_ID,
                        collector_name=collector_name,
                        access_level="public",
                        captured_at=captured_at,
                        source_url=url,
                        http_status=getattr(response, "status", None),
                        raw_path=str(raw_path.resolve()),
                        content_type=response.headers.get_content_type(),
                        content_hash=hashlib.sha256(raw_bytes).hexdigest(),
                    ).to_dict()
                    return raw_bytes.decode("utf-8", errors="replace"), snapshot
            except (HTTPError, URLError, TimeoutError, IncompleteRead, ConnectionError, OSError) as error:
                last_error = error
                if attempt < attempt_total:
                    time.sleep(0.8 * attempt)
    raise RuntimeError(f"fetch failed for {url}: {last_error}")


def synthetic_failed_snapshot(
    *,
    url: str,
    raw_path: Path,
    collector_name: str,
    captured_at: str,
    error: Exception,
) -> dict[str, Any]:
    raw_bytes = f"fetch failed: {type(error).__name__}: {error}".encode("utf-8")
    return RawSnapshot(
        snapshot_id=build_snapshot_id(RAW_CHANNEL_ID, collector_name, raw_bytes, f"{url}|{raw_path.as_posix()}|failed"),
        channel_id=RAW_CHANNEL_ID,
        collector_name=collector_name,
        access_level="public",
        captured_at=captured_at,
        source_url=url,
        http_status=None,
        raw_path=str(raw_path.resolve()),
        content_type="text/plain",
        content_hash=hashlib.sha256(raw_bytes).hexdigest(),
        parse_status="fetch_failed",
    ).to_dict()


def synthetic_skipped_snapshot(
    *,
    url: str,
    raw_path: Path,
    collector_name: str,
    captured_at: str,
) -> dict[str, Any]:
    raw_bytes = b"skipped by --skip-dividends"
    return RawSnapshot(
        snapshot_id=build_snapshot_id(RAW_CHANNEL_ID, collector_name, raw_bytes, f"{url}|{raw_path.as_posix()}|skipped"),
        channel_id=RAW_CHANNEL_ID,
        collector_name=collector_name,
        access_level="public",
        captured_at=captured_at,
        source_url=url,
        http_status=None,
        raw_path=str(raw_path.resolve()),
        content_type="text/plain",
        content_hash=hashlib.sha256(raw_bytes).hexdigest(),
        parse_status="skipped",
    ).to_dict()


def init_db(conn: sqlite3.Connection, schema_path: Path) -> None:
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA temp_store = MEMORY;")
    conn.execute("PRAGMA busy_timeout = 3000;")
    conn.executescript(schema_path.read_text(encoding="utf-8"))


def discover_fund_targets(conn: sqlite3.Connection) -> list[FundTarget]:
    sql = """
    WITH fund_pool AS (
        SELECT "基金代码" AS fund_code, "基金名称" AS fund_name
        FROM "策略当前持仓"
        WHERE "基金代码" IS NOT NULL AND TRIM("基金代码") <> ''
        UNION ALL
        SELECT "基金代码" AS fund_code, "基金名称" AS fund_name
        FROM "策略调仓明细"
        WHERE "基金代码" IS NOT NULL AND TRIM("基金代码") <> ''
    )
    SELECT
        p.fund_code,
        COALESCE(MAX(NULLIF(TRIM(i."基金名称"), '')), MAX(NULLIF(TRIM(p.fund_name), ''))) AS fund_name,
        MAX(NULLIF(TRIM(i."基金类型"), '')) AS fund_type,
        MAX(NULLIF(TRIM(i."基金公司"), '')) AS fund_company
    FROM fund_pool p
    LEFT JOIN "基金信息" i
        ON i."基金代码" = p.fund_code
    GROUP BY p.fund_code
    ORDER BY p.fund_code
    """
    return [
        FundTarget(
            fund_code=sanitize_fund_code(row[0]),
            fund_name=normalize_text(row[1]),
            fund_type=normalize_text(row[2]),
            fund_company=normalize_text(row[3]),
        )
        for row in conn.execute(sql)
        if sanitize_fund_code(row[0])
    ]


def discover_dict_fund_targets(conn: sqlite3.Connection, *, current_only: bool) -> list[FundTarget]:
    where = 'WHERE d."基金代码" IS NOT NULL AND TRIM(d."基金代码") <> \'\''
    if current_only:
        where += ' AND COALESCE(d."是否当前库使用", 0) = 1'
    sql = f"""
    SELECT
        d."基金代码" AS fund_code,
        COALESCE(
            NULLIF(TRIM(i."基金名称"), ''),
            NULLIF(TRIM(d."标准基金名称"), '')
        ) AS fund_name,
        COALESCE(
            NULLIF(TRIM(i."基金类型"), ''),
            NULLIF(TRIM(d."天天基金细分类"), ''),
            NULLIF(TRIM(d."标准资产细类"), ''),
            NULLIF(TRIM(d."标准资产大类"), '')
        ) AS fund_type,
        COALESCE(
            NULLIF(TRIM(i."基金公司"), ''),
            NULLIF(TRIM(d."基金公司"), '')
        ) AS fund_company
    FROM "基金标准分类字典" d
    LEFT JOIN "基金信息" i
        ON i."基金代码" = d."基金代码"
    {where}
    ORDER BY d."基金代码"
    """
    return [
        FundTarget(
            fund_code=sanitize_fund_code(row[0]),
            fund_name=normalize_text(row[1]),
            fund_type=normalize_text(row[2]),
            fund_company=normalize_text(row[3]),
        )
        for row in conn.execute(sql)
        if sanitize_fund_code(row[0])
    ]


def discover_existing_meta_codes(conn: sqlite3.Connection) -> set[str]:
    try:
        rows = conn.execute('SELECT "基金代码" FROM "基金净值概况"').fetchall()
    except sqlite3.OperationalError:
        return set()
    return {sanitize_fund_code(row[0]) for row in rows if sanitize_fund_code(row[0])}


def discover_existing_latest_nav_dates(conn: sqlite3.Connection) -> dict[str, str]:
    try:
        rows = conn.execute(
            '''
            SELECT "基金代码", MAX("交易日期")
            FROM "基金日度净值"
            GROUP BY "基金代码"
            '''
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    latest: dict[str, str] = {}
    for code, trade_date in rows:
        fund_code = sanitize_fund_code(code)
        date_text = normalize_ymd(str(trade_date or ""))
        if fund_code and date_text:
            latest[fund_code] = date_text
    return latest


def update_fund_info_from_meta(conn: sqlite3.Connection, meta_row: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO "基金信息" (
            "基金代码", "基金名称", "基金公司", "基金类型", "最新净值", "最新净值日期", "数据来源"
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT("基金代码") DO UPDATE SET
            "基金名称"=COALESCE(excluded."基金名称", "基金信息"."基金名称"),
            "基金公司"=COALESCE(excluded."基金公司", "基金信息"."基金公司"),
            "基金类型"=COALESCE(excluded."基金类型", "基金信息"."基金类型"),
            "最新净值"=COALESCE(excluded."最新净值", "基金信息"."最新净值"),
            "最新净值日期"=COALESCE(excluded."最新净值日期", "基金信息"."最新净值日期"),
            "数据来源"=COALESCE(excluded."数据来源", "基金信息"."数据来源"),
            "最近更新时间"=CURRENT_TIMESTAMP
        """,
        [
            meta_row["基金代码"],
            meta_row.get("基金名称"),
            meta_row.get("基金公司"),
            meta_row.get("基金类型"),
            meta_row.get("最新单位净值"),
            meta_row.get("历史结束日期"),
            meta_row.get("数据来源"),
        ],
    )


def upsert_daily_rows(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    conn.executemany(
        """
        INSERT INTO "基金日度净值" (
            "基金代码", "交易日期", "基金名称", "基金类型", "基金公司", "净值口径", "单位净值", "累计净值",
            "日收益率_百分比", "每万份收益", "七日年化收益率_百分比", "净值图分红送配", "是否货币基金",
            "数据来源", "原始净值快照ID", "采集时间"
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT("基金代码", "交易日期") DO UPDATE SET
            "基金名称"=excluded."基金名称",
            "基金类型"=COALESCE(excluded."基金类型", "基金日度净值"."基金类型"),
            "基金公司"=COALESCE(excluded."基金公司", "基金日度净值"."基金公司"),
            "净值口径"=excluded."净值口径",
            "单位净值"=excluded."单位净值",
            "累计净值"=excluded."累计净值",
            "日收益率_百分比"=excluded."日收益率_百分比",
            "每万份收益"=excluded."每万份收益",
            "七日年化收益率_百分比"=excluded."七日年化收益率_百分比",
            "净值图分红送配"=excluded."净值图分红送配",
            "是否货币基金"=excluded."是否货币基金",
            "数据来源"=excluded."数据来源",
            "原始净值快照ID"=excluded."原始净值快照ID",
            "采集时间"=excluded."采集时间"
        """,
        [
            [
                row["基金代码"],
                row["交易日期"],
                row.get("基金名称"),
                row.get("基金类型"),
                row.get("基金公司"),
                row["净值口径"],
                row.get("单位净值"),
                row.get("累计净值"),
                row.get("日收益率_百分比"),
                row.get("每万份收益"),
                row.get("七日年化收益率_百分比"),
                row.get("净值图分红送配"),
                row["是否货币基金"],
                row["数据来源"],
                row["原始净值快照ID"],
                row["采集时间"],
            ]
            for row in rows
        ],
    )


def upsert_dividend_rows(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    conn.executemany(
        """
        INSERT INTO "基金分红送配" (
            "基金代码", "权益登记日", "除息日", "基金名称", "年份", "每份分红", "分红发放日",
            "数据来源", "原始分红快照ID", "采集时间"
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT("基金代码", "权益登记日", "每份分红") DO UPDATE SET
            "除息日"=excluded."除息日",
            "基金名称"=excluded."基金名称",
            "年份"=excluded."年份",
            "分红发放日"=excluded."分红发放日",
            "数据来源"=excluded."数据来源",
            "原始分红快照ID"=excluded."原始分红快照ID",
            "采集时间"=excluded."采集时间"
        """,
        [
            [
                row["基金代码"],
                row["权益登记日"],
                row.get("除息日"),
                row.get("基金名称"),
                row.get("年份"),
                row["每份分红"],
                row.get("分红发放日"),
                row["数据来源"],
                row["原始分红快照ID"],
                row["采集时间"],
            ]
            for row in rows
        ],
    )


def delete_existing_fund_history(conn: sqlite3.Connection, fund_code: str) -> None:
    conn.execute('DELETE FROM "基金日度净值" WHERE "基金代码" = ?', [fund_code])
    conn.execute('DELETE FROM "基金分红送配" WHERE "基金代码" = ?', [fund_code])
    conn.execute('DELETE FROM "基金净值概况" WHERE "基金代码" = ?', [fund_code])


def upsert_meta_row(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO "基金净值概况" (
            "基金代码", "基金名称", "基金类型", "基金公司", "净值口径", "是否货币基金", "历史起始日期",
            "历史结束日期", "历史记录数", "分红事件数", "最新单位净值", "最新累计净值", "最新日收益率_百分比",
            "最新每万份收益", "最新七日年化收益率_百分比", "数据来源", "原始净值快照ID", "原始分红快照ID", "最近采集时间"
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT("基金代码") DO UPDATE SET
            "基金名称"=COALESCE(excluded."基金名称", "基金净值概况"."基金名称"),
            "基金类型"=COALESCE(excluded."基金类型", "基金净值概况"."基金类型"),
            "基金公司"=COALESCE(excluded."基金公司", "基金净值概况"."基金公司"),
            "净值口径"=excluded."净值口径",
            "是否货币基金"=excluded."是否货币基金",
            "历史起始日期"=excluded."历史起始日期",
            "历史结束日期"=excluded."历史结束日期",
            "历史记录数"=excluded."历史记录数",
            "分红事件数"=excluded."分红事件数",
            "最新单位净值"=excluded."最新单位净值",
            "最新累计净值"=excluded."最新累计净值",
            "最新日收益率_百分比"=excluded."最新日收益率_百分比",
            "最新每万份收益"=excluded."最新每万份收益",
            "最新七日年化收益率_百分比"=excluded."最新七日年化收益率_百分比",
            "数据来源"=excluded."数据来源",
            "原始净值快照ID"=excluded."原始净值快照ID",
            "原始分红快照ID"=excluded."原始分红快照ID",
            "最近采集时间"=excluded."最近采集时间"
        """,
        [
            row["基金代码"],
            row.get("基金名称"),
            row.get("基金类型"),
            row.get("基金公司"),
            row["净值口径"],
            row["是否货币基金"],
            row.get("历史起始日期"),
            row.get("历史结束日期"),
            row["历史记录数"],
            row["分红事件数"],
            row.get("最新单位净值"),
            row.get("最新累计净值"),
            row.get("最新日收益率_百分比"),
            row.get("最新每万份收益"),
            row.get("最新七日年化收益率_百分比"),
            row["数据来源"],
            row["原始净值快照ID"],
            row.get("原始分红快照ID"),
            row["最近采集时间"],
        ],
    )


def rebuild_meta_row_from_db(conn: sqlite3.Connection, fund_code: str, fallback: dict[str, Any]) -> dict[str, Any]:
    stats = conn.execute(
        """
        SELECT MIN("交易日期"), MAX("交易日期"), COUNT(*)
        FROM "基金日度净值"
        WHERE "基金代码" = ?
        """,
        [fund_code],
    ).fetchone()
    if stats is None or not stats[2]:
        return fallback
    latest = conn.execute(
        """
        SELECT *
        FROM "基金日度净值"
        WHERE "基金代码" = ? AND "交易日期" = ?
        ORDER BY "采集时间" DESC
        LIMIT 1
        """,
        [fund_code, stats[1]],
    ).fetchone()
    if latest is None:
        return fallback
    dividend_count = conn.execute(
        'SELECT COUNT(*) FROM "基金分红送配" WHERE "基金代码" = ?',
        [fund_code],
    ).fetchone()[0]
    row = dict(fallback)
    row.update(
        {
            "基金代码": fund_code,
            "基金名称": latest["基金名称"] or fallback.get("基金名称"),
            "基金类型": latest["基金类型"] or fallback.get("基金类型"),
            "基金公司": latest["基金公司"] or fallback.get("基金公司"),
            "净值口径": latest["净值口径"] or fallback.get("净值口径"),
            "是否货币基金": latest["是否货币基金"] if latest["是否货币基金"] is not None else fallback.get("是否货币基金", 0),
            "历史起始日期": stats[0],
            "历史结束日期": stats[1],
            "历史记录数": stats[2],
            "分红事件数": dividend_count,
            "最新单位净值": latest["单位净值"],
            "最新累计净值": latest["累计净值"],
            "最新日收益率_百分比": latest["日收益率_百分比"],
            "最新每万份收益": latest["每万份收益"],
            "最新七日年化收益率_百分比": latest["七日年化收益率_百分比"],
            "数据来源": latest["数据来源"] or fallback.get("数据来源"),
            "原始净值快照ID": latest["原始净值快照ID"] or fallback.get("原始净值快照ID"),
            "原始分红快照ID": fallback.get("原始分红快照ID"),
            "最近采集时间": fallback.get("最近采集时间") or latest["采集时间"],
        }
    )
    return row


def upsert_raw_snapshot_index(conn: sqlite3.Connection, row: dict[str, Any]) -> str:
    existing = conn.execute(
        "SELECT channel_id, collector_name, captured_at, raw_path, content_hash, parse_status "
        "FROM raw_snapshot WHERE snapshot_id = ?",
        [row["snapshot_id"]],
    ).fetchone()
    expected = (
        row.get("channel_id"),
        row.get("collector_name"),
        row.get("captured_at"),
        row.get("raw_path"),
        row.get("content_hash"),
        row.get("parse_status") or "success",
    )
    if existing is None:
        status = "inserted"
    else:
        status = "unchanged" if tuple(existing) == expected else "updated"

    conn.execute(
        """
        INSERT INTO raw_snapshot (
            snapshot_id, channel_id, collector_name, access_level, captured_at, source_url,
            http_status, raw_path, content_type, content_hash, parse_status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(snapshot_id) DO UPDATE SET
            channel_id=excluded.channel_id,
            collector_name=excluded.collector_name,
            access_level=excluded.access_level,
            captured_at=excluded.captured_at,
            source_url=excluded.source_url,
            http_status=excluded.http_status,
            raw_path=excluded.raw_path,
            content_type=excluded.content_type,
            content_hash=excluded.content_hash,
            parse_status=excluded.parse_status
        """,
        [
            row["snapshot_id"],
            row.get("channel_id"),
            row.get("collector_name"),
            row.get("access_level") or "public",
            row.get("captured_at"),
            row.get("source_url"),
            row.get("http_status"),
            row.get("raw_path"),
            row.get("content_type"),
            row.get("content_hash"),
            row.get("parse_status") or "success",
        ],
    )
    return status


def sync_raw_snapshot_index(raw_index_db: Path, raw_snapshots: list[dict[str, Any]]) -> dict[str, int]:
    counters = {"inserted": 0, "updated": 0, "unchanged": 0}
    raw_index_db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(raw_index_db)
    try:
        for row in raw_snapshots:
            status = upsert_raw_snapshot_index(conn, row)
            counters[status] += 1
        conn.commit()
    finally:
        conn.close()
    return counters


def collect_one_fund(
    target: FundTarget,
    *,
    raw_base_dir: Path,
    captured_at: str,
    run_id: str,
    timeout_sec: int,
    retries: int,
    f10_per_page: int,
    skip_dividends: bool = False,
) -> dict[str, Any]:
    fund_code = target.fund_code
    fund_dir = raw_base_dir / "funds" / fund_code
    history_url = PINGZHONGDATA_URL.format(fund_code=fund_code)
    dividend_url = DIVIDEND_URL.format(fund_code=fund_code)
    try:
        history_text, nav_snapshot = fetch_url(
            url=history_url,
            raw_path=fund_dir / "pingzhongdata.js",
            collector_name="pingzhongdata_history",
            captured_at=captured_at,
            timeout=timeout_sec,
            retries=retries,
        )
    except Exception:
        return collect_one_fund_f10(
            target,
            raw_base_dir=raw_base_dir,
            captured_at=captured_at,
            run_id=run_id,
            timeout_sec=timeout_sec,
            retries=retries,
            per_page=f10_per_page,
        )
    if skip_dividends:
        dividend_text = ""
        dividend_snapshot = synthetic_skipped_snapshot(
            url=dividend_url,
            raw_path=fund_dir / "fhsp.html",
            collector_name="fhsp_dividend",
            captured_at=captured_at,
        )
    else:
        try:
            dividend_text, dividend_snapshot = fetch_url(
                url=dividend_url,
                raw_path=fund_dir / "fhsp.html",
                collector_name="fhsp_dividend",
                captured_at=captured_at,
                timeout=timeout_sec,
                retries=retries,
            )
        except Exception as error:
            dividend_text = ""
            dividend_snapshot = synthetic_failed_snapshot(
                url=dividend_url,
                raw_path=fund_dir / "fhsp.html",
                collector_name="fhsp_dividend",
                captured_at=captured_at,
                error=error,
            )
    try:
        return build_fund_result(
            target,
            history_text=history_text,
            dividend_text=dividend_text,
            nav_snapshot=nav_snapshot,
            dividend_snapshot=dividend_snapshot,
            captured_at=captured_at,
            run_id=run_id,
        )
    except Exception:
        return collect_one_fund_f10(
            target,
            raw_base_dir=raw_base_dir,
            captured_at=captured_at,
            run_id=run_id,
            timeout_sec=timeout_sec,
            retries=retries,
            per_page=f10_per_page,
        )


def fetch_f10_page(
    *,
    fund_code: str,
    page_no: int,
    per_page: int,
    start_date: str | None = None,
    end_date: str | None = None,
    fund_dir: Path,
    captured_at: str,
    timeout_sec: int,
    retries: int,
) -> tuple[dict[str, Any], list[str], list[list[str]], dict[str, Any]]:
    params = {
        "type": "lsjz",
        "code": fund_code,
        "page": page_no,
        "per": max(1, min(per_page, 2000)),
    }
    if start_date:
        params["sdate"] = start_date
    if end_date:
        params["edate"] = end_date
    url = f"{F10_API_URL}?{urlencode(params)}"
    text, snapshot = fetch_url(
        url=url,
        raw_path=fund_dir / f"f10_page_{page_no:04d}.js",
        collector_name="fund_history_lsjz",
        captured_at=captured_at,
        timeout=timeout_sec,
        retries=retries,
    )
    payload = parse_f10_payload(text)
    headers, rows = parse_table_fragment(payload["content"])
    return payload, headers, rows, snapshot


def collect_one_fund_f10(
    target: FundTarget,
    *,
    raw_base_dir: Path,
    captured_at: str,
    run_id: str,
    timeout_sec: int,
    retries: int,
    per_page: int,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    fund_code = target.fund_code
    fund_dir = raw_base_dir / "funds" / fund_code
    first_payload, headers, rows, first_snapshot = fetch_f10_page(
        fund_code=fund_code,
        page_no=1,
        per_page=per_page,
        start_date=start_date,
        end_date=end_date,
        fund_dir=fund_dir,
        captured_at=captured_at,
        timeout_sec=timeout_sec,
        retries=retries,
    )
    all_rows: list[list[str]] = list(rows)
    snapshot_ids = [first_snapshot["snapshot_id"]]
    pages_total = int(first_payload.get("pages") or 0)
    for page_no in range(2, pages_total + 1):
        try:
            _payload, _headers, page_rows, snapshot = fetch_f10_page(
                fund_code=fund_code,
                page_no=page_no,
                per_page=per_page,
                start_date=start_date,
                end_date=end_date,
                fund_dir=fund_dir,
                captured_at=captured_at,
                timeout_sec=timeout_sec,
                retries=retries,
            )
        except Exception:
            if all_rows:
                break
            raise
        if _headers:
            headers = _headers
        all_rows.extend(page_rows)
        snapshot_ids.append(snapshot["snapshot_id"])

    value_type = value_type_from_headers(headers)
    is_money_market = value_type == "money_market"
    daily_rows: list[dict[str, Any]] = []
    for cells in all_rows:
        record = row_as_map(headers, cells)
        trade_date = parse_ymd(record.get("净值日期"))
        if not trade_date:
            continue
        per_10k = to_float(record.get("每万份收益"))
        daily_rows.append(
            {
                "基金代码": fund_code,
                "交易日期": trade_date,
                "基金名称": target.fund_name,
                "基金类型": target.fund_type,
                "基金公司": target.fund_company,
                "净值口径": "货币基金收益" if is_money_market else "单位净值",
                "单位净值": to_float(record.get("单位净值")),
                "累计净值": to_float(record.get("累计净值")),
                "日收益率_百分比": round(per_10k / 100, 8) if is_money_market and per_10k is not None else to_float(record.get("日增长率")),
                "每万份收益": per_10k,
                "七日年化收益率_百分比": to_float(record.get("7日年化收益率（%）")),
                "净值图分红送配": normalize_text(record.get("分红送配")),
                "是否货币基金": 1 if is_money_market else 0,
                "数据来源": "天天基金_lsjz",
                "原始净值快照ID": first_snapshot["snapshot_id"],
                "采集时间": captured_at,
                "run_id": run_id,
            }
        )

    daily_rows.sort(key=lambda row: row["交易日期"] or "", reverse=True)
    trade_dates = [row["交易日期"] for row in daily_rows if row.get("交易日期")]
    latest_row = daily_rows[0] if daily_rows else {}
    meta_row = {
        "基金代码": fund_code,
        "基金名称": target.fund_name,
        "基金类型": target.fund_type,
        "基金公司": target.fund_company,
        "净值口径": "货币基金收益" if is_money_market else "单位净值",
        "是否货币基金": 1 if is_money_market else 0,
        "历史起始日期": min(trade_dates) if trade_dates else None,
        "历史结束日期": max(trade_dates) if trade_dates else None,
        "历史记录数": len(daily_rows),
        "分红事件数": 0,
        "最新单位净值": latest_row.get("单位净值"),
        "最新累计净值": latest_row.get("累计净值"),
        "最新日收益率_百分比": latest_row.get("日收益率_百分比"),
        "最新每万份收益": latest_row.get("每万份收益"),
        "最新七日年化收益率_百分比": latest_row.get("七日年化收益率_百分比"),
        "数据来源": "天天基金_lsjz",
        "原始净值快照ID": first_snapshot["snapshot_id"],
        "原始分红快照ID": None,
        "最近采集时间": captured_at,
        "run_id": run_id,
        "source_snapshot_ids": snapshot_ids,
    }
    return {
        "fund_code": fund_code,
        "fund_name": target.fund_name,
        "daily_rows": daily_rows,
        "dividend_rows": [],
        "meta_row": meta_row,
        "raw_snapshots": [],
        "incremental_update": bool(start_date or end_date),
    }


def build_snapshot_from_raw_file(
    raw_path: Path,
    *,
    collector_name: str,
    source_url: str,
    captured_at: str,
) -> tuple[str, dict[str, Any]]:
    raw_bytes = raw_path.read_bytes()
    snapshot = RawSnapshot(
        snapshot_id=build_snapshot_id(RAW_CHANNEL_ID, collector_name, raw_bytes, f"{source_url}|{raw_path.as_posix()}"),
        channel_id=RAW_CHANNEL_ID,
        collector_name=collector_name,
        access_level="public",
        captured_at=captured_at,
        source_url=source_url,
        http_status=200,
        raw_path=str(raw_path.resolve()),
        content_type="text/plain",
        content_hash=hashlib.sha256(raw_bytes).hexdigest(),
    ).to_dict()
    return raw_bytes.decode("utf-8", errors="replace"), snapshot


def build_fund_result(
    target: FundTarget,
    *,
    history_text: str,
    dividend_text: str,
    nav_snapshot: dict[str, Any],
    dividend_snapshot: dict[str, Any],
    captured_at: str,
    run_id: str,
) -> dict[str, Any]:
    fund_code = target.fund_code

    parsed_name = normalize_text(parse_quoted_js_string(history_text, "fS_name"))
    is_money_market = bool(parse_js_bool(history_text, "ishb"))
    fund_name = parsed_name or target.fund_name
    fund_type = target.fund_type
    fund_company = target.fund_company
    nav_source = "天天基金_pingzhongdata"
    dividend_source = "天天基金_fhsp"

    daily_rows: list[dict[str, Any]] = []
    if is_money_market:
        incomes = parse_js_array(history_text, "Data_millionCopiesIncome") or []
        annualized = parse_js_array(history_text, "Data_sevenDaysYearIncome") or []
        annualized_map = {epoch_millis_to_ymd(item[0]): to_float(item[1]) for item in annualized if len(item) >= 2}
        for item in incomes:
            if len(item) < 2:
                continue
            trade_date = epoch_millis_to_ymd(item[0])
            per_10k = to_float(item[1])
            daily_rows.append(
                {
                    "基金代码": fund_code,
                    "交易日期": trade_date,
                    "基金名称": fund_name,
                    "基金类型": fund_type,
                    "基金公司": fund_company,
                    "净值口径": "货币基金收益",
                    "单位净值": None,
                    "累计净值": None,
                    "日收益率_百分比": round(per_10k / 100, 8) if per_10k is not None else None,
                    "每万份收益": per_10k,
                    "七日年化收益率_百分比": annualized_map.get(trade_date),
                    "净值图分红送配": None,
                    "是否货币基金": 1,
                    "数据来源": nav_source,
                    "原始净值快照ID": nav_snapshot["snapshot_id"],
                    "采集时间": captured_at,
                    "run_id": run_id,
                }
            )
    else:
        nav_series = parse_js_array(history_text, "Data_netWorthTrend") or []
        accumulated_series = parse_js_array(history_text, "Data_ACWorthTrend") or []
        accumulated_map = {epoch_millis_to_ymd(item[0]): to_float(item[1]) for item in accumulated_series if len(item) >= 2}
        for item in nav_series:
            trade_date = epoch_millis_to_ymd(item.get("x"))
            daily_rows.append(
                {
                    "基金代码": fund_code,
                    "交易日期": trade_date,
                    "基金名称": fund_name,
                    "基金类型": fund_type,
                    "基金公司": fund_company,
                    "净值口径": "单位净值",
                    "单位净值": to_float(item.get("y")),
                    "累计净值": accumulated_map.get(trade_date),
                    "日收益率_百分比": to_float(item.get("equityReturn")),
                    "每万份收益": None,
                    "七日年化收益率_百分比": None,
                    "净值图分红送配": normalize_text(item.get("unitMoney")),
                    "是否货币基金": 0,
                    "数据来源": nav_source,
                    "原始净值快照ID": nav_snapshot["snapshot_id"],
                    "采集时间": captured_at,
                    "run_id": run_id,
                }
            )

    daily_rows.sort(key=lambda row: row["交易日期"] or "", reverse=True)

    _, dividend_table_rows = parse_html_table(dividend_text, "cfxq")
    dividend_rows: list[dict[str, Any]] = []
    for cells in dividend_table_rows:
        if len(cells) == 1 and "暂无分红信息" in cells[0]:
            continue
        if len(cells) < 5:
            continue
        dividend_rows.append(
            {
                "基金代码": fund_code,
                "基金名称": fund_name,
                "年份": normalize_text(cells[0]),
                "权益登记日": normalize_text(cells[1]),
                "除息日": normalize_text(cells[2]),
                "每份分红": normalize_text(cells[3]),
                "分红发放日": normalize_text(cells[4]),
                "数据来源": dividend_source,
                "原始分红快照ID": dividend_snapshot["snapshot_id"],
                "采集时间": captured_at,
                "run_id": run_id,
            }
        )

    if not dividend_rows:
        for row in daily_rows:
            if row.get("净值图分红送配"):
                dividend_rows.append(
                    {
                        "基金代码": fund_code,
                        "基金名称": fund_name,
                        "年份": row["交易日期"][:4] if row.get("交易日期") else None,
                        "权益登记日": row["交易日期"],
                        "除息日": row["交易日期"],
                        "每份分红": row["净值图分红送配"],
                        "分红发放日": None,
                        "数据来源": "天天基金_pingzhongdata_unitMoney",
                        "原始分红快照ID": nav_snapshot["snapshot_id"],
                        "采集时间": captured_at,
                        "run_id": run_id,
                    }
                )

    trade_dates = [row["交易日期"] for row in daily_rows if row.get("交易日期")]
    latest_row = daily_rows[0] if daily_rows else {}
    meta_row = {
        "基金代码": fund_code,
        "基金名称": fund_name,
        "基金类型": fund_type,
        "基金公司": fund_company,
        "净值口径": "货币基金收益" if is_money_market else "单位净值",
        "是否货币基金": 1 if is_money_market else 0,
        "历史起始日期": min(trade_dates) if trade_dates else None,
        "历史结束日期": max(trade_dates) if trade_dates else None,
        "历史记录数": len(daily_rows),
        "分红事件数": len(dividend_rows),
        "最新单位净值": latest_row.get("单位净值"),
        "最新累计净值": latest_row.get("累计净值"),
        "最新日收益率_百分比": latest_row.get("日收益率_百分比"),
        "最新每万份收益": latest_row.get("每万份收益"),
        "最新七日年化收益率_百分比": latest_row.get("七日年化收益率_百分比"),
        "数据来源": nav_source,
        "原始净值快照ID": nav_snapshot["snapshot_id"],
        "原始分红快照ID": dividend_snapshot["snapshot_id"],
        "最近采集时间": captured_at,
        "run_id": run_id,
    }

    return {
        "fund_code": fund_code,
        "fund_name": fund_name,
        "daily_rows": daily_rows,
        "dividend_rows": dividend_rows,
        "meta_row": meta_row,
        "raw_snapshots": [nav_snapshot, dividend_snapshot],
    }


def load_one_fund_from_raw_dir(
    target: FundTarget,
    *,
    fund_dir: Path,
    captured_at: str,
    run_id: str,
) -> dict[str, Any]:
    history_path = fund_dir / "pingzhongdata.js"
    dividend_path = fund_dir / "fhsp.html"
    history_text, nav_snapshot = build_snapshot_from_raw_file(
        history_path,
        collector_name="pingzhongdata_history",
        source_url=PINGZHONGDATA_URL.format(fund_code=target.fund_code),
        captured_at=captured_at,
    )
    dividend_text, dividend_snapshot = build_snapshot_from_raw_file(
        dividend_path,
        collector_name="fhsp_dividend",
        source_url=DIVIDEND_URL.format(fund_code=target.fund_code),
        captured_at=captured_at,
    )
    return build_fund_result(
        target,
        history_text=history_text,
        dividend_text=dividend_text,
        nav_snapshot=nav_snapshot,
        dividend_snapshot=dividend_snapshot,
        captured_at=captured_at,
        run_id=run_id,
    )


def main() -> None:
    args = parse_args()
    run_at = now_local()
    day = run_at.strftime("%Y-%m-%d")
    run_id = run_at.strftime("%Y%m%dT%H%M%S%z")
    captured_at = run_at.isoformat(timespec="seconds")
    raw_base_dir = PROJECT_ROOT / "data" / "raw" / RAW_CHANNEL_ID / "eastmoney" / day / run_id
    normalized_base_dir = PROJECT_ROOT / "data" / "normalized" / RAW_CHANNEL_ID
    if args.raw_run_dir is None:
        raw_base_dir.mkdir(parents=True, exist_ok=True)
    else:
        raw_base_dir = args.raw_run_dir.resolve()

    conn = sqlite3.connect(args.db_path)
    conn.row_factory = sqlite3.Row
    init_db(conn, args.schema_path)

    if args.target_source == "all-dict":
        all_targets = discover_dict_fund_targets(conn, current_only=False)
    elif args.target_source == "current-dict":
        all_targets = discover_dict_fund_targets(conn, current_only=True)
    else:
        all_targets = discover_fund_targets(conn)
    existing_codes = discover_existing_meta_codes(conn)
    existing_latest_nav_dates = discover_existing_latest_nav_dates(conn) if args.incremental_from_existing else {}
    selected_codes = list(args.fund_code)
    for code_file in args.fund_code_file:
        for line in code_file.read_text(encoding="utf-8-sig").splitlines():
            text = line.split("#", 1)[0].strip()
            if text:
                selected_codes.append(text)
    selected = {sanitize_fund_code(code) for code in selected_codes if sanitize_fund_code(code)}

    if args.raw_run_dir is None and selected:
        target_map = {target.fund_code: target for target in all_targets}
        all_targets = [target_map.get(code, FundTarget(code, None, None, None)) for code in sorted(selected)]
    elif args.raw_run_dir is not None:
        target_map = {target.fund_code: target for target in all_targets}
        raw_targets: list[FundTarget] = []
        funds_root = raw_base_dir / "funds"
        if funds_root.exists():
            for fund_dir in sorted(funds_root.iterdir()):
                if not fund_dir.is_dir():
                    continue
                if not (fund_dir / "pingzhongdata.js").exists() or not (fund_dir / "fhsp.html").exists():
                    continue
                fund_code = sanitize_fund_code(fund_dir.name)
                if selected and fund_code not in selected:
                    continue
                raw_targets.append(target_map.get(fund_code, FundTarget(fund_code, None, None, None)))
        all_targets = raw_targets
    if not args.refresh:
        all_targets = [target for target in all_targets if target.fund_code not in existing_codes]
    if args.only_nav_before:
        stale_cutoff = normalize_ymd(args.only_nav_before)
        if not stale_cutoff:
            raise ValueError(f"invalid --only-nav-before date: {args.only_nav_before}")
        latest_dates = existing_latest_nav_dates or discover_existing_latest_nav_dates(conn)
        all_targets = [
            target
            for target in all_targets
            if not latest_dates.get(target.fund_code) or latest_dates[target.fund_code] < stale_cutoff
        ]
    if args.limit and args.limit > 0:
        all_targets = all_targets[: args.limit]

    incremental_mode = args.incremental_from_existing or args.incremental_start_date is not None or (
        args.incremental_days is not None and args.incremental_days > 0
    )
    incremental_start_date: str | None = None
    incremental_end_date: str | None = None
    if incremental_mode:
        incremental_end_date = now_local().date().strftime("%Y-%m-%d")
        if args.incremental_start_date:
            incremental_start_date = args.incremental_start_date
        elif args.incremental_from_existing:
            incremental_start_date = None
        else:
            incremental_start_date = (now_local().date() - timedelta(days=max(1, args.incremental_days))).strftime("%Y-%m-%d")

    daily_path = normalized_base_dir / ENTITY_HISTORY_DAILY / day / f"{run_id}.jsonl"
    dividend_path = normalized_base_dir / ENTITY_DIVIDEND_EVENT / day / f"{run_id}.jsonl"
    meta_path = normalized_base_dir / ENTITY_HISTORY_META / day / f"{run_id}.jsonl"
    summary_path = normalized_base_dir / ENTITY_COLLECTION_SUMMARY / day / f"{run_id}.json"

    raw_snapshots: list[dict[str, Any]] = []
    raw_lock = Lock()
    counters = {
        "targets": len(all_targets),
        "success": 0,
        "failed": 0,
        "history_rows": 0,
        "dividend_rows": 0,
    }
    failures: list[dict[str, Any]] = []
    successful_since_commit = 0

    def worker(target: FundTarget) -> dict[str, Any]:
        if args.raw_run_dir is not None:
            return load_one_fund_from_raw_dir(
                target,
                fund_dir=raw_base_dir / "funds" / target.fund_code,
                captured_at=captured_at,
                run_id=run_id,
            )
        if incremental_mode and target.fund_code in existing_codes:
            fund_start_date = incremental_start_date
            if args.incremental_from_existing:
                fund_start_date = existing_latest_nav_dates.get(target.fund_code)
                if not fund_start_date:
                    return collect_one_fund(
                        target,
                        raw_base_dir=raw_base_dir,
                        captured_at=captured_at,
                        run_id=run_id,
                        timeout_sec=max(1, args.timeout_sec),
                        retries=max(0, args.retries),
                        f10_per_page=max(1, args.f10_per_page),
                        skip_dividends=args.skip_dividends,
                    )
            return collect_one_fund_f10(
                target,
                raw_base_dir=raw_base_dir,
                captured_at=captured_at,
                run_id=run_id,
                timeout_sec=max(1, args.timeout_sec),
                retries=max(0, args.retries),
                per_page=max(1, args.f10_per_page),
                start_date=fund_start_date,
                end_date=incremental_end_date,
            )
        return collect_one_fund(
            target,
            raw_base_dir=raw_base_dir,
            captured_at=captured_at,
            run_id=run_id,
            timeout_sec=max(1, args.timeout_sec),
            retries=max(0, args.retries),
            f10_per_page=max(1, args.f10_per_page),
            skip_dividends=args.skip_dividends,
        )

    try:
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
            future_map = {executor.submit(worker, target): target for target in all_targets}
            for index, future in enumerate(as_completed(future_map), start=1):
                target = future_map[future]
                try:
                    result = future.result()
                except Exception as error:
                    counters["failed"] += 1
                    failures.append(
                        {
                            "fund_code": target.fund_code,
                            "fund_name": target.fund_name,
                            "error": str(error),
                        }
                    )
                    print(f"[{index}/{len(all_targets)}] failed {target.fund_code} {target.fund_name or ''}: {error}", flush=True)
                    continue

                daily_rows = result["daily_rows"]
                dividend_rows = result["dividend_rows"]
                meta_row = result["meta_row"]
                is_incremental_update = bool(result.get("incremental_update")) and target.fund_code in existing_codes
                if args.refresh and not is_incremental_update:
                    delete_existing_fund_history(conn, target.fund_code)
                    update_fund_info_from_meta(conn, meta_row)
                upsert_daily_rows(conn, daily_rows)
                upsert_dividend_rows(conn, dividend_rows)
                meta_to_upsert = rebuild_meta_row_from_db(conn, target.fund_code, meta_row) if is_incremental_update else meta_row
                update_fund_info_from_meta(conn, meta_to_upsert)
                upsert_meta_row(conn, meta_to_upsert)
                if not args.no_output_files:
                    write_jsonl(daily_path, daily_rows)
                    write_jsonl(dividend_path, dividend_rows)
                    write_jsonl(meta_path, [meta_to_upsert])
                with raw_lock:
                    raw_snapshots.extend(result["raw_snapshots"])
                counters["success"] += 1
                counters["history_rows"] += len(daily_rows)
                counters["dividend_rows"] += len(dividend_rows)
                successful_since_commit += 1

                if successful_since_commit >= max(1, args.commit_every):
                    conn.commit()
                    successful_since_commit = 0

                if index == 1 or index == len(all_targets) or index % max(1, args.progress_every) == 0:
                    print(
                        f"[{index}/{len(all_targets)}] ok {target.fund_code} "
                        f"history={len(daily_rows)} dividend={len(dividend_rows)} "
                        f"success={counters['success']} failed={counters['failed']}",
                        flush=True,
                    )
    finally:
        conn.commit()

    if not args.no_output_files:
        manifest_path = raw_base_dir / "_manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "channel_id": RAW_CHANNEL_ID,
                    "channel_name": RAW_CHANNEL_NAME,
                    "run_id": run_id,
                    "captured_at": captured_at,
                    "raw_snapshots": raw_snapshots,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        if not args.skip_raw_index_sync:
            raw_index_counters = sync_raw_snapshot_index(args.raw_index_db, raw_snapshots)
            print(
                "raw_index_sync "
                f"inserted={raw_index_counters['inserted']} "
                f"updated={raw_index_counters['updated']} "
                f"unchanged={raw_index_counters['unchanged']}",
                flush=True,
            )

    summary = {
        "channel_id": RAW_CHANNEL_ID,
        "channel_name": RAW_CHANNEL_NAME,
        "run_id": run_id,
        "captured_at": captured_at,
        "db_path": str(args.db_path.resolve()),
        "raw_dir": str(raw_base_dir.resolve()),
        "normalized_dir": str(normalized_base_dir.resolve()),
        "daily_path": str(daily_path.resolve()),
        "dividend_path": str(dividend_path.resolve()),
        "meta_path": str(meta_path.resolve()),
        "target_source": args.target_source,
        "skip_dividends": bool(args.skip_dividends),
        "incremental_mode": incremental_mode,
        "incremental_from_existing": bool(args.incremental_from_existing),
        "only_nav_before": args.only_nav_before,
        "incremental_start_date": incremental_start_date,
        "incremental_end_date": incremental_end_date,
        "existing_latest_nav_date_fund_total": len(existing_latest_nav_dates),
        "target_fund_total": counters["targets"],
        "successful_fund_total": counters["success"],
        "failed_fund_total": counters["failed"],
        "history_row_total": counters["history_rows"],
        "dividend_row_total": counters["dividend_rows"],
        "failed_funds": failures,
        "targets": [
            {
                "fund_code": target.fund_code,
                "fund_name": target.fund_name,
                "fund_type": target.fund_type,
            }
            for target in all_targets
        ],
    }
    if not args.no_output_files:
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    print("backfill complete", flush=True)
    print(f"run_id={run_id}", flush=True)
    print(f"targets={counters['targets']} success={counters['success']} failed={counters['failed']}", flush=True)
    print(f"history_rows={counters['history_rows']} dividend_rows={counters['dividend_rows']}", flush=True)
    print(f"db={args.db_path}", flush=True)
    conn.close()


if __name__ == "__main__":
    main()
