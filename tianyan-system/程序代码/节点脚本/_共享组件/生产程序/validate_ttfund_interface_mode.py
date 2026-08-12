from __future__ import annotations

import argparse
import json
import sys
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from time import sleep
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
sys.path.insert(0, str(PROJECT_ROOT / "节点脚本" / "_共享组件" / "python_src"))

from advisor_monitor.collectors.ttfund_loggedin import QUOTE_API_URL, USER_AGENT  # noqa: E402


RETRIEVAL_SEARCH_URL = "https://ibgmarket.tiantianfunds.com/combine/retrieval/search"
DEFAULT_RETRIEVAL_SEARCH_PAYLOAD = {
    "strategy": "ACTIVITY.2025.ZZDS.PD2",
    "fidlds": (
        "FCODE,FUNDTYPE,ISHUOQI,SHORTNAME,FSRQ,DWJZ,RZDF,SYL_Z,SYL_Y,SYL_3Y,"
        "SYL_6Y,SYL_1N,SYL_2N,SYL_3N,SOURCERATE,RATE,ISBUY,ISSALES,TJDLIST"
    ),
    "pageIndex": "1",
    "pageSize": "10",
    "filter": "ISSALES:1",
    "sort": "",
}


@dataclass(frozen=True)
class SampleStrategy:
    advisor_name: str
    strategy_id: str
    strategy_name: str | None
    strategy_type: str | None
    minimum_amount: float | None
    launch_date: str | None
    source_snapshot_id: str | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate TTFund interface-mode effectiveness with one sample strategy per advisor."
    )
    parser.add_argument(
        "--strategy-master-path",
        type=Path,
        default=None,
        help="Optional strategy_master jsonl path. Defaults to latest normalized file.",
    )
    parser.add_argument(
        "--sample-limit",
        type=int,
        default=None,
        help="Optional cap after one-per-advisor sampling.",
    )
    parser.add_argument(
        "--quote-batch-size",
        type=int,
        default=20,
        help="Batch size for getTGQuoteByFavor validation requests.",
    )
    parser.add_argument(
        "--as-of-date",
        type=str,
        default=datetime.now().strftime("%Y-%m-%d"),
        help="Date string appended into tgCodeWithDateStr. Quote API still returns latest trade snapshot.",
    )
    parser.add_argument(
        "--skip-retrieval-search-probe",
        action="store_true",
        help="Skip /combine/retrieval/search probe.",
    )
    return parser.parse_args()


def find_latest_jsonl(directory: Path) -> Path:
    candidates = sorted(directory.glob("*/*.jsonl"))
    if not candidates:
        raise FileNotFoundError(f"No jsonl files found under {directory}")
    return candidates[-1]


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def choose_one_per_advisor(rows: list[dict[str, Any]], sample_limit: int | None) -> list[SampleStrategy]:
    rows_by_advisor: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        advisor_name = (row.get("advisor_name") or "").strip() or "UNKNOWN"
        strategy_id = str(row.get("source_strategy_id") or "").strip()
        if not strategy_id:
            continue
        rows_by_advisor.setdefault(advisor_name, []).append(row)

    selected: OrderedDict[str, SampleStrategy] = OrderedDict()
    for advisor_name in sorted(rows_by_advisor):
        candidate = sorted(
            rows_by_advisor[advisor_name],
            key=lambda row: (
                str(row.get("launch_date") or "9999-99-99"),
                str(row.get("source_strategy_id") or ""),
            ),
        )[0]
        selected[advisor_name] = SampleStrategy(
            advisor_name=advisor_name,
            strategy_id=str(candidate.get("source_strategy_id") or "").strip(),
            strategy_name=candidate.get("strategy_name"),
            strategy_type=candidate.get("strategy_type"),
            minimum_amount=candidate.get("minimum_amount"),
            launch_date=candidate.get("launch_date"),
            source_snapshot_id=candidate.get("source_snapshot_id"),
        )
        if sample_limit and len(selected) >= sample_limit:
            break
    return list(selected.values())


def resolve_entity_jsonl(
    entity: str,
    *,
    preferred_day: str | None = None,
    preferred_filename: str | None = None,
) -> Path | None:
    entity_dir = PROJECT_ROOT / "data" / "normalized" / "ttfund" / entity
    if not entity_dir.exists():
        return None
    if preferred_day and preferred_filename:
        candidate = entity_dir / preferred_day / preferred_filename
        if candidate.exists():
            return candidate
    return find_latest_jsonl(entity_dir)


def load_strategy_set_by_entity(
    entity: str,
    *,
    preferred_day: str | None = None,
    preferred_filename: str | None = None,
) -> tuple[set[str], Path | None]:
    resolved_path = resolve_entity_jsonl(
        entity,
        preferred_day=preferred_day,
        preferred_filename=preferred_filename,
    )
    if resolved_path is None:
        return set(), None
    rows = load_jsonl(resolved_path)
    return {
        str(row.get("source_strategy_id") or "").strip()
        for row in rows
        if str(row.get("source_strategy_id") or "").strip()
    }, resolved_path


