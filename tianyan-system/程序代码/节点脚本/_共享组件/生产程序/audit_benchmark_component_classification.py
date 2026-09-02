from __future__ import annotations

import argparse
import csv
import json
import math
import re
import shutil
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from benchmark_asset_classification import (
    DYNAMIC_BENCHMARK_PATTERN,
    clean_text,
    compact_text,
    compute_benchmark_asset_mix,
    contains_non_classifiable_multi_asset_index,
    infer_generic_index_entry,
    is_fixed_return_part,
    is_static_benchmark_formula,
    load_benchmark_catalog,
    match_catalog_entry,
    normalize_formula_text,
    parse_weight_from_part,
    split_formula_parts,
)


ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_DB = ROOT / "data" / "analysis_zh_current.sqlite"
DEFAULT_OUTPUT_ROOT = ROOT / "outputs" / "benchmark_component_classification_audit"
DEFAULT_FORMAL_REPORT_DIR = ROOT / "site" / "reports"

VECTOR_FIELDS = ["权益", "债券", "现金", "商品", "另类", "未知"]
EXPECTED_VECTOR_FIELDS = {
    "权益": "基准资产大类-权益",
    "债券": "基准资产大类-债券",
    "现金": "基准资产大类-现金",
    "商品": "基准资产大类-商品",
    "另类": "基准资产大类-另类",
    "未知": "基准资产大类-其他",
}
FUND_VECTOR_FIELDS = {
    "权益": "基准权益权重_百分比",
    "债券": "基准债券权重_百分比",
    "现金": "基准货币权重_百分比",
    "商品": "基准商品权重_百分比",
    "另类": "基准另类权重_百分比",
    "未知": "基准未知权重_百分比",
}
STRATEGY_VECTOR_FIELDS = {
    "权益": "基准资产大类-权益",
    "债券": "基准资产大类-债券",
    "现金": "基准资产大类-现金",
    "商品": "基准资产大类-商品",
    "另类": "基准资产大类-另类",
    "未知": "基准资产大类-其他",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit benchmark component asset classifications and stored benchmark vectors.")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--formal-report-dir", type=Path, default=DEFAULT_FORMAL_REPORT_DIR)
    parser.add_argument("--no-formal-copy", action="store_true")
    return parser.parse_args()


def clean(value: Any) -> str:
    return clean_text(value, "") or ""


def as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def values_differ(left: Any, right: Any, tolerance: float = 0.011) -> bool:
    lhs = as_float(left)
    rhs = as_float(right)
    if lhs is None and rhs is None:
        return False
    if lhs is None or rhs is None:
        return True
    return abs(lhs - rhs) > tolerance


def unresolved_reason(benchmark: str, note: str, expected: dict[str, Any]) -> str:
    if is_fixed_return_part(benchmark):
        return "纯数值或绝对收益基准不包含资产成分，无法据此生成权益分档"
    if contains_non_classifiable_multi_asset_index(benchmark):
        return "多资产或动态基金指数没有固定权益权重，需报告日成分表"
    if DYNAMIC_BENCHMARK_PATTERN.search(benchmark):
        return "动态变量基准未披露当期变量值或可用年度权重表"
    if expected.get("基准映射置信度") == "低":
        return "已识别部分成分，但存在未映射指数或权重"
    if "估算" in note:
        return "存在经验估算，未取得带来源的当期权重"
    return "基准成分或权重尚未完整映射"


def compact_component_text(part: str) -> str:
    first = re.split(r"_x000D_|[\r\n]", part, maxsplit=1, flags=re.IGNORECASE)[0]
    first = re.sub(r"^(?:业绩比较基准|基准)\s*[=:：]?", "", first.strip())
    return first[:500]


