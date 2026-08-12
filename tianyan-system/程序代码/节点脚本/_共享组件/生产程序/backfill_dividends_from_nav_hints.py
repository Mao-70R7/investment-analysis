from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="从基金日度净值的净值图分红提示回填基金分红送配明细。")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def parse_cash_dividend(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if "拆分" in text or "折算" in text:
        return None
    if "分红" not in text and "派现金" not in text and "派现" not in text:
        return None
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)", text.replace(",", ""))
    if not match:
        return None
    amount = float(match.group(1))
    if re.search(r"每\s*10\s*份|10\s*份", text):
        amount /= 10.0
    if not math.isfinite(amount) or amount <= 0:
        return None
    return amount


def canonical_dividend_text(amount: float) -> str:
    text = f"{amount:.6f}".rstrip("0").rstrip(".")
    return f"每份派现金{text}元"


def existing_dividend_amounts(conn: sqlite3.Connection) -> dict[tuple[str, str], list[float]]:
    rows = conn.execute(
        """
        SELECT "基金代码", COALESCE("除息日", "权益登记日") AS "分红日", "每份分红"
        FROM "基金分红送配"
        """
    ).fetchall()
    result: dict[tuple[str, str], list[float]] = {}
    for code, date_value, dividend_text in rows:
        amount = parse_cash_dividend(dividend_text)
        if code and date_value and amount is not None:
            result.setdefault((str(code), str(date_value)), []).append(amount)
    return result


def main() -> None:
    args = parse_args()
    conn = sqlite3.connect(args.db_path)
    try:
        existing = existing_dividend_amounts(conn)
        nav_rows = conn.execute(
            """
            SELECT "基金代码", "交易日期", "基金名称", "净值图分红送配", "原始净值快照ID", "采集时间"
            FROM "基金日度净值"
            WHERE "净值图分红送配" IS NOT NULL
              AND TRIM("净值图分红送配") <> ''
              AND TRIM("净值图分红送配") <> '--'
            """
        ).fetchall()
        insert_rows: list[tuple[Any, ...]] = []
        skipped_split = 0
        skipped_existing = 0
        skipped_unparseable = 0
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        for code, trade_date, fund_name, hint, source_snapshot_id, captured_at in nav_rows:
            amount = parse_cash_dividend(hint)
            if amount is None:
                if hint and ("拆分" in str(hint) or "折算" in str(hint)):
                    skipped_split += 1
                else:
                    skipped_unparseable += 1
                continue
            candidates = existing.get((str(code), str(trade_date)), [])
            if any(abs(amount - candidate) <= 0.00005 for candidate in candidates):
                skipped_existing += 1
                continue
            insert_rows.append(
                (
                    code,
                    trade_date,
                    trade_date,
                    fund_name,
                    str(trade_date)[:4] if trade_date else None,
                    canonical_dividend_text(amount),
                    None,
                    "基金日度净值_净值图分红送配回填",
                    source_snapshot_id,
                    captured_at or now,
                )
            )
            existing.setdefault((str(code), str(trade_date)), []).append(amount)

        if not args.dry_run and insert_rows:
            conn.executemany(
                """
                INSERT INTO "基金分红送配" (
                    "基金代码", "权益登记日", "除息日", "基金名称", "年份", "每份分红", "分红发放日",
                    "数据来源", "原始分红快照ID", "采集时间"
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT("基金代码", "权益登记日", "每份分红") DO UPDATE SET
                    "除息日"=excluded."除息日",
                    "基金名称"=COALESCE(excluded."基金名称", "基金分红送配"."基金名称"),
                    "年份"=excluded."年份",
                    "数据来源"=excluded."数据来源",
                    "原始分红快照ID"=COALESCE(excluded."原始分红快照ID", "基金分红送配"."原始分红快照ID"),
                    "采集时间"=excluded."采集时间"
                """,
                insert_rows,
            )
            conn.execute(
                """
                UPDATE "基金净值概况"
                SET "分红事件数" = (
                    SELECT COUNT(*)
                    FROM "基金分红送配" d
                    WHERE d."基金代码" = "基金净值概况"."基金代码"
                )
                """
            )
            conn.commit()

        summary = {
            "数据库": str(args.db_path.resolve()),
            "是否试运行": bool(args.dry_run),
            "净值图提示行数": len(nav_rows),
            "拟回填分红行数": len(insert_rows),
            "已存在跳过行数": skipped_existing,
            "拆分折算跳过行数": skipped_split,
            "不可解析跳过行数": skipped_unparseable,
            "实际写入行数": 0 if args.dry_run else len(insert_rows),
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
