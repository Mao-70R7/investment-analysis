import json
import re
import sqlite3
from pathlib import Path
from typing import Any


ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DB_PATH = ROOT / "data" / "analysis_zh_current.sqlite"


def norm_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def parse_fee_rate_pct(value: Any) -> float | None:
    """Return annual advisory fee rate in percent, e.g. 0.5 for 年化0.5%."""
    text = norm_text(value)
    if not text:
        return None
    lowered = text.lower()
    if lowered in {"0", "0.0", "0%", "免费", "免投顾费", "无", "不收取"}:
        return 0.0
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*%", text)
    if match:
        return float(match.group(1))
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)", text)
    if match:
        value_num = float(match.group(1))
        # Values greater than 1 are usually already percent; small decimals are usually ratios.
        return value_num if value_num > 1 else value_num * 100
    return None


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    def one(sql: str) -> dict:
        return dict(conn.execute(sql).fetchone())

    def many(sql: str) -> list[dict]:
        return [dict(row) for row in conn.execute(sql).fetchall()]

    strategies = [dict(row) for row in conn.execute('SELECT * FROM "策略信息"')]
    fee_stats_by_channel: dict[str, dict[str, Any]] = {}
    for row in strategies:
        channel = row.get("渠道ID") or "未知"
        stats = fee_stats_by_channel.setdefault(
            channel,
            {
                "strategies": 0,
                "has_fee_text": 0,
                "parsed_fee": 0,
                "missing_fee": 0,
                "fee_values": {},
            },
        )
        stats["strategies"] += 1
        fee_text = norm_text(row.get("投顾费率"))
        parsed = parse_fee_rate_pct(fee_text)
        if fee_text:
            stats["has_fee_text"] += 1
            stats["fee_values"][fee_text] = stats["fee_values"].get(fee_text, 0) + 1
        if parsed is not None:
            stats["parsed_fee"] += 1
        else:
            stats["missing_fee"] += 1
    for stats in fee_stats_by_channel.values():
        stats["fee_values"] = [
            {"fee_text": key, "strategies": value}
            for key, value in sorted(stats["fee_values"].items(), key=lambda item: (-item[1], item[0]))[:20]
        ]

    result = {
        "strategy_info_overall": one(
            '''
            SELECT
              COUNT(*) AS strategies,
              SUM(CASE WHEN NULLIF(TRIM("投顾费率"), '') IS NOT NULL THEN 1 ELSE 0 END) AS has_fee_text,
              SUM(CASE WHEN NULLIF(TRIM("业绩基准"), '') IS NOT NULL THEN 1 ELSE 0 END) AS has_benchmark_text,
              SUM(CASE WHEN NULLIF(TRIM("策略类型"), '') IS NOT NULL THEN 1 ELSE 0 END) AS has_strategy_type,
              SUM(CASE WHEN NULLIF(TRIM("风险等级"), '') IS NOT NULL THEN 1 ELSE 0 END) AS has_risk_level
            FROM "策略信息"
            '''
        ),
        "strategy_info_by_channel": many(
            '''
            SELECT
              s."渠道ID" AS channel_id,
              COALESCE(c."渠道名称", s."渠道ID") AS channel_name,
              COUNT(*) AS strategies,
              SUM(CASE WHEN NULLIF(TRIM(s."投顾费率"), '') IS NOT NULL THEN 1 ELSE 0 END) AS has_fee_text,
              SUM(CASE WHEN NULLIF(TRIM(s."业绩基准"), '') IS NOT NULL THEN 1 ELSE 0 END) AS has_benchmark_text,
              SUM(CASE WHEN NULLIF(TRIM(s."策略类型"), '') IS NOT NULL THEN 1 ELSE 0 END) AS has_strategy_type,
              SUM(CASE WHEN NULLIF(TRIM(s."风险等级"), '') IS NOT NULL THEN 1 ELSE 0 END) AS has_risk_level
            FROM "策略信息" s
            LEFT JOIN "渠道信息" c ON c."渠道ID" = s."渠道ID"
            GROUP BY s."渠道ID", channel_name
            ORDER BY strategies DESC
            '''
        ),
        "benchmark_curve_coverage": one(
            '''
            SELECT
              (SELECT COUNT(DISTINCT "统一策略ID") FROM "策略产品披露净值" WHERE "基准收益率_百分比" IS NOT NULL) AS disclosed_nav_benchmark_strategies,
              (SELECT COUNT(*) FROM "策略产品披露净值" WHERE "基准收益率_百分比" IS NOT NULL) AS disclosed_nav_benchmark_rows,
              (SELECT COUNT(DISTINCT "统一策略ID") FROM "策略日度业绩" WHERE "基准收益率_百分比" IS NOT NULL) AS official_daily_benchmark_strategies,
              (SELECT COUNT(*) FROM "策略日度业绩" WHERE "基准收益率_百分比" IS NOT NULL) AS official_daily_benchmark_rows,
              (SELECT COUNT(DISTINCT "统一策略ID") FROM "策略区间业绩" WHERE "基准收益率_百分比" IS NOT NULL) AS interval_benchmark_strategies,
              (SELECT COUNT(*) FROM "策略区间业绩" WHERE "基准收益率_百分比" IS NOT NULL) AS interval_benchmark_rows
            '''
        ),
        "benchmark_curve_by_channel": many(
            '''
            WITH b AS (
              SELECT "统一策略ID", COUNT(*) AS rows
              FROM "策略产品披露净值"
              WHERE "基准收益率_百分比" IS NOT NULL
              GROUP BY "统一策略ID"
              UNION ALL
              SELECT "统一策略ID", COUNT(*) AS rows
              FROM "策略日度业绩"
              WHERE "基准收益率_百分比" IS NOT NULL
              GROUP BY "统一策略ID"
            ),
            per_strategy AS (
              SELECT "统一策略ID", SUM(rows) AS benchmark_rows
              FROM b
              GROUP BY "统一策略ID"
            )
            SELECT
              s."渠道ID" AS channel_id,
              COALESCE(c."渠道名称", s."渠道ID") AS channel_name,
              COUNT(*) AS strategies,
              SUM(CASE WHEN p.benchmark_rows IS NOT NULL THEN 1 ELSE 0 END) AS has_daily_benchmark_curve,
              SUM(COALESCE(p.benchmark_rows, 0)) AS benchmark_rows
            FROM "策略信息" s
            LEFT JOIN per_strategy p ON p."统一策略ID" = s."统一策略ID"
            LEFT JOIN "渠道信息" c ON c."渠道ID" = s."渠道ID"
            GROUP BY s."渠道ID", channel_name
            ORDER BY strategies DESC
            '''
        ),
        "simulation_fee_quality": one(
            '''
            SELECT
              COUNT(*) AS strategies,
              SUM(CASE WHEN "是否纳入模拟"=1 THEN 1 ELSE 0 END) AS included_simulation,
              SUM(CASE WHEN "缺失投顾费率按0处理"=1 THEN 1 ELSE 0 END) AS missing_fee_treated_as_zero,
              SUM(CASE WHEN "投顾费率_年化_百分比" IS NOT NULL THEN 1 ELSE 0 END) AS has_annual_fee_pct,
              AVG("投顾费率_年化_百分比") AS avg_annual_fee_pct
            FROM "策略模拟净值质量"
            '''
        ),
        "simulation_fee_by_channel": many(
            '''
            SELECT
              q."渠道ID" AS channel_id,
              COALESCE(c."渠道名称", q."渠道ID") AS channel_name,
              COUNT(*) AS strategies,
              SUM(CASE WHEN q."是否纳入模拟"=1 THEN 1 ELSE 0 END) AS included_simulation,
              SUM(CASE WHEN q."缺失投顾费率按0处理"=1 THEN 1 ELSE 0 END) AS missing_fee_treated_as_zero,
              SUM(CASE WHEN q."投顾费率_年化_百分比" IS NOT NULL THEN 1 ELSE 0 END) AS has_annual_fee_pct,
              ROUND(AVG(q."投顾费率_年化_百分比"), 6) AS avg_annual_fee_pct
            FROM "策略模拟净值质量" q
            LEFT JOIN "渠道信息" c ON c."渠道ID" = q."渠道ID"
            GROUP BY q."渠道ID", channel_name
            ORDER BY strategies DESC
            '''
        ),
        "benchmark_text_examples": many(
            '''
            SELECT "渠道ID" AS channel_id, "策略名称" AS strategy_name, "业绩基准" AS benchmark
            FROM "策略信息"
            WHERE NULLIF(TRIM("业绩基准"), '') IS NOT NULL
            ORDER BY "渠道ID", "策略名称"
            LIMIT 30
            '''
        ),
        "fee_text_examples": many(
            '''
            SELECT "渠道ID" AS channel_id, "策略名称" AS strategy_name, "投顾费率" AS fee
            FROM "策略信息"
            WHERE NULLIF(TRIM("投顾费率"), '') IS NOT NULL
            ORDER BY "渠道ID", "策略名称"
            LIMIT 30
            '''
        ),
        "fee_parse_by_channel": fee_stats_by_channel,
    }
    conn.close()

    output_dir = ROOT / "outputs" / "basic_data_readiness"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "strategy_benchmark_fee_audit.json"
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output_path": str(output_path), **result}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
