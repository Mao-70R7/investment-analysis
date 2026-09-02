from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import defaultdict
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Normalize a full signed Qieman history run without writing the production database."
    )
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--metadata-dir", type=Path, required=True)
    parser.add_argument("--benchmark-path", type=Path)
    parser.add_argument("--db-path", type=Path)
    return parser.parse_args()


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


def number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def zero_number(value: Any) -> float:
    parsed = number(value)
    return parsed if parsed is not None else 0.0


def shanghai_date(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value) / 1000, tz=SHANGHAI).date().isoformat()
    text = str(value).strip()
    if not text:
        return None
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(SHANGHAI).date().isoformat()
    except ValueError:
        return text[:10]


def duplicate_count(rows: Iterable[dict[str, Any]], keys: tuple[str, ...]) -> int:
    values = [tuple(row.get(key) for key in keys) for row in rows]
    return len(values) - len(set(values))


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


def load_fund_names(db_path: Path | None) -> dict[str, str]:
    if db_path is None or not db_path.exists():
        return {}
    uri = "file:" + str(db_path.resolve()).replace("\\", "/") + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        return {
            str(code): str(name)
            for code, name in connection.execute(
                'SELECT "基金代码", "基金名称" FROM "基金信息" '
                'WHERE "基金代码" IS NOT NULL AND "基金名称" IS NOT NULL'
            )
        }
    finally:
        connection.close()


def resolve_fund_name(
    fund_code: str | None,
    official_name: Any,
    event_names: dict[str, str],
    current_names: dict[str, str],
    db_names: dict[str, str],
) -> tuple[str | None, str]:
    code = fund_code or ""
    if official_name:
        return str(official_name), "official_history_payload"
    if event_names.get(code):
        return event_names[code], "official_event_related_payload"
    if current_names.get(code):
        return current_names[code], "official_current_composition"
    if db_names.get(code):
        return db_names[code], "main_db_fund_dim_read_only"
    return None, "unresolved"


