from __future__ import annotations

import argparse
import json
import math
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
CURVE_API_URL = "https://ibgmarket.tiantianfunds.com/combine/investAdviserInfo/fundIAAIChartPro"
DEFAULT_ANCHOR_STRATEGY_ID = "JQNQMI3"
DEFAULT_RANGE = "ln"
DEFAULT_BASE_LEVEL = 1000.0
USER_AGENT = "ttjj/6.6.19 Android advisor-monitor/0.1"


INDEX_CONFIG: dict[str, dict[str, str]] = {
    "990100.MI": {"name": "MSCI全球/发达市场", "ttfund_index_code": "990100", "group": "海外指数"},
    "SPX.GI": {"name": "标普500", "ttfund_index_code": "SPX", "group": "海外指数"},
    "NDX.GI": {"name": "纳斯达克100", "ttfund_index_code": "NDX", "group": "海外指数"},
    "000510.SH": {"name": "中证A500", "ttfund_index_code": "000510", "group": "股票指数"},
    "930903.CSI": {"name": "中证A股", "ttfund_index_code": "930903", "group": "股票指数"},
    "H30318.CSI": {"name": "TMT150", "ttfund_index_code": "H30318", "group": "股票指数"},
    "000941.SH": {"name": "中证新能源", "ttfund_index_code": "000941", "group": "股票指数"},
    "AU9999.SGE": {"name": "上海黄金Au99.99", "ttfund_index_code": "AU9999", "group": "商品指数"},
    "NHCI.NHF": {"name": "南华商品指数", "ttfund_index_code": "NHCI", "group": "商品指数"},
    "H30009.CSI": {"name": "中证商品CFI", "ttfund_index_code": "H30009", "group": "商品指数"},
    "H11061.CSI": {"name": "中证商品期货综合(CFCI)", "ttfund_index_code": "H11061", "group": "商品指数"},
    "H11006.CSI": {"name": "中证国债", "ttfund_index_code": "H11006", "group": "债券指数"},
    "H11008.CSI": {"name": "中证企业债", "ttfund_index_code": "H11008", "group": "债券指数"},
    "H11001.CSI": {"name": "中证全债", "ttfund_index_code": "H11001", "group": "债券指数"},
    "H11009.CSI": {"name": "中证综合债", "ttfund_index_code": "H11009", "group": "债券指数"},
    "H11015.CSI": {"name": "中证短债", "ttfund_index_code": "H11015", "group": "债券指数"},
    "H11025.CSI": {"name": "中证货币基金", "ttfund_index_code": "H11025", "group": "基金指数"},
    "H11023.CSI": {"name": "中证债券型基金", "ttfund_index_code": "H11023", "group": "基金指数"},
    "930950.CSI": {"name": "中证偏股型基金", "ttfund_index_code": "930950", "group": "基金指数"},
    "930609.CSI": {"name": "中证纯债债券型基金", "ttfund_index_code": "930609", "group": "基金指数"},
    "930610.CSI": {"name": "中证普通债券型基金", "ttfund_index_code": "930610", "group": "基金指数"},
    "CBA00201.CS": {"name": "中债-综合财富(总值)", "ttfund_index_code": "CBA00201", "group": "中债指数"},
    "CBA00203.CS": {"name": "中债-综合全价(总值)", "ttfund_index_code": "CBA00203", "group": "中债指数"},
    "CBA00101.CS": {"name": "中债-总财富(总值)", "ttfund_index_code": "CBA00101", "group": "中债指数"},
    "CBA00103.CS": {"name": "中债-总全价(总值)", "ttfund_index_code": "CBA00103", "group": "中债指数"},
    "CBA00303.CS": {"name": "中债-总指数全价", "ttfund_index_code": "CBA00303", "group": "中债指数"},
    "CBA00123.CS": {"name": "中债-新综合全价(1-3年)", "ttfund_index_code": "CBA00123", "group": "中债指数"},
    "CBA00121.CS": {"name": "中债-新综合财富(1-3年)", "ttfund_index_code": "CBA00121", "group": "中债指数"},
    "CBA01103.CS": {"name": "中债-固定利率国债全价(总值)", "ttfund_index_code": "CBA01103", "group": "中债指数"},
    "CBA00601.CS": {"name": "中债-新综合财富(总值)", "ttfund_index_code": "CBA00601", "group": "中债指数"},
    "CBA00603.CS": {"name": "中债-新综合全价(总值)", "ttfund_index_code": "CBA00603", "group": "中债指数"},
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="用天天基金 App 对比指数曲线补充全局指数行情表，不按策略单独维护对比指数。"
    )
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--index-code", action="append", choices=sorted(INDEX_CONFIG), help="全局指数代码，可重复传入。")
    parser.add_argument("--all", action="store_true", help="更新脚本内置的全部天天对比指数。")
    parser.add_argument("--anchor-strategy-id", default=DEFAULT_ANCHOR_STRATEGY_ID)
    parser.add_argument("--range", dest="range_code", default=DEFAULT_RANGE)
    parser.add_argument("--base-level", type=float, default=DEFAULT_BASE_LEVEL)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--run-id")
    return parser.parse_args()


