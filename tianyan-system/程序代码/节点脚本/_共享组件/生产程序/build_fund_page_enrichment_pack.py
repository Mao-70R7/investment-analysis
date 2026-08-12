from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
DEFAULT_SITE_DIR = PROJECT_ROOT / "site" / "basic_data"
CN_TZ_SUFFIX = "+08:00"
BROAD_UNKNOWN_TOLERANCE_PP = 0.01


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build per-fund enrichment packs for the basic_data fund detail page.")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--site-dir", type=Path, default=DEFAULT_SITE_DIR, help="basic_data page directory, for example site/basic_data.")
    parser.add_argument("--nav-lookback-days", type=int, default=370)
    parser.add_argument("--max-nav-points", type=int, default=90)
    parser.add_argument("--top-stock-holdings", type=int, default=30)
    parser.add_argument("--top-bond-holdings", type=int, default=30)
    parser.add_argument(
        "--fund-universe",
        choices=["all-dict", "current-dict", "site-plus-fof"],
        default="all-dict",
        help="Fund detail scope: all public funds in dictionary, current dictionary funds, or legacy site pack plus FOF.",
    )
    parser.add_argument("--limit", type=int, default=0, help="Optional debug limit for fund count.")
    return parser.parse_args()


def now_cn_iso() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S") + CN_TZ_SUFFIX


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def write_text_if_changed(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            if path.read_text(encoding="utf-8") == text:
                return
        except UnicodeDecodeError:
            pass
    last_error: OSError | None = None
    for attempt in range(8):
        tmp = path.with_name(f".{path.name}.{os.getpid()}.{attempt}.tmp")
        try:
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(path)
            return
        except OSError as exc:
            last_error = exc
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
            time.sleep(0.15 * (attempt + 1))
    if last_error is not None:
        raise last_error


def js_assignment(path: Path, lhs: str, payload: Any) -> None:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    text = (
        "window.__BASIC_DATA__ = window.__BASIC_DATA__ || {}; "
        "window.__BASIC_DATA__.fundEnrichmentDetails = window.__BASIC_DATA__.fundEnrichmentDetails || {}; "
        f"{lhs} = {body};\n"
    )
    write_text_if_changed(path, text)


def safe_filename(value: str) -> str:
    safe = re.sub(r"[^0-9A-Za-z_.-]+", "_", str(value or "").strip())
    return safe[:120] or "fund"


def normalize_fund_code(value: Any) -> str:
    match = re.search(r"(\d{6})", str(value or ""))
    return match.group(1) if match else ""


def load_fund_list(site_dir: Path) -> list[dict[str, str]]:
    pack_path = site_dir / "data" / "fund_detail_pack.js"
    if not pack_path.exists():
        raise SystemExit(f"missing fund detail pack: {pack_path}")
    text = read_text(pack_path)
    match = re.search(r"fundDetailPack\s*=\s*(\{.*\});?\s*$", text, re.S)
    if not match:
        raise SystemExit(f"cannot parse fund detail pack: {pack_path}")
    pack = json.loads(match.group(1))
    fields = pack.get("fundFields") or []
    funds = pack.get("funds") or []
    code_index = fields.index("基金代码") if "基金代码" in fields else 0
    name_index = fields.index("基金名称") if "基金名称" in fields else 1
    company_index = fields.index("基金公司") if "基金公司" in fields else -1
    seen: dict[str, dict[str, str]] = {}
    for row in funds:
        code = normalize_fund_code(row[code_index] if code_index < len(row) else "")
        if not code:
            continue
        item = seen.setdefault(
            code,
            {
                "code": code,
                "name": str(row[name_index] if name_index < len(row) else ""),
                "company": str(row[company_index] if company_index >= 0 and company_index < len(row) else ""),
            },
        )
        if not item.get("name") and name_index < len(row):
            item["name"] = str(row[name_index] or "")
        if not item.get("company") and company_index >= 0 and company_index < len(row):
            item["company"] = str(row[company_index] or "")
    return sorted(seen.values(), key=lambda item: item["code"])


def merge_fund_lists(*sources: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: dict[str, dict[str, str]] = {}
    for source in sources:
        for fund in source:
            code = normalize_fund_code(fund.get("code"))
            if not code:
                continue
            item = seen.setdefault(code, {"code": code, "name": "", "company": ""})
            if not item.get("name") and fund.get("name"):
                item["name"] = str(fund.get("name") or "")
            if not item.get("company") and fund.get("company"):
                item["company"] = str(fund.get("company") or "")
    return sorted(seen.values(), key=lambda item: item["code"])


def connect_db(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise SystemExit(f"missing sqlite database: {path}")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def one(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...]) -> dict[str, Any] | None:
    row = conn.execute(sql, params).fetchone()
    return dict(row) if row else None


def many(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def optional_one(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...]) -> dict[str, Any]:
    try:
        return one(conn, sql, params) or {}
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return {}
        raise


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    return row is not None


def load_fof_universe_fund_list(conn: sqlite3.Connection) -> list[dict[str, str]]:
    rows = many(
        conn,
        """
        SELECT d."基金代码" AS code,
               COALESCE(
                 NULLIF(TRIM(p."基金名称"), ''),
                 NULLIF(TRIM(i."基金名称"), ''),
                 NULLIF(TRIM(b."F10基金简称"), ''),
                 NULLIF(TRIM(b."F10基金全称"), ''),
                 NULLIF(TRIM(d."标准基金名称"), ''),
                 d."基金代码"
               ) AS name,
               COALESCE(
                 NULLIF(TRIM(p."基金公司"), ''),
                 NULLIF(TRIM(i."基金公司"), ''),
                 NULLIF(TRIM(b."基金公司"), ''),
                 NULLIF(TRIM(d."基金公司"), '')
               ) AS company
        FROM "基金标准分类字典" d
        LEFT JOIN "FOF产品绩效快照" p ON p."基金代码" = d."基金代码"
        LEFT JOIN "基金信息" i ON i."基金代码" = d."基金代码"
        LEFT JOIN "FOF基金F10基准" b ON b."基金代码" = d."基金代码"
        WHERE d."是否FOF" = 1
        ORDER BY d."基金代码"
        """,
        (),
    )
    return [
        {
            "code": normalize_fund_code(row.get("code")),
            "name": str(row.get("name") or ""),
            "company": str(row.get("company") or ""),
        }
        for row in rows
        if normalize_fund_code(row.get("code"))
    ]


def load_dictionary_fund_list(conn: sqlite3.Connection, *, current_only: bool) -> list[dict[str, str]]:
    where_clause = 'WHERE d."是否当前库使用" = 1' if current_only else ""
    has_public_snapshot = table_exists(conn, "公募基金产品绩效快照")
    has_fund_f10 = table_exists(conn, "基金F10基准")
    public_join = (
        'LEFT JOIN "公募基金产品绩效快照" p ON p."基金代码" = d."基金代码"'
        if has_public_snapshot
        else ""
    )
    fund_f10_join = (
        'LEFT JOIN "基金F10基准" b ON b."基金代码" = d."基金代码"'
        if has_fund_f10
        else ""
    )
    public_name = 'NULLIF(TRIM(p."基金名称"), \'\'),' if has_public_snapshot else ""
    public_company = 'NULLIF(TRIM(p."基金公司"), \'\'),' if has_public_snapshot else ""
    f10_name = 'NULLIF(TRIM(b."F10基金简称"), \'\'), NULLIF(TRIM(b."F10基金全称"), \'\'), NULLIF(TRIM(b."基金名称"), \'\'),' if has_fund_f10 else ""
    f10_company = 'NULLIF(TRIM(b."基金公司"), \'\'),' if has_fund_f10 else ""
    rows = many(
        conn,
        f"""
        SELECT d."基金代码" AS code,
               COALESCE(
                 {public_name}
                 NULLIF(TRIM(i."基金名称"), ''),
                 {f10_name}
                 NULLIF(TRIM(d."标准基金名称"), ''),
                 d."基金代码"
               ) AS name,
               COALESCE(
                 {public_company}
                 NULLIF(TRIM(i."基金公司"), ''),
                 {f10_company}
                 NULLIF(TRIM(d."基金公司"), '')
               ) AS company
        FROM "基金标准分类字典" d
        LEFT JOIN "基金信息" i ON i."基金代码" = d."基金代码"
        {public_join}
        {fund_f10_join}
        {where_clause}
        ORDER BY d."基金代码"
        """,
        (),
    )
    return [
        {
            "code": normalize_fund_code(row.get("code")),
            "name": str(row.get("name") or ""),
            "company": str(row.get("company") or ""),
        }
        for row in rows
        if normalize_fund_code(row.get("code"))
    ]


def load_strategy_referenced_fund_list(conn: sqlite3.Connection) -> list[dict[str, str]]:
    """Include every fund that a strategy page can link to, even outside the public dictionary."""

    sources = (
        ("策略当前持仓", "基金代码", "基金名称"),
        ("策略调仓明细", "基金代码", "基金名称"),
        ("信号策略基金指令", "基金代码", "基金名称"),
        ("策略当前持仓推算补齐", "基金代码", "基金名称"),
    )
    seen: dict[str, dict[str, str]] = {}
    for table, code_field, name_field in sources:
        if not table_exists(conn, table):
            continue
        columns = {str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")')}
        if code_field not in columns or name_field not in columns:
            continue
        for row in many(
            conn,
            f'''SELECT "{code_field}" AS code, MAX("{name_field}") AS name
                FROM "{table}"
                WHERE TRIM(COALESCE("{code_field}", '')) <> ''
                GROUP BY "{code_field}"''',
            (),
        ):
            code = normalize_fund_code(row.get("code"))
            if not code:
                continue
            item = seen.setdefault(code, {"code": code, "name": "", "company": ""})
            if not item["name"] and row.get("name"):
                item["name"] = str(row.get("name") or "")
    if table_exists(conn, "基金信息"):
        for item in seen.values():
            info = one(
                conn,
                'SELECT "基金名称", "基金公司" FROM "基金信息" WHERE "基金代码"=?',
                (item["code"],),
            ) or {}
            item["name"] = str(info.get("基金名称") or item["name"] or item["code"])
            item["company"] = str(info.get("基金公司") or "")
    return sorted(seen.values(), key=lambda item: item["code"])


def round_float(value: Any, digits: int = 6) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, digits)


def broad_equity_bucket_from_percent(value: float | None) -> str:
    if value is None:
        return ""
    if value <= 0:
        return "L0"
    level = min(10, max(1, int((value + 9.999999) // 10)))
    return f"L{level}"


def add_broad_equity_fields(row: dict[str, Any]) -> dict[str, Any]:
    if not row:
        return row
    equity = round_float(row.get("基准权益权重_百分比")) or 0.0
    commodity = round_float(row.get("基准商品权重_百分比")) or 0.0
    alternative = round_float(row.get("基准另类权重_百分比")) or 0.0
    unknown = round_float(row.get("基准未知权重_百分比")) or 0.0
    if unknown > BROAD_UNKNOWN_TOLERANCE_PP:
        row["基准风险资产权重_百分比"] = None
        row["基准风险资产权重"] = ""
        row["基准风险资产权重说明"] = "基准未知权重超过0.01%，基准风险资产不硬分档。"
    else:
        broad = max(0.0, min(100.0, equity + commodity + alternative))
        row["基准风险资产权重_百分比"] = round_float(broad, 6)
        row["基准风险资产权重"] = broad_equity_bucket_from_percent(broad)
        row["基准风险资产权重说明"] = "基准风险资产=基准权益+基准商品+基准另类；港股/海外权益是权益子项，不重复计入。"
    row["基准风险资产口径说明"] = "基准风险资产用于观察权益、商品、另类风险资产合并后的分档；正式可比池仍使用基准风险资产权重+非权益比较轨道。"
    return row


def normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, float):
            out[key] = round_float(value, 6)
        else:
            out[key] = value
    return out


def normalize_json_text(value: Any) -> Any:
    if not value:
        return None
    try:
        return json.loads(str(value))
    except json.JSONDecodeError:
        return value


def downsample_nav(rows: list[dict[str, Any]], max_points: int) -> list[dict[str, Any]]:
    if len(rows) <= max_points:
        return rows
    weekly: dict[tuple[int, int], dict[str, Any]] = {}
    for row in rows:
        try:
            d = datetime.strptime(str(row.get("交易日期")), "%Y-%m-%d").date()
        except ValueError:
            continue
        iso = d.isocalendar()
        weekly[(iso.year, iso.week)] = row
    points = sorted(weekly.values(), key=lambda item: str(item.get("交易日期") or ""))
    if not points:
        return rows[-max_points:]
    if points[-1].get("交易日期") != rows[-1].get("交易日期"):
        points.append(rows[-1])
    if len(points) <= max_points:
        return points
    step = (len(points) - 1) / (max_points - 1)
    selected = [points[round(i * step)] for i in range(max_points)]
    deduped: list[dict[str, Any]] = []
    seen_dates: set[str] = set()
    for point in selected:
        d = str(point.get("交易日期") or "")
        if d and d not in seen_dates:
            deduped.append(point)
            seen_dates.add(d)
    if deduped[-1].get("交易日期") != points[-1].get("交易日期"):
        deduped[-1] = points[-1]
    return deduped


def build_nav(conn: sqlite3.Connection, code: str, lookback_days: int, max_points: int) -> dict[str, Any]:
    cutoff = (date.today() - timedelta(days=lookback_days)).isoformat()
    raw_rows = many(
        conn,
        """
        SELECT "交易日期","单位净值","累计净值","复权净值","日收益率_百分比","每万份收益","七日年化收益率_百分比",
               "净值口径","是否货币基金","数据来源"
        FROM "基金日度净值"
        WHERE "基金代码" = ? AND "交易日期" >= ?
        ORDER BY "交易日期"
        """,
        (code, cutoff),
    )
    if not raw_rows:
        raw_rows = many(
            conn,
            """
            SELECT "交易日期","单位净值","累计净值","复权净值","日收益率_百分比","每万份收益","七日年化收益率_百分比",
                   "净值口径","是否货币基金","数据来源"
            FROM "基金日度净值"
            WHERE "基金代码" = ?
            ORDER BY "交易日期" DESC
            LIMIT 120
            """,
            (code,),
        )
        raw_rows.reverse()
    rows: list[dict[str, Any]] = []
    base_value: float | None = None
    previous_value: float | None = None
    previous_raw_value: float | None = None
    extended_adjusted_rows = 0
    basis = "复权净值"
    for raw in raw_rows:
        row = normalize_row(raw)
        unit_nav = round_float(row.get("单位净值"), 6)
        acc_nav = round_float(row.get("累计净值"), 6)
        adjusted_nav = round_float(row.get("复权净值"), 8)
        daily_pct = round_float(row.get("日收益率_百分比"), 6)
        raw_value = acc_nav if acc_nav is not None else unit_nav
        is_money = bool(row.get("是否货币基金"))
        if adjusted_nav is not None and adjusted_nav > 0:
            value = adjusted_nav
        elif previous_value is not None and daily_pct is not None and daily_pct > -100:
            value = previous_value * (1 + daily_pct / 100)
            extended_adjusted_rows += 1
        elif previous_value is not None and raw_value is not None and previous_raw_value is not None and previous_raw_value > 0:
            value = previous_value * raw_value / previous_raw_value
            extended_adjusted_rows += 1
        elif raw_value is not None and raw_value > 0:
            value = raw_value
        elif daily_pct is not None and daily_pct > -100:
            value = (previous_value or 1.0) * (1 + daily_pct / 100)
            extended_adjusted_rows += 1
        else:
            value = None
        if value is not None and value > 0:
            if base_value is None:
                base_value = value
            index_value = value / base_value * 100 if base_value else None
            previous_value = value
        else:
            index_value = None
        if raw_value is not None and raw_value > 0:
            previous_raw_value = raw_value
        row["走势图指数"] = round_float(index_value, 6)
        rows.append(row)
    if extended_adjusted_rows:
        basis = "复权净值续接" if not is_money else "货币基金收益复利续接"
    elif rows and not any(row.get("复权净值") is not None for row in rows):
        basis = "累计净值" if any(row.get("累计净值") is not None for row in rows) else "单位净值"
    points = downsample_nav([row for row in rows if row.get("走势图指数") is not None], max_points)
    latest = rows[-1] if rows else None
    return {
        "basis": basis,
        "lookbackDays": lookback_days,
        "maxPoints": max_points,
        "latest": latest,
        "rows": points,
    }


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


_INDEX_SUMMARY_CACHE: list[dict[str, Any]] | None = None
_INDEX_SERIES_CACHE: dict[str, list[dict[str, Any]]] = {}


def load_index_summary(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    global _INDEX_SUMMARY_CACHE
    if _INDEX_SUMMARY_CACHE is None:
        _INDEX_SUMMARY_CACHE = many(
            conn,
            """
            SELECT "指数代码","指数名称",MAX("交易日期") AS latest_date,COUNT(*) AS row_count
            FROM "指数日度行情"
            GROUP BY "指数代码","指数名称"
            """,
            (),
        )
    return _INDEX_SUMMARY_CACHE


def load_index_series(conn: sqlite3.Connection, code: str) -> list[dict[str, Any]]:
    if code not in _INDEX_SERIES_CACHE:
        _INDEX_SERIES_CACHE[code] = many(
            conn,
            """
            SELECT "交易日期","收盘点位","日涨跌幅_百分比","数据来源"
            FROM "指数日度行情"
            WHERE "指数代码" = ?
            ORDER BY "交易日期"
            """,
            (code,),
        )
    return _INDEX_SERIES_CACHE[code]


def choose_benchmark(conn: sqlite3.Connection, fund: dict[str, str], profile: dict[str, Any], snapshots: list[dict[str, Any]]) -> dict[str, str]:
    dictionary = profile.get("dictionary") or {}
    info = profile.get("info") or {}
    fof_snapshot = profile.get("fofSnapshot") or {}
    fof_f10 = profile.get("fofF10") or {}
    public_snapshot = profile.get("publicSnapshot") or {}
    fund_f10 = profile.get("fundF10") or {}
    latest_snapshot = snapshots[0] if snapshots else {}
    fund_name = " ".join(
        str(value or "")
        for value in (
            fund.get("name"),
            info.get("基金名称"),
            dictionary.get("标准基金名称"),
            public_snapshot.get("基金名称"),
            public_snapshot.get("业绩比较基准"),
            public_snapshot.get("跟踪标的"),
            public_snapshot.get("F10基金类型"),
            fof_snapshot.get("基金名称"),
            fof_snapshot.get("业绩比较基准"),
            fof_snapshot.get("FOF公开分类"),
            fof_snapshot.get("FOF基准细分分类"),
            fund_f10.get("业绩比较基准"),
            fund_f10.get("跟踪标的"),
            fund_f10.get("F10基金类型"),
            fof_f10.get("业绩比较基准"),
            fof_f10.get("F10基金类型"),
            dictionary.get("跟踪指数_名称推断"),
            info.get("跟踪指数"),
            dictionary.get("天天基金大类"),
            dictionary.get("天天基金二级分类"),
            dictionary.get("标准资产大类"),
            dictionary.get("标准资产细类"),
            dictionary.get("市场地域标签"),
            latest_snapshot.get("行业主题"),
            latest_snapshot.get("研报大类资产"),
        )
    )
    index_rows = load_index_summary(conn)
    tracking_text = clean_text(dictionary.get("跟踪指数_名称推断") or info.get("跟踪指数"))
    if tracking_text:
        matches = []
        for row in index_rows:
            index_name = clean_text(row.get("指数名称"))
            if tracking_text and index_name and (tracking_text in index_name or index_name in tracking_text):
                matches.append(row)
        if matches:
            row = sorted(matches, key=lambda item: (item.get("row_count") or 0, item.get("latest_date") or ""), reverse=True)[0]
            return {"code": row["指数代码"], "name": row["指数名称"], "reason": "匹配基金字典中的跟踪指数名称"}
    text = fund_name
    rules = [
        (r"货币|现金|每万份|七日年化", "H11025.CSI", "中证货币基金", "货币基金默认基准"),
        (r"短债|中短债", "H11015.CSI", "中证短债", "短债基金默认基准"),
        (r"纯债", "930609.CSI", "中证纯债债券型基金", "纯债基金默认基准"),
        (r"债券|固收|可转债", "H11023.CSI", "中证债券型基金", "债券基金默认基准"),
        (r"黄金|金价|贵金属", "AU9999.SGE", "上海黄金Au99.99", "黄金商品基金默认基准"),
        (r"商品|大宗|期货", "NHCI.NHF", "南华商品指数", "商品基金默认基准"),
        (r"港股|恒生|h股|港股通", "HSI.HI", "恒生指数", "港股基金默认基准"),
        (r"纳斯达克|纳指|nasdaq|美股科技", "NDX.GI", "纳斯达克100", "美股科技基金默认基准"),
        (r"标普|s&p|美国", "SPX.GI", "标普500", "美股基金默认基准"),
        (r"tmt|人工智能|ai|科技|信息|计算机|电子|通信", "H30318.CSI", "TMT150", "科技主题基金默认基准"),
        (r"医药|医疗|创新药|生物", "000933.SH", "中证医药卫生", "医药主题基金默认基准"),
        (r"消费|食品|饮料|白酒", "000942.SH", "中证内地消费主题", "消费主题基金默认基准"),
        (r"军工|国防", "399967.SZ", "中证军工", "军工主题基金默认基准"),
        (r"新能源|光伏|电池", "000941.SH", "中证新能源", "新能源主题基金默认基准"),
        (r"中证1000", "000852.SH", "中证1000", "宽基指数基金默认基准"),
        (r"中证500", "000905.SH", "中证500", "宽基指数基金默认基准"),
        (r"沪深300", "000300.SH", "沪深300", "宽基指数基金默认基准"),
        (r"a500|中证a500", "000510.SH", "中证A500", "宽基指数基金默认基准"),
        (r"股票|权益|混合|指数|etf|联接", "930950.CSI", "中证偏股型基金", "权益/混合基金默认基准"),
    ]
    for pattern, code, name, reason in rules:
        if re.search(pattern, text, re.IGNORECASE):
            return {"code": code, "name": name, "reason": reason}
    return {"code": "000300.SH", "name": "沪深300", "reason": "未命中特定类别，使用沪深300作为通用权益参考"}


def build_benchmark(conn: sqlite3.Connection, benchmark: dict[str, str], nav: dict[str, Any], max_points: int) -> dict[str, Any]:
    nav_rows = nav.get("rows") or []
    if not nav_rows:
        return {**benchmark, "rows": [], "status": "missing_fund_nav"}
    start = str(nav_rows[0].get("交易日期") or "")
    end = str(nav_rows[-1].get("交易日期") or "")
    rows = [
        row
        for row in load_index_series(conn, benchmark["code"])
        if start <= str(row.get("交易日期") or "") <= end
    ]
    if not rows:
        return {**benchmark, "rows": [], "status": "missing_index_series"}
    normalized: list[dict[str, Any]] = []
    base: float | None = None
    for row in rows:
        close = round_float(row.get("收盘点位"), 6)
        if close is None:
            continue
        if base is None:
            base = close
        normalized.append(
            {
                "交易日期": row.get("交易日期"),
                "收盘点位": close,
                "日涨跌幅_百分比": round_float(row.get("日涨跌幅_百分比"), 6),
                "走势图指数": round_float(close / base * 100 if base else None, 6),
                "数据来源": row.get("数据来源"),
            }
        )
    return {**benchmark, "rows": downsample_nav(normalized, max_points), "status": "ok"}


def report_dates_for(conn: sqlite3.Connection, code: str) -> list[str]:
    dates: set[str] = set()
    for table in ("基金季度资产配置", "基金季度股票持仓", "基金季度债券持仓", "基金季度行业配置", "基金分类快照"):
        rows = many(
            conn,
            f"""
            SELECT "报告期" AS report_date
            FROM "{table}"
            WHERE "基金代码" = ?
            GROUP BY "报告期"
            ORDER BY "报告期" DESC
            LIMIT 4
            """,
            (code,),
        )
        if rows:
            dates.add(str(rows[0]["report_date"]))
        for row in rows:
            d = str(row["report_date"] or "")
            if d.endswith("-12-31"):
                dates.add(d)
                break
    return sorted(dates, reverse=True)


def build_asset_reports(conn: sqlite3.Connection, code: str) -> list[dict[str, Any]]:
    rows = many(
        conn,
        """
        SELECT "报告期","披露日期","股票占比_百分比","债券占比_百分比","现金占比_百分比",
               "基金占比_百分比","商品占比_百分比","存托凭证占比_百分比","其他占比_百分比",
               "净资产_亿元","数据来源","采集时间"
        FROM "基金季度资产配置"
        WHERE "基金代码" = ?
        ORDER BY "报告期" DESC
        LIMIT 8
        """,
        (code,),
    )
    return [normalize_row(row) for row in rows]


def build_stock_reports(conn: sqlite3.Connection, code: str, report_dates: list[str], limit: int) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    for report_date in report_dates:
        rows = many(
            conn,
            """
            SELECT "股票代码","股票名称","市场代码","占基金净值比例_百分比","持股数_万股",
                   "持仓市值_万元","行业一级","行业二级","行业来源","数据来源"
            FROM "基金季度股票持仓"
            WHERE "基金代码" = ? AND "报告期" = ?
            ORDER BY COALESCE("占基金净值比例_百分比", 0) DESC, "股票代码"
            LIMIT ?
            """,
            (code, report_date, limit),
        )
        if rows:
            reports.append(
                {
                    "reportDate": report_date,
                    "rows": [normalize_row(row) for row in rows],
                    "totalWeight": round_float(sum(float(row.get("占基金净值比例_百分比") or 0) for row in rows), 6),
                    "rowLimit": limit,
                }
            )
    return reports


def build_bond_reports(conn: sqlite3.Connection, code: str, report_dates: list[str], limit: int) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    for report_date in report_dates:
        rows = many(
            conn,
            """
            SELECT "债券代码","债券名称","占基金净值比例_百分比","持债数量","持仓市值_万元","债券类型","数据来源"
            FROM "基金季度债券持仓"
            WHERE "基金代码" = ? AND "报告期" = ?
            ORDER BY COALESCE("占基金净值比例_百分比", 0) DESC, "债券代码"
            LIMIT ?
            """,
            (code, report_date, limit),
        )
        if rows:
            reports.append(
                {
                    "reportDate": report_date,
                    "rows": [normalize_row(row) for row in rows],
                    "totalWeight": round_float(sum(float(row.get("占基金净值比例_百分比") or 0) for row in rows), 6),
                    "rowLimit": limit,
                }
            )
    return reports


def build_industry_reports(conn: sqlite3.Connection, code: str, report_dates: list[str]) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    for report_date in report_dates:
        rows = many(
            conn,
            """
            SELECT "行业一级","占基金净值比例_百分比","股票持仓样本数","数据来源"
            FROM "基金季度行业配置"
            WHERE "基金代码" = ? AND "报告期" = ?
            ORDER BY COALESCE("占基金净值比例_百分比", 0) DESC, "行业一级"
            """,
            (code, report_date),
        )
        rows = [
            row
            for row in rows
            if str(row.get("行业一级") or "").strip() not in {"", "未识别"}
        ]
        if rows:
            reports.append(
                {
                    "reportDate": report_date,
                    "rows": [normalize_row(row) for row in rows],
                    "totalWeight": round_float(sum(float(row.get("占基金净值比例_百分比") or 0) for row in rows), 6),
                }
            )
    return reports


def build_holding_coverage(
    asset_reports: list[dict[str, Any]],
    stock_reports: list[dict[str, Any]],
    bond_reports: list[dict[str, Any]],
    industry_reports: list[dict[str, Any]],
) -> dict[str, Any]:
    latest_asset = asset_reports[0] if asset_reports else {}
    stock_count = sum(len(report.get("rows") or []) for report in stock_reports)
    bond_count = sum(len(report.get("rows") or []) for report in bond_reports)
    industry_count = sum(len(report.get("rows") or []) for report in industry_reports)
    status_parts = []
    if asset_reports:
        status_parts.append("已取得季报资产配置")
    else:
        status_parts.append("未取得季报资产配置")
    if stock_count:
        status_parts.append("已取得股票持仓明细")
    else:
        status_parts.append("股票持仓接口为空或未披露")
    if bond_count:
        status_parts.append("已取得债券持仓明细")
    else:
        status_parts.append("债券持仓接口为空或未披露")
    if industry_count:
        status_parts.append("已由股票持仓推导有效行业配置")
    elif stock_count:
        status_parts.append("股票重仓股已取得；行业映射覆盖不足，前台不展示行业配置")
    else:
        status_parts.append("无股票持仓样本，无法推导行业配置")
    other_weight = round_float(latest_asset.get("其他占比_百分比"), 4)
    fund_weight = round_float(latest_asset.get("基金占比_百分比"), 4)
    note = "；".join(status_parts)
    if (other_weight and other_weight >= 50) or (fund_weight and fund_weight >= 20):
        note += "；资产配置中基金/其他占比较高，可能是ETF联接、FOF、QDII或底层基金投资，当前公开接口未拆出完整底层证券明细"
    return {
        "资产配置报告期": latest_asset.get("报告期"),
        "股票明细报告数": len(stock_reports),
        "股票明细行数": stock_count,
        "债券明细报告数": len(bond_reports),
        "债券明细行数": bond_count,
        "行业配置报告数": len(industry_reports),
        "行业配置行数": industry_count,
        "最新其他占比_百分比": other_weight,
        "最新基金占比_百分比": fund_weight,
        "覆盖说明": note,
    }


def build_classification_snapshots(conn: sqlite3.Connection, code: str) -> list[dict[str, Any]]:
    rows = many(
        conn,
        """
        SELECT "报告期","披露日期","基金名称","基金公司","基金类型","二级分类",
               "资产暴露JSON","行业暴露JSON","主题标签JSON","分类来源","是否估算","覆盖状态","生成时间"
        FROM "基金分类快照"
        WHERE "基金代码" = ?
        ORDER BY "报告期" DESC
        LIMIT 4
        """,
        (code,),
    )
    snapshots = []
    for row in rows:
        item = normalize_row(row)
        item["资产暴露"] = normalize_json_text(item.pop("资产暴露JSON", None))
        item["行业暴露"] = normalize_json_text(item.pop("行业暴露JSON", None))
        item["主题标签"] = normalize_json_text(item.pop("主题标签JSON", None))
        snapshots.append(item)
    return snapshots


def build_fund_profile(conn: sqlite3.Connection, code: str) -> dict[str, Any]:
    info = one(conn, 'SELECT * FROM "基金信息" WHERE "基金代码" = ?', (code,)) or {}
    nav_summary = one(conn, 'SELECT * FROM "基金净值概况" WHERE "基金代码" = ?', (code,)) or {}
    dictionary = one(conn, 'SELECT * FROM "基金标准分类字典" WHERE "基金代码" = ?', (code,)) or {}
    public_snapshot = optional_one(conn, 'SELECT * FROM "公募基金产品绩效快照" WHERE "基金代码" = ?', (code,))
    fund_f10 = optional_one(conn, 'SELECT * FROM "基金F10基准" WHERE "基金代码" = ?', (code,))
    fof_snapshot = optional_one(conn, 'SELECT * FROM "FOF产品绩效快照" WHERE "基金代码" = ?', (code,))
    fof_f10 = optional_one(conn, 'SELECT * FROM "FOF基金F10基准" WHERE "基金代码" = ?', (code,))
    add_broad_equity_fields(public_snapshot)
    add_broad_equity_fields(fof_snapshot)
    if dictionary.get("主题标签JSON"):
        dictionary["主题标签"] = normalize_json_text(dictionary.pop("主题标签JSON"))
    if info.get("主题标签JSON"):
        info["主题标签"] = normalize_json_text(info.pop("主题标签JSON"))
    return {
        "info": normalize_row(info),
        "navSummary": normalize_row(nav_summary),
        "dictionary": normalize_row(dictionary),
        "publicSnapshot": normalize_row(public_snapshot),
        "fundF10": normalize_row(fund_f10),
        "fofSnapshot": normalize_row(fof_snapshot),
        "fofF10": normalize_row(fof_f10),
    }


def build_payload(conn: sqlite3.Connection, fund: dict[str, str], args: argparse.Namespace, generated_at: str) -> dict[str, Any]:
    code = fund["code"]
    report_dates = report_dates_for(conn, code)
    profile = build_fund_profile(conn, code)
    snapshots = build_classification_snapshots(conn, code)
    nav = build_nav(conn, code, args.nav_lookback_days, args.max_nav_points)
    asset_reports = build_asset_reports(conn, code)
    stock_reports = build_stock_reports(conn, code, report_dates, args.top_stock_holdings)
    bond_reports = build_bond_reports(conn, code, report_dates, args.top_bond_holdings)
    industry_reports = build_industry_reports(conn, code, report_dates)
    benchmark_choice = choose_benchmark(conn, fund, profile, snapshots)
    return {
        "version": 1,
        "generatedAt": generated_at,
        "fund": fund,
        "profile": profile,
        "classificationSnapshots": snapshots,
        "nav": nav,
        "benchmark": build_benchmark(conn, benchmark_choice, nav, args.max_nav_points),
        "assetReports": asset_reports,
        "stockReports": stock_reports,
        "bondReports": bond_reports,
        "industryReports": industry_reports,
        "holdingCoverage": build_holding_coverage(asset_reports, stock_reports, bond_reports, industry_reports),
        "reportDates": report_dates,
        "sourceNote": "净值来自基金日度净值；资产、股票、债券和行业持仓来自东财F10季报/年报穿透数据；分类实体来自本地基金分类快照和语义索引。",
    }


def main() -> None:
    args = parse_args()
    site_dir = args.site_dir.resolve()
    limited_run = args.limit > 0
    out_dir = site_dir / "data" / "fund_details"
    out_dir.mkdir(parents=True, exist_ok=True)
    expected_files: set[str] = set()
    generated_at = now_cn_iso()
    stats = {
        "fundCount": 0,
        "navFundCount": 0,
        "assetReportFundCount": 0,
        "stockReportFundCount": 0,
        "bondReportFundCount": 0,
        "industryReportFundCount": 0,
        "benchmarkFundCount": 0,
    }
    with connect_db(args.db_path.resolve()) as conn:
        site_funds = load_fund_list(site_dir) if args.fund_universe == "site-plus-fof" else []
        fof_funds = load_fof_universe_fund_list(conn)
        strategy_referenced_funds = load_strategy_referenced_fund_list(conn)
        dictionary_funds = []
        if args.fund_universe == "all-dict":
            dictionary_funds = load_dictionary_fund_list(conn, current_only=False)
            funds = merge_fund_lists(dictionary_funds, fof_funds, strategy_referenced_funds)
        elif args.fund_universe == "current-dict":
            dictionary_funds = load_dictionary_fund_list(conn, current_only=True)
            funds = merge_fund_lists(dictionary_funds, fof_funds, strategy_referenced_funds)
        else:
            funds = merge_fund_lists(site_funds, fof_funds, strategy_referenced_funds)
        if args.limit > 0:
            funds = funds[: args.limit]
        stats["siteFundCount"] = len(site_funds)
        stats["dictionaryFundCount"] = len(dictionary_funds)
        stats["fofUniverseFundCount"] = len(fof_funds)
        stats["strategyReferencedFundCount"] = len(strategy_referenced_funds)
        for index, fund in enumerate(funds, start=1):
            payload = build_payload(conn, fund, args, generated_at)
            filename = f"{safe_filename(fund['code'])}.js"
            expected_files.add(filename)
            js_assignment(out_dir / filename, f'window.__BASIC_DATA__.fundEnrichmentDetails["{fund["code"]}"]', payload)
            stats["fundCount"] += 1
            if payload.get("nav", {}).get("rows"):
                stats["navFundCount"] += 1
            if payload.get("assetReports"):
                stats["assetReportFundCount"] += 1
            if payload.get("stockReports"):
                stats["stockReportFundCount"] += 1
            if payload.get("bondReports"):
                stats["bondReportFundCount"] += 1
            if payload.get("industryReports"):
                stats["industryReportFundCount"] += 1
            if payload.get("benchmark", {}).get("rows"):
                stats["benchmarkFundCount"] += 1
            if index % 250 == 0 or index == len(funds):
                print(f"[fund-enrichment] {index}/{len(funds)} files")
    if not limited_run:
        for path in out_dir.glob("*.js"):
            if path.name.startswith("_"):
                continue
            if path.name not in expected_files:
                path.unlink()
    manifest = {
        "version": 1,
        "generatedAt": generated_at,
        "siteDir": str(site_dir),
        "dbPath": str(args.db_path.resolve()),
        "fundUniverse": args.fund_universe,
        "navLookbackDays": args.nav_lookback_days,
        "maxNavPoints": args.max_nav_points,
        **stats,
    }
    if not limited_run:
        manifest_text = (
            "window.__BASIC_DATA__ = window.__BASIC_DATA__ || {}; "
            f"window.__BASIC_DATA__.fundEnrichmentManifest = {json.dumps(manifest, ensure_ascii=False, separators=(',', ':'))};\n"
        )
        write_text_if_changed(out_dir / "_manifest.js", manifest_text)
    else:
        manifest["debugLimitedRun"] = True
        manifest["note"] = "limit 模式只重建目标样本文件，不清理正式目录，也不改写 _manifest.js。"
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
