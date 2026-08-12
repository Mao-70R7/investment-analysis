from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
import time
from datetime import datetime, timezone
from http.client import IncompleteRead
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
DEFAULT_COVERAGE_FILE = PROJECT_ROOT / "outputs" / "fund_dependency_backfill" / "2026-05-27" / "guangfa_missing_fund_coverage.csv"
DEFAULT_RAW_ROOT = PROJECT_ROOT / "data" / "raw" / "sina_fund_nav"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "fund_dependency_backfill"
API_URL = "https://stock.finance.sina.com.cn/fundInfo/api/openapi.php/CaihuiFundInfoService.getNav"
REFERER_URL = "https://stock.finance.sina.com.cn/fundInfo/view/FundInfo_LSJZ.php"
USER_AGENT = "Mozilla/5.0 advisor-monitor/0.1"
PER_PAGE = 20


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill fund NAV from Sina Caihui fund NAV API into analysis SQLite.")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--coverage-file", type=Path, default=DEFAULT_COVERAGE_FILE)
    parser.add_argument("--raw-root", type=Path, default=DEFAULT_RAW_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--fund-code", action="append", default=[], help="Optional explicit fund code filter.")
    parser.add_argument("--timeout-sec", type=int, default=15)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--sleep-sec", type=float, default=0.15)
    return parser.parse_args()


def to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).strip().replace(",", ""))
    except ValueError:
        return None


def normalize_date(value: Any) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    if " " in text:
        text = text.split(" ", 1)[0]
    text = text.replace("/", "-")
    parts = text.split("-")
    if len(parts) != 3:
        return None
    return f"{int(parts[0]):04d}-{int(parts[1]):02d}-{int(parts[2]):02d}"


def fetch_json(url: str, *, timeout: int, retries: int) -> dict[str, Any]:
    headers = {"User-Agent": USER_AGENT, "Referer": REFERER_URL}
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            request = Request(url, headers=headers)
            with urlopen(request, timeout=timeout) as response:
                try:
                    raw = response.read()
                except IncompleteRead as error:
                    raw = error.partial
            return json.loads(raw.decode("utf-8", errors="replace"))
        except Exception as error:  # pragma: no cover - network-dependent
            last_error = error
            if attempt < retries:
                time.sleep(0.8 * attempt)
    raise RuntimeError(f"fetch failed for {url}: {last_error}")


