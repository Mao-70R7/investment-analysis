from __future__ import annotations

import argparse
import http.client
import json
import sqlite3
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
DEFAULT_CODES = ("968048", "968052", "968163")
DEFAULT_SOURCE_URL = "https://www.cifm.com/web/search"
DEFAULT_CHANNEL_ID = "269512"
DEFAULT_START_DATE = "2020-01-01"


@dataclass(frozen=True)
class FundNavRow:
    fund_code: str
    trade_date: str
    fund_name: str | None
    fund_type: str | None
    nav: float | None
    accumulated_nav: float | None
    daily_return: float | None
    fund_state: str | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill JPMorgan/CIFM mutual-recognition fund NAVs into analysis_zh_current.sqlite."
    )
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--fund-code", action="append", default=[], help="Fund code to fetch; repeatable.")
    parser.add_argument("--start-date", default=DEFAULT_START_DATE)
    parser.add_argument("--end-date", default=date.today().isoformat())
    parser.add_argument("--page-size", type=int, default=50)
    parser.add_argument("--max-retries", type=int, default=4)
    parser.add_argument("--retry-sleep", type=float, default=1.5)
    parser.add_argument("--source-url", default=DEFAULT_SOURCE_URL)
    parser.add_argument("--channel-id", default=DEFAULT_CHANNEL_ID)
    parser.add_argument(
        "--raw-output-dir",
        type=Path,
        default=PROJECT_ROOT / "data" / "raw" / "cifm_mutual_fund_nav",
    )
    parser.add_argument(
        "--normalized-output-dir",
        type=Path,
        default=PROJECT_ROOT / "data" / "normalized" / "cifm_mutual_fund_nav",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def now_shanghai() -> datetime:
    return datetime.now(ZoneInfo("Asia/Shanghai"))


def as_float(value: Any) -> float | None:
    if value in (None, "", "--"):
        return None
    text = str(value).strip().replace(",", "")
    if text.endswith("%"):
        text = text[:-1]
    try:
        return float(text)
    except ValueError:
        return None


def normalize_date(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip().replace(".", "-").replace("/", "-")
    parts = text.split("-")
    if len(parts) == 3:
        return f"{int(parts[0]):04d}-{int(parts[1]):02d}-{int(parts[2]):02d}"
    return text


def fetch_page(
    source_url: str,
    channel_id: str,
    fund_code: str,
    start_date: str,
    end_date: str,
    page: int,
    page_size: int,
    max_retries: int = 4,
    retry_sleep: float = 1.5,
) -> dict[str, Any]:
    body = urllib.parse.urlencode(
        {
            "channelid": channel_id,
            "searchword": f"c_fundcode={fund_code}",
            "startDate": start_date,
            "endDate": end_date,
            "page": str(page),
            "perpage": str(page_size),
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        source_url,
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "User-Agent": "Mozilla/5.0",
            "Referer": f"https://www.cifm.com/fund/{fund_code}/",
        },
        method="POST",
    )
    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                return json.loads(response.read().decode("utf-8"))
        except (http.client.IncompleteRead, TimeoutError, OSError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt >= max_retries:
                break
            time.sleep(retry_sleep * attempt)
    raise RuntimeError(
        f"failed to fetch CIFM nav page after {max_retries} attempts: "
        f"fund_code={fund_code}, page={page}, page_size={page_size}, error={last_error}"
    ) from last_error


def fetch_fund_rows(args: argparse.Namespace, fund_code: str) -> tuple[list[dict[str, Any]], list[FundNavRow]]:
    raw_pages: list[dict[str, Any]] = []
    rows: list[FundNavRow] = []
    page = 1
    page_total = 1
    while page <= page_total:
        payload = fetch_page(
            args.source_url,
            args.channel_id,
            fund_code,
            args.start_date,
            args.end_date,
            page,
            args.page_size,
            args.max_retries,
            args.retry_sleep,
        )
        raw_pages.append(payload)
        page_total = int(payload.get("pageTotal") or 1)
        for item in payload.get("rows") or []:
            trade_date = normalize_date(item.get("FUNDDATE"))
            if not trade_date:
                continue
            rows.append(
                FundNavRow(
                    fund_code=str(item.get("FUNDCODE") or fund_code).strip(),
                    trade_date=trade_date,
                    fund_name=item.get("FUNDNAME"),
                    fund_type=item.get("FUNDTYPE"),
                    nav=as_float(item.get("NETVALUE")),
                    accumulated_nav=as_float(item.get("TOTALNETVALUE")),
                    daily_return=as_float(item.get("TODAYWAVER")),
                    fund_state=item.get("FUNDSTATE"),
                )
            )
        page += 1
    rows.sort(key=lambda row: row.trade_date)
    return raw_pages, rows


def write_artifacts(
    args: argparse.Namespace,
    run_id: str,
    fund_code: str,
    raw_pages: list[dict[str, Any]],
    rows: list[FundNavRow],
) -> tuple[Path, Path]:
    raw_dir = args.raw_output_dir / run_id
    normalized_dir = args.normalized_output_dir / "fund_nav_daily" / run_id
    raw_dir.mkdir(parents=True, exist_ok=True)
    normalized_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / f"{fund_code}.json"
    normalized_path = normalized_dir / f"{fund_code}.jsonl"
    raw_path.write_text(json.dumps(raw_pages, ensure_ascii=False, indent=2), encoding="utf-8")
    with normalized_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row.__dict__, ensure_ascii=False, sort_keys=True) + "\n")
    return raw_path, normalized_path


def ensure_fund_info(conn: sqlite3.Connection, row: FundNavRow, captured_at: str) -> None:
    conn.execute(
        """
        INSERT INTO "基金信息" (
            "基金代码", "基金名称", "基金公司", "基金类型", "最新净值", "最新净值日期", "基金状态", "数据来源", "最近更新时间"
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT("基金代码") DO UPDATE SET
            "基金名称"=COALESCE(excluded."基金名称", "基金信息"."基金名称"),
            "基金公司"=COALESCE("基金信息"."基金公司", excluded."基金公司"),
            "基金类型"=COALESCE(excluded."基金类型", "基金信息"."基金类型"),
            "最新净值"=COALESCE(excluded."最新净值", "基金信息"."最新净值"),
            "最新净值日期"=COALESCE(excluded."最新净值日期", "基金信息"."最新净值日期"),
            "基金状态"=COALESCE(excluded."基金状态", "基金信息"."基金状态"),
            "数据来源"=excluded."数据来源",
            "最近更新时间"=excluded."最近更新时间"
        """,
        (
            row.fund_code,
            row.fund_name,
            "摩根基金",
            row.fund_type or "互认基金",
            row.nav,
            row.trade_date,
            row.fund_state,
            "摩根基金官网_web_search",
            captured_at,
        ),
    )


def upsert_daily_rows(
    conn: sqlite3.Connection,
    rows: list[FundNavRow],
    source_snapshot_id: str,
    captured_at: str,
) -> int:
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
            "数据来源"=excluded."数据来源",
            "原始净值快照ID"=excluded."原始净值快照ID",
            "采集时间"=excluded."采集时间"
        """,
        [
            (
                row.fund_code,
                row.trade_date,
                row.fund_name,
                row.fund_type or "互认基金",
                "摩根基金",
                "单位净值",
                row.nav,
                row.accumulated_nav,
                row.daily_return,
                None,
                None,
                None,
                0,
                "摩根基金官网_web_search",
                source_snapshot_id,
                captured_at,
            )
            for row in rows
        ],
    )
    return len(rows)


def upsert_fund_summary(
    conn: sqlite3.Connection,
    fund_code: str,
    rows: list[FundNavRow],
    source_snapshot_id: str,
    captured_at: str,
) -> None:
    if not rows:
        return
    first = rows[0]
    latest = rows[-1]
    conn.execute(
        """
        INSERT INTO "基金净值概况" (
            "基金代码", "基金名称", "基金类型", "基金公司", "净值口径", "是否货币基金",
            "历史起始日期", "历史结束日期", "历史记录数", "分红事件数",
            "最新单位净值", "最新累计净值", "最新日收益率_百分比", "最新每万份收益", "最新七日年化收益率_百分比",
            "数据来源", "原始净值快照ID", "原始分红快照ID", "最近采集时间"
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT("基金代码") DO UPDATE SET
            "基金名称"=excluded."基金名称",
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
            fund_code,
            latest.fund_name or first.fund_name,
            latest.fund_type or first.fund_type or "互认基金",
            "摩根基金",
            "单位净值",
            0,
            first.trade_date,
            latest.trade_date,
            len(rows),
            0,
            latest.nav,
            latest.accumulated_nav,
            latest.daily_return,
            None,
            None,
            "摩根基金官网_web_search",
            source_snapshot_id,
            None,
            captured_at,
        ),
    )


