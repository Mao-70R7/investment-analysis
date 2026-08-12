from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
DEFAULT_SITE_DIR = PROJECT_ROOT / "site" / "basic_data"
REFINED_CHANNELS = ("ttfund", "gffunds", "zocaifu", "gfsec_fima")
CONFIRMED_SIGNAL_STRATEGY_IDS = (
    "ttfund__91NE3OR",
    "ttfund__LF94Q2M",
    "zocaifu__8100000905",
    "gffunds__GFJJ000219",
    "ttfund__YGKZ97T",
    "gffunds__GFJJ001221",
    "ttfund__QOU1RYF",
    "ttfund__IWNVGBF",
    "ttfund__QZOUV3Q",
    "ttfund__SIWGVYM",
    "ttfund__SX1CRWN",
    "ttfund__LZM096U",
    "ttfund__X5AHNCD",
)
PAGE_PACK_FRESHNESS_GRACE_SECONDS = 3600


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def pct(numerator: float, denominator: float) -> float:
    return round(numerator * 100 / denominator, 4) if denominator else 0.0


def connect_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def one(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any]:
    row = conn.execute(sql, params).fetchone()
    return dict(row) if row else {}


def many(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def safe_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def parse_json_object(value: Any) -> dict[str, float]:
    if value is None:
        return {}
    if isinstance(value, dict):
        data = value
    else:
        text = str(value).strip()
        if not text or text == "{}":
            return {}
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return {}
    out: dict[str, float] = {}
    if isinstance(data, dict):
        for key, raw in data.items():
            number = safe_float(raw)
            if abs(number) > 1e-9:
                out[str(key)] = number
    return out


def load_manifest(site_dir: Path) -> dict[str, Any]:
    manifest_path = site_dir.parent / "deployment_manifest.json"
    if not manifest_path.exists():
        return {"status": "missing_manifest", "missing": ["deployment_manifest.json"]}
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        return {"status": "invalid_manifest", "missing": [f"deployment_manifest.json: {exc}"]}


def file_info(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"path": str(path), "exists": False, "bytes": 0, "mtime": ""}
    stat = path.stat()
    return {
        "path": str(path),
        "exists": True,
        "bytes": stat.st_size,
        "mtime": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds"),
        "mtimeTs": stat.st_mtime,
    }


def check_impact(name: str) -> tuple[str, str]:
    if "数据包" in name or "部署" in name:
        return ("首页、策略列表、数据洞察、详情页、内网静态部署", "节点脚本/_共享组件/生产程序/build_basic_data_report_packs.py；节点脚本/_共享组件/生产程序/export_basic_data_pages.py")
    if "基金" in name or "股票行业" in name:
        return ("基金详情、策略详情、数据洞察仓位分析、主题分析、AI选策略", "节点脚本/_共享组件/生产程序/构建基金经济暴露快照.py；节点脚本/_共享组件/生产程序/同步基金经济暴露到页面包.py；节点脚本/_共享组件/生产程序/构建基础数据质量包.py")
    if "策略治理" in name or "测试组合" in name or "信号类" in name or "stopped" in name or "目标盈" in name:
        return ("策略列表、策略详情、策略对比、目标盈分析、排名样本", "节点脚本/_共享组件/生产程序/治理策略生命周期和调仓去重.py")
    if "调仓" in name:
        return ("数据洞察调仓分析、策略详情历史调仓、基金调仓榜单", "节点脚本/_共享组件/生产程序/治理策略生命周期和调仓去重.py；节点脚本/_共享组件/生产程序/apply_field_renames_and_build_insights.js")
    if "策略元数据" in name:
        return ("策略列表、策略对比、AI选策略、负责人总览", "节点脚本/_共享组件/生产程序/build_strategy_benchmark_fee_status.py；节点脚本/_共享组件/生产程序/更新广发策略费率基准元数据.py")
    return ("全局数据质量页", "对应采集、加工或报表构建脚本")


def status_item(name: str, status: str, value: Any, threshold: str, reason: str) -> dict[str, Any]:
    impact_pages, owner_script = check_impact(name)
    return {
        "项目": name,
        "状态": status,
        "当前值": value,
        "门槛": threshold,
        "说明": reason,
        "影响页面": impact_pages,
        "修复责任脚本": owner_script,
        "name": name,
        "status": status,
    }


def table_count(conn: sqlite3.Connection, table: str) -> int:
    if not table_exists(conn, table):
        return 0
    return safe_int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])


def has_target_profit_evidence(text: str) -> bool:
    normalized = str(text or "").strip()
    if not normalized:
        return False
    strong_brand = re.search(r"目标盈|小目标|小赢家|小杏运|步步高|小星愿|小盈加|智盈|智慧目标投|小常乐|常乐", normalized)
    explicit_goal = re.search(
        r"目标收益|收益目标|绝对收益目标|目标止盈|止盈目标|达标即止盈|达标止盈|止盈达标|止盈提醒|达到目标|目标达成|达标退出|达标赎回",
        normalized,
    )
    lifecycle = re.search(
        r"期次|第[零一二三四五六七八九十百千万\d]+期|\d{1,2}期|到期|期满|运作期|封闭期|续作|赎回|退出|发售|发行|自动终止|stopped|两年期|一年期|年中版|新年特供",
        normalized,
        re.I,
    )
    return bool(strong_brand or (explicit_goal and lifecycle))


def refined_strategy_count(conn: sqlite3.Connection) -> int:
    if not table_exists(conn, "策略信息"):
        return 0
    placeholders = ",".join("?" for _ in REFINED_CHANNELS)
    return safe_int(
        conn.execute(
            f'SELECT COUNT(*) FROM "策略信息" WHERE "渠道ID" IN ({placeholders})',
            REFINED_CHANNELS,
        ).fetchone()[0]
    )


