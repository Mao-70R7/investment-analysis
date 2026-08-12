from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import re
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http.client import IncompleteRead
from pathlib import Path
from typing import Any
from urllib.parse import quote
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "current_fund_lookthrough_quality"
DEFAULT_RAW_ROOT = PROJECT_ROOT / "data" / "raw" / "fund_lookthrough"
DEFAULT_STOCK_INDUSTRY_OVERRIDE_PATH = PROJECT_ROOT / "config" / "股票行业映射补充表.csv"
CN_TZ = timezone(timedelta(hours=8))
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

ASSET_PAGE_URL = "http://fundf10.eastmoney.com/zcpz_{fund_code}.html"
ARCHIVE_DATA_URL = "http://fundf10.eastmoney.com/FundArchivesDatas.aspx"
PUSH2_ULIST_URL = "https://push2.eastmoney.com/api/qt/ulist.np/get"
PUSH2_STOCK_URL = "https://push2.eastmoney.com/api/qt/stock/get"
EASTMONEY_SEARCH_URL = "https://searchapi.eastmoney.com/api/suggest/get"

TABLE_ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
TABLE_CELL_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.IGNORECASE | re.DOTALL)
TAG_RE = re.compile(r"<[^>]+>")

LOOKTHROUGH_SCHEMA_SQL = r"""
CREATE TABLE IF NOT EXISTS "基金季报原始快照" (
  "原始快照ID" TEXT PRIMARY KEY,
  "基金代码" TEXT NOT NULL,
  "数据类型" TEXT NOT NULL,
  "数据来源" TEXT NOT NULL,
  "来源URL" TEXT NOT NULL,
  "报告期" TEXT,
  "抓取时间" TEXT NOT NULL,
  "HTTP状态" INTEGER,
  "内容哈希" TEXT NOT NULL,
  "原始路径" TEXT NOT NULL,
  "解析状态" TEXT NOT NULL,
  "错误信息" TEXT
);

CREATE TABLE IF NOT EXISTS "基金季度资产配置" (
  "基金代码" TEXT NOT NULL,
  "报告期" TEXT NOT NULL,
  "披露日期" TEXT,
  "股票占比_百分比" REAL,
  "债券占比_百分比" REAL,
  "现金占比_百分比" REAL,
  "基金占比_百分比" REAL,
  "商品占比_百分比" REAL,
  "存托凭证占比_百分比" REAL,
  "其他占比_百分比" REAL,
  "净资产_亿元" REAL,
  "数据来源" TEXT NOT NULL,
  "原始快照ID" TEXT,
  "采集时间" TEXT NOT NULL,
  PRIMARY KEY ("基金代码", "报告期", "数据来源")
);

CREATE TABLE IF NOT EXISTS "股票行业映射" (
  "股票代码" TEXT PRIMARY KEY,
  "股票名称" TEXT,
  "市场代码" TEXT,
  "东财行业" TEXT,
  "行业一级" TEXT,
  "行业二级" TEXT,
  "地区板块" TEXT,
  "数据来源" TEXT NOT NULL,
  "更新时间" TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS "基金季度股票持仓" (
  "基金代码" TEXT NOT NULL,
  "报告期" TEXT NOT NULL,
  "股票代码" TEXT NOT NULL,
  "股票名称" TEXT,
  "市场代码" TEXT,
  "占基金净值比例_百分比" REAL,
  "持股数_万股" REAL,
  "持仓市值_万元" REAL,
  "行业一级" TEXT,
  "行业二级" TEXT,
  "行业来源" TEXT,
  "数据来源" TEXT NOT NULL,
  "原始快照ID" TEXT,
  "采集时间" TEXT NOT NULL,
  PRIMARY KEY ("基金代码", "报告期", "股票代码", "数据来源")
);

CREATE TABLE IF NOT EXISTS "基金季度债券持仓" (
  "基金代码" TEXT NOT NULL,
  "报告期" TEXT NOT NULL,
  "债券代码" TEXT NOT NULL,
  "债券名称" TEXT,
  "占基金净值比例_百分比" REAL,
  "持债数量" REAL,
  "持仓市值_万元" REAL,
  "债券类型" TEXT,
  "数据来源" TEXT NOT NULL,
  "原始快照ID" TEXT,
  "采集时间" TEXT NOT NULL,
  PRIMARY KEY ("基金代码", "报告期", "债券代码", "数据来源")
);

CREATE TABLE IF NOT EXISTS "基金季度行业配置" (
  "基金代码" TEXT NOT NULL,
  "报告期" TEXT NOT NULL,
  "行业一级" TEXT NOT NULL,
  "占基金净值比例_百分比" REAL,
  "股票持仓样本数" INTEGER,
  "数据来源" TEXT NOT NULL,
  "生成时间" TEXT NOT NULL,
  PRIMARY KEY ("基金代码", "报告期", "行业一级", "数据来源")
);

CREATE TABLE IF NOT EXISTS "基金分类快照" (
  "基金代码" TEXT NOT NULL,
  "报告期" TEXT NOT NULL,
  "披露日期" TEXT,
  "基金名称" TEXT,
  "基金公司" TEXT,
  "基金类型" TEXT,
  "二级分类" TEXT,
  "资产暴露JSON" TEXT,
  "行业暴露JSON" TEXT,
  "主题标签JSON" TEXT,
  "分类来源" TEXT NOT NULL,
  "是否估算" INTEGER NOT NULL DEFAULT 0,
  "覆盖状态" TEXT NOT NULL,
  "生成时间" TEXT NOT NULL,
  PRIMARY KEY ("基金代码", "报告期")
);

CREATE TABLE IF NOT EXISTS "基金穿透数据质量" (
  "运行ID" TEXT NOT NULL,
  "指标名" TEXT NOT NULL,
  "指标值" REAL,
  "指标文本" TEXT,
  "生成时间" TEXT NOT NULL,
  PRIMARY KEY ("运行ID", "指标名")
);

CREATE INDEX IF NOT EXISTS "idx_基金季报原始快照_基金类型时间"
ON "基金季报原始快照"("基金代码", "数据类型", "抓取时间");

CREATE INDEX IF NOT EXISTS "idx_基金季度资产配置_报告期"
ON "基金季度资产配置"("报告期");

CREATE INDEX IF NOT EXISTS "idx_基金季度股票持仓_报告期"
ON "基金季度股票持仓"("报告期");

CREATE INDEX IF NOT EXISTS "idx_基金分类快照_基金代码报告期"
ON "基金分类快照"("基金代码", "报告期");
"""


@dataclass(frozen=True)
class FundTarget:
    fund_code: str
    fund_name: str = ""
    fund_type: str = ""
    fund_company: str = ""


def now_cn() -> str:
    return datetime.now(CN_TZ).isoformat(timespec="seconds")


def run_id() -> str:
    return datetime.now(CN_TZ).strftime("%Y%m%dT%H%M%S%z")


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(LOOKTHROUGH_SCHEMA_SQL)
    conn.commit()


