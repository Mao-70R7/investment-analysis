from __future__ import annotations

import argparse
import json
import re
import sqlite3
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
DEFAULT_CODES = ("968202",)
SOURCE_NAME = "新浪财经_xh5Fund_nav"
SOURCE_URL_TEMPLATE = "https://finance.sina.com.cn/fund/api/xh5Fund/nav/{fund_code}.js"
OPENAPI_SOURCE_NAME = "新浪财经_CaihuiFundInfoService.getNav"
COMBINED_SOURCE_NAME = "新浪财经_xh5Fund全历史+OpenAPI增量"
OPENAPI_URL = "https://stock.finance.sina.com.cn/fundInfo/api/openapi.php/CaihuiFundInfoService.getNav"


@dataclass(frozen=True)
class FundNavRow:
    fund_code: str
    trade_date: str
    fund_name: str | None
    fund_type: str
    fund_company: str
    unit_nav: float | None
    accumulated_nav: float | None
    daily_return_pct: float | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="补齐新浪财经 xh5Fund 历史净值到当前分析库。")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--fund-code", action="append", default=[], help="基金代码，可重复。默认补 968202。")
    parser.add_argument("--fund-name", default=None)
    parser.add_argument("--fund-type", default=None)
    parser.add_argument("--fund-company", default=None)
    parser.add_argument("--end-date", default=date.today().isoformat())
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--raw-output-dir", type=Path, default=PROJECT_ROOT / "data" / "raw" / "sina_xh5_fund_nav")
    parser.add_argument(
        "--normalized-output-dir",
        type=Path,
        default=PROJECT_ROOT / "data" / "normalized" / "sina_xh5_fund_nav",
    )
    return parser.parse_args()


def now_shanghai() -> datetime:
    return datetime.now(ZoneInfo("Asia/Shanghai"))


def as_float(value: str | None) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text == "--":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def normalize_date(value: str) -> str:
    text = str(value).strip()
    if re.fullmatch(r"\d{8}", text):
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return text.replace("/", "-").replace(".", "-")


def is_valid_trade_date(value: str) -> bool:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return False
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def fetch_sina_payload(fund_code: str) -> tuple[str, dict[str, Any]]:
    url = SOURCE_URL_TEMPLATE.format(fund_code=fund_code)
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": f"https://finance.sina.com.cn/fund/quotes/{fund_code}/bc.shtml",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        text = response.read().decode("utf-8", errors="replace")
    match = re.search(r"xh5Fund\((\{.*?\})\)", text, flags=re.S)
    if not match:
        raise ValueError(f"无法解析新浪 xh5Fund 响应: {fund_code}")
    payload = json.loads(match.group(1))
    return text, payload


