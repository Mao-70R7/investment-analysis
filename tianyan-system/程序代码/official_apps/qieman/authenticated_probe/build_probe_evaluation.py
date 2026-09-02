from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from probe_qieman_device import PROBE_ROOT, now_local, write_json


ENTITIES = (
    "strategy_master",
    "strategy_summary_metrics",
    "strategy_performance_daily",
    "strategy_performance_interval",
    "strategy_fund_snapshot",
    "strategy_rebalance_event",
    "strategy_rebalance_fund_delta",
    "strategy_asset_allocation_sample",
)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def validate_sample(run_dir: Path) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    coverage = read_json(run_dir / "coverage_assessment.json")
    entities = {name: read_jsonl(run_dir / "normalized" / f"{name}.jsonl") for name in ENTITIES}
    issues: list[dict[str, Any]] = []
    masters = entities["strategy_master"]
    if len(masters) != 1:
        issues.append({"code": "STRATEGY_MASTER_COUNT", "actual": len(masters), "expected": 1})

    holdings = entities["strategy_fund_snapshot"]
    holding_keys = [(row.get("snapshot_id"), row.get("fund_code")) for row in holdings]
    if len(holding_keys) != len(set(holding_keys)):
        issues.append({"code": "DUPLICATE_HOLDING_KEY"})
    invalid_precise = [row.get("fund_code") for row in holdings if row.get("is_precise_weight") and row.get("fund_weight") is None]
    if invalid_precise:
        issues.append({"code": "PRECISE_WEIGHT_IS_NULL", "fund_codes": invalid_precise})

    exact_weights = [float(row["fund_weight"]) for row in holdings if row.get("is_precise_weight")]
    all_precise = bool(holdings) and len(exact_weights) == len(holdings)
    weight_sum = round(sum(exact_weights), 6) if all_precise else None
    if all_precise and not 99.5 <= (weight_sum or 0) <= 100.5:
        issues.append({"code": "HOLDING_WEIGHT_SUM_OUT_OF_RANGE", "weight_sum": weight_sum})

    allocations = entities["strategy_asset_allocation_sample"]
    allocation_sum = round(sum(float(row["asset_weight"]) for row in allocations), 6) if allocations else None
    if allocations and not 99.5 <= (allocation_sum or 0) <= 100.5:
        issues.append({"code": "ASSET_ALLOCATION_SUM_OUT_OF_RANGE", "weight_sum": allocation_sum})

    strategy_id = coverage.get("strategy_id")
    summary = {
        "strategy_id": strategy_id,
        "strategy_name": coverage.get("strategy_name"),
        "source_run_dir": str(run_dir),
        "benchmark": masters[0].get("benchmark") if masters else None,
        "summary_as_of_date": masters[0].get("extra", {}).get("performance_summary", {}).get("as_of_date") if masters else None,
        "holding_rows": len(holdings),
        "precise_weight_rows": len(exact_weights),
        "weight_sum": weight_sum,
        "position_date_rows": sum(1 for row in holdings if row.get("position_date")),
        "asset_allocation_rows": len(allocations),
        "asset_allocation_sum": allocation_sum,
        "rebalance_event_rows": len(entities["strategy_rebalance_event"]),
        "rebalance_delta_rows": len(entities["strategy_rebalance_fund_delta"]),
        "daily_performance_rows": len(entities["strategy_performance_daily"]),
        "interval_performance_rows": len(entities["strategy_performance_interval"]),
        "issues": issues,
        "status": "pass_sample_gate" if not issues else "fail_sample_gate",
    }
    return summary, entities


