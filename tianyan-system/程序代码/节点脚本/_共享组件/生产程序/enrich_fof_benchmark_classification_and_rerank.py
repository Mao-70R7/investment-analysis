from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_INPUT = PROJECT_ROOT / "outputs" / "fof_h1_strategy_ranking" / "latest_fof_h1_strategy_ranking_data.json"
DEFAULT_BENCHMARK = PROJECT_ROOT / "data" / "raw" / "fof_f10_benchmark" / "latest_fof_f10_benchmarks.json"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "fof_benchmark_ranking"
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
TZ = timezone(timedelta(hours=8))


EQUITY_KEYWORDS = [
    "沪深300",
    "中证A500",
    "A500",
    "中证800",
    "中证500",
    "中证1000",
    "中证全指",
    "上证50",
    "上证红利",
    "中证红利",
    "创业板",
    "科创",
    "深证",
    "股票",
    "偏股",
    "普通股票",
    "权益",
    "MSCI",
    "MSCI发达市场",
    "标普",
    "S&P",
    "纳斯达克",
    "NASDAQ",
    "恒生",
    "港股",
    "港股通综合",
    "中证港股通综合",
    "H股",
    "日经",
    "富时",
    "FTSE",
    "道琼斯",
    "Russell",
]
NON_FIXED_MULTI_ASSET_INDEX_KEYWORDS = [
    "中证开放式基金指数",
    "中证混合型基金指数",
    "中证普通混合型基金指数",
    "中证灵活配置混合型基金指数",
    "中证工银财富动态配置基金指数",
]
BOND_KEYWORDS = [
    "中债",
    "国债",
    "债券",
    "全债",
    "信用债",
    "政金债",
    "债",
    "Aggregate Bond",
    "Global Aggregate",
    "J.P.Morgan",
    "摩根大通全球债券",
    "彭博全球综合债券",
    "巴克莱全球综合债券",
]
CASH_KEYWORDS = ["活期存款", "定期存款", "银行存款", "存款利率", "货币", "现金", "同业存单", "七天通知"]
COMMODITY_KEYWORDS = ["商品", "黄金", "伦敦金", "金价格", "贵金属", "原油", "白银", "SGE", "AU9999", "GSCI", "中证商品期货价格指数", "REIT", "另类"]
OVERSEAS_KEYWORDS = [
    "QDII",
    "海外",
    "全球",
    "MSCI",
    "MSCI发达市场",
    "标普",
    "S&P",
    "纳斯达克",
    "NASDAQ",
    "恒生",
    "港股",
    "港股通综合",
    "中证港股通综合",
    "H股",
    "J.P.Morgan",
    "Global",
    "美元",
    "美国",
    "欧洲",
    "亚太",
    "印度",
    "日经",
    "富时",
    "FTSE",
]


def now_text() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def default_run_id() -> str:
    return datetime.now(TZ).strftime("%Y%m%d_fof_benchmark_classification")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def clean(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text in {"None", "nan", "null", "--", "-"} else text


def to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value) if math.isfinite(float(value)) else None
    text = str(value).strip().replace(",", "").replace("%", "")
    if text in {"", "--", "-"}:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def round_or_none(value: float | None, digits: int = 4) -> float | None:
    return round(value, digits) if value is not None and math.isfinite(value) else None


def has_any(text: str, keywords: list[str]) -> bool:
    upper = text.upper()
    return any(keyword.upper() in upper for keyword in keywords)


def normalize_benchmark(text: str) -> str:
    text = clean(text)
    text = text.replace("×", "*").replace("✕", "*").replace("＊", "*").replace("X", "*").replace("x", "*")
    text = text.replace("＋", "+").replace("，", "+").replace("、", "+")
    text = re.sub(r"\s+", "", text)
    return text


def split_components(text: str) -> list[str]:
    normalized = normalize_benchmark(text)
    if not normalized:
        return []
    parts = [part for part in re.split(r"\+|加上", normalized) if part]
    return parts or [normalized]


def component_weight(component: str, component_count: int) -> tuple[float | None, bool]:
    weights = re.findall(r"(?<!\d)(\d+(?:\.\d+)?)\s*%", component)
    if weights:
        return float(weights[-1]), True
    if component_count == 1:
        return 100.0, False
    return None, False