def build_stock_quality(conn: sqlite3.Connection) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not table_exists(conn, "基金季度股票持仓"):
        return {}, []
    stock_map = one(
        conn,
        """
        SELECT COUNT(*) AS total_rows,
               SUM(CASE WHEN COALESCE("行业一级",'') NOT IN ('','未识别') THEN 1 ELSE 0 END) AS mapped_rows,
               SUM(CASE WHEN COALESCE("行业一级",'') IN ('','未识别') THEN 1 ELSE 0 END) AS unmapped_rows,
               COUNT(DISTINCT CASE WHEN COALESCE("行业一级",'') NOT IN ('','未识别') THEN "基金代码" END) AS mapped_funds,
               COUNT(DISTINCT CASE WHEN COALESCE("行业一级",'') IN ('','未识别') THEN "基金代码" END) AS unmapped_funds
        FROM "基金季度股票持仓"
        """,
    )
    unmapped_top = many(
        conn,
        """
        SELECT "股票代码" AS 股票代码,
               MAX("股票名称") AS 股票名称,
               COUNT(*) AS 持仓行数,
               COUNT(DISTINCT "基金代码") AS 基金数,
               ROUND(SUM(COALESCE("占基金净值比例_百分比",0)), 4) AS 合计权重
        FROM "基金季度股票持仓"
        WHERE COALESCE("行业一级",'') IN ('','未识别')
        GROUP BY "股票代码"
        ORDER BY 基金数 DESC, 合计权重 DESC
        LIMIT 60
        """,
    )
    total = safe_int(stock_map.get("total_rows"))
    stock_map["mappedRate"] = pct(safe_int(stock_map.get("mapped_rows")), total)
    stock_map["unmappedRate"] = pct(safe_int(stock_map.get("unmapped_rows")), total)
    return stock_map, unmapped_top


def build_positive_stock_missing(conn: sqlite3.Connection) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not table_exists(conn, "基金季度资产配置") or not table_exists(conn, "基金季度股票持仓"):
        return {}, []
    summary = one(
        conn,
        """
        WITH latest_asset AS (
          SELECT "基金代码", MAX("报告期") AS report FROM "基金季度资产配置" GROUP BY "基金代码"
        ),
        a AS (
          SELECT x.* FROM "基金季度资产配置" x
          JOIN latest_asset l ON x."基金代码"=l."基金代码" AND x."报告期"=l.report
        ),
        st AS (SELECT DISTINCT "基金代码", "报告期" FROM "基金季度股票持仓")
        SELECT COUNT(*) AS positive_stock_funds,
               SUM(CASE WHEN st."基金代码" IS NULL THEN 1 ELSE 0 END) AS missing_stock_rows
        FROM a
        LEFT JOIN st ON a."基金代码"=st."基金代码" AND a."报告期"=st."报告期"
        WHERE COALESCE(a."股票占比_百分比",0) > 0.5
        """,
    )
    examples = many(
        conn,
        """
        WITH latest_asset AS (
          SELECT "基金代码", MAX("报告期") AS report FROM "基金季度资产配置" GROUP BY "基金代码"
        ),
        a AS (
          SELECT x.* FROM "基金季度资产配置" x
          JOIN latest_asset l ON x."基金代码"=l."基金代码" AND x."报告期"=l.report
        ),
        st AS (SELECT DISTINCT "基金代码", "报告期" FROM "基金季度股票持仓"),
        name_map AS (
          SELECT "基金代码", MAX("基金名称") AS "基金名称" FROM "基金分类快照" GROUP BY "基金代码"
        )
        SELECT a."基金代码" AS 基金代码,
               COALESCE(name_map."基金名称",'') AS 基金名称,
               a."报告期" AS 报告期,
               ROUND(COALESCE(a."股票占比_百分比",0),4) AS 股票占比
        FROM a
        LEFT JOIN st ON a."基金代码"=st."基金代码" AND a."报告期"=st."报告期"
        LEFT JOIN name_map ON a."基金代码"=name_map."基金代码"
        WHERE COALESCE(a."股票占比_百分比",0) > 0.5 AND st."基金代码" IS NULL
        ORDER BY 股票占比 DESC, a."基金代码"
        LIMIT 80
        """,
    )
    return summary, examples


def latest_fund_snapshot_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    if table_exists(conn, "基金经济暴露快照"):
        return many(
            conn,
            """
            SELECT "基金代码",
                   "报告期",
                   "基金名称",
                   "标准资产大类" AS 基金类型,
                   "标准资产细类" AS 二级分类,
                   "标准资产大类",
                   "标准资产细类",
                   "经济资产暴露JSON" AS 资产暴露JSON,
                   "经济行业暴露JSON" AS 行业暴露JSON,
                   "主题标签JSON",
                   "穿透方法" AS 分类来源,
                   "质量状态" AS 覆盖状态,
                   "质量状态",
                   "证据说明",
                   "置信度"
            FROM "基金经济暴露快照"
            """,
        )
    if not table_exists(conn, "基金分类快照"):
        return []
    return many(
        conn,
        """
        WITH latest AS (
          SELECT "基金代码", MAX("报告期") AS report FROM "基金分类快照" GROUP BY "基金代码"
        )
        SELECT x.*
        FROM "基金分类快照" x
        JOIN latest l ON x."基金代码"=l."基金代码" AND x."报告期"=l.report
        """,
    )


def equity_like_exposure(asset: dict[str, float]) -> float:
    equity_keywords = ("股票", "权益", "A股", "港股", "美股", "海外权益", "权益基金", "ETF")
    return sum(value for key, value in asset.items() if any(token in key for token in equity_keywords))