def semantic_asset(part: str) -> tuple[str, str]:
    text = compact_text(part)
    if not text:
        return "", ""
    if part.count("指数") > 1 or len(set(re.findall(r"20\d{2}", part))) > 1:
        return "", ""
    if contains_non_classifiable_multi_asset_index(part):
        return "未知", "多资产或动态基金指数没有固定权益权重"
    if "股票" in text and any(term in text for term in ["黄金", "商品", "原油", "能源", "有色金属"]):
        return "权益", "资源产业股票指数仍属于权益"
    if any(term in text for term in ["货币基金", "货币型基金", "中证货币", "货币市场", "活期存款", "定期存款", "存款利率", "SHIBOR", "HIBOR", "LIBOR", "SOFR", "FR007", "DR007"]):
        return "现金", "名称明确为货币市场、货币基金或存款利率"
    if any(term in text for term in ["中债", "债券", "国债", "全债", "短债", "综合债", "综合全价总值", "纯债", "转债", "金融债", "企业债", "公司债", "信用债", "利率债", "同业存单", "短融", "可转债", "美元债", "GLOBALBOND", "AGGREGATEBOND"]):
        return "债券", "名称明确为债券指数"
    if "REIT" in text or "不动产" in text:
        return "另类", "名称明确为REITs或不动产指数"
    if any(term in text for term in ["CME中国商品", "中证商品", "商品CIFI", "商品CFI", "商品CFCI", "商品期货", "商品综合", "商品指数", "期货指数", "黄金", "白银", "AU9999", "原油", "WTI", "BRENT", "GSCI", "COMMODITY"]):
        return "商品", "名称明确为商品现货或期货指数"
    if any(term in text for term in ["沪深", "中证", "上证", "深证", "创业板", "科创", "恒生", "港股", "MSCI", "标普", "S&P", "纳斯达克", "股票", "偏股"]):
        return "权益", "名称具备明确股票或权益指数语义"
    return "", ""


