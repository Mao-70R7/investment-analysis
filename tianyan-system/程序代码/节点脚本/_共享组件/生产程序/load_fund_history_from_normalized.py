from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
DEFAULT_SCHEMA_PATH = PROJECT_ROOT / "schemas" / "analysis_zh_current.sql"
DEFAULT_NORMALIZED_ROOT = PROJECT_ROOT / "data" / "normalized" / "ttfund_fund_nav"

BATCH_SIZE = 100_000


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Load normalized fund NAV/dividend JSONL files into analysis_zh_current.sqlite."
    )
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--schema-path", type=Path, default=DEFAULT_SCHEMA_PATH)
    parser.add_argument("--normalized-root", type=Path, default=DEFAULT_NORMALIZED_ROOT)
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    parser.add_argument("--append", action="store_true", help="Append/upsert without clearing existing fund history tables.")
    parser.add_argument("--daily-file", action="append", type=Path, default=[], help="Append/upsert only the specified daily NAV JSONL file.")
    parser.add_argument("--dividend-file", action="append", type=Path, default=[], help="Append/upsert only the specified dividend JSONL file.")
    parser.add_argument("--meta-file", action="append", type=Path, default=[], help="Append/upsert only the specified fund NAV meta JSONL file.")
    return parser.parse_args()


def init_db(conn: sqlite3.Connection, schema_path: Path) -> None:
    conn.executescript(schema_path.read_text(encoding="utf-8"))


def to_int_flag(value: Any) -> int:
    return 1 if bool(value) else 0


