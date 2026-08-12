# -*- coding: utf-8 -*-
"""Recover 基金F10基准 rows from already downloaded fundf10 jbgk HTML files."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from collect_fund_f10_benchmarks import DEFAULT_DB_PATH, extract_page_fields, write_sqlite


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_HTML_DIR = PROJECT_ROOT / "data" / "raw" / "fund_f10_benchmark"
TZ = timezone(timedelta(hours=8))


def now_text() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def default_run_id() -> str:
    return datetime.now(TZ).strftime("%Y%m%dT%H%M%S%z")


def clean(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text in {"None", "nan", "null", "--", "-"} else text


def fund_lookup(db_path: Path) -> dict[str, dict[str, str]]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT
              d."基金代码" AS code,
              COALESCE(NULLIF(TRIM(i."基金名称"), ''), NULLIF(TRIM(d."标准基金名称"), ''), d."基金代码") AS name,
              COALESCE(NULLIF(TRIM(i."基金类型"), ''), NULLIF(TRIM(d."天天基金细分类"), ''), NULLIF(TRIM(d."标准资产细类"), ''), NULLIF(TRIM(d."标准资产大类"), '')) AS fund_type,
              COALESCE(NULLIF(TRIM(i."基金公司"), ''), NULLIF(TRIM(d."基金公司"), '')) AS company
            FROM "基金标准分类字典" d
            LEFT JOIN "基金信息" i ON i."基金代码" = d."基金代码"
            WHERE d."基金代码" IS NOT NULL AND TRIM(d."基金代码") <> ''
            """
        ).fetchall()
    finally:
        conn.close()
    return {
        clean(row["code"]): {
            "基金名称": clean(row["name"]),
            "基金类型": clean(row["fund_type"]),
            "基金公司": clean(row["company"]),
        }
        for row in rows
    }


def html_files(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    candidates: list[Path] = []
    if (root / "html").exists():
        candidates.extend((root / "html").glob("*.html"))
    candidates.extend(root.glob("*.html"))
    return sorted({path.resolve() for path in candidates})


def parse_file(path: Path, lookup: dict[str, dict[str, str]]) -> dict[str, Any] | None:
    code = path.stem.strip()
    if not re.fullmatch(r"\d{6}", code):
        return None
    page_text = path.read_text(encoding="utf-8", errors="ignore")
    fields = extract_page_fields(page_text)
    fund = lookup.get(code, {})
    status = "cached" if fields.get("业绩比较基准") or fields.get("F10基金类型") else "parsed_empty"
    return {
        "基金代码": code,
        "基金名称": fund.get("基金名称") or code,
        "基金类型": fund.get("基金类型") or "",
        "基金公司": fund.get("基金公司") or "",
        "F10基金类型": fields.get("F10基金类型", ""),
        "业绩比较基准": fields.get("业绩比较基准", ""),
        "跟踪标的": fields.get("跟踪标的", ""),
        "F10基金简称": fields.get("基金简称", ""),
        "F10基金全称": fields.get("基金全称", ""),
        "F10成立日期": fields.get("成立日期", ""),
        "F10页面URL": f"http://fundf10.eastmoney.com/jbgk_{code}.html",
        "原始HTML路径": str(path.relative_to(PROJECT_ROOT)) if path.is_relative_to(PROJECT_ROOT) else str(path),
        "采集状态": status,
        "HTTP状态": 200 if status == "cached" else None,
        "错误信息": "" if status == "cached" else "cached HTML parsed without benchmark/type fields",
        "采集开始时间": "",
        "采集完成时间": now_text(),
    }


def summarize(rows: list[dict[str, Any]], run_id: str, html_dir: Path) -> dict[str, Any]:
    status_counts: dict[str, int] = {}
    for row in rows:
        status = row.get("采集状态") or ""
        status_counts[status] = status_counts.get(status, 0) + 1
    benchmark_rows = sum(1 for row in rows if row.get("业绩比较基准"))
    return {
        "runId": run_id,
        "htmlDir": str(html_dir),
        "htmlFileCount": len(rows),
        "业绩比较基准覆盖数": benchmark_rows,
        "业绩比较基准覆盖率": round(benchmark_rows / len(rows), 6) if rows else None,
        "采集状态分布": dict(sorted(status_counts.items())),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Load 基金F10基准 from cached Eastmoney fundf10 HTML files.")
    parser.add_argument("html_dir", type=Path, help="Run directory or html directory containing jbgk HTML files.")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--run-id", default=default_run_id())
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--skip-db", action="store_true")
    args = parser.parse_args()

    lookup = fund_lookup(args.db_path)
    rows = [row for path in html_files(args.html_dir) if (row := parse_file(path, lookup))]
    rows.sort(key=lambda item: item.get("基金代码") or "")
    if not args.skip_db and rows:
        write_sqlite(args.db_path, rows, args.run_id)
    summary = summarize(rows, args.run_id, args.html_dir)
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(
            json.dumps({"meta": summary, "rows": rows}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
