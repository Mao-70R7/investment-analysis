from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime
from pathlib import Path
from typing import Any

from probe_qieman_device import (
    PROBE_ROOT,
    acquire_device_lock,
    active_locks,
    now_local,
    redact_sensitive_text,
    release_device_lock,
    run_adb,
    write_json,
)


PACKAGE_NAME = "cn.yingmi.qieman.hermione"
REMOTE_XML = "/sdcard/qieman_codex_strategy_window.xml"
SAFE_TAB_COORDINATES = {
    "performance": (310, 330),
    "configuration": (540, 330),
    "announcement": (765, 330),
}
DROP_TERMS = (
    "本次的投资目标",
    "已确认，继续了解此策略",
    "买入【",
    "确认购买",
    "身份证",
    "银行卡",
    "默认账户",
    "资产总额",
    "总资产",
    "持有收益",
)
UUID_RE = re.compile(r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b")
FUND_CODE_RE = re.compile(r"^\d{6}$")
PERCENT_RE = re.compile(r"^([+-]?\d+(?:\.\d+)?)%$")
ISO_DATE_RE = re.compile(r"^20\d{2}-\d{2}-\d{2}$")
MONTH_DAY_RE = re.compile(r"^(\d{2})-(\d{2})$")


def stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)
    path.write_text(text, encoding="utf-8")