def render_markdown(evaluation: dict[str, Any]) -> str:
    lines = [
        "# 且慢 App 投顾数据技术验证",
        "",
        f"生成时间：{evaluation['generated_at']}",
        "",
        "本报告只覆盖代表性登录态样本，不代表且慢全量策略已采完；样本数据不写主库和每日更新 DAG。",
        "",
        "## 样本结果",
        "",
        "| 策略 | 基准 | 基金行数 | 精确权重 | 权重合计 | 组合持仓日期 | 调仓事件 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for sample in evaluation["samples"]:
        lines.append(
            f"| {sample['strategy_name']} | {sample.get('benchmark') or '未见'} | {sample['holding_rows']} | "
            f"{sample['precise_weight_rows']} | {sample['weight_sum'] if sample['weight_sum'] is not None else '未披露'} | "
            f"{sample['position_date_rows']} | {sample['rebalance_event_rows']} |"
        )
    lines.extend(
        [
            "",
            "## 技术结论",
            "",
            "- 优先方案：登录态 App 进入策略详情后，读取非压缩 Android 无障碍树；用 OCR 截图作为交叉验证。",
            "- 匿名接口：`/pmdj/v2/m4` 可返回精选目录；策略详情接口匿名请求虽返回 HTTP 200，但正文为空，需要 App 登录态的 Authorization 与签名上下文。",
            "- 可稳定取得：策略名称、管理方、风险等级、最低金额、费率、业绩基准、摘要业绩、基金类型占比、官方最近调仓说明。",
            "- 条件取得：基金名称、代码和权重按策略披露。货币三佳、周周同行有精确权重；全球丰收只披露基金清单和基金类型占比。",
            "- 当前缺口：精确成立日、组合持仓日期、结构化日度曲线、明确区间收益值、调前/调后基金权重。缺口不得用运行天数、基金净值日期或图像曲线反推。",
            "- 全量化前提：遍历 App 全部分类和卡片，处理单页约 20–30 秒的 H5 冷加载，并对每个策略分别判断是否展示权重与调仓明细。",
            "",
            "## 质量门禁",
            "",
            f"样本门禁：{evaluation['quality_status']}；精确基金权重合计必须约等于 100%，精确权重不得为空，业务键不得重复。",
            "",
        ]
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a Qieman authenticated sample evaluation pack.")
    parser.add_argument("--sample-run", action="append", required=True, type=Path)
    parser.add_argument("--public-run", type=Path)
    parser.add_argument("--output-root", type=Path, default=PROBE_ROOT / "evaluations")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    probe_runs_root = (PROBE_ROOT / "runs").resolve()
    sample_dirs = [path.resolve() for path in args.sample_run]
    for path in sample_dirs:
        if probe_runs_root not in path.parents:
            raise ValueError(f"sample run must be under {probe_runs_root}: {path}")

    samples: list[dict[str, Any]] = []
    merged = {name: [] for name in ENTITIES}
    for run_dir in sample_dirs:
        sample, entities = validate_sample(run_dir)
        samples.append(sample)
        for name in ENTITIES:
            merged[name].extend(entities[name])

    public_candidate_count = None
    if args.public_run:
        public_rows = read_jsonl(args.public_run.resolve() / "normalized" / "strategy_master_candidates.jsonl")
        public_candidate_count = len(public_rows)

    generated_at = now_local()
    run_id = generated_at.strftime("%Y%m%dT%H%M%S%z") + "-evaluation"
    output_dir = args.output_root.resolve() / run_id
    for name, rows in merged.items():
        write_jsonl(output_dir / "validated_samples" / f"{name}.jsonl", rows)

    aggregate_counts = {name: len(rows) for name, rows in merged.items()}
    quality_status = "pass_sample_gate" if all(sample["status"] == "pass_sample_gate" for sample in samples) else "fail_sample_gate"
    evaluation = {
        "state": "qieman_authenticated_sample_evaluation_complete",
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "run_id": run_id,
        "scope": "representative_samples_not_full_catalog",
        "channel_id": "qieman",
        "app_package": "cn.yingmi.qieman.hermione",
        "app_entry": "且慢 App > 投顾 > 且慢四笔钱",
        "public_m4_candidate_count": public_candidate_count,
        "sample_count": len(samples),
        "samples": samples,
        "aggregate_counts": aggregate_counts,
        "quality_status": quality_status,
        "production_integration": {
            "main_db_written": False,
            "daily_dag_modified": False,
            "official_output_written": False,
            "reason": "probe-only; all precise holdings still lack an explicit portfolio position_date",
        },
        "method_assessment": {
            "authenticated_accessibility_dom": "feasible_primary_but_webview_dump_can_be_killed_and_requires_retry_or_sanitized_reparse",
            "ocr_screenshot": "feasible_secondary_cross_check_not_curve_source",
            "anonymous_api": "catalog_only_detail_body_empty_without_login_context",
            "authenticated_direct_api": "preferred_if_authorization_context_can_be_exported_without_persisting_tokens; not yet non-invasively available",
            "proxy_or_certificate_install": "not_attempted_requires_separate_authorization",
        },
        "output_dir": str(output_dir),
    }
    write_json(output_dir / "technical_evaluation.json", evaluation)
    (output_dir / "TECHNICAL_EVALUATION.md").write_text(render_markdown(evaluation), encoding="utf-8")
    print(json.dumps(evaluation, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