def pick(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row and row[key] is not None:
            return row[key]
    return None


def normalize_date_text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    match = re.search(r"([12]\d{3})[-/.]?([01]\d)[-/.]?([0-3]\d)", text)
    if not match:
        return text or None
    return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"


def nav_basis(row: dict[str, Any]) -> str:
    explicit = pick(row, "净值口径")
    if explicit:
        return str(explicit)
    if pick(row, "is_money_market", "是否货币基金"):
        return "货币基金收益"
    return "单位净值"


def data_source(row: dict[str, Any]) -> str:
    explicit = pick(row, "数据来源")
    if explicit:
        return str(explicit)
    if pick(row, "is_money_market", "是否货币基金"):
        return "天天基金_pingzhongdata"
    if row.get("value_type") == "nav":
        return "天天基金_lsjz"
    return "天天基金"


def iter_jsonl(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"{path}:{line_no} JSON parse failed: {error}") from error


def reset_fund_history(conn: sqlite3.Connection) -> None:
    for table in ["基金日度净值", "基金分红送配", "基金净值概况"]:
        conn.execute(f'DELETE FROM "{table}"')


def insert_daily_batch(conn: sqlite3.Connection, batch: list[dict[str, Any]]) -> None:
    if not batch:
        return
    conn.executemany(
        """
        INSERT INTO "基金日度净值" (
            "基金代码", "交易日期", "基金名称", "基金类型", "基金公司", "净值口径",
            "单位净值", "累计净值", "日收益率_百分比", "每万份收益", "七日年化收益率_百分比",
            "净值图分红送配", "是否货币基金", "数据来源", "原始净值快照ID", "采集时间"
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
            (
                pick(row, "fund_code", "基金代码"),
                normalize_date_text(pick(row, "trade_date", "交易日期")),
                pick(row, "fund_name", "基金名称"),
                pick(row, "fund_type", "基金类型"),
                pick(row, "fund_company", "基金公司"),
                nav_basis(row),
                pick(row, "nav", "单位净值"),
                pick(row, "accumulated_nav", "累计净值"),
                pick(row, "daily_return", "日收益率_百分比"),
                pick(row, "per_10k_yield", "每万份收益"),
                pick(row, "seven_day_annualized", "七日年化收益率_百分比"),
                pick(row, "dividend_info", "净值图分红送配"),
                to_int_flag(pick(row, "is_money_market", "是否货币基金")),
                data_source(row),
                pick(row, "source_snapshot_id", "原始净值快照ID"),
                pick(row, "captured_at", "采集时间"),
            )
            for row in batch
            if pick(row, "fund_code", "基金代码") and normalize_date_text(pick(row, "trade_date", "交易日期"))
        ],
    )


def insert_dividend_batch(conn: sqlite3.Connection, batch: list[dict[str, Any]]) -> None:
    if not batch:
        return
    fund_rows = []
    seen_codes: set[str] = set()
    for row in batch:
        fund_code = row.get("基金代码")
        if not fund_code or fund_code in seen_codes:
            continue
        seen_codes.add(str(fund_code))
        fund_rows.append((fund_code, row.get("基金名称"), "天天基金_fhsp"))
    if fund_rows:
        conn.executemany(
            """
            INSERT INTO "基金信息" ("基金代码", "基金名称", "数据来源")
            VALUES (?, ?, ?)
            ON CONFLICT("基金代码") DO UPDATE SET
                "基金名称"=COALESCE(excluded."基金名称", "基金信息"."基金名称"),
                "数据来源"=COALESCE("基金信息"."数据来源", excluded."数据来源")
            """,
            fund_rows,
        )
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
            (
                row.get("基金代码"),
                normalize_date_text(row.get("权益登记日")),
                normalize_date_text(row.get("除息日")),
                row.get("基金名称"),
                row.get("年份"),
                row.get("每份分红"),
                normalize_date_text(row.get("分红发放日")),
                row.get("数据来源") or "天天基金_fhsp",
                row.get("原始分红快照ID"),
                row.get("采集时间"),
            )
            for row in batch
            if row.get("基金代码") and normalize_date_text(row.get("权益登记日")) and row.get("每份分红")
        ],
    )


def insert_meta_row(conn: sqlite3.Connection, row: dict[str, Any], dividend_count: int) -> None:
    is_money_market = bool(
        pick(row, "是否货币基金")
        or (pick(row, "latest_per_10k_yield", "最新每万份收益") is not None and pick(row, "latest_nav", "最新单位净值") is None)
    )
    source_snapshot_ids = pick(row, "source_snapshot_ids") or []
    source_snapshot_id = pick(row, "原始净值快照ID") or (source_snapshot_ids[0] if source_snapshot_ids else None)
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
        (
            pick(row, "fund_code", "基金代码"),
            pick(row, "fund_name", "基金名称"),
            pick(row, "fund_company", "基金公司"),
            pick(row, "fund_type", "基金类型"),
            pick(row, "latest_nav", "最新单位净值"),
            normalize_date_text(pick(row, "last_trade_date", "历史结束日期")),
            pick(row, "数据来源") or "天天基金_lsjz",
        ),
    )
    conn.execute(
        """
        INSERT INTO "基金净值概况" (
            "基金代码", "基金名称", "基金类型", "基金公司", "净值口径", "是否货币基金",
            "历史起始日期", "历史结束日期", "历史记录数", "分红事件数", "最新单位净值",
            "最新累计净值", "最新日收益率_百分比", "最新每万份收益", "最新七日年化收益率_百分比",
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
        (
            pick(row, "fund_code", "基金代码"),
            pick(row, "fund_name", "基金名称"),
            pick(row, "fund_type", "基金类型"),
            pick(row, "fund_company", "基金公司"),
            pick(row, "净值口径") or ("货币基金收益" if is_money_market else "单位净值"),
            to_int_flag(is_money_market),
            normalize_date_text(pick(row, "first_trade_date", "历史起始日期")),
            normalize_date_text(pick(row, "last_trade_date", "历史结束日期")),
            pick(row, "row_total", "records_total", "历史记录数") or 0,
            pick(row, "分红事件数") if pick(row, "分红事件数") is not None else dividend_count,
            pick(row, "latest_nav", "最新单位净值"),
            pick(row, "latest_accumulated_nav", "最新累计净值"),
            pick(row, "latest_daily_return", "最新日收益率_百分比"),
            pick(row, "latest_per_10k_yield", "最新每万份收益"),
            pick(row, "latest_seven_day_annualized", "最新七日年化收益率_百分比"),
            pick(row, "数据来源") or "天天基金_lsjz",
            source_snapshot_id,
            pick(row, "原始分红快照ID"),
            pick(row, "captured_at", "最近采集时间"),
        ),
    )
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
        (
            pick(row, "fund_code", "基金代码"),
            pick(row, "fund_name", "基金名称"),
            pick(row, "fund_company", "基金公司"),
            pick(row, "fund_type", "基金类型"),
            pick(row, "latest_nav", "最新单位净值"),
            pick(row, "last_trade_date", "历史结束日期"),
            pick(row, "数据来源") or "天天基金_lsjz",
        ),
    )


def load_daily(conn: sqlite3.Connection, root: Path, batch_size: int) -> int:
    daily_root = root / "fund_nav_history_daily"
    return load_daily_files(conn, sorted(daily_root.rglob("*.jsonl")), batch_size)


def load_daily_files(conn: sqlite3.Connection, paths: list[Path], batch_size: int) -> int:
    batch: list[dict[str, Any]] = []
    total = 0
    for path in sorted(paths):
        for row in iter_jsonl(path):
            batch.append(row)
            total += 1
            if len(batch) >= batch_size:
                insert_daily_batch(conn, batch)
                batch.clear()
    insert_daily_batch(conn, batch)
    return total


def count_dividends(root: Path) -> Counter[str]:
    dividend_root = root / "fund_dividend_event"
    counts: Counter[str] = Counter()
    for path in sorted(dividend_root.rglob("*.jsonl")):
        for row in iter_jsonl(path):
            if row.get("基金代码"):
                counts[str(row["基金代码"])] += 1
    return counts


def load_dividends(conn: sqlite3.Connection, root: Path, batch_size: int) -> int:
    dividend_root = root / "fund_dividend_event"
    return load_dividend_files(conn, sorted(dividend_root.rglob("*.jsonl")), batch_size)


def load_dividend_files(conn: sqlite3.Connection, paths: list[Path], batch_size: int) -> int:
    batch: list[dict[str, Any]] = []
    total = 0
    for path in sorted(paths):
        for row in iter_jsonl(path):
            batch.append(row)
            total += 1
            if len(batch) >= batch_size:
                insert_dividend_batch(conn, batch)
                batch.clear()
    insert_dividend_batch(conn, batch)
    return total


def load_meta(conn: sqlite3.Connection, root: Path, dividend_counts: Counter[str]) -> int:
    meta_root = root / "fund_nav_history_meta"
    return load_meta_files(conn, sorted(meta_root.rglob("*.jsonl")), dividend_counts)


def load_meta_files(conn: sqlite3.Connection, paths: list[Path], dividend_counts: Counter[str]) -> int:
    total = 0
    for path in sorted(paths):
        for row in iter_jsonl(path):
            fund_code = pick(row, "fund_code", "基金代码")
            if not fund_code:
                continue
            insert_meta_row(conn, row, dividend_counts[str(fund_code)])
            total += 1
    return total


def table_count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])


