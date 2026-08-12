#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Audit mixed advisor/public-fund performance source and scatter pack quality."""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
FORMAL_ROOT = PROJECT_ROOT / "site"
DEFAULT_SOURCE = FORMAL_ROOT / "reports" / "advisor_public_fund_mixed_performance_20260630" / "workbook_source.json"
DEFAULT_SCATTER = FORMAL_ROOT / "basic_data" / "data" / "mixed_performance_scatter_pack.json"
DEFAULT_QA = FORMAL_ROOT / "reports" / "advisor_public_fund_mixed_performance_20260630" / "qa_summary.json"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "mixed_performance_data_quality_audit"
CN_TZ = timezone(timedelta(hours=8))

INTERVALS = ["上半年", "今年以来", "近1月", "近3月", "近6月", "近1年"]
MISSING_BUCKETS = {"", "未分档", "未知", "NA", "N/A", "-"}
WEIGHT_FIELDS = [
    "基准权益权重",
    "基准债券权重",
    "基准货币权重",
    "基准商品权重",
    "基准另类权重",
    "基准未知权重",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--scatter", type=Path, default=DEFAULT_SCATTER)
    parser.add_argument("--qa", type=Path, default=DEFAULT_QA)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--fail-on-error", action="store_true")
    return parser.parse_args()


def clean(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"", "none", "nan", "null"} else text


def number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def has_bucket(row: dict[str, Any]) -> bool:
    return clean(row.get("基准风险资产权重")) not in MISSING_BUCKETS


def interval_complete(row: dict[str, Any], interval: str) -> bool:
    return (
        number(row.get(f"{interval}收益率")) is not None
        and number(row.get(f"{interval}最大回撤")) is not None
        and number(row.get(f"{interval}年化波动率")) is not None
    )


def interval_has_return(row: dict[str, Any], interval: str) -> bool:
    return number(row.get(f"{interval}收益率")) is not None


def counter_dict(values: list[Any] | tuple[Any, ...]) -> dict[str, int]:
    return dict(Counter(clean(value) or "空" for value in values).most_common())


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8-sig")
        return
    headers: list[str] = []
    for row in rows:
        for key in row:
            if key not in headers:
                headers.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def vector_total(row: dict[str, Any]) -> float | None:
    explicit = number(row.get("基准互斥权重合计_百分比"))
    if explicit is not None:
        return explicit
    values = [number(row.get(field)) for field in WEIGHT_FIELDS]
    if all(value is None for value in values):
        return None
    return sum(value or 0 for value in values) * 100


def benchmark_vector_issues(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    total_issues: list[dict[str, Any]] = []
    unknown_issues: list[dict[str, Any]] = []
    for row in rows:
        if not has_bucket(row):
            continue
        total = vector_total(row)
        unknown = number(row.get("基准未知权重")) or 0
        base = {
            "产品类型": row.get("产品类型"),
            "产品代码": row.get("产品代码"),
            "产品名称": row.get("产品名称"),
            "机构": row.get("机构"),
            "基准风险资产权重": row.get("基准风险资产权重"),
            "基准风险资产权重来源": row.get("基准风险资产权重来源"),
            "正式可比池": row.get("正式可比池"),
            "业绩比较基准": row.get("业绩比较基准"),
            "基准互斥权重合计_百分比": total,
            "基准未知权重": unknown,
        }
        if total is None or abs(total - 100) > 0.01:
            total_issues.append(base)
        if unknown > 0.0001:
            unknown_issues.append(base)
    return total_issues, unknown_issues


def build_report(summary: dict[str, Any]) -> str:
    lines = [
        "# 投顾策略 + 公募基金混排数据质量专项稽核",
        "",
        f"- 生成时间：{summary['generatedAt']}",
        f"- 源包日期：{summary['sourceMeta'].get('asOfDate')}",
        f"- 源包行数：{summary['sourceMeta'].get('exportRowCount')}；点阵包行数：{summary['scatterMeta'].get('includedRowCount')}",
        f"- QA 状态：{json.dumps(summary['qaStatusCounts'], ensure_ascii=False)}",
        "",
        "## 数量链路",
        "",
        f"- 原始投顾：{summary['sourceMeta'].get('rawStrategyRowCount')}；导出投顾：{summary['sourceMeta'].get('strategyRowCount')}；点阵包投顾：{summary['scatterProductTypeCounts'].get('投顾策略', 0)}。",
        f"- 信号类策略导出：{summary['signalStrategyCount']}；其中进入点阵包：{summary['signalStrategyInScatterCount']}。",
        f"- 有分档但无完整收益-风险指标：源包 {summary['sourceNoCompleteMetricCount']}；点阵包保留 {summary['scatterNoCompleteMetricCount']}。",
        f"- 未分档产品：源包 {summary['sourceUnbucketedCount']}；点阵包剔除 {summary['scatterMeta'].get('excludedUnbucketedRowCount')}。",
        "",
        "## 基准与指标检查",
        "",
        f"- 基准互斥权重合计异常：{summary['benchmarkVectorTotalIssueCount']}。",
        f"- 基准未知权重大于阈值：{summary['benchmarkUnknownWeightIssueCount']}。",
        f"- 上半年有收益样本：{summary['returnCoverageByInterval'].get('上半年', {}).get('hasReturn', 0)}；上半年收益+回撤+波动完整样本：{summary['returnCoverageByInterval'].get('上半年', {}).get('complete', 0)}。",
        "",
        "## QA 抽样覆盖",
        "",
        f"- QA 样本总数：{summary['qaRowCount']}；公募基金：{summary['qaProductTypeCounts'].get('公募基金', 0)}；投顾策略：{summary['qaProductTypeCounts'].get('投顾策略', 0)}。",
        f"- 投顾 QA 覆盖机构数：{summary['strategyQaInstitutionCount']}。",
        f"- QA 基准来源覆盖：{json.dumps(summary['qaBenchmarkSourceCounts'], ensure_ascii=False)}。",
        "",
        "## 结论",
        "",
    ]
    if summary["errorCount"]:
        lines.append(f"- 存在 {summary['errorCount']} 类 error，需要修复后再用于业务结论。")
    else:
        lines.append("- 未发现阻断级 error。")
    if summary["warnCount"]:
        lines.append(f"- 存在 {summary['warnCount']} 类 warn，需在业务解释中披露或继续补数。")
    else:
        lines.append("- 未发现 warn。")
    lines.extend(
        [
            "",
            "## 主要提示",
            "",
            "- 缺风险指标的产品已保留在列表，但不参与点阵坐标绘制和风险中位线计算。",
            "- 收益排名按当前区间有收益的产品计算；缺收益的产品不计算排名。",
            "- 广发外部原始代码产品若无法与官方披露净值一致核对，风险指标继续留空，避免伪造回撤和波动。",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    source = json.loads(args.source.read_text(encoding="utf-8"))
    scatter = json.loads(args.scatter.read_text(encoding="utf-8"))
    qa = json.loads(args.qa.read_text(encoding="utf-8")) if args.qa.exists() else {"qaRows": []}

    source_rows = source.get("rows") or []
    scatter_rows = scatter.get("rows") or []
    qa_rows = qa.get("qaRows") or []

    source_unbucketed = [row for row in source_rows if not has_bucket(row)]
    source_no_complete = [row for row in source_rows if has_bucket(row) and not any(interval_complete(row, interval) for interval in INTERVALS)]
    scatter_no_complete = [row for row in scatter_rows if not row.get("completeIntervals")]
    signal_rows = [row for row in source_rows if clean(row.get("生命周期状态")) == "信号类策略"]
    signal_scatter_ids = {clean(row.get("id")) for row in scatter_rows}
    signal_in_scatter = [
        row for row in signal_rows
        if clean(row.get("产品ID")) in signal_scatter_ids or clean(row.get("产品代码")) in signal_scatter_ids
    ]
    vector_total_issues, unknown_issues = benchmark_vector_issues(source_rows)

    return_coverage = {}
    for interval in INTERVALS:
        has_return = [row for row in source_rows if has_bucket(row) and interval_has_return(row, interval)]
        complete = [row for row in source_rows if has_bucket(row) and interval_complete(row, interval)]
        return_coverage[interval] = {
            "hasReturn": len(has_return),
            "complete": len(complete),
            "hasReturnByProductType": counter_dict([row.get("产品类型") for row in has_return]),
            "completeByProductType": counter_dict([row.get("产品类型") for row in complete]),
        }

    qa_status_counts = counter_dict([row.get("核对状态") for row in qa_rows])
    issues: list[dict[str, Any]] = []
    if any(status != "通过" for status in qa_status_counts):
        issues.append({"severity": "error", "ruleId": "MIXED_QA_NOT_ALL_PASS", "count": sum(count for status, count in qa_status_counts.items() if status != "通过")})
    if vector_total_issues:
        issues.append({"severity": "error", "ruleId": "BENCHMARK_VECTOR_TOTAL_INVALID", "count": len(vector_total_issues)})
    if unknown_issues:
        issues.append({"severity": "warn", "ruleId": "BENCHMARK_UNKNOWN_WEIGHT_PRESENT", "count": len(unknown_issues)})
    if source_unbucketed:
        issues.append({"severity": "warn", "ruleId": "MIXED_UNBUCKETED_PRODUCTS_EXCLUDED_FROM_SCATTER", "count": len(source_unbucketed)})
    if scatter_no_complete:
        issues.append({"severity": "warn", "ruleId": "MIXED_PRODUCTS_WITH_INCOMPLETE_RISK_METRICS", "count": len(scatter_no_complete)})

    now = datetime.now(CN_TZ).replace(microsecond=0)
    output_dir = args.output_root / now.strftime("%Y%m%dT%H%M%S%z")
    output_dir.mkdir(parents=True, exist_ok=True)

    summary = {
        "generatedAt": now.isoformat(),
        "sourcePath": str(args.source),
        "scatterPath": str(args.scatter),
        "qaPath": str(args.qa),
        "sourceMeta": source.get("meta") or {},
        "scatterMeta": scatter.get("meta") or {},
        "sourceProductTypeCounts": counter_dict([row.get("产品类型") for row in source_rows]),
        "scatterProductTypeCounts": counter_dict([row.get("productType") for row in scatter_rows]),
        "sourceUnbucketedCount": len(source_unbucketed),
        "sourceNoCompleteMetricCount": len(source_no_complete),
        "scatterNoCompleteMetricCount": len(scatter_no_complete),
        "signalStrategyCount": len(signal_rows),
        "signalStrategyInScatterCount": len(signal_in_scatter),
        "benchmarkVectorTotalIssueCount": len(vector_total_issues),
        "benchmarkUnknownWeightIssueCount": len(unknown_issues),
        "benchmarkSourceCounts": counter_dict([row.get("基准风险资产权重来源") for row in source_rows if has_bucket(row)]),
        "returnCoverageByInterval": return_coverage,
        "qaRowCount": len(qa_rows),
        "qaStatusCounts": qa_status_counts,
        "qaProductTypeCounts": counter_dict([row.get("产品类型") for row in qa_rows]),
        "qaFundMainTypeCounts": counter_dict([row.get("基金主类型") for row in qa_rows]),
        "qaBenchmarkSourceCounts": counter_dict([row.get("基准风险资产权重来源") for row in qa_rows]),
        "strategyQaInstitutionCount": len({clean(row.get("机构")) for row in qa_rows if clean(row.get("产品类型")) == "投顾策略"}),
        "issues": issues,
        "errorCount": sum(1 for issue in issues if issue["severity"] == "error"),
        "warnCount": sum(1 for issue in issues if issue["severity"] == "warn"),
    }

    (output_dir / "mixed_performance_data_quality_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "mixed_performance_data_quality_report.md").write_text(build_report(summary), encoding="utf-8")
    write_csv(output_dir / "unbucketed_products.csv", source_unbucketed)
    write_csv(output_dir / "incomplete_metric_products.csv", source_no_complete)
    write_csv(output_dir / "benchmark_vector_total_issues.csv", vector_total_issues)
    write_csv(output_dir / "benchmark_unknown_weight_issues.csv", unknown_issues)
    write_csv(output_dir / "qa_rows.csv", qa_rows)

    print(json.dumps({"outputDir": str(output_dir), **summary}, ensure_ascii=False, indent=2))
    return 1 if args.fail_on_error and summary["errorCount"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