def industry_applicable(row: dict[str, Any]) -> bool:
    asset = parse_json_object(row.get("资产暴露JSON"))
    asset_keys = " ".join(asset.keys())
    standard_text = " ".join(str(row.get(key) or "") for key in ("标准资产大类", "标准资产细类", "基金类型", "二级分类"))
    text = " ".join(str(row.get(key) or "") for key in ("基金名称", "基金类型", "二级分类", "主题标签JSON", "标准资产大类", "标准资产细类", "分类来源", "覆盖状态"))
    if any(
        term in standard_text or term in asset_keys
        for term in (
            "货币",
            "现金",
            "纯债",
            "短债",
            "中短债",
            "同业存单",
            "债券指数",
            "债券",
            "固收",
            "政策性金融债",
            "信用债",
            "可转债",
            "海外债券",
            "美元债",
            "黄金",
            "贵金属",
            "商品",
        )
    ):
        if equity_like_exposure(asset) < 5:
            return False
    if equity_like_exposure(asset) >= 5:
        return True
    equity_terms = ("股票", "混合", "权益", "指数", "ETF", "QDII", "港股", "美股", "海外", "医药", "消费", "科技", "人工智能", "半导体", "芯片", "新能源")
    non_industry_terms = (
        "货币",
        "现金",
        "存款",
        "纯债",
        "短债",
        "中短债",
        "债券",
        "固收",
        "同业存单",
        "政策性金融债",
        "信用债",
        "可转债",
        "黄金",
        "贵金属",
        "商品",
        "美元债",
        "海外债券",
    )
    return any(term in text for term in equity_terms) and not any(term in standard_text or term in asset_keys for term in non_industry_terms)


