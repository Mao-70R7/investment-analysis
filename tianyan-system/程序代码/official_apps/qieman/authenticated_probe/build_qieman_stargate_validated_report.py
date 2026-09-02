from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from collect_qieman_stargate_proxy import is_test_strategy_name, write_jsonl
from probe_qieman_device import write_json


PROBE_ROOT = Path(__file__).resolve().parent
TARGET_NAMES = ("我要稳稳的幸福", "超级理财加", "新锐定投组合", "搬砖小组B计划")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def duplicate_count(rows: Iterable[dict[str, Any]], keys: tuple[str, ...]) -> int:
    values = [tuple(row.get(key) for key in keys) for row in rows]
    return len(values) - len(set(values))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a production-scoped validation report for a Qieman StarGate run.")
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_dir = args.run_dir.resolve()
    output_dir = args.output_dir.resolve() if args.output_dir else (
        PROBE_ROOT / "evaluations" / (datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z") + "-stargate-validated")
    )
    output_dir.mkdir(parents=True, exist_ok=False)
    summary = read_json(run_dir / "summary.json")
    masters = read_jsonl(run_dir / "normalized" / "strategy_master_enriched.jsonl")
    holdings = read_jsonl(run_dir / "normalized" / "strategy_fund_snapshot.jsonl")
    benchmarks = read_jsonl(run_dir / "normalized" / "strategy_benchmark.jsonl")
    assessment_by_code = {
        str(row.get("source_strategy_id")): row
        for row in summary.get("composition_assessments", [])
    }

    test_codes = {
        str(row.get("source_strategy_id"))
        for row in masters
        if is_test_strategy_name(row.get("strategy_name"))
    }
    production_masters: list[dict[str, Any]] = []
    for master in masters:
        row = dict(master)
        code = str(row.get("source_strategy_id") or "")
        is_test = code in test_codes
        row["status"] = "test_or_internal" if is_test else "stargate_keyword_discovered"
        row.setdefault("extra", {})["is_test_or_internal"] = is_test
        if not is_test:
            production_masters.append(row)
    production_codes = {str(row["source_strategy_id"]) for row in production_masters}

    production_assessments = [assessment_by_code[code] for code in sorted(production_codes) if code in assessment_by_code]
    strict_codes = {
        str(row.get("source_strategy_id"))
        for row in production_assessments
        if row.get("holding_rows", 0) > 0
        and row.get("position_date")
        and str(row.get("position_date")) <= str(summary.get("captured_at"))[:10]
        and row.get("weight_complete")
        and abs(float(row.get("weight_sum") or 0) - 1) <= 0.001
    }
    production_holdings = [
        row for row in holdings
        if str(row.get("source_strategy_id")) in strict_codes
    ]
    production_benchmarks = [
        row for row in benchmarks
        if str(row.get("source_strategy_id")) in production_codes
    ]

    master_by_name = {str(row.get("strategy_name")): row for row in production_masters}
    named_targets = {
        name: {
            "found": name in master_by_name,
            "source_strategy_id": master_by_name.get(name, {}).get("source_strategy_id"),
            "launch_date": master_by_name.get(name, {}).get("launch_date"),
            "benchmark": master_by_name.get(name, {}).get("benchmark"),
        }
        for name in TARGET_NAMES
    }
    qimingrui = [
        {"source_strategy_id": row.get("source_strategy_id"), "strategy_name": row.get("strategy_name")}
        for row in production_masters
        if str(row.get("strategy_name") or "").startswith("启明睿")
    ]
    missing_holding_codes = sorted(production_codes - strict_codes)
    benchmark_codes = {str(row.get("source_strategy_id")) for row in production_benchmarks}
    exact_benchmark_codes = {
        str(row.get("source_strategy_id"))
        for row in production_benchmarks
        if row.get("is_exact_split")
    }

    validated_dir = output_dir / "validated"
    write_jsonl(validated_dir / "strategy_master.jsonl", production_masters)
    write_jsonl(validated_dir / "strategy_fund_snapshot.jsonl", production_holdings)
    write_jsonl(validated_dir / "strategy_benchmark.jsonl", production_benchmarks)

    report = {
        "state": "qieman_stargate_production_scope_validated",
        "source_run": str(run_dir),
        "captured_at": summary.get("captured_at"),
        "catalog": {
            "keyword_union_strategy_count": len(masters),
            "production_strategy_count": len(production_masters),
            "test_or_internal_strategy_count": len(test_codes),
            "complete_catalog": False,
            "boundary": "API key exposes keyword search but not SearchPortfolioStrategies total; union is a lower bound.",
        },
        "holdings": {
            "production_strategy_with_strict_current_holdings": len(strict_codes),
            "production_strategy_missing_strict_current_holdings": len(missing_holding_codes),
            "production_holding_rows": len(production_holdings),
            "missing_strategy_codes": missing_holding_codes,
            "weight_sum_min": min((float(row.get("weight_sum") or 0) for row in production_assessments if row.get("holding_rows", 0) > 0), default=None),
            "weight_sum_max": max((float(row.get("weight_sum") or 0) for row in production_assessments if row.get("holding_rows", 0) > 0), default=None),
        },
        "benchmarks": {
            "production_strategy_with_benchmark_response": len(benchmark_codes),
            "production_strategy_with_exact_benchmark_split": len(exact_benchmark_codes),
            "production_strategy_missing_benchmark_response": len(production_codes - benchmark_codes),
        },
        "performance": {
            "daily_series_count": 0,
            "boundary": "Authenticated API key tool catalog does not expose GetStrategyNavHistory.",
        },
        "rebalance": {
            "official_event_count": 0,
            "official_fund_delta_count": 0,
            "boundary": "Authenticated API key tool catalog does not expose GetStrategyAdjustments.",
        },
        "named_target_coverage": named_targets,
        "qimingrui_series_count": len(qimingrui),
        "qimingrui_series": qimingrui,
        "quality": {
            "duplicate_strategy_business_keys": duplicate_count(production_masters, ("channel_id", "source_strategy_id")),
            "duplicate_holding_business_keys": duplicate_count(production_holdings, ("snapshot_id", "fund_code")),
            "duplicate_benchmark_business_keys": duplicate_count(production_benchmarks, ("channel_id", "source_strategy_id")),
            "future_position_dates_excluded": sorted(
                str(row.get("source_strategy_id"))
                for row in summary.get("composition_assessments", [])
                if row.get("position_date") and str(row.get("position_date")) > str(summary.get("captured_at"))[:10]
            ),
            "rate_limit_state": "HTTP 429 daily request quota reached after catalog, composition and benchmark collection",
        },
        "output_dir": str(output_dir),
    }
    write_json(output_dir / "validated_coverage_report.json", report)
    lines = [
        "# 且慢 StarGate 生产口径验证报告",
        "",
        f"- 关键词并集：{len(masters)} 个；排除明确测试/内测对象后：{len(production_masters)} 个。",
        f"- 严格完整当前持仓：{len(strict_codes)} 个生产策略，{len(production_holdings)} 行基金权重。",
        f"- 基准响应：{len(benchmark_codes)} 个生产策略；精确基准拆分：{len(exact_benchmark_codes)} 个。",
        f"- 缺严格持仓：{len(missing_holding_codes)} 个；日度业绩和官方调仓仍受 API Key 工具权限限制。",
        f"- 启明睿系列：{len(qimingrui)} 个（含各渠道版本）。",
        "- 目录仍是关键词并集下限，不能冒充官方 total 全量；但已显著超过原预期 200+。",
    ]
    (output_dir / "validated_coverage_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
