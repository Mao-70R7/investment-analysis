from __future__ import annotations

import argparse
import csv
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Iterable


PROBE_ROOT = Path(__file__).resolve().parent


SPECIAL_PAGE_NOTES = {
    "南方梦想佳": "目录卡片跳转到支付宝登录页，未暴露且慢策略代码或详情数据。",
    "风和日丽": "详情页停留在骨架屏，未形成可解析内容。",
    "马拉松固收增强": "自定义营销页可见，但未暴露标准策略路由和策略代码。",
    "简慢投资组合": "自定义介绍页为空白，未暴露标准策略路由和策略代码。",
}


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def xml_texts(path: Path) -> list[str]:
    root = ET.parse(path).getroot()
    return [node.attrib["text"].strip() for node in root.iter("node") if node.attrib.get("text", "").strip()]


def exact_benchmark(texts: Iterable[str]) -> str | None:
    for text in texts:
        match = re.match(r"^业绩(?:比较)?基准[：:]\s*(.+?)\s*$", text)
        if match and match.group(1):
            return match.group(1)
    return None


def has_performance_summary(texts: Iterable[str]) -> bool:
    joined = "\n".join(texts)
    return all(term in joined for term in ("累计收益", "年化收益", "最大回撤"))


def build_rows(
    catalog: dict[str, Any],
    first_screen: dict[str, Any],
    xixi_texts: list[str],
    holding_ocr: dict[str, Any],
) -> list[dict[str, Any]]:
    ocr_by_name = {row["strategy_name"]: row for row in first_screen.get("rows", [])}
    xixi_benchmark = exact_benchmark(xixi_texts)
    xixi_has_summary = has_performance_summary(xixi_texts)
    confirmed_holding_count = int(holding_ocr.get("unique_fund_code_count") or 0)
    rows: list[dict[str, Any]] = []

    for mapping in catalog.get("mappings", []):
        name = str(mapping["strategy_name"])
        detail_texts = [str(item) for item in mapping.get("detail_text_sample", [])]
        ocr_assessment = ocr_by_name.get(name, {}).get("assessment", {})
        summary_available = bool(ocr_assessment.get("performance_keyword_seen")) or has_performance_summary(detail_texts)
        benchmark = exact_benchmark(detail_texts)
        if name == "息息相关":
            summary_available = summary_available or xixi_has_summary
            benchmark = benchmark or xixi_benchmark

        holding_constituent_count = confirmed_holding_count if name == "息息相关" else 0
        route_mapped = mapping.get("status") == "mapped" and bool(mapping.get("source_strategy_id"))
        missing: list[str] = []
        if not summary_available:
            missing.append("业绩摘要")
        missing.append("结构化日度业绩序列")
        if not benchmark:
            missing.append("精确基准名称或公式")
        if not holding_constituent_count:
            missing.append("已验证的基金级持仓名单")
        missing.append("单基金仓位占比及持仓日期")
        page_note = SPECIAL_PAGE_NOTES.get(name)
        if page_note:
            missing.append(page_note)

        rows.append(
            {
                "strategy_name": name,
                "source_strategy_id": mapping.get("source_strategy_id"),
                "route_mapped": route_mapped,
                "performance_summary_available": summary_available,
                "performance_daily_series_complete": False,
                "exact_benchmark_available": bool(benchmark),
                "exact_benchmark": benchmark,
                "holding_constituent_list_confirmed": holding_constituent_count > 0,
                "holding_constituent_count": holding_constituent_count,
                "holding_fund_weight_complete": False,
                "holding_snapshot_date_available": False,
                "all_three_strictly_complete": False,
                "missing_or_limitation": "；".join(missing),
            }
        )
    return rows