def now_local() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(str(value).strip().replace("%", ""))
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS "指数日度行情" (
            "指数代码" TEXT NOT NULL,
            "指数名称" TEXT NOT NULL,
            "交易日期" TEXT NOT NULL,
            "开盘点位" REAL,
            "收盘点位" REAL,
            "最高点位" REAL,
            "最低点位" REAL,
            "日涨跌幅_百分比" REAL,
            "数据来源" TEXT NOT NULL,
            "采集时间" TEXT NOT NULL,
            PRIMARY KEY ("指数代码", "交易日期")
        )
        """
    )


def curve_params(anchor_strategy_id: str, ttfund_index_code: str, range_code: str) -> dict[str, str]:
    return {
        "product": "Fund",
        "appVersion": "6.6.19",
        "serverversion": "6.6.19",
        "version": "6.6.19",
        "plat": "Android",
        "indexCode": ttfund_index_code,
        "CODE": anchor_strategy_id,
        "RANGE": range_code,
    }


def fetch_index_curve(
    session: requests.Session,
    *,
    anchor_strategy_id: str,
    ttfund_index_code: str,
    range_code: str,
    timeout: float,
    retries: int,
) -> dict[str, Any]:
    params = curve_params(anchor_strategy_id, ttfund_index_code, range_code)
    url = requests.Request("GET", CURVE_API_URL, params=params).prepare().url or CURVE_API_URL
    last_error = ""
    for attempt in range(1, retries + 1):
        try:
            response = session.get(CURVE_API_URL, params=params, timeout=timeout)
            text = response.text.lstrip("\ufeff")
            payload = json.loads(text)
            data = payload.get("data") or payload.get("Data")
            rows = data if isinstance(data, list) else []
            return {
                "ok": response.status_code == 200 and bool(rows),
                "status_code": response.status_code,
                "url": url,
                "payload": payload,
                "rows": rows,
                "attempts": attempt,
                "error": None,
            }
        except Exception as exc:  # noqa: BLE001 - collection script should keep retry context.
            last_error = f"{type(exc).__name__}: {str(exc)[:300]}"
            if attempt < retries:
                time.sleep(0.5 * attempt)
    return {
        "ok": False,
        "status_code": None,
        "url": url,
        "payload": None,
        "rows": [],
        "attempts": retries,
        "error": last_error,
    }


def build_rows(
    *,
    canonical_code: str,
    index_name: str,
    ttfund_index_code: str,
    anchor_strategy_id: str,
    raw_rows: list[dict[str, Any]],
    base_level: float,
    collected_at: str,
) -> list[tuple[Any, ...]]:
    output: list[tuple[Any, ...]] = []
    previous_close: float | None = None
    source = f"天天基金fundIAAIChartPro对比指数(indexCode={ttfund_index_code};anchor={anchor_strategy_id};base={base_level:g})"
    for item in raw_rows:
        if not isinstance(item, dict):
            continue
        trade_date = str(item.get("PDATE") or "").strip()
        index_return_pct = to_float(item.get("indexSe"))
        if not trade_date or index_return_pct is None:
            continue
        close_value = round(base_level * (1.0 + index_return_pct / 100.0), 8)
        pct_change = 0.0
        if previous_close not in (None, 0):
            pct_change = round((close_value / previous_close - 1.0) * 100.0, 8)
        output.append(
            (
                canonical_code,
                index_name,
                trade_date,
                None,
                close_value,
                None,
                None,
                pct_change,
                source,
                collected_at,
            )
        )
        previous_close = close_value
    return output


def upsert_rows(conn: sqlite3.Connection, rows: list[tuple[Any, ...]]) -> None:
    conn.executemany(
        """
        INSERT OR REPLACE INTO "指数日度行情"
        ("指数代码", "指数名称", "交易日期", "开盘点位", "收盘点位", "最高点位", "最低点位", "日涨跌幅_百分比", "数据来源", "采集时间")
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def update_one(
    conn: sqlite3.Connection,
    session: requests.Session,
    *,
    canonical_code: str,
    anchor_strategy_id: str,
    range_code: str,
    base_level: float,
    timeout: float,
    retries: int,
    collected_at: str,
) -> dict[str, Any]:
    config = INDEX_CONFIG[canonical_code]
    result = fetch_index_curve(
        session,
        anchor_strategy_id=anchor_strategy_id,
        ttfund_index_code=config["ttfund_index_code"],
        range_code=range_code,
        timeout=timeout,
        retries=retries,
    )
    rows = build_rows(
        canonical_code=canonical_code,
        index_name=config["name"],
        ttfund_index_code=config["ttfund_index_code"],
        anchor_strategy_id=anchor_strategy_id,
        raw_rows=result["rows"],
        base_level=base_level,
        collected_at=collected_at,
    )
    if rows:
        upsert_rows(conn, rows)
        conn.commit()
    return {
        "指数代码": canonical_code,
        "指数名称": config["name"],
        "天天indexCode": config["ttfund_index_code"],
        "写入行数": len(rows),
        "开始日期": rows[0][2] if rows else None,
        "结束日期": rows[-1][2] if rows else None,
        "接口状态": result["status_code"],
        "尝试次数": result["attempts"],
        "错误": result["error"],
        "url": result["url"],
    }


def main() -> None:
    args = parse_args()
    index_codes = sorted(INDEX_CONFIG) if args.all or not args.index_code else args.index_code
    run_id = args.run_id or now_local().strftime("ttfund_global_index_%Y%m%d_%H%M%S")
    collected_at = now_local().isoformat(timespec="seconds")

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json,text/plain,*/*"})

    with sqlite3.connect(args.db_path) as conn:
        ensure_table(conn)
        results = [
            update_one(
                conn,
                session,
                canonical_code=index_code,
                anchor_strategy_id=args.anchor_strategy_id,
                range_code=args.range_code,
                base_level=args.base_level,
                timeout=args.timeout,
                retries=args.retries,
                collected_at=collected_at,
            )
            for index_code in index_codes
        ]

    output_dir = PROJECT_ROOT / "outputs" / "ttfund_global_index_quotes" / now_local().strftime("%Y-%m-%d") / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "run_id": run_id,
        "数据库": str(args.db_path),
        "锚点策略ID": args.anchor_strategy_id,
        "基准点位": args.base_level,
        "结果": results,
    }
    summary_path = output_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"summary_path": str(summary_path), **summary}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
