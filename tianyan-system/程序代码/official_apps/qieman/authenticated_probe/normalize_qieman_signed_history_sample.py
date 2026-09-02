from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from probe_qieman_device import active_locks


CHANNEL_ID = "qieman"
SHANGHAI = ZoneInfo("Asia/Shanghai")
FUND_ASSET_TYPES = {
    "1": "EQUITY_FUND",
    "2": "BOND_FUND",
    "3": "MIXED_FUND",
    "4": "MONEY_FUND",
    "6": "INDEX_FUND",
    "7": "QDII_FUND",
}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    temporary.replace(path)


def stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def shanghai_date(milliseconds: int | float) -> str:
    return datetime.fromtimestamp(float(milliseconds) / 1000, tz=SHANGHAI).date().isoformat()


def weight(value: Any) -> float:
    return float(str(value or "0").strip() or 0)


def action_type(before: float, after: float, tolerance: float = 1e-12) -> str:
    if abs(before) <= tolerance and after > tolerance:
        return "buy"
    if before > tolerance and abs(after) <= tolerance:
        return "sell"
    if after - before > tolerance:
        return "increase"
    if before - after > tolerance:
        return "decrease"
    return "keep"


def duplicate_count(rows: Iterable[dict[str, Any]], keys: tuple[str, ...]) -> int:
    values = [tuple(row.get(key) for key in keys) for row in rows]
    return len(values) - len(set(values))


