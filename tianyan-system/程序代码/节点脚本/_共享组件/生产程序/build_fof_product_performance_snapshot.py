from __future__ import annotations

import argparse
import json
import math
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
DEFAULT_SOURCE_JSON = PROJECT_ROOT / "outputs" / "fof_benchmark_ranking" / "latest_fof_benchmark_classified_ranking_data.json"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "fof_data_completeness"
TABLE_NAME = "FOF产品绩效快照"

INTERVALS = [
    ("上半年", "h1", "上半年收益率_百分比"),
    ("今年以来", "ytd", "今年以来收益率_百分比"),
    ("近1月", "1m", "近1月收益率_百分比"),
    ("近3月", "3m", "近3月收益率_百分比"),
    ("近6月", "6m", "近6月收益率_百分比"),
    ("近1年", "1y", "近1年收益率_百分比"),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build all-market FOF product performance snapshot into SQLite.")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--source-json", type=Path, default=DEFAULT_SOURCE_JSON)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--write-latest-nav", action="store_true", help="Upsert rankhandler latest NAV point into 基金日度净值.")
    parser.add_argument("--skip-fund-info", action="store_true", help="Do not upsert 基金信息/基金净值概况 from snapshot.")
    return parser.parse_args()


def clean(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text in {"None", "nan", "null", "--", "-"} else text


def as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    try:
        number = float(str(value).replace("%", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def round_or_none(value: Any, digits: int = 6) -> float | None:
    number = as_float(value)
    return round(number, digits) if number is not None else None


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def date_or_none(value: Any) -> date | None:
    text = clean(value)
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def interval_starts(end_date: date, h1_start: date) -> dict[str, date]:
    return {
        "上半年": h1_start,
        "今年以来": date(end_date.year - 1, 12, 31),
        "近1月": end_date - timedelta(days=30),
        "近3月": end_date - timedelta(days=90),
        "近6月": end_date - timedelta(days=183),
        "近1年": end_date - timedelta(days=365),
    }


def fetch_nav_series(conn: sqlite3.Connection, code: str, lower_bound: date, end_date: date) -> list[tuple[str, float]]:
    rows = conn.execute(
        """
        SELECT "交易日期", MAX(COALESCE("累计净值", "单位净值")) AS nav
        FROM "基金日度净值"
        WHERE "基金代码" = ?
          AND "交易日期" >= ?
          AND "交易日期" <= ?
          AND COALESCE("累计净值", "单位净值") IS NOT NULL
        GROUP BY "交易日期"
        ORDER BY "交易日期"
        """,
        (code, lower_bound.isoformat(), end_date.isoformat()),
    ).fetchall()
    out: list[tuple[str, float]] = []
    for trade_date, nav in rows:
        number = as_float(nav)
        if clean(trade_date) and number is not None and number > 0:
            out.append((str(trade_date)[:10], number))
    return out


def value_on_or_before(series: list[tuple[str, float]], target: date) -> tuple[str, float] | None:
    selected: tuple[str, float] | None = None
    target_text = target.isoformat()
    for trade_date, nav in series:
        if trade_date <= target_text:
            selected = (trade_date, nav)
        else:
            break
    return selected


def calc_risk(series: list[tuple[str, float]], start_date: date, end_date: date) -> dict[str, Any]:
    start = value_on_or_before(series, start_date)
    if not start:
        return {"maxDrawdown": None, "volatility": None, "navPointCount": 0, "riskStatus": "缺起始净值"}
    points: dict[str, float] = {start[0]: start[1]}
    end_text = end_date.isoformat()
    for trade_date, nav in series:
        if start[0] < trade_date <= end_text and nav > 0:
            points[trade_date] = nav
    ordered = sorted(points.items())
    if len(ordered) < 2:
        return {"maxDrawdown": None, "volatility": None, "navPointCount": len(ordered), "riskStatus": "净值点不足"}
    peak = ordered[0][1]
    max_drawdown = 0.0
    daily_returns: list[float] = []
    prev = ordered[0][1]
    for _, nav in ordered[1:]:
        if peak > 0:
            max_drawdown = min(max_drawdown, nav / peak - 1.0)
        peak = max(peak, nav)
        if prev > 0:
            daily_returns.append(nav / prev - 1.0)
        prev = nav
    volatility = None
    if len(daily_returns) >= 2:
        mean = sum(daily_returns) / len(daily_returns)
        variance = sum((item - mean) ** 2 for item in daily_returns) / (len(daily_returns) - 1)
        volatility = math.sqrt(variance) * math.sqrt(252) * 100.0
    return {
        "maxDrawdown": round(max_drawdown * 100.0, 6),
        "volatility": round(volatility, 6) if volatility is not None else None,
        "navPointCount": len(ordered),
        "riskStatus": "本地历史净值",
    }


def calc_interval_return(series: list[tuple[str, float]], start_date: date, end_date: date) -> float | None:
    start = value_on_or_before(series, start_date)
    end = value_on_or_before(series, end_date)
    if not start or not end or end[0] <= start[0] or start[1] <= 0:
        return None
    return round((end[1] / start[1] - 1.0) * 100.0, 6)


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS "{TABLE_NAME}" (
          "基金代码" TEXT PRIMARY KEY,
          "基金名称" TEXT,
          "基金公司" TEXT,
          "基金经理" TEXT,
          "天天基金细分类" TEXT,
          "天天基金大类" TEXT,
          "天天基金二级分类" TEXT,
          "是否QDII" INTEGER,
          "FOF公开分类" TEXT,
          "FOF基准细分分类" TEXT,
          "解析置信度" TEXT,
          "解析置信度分数" REAL,
          "业绩比较基准" TEXT,
          "F10基金类型" TEXT,
          "F10成立日期" TEXT,
          "基准权益权重_百分比" REAL,
          "基准风险资产权重" TEXT,
          "基准风险资产权重_百分比" REAL,
          "基准债券权重_百分比" REAL,
          "基准货币权重_百分比" REAL,
          "基准商品权重_百分比" REAL,
          "基准海外权重_百分比" REAL,
          "基准未知权重_百分比" REAL,
          "基准权重合计_百分比" REAL,
          "净值日期" TEXT,
          "单位净值" REAL,
          "累计净值" REAL,
          "上半年收益率_百分比" REAL,
          "今年以来收益率_百分比" REAL,
          "近1月收益率_百分比" REAL,
          "近3月收益率_百分比" REAL,
          "近6月收益率_百分比" REAL,
          "近1年收益率_百分比" REAL,
          "上半年最大回撤_百分比" REAL,
          "今年以来最大回撤_百分比" REAL,
          "近1月最大回撤_百分比" REAL,
          "近3月最大回撤_百分比" REAL,
          "近6月最大回撤_百分比" REAL,
          "近1年最大回撤_百分比" REAL,
          "上半年年化波动率_百分比" REAL,
          "今年以来年化波动率_百分比" REAL,
          "近1月年化波动率_百分比" REAL,
          "近3月年化波动率_百分比" REAL,
          "近6月年化波动率_百分比" REAL,
          "近1年年化波动率_百分比" REAL,
          "本地净值记录数" INTEGER,
          "本地净值起始日" TEXT,
          "本地净值截止日" TEXT,
          "收益数据状态" TEXT,
          "风险数据状态" TEXT,
          "数据来源" TEXT,
          "更新时间" TEXT
        )
        """
    )
    columns = {row[1] for row in conn.execute(f'PRAGMA table_info("{TABLE_NAME}")').fetchall()}
    if "基准风险资产权重" not in columns:
        conn.execute(f'ALTER TABLE "{TABLE_NAME}" ADD COLUMN "基准风险资产权重" TEXT')
    if "基准风险资产权重_百分比" not in columns:
        conn.execute(f'ALTER TABLE "{TABLE_NAME}" ADD COLUMN "基准风险资产权重_百分比" REAL')


def company_alias(value: Any) -> str:
    text = clean(value)
    suffixes = [
        "基金管理股份有限公司",
        "基金管理有限公司",
        "基金管理有限责任公司",
        "基金有限公司",
        "基金公司",
        "基金",
        "证券资产管理有限公司",
        "证券资产管理股份有限公司",
        "证券资管",
        "资产管理有限公司",
        "资产管理有限责任公司",
        "资产管理股份有限公司",
        "资产管理",
        "管理有限公司",
        "股份有限公司",
        "有限责任公司",
        "有限公司",
    ]
    for suffix in suffixes:
        if text.endswith(suffix):
            text = text[: -len(suffix)]
            break
    return text


def build_company_aliases(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    counts: dict[tuple[str, str], int] = {}
    for (company,) in conn.execute(
        """
        SELECT "基金公司"
        FROM "基金标准分类字典"
        WHERE NULLIF(TRIM("基金公司"), '') IS NOT NULL
        UNION ALL
        SELECT "基金公司"
        FROM "基金信息"
        WHERE NULLIF(TRIM("基金公司"), '') IS NOT NULL
        """
    ):
        canonical = clean(company)
        alias = company_alias(canonical)
        if len(alias) < 2:
            continue
        counts[(alias, canonical)] = counts.get((alias, canonical), 0) + 1
    best_by_alias: dict[str, tuple[str, int]] = {}
    for (alias, canonical), count in counts.items():
        current = best_by_alias.get(alias)
        if current is None or count > current[1]:
            best_by_alias[alias] = (canonical, count)
    return sorted(((alias, item[0]) for alias, item in best_by_alias.items()), key=lambda item: len(item[0]), reverse=True)


def infer_company_from_name(name: str, aliases: list[tuple[str, str]]) -> str:
    for alias, company in aliases:
        if name.startswith(alias):
            return company
    return ""


def upsert_fund_info(conn: sqlite3.Connection, row: dict[str, Any], generated_at: str, write_latest_nav: bool) -> None:
    code = clean(row.get("基金代码"))
    if not code:
        return
    fund_name = clean(row.get("基金名称")) or code
    fund_type = clean(row.get("F10基金类型")) or clean(row.get("天天基金细分类"))
    fund_company = clean(row.get("基金公司")) or None
    if fund_company:
        conn.execute(
            """
            UPDATE "基金标准分类字典"
            SET "基金公司" = COALESCE(NULLIF(TRIM("基金公司"), ''), ?)
            WHERE "基金代码" = ?
            """,
            (fund_company, code),
        )
    latest_nav = round_or_none(row.get("单位净值"))
    latest_nav_date = clean(row.get("净值日期"))
    conn.execute(
        """
        INSERT INTO "基金信息" ("基金代码", "基金名称", "基金公司", "基金类型", "最新净值", "最新净值日期", "数据来源", "最近更新时间")
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT("基金代码") DO UPDATE SET
            "基金名称"=COALESCE(NULLIF("基金信息"."基金名称", ''), excluded."基金名称"),
            "基金公司"=COALESCE(NULLIF("基金信息"."基金公司", ''), excluded."基金公司"),
            "基金类型"=COALESCE(NULLIF("基金信息"."基金类型", ''), excluded."基金类型"),
            "最新净值"=COALESCE(excluded."最新净值", "基金信息"."最新净值"),
            "最新净值日期"=COALESCE(excluded."最新净值日期", "基金信息"."最新净值日期"),
            "数据来源"=COALESCE("基金信息"."数据来源", excluded."数据来源"),
            "最近更新时间"=excluded."最近更新时间"
        """,
        (code, fund_name, fund_company, fund_type, latest_nav, latest_nav_date or None, "东方财富_rankhandler_FOF快照", generated_at),
    )
    if not write_latest_nav or not latest_nav_date or latest_nav is None:
        return
    conn.execute(
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
            "单位净值"=COALESCE(excluded."单位净值", "基金日度净值"."单位净值"),
            "累计净值"=COALESCE(excluded."累计净值", "基金日度净值"."累计净值"),
            "日收益率_百分比"=COALESCE(excluded."日收益率_百分比", "基金日度净值"."日收益率_百分比"),
            "数据来源"=COALESCE("基金日度净值"."数据来源", excluded."数据来源"),
            "采集时间"=excluded."采集时间"
        """,
        (
            code,
            latest_nav_date,
            fund_name,
            fund_type,
            fund_company,
            "rankhandler最新净值点",
            latest_nav,
            round_or_none(row.get("累计净值")),
            round_or_none(row.get("日涨幅_百分比")),
            None,
            None,
            None,
            0,
            "东方财富_rankhandler_FOF快照",
            f"fof_rankhandler_snapshot-{code}",
            generated_at,
        ),
    )


def build_snapshot(args: argparse.Namespace) -> dict[str, Any]:
    source = read_json(args.source_json)
    meta = source.get("meta") or {}
    end_date = date_or_none(meta.get("基金收益截止锚点")) or date_or_none(meta.get("策略收益截止锚点")) or date(2026, 6, 30)
    h1_start = date_or_none(meta.get("基金收益起始锚点")) or date(end_date.year - 1, 12, 31)
    starts = interval_starts(end_date, h1_start)
    lower_bound = min(starts.values()) - timedelta(days=45)
    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")

    conn = sqlite3.connect(args.db_path)
    conn.row_factory = sqlite3.Row
    ensure_table(conn)
    rows_out: list[dict[str, Any]] = []
    try:
        company_aliases = build_company_aliases(conn)
        for row in source.get("fofRows") or []:
            code = clean(row.get("基金代码"))
            name = clean(row.get("基金名称"))
            company = clean(row.get("基金公司")) or infer_company_from_name(name, company_aliases)
            nav_series = fetch_nav_series(conn, code, lower_bound, end_date) if code else []
            risks = {label: calc_risk(nav_series, starts[label], end_date) for label, _key, _field in INTERVALS}
            local_returns = {
                label: calc_interval_return(nav_series, starts[label], end_date)
                for label, _key, _field in INTERVALS
            }
            risk_ok = sum(1 for item in risks.values() if item.get("maxDrawdown") is not None)
            return_ok = sum(
                1
                for label, _key, field in INTERVALS
                if as_float(row.get(field)) is not None or local_returns.get(label) is not None
            )
            snapshot = {
                "基金代码": code,
                "基金名称": name,
                "基金公司": company,
                "基金经理": clean(row.get("基金经理")),
                "天天基金细分类": clean(row.get("天天基金细分类")),
                "天天基金大类": clean(row.get("天天基金大类")),
                "天天基金二级分类": clean(row.get("天天基金二级分类")),
                "是否QDII": int(as_float(row.get("是否QDII")) or 0),
                "FOF公开分类": clean(row.get("FOF公开分类")),
                "FOF基准细分分类": clean(row.get("FOF基准细分分类")),
                "解析置信度": clean(row.get("解析置信度")),
                "解析置信度分数": round_or_none(row.get("解析置信度分数")),
                "业绩比较基准": clean(row.get("业绩比较基准")),
                "F10基金类型": clean(row.get("F10基金类型")),
                "F10成立日期": clean(row.get("F10成立日期")) or clean(row.get("成立日期")),
                "基准权益权重_百分比": round_or_none(row.get("基准权益权重_百分比")),
                "基准风险资产权重": clean(row.get("基准风险资产权重")) or clean(row.get("FOF基准风险资产权重")),
                "基准风险资产权重_百分比": round_or_none(row.get("基准风险资产权重_百分比")) if round_or_none(row.get("基准风险资产权重_百分比")) is not None else round_or_none((as_float(row.get("基准权益权重_百分比")) or 0.0) + (as_float(row.get("基准商品权重_百分比")) or 0.0)),
                "基准债券权重_百分比": round_or_none(row.get("基准债券权重_百分比")),
                "基准货币权重_百分比": round_or_none(row.get("基准货币权重_百分比")),
                "基准商品权重_百分比": round_or_none(row.get("基准商品权重_百分比")),
                "基准海外权重_百分比": round_or_none(row.get("基准海外权重_百分比")),
                "基准未知权重_百分比": round_or_none(row.get("基准未知权重_百分比")),
                "基准权重合计_百分比": round_or_none(row.get("基准权重合计_百分比")),
                "净值日期": clean(row.get("净值日期")),
                "单位净值": round_or_none(row.get("单位净值")),
                "累计净值": round_or_none(row.get("累计净值")),
                "本地净值记录数": len(nav_series),
                "本地净值起始日": nav_series[0][0] if nav_series else "",
                "本地净值截止日": nav_series[-1][0] if nav_series else "",
                "收益数据状态": "有区间收益" if return_ok else "缺区间收益",
                "风险数据状态": "有历史净值风险指标" if risk_ok else "缺历史净值，无法计算波动/回撤",
                "数据来源": "FOF基准细分排名JSON+FOF F10基准+本地基金日度净值",
                "更新时间": generated_at,
            }
            for label, _key, field in INTERVALS:
                source_return = round_or_none(row.get(field))
                snapshot[field] = source_return if source_return is not None else local_returns.get(label)
                snapshot[f"{label}最大回撤_百分比"] = risks[label].get("maxDrawdown")
                snapshot[f"{label}年化波动率_百分比"] = risks[label].get("volatility")
            rows_out.append(snapshot)
            upsert_fund_info(conn, snapshot, generated_at, args.write_latest_nav and not args.skip_fund_info)

        columns = list(rows_out[0].keys()) if rows_out else []
        placeholders = ",".join("?" for _ in columns)
        update_sql = ", ".join(f'"{col}"=excluded."{col}"' for col in columns if col != "基金代码")
        conn.executemany(
            f"""
            INSERT INTO "{TABLE_NAME}" ({",".join(f'"{col}"' for col in columns)})
            VALUES ({placeholders})
            ON CONFLICT("基金代码") DO UPDATE SET {update_sql}
            """,
            [[row.get(col) for col in columns] for row in rows_out],
        )
        conn.commit()
    finally:
        conn.close()

    output_dir = args.output_root / datetime.now().strftime("%Y%m%dT%H%M%S_fof_snapshot")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_json = output_dir / "fof_product_performance_snapshot.json"
    summary_json = output_dir / "summary.json"
    output_json.write_text(json.dumps(rows_out, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {
        "generated_at": generated_at,
        "db_path": str(args.db_path),
        "source_json": str(args.source_json),
        "output_dir": str(output_dir),
        "table": TABLE_NAME,
        "fof_count": len(rows_out),
        "return_any_count": sum(1 for row in rows_out if row.get("收益数据状态") == "有区间收益"),
        "risk_any_count": sum(1 for row in rows_out if row.get("风险数据状态") == "有历史净值风险指标"),
        "missing_risk_count": sum(1 for row in rows_out if row.get("风险数据状态") != "有历史净值风险指标"),
        "benchmark_equity_bucket_count": sum(1 for row in rows_out if clean(row.get("基准风险资产权重"))),
        "latest_nav_point_count": sum(1 for row in rows_out if clean(row.get("净值日期")) and row.get("单位净值") is not None),
        "write_latest_nav": bool(args.write_latest_nav),
        "snapshot_json": str(output_json),
    }
    summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    args = parse_args()
    summary = build_snapshot(args)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
