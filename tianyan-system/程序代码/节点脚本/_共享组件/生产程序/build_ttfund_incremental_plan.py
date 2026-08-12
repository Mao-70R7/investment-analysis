from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DATA_ROOT = Path(os.environ.get("ADVISOR_DATABASE_ROOT") or PROJECT_ROOT / "data").resolve()
RAW_ROOT = Path(os.environ.get("ADVISOR_RAW_ROOT") or DATA_ROOT / "raw").resolve()
NORMALIZED_ROOT = Path(os.environ.get("ADVISOR_NORMALIZED_ROOT") or DATA_ROOT / "normalized").resolve()
OUTPUT_ROOT = Path(os.environ.get("ADVISOR_OUTPUT_ROOT") or PROJECT_ROOT / "outputs").resolve()
DEFAULT_DB_PATH = DATA_ROOT / "analysis_zh_current.sqlite"
sys.path.insert(0, str(PROJECT_ROOT / "节点脚本" / "_共享组件" / "python_src"))

DB_BASELINE_ERROR: str | None = None

from advisor_monitor.collectors.ttfund_loggedin import (  # noqa: E402
    ADJUSTMENT_CACHE_PATTERNS,
    ADJUSTMENT_HISTORY_CACHE_PATTERNS,
    DETAIL_CACHE_PATTERNS,
    HOME_CACHE_PREFIXES,
    QUOTE_API_URL,
    TTFundLoggedInCollector,
    USER_AGENT,
    parse_ymd,
    recursive_nodes,
)
from advisor_monitor.strategy_catalog import load_catalog_manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build an incremental execution plan for TTFund strategy updates."
    )
    parser.add_argument(
        "--history-mode",
        choices=("latest_only", "all_missing", "none"),
        default="latest_only",
        help="How to choose history repair targets.",
    )
    parser.add_argument(
        "--quote-batch-size",
        type=int,
        default=50,
        help="Batch size for public quote probing.",
    )
    parser.add_argument(
        "--quote-probe-timeout-sec",
        type=int,
        default=20,
        help="Short per-request timeout for planning quote probes.",
    )
    parser.add_argument(
        "--output-path",
        type=Path,
        default=None,
        help="Write the computed plan to this path. Defaults to data/raw/ttfund/incremental_plans/<day>/<runid>.json.",
    )
    parser.add_argument(
        "--selection-only",
        action="store_true",
        help="Print the computed plan and exit.",
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Print only a compact console summary. The complete plan is still written to --output-path.",
    )
    parser.add_argument(
        "--discovery-cache-dir",
        type=Path,
        default=RAW_ROOT / "device_cache",
        help="Local TTFund device_cache mirror used to discover new strategies before planning.",
    )
    parser.add_argument(
        "--catalog-manifest-path",
        type=Path,
        default=None,
        help="Optional per-run App catalog discovery manifest. IDs are merged into the incremental scope.",
    )
    parser.add_argument(
        "--direct-rebalance-probe-mode",
        choices=("all", "updated", "stale", "none"),
        default="all",
        help="Strategy scope for fast direct-interface rebalance probing.",
    )
    parser.add_argument(
        "--adb-rebalance-fallback-mode",
        choices=("selected", "missing", "none"),
        default="selected",
        help="Strategy scope for slower ADB history fallback when direct-interface probing is unavailable or fails.",
    )
    parser.add_argument(
        "--rebalance-stale-days",
        type=int,
        default=7,
        help="Treat local rebalance history cache as stale after this many days.",
    )
    parser.add_argument(
        "--rebalance-rolling-limit",
        type=int,
        default=60,
        help="Maximum stale strategies to include in one ADB fallback run.",
    )
    parser.add_argument(
        "--benchmark-detail-repair-mode",
        choices=("missing_detail", "all_missing_text", "none"),
        default="missing_detail",
        help=(
            "Select strategies for ADB detail repair when benchmark text is missing. "
            "missing_detail only retries absent/invalid detail caches; all_missing_text also retries valid detail caches without benchmark text."
        ),
    )
    parser.add_argument(
        "--benchmark-detail-repair-limit",
        type=int,
        default=80,
        help="Maximum benchmark detail repair targets per run. Use 0 for no limit.",
    )
    parser.add_argument(
        "--benchmark-detail-cooldown-days",
        type=int,
        default=7,
        help="Retry a valid detail cache with undisclosed benchmark text only after this many days.",
    )
    parser.add_argument(
        "--detail-cooldown-days",
        type=int,
        default=7,
        help="Refresh full ADB detail cache only when the latest valid detail cache is older than this many days. Use 0 to disable periodic detail refresh.",
    )
    parser.add_argument(
        "--detail-refresh-limit",
        type=int,
        default=80,
        help="Maximum missing or stale detail targets per run. Use 0 for no limit.",
    )
    parser.add_argument(
        "--current-holding-cooldown-days",
        type=int,
        default=1,
        help="Refresh App current holding detail cache after this many days. Use 0 to refresh every run.",
    )
    parser.add_argument(
        "--current-holding-refresh-limit",
        type=int,
        default=0,
        help="Maximum current holding detail targets per run. Use 0 for no limit.",
    )
    return parser.parse_args()


def latest_file(base_dir: Path, suffix: str) -> Path | None:
    if not base_dir.exists():
        return None
    candidates = sorted(base_dir.rglob(f"*{suffix}"))
    return candidates[-1] if candidates else None


def best_collection_summary(base_dir: Path) -> tuple[Path | None, dict[str, Any] | None]:
    if not base_dir.exists():
        return None, None
    best_path: Path | None = None
    best_summary: dict[str, Any] | None = None
    best_key: tuple[int, int, str] | None = None
    for path in sorted(base_dir.rglob("*.json")):
        try:
            summary = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if str(summary.get("batch_state") or "").startswith("failed"):
            continue
        strategy_total = int(summary.get("strategy_total") or 0)
        detail_total = int(summary.get("detail_cache_strategy_total") or 0)
        captured_at = str(summary.get("captured_at") or "")
        key = (strategy_total, detail_total, captured_at)
        if best_key is None or key > best_key:
            best_key = key
            best_path = path
            best_summary = summary
    return best_path, best_summary


def run_file(base_dir: Path, run_id: str | None, suffix: str = ".jsonl") -> Path | None:
    if not base_dir.exists() or not run_id:
        return None
    matches = sorted(base_dir.rglob(f"{run_id}{suffix}"))
    return matches[-1] if matches else None