def main() -> None:
    args = parse_args()
    fund_codes = tuple(args.fund_code or DEFAULT_CODES)
    captured = now_shanghai()
    captured_at = captured.isoformat(timespec="seconds")
    run_id = captured.strftime("%Y%m%dT%H%M%S%z")
    summary: dict[str, Any] = {
        "run_id": run_id,
        "source_url": args.source_url,
        "channel_id": args.channel_id,
        "start_date": args.start_date,
        "end_date": args.end_date,
        "dry_run": args.dry_run,
        "funds": {},
    }

    conn = sqlite3.connect(args.db_path)
    try:
        with conn:
            for fund_code in fund_codes:
                raw_pages, rows = fetch_fund_rows(args, fund_code)
                raw_path, normalized_path = write_artifacts(args, run_id, fund_code, raw_pages, rows)
                snapshot_id = f"cifm_web_search_{fund_code}_{run_id}"
                if rows and not args.dry_run:
                    ensure_fund_info(conn, rows[-1], captured_at)
                    inserted = upsert_daily_rows(conn, rows, snapshot_id, captured_at)
                    upsert_fund_summary(conn, fund_code, rows, snapshot_id, captured_at)
                else:
                    inserted = 0
                summary["funds"][fund_code] = {
                    "raw_pages": len(raw_pages),
                    "fetched_rows": len(rows),
                    "upserted_rows": inserted,
                    "first_date": rows[0].trade_date if rows else None,
                    "last_date": rows[-1].trade_date if rows else None,
                    "latest_nav": rows[-1].nav if rows else None,
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
