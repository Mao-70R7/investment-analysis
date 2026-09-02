from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
sys.path.insert(0, str(PROJECT_ROOT / "节点脚本" / "_共享组件" / "python_src"))

from advisor_monitor.gffunds_public_jobs import post_public_json  # noqa: E402


DB_PATH = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "gffunds_strategy_ytd_reconciliation"
TABLE_NAME = "广发策略源端收益复爬核对"


def now_local() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def clean(value: Any) -> str:
    return "" if value is None else str(value).strip()


def to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        result = float(str(value).replace("%", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def ytd_row(payload: dict[str, Any], end_date: str) -> dict[str, Any] | None:
    rows = sorted(
        payload.get("adv_yield_trend_list") or [],
        key=lambda item: clean(item.get("yield_date")),
    )
    if not rows:
        return None
    exact = [row for row in rows if clean(row.get("yield_date")) == end_date]
    return exact[-1] if exact else next((row for row in reversed(rows) if clean(row.get("yield_date")) <= end_date), None)


def latest_row(payload: dict[str, Any]) -> dict[str, Any] | None:
    rows = sorted(
        payload.get("adv_yield_trend_list") or [],
        key=lambda item: clean(item.get("yield_date")),
    )
    return rows[-1] if rows else None


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        f'''
        CREATE TABLE IF NOT EXISTS "{TABLE_NAME}" (
          "策略代码" TEXT PRIMARY KEY,
          "策略名称" TEXT,
          "核对截止日期" TEXT NOT NULL,
          "外部上半年收益率_百分比" REAL,
          "外部基准上半年收益率_百分比" REAL,
          "官方App上半年收益率_百分比" REAL,
          "官方App基准上半年收益率_百分比" REAL,
          "本地披露净值重算收益率_百分比" REAL,
          "本地披露净值重算基准收益率_百分比" REAL,
          "最终确认收益率_百分比" REAL,
          "最终确认基准收益率_百分比" REAL,
          "最终确认来源" TEXT,
          "外部与官方App差异_百分点" REAL,
          "外部与本地披露差异_百分点" REAL,
          "核对结论" TEXT,
          "官方App区间起始日" TEXT,
          "官方App区间截止日" TEXT,
          "官方App最新日期" TEXT,
          "官方App最新收益率_百分比" REAL,
          "官方App最新基准收益率_百分比" REAL,
          "官方App最大回撤_百分比" REAL,
          "源端返回码" TEXT,
          "源端错误" TEXT,
          "原始响应路径" TEXT,
          "run_id" TEXT NOT NULL,
          "更新时间" TEXT NOT NULL
        )
        '''
    )


def table_names(conn: sqlite3.Connection) -> list[str]:
    return [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]


def load_external_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    table = table_names(conn)[28]
    return [dict(row) for row in conn.execute(f'SELECT * FROM "{table}" ORDER BY "策略代码"')]


def local_disclosure_metric(conn: sqlite3.Connection, code: str) -> tuple[float | None, float | None]:
    table = table_names(conn)[37]
    start = conn.execute(
        f'''
        SELECT "披露单位净值", "基准收益率_百分比"
        FROM "{table}"
        WHERE "渠道ID"='gffunds' AND "渠道策略ID"=? AND "交易日期"='2025-12-31'
        ORDER BY "生成时间" DESC LIMIT 1
        ''',
        (code,),
    ).fetchone()
    end = conn.execute(
        f'''
        SELECT "披露单位净值", "基准收益率_百分比"
        FROM "{table}"
        WHERE "渠道ID"='gffunds' AND "渠道策略ID"=? AND "交易日期"='2026-06-30'
        ORDER BY "生成时间" DESC LIMIT 1
        ''',
        (code,),
    ).fetchone()
    if not start or not end:
        return None, None
    start_nav, start_bench = to_float(start[0]), to_float(start[1])
    end_nav, end_bench = to_float(end[0]), to_float(end[1])
    ret = (end_nav / start_nav - 1) * 100 if start_nav and end_nav else None
    bench = ((1 + end_bench / 100) / (1 + start_bench / 100) - 1) * 100 if start_bench is not None and end_bench is not None else None
    return ret, bench


def fetch_one(row: dict[str, Any], *, end_date: str, timeout: int, raw_dir: Path, run_id: str) -> dict[str, Any]:
    code = clean(row.get("策略代码")).upper()
    name = clean(row.get("策略名称"))
    raw_path = raw_dir / f"{code}_section_5.json"
    source_error = ""
    payload: dict[str, Any] = {}
    try:
        payload = post_public_json(
            "get_investadvisor_yield_trend",
            {"session_id": "", "adv_id": code, "section_type": "5", "from_page": "StrategyDetail"},
            timeout=timeout,
        )
        raw_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as error:  # pragma: no cover - network dependent
        source_error = f"{type(error).__name__}: {error}"
    item = ytd_row(payload, end_date) if payload else None
    latest = latest_row(payload) if payload else None
    app_ret = to_float(item.get("yield_rate")) if item else None
    app_bench = to_float(item.get("base_yield_rate")) if item else None
    external_ret = to_float(row.get("上半年收益率_百分比"))
    external_bench = to_float(row.get("基准上半年收益率_百分比"))
    diff_app = app_ret - external_ret if app_ret is not None and external_ret is not None else None
    conclusion = "一致"
    if app_ret is None:
        conclusion = "缺官方App源端收益"
    elif diff_app is not None and abs(diff_app) > 0.05:
        conclusion = "外部收益与官方App不一致，以官方App为准"
    return {
        "策略代码": code,
        "策略名称": name,
        "核对截止日期": end_date,
        "外部上半年收益率_百分比": external_ret,
        "外部基准上半年收益率_百分比": external_bench,
        "官方App上半年收益率_百分比": app_ret,
        "官方App基准上半年收益率_百分比": app_bench,
        "本地披露净值重算收益率_百分比": None,
        "本地披露净值重算基准收益率_百分比": None,
        "最终确认收益率_百分比": app_ret if app_ret is not None else external_ret,
        "最终确认基准收益率_百分比": app_bench if app_bench is not None else external_bench,
        "最终确认来源": "广发官方App接口get_investadvisor_yield_trend(section_type=5)" if app_ret is not None else "外部广发策略上半年业绩",
        "外部与官方App差异_百分点": diff_app,
        "外部与本地披露差异_百分点": None,
        "核对结论": conclusion,
        "官方App区间起始日": clean((payload.get("adv_yield_trend_list") or [{}])[0].get("yield_date")) if payload.get("adv_yield_trend_list") else "",
        "官方App区间截止日": clean(item.get("yield_date")) if item else "",
        "官方App最新日期": clean(latest.get("yield_date")) if latest else "",
        "官方App最新收益率_百分比": to_float(latest.get("yield_rate")) if latest else None,
        "官方App最新基准收益率_百分比": to_float(latest.get("base_yield_rate")) if latest else None,
        "官方App最大回撤_百分比": to_float(payload.get("max_drawdown")) if payload else None,
        "源端返回码": clean(payload.get("RETCODE")) if payload else "",
        "源端错误": source_error,
        "原始响应路径": str(raw_path) if raw_path.exists() else "",
        "run_id": run_id,
        "更新时间": now_local().isoformat(timespec="seconds"),
    }


def upsert_rows(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    columns = list(rows[0].keys())
    quoted = ",".join(f'"{column}"' for column in columns)
    placeholders = ",".join("?" for _ in columns)
    updates = ",".join(f'"{column}"=excluded."{column}"' for column in columns if column != "策略代码")
    for row in rows:
        conn.execute(
            f'INSERT INTO "{TABLE_NAME}" ({quoted}) VALUES ({placeholders}) ON CONFLICT("策略代码") DO UPDATE SET {updates}',
            [row.get(column) for column in columns],
        )


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Refetch GFFunds strategy YTD returns and reconcile with external XLS.")
    parser.add_argument("--db-path", type=Path, default=DB_PATH)
    parser.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    parser.add_argument("--end-date", default="2026-06-30")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--timeout", type=int, default=45)
    args = parser.parse_args()

    run_at = now_local()
    run_id = run_at.strftime("%Y%m%dT%H%M%S%z")
    run_dir = args.output_root / run_id
    raw_dir = run_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(args.db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    ensure_table(conn)
    external_rows = load_external_rows(conn)

    rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {
            executor.submit(fetch_one, row, end_date=args.end_date, timeout=args.timeout, raw_dir=raw_dir, run_id=run_id): row
            for row in external_rows
        }
        for index, future in enumerate(as_completed(futures), start=1):
            result = future.result()
            local_ret, local_bench = local_disclosure_metric(conn, result["策略代码"])
            result["本地披露净值重算收益率_百分比"] = local_ret
            result["本地披露净值重算基准收益率_百分比"] = local_bench
            external_ret = result.get("外部上半年收益率_百分比")
            result["外部与本地披露差异_百分点"] = (
                local_ret - external_ret if local_ret is not None and external_ret is not None else None
            )
            rows.append(result)
            if index % 20 == 0 or index == len(external_rows):
                print(f"[strategy {index}/{len(external_rows)}] {result['策略代码']} {result['核对结论']}", flush=True)

    rows.sort(key=lambda item: (abs(item.get("外部与官方App差异_百分点") or 0), item["策略代码"]), reverse=True)
    conn.execute("BEGIN")
    upsert_rows(conn, rows)
    conn.commit()
    conn.close()

    csv_path = run_dir / "gffunds_strategy_ytd_reconciliation.csv"
    json_path = run_dir / "gffunds_strategy_ytd_reconciliation.json"
    write_csv(csv_path, rows)
    summary = {
        "runId": run_id,
        "startedAt": run_at.isoformat(timespec="seconds"),
        "finishedAt": now_local().isoformat(timespec="seconds"),
        "rows": len(rows),
        "officialAppCovered": sum(1 for row in rows if row.get("官方App上半年收益率_百分比") is not None),
        "externalDiffCount": sum(1 for row in rows if row.get("外部与官方App差异_百分点") is not None and abs(row["外部与官方App差异_百分点"]) > 0.05),
        "csvPath": str(csv_path),
        "jsonPath": str(json_path),
        "rawDir": str(raw_dir),
    }
    json_path.write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