def component_asset_type(component: str) -> str:
    if has_any(component, NON_FIXED_MULTI_ASSET_INDEX_KEYWORDS):
        return "unknown"
    if "股票" in component and has_any(component, COMMODITY_KEYWORDS):
        return "equity"
    if has_any(component, COMMODITY_KEYWORDS):
        return "commodity"
    if has_any(component, EQUITY_KEYWORDS):
        return "equity"
    if has_any(component, BOND_KEYWORDS):
        return "bond"
    if has_any(component, CASH_KEYWORDS):
        return "cash"
    return "unknown"


def risk_asset_bucket(risk_asset_pct: float | None, parsed: bool) -> str:
    if not parsed or risk_asset_pct is None or not math.isfinite(float(risk_asset_pct)):
        return ""
    if risk_asset_pct <= 0:
        return "L0"
    return f"L{min(10, max(1, math.ceil(risk_asset_pct / 10)))}"


def infer_target_year(*values: str) -> int | None:
    evidence = " ".join(clean(value) for value in values)
    years = [int(item) for item in re.findall(r"20[3-6]\d", evidence)]
    return min(years) if years else None


def glidepath_equity_pct(target_year: int) -> float:
    if target_year <= 2030:
        return 35.0
    if target_year <= 2035:
        return 50.0
    if target_year <= 2040:
        return 60.0
    if target_year <= 2045:
        return 70.0
    if target_year <= 2050:
        return 80.0
    return 85.0


def variable_glidepath_result(benchmark: str, public_class: str, fund_type: str, name: str) -> dict[str, Any] | None:
    text = clean(benchmark)
    if not re.search(r"下滑曲线|(?<![A-Za-z])X(?![A-Za-z])|\(1-X\)|（1-X）", text, flags=re.IGNORECASE):
        return None
    evidence_text = " ".join([text, clean(public_class), clean(fund_type), clean(name)])
    if not (has_any(evidence_text, EQUITY_KEYWORDS) and has_any(evidence_text, BOND_KEYWORDS)):
        return None
    overseas = has_any(evidence_text, OVERSEAS_KEYWORDS)
    try:
        from benchmark_asset_classification import compute_benchmark_asset_mix, load_benchmark_catalog

        mix = compute_benchmark_asset_mix(text, load_benchmark_catalog())
    except Exception:
        mix = {}
    bucket = clean(mix.get("基准风险资产权重"))
    confidence = clean(mix.get("基准映射置信度"))
    if bucket and confidence in {"高", "中"}:
        weights = {
            "equity": to_float(mix.get("基准资产大类-权益")) or 0.0,
            "bond": to_float(mix.get("基准资产大类-债券")) or 0.0,
            "cash": to_float(mix.get("基准资产大类-现金")) or 0.0,
            "commodity": to_float(mix.get("基准资产大类-商品")) or 0.0,
            "unknown": to_float(mix.get("基准资产大类-其他")) or 0.0,
            "overseas": to_float(mix.get("基准海外权益权重")) or 0.0,
        }
        classification = classify_by_weights(weights, overseas, True)
        return {
            "基准细分分类": classification,
            "解析置信度": confidence,
            "解析置信度分数": 0.9 if confidence == "高" else 0.6,
            "基准权益权重_百分比": round_or_none(weights["equity"]),
            "基准风险资产权重": bucket,
            "基准风险资产权重_百分比": round_or_none(weights["equity"] + weights["commodity"]),
            "基准债券权重_百分比": round_or_none(weights["bond"]),
            "基准货币权重_百分比": round_or_none(weights["cash"]),
            "基准商品权重_百分比": round_or_none(weights["commodity"]),
            "基准海外权重_百分比": round_or_none(weights["overseas"]),
            "基准未知权重_百分比": round_or_none(weights["unknown"]),
            "基准权重合计_百分比": round_or_none(sum(weights[key] for key in ["equity", "bond", "cash", "commodity", "unknown"])),
            "基准解析说明": clean(mix.get("基准公式解析")),
            "是否海外基准": 1 if overseas else 0,
        }
    return {
        "基准细分分类": "基准未解析",
        "解析置信度": "低",
        "解析置信度分数": 0.2,
        "基准权益权重_百分比": None,
        "基准风险资产权重": "",
        "基准风险资产权重_百分比": None,
        "基准债券权重_百分比": None,
        "基准货币权重_百分比": None,
        "基准商品权重_百分比": None,
        "基准海外权重_百分比": None,
        "基准未知权重_百分比": 100.0,
        "基准权重合计_百分比": 100.0,
        "基准解析说明": "动态下滑曲线未能从原文提取当前年度权重，保持未知，不使用目标年份估算。",
        "是否海外基准": 1 if overseas else 0,
    }