def sanitize_dom_text(value: str | None) -> str | None:
    if not value:
        return None
    clean = html.unescape(value).replace("\u200b", "").strip()
    if not clean:
        return None
    if len(clean) > 2000 or "base64," in clean.lower() or clean.lstrip().startswith("<svg"):
        return None
    if UUID_RE.search(clean) or any(term in clean for term in DROP_TERMS):
        return None
    clean = re.sub(
        r"(?i)authorization\s*[:=]\s*(?:bearer\s+)?[^\s&\"']+",
        "Authorization: <redacted>",
        clean,
    )
    clean = redact_sensitive_text(clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean or None


def extract_sanitized_nodes(xml_bytes: bytes) -> list[dict[str, Any]]:
    root = ET.fromstring(xml_bytes.decode("utf-8", errors="replace"))
    rows: list[dict[str, Any]] = []
    for node in root.iter("node"):
        for attribute in ("text", "content-desc"):
            clean = sanitize_dom_text(node.attrib.get(attribute))
            if not clean:
                continue
            row = {
                "text": clean,
                "attribute": attribute,
                "class": node.attrib.get("class") or None,
                "resource_id": node.attrib.get("resource-id") or None,
                "bounds": node.attrib.get("bounds") or None,
                "clickable": node.attrib.get("clickable") == "true",
            }
            if rows and rows[-1]["text"] == clean and rows[-1]["bounds"] == row["bounds"]:
                continue
            rows.append(row)
    return rows


def ordered_texts(nodes: list[dict[str, Any]]) -> list[str]:
    result: list[str] = []
    for node in nodes:
        value = node["text"]
        if result and result[-1] == value:
            continue
        result.append(value)
    return result


def parse_percentage(value: str | None) -> float | None:
    match = PERCENT_RE.fullmatch(value or "")
    return float(match.group(1)) if match else None


def previous_value(texts: list[str], label: str, max_distance: int = 3) -> str | None:
    for index, value in enumerate(texts):
        if value != label:
            continue
        for candidate in reversed(texts[max(0, index - max_distance) : index]):
            if PERCENT_RE.fullmatch(candidate) or re.fullmatch(r"\d+(?:\.\d+)?", candidate):
                return candidate
    return None


def following_value(texts: list[str], label_prefix: str, max_distance: int = 3) -> str | None:
    for index, value in enumerate(texts):
        if value.startswith(label_prefix):
            suffix = value[len(label_prefix) :].lstrip("：: ")
            if suffix:
                return suffix
            for candidate in texts[index + 1 : index + 1 + max_distance]:
                if candidate and candidate not in {"本策略", "业绩基准"}:
                    return candidate
    return None


def infer_full_date(month_day: str | None, as_of_date: str | None) -> str | None:
    match = MONTH_DAY_RE.fullmatch(month_day or "")
    if not match or not as_of_date:
        return None
    year = int(as_of_date[:4])
    month, day = int(match.group(1)), int(match.group(2))
    try:
        candidate = date(year, month, day)
        reference = date.fromisoformat(as_of_date)
    except ValueError:
        return None
    if candidate > reference:
        candidate = date(year - 1, month, day)
    return candidate.isoformat()


def looks_like_fund_name(value: str) -> bool:
    return not value.startswith("无法买入时") and bool(
        re.search(r"基金|货币|债|混合|指数|ETF|LOF|QDII|商品|股票|钱袋子", value, re.IGNORECASE)
    )


def parse_holdings(texts: list[str], as_of_date: str | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    fund_header_index = next((index for index, value in enumerate(texts) if value == "基金"), None)
    weight_label_index = next((index for index, value in enumerate(texts) if value == "配置占比"), None)
    daily_search_after = weight_label_index if weight_label_index is not None else fund_header_index
    daily_label_index = next(
        (
            index
            for index, value in enumerate(texts)
            if value == "日涨跌" and (daily_search_after is None or index > daily_search_after)
        ),
        None,
    )
    section_end = weight_label_index if weight_label_index is not None else daily_label_index
    for code_index, code in enumerate(texts):
        if section_end is not None and code_index > section_end:
            break
        if not FUND_CODE_RE.fullmatch(code):
            continue
        name = texts[code_index - 1] if code_index else None
        if not name:
            continue
        in_explicit_fund_section = fund_header_index is not None and code_index > fund_header_index
        if name.startswith("无法买入时") or name.startswith("与") or (not in_explicit_fund_section and not looks_like_fund_name(name)):
            continue
        rows.append(
            {
                "fund_code": code,
                "fund_name": name,
                "fund_weight": None,
                "fund_nav_date": None,
            }
        )
    unique: dict[str, dict[str, Any]] = {}
    for row in rows:
        unique.setdefault(row["fund_code"], row)
    ordered = list(unique.values())

    if weight_label_index is not None and daily_label_index is not None and weight_label_index < daily_label_index:
        candidates = [
            parse_percentage(value)
            for value in texts[weight_label_index + 1 : daily_label_index]
            if PERCENT_RE.fullmatch(value)
        ]
        candidates = [value for value in candidates if value is not None]
        possible_weights = candidates[: len(ordered)]
        if len(possible_weights) == len(ordered) and 99.5 <= sum(possible_weights) <= 100.5:
            for row, weight in zip(ordered, possible_weights, strict=True):
                row["fund_weight"] = weight

    if daily_label_index is not None:
        nav_dates = [value for value in texts[daily_label_index + 1 :] if MONTH_DAY_RE.fullmatch(value)]
        if len(nav_dates) >= len(ordered):
            for row, nav_date in zip(ordered, nav_dates[: len(ordered)], strict=True):
                row["fund_nav_date"] = infer_full_date(nav_date, as_of_date)
    return ordered


def parse_asset_allocations(texts: list[str]) -> list[dict[str, Any]]:
    known_labels = {"QDII", "债券型", "股票型", "另类型", "另类型", "混合型", "货币型", "指数型", "FOF"}
    rows: list[dict[str, Any]] = []
    for index, label in enumerate(texts[:-1]):
        if label not in known_labels:
            continue
        value = parse_percentage(texts[index + 1])
        if value is None:
            continue
        rows.append({"asset_label": "其他类型" if label in {"另类型", "另类型"} else label, "asset_weight": value})
    unique: dict[str, dict[str, Any]] = {}
    for row in rows:
        unique.setdefault(row["asset_label"], row)
    return list(unique.values())


def parse_rebalance(texts: list[str]) -> dict[str, Any] | None:
    recent_index = next((index for index, value in enumerate(texts) if value in {"最近调仓", "最新调仓"}), None)
    if recent_index is not None:
        event_date = next((value for value in texts[recent_index + 1 : recent_index + 5] if ISO_DATE_RE.fullmatch(value)), None)
        reason = next(
            (
                value
                for value in texts[recent_index + 1 : recent_index + 8]
                if len(value) >= 12 and value not in {"调仓记录", "最近调仓", "最新调仓"} and not ISO_DATE_RE.fullmatch(value)
            ),
            None,
        )
        if not reason:
            return None
    else:
        joined = "\n".join(texts)
        reason_match = re.search(r"([^\n]{0,40}(?:例行)?调仓[^\n]{8,500})", joined)
        if not reason_match:
            return None
        reason = reason_match.group(1).strip()
        reason_index = joined[: reason_match.start()].count("\n")
        candidates = [value for value in texts[max(0, reason_index - 8) : reason_index + 3] if ISO_DATE_RE.fullmatch(value)]
        event_date = candidates[-1] if candidates else None
    if "用户在社区" in reason or "自动调仓失败" in reason:
        return None
    no_trade = any(term in reason for term in ("不需要调仓", "无需调仓", "保持不变"))
    return {"rebalance_date": event_date, "event_reason": reason, "no_trade": no_trade}


def parse_strategy_data(
    texts: list[str],
    strategy_id: str,
    strategy_name: str,
    captured_at: datetime,
) -> dict[str, list[dict[str, Any]] | dict[str, Any]]:
    joined = "\n".join(texts)
    iso_dates = [value for value in texts if ISO_DATE_RE.fullmatch(value)]
    capture_date = captured_at.date().isoformat()
    daily_metric_index = next((index for index, value in enumerate(texts) if value in {"持仓日涨跌", "日涨跌"}), None)
    daily_metric_month_day = (
        next((value for value in texts[daily_metric_index + 1 : daily_metric_index + 8] if MONTH_DAY_RE.fullmatch(value)), None)
        if daily_metric_index is not None
        else None
    )
    as_of_date = infer_full_date(daily_metric_month_day, capture_date)
    advisor_match = re.search(r"本策略由([^\n]{2,40}?)提供", joined)
    risk_match = re.search(r"(?:R\d[（(]?[^\n]{0,8})?((?:低|中低|中|中高|高)风险)", joined)
    run_age_match = re.search(r"运行\s*(\d+年\d+天)", joined)
    minimum_match = re.search(r"(\d+(?:\.\d+)?)元起(?:购|投)", joined)
    minimum_amount = float(minimum_match.group(1)) if minimum_match else None
    if minimum_amount is None:
        for index, value in enumerate(texts):
            if value.startswith("元起") and index and re.fullmatch(r"\d+(?:\.\d+)?", texts[index - 1]):
                minimum_amount = float(texts[index - 1])
                break
    fee_label_index = next((index for index, value in enumerate(texts) if value.startswith("投顾服务费：")), None)
    fee_window = texts[fee_label_index : fee_label_index + 8] if fee_label_index is not None else []
    fee_rate_text = next((value for value in fee_window if re.search(r"[0-9.]+%(?:-[0-9.]+%)?/年", value)), None)
    effective_fee_match = re.search(r"折后\s*([0-9.]+%/年)", "\n".join(fee_window))
    discount_match = re.search(r"([0-9.]+折)", "\n".join(fee_window))
    cap_amount = next(
        (
            texts[index + 1]
            for index, value in enumerate(texts[:-1])
            if fee_label_index is not None and fee_label_index <= index < fee_label_index + 8 and "每月满" in value and re.fullmatch(r"\d+", texts[index + 1])
        ),
        None,
    )
    holding_period_match = re.search(r"建议持有\s*(\d+年(?:以上|以内))", joined)
    benchmark = next(
        (
            value.split("：", 1)[1].strip()
            for value in texts
            if value.startswith("业绩基准：") and len(value.split("：", 1)[1].strip()) > 3
        ),
        None,
    )
    if not benchmark:
        candidate = following_value(texts, "业绩基准", max_distance=4)
        benchmark = candidate if candidate and not PERCENT_RE.fullmatch(candidate) and not candidate.startswith("本策略") else None

    summary_metrics = {
        "as_of_date": as_of_date,
        "cumulative_return": parse_percentage(previous_value(texts, "累计收益率")) or parse_percentage(previous_value(texts, "累计收益")),
        "annualized_return": parse_percentage(previous_value(texts, "年化收益")),
        "max_drawdown": parse_percentage(previous_value(texts, "最大回撤")),
        "annualized_volatility": parse_percentage(previous_value(texts, "年化波动")),
        "sharpe_ratio": (
            float(previous_value(texts, "夏普比率")) if previous_value(texts, "夏普比率") else None
        ),
        "seven_day_annualized_return": parse_percentage(previous_value(texts, "七日年化收益率")),
        "ten_thousand_share_income": (
            float(previous_value(texts, "万份收益(元)")) if previous_value(texts, "万份收益(元)") else None
        ),
        "daily_return": parse_percentage(previous_value(texts, "持仓日涨跌")) or parse_percentage(previous_value(texts, "日涨跌")),
    }
    if all(value is None for key, value in summary_metrics.items() if key != "as_of_date"):
        summary_metrics = {"as_of_date": as_of_date}

    snapshot_seed = {
        "channel_id": "qieman",
        "source_strategy_id": strategy_id,
        "captured_at": captured_at.isoformat(timespec="seconds"),
        "texts": texts,
    }
    source_snapshot_id = "qieman-auth-ui-" + stable_hash(snapshot_seed)[:16]
    first_last_seen = captured_at.isoformat(timespec="seconds")
    master = {
        "channel_id": "qieman",
        "source_strategy_id": strategy_id,
        "strategy_name": strategy_name,
        "advisor_name": advisor_match.group(1).strip() if advisor_match else None,
        "strategy_type": next((value for value in texts[:20] if value in {"活钱", "长钱", "短期稳健", "长期投资"}), None),
        "risk_level": risk_match.group(1) if risk_match else None,
        "launch_date": None,
        "suggested_holding_period": holding_period_match.group(1) if holding_period_match else next((value for value in texts if re.fullmatch(r"\d+年(?:以上|以内)", value)), None),
        "minimum_amount": minimum_amount,
        "advisory_fee_rate": (
            f"{fee_rate_text}{f'，每月满{cap_amount}元封顶' if cap_amount else ''}"
            if fee_rate_text and "-" in fee_rate_text
            else f"{effective_fee_match.group(1)}（{discount_match.group(1)}）"
            if effective_fee_match and discount_match
            else effective_fee_match.group(1)
            if effective_fee_match
            else fee_rate_text
            if fee_rate_text
            else None
        ),
        "benchmark": benchmark,
        "tags": [value for value in texts[:20] if value in {"活钱", "短期稳健", "长期投资", "投顾策略", "低风险", "中低风险", "中风险", "中高风险", "高风险"}],
        "strategy_description": None,
        "status": "login_required",
        "source_url": None,
        "first_seen_at": first_last_seen,
        "last_seen_at": first_last_seen,
        "run_id": captured_at.strftime("%Y%m%dT%H%M%S%z") + "-strategy-dom",
        "source_snapshot_id": source_snapshot_id,
        "extra": {
            "running_age_text": run_age_match.group(1) if run_age_match else None,
            "performance_summary": summary_metrics,
            "performance_summary_semantics": "official_authenticated_ui_summary_not_interval_or_daily_curve",
        },
    }

    holding_candidates = parse_holdings(texts, capture_date)
    weight_complete = bool(holding_candidates) and all(row["fund_weight"] is not None for row in holding_candidates)
    weight_sum = round(sum(row["fund_weight"] or 0 for row in holding_candidates), 6) if weight_complete else None
    snapshot_id = f"qieman-{strategy_id}-holding-capture-{captured_at.date().isoformat()}-{source_snapshot_id[-8:]}"
    holdings: list[dict[str, Any]] = []
    for candidate in holding_candidates:
        raw_record_hash = stable_hash({"strategy_id": strategy_id, **candidate})
        holdings.append(
            {
                "snapshot_id": snapshot_id,
                "channel_id": "qieman",
                "source_strategy_id": strategy_id,
                "position_date": None,
                "disclosure_date": captured_at.date().isoformat(),
                "fund_code": candidate["fund_code"],
                "fund_name": candidate["fund_name"],
                "fund_asset_type": None,
                "fund_group_name": None,
                "fund_weight": candidate["fund_weight"],
                "fund_nav": None,
                "fund_nav_date": candidate["fund_nav_date"],
                "is_precise_weight": candidate["fund_weight"] is not None,
                "is_login_required": True,
                "source_url": None,
                "raw_record_hash": raw_record_hash,
                "confidence_level": (
                    "official_authenticated_ui_exact_weight_missing_position_date"
                    if candidate["fund_weight"] is not None
                    else "official_authenticated_ui_fund_list_no_weight"
                ),
                "access_level": "login",
                "run_id": master["run_id"],
                "source_snapshot_id": source_snapshot_id,
                "extra": {
                    "position_date_status": "not_disclosed_on_visible_configuration_page",
                    "weight_status": "exact" if candidate["fund_weight"] is not None else "not_disclosed",
                },
            }
        )

    rebalance = parse_rebalance(texts)
    events: list[dict[str, Any]] = []
    if rebalance and rebalance["rebalance_date"]:
        event_id = "qieman-" + stable_hash({"strategy_id": strategy_id, **rebalance})[:24]
        events.append(
            {
                "rebalance_event_id": event_id,
                "channel_id": "qieman",
                "source_strategy_id": strategy_id,
                "rebalance_date": rebalance["rebalance_date"],
                "previous_position_date": None,
                "new_position_date": None,
                "disclosure_date": captured_at.date().isoformat(),
                "event_title": f"{strategy_name} {'调仓复核' if rebalance['no_trade'] else '调仓'}",
                "event_reason": rebalance["event_reason"],
                "source_url": None,
                "source_snapshot_id": source_snapshot_id,
                "confidence_level": "official_authenticated_ui_exact_event_no_trade" if rebalance["no_trade"] else "official_authenticated_ui_event_only",
                "run_id": master["run_id"],
                "extra": {"no_trade": rebalance["no_trade"], "fund_delta_status": "empty_no_trade" if rebalance["no_trade"] else "not_disclosed"},
            }
        )

    allocations = [
        {
            "channel_id": "qieman",
            "source_strategy_id": strategy_id,
            "disclosure_date": captured_at.date().isoformat(),
            "position_date": None,
            "allocation_dimension": "fund_type",
            "source_snapshot_id": source_snapshot_id,
            "access_level": "login",
            **row,
        }
        for row in parse_asset_allocations(texts)
    ]
    assessment = {
        "strategy_id": strategy_id,
        "strategy_name": strategy_name,
        "captured_at": captured_at.isoformat(timespec="seconds"),
        "source_snapshot_id": source_snapshot_id,
        "text_node_count": len(texts),
        "entities": {
            "strategy_master": {"rows": 1, "status": "partial_login_ui"},
            "strategy_performance_daily": {"rows": 0, "status": "chart_visible_but_no_structured_daily_points"},
            "strategy_performance_interval": {"rows": 0, "status": "interval_tabs_visible_without_bound_values"},
            "strategy_fund_snapshot": {
                "rows": len(holdings),
                "status": (
                    "exact_fund_and_weight_missing_position_date"
                    if weight_complete
                    else "exact_fund_list_weights_not_disclosed"
                    if holdings
                    else "not_visible_or_not_parsed"
                ),
                "weight_sum": weight_sum,
                "weight_complete": weight_complete,
            },
            "strategy_rebalance_event": {"rows": len(events), "status": "official_event" if events else "not_visible_or_not_parsed"},
            "strategy_rebalance_fund_delta": {"rows": 0, "status": "empty_no_trade_event" if events and events[0]["extra"]["no_trade"] else "not_disclosed"},
            "strategy_asset_allocation_sample": {
                "rows": len(allocations),
                "status": "exact_aggregate_weight_missing_position_date" if allocations else "not_visible_or_not_parsed",
                "weight_sum": round(sum(row["asset_weight"] for row in allocations), 6) if allocations else None,
            },
        },
        "field_status": {
            "benchmark": "verified" if benchmark else "not_visible",
            "launch_date": "not_disclosed_running_age_is_not_launch_date",
            "position_date": "not_disclosed",
            "fund_nav_date": "visible_date_is_fund_daily_change_date_not_position_date" if holdings else "not_visible",
            "daily_curve": "not_extractable_from_accessibility_tree",
        },
        "quality_gate": "sample_only_not_main_db_ready" if holdings and any(row["position_date"] is None for row in holdings) else "sample_only",
    }
    return {
        "strategy_master": [master],
        "strategy_summary_metrics": [{"channel_id": "qieman", "source_strategy_id": strategy_id, "source_snapshot_id": source_snapshot_id, **summary_metrics}],
        "strategy_performance_daily": [],
        "strategy_performance_interval": [],
        "strategy_fund_snapshot": holdings,
        "strategy_rebalance_event": events,
        "strategy_rebalance_fund_delta": [],
        "strategy_asset_allocation_sample": allocations,
        "coverage_assessment": assessment,
    }


def dump_window_xml(adb_path: str, device_id: str) -> bytes:
    diagnostics: list[str] = []
    run_adb(adb_path, device_id, "shell", "rm", "-f", REMOTE_XML, timeout=20)
    for attempt in range(3):
        command = ["shell", "uiautomator", "dump"]
        if attempt == 2:
            command.append("--compressed")
        command.append(REMOTE_XML)
        dumped = run_adb(adb_path, device_id, *command, timeout=60)
        diagnostics.append(
            f"attempt={attempt + 1},rc={dumped.returncode},stdout={dumped.stdout.strip()[:200]},stderr={dumped.stderr.strip()[:200]}"
        )
        read = run_adb(adb_path, device_id, "exec-out", "cat", REMOTE_XML, timeout=30, binary=True)
        if read.returncode == 0 and read.stdout and b"<hierarchy" in read.stdout:
            run_adb(adb_path, device_id, "shell", "rm", "-f", REMOTE_XML, timeout=20)
            return read.stdout
        time.sleep(1.5)
    run_adb(adb_path, device_id, "shell", "rm", "-f", REMOTE_XML, timeout=20)
    raise RuntimeError("uiautomator XML read failed after retries: " + " | ".join(diagnostics))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract sanitized, non-trading Qieman strategy fields from Android accessibility DOM.")
    parser.add_argument("--device-id")
    parser.add_argument("--strategy-id", required=True)
    parser.add_argument("--strategy-name", required=True)
    parser.add_argument("--safe-tab", choices=sorted(SAFE_TAB_COORDINATES))
    parser.add_argument("--wait-sec", type=float, default=2.0)
    parser.add_argument("--adb-path", default=str(PROBE_ROOT.parents[2] / "tools" / "platform-tools" / "adb.exe"))
    parser.add_argument("--output-root", type=Path, default=PROBE_ROOT / "runs")
    parser.add_argument(
        "--sanitized-nodes-input",
        type=Path,
        help="Re-normalize a prior sanitized_text_nodes.json without touching the device.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    captured_at = now_local()
    run_id = captured_at.strftime("%Y%m%dT%H%M%S%z") + "-strategy-dom"
    run_dir = args.output_root.resolve() / run_id / args.strategy_id
    if args.sanitized_nodes_input:
        payload = json.loads(args.sanitized_nodes_input.resolve().read_text(encoding="utf-8-sig"))
        nodes = payload.get("nodes") if isinstance(payload, dict) else None
        if not isinstance(nodes, list) or not all(isinstance(row, dict) and row.get("text") for row in nodes):
            raise ValueError("--sanitized-nodes-input must contain a {'nodes': [...]} payload")
        raw_xml_saved = False
        input_mode = "sanitized_dom_reparse"
    else:
        if not args.device_id:
            raise SystemExit("--device-id is required unless --sanitized-nodes-input is used")
        locks = active_locks()
        if locks:
            raise SystemExit("active production lock; DOM extraction aborted: " + ", ".join(locks))
        lock_path, lock_token = acquire_device_lock(run_id)
        try:
            front = run_adb(args.adb_path, args.device_id, "shell", "dumpsys", "activity", "activities", timeout=30)
            if front.returncode != 0 or PACKAGE_NAME not in front.stdout:
                raise RuntimeError("Qieman is not the foreground app; extraction stopped without interaction")
            if args.safe_tab:
                x, y = SAFE_TAB_COORDINATES[args.safe_tab]
                tapped = run_adb(args.adb_path, args.device_id, "shell", "input", "tap", str(x), str(y), timeout=20)
                if tapped.returncode != 0:
                    raise RuntimeError(tapped.stderr or tapped.stdout or "safe tab tap failed")
                time.sleep(max(0.5, args.wait_sec))
            xml_bytes = dump_window_xml(args.adb_path, args.device_id)
        finally:
            release_device_lock(lock_path, lock_token)
        nodes = extract_sanitized_nodes(xml_bytes)
        raw_xml_saved = False
        input_mode = "authenticated_device_dom"
    texts = ordered_texts(nodes)
    parsed = parse_strategy_data(texts, args.strategy_id, args.strategy_name, captured_at)
    normalized_dir = run_dir / "normalized"
    write_json(run_dir / "sanitized_text_nodes.json", {"nodes": nodes, "raw_xml_saved": raw_xml_saved})
    for entity in (
        "strategy_master",
        "strategy_summary_metrics",
        "strategy_performance_daily",
        "strategy_performance_interval",
        "strategy_fund_snapshot",
        "strategy_rebalance_event",
        "strategy_rebalance_fund_delta",
        "strategy_asset_allocation_sample",
    ):
        write_jsonl(normalized_dir / f"{entity}.jsonl", parsed[entity])
    write_json(run_dir / "coverage_assessment.json", parsed["coverage_assessment"])
    summary = {
        "state": "authenticated_strategy_dom_extracted",
        "run_id": run_id,
        "strategy_id": args.strategy_id,
        "strategy_name": args.strategy_name,
        "safe_tab": args.safe_tab,
        "input_mode": input_mode,
        "raw_xml_saved": raw_xml_saved,
        "sanitized_text_node_count": len(nodes),
        "counts": {entity: len(parsed[entity]) for entity in parsed if isinstance(parsed[entity], list)},
        "quality_gate": parsed["coverage_assessment"]["quality_gate"],
        "run_dir": str(run_dir),
    }
    write_json(run_dir / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
