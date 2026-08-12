from __future__ import annotations

import argparse
import gzip
import json
import math
import re
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from backfill_fund_history_analysis_sqlite import epoch_millis_to_ymd, parse_js_array, to_float


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_DB = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
DEFAULT_INPUTS = [
    PROJECT_ROOT / "outputs" / "public_fund_xls_metrics",
    PROJECT_ROOT / "outputs" / "public_fund_xls_metrics_smoke",
]
TABLE = "基金日度净值"
VALID_SOURCES = {"新浪财经_xh5Fund_复权净值", "天天基金_pingzhongdata_复权指数"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Load the public-fund adjusted NAV window from archived collector responses.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--input-root", type=Path, action="append", default=[])
    parser.add_argument("--start-date", default="2025-05-01")
    parser.add_argument("--end-date", default="2026-06-30")
    parser.add_argument("--commit-rows", type=int, default=100_000)
    parser.add_argument("--progress-files", type=int, default=20)
    return parser.parse_args()


def positive(value: Any) -> float | None:
    number = to_float(value)
    return number if number is not None and number > 0 and math.isfinite(number) else None


def ensure_columns(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute(f'PRAGMA table_info("{TABLE}")')}
    for column, column_type in [("复权净值", "REAL"), ("复权来源", "TEXT")]:
        if column not in columns:
            conn.execute(f'ALTER TABLE "{TABLE}" ADD COLUMN "{column}" {column_type}')
    conn.commit()


def load_fund_meta(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        '''
        SELECT "基金代码", "标准基金名称", "天天基金细分类", "基金公司", "是否货币基金"
        FROM "基金标准分类字典"
        '''
    )
    return {
        str(row[0]): {
            "name": row[1],
            "type": row[2],
            "company": row[3],
            "is_money": int(row[4] or 0),
        }
        for row in rows
    }


def raw_files(roots: list[Path]) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        if root.exists():
            files.extend(root.glob("**/raw_batch_*.jsonl.gz"))
    return sorted(set(path.resolve() for path in files), key=lambda path: str(path))


def iter_records(path: Path) -> Iterable[dict[str, Any]]:
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    yield json.loads(line)
    except EOFError:
        return


def parse_xh5(
    text: str,
    *,
    start_date: str,
    end_date: str,
) -> list[dict[str, Any]]:
    match = re.search(r"xh5Fund\((\{.*?\})\)", text, flags=re.S)
    if not match:
        return []
    payload = json.loads(match.group(1))
    events: dict[str, str] = {}
    days = str(payload.get("fhday") or "").split(",")
    values = str(payload.get("fhvalue") or "").split(",")
    splits = str(payload.get("fhchaifen") or "").split(",")
    for index, day in enumerate(days):
        if not re.fullmatch(r"\d{8}", day or ""):
            continue
        ymd = f"{day[:4]}-{day[4:6]}-{day[6:8]}"
        parts = []
        if index < len(values) and values[index]:
            parts.append(f"分红={values[index]}")
        if index < len(splits) and splits[index] not in {"", "0"}:
            parts.append(f"拆分={splits[index]}")
        if parts:
            events[ymd] = "；".join(parts)
    rows: list[dict[str, Any]] = []
    for item in str(payload.get("data") or "").split("#"):
        parts = item.split(",")
        if len(parts) < 4 or not re.fullmatch(r"\d{8}", parts[0]):
            continue
        trade_date = f"{parts[0][:4]}-{parts[0][4:6]}-{parts[0][6:8]}"
        adjusted = positive(parts[3])
        if not (start_date <= trade_date <= end_date) or adjusted is None:
            continue
        rows.append(
            {
                "date": trade_date,
                "unit": positive(parts[1]),
                "accum": positive(parts[2]),
                "adjusted": adjusted,
                "daily": None,
                "per10k": None,
                "seven_day": None,
                "event": events.get(trade_date, ""),
                "method": "新浪_xh5_直接复权净值",
            }
        )
    rows.sort(key=lambda row: row["date"])
    previous: float | None = None
    for row in rows:
        if previous:
            row["daily"] = (row["adjusted"] / previous - 1.0) * 100.0
        previous = row["adjusted"]
    return rows


def parse_pingzhong(
    text: str,
    *,
    is_money: bool,
    start_date: str,
    end_date: str,
) -> list[dict[str, Any]]:
    if is_money:
        incomes = parse_js_array(text, "Data_millionCopiesIncome") or []
        seven_days = parse_js_array(text, "Data_sevenDaysYearIncome") or []
        seven_map = {
            epoch_millis_to_ymd(item[0]): to_float(item[1])
            for item in seven_days
            if isinstance(item, list) and len(item) >= 2
        }
        adjusted = 100.0
        rows: list[dict[str, Any]] = []
        for item in sorted(incomes, key=lambda value: value[0] if isinstance(value, list) and value else 0):
            if not isinstance(item, list) or len(item) < 2:
                continue
            trade_date = epoch_millis_to_ymd(item[0])
            per10k = to_float(item[1])
            if not trade_date or per10k is None or trade_date > end_date:
                continue
            adjusted *= 1.0 + per10k / 10_000.0
            if trade_date < start_date:
                continue
            rows.append(
                {
                    "date": trade_date,
                    "unit": None,
                    "accum": None,
                    "adjusted": adjusted,
                    "daily": per10k / 100.0,
                    "per10k": per10k,
                    "seven_day": seven_map.get(trade_date),
                    "event": "",
                    "method": "万份收益复利指数",
                }
            )
        return rows

    nav_series = parse_js_array(text, "Data_netWorthTrend") or []
    accumulated_series = parse_js_array(text, "Data_ACWorthTrend") or []
    accumulated_map = {
        epoch_millis_to_ymd(item[0]): positive(item[1])
        for item in accumulated_series
        if isinstance(item, list) and len(item) >= 2
    }
    records: list[tuple[str, float, float | None, float | None, str]] = []
    for item in nav_series:
        if not isinstance(item, dict):
            continue
        trade_date = epoch_millis_to_ymd(item.get("x"))
        unit = positive(item.get("y"))
        if not trade_date or trade_date > end_date or unit is None:
            continue
        records.append(
            (
                trade_date,
                unit,
                accumulated_map.get(trade_date),
                to_float(item.get("equityReturn")),
                str(item.get("unitMoney") or ""),
            )
        )
    records.sort(key=lambda item: item[0])
    adjusted = 100.0
    rows = []
    for index, (trade_date, unit, accum, daily, event) in enumerate(records):
        if index and daily is not None and math.isfinite(daily) and 1.0 + daily / 100.0 > 0:
            adjusted *= 1.0 + daily / 100.0
        if trade_date < start_date:
            continue
        rows.append(
            {
                "date": trade_date,
                "unit": unit,
                "accum": accum,
                "adjusted": adjusted,
                "daily": daily,
                "per10k": None,
                "seven_day": None,
                "event": event,
                "method": "天天_日增长率复利指数",
            }
        )
    return rows


def parse_record(
    record: dict[str, Any],
    *,
    fund_meta: dict[str, dict[str, Any]],
    start_date: str,
    end_date: str,
) -> tuple[str, list[dict[str, Any]], str] | None:
    source = str(record.get("source") or "")
    if source not in VALID_SOURCES:
        return None
    code = str(record.get("fundCode") or "").strip()
    documents = record.get("documents") or []
    if not code or not documents:
        return None
    text = str(documents[-1].get("text") or "")
    if source.startswith("新浪"):
        rows = parse_xh5(text, start_date=start_date, end_date=end_date)
    else:
        rows = parse_pingzhong(
            text,
            is_money=bool(fund_meta.get(code, {}).get("is_money")),
            start_date=start_date,
            end_date=end_date,
        )
    return code, rows, str(record.get("capturedAt") or "")


def upsert_rows(
    conn: sqlite3.Connection,
    *,
    code: str,
    rows: list[dict[str, Any]],
    meta: dict[str, Any],
    captured_at: str,
) -> None:
    sql = f'''
        INSERT INTO "{TABLE}" (
          "基金代码", "交易日期", "基金名称", "基金类型", "基金公司", "净值口径",
          "单位净值", "累计净值", "日收益率_百分比", "每万份收益", "七日年化收益率_百分比", "净值图分红送配",
          "是否货币基金", "数据来源", "原始净值快照ID", "采集时间", "复权净值", "复权来源"
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT("基金代码", "交易日期") DO UPDATE SET
          "基金名称"=excluded."基金名称", "基金类型"=excluded."基金类型", "基金公司"=excluded."基金公司",
          "净值口径"=excluded."净值口径", "单位净值"=COALESCE(excluded."单位净值", "{TABLE}"."单位净值"),
          "累计净值"=COALESCE(excluded."累计净值", "{TABLE}"."累计净值"), "日收益率_百分比"=COALESCE(excluded."日收益率_百分比", "{TABLE}"."日收益率_百分比"),
          "每万份收益"=COALESCE(excluded."每万份收益", "{TABLE}"."每万份收益"), "七日年化收益率_百分比"=COALESCE(excluded."七日年化收益率_百分比", "{TABLE}"."七日年化收益率_百分比"),
          "净值图分红送配"=COALESCE(NULLIF(excluded."净值图分红送配", ''), "{TABLE}"."净值图分红送配"), "是否货币基金"=excluded."是否货币基金",
          "数据来源"=excluded."数据来源", "原始净值快照ID"=excluded."原始净值快照ID", "采集时间"=excluded."采集时间",
          "复权净值"=excluded."复权净值", "复权来源"=excluded."复权来源"
    '''
    values = [
        (
            code,
            row["date"],
            meta.get("name"),
            meta.get("type"),
            meta.get("company"),
            "复权总回报",
            row.get("unit"),
            row.get("accum"),
            row.get("daily"),
            row.get("per10k"),
            row.get("seven_day"),
            row.get("event"),
            int(meta.get("is_money") or 0),
            row.get("method"),
            f"public_fund_xls_raw::{code}::{captured_at}",
            captured_at,
            row.get("adjusted"),
            row.get("method"),
        )
        for row in rows
    ]
    conn.executemany(sql, values)


def refresh_nav_overview(conn: sqlite3.Connection, *, start_date: str, end_date: str) -> int:
    rows = conn.execute(
        f'''
        WITH ranked AS (
          SELECT n.*,
                 ROW_NUMBER() OVER (PARTITION BY n."基金代码" ORDER BY n."交易日期" DESC) AS rn,
                 COUNT(*) OVER (PARTITION BY n."基金代码") AS row_total,
                 MIN(n."交易日期") OVER (PARTITION BY n."基金代码") AS first_date,
                 MAX(n."交易日期") OVER (PARTITION BY n."基金代码") AS last_date,
                 SUM(CASE WHEN COALESCE(n."净值图分红送配", '') <> '' THEN 1 ELSE 0 END)
                   OVER (PARTITION BY n."基金代码") AS dividend_total
          FROM "{TABLE}" n
          WHERE n."复权净值" IS NOT NULL
            AND n."交易日期" BETWEEN ? AND ?
        )
        SELECT * FROM ranked WHERE rn = 1
        ''',
        (start_date, end_date),
    ).fetchall()
    sql = '''
        INSERT INTO "基金净值概况" (
          "基金代码", "基金名称", "基金类型", "基金公司", "净值口径", "是否货币基金",
          "历史起始日期", "历史结束日期", "历史记录数", "分红事件数", "最新单位净值", "最新累计净值",
          "最新日收益率_百分比", "最新每万份收益", "最新七日年化收益率_百分比", "数据来源",
          "原始净值快照ID", "原始分红快照ID", "最近采集时间"
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT("基金代码") DO UPDATE SET
          "基金名称"=excluded."基金名称", "基金类型"=excluded."基金类型", "基金公司"=excluded."基金公司",
          "净值口径"=excluded."净值口径", "是否货币基金"=excluded."是否货币基金", "历史起始日期"=excluded."历史起始日期",
          "历史结束日期"=excluded."历史结束日期", "历史记录数"=excluded."历史记录数", "分红事件数"=excluded."分红事件数",
          "最新单位净值"=excluded."最新单位净值", "最新累计净值"=excluded."最新累计净值", "最新日收益率_百分比"=excluded."最新日收益率_百分比",
          "最新每万份收益"=excluded."最新每万份收益", "最新七日年化收益率_百分比"=excluded."最新七日年化收益率_百分比",
          "数据来源"=excluded."数据来源", "原始净值快照ID"=excluded."原始净值快照ID", "最近采集时间"=excluded."最近采集时间"
    '''
    conn.executemany(
        sql,
        [
            (
                row["基金代码"], row["基金名称"], row["基金类型"], row["基金公司"], "复权总回报",
                int(row["是否货币基金"] or 0), row["first_date"], row["last_date"], int(row["row_total"] or 0), int(row["dividend_total"] or 0),
                row["单位净值"], row["累计净值"], row["日收益率_百分比"], row["每万份收益"], row["七日年化收益率_百分比"],
                row["数据来源"], row["原始净值快照ID"], None, row["采集时间"],
            )
            for row in rows
        ],
    )
    conn.commit()
    return len(rows)


def main() -> int:
    args = parse_args()
    roots = args.input_root or DEFAULT_INPUTS
    files = raw_files(roots)
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA journal_mode=WAL")
    ensure_columns(conn)
    fund_meta = load_fund_meta(conn)
    processed_codes: set[str] = set()
    skipped_corrupt = 0
    pending_rows = 0
    total_rows = 0
    for file_index, path in enumerate(files, start=1):
        try:
            for record in iter_records(path):
                parsed = parse_record(record, fund_meta=fund_meta, start_date=args.start_date, end_date=args.end_date)
                if not parsed:
                    continue
                code, rows, captured_at = parsed
                if rows:
                    upsert_rows(conn, code=code, rows=rows, meta=fund_meta.get(code, {}), captured_at=captured_at)
                    processed_codes.add(code)
                    pending_rows += len(rows)
                    total_rows += len(rows)
                    if pending_rows >= max(1, args.commit_rows):
                        conn.commit()
                        print(
                            f"[load] files={file_index}/{len(files)} funds={len(processed_codes)} rows={total_rows}",
                            flush=True,
                        )
                        pending_rows = 0
        except (json.JSONDecodeError, OSError, ValueError):
            skipped_corrupt += 1
        if file_index % max(1, args.progress_files) == 0 or file_index == len(files):
            print(
                f"[scan {file_index}/{len(files)}] funds={len(processed_codes)} rows={total_rows} skipped_corrupt={skipped_corrupt}",
                flush=True,
            )
    conn.commit()
    overview_count = refresh_nav_overview(conn, start_date=args.start_date, end_date=args.end_date)
    summary = {
        "inputFiles": len(files),
        "funds": len(processed_codes),
        "rows": total_rows,
        "overviewFunds": overview_count,
        "startDate": args.start_date,
        "endDate": args.end_date,
        "skippedCorruptFiles": skipped_corrupt,
    }
    output = PROJECT_ROOT / "outputs" / "public_fund_adjusted_nav_load_summary.json"
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    conn.close()
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