def confidence_label(score: float) -> str:
    if score >= 0.75:
        return "高"
    if score >= 0.45:
        return "中"
    return "低"


def classify_by_weights(weights: dict[str, float], overseas: bool, parsed: bool) -> str:
    if not parsed:
        return "基准未解析"
    equity = weights.get("equity", 0.0)
    bond = weights.get("bond", 0.0)
    cash = weights.get("cash", 0.0)
    commodity = weights.get("commodity", 0.0)
    unknown = weights.get("unknown", 0.0)
    if unknown >= 50:
        return "基准未解析"
    if overseas:
        if commodity >= 30:
            return "QDII-商品/另类"
        if equity >= 70:
            return "QDII-权益型"
        if bond + cash >= 80 and equity < 10:
            return "QDII-债券型"
        if equity <= 30:
            return "QDII-稳健配置型"
        if equity <= 70:
            return "QDII-均衡配置型"
        return "QDII-权益型"
    if commodity >= 50:
        return "商品/另类型"
    if cash >= 80 and equity < 5 and bond < 25:
        return "货币/现金型"
    if bond + cash >= 90 and equity < 5:
        return "纯债型"
    if equity <= 10:
        return "固收+低权益(0-10%)"
    if equity <= 30:
        return "偏债混合型(10-30%)"
    if equity <= 50:
        return "平衡混合型(30-50%)"
    if equity <= 70:
        return "偏股混合型(50-70%)"
    return "权益型(70-100%)"


def parse_benchmark(benchmark: str, public_class: str = "", fund_type: str = "", name: str = "") -> dict[str, Any]:
    text = clean(benchmark)
    evidence_text = " ".join([text, clean(public_class), clean(fund_type), clean(name)])
    overseas = has_any(evidence_text, OVERSEAS_KEYWORDS)
    if not text:
        fallback = "基准未披露"
        return {
            "基准细分分类": fallback,
            "解析置信度": "低",
            "解析置信度分数": 0.1,
            "基准权益权重_百分比": None,
            "基准风险资产权重": "",
            "基准风险资产权重_百分比": None,
            "基准债券权重_百分比": None,
            "基准货币权重_百分比": None,
            "基准商品权重_百分比": None,
            "基准海外权重_百分比": None,
            "基准未知权重_百分比": None,
            "基准权重合计_百分比": None,
            "基准解析说明": "未披露业绩比较基准，不能按基准细分排名。",
            "是否海外基准": 1 if overseas else 0,
        }

    glidepath = variable_glidepath_result(text, public_class, fund_type, name)
    if glidepath is not None:
        return glidepath

    components = split_components(text)
    weights = {"equity": 0.0, "bond": 0.0, "cash": 0.0, "commodity": 0.0, "unknown": 0.0, "overseas": 0.0}
    explicit_weights = 0
    assigned_weights = 0
    unknown_components: list[str] = []
    for component in components:
        weight, explicit = component_weight(component, len(components))
        if weight is None:
            unknown_components.append(component)
            continue
        asset_type = component_asset_type(component)
        weights[asset_type] += weight
        if has_any(component, OVERSEAS_KEYWORDS):
            weights["overseas"] += weight
        if explicit:
            explicit_weights += 1
        assigned_weights += 1
        if asset_type == "unknown":
            unknown_components.append(component)

    total_weight = sum(weights[k] for k in ("equity", "bond", "cash", "commodity", "unknown"))
    parsed = assigned_weights > 0 and total_weight > 0
    if weights["overseas"] > 0:
        overseas = True
    classification = classify_by_weights(weights, overseas, parsed)

    if not parsed:
        score = 0.2
    else:
        total_gap = abs(total_weight - 100.0)
        unknown_weight = weights["unknown"]
        if explicit_weights == len(components) and total_gap <= 8 and unknown_weight <= 10:
            score = 0.9
        elif total_gap <= 25 and unknown_weight <= 25:
            score = 0.6
        else:
            score = 0.35
    if classification in {"基准未解析", "基准未披露"}:
        score = min(score, 0.35)

    pieces = [
        f"权益{weights['equity']:.1f}%",
        f"债券{weights['bond']:.1f}%",
        f"货币{weights['cash']:.1f}%",
        f"商品/另类{weights['commodity']:.1f}%",
        f"未知{weights['unknown']:.1f}%",
        f"合计{total_weight:.1f}%",
    ]
    if unknown_components:
        pieces.append(f"未识别片段{len(unknown_components)}个")
    return {
        "基准细分分类": classification,
        "解析置信度": confidence_label(score),
        "解析置信度分数": round(score, 4),
        "基准权益权重_百分比": round_or_none(weights["equity"]),
        "基准风险资产权重": risk_asset_bucket(
            weights["equity"] + weights["commodity"],
            parsed and classification not in {"基准未解析", "基准未披露"} and weights["unknown"] <= 0,
        ),
        "基准风险资产权重_百分比": round_or_none(weights["equity"] + weights["commodity"]),
        "基准债券权重_百分比": round_or_none(weights["bond"]),
        "基准货币权重_百分比": round_or_none(weights["cash"]),
        "基准商品权重_百分比": round_or_none(weights["commodity"]),
        "基准海外权重_百分比": round_or_none(weights["overseas"]),
        "基准未知权重_百分比": round_or_none(weights["unknown"]),
        "基准权重合计_百分比": round_or_none(total_weight),
        "基准解析说明": "；".join(pieces),
        "是否海外基准": 1 if overseas else 0,
    }


