from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from probe_qieman_device import active_locks


ENTITY_FILES = {
    "strategy_master": "strategy_master.jsonl",
    "strategy_benchmark": "strategy_benchmark.jsonl",
    "strategy_fund_snapshot": "strategy_fund_snapshot.jsonl",
    "strategy_performance_daily": "strategy_performance_daily.jsonl",
    "strategy_rebalance_event": "strategy_rebalance_event.jsonl",
    "strategy_rebalance_fund_delta": "strategy_rebalance_fund_delta.jsonl",
    "strategy_fund_snapshot_history": "strategy_fund_snapshot_history.jsonl",
    "signal_strategy_event": "signal_strategy_event.jsonl",
    "signal_fund_instruction": "signal_fund_instruction.jsonl",
    "signal_rebalance_projection_event": "signal_rebalance_projection_event.jsonl",
    "signal_rebalance_projection_delta": "signal_rebalance_projection_delta.jsonl",
    "strategy_coverage": "strategy_coverage.jsonl",
    "strategy_incomplete_requested_data": "strategy_incomplete_requested_data.jsonl",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit an isolated full Qieman history package.")
    parser.add_argument("--run-dir", type=Path, required=True)
    return parser.parse_args()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def audit_business_keys(
    path: Path, keys: tuple[str, ...]
) -> tuple[int, int, int]:
    seen: set[tuple[Any, ...]] = set()
    rows = 0
    duplicates = 0
    blank_keys = 0
    for row in iter_jsonl(path):
        rows += 1
        key = tuple(row.get(name) for name in keys)
        if any(value is None or value == "" for value in key):
            blank_keys += 1
        if key in seen:
            duplicates += 1
        else:
            seen.add(key)
    return rows, duplicates, blank_keys


def main() -> None:
    args = parse_args()
    locks = active_locks()
    if locks:
        raise SystemExit("active production lock; isolated Qieman audit aborted: " + ", ".join(locks))
    run_dir = args.run_dir.resolve()
    normalized_dir = run_dir / "normalized"
    summary = read_json(run_dir / "summary.json")
    quality = read_json(run_dir / "normalized_quality_report.json")
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    checks: dict[str, Any] = {}

    expected_strategies = int(quality.get("catalog_strategy_count") or 0)
    if summary.get("state") != "signed_history_catalog_complete":
        errors.append({"rule_id": "QIEMAN_RAW_RUN_NOT_COMPLETE", "actual": summary.get("state")})
    if int(summary.get("completeStrategyCount") or 0) != expected_strategies:
        errors.append(
            {
                "rule_id": "QIEMAN_RAW_STRATEGY_COUNT_MISMATCH",
                "expected": expected_strategies,
                "actual": summary.get("completeStrategyCount"),
            }
        )

    eligible_codes = {
        str(row.get("strategyCode") or "").strip()
        for row in summary.get("results") or []
        if str(row.get("strategyCode") or "").strip()
    }
    excluded_internal_codes = {
        str(value or "").strip()
        for value in summary.get("excludedInternalStrategyIds") or []
        if str(value or "").strip()
    }
    nav_codes = {path.stem for path in (run_dir / "raw" / "nav").glob("*.json")}
    regular_codes = {path.stem for path in (run_dir / "raw" / "regular_adjustments").glob("*.json")}
    signal_codes = {path.stem for path in (run_dir / "raw" / "signal_adjustments").glob("*.json")}
    history_codes = regular_codes | signal_codes
    raw_counts = {
        "nav": len(nav_codes),
        "regular_adjustments": len(regular_codes),
        "signal_adjustments": len(signal_codes),
        "eligible_nav": len(nav_codes & eligible_codes),
        "eligible_history": len(history_codes & eligible_codes),
        "preserved_excluded_internal_nav": len((nav_codes - eligible_codes) & excluded_internal_codes),
        "preserved_excluded_internal_history": len((history_codes - eligible_codes) & excluded_internal_codes),
    }
    checks["raw_file_counts"] = raw_counts
    missing_nav = sorted(eligible_codes - nav_codes)
    unexpected_nav = sorted((nav_codes - eligible_codes) - excluded_internal_codes)
    if missing_nav or unexpected_nav:
        errors.append(
            {
                "rule_id": "QIEMAN_RAW_NAV_FILE_COUNT",
                "expected": expected_strategies,
                "actual": raw_counts["eligible_nav"],
                "missingEligibleIds": missing_nav,
                "unexpectedIds": unexpected_nav,
            }
        )
    missing_history = sorted(eligible_codes - history_codes)
    unexpected_history = sorted((history_codes - eligible_codes) - excluded_internal_codes)
    if missing_history or unexpected_history:
        errors.append(
            {
                "rule_id": "QIEMAN_RAW_HISTORY_FILE_COUNT",
                "expected": expected_strategies,
                "actual": raw_counts["eligible_history"],
                "missingEligibleIds": missing_history,
                "unexpectedIds": unexpected_history,
            }
        )

    line_counts: dict[str, int] = {}
    for entity, file_name in ENTITY_FILES.items():
        path = normalized_dir / file_name
        if not path.exists():
            errors.append({"rule_id": "QIEMAN_NORMALIZED_ENTITY_MISSING", "entity": entity, "path": str(path)})
            continue
        line_counts[entity] = sum(1 for _ in iter_jsonl(path))
    checks["line_counts"] = line_counts
    for entity, expected in (quality.get("counts") or {}).items():
        if entity in line_counts and line_counts[entity] != int(expected):
            errors.append(
                {
                    "rule_id": "QIEMAN_NORMALIZED_COUNT_MISMATCH",
                    "entity": entity,
                    "expected": int(expected),
                    "actual": line_counts[entity],
                }
            )
    if line_counts.get("strategy_coverage") != expected_strategies:
        errors.append(
            {
                "rule_id": "QIEMAN_COVERAGE_ROW_COUNT",
                "expected": expected_strategies,
                "actual": line_counts.get("strategy_coverage"),
            }
        )
    expected_incomplete = expected_strategies - int(quality.get("complete_requested_data_count") or 0)
    if line_counts.get("strategy_incomplete_requested_data") != expected_incomplete:
        errors.append(
            {
                "rule_id": "QIEMAN_INCOMPLETE_LIST_COUNT",
                "expected": expected_incomplete,
                "actual": line_counts.get("strategy_incomplete_requested_data"),
            }
        )

    key_specs = {
        "strategy_master": ("channel_id", "source_strategy_id"),
        "strategy_benchmark": ("channel_id", "source_strategy_id"),
        "strategy_performance_daily": ("channel_id", "source_strategy_id", "trade_date"),
        "strategy_rebalance_event": ("rebalance_event_id",),
        "strategy_rebalance_fund_delta": ("rebalance_event_id", "fund_code"),
        "strategy_fund_snapshot_history": ("snapshot_id", "fund_code"),
        "signal_strategy_event": ("signal_event_id",),
        "signal_fund_instruction": ("signal_instruction_id",),
        "signal_rebalance_projection_event": ("rebalance_event_id",),
        "signal_rebalance_projection_delta": ("rebalance_event_id", "fund_code"),
        "strategy_coverage": ("source_strategy_id",),
    }
    business_key_checks: dict[str, Any] = {}
    for entity, keys in key_specs.items():
        path = normalized_dir / ENTITY_FILES[entity]
        if not path.exists():
            continue
        rows, duplicates, blank_keys = audit_business_keys(path, keys)
        business_key_checks[entity] = {
            "rows": rows,
            "duplicates": duplicates,
            "blank_keys": blank_keys,
            "keys": list(keys),
        }
        if duplicates:
            errors.append({"rule_id": "QIEMAN_DUPLICATE_BUSINESS_KEY", "entity": entity, "count": duplicates})
        if blank_keys:
            errors.append({"rule_id": "QIEMAN_BLANK_BUSINESS_KEY", "entity": entity, "count": blank_keys})
    checks["business_keys"] = business_key_checks

    snapshot_sums: dict[str, float] = defaultdict(float)
    missing_fund_code = 0
    missing_fund_name = 0
    for row in iter_jsonl(normalized_dir / ENTITY_FILES["strategy_fund_snapshot_history"]):
        snapshot_sums[str(row.get("snapshot_id") or "")] += float(row.get("fund_weight") or 0)
        missing_fund_code += not bool(row.get("fund_code"))
        missing_fund_name += not bool(row.get("fund_name"))
    invalid_snapshot_sums = {
        key: round(value, 8)
        for key, value in snapshot_sums.items()
        if abs(value - 1) > 0.001
    }
    checks["historical_position"] = {
        "snapshot_count": len(snapshot_sums),
        "invalid_weight_sum_count": len(invalid_snapshot_sums),
        "missing_fund_code": missing_fund_code,
        "missing_fund_name": missing_fund_name,
    }
    if invalid_snapshot_sums:
        errors.append(
            {
                "rule_id": "QIEMAN_HISTORICAL_POSITION_WEIGHT_SUM",
                "count": len(invalid_snapshot_sums),
                "samples": dict(list(invalid_snapshot_sums.items())[:20]),
            }
        )
    if missing_fund_code or missing_fund_name:
        errors.append(
            {
                "rule_id": "QIEMAN_HISTORICAL_POSITION_FUND_IDENTITY",
                "missing_fund_code": missing_fund_code,
                "missing_fund_name": missing_fund_name,
            }
        )

    eligible_projection_rows = 0
    for row in iter_jsonl(normalized_dir / ENTITY_FILES["signal_rebalance_projection_delta"]):
        eligible_projection_rows += row.get("eligible_for_official_rebalance_table") is not False
    checks["signal_projection_non_official_gate"] = {
        "unexpected_eligible_rows": eligible_projection_rows
    }
    if eligible_projection_rows:
        errors.append(
            {
                "rule_id": "QIEMAN_SIGNAL_PROJECTION_OFFICIAL_LEAK",
                "count": eligible_projection_rows,
            }
        )

    coverage = quality.get("coverage") or {}
    warning_specs = (
        ("QIEMAN_BENCHMARK_RESPONSE_INEXACT", int(coverage.get("benchmark_response_inexact") or 0)),
        ("QIEMAN_PERFORMANCE_ZERO_ROWS", int(coverage.get("performance_zero_rows") or 0)),
        (
            "QIEMAN_CURRENT_POSITION_MISSING_OR_INCOMPLETE",
            expected_strategies - int(coverage.get("current_position_complete") or 0),
        ),
        ("QIEMAN_SIGNAL_POSITION_HISTORY_PARTIAL", int(coverage.get("signal_position_history_partial") or 0)),
        ("QIEMAN_HISTORY_REFRESH_RETAINED_BASELINE", int(coverage.get("history_refresh_retained") or 0)),
        (
            "QIEMAN_STRICT_REQUESTED_DATA_INCOMPLETE",
            expected_incomplete,
        ),
    )
    for rule_id, count in warning_specs:
        if count:
            warnings.append({"rule_id": rule_id, "count": count})

    report = {
        "state": "qieman_isolated_data_audit_complete",
        "status": "error" if errors else "warn" if warnings else "passed",
        "run_id": quality.get("run_id"),
        "production_database_written": False,
        "daily_update_pipeline_touched": False,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": errors,
        "warnings": warnings,
        "checks": checks,
        "quality_report_path": str(run_dir / "normalized_quality_report.json"),
        "run_dir": str(run_dir),
    }
    write_json(run_dir / "qieman_data_audit_report.json", report)
    lines = [
        "# 且慢隔离数据专项稽核",
        "",
        f"- 状态：{report['status']}",
        f"- error：{len(errors)}；warning：{len(warnings)}",
        f"- 策略目录：{expected_strategies}；严格完整：{quality.get('complete_requested_data_count')}；明确缺口：{expected_incomplete}",
        f"- 原始文件：nav {raw_counts['nav']}，普通调仓 {raw_counts['regular_adjustments']}，发车信号 {raw_counts['signal_adjustments']}",
        f"- 历史仓位快照：{len(snapshot_sums)} 个；权重合计异常：{len(invalid_snapshot_sums)} 个",
        f"- 历史仓位基金代码缺失：{missing_fund_code}；基金名称缺失：{missing_fund_name}",
        "- 信号兼容投影不得进入普通官方调仓表。",
        "- 本稽核不写主库、不修改每日 DAG。",
    ]
    (run_dir / "qieman_data_audit_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if errors:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