def fetch_fund_range(
    fund_code: str,
    start_date: str,
    end_date: str,
    *,
    raw_dir: Path,
    timeout: int,
    retries: int,
    sleep_sec: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    raw_dir.mkdir(parents=True, exist_ok=True)
    all_items: list[dict[str, Any]] = []
    pages = 1
    total = 0
    for page in range(1, 10_000):
        params = {"symbol": fund_code, "datefrom": start_date, "dateto": end_date, "page": page}
        url = f"{API_URL}?{urlencode(params)}"
        payload = fetch_json(url, timeout=timeout, retries=retries)
        (raw_dir / f"{fund_code}_page_{page:04d}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        data = (((payload.get("result") or {}).get("data") or {}).get("data") or [])
        total = int((((payload.get("result") or {}).get("data") or {}).get("total_num") or 0))
        all_items.extend(data)
        pages = max(1, math.ceil(total / PER_PAGE)) if total else 1
        if page >= pages:
            break
        if sleep_sec:
            time.sleep(sleep_sec)

    dedup: dict[str, dict[str, Any]] = {}
    for item in all_items:
        trade_date = normalize_date(item.get("fbrq"))
        if trade_date:
            dedup[trade_date] = item

    sorted_dates = sorted(dedup)
    daily_rows: list[dict[str, Any]] = []
    prev_nav: float | None = None
    for trade_date in sorted_dates:
        item = dedup[trade_date]
        nav = to_float(item.get("jjjz"))
        acc = to_float(item.get("ljjz"))
        daily_return = None
        if nav is not None and prev_nav not in (None, 0):
            daily_return = round((nav / prev_nav - 1.0) * 100.0, 8)
        if nav is not None:
            prev_nav = nav
        daily_rows.append(
            {
                "基金代码": fund_code,
                "交易日期": trade_date,
                "单位净值": nav,
                "累计净值": acc,
                "日收益率_百分比": daily_return,
            }
        )
    return daily_rows, all_items, total


def load_targets(path: Path, selected_codes: set[str]) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    targets: list[dict[str, str]] = []
    for row in rows:
        code = (row.get("基金代码") or "").strip()
        if not code:
            continue
        if selected_codes and code not in selected_codes:
            continue
        targets.append(
            {
                "fund_code": code,
                "fund_name": (row.get("基金名称") or "").strip() or None,
                "fund_type": (row.get("基金类型") or "").strip() or None,
                "fund_company": (row.get("基金公司") or "").strip() or None,
                "start_date": (row.get("需要起始日") or "").strip(),
                "end_date": (row.get("需要结束日") or "").strip(),
                "gap_status": (row.get("缺口状态") or "").strip(),
            }
        )
    return targets


def upsert_rows(conn: sqlite3.Connection, target: dict[str, str], rows: list[dict[str, Any]], captured_at: str) -> None:
    if not rows:
        return
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
            "日收益率_百分比"=COALESCE(excluded."日收益率_百分比", "基金日度净值"."日收益率_百分比"),
            "是否货币基金"=0,
            "数据来源"=excluded."数据来源",
            "原始净值快照ID"=excluded."原始净值快照ID",
            "采集时间"=excluded."采集时间"
        """,
        [
            (
                row["基金代码"],
                row["交易日期"],
                target.get("fund_name"),
                target.get("fund_type"),
                target.get("fund_company"),
                "单位净值",
                row.get("单位净值"),
                row.get("累计净值"),
                row.get("日收益率_百分比"),
                None,
                None,
                None,
                0,
                "新浪财经_CaihuiFundInfoService.getNav",
                f"sina_fund_nav-{target['fund_code']}",
                captured_at,
            )
            for row in rows
        ],
    )
    trade_dates = [row["交易日期"] for row in rows if row.get("交易日期")]
    latest = max(rows, key=lambda row: row["交易日期"])
    conn.execute(
        """
        INSERT INTO "基金信息" ("基金代码", "基金名称", "基金公司", "基金类型", "最新净值", "最新净值日期", "数据来源")
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT("基金代码") DO UPDATE SET
            "基金名称"=COALESCE("基金信息"."基金名称", excluded."基金名称"),
            "基金公司"=COALESCE("基金信息"."基金公司", excluded."基金公司"),
            "基金类型"=COALESCE("基金信息"."基金类型", excluded."基金类型"),
            "最新净值"=excluded."最新净值",
            "最新净值日期"=excluded."最新净值日期",
            "数据来源"=COALESCE("基金信息"."数据来源", excluded."数据来源"),
            "最近更新时间"=CURRENT_TIMESTAMP
        """,
        (
            target["fund_code"],
            target.get("fund_name"),
            target.get("fund_company"),
            target.get("fund_type"),
            latest.get("单位净值"),
            latest.get("交易日期"),
            "新浪财经_CaihuiFundInfoService.getNav",
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
            "基金名称"=COALESCE("基金净值概况"."基金名称", excluded."基金名称"),
            "基金类型"=COALESCE("基金净值概况"."基金类型", excluded."基金类型"),
            "基金公司"=COALESCE("基金净值概况"."基金公司", excluded."基金公司"),
            "净值口径"=excluded."净值口径",
            "是否货币基金"=excluded."是否货币基金",
            "历史起始日期"=min(COALESCE("基金净值概况"."历史起始日期", excluded."历史起始日期"), excluded."历史起始日期"),
            "历史结束日期"=max(COALESCE("基金净值概况"."历史结束日期", excluded."历史结束日期"), excluded."历史结束日期"),
            "历史记录数"=(select count(*) from "基金日度净值" where "基金代码"=excluded."基金代码"),
            "最新单位净值"=excluded."最新单位净值",
            "最新累计净值"=excluded."最新累计净值",
            "最新日收益率_百分比"=excluded."最新日收益率_百分比",
            "数据来源"=excluded."数据来源",
            "原始净值快照ID"=excluded."原始净值快照ID",
            "最近采集时间"=excluded."最近采集时间"
        """,
        (
            target["fund_code"],
            target.get("fund_name"),
            target.get("fund_type"),
            target.get("fund_company"),
            "单位净值",
            0,
            min(trade_dates),
            max(trade_dates),
            len(rows),
            0,
            latest.get("单位净值"),
            latest.get("累计净值"),
            latest.get("日收益率_百分比"),
            None,
            None,
            "新浪财经_CaihuiFundInfoService.getNav",
            f"sina_fund_nav-{target['fund_code']}",
            None,
            captured_at,
        ),
    )


def main() -> None:
    args = parse_args()
    selected = {str(code).strip() for code in args.fund_code if str(code).strip()}
    targets = load_targets(args.coverage_file, selected)
    run_at = datetime.now(timezone.utc).astimezone()
    day = run_at.strftime("%Y-%m-%d")
    run_id = run_at.strftime("%Y%m%dT%H%M%S%z")
    captured_at = run_at.isoformat(timespec="seconds")
    raw_dir = args.raw_root / day / run_id
    output_dir = args.output_root / day
    output_dir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(args.db_path)
    conn.execute("PRAGMA busy_timeout = 3000;")
    summary_rows: list[dict[str, Any]] = []
    for index, target in enumerate(targets, start=1):
        fund_code = target["fund_code"]
        try:
            rows, raw_items, total = fetch_fund_range(
                fund_code,
                target["start_date"],
                target["end_date"],
                raw_dir=raw_dir / "funds" / fund_code,
                timeout=max(1, args.timeout_sec),
                retries=max(1, args.retries),
                sleep_sec=max(0.0, args.sleep_sec),
            )
            upsert_rows(conn, target, rows, captured_at)
            conn.commit()
            status = "成功" if rows else "无数据"
            summary_rows.append(
                {
                    "基金代码": fund_code,
                    "基金名称": target.get("fund_name"),
                    "原缺口状态": target.get("gap_status"),
                    "状态": status,
                    "接口总记录数": total,
                    "入库记录数": len(rows),
                    "入库起始日": min((row["交易日期"] for row in rows), default=None),
                    "入库结束日": max((row["交易日期"] for row in rows), default=None),
                }
            )
            print(f"[{index}/{len(targets)}] {fund_code} {status} rows={len(rows)} total={total}", flush=True)
        except Exception as error:
            summary_rows.append(
                {
                    "基金代码": fund_code,
                    "基金名称": target.get("fund_name"),
                    "原缺口状态": target.get("gap_status"),
                    "状态": "失败",
                    "错误": str(error),
                }
            )
            print(f"[{index}/{len(targets)}] {fund_code} failed: {error}", flush=True)

    summary_path = output_dir / f"sina_fund_nav_backfill_{run_id}.csv"
    write_headers = sorted({key for row in summary_rows for key in row})
    with summary_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=write_headers)
        writer.writeheader()
        writer.writerows(summary_rows)
    print(
        json.dumps(
            {
                "run_id": run_id,
                "targets": len(targets),
                "success": sum(1 for row in summary_rows if row.get("状态") == "成功"),
                "empty": sum(1 for row in summary_rows if row.get("状态") == "无数据"),
                "failed": sum(1 for row in summary_rows if row.get("状态") == "失败"),
                "summary_path": str(summary_path.resolve()),
                "raw_dir": str(raw_dir.resolve()),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    conn.close()


if __name__ == "__main__":
    main()
