from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
CHANNEL_TTFUND = "ttfund"
CHANNEL_GFFUNDS = "gffunds"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize daily incremental collection gaps for TTFund and GFFunds.")
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--ttfund-summary", type=Path)
    parser.add_argument("--run-started-at", help="Optional ISO timestamp used to prefer collection summaries from this run.")
    parser.add_argument("--run-finished-at", help="Optional ISO timestamp used to exclude summaries created after this run.")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--sample-limit", type=int, default=80)
    return parser.parse_args()


def read_json(path: Path | None) -> Any:
    if not path or not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None


def read_jsonl(path: Path | None) -> list[dict[str, Any]]:
    if not path or not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                rows.append(item)
    return rows


def parse_dt(text: str | None) -> datetime | None:
    if not text:
        return None
    candidate = str(text).strip().replace("Z", "+00:00")
    for value in (candidate, candidate[:19]):
        try:
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is not None:
                return parsed.astimezone().replace(tzinfo=None)
            return parsed
        except ValueError:
            continue
    return None


def latest_file(
    root: Path,
    pattern: str = "*.json",
    since: datetime | None = None,
    until: datetime | None = None,
) -> Path | None:
    if not root.exists():
        return None
    files = [path for path in root.rglob(pattern) if path.is_file()]
    if since is not None:
        files = [path for path in files if datetime.fromtimestamp(path.stat().st_mtime) >= since]
    if until is not None:
        files = [path for path in files if datetime.fromtimestamp(path.stat().st_mtime) <= until]
    if not files:
        return None
    return max(files, key=lambda path: path.stat().st_mtime)