def position_map(items: Any, phase: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    if not isinstance(items, list):
        return result
    weight_field = "percent"
    if phase == "post_target" and items:
        candidates = ("targetCapitalPercent", "targetPercent", "capitalPercent", "percent")
        candidate_sums = {
            field: sum(zero_number(item.get(field)) for item in items if isinstance(item, dict))
            for field in candidates
        }
        weight_field = min(
            candidates,
            key=lambda field: (
                0 if abs(candidate_sums[field] - 1) <= 0.001 else 1,
                abs(candidate_sums[field] - 1),
                candidates.index(field),
            ),
        )
    for item in items:
        if not isinstance(item, dict):
            continue
        code = str(item.get("fundCode") or item.get("prodCode") or "").strip()
        if not code:
            continue
        result[code] = {
            "raw": item,
            "weight": zero_number(item.get(weight_field)),
            "weight_field": weight_field,
        }
    return result


def normalise_performance(
    code: str, payload: Any, run_id: str
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not isinstance(payload, list):
        return rows
    for item in payload:
        if not isinstance(item, dict) or item.get("navDate") is None or item.get("nav") is None:
            continue
        nav = number(item.get("nav"))
        trade_date = shanghai_date(item.get("navDate"))
        if nav is None or not trade_date:
            continue
        rows.append(
            {
                "channel_id": CHANNEL_ID,
                "source_strategy_id": code,
                "trade_date": trade_date,
                "nav": nav,
                "daily_return": number(item.get("dailyReturn")),
                "cumulative_return": nav - 1,
                "benchmark_return": None,
                "index_return": None,
                "max_drawdown": None,
                "source_snapshot_id": f"{run_id}:{code}:nav-history",
                "confidence_level": "official_signed_public_daily_history",
                "access_level": "anonymous_public_signed_page_protocol",
                "run_id": run_id,
            }
        )
    return rows


def normalise_regular_history(
    code: str,
    payload: dict[str, Any],
    run_id: str,
    disclosure_date: str,
    db_names: dict[str, str],
    current_names: dict[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    events: list[dict[str, Any]] = []
    deltas: list[dict[str, Any]] = []
    snapshots: list[dict[str, Any]] = []
    adjustments = payload.get("content") if isinstance(payload, dict) else []
    adjustments = adjustments if isinstance(adjustments, list) else []
    ordered = sorted(
        (item for item in adjustments if isinstance(item, dict)),
        key=lambda item: str(item.get("adjustedOn") or ""),
    )
    previous_date_by_id: dict[str, str | None] = {}
    previous_date: str | None = None
    for item in ordered:
        adjustment_id = str(item.get("adjustmentId") or "")
        previous_date_by_id[adjustment_id] = previous_date
        previous_date = str(item.get("adjustedOn") or "") or previous_date
    valid_before = True
    valid_after = True
    for item in adjustments:
        if not isinstance(item, dict):
            continue
        adjustment_id = str(item.get("adjustmentId") or "")
        rebalance_date = str(item.get("adjustedOn") or "")
        event_id = f"qieman-{code}-{adjustment_id}"
        details = item.get("details") if isinstance(item.get("details"), list) else []
        before_sum = sum(zero_number(detail.get("fromPercent")) for detail in details if isinstance(detail, dict))
        after_sum = sum(zero_number(detail.get("toPercent")) for detail in details if isinstance(detail, dict))
        initial = rebalance_date == min((str(x.get("adjustedOn") or "") for x in ordered), default=None)
        valid_before = valid_before and (abs(before_sum - 1) <= 0.001 or (initial and abs(before_sum) <= 0.001))
        valid_after = valid_after and abs(after_sum - 1) <= 0.001
        events.append(
            {
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
                "payload_type": "official_regular_rebalance",
                "source_snapshot_id": adjustment_id,
                "confidence_level": "official_signed_public_full_rebalance_event",
                "access_level": "anonymous_public_signed_page_protocol",
                "detail_count": len(details),
                "before_weight_sum": round(before_sum, 8),
                "after_weight_sum": round(after_sum, 8),
                "turnover_rate": round(
                    0.5
                    * sum(
                        abs(zero_number(detail.get("toPercent")) - zero_number(detail.get("fromPercent")))
                        for detail in details
                        if isinstance(detail, dict)
                    ),
                    8,
                ),
                "official_status": item.get("status"),
                "run_id": run_id,
            }
        )
        for detail in details:
            if not isinstance(detail, dict):
                continue
            fund_code = str(detail.get("fundCode") or detail.get("prodCode") or "").strip() or None
            official_name = detail.get("fundName") or detail.get("prodName")
            fund_name, resolution = resolve_fund_name(
                fund_code, official_name, {}, current_names, db_names
            )
            before = zero_number(detail.get("fromPercent"))
            after = zero_number(detail.get("toPercent"))
            raw_hash = stable_hash({"strategy_code": code, "adjustment_id": adjustment_id, "detail": detail})
            deltas.append(
                {
                    "rebalance_event_id": event_id,
                    "channel_id": CHANNEL_ID,
                    "source_strategy_id": code,
                    "rebalance_date": rebalance_date,
                    "fund_code": fund_code,
                    "fund_name": fund_name,
                    "before_weight": before,
                    "after_weight": after,
                    "weight_delta": after - before,
                    "action_type": action_type(before, after),
                    "fund_asset_type": FUND_ASSET_TYPES.get(str(detail.get("fundType") or "")),
                    "official_fund_name": str(official_name) if official_name else None,
                    "fund_name_resolution": resolution,
                    "raw_record_hash": raw_hash,
                    "confidence_level": "official_signed_public_exact_rebalance_weight",
                    "run_id": run_id,
                }
            )
            if after > 0:
                snapshots.append(
                    {
                        "snapshot_id": f"qieman-history-{code}-{adjustment_id}-post",
                        "source_event_id": event_id,
                        "snapshot_phase": "post_rebalance",
                        "channel_id": CHANNEL_ID,
                        "source_strategy_id": code,
                        "position_date": rebalance_date,
                        "disclosure_date": disclosure_date,
                        "fund_code": fund_code,
                        "fund_name": fund_name,
                        "fund_asset_type": FUND_ASSET_TYPES.get(str(detail.get("fundType") or "")),
                        "fund_weight": after,
                        "is_precise_weight": True,
                        "source_url": f"https://qieman.com/pmdj/v1/pomodels/{code}/adjustments",
                        "raw_record_hash": raw_hash,
                        "confidence_level": "official_signed_public_rebalance_post_position",
                        "access_level": "anonymous_public_signed_page_protocol",
                        "run_id": run_id,
                    }
                )
    status = "official_endpoint_complete"
    if not payload.get("complete"):
        status = "incomplete_endpoint"
    elif adjustments and not (valid_before and valid_after):
        status = "invalid_weight_sums"
    elif not adjustments:
        status = "official_endpoint_zero_disclosed_events"
    return events, deltas, snapshots, {
        "status": status,
        "events": len(events),
        "fund_delta_rows": len(deltas),
        "historical_position_rows": len(snapshots),
        "all_before_weight_sums_valid": valid_before,
        "all_after_weight_sums_valid": valid_after,
    }


def normalise_signal_history(
    code: str,
    strategy_name: str | None,
    payload: dict[str, Any],
    run_id: str,
    disclosure_date: str,
    db_names: dict[str, str],
    current_names: dict[str, str],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, Any],
]:
    signal_events: list[dict[str, Any]] = []
    instructions: list[dict[str, Any]] = []
    snapshots: list[dict[str, Any]] = []
    projected_events: list[dict[str, Any]] = []
    projected_deltas: list[dict[str, Any]] = []
    items = payload.get("content") if isinstance(payload, dict) else []
    items = items if isinstance(items, list) else []
    events_with_pre = 0
    events_with_post = 0
    invalid_pre_sums = 0
    invalid_post_sums = 0
    post_weight_field_counts: dict[str, int] = defaultdict(int)
    for item in items:
        if not isinstance(item, dict):
            continue
        raw_event_id = str(item.get("id") or item.get("sigId") or item.get("dedupKey") or "")
        event_id = f"qieman-signal-{code}-{raw_event_id}"
        signal_date = str(item.get("adjustedDate") or "")
        extra = item.get("extra") if isinstance(item.get("extra"), dict) else {}
        pre_container = extra.get("signalPoSimulateAsset") if isinstance(extra.get("signalPoSimulateAsset"), dict) else {}
        pre = position_map(pre_container.get("compositionAssetList"), "pre_observed")
        post = position_map(extra.get("modelTargetComposition"), "post_target")
        pre_sum = sum(row["weight"] for row in pre.values())
        post_sum = sum(row["weight"] for row in post.values())
        if pre:
            events_with_pre += 1
            if abs(pre_sum - 1) > 0.001:
                invalid_pre_sums += 1
        if post:
            events_with_post += 1
            post_weight_field_counts[str(next(iter(post.values())).get("weight_field"))] += 1
            if abs(post_sum - 1) > 0.001:
                invalid_post_sums += 1
        event_names: dict[str, str] = {}
        for collection in (item.get("buyOrders"), item.get("redeemOrders"), item.get("convertOrders"), extra.get("modelTargetComposition")):
            if not isinstance(collection, list):
                continue
            for row in collection:
                if not isinstance(row, dict):
                    continue
                source_code = str(row.get("fundCode") or "").strip()
                target_code = str(row.get("targetFundCode") or "").strip()
                if source_code and (row.get("fundName") or row.get("mainFundName")):
                    event_names[source_code] = str(row.get("fundName") or row.get("mainFundName"))
                if target_code and row.get("targetFundName"):
                    event_names[target_code] = str(row.get("targetFundName"))
        buy_orders = item.get("buyOrders") if isinstance(item.get("buyOrders"), list) else []
        redeem_orders = item.get("redeemOrders") if isinstance(item.get("redeemOrders"), list) else []
        convert_orders = item.get("convertOrders") if isinstance(item.get("convertOrders"), list) else []
        signal_events.append(
            {
                "signal_event_id": event_id,
                "channel_id": CHANNEL_ID,
                "source_strategy_id": code,
                "strategy_name": strategy_name,
                "signal_date": signal_date,
                "signal_time": item.get("createdTime"),
                "signal_title": item.get("adjustSummary") or item.get("sigSummary"),
                "signal_reason": item.get("description"),
                "signal_summary": item.get("sigSummary"),
                "buy_instruction_count": len(buy_orders),
                "redeem_instruction_count": len(redeem_orders),
                "convert_instruction_count": len(convert_orders),
                "buy_total_amount": number(item.get("buyTotalAmount")),
                "buy_mode": item.get("buyMode"),
                "convert_mode": item.get("convertMode"),
                "source_event_id": item.get("id"),
                "source_signal_id": item.get("sigId"),
                "source_dedup_key": item.get("dedupKey"),
                "expected_confirm_day": extra.get("expectConfirmDay"),
                "official_turnover_rate": number(extra.get("turnoverRate")),
                "has_exact_pre_position": bool(pre) and abs(pre_sum - 1) <= 0.001,
                "has_exact_post_position": bool(post) and abs(post_sum - 1) <= 0.001,
                "pre_weight_sum": round(pre_sum, 8),
                "post_weight_sum": round(post_sum, 8),
                "confidence_level": "official_signed_public_signal_event",
                "access_level": "anonymous_public_signed_page_protocol",
                "run_id": run_id,
            }
        )
        instruction_sequence = 0
        for raw_action, orders, semantics in (
            ("buy", buy_orders, "official_new_cash_distribution_ratio_not_portfolio_weight"),
            ("redeem", redeem_orders, "official_redeem_order_ratio_not_portfolio_weight"),
            ("convert", convert_orders, "official_conversion_split_ratio_not_portfolio_weight"),
        ):
            for order in orders:
                if not isinstance(order, dict):
                    continue
                instruction_sequence += 1
                fund_code = str(order.get("fundCode") or "").strip() or None
                target_fund_code = str(order.get("targetFundCode") or "").strip() or None
                lookup_code = target_fund_code if raw_action == "convert" else fund_code
                official_name = order.get("targetFundName") if raw_action == "convert" else order.get("fundName") or order.get("prodName")
                fund_name, resolution = resolve_fund_name(
                    lookup_code, official_name, event_names, current_names, db_names
                )
                before_row = pre.get(lookup_code or "")
                after_row = post.get(lookup_code or "")
                instructions.append(
                    {
                        "signal_instruction_id": f"{event_id}-{instruction_sequence}",
                        "signal_event_id": event_id,
                        "channel_id": CHANNEL_ID,
                        "source_strategy_id": code,
                        "signal_date": signal_date,
                        "instruction_sequence": instruction_sequence,
                        "raw_action": raw_action,
                        "source_fund_code": fund_code,
                        "source_fund_name": order.get("fundName"),
                        "target_fund_code": target_fund_code,
                        "target_fund_name": order.get("targetFundName"),
                        "fund_code": lookup_code,
                        "fund_name": fund_name,
                        "fund_name_resolution": resolution,
                        "instruction_ratio": number(order.get("percent")),
                        "instruction_ratio_semantics": semantics,
                        "instruction_amount": number(order.get("amount")),
                        "before_portfolio_weight": before_row.get("weight") if before_row else None,
                        "after_portfolio_weight": after_row.get("weight") if after_row else None,
                        "portfolio_weight_source": "official_full_pre_post_snapshots"
                        if before_row or after_row
                        else None,
                        "raw_record_hash": stable_hash(
                            {"strategy_code": code, "event_id": raw_event_id, "sequence": instruction_sequence, "order": order}
                        ),
                        "confidence_level": "official_signed_public_signal_instruction",
                        "run_id": run_id,
                    }
                )
        for phase, mapping, position_date in (
            ("pre_observed", pre, shanghai_date(pre_container.get("updatedDate")) or signal_date),
            ("post_target", post, signal_date),
        ):
            for fund_code, value in mapping.items():
                raw = value["raw"]
                fund_name, resolution = resolve_fund_name(
                    fund_code,
                    raw.get("fundName") or raw.get("mainFundName"),
                    event_names,
                    current_names,
                    db_names,
                )
                snapshots.append(
                    {
                        "snapshot_id": f"qieman-signal-history-{code}-{raw_event_id}-{phase}",
                        "source_event_id": event_id,
                        "snapshot_phase": phase,
                        "channel_id": CHANNEL_ID,
                        "source_strategy_id": code,
                        "position_date": position_date,
                        "disclosure_date": disclosure_date,
                        "fund_code": fund_code,
                        "fund_name": fund_name,
                        "fund_name_resolution": resolution,
                        "fund_weight": value["weight"],
                        "fund_weight_field": value.get("weight_field"),
                        "fund_nav": number(raw.get("nav")),
                        "market_value": number(raw.get("marketValue") or raw.get("capitalAmount")),
                        "fund_share": number(raw.get("share")),
                        "is_precise_weight": True,
                        "source_url": f"https://qieman.com/pmdj/v1/pomodels/{code}/sig-adjustments",
                        "raw_record_hash": stable_hash(
                            {"strategy_code": code, "event_id": raw_event_id, "phase": phase, "row": raw}
                        ),
                        "confidence_level": "official_signed_public_signal_full_position_snapshot",
                        "access_level": "anonymous_public_signed_page_protocol",
                        "run_id": run_id,
                    }
                )
        if post:
            exact_pre_post = bool(pre) and abs(pre_sum - 1) <= 0.001 and abs(post_sum - 1) <= 0.001
            projected_events.append(
                {
                    "rebalance_event_id": f"qieman-signal-projection-{code}-{raw_event_id}",
                    "source_signal_event_id": event_id,
                    "channel_id": CHANNEL_ID,
                    "source_strategy_id": code,
                    "rebalance_date": signal_date,
                    "new_position_date": signal_date,
                    "disclosure_date": disclosure_date,
                    "event_title": item.get("adjustSummary") or item.get("sigSummary"),
                    "event_reason": item.get("description"),
                    "payload_type": "signal_projection",
                    "before_weight_sum": round(pre_sum, 8),
                    "after_weight_sum": round(post_sum, 8),
                    "detail_count": len(set(pre) | set(post)),
                    "confidence_level": "official_exact_pre_post"
                    if exact_pre_post
                    else "official_post_target_only",
                    "is_official_regular_rebalance": False,
                    "run_id": run_id,
                }
            )
            for fund_code in sorted(set(pre) | set(post)):
                before = pre.get(fund_code, {}).get("weight", 0.0)
                after = post.get(fund_code, {}).get("weight", 0.0)
                raw = post.get(fund_code, {}).get("raw") or pre.get(fund_code, {}).get("raw") or {}
                fund_name, resolution = resolve_fund_name(
                    fund_code,
                    raw.get("fundName") or raw.get("mainFundName"),
                    event_names,
                    current_names,
                    db_names,
                )
                projected_deltas.append(
                    {
                        "rebalance_event_id": f"qieman-signal-projection-{code}-{raw_event_id}",
                        "source_signal_event_id": event_id,
                        "channel_id": CHANNEL_ID,
                        "source_strategy_id": code,
                        "rebalance_date": signal_date,
                        "fund_code": fund_code,
                        "fund_name": fund_name,
                        "fund_name_resolution": resolution,
                        "before_weight": before if pre else None,
                        "after_weight": after,
                        "weight_delta": after - before if pre else None,
                        "action_type": action_type(before, after) if pre else "initial_or_post_only",
                        "payload_type": "signal_projection",
                        "confidence_level": "official_exact_pre_post"
                        if exact_pre_post
                        else "official_post_target_only",
                        "eligible_for_official_rebalance_table": False,
                        "run_id": run_id,
                    }
                )
    position_history_complete = bool(items) and events_with_post == len(items) and invalid_post_sums == 0
    status = "official_endpoint_complete"
    if not payload.get("complete"):
        status = "incomplete_endpoint"
    elif not items:
        status = "official_endpoint_zero_disclosed_events"
    elif not position_history_complete:
        status = "events_complete_positions_partially_disclosed"
    return signal_events, instructions, snapshots, projected_events, projected_deltas, {
        "status": status,
        "events": len(signal_events),
        "instruction_rows": len(instructions),
        "events_with_pre_position": events_with_pre,
        "events_with_post_position": events_with_post,
        "events_missing_pre_position": len(items) - events_with_pre,
        "events_missing_post_position": len(items) - events_with_post,
        "invalid_pre_weight_sums": invalid_pre_sums,
        "invalid_post_weight_sums": invalid_post_sums,
        "post_weight_field_counts": dict(sorted(post_weight_field_counts.items())),
        "historical_position_rows": len(snapshots),
        "position_history_complete": position_history_complete,
    }


def main() -> None:
    args = parse_args()
    locks = active_locks()
    if locks:
        raise SystemExit("active production lock; Qieman history normalization aborted: " + ", ".join(locks))
    run_dir = args.run_dir.resolve()
    metadata_dir = args.metadata_dir.resolve()
    raw_dir = run_dir / "raw"
    output_dir = run_dir / "normalized"
    summary = read_json(run_dir / "summary.json")
    run_id = str(summary.get("runId") or run_dir.name)
    disclosure_date = f"{run_id[:4]}-{run_id[4:6]}-{run_id[6:8]}"
    results = summary.get("results") if isinstance(summary.get("results"), list) else []
    strategy_codes = [str(row.get("strategyCode")) for row in results if row.get("strategyCode")]
    result_by_code = {str(row.get("strategyCode")): row for row in results}

    masters_all = read_jsonl(metadata_dir / "strategy_master.jsonl")
    benchmarks_all = read_jsonl(
        args.benchmark_path.resolve()
        if args.benchmark_path
        else metadata_dir / "strategy_benchmark.jsonl"
    )
    holdings_all = read_jsonl(metadata_dir / "strategy_fund_snapshot.jsonl")
    masters = [row for row in masters_all if str(row.get("source_strategy_id")) in strategy_codes]
    benchmarks = [row for row in benchmarks_all if str(row.get("source_strategy_id")) in strategy_codes]
    holdings = [row for row in holdings_all if str(row.get("source_strategy_id")) in strategy_codes]
    master_by_code = {str(row.get("source_strategy_id")): row for row in masters}
    benchmark_by_code = {str(row.get("source_strategy_id")): row for row in benchmarks}
    current_by_code: dict[str, list[dict[str, Any]]] = defaultdict(list)
    current_names_by_code: dict[str, dict[str, str]] = defaultdict(dict)
    for row in holdings:
        code = str(row.get("source_strategy_id"))
        current_by_code[code].append(row)
        fund_code = str(row.get("fund_code") or "")
        if fund_code and row.get("fund_name"):
            current_names_by_code[code][fund_code] = str(row.get("fund_name"))
    db_names = load_fund_names(args.db_path.resolve() if args.db_path else None)

    performance_rows: list[dict[str, Any]] = []
    rebalance_events: list[dict[str, Any]] = []
    rebalance_deltas: list[dict[str, Any]] = []
    historical_positions: list[dict[str, Any]] = []
    signal_events: list[dict[str, Any]] = []
    signal_instructions: list[dict[str, Any]] = []
    signal_projected_events: list[dict[str, Any]] = []
    signal_projected_deltas: list[dict[str, Any]] = []
    assessments: list[dict[str, Any]] = []

    for code in strategy_codes:
        nav_path = raw_dir / "nav" / f"{code}.json"
        nav_payload = read_json(nav_path) if nav_path.exists() else None
        code_performance = normalise_performance(code, nav_payload, run_id)
        performance_rows.extend(code_performance)
        master = master_by_code.get(code, {})
        strategy_name = master.get("strategy_name")
        history_result = result_by_code.get(code, {}).get("history") or {}
        if code.startswith("SI"):
            history_path = raw_dir / "signal_adjustments" / f"{code}.json"
            history_payload = read_json(history_path) if history_path.exists() else {"content": [], "complete": False}
            se, si, hp, pe, pd, history_quality = normalise_signal_history(
                code,
                strategy_name,
                history_payload,
                run_id,
                disclosure_date,
                db_names,
                current_names_by_code.get(code, {}),
            )
            signal_events.extend(se)
            signal_instructions.extend(si)
            historical_positions.extend(hp)
            signal_projected_events.extend(pe)
            signal_projected_deltas.extend(pd)
            history_kind = "signal"
        else:
            history_path = raw_dir / "regular_adjustments" / f"{code}.json"
            history_payload = read_json(history_path) if history_path.exists() else {"content": [], "complete": False}
            re, rd, hp, history_quality = normalise_regular_history(
                code,
                history_payload,
                run_id,
                disclosure_date,
                db_names,
                current_names_by_code.get(code, {}),
            )
            rebalance_events.extend(re)
            rebalance_deltas.extend(rd)
            historical_positions.extend(hp)
            history_kind = "regular"

        nav_dates = [row["trade_date"] for row in code_performance]
        current_rows = current_by_code.get(code, [])
        current_sum = sum(zero_number(row.get("fund_weight")) for row in current_rows)
        benchmark = benchmark_by_code.get(code)
        performance_status = (
            "complete_official_daily_history"
            if code_performance
            else "official_endpoint_zero_rows"
            if result_by_code.get(code, {}).get("nav", {}).get("complete")
            else "fetch_incomplete"
        )
        benchmark_status = (
            "complete_exact_split"
            if benchmark and benchmark.get("is_exact_split")
            else "response_without_exact_split"
            if benchmark
            else "missing_response"
        )
        current_status = (
            "complete_exact_current_position"
            if current_rows and abs(current_sum - 1) <= 0.001
            else "missing_or_incomplete"
        )
        position_history_complete = (
            history_quality.get("position_history_complete")
            if history_kind == "signal"
            else history_quality.get("status")
            in {"official_endpoint_complete", "official_endpoint_zero_disclosed_events"}
        )
        history_retained_baseline = bool(
            history_result.get("retainedBaseline")
            or history_payload.get("retainedBaseline")
        )
        history_refresh_complete = bool(
            history_result.get("refreshComplete", True) is not False
            and history_payload.get("refreshComplete", True) is not False
            and not history_retained_baseline
        )
        assessments.append(
            {
                "source_strategy_id": code,
                "strategy_name": strategy_name,
                "strategy_kind": history_kind,
                "transport_complete": bool(result_by_code.get(code, {}).get("complete")),
                "performance": {
                    "rows": len(code_performance),
                    "first_trade_date": min(nav_dates) if nav_dates else None,
                    "last_trade_date": max(nav_dates) if nav_dates else None,
                    "status": performance_status,
                },
                "benchmark": {
                    "status": benchmark_status,
                    "description": benchmark.get("benchmark_description") if benchmark else None,
                    "component_count": len(benchmark.get("benchmark_components") or []) if benchmark else 0,
                    "is_exact_split": bool(benchmark and benchmark.get("is_exact_split")),
                },
                "current_position": {
                    "rows": len(current_rows),
                    "weight_sum": round(current_sum, 8),
                    "status": current_status,
                },
                "history": {
                    **history_quality,
                    "endpoint_row_count": history_result.get("rowCount"),
                    "refresh_complete": history_refresh_complete,
                    "retained_baseline": history_retained_baseline,
                    "retained_from_run_dir": history_result.get("retainedFromRunDir")
                    or history_payload.get("retainedFromRunDir"),
                    "refresh_error_code": history_result.get("refreshErrorCode")
                    or history_payload.get("refreshErrorCode"),
                },
                "complete_requested_data": bool(
                    code_performance
                    and benchmark
                    and benchmark.get("is_exact_split")
                    and current_status == "complete_exact_current_position"
                    and position_history_complete
                    and history_refresh_complete
                    and result_by_code.get(code, {}).get("complete")
                ),
            }
        )

    write_jsonl(output_dir / "strategy_master.jsonl", masters)
    write_jsonl(output_dir / "strategy_benchmark.jsonl", benchmarks)
    write_jsonl(output_dir / "strategy_fund_snapshot.jsonl", holdings)
    write_jsonl(output_dir / "strategy_performance_daily.jsonl", performance_rows)
    write_jsonl(output_dir / "strategy_rebalance_event.jsonl", rebalance_events)
    write_jsonl(output_dir / "strategy_rebalance_fund_delta.jsonl", rebalance_deltas)
    write_jsonl(output_dir / "strategy_fund_snapshot_history.jsonl", historical_positions)
    write_jsonl(output_dir / "signal_strategy_event.jsonl", signal_events)
    write_jsonl(output_dir / "signal_fund_instruction.jsonl", signal_instructions)
    write_jsonl(output_dir / "signal_rebalance_projection_event.jsonl", signal_projected_events)
    write_jsonl(output_dir / "signal_rebalance_projection_delta.jsonl", signal_projected_deltas)
    write_jsonl(output_dir / "strategy_coverage.jsonl", assessments)
    write_jsonl(
        output_dir / "strategy_incomplete_requested_data.jsonl",
        (row for row in assessments if not row.get("complete_requested_data")),
    )

    report = {
        "state": "qieman_signed_history_catalog_normalized",
        "run_id": run_id,
        "production_database_written": False,
        "daily_update_pipeline_touched": False,
        "catalog_strategy_count": len(strategy_codes),
        "complete_requested_data_count": sum(bool(row.get("complete_requested_data")) for row in assessments),
        "counts": {
            "strategy_master": len(masters),
            "strategy_benchmark": len(benchmarks),
            "strategy_fund_snapshot": len(holdings),
            "strategy_performance_daily": len(performance_rows),
            "strategy_rebalance_event": len(rebalance_events),
            "strategy_rebalance_fund_delta": len(rebalance_deltas),
            "strategy_fund_snapshot_history": len(historical_positions),
            "signal_strategy_event": len(signal_events),
            "signal_fund_instruction": len(signal_instructions),
            "signal_rebalance_projection_event": len(signal_projected_events),
            "signal_rebalance_projection_delta": len(signal_projected_deltas),
        },
        "coverage": {
            "performance_with_rows": sum(row["performance"]["rows"] > 0 for row in assessments),
            "performance_zero_rows": sum(row["performance"]["rows"] == 0 for row in assessments),
            "benchmark_exact_split": sum(row["benchmark"]["status"] == "complete_exact_split" for row in assessments),
            "benchmark_response_inexact": sum(row["benchmark"]["status"] == "response_without_exact_split" for row in assessments),
            "benchmark_missing_response": sum(row["benchmark"]["status"] == "missing_response" for row in assessments),
            "current_position_complete": sum(row["current_position"]["status"] == "complete_exact_current_position" for row in assessments),
            "regular_strategy_count": sum(row["strategy_kind"] == "regular" for row in assessments),
            "signal_strategy_count": sum(row["strategy_kind"] == "signal" for row in assessments),
            "signal_position_history_complete": sum(
                row["strategy_kind"] == "signal" and row["history"].get("position_history_complete")
                for row in assessments
            ),
            "signal_position_history_partial": sum(
                row["strategy_kind"] == "signal"
                and row["history"].get("events", 0) > 0
                and not row["history"].get("position_history_complete")
                for row in assessments
            ),
            "history_refresh_retained": sum(
                bool(row["history"].get("retained_baseline")) for row in assessments
            ),
            "history_refresh_complete": sum(
                bool(row["history"].get("refresh_complete")) for row in assessments
            ),
        },
        "quality": {
            "duplicate_performance_business_keys": duplicate_count(
                performance_rows, ("channel_id", "source_strategy_id", "trade_date")
            ),
            "duplicate_regular_event_business_keys": duplicate_count(rebalance_events, ("rebalance_event_id",)),
            "duplicate_regular_delta_business_keys": duplicate_count(
                rebalance_deltas, ("rebalance_event_id", "fund_code")
            ),
            "duplicate_signal_event_business_keys": duplicate_count(signal_events, ("signal_event_id",)),
            "duplicate_signal_instruction_business_keys": duplicate_count(
                signal_instructions, ("signal_instruction_id",)
            ),
            "duplicate_historical_position_business_keys": duplicate_count(
                historical_positions, ("snapshot_id", "fund_code")
            ),
            "missing_historical_position_fund_codes": sum(not row.get("fund_code") for row in historical_positions),
            "missing_historical_position_fund_names": sum(not row.get("fund_name") for row in historical_positions),
        },
        "data_boundary": {
            "catalog": "The 611-strategy production keyword union is a lower bound, not a proven official total.",
            "benchmark_daily_curve": "Not exposed by the collected endpoints; benchmark names and component weights are separate metadata facts.",
            "signal_instruction_ratio": "Order percent fields are retained as instruction ratios and are never treated as portfolio weights.",
            "signal_position": "Portfolio weights come only from official pre-observed and post-target full snapshots. Missing older snapshots are not reconstructed from order ratios.",
            "signal_projection": "Compatibility projections remain isolated and are not eligible for the official regular-rebalance table.",
            "zero_rows": "HTTP 200 with zero rows is an official zero-row disclosure, not fabricated history and not equivalent to a non-empty complete series.",
        },
        "input_sources": {
            "catalog_summary": str(run_dir / "summary.json"),
            "metadata_dir": str(metadata_dir),
            "benchmark_path": str(
                args.benchmark_path.resolve()
                if args.benchmark_path
                else metadata_dir / "strategy_benchmark.jsonl"
            ),
        },
        "strategy_assessments": assessments,
        "output_dir": str(output_dir),
    }
    write_json(run_dir / "normalized_quality_report.json", report)
    summary_lines = [
        "# 且慢全量历史数据覆盖报告",
        "",
        f"- 目录策略：{len(strategy_codes)} 个（关键词并集生产口径下限，不冒充官方 total）。",
        f"- 请求数据完整：{report['complete_requested_data_count']} 个；有明确缺口：{len(strategy_codes) - report['complete_requested_data_count']} 个。",
        f"- 日度业绩：{report['coverage']['performance_with_rows']} 个策略，{len(performance_rows)} 行。",
        f"- 精确基准拆分：{report['coverage']['benchmark_exact_split']} 个；非精确官方响应：{report['coverage']['benchmark_response_inexact']} 个。",
        f"- 精确当前仓位：{report['coverage']['current_position_complete']} 个。",
        f"- 普通调仓：{len(rebalance_events)} 个事件，{len(rebalance_deltas)} 条基金权重明细。",
        f"- 发车信号：{len(signal_events)} 个事件，{len(signal_instructions)} 条买入/赎回/转换指令。",
        f"- 历史仓位：{len(historical_positions)} 行；发车类完整 {report['coverage']['signal_position_history_complete']} 个、部分披露 {report['coverage']['signal_position_history_partial']} 个。",
        "- 发车指令比例不是组合仓位；历史仓位只使用官方调前/调后完整快照，缺失旧快照不做推算。",
        "- 本批次未写生产数据库、未修改每日更新 DAG。",
    ]
    (run_dir / "normalized_coverage_summary.md").write_text(
        "\n".join(summary_lines) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