def component_rows(formulas: dict[str, int], catalog: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    aggregate: dict[str, dict[str, Any]] = {}
    conflicts: list[dict[str, Any]] = []
    for formula, product_count in formulas.items():
        if "未披露" in formula or "暂无" in formula:
            continue
        normalized = normalize_formula_text(formula)
        for raw_part in split_formula_parts(normalized) or [normalized]:
            if is_fixed_return_part(raw_part):
                continue
            part = compact_component_text(raw_part)
            if not part:
                continue
            if not any(term in part.upper() for term in ["指数", "存款", "利率", "SHIBOR", "HIBOR", "LIBOR", "SOFR", "FR007", "DR007", "MSCI", "S&P", "WTI", "BRENT", "GSCI", "债"]):
                continue
            entry = match_catalog_entry(part, catalog)
            method = "指数目录"
            if entry is None:
                entry = infer_generic_index_entry(part)
                method = "明确语义规则" if entry is not None else "未映射"
            actual_major = entry.asset_major if entry is not None else "未知"
            actual_category = entry.asset_category if entry is not None else "未知"
            canonical_name = entry.index_name if entry is not None else ""
            expected_major, expected_reason = semantic_asset(part)
            status = "通过"
            issue = ""
            if contains_non_classifiable_multi_asset_index(part):
                status = "待确认"
                issue = "指数本身跨多资产或动态配置，不能按名称固定分类；产品须按报告日成分表处理"
            elif expected_major and actual_major != expected_major:
                status = "错误"
                issue = f"明确语义应为{expected_major}，当前识别为{actual_major}"
                conflicts.append(
                    {
                        "基准成分": part,
                        "当前分类": actual_major,
                        "应属分类": expected_major,
                        "判断依据": expected_reason,
                        "使用产品数": product_count,
                    }
                )
            elif entry is None:
                status = "待确认"
                issue = "未找到可靠指数目录或明确资产语义"
            key = compact_text(part)
            item = aggregate.setdefault(
                key,
                {
                    "基准成分": part,
                    "标准指数名称": canonical_name,
                    "资产大类": actual_major,
                    "资产类别": actual_category,
                    "识别方式": method,
                    "明确语义分类": expected_major,
                    "判断依据": expected_reason,
                    "稽核状态": status,
                    "问题说明": issue,
                    "使用产品数": 0,
                    "出现基准公式数": 0,
                    "示例权重": parse_weight_from_part(part),
                },
            )
            item["使用产品数"] += product_count
            item["出现基准公式数"] += 1
            if item["稽核状态"] == "通过" and status != "通过":
                item["稽核状态"] = status
                item["问题说明"] = issue
    return sorted(aggregate.values(), key=lambda item: (item["稽核状态"] != "错误", item["稽核状态"] != "待确认", -item["使用产品数"], item["基准成分"])), conflicts


def compare_product(
    product_type: str,
    product_id: str,
    product_name: str,
    benchmark: str,
    stored: dict[str, Any],
    stored_fields: dict[str, str],
    stored_bucket_field: str,
    expected: dict[str, Any],
) -> dict[str, Any] | None:
    if not benchmark or not is_static_benchmark_formula(benchmark):
        return None
    if clean(stored.get("基准风险资产权重来源")) == "年度披露权重核验":
        return None
    if expected.get("基准资产已映射权重") is None and expected.get("基准资产未映射权重") is None:
        return None
    expected_vector = {name: expected.get(field) for name, field in EXPECTED_VECTOR_FIELDS.items()}
    stored_vector = {name: stored.get(field) for name, field in stored_fields.items()}
    expected_bucket = clean(expected.get("基准风险资产权重"))
    stored_bucket = clean(stored.get(stored_bucket_field))
    changed = [name for name in VECTOR_FIELDS if values_differ(stored_vector.get(name), expected_vector.get(name))]
    if stored_bucket != expected_bucket:
        changed.append("权益分档")
    if not changed:
        return None
    return {
        "产品类型": product_type,
        "产品代码": product_id,
        "产品名称": product_name,
        "业绩比较基准": benchmark,
        "差异字段": "、".join(changed),
        "当前权益": stored_vector["权益"],
        "当前债券": stored_vector["债券"],
        "当前现金": stored_vector["现金"],
        "当前商品": stored_vector["商品"],
        "当前另类": stored_vector["另类"],
        "当前未知": stored_vector["未知"],
        "当前分档": stored_bucket,
        "应为权益": expected_vector["权益"],
        "应为债券": expected_vector["债券"],
        "应为现金": expected_vector["现金"],
        "应为商品": expected_vector["商品"],
        "应为另类": expected_vector["另类"],
        "应为未知": expected_vector["未知"],
        "应为分档": expected_bucket,
        "统一解析说明": expected.get("基准公式解析"),
        "映射置信度": expected.get("基准映射置信度"),
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for field in row:
            if field not in seen:
                seen.add(field)
                fields.append(field)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields or ["说明"])
        writer.writeheader()
        if rows:
            writer.writerows(rows)


def main() -> int:
    args = parse_args()
    if not args.db_path.exists():
        raise FileNotFoundError(args.db_path)
    run_id = datetime.now().strftime("%Y%m%dT%H%M%S")
    output_dir = args.output_root / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    catalog = load_benchmark_catalog()
    formula_counts: Counter[str] = Counter()
    mismatches: list[dict[str, Any]] = []
    uncertain_products: list[dict[str, Any]] = []
    formula_cache: dict[str, dict[str, Any]] = {}

    with sqlite3.connect(args.db_path) as conn:
        conn.row_factory = sqlite3.Row
        fund_rows = [dict(row) for row in conn.execute('SELECT * FROM "公募基金产品绩效快照"')]
        strategy_rows = [dict(row) for row in conn.execute('SELECT * FROM "策略基准资产配置"')]

    for row in fund_rows:
        benchmark = clean(row.get("业绩比较基准"))
        if benchmark:
            formula_counts[benchmark] += 1
        if not benchmark or "未披露" in benchmark or "暂无" in benchmark:
            continue
        expected = formula_cache.setdefault(benchmark, compute_benchmark_asset_mix(benchmark, catalog))
        mismatch = compare_product(
            "公募基金",
            clean(row.get("基金代码")),
            clean(row.get("基金名称")),
            benchmark,
            row,
            FUND_VECTOR_FIELDS,
            "基准风险资产权重",
            expected,
        )
        if mismatch:
            mismatches.append(mismatch)
        unknown = as_float(row.get("基准未知权重_百分比")) or 0.0
        note = clean(row.get("基准解析说明"))
        annual_verified = clean(row.get("基准风险资产权重来源")) == "年度披露权重核验"
        current_bucket = clean(row.get("基准风险资产权重"))
        if unknown > 0.01 or not current_bucket or (contains_non_classifiable_multi_asset_index(benchmark) and not annual_verified) or "估算" in note:
            uncertain_products.append(
                {
                    "产品类型": "公募基金",
                    "产品代码": row.get("基金代码"),
                    "产品名称": row.get("基金名称"),
                    "业绩比较基准": benchmark,
                    "未知权重": unknown,
                    "当前分档": row.get("基准风险资产权重"),
                    "当前解析说明": note,
                    "待确认原因": unresolved_reason(benchmark, note, expected),
                }
            )

    for row in strategy_rows:
        benchmark = clean(row.get("业绩基准文本"))
        if benchmark:
            formula_counts[benchmark] += 1
        if not benchmark:
            continue
        expected = formula_cache.setdefault(benchmark, compute_benchmark_asset_mix(benchmark, catalog))
        mismatch = compare_product(
            "投顾策略",
            clean(row.get("统一策略ID")),
            clean(row.get("策略名称")),
            benchmark,
            row,
            STRATEGY_VECTOR_FIELDS,
            "基准风险资产权重",
            expected,
        )
        if mismatch:
            mismatches.append(mismatch)
        unknown = as_float(row.get("基准资产大类-其他")) or 0.0
        current_bucket = clean(row.get("基准风险资产权重"))
        if unknown > 0.01 or not current_bucket or contains_non_classifiable_multi_asset_index(benchmark):
            uncertain_products.append(
                {
                    "产品类型": "投顾策略",
                    "产品代码": row.get("统一策略ID"),
                    "产品名称": row.get("策略名称"),
                    "业绩比较基准": benchmark,
                    "未知权重": unknown,
                    "当前分档": row.get("基准风险资产权重"),
                    "当前解析说明": row.get("基准公式解析"),
                    "待确认原因": unresolved_reason(benchmark, clean(row.get("基准公式解析")), expected),
                }
            )

    components, semantic_conflicts = component_rows(dict(formula_counts), catalog)
    unresolved_components = [row for row in components if row["稽核状态"] == "待确认"]
    summary = {
        "生成时间": datetime.now().astimezone().isoformat(timespec="seconds"),
        "数据库": str(args.db_path.resolve()),
        "投顾策略数": len(strategy_rows),
        "公募基金数": len(fund_rows),
        "公募基金基准未取得数": sum(1 for row in fund_rows if not clean(row.get("业绩比较基准"))),
        "公募基金基准未披露数": sum(1 for row in fund_rows if any(term in clean(row.get("业绩比较基准")) for term in ["未披露", "暂无"])),
        "有基准原文产品数": sum(formula_counts.values()),
        "唯一基准公式数": len(formula_counts),
        "唯一基准成分数": len(components),
        "静态基准向量不一致产品数": len(mismatches),
        "明确资产语义冲突成分数": len(semantic_conflicts),
        "待确认成分数": len(unresolved_components),
        "待确认产品数": len(uncertain_products),
        "结论": "通过" if not mismatches and not semantic_conflicts else "存在明确错误",
    }
    payload = {
        "summary": summary,
        "staticVectorMismatches": mismatches,
        "semanticConflicts": semantic_conflicts,
        "unresolvedComponents": unresolved_components,
        "uncertainProducts": uncertain_products,
        "components": components,
    }
    json_path = output_dir / "benchmark_component_classification_audit.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(output_dir / "明确错误_静态基准向量.csv", mismatches)
    write_csv(output_dir / "明确错误_资产语义冲突.csv", semantic_conflicts)
    write_csv(output_dir / "待确认基准成分.csv", unresolved_components)
    write_csv(output_dir / "待确认产品.csv", uncertain_products)
    write_csv(output_dir / "全部基准成分映射.csv", components)
    (args.output_root / "latest_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.output_root / "latest_report_path.txt").write_text(str(json_path.resolve()), encoding="utf-8")

    if not args.no_formal_copy:
        args.formal_report_dir.mkdir(parents=True, exist_ok=True)
        formal_dir = args.formal_report_dir / f"基准成分分类稽核_{run_id}"
        if formal_dir.exists():
            shutil.rmtree(formal_dir)
        shutil.copytree(output_dir, formal_dir)
        (args.formal_report_dir / "基准成分分类稽核_最新路径.txt").write_text(str(formal_dir), encoding="utf-8")
        summary["正式报告目录"] = str(formal_dir)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not mismatches and not semantic_conflicts else 2


if __name__ == "__main__":
    raise SystemExit(main())