def current_held_fund_weights(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    if not table_exists(conn, "策略当前持仓"):
        return {}
    placeholders = ",".join("?" for _ in REFINED_CHANNELS)
    if table_exists(conn, "策略治理标签"):
        rows = many(
            conn,
            f"""
            SELECT h."基金代码" AS 基金代码,
                   MAX(h."基金名称") AS 基金名称,
                   COUNT(DISTINCT h."统一策略ID") AS 持仓策略数,
                   ROUND(SUM(CASE WHEN COALESCE(h."基金权重_百分比",0) > 0.5 THEN COALESCE(h."基金权重_百分比",0) ELSE 0 END), 4) AS 全市场当前持仓权重
            FROM "策略当前持仓" h
            LEFT JOIN "策略治理标签" g ON g."统一策略ID" = h."统一策略ID"
            WHERE h."渠道ID" IN ({placeholders})
              AND COALESCE(g."是否纳入常规排名", 1) = 1
              AND COALESCE(h."基金权重_百分比",0) > 0.5
            GROUP BY h."基金代码"
            """,
            REFINED_CHANNELS,
        )
    else:
        rows = many(
            conn,
            f"""
            SELECT "基金代码" AS 基金代码,
                   MAX("基金名称") AS 基金名称,
                   COUNT(DISTINCT "统一策略ID") AS 持仓策略数,
                   ROUND(SUM(CASE WHEN COALESCE("基金权重_百分比",0) > 0.5 THEN COALESCE("基金权重_百分比",0) ELSE 0 END), 4) AS 全市场当前持仓权重
            FROM "策略当前持仓"
            WHERE "渠道ID" IN ({placeholders})
              AND COALESCE("基金权重_百分比",0) > 0.5
            GROUP BY "基金代码"
            """,
            REFINED_CHANNELS,
        )
    return {str(row.get("基金代码") or ""): row for row in rows if row.get("基金代码")}


def build_fund_classification_quality(conn: sqlite3.Connection) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    snapshot_rows = latest_fund_snapshot_rows(conn)
    held_weights = current_held_fund_weights(conn)
    latest_report = max((str(row.get("报告期") or "") for row in snapshot_rows), default="")
    funds = len(snapshot_rows)
    asset_funds = sum(1 for row in snapshot_rows if parse_json_object(row.get("资产暴露JSON")))
    applicable_rows = [row for row in snapshot_rows if industry_applicable(row)]
    applicable_codes = {str(row.get("基金代码") or "") for row in applicable_rows}
    covered_codes = {str(row.get("基金代码") or "") for row in applicable_rows if parse_json_object(row.get("行业暴露JSON"))}
    held_applicable_codes = {code for code in applicable_codes if code in held_weights}
    held_covered_codes = {code for code in held_applicable_codes if code in covered_codes}
    missing_examples: list[dict[str, Any]] = []
    rows_by_code = {str(row.get("基金代码") or ""): row for row in snapshot_rows}
    for code in sorted(held_applicable_codes - covered_codes, key=lambda item: safe_float(held_weights.get(item, {}).get("全市场当前持仓权重")), reverse=True):
        row = rows_by_code.get(code, {})
        hold = held_weights.get(code, {})
        missing_examples.append(
            {
                "基金代码": code,
                "基金名称": row.get("基金名称") or hold.get("基金名称") or "",
                "基金类型": row.get("基金类型") or "",
                "二级分类": row.get("二级分类") or "",
                "持仓策略数": hold.get("持仓策略数") or 0,
                "全市场当前持仓权重": hold.get("全市场当前持仓权重") or 0,
                "缺口说明": "该基金属于应做行业穿透的权益/混合/QDII/指数类样本，但行业暴露JSON为空。",
            }
        )
    summary = {
        "funds": funds,
        "asset_exposure_funds": asset_funds,
        "assetExposureRate": pct(asset_funds, funds),
        "industry_applicable_funds": len(applicable_rows),
        "industry_exposure_funds": len(covered_codes),
        "industryExposureRate": pct(len(covered_codes), len(applicable_rows)),
        "held_industry_applicable_funds": len(held_applicable_codes),
        "held_industry_exposure_funds": len(held_covered_codes),
        "heldIndustryExposureRate": pct(len(held_covered_codes), len(held_applicable_codes)),
        "latest_report": latest_report,
    }
    return summary, missing_examples[:80]


def build_strategy_holding_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    if not table_exists(conn, "策略当前持仓"):
        return {}
    placeholders = ",".join("?" for _ in REFINED_CHANNELS)
    return one(
        conn,
        f"""
        SELECT COUNT(DISTINCT "统一策略ID") AS strategies,
               COUNT(*) AS holding_rows,
               COUNT(DISTINCT "基金代码") AS held_funds,
               MAX("持仓日期") AS latest_holding_date,
               MIN("持仓日期") AS earliest_holding_date
        FROM "策略当前持仓"
        WHERE "渠道ID" IN ({placeholders})
        """,
        REFINED_CHANNELS,
    )


def build_rebalance_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    if not table_exists(conn, "策略调仓明细"):
        return {}
    placeholders = ",".join("?" for _ in REFINED_CHANNELS)
    return one(
        conn,
        f"""
        SELECT COUNT(*) AS rows,
               COUNT(DISTINCT "统一策略ID") AS strategies,
               MAX("调仓日期") AS latest_rebalance_date
        FROM "策略调仓明细"
        WHERE "渠道ID" IN ({placeholders})
        """,
        REFINED_CHANNELS,
    )


def build_duplicate_rebalance(conn: sqlite3.Connection) -> dict[str, Any]:
    if not table_exists(conn, "策略调仓事件"):
        return {"duplicateGroups": 0, "duplicateEvents": 0}
    placeholders = ",".join("?" for _ in REFINED_CHANNELS)
    return one(
        conn,
        f"""
        WITH g AS (
          SELECT "渠道ID", "统一策略ID", "调仓日期",
                 COALESCE("本次仓位日期", '') AS this_date,
                 COALESCE("调仓标题", '') AS title,
                 COALESCE("调仓原因", '') AS reason,
                 COUNT(*) AS n
          FROM "策略调仓事件"
          WHERE "渠道ID" IN ({placeholders})
          GROUP BY 1,2,3,4,5,6
          HAVING COUNT(*) > 1
        )
        SELECT COUNT(*) AS duplicateGroups,
               COALESCE(SUM(n - 1), 0) AS duplicateEvents
        FROM g
        """,
        REFINED_CHANNELS,
    )


def build_governance_quality(conn: sqlite3.Connection) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    refined_count = refined_strategy_count(conn)
    if not table_exists(conn, "策略治理标签"):
        return {"tableExists": False, "refinedStrategies": refined_count}, [], [], []
    summary = one(
        conn,
        """
        SELECT COUNT(*) AS governanceRows,
               SUM(CASE WHEN "是否测试组合" = 1 THEN 1 ELSE 0 END) AS testStrategies,
               SUM(CASE WHEN "是否信号类组合" = 1 THEN 1 ELSE 0 END) AS signalStrategies,
               SUM(CASE WHEN "是否目标盈期次" = 1 THEN 1 ELSE 0 END) AS targetProfitPeriods,
               SUM(CASE WHEN "是否已停止" = 1 THEN 1 ELSE 0 END) AS stoppedStrategies,
               SUM(CASE WHEN "是否纳入常规排名" = 1 THEN 1 ELSE 0 END) AS regularRankStrategies,
               SUM(CASE WHEN "是否测试组合" = 1 AND "是否纳入常规排名" = 1 THEN 1 ELSE 0 END) AS testsInRegularRank,
               SUM(CASE WHEN "是否信号类组合" = 1 AND "是否纳入常规排名" = 1 THEN 1 ELSE 0 END) AS signalsInRegularRank,
               SUM(CASE WHEN "是否目标盈期次" = 1 AND "是否纳入常规排名" = 1 THEN 1 ELSE 0 END) AS targetProfitInRegularRank,
               SUM(CASE WHEN "是否目标盈期次" = 1 AND "是否已停止" = 1 AND "是否纳入常规排名" = 1 THEN 1 ELSE 0 END) AS stoppedTargetsInRegularRank,
               SUM(CASE WHEN "治理状态" = '当前基金权重未完整披露' THEN 1 ELSE 0 END) AS incompleteCurrentWeightStrategies
        FROM "策略治理标签"
        """,
    )
    confirmed_signal_count = safe_int(
        conn.execute(
            f"""
            SELECT COUNT(*) FROM "策略治理标签"
            WHERE "统一策略ID" IN ({",".join("?" for _ in CONFIRMED_SIGNAL_STRATEGY_IDS)})
              AND "是否信号类组合" = 1
            """,
            CONFIRMED_SIGNAL_STRATEGY_IDS,
        ).fetchone()[0]
    )
    summary.update({"tableExists": True, "refinedStrategies": refined_count, "confirmedSignalCount": confirmed_signal_count})
    special_rows = many(
        conn,
        """
        SELECT "统一策略ID", "渠道ID", "策略名称", "投顾机构", "治理状态", "分析分组",
               "是否纳入常规排名", "业绩分析截止日期", "持仓处理方式", "调仓展示方式", "规则说明"
        FROM "策略治理标签"
        WHERE "是否测试组合" = 1 OR "是否信号类组合" = 1 OR "是否目标盈期次" = 1 OR "是否已停止" = 1
        ORDER BY "分析分组", "策略名称"
        LIMIT 200
        """,
    )
    incomplete_rows = many(
        conn,
        """
        SELECT "统一策略ID", "渠道ID", "策略名称", "投顾机构", "原始持仓权重合计", "原始持仓行数",
               "最近调仓日期", "持仓处理方式", "规则说明"
        FROM "策略治理标签"
        WHERE "治理状态" = '当前基金权重未完整披露'
        ORDER BY "原始持仓权重合计", "策略名称"
        LIMIT 80
        """,
    )
    suspicious_candidates = many(
        conn,
        """
        SELECT g."统一策略ID", g."渠道ID", g."策略名称", g."投顾机构", g."治理状态", g."分析分组",
               g."是否目标盈期次", g."是否纳入常规排名", g."规则说明",
               s."策略类型", s."策略状态", s."策略描述", s."标签JSON", s."业绩基准"
        FROM "策略治理标签" g
        LEFT JOIN "策略信息" s ON s."统一策略ID" = g."统一策略ID"
        WHERE g."是否目标盈期次" = 1
        ORDER BY g."策略名称"
        """,
    )
    suspicious_target_rows: list[dict[str, Any]] = []
    for row in suspicious_candidates:
        evidence_text = " ".join(
            str(row.get(key) or "")
            for key in ["策略名称", "策略类型", "策略状态", "策略描述", "标签JSON", "业绩基准"]
        )
        if has_target_profit_evidence(evidence_text):
            continue
        item = dict(row)
        item["疑似误判原因"] = "目标盈标记缺少目标盈/小目标等品牌，且缺少明确目标收益/达标止盈机制与期次、到期、赎回等生命周期证据。"
        item["原始文本摘要"] = evidence_text[:500]
        suspicious_target_rows.append(item)
    summary["suspiciousTargetProfitLabels"] = len(suspicious_target_rows)
    return summary, special_rows, incomplete_rows, suspicious_target_rows[:80]


def build_strategy_metadata_quality(conn: sqlite3.Connection) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not table_exists(conn, "策略信息"):
        return {}, []
    placeholders = ",".join("?" for _ in REFINED_CHANNELS)
    join_governance = table_exists(conn, "策略治理标签")
    if join_governance:
        base_sql = f"""
            FROM "策略信息" s
            LEFT JOIN "策略治理标签" g ON g."统一策略ID" = s."统一策略ID"
            WHERE s."渠道ID" IN ({placeholders}) AND COALESCE(g."是否纳入常规排名", 1) = 1
        """
    else:
        base_sql = f"""
            FROM "策略信息" s
            WHERE s."渠道ID" IN ({placeholders})
        """
    summary = one(
        conn,
        f"""
        SELECT COUNT(*) AS regularStrategies,
               SUM(CASE WHEN COALESCE(s."策略名称",'') = '' THEN 1 ELSE 0 END) AS missingName,
               SUM(CASE WHEN COALESCE(s."投顾机构",'') = '' THEN 1 ELSE 0 END) AS missingAdvisor,
               SUM(CASE WHEN COALESCE(s."成立日期",'') = '' THEN 1 ELSE 0 END) AS missingStartDate,
               SUM(CASE WHEN COALESCE(s."风险等级",'') = '' THEN 1 ELSE 0 END) AS missingRiskLevel,
               SUM(CASE WHEN COALESCE(s."投顾费率",'') = '' THEN 1 ELSE 0 END) AS missingFee,
               SUM(CASE WHEN COALESCE(s."业绩基准",'') = '' THEN 1 ELSE 0 END) AS missingBenchmark
        {base_sql}
        """,
        REFINED_CHANNELS,
    )
    examples = many(
        conn,
        f"""
        SELECT s."统一策略ID", s."渠道ID", s."策略名称", s."投顾机构",
               TRIM(
                 CASE WHEN COALESCE(s."成立日期",'') = '' THEN '成立日期;' ELSE '' END ||
                 CASE WHEN COALESCE(s."风险等级",'') = '' THEN '风险等级;' ELSE '' END ||
                 CASE WHEN COALESCE(s."投顾费率",'') = '' THEN '投顾费率;' ELSE '' END ||
                 CASE WHEN COALESCE(s."业绩基准",'') = '' THEN '业绩基准;' ELSE '' END
               ) AS 缺失字段
        {base_sql}
          AND (
            COALESCE(s."成立日期",'') = '' OR COALESCE(s."风险等级",'') = '' OR
            COALESCE(s."投顾费率",'') = '' OR COALESCE(s."业绩基准",'') = ''
          )
        ORDER BY s."渠道ID", s."策略名称"
        LIMIT 80
        """,
        REFINED_CHANNELS,
    )
    total = safe_int(summary.get("regularStrategies"))
    missing_any = len(examples)
    summary["missingAnyImportantRate"] = pct(missing_any, total)
    return summary, examples


def build_files(site_dir: Path) -> dict[str, dict[str, Any]]:
    data_dir = site_dir / "data"
    paths = {
        "basic_summary": data_dir / "basic_summary.js",
        "basic_summary_core": data_dir / "basic_summary_core.js",
        "holding_snapshot_pack": data_dir / "holding_snapshot_pack.js",
        "rebalance_fund_category_pack": data_dir / "rebalance_fund_category_pack.js",
        "fund_detail_pack": data_dir / "fund_detail_pack.js",
        "fund_economic_exposure_pack": data_dir / "fund_economic_exposure_pack.js",
        "ai_semantic_index": data_dir / "ai_semantic_index.js",
        "standard_entity_dictionary": data_dir / "standard_entity_dictionary.js",
        "topic_analysis_manifest": data_dir / "topic_analysis_manifest.js",
        "data_pack_manifest": data_dir / "data_pack_manifest.js",
        "data_quality_pack": data_dir / "data_quality_pack.js",
    }
    return {name: file_info(path) for name, path in paths.items()}


def build_data_pack_manifest(site_dir: Path) -> dict[str, Any]:
    data_dir = site_dir / "data"
    files: list[dict[str, Any]] = []
    if data_dir.exists():
        for path in sorted(item for item in data_dir.rglob("*") if item.is_file() and item.suffix.lower() in {".js", ".json"}):
            rel = path.relative_to(site_dir).as_posix()
            info = file_info(path)
            files.append(
                {
                    "path": rel,
                    "bytes": info.get("bytes", 0),
                    "mtime": info.get("mtime", ""),
                    "role": "首屏核心" if path.name == "basic_summary_core.js" else ("按页/按需加载" if path.parent.name in {"details", "fund_details"} else "页面数据包"),
                }
            )
    total_bytes = sum(safe_int(item.get("bytes")) for item in files)
    first_screen_names = {"basic_summary_core.js", "basic-common.js"}
    first_screen_bytes = sum(safe_int(item.get("bytes")) for item in files if Path(str(item.get("path"))).name in first_screen_names)
    max_file = max(files, key=lambda item: safe_int(item.get("bytes")), default={})
    return {
        "version": 1,
        "generatedAt": now_iso(),
        "totalFiles": len(files),
        "totalBytes": total_bytes,
        "maxFile": max_file,
        "firstScreenBytes": first_screen_bytes,
        "thresholds": {
            "basic_data_total_warn_mb": 2048,
            "single_pack_warn_mb": 160,
            "first_screen_warn_mb": 40,
        },
        "pageDependencies": {
            "index.html": ["basic_summary_core.js"],
            "strategies.html": ["basic_summary_core.js"],
            "insights.html": ["basic_summary_core.js", "insight_data_pack.js", "holding_snapshot_pack.js", "fund_detail_pack.js"],
            "compare.html": ["basic_summary_core.js", "holding_snapshot_pack.js", "fund_detail_pack.js"],
            "strategy.html": ["basic_summary_core.js", "fund_detail_pack.js", "ai_semantic_index.js", "details/<策略ID>.js"],
            "fund.html": ["fund_detail_pack.js", "fund_economic_exposure_pack.js", "fund_details/<基金代码>.js"],
            "data-quality.html": ["data_quality_pack.js", "standard_entity_dictionary.js", "data_pack_manifest.js"],
        },
        "files": files,
    }


def build_pack(db_path: Path, site_dir: Path) -> dict[str, Any]:
    db_info = file_info(db_path)
    with connect_db(db_path) as conn:
        quick_check = conn.execute("PRAGMA quick_check").fetchone()[0]
        stock_map, unmapped_top = build_stock_quality(conn)
        positive_stock_missing, positive_stock_missing_examples = build_positive_stock_missing(conn)
        fund_classification, industry_missing_examples = build_fund_classification_quality(conn)
        strategy_hold = build_strategy_holding_summary(conn)
        rebalance = build_rebalance_summary(conn)
        duplicate_rebalance = build_duplicate_rebalance(conn)
        governance, special_strategy_examples, incomplete_current_weight_examples, suspicious_target_profit_examples = build_governance_quality(conn)
        strategy_metadata, strategy_metadata_examples = build_strategy_metadata_quality(conn)
        table_counts = {
            "策略信息": table_count(conn, "策略信息"),
            "策略当前持仓": table_count(conn, "策略当前持仓"),
            "策略调仓事件": table_count(conn, "策略调仓事件"),
            "策略调仓明细": table_count(conn, "策略调仓明细"),
            "基金分类快照": table_count(conn, "基金分类快照"),
            "基金经济暴露快照": table_count(conn, "基金经济暴露快照"),
            "基金季度资产配置": table_count(conn, "基金季度资产配置"),
            "基金季度股票持仓": table_count(conn, "基金季度股票持仓"),
        }

    manifest = load_manifest(site_dir)
    files = build_files(site_dir)
    required_files = {name: info for name, info in files.items() if name not in {"data_quality_pack", "data_pack_manifest"}}
    missing_files = [name for name, info in required_files.items() if not info["exists"] or safe_int(info["bytes"]) <= 0]
    stale_files = [
        name
        for name, info in required_files.items()
        if info["exists"]
        and db_info.get("mtimeTs")
        and info.get("mtimeTs")
        and info["mtimeTs"] + PAGE_PACK_FRESHNESS_GRACE_SECONDS < db_info["mtimeTs"]
    ]
    oversized_files = [
        {"数据包": name, "大小MB": round(safe_int(info.get("bytes")) / 1024 / 1024, 2)}
        for name, info in required_files.items()
        if safe_int(info.get("bytes")) > 160 * 1024 * 1024
    ]

    stock_total = safe_int(stock_map.get("total_rows"))
    stock_unmapped_rate = pct(safe_int(stock_map.get("unmapped_rows")), stock_total)
    asset_rate = safe_float(fund_classification.get("assetExposureRate"))
    industry_rate = safe_float(fund_classification.get("industryExposureRate"))
    held_industry_rate = safe_float(fund_classification.get("heldIndustryExposureRate"))
    missing_stock_rows = safe_int(positive_stock_missing.get("missing_stock_rows"))
    duplicate_groups = safe_int(duplicate_rebalance.get("duplicateGroups"))
    metadata_missing_rate = safe_float(strategy_metadata.get("missingAnyImportantRate"))

    checks = [
        status_item("SQLite quick_check", "ok" if quick_check == "ok" else "fail", quick_check, "ok", "主分析库必须通过 SQLite 快速一致性检查。"),
        status_item("部署清单", "ok" if manifest.get("status") == "ready" and not manifest.get("missing") else "warn", manifest.get("status"), "ready 且 missing=[]", "最终外部页面同步前应确保 deployment_manifest.json 就绪。"),
        status_item("页面数据包完整", "ok" if not missing_files else "fail", f"缺失 {len(missing_files)} 个", "全部存在且 >0 bytes", "缺失数据包会导致页面或详情链接无法正常打开。"),
        status_item("页面数据包新鲜度", "ok" if not stale_files else "warn", f"明显落后 {len(stale_files)} 个", "数据包更新时间不应明显早于数据库", "数据库更新后应重建页面数据包；同一构建流水线内后续审计或 manifest 写入可能刷新 SQLite mtime，1 小时内不判定为页面包落后。"),
        status_item("页面数据包体积", "ok" if not oversized_files else "warn", f"超 160MB {len(oversized_files)} 个", "建议单包 <160MB", "体积过大会影响内网服务器首屏加载，后续可继续拆包或懒加载。"),
        status_item("策略治理标签覆盖", "ok" if governance.get("tableExists") and safe_int(governance.get("governanceRows")) >= safe_int(governance.get("refinedStrategies")) else "fail", f"{governance.get('governanceRows', 0)} / {governance.get('refinedStrategies', 0)}", "天天+广发+中欧钱滚滚全覆盖", "治理标签负责测试剔除、信号类、stopped 期次和当前权重缺披露口径。"),
        status_item("测试组合剔除", "ok" if safe_int(governance.get("testsInRegularRank")) == 0 else "fail", governance.get("testsInRegularRank", 0), "0", "名称含测试/test/演示等组合不得进入常规排名和市场统计。"),
        status_item("信号类策略识别", "ok" if safe_int(governance.get("confirmedSignalCount")) >= len(CONFIRMED_SIGNAL_STRATEGY_IDS) else "fail", governance.get("confirmedSignalCount", 0), f">={len(CONFIRMED_SIGNAL_STRATEGY_IDS)}", "砺远+、中欧/钱滚滚薪动月月投、超级定投家、指数100份、100份发车带投及智能发车/滚动带投策略按信号服务单独分析，不按普通调仓组合解释。"),
        status_item("目标盈标签强证据", "ok" if safe_int(governance.get("suspiciousTargetProfitLabels")) == 0 else "fail", governance.get("suspiciousTargetProfitLabels", 0), "0", "目标盈/期次标签必须有目标盈、小目标、小赢家、小杏运、小盈加、智盈等品牌，或同时具备明确目标收益/达标止盈机制与期次、到期、赎回等生命周期证据；普通止盈止损、预期兑现后止盈、以期满足、目标日期到期时间不得触发。"),
        status_item("目标盈期次单独分析", "ok" if safe_int(governance.get("targetProfitInRegularRank")) == 0 else "fail", governance.get("targetProfitInRegularRank", 0), "0", "目标盈/期次策略无论运行中还是已停止，都应进入目标盈生命周期复盘，不混入常规策略排名。"),
        status_item("stopped 期次单独分析", "ok" if safe_int(governance.get("stoppedTargetsInRegularRank")) == 0 else "fail", governance.get("stoppedTargetsInRegularRank", 0), "0", "已停止目标盈/期次策略的业绩只分析到 stopped/到期日前，不能混入当前运行榜单。"),
        status_item("调仓事件重复业务键", "ok" if duplicate_groups == 0 else "fail", f"{duplicate_groups} 组 / {duplicate_rebalance.get('duplicateEvents', 0)} 条冗余", "0", "按渠道、策略、调仓日期、本次仓位日期、标题、原因合并重复调仓事件。"),
        status_item("最新股票资产基金缺股票持仓", "ok" if missing_stock_rows == 0 else "fail", missing_stock_rows, "0", "最新季报股票资产占比 >0.5% 的基金必须有同报告期股票持仓明细，否则会影响行业/主题穿透。"),
        status_item("股票行业未识别率", "ok" if stock_unmapped_rate <= 2 else "warn", f"{stock_unmapped_rate:.2f}%", "<=2%", "未识别股票行业会削弱基金行业暴露、策略行业暴露和主题分析可信度。"),
        status_item("基金资产暴露覆盖", "ok" if asset_rate >= 99 else "fail", f"{asset_rate:.2f}%", ">=99%", "资产暴露是大类资产配置、持仓分布和策略筛选的基础。"),
        status_item("基金行业暴露覆盖", "ok" if industry_rate >= 85 else "warn", f"{industry_rate:.2f}%", ">=85%（仅应穿透基金）", "分母只包括权益/混合/QDII/指数等应当有行业暴露的基金，不再用货币债券基金稀释。"),
        status_item("当前持仓基金行业覆盖", "ok" if held_industry_rate >= 90 else "warn", f"{held_industry_rate:.2f}%", ">=90%（当前持仓且权重>0.5%）", "当前策略实际持仓中的应穿透基金若缺行业，会直接影响仓位分析和主题分析。"),
        status_item("重要策略元数据缺失", "ok" if metadata_missing_rate <= 5 else ("warn" if metadata_missing_rate <= 20 else "fail"), f"{metadata_missing_rate:.2f}%", "<=5%", "成立日期、风险等级、投顾费率、业绩基准属于重要字段，缺失过多会影响筛选和对比。"),
    ]
    status = "fail" if any(item["状态"] == "fail" for item in checks) else ("warn" if any(item["状态"] == "warn" for item in checks) else "ok")
    return {
        "version": 2,
        "generatedAt": now_iso(),
        "status": status,
        "refinedChannels": list(REFINED_CHANNELS),
        "dataVersion": {"db": db_info, "tableCounts": table_counts},
        "checks": checks,
        "metrics": {
            "stockIndustry": stock_map,
            "fundClassification": fund_classification,
            "strategyHolding": strategy_hold,
            "rebalance": rebalance,
            "duplicateRebalance": duplicate_rebalance,
            "positiveStockMissing": positive_stock_missing,
            "strategyGovernance": governance,
            "strategyMetadata": strategy_metadata,
        },
        "files": files,
        "dataPackManifest": build_data_pack_manifest(site_dir),
        "fileIssues": {"missing": missing_files, "stale": stale_files, "oversized": oversized_files},
        "unmappedStocks": unmapped_top,
        "importantGaps": {
            "positiveStockMissingFunds": positive_stock_missing_examples,
            "industryExposureMissingHeldFunds": industry_missing_examples,
            "strategyMetadataMissing": strategy_metadata_examples,
            "specialStrategies": special_strategy_examples,
            "currentWeightIncompleteStrategies": incomplete_current_weight_examples,
            "suspiciousTargetProfitLabels": suspicious_target_profit_examples,
        },
        "manifest": {"status": manifest.get("status"), "missing": manifest.get("missing") or []},
        "口径说明": {
            "股票行业未识别率": "基金季度股票持仓中行业一级为空或未识别的行数 / 基金季度股票持仓总行数。该指标直接影响基金行业暴露、策略行业暴露、AI主题和国家/行业实体判断。",
            "基金资产暴露覆盖": "基金经济暴露快照中经济资产暴露JSON非空的基金数 / 基金经济暴露快照基金数。经济资产暴露在原始季报资产配置基础上，对基金/其他高占比、ETF联接、FOF、QDII、黄金、固收指数等做可审计重映射。",
            "基金行业暴露覆盖": "基金经济暴露快照中经济行业暴露JSON非空的应穿透基金数 / 应穿透基金数。黄金/商品、纯债、货币、海外债券不要求股票行业穿透；权益、海外权益、行业主题、指数权益、主动权益需要行业或主题证据。",
            "当前持仓基金行业覆盖": "当前纳入常规排名的天天/广发策略持仓中，基金权重 >0.5% 且属于应穿透基金的行业暴露覆盖率。分母排除黄金/商品、货币、纯债、短债、海外债券等不适用行业穿透的资产。",
            "策略治理标签": "策略治理标签由 节点脚本/_共享组件/生产程序/治理策略生命周期和调仓去重.py 生成。名称含测试的组合剔除；砺远+、中欧/钱滚滚薪动月月投、超级定投家、指数100份、100份发车带投及智能发车/滚动带投等信号类策略单独展示信号；stopped/已止盈/期满目标盈期次策略业绩截止到停止或到期日前；当前基金权重未披露的正常策略保留排名但标注推算口径。",
            "目标盈标签强证据": "目标盈标签不得由裸止盈、止盈止损、以期满足、到期时间等弱词单独触发。强证据为目标盈/小目标/小赢家/小杏运/小盈加/智盈等品牌，或明确目标收益/达标止盈机制并同时具备期次、到期、赎回等生命周期证据。",
            "调仓事件重复业务键": "重复判断不使用原始事件ID，而按渠道ID、统一策略ID、调仓日期、本次仓位日期、调仓标题、调仓原因组成业务键。保留细节最完整、调后权重更接近100%、基金代码更完整、精度更高的事件。",
            "重要策略元数据缺失": "在纳入常规排名的天天/广发策略中统计成立日期、风险等级、投顾费率、业绩基准缺失率。缺失字段会影响策略筛选、策略对比、费率对比和风险收益解释。",
        },
    }


def write_pack(site_dir: Path, pack: dict[str, Any]) -> None:
    data_dir = site_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(pack, ensure_ascii=False, separators=(",", ":"))
    (data_dir / "data_quality_pack.json").write_text(json.dumps(pack, ensure_ascii=False, indent=2), encoding="utf-8")
    (data_dir / "data_quality_pack.js").write_text(f"window.__BASIC_DATA_QUALITY_PACK__ = {payload};\n", encoding="utf-8")
    manifest = build_data_pack_manifest(site_dir)
    manifest_payload = json.dumps(manifest, ensure_ascii=False, separators=(",", ":"))
    (data_dir / "data_pack_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (data_dir / "data_pack_manifest.js").write_text(f"window.__BASIC_DATA_PACK_MANIFEST__ = {manifest_payload};\n", encoding="utf-8")


def accept_pending_manifest_generation(pack: dict[str, Any]) -> None:
    manifest = pack.get("manifest") if isinstance(pack.get("manifest"), dict) else {}
    if manifest.get("status") != "missing_manifest":
        return
    for check in pack.get("checks") or []:
        if not isinstance(check, dict) or check.get("项目") != "部署清单":
            continue
        check["状态"] = "ok"
        check["status"] = "ok"
        check["当前值"] = "pending_generation"
        check["说明"] = "最小发布源构建将在本质量门禁通过后立即生成并硬校验 deployment_manifest.json。"
    manifest["status"] = "pending_generation"
    manifest["missing"] = []
    pack["manifest"] = manifest
    checks = pack.get("checks") or []
    pack["status"] = (
        "fail"
        if any(item.get("状态") == "fail" for item in checks if isinstance(item, dict))
        else (
            "warn"
            if any(item.get("状态") == "warn" for item in checks if isinstance(item, dict))
            else "ok"
        )
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="构建基础数据质量包，并可作为增量更新后的验收门槛。")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--site-dir", type=Path, default=DEFAULT_SITE_DIR)
    parser.add_argument("--fail-on-error", action="store_true")
    parser.add_argument(
        "--deployment-manifest-pending",
        action="store_true",
        help="Allow a missing deployment manifest only when the caller generates and hard-validates it in the immediately following step.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    pack = build_pack(args.db_path, args.site_dir)
    if args.deployment_manifest_pending:
        accept_pending_manifest_generation(pack)
    write_pack(args.site_dir, pack)
    print(json.dumps({"状态": pack["status"], "checks": pack["checks"], "输出": str(args.site_dir / "data" / "data_quality_pack.js")}, ensure_ascii=False, indent=2))
    if args.fail_on_error and pack["status"] == "fail":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
