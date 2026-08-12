import json
import sqlite3
from pathlib import Path


ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DB_PATH = ROOT / "data" / "analysis_zh_current.sqlite"
FUND_DICT = "基金标准分类字典"


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    def one(sql: str) -> dict:
        return dict(conn.execute(sql).fetchone())

    def many(sql: str) -> list[dict]:
        return [dict(row) for row in conn.execute(sql).fetchall()]

    result = {
        "strategy_base": one(
            '''
            SELECT
              COUNT(*) AS strategies,
              SUM(CASE WHEN NULLIF(TRIM("策略类型"), '') IS NOT NULL THEN 1 ELSE 0 END) AS has_strategy_type,
              SUM(CASE WHEN NULLIF(TRIM("风险等级"), '') IS NOT NULL THEN 1 ELSE 0 END) AS has_risk_level,
              SUM(CASE WHEN NULLIF(TRIM("标签JSON"), '') IS NOT NULL AND "标签JSON" <> '[]' THEN 1 ELSE 0 END) AS has_strategy_tags,
              SUM(CASE WHEN NULLIF(TRIM("业绩基准"), '') IS NOT NULL THEN 1 ELSE 0 END) AS has_benchmark_text
            FROM "策略信息"
            '''
        ),
        "current_holding_rows": one(
            f'''
            SELECT
              COUNT(*) AS rows,
              COUNT(DISTINCT h."统一策略ID") AS strategies,
              SUM(CASE WHEN NULLIF(TRIM(h."基金代码"), '') IS NOT NULL THEN 1 ELSE 0 END) AS rows_with_code,
              SUM(CASE WHEN d."基金代码" IS NOT NULL THEN 1 ELSE 0 END) AS rows_matched_dict,
              SUM(CASE WHEN d."天天基金细分类" IS NOT NULL AND d."天天基金细分类" NOT IN ('其他','未匹配') THEN 1 ELSE 0 END) AS rows_effective_type,
              SUM(CASE WHEN h."基金权重_百分比" IS NOT NULL THEN 1 ELSE 0 END) AS rows_with_weight,
              SUM(CASE WHEN h."是否精确权重"=1 THEN 1 ELSE 0 END) AS rows_exact_weight
            FROM "策略当前持仓" h
            LEFT JOIN "{FUND_DICT}" d ON d."基金代码" = h."基金代码"
            '''
        ),
        "current_holding_strategy_coverage": one(
            f'''
            WITH s AS (
              SELECT
                h."统一策略ID",
                COUNT(*) AS fund_rows,
                SUM(CASE WHEN d."天天基金细分类" IS NOT NULL AND d."天天基金细分类" NOT IN ('其他','未匹配') THEN 1 ELSE 0 END) AS typed_rows,
                SUM(COALESCE(h."基金权重_百分比", 0)) AS weight_sum,
                SUM(CASE WHEN d."天天基金细分类" IS NOT NULL AND d."天天基金细分类" NOT IN ('其他','未匹配')
                         THEN COALESCE(h."基金权重_百分比", 0) ELSE 0 END) AS typed_weight,
                SUM(CASE WHEN h."基金权重_百分比" IS NULL THEN 1 ELSE 0 END) AS null_weight_rows,
                SUM(CASE WHEN h."是否精确权重"=1 THEN 1 ELSE 0 END) AS exact_weight_rows
              FROM "策略当前持仓" h
              LEFT JOIN "{FUND_DICT}" d ON d."基金代码" = h."基金代码"
              GROUP BY h."统一策略ID"
            )
            SELECT
              COUNT(*) AS strategies_with_current_holdings,
              SUM(CASE WHEN typed_rows = fund_rows THEN 1 ELSE 0 END) AS all_rows_typed,
              SUM(CASE WHEN weight_sum > 0 AND typed_weight / weight_sum >= 0.95 THEN 1 ELSE 0 END) AS typed_weight_ge_95pct,
              SUM(CASE WHEN null_weight_rows = 0 THEN 1 ELSE 0 END) AS all_rows_have_weight,
              SUM(CASE WHEN exact_weight_rows = fund_rows THEN 1 ELSE 0 END) AS all_rows_exact_weight,
              SUM(CASE WHEN weight_sum BETWEEN 98 AND 102 THEN 1 ELSE 0 END) AS weight_sum_98_102
            FROM s
            '''
        ),
        "imputed_current_holding_rows": one(
            f'''
            SELECT
              COUNT(*) AS rows,
              COUNT(DISTINCT h."统一策略ID") AS strategies,
              SUM(CASE WHEN h."推算基金权重_百分比" IS NOT NULL THEN 1 ELSE 0 END) AS rows_with_weight,
              SUM(CASE WHEN d."基金代码" IS NOT NULL THEN 1 ELSE 0 END) AS rows_matched_dict,
              SUM(CASE WHEN d."天天基金细分类" IS NOT NULL AND d."天天基金细分类" NOT IN ('其他','未匹配') THEN 1 ELSE 0 END) AS rows_effective_type
            FROM "策略当前持仓推算补齐" h
            LEFT JOIN "{FUND_DICT}" d ON d."基金代码" = h."基金代码"
            '''
        ),
        "imputed_current_holding_strategy_coverage": one(
            f'''
            WITH s AS (
              SELECT
                h."统一策略ID",
                COUNT(*) AS fund_rows,
                SUM(CASE WHEN d."天天基金细分类" IS NOT NULL AND d."天天基金细分类" NOT IN ('其他','未匹配') THEN 1 ELSE 0 END) AS typed_rows,
                SUM(COALESCE(h."推算基金权重_百分比", 0)) AS weight_sum,
                SUM(CASE WHEN d."天天基金细分类" IS NOT NULL AND d."天天基金细分类" NOT IN ('其他','未匹配')
                         THEN COALESCE(h."推算基金权重_百分比", 0) ELSE 0 END) AS typed_weight
              FROM "策略当前持仓推算补齐" h
              LEFT JOIN "{FUND_DICT}" d ON d."基金代码" = h."基金代码"
              GROUP BY h."统一策略ID"
            )
            SELECT
              COUNT(*) AS strategies,
              SUM(CASE WHEN typed_rows = fund_rows THEN 1 ELSE 0 END) AS all_rows_typed,
              SUM(CASE WHEN weight_sum BETWEEN 98 AND 102 THEN 1 ELSE 0 END) AS weight_sum_98_102,
              SUM(CASE WHEN weight_sum > 0 AND typed_weight / weight_sum >= 0.95 THEN 1 ELSE 0 END) AS typed_weight_ge_95pct
            FROM s
            '''
        ),
        "latest_holding_audit": one(
            '''
            SELECT
              COUNT(*) AS strategies,
              SUM(CASE WHEN "是否已有当前持仓"=1 THEN 1 ELSE 0 END) AS has_current_holding,
              SUM(CASE WHEN "是否可推算补齐"=1 THEN 1 ELSE 0 END) AS can_impute,
              SUM(CASE WHEN "当前权重和_百分比" BETWEEN 98 AND 102 THEN 1 ELSE 0 END) AS current_weight_sum_98_102,
              SUM(CASE WHEN "推算权重和_百分比" BETWEEN 98 AND 102 THEN 1 ELSE 0 END) AS imputed_weight_sum_98_102
            FROM "最新持仓推算稽核策略汇总"
            '''
        ),
        "rebalance_rows": one(
            f'''
            SELECT
              COUNT(*) AS rows,
              COUNT(DISTINCT h."统一策略ID") AS strategies,
              COUNT(DISTINCT h."调仓事件ID") AS events,
              SUM(CASE WHEN NULLIF(TRIM(h."基金代码"), '') IS NOT NULL THEN 1 ELSE 0 END) AS rows_with_code,
              SUM(CASE WHEN d."基金代码" IS NOT NULL THEN 1 ELSE 0 END) AS rows_matched_dict,
              SUM(CASE WHEN d."天天基金细分类" IS NOT NULL AND d."天天基金细分类" NOT IN ('其他','未匹配') THEN 1 ELSE 0 END) AS rows_effective_type,
              SUM(CASE WHEN h."调后权重_百分比" IS NOT NULL THEN 1 ELSE 0 END) AS rows_with_after_weight,
              SUM(CASE WHEN h."权重变化_百分比" IS NOT NULL THEN 1 ELSE 0 END) AS rows_with_weight_change
            FROM "策略调仓明细" h
            LEFT JOIN "{FUND_DICT}" d ON d."基金代码" = h."基金代码"
            '''
        ),
        "rebalance_strategy_coverage": one(
            f'''
            WITH s AS (
              SELECT
                h."统一策略ID",
                COUNT(*) AS rows,
                SUM(CASE WHEN d."天天基金细分类" IS NOT NULL AND d."天天基金细分类" NOT IN ('其他','未匹配') THEN 1 ELSE 0 END) AS typed_rows,
                SUM(CASE WHEN h."调后权重_百分比" IS NULL THEN 1 ELSE 0 END) AS null_after_weight_rows,
                COUNT(DISTINCT h."调仓事件ID") AS events
              FROM "策略调仓明细" h
              LEFT JOIN "{FUND_DICT}" d ON d."基金代码" = h."基金代码"
              GROUP BY h."统一策略ID"
            )
            SELECT
              COUNT(*) AS strategies_with_rebalance,
              SUM(CASE WHEN typed_rows = rows THEN 1 ELSE 0 END) AS all_rebalance_rows_typed,
              SUM(CASE WHEN null_after_weight_rows = 0 THEN 1 ELSE 0 END) AS all_rebalance_rows_have_after_weight,
              SUM(CASE WHEN events >= 2 THEN 1 ELSE 0 END) AS strategies_with_at_least_2_events
            FROM s
            '''
        ),
        "performance_data": one(
            '''
            SELECT
              (SELECT COUNT(DISTINCT "统一策略ID") FROM "策略标准业绩净值") AS strategies_standard_nav,
              (SELECT COUNT(*) FROM "策略标准业绩净值") AS standard_nav_rows,
              (SELECT COUNT(DISTINCT "统一策略ID") FROM "策略日度业绩") AS strategies_official_daily,
              (SELECT COUNT(*) FROM "策略日度业绩") AS official_daily_rows,
              (SELECT COUNT(DISTINCT "统一策略ID") FROM "策略产品披露净值") AS strategies_disclosed_nav,
              (SELECT COUNT(*) FROM "策略产品披露净值") AS disclosed_nav_rows,
              (SELECT COUNT(DISTINCT "统一策略ID") FROM "策略披露风险指标") AS strategies_disclosed_risk,
              (SELECT COUNT(*) FROM "策略披露风险指标") AS disclosed_risk_rows
            '''
        ),
        "simulation_quality": one(
            '''
            SELECT
              COUNT(*) AS strategies,
              SUM(CASE WHEN "是否纳入模拟"=1 THEN 1 ELSE 0 END) AS included_simulation,
              SUM(CASE WHEN "模拟交易日数" >= 120 THEN 1 ELSE 0 END) AS sim_days_ge_120,
              SUM(CASE WHEN "模拟交易日数" >= 250 THEN 1 ELSE 0 END) AS sim_days_ge_250,
              SUM(CASE WHEN "质量等级" LIKE '%完整%' THEN 1 ELSE 0 END) AS quality_complete_like
            FROM "策略模拟净值质量"
            '''
        ),
        "fund_dictionary_current": one(
            f'''
            SELECT
              COUNT(*) AS current_funds,
              SUM(CASE WHEN "天天基金细分类" IS NOT NULL THEN 1 ELSE 0 END) AS has_type,
              SUM(CASE WHEN "天天基金细分类" IS NOT NULL AND "天天基金细分类" NOT IN ('其他','未匹配') THEN 1 ELSE 0 END) AS effective_type,
              SUM(CASE WHEN NULLIF(TRIM("基金公司"), '') IS NOT NULL THEN 1 ELSE 0 END) AS has_company,
              SUM(CASE WHEN "主题标签JSON" IS NOT NULL AND "主题标签JSON" <> '[]' THEN 1 ELSE 0 END) AS has_theme_tags,
              SUM(CASE WHEN NULLIF(TRIM("跟踪指数_名称推断"), '') IS NOT NULL THEN 1 ELSE 0 END) AS inferred_tracking_index
            FROM "{FUND_DICT}"
            WHERE "是否当前库使用"=1
            '''
        ),
        "fund_bucket_current": many(
            f'''
            SELECT "投顾资产分类桶" AS bucket, COUNT(*) AS funds
            FROM "{FUND_DICT}"
            WHERE "是否当前库使用"=1
            GROUP BY "投顾资产分类桶"
            ORDER BY funds DESC
            '''
        ),
        "strategy_type_top": many(
            '''
            SELECT COALESCE(NULLIF(TRIM("策略类型"), ''), '未披露') AS strategy_type, COUNT(*) AS strategies
            FROM "策略信息"
            GROUP BY strategy_type
            ORDER BY strategies DESC
            LIMIT 20
            '''
        ),
        "risk_level_top": many(
            '''
            SELECT COALESCE(NULLIF(TRIM("风险等级"), ''), '未披露') AS risk_level, COUNT(*) AS strategies
            FROM "策略信息"
            GROUP BY risk_level
            ORDER BY strategies DESC
            LIMIT 20
            '''
        ),
        "disclosed_risk_interval_top": many(
            '''
            SELECT COALESCE(NULLIF(TRIM("区间名称"), ''), "区间代码", '未披露') AS interval_name, COUNT(*) AS rows
            FROM "策略披露风险指标"
            GROUP BY interval_name
            ORDER BY rows DESC
            '''
        ),
    }
    conn.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