def main() -> None:
    args = parse_args()
    generated_at = datetime.now().isoformat(timespec="seconds")
    conn = sqlite3.connect(args.db_path)
    try:
        init_db(conn, args.schema_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=OFF")
        if not args.append:
            reset_fund_history(conn)
            conn.commit()
        if args.daily_file or args.dividend_file or args.meta_file:
            dividend_counts = count_dividends(args.normalized_root)
            if args.dividend_file:
                dividend_counts = Counter()
                for path in args.dividend_file:
                    for row in iter_jsonl(path):
                        fund_code = pick(row, "fund_code", "基金代码")
                        if fund_code:
                            dividend_counts[str(fund_code)] += 1
            meta_input_rows = load_meta_files(conn, args.meta_file, dividend_counts) if args.meta_file else 0
            conn.commit()
            daily_input_rows = load_daily_files(conn, args.daily_file, max(1, args.batch_size)) if args.daily_file else 0
            dividend_input_rows = load_dividend_files(conn, args.dividend_file, max(1, args.batch_size)) if args.dividend_file else 0
        else:
            dividend_counts = count_dividends(args.normalized_root)
            meta_input_rows = load_meta(conn, args.normalized_root, dividend_counts)
            conn.commit()
            daily_input_rows = load_daily(conn, args.normalized_root, max(1, args.batch_size))
            dividend_input_rows = load_dividends(conn, args.normalized_root, max(1, args.batch_size))
        conn.commit()
        summary = {
            "generated_at": generated_at,
            "normalized_root": str(args.normalized_root.resolve()),
            "append": bool(args.append),
            "daily_files": [str(path.resolve()) for path in args.daily_file],
            "dividend_files": [str(path.resolve()) for path in args.dividend_file],
            "meta_files": [str(path.resolve()) for path in args.meta_file],
            "daily_input_rows": daily_input_rows,
            "dividend_input_rows": dividend_input_rows,
            "meta_input_rows": meta_input_rows,
            "table_counts": {
                "基金日度净值": table_count(conn, "基金日度净值"),
                "基金分红送配": table_count(conn, "基金分红送配"),
                "基金净值概况": table_count(conn, "基金净值概况"),
                "基金信息": table_count(conn, "基金信息"),
            },
            "latest_fund_nav_date": conn.execute('SELECT MAX("交易日期") FROM "基金日度净值"').fetchone()[0],
        }
    finally:
        conn.close()

    output_dir = PROJECT_ROOT / "outputs" / "fund_history_from_normalized"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
