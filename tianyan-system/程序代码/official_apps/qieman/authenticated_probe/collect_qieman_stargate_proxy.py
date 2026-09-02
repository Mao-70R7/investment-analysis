from __future__ import annotations

import argparse
import hashlib
import json
import re
import socket
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

from probe_qieman_device import active_locks, now_local, write_json


PROBE_ROOT = Path(__file__).resolve().parent
DEFAULT_KEYWORDS = (
    "组",
    "计划",
    "策略",
    "投",
    "稳",
    "盈",
    "定投",
    "幸福",
    "启明",
    "理财",
    "全",
    "优选",
    "基金",
    "小组",
    "新锐",
)

DETAIL_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "source_strategy_id": ("策略代码", "组合代码", "strategyCode", "poCode", "strategy_code"),
    "strategy_name": ("策略名称", "组合名称", "strategyName", "poName", "strategy_name"),
    "advisor_name": (
        "策略管理人",
        "投顾管理人",
        "投顾机构",
        "管理人名称",
        "策略提供方",
        "advisorName",
        "managerName",
        "institutionName",
    ),
    "strategy_type": ("策略类型", "组合类型", "服务模式", "strategyType", "portfolioType"),
    "risk_level": ("策略风险等级", "风险等级", "riskLevel", "riskGrade"),
    "launch_date": ("策略成立时间", "成立日期", "成立时间", "establishedOn", "launchDate"),
    "suggested_holding_period": (
        "建议持有时长",
        "建议持有期",
        "建议持有时间",
        "建议持有",
        "suggestedHoldingPeriod",
        "holdingPeriod",
    ),
    "minimum_amount": ("起投金额", "最低起投金额", "最小申购金额", "minimumAmount", "minInvestment"),
    "advisory_fee_rate": (
        "投顾费率",
        "投顾服务费率",
        "投顾服务费",
        "服务费率",
        "advisoryFeeRate",
        "serviceFeeRate",
    ),
    "source_url": ("策略详情链接", "详情页链接", "策略链接", "sourceUrl", "detailUrl"),
    "strategy_description": ("策略简介", "策略描述", "投资特点", "strategyDescription", "description"),
}