def load_json(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_benchmark_repair_attempt_mtime_by_strategy(root: Path | None = None) -> dict[str, float]:
    attempts: dict[str, float] = {}
    if root is not None:
        result_paths = root.rglob("result.json") if root.exists() else []
    else:
        app_drive_root = RAW_ROOT / "ttfund" / "app_drive"
        incremental_root = RAW_ROOT / "ttfund" / "incremental_update_runs"
        repair_root = OUTPUT_ROOT / "ttfund_benchmark_detail_repair"
        result_paths = list(app_drive_root.glob("*/*/*/result.json"))
        result_paths.extend(incremental_root.glob("*/*/01_detail_drive/*/result.json"))
        result_paths.extend(repair_root.glob("*/*/*/result.json"))
    for path in result_paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        strategy_id = str(payload.get("strategy_id") or "").strip()
        if not strategy_id:
            continue
        touched_benchmark = bool(payload.get("detail_ok")) and (
            "benchmark_text_ok" in payload
            or "benchmark_ui_text_ok" in payload
            or "required_fields_ok" in payload
            or "incomplete_reason" in payload
        )
        if not touched_benchmark:
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        current = attempts.get(strategy_id)
        if current is None or mtime > current:
            attempts[strategy_id] = mtime
    return attempts


def load_jsonl(path: Path | None) -> list[dict[str, Any]]:
    if path is None or not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = str(value or "").strip()
        if item and item not in seen:
            result.append(item)
            seen.add(item)
    return result


def ordered_union(*groups: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for value in group:
            item = str(value or "").strip()
            if item and item not in seen:
                merged.append(item)
                seen.add(item)
    return merged


def extract_strategy_id(patterns: tuple[Any, ...], filename: str) -> str | None:
    for pattern in patterns:
        match = pattern.search(filename)
        if match:
            return str(match.group("sid")).strip()
    return None


def load_cache_json(path: Path) -> Any | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        payload = json.loads(text)
        if isinstance(payload, str):
            inner = payload.strip()
            if inner and inner[0] in "{[":
                return json.loads(inner)
        return payload
    except (OSError, json.JSONDecodeError):
        return None


def cache_mtime_iso(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).astimezone().isoformat(timespec="seconds")
    except OSError:
        return None


def load_cache_inventory(cache_dir: Path) -> dict[str, Any]:
    inventory: dict[str, Any] = {
        "cache_dir": path_text(cache_dir),
        "exists": cache_dir.exists(),
        "detail_freshness_source": "strategyDetailPageData",
        "layout_cache_counts_as_detail": False,
        "home_strategy_ids": [],
        "detail_strategy_ids": [],
        "detail_file_strategy_ids": [],
        "detail_layout_file_strategy_ids": [],
        "invalid_detail_strategy_ids": [],
        "invalid_detail_layout_strategy_ids": [],
        "detail_benchmark_strategy_ids": [],
        "detail_without_benchmark_strategy_ids": [],
        "detail_mtime_by_strategy": {},
        "detail_lifecycle_by_strategy": {},
        "latest_adjustment_strategy_ids": [],
        "history_adjustment_strategy_ids": [],
        "history_adjustment_mtime_by_strategy": {},
        "file_total": 0,
        "home_file_total": 0,
    }
    if not cache_dir.exists():
        return inventory

    home_ids: list[str] = []
    detail_ids: list[str] = []
    detail_file_ids: list[str] = []
    detail_layout_file_ids: list[str] = []
    invalid_detail_ids: list[str] = []
    invalid_detail_layout_ids: list[str] = []
    detail_benchmark_ids: list[str] = []
    detail_without_benchmark_ids: list[str] = []
    detail_mtime_by_strategy: dict[str, str] = {}
    detail_lifecycle_by_strategy: dict[str, dict[str, Any]] = {}
    detail_lifecycle_mtime_by_strategy: dict[str, float] = {}
    latest_adjustment_ids: list[str] = []
    history_adjustment_ids: list[str] = []
    history_mtime_by_strategy: dict[str, str] = {}
    file_total = 0
    home_file_total = 0

    for path in sorted(cache_dir.rglob("*")):
        if not path.is_file():
            continue
        file_total += 1
        name = path.name
        if name.startswith(HOME_CACHE_PREFIXES):
            home_file_total += 1
            payload = load_cache_json(path)
            if isinstance(payload, dict):
                for node in recursive_nodes(payload):
                    strategy_id = str(node.get("strategyId") or "").strip()
                    if strategy_id:
                        home_ids.append(strategy_id)
            continue

        strategy_id = extract_strategy_id(DETAIL_CACHE_PATTERNS, name)
        if strategy_id:
            is_detail_response = name.startswith("strategyDetailPageData")
            mtime = cache_mtime_iso(path)
            payload = load_cache_json(path)
            extend_info = payload.get("tgExtendInfo") if isinstance(payload, dict) else None

            # The layout cache can be regenerated from stale local state even when
            # the detail request has failed. It is useful as a diagnostic artifact,
            # but must never refresh the full-detail cooldown or mark a collection
            # attempt successful. Only strategyDetailPageData is the authoritative
            # response cache for detail completeness and freshness.
            if not is_detail_response:
                detail_layout_file_ids.append(strategy_id)
                if not (isinstance(extend_info, dict) and extend_info):
                    invalid_detail_layout_ids.append(strategy_id)
                continue

            detail_file_ids.append(strategy_id)
            if isinstance(extend_info, dict) and extend_info:
                detail_ids.append(strategy_id)
                if mtime:
                    current = detail_mtime_by_strategy.get(strategy_id)
                    if current is None or mtime > current:
                        detail_mtime_by_strategy[strategy_id] = mtime
                try:
                    lifecycle_mtime = path.stat().st_mtime
                except OSError:
                    lifecycle_mtime = 0.0
                previous_mtime = detail_lifecycle_mtime_by_strategy.get(strategy_id, -1.0)
                if lifecycle_mtime >= previous_mtime:
                    detail_lifecycle_mtime_by_strategy[strategy_id] = lifecycle_mtime
                    detail_lifecycle_by_strategy[strategy_id] = {
                        "is_stop": extend_info.get("isStop"),
                        "current_stage": str(extend_info.get("currentStage") or "").strip() or None,
                        "operate_end_time": str(extend_info.get("operateEndTime") or "").strip() or None,
                        "end_time": str(extend_info.get("endTime") or "").strip() or None,
                        "server_time": str(extend_info.get("serverTime") or "").strip() or None,
                        "cache_mtime": mtime,
                        "source_path": path_text(path),
                    }
                if detail_benchmark_text(payload):
                    detail_benchmark_ids.append(strategy_id)
                else:
                    detail_without_benchmark_ids.append(strategy_id)
            else:
                invalid_detail_ids.append(strategy_id)
            continue

        strategy_id = extract_strategy_id(ADJUSTMENT_HISTORY_CACHE_PATTERNS, name)
        if strategy_id:
            history_adjustment_ids.append(strategy_id)
            mtime = cache_mtime_iso(path)
            if mtime:
                current = history_mtime_by_strategy.get(strategy_id)
                if current is None or mtime > current:
                    history_mtime_by_strategy[strategy_id] = mtime
            continue

        strategy_id = extract_strategy_id(ADJUSTMENT_CACHE_PATTERNS, name)
        if strategy_id:
            latest_adjustment_ids.append(strategy_id)

    inventory.update(
        {
            "home_strategy_ids": dedupe(home_ids),
            "detail_strategy_ids": dedupe(detail_ids),
            "detail_file_strategy_ids": dedupe(detail_file_ids),
            "detail_layout_file_strategy_ids": dedupe(detail_layout_file_ids),
            "invalid_detail_strategy_ids": dedupe(invalid_detail_ids),
            "invalid_detail_layout_strategy_ids": dedupe(invalid_detail_layout_ids),
            "detail_benchmark_strategy_ids": dedupe(detail_benchmark_ids),
            "detail_without_benchmark_strategy_ids": dedupe(detail_without_benchmark_ids),
            "detail_mtime_by_strategy": detail_mtime_by_strategy,
            "detail_lifecycle_by_strategy": detail_lifecycle_by_strategy,
            "latest_adjustment_strategy_ids": dedupe(latest_adjustment_ids),
            "history_adjustment_strategy_ids": dedupe(history_adjustment_ids),
            "history_adjustment_mtime_by_strategy": history_mtime_by_strategy,
            "file_total": file_total,
            "home_file_total": home_file_total,
        }
    )
    return inventory


def load_db_baseline() -> dict[str, Any] | None:
    global DB_BASELINE_ERROR
    DB_BASELINE_ERROR = None
    if not DEFAULT_DB_PATH.exists():
        DB_BASELINE_ERROR = f"database_not_found: {DEFAULT_DB_PATH}"
        return None
    try:
        conn = sqlite3.connect(DEFAULT_DB_PATH)
        conn.row_factory = sqlite3.Row
        strategy_status_by_id: dict[str, str | None] = {}
        try:
            status_rows = conn.execute(
                'SELECT "渠道策略ID", "策略状态" FROM "策略信息" WHERE "渠道ID" = ? ORDER BY "渠道策略ID"',
                ("ttfund",),
            )
            strategy_status_by_id = {
                str(row[0]).strip(): (str(row[1]).strip() if row[1] is not None and str(row[1]).strip() else None)
                for row in status_rows
                if str(row[0]).strip()
            }
        except sqlite3.Error:
            strategy_status_by_id = {}
    except sqlite3.Error as exc:
        DB_BASELINE_ERROR = f"{type(exc).__name__}: {exc}"
        return None
    try:
        strategy_rows = [
            dict(row)
            for row in conn.execute(
                'SELECT "渠道策略ID", "业绩基准" FROM "策略信息" WHERE "渠道ID" = ? ORDER BY "渠道策略ID"',
                ("ttfund",),
            )
        ]
        strategy_ids = [str(row["渠道策略ID"]).strip() for row in strategy_rows if str(row["渠道策略ID"]).strip()]
        benchmark_text_ids = {
            str(row["渠道策略ID"]).strip()
            for row in strategy_rows
            if str(row["渠道策略ID"]).strip() and str(row.get("业绩基准") or "").strip()
        }
        if not strategy_ids:
            return None
        latest_trade_date = conn.execute(
            'SELECT MAX("交易日期") FROM "策略日度业绩" WHERE "渠道ID" = ?',
            ("ttfund",),
        ).fetchone()[0]
        latest_trade_rows = 0
        latest_trade_strategy_total = 0
        if latest_trade_date:
            latest_trade_rows, latest_trade_strategy_total = conn.execute(
                'SELECT COUNT(*), COUNT(DISTINCT "统一策略ID") FROM "策略日度业绩" WHERE "渠道ID" = ? AND "交易日期" = ?',
                ("ttfund", latest_trade_date),
            ).fetchone()
        event_ids = {
            str(row[0]).strip()
            for row in conn.execute('SELECT DISTINCT "渠道策略ID" FROM "策略调仓事件" WHERE "渠道ID" = ?', ("ttfund",))
            if str(row[0]).strip()
        }
        delta_ids = {
            str(row[0]).strip()
            for row in conn.execute('SELECT DISTINCT "渠道策略ID" FROM "策略调仓明细" WHERE "渠道ID" = ?', ("ttfund",))
            if str(row[0]).strip()
        }
        current_ids = {
            str(row[0]).strip()
            for row in conn.execute('SELECT DISTINCT "渠道策略ID" FROM "策略当前持仓" WHERE "渠道ID" = ?', ("ttfund",))
            if str(row[0]).strip()
        }
    except sqlite3.Error as exc:
        DB_BASELINE_ERROR = f"{type(exc).__name__}: {exc}"
        return None
    finally:
        conn.close()
    return {
        "strategy_ids": strategy_ids,
        "benchmark_text_ids": benchmark_text_ids,
        "latest_trade_date": latest_trade_date,
        "latest_trade_rows": int(latest_trade_rows or 0),
        "latest_trade_strategy_total": int(latest_trade_strategy_total or 0),
        "history_complete_ids": event_ids & delta_ids,
        "latest_strategy_ids": current_ids,
        "strategy_status_by_id": strategy_status_by_id,
    }


def path_text(path: Path | None) -> str | None:
    if path is None:
        return None
    return path.resolve().as_posix()


def norm_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text in {"--", "null", "None"}:
        return None
    return text


def detail_benchmark_text(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    candidates = (
        "basicCalFormulaRemark",
        "basicCalFormula",
        "benchmark",
        "benchmarkDesc",
        "benchmarkRemark",
        "standardDesc",
        "业绩比较基准",
        "业绩基准",
    )
    extend_info = payload.get("tgExtendInfo")
    for container in (extend_info, payload):
        if not isinstance(container, dict):
            continue
        for key in candidates:
            text = norm_text(container.get(key))
            if text:
                return text
    for node in recursive_nodes(payload):
        for key in candidates:
            text = norm_text(node.get(key))
            if text:
                return text
    return None


def max_text(values: list[str | None]) -> str | None:
    filtered = [value for value in values if value]
    return max(filtered) if filtered else None


def is_stopped_strategy_status(status: Any) -> bool:
    text = str(status or "").strip().lower()
    if not text:
        return False
    stopped_tokens = ("stopped", "terminated", "closed", "\u505c\u8fd0", "\u7ec8\u6b62", "\u6e05\u76d8", "\u5df2\u7ec8\u6b62")
    return any(token in text for token in stopped_tokens)


def parse_lifecycle_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text or text in {"-", "--"}:
        return None
    candidate = text[:10]
    for pattern in ("%Y-%m-%d", "%y-%m-%d", "%Y/%m/%d", "%y/%m/%d"):
        try:
            return datetime.strptime(candidate, pattern).date()
        except ValueError:
            continue
    return None


def classify_current_holding_lifecycle(
    status: Any,
    lifecycle: dict[str, Any] | None,
    *,
    as_of: date | None = None,
) -> str:
    """Classify whether a strategy is definitively past its operating lifecycle.

    TTFund's isStop/status=stopped also covers temporarily unavailable products,
    so an uncertain or still-operating strategy must stay in the daily refresh scope.
    """
    if not is_stopped_strategy_status(status):
        return "active_status"
    if not lifecycle:
        return "stopped_lifecycle_missing"
    if lifecycle.get("is_stop") is False:
        return "stopped_flag_cleared"

    stage = str(lifecycle.get("current_stage") or "").strip()
    operate_end = parse_lifecycle_date(lifecycle.get("operate_end_time"))
    today = as_of or datetime.now().astimezone().date()
    if stage == "observeStage" and operate_end is not None and operate_end <= today:
        return "definitively_stopped"
    if operate_end is not None and operate_end > today:
        return "stopped_operation_active"
    if stage in {"subscribeStage", "calmStage"}:
        return "stopped_active_stage"
    return "stopped_lifecycle_ambiguous"


def estimate_history_seconds(selected_ids: list[str], latest_only_ids: set[str]) -> int:
    total = 0
    for strategy_id in selected_ids:
        if strategy_id in latest_only_ids:
            total += 35
        else:
            total += 55
    return total


def collect_quote_snapshots_for_plan(
    collector: TTFundLoggedInCollector,
    strategy_ids: list[str],
    *,
    timeout_sec: int,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    results: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, Any]] = []
    max_retry_depth = 2

    def fetch_batch(batch_ids: list[str], batch_label: str, depth: int = 0) -> None:
        try:
            tg_code_with_date = ",".join(f"{strategy_id}_{collector.day}" for strategy_id in batch_ids)
            payload = f"tgCodeWithDateStr={tg_code_with_date}".encode("utf-8")
            request = Request(
                QUOTE_API_URL,
                data=payload,
                method="POST",
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "User-Agent": USER_AGENT,
                    "Accept-Encoding": "identity",
                    "Connection": "close",
                },
            )
            with urlopen(request, timeout=max(1, timeout_sec)) as response:
                payload_text = response.read().decode("utf-8", errors="replace")
            payload_json = json.loads(payload_text)
        except Exception as error:  # pragma: no cover - network can fail in many ways
            if len(batch_ids) > 1 and depth < max_retry_depth:
                midpoint = max(1, len(batch_ids) // 2)
                fetch_batch(batch_ids[:midpoint], f"{batch_label}a", depth + 1)
                fetch_batch(batch_ids[midpoint:], f"{batch_label}b", depth + 1)
                return
            errors.append(
                {
                    "batch": batch_label,
                    "strategy_total": len(batch_ids),
                    "strategy_ids": batch_ids,
                    "error": f"{type(error).__name__}: {error}",
                }
            )
            return
        for row in payload_json.get("Data") or []:
            strategy_id = str(row.get("TGCODE") or "").strip()
            if not strategy_id:
                continue
            results[strategy_id] = {
                "row": row,
                "source_snapshot_id": f"plan_quote_probe:{collector.run_id}:{batch_label}",
            }

    for batch_index, start in enumerate(range(0, len(strategy_ids), collector.quote_batch_size), start=1):
        batch_ids = strategy_ids[start:start + collector.quote_batch_size]
        batch_label = f"plan_{batch_index:04d}"
        fetch_batch(batch_ids, batch_label)
    return results, errors


def main() -> None:
    args = parse_args()

    summary_path, summary = best_collection_summary(NORMALIZED_ROOT / "ttfund" / "collection_summary")
    selected_run_id = str((summary or {}).get("run_id") or "").strip() or None
    master_path = run_file(
        NORMALIZED_ROOT / "ttfund" / "strategy_master", selected_run_id
    )
    daily_path = run_file(
        NORMALIZED_ROOT / "ttfund" / "strategy_performance_daily", selected_run_id
    )
    event_path = run_file(
        NORMALIZED_ROOT / "ttfund" / "strategy_rebalance_event", selected_run_id
    )
    delta_path = run_file(
        NORMALIZED_ROOT / "ttfund" / "strategy_rebalance_fund_delta", selected_run_id
    )

    master_rows = load_jsonl(master_path)
    daily_rows = load_jsonl(daily_path)
    event_rows = load_jsonl(event_path)
    delta_rows = load_jsonl(delta_path)
    db_baseline = load_db_baseline()
    cache_inventory = load_cache_inventory(args.discovery_cache_dir)
    catalog_manifest = load_catalog_manifest(args.catalog_manifest_path)

    db_strategy_ids = list((db_baseline or {}).get("strategy_ids") or [])
    master_strategy_ids = [
        str(row.get("source_strategy_id") or "").strip()
        for row in master_rows
        if str(row.get("source_strategy_id") or "").strip()
    ]
    catalog_strategy_ids = dedupe(list(catalog_manifest.get("catalog_strategy_ids") or []))
    cache_strategy_ids = dedupe(
        catalog_strategy_ids
        + list(cache_inventory.get("home_strategy_ids") or [])
        + list(cache_inventory.get("detail_strategy_ids") or [])
        + list(cache_inventory.get("latest_adjustment_strategy_ids") or [])
        + list(cache_inventory.get("history_adjustment_strategy_ids") or [])
    )
    strategy_ids = dedupe(db_strategy_ids + master_strategy_ids + cache_strategy_ids)

    if not strategy_ids or summary is None:
        output_path = args.output_path
        if output_path is None:
            now = datetime.now().astimezone()
            output_path = (
                RAW_ROOT
                / "ttfund"
                / "incremental_plans"
                / now.strftime("%Y-%m-%d")
                / f"{now.strftime('%Y%m%dT%H%M%S%z')}.json"
            )
        plan = {
            "state": "missing_local_baseline",
            "requires_full_capture": True,
            "message": "No local normalized baseline found. Run full capture first.",
            "output_path": path_text(output_path),
            "summary_path": path_text(summary_path),
            "master_path": path_text(master_path),
            "db_baseline": {
                "state": "unavailable" if DB_BASELINE_ERROR else "not_used",
                "error": DB_BASELINE_ERROR,
                "db_path": path_text(DEFAULT_DB_PATH),
            },
            "cache_inventory": {
                "cache_dir": cache_inventory.get("cache_dir"),
                "exists": cache_inventory.get("exists"),
                "home_strategy_total": len(cache_inventory.get("home_strategy_ids") or []),
                "cache_strategy_total": len(cache_strategy_ids),
            },
            "catalog_discovery": {
                "manifest_path": catalog_manifest.get("manifest_path"),
                "state": catalog_manifest.get("state"),
                "catalog_strategy_total": len(catalog_strategy_ids),
                "catalog_complete": bool(catalog_manifest.get("catalog_complete")),
                "catalog_completeness": catalog_manifest.get("catalog_completeness"),
            },
        }
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return

    local_latest_trade_date = db_baseline.get("latest_trade_date") if db_baseline else max_text([row.get("trade_date") for row in daily_rows])
    local_latest_run_id = summary.get("run_id")
    local_latest_captured_at = summary.get("captured_at")

    history_event_by_id: dict[str, dict[str, Any]] = {}
    history_event_strategy_ids: set[str] = set()
    latest_strategy_ids: set[str] = set()
    for row in event_rows:
        strategy_id = str(row.get("source_strategy_id") or "").strip()
        payload_type = row.get("payload_type")
        if payload_type == "history":
            rebalance_event_id = str(row.get("rebalance_event_id") or "").strip()
            if rebalance_event_id:
                history_event_by_id[rebalance_event_id] = row
            if strategy_id:
                history_event_strategy_ids.add(strategy_id)
        elif payload_type == "latest" and strategy_id:
            latest_strategy_ids.add(strategy_id)

    history_delta_strategy_ids: set[str] = set()
    for row in delta_rows:
        if row.get("payload_type") != "history":
            continue
        rebalance_event_id = str(row.get("rebalance_event_id") or "").strip()
        event_row = history_event_by_id.get(rebalance_event_id)
        if event_row:
            strategy_id = str(event_row.get("source_strategy_id") or "").strip()
            if strategy_id:
                history_delta_strategy_ids.add(strategy_id)

    history_complete_ids = set(
        db_baseline.get("history_complete_ids") if db_baseline else history_event_strategy_ids & history_delta_strategy_ids
    )
    if db_baseline:
        latest_strategy_ids |= set(db_baseline.get("latest_strategy_ids") or set())
    detail_cache_ids = set(cache_inventory.get("detail_strategy_ids") or [])
    detail_file_ids = set(cache_inventory.get("detail_file_strategy_ids") or [])
    detail_layout_file_ids = set(cache_inventory.get("detail_layout_file_strategy_ids") or [])
    invalid_detail_ids = set(cache_inventory.get("invalid_detail_strategy_ids") or [])
    invalid_detail_layout_ids = set(cache_inventory.get("invalid_detail_layout_strategy_ids") or [])
    detail_benchmark_ids = set(cache_inventory.get("detail_benchmark_strategy_ids") or [])
    detail_without_benchmark_ids = set(cache_inventory.get("detail_without_benchmark_strategy_ids") or [])
    detail_mtime_by_strategy = cache_inventory.get("detail_mtime_by_strategy") or {}
    detail_lifecycle_by_strategy = cache_inventory.get("detail_lifecycle_by_strategy") or {}
    benchmark_attempt_mtime_by_strategy = load_benchmark_repair_attempt_mtime_by_strategy()
    latest_adjustment_cache_ids = set(cache_inventory.get("latest_adjustment_strategy_ids") or [])
    history_adjustment_cache_ids = set(cache_inventory.get("history_adjustment_strategy_ids") or [])
    benchmark_text_ids = set(db_baseline.get("benchmark_text_ids") if db_baseline else [])
    local_known_ids = set(db_strategy_ids) | set(master_strategy_ids)
    cache_discovered_new_ids = sorted(set(cache_strategy_ids) - local_known_ids)
    catalog_discovered_new_ids = sorted(set(catalog_strategy_ids) - local_known_ids)
    new_strategy_ids = sorted(set(cache_discovered_new_ids) | set(catalog_discovered_new_ids))
    strategy_status_by_id = dict((db_baseline or {}).get("strategy_status_by_id") or {})
    lifecycle_class_by_strategy = {
        strategy_id: classify_current_holding_lifecycle(
            strategy_status_by_id.get(strategy_id),
            detail_lifecycle_by_strategy.get(strategy_id),
        )
        for strategy_id in strategy_ids
    }
    lifecycle_reason_counts: dict[str, int] = {}
    for lifecycle_class in lifecycle_class_by_strategy.values():
        lifecycle_reason_counts[lifecycle_class] = lifecycle_reason_counts.get(lifecycle_class, 0) + 1
    stopped_current_holding_skipped_ids = sorted(
        strategy_id
        for strategy_id, lifecycle_class in lifecycle_class_by_strategy.items()
        if lifecycle_class == "definitively_stopped"
    )
    stopped_but_refreshable_ids = sorted(
        strategy_id
        for strategy_id, lifecycle_class in lifecycle_class_by_strategy.items()
        if is_stopped_strategy_status(strategy_status_by_id.get(strategy_id))
        and lifecycle_class != "definitively_stopped"
    )
    current_holding_scope_ids = [
        strategy_id for strategy_id in strategy_ids if lifecycle_class_by_strategy[strategy_id] != "definitively_stopped"
    ]
    current_holding_scope_id_set = set(current_holding_scope_ids)
    detail_missing_ids = sorted(set(strategy_ids) - detail_cache_ids)
    benchmark_text_missing_ids = sorted(set(strategy_ids) - benchmark_text_ids)
    no_valid_detail_ids = sorted(set(strategy_ids) - detail_cache_ids)
    now_ts = datetime.now().astimezone().timestamp()
    detail_cooldown_seconds = max(0, args.detail_cooldown_days) * 86400
    benchmark_detail_cooldown_seconds = max(0, args.benchmark_detail_cooldown_days) * 86400
    stale_detail_ids: list[str] = []
    if detail_cooldown_seconds:
        for strategy_id in strategy_ids:
            if strategy_id not in detail_cache_ids:
                continue
            mtime_text = detail_mtime_by_strategy.get(strategy_id)
            if not mtime_text:
                stale_detail_ids.append(strategy_id)
                continue
            try:
                mtime = datetime.fromisoformat(str(mtime_text)).timestamp()
            except ValueError:
                stale_detail_ids.append(strategy_id)
                continue
            if now_ts - mtime >= detail_cooldown_seconds:
                stale_detail_ids.append(strategy_id)
    stale_detail_ids = sorted(set(stale_detail_ids))
    detail_refresh_ids = ordered_union(detail_missing_ids, stale_detail_ids)
    if args.detail_refresh_limit > 0:
        detail_refresh_ids = detail_refresh_ids[: args.detail_refresh_limit]
    current_holding_cooldown_seconds = max(0, args.current_holding_cooldown_days) * 86400
    stale_current_holding_ids: list[str] = []
    for strategy_id in current_holding_scope_ids:
        if strategy_id not in detail_cache_ids:
            continue
        mtime_text = detail_mtime_by_strategy.get(strategy_id)
        if not mtime_text:
            stale_current_holding_ids.append(strategy_id)
            continue
        try:
            mtime = datetime.fromisoformat(str(mtime_text)).timestamp()
        except ValueError:
            stale_current_holding_ids.append(strategy_id)
            continue
        if current_holding_cooldown_seconds == 0 or now_ts - mtime >= current_holding_cooldown_seconds:
            stale_current_holding_ids.append(strategy_id)
    stale_current_holding_ids = sorted(set(stale_current_holding_ids))
    current_holding_detail_missing_ids = [
        strategy_id for strategy_id in detail_missing_ids if strategy_id in current_holding_scope_id_set
    ]
    current_holding_refresh_ids = ordered_union(current_holding_detail_missing_ids, stale_current_holding_ids)
    if args.current_holding_refresh_limit > 0:
        current_holding_refresh_ids = current_holding_refresh_ids[: args.current_holding_refresh_limit]
    if args.benchmark_detail_repair_mode == "none":
        benchmark_detail_repair_ids: list[str] = []
    else:
        benchmark_candidates = (set(benchmark_text_missing_ids) - detail_benchmark_ids)
        if args.benchmark_detail_repair_mode == "missing_detail":
            benchmark_candidates &= set(no_valid_detail_ids)
        if benchmark_detail_cooldown_seconds:
            benchmark_recent_attempt_ids = {
                strategy_id
                for strategy_id, mtime in benchmark_attempt_mtime_by_strategy.items()
                if now_ts - mtime < benchmark_detail_cooldown_seconds
            }
            benchmark_candidates -= benchmark_recent_attempt_ids
        else:
            benchmark_recent_attempt_ids = set()
        benchmark_detail_repair_ids = sorted(benchmark_candidates)
        if args.benchmark_detail_repair_limit > 0:
            benchmark_detail_repair_ids = benchmark_detail_repair_ids[: args.benchmark_detail_repair_limit]
    if args.benchmark_detail_repair_mode == "none":
        benchmark_recent_attempt_ids = set()
    missing_history_ids = sorted(set(strategy_ids) - history_complete_ids)
    latest_only_ids = sorted(strategy_id for strategy_id in missing_history_ids if strategy_id in latest_strategy_ids)

    collector = TTFundLoggedInCollector(
        PROJECT_ROOT,
        sync_device_cache=False,
        fetch_public_quote=True,
        quote_batch_size=args.quote_batch_size,
    )
    quotes_by_strategy, quote_probe_errors = collect_quote_snapshots_for_plan(
        collector,
        strategy_ids,
        timeout_sec=args.quote_probe_timeout_sec,
    )
    remote_trade_date_by_strategy = {
        strategy_id: parse_ymd((meta.get("row") or {}).get("JZRQ") or (meta.get("row") or {}).get("SYRQ"))
        for strategy_id, meta in quotes_by_strategy.items()
    }
    remote_max_trade_date = max_text(list(remote_trade_date_by_strategy.values()))
    quote_probe_incomplete = bool(quote_probe_errors)
    updated_strategy_ids = sorted(
        strategy_id
        for strategy_id, trade_date in remote_trade_date_by_strategy.items()
        if trade_date and (local_latest_trade_date is None or trade_date >= local_latest_trade_date)
    )
    newer_strategy_ids = sorted(
        strategy_id
        for strategy_id, trade_date in remote_trade_date_by_strategy.items()
        if trade_date and (local_latest_trade_date is None or trade_date > local_latest_trade_date)
    )
    has_new_trade_date = bool(
        remote_max_trade_date and (local_latest_trade_date is None or remote_max_trade_date > local_latest_trade_date)
    )

    same_trade_date = bool(remote_max_trade_date and local_latest_trade_date == remote_max_trade_date)
    local_latest_strategy_total = int((db_baseline or {}).get("latest_trade_strategy_total") or 0)
    local_latest_rows = int((db_baseline or {}).get("latest_trade_rows") or 0)
    min_same_day_strategy_total = max(1, int(len(strategy_ids) * 0.9))
    local_same_day_coverage_ok = same_trade_date and local_latest_strategy_total >= min_same_day_strategy_total
    needs_same_day_repair = same_trade_date and not local_same_day_coverage_ok

    history_mtime_by_strategy = cache_inventory.get("history_adjustment_mtime_by_strategy") or {}
    stale_cutoff_seconds = max(0, args.rebalance_stale_days) * 86400
    now_ts = datetime.now().astimezone().timestamp()
    stale_history_ids: list[str] = []
    for strategy_id in strategy_ids:
        mtime_text = history_mtime_by_strategy.get(strategy_id)
        if not mtime_text:
            stale_history_ids.append(strategy_id)
            continue
        try:
            mtime = datetime.fromisoformat(str(mtime_text)).timestamp()
        except ValueError:
            stale_history_ids.append(strategy_id)
            continue
        if stale_cutoff_seconds and now_ts - mtime >= stale_cutoff_seconds:
            stale_history_ids.append(strategy_id)
    stale_history_ids = sorted(set(stale_history_ids))
    stale_history_ids_for_fallback = stale_history_ids
    if args.rebalance_rolling_limit > 0:
        stale_history_ids_for_fallback = stale_history_ids_for_fallback[: args.rebalance_rolling_limit]

    if args.direct_rebalance_probe_mode == "none":
        selected_rebalance_probe_ids: list[str] = []
    elif args.direct_rebalance_probe_mode == "all":
        selected_rebalance_probe_ids = list(strategy_ids)
    elif args.direct_rebalance_probe_mode == "updated":
        selected_rebalance_probe_ids = sorted(set(new_strategy_ids) | set(newer_strategy_ids) | set(updated_strategy_ids))
    else:
        selected_rebalance_probe_ids = sorted(
            set(new_strategy_ids)
            | set(newer_strategy_ids)
            | set(updated_strategy_ids)
            | set(stale_history_ids)
        )

    if args.history_mode == "none" or args.adb_rebalance_fallback_mode == "none":
        selected_history_ids = []
    elif args.history_mode == "all_missing" or args.adb_rebalance_fallback_mode == "missing":
        selected_history_ids = list(missing_history_ids)
    else:
        selected_history_ids = []

    selected_detail_ids = ordered_union(detail_refresh_ids, benchmark_detail_repair_ids)
    detail_missing_total = len(detail_missing_ids)
    strategy_total = len(strategy_ids)
    should_run_detail_drive = len(selected_detail_ids) > 0
    should_run_current_holding_drive = bool(current_holding_refresh_ids)
    should_run_history_drive = bool(selected_history_ids)
    should_run_direct_rebalance_probe = bool(selected_rebalance_probe_ids)
    should_sync_device_cache = should_run_detail_drive or should_run_history_drive or should_run_current_holding_drive
    should_collect = (
        has_new_trade_date
        or should_sync_device_cache
        or needs_same_day_repair
        or bool(new_strategy_ids)
        or quote_probe_incomplete
    )

    probe_seconds = 25
    detail_seconds = len(selected_detail_ids) * 16
    current_holding_seconds = len(current_holding_refresh_ids) * 8
    direct_probe_seconds = len(selected_rebalance_probe_ids) * 2
    selected_rebalance_probe_set = set(selected_rebalance_probe_ids)
    estimated_history_after_direct_ids = (
        [strategy_id for strategy_id in selected_history_ids if strategy_id not in selected_rebalance_probe_set]
        if should_run_direct_rebalance_probe
        else selected_history_ids
    )
    history_seconds = estimate_history_seconds(estimated_history_after_direct_ids, set(latest_only_ids))
    fallback_history_seconds = estimate_history_seconds(selected_history_ids, set(latest_only_ids))
    collect_seconds = 70 if should_sync_device_cache else (45 if should_collect else 0)
    total_seconds = probe_seconds + direct_probe_seconds + detail_seconds + current_holding_seconds + history_seconds + collect_seconds

    output_path = args.output_path
    if output_path is None:
        output_path = (
            RAW_ROOT
            / "ttfund"
            / "incremental_plans"
            / collector.day
            / f"{collector.run_id}.json"
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    plan = {
        "state": "ready",
        "requires_full_capture": False,
        "history_mode": args.history_mode,
        "local_baseline": {
            "summary_path": path_text(summary_path),
            "master_path": path_text(master_path),
            "daily_path": path_text(daily_path),
            "event_path": path_text(event_path),
            "delta_path": path_text(delta_path),
            "run_id": local_latest_run_id,
            "captured_at": local_latest_captured_at,
            "latest_trade_date": local_latest_trade_date,
            "latest_trade_rows": local_latest_rows,
            "latest_trade_strategy_total": local_latest_strategy_total,
            "same_day_coverage_ok": local_same_day_coverage_ok,
            "same_day_min_strategy_total": min_same_day_strategy_total,
            "strategy_total": strategy_total,
            "db_strategy_total": len(db_strategy_ids),
            "master_strategy_total": len(set(master_strategy_ids)),
            "cache_strategy_total": len(cache_strategy_ids),
            "catalog_strategy_total": len(catalog_strategy_ids),
            "catalog_strategy_ids": sorted(catalog_strategy_ids),
            "catalog_discovery_state": catalog_manifest.get("state"),
            "catalog_complete": bool(catalog_manifest.get("catalog_complete")),
            "catalog_completeness": catalog_manifest.get("catalog_completeness"),
            "catalog_discovered_new_total": len(catalog_discovered_new_ids),
            "catalog_discovered_new_ids": catalog_discovered_new_ids,
            "new_strategy_total": len(new_strategy_ids),
            "new_strategy_ids": new_strategy_ids,
            "cache_discovered_new_total": len(cache_discovered_new_ids),
            "cache_discovered_new_ids": cache_discovered_new_ids,
            "detail_cache_strategy_total": int(summary.get("detail_cache_strategy_total") or 0),
            "db_baseline_state": "available" if db_baseline else ("unavailable" if DB_BASELINE_ERROR else "not_found"),
            "db_baseline_error": DB_BASELINE_ERROR,
            "db_path": path_text(DEFAULT_DB_PATH),
            "local_detail_cache_strategy_total": len(detail_cache_ids),
            "local_detail_file_strategy_total": len(detail_file_ids),
            "local_detail_layout_file_strategy_total": len(detail_layout_file_ids),
            "invalid_detail_cache_strategy_total": len(invalid_detail_ids),
            "invalid_detail_layout_cache_strategy_total": len(invalid_detail_layout_ids),
            "detail_benchmark_text_strategy_total": len(detail_benchmark_ids),
            "detail_without_benchmark_text_strategy_total": len(detail_without_benchmark_ids),
            "benchmark_detail_attempt_strategy_total": len(benchmark_attempt_mtime_by_strategy),
            "benchmark_detail_recent_attempt_total": len(benchmark_recent_attempt_ids),
            "benchmark_detail_cooldown_days": args.benchmark_detail_cooldown_days,
            "detail_cooldown_days": args.detail_cooldown_days,
            "stale_detail_total": len(stale_detail_ids),
            "detail_refresh_limit": args.detail_refresh_limit,
            "detail_refresh_total": len(detail_refresh_ids),
            "current_holding_cooldown_days": args.current_holding_cooldown_days,
            "current_holding_scope_total": len(current_holding_scope_ids),
            "stopped_current_holding_skipped_total": len(stopped_current_holding_skipped_ids),
            "definitively_stopped_current_holding_skipped_total": len(stopped_current_holding_skipped_ids),
            "stopped_but_refreshable_total": len(stopped_but_refreshable_ids),
            "current_holding_lifecycle_reason_counts": lifecycle_reason_counts,
            "stale_current_holding_total": len(stale_current_holding_ids),
            "current_holding_refresh_limit": args.current_holding_refresh_limit,
            "current_holding_refresh_total": len(current_holding_refresh_ids),
            "benchmark_text_strategy_total": len(benchmark_text_ids),
            "benchmark_text_missing_total": len(benchmark_text_missing_ids),
            "local_latest_adjustment_cache_strategy_total": len(latest_adjustment_cache_ids),
            "local_history_adjustment_cache_strategy_total": len(history_adjustment_cache_ids),
            "history_complete_strategy_total": len(history_complete_ids),
            "history_missing_strategy_total": len(missing_history_ids),
            "latest_only_history_missing_total": len(latest_only_ids),
        },
        "cache_inventory": {
            "cache_dir": cache_inventory.get("cache_dir"),
            "exists": cache_inventory.get("exists"),
            "detail_freshness_source": cache_inventory.get("detail_freshness_source"),
            "layout_cache_counts_as_detail": cache_inventory.get("layout_cache_counts_as_detail"),
            "file_total": cache_inventory.get("file_total"),
            "home_file_total": cache_inventory.get("home_file_total"),
            "home_strategy_total": len(cache_inventory.get("home_strategy_ids") or []),
            "catalog_strategy_total": len(catalog_strategy_ids),
            "catalog_manifest_path": catalog_manifest.get("manifest_path"),
            "catalog_discovery_state": catalog_manifest.get("state"),
            "catalog_complete": bool(catalog_manifest.get("catalog_complete")),
            "catalog_completeness": catalog_manifest.get("catalog_completeness"),
            "detail_strategy_total": len(detail_cache_ids),
            "detail_file_strategy_total": len(detail_file_ids),
            "detail_layout_file_strategy_total": len(detail_layout_file_ids),
            "invalid_detail_strategy_total": len(invalid_detail_ids),
            "invalid_detail_layout_strategy_total": len(invalid_detail_layout_ids),
            "detail_benchmark_strategy_total": len(detail_benchmark_ids),
            "detail_without_benchmark_strategy_total": len(detail_without_benchmark_ids),
            "detail_mtime_strategy_total": len(detail_mtime_by_strategy),
            "detail_lifecycle_strategy_total": len(detail_lifecycle_by_strategy),
            "latest_adjustment_strategy_total": len(latest_adjustment_cache_ids),
            "history_adjustment_strategy_total": len(history_adjustment_cache_ids),
        },
        "remote_probe": {
            "run_id": collector.run_id,
            "raw_dir": path_text(collector.raw_base_dir),
            "quote_strategy_total": len(quotes_by_strategy),
            "quote_probe_error_total": len(quote_probe_errors),
            "quote_probe_errors": quote_probe_errors[:10],
            "quote_probe_incomplete": quote_probe_incomplete,
            "updated_strategy_total_ge_watermark": len(updated_strategy_ids),
            "newer_strategy_total_gt_watermark": len(newer_strategy_ids),
            "max_trade_date": remote_max_trade_date,
            "has_new_trade_date": has_new_trade_date,
            "needs_same_day_repair": needs_same_day_repair,
        },
        "selection": {
            "detail_missing_total": detail_missing_total,
            "detail_mode": "missing_or_stale_detail_plus_benchmark_repair",
            "detail_cooldown_days": args.detail_cooldown_days,
            "detail_refresh_limit": args.detail_refresh_limit,
            "stale_detail_total": len(stale_detail_ids),
            "stale_detail_ids": stale_detail_ids[:200],
            "detail_refresh_total": len(detail_refresh_ids),
            "detail_refresh_ids": detail_refresh_ids,
            "selected_detail_total": len(selected_detail_ids),
            "selected_detail_ids": selected_detail_ids,
            "benchmark_detail_repair_mode": args.benchmark_detail_repair_mode,
            "benchmark_detail_repair_limit": args.benchmark_detail_repair_limit,
            "benchmark_detail_cooldown_days": args.benchmark_detail_cooldown_days,
            "benchmark_detail_repair_total": len(benchmark_detail_repair_ids),
            "benchmark_detail_repair_ids": benchmark_detail_repair_ids,
            "benchmark_detail_recent_attempt_ids": sorted(benchmark_recent_attempt_ids)[:200],
            "invalid_detail_ids": sorted(invalid_detail_ids)[:200],
            "detail_without_benchmark_ids": sorted(detail_without_benchmark_ids)[:200],
            "current_holding_mode": "daily_detail_cache_refresh_independent_from_basic_info",
            "current_holding_cooldown_days": args.current_holding_cooldown_days,
            "current_holding_refresh_limit": args.current_holding_refresh_limit,
            "current_holding_scope_total": len(current_holding_scope_ids),
            "stopped_current_holding_skipped_total": len(stopped_current_holding_skipped_ids),
            "stopped_current_holding_skipped_ids": stopped_current_holding_skipped_ids[:200],
            "definitively_stopped_current_holding_skipped_total": len(stopped_current_holding_skipped_ids),
            "definitively_stopped_current_holding_skipped_ids": stopped_current_holding_skipped_ids[:200],
            "stopped_but_refreshable_total": len(stopped_but_refreshable_ids),
            "stopped_but_refreshable_ids": stopped_but_refreshable_ids[:200],
            "current_holding_lifecycle_reason_counts": lifecycle_reason_counts,
            "stale_current_holding_total": len(stale_current_holding_ids),
            "stale_current_holding_ids": stale_current_holding_ids[:200],
            "selected_current_holding_total": len(current_holding_refresh_ids),
            "selected_current_holding_ids": current_holding_refresh_ids,
            "history_missing_total": len(missing_history_ids),
            "history_latest_only_total": len(latest_only_ids),
            "stale_history_total": len(stale_history_ids),
            "stale_history_fallback_total": len(stale_history_ids_for_fallback),
            "stale_history_fallback_ids": stale_history_ids_for_fallback,
            "direct_rebalance_probe_mode": args.direct_rebalance_probe_mode,
            "selected_rebalance_probe_total": len(selected_rebalance_probe_ids),
            "selected_rebalance_probe_ids": selected_rebalance_probe_ids,
            "adb_rebalance_fallback_mode": args.adb_rebalance_fallback_mode,
            "selected_history_total": len(selected_history_ids),
            "selected_history_ids": selected_history_ids,
            "estimated_history_after_direct_total": len(estimated_history_after_direct_ids),
            "estimated_history_after_direct_ids": estimated_history_after_direct_ids,
            "latest_only_ids": latest_only_ids,
            "all_missing_history_ids": missing_history_ids,
        },
        "actions": {
            "should_run_direct_rebalance_probe": should_run_direct_rebalance_probe,
            "should_run_detail_drive": should_run_detail_drive,
            "should_run_current_holding_drive": should_run_current_holding_drive,
            "should_run_history_drive": should_run_history_drive,
            "should_collect": should_collect,
            "should_sync_device_cache": should_sync_device_cache,
        },
        "cadence_policy": {
            "daily": [
                "public_quote_probe",
                "strategy_performance_daily_incremental",
                "current_holding_detail_refresh",
                "direct_rebalance_probe",
            ],
            "low_frequency": {
                "strategy_detail": {
                    "cooldown_days": args.detail_cooldown_days,
                    "refresh_limit": args.detail_refresh_limit,
                    "missing_or_invalid_detail_always_selected": True,
                },
                "benchmark_detail_repair": {
                    "mode": args.benchmark_detail_repair_mode,
                    "limit": args.benchmark_detail_repair_limit,
                    "missing_detail_always_selected": args.benchmark_detail_repair_mode != "none",
                },
                "adb_history_fallback": {
                    "stale_days": args.rebalance_stale_days,
                    "rolling_limit": args.rebalance_rolling_limit,
                    "mode": args.adb_rebalance_fallback_mode,
                },
            },
        },
        "estimates": {
            "quote_probe_seconds": probe_seconds,
            "direct_rebalance_probe_seconds": direct_probe_seconds,
            "detail_drive_seconds": detail_seconds,
            "current_holding_drive_seconds": current_holding_seconds,
            "history_drive_seconds": history_seconds,
            "history_drive_worst_case_seconds": fallback_history_seconds,
            "collect_seconds": collect_seconds,
            "total_seconds": total_seconds,
            "total_minutes": round(total_seconds / 60, 1),
        },
    }

    plan["output_path"] = path_text(output_path)
    output_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.summary_only:
        console_summary = {
            "state": plan["state"],
            "output_path": plan["output_path"],
            "target_trade_date": remote_max_trade_date,
            "local_trade_date": local_latest_trade_date,
            "strategy_total": len(strategy_ids),
            "quote_strategy_total": len(quotes_by_strategy),
            "quote_probe_error_total": len(quote_probe_errors),
            "quote_probe_incomplete": quote_probe_incomplete,
            "db_baseline_state": plan["local_baseline"].get("db_baseline_state"),
            "db_baseline_error": plan["local_baseline"].get("db_baseline_error"),
            "selected_detail_total": len(selected_detail_ids),
            "stale_detail_total": len(stale_detail_ids),
            "detail_cooldown_days": args.detail_cooldown_days,
            "selected_current_holding_total": len(current_holding_refresh_ids),
            "stale_current_holding_total": len(stale_current_holding_ids),
            "current_holding_cooldown_days": args.current_holding_cooldown_days,
            "definitively_stopped_skipped_total": len(stopped_current_holding_skipped_ids),
            "stopped_but_refreshable_total": len(stopped_but_refreshable_ids),
            "benchmark_detail_repair_total": len(benchmark_detail_repair_ids),
            "selected_rebalance_probe_total": len(selected_rebalance_probe_ids),
            "selected_history_total": len(selected_history_ids),
            "estimated_minutes": round(total_seconds / 60, 1),
        }
        print(json.dumps(console_summary, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(plan, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
