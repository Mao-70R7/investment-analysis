from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
DEFAULT_CODES = ("968157",)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill Eastmoney overseas fund NAVs from embedded page data.")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--fund-code", action="append", default=[])
    parser.add_argument("--raw-output-dir", type=Path, default=PROJECT_ROOT / "data" / "raw" / "ttfund_overseas_nav")
    parser.add_argument(
        "--normalized-output-dir",
        type=Path,
        default=PROJECT_ROOT / "data" / "normalized" / "ttfund_overseas_nav",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def now_shanghai() -> datetime:
    return datetime.now(ZoneInfo("Asia/Shanghai"))


def fetch_text(url: str) -> str:
    response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    return response.text


def parse_chart_rows(html: str) -> list[dict[str, Any]]:
    match = re.search(r"var\s+dwJZChartData\s*=\s*eval\((\{.*?\})\);", html, flags=re.S)
    if not match:
        raise ValueError("dwJZChartData not found")
    payload = json.loads(match.group(1))
    rows: list[dict[str, Any]] = []
    for item in payload.get("dataList") or []:
        millis = item.get("x")
        nav = item.get("y")
        if millis is None or nav in (None, ""):
            continue
        trade_date = datetime.fromtimestamp(int(millis) / 1000, tz=timezone.utc).date().isoformat()
        ret_text = str(item.get("equityReturn") or "").strip()
        daily_return = None
        if ret_text and ret_text != "--":
            daily_return = float(ret_text.rstrip("%"))
        rows.append(
            {
                "trade_date": trade_date,
                "nav": float(nav),
                "daily_return": daily_return,
                "unit_money": item.get("unitMoney"),
            }
        )
    rows.sort(key=lambda row: row["trade_date"])
    return rows


def parse_fund_name(html: str, fund_code: str) -> str:
    match = re.search(r"<title>(.*?)</title>", html, flags=re.S)
    if not match:
        return fund_code
    title = re.sub(r"\s+", "", match.group(1))
    return title.split("(")[0].replace("基金净值回报和阶段收益信息_海外基金档案_天天基金网", "") or fund_code


def write_artifacts(
    args: argparse.Namespace,
    run_id: str,
    fund_code: str,
    html: str,
    rows: list[dict[str, Any]],
) -> tuple[Path, Path]:
    raw_dir = args.raw_output_dir / run_id
    normalized_dir = args.normalized_output_dir / "fund_nav_daily" / run_id
    raw_dir.mkdir(parents=True, exist_ok=True)
    normalized_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / f"{fund_code}.html"
    normalized_path = normalized_dir / f"{fund_code}.jsonl"
    raw_path.write_text(html, encoding="utf-8")
    with normalized_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps({"fund_code": fund_code, **row}, ensure_ascii=False, sort_keys=True) + "\n")
    return raw_path, normalized_path


def upsert_rows(
    conn: sqlite3.Connection,
    fund_code: str,
    fund_name: str,
    rows: list[dict[str, Any]],
    snapshot_id: str,
    captured_at: str,
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
            "基金名称"=excluded."基金名称",
            "基金类型"=COALESCE(excluded."基金类型", "基金信息"."基金类型"),
            "最新净值"=excluded."最新净值",
            "最新净值日期"=excluded."最新净值日期",
            "基金状态"=COALESCE(excluded."基金状态", "基金信息"."基金状态"),
            "数据来源"=excluded."数据来源",
            "最近更新时间"=excluded."最近更新时间"
        """,
        (fund_code, fund_name, "东亚联丰投资管理", "互认基金", latest["nav"], latest["trade_date"], "正常披露", "天天基金海外基金页面", captured_at),
    )
    conn.executemany(
        """
        INSERT INTO "基金日度净值" (
            "基金代码", "交易日期", "基金名称", "基金类型", "基金公司", "净值口径",
            "单位净值", "累计净值", "日收益率_百分比", "每万份收益", "七日年化收益率_百分比",
            "净值图分红送配", "是否货币基金", "数据来源", "原始净值快照ID", "采集时间"
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT("基金代码", "交易日期") DO UPDATE SET
            "基金名称"=excluded."基金名称",
            "基金类型"=excluded."基金类型",
            "基金公司"=excluded."基金公司",
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
                fund_code,
                row["trade_date"],
                fund_name,
                "互认基金",
                "东亚联丰投资管理",
                "单位净值",
                row["nav"],
                row["nav"],
                row["daily_return"],
                None,
                None,
                None,
                0,
                "天天基金海外基金页面",
                snapshot_id,
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
            "基金名称"=excluded."基金名称",
            "基金类型"=excluded."基金类型",
            "基金公司"=excluded."基金公司",
            "净值口径"=excluded."净值口径",
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
            fund_name,
            "互认基金",
            "东亚联丰投资管理",
            "单位净值",
            0,
            rows[0]["trade_date"],
            latest["trade_date"],
            len(rows),
            0,
            latest["nav"],
            latest["nav"],
            latest["daily_return"],
            None,
            None,
            "天天基金海外基金页面",
            snapshot_id,
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
    summary: dict[str, Any] = {"run_id": run_id, "dry_run": args.dry_run, "funds": {}}
    conn = sqlite3.connect(args.db_path)
    try:
        with conn:
            for fund_code in fund_codes:
                url = f"https://overseas.1234567.com.cn/f10/FundJz/{fund_code}"
                try:
                    html = fetch_text(url)
                    rows = parse_chart_rows(html)
                    fund_name = parse_fund_name(html, fund_code)
                    raw_path, normalized_path = write_artifacts(args, run_id, fund_code, html, rows)
                    snapshot_id = f"ttfund_overseas_{fund_code}_{run_id}"
                    upserted = 0 if args.dry_run else upsert_rows(conn, fund_code, fund_name, rows, snapshot_id, captured_at)
                    summary["funds"][fund_code] = {
                        "source_url": url,
                        "status": "ok",
                        "fetched_rows": len(rows),
                        "upserted_rows": upserted,
                        "first_date": rows[0]["trade_date"] if rows else None,
                        "last_date": rows[-1]["trade_date"] if rows else None,
                        "latest_nav": rows[-1]["nav"] if rows else None,
                        "raw_path": str(raw_path),
                        "normalized_path": str(normalized_path),
                    }
                except (requests.RequestException, TimeoutError, ValueError, json.JSONDecodeError) as exc:
                    summary["funds"][fund_code] = {
                        "source_url": url,
                        "status": "failed",
                        "error": f"{type(exc).__name__}: {exc}",
                        "fetched_rows": 0,
                        "upserted_rows": 0,
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