def percentile_rank(value: float | None, pool: list[float]) -> tuple[int | None, int, float | None, float | None]:
    valid = sorted([item for item in pool if item is not None and math.isfinite(item)], reverse=True)
    if value is None or not valid:
        return None, len(valid), None, None
    rank = 1 + sum(1 for item in valid if item > value)
    denominator = max(len(valid), rank)
    percentile = rank / denominator if denominator else None
    beat = 1 - percentile if percentile is not None else None
    return rank, len(valid), beat, percentile


def mean(values: list[float]) -> float | None:
    valid = [item for item in values if item is not None and math.isfinite(item)]
    return round_or_none(sum(valid) / len(valid)) if valid else None


def median(values: list[float]) -> float | None:
    valid = [item for item in values if item is not None and math.isfinite(item)]
    return round_or_none(statistics.median(valid)) if valid else None


def category_summary(
    strategy_rows: list[dict[str, Any]],
    fof_rows: list[dict[str, Any]],
    category_field: str,
    strategy_category_field: str,
) -> list[dict[str, Any]]:
    categories = sorted(
        set(clean(row.get(strategy_category_field)) for row in strategy_rows if clean(row.get(strategy_category_field)))
        | set(clean(row.get(category_field)) for row in fof_rows if clean(row.get(category_field)))
    )
    out: list[dict[str, Any]] = []
    for category in categories:
        strategies = [row for row in strategy_rows if row.get(strategy_category_field) == category and to_float(row.get("策略H1收益率_百分比")) is not None]
        fofs = [row for row in fof_rows if row.get(category_field) == category]
        fofs_with_return = [row for row in fofs if to_float(row.get("上半年收益率_百分比")) is not None]
        percentiles = [to_float(row.get("排名位置百分位")) for row in strategies if to_float(row.get("排名位置百分位")) is not None]
        out.append(
            {
                "分类": category,
                "策略数量": len(strategies),
                "对客策略数量": sum(1 for row in strategies if row.get("是否对客") == "是"),
                "FOF产品总数": len(fofs),
                "有收益FOF产品数": len(fofs_with_return),
                "策略平均H1收益率_百分比": mean([to_float(row.get("策略H1收益率_百分比")) for row in strategies]),
                "策略中位数H1收益率_百分比": median([to_float(row.get("策略H1收益率_百分比")) for row in strategies]),
                "FOF平均H1收益率_百分比": mean([to_float(row.get("上半年收益率_百分比")) for row in fofs_with_return]),
                "FOF中位数H1收益率_百分比": median([to_float(row.get("上半年收益率_百分比")) for row in fofs_with_return]),
                "策略平均排名百分位": mean(percentiles),
                "策略中位数排名百分位": median(percentiles),
            }
        )
    out.sort(key=lambda row: (0 if row["有收益FOF产品数"] >= 10 else 1, row["分类"]))
    return out


