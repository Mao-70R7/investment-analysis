import json
import sqlite3
from pathlib import Path


ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DB_PATH = ROOT / "data" / "analysis_zh_current.sqlite"
TABLE_NAME = "基金标准分类字典"


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    def one(sql: str) -> dict:
        return dict(conn.execute(sql).fetchone())

    def many(sql: str) -> list[dict]:
        return [dict(row) for row in conn.execute(sql).fetchall()]

    result = {
        "dictionary_coverage": one(
            f'''
            SELECT
              COUNT(*) AS total,
              SUM(CASE WHEN "是否当前库使用"=1 THEN 1 ELSE 0 END) AS current_used,
              SUM(CASE WHEN NULLIF(TRIM("天天基金细分类"), '') IS NOT NULL THEN 1 ELSE 0 END) AS has_type,
              SUM(CASE WHEN NULLIF(TRIM("基金公司"), '') IS NOT NULL THEN 1 ELSE 0 END) AS has_company,
              SUM(CASE WHEN NULLIF(TRIM("基金经理"), '') IS NOT NULL THEN 1 ELSE 0 END) AS has_manager,
              SUM(CASE WHEN "主题标签JSON" IS NOT NULL AND "主题标签JSON" <> '[]' THEN 1 ELSE 0 END) AS has_theme_tags
            FROM "{TABLE_NAME}"
            '''
        ),
        "current_coverage": one(
            f'''
            SELECT
              COUNT(*) AS total,
              SUM(CASE WHEN NULLIF(TRIM("天天基金细分类"), '') IS NOT NULL THEN 1 ELSE 0 END) AS has_type,
              SUM(CASE WHEN "天天基金细分类"='未披露' THEN 1 ELSE 0 END) AS type_undisclosed,
              SUM(CASE WHEN "天天基金细分类"='其他' THEN 1 ELSE 0 END) AS type_other,
              SUM(CASE WHEN NULLIF(TRIM("天天基金细分类"), '') IS NOT NULL
                         AND "天天基金细分类" NOT IN ('未披露','其他') THEN 1 ELSE 0 END) AS has_effective_type,
              SUM(CASE WHEN NULLIF(TRIM("基金公司"), '') IS NOT NULL THEN 1 ELSE 0 END) AS has_company,
              SUM(CASE WHEN NULLIF(TRIM("基金经理"), '') IS NOT NULL THEN 1 ELSE 0 END) AS has_manager,
              SUM(CASE WHEN "主题标签JSON" IS NOT NULL AND "主题标签JSON" <> '[]' THEN 1 ELSE 0 END) AS has_theme_tags,
              SUM(CASE WHEN NULLIF(TRIM("跟踪指数_名称推断"), '') IS NOT NULL THEN 1 ELSE 0 END) AS has_tracking_index_inferred
            FROM "{TABLE_NAME}"
            WHERE "是否当前库使用"=1
            '''
        ),
        "fund_info_after_backfill": one(
            '''
            SELECT
              COUNT(*) AS total,
              SUM(CASE WHEN NULLIF(TRIM("基金类型"), '') IS NOT NULL THEN 1 ELSE 0 END) AS has_type,
              SUM(CASE WHEN NULLIF(TRIM("基金公司"), '') IS NOT NULL THEN 1 ELSE 0 END) AS has_company,
              SUM(CASE WHEN NULLIF(TRIM("跟踪指数"), '') IS NOT NULL THEN 1 ELSE 0 END) AS has_tracking_index,
              SUM(CASE WHEN "主题标签JSON" IS NOT NULL AND "主题标签JSON" <> '[]' THEN 1 ELSE 0 END) AS has_theme_tags
            FROM "基金信息"
            '''
        ),
        "advisor_bucket_counts": many(
            f'''
            SELECT "投顾资产分类桶" AS bucket, COUNT(*) AS n
            FROM "{TABLE_NAME}"
            WHERE "是否当前库使用"=1
            GROUP BY "投顾资产分类桶"
            ORDER BY n DESC
            '''
        ),
        "region_counts": many(
            f'''
            SELECT "市场地域标签" AS region, COUNT(*) AS n
            FROM "{TABLE_NAME}"
            WHERE "是否当前库使用"=1
            GROUP BY "市场地域标签"
            ORDER BY n DESC
            '''
        ),
        "active_passive_counts": many(
            f'''
            SELECT "主动被动标签" AS active_passive, COUNT(*) AS n
            FROM "{TABLE_NAME}"
            WHERE "是否当前库使用"=1
            GROUP BY "主动被动标签"
            ORDER BY n DESC
            '''
        ),
        "flag_counts": one(
            f'''
            SELECT
              SUM("是否货币基金") AS money,
              SUM("是否债券基金") AS bond,
              SUM("是否权益基金") AS equity,
              SUM("是否混合基金") AS mixed,
              SUM("是否指数基金") AS index_funds,
              SUM("是否ETF") AS etf,
              SUM("是否ETF联接") AS etf_link,
              SUM("是否指数增强") AS enhanced_index,
              SUM("是否QDII") AS qdii,
              SUM("是否FOF") AS fof,
              SUM("是否REITs") AS reits,
              SUM("是否商品黄金") AS commodity
            FROM "{TABLE_NAME}"
            WHERE "是否当前库使用"=1
            '''
        ),
        "missing_company_examples": many(
            f'''
            SELECT "基金代码" AS code, "标准基金名称" AS name, "天天基金细分类" AS type, "分类来源" AS class_source
            FROM "{TABLE_NAME}"
            WHERE "是否当前库使用"=1 AND NULLIF(TRIM("基金公司"), '') IS NULL
            ORDER BY "基金代码"
            LIMIT 20
            '''
        ),
        "missing_or_weak_type_examples": many(
            f'''
            SELECT "基金代码" AS code, "标准基金名称" AS name, "天天基金细分类" AS type,
                   "投顾资产分类桶" AS bucket, "分类来源" AS class_source
            FROM "{TABLE_NAME}"
            WHERE "是否当前库使用"=1
              AND (NULLIF(TRIM("天天基金细分类"), '') IS NULL OR "天天基金细分类" IN ('未披露','其他'))
            ORDER BY "基金代码"
            LIMIT 30
            '''
        ),
    }
    conn.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