def request_proxy(
    port: int,
    operation_id: str,
    *,
    query: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
    timeout: int = 90,
    max_attempts: int = 4,
    retry_delay_seconds: float = 1.0,
) -> Any:
    message: dict[str, Any] = {"action": "proxy", "operationId": operation_id}
    if query is not None:
        message["query"] = query
    if body is not None:
        message["body"] = body
    encoded = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    attempts = max(1, max_attempts)
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            chunks: list[bytes] = []
            with socket.create_connection(("127.0.0.1", port), timeout=timeout) as client:
                client.settimeout(timeout)
                client.sendall(encoded)
                client.shutdown(socket.SHUT_WR)
                while True:
                    chunk = client.recv(1024 * 1024)
                    if not chunk:
                        break
                    chunks.append(chunk)
            if not chunks:
                raise EOFError("proxy closed without a response")
            response = json.loads(b"".join(chunks).decode("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError, EOFError) as error:
            last_error = error
            if attempt >= attempts:
                break
            delay = max(0.0, retry_delay_seconds) * (2 ** (attempt - 1))
            print(
                f"[QIEMAN_PROXY_RETRY] operation={operation_id} attempt={attempt}/{attempts} "
                f"error={type(error).__name__}: {error}; retry_in={delay:.1f}s",
                flush=True,
            )
            if delay:
                time.sleep(delay)
            continue
        if not isinstance(response, dict):
            raise RuntimeError(f"proxy {operation_id} returned a non-object response")
        if response.get("state") != "ok":
            raise RuntimeError(f"proxy {operation_id} failed: {response.get('message') or response.get('state')}")
        return response.get("payload")
    raise RuntimeError(
        f"proxy {operation_id} transport failed after {attempts} attempts: "
        f"{type(last_error).__name__ if last_error else 'unknown'}: {last_error}"
    ) from last_error


def read_cached_payload(resume_run_dir: Path | None, relative_path: Path) -> dict[str, Any] | None:
    if resume_run_dir is None:
        return None
    source = resume_run_dir / relative_path
    if not source.is_file():
        return None
    try:
        payload = json.loads(source.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def payload_with_resume(
    run_dir: Path,
    resume_run_dir: Path | None,
    relative_path: Path,
    fetch: Callable[[], Any],
) -> tuple[Any, bool]:
    cached = read_cached_payload(resume_run_dir, relative_path)
    if cached is not None:
        write_json(run_dir / relative_path, cached)
        return cached, True
    payload = fetch()
    write_json(run_dir / relative_path, payload)
    return payload, False


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def safe_slug(value: str) -> str:
    ascii_slug = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_")
    if ascii_slug:
        return ascii_slug
    return "-".join(f"u{ord(char):04x}" for char in value)


def parse_percent(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text.removesuffix("%")) / 100 if text.endswith("%") else float(text)
    except ValueError:
        return None


def stable_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def first_detail_value(row: dict[str, Any], field: str) -> tuple[Any, str | None]:
    for source_key in DETAIL_FIELD_ALIASES[field]:
        value = row.get(source_key)
        if value is not None and str(value).strip() != "":
            return value, source_key
    return None, None


def parse_amount(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    match = re.search(r"-?\d+(?:\.\d+)?", str(value).replace(",", ""))
    return float(match.group()) if match else None


def normalize_detail_fields(row: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    lineage: dict[str, str] = {}
    for field in DETAIL_FIELD_ALIASES:
        value, source_key = first_detail_value(row, field)
        if source_key is None:
            continue
        if field == "minimum_amount":
            value = parse_amount(value)
            if value is None:
                continue
        elif field == "source_url":
            value = str(value).strip()
            if not re.match(r"^https?://", value, flags=re.IGNORECASE):
                continue
        elif not isinstance(value, (dict, list)):
            value = str(value).strip()
        normalized[field] = value
        lineage[field] = source_key
    normalized["lineage"] = lineage
    return normalized


def merge_detail_into_master(master: dict[str, Any], detail: dict[str, Any]) -> dict[str, Any]:
    output = dict(master)
    extra = dict(output.get("extra") or {}) if isinstance(output.get("extra"), dict) else {}
    detail_lineage = detail.get("lineage") if isinstance(detail.get("lineage"), dict) else {}
    merged_lineage = dict(extra.get("stargate_detail_lineage") or {})
    for field in (
        "advisor_name",
        "strategy_type",
        "risk_level",
        "launch_date",
        "suggested_holding_period",
        "minimum_amount",
        "advisory_fee_rate",
        "source_url",
        "strategy_description",
    ):
        if output.get(field) in (None, "") and detail.get(field) not in (None, ""):
            output[field] = detail[field]
            merged_lineage[field] = f"official_stargate_detail:{detail_lineage.get(field)}"
    if merged_lineage:
        extra["stargate_detail_lineage"] = merged_lineage
    output["extra"] = extra
    return output


def is_test_strategy_name(value: Any) -> bool:
    return bool(re.search(r"测试|TEST|staging|内测", str(value or ""), flags=re.IGNORECASE))


def normalize_master(row: dict[str, Any], captured_at: str, run_id: str, keywords: list[str]) -> dict[str, Any]:
    is_test = is_test_strategy_name(row.get("策略名称"))
    return {
        "channel_id": "qieman",
        "source_strategy_id": row.get("策略代码"),
        "strategy_name": row.get("策略名称"),
        "advisor_name": None,
        "strategy_type": None,
        "risk_level": row.get("策略风险等级"),
        "launch_date": row.get("策略成立时间"),
        "suggested_holding_period": None,
        "minimum_amount": None,
        "advisory_fee_rate": None,
        "benchmark": None,
        "tags": [],
        "strategy_description": row.get("策略简介") or row.get("策略描述"),
        "status": "test_or_internal" if is_test else "stargate_keyword_discovered",
        "source_url": None,
        "first_seen_at": captured_at,
        "last_seen_at": captured_at,
        "run_id": run_id,
        "confidence_level": "official_stargate_structured_keyword_search",
        "extra": {
            "matched_keywords": sorted(keywords),
            "strategy_detail_text": row.get("策略描述"),
            "keyword_union_is_not_complete_catalog": True,
            "is_test_or_internal": is_test,
        },
    }


def normalize_composition(
    strategy_code: str,
    payload: dict[str, Any],
    captured_at: str,
    run_id: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    category_rows: list[tuple[str, dict[str, Any]]] = []
    updated_dates: set[str] = set()
    for category_name, category in payload.items():
        if not isinstance(category, dict):
            continue
        holdings = category.get("持有成分") if isinstance(category.get("持有成分"), list) else []
        for holding in holdings:
            if isinstance(holding, dict):
                category_rows.append((str(category_name), holding))
                updated = str(holding.get("最新更新时间") or "").strip()
                match = re.match(r"(\d{4}-\d{2}-\d{2})", updated)
                if match:
                    updated_dates.add(match.group(1))
    position_date = next(iter(updated_dates)) if len(updated_dates) == 1 else None
    position_date_in_future = bool(position_date and position_date > captured_at[:10])
    snapshot_seed = {
        "strategy_code": strategy_code,
        "captured_at": captured_at,
        "position_date": position_date,
        "rows": category_rows,
    }
    snapshot_id = f"qieman-{strategy_code}-{position_date or captured_at[:10]}-{stable_hash(snapshot_seed)[:12]}"
    normalized: list[dict[str, Any]] = []
    for category_name, holding in category_rows:
        fund_code = str(holding.get("基金代码") or "").strip() or None
        weight = parse_percent(holding.get("持仓占比"))
        raw_hash = stable_hash({"strategy_code": strategy_code, "holding": holding})
        normalized.append(
            {
                "snapshot_id": snapshot_id,
                "channel_id": "qieman",
                "source_strategy_id": strategy_code,
                "position_date": position_date,
                "disclosure_date": captured_at[:10],
                "fund_code": fund_code,
                "fund_name": holding.get("基金名称"),
                "fund_asset_type": holding.get("基金类型"),
                "fund_group_name": category_name,
                "fund_weight": weight,
                "fund_nav": holding.get("最新净值"),
                "fund_nav_date": holding.get("最新净值日期"),
                "is_precise_weight": weight is not None,
                "is_login_required": True,
                "source_url": None,
                "raw_record_hash": raw_hash,
                "confidence_level": (
                    "official_stargate_exact_current_composition"
                    if position_date and not position_date_in_future and fund_code and weight is not None
                    else "official_stargate_composition_partial_date"
                ),
                "access_level": "official_api_key",
                "run_id": run_id,
                "extra": {
                    "holding_updated_at": holding.get("最新更新时间"),
                    "daily_return": parse_percent(holding.get("日涨跌幅")),
                    "fund_full_name": holding.get("基金全称"),
                    "candidate_funds": holding.get("替代基金列表"),
                },
            }
        )
    weight_complete = bool(normalized) and all(row["fund_weight"] is not None for row in normalized)
    weight_sum = sum(row["fund_weight"] or 0 for row in normalized) if weight_complete else None
    assessment = {
        "source_strategy_id": strategy_code,
        "holding_rows": len(normalized),
        "position_date": position_date,
        "distinct_holding_update_dates": sorted(updated_dates),
        "weight_complete": weight_complete,
        "weight_sum": round(weight_sum, 8) if weight_sum is not None else None,
        "position_date_in_future": position_date_in_future,
        "strict_complete": bool(
            position_date
            and not position_date_in_future
            and weight_complete
            and abs((weight_sum or 0) - 1) <= 0.001
        ),
    }
    return normalized, assessment


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect Qieman keyword catalog and current compositions through an in-memory API-key proxy.")
    parser.add_argument("--proxy-port", type=int, default=43912)
    parser.add_argument("--keywords", default=",".join(DEFAULT_KEYWORDS))
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--max-pages", type=int, default=30)
    parser.add_argument("--composition-batch-size", type=int, default=40)
    parser.add_argument("--output-root", type=Path, default=PROBE_ROOT / "runs")
    parser.add_argument("--run-id")
    parser.add_argument("--resume-run-dir", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    locks = active_locks()
    if locks:
        raise SystemExit("active production lock; Qieman StarGate collection aborted: " + ", ".join(locks))
    started = now_local()
    captured_at = started.isoformat(timespec="seconds")
    run_id = str(args.run_id or "").strip() or started.strftime("%Y%m%dT%H%M%S%z") + "-stargate-keyword"
    run_dir = args.output_root.resolve() / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    resume_run_dir = args.resume_run_dir.resolve() if args.resume_run_dir else None
    if resume_run_dir is not None and not resume_run_dir.is_dir():
        raise SystemExit(f"Qieman resume run directory does not exist: {resume_run_dir}")
    if resume_run_dir == run_dir:
        raise SystemExit("Qieman resume run directory must differ from the current run directory")
    resume_cache_hits = {"search": 0, "details": 0, "composition": 0}
    keywords = list(dict.fromkeys(value.strip() for value in args.keywords.split(",") if value.strip()))
    page_size = max(1, min(100, args.page_size))
    rows_by_code: dict[str, dict[str, Any]] = {}
    matched_keywords: dict[str, set[str]] = {}
    query_summaries: list[dict[str, Any]] = []

    for keyword in keywords:
        query_unique: set[str] = set()
        page_summaries: list[dict[str, Any]] = []
        for page in range(1, args.max_pages + 1):
            relative_path = Path("raw") / "search" / safe_slug(keyword) / f"page_{page:04d}.json"
            payload, reused = payload_with_resume(
                run_dir,
                resume_run_dir,
                relative_path,
                lambda: request_proxy(
                    args.proxy_port,
                    "StrategySearchByKeyword",
                    query={"keyword": keyword, "pageNum": page, "pageSize": page_size},
                ),
            )
            resume_cache_hits["search"] += int(reused)
            rows = payload.get("rows") if isinstance(payload, dict) and isinstance(payload.get("rows"), list) else []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                code = str(row.get("策略代码") or "").strip()
                if not code:
                    continue
                rows_by_code[code] = row
                matched_keywords.setdefault(code, set()).add(keyword)
                query_unique.add(code)
            has_more = bool(payload.get("hasMoreRows")) if isinstance(payload, dict) else False
            page_summaries.append({"page": page, "rows": len(rows), "unique_after": len(query_unique), "has_more": has_more})
            if not has_more or not rows:
                break
        query_summaries.append({"keyword": keyword, "unique_count": len(query_unique), "pages": page_summaries})

    masters = [
        normalize_master(rows_by_code[code], captured_at, run_id, sorted(matched_keywords.get(code, set())))
        for code in sorted(rows_by_code)
    ]
    detail_rows_by_code: dict[str, dict[str, Any]] = {}
    detail_batch_summaries: list[dict[str, Any]] = []
    detail_collection_error: str | None = None
    detail_batch_size = 100
    codes = sorted(rows_by_code)
    for offset in range(0, len(codes), detail_batch_size):
        batch = codes[offset : offset + detail_batch_size]
        batch_number = offset // detail_batch_size + 1
        batch_seen: set[str] = set()
        page_summaries: list[dict[str, Any]] = []
        terminal_page_seen = False
        for page in range(1, args.max_pages + 1):
            try:
                relative_path = Path("raw") / "details" / f"batch_{batch_number:04d}_page_{page:04d}.json"
                payload, reused = payload_with_resume(
                    run_dir,
                    resume_run_dir,
                    relative_path,
                    lambda: request_proxy(
                        args.proxy_port,
                        "GetStrategyDetails",
                        body={"strategy_codes": ",".join(batch), "page_num": page, "page_size": 100},
                        timeout=180,
                    ),
                )
                resume_cache_hits["details"] += int(reused)
            except RuntimeError as error:
                detail_collection_error = str(error)
                page_summaries.append({"page": page, "rows": 0, "accepted": 0, "status": "unavailable"})
                break
            rows = payload.get("rows") if isinstance(payload, dict) and isinstance(payload.get("rows"), list) else []
            accepted = 0
            for raw_detail in rows:
                if not isinstance(raw_detail, dict):
                    continue
                detail = normalize_detail_fields(raw_detail)
                code = str(detail.get("source_strategy_id") or "").strip()
                if code not in batch:
                    continue
                detail_rows_by_code[code] = detail
                batch_seen.add(code)
                accepted += 1
            has_more = bool(payload.get("hasMoreRows")) if isinstance(payload, dict) else False
            page_summaries.append(
                {"page": page, "rows": len(rows), "accepted": accepted, "has_more": has_more, "status": "success"}
            )
            if not has_more:
                terminal_page_seen = True
                break
            if not rows:
                detail_collection_error = (
                    f"GetStrategyDetails batch {batch_number} page {page} declared hasMoreRows with zero rows"
                )
                break
        detail_batch_summaries.append(
            {
                "batch": batch_number,
                "requested": len(batch),
                "accepted_unique": len(batch_seen),
                "missing_codes": sorted(set(batch) - batch_seen),
                "terminal_page_seen": terminal_page_seen,
                "pages": page_summaries,
                "status": "complete" if terminal_page_seen and len(batch_seen) == len(batch) else "partial_or_unavailable",
            }
        )
        if not terminal_page_seen and detail_collection_error is None:
            detail_collection_error = (
                f"GetStrategyDetails batch {batch_number} exceeded max_pages={args.max_pages} before terminal page"
            )
        if detail_collection_error:
            break
    masters = [
        merge_detail_into_master(master, detail_rows_by_code.get(str(master.get("source_strategy_id")), {}))
        for master in masters
    ]
    write_jsonl(run_dir / "normalized" / "strategy_master.jsonl", masters)

    holding_rows: list[dict[str, Any]] = []
    composition_assessments: list[dict[str, Any]] = []
    batch_size = max(1, min(100, args.composition_batch_size))
    for offset in range(0, len(codes), batch_size):
        batch = codes[offset : offset + batch_size]
        batch_number = offset // batch_size + 1
        relative_path = Path("raw") / "composition" / f"batch_{batch_number:04d}.json"
        payload, reused = payload_with_resume(
            run_dir,
            resume_run_dir,
            relative_path,
            lambda: request_proxy(
                args.proxy_port,
                "BatchGetStrategiesComposition",
                body={"strategyCodes": batch},
                timeout=180,
            ),
        )
        resume_cache_hits["composition"] += int(reused)
        if not isinstance(payload, dict):
            continue
        for code in batch:
            strategy_payload = payload.get(code)
            if not isinstance(strategy_payload, dict):
                composition_assessments.append(
                    {"source_strategy_id": code, "holding_rows": 0, "strict_complete": False, "status": "missing_response"}
                )
                continue
            normalized, assessment = normalize_composition(code, strategy_payload, captured_at, run_id)
            holding_rows.extend(normalized)
            composition_assessments.append(assessment)
    write_jsonl(run_dir / "normalized" / "strategy_fund_snapshot.jsonl", holding_rows)

    duplicate_codes = len(masters) - len({row["source_strategy_id"] for row in masters})
    duplicate_holdings = len(holding_rows) - len(
        {(row["snapshot_id"], row["fund_code"]) for row in holding_rows}
    )
    strict_complete = sum(1 for row in composition_assessments if row.get("strict_complete"))
    with_holdings = sum(1 for row in composition_assessments if row.get("holding_rows", 0) > 0)
    production_codes = {
        str(row["source_strategy_id"])
        for row in masters
        if row.get("status") != "test_or_internal"
    }
    assessment_by_code = {str(row.get("source_strategy_id")): row for row in composition_assessments}
    summary = {
        "state": "stargate_keyword_union_collected",
        "run_id": run_id,
        "captured_at": captured_at,
        "keyword_count": len(keywords),
        "strategy_count": len(masters),
        "production_strategy_count": len(production_codes),
        "test_or_internal_strategy_count": len(masters) - len(production_codes),
        "strategy_detail_rows": len(detail_rows_by_code),
        "strategy_detail_status": "complete" if len(detail_rows_by_code) == len(codes) else "partial_or_unavailable",
        "strategy_detail_error": detail_collection_error,
        "strategy_detail_batch_summaries": detail_batch_summaries,
        "strategy_detail_field_coverage": {
            field: sum(master.get(field) not in (None, "") for master in masters)
            for field in (
                "advisor_name",
                "strategy_type",
                "risk_level",
                "launch_date",
                "suggested_holding_period",
                "minimum_amount",
                "advisory_fee_rate",
                "source_url",
            )
        },
        "complete_catalog": False,
        "catalog_boundary": "authenticated keyword union; API key does not expose SearchPortfolioStrategies total",
        "strategy_with_current_holdings": with_holdings,
        "strict_complete_current_holdings": strict_complete,
        "production_strategy_with_current_holdings": sum(
            1 for code in production_codes if assessment_by_code.get(code, {}).get("holding_rows", 0) > 0
        ),
        "production_strict_complete_current_holdings": sum(
            1 for code in production_codes if assessment_by_code.get(code, {}).get("strict_complete")
        ),
        "holding_rows": len(holding_rows),
        "precise_weight_rows": sum(1 for row in holding_rows if row.get("is_precise_weight")),
        "duplicate_strategy_business_keys": duplicate_codes,
        "duplicate_holding_business_keys": duplicate_holdings,
        "query_summaries": query_summaries,
        "composition_assessments": composition_assessments,
        "resume_run_dir": str(resume_run_dir) if resume_run_dir else None,
        "resume_cache_hits": resume_cache_hits,
        "run_dir": str(run_dir),
    }
    write_json(run_dir / "summary.json", summary)
    print(json.dumps({key: value for key, value in summary.items() if key not in {"query_summaries", "composition_assessments"}}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