def latest_json_matching(
    root: Path,
    predicate: Any,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
) -> tuple[Path | None, dict[str, Any] | None]:
    if not root.exists():
        return None, None
    candidates = sorted(
        (path for path in root.rglob("*.json") if path.is_file()),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    for path in candidates:
        if since is not None and datetime.fromtimestamp(path.stat().st_mtime) < since:
            continue
        if until is not None and datetime.fromtimestamp(path.stat().st_mtime) > until:
            continue
        payload = read_json(path)
        if isinstance(payload, dict) and predicate(payload):
            return path, payload
    return None, None


def sample(values: list[str], limit: int) -> list[str]:
    return values[: max(0, limit)]


def read_strategy_file(path: Path | None) -> list[str]:
    if not path or not path.exists():
        return []
    return [line.strip() for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def optional_path(value: Any) -> Path | None:
    text = str(value or "").strip()
    return Path(text) if text else None


def result_ids(path: Path | None, *ok_fields: str) -> set[str]:
    rows = read_json(path)
    if not isinstance(rows, list):
        return set()
    ok: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        strategy_id = str(row.get("strategy_id") or "").strip()
        if strategy_id and any(bool(row.get(field)) for field in ok_fields):
            ok.add(strategy_id)
    return ok


def result_requested_ids(path: Path | None) -> set[str]:
    rows = read_json(path)
    if not isinstance(rows, list):
        return set()
    return {str(row.get("strategy_id") or "").strip() for row in rows if isinstance(row, dict) and row.get("strategy_id")}


def csv_first_available_column(path: Path | None, columns: tuple[str, ...]) -> list[str]:
    if not path or not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            available = set(reader.fieldnames or [])
            selected = next((column for column in columns if column in available), None)
            if selected is None:
                return []
            return [
                str(row.get(selected) or "").strip()
                for row in reader
                if str(row.get(selected) or "").strip()
            ]
    except OSError:
        return []


def query_rows(db_path: Path, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    if not db_path.exists():
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def query_scalar(db_path: Path, sql: str, params: tuple[Any, ...] = ()) -> Any:
    rows = query_rows(db_path, sql, params)
    if not rows:
        return None
    return next(iter(rows[0].values()))


def load_existing_fund_codes(db_path: Path) -> set[str]:
    rows = query_rows(db_path, 'SELECT DISTINCT "基金代码" AS fund_code FROM "基金日度净值"')
    return {str(row.get("fund_code") or "").strip() for row in rows if row.get("fund_code")}


def load_latest_holding_funds(db_path: Path) -> dict[str, str]:
    rows = query_rows(
        db_path,
        '''
        WITH latest_strategy_holding AS (
            SELECT "\u7edf\u4e00\u7b56\u7565ID" AS strategy_id, MAX("\u6301\u4ed3\u65e5\u671f") AS holding_date
            FROM "\u7b56\u7565\u5f53\u524d\u6301\u4ed3"
            WHERE "\u6e20\u9053ID" IN (?, ?)
            GROUP BY "\u7edf\u4e00\u7b56\u7565ID"
        )
        SELECT h."\u57fa\u91d1\u4ee3\u7801" AS fund_code, MAX(h."\u6301\u4ed3\u65e5\u671f") AS holding_date
        FROM "\u7b56\u7565\u5f53\u524d\u6301\u4ed3" h
        JOIN latest_strategy_holding latest
          ON latest.strategy_id = h."\u7edf\u4e00\u7b56\u7565ID"
         AND latest.holding_date = h."\u6301\u4ed3\u65e5\u671f"
        WHERE COALESCE(h."\u57fa\u91d1\u6743\u91cd_\u767e\u5206\u6bd4", 0) > 0
        GROUP BY h."\u57fa\u91d1\u4ee3\u7801"
        ''',
        (CHANNEL_TTFUND, CHANNEL_GFFUNDS),
    )
    return {
        str(row.get("fund_code") or "").strip(): str(row.get("holding_date") or "").strip()
        for row in rows
        if row.get("fund_code")
    }


def resolve_official_curve_summary(project_root: Path, summary: dict[str, Any]) -> Path | None:
    configured = optional_path(summary.get("official_curve_summary_path"))
    if configured and configured.exists():
        return configured
    run_id = str(summary.get("collect_run_id") or "").strip()
    root = project_root / "outputs" / "ttfund_official_performance_curve"
    if run_id:
        matches = [path for path in root.rglob("official_curve_summary.json") if path.parent.name == run_id]
        if matches:
            return max(matches, key=lambda path: path.stat().st_mtime_ns)
    return latest_file(root, "official_curve_summary.json")


def summarize_ttfund(project_root: Path, summary_path: Path | None, limit: int) -> dict[str, Any]:
    summary = read_json(summary_path)
    if not isinstance(summary, dict):
        return {"state": "unavailable", "summary_path": str(summary_path) if summary_path else None}
    job_root = optional_path(summary.get("job_root"))
    plan = read_json(optional_path(summary.get("plan_path")))
    selected_current: list[str] = []
    plan_selection: dict[str, Any] = {}
    if isinstance(plan, dict):
        plan_selection = plan.get("selection") or {}
        selected_current = [str(item) for item in (plan_selection.get("selected_current_holding_ids") or []) if item]

    direct_dir = optional_path(summary.get("direct_interface_run_dir"))
    direct_history_dir = optional_path(summary.get("direct_history_run_dir"))
    detail_results = job_root / "01_detail_drive" / "results.json" if job_root else None
    current_results = job_root / "01_current_holding_drive" / "results.json" if job_root else None
    history_results = job_root / "02_history_drive" / "results.json" if job_root else None
    detail_physical_results = job_root / "01_detail_drive_physical_retry" / "results.json" if job_root else None
    current_physical_results = job_root / "01_current_holding_drive_physical_retry" / "results.json" if job_root else None
    history_physical_results = job_root / "02_history_drive_physical_retry" / "results.json" if job_root else None
    direct_results = direct_dir / "results.json" if direct_dir else None
    direct_history_results = direct_history_dir / "results.json" if direct_history_dir else None

    detail_success = (
        result_ids(direct_results, "detail_ok")
        | result_ids(detail_results, "detail_ok")
        | result_ids(current_results, "detail_ok")
        | result_ids(detail_physical_results, "detail_ok")
        | result_ids(current_physical_results, "detail_ok")
    )
    current_success = (
        result_ids(direct_results, "holding_info_ok")
        | result_ids(detail_results, "holding_info_ok")
        | result_ids(current_results, "holding_info_ok")
        | result_ids(detail_physical_results, "holding_info_ok")
        | result_ids(current_physical_results, "holding_info_ok")
    )
    current_failed = sorted(set(selected_current) - current_success)
    detail_failed = sorted(set(selected_current) - detail_success)

    latest_target_path = optional_path(summary.get("latest_rebalance_prefilter_target_path"))
    history_targets = set(read_strategy_file(direct_history_dir / "strategy_ids.txt" if direct_history_dir else None))
    history_targets |= set(read_strategy_file(latest_target_path))
    history_targets |= result_requested_ids(history_results)
    history_targets |= result_requested_ids(history_physical_results)
    history_success = result_ids(
        direct_history_results, "history_adjustment_ok", "history_checked_ok", "history_page_seen"
    ) | result_ids(
        history_results, "history_adjustment_ok", "history_checked_ok", "history_page_seen"
    )
    history_success |= result_ids(
        history_physical_results, "history_adjustment_ok", "history_checked_ok", "history_page_seen"
    )
    history_failed = sorted(history_targets - history_success)

    projection_summary_path = latest_file(project_root / "outputs" / "current_holding_projection_audit", "summary.json")
    projection_summary = read_json(projection_summary_path)
    projection_channel = {}
    if isinstance(projection_summary, dict):
        projection_channel = (projection_summary.get("channel_summary") or {}).get(CHANNEL_TTFUND, {})

    official_curve_summary_path = resolve_official_curve_summary(project_root, summary)
    official_curve_summary = read_json(official_curve_summary_path)
    missing_csv = optional_path((official_curve_summary or {}).get("missing_csv")) if isinstance(official_curve_summary, dict) else None
    official_curve_missing_ids = sorted(
        set(csv_first_available_column(missing_csv, ("渠道策略ID", "source_strategy_id", "strategy_id")))
    )

    return {
        "state": "current_run",
        "summary_path": str(summary_path) if summary_path else None,
        "job_root": str(job_root) if job_root else None,
        "planned_strategy_total": (plan.get("local_baseline") or {}).get("strategy_total") if isinstance(plan, dict) else None,
        "selected_current_holding_total": len(selected_current),
        "raw_current_holding_success_total": len(set(selected_current) & current_success),
        "raw_current_holding_failed_total": len(current_failed),
        "raw_current_holding_failed_strategy_ids": sample(current_failed, limit),
        "detail_page_failed_total": len(detail_failed),
        "detail_page_failed_strategy_ids": sample(detail_failed, limit),
        "fallback_device_id": summary.get("fallback_device_id"),
        "detail_primary_failed_total": summary.get("detail_primary_failed_total"),
        "detail_physical_retry_success_total": summary.get("detail_physical_retry_success_total"),
        "detail_final_failed_total": summary.get("detail_final_failed_total"),
        "current_holding_primary_failed_total": summary.get("current_holding_primary_failed_total"),
        "current_holding_physical_retry_success_total": summary.get("current_holding_physical_retry_success_total"),
        "current_holding_final_failed_total": summary.get("current_holding_final_failed_total"),
        "history_primary_failed_total": summary.get("history_primary_failed_total"),
        "history_physical_retry_success_total": summary.get("history_physical_retry_success_total"),
        "history_final_failed_total": summary.get("history_final_failed_total"),
        "definitively_stopped_skipped_total": plan_selection.get(
            "definitively_stopped_current_holding_skipped_total",
            plan_selection.get("stopped_current_holding_skipped_total"),
        ),
        "stopped_but_refreshable_total": plan_selection.get("stopped_but_refreshable_total"),
        "current_holding_lifecycle_reason_counts": plan_selection.get("current_holding_lifecycle_reason_counts"),
        "projected_current_holding_summary_path": str(projection_summary_path) if projection_summary_path else None,
        "projected_current_holding_total": projection_channel.get("缺当前且已推算补齐策略数"),
        "stale_projected_current_holding_total": projection_channel.get("持仓滞后滚动推算策略数"),
        "rebalance_history_target_total": len(history_targets),
        "rebalance_history_failed_total": len(history_failed),
        "rebalance_history_failed_strategy_ids": sample(history_failed, limit),
        "latest_rebalance_prefilter_state": summary.get("latest_rebalance_prefilter_state"),
        "direct_interface_state": summary.get("direct_interface_state"),
        "direct_history_state": summary.get("direct_history_state"),
        "official_curve_summary_path": str(official_curve_summary_path) if official_curve_summary_path else None,
        "official_curve_strategy_total": (official_curve_summary or {}).get("strategy_total") if isinstance(official_curve_summary, dict) else None,
        "official_curve_success_total": (official_curve_summary or {}).get("curve_strategy_total") if isinstance(official_curve_summary, dict) else None,
        "official_curve_missing_total": (official_curve_summary or {}).get("missing_strategy_total") if isinstance(official_curve_summary, dict) else None,
        "official_curve_missing_strategy_ids": sample(official_curve_missing_ids, limit),
        "official_curve_device_retry_summary_path": (
            (official_curve_summary or {}).get("device_retry_summary_path")
            if isinstance(official_curve_summary, dict)
            else None
        ),
        "official_curve_retry_recovered_total": (
            ((official_curve_summary or {}).get("device_retry") or {}).get("recovered_total")
            if isinstance(official_curve_summary, dict)
            else None
        ),
        "official_curve_final_active_missing_total": (
            (official_curve_summary or {}).get("final_active_missing_total")
            if isinstance(official_curve_summary, dict)
            else None
        ),
        "latest_disclosure_date": summary.get("target_trade_date") or summary.get("remote_max_trade_date"),
    }


def summarize_gffunds(
    project_root: Path,
    db_path: Path,
    since: datetime | None,
    until: datetime | None,
    limit: int,
) -> dict[str, Any]:
    summary_root = project_root / "data" / "normalized" / CHANNEL_GFFUNDS / "collection_summary"
    metadata_summary_path, metadata_summary = latest_json_matching(
        summary_root,
        lambda payload: payload.get("collector") == "update_gffunds_strategy_metadata",
        since=since,
        until=until,
    )
    metadata_current_run = metadata_summary_path is not None
    if metadata_summary_path is None:
        metadata_summary_path, metadata_summary = latest_json_matching(
            summary_root,
            lambda payload: payload.get("collector") == "update_gffunds_strategy_metadata",
            until=until,
        )
    collection_summary_path, collection_summary = latest_json_matching(
        summary_root,
        lambda payload: payload.get("collection_status") is not None,
        since=since,
        until=until,
    )
    collection_current_run = collection_summary_path is not None
    if collection_summary_path is None:
        collection_summary_path, collection_summary = latest_json_matching(
            summary_root,
            lambda payload: payload.get("collection_status") is not None,
            until=until,
        )

    missing_rows = query_rows(
        db_path,
        '''
        SELECT "渠道策略ID" AS strategy_id, "策略名称" AS strategy_name,
               "投顾费率" AS advisory_fee_rate, "业绩基准" AS benchmark
        FROM "策略信息"
        WHERE "渠道ID" = ?
          AND (
              "投顾费率" IS NULL OR TRIM(CAST("投顾费率" AS TEXT)) = ''
              OR "业绩基准" IS NULL OR TRIM(CAST("业绩基准" AS TEXT)) = ''
          )
        ORDER BY "渠道策略ID"
        ''',
        (CHANNEL_GFFUNDS,),
    )
    normalized_path = optional_path((metadata_summary or {}).get("normalized_path")) if isinstance(metadata_summary, dict) else None
    metadata_rows = read_jsonl(normalized_path)
    pdf_parse_failures = [
        str(row.get("source_strategy_id") or "")
        for row in metadata_rows
        if (((row.get("extra") or {}).get("protocol_parse_error")) and row.get("source_strategy_id"))
    ]
    pdf_parse_failure_details = [
        {
            "strategy_id": str(row.get("source_strategy_id") or ""),
            "error": str((row.get("extra") or {}).get("protocol_parse_error") or ""),
        }
        for row in metadata_rows
        if (((row.get("extra") or {}).get("protocol_parse_error")) and row.get("source_strategy_id"))
    ]
    latest_date = query_scalar(
        db_path,
        'SELECT MAX("交易日期") FROM "策略标准业绩净值" WHERE "渠道ID" = ?',
        (CHANNEL_GFFUNDS,),
    )
    return {
        "state": (
            "current_run"
            if metadata_current_run or collection_current_run
            else ("historical" if metadata_summary_path or collection_summary_path else "unavailable")
        ),
        "metadata_current_run": metadata_current_run,
        "collection_current_run": collection_current_run,
        "metadata_summary_path": str(metadata_summary_path) if metadata_summary_path else None,
        "metadata_selected_total": (metadata_summary or {}).get("selected_strategy_total") if isinstance(metadata_summary, dict) else None,
        "metadata_success_total": (metadata_summary or {}).get("success_total") if isinstance(metadata_summary, dict) else None,
        "metadata_failure_total": (metadata_summary or {}).get("failure_total") if isinstance(metadata_summary, dict) else None,
        "collection_summary_path": str(collection_summary_path) if collection_summary_path else None,
        "collection_status": (collection_summary or {}).get("collection_status") if isinstance(collection_summary, dict) else None,
        "collection_strategy_total": (collection_summary or {}).get("strategy_total") if isinstance(collection_summary, dict) else None,
        "collection_current_holding_rows": (collection_summary or {}).get("current_holding_rows") if isinstance(collection_summary, dict) else None,
        "collection_rebalance_event_total": (collection_summary or {}).get("rebalance_event_total") if isinstance(collection_summary, dict) else None,
        "benchmark_or_fee_missing_total": len(missing_rows),
        "benchmark_or_fee_missing_strategy_ids": sample([str(row.get("strategy_id") or "") for row in missing_rows], limit),
        "pdf_parse_failed_total": len(pdf_parse_failures),
        "pdf_parse_failed_strategy_ids": sample(sorted(set(pdf_parse_failures)), limit),
        "pdf_parse_failures": pdf_parse_failure_details[: max(0, limit)],
        "latest_disclosure_date": latest_date,
    }


def summarize_fund_nav(
    project_root: Path,
    db_path: Path,
    since: datetime | None,
    until: datetime | None,
    limit: int,
) -> dict[str, Any]:
    summary_path = latest_file(
        project_root / "data" / "normalized" / "ttfund_fund_nav" / "collection_summary",
        "*.json",
        since,
        until,
    )
    summary_current_run = summary_path is not None
    summary_path = summary_path or latest_file(
        project_root / "data" / "normalized" / "ttfund_fund_nav" / "collection_summary",
        "*.json",
        until=until,
    )
    summary = read_json(summary_path)
    failures = (
        ((summary or {}).get("failures") or (summary or {}).get("failures_sample") or [])
        if isinstance(summary, dict)
        else []
    )
    if not isinstance(failures, list):
        failures = []
    counters = (summary or {}).get("counters") if isinstance(summary, dict) else {}
    if not isinstance(counters, dict):
        counters = {}
    empty_codes = (summary or {}).get("empty_fund_codes") if isinstance(summary, dict) else []
    if not isinstance(empty_codes, list):
        empty_codes = []
    empty_codes = sorted({str(code).strip() for code in empty_codes if str(code).strip()})
    existing_codes = load_existing_fund_codes(db_path)
    failed_codes = [str(item.get("fund_code") or "").strip() for item in failures if isinstance(item, dict) and item.get("fund_code")]
    new_failed = sorted({code for code in failed_codes if code and code not in existing_codes})
    old_failed = sorted({code for code in failed_codes if code and code in existing_codes})
    latest_holding_funds = load_latest_holding_funds(db_path)
    empty_latest_holding_codes = sorted(set(empty_codes) & set(latest_holding_funds))
    latest_holding_date = max(latest_holding_funds.values(), default=None)
    empty_current_disclosure_codes = [
        code for code in empty_latest_holding_codes if latest_holding_funds.get(code) == latest_holding_date
    ]
    latest_date = query_scalar(
        db_path,
        'SELECT MAX("\u4ea4\u6613\u65e5\u671f") FROM "\u57fa\u91d1\u65e5\u5ea6\u51c0\u503c"',
    )
    return {
        "state": "current_run" if summary_current_run else ("historical" if summary_path else "unavailable"),
        "summary_current_run": summary_current_run,
        "summary_path": str(summary_path) if summary_path else None,
        "target_fund_total": counters.get("targets", (summary or {}).get("target_fund_total")) if isinstance(summary, dict) else None,
        "successful_fund_total": counters.get("success", (summary or {}).get("successful_fund_total")) if isinstance(summary, dict) else None,
        "empty_response_total": counters.get("empty", len(empty_codes)),
        "empty_response_fund_codes": sample(empty_codes, limit),
        "empty_latest_holding_total": len(empty_latest_holding_codes),
        "empty_latest_holding_fund_codes": sample(empty_latest_holding_codes, limit),
        "empty_current_disclosure_total": len(empty_current_disclosure_codes),
        "empty_current_disclosure_fund_codes": sample(empty_current_disclosure_codes, limit),
        "latest_holding_date": latest_holding_date,
        "failed_fund_total": counters.get("failed", len(failed_codes)),
        "new_fund_full_history_failed_total": len(new_failed),
        "new_fund_full_history_failed_codes": sample(new_failed, limit),
        "existing_fund_incremental_failed_total": len(old_failed),
        "existing_fund_incremental_failed_codes": sample(old_failed, limit),
        "daily_rows_total": counters.get("daily_rows"),
        "incremental_from_existing": (summary or {}).get("incremental_from_existing") if isinstance(summary, dict) else None,
        "retry_rounds": (summary or {}).get("retry_rounds") if isinstance(summary, dict) else None,
        "primary_failed_total": (summary or {}).get("primary_failed_total") if isinstance(summary, dict) else None,
        "critical_current_holding_failed_total": (
            (summary or {}).get("critical_current_holding_failed_total") if isinstance(summary, dict) else None
        ),
        "critical_current_holding_failed_codes": sample(
            (summary or {}).get("critical_current_holding_failed_codes") or [], limit
        ) if isinstance(summary, dict) else [],
        "critical_current_holding_empty_total": (
            (summary or {}).get("critical_current_holding_empty_total") if isinstance(summary, dict) else None
        ),
        "critical_current_holding_empty_codes": sample(
            (summary or {}).get("critical_current_holding_empty_codes") or [], limit
        ) if isinstance(summary, dict) else [],
        "gate_status": (summary or {}).get("gate_status") if isinstance(summary, dict) else None,
        "latest_nav_date": latest_date,
    }


def write_markdown(path: Path, payload: dict[str, Any]) -> None:
    lines: list[str] = [
        "# 增量采集缺口汇总",
        "",
        f"- 生成时间: {payload.get('generated_at')}",
        f"- 数据库: {payload.get('db_path')}",
        "",
        "## 天天投顾",
    ]
    ttfund = payload.get("ttfund") or {}
    lines.extend(
        [
            f"- 本次运行状态: {ttfund.get('state')}",
            f"- 当前仓位计划刷新策略数: {ttfund.get('selected_current_holding_total')}",
            f"- 原始当前持仓采集成功数: {ttfund.get('raw_current_holding_success_total')}",
            f"- 原始当前持仓未采到策略数: {ttfund.get('raw_current_holding_failed_total')}",
            f"- 真机首轮后当前持仓失败数: {ttfund.get('current_holding_primary_failed_total')}",
            f"- 同一真机第二批次补采成功数: {ttfund.get('current_holding_physical_retry_success_total')}",
            f"- 真机两批次后当前持仓仍失败数: {ttfund.get('current_holding_final_failed_total')}",
            f"- 生命周期明确结束并跳过策略数: {ttfund.get('definitively_stopped_skipped_total')}",
            f"- 停止售卖但仍纳入刷新策略数: {ttfund.get('stopped_but_refreshable_total')}",
            f"- 官方业绩曲线缺失策略数: {ttfund.get('official_curve_missing_total')}",
            f"- 官方业绩曲线设备复核后恢复数: {ttfund.get('official_curve_retry_recovered_total')}",
            f"- 官方业绩曲线复核后正常策略仍缺失数: {ttfund.get('official_curve_final_active_missing_total')}",
            f"- 已推算补齐策略数: {ttfund.get('projected_current_holding_total')}",
            f"- 调仓历史补齐失败策略数: {ttfund.get('rebalance_history_failed_total')}",
            f"- 最新披露日期: {ttfund.get('latest_disclosure_date')}",
            "",
            "## 广发基金 App",
        ]
    )
    gffunds = payload.get("gffunds") or {}
    lines.extend(
        [
            f"- 本次运行状态: {gffunds.get('state')}（historical 表示仅展示历史水位，不代表本次已执行）",
            f"- 基准或费率仍缺失策略数: {gffunds.get('benchmark_or_fee_missing_total')}",
            f"- PDF 解析失败策略数: {gffunds.get('pdf_parse_failed_total')}",
            f"- 最新披露日期: {gffunds.get('latest_disclosure_date')}",
            "",
            "## 仓位基金净值",
        ]
    )
    fund_nav = payload.get("fund_nav") or {}
    lines.extend(
        [
            f"- 本次运行状态: {fund_nav.get('state')}（historical 表示仅展示历史水位，不代表本次已执行）",
            f"- 基金池目标数: {fund_nav.get('target_fund_total')}",
            f"- 净值返回成功基金数: {fund_nav.get('successful_fund_total')}",
            f"- 净值空返回基金数: {fund_nav.get('empty_response_total')}",
            f"- 影响当期最新持仓的空返回基金数: {fund_nav.get('empty_current_disclosure_total')}",
            f"- 净值失败基金数: {fund_nav.get('failed_fund_total')}",
            f"- 首轮失败基金数: {fund_nav.get('primary_failed_total')}",
            f"- 重试后仍影响当前持仓的失败基金数: {fund_nav.get('critical_current_holding_failed_total')}",
            f"- 重试后当前持仓接口空返回基金数: {fund_nav.get('critical_current_holding_empty_total')}",
            f"- 净值发布门禁: {fund_nav.get('gate_status')}",
            f"- 新基金完整历史失败数: {fund_nav.get('new_fund_full_history_failed_total')}",
            f"- 老基金增量失败数: {fund_nav.get('existing_fund_incremental_failed_total')}",
            f"- 最新净值日期: {fund_nav.get('latest_nav_date')}",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    project_root = args.project_root.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    since = parse_dt(args.run_started_at)
    until = parse_dt(args.run_finished_at)
    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    payload = {
        "generated_at": generated_at,
        "project_root": str(project_root),
        "db_path": str(args.db_path.resolve()),
        "ttfund": summarize_ttfund(project_root, args.ttfund_summary, args.sample_limit),
        "gffunds": summarize_gffunds(project_root, args.db_path, since, until, args.sample_limit),
        "fund_nav": summarize_fund_nav(project_root, args.db_path, since, until, args.sample_limit),
    }
    json_path = output_dir / "collection_gap_summary.json"
    md_path = output_dir / "collection_gap_summary.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(md_path, payload)
    print(json.dumps({"json_path": str(json_path), "md_path": str(md_path)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