def ranking_category_summary(strategy_rows: list[dict[str, Any]], fof_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    categories = sorted(set(clean(row.get("排名采用分类")) for row in strategy_rows if clean(row.get("排名采用分类"))))
    out: list[dict[str, Any]] = []
    for category in categories:
        strategies = [row for row in strategy_rows if row.get("排名采用分类") == category and to_float(row.get("策略H1收益率_百分比")) is not None]
        bucket_fofs = [
            row
            for row in fof_rows
            if clean(row.get("基准风险资产权重")) == category and row.get("解析置信度") in {"高", "中"}
        ]
        if bucket_fofs:
            fofs = bucket_fofs
        elif category.startswith("FOF-") or category == "QDII-FOF":
            fofs = [row for row in fof_rows if row.get("FOF可比分类") == category]
        else:
            fofs = [
                row
                for row in fof_rows
                if row.get("FOF基准细分分类") == category and row.get("解析置信度") in {"高", "中"}
            ]
        fofs_with_return = [row for row in fofs if to_float(row.get("上半年收益率_百分比")) is not None]
        percentiles = [to_float(row.get("排名位置百分位")) for row in strategies if to_float(row.get("排名位置百分位")) is not None]
        out.append(
            {
                "分类": category,
                "策略数量": len(strategies),
                "对客策略数量": sum(1 for row in strategies if row.get("是否对客") == "是"),
                "FOF产品总数": len(fofs),
                "有收益FOF产品数": len(fofs_with_return),
                "策略平均H1收益率_百分比": mean([to_float(row.get("策略H1收益率_百分比")) for row in strategies]),
                "策略中位数H1收益率_百分比": median([to_float(row.get("策略H1收益率_百分比")) for row in strategies]),
                "FOF平均H1收益率_百分比": mean([to_float(row.get("上半年收益率_百分比")) for row in fofs_with_return]),
                "FOF中位数H1收益率_百分比": median([to_float(row.get("上半年收益率_百分比")) for row in fofs_with_return]),
                "策略平均排名百分位": mean(percentiles),
                "策略中位数排名百分位": median(percentiles),
            }
        )
    out.sort(key=lambda row: (0 if row["有收益FOF产品数"] >= 10 else 1, row["分类"]))
    return out


def ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl_type: str) -> None:
    columns = {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()}
    if column not in columns:
        conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {ddl_type}')