def connect_db(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    return conn


def normalize_fund_code(value: Any) -> str:
    match = re.search(r"(\d{6})", str(value or ""))
    return match.group(1) if match else ""


def normalize_date(value: Any) -> str:
    text = str(value or "")
    max_year = datetime.now(CN_TZ).year + 1
    patterns = [
        r"(?<!\d)([12]\d{3})[-/.年]\s*([01]?\d)[-/.月]\s*([0-3]?\d)(?:日)?(?!\d)",
        r"(?<!\d)([12]\d{3})([01]\d)([0-3]\d)(?!\d)",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            year = int(match.group(1))
            month = int(match.group(2))
            day = int(match.group(3))
            if year < 1990 or year > max_year:
                continue
            try:
                datetime(year, month, day)
            except ValueError:
                continue
            return f"{year:04d}-{month:02d}-{day:02d}"
    return ""


def quarter_end_date(year: int, quarter: int) -> str:
    if quarter == 1:
        return f"{year:04d}-03-31"
    if quarter == 2:
        return f"{year:04d}-06-30"
    if quarter == 3:
        return f"{year:04d}-09-30"
    if quarter == 4:
        return f"{year:04d}-12-31"
    return ""


def extract_archive_report_date(content: str) -> str:
    plain = clean_text(content)
    date_patterns = [
        r"(?:截止至|截至|截止日期|报告期)[:：]?\s*([12]\d{3}[-/.年][01]?\d[-/.月][0-3]?\d日?)",
        r"(?:截止至|截至|截止日期|报告期).{0,24}?([12]\d{3}[-/.年][01]?\d[-/.月][0-3]?\d日?)",
    ]
    for pattern in date_patterns:
        match = re.search(pattern, plain)
        if match:
            report_date = normalize_date(match.group(1))
            if report_date:
                return report_date
    quarter_match = re.search(r"([12]\d{3})\s*年\s*([1-4])\s*季度", plain)
    if quarter_match:
        year = int(quarter_match.group(1))
        quarter = int(quarter_match.group(2))
        if 1990 <= year <= datetime.now(CN_TZ).year + 1:
            return quarter_end_date(year, quarter)
    return ""


def parse_float(value: Any) -> float | None:
    if value is None:
        return None
    text = html.unescape(str(value)).replace(",", "").replace("%", "").strip()
    text = text.replace("--", "").replace("－", "").replace("—", "")
    if not text:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def clean_text(value: str) -> str:
    value = TAG_RE.sub(" ", value)
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def content_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def http_get(url: str, timeout_sec: int = 20, retries: int = 2, referer: str | None = None) -> tuple[int | None, bytes, str | None]:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    if referer:
        headers["Referer"] = referer
    last_error: str | None = None
    for attempt in range(retries + 1):
        try:
            with urlopen(Request(url, headers=headers), timeout=timeout_sec) as resp:
                try:
                    body = resp.read()
                except IncompleteRead as exc:
                    body = exc.partial
                return getattr(resp, "status", None), body, None
        except HTTPError as exc:
            last_error = f"HTTP {exc.code}: {exc.reason}"
            try:
                body = exc.read()
            except Exception:
                body = b""
            if attempt >= retries:
                return exc.code, body, last_error
        except (URLError, TimeoutError, OSError) as exc:
            last_error = str(exc)
            if attempt >= retries:
                return None, b"", last_error
        time.sleep(0.8 + attempt * 0.6)
    return None, b"", last_error


def raw_snapshot_path(raw_root: Path, data_type: str, fund_code: str, suffix: str) -> Path:
    day = datetime.now(CN_TZ).strftime("%Y-%m-%d")
    path = raw_root / data_type / day / f"{fund_code}{suffix}"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def latest_non_empty_raw_snapshot(raw_root: Path, data_type: str, fund_code: str, suffix: str = ".js") -> tuple[bytes, Path | None]:
    base = raw_root / data_type
    if not base.exists():
        return b"", None
    candidates = sorted(
        base.glob(f"*/{fund_code}{suffix}"),
        key=lambda path: path.stat().st_mtime if path.exists() else 0,
        reverse=True,
    )
    for path in candidates:
        try:
            if path.stat().st_size > 0:
                return path.read_bytes(), path
        except OSError:
            continue
    return b"", None


def save_raw_snapshot(
    conn: sqlite3.Connection,
    raw_root: Path,
    fund_code: str,
    data_type: str,
    source_url: str,
    status: int | None,
    body: bytes,
    report_date: str = "",
    parse_status: str = "parsed",
    error: str | None = None,
    suffix: str = ".html",
) -> str:
    captured_at = now_cn()
    digest = content_sha256(body)
    snapshot_id = f"eastmoney_f10-{data_type}-{fund_code}-{digest[:16]}"
    raw_path = raw_snapshot_path(raw_root, data_type, fund_code, suffix)
    if body or not raw_path.exists() or raw_path.stat().st_size == 0:
        raw_path.write_bytes(body)
    conn.execute(
        """
        INSERT OR REPLACE INTO "基金季报原始快照"
        ("原始快照ID","基金代码","数据类型","数据来源","来源URL","报告期","抓取时间",
         "HTTP状态","内容哈希","原始路径","解析状态","错误信息")
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            snapshot_id,
            fund_code,
            data_type,
            "东财F10",
            source_url,
            report_date or None,
            captured_at,
            status,
            digest,
            str(raw_path.relative_to(PROJECT_ROOT)),
            parse_status,
            error,
        ),
    )
    return snapshot_id


def latest_snapshot_is_fresh(conn: sqlite3.Connection, fund_code: str, data_type: str, stale_days: int) -> bool:
    if stale_days <= 0:
        return False
    row = conn.execute(
        """
        SELECT MAX("抓取时间") AS ts
        FROM "基金季报原始快照"
        WHERE "基金代码"=? AND "数据类型"=? AND "解析状态"='parsed'
        """,
        (fund_code, data_type),
    ).fetchone()
    if not row or not row["ts"]:
        return False
    try:
        captured = datetime.fromisoformat(str(row["ts"]))
    except ValueError:
        return False
    return datetime.now(CN_TZ) - captured < timedelta(days=stale_days)


def load_target_funds(conn: sqlite3.Connection, limit: int | None = None, fund_codes: list[str] | None = None) -> list[FundTarget]:
    if fund_codes:
        rows = []
        for code in sorted({normalize_fund_code(code) for code in fund_codes if normalize_fund_code(code)}):
            row = conn.execute(
                """
                SELECT "基金代码","基金名称","基金类型","基金公司"
                FROM "基金信息" WHERE "基金代码"=?
                """,
                (code,),
            ).fetchone()
            rows.append(
                FundTarget(
                    code,
                    str(row["基金名称"] or "") if row else "",
                    str(row["基金类型"] or "") if row else "",
                    str(row["基金公司"] or "") if row else "",
                )
            )
        return rows

    sql = """
    WITH current_dates AS (
      SELECT "统一策略ID", MAX("持仓日期") AS latest_date
      FROM "策略当前持仓"
      GROUP BY "统一策略ID"
    ),
    target_codes AS (
      SELECT h."基金代码" AS code, h."基金名称" AS name
      FROM "策略当前持仓" h
      JOIN current_dates d
        ON d."统一策略ID" = h."统一策略ID" AND d.latest_date = h."持仓日期"
      WHERE h."基金代码" GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
        AND COALESCE(h."基金权重_百分比", 0) > 0
      UNION
      SELECT "基金代码" AS code, "基金名称" AS name
      FROM "策略调仓明细"
      WHERE "基金代码" GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
        AND "调仓日期" >= date('now','-420 day')
    )
    SELECT t.code AS "基金代码",
           COALESCE(f."基金名称", c."标准基金名称", t.name, '') AS "基金名称",
           COALESCE(f."基金类型", c."天天基金细分类", c."天天基金大类", '') AS "基金类型",
           COALESCE(f."基金公司", c."基金公司", '') AS "基金公司"
    FROM target_codes t
    LEFT JOIN "基金信息" f ON f."基金代码" = t.code
    LEFT JOIN "基金标准分类字典" c ON c."基金代码" = t.code
    GROUP BY t.code
    ORDER BY t.code
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [
        FundTarget(
            str(row["基金代码"] or ""),
            str(row["基金名称"] or ""),
            str(row["基金类型"] or ""),
            str(row["基金公司"] or ""),
        )
        for row in conn.execute(sql).fetchall()
    ]


def decode_text(body: bytes) -> str:
    for encoding in ("utf-8", "gb18030"):
        try:
            return body.decode(encoding)
        except UnicodeDecodeError:
            continue
    return body.decode("utf-8", errors="replace")


def parse_asset_chart_data(html_text: str) -> list[dict[str, Any]]:
    match = re.search(r"var\s+chartData\s*=\s*(\{.*?\});", html_text, re.DOTALL)
    if not match:
        return []
    payload = json.loads(match.group(1))
    dates = payload.get("Dates") or []
    rows: list[dict[str, Any]] = []
    for index, report_date in enumerate(dates):
        stock = value_at(payload.get("GP"), index)
        bond = value_at(payload.get("ZQ"), index)
        cash = value_at(payload.get("XJ"), index)
        cdr = value_at(payload.get("CTPZ"), index)
        nav = value_at(payload.get("JZC"), index)
        known = sum(v or 0 for v in [stock, bond, cash, cdr])
        other = max(0.0, round(100 - known, 4)) if known > 0 else None
        rows.append(
            {
                "报告期": normalize_date(report_date),
                "股票占比_百分比": stock,
                "债券占比_百分比": bond,
                "现金占比_百分比": cash,
                "基金占比_百分比": None,
                "商品占比_百分比": None,
                "存托凭证占比_百分比": cdr,
                "其他占比_百分比": other,
                "净资产_亿元": nav,
            }
        )
    return [row for row in rows if row["报告期"]]


def value_at(values: Any, index: int) -> float | None:
    if not isinstance(values, list) or index >= len(values):
        return None
    value = values[index]
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fetch_asset_allocation(target: FundTarget, args: argparse.Namespace) -> dict[str, Any]:
    with connect_db(args.db_path) as conn:
        if not args.force_refresh and latest_snapshot_is_fresh(conn, target.fund_code, "asset_allocation", args.stale_days):
            return {"基金代码": target.fund_code, "状态": "skipped_fresh"}
    url = ASSET_PAGE_URL.format(fund_code=target.fund_code)
    status, body, error = http_get(url, timeout_sec=args.timeout_sec, retries=args.retries)
    html_text = decode_text(body)
    rows: list[dict[str, Any]] = []
    parse_status = "failed"
    parse_error = error
    if body and status and status < 400:
        try:
            rows = parse_asset_chart_data(html_text)
            parse_status = "parsed" if rows else "empty"
            parse_error = None if rows else "chartData_not_found_or_empty"
        except Exception as exc:  # noqa: BLE001
            parse_error = f"parse_error: {exc}"
    with connect_db(args.db_path) as conn:
        snapshot_id = save_raw_snapshot(
            conn,
            args.raw_root,
            target.fund_code,
            "asset_allocation",
            url,
            status,
            body,
            rows[-1]["报告期"] if rows else "",
            parse_status,
            parse_error,
        )
        if rows:
            captured_at = now_cn()
            for row in rows:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO "基金季度资产配置"
                    ("基金代码","报告期","披露日期","股票占比_百分比","债券占比_百分比",
                     "现金占比_百分比","基金占比_百分比","商品占比_百分比","存托凭证占比_百分比",
                     "其他占比_百分比","净资产_亿元","数据来源","原始快照ID","采集时间")
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        target.fund_code,
                        row["报告期"],
                        None,
                        row["股票占比_百分比"],
                        row["债券占比_百分比"],
                        row["现金占比_百分比"],
                        row["基金占比_百分比"],
                        row["商品占比_百分比"],
                        row["存托凭证占比_百分比"],
                        row["其他占比_百分比"],
                        row["净资产_亿元"],
                        "东财F10资产配置",
                        snapshot_id,
                        captured_at,
                    ),
                )
        conn.commit()
    return {"基金代码": target.fund_code, "状态": parse_status, "报告期数": len(rows), "错误信息": parse_error}


def archive_url(data_type: str, fund_code: str, top_line: int = 50) -> str:
    if data_type == "stock_holding":
        query = f"type=jjcc&code={fund_code}&topline={int(top_line)}&year=&month=&rt=0.1"
    elif data_type == "bond_holding":
        query = f"type=zqcc&code={fund_code}&year=&rt=0.1"
    else:
        raise ValueError(f"unknown archive data_type: {data_type}")
    return f"{ARCHIVE_DATA_URL}?{query}"


def parse_js_content_variable(text: str) -> tuple[str, list[int], int | None]:
    content_match = re.search(r'content:"(.*?)",arryear:', text, re.DOTALL)
    content = ""
    if content_match:
        content = content_match.group(1).replace('\\"', '"').replace("\\'", "'")
    years_match = re.search(r"arryear:\[([^\]]*)\]", text)
    years: list[int] = []
    if years_match:
        for part in years_match.group(1).split(","):
            part = part.strip()
            if part.isdigit():
                years.append(int(part))
    cur_match = re.search(r"curyear:(\d{4})", text)
    cur_year = int(cur_match.group(1)) if cur_match else None
    return content, years, cur_year


def table_cells(row_html: str) -> list[str]:
    return [clean_text(cell) for cell in TABLE_CELL_RE.findall(row_html)]


def market_code_from_row(row_html: str, stock_code: str) -> str:
    match = re.search(r"unify/r/([01]\.\d{6})", row_html)
    if match:
        return match.group(1)
    if re.fullmatch(r"\d{6}", stock_code):
        return f"1.{stock_code}" if stock_code.startswith(("5", "6", "9")) else f"0.{stock_code}"
    return stock_code


def parse_stock_holding_content(content: str) -> tuple[str, list[dict[str, Any]]]:
    report_date = extract_archive_report_date(content)
    rows: list[dict[str, Any]] = []
    for row_html in TABLE_ROW_RE.findall(content):
        cells = table_cells(row_html)
        if len(cells) < 7 or not re.fullmatch(r"\d+", cells[0] or ""):
            continue
        stock_code = str(cells[1] if len(cells) > 1 else "").strip().upper()
        stock_code = re.sub(r"\s+", "", stock_code)
        if not re.fullmatch(r"[A-Z0-9.\\-]{1,16}", stock_code):
            continue
        stock_name = cells[2] if len(cells) > 2 else ""
        rows.append(
            {
                "股票代码": stock_code,
                "股票名称": stock_name,
                "市场代码": market_code_from_row(row_html, stock_code),
                "占基金净值比例_百分比": parse_float(cells[-3]) if len(cells) >= 3 else None,
                "持股数_万股": parse_float(cells[-2]) if len(cells) >= 2 else None,
                "持仓市值_万元": parse_float(cells[-1]) if len(cells) >= 1 else None,
            }
        )
    return report_date, rows


def parse_bond_holding_content(content: str) -> tuple[str, list[dict[str, Any]]]:
    report_date = extract_archive_report_date(content)
    rows: list[dict[str, Any]] = []
    for row_html in TABLE_ROW_RE.findall(content):
        cells = table_cells(row_html)
        if len(cells) < 5 or not re.fullmatch(r"\d+", cells[0] or ""):
            continue
        code_match = re.search(r"[A-Za-z0-9]{4,}", cells[1] if len(cells) > 1 else "")
        if not code_match:
            continue
        bond_code = code_match.group(0)
        ratio = None
        amount = None
        market_value = None
        numeric_tail = cells[3:]
        if len(numeric_tail) == 2:
            ratio = parse_float(numeric_tail[0])
            market_value = parse_float(numeric_tail[1])
        elif len(numeric_tail) >= 3:
            ratio = parse_float(numeric_tail[-3])
            amount = parse_float(numeric_tail[-2])
            market_value = parse_float(numeric_tail[-1])
        rows.append(
            {
                "债券代码": bond_code,
                "债券名称": cells[2] if len(cells) > 2 else "",
                "占基金净值比例_百分比": ratio,
                "持债数量": amount,
                "持仓市值_万元": market_value,
                "债券类型": infer_bond_type(cells[2] if len(cells) > 2 else ""),
            }
        )
    return report_date, rows


def infer_bond_type(name: str) -> str:
    if re.search(r"转债|可转债", name):
        return "可转债"
    if re.search(r"国债|国开|农发|进出|政策", name):
        return "利率债/政策性金融债"
    if re.search(r"金融债|银行", name):
        return "金融债"
    if re.search(r"企业债|公司债|中票|短融|MTN|CP", name, re.IGNORECASE):
        return "信用债"
    return "债券"


def eastmoney_industry_to_level1(industry: str, stock_name: str = "") -> str:
    text = f"{industry} {stock_name}"
    rules = [
        ("电子", r"半导体|元件|光学光电子|消费电子|电子化学品|电子元件|集成电路|芯片"),
        ("通信", r"通信设备|通信服务|光通信|电信运营"),
        ("计算机", r"软件|计算机|IT服务|互联网服务|云服务|信息安全|数字"),
        ("传媒", r"游戏|文化传媒|影视|出版|广告营销|互联网电商"),
        ("电力设备", r"电池|光伏|风电|电源设备|电网设备|电力设备|储能"),
        ("机械设备", r"工程机械|通用设备|专用设备|机器人|自动化|轨交设备"),
        ("汽车", r"汽车|汽车零部件|乘用车|商用车|摩托车"),
        ("国防军工", r"航天|航空|军工|船舶|兵器"),
        ("医药生物", r"化学制药|生物制品|医疗器械|医疗服务|中药|医药商业|创新药"),
        ("食品饮料", r"白酒|饮料|食品|调味|乳品|啤酒|休闲食品"),
        ("家用电器", r"家电|家用电器|厨卫电器|小家电"),
        ("商贸零售", r"零售|商贸|电商|百货"),
        ("社会服务", r"旅游|酒店|餐饮|教育|免税|专业服务"),
        ("农林牧渔", r"农牧|养殖|种植|饲料|渔业|种业"),
        ("银行", r"银行"),
        ("非银金融", r"证券|保险|多元金融|非银"),
        ("房地产", r"房地产|物业|房产"),
        ("有色金属", r"有色|金属|小金属|工业金属|贵金属|能源金属"),
        ("基础化工", r"化学|化工|塑料|橡胶|农化|化纤"),
        ("钢铁", r"钢铁|特钢"),
        ("煤炭", r"煤炭"),
        ("石油石化", r"石油|燃气|油服|炼化|石化"),
        ("公用事业", r"电力|公用事业|环保|水务|燃气"),
        ("交通运输", r"航空机场|航运港口|铁路公路|物流|快递|交通"),
        ("建筑材料", r"水泥|玻璃|装修建材|建筑材料"),
        ("建筑装饰", r"工程建设|建筑装饰|基建"),
        ("纺织服饰", r"纺织|服装|饰品"),
        ("美容护理", r"美容护理|化妆品"),
        ("轻工制造", r"造纸|包装|家居用品|文娱用品"),
        ("环保", r"环保"),
    ]
    for level1, pattern in rules:
        if re.search(pattern, text):
            return level1
    return industry or ""


def has_valid_stock_industry(row: dict[str, Any] | None) -> bool:
    if not row:
        return False
    return any(str(row.get(key) or "").strip() and str(row.get(key) or "").strip() != "未识别" for key in ("行业一级", "行业二级", "东财行业"))


def normalize_stock_industry_payload(row: dict[str, Any], source: str = "") -> dict[str, str]:
    code = str(row.get("股票代码") or row.get("stock_code") or "").strip().upper()
    name = str(row.get("股票名称") or row.get("stock_name") or "").strip()
    industry2 = str(row.get("行业二级") or row.get("东财行业") or row.get("industry2") or "").strip()
    industry1 = str(row.get("行业一级") or row.get("industry1") or "").strip()
    payload = {
        "股票代码": code,
        "股票名称": name,
        "市场代码": str(row.get("市场代码") or row.get("market_code") or "").strip(),
        "东财行业": str(row.get("东财行业") or row.get("eastmoney_industry") or industry2).strip(),
        "行业一级": industry1 or eastmoney_industry_to_level1(industry2, name),
        "行业二级": industry2,
        "地区板块": str(row.get("地区板块") or row.get("region") or "").strip(),
        "映射来源": source or str(row.get("映射来源") or row.get("source") or "").strip(),
        "映射说明": str(row.get("映射说明") or row.get("note") or "").strip(),
    }
    if not payload["行业二级"] and payload["东财行业"]:
        payload["行业二级"] = payload["东财行业"]
    if not payload["行业一级"] and payload["东财行业"]:
        payload["行业一级"] = eastmoney_industry_to_level1(payload["东财行业"], name)
    return payload


def load_stock_industry_overrides(path: Path = DEFAULT_STOCK_INDUSTRY_OVERRIDE_PATH) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    overrides: dict[str, dict[str, str]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            payload = normalize_stock_industry_payload(row, "人工补充表")
            if payload["股票代码"] and has_valid_stock_industry(payload):
                overrides[payload["股票代码"]] = payload
    return overrides


def stock_secid_from_row(stock: dict[str, Any]) -> str:
    market_code = str(stock.get("市场代码") or "").strip()
    stock_code = str(stock.get("股票代码") or "").strip().upper()
    if market_code:
        return market_code
    if re.fullmatch(r"\d{6}", stock_code):
        return f"1.{stock_code}" if stock_code.startswith(("5", "6", "9")) else f"0.{stock_code}"
    return stock_code


def resolve_stock_quote_id(stock_code: str, stock_name: str = "", timeout_sec: int = 20) -> tuple[str, str]:
    query = str(stock_code or stock_name or "").strip()
    if not query:
        return "", ""
    url = f"{EASTMONEY_SEARCH_URL}?input={quote(query)}&type=14"
    status, body, error = http_get(url, timeout_sec=timeout_sec, retries=0, referer="https://quote.eastmoney.com/")
    if not body or status != 200 or error:
        return "", ""
    try:
        payload = json.loads(decode_text(body))
    except json.JSONDecodeError:
        return "", ""
    rows = ((payload.get("QuotationCodeTable") or {}).get("Data") or [])
    if not rows:
        return "", ""
    code_norm = str(stock_code or "").strip().upper()
    for item in rows:
        item_code = str(item.get("Code") or item.get("UnifiedCode") or "").strip().upper()
        quote_id = str(item.get("QuoteID") or "").strip()
        if quote_id and item_code == code_norm:
            return quote_id, str(item.get("SecurityTypeName") or item.get("Classify") or "")
    item = rows[0]
    return str(item.get("QuoteID") or "").strip(), str(item.get("SecurityTypeName") or item.get("Classify") or "")


def fetch_single_stock_industry(secid: str, timeout_sec: int = 20, stock_code: str = "", stock_name: str = "") -> dict[str, str]:
    source_secid = str(secid or "").strip()
    if not re.fullmatch(r"[01]\.\d{6}", source_secid):
        resolved, security_type = resolve_stock_quote_id(stock_code or source_secid, stock_name, timeout_sec=timeout_sec)
        if resolved:
            source_secid = resolved
    else:
        security_type = "A股"
    if not re.fullmatch(r"\d+\.[A-Z0-9.\-]+", source_secid, re.IGNORECASE):
        return {}
    url = f"{PUSH2_STOCK_URL}?fltt=2&invt=2&fields=f57,f58,f100,f102,f127&secid={source_secid}"
    status, body, error = http_get(url, timeout_sec=timeout_sec, retries=0, referer="https://quote.eastmoney.com/")
    if not body or status != 200 or error:
        return {}
    try:
        payload = json.loads(decode_text(body))
    except json.JSONDecodeError:
        return {}
    data = payload.get("data") or {}
    code = str(data.get("f57") or "")
    name = str(data.get("f58") or "")
    industry = str(data.get("f100") or data.get("f127") or "")
    board = str(data.get("f102") or "")
    if not code or not industry:
        return {}
    return {
        "股票代码": code,
        "股票名称": name,
        "市场代码": source_secid,
        "东财行业": industry,
        "行业一级": eastmoney_industry_to_level1(industry, name),
        "行业二级": industry,
        "地区板块": board or security_type,
    }


def enrich_stock_industries(stocks: list[dict[str, Any]], timeout_sec: int = 20) -> dict[str, dict[str, str]]:
    secids = []
    secid_to_stock: dict[str, dict[str, Any]] = {}
    for stock in stocks:
        market_code = stock_secid_from_row(stock)
        if market_code:
            secids.append(market_code)
            secid_to_stock[market_code] = stock
    secids = sorted(set(secids))
    out: dict[str, dict[str, str]] = {}
    for index in range(0, len(secids), 80):
        chunk = ",".join(secids[index : index + 80])
        url = f"{PUSH2_ULIST_URL}?fltt=2&invt=2&fields=f12,f14,f100,f102,f127&secids={chunk}"
        status, body, error = http_get(url, timeout_sec=timeout_sec, retries=1)
        if not body or status != 200 or error:
            continue
        try:
            payload = json.loads(decode_text(body))
        except json.JSONDecodeError:
            continue
        for item in (payload.get("data") or {}).get("diff") or []:
            code = str(item.get("f12") or "")
            name = str(item.get("f14") or "")
            industry = str(item.get("f100") or item.get("f127") or "")
            board = str(item.get("f102") or "")
            if not code or not industry:
                continue
            out[code] = {
                "股票代码": code,
                "股票名称": name,
                "市场代码": "",
                "东财行业": industry,
                "行业一级": eastmoney_industry_to_level1(industry, name),
                "行业二级": industry,
                "地区板块": board,
            }
    missing_secids = [secid for secid in secids if not has_valid_stock_industry(out.get(secid.split(".", 1)[-1]))]
    for secid in missing_secids:
        stock = secid_to_stock.get(secid, {})
        info = fetch_single_stock_industry(
            secid,
            timeout_sec=timeout_sec,
            stock_code=str(stock.get("股票代码") or ""),
            stock_name=str(stock.get("股票名称") or ""),
        )
        if has_valid_stock_industry(info):
            out[info["股票代码"]] = info
            time.sleep(0.02)
    return out


def load_existing_stock_industry_map(conn: sqlite3.Connection, stock_codes: list[str]) -> dict[str, dict[str, str]]:
    codes = sorted({str(code or "").strip().upper() for code in stock_codes if str(code or "").strip()})
    if not codes:
        return {}
    out: dict[str, dict[str, str]] = {}
    for index in range(0, len(codes), 500):
        chunk = codes[index : index + 500]
        placeholders = ",".join("?" for _ in chunk)
        rows = conn.execute(
            f"""
            SELECT "股票代码","股票名称","市场代码","东财行业","行业一级","行业二级","地区板块"
            FROM "股票行业映射"
            WHERE "股票代码" IN ({placeholders})
            """,
            chunk,
        ).fetchall()
        for row in rows:
            payload = {key: str(row[key] or "") for key in row.keys()}
            if has_valid_stock_industry(payload):
                out[payload["股票代码"]] = payload
    return out


def merge_stock_industry(row: dict[str, Any], fetched: dict[str, Any] | None, existing: dict[str, Any] | None) -> dict[str, str]:
    merged: dict[str, str] = {}
    for source in (existing or {}, fetched or {}):
        for key in ("股票代码", "股票名称", "市场代码", "东财行业", "行业一级", "行业二级", "地区板块"):
            value = str(source.get(key) or "").strip()
            if value and value != "未识别":
                merged[key] = value
    merged["股票代码"] = str(row.get("股票代码") or merged.get("股票代码") or "").strip().upper()
    merged["股票名称"] = str((fetched or {}).get("股票名称") or row.get("股票名称") or merged.get("股票名称") or "").strip()
    merged["市场代码"] = str(row.get("市场代码") or merged.get("市场代码") or "").strip()
    if not merged.get("行业一级") and merged.get("东财行业"):
        merged["行业一级"] = eastmoney_industry_to_level1(merged["东财行业"], merged.get("股票名称", ""))
    return merged


def repair_stock_industry_fields(
    conn: sqlite3.Connection,
    fetch_missing: bool = False,
    timeout_sec: int = 20,
    workers: int = 8,
    limit: int | None = None,
) -> dict[str, Any]:
    captured_at = now_cn()
    best: dict[str, dict[str, str]] = {}
    override_rows = load_stock_industry_overrides()
    best.update(override_rows)
    mapped_rows = conn.execute(
        """
        SELECT "股票代码","股票名称","市场代码","东财行业","行业一级","行业二级","地区板块"
        FROM "股票行业映射"
        WHERE COALESCE("行业一级",'') NOT IN ('','未识别')
           OR COALESCE("行业二级",'') NOT IN ('','未识别')
           OR COALESCE("东财行业",'') NOT IN ('','未识别')
        """
    ).fetchall()
    for row in mapped_rows:
        payload = {key: str(row[key] or "") for key in row.keys()}
        if has_valid_stock_industry(payload):
            best[payload["股票代码"]] = payload

    holding_rows = conn.execute(
        """
        SELECT "股票代码", MAX("股票名称") AS "股票名称", MAX("市场代码") AS "市场代码",
               "行业一级","行业二级", COUNT(*) AS cnt,
               SUM(COALESCE("占基金净值比例_百分比",0)) AS weight
        FROM "基金季度股票持仓"
        WHERE COALESCE("行业一级",'') NOT IN ('','未识别')
        GROUP BY "股票代码","行业一级","行业二级"
        ORDER BY cnt DESC, weight DESC
        """
    ).fetchall()
    for row in holding_rows:
        code = str(row["股票代码"] or "")
        if code in best:
            continue
        payload = {
            "股票代码": code,
            "股票名称": str(row["股票名称"] or ""),
            "市场代码": str(row["市场代码"] or ""),
            "东财行业": str(row["行业二级"] or ""),
            "行业一级": str(row["行业一级"] or ""),
            "行业二级": str(row["行业二级"] or ""),
            "地区板块": "",
        }
        if has_valid_stock_industry(payload):
            best[code] = payload

    distinct_rows = conn.execute(
        """
        SELECT "股票代码","股票名称",MAX("市场代码") AS "市场代码",
               SUM(CASE WHEN COALESCE("行业一级",'') IN ('','未识别') THEN 1 ELSE 0 END) AS missing_rows
        FROM "基金季度股票持仓"
        GROUP BY "股票代码","股票名称"
        """
    ).fetchall()
    missing_fetch_rows = []
    for row in distinct_rows:
        code = str(row["股票代码"] or "").strip().upper()
        if not code or code in best:
            continue
        secid = stock_secid_from_row({"股票代码": code, "市场代码": row["市场代码"]})
        if re.fullmatch(r"[A-Z0-9.\-]{1,16}", code):
            missing_fetch_rows.append({"股票代码": code, "股票名称": row["股票名称"], "市场代码": secid})
    if limit:
        missing_fetch_rows = missing_fetch_rows[: max(0, limit)]

    fetched_count = 0
    if fetch_missing and missing_fetch_rows:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            future_map = {
                pool.submit(
                    fetch_single_stock_industry,
                    str(row["市场代码"]),
                    timeout_sec,
                    str(row["股票代码"] or ""),
                    str(row["股票名称"] or ""),
                ): row
                for row in missing_fetch_rows
            }
            for future in as_completed(future_map):
                info = future.result()
                if has_valid_stock_industry(info):
                    best[info["股票代码"]] = info
                    fetched_count += 1

    updated_map = 0
    updated_holdings = 0
    for code, info in sorted(best.items()):
        if not has_valid_stock_industry(info):
            continue
        source_label = info.get("映射来源") or "东财行业映射回填"
        before_map = conn.total_changes
        conn.execute(
            """
            INSERT OR REPLACE INTO "股票行业映射"
            ("股票代码","股票名称","市场代码","东财行业","行业一级","行业二级","地区板块","数据来源","更新时间")
            VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                code,
                info.get("股票名称") or "",
                info.get("市场代码") or stock_secid_from_row(info),
                info.get("东财行业") or info.get("行业二级") or "",
                info.get("行业一级") or eastmoney_industry_to_level1(info.get("行业二级") or info.get("东财行业") or "", info.get("股票名称") or ""),
                info.get("行业二级") or info.get("东财行业") or "",
                info.get("地区板块") or "",
                source_label,
                captured_at,
            ),
        )
        updated_map += conn.total_changes - before_map
        before = conn.total_changes
        conn.execute(
            """
            UPDATE "基金季度股票持仓"
            SET "行业一级"=?,
                "行业二级"=?,
                "行业来源"='东财行业映射回填'
            WHERE "股票代码"=?
              AND (COALESCE("行业一级",'') IN ('','未识别') OR COALESCE("行业二级",'') IN ('','未识别'))
            """,
            (
                info.get("行业一级") or eastmoney_industry_to_level1(info.get("行业二级") or info.get("东财行业") or "", info.get("股票名称") or ""),
                info.get("行业二级") or info.get("东财行业") or "",
                code,
            ),
        )
        updated_holdings += conn.total_changes - before
    conn.commit()
    return {
        "状态": "repaired",
        "有效映射股票数": len(best),
        "人工补充股票数": len(override_rows),
        "接口补充股票数": fetched_count,
        "待接口补充股票数": len(missing_fetch_rows),
        "更新持仓行数": updated_holdings,
        "更新时间": captured_at,
    }


def fetch_archive_holdings(target: FundTarget, args: argparse.Namespace, data_type: str) -> dict[str, Any]:
    stale_type = "stock_holding" if data_type == "stock_holding" else "bond_holding"
    with connect_db(args.db_path) as conn:
        if not args.force_refresh and latest_snapshot_is_fresh(conn, target.fund_code, stale_type, args.stale_days):
            return {"基金代码": target.fund_code, "数据类型": stale_type, "状态": "skipped_fresh"}
    url = archive_url(data_type, target.fund_code, args.top_line)
    status, body, error = http_get(
        url,
        timeout_sec=args.timeout_sec,
        retries=args.retries,
        referer=f"http://fundf10.eastmoney.com/ccmx_{target.fund_code}.html",
    )
    text = decode_text(body)
    content, _, _ = parse_js_content_variable(text)
    rows: list[dict[str, Any]]
    if data_type == "stock_holding":
        report_date, rows = parse_stock_holding_content(content)
    else:
        report_date, rows = parse_bond_holding_content(content)
    raw_fallback_path: Path | None = None
    if not rows and (not body or status is None or status >= 400):
        fallback_body, fallback_path = latest_non_empty_raw_snapshot(args.raw_root, stale_type, target.fund_code, ".js")
        if fallback_body:
            fallback_text = decode_text(fallback_body)
            fallback_content, _, _ = parse_js_content_variable(fallback_text)
            if data_type == "stock_holding":
                fallback_report_date, fallback_rows = parse_stock_holding_content(fallback_content)
            else:
                fallback_report_date, fallback_rows = parse_bond_holding_content(fallback_content)
            if fallback_rows:
                body = fallback_body
                content = fallback_content
                report_date = fallback_report_date
                rows = fallback_rows
                raw_fallback_path = fallback_path
    if rows and report_date:
        parse_status = "parsed"
        parse_error = None
    elif rows:
        parse_status = "failed"
        parse_error = "report_date_not_found"
    else:
        parse_status = "empty" if status and status < 400 else "failed"
        parse_error = error or "archive_content_empty"
    existing_industry_map: dict[str, dict[str, str]] = {}
    if data_type == "stock_holding" and rows:
        with connect_db(args.db_path) as conn:
            existing_industry_map = load_existing_stock_industry_map(conn, [row["股票代码"] for row in rows])
        rows_missing_industry = [row for row in rows if not has_valid_stock_industry(existing_industry_map.get(row["股票代码"]))]
        industry_map = enrich_stock_industries(rows_missing_industry, args.timeout_sec)
    else:
        industry_map = {}
    captured_at = now_cn()
    with connect_db(args.db_path) as conn:
        snapshot_id = save_raw_snapshot(
            conn,
            args.raw_root,
            target.fund_code,
            stale_type,
            url,
            status,
            body,
            report_date,
            parse_status,
            parse_error,
            suffix=".js" if body else ".empty.js",
        )
        if rows and report_date:
            if data_type == "stock_holding":
                conn.execute(
                    'DELETE FROM "基金季度股票持仓" WHERE "基金代码"=? AND "报告期"=?',
                    (target.fund_code, report_date),
                )
                for row in rows:
                    industry = merge_stock_industry(row, industry_map.get(row["股票代码"]), existing_industry_map.get(row["股票代码"]))
                    if has_valid_stock_industry(industry):
                        conn.execute(
                            """
                            INSERT OR REPLACE INTO "股票行业映射"
                            ("股票代码","股票名称","市场代码","东财行业","行业一级","行业二级","地区板块","数据来源","更新时间")
                            VALUES (?,?,?,?,?,?,?,?,?)
                            """,
                            (
                                row["股票代码"],
                                industry.get("股票名称") or row["股票名称"],
                                industry.get("市场代码") or row["市场代码"],
                                industry.get("东财行业") or "",
                                industry.get("行业一级") or "",
                                industry.get("行业二级") or "",
                                industry.get("地区板块") or "",
                                "东财push2",
                                captured_at,
                            ),
                        )
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO "基金季度股票持仓"
                        ("基金代码","报告期","股票代码","股票名称","市场代码","占基金净值比例_百分比",
                         "持股数_万股","持仓市值_万元","行业一级","行业二级","行业来源",
                         "数据来源","原始快照ID","采集时间")
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            target.fund_code,
                            report_date,
                            row["股票代码"],
                            row["股票名称"],
                            row["市场代码"],
                            row["占基金净值比例_百分比"],
                            row["持股数_万股"],
                            row["持仓市值_万元"],
                            industry.get("行业一级") or "",
                            industry.get("行业二级") or industry.get("东财行业") or "",
                            "东财push2行业字段",
                            "东财F10基金持仓",
                            snapshot_id,
                            captured_at,
                        ),
                    )
            else:
                conn.execute(
                    'DELETE FROM "基金季度债券持仓" WHERE "基金代码"=? AND "报告期"=?',
                    (target.fund_code, report_date),
                )
                for row in rows:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO "基金季度债券持仓"
                        ("基金代码","报告期","债券代码","债券名称","占基金净值比例_百分比","持债数量",
                         "持仓市值_万元","债券类型","数据来源","原始快照ID","采集时间")
                        VALUES (?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            target.fund_code,
                            report_date,
                            row["债券代码"],
                            row["债券名称"],
                            row["占基金净值比例_百分比"],
                            row["持债数量"],
                            row["持仓市值_万元"],
                            row["债券类型"],
                            "东财F10债券持仓",
                            snapshot_id,
                            captured_at,
                        ),
                    )
        conn.commit()
    return {
        "基金代码": target.fund_code,
        "数据类型": stale_type,
        "状态": parse_status,
        "报告期": report_date,
        "行数": len(rows),
        "错误信息": parse_error,
        "原始快照兜底": str(raw_fallback_path.relative_to(PROJECT_ROOT)) if raw_fallback_path else "",
    }


def rebuild_industry_allocation(conn: sqlite3.Connection) -> int:
    generated_at = now_cn()
    conn.execute('DELETE FROM "基金季度行业配置" WHERE "数据来源"=?', ("东财F10股票持仓推导",))
    rows = conn.execute(
        """
        SELECT "基金代码","报告期",COALESCE(NULLIF("行业一级",''),'未识别') AS industry,
               SUM(COALESCE("占基金净值比例_百分比",0)) AS weight,
               COUNT(*) AS sample_count
        FROM "基金季度股票持仓"
        GROUP BY "基金代码","报告期",COALESCE(NULLIF("行业一级",''),'未识别')
        HAVING weight > 0
        """
    ).fetchall()
    for row in rows:
        conn.execute(
            """
            INSERT OR REPLACE INTO "基金季度行业配置"
            ("基金代码","报告期","行业一级","占基金净值比例_百分比","股票持仓样本数","数据来源","生成时间")
            VALUES (?,?,?,?,?,?,?)
            """,
            (
                row["基金代码"],
                row["报告期"],
                row["industry"],
                row["weight"],
                row["sample_count"],
                "东财F10股票持仓推导",
                generated_at,
            ),
        )
    conn.commit()
    return len(rows)


def normalize_exposure(items: dict[str, float | None], keep_sum: bool = True) -> dict[str, float]:
    cleaned: dict[str, float] = {}
    for key, value in items.items():
        if not key or value is None:
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if abs(number) < 0.0001:
            continue
        cleaned[key] = round(number, 4)
    if keep_sum:
        return cleaned
    total = sum(cleaned.values())
    if total <= 0:
        return {}
    return {key: round(value * 100 / total, 4) for key, value in cleaned.items()}


def equity_bucket(fund_name: str, fund_type: str, region_tag: str = "") -> str:
    text = f"{fund_name} {fund_type} {region_tag}"
    if re.search(r"港股|恒生|H股|港股通", text):
        return "港股"
    if re.search(r"纳斯达克|纳指|标普|美股|美国|S&P|NASDAQ", text, re.IGNORECASE):
        return "美股"
    if re.search(r"越南|印度|巴西|东盟|新兴市场", text):
        return "新兴市场"
    if re.search(r"德国|日本|欧洲|全球|海外|QDII|发达市场", text):
        return "其他发达市场"
    return "A股"


def bond_bucket(fund_name: str, fund_type: str) -> str:
    return "海外债券" if re.search(r"海外债|美元债|亚洲债|全球债|QDII债|中资美元债", f"{fund_name} {fund_type}") else "债券"


def build_asset_exposure(row: sqlite3.Row, fund_name: str, fund_type: str, region_tag: str = "") -> dict[str, float]:
    exposure: dict[str, float | None] = {}
    stock = row["股票占比_百分比"]
    if stock is not None and stock > 0:
        exposure[equity_bucket(fund_name, fund_type, region_tag)] = stock
    bond = row["债券占比_百分比"]
    if bond is not None and bond > 0:
        exposure[bond_bucket(fund_name, fund_type)] = bond
    cash = row["现金占比_百分比"]
    if cash is not None and cash > 0:
        exposure["货币及现金"] = cash
    fund = row["基金占比_百分比"]
    if fund is not None and fund > 0:
        exposure["基金"] = fund
    commodity = row["商品占比_百分比"]
    if commodity is not None and commodity > 0:
        exposure["其他商品"] = commodity
    cdr = row["存托凭证占比_百分比"]
    if cdr is not None and cdr > 0:
        exposure["存托凭证"] = cdr
    other = row["其他占比_百分比"]
    if other is not None and other > 0.05:
        exposure["其他"] = other
    return normalize_exposure(exposure)


def latest_asset_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        WITH latest AS (
          SELECT "基金代码", MAX("报告期") AS latest_report
          FROM "基金季度资产配置"
          GROUP BY "基金代码"
        )
        SELECT a.*, f."基金名称" AS fund_name, f."基金公司" AS fund_company,
               COALESCE(f."基金类型", c."天天基金细分类", c."天天基金大类") AS fund_type,
               c."天天基金二级分类" AS secondary_class,
               c."市场地域标签" AS region_tag,
               c."主题标签JSON" AS theme_json
        FROM "基金季度资产配置" a
        JOIN latest l ON l."基金代码"=a."基金代码" AND l.latest_report=a."报告期"
        LEFT JOIN "基金信息" f ON f."基金代码"=a."基金代码"
        LEFT JOIN "基金标准分类字典" c ON c."基金代码"=a."基金代码"
        """
    ).fetchall()


def industry_exposure_for(conn: sqlite3.Connection, fund_code: str, report_date: str) -> dict[str, float]:
    rows = conn.execute(
        """
        SELECT "行业一级", "占基金净值比例_百分比"
        FROM "基金季度行业配置"
        WHERE "基金代码"=? AND "报告期"=? AND "行业一级" <> '未识别'
        """,
        (fund_code, report_date),
    ).fetchall()
    weights = {str(row["行业一级"]): float(row["占基金净值比例_百分比"] or 0) for row in rows}
    return normalize_exposure(weights, keep_sum=False)


def build_classification_snapshots(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    generated_at = now_cn()
    conn.execute('DELETE FROM "基金分类快照"')
    output: list[dict[str, Any]] = []
    for row in latest_asset_rows(conn):
        fund_code = str(row["基金代码"])
        fund_name = str(row["fund_name"] or "")
        fund_type = str(row["fund_type"] or "")
        company = str(row["fund_company"] or "")
        secondary = str(row["secondary_class"] or fund_type or "")
        region_tag = str(row["region_tag"] or "")
        asset_exposure = build_asset_exposure(row, fund_name, fund_type, region_tag)
        industry_exposure = industry_exposure_for(conn, fund_code, str(row["报告期"]))
        theme_json = row["theme_json"] or "[]"
        coverage = "exact_quarterly_asset_and_stock" if industry_exposure else "exact_quarterly_asset_only"
        payload = {
            "基金代码": fund_code,
            "报告期": row["报告期"],
            "披露日期": row["披露日期"],
            "基金名称": fund_name,
            "基金公司": company,
            "基金类型": fund_type,
            "二级分类": secondary,
            "资产暴露": asset_exposure,
            "行业暴露": industry_exposure,
            "主题标签JSON": theme_json,
            "分类来源": "东财F10季报穿透",
            "是否估算": "否",
            "覆盖状态": coverage,
            "生成时间": generated_at,
        }
        conn.execute(
            """
            INSERT OR REPLACE INTO "基金分类快照"
            ("基金代码","报告期","披露日期","基金名称","基金公司","基金类型","二级分类",
             "资产暴露JSON","行业暴露JSON","主题标签JSON","分类来源","是否估算","覆盖状态","生成时间")
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                fund_code,
                row["报告期"],
                row["披露日期"],
                fund_name,
                company,
                fund_type,
                secondary,
                json.dumps(asset_exposure, ensure_ascii=False, sort_keys=True),
                json.dumps(industry_exposure, ensure_ascii=False, sort_keys=True),
                theme_json,
                "东财F10季报穿透",
                0,
                coverage,
                generated_at,
            ),
        )
        output.append(payload)
    conn.commit()
    return output


def write_snapshot_exports(rows: list[dict[str, Any]], output_root: Path = DEFAULT_OUTPUT_ROOT) -> dict[str, Path]:
    output_root.mkdir(parents=True, exist_ok=True)
    json_path = output_root / "latest_fund_classification_snapshot.json"
    js_path = output_root / "latest_fund_classification_snapshot.js"
    payload = {
        "生成时间": now_cn(),
        "说明": "基金资产/行业暴露优先来自东财F10季报穿透；缺失基金由报表加工脚本继续使用规则估算兜底。",
        "rows": rows,
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    json_path.write_text(text, encoding="utf-8")
    js_path.write_text(f"window.__FUND_LOOKTHROUGH_SNAPSHOT__ = {json.dumps(payload, ensure_ascii=False)};\n", encoding="utf-8")
    return {"json": json_path, "js": js_path}


def collect_with_workers(
    targets: list[FundTarget],
    args: argparse.Namespace,
    worker_fn,
    progress_label: str,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        future_map = {pool.submit(worker_fn, target, args): target for target in targets}
        for index, future in enumerate(as_completed(future_map), start=1):
            target = future_map[future]
            try:
                result = future.result()
            except Exception as exc:  # noqa: BLE001
                result = {"基金代码": target.fund_code, "状态": "failed", "错误信息": str(exc)}
            results.append(result)
            if index == 1 or index % max(1, args.progress_every) == 0 or index == len(targets):
                print(f"[{progress_label}] {index}/{len(targets)} {target.fund_code} {result.get('状态')}", flush=True)
    return results


def write_run_summary(output_root: Path, name: str, results: list[dict[str, Any]], extra: dict[str, Any] | None = None) -> Path:
    output_root.mkdir(parents=True, exist_ok=True)
    path = output_root / f"{name}_{run_id()}.json"
    payload = {
        "生成时间": now_cn(),
        "任务": name,
        "总数": len(results),
        "状态统计": status_counts(results),
        "结果": results,
    }
    if extra:
        payload.update(extra)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def status_counts(results: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in results:
        status = str(row.get("状态") or "unknown")
        out[status] = out.get(status, 0) + 1
    return out


def cleanup_impossible_bond_holding_weights(db_path: Path = DEFAULT_DB_PATH, threshold: float = 200.0) -> dict[str, Any]:
    with connect_db(db_path) as conn:
        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT "基金代码","报告期","债券代码","债券名称","占基金净值比例_百分比","原始快照ID","采集时间"
                FROM "基金季度债券持仓"
                WHERE "占基金净值比例_百分比" > ?
                ORDER BY "占基金净值比例_百分比" DESC
                """,
                (threshold,),
            )
        ]
        if rows:
            conn.execute(
                'DELETE FROM "基金季度债券持仓" WHERE "占基金净值比例_百分比" > ?',
                (threshold,),
            )
            conn.commit()
    return {"阈值": threshold, "清理行数": len(rows), "样例": rows[:20]}


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--raw-root", type=Path, default=DEFAULT_RAW_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--fund-code", action="append", default=[], help="只处理指定基金代码，可重复传入。")
    parser.add_argument("--limit", type=int, default=None, help="最多处理多少只基金，用于抽样验证。")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--timeout-sec", type=int, default=20)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument(
        "--stale-days",
        type=int,
        default=30,
        help="最近 N 天已成功抓取则跳过，0 表示不跳过；未采过或解析失败的基金仍会采集。",
    )
    parser.add_argument("--force-refresh", action="store_true")
    parser.add_argument("--progress-every", type=int, default=50)
    parser.add_argument("--dry-run", action="store_true")


def build_targets_from_args(args: argparse.Namespace) -> list[FundTarget]:
    with connect_db(args.db_path) as conn:
        return load_target_funds(conn, limit=args.limit, fund_codes=args.fund_code)


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