def load_fund_names(db_path: Path | None) -> dict[str, str]:
    if db_path is None or not db_path.exists():
        return {}
    uri = "file:" + str(db_path.resolve()).replace("\\", "/") + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        table = "基金信息"
        code_column = "基金代码"
        name_column = "基金名称"
        return {
            str(code): str(name)
            for code, name in connection.execute(
                f'SELECT "{code_column}", "{name_column}" FROM "{table}" '
                f'WHERE "{code_column}" IS NOT NULL AND "{name_column}" IS NOT NULL'
            )
        }
    finally:
        connection.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Normalize a signed Qieman web-history sample without writing the production database."
    )
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--metadata-dir", type=Path, required=True)
    parser.add_argument("--db-path", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    locks = active_locks()
    if locks:
        raise SystemExit("active production lock; signed-history normalization aborted: " + ", ".join(locks))

    run_dir = args.run_dir.resolve()
    metadata_dir = args.metadata_dir.resolve()
    raw_dir = run_dir / "raw"
    output_dir = run_dir / "normalized"
    summary = read_json(run_dir / "summary.json")
    run_id = str(summary.get("runId") or run_dir.name)
    disclosure_date = run_id[:8]
    disclosure_date = f"{disclosure_date[:4]}-{disclosure_date[4:6]}-{disclosure_date[6:8]}"
    strategy_codes = [str(value) for value in summary.get("strategyCodes") or []]

    masters_all = read_jsonl(metadata_dir / "strategy_master.jsonl")
    benchmarks_all = read_jsonl(metadata_dir / "strategy_benchmark.jsonl")
    current_holdings_all = read_jsonl(metadata_dir / "strategy_fund_snapshot.jsonl")
    masters = [row for row in masters_all if str(row.get("source_strategy_id")) in strategy_codes]
    benchmarks = [row for row in benchmarks_all if str(row.get("source_strategy_id")) in strategy_codes]
    current_holdings = [
        row for row in current_holdings_all if str(row.get("source_strategy_id")) in strategy_codes
    ]
    master_by_code = {str(row.get("source_strategy_id")): row for row in masters}
    benchmark_by_code = {str(row.get("source_strategy_id")): row for row in benchmarks}
    current_by_code: dict[str, list[dict[str, Any]]] = {}
    for row in current_holdings:
        current_by_code.setdefault(str(row.get("source_strategy_id")), []).append(row)
    fund_names = load_fund_names(args.db_path.resolve() if args.db_path else None)

    performance_rows: list[dict[str, Any]] = []
    event_rows: list[dict[str, Any]] = []
    delta_rows: list[dict[str, Any]] = []
    post_position_rows: list[dict[str, Any]] = []
    strategy_assessments: list[dict[str, Any]] = []

    for code in strategy_codes:
        nav_payload = read_json(raw_dir / f"{code}_nav_history.json")
        adjustment_payload = read_json(raw_dir / f"{code}_adjustments.json")
        adjustments = adjustment_payload.get("content") if isinstance(adjustment_payload, dict) else []
        adjustments = adjustments if isinstance(adjustments, list) else []

        code_performance: list[dict[str, Any]] = []
        for item in nav_payload if isinstance(nav_payload, list) else []:
            if not isinstance(item, dict) or item.get("navDate") is None or item.get("nav") is None:
                continue
            nav = float(item["nav"])
            row = {
                "channel_id": CHANNEL_ID,
                "source_strategy_id": code,
                "trade_date": shanghai_date(item["navDate"]),
                "nav": nav,
                "daily_return": item.get("dailyReturn"),
                "cumulative_return": nav - 1,
                "benchmark_return": None,
                "index_return": None,
                "max_drawdown": None,
                "source_snapshot_id": f"{run_id}:{code}:nav-history",
                "confidence_level": "official_signed_public_daily_history",
                "access_level": "anonymous_public_signed_page_protocol",
                "run_id": run_id,
            }
            code_performance.append(row)
            performance_rows.append(row)

        code_events: list[dict[str, Any]] = []
        code_deltas: list[dict[str, Any]] = []
        code_positions: list[dict[str, Any]] = []
        ordered_ascending = sorted(
            (item for item in adjustments if isinstance(item, dict)),
            key=lambda item: str(item.get("adjustedOn") or ""),
        )
        previous_date_by_id: dict[str, str | None] = {}
        previous_date: str | None = None
        for item in ordered_ascending:
            previous_date_by_id[str(item.get("adjustmentId"))] = previous_date
            previous_date = str(item.get("adjustedOn") or "") or previous_date

        event_sum_checks: list[dict[str, Any]] = []
        for item in adjustments:
            if not isinstance(item, dict):
                continue
            adjustment_id = str(item.get("adjustmentId") or "")
            rebalance_date = str(item.get("adjustedOn") or "")
            event_id = f"qieman-{code}-{adjustment_id}"
            details = item.get("details") if isinstance(item.get("details"), list) else []
            before_sum = sum(weight(detail.get("fromPercent")) for detail in details if isinstance(detail, dict))
            after_sum = sum(weight(detail.get("toPercent")) for detail in details if isinstance(detail, dict))
            turnover = 0.5 * sum(
                abs(weight(detail.get("toPercent")) - weight(detail.get("fromPercent")))
                for detail in details
                if isinstance(detail, dict)
            )
            event = {
                "rebalance_event_id": event_id,
                "channel_id": CHANNEL_ID,
                "source_strategy_id": code,
                "rebalance_date": rebalance_date,
                "previous_position_date": previous_date_by_id.get(adjustment_id),
                "previous_position_date_is_inferred": previous_date_by_id.get(adjustment_id) is not None,
                "new_position_date": rebalance_date,
                "disclosure_date": disclosure_date,
                "event_title": "官方调仓",
                "event_reason": item.get("comment"),
                "source_url": f"https://qieman.com/pmdj/v1/pomodels/{code}/adjustments",
                "source_snapshot_id": adjustment_id,
                "confidence_level": "official_signed_public_full_rebalance_event",
                "access_level": "anonymous_public_signed_page_protocol",
                "detail_count": len(details),
                "before_weight_sum": round(before_sum, 8),
                "after_weight_sum": round(after_sum, 8),
                "turnover_rate": round(turnover, 8),
                "official_status": item.get("status"),
                "run_id": run_id,
            }
            code_events.append(event)
            event_rows.append(event)
            event_sum_checks.append(
                {
                    "rebalance_event_id": event_id,
                    "rebalance_date": rebalance_date,
                    "before_weight_sum": round(before_sum, 8),
                    "after_weight_sum": round(after_sum, 8),
                    "detail_count": len(details),
                }
            )

            for detail in details:
                if not isinstance(detail, dict):
                    continue
                fund_code = str(detail.get("fundCode") or detail.get("prodCode") or "").strip() or None
                official_name = str(detail.get("fundName") or detail.get("prodName") or "").strip() or None
                resolved_name = fund_names.get(fund_code or "") or official_name
                before = weight(detail.get("fromPercent"))
                after = weight(detail.get("toPercent"))
                raw_hash = stable_hash({"strategy_code": code, "adjustment_id": adjustment_id, "detail": detail})
                delta = {
                    "rebalance_event_id": event_id,
                    "channel_id": CHANNEL_ID,
                    "source_strategy_id": code,
                    "rebalance_date": rebalance_date,
                    "fund_code": fund_code,
                    "fund_name": resolved_name,
                    "before_weight": before,
                    "after_weight": after,
                    "weight_delta": after - before,
                    "action_type": action_type(before, after),
                    "fund_asset_type": FUND_ASSET_TYPES.get(str(detail.get("fundType") or "")),
                    "raw_fund_type": detail.get("fundType"),
                    "raw_fund_invest_type": detail.get("fundInvestType"),
                    "official_fund_name": official_name,
                    "fund_name_resolution": "main_db_fund_dim" if fund_code in fund_names else "official_history_payload",
                    "is_qdii": bool(detail.get("isQdii")),
                    "is_lof": bool(detail.get("isLof")),
                    "is_etf": bool(detail.get("isEtf")),
                    "is_index": bool(detail.get("isIndex")),
                    "raw_record_hash": raw_hash,
                    "confidence_level": "official_signed_public_exact_rebalance_weight",
                    "run_id": run_id,
                }
                code_deltas.append(delta)
                delta_rows.append(delta)
                if after > 0:
                    snapshot_id = f"qieman-history-{code}-{rebalance_date}-{adjustment_id}"
                    snapshot = {
                        "snapshot_id": snapshot_id,
                        "channel_id": CHANNEL_ID,
                        "source_strategy_id": code,
                        "position_date": rebalance_date,
                        "disclosure_date": disclosure_date,
                        "fund_code": fund_code,
                        "fund_name": resolved_name,
                        "fund_asset_type": FUND_ASSET_TYPES.get(str(detail.get("fundType") or "")),
                        "fund_group_name": None,
                        "fund_weight": after,
                        "fund_nav": None,
                        "fund_nav_date": None,
                        "is_precise_weight": True,
                        "is_login_required": False,
                        "source_url": f"https://qieman.com/pmdj/v1/pomodels/{code}/adjustments",
                        "raw_record_hash": raw_hash,
                        "confidence_level": "official_signed_public_rebalance_post_position",
                        "access_level": "anonymous_public_signed_page_protocol",
                        "run_id": run_id,
                    }
                    code_positions.append(snapshot)
                    post_position_rows.append(snapshot)

        nav_dates = [str(row["trade_date"]) for row in code_performance]
        current_rows = current_by_code.get(code, [])
        current_sum = sum(float(row.get("fund_weight") or 0) for row in current_rows)
        benchmark = benchmark_by_code.get(code, {})
        endpoint_complete = bool(
            isinstance(adjustment_payload, dict)
            and adjustment_payload.get("last") is True
            and int(adjustment_payload.get("totalElements") or 0) == len(adjustments)
        )
        after_sums_valid = all(abs(float(check["after_weight_sum"]) - 1) <= 0.001 for check in event_sum_checks)
        before_sums_valid = all(
            abs(float(check["before_weight_sum"]) - 1) <= 0.001
            or (
                check["rebalance_date"] == min((x["rebalance_date"] for x in event_sum_checks), default=None)
                and abs(float(check["before_weight_sum"])) <= 0.001
            )
            for check in event_sum_checks
        )
        event_history_status = (
            "complete_official_history"
            if adjustments and endpoint_complete and after_sums_valid and before_sums_valid
            else "official_endpoint_zero_events_no_history"
            if not adjustments and endpoint_complete
            else "incomplete"
        )
        strategy_assessments.append(
            {
                "source_strategy_id": code,
                "strategy_name": master_by_code.get(code, {}).get("strategy_name"),
                "performance": {
                    "rows": len(code_performance),
                    "first_trade_date": min(nav_dates) if nav_dates else None,
                    "last_trade_date": max(nav_dates) if nav_dates else None,
                    "duplicate_business_keys": duplicate_count(
                        code_performance, ("channel_id", "source_strategy_id", "trade_date")
                    ),
                    "null_daily_return_rows": sum(row.get("daily_return") is None for row in code_performance),
                    "status": "complete_official_daily_history" if code_performance else "missing",
                },
                "benchmark": {
                    "description": benchmark.get("benchmark_description"),
                    "component_count": len(benchmark.get("benchmark_components") or []),
                    "weight_sum": benchmark.get("benchmark_weight_sum"),
                    "is_exact_split": bool(benchmark.get("is_exact_split")),
                    "status": "complete_exact_split" if benchmark.get("is_exact_split") else "missing_or_inexact",
                },
                "current_position": {
                    "rows": len(current_rows),
                    "position_dates": sorted({str(row.get("position_date")) for row in current_rows}),
                    "weight_sum": round(current_sum, 8),
                    "status": "complete_exact_current_position"
                    if current_rows and abs(current_sum - 1) <= 0.001
                    else "missing_or_incomplete",
                },
                "rebalance_history": {
                    "events": len(code_events),
                    "fund_delta_rows": len(code_deltas),
                    "post_position_rows": len(code_positions),
                    "first_event_date": min((row["rebalance_date"] for row in code_events), default=None),
                    "last_event_date": max((row["rebalance_date"] for row in code_events), default=None),
                    "endpoint_total_elements": adjustment_payload.get("totalElements")
                    if isinstance(adjustment_payload, dict)
                    else None,
                    "endpoint_last_page": adjustment_payload.get("last")
                    if isinstance(adjustment_payload, dict)
                    else None,
                    "duplicate_event_keys": duplicate_count(code_events, ("rebalance_event_id",)),
                    "duplicate_delta_keys": duplicate_count(code_deltas, ("rebalance_event_id", "fund_code")),
                    "after_weight_sums_valid": after_sums_valid,
                    "before_weight_sums_valid": before_sums_valid,
                    "status": event_history_status,
                },
                "all_requested_history_complete": bool(
                    code_performance
                    and benchmark.get("is_exact_split")
                    and current_rows
                    and abs(current_sum - 1) <= 0.001
                    and event_history_status == "complete_official_history"
                ),
            }
        )

    write_jsonl(output_dir / "strategy_master.jsonl", masters)
    write_jsonl(output_dir / "strategy_benchmark.jsonl", benchmarks)
    write_jsonl(output_dir / "strategy_fund_snapshot.jsonl", current_holdings)
    write_jsonl(output_dir / "strategy_performance_daily.jsonl", performance_rows)
    write_jsonl(output_dir / "strategy_rebalance_event.jsonl", event_rows)
    write_jsonl(output_dir / "strategy_rebalance_fund_delta.jsonl", delta_rows)
    write_jsonl(output_dir / "strategy_fund_snapshot_history.jsonl", post_position_rows)

    report = {
        "state": "qieman_signed_history_sample_normalized",
        "run_id": run_id,
        "production_database_written": False,
        "daily_update_pipeline_touched": False,
        "strategy_count": len(strategy_codes),
        "complete_all_requested_history_count": sum(
            bool(row.get("all_requested_history_complete")) for row in strategy_assessments
        ),
        "counts": {
            "strategy_master": len(masters),
            "strategy_benchmark": len(benchmarks),
            "strategy_fund_snapshot": len(current_holdings),
            "strategy_performance_daily": len(performance_rows),
            "strategy_rebalance_event": len(event_rows),
            "strategy_rebalance_fund_delta": len(delta_rows),
            "strategy_fund_snapshot_history": len(post_position_rows),
        },
        "quality": {
            "duplicate_performance_business_keys": duplicate_count(
                performance_rows, ("channel_id", "source_strategy_id", "trade_date")
            ),
            "duplicate_event_business_keys": duplicate_count(event_rows, ("rebalance_event_id",)),
            "duplicate_delta_business_keys": duplicate_count(
                delta_rows, ("rebalance_event_id", "fund_code")
            ),
            "duplicate_historical_position_business_keys": duplicate_count(
                post_position_rows, ("snapshot_id", "fund_code")
            ),
            "missing_fund_codes": sum(not row.get("fund_code") for row in delta_rows),
            "missing_fund_names": sum(not row.get("fund_name") for row in delta_rows),
            "main_db_fund_name_resolutions": sum(
                row.get("fund_name_resolution") == "main_db_fund_dim" for row in delta_rows
            ),
            "official_payload_fund_name_resolutions": sum(
                row.get("fund_name_resolution") == "official_history_payload" for row in delta_rows
            ),
        },
        "strategy_assessments": strategy_assessments,
        "data_boundary": {
            "benchmark_daily_curve": "not_collected; benchmark information and exact component split are complete",
            "zero_event_strategy": "A zero-row complete endpoint response is preserved as no disclosed history, not synthesized.",
            "historical_position_semantics": "Each official adjustment contains a full before/after fund list; post-position snapshots use exact after_weight values.",
        },
        "output_dir": str(output_dir),
    }
    write_json(run_dir / "normalized_quality_report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
