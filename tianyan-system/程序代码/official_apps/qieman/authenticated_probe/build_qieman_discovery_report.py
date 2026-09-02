from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Any, Iterable


VALID_STRATEGY_ID_RE = re.compile(r"^(?:(?:ZH|SI)\d+|J\d+)$")
TARGET_NAMES = ("我要稳稳的幸福", "超级理财加", "新锐定投组合", "搬砖小组B计划")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def valid_strategy_id(value: Any) -> bool:
    return bool(VALID_STRATEGY_ID_RE.fullmatch(str(value or "")))


def historical_id_mappings(runs_root: Path) -> dict[str, str]:
    by_name: dict[str, str] = {}
    for path in runs_root.glob("**/summary.json"):
        try:
            payload = read_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        strategy_id = payload.get("strategy_id")
        strategy_name = str(payload.get("strategy_name") or "").strip()
        if strategy_name and valid_strategy_id(strategy_id):
            by_name.setdefault(strategy_name, str(strategy_id))
    return by_name


def build_rows(
    public_rows: Iterable[dict[str, Any]],
    search_rows: Iterable[dict[str, Any]],
    historical_mappings: dict[str, str],
) -> list[dict[str, Any]]:
    public_by_name = {str(row["strategy_name"]): row for row in public_rows if row.get("strategy_name")}
    search_by_name = {str(row["strategy_name"]): row for row in search_rows if row.get("strategy_name")}
    names = sorted(set(public_by_name) | set(search_by_name) | set(historical_mappings))
    rows: list[dict[str, Any]] = []
    for name in names:
        public = public_by_name.get(name) or {}
        search = search_by_name.get(name) or {}
        public_id = public.get("source_strategy_id")
        historical_id = historical_mappings.get(name)
        source_strategy_id = public_id or historical_id
        extra = search.get("extra") or {}
        rows.append(
            {
                "strategy_name": name,
                "source_strategy_id": source_strategy_id,
                "advisor_name": public.get("advisor_name") or search.get("advisor_name"),
                "risk_level": public.get("risk_level") or search.get("risk_level"),
                "suggested_holding_period": public.get("suggested_holding_period")
                or search.get("suggested_holding_period"),
                "public_catalog_seen": bool(public),
                "authenticated_search_seen": bool(search),
                "historical_detail_mapped": bool(historical_id),
                "strategy_id_available": bool(source_strategy_id),
                "strategy_id_conflict": bool(public_id and historical_id and public_id != historical_id),
                "search_queries": extra.get("search_queries") or [],
                "search_snapshot_annualized_return": extra.get("historical_annualized_return"),
                "search_snapshot_max_drawdown": extra.get("historical_max_drawdown"),
                "formal_daily_performance_available": False,
                "strict_holding_complete": False,
            }
        )
    return rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0])
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({**row, "search_queries": "|".join(row["search_queries"])})


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    summary = report["summary"]
    targets = report["named_target_coverage"]
    lines = [
        "# 且慢投顾策略发现与完整性报告",
        "",
        "## 结论",
        "",
        f"- 当前可追溯发现 {summary['union_strategy_name_count']} 个策略名称，较旧严选单入口 16 个扩大到 {summary['coverage_multiplier_vs_old_16']:.2f} 倍。",
        f"- 其中 {summary['strategy_id_available_count']} 个已有稳定策略代码，{summary['strategy_id_missing_count']} 个仍只有登录态搜索名称。",
        f"- 公开 M4/严选目录 {summary['public_catalog_count']} 个；五个登录态搜索词并集 {summary['authenticated_search_count']} 个；重叠 {summary['public_search_overlap_count']} 个。",
        "- 结构化日度业绩、带明确持仓日期的完整基金权重、全量调仓明细目前仍未形成全策略数据集。",
        "",
        "## 用户点名策略",
        "",
    ]
    for name, row in targets.items():
        lines.append(f"- {name}: 已发现={row['discovered']}，策略代码={row.get('source_strategy_id') or '待补'}")
    lines.extend(
        [
            f"- 启明睿系列：{len(report['qimingrui_series'])} 个（{'、'.join(report['qimingrui_series'])}）。",
            "",
            "## 严格字段覆盖",
            "",
            f"- 已验证精确基准：{summary['exact_benchmark_sample_count']} 个策略样本。",
            f"- 已验证基金明细：{summary['sample_holding_rows']} 行；其中有精确权重 {summary['sample_precise_weight_rows']} 行。",
            f"- 带明确组合持仓日期的完整基金权重：{summary['strict_holding_complete_strategy_count']} 个策略。",
            f"- 结构化日度业绩：{summary['formal_daily_performance_strategy_count']} 个策略。",
            f"- 样本调仓事件：{summary['sample_rebalance_event_rows']} 条；调前/调后基金权重明细 {summary['sample_rebalance_delta_rows']} 行。",
            "",
            "## 可直接解决全量缺口的官方接口",
            "",
            "StarGate 官方 OpenAPI 已确认提供 SearchPortfolioStrategies、GetStrategyDetails、GetStrategyNavHistory、GetStrategyBenchmark、BatchGetStrategiesComposition 和 GetStrategyAdjustments。当前匿名调用返回 401 缺少 API 密钥；一次性 API Key 授权后可先取得精确总数，再批量补齐成立日、业绩、基准拆分、持仓和调仓。",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reconcile Qieman public, authenticated-search and sample evidence.")
    parser.add_argument("--public-run", type=Path, required=True)
    parser.add_argument("--search-run", type=Path, required=True)
    parser.add_argument("--stargate-run", type=Path, required=True)
    parser.add_argument("--runs-root", type=Path, required=True)
    parser.add_argument("--sample-evaluation", type=Path, required=True)
    parser.add_argument("--catalog-coverage", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    public_rows = read_jsonl(args.public_run / "normalized" / "strategy_master_candidates.jsonl")
    search_rows = read_jsonl(args.search_run / "normalized" / "strategy_master_candidates.jsonl")
    historical_mappings = historical_id_mappings(args.runs_root)
    rows = build_rows(public_rows, search_rows, historical_mappings)
    sample = read_json(args.sample_evaluation)
    catalog = read_json(args.catalog_coverage)
    stargate = read_json(args.stargate_run / "summary.json")
    exact_benchmark_names = {
        str(row["strategy_name"])
        for row in catalog.get("rows", [])
        if row.get("exact_benchmark_available")
    }
    exact_benchmark_names.update(
        str(row["strategy_name"]) for row in sample.get("samples", []) if row.get("benchmark")
    )
    public_names = {str(row["strategy_name"]) for row in public_rows}
    search_names = {str(row["strategy_name"]) for row in search_rows}
    aggregate = sample.get("aggregate_counts") or {}
    summary = {
        "old_single_catalog_count": 16,
        "public_catalog_count": len(public_names),
        "authenticated_search_count": len(search_names),
        "public_search_overlap_count": len(public_names & search_names),
        "union_strategy_name_count": len(rows),
        "coverage_multiplier_vs_old_16": len(rows) / 16,
        "strategy_id_available_count": sum(bool(row["strategy_id_available"]) for row in rows),
        "strategy_id_missing_count": sum(not row["strategy_id_available"] for row in rows),
        "strategy_id_conflict_count": sum(bool(row["strategy_id_conflict"]) for row in rows),
        "duplicate_strategy_name_count": len(rows) - len({row["strategy_name"] for row in rows}),
        "duplicate_known_business_key_count": len(
            [row for row in rows if row["source_strategy_id"]]
        )
        - len({row["source_strategy_id"] for row in rows if row["source_strategy_id"]}),
        "exact_benchmark_sample_count": len(exact_benchmark_names),
        "formal_daily_performance_strategy_count": 0,
        "sample_holding_rows": int(aggregate.get("strategy_fund_snapshot") or 0),
        "sample_precise_weight_rows": sum(int(row.get("precise_weight_rows") or 0) for row in sample.get("samples", [])),
        "strict_holding_complete_strategy_count": 0,
        "sample_rebalance_event_rows": int(aggregate.get("strategy_rebalance_event") or 0),
        "sample_rebalance_delta_rows": int(aggregate.get("strategy_rebalance_fund_delta") or 0),
        "stargate_state": stargate.get("state"),
        "stargate_unauthenticated_status": stargate.get("unauthenticated_search_status"),
    }
    by_name = {row["strategy_name"]: row for row in rows}
    target_coverage = {
        name: {
            "discovered": name in by_name,
            "source_strategy_id": by_name.get(name, {}).get("source_strategy_id"),
        }
        for name in TARGET_NAMES
    }
    qimingrui = sorted(name for name in by_name if name.startswith("启明睿"))
    report = {
        "state": "qieman_discovery_coverage_reconciled",
        "summary": summary,
        "named_target_coverage": target_coverage,
        "qimingrui_series": qimingrui,
        "exact_benchmark_sample_names": sorted(exact_benchmark_names),
        "rows": rows,
        "evidence": {
            "public_run": str(args.public_run.resolve()),
            "search_run": str(args.search_run.resolve()),
            "stargate_run": str(args.stargate_run.resolve()),
            "sample_evaluation": str(args.sample_evaluation.resolve()),
            "catalog_coverage": str(args.catalog_coverage.resolve()),
        },
        "quality_boundary": (
            "策略名称搜索候选不等于完整策略目录；卡片指标不等于日度业绩；"
            "缺组合持仓日期的精确权重样本不得进入正式持仓快照。"
        ),
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_json(args.output_dir / "discovery_coverage_report.json", report)
    write_csv(args.output_dir / "discovery_strategy_matrix.csv", rows)
    write_markdown(args.output_dir / "discovery_coverage_report.md", report)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