def post_form(url: str, payload: dict[str, Any] | str) -> dict[str, Any]:
    if isinstance(payload, dict):
        raw = urlencode(payload).encode("utf-8")
    else:
        raw = payload.encode("utf-8")
    request = Request(
        url,
        data=raw,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": USER_AGENT,
        },
    )
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            with urlopen(request, timeout=45) as response:
                return json.loads(response.read().decode("utf-8", errors="replace"))
        except (HTTPError, URLError, OSError, json.JSONDecodeError) as error:
            last_error = error
            if attempt == 3:
                break
            sleep(1.0 * attempt)
    raise RuntimeError(f"request failed after retries: {url}") from last_error


def validate_quote_interface(
    samples: list[SampleStrategy],
    *,
    as_of_date: str,
    quote_batch_size: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    quote_rows: dict[str, dict[str, Any]] = {}
    for start in range(0, len(samples), quote_batch_size):
        batch = samples[start:start + quote_batch_size]
        payload = "tgCodeWithDateStr=" + ",".join(
            f"{sample.strategy_id}_{as_of_date}" for sample in batch
        )
        response = post_form(QUOTE_API_URL, payload)
        for row in response.get("Data") or []:
            strategy_id = str(row.get("TGCODE") or "").strip()
            if strategy_id:
                quote_rows[strategy_id] = row

    sample_results: list[dict[str, Any]] = []
    field_names = [
        "TGCODE",
        "TGNAME",
        "LOGO_NAME",
        "ESTABDATE",
        "SYL_D",
        "SYL_Z",
        "SYL_Y",
        "SYL_1N",
        "SYL_LN",
        "SYRQ",
        "JZRQ",
        "SALE_DATE",
        "SALE_END_DATE",
    ]
    field_coverage = {field: 0 for field in field_names}

    for sample in samples:
        quote = quote_rows.get(sample.strategy_id)
        for field in field_names:
            value = (quote or {}).get(field)
            if value not in (None, "", "--"):
                field_coverage[field] += 1
        sample_results.append(
            {
                "advisor_name": sample.advisor_name,
                "strategy_id": sample.strategy_id,
                "strategy_name_master": sample.strategy_name,
                "strategy_type_master": sample.strategy_type,
                "launch_date_master": sample.launch_date,
                "detail_cache_present_in_master": bool(
                    sample.source_snapshot_id and "strategy_detail_cache" in sample.source_snapshot_id
                ),
                "quote_success": quote is not None,
                "quote_strategy_name": (quote or {}).get("TGNAME"),
                "quote_logo_name": (quote or {}).get("LOGO_NAME"),
                "quote_estabdate": (quote or {}).get("ESTABDATE"),
                "quote_trade_date": (quote or {}).get("SYRQ"),
                "quote_nav_date": (quote or {}).get("JZRQ"),
                "quote_daily_return": (quote or {}).get("SYL_D"),
                "quote_week_return": (quote or {}).get("SYL_Z"),
                "quote_month_return": (quote or {}).get("SYL_Y"),
                "quote_1y_return": (quote or {}).get("SYL_1N"),
                "quote_since_inception_return": (quote or {}).get("SYL_LN"),
                "quote_sale_date": (quote or {}).get("SALE_DATE"),
                "quote_sale_end_date": (quote or {}).get("SALE_END_DATE"),
                "interface_mode_depth": "quote_only",
            }
        )

    summary = {
        "sample_total": len(samples),
        "quote_success_total": sum(1 for row in sample_results if row["quote_success"]),
        "quote_missing_total": sum(1 for row in sample_results if not row["quote_success"]),
        "quote_field_coverage": field_coverage,
        "quote_source_url": QUOTE_API_URL,
        "quote_as_of_date_param": as_of_date,
    }
    return sample_results, summary


def probe_retrieval_search() -> dict[str, Any]:
    response = post_form(RETRIEVAL_SEARCH_URL, DEFAULT_RETRIEVAL_SEARCH_PAYLOAD)
    rows = response.get("data") or []
    first_row = rows[0] if rows else {}
    keys = sorted(first_row.keys()) if isinstance(first_row, dict) else []
    row_type = "unknown"
    if isinstance(first_row, dict) and "TGCODE" in first_row:
        row_type = "adviser_strategy"
    elif isinstance(first_row, dict) and "FCODE" in first_row:
        row_type = "fund_product"
    return {
        "source_url": RETRIEVAL_SEARCH_URL,
        "request_payload": DEFAULT_RETRIEVAL_SEARCH_PAYLOAD,
        "success": bool(response.get("success")),
        "response_total": response.get("total"),
        "response_page_size": response.get("pageSize"),
        "response_updatetime": response.get("updatetime"),
        "first_row_type": row_type,
        "first_row_keys": keys,
        "first_row_preview": first_row,
        "is_direct_adviser_strategy_endpoint": row_type == "adviser_strategy",
    }


def build_interface_assessment(
    quote_summary: dict[str, Any],
    retrieval_probe: dict[str, Any] | None,
    detail_set: set[str],
    holding_set: set[str],
    rebalance_set: set[str],
    history_rebalance_set: set[str],
    samples: list[SampleStrategy],
) -> dict[str, Any]:
    sample_ids = {sample.strategy_id for sample in samples}
    return {
        "quote_interface_effective": quote_summary["quote_success_total"] == quote_summary["sample_total"],
        "quote_interface_scope": "latest_quote_snapshot",
        "deep_direct_interface_recovered": False,
        "deep_direct_interface_scope": [],
        "retrieval_search_is_adviser_endpoint": (
            retrieval_probe.get("is_direct_adviser_strategy_endpoint") if retrieval_probe else None
        ),
        "sample_cache_baseline": {
            "detail_present_total": len(sample_ids & detail_set),
            "holding_present_total": len(sample_ids & holding_set),
            "rebalance_present_total": len(sample_ids & rebalance_set),
            "history_rebalance_present_total": len(sample_ids & history_rebalance_set),
        },
    }


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> None:
    args = parse_args()

    strategy_master_path = (
        args.strategy_master_path
        if args.strategy_master_path
        else find_latest_jsonl(PROJECT_ROOT / "data" / "normalized" / "ttfund" / "strategy_master")
    )
    master_rows = load_jsonl(strategy_master_path)
    samples = choose_one_per_advisor(master_rows, args.sample_limit)
    preferred_day = strategy_master_path.parent.name
    preferred_filename = strategy_master_path.name

    detail_set = {
        sample.strategy_id
        for sample in samples
        if sample.source_snapshot_id and "strategy_detail_cache" in sample.source_snapshot_id
    }
    holding_set, holding_rows_path = load_strategy_set_by_entity(
        "strategy_fund_snapshot",
        preferred_day=preferred_day,
        preferred_filename=preferred_filename,
    )
    rebalance_rows_path = resolve_entity_jsonl(
        "strategy_rebalance_event",
        preferred_day=preferred_day,
        preferred_filename=preferred_filename,
    )
    if rebalance_rows_path is None:
        raise FileNotFoundError("No strategy_rebalance_event jsonl found.")
    rebalance_rows = load_jsonl(rebalance_rows_path)
    rebalance_set = {
        str(row.get("source_strategy_id") or "").strip()
        for row in rebalance_rows
        if str(row.get("source_strategy_id") or "").strip()
    }
    history_rebalance_set = {
        str(row.get("source_strategy_id") or "").strip()
        for row in rebalance_rows
        if str(row.get("source_strategy_id") or "").strip() and row.get("payload_type") == "history"
    }

    sample_results, quote_summary = validate_quote_interface(
        samples,
        as_of_date=args.as_of_date,
        quote_batch_size=max(1, args.quote_batch_size),
    )
    retrieval_probe = None
    retrieval_probe_error = None
    if not args.skip_retrieval_search_probe:
        try:
            retrieval_probe = probe_retrieval_search()
        except Exception as error:  # pragma: no cover - defensive path for flaky network
            retrieval_probe_error = str(error)
    interface_assessment = build_interface_assessment(
        quote_summary,
        retrieval_probe,
        detail_set,
        holding_set,
        rebalance_set,
        history_rebalance_set,
        samples,
    )

    run_at = datetime.now()
    day = run_at.strftime("%Y-%m-%d")
    run_id = run_at.strftime("%Y%m%dT%H%M%S")
    output_dir = PROJECT_ROOT / "data" / "api_probe" / "ttfund_interface_mode" / day / run_id
    summary = {
        "captured_at": run_at.isoformat(timespec="seconds"),
        "strategy_master_path": str(strategy_master_path),
        "sample_selection_mode": "one_per_advisor",
        "sample_selection_order": "advisor_name + launch_date + strategy_id",
        "advisor_total": len(samples),
        "resolved_entity_paths": {
            "strategy_master": str(strategy_master_path),
            "strategy_fund_snapshot": str(holding_rows_path) if holding_rows_path else None,
            "strategy_rebalance_event": str(rebalance_rows_path),
        },
        "quote_summary": quote_summary,
        "retrieval_search_probe": retrieval_probe,
        "retrieval_search_probe_error": retrieval_probe_error,
        "interface_assessment": interface_assessment,
    }

    write_json(output_dir / "summary.json", summary)
    write_jsonl(output_dir / "samples.jsonl", sample_results)

    print("validation complete")
    print(f"strategy_master_path={strategy_master_path}")
    print(f"sample_total={len(samples)}")
    print(
        "quote_success="
        f"{quote_summary['quote_success_total']}/{quote_summary['sample_total']}"
    )
    if retrieval_probe is not None:
        print(
            "retrieval_search="
            f"type:{retrieval_probe['first_row_type']} "
            f"direct_adviser:{retrieval_probe['is_direct_adviser_strategy_endpoint']}"
        )
    print(f"output_dir={output_dir}")


if __name__ == "__main__":
    main()