def write_sqlite(db_path: Path, rows: list[dict[str, Any]], run_id: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS "FOF基准细分分类" (
              "基金代码" TEXT PRIMARY KEY,
              "基金名称" TEXT,
              "FOF公开分类" TEXT,
              "FOF基准细分分类" TEXT,
              "解析置信度" TEXT,
              "解析置信度分数" REAL,
              "业绩比较基准" TEXT,
              "F10基金类型" TEXT,
              "基准权益权重_百分比" REAL,
              "基准风险资产权重" TEXT,
              "基准风险资产权重_百分比" REAL,
              "基准债券权重_百分比" REAL,
              "基准货币权重_百分比" REAL,
              "基准商品权重_百分比" REAL,
              "基准海外权重_百分比" REAL,
              "基准未知权重_百分比" REAL,
              "基准权重合计_百分比" REAL,
              "基准解析说明" TEXT,
              "采集状态" TEXT,
              "采集批次" TEXT,
              "更新时间" TEXT
            )
            """
        )
        ensure_column(conn, "FOF基准细分分类", "基准风险资产权重", "TEXT")
        ensure_column(conn, "FOF基准细分分类", "基准风险资产权重_百分比", "REAL")
        now = now_text()
        for row in rows:
            conn.execute(
                """
                INSERT INTO "FOF基准细分分类" (
                  "基金代码","基金名称","FOF公开分类","FOF基准细分分类","解析置信度",
                  "解析置信度分数","业绩比较基准","F10基金类型","基准权益权重_百分比",
                  "基准风险资产权重","基准风险资产权重_百分比","基准债券权重_百分比","基准货币权重_百分比","基准商品权重_百分比",
                  "基准海外权重_百分比","基准未知权重_百分比","基准权重合计_百分比",
                  "基准解析说明","采集状态","采集批次","更新时间"
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT("基金代码") DO UPDATE SET
                  "基金名称"=excluded."基金名称",
                  "FOF公开分类"=excluded."FOF公开分类",
                  "FOF基准细分分类"=excluded."FOF基准细分分类",
                  "解析置信度"=excluded."解析置信度",
                  "解析置信度分数"=excluded."解析置信度分数",
                  "业绩比较基准"=excluded."业绩比较基准",
                  "F10基金类型"=excluded."F10基金类型",
                  "基准权益权重_百分比"=excluded."基准权益权重_百分比",
                  "基准风险资产权重"=excluded."基准风险资产权重",
                  "基准风险资产权重_百分比"=excluded."基准风险资产权重_百分比",
                  "基准债券权重_百分比"=excluded."基准债券权重_百分比",
                  "基准货币权重_百分比"=excluded."基准货币权重_百分比",
                  "基准商品权重_百分比"=excluded."基准商品权重_百分比",
                  "基准海外权重_百分比"=excluded."基准海外权重_百分比",
                  "基准未知权重_百分比"=excluded."基准未知权重_百分比",
                  "基准权重合计_百分比"=excluded."基准权重合计_百分比",
                  "基准解析说明"=excluded."基准解析说明",
                  "采集状态"=excluded."采集状态",
                  "采集批次"=excluded."采集批次",
                  "更新时间"=excluded."更新时间"
                """,
                [
                    row.get("基金代码"),
                    row.get("基金名称"),
                    row.get("FOF公开分类"),
                    row.get("FOF基准细分分类"),
                    row.get("解析置信度"),
                    row.get("解析置信度分数"),
                    row.get("业绩比较基准"),
                    row.get("F10基金类型"),
                    row.get("基准权益权重_百分比"),
                    row.get("基准风险资产权重"),
                    row.get("基准风险资产权重_百分比"),
                    row.get("基准债券权重_百分比"),
                    row.get("基准货币权重_百分比"),
                    row.get("基准商品权重_百分比"),
                    row.get("基准海外权重_百分比"),
                    row.get("基准未知权重_百分比"),
                    row.get("基准权重合计_百分比"),
                    row.get("基准解析说明"),
                    row.get("F10采集状态"),
                    run_id,
                    now,
                ],
            )
        conn.commit()
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Add FOF public/benchmark classifications and rerank strategies against benchmark-derived FOF peer groups.")
    parser.add_argument("--input-data", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--benchmark-data", type=Path, default=DEFAULT_BENCHMARK)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--run-id", default=default_run_id())
    parser.add_argument("--min-benchmark-peer-size", type=int, default=10)
    parser.add_argument("--skip-db", action="store_true")
    args = parser.parse_args()

    base = read_json(args.input_data)
    benchmark_payload = read_json(args.benchmark_data)
    benchmark_by_code = {str(row.get("基金代码") or ""): row for row in benchmark_payload.get("rows", [])}

    fof_rows: list[dict[str, Any]] = []
    for row in base.get("fofRows") or []:
        item = dict(row)
        code = clean(item.get("基金代码"))
        bench = benchmark_by_code.get(code, {})
        public_class = clean(bench.get("FOF公开分类")) or clean(item.get("天天基金细分类")) or "未分类"
        benchmark_text = clean(bench.get("业绩比较基准"))
        parsed = parse_benchmark(benchmark_text, public_class, clean(bench.get("F10基金类型")), clean(item.get("基金名称")))
        item.update(
            {
                "FOF公开分类": public_class,
                "F10基金类型": clean(bench.get("F10基金类型")),
                "业绩比较基准": benchmark_text,
                "FOF基准细分分类": parsed["基准细分分类"],
                "基准风险资产权重": parsed["基准风险资产权重"],
                "FOF基准风险资产权重": parsed["基准风险资产权重"],
                "基准风险资产权重_百分比": parsed["基准风险资产权重_百分比"],
                "解析置信度": parsed["解析置信度"],
                "解析置信度分数": parsed["解析置信度分数"],
                "基准权益权重_百分比": parsed["基准权益权重_百分比"],
                "基准债券权重_百分比": parsed["基准债券权重_百分比"],
                "基准货币权重_百分比": parsed["基准货币权重_百分比"],
                "基准商品权重_百分比": parsed["基准商品权重_百分比"],
                "基准海外权重_百分比": parsed["基准海外权重_百分比"],
                "基准未知权重_百分比": parsed["基准未知权重_百分比"],
                "基准权重合计_百分比": parsed["基准权重合计_百分比"],
                "基准解析说明": parsed["基准解析说明"],
                "F10采集状态": clean(bench.get("采集状态")) or "未采集",
                "F10页面URL": clean(bench.get("F10页面URL")),
            }
        )
        fof_rows.append(item)

    benchmark_pools: dict[str, list[float]] = defaultdict(list)
    benchmark_bucket_pools: dict[str, list[float]] = defaultdict(list)
    broad_pools: dict[str, list[float]] = defaultdict(list)
    for row in fof_rows:
        value = to_float(row.get("上半年收益率_百分比"))
        if value is None:
            continue
        broad_pools[clean(row.get("FOF可比分类"))].append(value)
        if row.get("解析置信度") in {"高", "中"} and clean(row.get("FOF基准细分分类")) not in {"", "基准未披露", "基准未解析"}:
            benchmark_pools[clean(row.get("FOF基准细分分类"))].append(value)
        if row.get("解析置信度") in {"高", "中"} and clean(row.get("基准风险资产权重")):
            benchmark_bucket_pools[clean(row.get("基准风险资产权重"))].append(value)

    strategy_rows: list[dict[str, Any]] = []
    ranking_basis_counts: Counter[str] = Counter()
    for row in base.get("strategyRows") or []:
        item = dict(row)
        parsed = parse_benchmark(clean(item.get("业绩基准")), clean(item.get("FOF可比分类")), "", clean(item.get("策略名称")))
        benchmark_class = parsed["基准细分分类"]
        benchmark_bucket = parsed["基准风险资产权重"]
        broad_class = clean(item.get("FOF可比分类"))
        use_bucket = parsed["解析置信度"] in {"高", "中"} and benchmark_bucket in benchmark_bucket_pools
        if use_bucket:
            pool = benchmark_bucket_pools[benchmark_bucket]
            ranking_class = benchmark_bucket
            ranking_basis = "基准风险资产权重"
        else:
            pool = broad_pools.get(broad_class, [])
            ranking_class = broad_class
            if parsed["解析置信度"] in {"高", "中"} and benchmark_bucket:
                ranking_basis = "基准风险资产权重样本不足，回退公开可比分类"
            else:
                ranking_basis = "策略基准未稳定解析，回退公开可比分类"
        h1 = to_float(item.get("策略H1收益率_百分比"))
        rank, pool_size, beat, percentile = percentile_rank(h1, pool)
        ranking_basis_counts[ranking_basis] += 1
        item.update(
            {
                "策略基准细分分类": benchmark_class,
                "策略基准风险资产权重": benchmark_bucket,
                "基准风险资产权重": benchmark_bucket,
                "策略基准风险资产权重_百分比": parsed["基准风险资产权重_百分比"],
                "基准风险资产权重_百分比": parsed["基准风险资产权重_百分比"],
                "策略基准解析置信度": parsed["解析置信度"],
                "策略基准解析置信度分数": parsed["解析置信度分数"],
                "策略基准权益权重_百分比": parsed["基准权益权重_百分比"],
                "策略基准债券权重_百分比": parsed["基准债券权重_百分比"],
                "策略基准货币权重_百分比": parsed["基准货币权重_百分比"],
                "策略基准商品权重_百分比": parsed["基准商品权重_百分比"],
                "策略基准海外权重_百分比": parsed["基准海外权重_百分比"],
                "策略基准解析说明": parsed["基准解析说明"],
                "原同类FOF样本数": item.get("同类FOF样本数"),
                "原同类FOF排名": item.get("同类FOF排名"),
                "排名采用分类口径": ranking_basis,
                "排名采用分类": ranking_class,
                "同类FOF样本数": pool_size,
                "同类FOF排名": rank,
                "击败同类FOF比例": round_or_none(beat, 6),
                "排名位置百分位": round_or_none(percentile, 6),
            }
        )
        strategy_rows.append(item)

    benchmark_category_rows = category_summary(strategy_rows, fof_rows, "FOF基准细分分类", "策略基准细分分类")
    benchmark_bucket_category_rows = category_summary(strategy_rows, fof_rows, "基准风险资产权重", "策略基准风险资产权重")
    ranking_category_rows = ranking_category_summary(strategy_rows, fof_rows)
    public_category_rows = category_summary(strategy_rows, fof_rows, "FOF可比分类", "FOF可比分类")

    confidence_counts = Counter(row.get("解析置信度") or "未识别" for row in fof_rows)
    strategy_confidence_counts = Counter(row.get("策略基准解析置信度") or "未识别" for row in strategy_rows)
    benchmark_class_counts = Counter(row.get("FOF基准细分分类") or "未分类" for row in fof_rows)
    benchmark_bucket_counts = Counter(row.get("基准风险资产权重") or "未分档" for row in fof_rows)
    strategy_bucket_counts = Counter(row.get("策略基准风险资产权重") or "未分档" for row in strategy_rows)
    f10_status_counts = Counter(row.get("F10采集状态") or "未采集" for row in fof_rows)
    missing_benchmark_rows = [
        {
            "基金代码": row.get("基金代码"),
            "基金名称": row.get("基金名称"),
            "FOF公开分类": row.get("FOF公开分类"),
            "F10采集状态": row.get("F10采集状态"),
        }
        for row in fof_rows
        if not row.get("业绩比较基准")
    ]
    low_confidence_rows = [
        {
            "基金代码": row.get("基金代码"),
            "基金名称": row.get("基金名称"),
            "FOF公开分类": row.get("FOF公开分类"),
            "业绩比较基准": row.get("业绩比较基准"),
            "基准解析说明": row.get("基准解析说明"),
        }
        for row in fof_rows
        if row.get("解析置信度") == "低"
    ][:200]

    meta = dict(base.get("meta") or {})
    meta.update(
        {
            "报告名称": "2026年上半年投顾策略-FOF基准细分排名报表",
            "生成时间": now_text(),
            "runId": args.run_id,
            "上游收益数据批次": (base.get("meta") or {}).get("runId"),
            "FOF总数": len(fof_rows),
            "FOF公开分类字段": "FOF公开分类",
            "FOF基准细分分类字段": "FOF基准细分分类",
            "FOF基准风险资产权重字段": "基准风险资产权重",
            "解析置信度字段": "解析置信度",
            "F10业绩比较基准覆盖数": sum(1 for row in fof_rows if row.get("业绩比较基准")),
            "F10业绩比较基准覆盖率": round(sum(1 for row in fof_rows if row.get("业绩比较基准")) / len(fof_rows), 6) if fof_rows else None,
            "FOF基准风险资产权重覆盖数": sum(1 for row in fof_rows if row.get("基准风险资产权重")),
            "FOF基准风险资产权重覆盖率": round(sum(1 for row in fof_rows if row.get("基准风险资产权重")) / len(fof_rows), 6) if fof_rows else None,
            "FOF基准解析置信度分布": dict(confidence_counts),
            "策略基准解析置信度分布": dict(strategy_confidence_counts),
            "FOF基准细分分类分布": dict(benchmark_class_counts),
            "FOF基准风险资产权重分布": dict(benchmark_bucket_counts),
            "策略基准风险资产权重分布": dict(strategy_bucket_counts),
            "F10采集状态分布": dict(f10_status_counts),
            "策略排名口径分布": dict(ranking_basis_counts),
            "基准细分排名最小FOF样本数": args.min_benchmark_peer_size,
            "基准细分FOF收益样本分布": {key: len(value) for key, value in sorted(benchmark_pools.items())},
            "基准风险资产权重FOF收益样本分布": {key: len(value) for key, value in sorted(benchmark_bucket_pools.items())},
            "公开可比FOF收益样本分布": {key: len(value) for key, value in sorted(broad_pools.items())},
        }
    )

    notes = list(base.get("notes") or [])
    notes.extend(
        [
            {"项目": "FOF公开分类", "说明": "来自天天基金公开分类字段，优先使用本地基金标准分类字典中的天天基金细分类，并通过 F10 采集结果留痕。"},
            {"项目": "FOF基准细分分类", "说明": "从天天 F10 的业绩比较基准原文解析权益、债券、货币、商品和海外权重，并按权重区间划入细分可比类别。"},
            {"项目": "基准风险资产权重", "说明": "按基准中的权益、商品和另类资产合计权重划分 L0-L10 档，投顾策略和 FOF 基金均优先使用该字段作为混排分类。"},
            {"项目": "解析置信度", "说明": "高：基准成分权重完整且未知成分很少；中：可解析但权重合计或部分片段存在瑕疵；低：基准缺失、不可解析或未知成分较多。"},
            {"项目": "策略重排", "说明": "策略基准可解析且对应 FOF 基准风险资产权重有收益样本时，使用基准风险资产权重重排；否则回退原 FOF可比分类。"},
        ]
    )

    payload = {
        "meta": meta,
        "strategyRows": strategy_rows,
        "fofRows": fof_rows,
        "benchmarkCategoryRows": benchmark_category_rows,
        "benchmarkBucketCategoryRows": benchmark_bucket_category_rows,
        "rankingCategoryRows": ranking_category_rows,
        "publicCategoryRows": public_category_rows,
        "categoryRows": ranking_category_rows,
        "missingBenchmarkRows": missing_benchmark_rows[:500],
        "lowConfidenceRows": low_confidence_rows,
        "notes": notes,
    }
    out_dir = args.output_root / args.run_id
    output_path = out_dir / "fof_benchmark_classified_ranking_data.json"
    write_json(output_path, payload)
    write_json(args.output_root / "latest_fof_benchmark_classified_ranking_data.json", payload)
    if not args.skip_db:
        write_sqlite(args.db_path, fof_rows, args.run_id)
    print(json.dumps(meta, ensure_ascii=False, indent=2), flush=True)
    print(f"[output] {output_path}", flush=True)


if __name__ == "__main__":
    main()
