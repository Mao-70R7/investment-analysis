from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from runtime_workspace import load_workspace


PROJECT_ROOT = next(
    parent
    for parent in Path(__file__).resolve().parents
    if (parent / "AGENTS.md").is_file() and (parent / "本机配置" / "runtime.local.json").is_file()
)
WORKSPACE = load_workspace(PROJECT_ROOT)
CHANNEL_ID = "southern"
CHANNEL_NAME = "南方基金/司南投顾"
LIVE_DIR = WORKSPACE.raw_root / CHANNEL_ID / "live_collect"
NORMALIZED_DIR = WORKSPACE.normalized_root / CHANNEL_ID


def now_id() -> str:
    return datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z")


def iso_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def day_now() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) for row in rows)
    path.write_text(text + ("\n" if rows else ""), encoding="utf-8")


def stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def to_number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def strip_html(value: str | None) -> str | None:
    if not value:
        return value
    return value.replace("<strong>", "").replace("</strong>", "").replace("<br/>", "\n")


def normalize_advisory_fee(trade_rate_result: dict[str, Any]) -> str | None:
    direct = strip_html(trade_rate_result.get("inIAServiceRate"))
    if direct:
        return direct
    tiers = [
        (str(item.get("cyje") or "").strip(), str(item.get("rate") or "").strip())
        for item in (trade_rate_result.get("ratelist") or [])
        if re.search(r"\d", str(item.get("rate") or ""))
    ]
    if not tiers:
        return None
    if len(tiers) == 1 and tiers[0][0] in {"", "-"}:
        return f"费率（年）：{tiers[0][1]}"
    return "费率（年）：" + "；".join(f"{amount} {rate}".strip() for amount, rate in tiers)


def normalize_date(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value)
    match = re.search(r"(20\d{2}[-/]?\d{2}[-/]?\d{2})", text)
    if not match:
        return None
    token = match.group(1).replace("/", "-")
    if "-" not in token and len(token) == 8:
        return token
    return token


def dashed_date(value: str | None) -> str | None:
    if not value:
        return None
    token = normalize_date(value)
    if not token:
        return None
    if "-" in token:
        return token
    return f"{token[:4]}-{token[4:6]}-{token[6:8]}"


def compact_date(value: str | None) -> str | None:
    token = dashed_date(value)
    return token.replace("-", "") if token else None


def parse_json_response(events: list[dict[str, Any]], url_key: str) -> dict[str, Any]:
    matched = [item for item in events if url_key in str(item.get("url") or "") and item.get("response_text")]
    if not matched:
        return {}
    text = matched[-1]["response_text"]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {}


def parse_ratio_info(ratio_info: str | None) -> list[dict[str, Any]]:
    if not ratio_info:
        return []
    rows: list[dict[str, Any]] = []
    for part in ratio_info.split(","):
        if not part.strip():
            continue
        fund_code, _, weight = part.partition("|")
        rows.append({"fund_code": fund_code.strip(), "fund_weight": to_number(weight.strip())})
    return rows


def find_latest_artifact() -> Path:
    files = sorted(LIVE_DIR.glob("southern_plan_detail-*.json"))
    if not files:
        raise FileNotFoundError(f"No southern_plan_detail artifact found under {LIVE_DIR}")
    return files[-1]