def fetch_sina_openapi_payload(fund_code: str, start_date: str, end_date: str) -> tuple[str, dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    raw_pages: list[str] = []
    page = 1
    total_pages = 1
    while page <= total_pages:
        query = urllib.parse.urlencode(
            {
                "symbol": fund_code,
                "datefrom": start_date,
                "dateto": end_date,
                "page": page,
            }
        )
        request = urllib.request.Request(
            f"{OPENAPI_URL}?{query}",
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": f"https://stock.finance.sina.com.cn/fundInfo/view/FundInfo_LSJZ.php?symbol={fund_code}",
            },
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            raw_text = response.read().decode("utf-8", errors="replace")
        raw_pages.append(raw_text)
        payload = json.loads(raw_text)
        status_code = ((payload.get("result") or {}).get("status") or {}).get("code")
        data_payload = ((payload.get("result") or {}).get("data") or {})
        if status_code != 0:
            raise ValueError(f"新浪净值开放接口返回错误: {fund_code}, status={status_code}")
        pages.extend(data_payload.get("data") or [])
        total_num = int(data_payload.get("total_num") or 0)
        total_pages = max(1, (total_num + 19) // 20)
        page += 1
    return "\n".join(raw_pages), {"symbol": fund_code, "data": pages}


def parse_nav_rows(
    fund_code: str,
    payload: dict[str, Any],
    fund_name: str | None,
    fund_type: str,
    fund_company: str,
    end_date: str,
) -> list[FundNavRow]:
    rows: list[FundNavRow] = []
    for item in str(payload.get("data") or "").split("#"):
        parts = item.split(",")
        if len(parts) < 2 or not parts[0]:
            continue
        trade_date = normalize_date(parts[0])
        if not is_valid_trade_date(trade_date) or trade_date > end_date:
            continue
        unit_nav = as_float(parts[1])
        accumulated_nav = as_float(parts[2] if len(parts) > 2 else None)
        if (unit_nav is None or unit_nav <= 0) and (accumulated_nav is None or accumulated_nav <= 0):
            continue
        rows.append(
            FundNavRow(
                fund_code=fund_code,
                trade_date=trade_date,
                fund_name=fund_name,
                fund_type=fund_type,
                fund_company=fund_company,
                unit_nav=unit_nav,
                accumulated_nav=accumulated_nav,
                daily_return_pct=None,
            )
        )
    rows.sort(key=lambda row: row.trade_date)
    enriched: list[FundNavRow] = []
    previous_nav: float | None = None
    for row in rows:
        if row.unit_nav is not None and previous_nav not in (None, 0):
            daily_return_pct = (row.unit_nav / previous_nav - 1) * 100
        else:
            daily_return_pct = None
        enriched.append(
            FundNavRow(
                fund_code=row.fund_code,
                trade_date=row.trade_date,
                fund_name=row.fund_name,
                fund_type=row.fund_type,
                fund_company=row.fund_company,
                unit_nav=row.unit_nav,
                accumulated_nav=row.accumulated_nav,
                daily_return_pct=daily_return_pct,
            )
        )
        if row.unit_nav is not None:
            previous_nav = row.unit_nav
    return enriched


def parse_openapi_nav_rows(
    fund_code: str,
    payload: dict[str, Any],
    fund_name: str | None,
    fund_type: str,
    fund_company: str,
    end_date: str,
) -> list[FundNavRow]:
    rows: list[FundNavRow] = []
    for item in payload.get("data") or []:
        trade_date = normalize_date(str(item.get("fbrq") or "").split(" ", 1)[0])
        if not is_valid_trade_date(trade_date) or trade_date > end_date:
            continue
        unit_nav = as_float(item.get("jjjz"))
        accumulated_nav = as_float(item.get("ljjz"))
        if (unit_nav is None or unit_nav <= 0) and (accumulated_nav is None or accumulated_nav <= 0):
            continue
        rows.append(
            FundNavRow(
                fund_code=fund_code,
                trade_date=trade_date,
                fund_name=fund_name,
                fund_type=fund_type,
                fund_company=fund_company,
                unit_nav=unit_nav,
                accumulated_nav=accumulated_nav,
                daily_return_pct=None,
            )
        )
    unique = {row.trade_date: row for row in rows}
    ordered = [unique[key] for key in sorted(unique)]
    enriched: list[FundNavRow] = []
    previous_nav: float | None = None
    for row in ordered:
        daily_return_pct = None
        if row.unit_nav is not None and previous_nav not in (None, 0):
            daily_return_pct = (row.unit_nav / previous_nav - 1) * 100
        enriched.append(
            FundNavRow(
                fund_code=row.fund_code,
                trade_date=row.trade_date,
                fund_name=row.fund_name,
                fund_type=row.fund_type,
                fund_company=row.fund_company,
                unit_nav=row.unit_nav,
                accumulated_nav=row.accumulated_nav,
                daily_return_pct=daily_return_pct,
            )
        )
        if row.unit_nav is not None:
            previous_nav = row.unit_nav
    return enriched


def fund_metadata_from_db(
    conn: sqlite3.Connection,
    fund_code: str,
    fallback_name: str | None,
    fallback_type: str | None,
    fallback_company: str | None,
) -> tuple[str | None, str, str]:
    row = conn.execute(
        'SELECT "基金名称", "基金类型", "基金公司" FROM "基金信息" WHERE "基金代码" = ?',
        (fund_code,),
    ).fetchone()
    fund_name = fallback_name or (row[0] if row and row[0] else None)
    fund_type = fallback_type or (row[1] if row and row[1] else None) or "未披露"
    fund_company = fallback_company or (row[2] if row and row[2] else None) or "未披露"
    return fund_name, fund_type, fund_company


def write_artifacts(
    args: argparse.Namespace,
    run_id: str,
    fund_code: str,
    raw_text: str,
    payload: dict[str, Any],
    rows: list[FundNavRow],
    source_name: str,
) -> tuple[Path, Path]:
    raw_dir = args.raw_output_dir / run_id
    normalized_dir = args.normalized_output_dir / "fund_nav_daily" / run_id
    raw_dir.mkdir(parents=True, exist_ok=True)
    normalized_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / f"{fund_code}.js"
    normalized_path = normalized_dir / f"{fund_code}.jsonl"
    raw_path.write_text(raw_text, encoding="utf-8")
    with normalized_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            record = asdict(row)
            record["source"] = source_name
            record["source_symbol"] = payload.get("symbol") or fund_code
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return raw_path, normalized_path


def upsert_rows(
    conn: sqlite3.Connection,
    rows: list[FundNavRow],
    source_snapshot_id: str,
    captured_at: str,
    source_name: str,
) -> int:
    if not rows:
        return 0
    latest = rows[-1]
    conn.execute(
        """
        INSERT INTO "基金信息" (
            "基金代码", "基金名称", "基金公司", "基金类型", "最新净值", "最新净值日期", "基金状态", "数据来源", "最近更新时间"
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT("基金代码") DO UPDATE SET
            "基金名称"=COALESCE(excluded."基金名称", "基金信息"."基金名称"),
            "基金公司"=COALESCE(excluded."基金公司", "基金信息"."基金公司"),
            "基金类型"=COALESCE(excluded."基金类型", "基金信息"."基金类型"),
            "最新净值"=excluded."最新净值",
            "最新净值日期"=excluded."最新净值日期",
            "基金状态"=COALESCE("基金信息"."基金状态", excluded."基金状态"),
            "数据来源"=excluded."数据来源",
            "最近更新时间"=excluded."最近更新时间"
        """,
        (
            latest.fund_code,
            latest.fund_name,
            latest.fund_company,
            latest.fund_type,
            latest.unit_nav,
            latest.trade_date,
            "正常",
            source_name,
            captured_at,
        ),
    )
    conn.executemany(
        """
        INSERT INTO "基金日度净值" (
            "基金代码", "交易日期", "基金名称", "基金类型", "基金公司", "净值口径",
            "单位净值", "累计净值", "日收益率_百分比", "每万份收益", "七日年化收益率_百分比",
            "净值图分红送配", "是否货币基金", "数据来源", "原始净值快照ID", "采集时间"
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT("基金代码", "交易日期") DO UPDATE SET
            "基金名称"=COALESCE(excluded."基金名称", "基金日度净值"."基金名称"),
            "基金类型"=COALESCE(excluded."基金类型", "基金日度净值"."基金类型"),
            "基金公司"=COALESCE(excluded."基金公司", "基金日度净值"."基金公司"),
            "净值口径"=excluded."净值口径",
            "单位净值"=excluded."单位净值",
            "累计净值"=excluded."累计净值",
            "日收益率_百分比"=excluded."日收益率_百分比",
            "数据来源"=excluded."数据来源",
            "原始净值快照ID"=excluded."原始净值快照ID",
            "采集时间"=excluded."采集时间"
        """,
        [
            (
                row.fund_code,
                row.trade_date,
                row.fund_name,
                row.fund_type,
                row.fund_company,
                "单位净值",
                row.unit_nav,
                row.accumulated_nav,
                row.daily_return_pct,
                None,
                None,
                None,
                0,
                source_name,
                source_snapshot_id,
                captured_at,
            )
            for row in rows
        ],
    )
    conn.execute(
        """
        INSERT INTO "基金净值概况" (
            "基金代码", "基金名称", "基金类型", "基金公司", "净值口径", "是否货币基金",
            "历史起始日期", "历史结束日期", "历史记录数", "分红事件数",
            "最新单位净值", "最新累计净值", "最新日收益率_百分比", "最新每万份收益", "最新七日年化收益率_百分比",
            "数据来源", "原始净值快照ID", "原始分红快照ID", "最近采集时间"
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
            "最新单位净值"=excluded."最新单位净值",
            "最新累计净值"=excluded."最新累计净值",
            "最新日收益率_百分比"=excluded."最新日收益率_百分比",
            "数据来源"=excluded."数据来源",
            "原始净值快照ID"=excluded."原始净值快照ID",
            "最近采集时间"=excluded."最近采集时间"
        """,
        (
            latest.fund_code,
            latest.fund_name,
            latest.fund_type,
            latest.fund_company,
            "单位净值",
            0,
            rows[0].trade_date,
            latest.trade_date,
            len(rows),
            0,
            latest.unit_nav,
            latest.accumulated_nav,
            latest.daily_return_pct,
            None,
            None,
            source_name,
            source_snapshot_id,
            None,
            captured_at,
        ),
    )
    return len(rows)


def main() -> None:
    args = parse_args()
    fund_codes = tuple(args.fund_code or DEFAULT_CODES)
    captured = now_shanghai()
    captured_at = captured.isoformat(timespec="seconds")
    run_id = captured.strftime("%Y%m%dT%H%M%S%z")
    summary: dict[str, Any] = {
        "run_id": run_id,
        "source": SOURCE_NAME,
        "dry_run": args.dry_run,
        "funds": {},
    }
    conn = sqlite3.connect(args.db_path)
    try:
        with conn:
            for fund_code in fund_codes:
                fund_name, fund_type, fund_company = fund_metadata_from_db(
                    conn,
                    fund_code,
                    args.fund_name,
                    args.fund_type,
                    args.fund_company,
                )
                raw_text = ""
                payload: dict[str, Any] = {"symbol": fund_code, "data": []}
                rows: list[FundNavRow] = []
                xh5_error: str | None = None
                try:
                    raw_text, payload = fetch_sina_payload(fund_code)
                    rows = parse_nav_rows(
                        fund_code,
                        payload,
                        fund_name,
                        fund_type,
                        fund_company,
                        args.end_date,
                    )
                except Exception as exc:  # noqa: BLE001
                    xh5_error = str(exc)
                xh5_valid = bool(rows)

                recent_start = ""
                if rows:
                    recent_start = (datetime.strptime(rows[-1].trade_date, "%Y-%m-%d") - timedelta(days=45)).date().isoformat()
                openapi_error: str | None = None
                openapi_valid = False
                try:
                    openapi_raw, openapi_payload = fetch_sina_openapi_payload(
                        fund_code,
                        recent_start,
                        args.end_date,
                    )
                    openapi_rows = parse_openapi_nav_rows(
                        fund_code,
                        openapi_payload,
                        fund_name,
                        fund_type,
                        fund_company,
                        args.end_date,
                    )
                    if openapi_rows:
                        openapi_valid = True
                        merged = {row.trade_date: row for row in rows}
                        merged.update({row.trade_date: row for row in openapi_rows})
                        rows = [merged[key] for key in sorted(merged)]
                        raw_text = f"/* xh5Fund */\n{raw_text}\n/* OpenAPI */\n{openapi_raw}"
                        payload = {"symbol": fund_code, "data": [asdict(row) for row in rows]}
                except Exception as exc:  # noqa: BLE001
                    openapi_error = str(exc)

                if not rows:
                    raise RuntimeError(
                        f"新浪两个净值接口均未返回有效数据: {fund_code}; "
                        f"xh5={xh5_error or 'empty'}; openapi={openapi_error or 'empty'}"
                    )
                if xh5_valid and openapi_valid:
                    source_name = COMBINED_SOURCE_NAME
                elif xh5_valid:
                    source_name = SOURCE_NAME
                else:
                    source_name = OPENAPI_SOURCE_NAME
                raw_path, normalized_path = write_artifacts(
                    args,
                    run_id,
                    fund_code,
                    raw_text,
                    payload,
                    rows,
                    source_name,
                )
                snapshot_id = f"sina_nav_{fund_code}_{run_id}"
                upserted = 0 if args.dry_run else upsert_rows(
                    conn,
                    rows,
                    snapshot_id,
                    captured_at,
                    source_name,
                )
                summary["funds"][fund_code] = {
                    "fund_name": fund_name,
                    "fund_type": fund_type,
                    "fund_company": fund_company,
                    "source": source_name,
                    "xh5_error": xh5_error,
                    "openapi_error": openapi_error,
                    "rows": len(rows),
                    "upserted_rows": upserted,
                    "first_date": rows[0].trade_date if rows else None,
                    "last_date": rows[-1].trade_date if rows else None,
                    "latest_nav": rows[-1].unit_nav if rows else None,
                    "raw_path": str(raw_path),
                    "normalized_path": str(normalized_path),
                }
    finally:
        conn.close()
    summary_dir = args.normalized_output_dir / "summary"
    summary_dir.mkdir(parents=True, exist_ok=True)
    summary_path = summary_dir / f"{run_id}.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    summary["summary_path"] = str(summary_path)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
