# -*- coding: utf-8 -*-
"""Build the mixed advisor/public-fund return-risk scatter page pack.

The source workbook pack keeps every public fund, including products without
local NAV metrics. The page keeps every product with a benchmark risk-asset bucket.
Rows without complete return-risk metrics remain visible in the table with
missing metrics shown as ``--``; the scatter plot only draws rows with numeric
coordinates for the selected interval and risk metric.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
FORMAL_ROOT = PROJECT_ROOT / "site"
DEFAULT_SOURCE = (
    FORMAL_ROOT
    / "reports"
    / "advisor_public_fund_mixed_performance_latest"
    / "workbook_source.json"
)

INTERVALS = ["上半年", "今年以来", "近1月", "近3月", "近6月", "近1年"]
BUCKET_ORDER = [f"L{i}" for i in range(11)]
BUCKET_LABELS = {
    "L0": "L0 / 0%",
    "L1": "L1 / 0-10%",
    "L2": "L2 / 10-20%",
    "L3": "L3 / 20-30%",
    "L4": "L4 / 30-40%",
    "L5": "L5 / 40-50%",
    "L6": "L6 / 50-60%",
    "L7": "L7 / 60-70%",
    "L8": "L8 / 70-80%",
    "L9": "L9 / 80-90%",
    "L10": "L10 / 90-100%",
}
MISSING_BUCKETS = {"", "未分档", "未知", "NA", "N/A", "-"}
BROAD_UNKNOWN_TOLERANCE = 0.0001


def as_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none"} else text


def is_yes(value: Any) -> bool:
    return clean_text(value) == "是"


def has_bucket(row: dict[str, Any]) -> bool:
    return clean_text(row.get("基准风险资产权重")) not in MISSING_BUCKETS


def risk_asset_weight(row: dict[str, Any]) -> float | None:
    direct = as_number(row.get("基准风险资产权重_百分比"))
    if direct is not None:
        return max(0.0, min(1.0, direct))
    weights = [
        as_number(row.get("基准权益权重")),
        as_number(row.get("基准商品权重")),
        as_number(row.get("基准另类权重")),
    ]
    if all(value is None for value in weights):
        return None
    unknown = as_number(row.get("基准未知权重")) or 0.0
    if unknown > BROAD_UNKNOWN_TOLERANCE:
        return None
    return max(0.0, min(1.0, sum(value or 0.0 for value in weights)))


def risk_asset_bucket(value: float | None) -> str:
    if value is None:
        return ""
    percent = value * 100.0
    if percent <= 0:
        return "L0"
    return f"L{min(10, max(1, math.ceil(percent / 10.0)))}"


def risk_asset_note(row: dict[str, Any], value: float | None, bucket: str) -> str:
    unknown = as_number(row.get("基准未知权重")) or 0.0
    if unknown > BROAD_UNKNOWN_TOLERANCE:
        return "基准未知权重超过0.01%，基准风险资产权重不硬分档。"
    if value is None or not bucket:
        return "缺少权益、商品、另类权重，无法计算基准风险资产权重。"
    return "基准风险资产权重=基准权益+基准商品+基准另类；港股/海外权益是权益子项，不重复计入。"


def canonicalize_risk_asset_terms(value: Any) -> str:
    text = clean_text(value)
    for legacy in ("广义权益分档", "基准权益分类档", "基准权益分档", "权益分档"):
        text = text.replace(legacy, "基准风险资产权重")
    return text


def interval_metrics(row: dict[str, Any], interval: str) -> dict[str, Any]:
    ret = as_number(row.get(f"{interval}收益率"))
    drawdown = as_number(row.get(f"{interval}最大回撤"))
    volatility = as_number(row.get(f"{interval}年化波动率"))
    nav_points = as_number(row.get(f"{interval}风险净值点数"))
    complete = ret is not None and drawdown is not None and volatility is not None
    return {
        "return": ret,
        "maxDrawdown": drawdown,
        "volatility": volatility,
        "navPoints": nav_points,
        "range": clean_text(row.get(f"{interval}区间")),
        "returnSource": clean_text(row.get(f"{interval}收益来源")),
        "riskSource": clean_text(row.get(f"{interval}风险来源")),
        "complete": complete,
    }


def build_row(row: dict[str, Any]) -> dict[str, Any] | None:
    intervals = {name: interval_metrics(row, name) for name in INTERVALS}
    complete_intervals = [name for name, metrics in intervals.items() if metrics["complete"]]

    product_type = clean_text(row.get("产品类型"))
    fund_main_type = clean_text(row.get("基金主类型"))
    institution = clean_text(row.get("机构"))
    product_name = clean_text(row.get("产品名称"))
    is_guangfa = is_yes(row.get("是否广发")) or "广发" in institution
    detail_url = clean_text(row.get("详情链接"))
    if not detail_url:
        code = clean_text(row.get("产品代码"))
        product_id = clean_text(row.get("产品ID"))
        detail_url = f"./fund.html?code={code}" if product_type == "公募基金" else f"./strategy.html?id={product_id}"
    broad_weight = risk_asset_weight(row)
    canonical_bucket = risk_asset_bucket(broad_weight) or clean_text(row.get("基准风险资产权重"))

    return {
        "id": clean_text(row.get("产品ID")) or clean_text(row.get("产品代码")) or product_name,
        "code": clean_text(row.get("产品代码")),
        "name": product_name,
        "productType": product_type,
        "fundMainType": fund_main_type if product_type == "公募基金" else "投顾策略",
        "fundTypeTags": clean_text(row.get("基金类型标签")),
        "institution": institution,
        "channel": clean_text(row.get("渠道")),
        "manager": clean_text(row.get("管理人/经理")),
        "isCustomer": clean_text(row.get("是否对客")),
        "isGuangfa": is_guangfa,
        "isFof": is_yes(row.get("是否FOF")),
        "isQdii": is_yes(row.get("是否QDII")),
        "isEtf": is_yes(row.get("是否ETF")),
        "isLof": is_yes(row.get("是否LOF")),
        "isReits": is_yes(row.get("是否REITs")),
        "assetClass": clean_text(row.get("标准资产大类")),
        "assetSubClass": clean_text(row.get("标准资产细类")),
        "bucket": canonical_bucket,
        "bucketLabel": BUCKET_LABELS.get(canonical_bucket, canonical_bucket),
        "bucketSource": clean_text(row.get("基准风险资产权重来源")),
        "bucketNote": canonicalize_risk_asset_terms(row.get("基准风险资产权重说明")),
        "broadEquityWeight": broad_weight,
        "broadEquityBucket": canonical_bucket,
        "broadEquityBucketLabel": BUCKET_LABELS.get(canonical_bucket, canonical_bucket),
        "broadEquityNote": risk_asset_note(row, broad_weight, canonical_bucket),
        "broadEquityMethod": "基准风险资产权重=权益+商品+另类；港股/海外权益不重复计入，未知权重超过0.01%不硬分档。",
        "hasBenchmark": clean_text(row.get("有基准")) == "是",
        "hasPerformance": clean_text(row.get("有业绩走势")) == "是",
        "hasHistoryPosition": clean_text(row.get("有历史仓位")) == "是",
        "clientActive": clean_text(row.get("对客未终止")) == "是",
        "comparisonTrack": clean_text(row.get("非权益比较轨道")),
        "formalPeerPool": clean_text(row.get("正式可比池")),
        "peerPoolEligible": clean_text(row.get("可比池样本资格")) == "是",
        "peerPoolNote": canonicalize_risk_asset_terms(row.get("可比池说明")),
        "absoluteReturnRank": as_number(row.get("绝对收益排名") or row.get("排名")),
        "bucketMixedRank": as_number(row.get("同档混排排名")),
        "peerRank": as_number(row.get("同类可比排名")),
        "peerSampleCount": as_number(row.get("同类可比样本数")),
        "peerTopQuartile": clean_text(row.get("同类前25%")),
        "benchmark": clean_text(row.get("业绩比较基准")),
        "benchmarkStatus": clean_text(row.get("业绩基准获取状态")),
        "benchmarkParseNote": canonicalize_risk_asset_terms(row.get("基准解析说明")),
        "confidence": clean_text(row.get("解析置信度")),
        "confidenceScore": as_number(row.get("解析置信度分数")),
        "benchmarkEquityWeight": as_number(row.get("基准权益权重")),
        "benchmarkBondWeight": as_number(row.get("基准债券权重")),
        "benchmarkCashWeight": as_number(row.get("基准货币权重")),
        "benchmarkCommodityWeight": as_number(row.get("基准商品权重")),
        "benchmarkAlternativeWeight": as_number(row.get("基准另类权重")),
        "benchmarkHkEquityWeight": as_number(row.get("基准港股权益权重")),
        "benchmarkOverseasEquityWeight": as_number(row.get("基准海外权益权重")),
        "benchmarkOverseasWeight": as_number(row.get("基准海外权重")),
        "benchmarkUnknownWeight": as_number(row.get("基准未知权重")),
        "benchmarkMutuallyExclusiveTotal": as_number(row.get("基准互斥权重合计_百分比")),
        "navCount": as_number(row.get("本地净值记录数")),
        "navStart": clean_text(row.get("本地净值起始日")),
        "navEnd": clean_text(row.get("本地净值截止日")),
        "establishedDate": clean_text(row.get("成立日期")),
        "detailUrl": detail_url,
        "intervals": intervals,
        "completeIntervals": complete_intervals,
    }


def count_rows(rows: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    counters = {
        "productType": Counter(),
        "fundMainType": Counter(),
        "bucket": Counter(),
        "broadEquityBucket": Counter(),
        "bucketSource": Counter(),
        "comparisonTrack": Counter(),
        "formalPeerPool": Counter(),
        "institution": Counter(),
        "intervalComplete": Counter(),
    }
    for row in rows:
        counters["productType"][row["productType"] or "未标识"] += 1
        counters["fundMainType"][row["fundMainType"] or "未标识"] += 1
        counters["bucket"][row["bucket"] or "未分档"] += 1
        counters["broadEquityBucket"][row["broadEquityBucket"] or "未分档"] += 1
        counters["bucketSource"][row["bucketSource"] or "未标识"] += 1
        counters["comparisonTrack"][row["comparisonTrack"] or "未形成轨道"] += 1
        counters["formalPeerPool"][row["formalPeerPool"] or "未进入"] += 1
        counters["institution"][row["institution"] or "未标识"] += 1
        for interval in row["completeIntervals"]:
            counters["intervalComplete"][interval] += 1
    return {key: dict(counter) for key, counter in counters.items()}


def portable_source_reference(source_path: Path, formal_root: Path | None) -> str:
    source = source_path.resolve()
    if formal_root is None:
        return str(source)
    try:
        return source.relative_to(formal_root.resolve()).as_posix()
    except ValueError:
        return str(source)


def build_pack(source_path: Path, *, formal_root: Path | None = None) -> dict[str, Any]:
    source = json.loads(source_path.read_text(encoding="utf-8"))
    source_rows = source.get("rows") or []
    included: list[dict[str, Any]] = []
    included_no_complete = 0

    for raw in source_rows:
        row = build_row(raw)
        if row is None:
            included_no_complete += 1
            continue
        if not row["completeIntervals"]:
            included_no_complete += 1
        included.append(row)

    included.sort(
        key=lambda item: (
            BUCKET_ORDER.index(item["bucket"]) if item["bucket"] in BUCKET_ORDER else 99,
            item["productType"],
            item["fundMainType"],
            item["name"],
            item["id"],
        )
    )

    meta = {
        "title": "投顾策略 + 公募基金全市场产品排名",
        "asOfDate": source.get("meta", {}).get("asOfDate") or "",
        "intervalAsOfDates": source.get("meta", {}).get("intervalAsOfDates") or {},
        "generatedAt": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "sourceWorkbookPack": portable_source_reference(source_path, formal_root),
        "sourceRowCount": len(source_rows),
        "includedRowCount": len(included),
        "excludedUnbucketedRowCount": 0,
        "includedUnbucketedRowCount": sum(1 for row in included if not clean_text(row.get("bucket")) or clean_text(row.get("bucket")) in MISSING_BUCKETS),
        "excludedNoCompleteMetricRowCount": 0,
        "includedNoCompleteMetricRowCount": included_no_complete,
        "guangfaRowCount": sum(1 for row in included if row["isGuangfa"]),
        "strategyRowCount": sum(1 for row in included if row["productType"] == "投顾策略"),
        "guangfaStrategyRowCount": sum(1 for row in included if row["productType"] == "投顾策略" and row["isGuangfa"]),
        "publicFundRowCount": sum(1 for row in included if row["productType"] == "公募基金"),
        "intervals": INTERVALS,
        "bucketOrder": BUCKET_ORDER,
        "bucketLabels": BUCKET_LABELS,
        "broadEquityBucketOrder": BUCKET_ORDER,
        "broadEquityBucketLabels": BUCKET_LABELS,
        "counts": count_rows(included),
        "filterPolicy": "保留策略列表当前可查询策略和全市场主份额公募基金；未分档、缺收益或缺风险指标产品继续保留并以--展示，点阵只绘制当前区间收益和风险坐标齐全的产品。",
        "rateUnit": "ratio",
    }
    return {"meta": meta, "rows": included}


def write_pack(pack: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "mixed_performance_scatter_pack.json"
    js_path = output_dir / "mixed_performance_scatter_pack.js"
    payload = json.dumps(pack, ensure_ascii=False, separators=(",", ":"))
    json_path.write_text(payload, encoding="utf-8")
    js_path.write_text(
        "window.__MIXED_PERFORMANCE_SCATTER_PACK__ = " + payload + ";\n",
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--formal-root", type=Path, default=FORMAL_ROOT)
    parser.add_argument("--dev-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--no-dev-copy", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    pack = build_pack(args.source, formal_root=args.formal_root)
    formal_data_dir = args.formal_root / "basic_data" / "data"
    write_pack(pack, formal_data_dir)
    if not args.no_dev_copy:
        write_pack(pack, args.dev_root / "basic_data" / "data")
    print(json.dumps(pack["meta"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