def normalize(artifact_path: Path, run_id: str, captured_at: str) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    artifact = read_json(artifact_path)
    events = artifact.get("events") or []
    comb_info = parse_json_response(events, "webIAqueryCombInfo")
    market = parse_json_response(events, "webIAcombFundMarketQuery")
    trade_rate = parse_json_response(events, "webIAqueryTradeRate")
    report = parse_json_response(events, "ia_report")

    strategy = comb_info.get("result") or {}
    if not strategy:
        raise ValueError("Missing webIAqueryCombInfo result in live artifact.")
    market_info = ((market.get("result") or {}).get("info") or {})
    market_list = market_info.get("comblist") or []
    benchmark_list = market_info.get("benchmarklist") or []
    if not market_list:
        raise ValueError("Missing webIAcombFundMarketQuery comblist in live artifact.")

    sort_list = strategy.get("sortlist") or []
    asset_config = next((item for item in sort_list if "资产配置" in str(item.get("name") or "")), {})
    ratio_list = asset_config.get("ratiolist") or []
    fund_list = asset_config.get("fundlist") or []
    source_strategy_id = str(strategy.get("combcode") or "79")
    page_url = ((artifact.get("after") or {}).get("url") or artifact.get("page_url"))
    latest_market_date = dashed_date(market_list[-1].get("date"))
    current_position_date = next(
        (
            item
            for item in [
                dashed_date(strategy.get("enddate")),
                dashed_date(strategy.get("positiondate")),
                dashed_date(asset_config.get("fundRadioDate")),
                latest_market_date,
            ]
            if item
        ),
        latest_market_date,
    )

    trade_rate_result = trade_rate.get("result") or {}
    benchmark = (sort_list[0] or {}).get("desc") if sort_list else None
    strategy_master = [
        {
            "channel_id": CHANNEL_ID,
            "source_strategy_id": source_strategy_id,
            "strategy_name": strategy.get("combname") or strategy.get("title"),
            "advisor_name": CHANNEL_NAME,
            "strategy_type": strategy.get("scenename") or strategy.get("strategy_type"),
            "risk_level": asset_config.get("riskLevelDesc") or strategy.get("riskLevelDesc"),
            "launch_date": dashed_date(strategy.get("setupdate")) or strategy.get("setupdate"),
            "suggested_holding_period": "2年以上",
            "minimum_amount": 500,
            "advisory_fee_rate": normalize_advisory_fee(trade_rate_result),
            "benchmark": benchmark,
            "tags": [item for item in [strategy.get("scenename"), strategy.get("provider"), strategy.get("manager")] if item],
            "strategy_description": strategy.get("title") or strategy.get("combname"),
            "status": strategy.get("status"),
            "source_url": page_url,
            "first_seen_at": captured_at,
            "last_seen_at": captured_at,
            "raw_path": str(artifact_path),
            "run_id": run_id,
            "captured_at": captured_at,
        }
    ]

    asset_snapshot = [
        {
            "snapshot_id": f"{CHANNEL_ID}-{source_strategy_id}-asset-{current_position_date}",
            "channel_id": CHANNEL_ID,
            "source_strategy_id": source_strategy_id,
            "position_date": current_position_date,
            "asset_class": item.get("name"),
            "asset_classify": item.get("classify"),
            "asset_weight": to_number(item.get("cur_ratio")),
            "weight_unit": "percent_point",
            "source_url": page_url,
            "confidence_level": "official_exact",
            "access_level": "login",
            "raw_path": str(artifact_path),
            "run_id": run_id,
            "captured_at": captured_at,
        }
        for item in ratio_list
    ]

    fund_name_map = {item.get("fundcode"): item.get("fundname") for item in fund_list}
    fund_type_map = {item.get("fundcode"): item.get("name") for item in fund_list}
    current_fund_snapshot = [
        {
            "snapshot_id": f"{CHANNEL_ID}-{source_strategy_id}-current-{current_position_date}",
            "channel_id": CHANNEL_ID,
            "source_strategy_id": source_strategy_id,
            "position_date": current_position_date,
            "disclosure_date": current_position_date,
            "fund_code": item.get("fundcode"),
            "fund_name": item.get("fundname"),
            "fund_asset_type": item.get("name"),
            "fund_group_name": item.get("name"),
            "fund_weight": to_number(item.get("cur_ratio")),
            "weight_unit": "percent_point",
            "fund_nav": None,
            "fund_nav_date": None,
            "is_precise_weight": True,
            "is_login_required": True,
            "source_url": page_url,
            "raw_record_hash": stable_hash(item),
            "confidence_level": "official_exact",
            "access_level": "login",
            "raw_path": str(artifact_path),
            "run_id": run_id,
            "captured_at": captured_at,
        }
        for item in fund_list
    ]

    performance_daily: list[dict[str, Any]] = []
    position_daily: list[dict[str, Any]] = []
    daily_weights: dict[str, dict[str, float | None]] = {}
    benchmark_return_by_date = {
        trade_date: to_number(item.get("regionRatio"))
        for item in benchmark_list
        if (trade_date := dashed_date(str(item.get("date") or "")))
    }
    for item in market_list:
        raw_trade_date = str(item.get("date") or "")
        trade_date = dashed_date(raw_trade_date)
        if not trade_date:
            continue
        performance_daily.append(
            {
                "channel_id": CHANNEL_ID,
                "source_strategy_id": source_strategy_id,
                "trade_date": trade_date,
                "nav": to_number(item.get("nav")),
                "daily_return": to_number(item.get("upratio")),
                "cumulative_return": to_number(item.get("regionRatio")),
                "benchmark_return": benchmark_return_by_date.get(trade_date),
                "index_return": None,
                "max_drawdown": None,
                # The authenticated market endpoint already returns percentage
                # points.  The legacy public IA006 endpoint used decimal ratios.
                "return_unit": "percent_point",
                "source_url": page_url,
                "confidence_level": "official_exact",
                "access_level": "login",
                "raw_path": str(artifact_path),
                "run_id": run_id,
                "captured_at": captured_at,
            }
        )
        parsed_ratio = parse_ratio_info(item.get("ratioinfo"))
        daily_weights[str(trade_date)] = {item["fund_code"]: item["fund_weight"] for item in parsed_ratio}
        for ratio_item in parsed_ratio:
            fund_code = ratio_item["fund_code"]
            position_daily.append(
                {
                    "snapshot_id": f"{CHANNEL_ID}-{source_strategy_id}-history-{trade_date}",
                    "channel_id": CHANNEL_ID,
                    "source_strategy_id": source_strategy_id,
                    "position_date": trade_date,
                    "disclosure_date": trade_date,
                    "fund_code": fund_code,
                    "fund_name": fund_name_map.get(fund_code),
                    "fund_asset_type": fund_type_map.get(fund_code),
                    "fund_group_name": fund_type_map.get(fund_code),
                    "fund_weight": ratio_item["fund_weight"],
                    "weight_unit": "percent_point",
                    "fund_nav": None,
                    "fund_nav_date": None,
                    "is_precise_weight": True,
                    "is_login_required": True,
                    "source_url": page_url,
                "raw_record_hash": stable_hash({"date": raw_trade_date, **ratio_item}),
                    "confidence_level": "official_exact",
                    "access_level": "login",
                    "raw_path": str(artifact_path),
                    "run_id": run_id,
                    "captured_at": captured_at,
                }
            )

    rebalance_events: list[dict[str, Any]] = []
    rebalance_deltas: list[dict[str, Any]] = []
    previous_date: str | None = None
    previous_fund_set: set[str] | None = None
    for trade_date in sorted(daily_weights):
        current_weights = daily_weights[trade_date]
        current_fund_set = set(current_weights)
        if previous_fund_set is not None and current_fund_set == previous_fund_set:
            previous_date = trade_date
            continue

        event_key = compact_date(trade_date) or trade_date
        event_id = f"{CHANNEL_ID}-{source_strategy_id}-ratioinfo-{event_key}"
        event_date = dashed_date(trade_date)
        prev_event_date = dashed_date(previous_date)
        is_initial = previous_fund_set is None
        event_sequence = len(rebalance_events) + 1
        rebalance_events.append(
            {
                "rebalance_event_id": event_id,
                "channel_id": CHANNEL_ID,
                "source_strategy_id": source_strategy_id,
                "rebalance_date": event_date,
                "previous_position_date": prev_event_date,
                "new_position_date": event_date,
                "disclosure_date": event_date,
                "event_title": (
                    "司南投顾初始仓位（官方每日ratioinfo）"
                    if is_initial
                    else "司南投顾调仓（由官方每日ratioinfo基金集合变化推断）"
                ),
                "event_reason": "官方未在当前页面显式披露调仓公告；根据每日ratioinfo基金集合变化生成，用于回放分析。",
                "previous_position_date_is_inferred": not is_initial,
                "event_sequence": event_sequence,
                "event_time": None,
                "payload_type": "official_daily_ratioinfo",
                "confidence_level": "official_daily_ratioinfo_inferred",
                "source_snapshot_id": f"{CHANNEL_ID}-{source_strategy_id}-history-{trade_date}",
                "source_url": page_url,
                "raw_path": str(artifact_path),
                "run_id": run_id,
                "captured_at": captured_at,
            }
        )

        previous_weights = daily_weights.get(previous_date or "", {})
        for fund_code in sorted(set(previous_weights) | set(current_weights)):
            before_weight = previous_weights.get(fund_code)
            after_weight = current_weights.get(fund_code)
            before_value = before_weight if before_weight is not None else 0.0
            after_value = after_weight if after_weight is not None else 0.0
            delta = round(after_value - before_value, 6)
            if is_initial:
                action_type = "initial"
            elif before_value == 0 and after_value > 0:
                action_type = "add"
            elif before_value > 0 and after_value == 0:
                action_type = "remove"
            elif delta > 0:
                action_type = "increase"
            elif delta < 0:
                action_type = "decrease"
            else:
                action_type = "unchanged"
            rebalance_deltas.append(
                {
                    "rebalance_event_id": event_id,
                    "channel_id": CHANNEL_ID,
                    "source_strategy_id": source_strategy_id,
                    "fund_code": fund_code,
                    "fund_name": fund_name_map.get(fund_code) or fund_code,
                    "fund_group_name": fund_type_map.get(fund_code),
                    "before_weight": before_weight,
                    "after_weight": after_weight,
                    "weight_delta": delta,
                    "action_type": action_type,
                    "source_snapshot_id": f"{CHANNEL_ID}-{source_strategy_id}-history-{trade_date}",
                    "source_url": page_url,
                    "raw_path": str(artifact_path),
                    "run_id": run_id,
                    "captured_at": captured_at,
                }
            )
        previous_fund_set = current_fund_set
        previous_date = trade_date

    notices = [
        {
            "channel_id": CHANNEL_ID,
            "source_strategy_id": source_strategy_id,
            "publish_date": item.get("PUBLISHTIME"),
            "title": item.get("TITLE"),
            "url": item.get("LINKURL"),
            "source": "ia_report",
            "raw_path": str(artifact_path),
            "run_id": run_id,
            "captured_at": captured_at,
        }
        for item in (report.get("data") or [])
    ]

    normalized = {
        "strategy_master": strategy_master,
        # Current and historical facts must remain separate.  The database loader
        # selects the latest date from strategy_fund_snapshot, while the complete
        # daily series is loaded only from strategy_fund_snapshot_history.
        "strategy_fund_snapshot": current_fund_snapshot,
        "strategy_asset_snapshot": asset_snapshot,
        "strategy_fund_snapshot_current": current_fund_snapshot,
        "strategy_performance_daily": performance_daily,
        "strategy_fund_snapshot_history": position_daily,
        "strategy_rebalance_event": rebalance_events,
        "strategy_rebalance_fund_delta": rebalance_deltas,
        "strategy_notice": notices,
    }
    summary = {
        "channel_id": CHANNEL_ID,
        "channel_name": CHANNEL_NAME,
        "run_id": run_id,
        "captured_at": captured_at,
        "collection_status": "success_login_live_artifact",
        "holding_penetration_status": "fund_weight_exact_login",
        "strategy_total": len(strategy_master),
        "current_holding_rows": len(current_fund_snapshot),
        "history_days": len(performance_daily),
        "history_position_rows": len(position_daily),
        "rebalance_event_total": len(rebalance_events),
        "raw_path": str(artifact_path),
        "counts": {key: len(rows) for key, rows in normalized.items()},
        "strategy": strategy_master[0],
        "latest_market_date": latest_market_date,
        "latest_nav": performance_daily[-1]["nav"],
        "latest_daily_return": performance_daily[-1]["daily_return"],
    }
    return normalized, summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, help="southern_plan_detail artifact path; defaults to latest.")
    parser.add_argument("--run-id", default=now_id())
    parser.add_argument("--captured-at", default=iso_now())
    args = parser.parse_args()

    artifact_path = args.input or find_latest_artifact()
    run_date = day_now()
    normalized, summary = normalize(artifact_path, args.run_id, args.captured_at)
    for entity, rows in normalized.items():
        write_jsonl(NORMALIZED_DIR / entity / run_date / f"{args.run_id}.jsonl", rows)
    write_json(NORMALIZED_DIR / "collection_summary" / run_date / f"{args.run_id}.json", summary)
    write_json(
        LIVE_DIR / f"normalized_summary-{args.run_id}.json",
        summary,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