def summarize(rows: list[dict[str, Any]], public_api: dict[str, Any]) -> dict[str, Any]:
    count = len(rows)
    return {
        "catalog_strategy_count": count,
        "mapped_strategy_code_count": sum(bool(row["route_mapped"]) for row in rows),
        "performance_summary_available_count": sum(bool(row["performance_summary_available"]) for row in rows),
        "performance_summary_missing_count": sum(not row["performance_summary_available"] for row in rows),
        "performance_daily_series_complete_count": sum(bool(row["performance_daily_series_complete"]) for row in rows),
        "performance_daily_series_missing_count": sum(not row["performance_daily_series_complete"] for row in rows),
        "exact_benchmark_available_count": sum(bool(row["exact_benchmark_available"]) for row in rows),
        "exact_benchmark_missing_count": sum(not row["exact_benchmark_available"] for row in rows),
        "holding_constituent_list_confirmed_count": sum(bool(row["holding_constituent_list_confirmed"]) for row in rows),
        "holding_fund_weight_complete_count": sum(bool(row["holding_fund_weight_complete"]) for row in rows),
        "holding_fund_weight_missing_count": sum(not row["holding_fund_weight_complete"] for row in rows),
        "all_three_strictly_complete_count": sum(bool(row["all_three_strictly_complete"]) for row in rows),
        "public_protected_endpoint_probe_count": int(public_api.get("protected_endpoint_probe_count") or 0),
        "public_protected_endpoint_nonempty_count": int(public_api.get("protected_endpoint_nonempty_count") or 0),
        "strict_definition": {
            "performance": "可落库的带日期结构化日度序列，而非截图或累计收益摘要",
            "benchmark": "明确的基准名称或拆分公式；基准日度序列另计，当前为零",
            "holding": "基金代码、名称、单基金仓位占比、明确持仓日期，且权重可对账",
        },
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def yes_no(value: Any) -> str:
    return "是" if value else "否"


def write_markdown(path: Path, summary: dict[str, Any], rows: list[dict[str, Any]], evidence: dict[str, str]) -> None:
    lines = [
        "# 且慢严选策略数据覆盖评估",
        "",
        "## 结论",
        "",
        f"- 严选策略目录：{summary['catalog_strategy_count']} 个；已映射标准策略代码 {summary['mapped_strategy_code_count']} 个。",
        f"- 可读业绩摘要：{summary['performance_summary_available_count']} 个；缺摘要 {summary['performance_summary_missing_count']} 个。",
        f"- 可落库的结构化日度业绩序列：{summary['performance_daily_series_complete_count']} 个。",
        f"- 精确基准名称或公式：{summary['exact_benchmark_available_count']} 个；缺精确基准 {summary['exact_benchmark_missing_count']} 个。",
        f"- 已验证基金级持仓名单：{summary['holding_constituent_list_confirmed_count']} 个策略；单基金仓位占比完整：{summary['holding_fund_weight_complete_count']} 个。",
        f"- 业绩、基准、基金权重三项严格完整：{summary['all_three_strictly_complete_count']} 个。",
        "",
        "这里将业绩摘要与日度业绩序列分开；将基金类型占比、基金名单与单基金仓位占比分开。截图曲线、日涨跌和类别分布均不升级为完整数据。",
        "",
        "## 策略明细",
        "",
        "| 策略 | 代码 | 业绩摘要 | 日度序列 | 精确基准 | 基金名单 | 基金权重 | 主要缺口 |",
        "|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        limitation = str(row["missing_or_limitation"]).replace("|", "\\|")
        lines.append(
            "| {strategy_name} | {source_strategy_id} | {perf} | {daily} | {benchmark} | {holdings} | {weights} | {limitation} |".format(
                strategy_name=row["strategy_name"],
                source_strategy_id=row["source_strategy_id"] or "—",
                perf=yes_no(row["performance_summary_available"]),
                daily=yes_no(row["performance_daily_series_complete"]),
                benchmark=row["exact_benchmark"] or "否",
                holdings=(f"是（{row['holding_constituent_count']}只）" if row["holding_constituent_list_confirmed"] else "未验证"),
                weights=yes_no(row["holding_fund_weight_complete"]),
                limitation=limitation,
            )
        )
    lines.extend(["", "## 证据", ""])
    lines.extend(f"- {label}: `{value}`" for label, value in evidence.items())
    lines.extend(
        [
            "",
            "## 技术边界",
            "",
            f"- 公开态共探测 {summary['public_protected_endpoint_probe_count']} 个受保护详情请求，非空响应 {summary['public_protected_endpoint_nonempty_count']} 个。",
            "- APK/前端反编译已定位详情、净值、调仓、候选基金等接口，但访问令牌由 App 原生桥注入；非调试包、run-as 拒绝且 WebView 调试端口未开放。",
            "- 已展开的基金表只有基金、最新操作、日涨跌三列，未出现单基金仓位字段；日涨跌百分比不能当成仓位占比。",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a strict coverage report for the authenticated Qieman curated catalog.")
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--first-screen", type=Path, required=True)
    parser.add_argument("--xixi-detail-xml", type=Path, required=True)
    parser.add_argument("--holding-ocr", type=Path, required=True)
    parser.add_argument("--public-api", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    catalog = read_json(args.catalog)
    first_screen = read_json(args.first_screen)
    holding_ocr = read_json(args.holding_ocr)
    public_api = read_json(args.public_api)
    rows = build_rows(catalog, first_screen, xml_texts(args.xixi_detail_xml), holding_ocr)
    summary = summarize(rows, public_api)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    evidence = {
        "目录枚举": str(args.catalog.resolve()),
        "首屏 OCR": str(args.first_screen.resolve()),
        "息息相关完整详情结构": str(args.xixi_detail_xml.resolve()),
        "息息相关展开持仓 OCR": str(args.holding_ocr.resolve()),
        "公开接口探测": str(args.public_api.resolve()),
    }
    payload = {
        "state": "qieman_catalog_coverage_assessed",
        "summary": summary,
        "rows": rows,
        "evidence": evidence,
    }
    (args.output_dir / "coverage_report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    write_csv(args.output_dir / "coverage_matrix.csv", rows)
    write_markdown(args.output_dir / "coverage_report.md", summary, rows, evidence)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
