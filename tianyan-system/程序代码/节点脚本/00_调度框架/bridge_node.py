from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen


def now_text() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def emit_console(message: str) -> None:
    """Relay node output without turning a detached console into a data failure."""

    try:
        print(message, flush=True)
    except (OSError, ValueError):
        # A caller may stop listening while a long-running child is still
        # healthy. Continue draining the child and silence later status lines;
        # node_result.json remains the authoritative result contract.
        sys.stdout = open(os.devnull, "w", encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON object expected: {path}")
    return payload


def child_run_id(parent_run_id: str, action: str, node_run_dir: Path) -> str:
    raw = f"{parent_run_id}__{action}__{node_run_dir.name}"
    return "".join(character if character.isalnum() or character in "+-_" else "_" for character in raw)


def load_daily_policy(code_root: Path) -> dict[str, Any]:
    path = code_root / "config" / "daily_update_policy.json"
    return read_json(path) if path.is_file() else {}


def business_day_lag(older: Any, newer: Any) -> int | None:
    """Return the weekday distance from an older disclosure date to a newer one."""

    try:
        start = date.fromisoformat(str(older or "")[:10])
        end = date.fromisoformat(str(newer or "")[:10])
    except ValueError:
        return None
    if start >= end:
        return 0
    lag = 0
    cursor = start
    while cursor < end:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:
            lag += 1
    return lag


def subtract_business_days(value: Any, days: int) -> str | None:
    try:
        cursor = date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None
    remaining = max(0, int(days))
    while remaining:
        cursor -= timedelta(days=1)
        if cursor.weekday() < 5:
            remaining -= 1
    return cursor.isoformat()


def qieman_nav_latest_date_counts(summary: dict[str, Any]) -> dict[str, int]:
    """Read per-strategy latest NAV dates, including legacy batches without the compact field."""

    raw_counts = summary.get("nav_latest_date_counts")
    if isinstance(raw_counts, dict):
        counts = {
            str(key).strip(): int(value or 0)
            for key, value in raw_counts.items()
            if str(key).strip() and int(value or 0) > 0
        }
        if counts:
            return counts

    history_run_dir = Path(str(summary.get("history_run_dir") or ""))
    history_summary = history_run_dir / "summary.json"
    if history_summary.is_file():
        payload = read_json(history_summary)
        counts: dict[str, int] = {}
        for item in payload.get("results") or []:
            if not isinstance(item, dict):
                continue
            nav = item.get("nav") if isinstance(item.get("nav"), dict) else {}
            latest_date = str(nav.get("latestDate") or "").strip()
            if latest_date:
                counts[latest_date] = counts.get(latest_date, 0) + 1
        if counts:
            return counts

    latest_date = str(summary.get("source_latest_nav_date") or "").strip()
    latest_total = int(summary.get("latest_nav_date_strategy_total") or 0)
    return {latest_date: latest_total} if latest_date and latest_total > 0 else {}


def assess_qieman_nav_freshness(
    summary: dict[str, Any],
    policy: dict[str, Any],
) -> dict[str, Any]:
    """Assess source freshness using a one-business-day per-product disclosure window."""

    source_latest = str(summary.get("source_latest_nav_date") or "").strip()
    strategy_total = int(summary.get("strategy_total") or 0)
    raw_maximum_lag = policy.get("maximumNavDateLagBusinessDays", 1)
    raw_minimum_ratio = policy.get(
        "minimumFreshNavDateRatio",
        policy.get("minimumLatestNavDateRatio", 0.98),
    )
    maximum_lag = max(0, min(5, int(raw_maximum_lag)))
    minimum_ratio = max(0.0, min(1.0, float(raw_minimum_ratio)))
    date_counts = qieman_nav_latest_date_counts(summary)
    non_empty_total = sum(date_counts.values())
    exact_total = int(date_counts.get(source_latest) or 0) if source_latest else 0
    fresh_total = 0
    if source_latest:
        fresh_total = sum(
            count
            for latest_date, count in date_counts.items()
            if (lag := business_day_lag(latest_date, source_latest)) is not None
            and lag <= maximum_lag
        )
    fresh_ratio = fresh_total / strategy_total if strategy_total else 0.0
    return {
        "sourceLatestNavDate": source_latest or None,
        "minimumFreshNavDate": subtract_business_days(source_latest, maximum_lag),
        "maximumNavDateLagBusinessDays": maximum_lag,
        "minimumFreshNavDateStrategyRatio": minimum_ratio,
        "strategyTotal": strategy_total,
        "nonEmptyNavStrategyTotal": non_empty_total,
        "latestNavDateStrategyTotal": exact_total,
        "latestNavDateStrategyRatio": (
            exact_total / strategy_total if strategy_total else 0.0
        ),
        "freshNavDateStrategyTotal": fresh_total,
        "freshNavDateStrategyRatio": fresh_ratio,
        "navLatestDateCounts": date_counts,
        "passed": bool(
            source_latest
            and strategy_total > 0
            and fresh_ratio + 1e-9 >= minimum_ratio
        ),
    }


GF_SUPPLEMENTAL_CHANNEL_IDS = ("gfsec_robot",)


def configured_gf_supplemental_channels(raw_value: str | None = None) -> tuple[str, ...]:
    """Return the explicitly selected GF supplemental channels in stable order."""

    raw = raw_value
    if raw is None:
        raw = os.environ.get("TIANYAN_GF_SUPPLEMENTAL_CHANNELS")
    if raw is None or not raw.strip():
        return GF_SUPPLEMENTAL_CHANNEL_IDS
    requested = [item.strip() for item in raw.replace(";", ",").split(",") if item.strip()]
    unknown = sorted(set(requested) - set(GF_SUPPLEMENTAL_CHANNEL_IDS))
    if unknown:
        raise ValueError(f"unsupported GF supplemental channels: {', '.join(unknown)}")
    selected = tuple(channel_id for channel_id in GF_SUPPLEMENTAL_CHANNEL_IDS if channel_id in requested)
    if not selected:
        raise ValueError("at least one GF supplemental channel is required")
    return selected


def assess_gffunds_core_coverage(
    summary: dict[str, Any],
    minimum_ratio: float,
) -> tuple[dict[str, float], list[str]]:
    # Target-profit ZY issues only expose public metadata; their issue-specific
    # performance/holding/rebalance requires login.  Core completeness ratios
    # therefore use ordinary GFJJ strategies as the denominator.
    strategy_total = int(summary.get("core_strategy_total") or summary.get("strategy_total") or 0)
    threshold = max(0.0, min(1.0, float(minimum_ratio)))
    numerators = {
        "performanceStrategyRatio": int(summary.get("yield_non_empty") or 0),
        "rebalanceResponseRatio": int(summary.get("rebalance_ok") or 0),
        "latestHoldingStrategyRatio": int(summary.get("latest_snapshot_non_empty") or 0),
    }
    ratios = {
        key: (value / strategy_total if strategy_total > 0 else 0.0)
        for key, value in numerators.items()
    }
    failures = [
        key
        for key, ratio in ratios.items()
        if strategy_total <= 0 or ratio < threshold
    ]
    return ratios, failures


def assess_strategy_catalog_batch(
    summary: dict[str, Any],
    policy: dict[str, Any],
) -> tuple[list[str], dict[str, Any]]:
    """Enforce source-catalog completeness and catalog-to-batch ID closure."""

    require_complete = bool(policy.get("requireCompleteCatalog"))
    require_batch_closure = bool(policy.get("requireCatalogBatchClosure"))
    catalog_complete = summary.get("catalog_complete") is True
    catalog_batch_closed = summary.get("catalog_batch_closed") is True
    failures: list[str] = []
    if require_complete and not catalog_complete:
        failures.append("strategy_catalog_complete")
    if require_batch_closure and not catalog_batch_closed:
        failures.append("strategy_catalog_batch_closure")
    return failures, {
        "requireCompleteCatalog": require_complete,
        "catalogComplete": catalog_complete,
        "requireCatalogBatchClosure": require_batch_closure,
        "catalogBatchClosed": catalog_batch_closed,
        "catalogStrategyTotal": int(summary.get("catalog_strategy_total") or 0),
        "catalogNewStrategyTotal": int(summary.get("catalog_new_strategy_total") or 0),
        "catalogNewStrategyCollectedTotal": int(
            summary.get("catalog_new_strategy_collected_total") or 0
        ),
        "catalogBatchMissingStrategyTotal": int(
            summary.get("catalog_batch_missing_strategy_total") or 0
        ),
        "catalogBatchMissingStrategyIds": list(
            summary.get("catalog_batch_missing_strategy_ids") or []
        ),
        "catalogNewStrategyMissingTotal": int(
            summary.get("catalog_new_strategy_missing_total") or 0
        ),
        "catalogNewStrategyMissingIds": list(
            summary.get("catalog_new_strategy_missing_ids") or []
        ),
    }


def previous_gffunds_collection_total(
    summary_root: Path,
    current_summary: Path,
) -> int:
    if not summary_root.is_dir():
        return 0
    for path in sorted(
        summary_root.glob("*/*.json"),
        key=lambda item: item.stat().st_mtime_ns,
        reverse=True,
    ):
        if path == current_summary or path.name.endswith(
            (".coverage.json", ".inventory.json")
        ):
            continue
        try:
            previous = read_json(path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if str(previous.get("channel_id") or "") != "gffunds":
            continue
        if not str(previous.get("collection_status") or "").startswith("success"):
            continue
        if not (
            ("yield_non_empty" in previous or "yield_ok" in previous)
            and "rebalance_ok" in previous
            and "latest_snapshot_non_empty" in previous
        ):
            continue
        candidate_total = int(previous.get("strategy_total") or 0)
        if candidate_total > 0:
            return candidate_total
    return 0


def previous_gfsec_fima_product_total(
    summary_root: Path,
    current_summary: Path,
) -> int:
    if not summary_root.is_dir():
        return 0
    for path in sorted(
        summary_root.glob("*/*.json"),
        key=lambda item: item.stat().st_mtime_ns,
        reverse=True,
    ):
        if path == current_summary or path.name.endswith((".coverage.json", ".inventory.json")):
            continue
        try:
            previous = read_json(path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if str(previous.get("channel_id") or "") != "gfsec_fima":
            continue
        if not str(previous.get("collection_status") or "").startswith("success"):
            continue
        candidate_total = int(previous.get("strategy_total") or previous.get("planned_product_total") or 0)
        if candidate_total > 0:
            return candidate_total
    return 0


def previous_channel_strategy_total(
    summary_root: Path,
    current_summary: Path,
    channel_id: str,
) -> int:
    if not summary_root.is_dir():
        return 0
    for path in sorted(
        summary_root.glob("*/*.json"),
        key=lambda item: item.stat().st_mtime_ns,
        reverse=True,
    ):
        if path == current_summary or path.name.endswith((".coverage.json", ".inventory.json")):
            continue
        try:
            previous = read_json(path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if str(previous.get("channel_id") or "") != channel_id:
            continue
        if not str(previous.get("collection_status") or "").startswith("success"):
            continue
        candidate_total = int(previous.get("strategy_total") or 0)
        if candidate_total > 0:
            return candidate_total
    return 0


def assess_gf_supplemental_channel(
    channel_id: str,
    summary: dict[str, Any],
    coverage: dict[str, Any],
    policy: dict[str, Any],
    previous_total: int,
    *,
    now: datetime | None = None,
) -> tuple[list[str], list[str], dict[str, Any]]:
    failures: list[str] = []
    warnings: list[str] = []
    strategy_total = int(summary.get("strategy_total") or 0)
    minimum_total = int(policy.get("minimumStrategyTotal") or 1)
    retention_ratio = float(policy.get("minimumInventoryRetentionRatio") or 0.8)
    if not str(summary.get("collection_status") or "").startswith("success"):
        failures.append("collection_status_not_success")
    if strategy_total < minimum_total:
        failures.append("strategy_total_below_minimum")
    if coverage.get("strategy_master_ok") is not True:
        failures.append("strategy_master_not_available")
    inventory_ratio = strategy_total / previous_total if previous_total > 0 else 1.0
    if previous_total > 0 and inventory_ratio < retention_ratio:
        failures.append("strategy_inventory_retention")
    catalog_failures, catalog_metrics = assess_strategy_catalog_batch(summary, policy)
    failures.extend(catalog_failures)

    if channel_id == "gfsec_robot":
        if int(summary.get("daily_performance_rows") or 0) < int(policy.get("minimumDailyPerformanceRows") or 1):
            failures.append("daily_performance_rows_below_minimum")
        if int(summary.get("interval_performance_rows") or 0) < int(policy.get("minimumIntervalPerformanceRows") or 1):
            failures.append("interval_performance_rows_below_minimum")
        if int(summary.get("recommendation_fund_rows") or 0) < int(policy.get("minimumRecommendationRows") or 1):
            failures.append("recommendation_rows_below_minimum")
        if coverage.get("fund_level_position_ok") is not True:
            warnings.append("public_recommendation_list_is_not_precise_holding")
    elif channel_id == "gfbank_cgb":
        if int(summary.get("daily_performance_rows") or 0) < int(policy.get("minimumDailyPerformanceRows") or 1):
            failures.append("authenticated_daily_performance_missing")
        if not str(summary.get("authenticated_cache_run_id") or "").strip():
            failures.append("authenticated_cache_provenance_missing")
        captured_text = str(summary.get("authenticated_cache_captured_at") or "").strip()
        if captured_text:
            try:
                captured_at = datetime.fromisoformat(captured_text.replace("Z", "+00:00"))
                reference = now or datetime.now().astimezone()
                if captured_at.tzinfo is None:
                    captured_at = captured_at.astimezone()
                age_days = max(0, (reference.astimezone(captured_at.tzinfo) - captured_at).days)
                max_age_days = int(policy.get("maximumAuthenticatedCacheAgeDays") or 30)
                if age_days > max_age_days:
                    warnings.append(f"authenticated_cache_stale_days={age_days}")
            except ValueError:
                warnings.append("authenticated_cache_timestamp_unparseable")
        if coverage.get("fund_level_position_ok") is not True:
            warnings.append("authenticated_ui_has_no_fund_level_holding")

    return (
        list(dict.fromkeys(failures)),
        list(dict.fromkeys(warnings)),
        {
            "strategyTotal": strategy_total,
            "previousStrategyTotal": previous_total,
            "inventoryRetentionRatio": inventory_ratio,
            "minimumStrategyTotal": minimum_total,
            "minimumInventoryRetentionRatio": retention_ratio,
            **catalog_metrics,
        },
    )


def cleanup_daily_logs(
    log_root: Path,
    lock_root: Path,
    current_run_id: str,
    policy: dict[str, Any],
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    daily_root = (log_root / "daily_update").resolve()
    protected = {current_run_id}
    lock_path = lock_root / "daily_update.lock"
    if lock_path.is_file():
        try:
            active_run_id = str(read_json(lock_path).get("runId") or "").strip()
            if active_run_id:
                protected.add(active_run_id)
        except (OSError, ValueError, json.JSONDecodeError):
            pass
    log_policy = policy.get("logs") if isinstance(policy.get("logs"), dict) else {}
    success_days = max(1, int(log_policy.get("successfulRunRetentionDays") or 30))
    failure_days = max(success_days, int(log_policy.get("failedOrUnfinishedRunRetentionDays") or 90))
    launcher_days = max(1, int(log_policy.get("launcherRetentionDays") or 30))
    now = datetime.now().astimezone()
    candidates: list[dict[str, Any]] = []
    removed: list[str] = []
    reclaimed_bytes = 0
    day_directories = (
        sorted(path for path in daily_root.iterdir() if path.is_dir())
        if daily_root.is_dir()
        else []
    )
    for day_dir in day_directories:
        for run_dir in sorted(path for path in day_dir.iterdir() if path.is_dir()):
            if run_dir.name in protected:
                continue
            try:
                resolved = run_dir.resolve()
                resolved.relative_to(daily_root)
            except (OSError, ValueError):
                continue
            summary_path = run_dir / "summary.json"
            summary: dict[str, Any] = {}
            if summary_path.is_file():
                try:
                    summary = read_json(summary_path)
                except (OSError, ValueError, json.JSONDecodeError):
                    summary = {}
            status = str(summary.get("status") or "unfinished")
            retention_days = (
                success_days
                if status in {"success", "success_with_warning"}
                else failure_days
            )
            age_days = (now.timestamp() - run_dir.stat().st_mtime) / 86400
            if age_days < retention_days:
                continue
            size_bytes = sum(
                path.stat().st_size
                for path in run_dir.rglob("*")
                if path.is_file()
            )
            candidate = {
                "path": str(run_dir),
                "runId": run_dir.name,
                "status": status,
                "ageDays": round(age_days, 1),
                "retentionDays": retention_days,
                "bytes": size_bytes,
            }
            candidates.append(candidate)
            if not dry_run:
                shutil.rmtree(run_dir)
                removed.append(str(run_dir))
                reclaimed_bytes += size_bytes
    launcher_candidates: list[dict[str, Any]] = []
    launcher_removed: list[str] = []
    launcher_root = (log_root / "launcher").resolve()
    try:
        launcher_root.relative_to(log_root.resolve())
    except ValueError:
        launcher_root = log_root.resolve() / "launcher"
    if launcher_root.is_dir():
        for path in sorted(launcher_root.glob("*.log")):
            if not path.is_file():
                continue
            age_days = (now.timestamp() - path.stat().st_mtime) / 86400
            if age_days < launcher_days:
                continue
            size_bytes = path.stat().st_size
            launcher_candidates.append(
                {
                    "path": str(path),
                    "ageDays": round(age_days, 1),
                    "retentionDays": launcher_days,
                    "bytes": size_bytes,
                }
            )
            if not dry_run:
                path.unlink()
                launcher_removed.append(str(path))
                reclaimed_bytes += size_bytes
    return {
        "successRetentionDays": success_days,
        "failureRetentionDays": failure_days,
        "launcherRetentionDays": launcher_days,
        "protectedRunIds": sorted(protected),
        "candidates": candidates,
        "removed": removed,
        "launcherCandidates": launcher_candidates,
        "launcherRemoved": launcher_removed,
        "reclaimedBytes": reclaimed_bytes,
        "dryRun": dry_run,
    }


def promote_report_directory(staging_root: Path, report_root: Path, token: str) -> dict[str, Any]:
    staging = staging_root.resolve()
    report = report_root.resolve()
    if staging.parent != report.parent:
        raise RuntimeError("report staging and formal roots must share the same parent")
    if not staging.is_dir():
        raise FileNotFoundError(f"report staging directory missing: {staging}")
    backup = report.parent / f".{report.name}.previous.{token}"
    if backup.exists():
        raise RuntimeError(f"report promotion backup already exists: {backup}")
    moved_formal = False
    try:
        if report.exists():
            os.replace(report, backup)
            moved_formal = True
        os.replace(staging, report)
    except BaseException:
        if moved_formal and backup.exists() and not report.exists():
            os.replace(backup, report)
        raise
    cleanup_warning = None
    if backup.exists():
        try:
            shutil.rmtree(backup)
        except OSError as exc:
            cleanup_warning = f"{type(exc).__name__}: {exc}"
    return {
        "status": "promoted",
        "stagingRoot": str(staging),
        "reportRoot": str(report),
        "previousRoot": str(backup),
        "previousCleanupWarning": cleanup_warning,
        "promotedAt": now_text(),
    }


def configured_report_scope(policy: dict[str, Any]) -> str:
    report_policy = policy.get("reports") if isinstance(policy.get("reports"), dict) else {}
    scope = str(report_policy.get("dailyScope") or "minimal_publish").strip().lower()
    if scope != "minimal_publish":
        raise ValueError(
            f"unsupported daily report scope: {scope}; only minimal_publish is maintained"
        )
    return "minimal_publish"


def cleanup_minimal_report_sources(
    source_root: Path,
    current_source: Path,
    *,
    retention_days: int,
    retain_failed_runs: int,
    dry_run: bool = False,
) -> dict[str, Any]:
    root = source_root.resolve()
    current = current_source.resolve()
    if current.parent != root:
        raise ValueError(f"minimal report source must be a direct child of {root}: {current}")
    candidates: list[dict[str, Any]] = []
    removed: list[str] = []
    reclaimed_bytes = 0
    if not root.is_dir():
        return {
            "root": str(root),
            "retentionDays": max(1, int(retention_days)),
            "retainFailedRuns": max(0, int(retain_failed_runs)),
            "candidates": candidates,
            "removed": removed,
            "reclaimedBytes": reclaimed_bytes,
            "dryRun": dry_run,
        }

    keep_count = max(0, int(retain_failed_runs))
    keep_days = max(1, int(retention_days))
    now = time.time()
    existing = sorted(
        (
            path
            for path in root.iterdir()
            if path.is_dir() and not path.is_symlink() and path.resolve() != current
        ),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    for index, path in enumerate(existing):
        resolved = path.resolve()
        if resolved.parent != root:
            continue
        age_days = (now - path.stat().st_mtime) / 86400
        if index < keep_count and age_days < keep_days:
            continue
        size_bytes = sum(
            item.stat().st_size
            for item in path.rglob("*")
            if item.is_file()
        )
        candidate = {
            "path": str(resolved),
            "ageDays": round(age_days, 1),
            "bytes": size_bytes,
        }
        candidates.append(candidate)
        if not dry_run:
            shutil.rmtree(resolved)
            removed.append(str(resolved))
            reclaimed_bytes += size_bytes
    return {
        "root": str(root),
        "retentionDays": keep_days,
        "retainFailedRuns": keep_count,
        "candidates": candidates,
        "removed": removed,
        "reclaimedBytes": reclaimed_bytes,
        "dryRun": dry_run,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--action", required=True)
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--node-run-dir", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


class Bridge:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.workspace_root = args.workspace_root.resolve()
        self.code_root = Path(os.environ["ADVISOR_CODE_ROOT"]).resolve()
        self.program_root = Path(os.environ["ADVISOR_LEGACY_PROGRAM_ROOT"]).resolve()
        self.database_root = Path(os.environ["ADVISOR_DATABASE_ROOT"]).resolve()
        self.report_root = Path(os.environ["ADVISOR_REPORT_ROOT"]).resolve()
        self.publish_root = Path(os.environ["ADVISOR_PUBLISH_ROOT"]).resolve()
        self.backup_root = Path(os.environ["ADVISOR_BACKUP_ROOT"]).resolve()
        self.output_root = Path(os.environ["ADVISOR_OUTPUT_ROOT"]).resolve()
        self.temp_root = Path(os.environ["ADVISOR_TEMP_ROOT"]).resolve()
        self.log_root = Path(os.environ["ADVISOR_LOG_ROOT"]).resolve()
        self.lock_root = Path(
            os.environ.get("ADVISOR_LOCK_ROOT") or self.workspace_root / "运行状态" / "locks"
        ).resolve()
        self.raw_root = Path(os.environ["ADVISOR_RAW_ROOT"]).resolve()
        self.normalized_root = Path(os.environ["ADVISOR_NORMALIZED_ROOT"]).resolve()
        self.node_run_dir = args.node_run_dir.resolve()
        self.node_run_dir.mkdir(parents=True, exist_ok=True)
        self.child_run_id = child_run_id(args.run_id, args.action, self.node_run_dir)
        self.policy = load_daily_policy(self.code_root)
        self.result_path = self.node_run_dir / "node_result.json"
        self.started_at = now_text()
        self.artifacts: list[dict[str, Any]] = []
        self.context_updates: dict[str, str] = {}
        self.counters: dict[str, Any] = {}
        self.warnings: list[str] = []
        self.watermarks: dict[str, Any] = {}

    @property
    def python(self) -> str:
        return os.environ.get("ADVISOR_PYTHON_EXE") or sys.executable

    def program(self, name: str) -> Path:
        path = self.program_root / name
        if not path.exists():
            raise FileNotFoundError(f"production program missing: {path}")
        return path

    def run_command(self, command: list[str], *, env: dict[str, str] | None = None) -> int:
        emit_console("COMMAND " + subprocess.list2cmdline(command))
        if self.args.dry_run:
            emit_console("[DONE] DRY RUN")
            return 0
        environment = os.environ.copy()
        if env:
            environment.update(env)
        process = subprocess.Popen(
            command,
            cwd=self.code_root,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert process.stdout is not None
        for line in process.stdout:
            emit_console(line.rstrip("\r\n"))
        return int(process.wait())

    def python_command(self, script: str, *arguments: str) -> list[str]:
        return [self.python, "-u", "-X", "utf8", str(self.program(script)), *arguments]

    def environment_preflight(self) -> int:
        required = ["requests", "pandas", "numpy", "openpyxl"]
        missing = [name for name in required if importlib.util.find_spec(name) is None]
        commands = {name: shutil.which(name) for name in ("git", "node")}
        adb = self.code_root / "tools" / "platform-tools" / "adb.exe"
        payload = {
            "python": sys.version,
            "missingModules": missing,
            "commands": commands,
            "adb": str(adb),
            "adbExists": adb.is_file(),
        }
        path = self.node_run_dir / "environment_preflight.json"
        atomic_json(path, payload)
        self.artifacts.append({"key": "environment_preflight", "path": str(path), "validationStatus": "passed" if not missing else "failed"})
        try:
            cleanup = cleanup_daily_logs(
                self.log_root,
                self.lock_root,
                self.args.run_id,
                self.policy,
                dry_run=self.args.dry_run,
            )
            cleanup_path = self.node_run_dir / "log_retention_cleanup.json"
            atomic_json(cleanup_path, cleanup)
            self.artifacts.append(
                {
                    "key": "log_retention_cleanup",
                    "path": str(cleanup_path),
                    "validationStatus": "passed",
                }
            )
            self.counters["removedOldLogRuns"] = len(cleanup.get("removed") or [])
            self.counters["removedOldLauncherLogs"] = len(
                cleanup.get("launcherRemoved") or []
            )
            self.counters["reclaimedLogBytes"] = int(cleanup.get("reclaimedBytes") or 0)
        except OSError as exc:
            warning = f"日志保留清理未完成：{type(exc).__name__}: {exc}"
            self.warnings.append(warning)
            print(f"[WARN] {warning}", flush=True)
        if missing:
            print(f"[ERROR] missing Python modules: {', '.join(missing)}", flush=True)
            return 2
        print("[DONE] Python and command dependency preflight passed.", flush=True)
        return 0

    def database_health(self) -> int:
        return self.run_command(
            self.python_command(
                "check_runtime_database_health.py",
                "--project-root",
                str(self.code_root),
                "--db-path",
                str(self.database_root / "analysis_zh_current.sqlite"),
                "--repair-empty",
                "--integrity-mode",
                "auto",
                "--full-check-interval-days",
                "7",
            )
        )

    def source_readiness(self) -> int:
        output = self.node_run_dir / "readiness.json"
        checks = 1 if self.args.dry_run else max(1, int(os.environ.get("ADVISOR_READINESS_MAX_CHECKS") or "6"))
        interval_seconds = max(0, int(os.environ.get("ADVISOR_READINESS_INTERVAL_SECONDS") or "1800"))
        code = 2
        for attempt in range(1, checks + 1):
            print(f"PROGRESS {json.dumps({'completed': attempt - 1, 'total': checks, 'unit': '次检查', 'message': '检查数据源是否就绪'}, ensure_ascii=False)}", flush=True)
            code = self.run_command(
                self.python_command(
                    "check_daily_source_readiness.py",
                    "--db-path",
                    str(self.database_root / "analysis_zh_current.sqlite"),
                    "--output-path",
                    str(output),
                )
            )
            if code == 0:
                print(f"PROGRESS {json.dumps({'completed': attempt, 'total': checks, 'unit': '次检查', 'message': '数据源已就绪'}, ensure_ascii=False)}", flush=True)
                self.artifacts.append({"key": "readiness", "path": str(output), "validationStatus": "passed"})
                return 0
            print(f"PROGRESS {json.dumps({'completed': attempt, 'total': checks, 'unit': '次检查', 'message': '本次检查未就绪'}, ensure_ascii=False)}", flush=True)
            if attempt < checks:
                next_check = datetime.now().astimezone() + timedelta(seconds=interval_seconds)
                print(
                    f"[WARN] source not ready; retry {attempt + 1}/{checks} at "
                    f"{next_check.isoformat(timespec='seconds')} after {interval_seconds}s.",
                    flush=True,
                )
                time.sleep(interval_seconds)
        return code

    def device_select(self) -> int:
        output = self.node_run_dir / "device_selection.json"
        config = {}
        config_path = self.workspace_root / "本机配置" / "runtime.local.json"
        if config_path.is_file():
            config = json.loads(config_path.read_text(encoding="utf-8-sig"))
        command = self.python_command(
            "select_daily_update_device.py",
            "--project-root",
            str(self.code_root),
            "--device-type",
            "physical",
            "--output-path",
            str(output),
        )
        adb = self.code_root / "tools" / "platform-tools" / "adb.exe"
        if adb.is_file():
            command.extend(["--adb-path", str(adb)])
        physical = str(config.get("physicalDeviceId") or "")
        if physical:
            command.extend(["--physical-device-id", physical])
        code = self.run_command(command)
        if self.args.dry_run:
            return code
        if code != 0 or not output.is_file():
            return code or 2
        payload = json.loads(output.read_text(encoding="utf-8-sig"))
        selected = payload.get("selected") or {}
        device_id = str(selected.get("deviceId") or selected.get("device_id") or "")
        device_type = str(selected.get("deviceType") or selected.get("device_type") or "")
        adb_path = str(selected.get("adbPath") or selected.get("adb_path") or adb)
        if not device_id:
            return 2
        self.context_updates.update(
            {
                "ADVISOR_DEVICE_ID": device_id,
                "TTFUND_DEVICE_ID": device_id,
                "ADVISOR_ADB_EXE": adb_path,
                "ADVISOR_UNATTENDED": "1",
            }
        )
        self.artifacts.append({"key": "device_selection", "path": str(output), "validationStatus": "passed"})
        self.counters.update(
            {
                "selectedDevice": device_id,
                "deviceMode": "physical_only",
                "fallbackDevice": None,
                "fallbackDeviceReady": None,
            }
        )
        return 0

    def ttfund_incremental(self) -> int:
        device = os.environ.get("ADVISOR_DEVICE_ID") or ""
        if not device and not self.args.dry_run:
            print("[ERROR] ADVISOR_DEVICE_ID is missing.", flush=True)
            return 2
        history_mode = os.environ.get("ADVISOR_HISTORY_MODE") or "latest_only"
        runner = self.program("run_ttfund_incremental_update.ps1")
        command = [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(runner),
            "-DeviceId",
            device,
            "-HistoryMode",
            history_mode,
            "-SkipQuality",
            "-PythonExe",
            self.python,
            "-AdbExe",
            os.environ.get("ADVISOR_ADB_EXE") or "adb",
            "-DirectRebalanceProbeMode",
            "all",
            "-RebalanceStaleDays",
            "1",
            "-RebalanceRollingLimit",
            "0",
            "-AdbFallbackLimit",
            "0",
            "-DetailCooldownDays",
            "7",
            "-DetailRefreshLimit",
            "70",
            "-StoppedDetailCooldownDays",
            "30",
            "-CurrentHoldingCooldownDays",
            "1",
            "-CurrentHoldingRefreshLimit",
            "0",
            "-BenchmarkDetailRepairMode",
            "all_missing_text",
            "-BenchmarkDetailRepairLimit",
            "70",
            "-BenchmarkDetailCooldownDays",
            "7",
            "-QuoteProbeTimeoutSec",
            "20",
            "-RunId",
            self.child_run_id,
        ]
        code = self.run_command(command, env={"ADVISOR_UNATTENDED": "1"})
        if self.args.dry_run:
            return code
        matches = sorted(
            (self.raw_root / "ttfund" / "incremental_update_runs").glob(
                f"*/{self.child_run_id}/summary.json"
            )
        )
        if not matches:
            print(f"[ERROR] exact TTFund summary missing for run_id={self.child_run_id}", flush=True)
            return code or 3
        summary_path = matches[-1]
        try:
            summary = read_json(summary_path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"[ERROR] invalid TTFund summary: {exc}", flush=True)
            return code or 3
        self.artifacts.append(
            {
                "key": "ttfund_incremental_summary",
                "path": str(summary_path),
                "validationStatus": "passed" if code == 0 else "failed",
            }
        )
        state = str(summary.get("state") or "")
        should_collect = bool(summary.get("should_collect"))
        collect_run_id = str(summary.get("collect_run_id") or "").strip()
        self.context_updates.update(
            {
                "TTFUND_INCREMENTAL_RUN_ID": self.child_run_id,
                "TTFUND_INCREMENTAL_SUMMARY_PATH": str(summary_path),
                "TTFUND_COLLECTION_REQUIRED": "1" if should_collect else "0",
                "TTFUND_TARGET_TRADE_DATE": str(summary.get("target_trade_date") or ""),
            }
        )
        if collect_run_id:
            self.context_updates["TTFUND_COLLECT_RUN_ID"] = collect_run_id
        counter_fields = {
            "detailFinalFailed": "detail_final_failed_total",
            "currentHoldingFinalFailed": "current_holding_final_failed_total",
            "historyFinalFailed": "history_final_failed_total",
            "officialCurveMissing": "official_curve_missing_strategy_total",
            "softGapTotal": "soft_gap_total",
        }
        for target, source in counter_fields.items():
            self.counters[target] = int(summary.get(source) or 0)
        official_summary_path = Path(str(summary.get("official_curve_summary_path") or ""))
        if official_summary_path.is_file():
            self.artifacts.append(
                {
                    "key": "ttfund_official_curve_summary",
                    "path": str(official_summary_path),
                    "validationStatus": "passed",
                }
            )
        if state in {"blocked", "failed"}:
            print(f"[ERROR] TTFund incremental state={state}", flush=True)
            return code or 20
        if state not in {"completed", "completed_with_warning"}:
            print(f"[ERROR] unexpected TTFund incremental state={state}", flush=True)
            return code or 3
        if should_collect and not collect_run_id:
            print(
                "[ERROR] TTFund summary requires collection but has no exact collect_run_id.",
                flush=True,
            )
            return code or 3
        if state == "completed_with_warning" or int(summary.get("soft_gap_total") or 0) > 0:
            warning = (
                "天天投顾批次存在可保留的局部缺口，成功数据继续入库；"
                f"soft_gap_total={int(summary.get('soft_gap_total') or 0)}"
            )
            self.warnings.append(warning)
            print(f"[WARN] {warning}", flush=True)
        return code

    def gffunds_performance(self) -> int:
        summary_path = self.node_run_dir / "gffunds_performance_summary.json"
        channel_policy = (
            self.policy.get("channels", {}).get("gffunds", {})
            if isinstance(self.policy.get("channels"), dict)
            else {}
        )
        minimum_ratio = float(channel_policy.get("minimumUsablePerformanceRatio") or 0.95)
        code = self.run_command(
            self.python_command(
                "update_gffunds_performance_curves.py",
                "--workers",
                "8",
                "--retry-failed-rounds",
                "2",
                "--retry-workers",
                "2",
                "--retry-backoff-seconds",
                "5",
                "--min-usable-ratio",
                str(minimum_ratio),
                "--acceptable-business-lag-days",
                "1",
                "--db-path",
                str(self.database_root / "analysis_zh_current.sqlite"),
                "--raw-root",
                str(self.raw_root),
                "--normalized-root",
                str(self.normalized_root),
                "--run-id",
                self.child_run_id,
                "--result-summary-path",
                str(summary_path),
            )
        )
        if not summary_path.is_file():
            return code
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[ERROR] invalid GFFunds performance summary: {exc}", flush=True)
            return 3
        validation_status = "passed" if code == 0 else "failed"
        self.artifacts.append(
            {
                "key": "gffunds_performance_summary",
                "path": str(summary_path),
                "validationStatus": validation_status,
            }
        )
        normalized_path = Path(str(summary.get("normalized_path") or ""))
        if normalized_path.is_file():
            self.artifacts.append(
                {
                    "key": "gffunds_performance_daily",
                    "path": str(normalized_path),
                    "validationStatus": validation_status,
                }
            )
        self.counters.update(
            {
                "strategyTotal": int(summary.get("strategy_total") or 0),
                "freshSuccessTotal": int(summary.get("fresh_success_total") or 0),
                "retryRecoveredTotal": int(summary.get("retry_recovered_total") or 0),
                "reusedLastGoodTotal": int(summary.get("reused_last_good_total") or 0),
                "usableTotal": int(summary.get("usable_total") or 0),
                "failureTotal": int(summary.get("failure_total") or 0),
                "activeLaggingOneDayTotal": int(summary.get("active_lagging_one_day_total") or 0),
                "activeLaggingMoreTotal": int(summary.get("active_lagging_more_total") or 0),
                "dailyRowsTotal": int(summary.get("daily_rows_total") or 0),
            }
        )
        self.watermarks["latestTradeDate"] = summary.get("latest_trade_date")
        actual_run_id = str(summary.get("run_id") or "")
        if actual_run_id != self.child_run_id:
            print(
                f"[ERROR] GFFunds performance run id mismatch: expected={self.child_run_id}, actual={actual_run_id}",
                flush=True,
            )
            return 3
        self.context_updates.update(
            {
                "GFFUNDS_PERFORMANCE_RUN_ID": actual_run_id,
                "GFFUNDS_PERFORMANCE_SUMMARY_PATH": str(summary_path),
            }
        )
        for warning in summary.get("warnings") or []:
            self.warnings.append(str(warning))
        return code

    def gffunds_metadata(self) -> int:
        summary_path = self.node_run_dir / "gffunds_metadata_summary.json"
        code = self.run_command(
            self.python_command(
                "更新广发策略费率基准元数据.py",
                "--workers",
                "6",
                "--stale-days",
                "7",
                "--pdf-on-missing-only",
                "--db-path",
                str(self.database_root / "analysis_zh_current.sqlite"),
                "--run-id",
                self.child_run_id,
                "--result-summary-path",
                str(summary_path),
            )
        )
        if summary_path.is_file():
            summary = read_json(summary_path)
            self.artifacts.append(
                {
                    "key": "gffunds_metadata_summary",
                    "path": str(summary_path),
                    "validationStatus": "passed" if code == 0 else "failed",
                }
            )
            self.counters["selectedStrategyTotal"] = int(summary.get("selected_strategy_total") or 0)
            self.counters["successTotal"] = int(summary.get("success_total") or 0)
            self.counters["failureTotal"] = int(summary.get("failure_total") or 0)
            if int(summary.get("failure_total") or 0) > 0:
                warning = (
                    "广发低频费率/基准补充有局部失败，核心仓位及业绩不受影响；"
                    f"failure_total={int(summary.get('failure_total') or 0)}"
                )
                self.warnings.append(warning)
                print(f"[WARN] {warning}", flush=True)
        return code

    def gffunds_collect(self) -> int:
        result_path = self.node_run_dir / "gffunds_collect_result.json"
        channel_policy = (
            self.policy.get("channels", {}).get("gffunds", {})
            if isinstance(self.policy.get("channels"), dict)
            else {}
        )
        refresh_value = channel_policy.get("latestAdjustmentDetailRefreshDays", 1)
        refresh_days = max(0, int(1 if refresh_value is None else refresh_value))
        code = self.run_command(
            self.python_command(
                "collect_official_apps_public.py",
                "--apps",
                "gffunds",
                "--workers",
                "8",
                "--gffunds-skip-fund-nav",
                "--gffunds-skip-protocol-pdf",
                "--gffunds-latest-adjustment-refresh-days",
                str(refresh_days),
                "--run-id",
                self.child_run_id,
                "--result-summary-path",
                str(result_path),
            )
        )
        if self.args.dry_run:
            return code
        if not result_path.is_file():
            print("[ERROR] GFFunds exact collection result is missing.", flush=True)
            return code or 3
        result = read_json(result_path)
        summary = result.get("gffunds") if isinstance(result.get("gffunds"), dict) else {}
        actual_run_id = str(summary.get("run_id") or "")
        if actual_run_id != self.child_run_id:
            print(
                f"[ERROR] GFFunds collection run id mismatch: expected={self.child_run_id}, actual={actual_run_id}",
                flush=True,
            )
            return code or 3
        output_paths = summary.get("output_paths") if isinstance(summary.get("output_paths"), dict) else {}
        exact_summary = Path(str(output_paths.get("normalized_summary") or ""))
        exact_coverage = Path(str(output_paths.get("exact_coverage") or ""))
        if not exact_summary.is_file() or not exact_coverage.is_file():
            print("[ERROR] GFFunds exact summary or coverage artifact is missing.", flush=True)
            return code or 3
        self.artifacts.extend(
            [
                {
                    "key": "gffunds_collect_result",
                    "path": str(result_path),
                    "validationStatus": "passed" if code == 0 else "failed",
                },
                {
                    "key": "gffunds_exact_summary",
                    "path": str(exact_summary),
                    "validationStatus": "passed" if code == 0 else "failed",
                },
                {
                    "key": "gffunds_exact_coverage",
                    "path": str(exact_coverage),
                    "validationStatus": "passed" if code == 0 else "failed",
                },
            ]
        )
        for target, source in {
            "strategyTotal": "strategy_total",
            "coreStrategyTotal": "core_strategy_total",
            "profitIssueStrategyTotal": "profit_issue_strategy_total",
            "catalogStrategyTotal": "catalog_strategy_total",
            "profitCatalogStrategyTotal": "profit_catalog_strategy_total",
            "catalogNewStrategyTotal": "catalog_new_strategy_total",
            "catalogNewStrategyCollectedTotal": "catalog_new_strategy_collected_total",
            "catalogBatchMissingStrategyTotal": "catalog_batch_missing_strategy_total",
            "dailyRowsTotal": "daily_rows_total",
            "currentHoldingRows": "current_holding_rows",
            "rebalanceEventTotal": "rebalance_event_total",
            "rebalanceFundDeltaTotal": "rebalance_fund_delta_total",
            "adjustmentDetailReusedTotal": "adjustment_detail_reused_total",
            "adjustmentDetailFetchedTotal": "adjustment_detail_fetched_total",
            "adjustmentDetailFallbackTotal": "adjustment_detail_fallback_total",
            "protocolPdfCacheFallbackTotal": "protocol_pdf_cache_fallback_total",
        }.items():
            self.counters[target] = int(summary.get(source) or 0)
        self.context_updates.update(
            {
                "GFFUNDS_COLLECT_RUN_ID": actual_run_id,
                "GFFUNDS_COLLECT_SUMMARY_PATH": str(exact_summary),
                "GFFUNDS_COLLECT_COVERAGE_PATH": str(exact_coverage),
            }
        )
        if not str(summary.get("collection_status") or "").startswith("success"):
            return code or 20
        return code

    def gffunds_gate(self) -> int:
        if self.args.dry_run:
            return 0
        expected_run_id = str(os.environ.get("GFFUNDS_COLLECT_RUN_ID") or "").strip()
        summary = Path(str(os.environ.get("GFFUNDS_COLLECT_SUMMARY_PATH") or ""))
        coverage = Path(str(os.environ.get("GFFUNDS_COLLECT_COVERAGE_PATH") or ""))
        if not expected_run_id or not summary.is_file() or not coverage.is_file():
            print("[ERROR] exact GFFunds batch context is missing.", flush=True)
            return 20
        summary_payload = read_json(summary)
        coverage_payload = read_json(coverage)
        actual_ids = {
            str(summary_payload.get("run_id") or ""),
            str(coverage_payload.get("run_id") or ""),
        }
        if actual_ids != {expected_run_id}:
            print(
                f"[ERROR] GFFunds gate provenance mismatch: expected={expected_run_id}, actual={sorted(actual_ids)}",
                flush=True,
            )
            return 20
        channel_policy = (
            self.policy.get("channels", {}).get("gffunds", {})
            if isinstance(self.policy.get("channels"), dict)
            else {}
        )
        required_checks = list(
            channel_policy.get("requiredCoverageChecks")
            or [
                "strategy_master_ok",
                "daily_performance_ok",
                "fund_level_position_ok",
                "rebalance_event_ok",
                "rebalance_fund_delta_ok",
            ]
        )
        optional_checks = list(channel_policy.get("optionalCoverageChecks") or [])
        failed_checks = [key for key in required_checks if coverage_payload.get(key) is not True]
        optional_failures = [key for key in optional_checks if coverage_payload.get(key) is not True]
        current_total = int(summary_payload.get("strategy_total") or 0)
        retention_ratio = float(channel_policy.get("minimumInventoryRetentionRatio") or 0.9)
        core_coverage_ratio = float(channel_policy.get("minimumCoreStrategyCoverageRatio") or 0.95)
        summary_root = self.normalized_root / "gffunds" / "collection_summary"
        previous_total = previous_gffunds_collection_total(summary_root, summary)
        inventory_ok = (
            previous_total <= 0
            or current_total >= previous_total * retention_ratio
        )
        accepted_inventory_drop = (
            inventory_ok and previous_total > 0 and current_total < previous_total
        )
        if not inventory_ok:
            failed_checks.append("strategy_inventory_retention")
        core_ratios, core_ratio_failures = assess_gffunds_core_coverage(
            summary_payload,
            core_coverage_ratio,
        )
        failed_checks.extend(core_ratio_failures)
        catalog_failures, catalog_metrics = assess_strategy_catalog_batch(
            summary_payload,
            channel_policy,
        )
        failed_checks.extend(catalog_failures)
        failed_checks = list(dict.fromkeys(failed_checks))
        accepted_core_gaps = [
            key
            for key, ratio in core_ratios.items()
            if key not in core_ratio_failures and ratio < 1.0
        ]
        adjustment_fallback_total = int(
            summary_payload.get("adjustment_detail_fallback_total") or 0
        )
        gate_path = self.node_run_dir / "gffunds_gate.json"
        gate_payload = {
            "runId": expected_run_id,
            "summaryPath": str(summary),
            "coveragePath": str(coverage),
            "requiredChecks": required_checks,
            "failedRequiredChecks": failed_checks,
            "optionalChecks": optional_checks,
            "failedOptionalChecks": optional_failures,
            "strategyTotal": current_total,
            "previousStrategyTotal": previous_total,
            "minimumInventoryRetentionRatio": retention_ratio,
            "inventoryRetentionPassed": inventory_ok,
            "acceptedInventoryDrop": accepted_inventory_drop,
            "minimumCoreStrategyCoverageRatio": core_coverage_ratio,
            "coreStrategyCoverageRatios": core_ratios,
            "acceptedCoreCoverageGaps": accepted_core_gaps,
            "adjustmentDetailFallbackTotal": adjustment_fallback_total,
            **catalog_metrics,
        }
        atomic_json(gate_path, gate_payload)
        self.artifacts.extend(
            [
                {"key": "gffunds_summary", "path": str(summary), "validationStatus": "passed"},
                {"key": "gffunds_coverage", "path": str(coverage), "validationStatus": "passed" if not failed_checks else "failed"},
                {"key": "gffunds_gate", "path": str(gate_path), "validationStatus": "passed" if not failed_checks else "failed"},
            ]
        )
        if failed_checks:
            print(f"[ERROR] GFFunds required gate checks failed: {failed_checks}", flush=True)
            return 20
        if optional_failures:
            warning = f"广发可选覆盖项未满足，不阻断核心数据：{optional_failures}"
            self.warnings.append(warning)
            print(f"[WARN] {warning}", flush=True)
        if summary_payload.get("catalog_complete") is not True:
            warning = (
                "广发策略目录本轮未能证明完整，已保留并采集当前返回策略；"
                f"catalog_total={int(summary_payload.get('catalog_strategy_total') or 0)}"
            )
            self.warnings.append(warning)
            print(f"[WARN] {warning}", flush=True)
        if accepted_inventory_drop:
            warning = (
                "广发策略数量低于上一完整批次但仍达到保留门槛，"
                f"previous={previous_total}, current={current_total}"
            )
            self.warnings.append(warning)
            print(f"[WARN] {warning}", flush=True)
        if accepted_core_gaps:
            warning = (
                "广发核心策略覆盖达到门槛但未满覆盖，不阻断已验证数据："
                f"{accepted_core_gaps}"
            )
            self.warnings.append(warning)
            print(f"[WARN] {warning}", flush=True)
        if adjustment_fallback_total:
            warning = (
                "广发最新调仓明细刷新失败时使用了已验证缓存，"
                f"fallback_total={adjustment_fallback_total}"
            )
            self.warnings.append(warning)
            print(f"[WARN] {warning}", flush=True)
        self.context_updates.update(
            {
                "GFFUNDS_GATE_PASSED": "1",
                "GFFUNDS_GATE_RUN_ID": expected_run_id,
            }
        )
        self.counters.update(
            {
                "strategyTotal": current_total,
                "previousStrategyTotal": previous_total,
                "failedOptionalChecks": len(optional_failures),
                "acceptedInventoryDrop": int(accepted_inventory_drop),
                "acceptedCoreCoverageGaps": len(accepted_core_gaps),
                "adjustmentDetailFallbackTotal": adjustment_fallback_total,
                "catalogNewStrategyTotal": catalog_metrics["catalogNewStrategyTotal"],
                "catalogNewStrategyCollectedTotal": catalog_metrics[
                    "catalogNewStrategyCollectedTotal"
                ],
                "catalogBatchMissingStrategyTotal": catalog_metrics[
                    "catalogBatchMissingStrategyTotal"
                ],
                "performanceStrategyCoveragePermille": round(
                    core_ratios["performanceStrategyRatio"] * 1000
                ),
                "rebalanceResponseCoveragePermille": round(
                    core_ratios["rebalanceResponseRatio"] * 1000
                ),
                "latestHoldingStrategyCoveragePermille": round(
                    core_ratios["latestHoldingStrategyRatio"] * 1000
                ),
            }
        )
        print("[DONE] GFFunds exact batch and required coverage checks passed.", flush=True)
        return 0

    def gfsec_fima_collect(self) -> int:
        result_path = self.node_run_dir / "gfsec_fima_collect_result.json"
        channel_policy = (
            self.policy.get("channels", {}).get("gfsec_fima", {})
            if isinstance(self.policy.get("channels"), dict)
            else {}
        )
        daily_page_size = max(1, int(channel_policy.get("dailyPerformancePageSize") or 400))
        code = self.run_command(
            self.python_command(
                "collect_official_apps_public.py",
                "--apps",
                "gfsec_fima",
                "--workers",
                "8",
                "--gfsec-fima-daily-page-size",
                str(daily_page_size),
                "--run-id",
                self.child_run_id,
                "--result-summary-path",
                str(result_path),
            )
        )
        if self.args.dry_run:
            return code
        if not result_path.is_file():
            print("[ERROR] GFSEC FIMA collection result is missing.", flush=True)
            return code or 3
        result = read_json(result_path)
        summary = result.get("gfsec_fima") if isinstance(result.get("gfsec_fima"), dict) else {}
        actual_run_id = str(summary.get("run_id") or "")
        if actual_run_id != self.child_run_id:
            print(
                f"[ERROR] GFSEC FIMA run id mismatch: expected={self.child_run_id}, actual={actual_run_id}",
                flush=True,
            )
            return code or 3
        output_paths = summary.get("output_paths") if isinstance(summary.get("output_paths"), dict) else {}
        exact_summary = Path(str(output_paths.get("normalized_summary") or ""))
        exact_coverage = Path(str(output_paths.get("exact_coverage") or ""))
        if not exact_summary.is_file() or not exact_coverage.is_file():
            print("[ERROR] GFSEC FIMA exact summary or coverage artifact is missing.", flush=True)
            return code or 3
        validation_status = "passed" if code == 0 else "failed"
        self.artifacts.extend(
            [
                {"key": "gfsec_fima_collect_result", "path": str(result_path), "validationStatus": validation_status},
                {"key": "gfsec_fima_exact_summary", "path": str(exact_summary), "validationStatus": validation_status},
                {"key": "gfsec_fima_exact_coverage", "path": str(exact_coverage), "validationStatus": validation_status},
            ]
        )
        counter_map = {
            "plannedPortfolioTotal": "planned_portfolio_total",
            "processedPortfolioTotal": "processed_portfolio_total",
            "successPortfolioTotal": "success_portfolio_total",
            "failurePortfolioTotal": "failure_portfolio_total",
            "plannedStrategyTotal": "planned_product_total",
            "catalogStrategyTotal": "catalog_strategy_total",
            "catalogNewStrategyTotal": "catalog_new_strategy_total",
            "catalogNewStrategyCollectedTotal": "catalog_new_strategy_collected_total",
            "catalogBatchMissingStrategyTotal": "catalog_batch_missing_strategy_total",
            "processedStrategyTotal": "processed_product_total",
            "successTotal": "success_product_total",
            "failureTotal": "failure_product_total",
            "currentHoldingRows": "current_holding_rows",
            "currentHoldingStrategyTotal": "current_holding_strategy_total",
            "dailyPerformanceRows": "daily_performance_rows",
            "rebalanceEventTotal": "rebalance_event_total",
            "rebalanceFundDeltaTotal": "rebalance_fund_delta_total",
            "alternativeFundExcludedTotal": "alternative_fund_excluded_total",
        }
        for target, source in counter_map.items():
            self.counters[target] = int(summary.get(source) or 0)
        self.context_updates.update(
            {
                "GFSEC_FIMA_COLLECT_RUN_ID": actual_run_id,
                "GFSEC_FIMA_COLLECT_SUMMARY_PATH": str(exact_summary),
                "GFSEC_FIMA_COLLECT_COVERAGE_PATH": str(exact_coverage),
            }
        )
        if not str(summary.get("collection_status") or "").startswith("success"):
            return code or 20
        return code

    def gfsec_fima_gate(self) -> int:
        if self.args.dry_run:
            return 0
        expected_run_id = str(os.environ.get("GFSEC_FIMA_COLLECT_RUN_ID") or "").strip()
        summary_path = Path(str(os.environ.get("GFSEC_FIMA_COLLECT_SUMMARY_PATH") or ""))
        coverage_path = Path(str(os.environ.get("GFSEC_FIMA_COLLECT_COVERAGE_PATH") or ""))
        if not expected_run_id or not summary_path.is_file() or not coverage_path.is_file():
            print("[ERROR] exact GFSEC FIMA batch context is missing.", flush=True)
            return 20
        summary = read_json(summary_path)
        coverage = read_json(coverage_path)
        actual_ids = {str(summary.get("run_id") or ""), str(coverage.get("run_id") or "")}
        if actual_ids != {expected_run_id}:
            print(
                f"[ERROR] GFSEC FIMA gate provenance mismatch: expected={expected_run_id}, actual={sorted(actual_ids)}",
                flush=True,
            )
            return 20
        channel_policy = (
            self.policy.get("channels", {}).get("gfsec_fima", {})
            if isinstance(self.policy.get("channels"), dict)
            else {}
        )
        required_checks = list(
            channel_policy.get("requiredCoverageChecks")
            or [
                "strategy_master_ok",
                "daily_performance_ok",
                "fund_level_position_ok",
                "rebalance_event_ok",
                "rebalance_fund_delta_ok",
            ]
        )
        failed_checks = [key for key in required_checks if coverage.get(key) is not True]
        planned_portfolios = int(summary.get("planned_portfolio_total") or 0)
        processed_portfolios = int(summary.get("processed_portfolio_total") or 0)
        success_portfolios = int(summary.get("success_portfolio_total") or 0)
        failure_portfolios = int(summary.get("failure_portfolio_total") or 0)
        planned_products = int(summary.get("planned_product_total") or 0)
        processed_products = int(summary.get("processed_product_total") or 0)
        success_products = int(summary.get("success_product_total") or 0)
        failure_products = int(summary.get("failure_product_total") or 0)
        print(
            "PROGRESS "
            + json.dumps(
                {
                    "completed": processed_portfolios,
                    "total": planned_portfolios,
                    "unit": "个底层组合",
                    "message": f"验收 成功={success_portfolios} 失败={failure_portfolios}",
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        if planned_portfolios <= 0 or processed_portfolios != planned_portfolios:
            failed_checks.append("portfolio_plan_processed_invariant")
        if success_portfolios + failure_portfolios != processed_portfolios:
            failed_checks.append("portfolio_success_failure_invariant")
        if planned_products <= 0 or processed_products != planned_products:
            failed_checks.append("product_plan_processed_invariant")
        if success_products + failure_products != processed_products:
            failed_checks.append("product_success_failure_invariant")
        minimum_ratio = float(channel_policy.get("minimumPortfolioCoverageRatio") or 0.95)
        coverage_ratio = success_portfolios / planned_portfolios if planned_portfolios else 0.0
        if coverage_ratio < minimum_ratio:
            failed_checks.append("portfolio_success_coverage")
        retention_ratio = float(channel_policy.get("minimumInventoryRetentionRatio") or 0.9)
        previous_total = previous_gfsec_fima_product_total(
            self.normalized_root / "gfsec_fima" / "collection_summary",
            summary_path,
        )
        inventory_ok = previous_total <= 0 or planned_products >= previous_total * retention_ratio
        if not inventory_ok:
            failed_checks.append("strategy_inventory_retention")
        catalog_failures, catalog_metrics = assess_strategy_catalog_batch(
            summary,
            channel_policy,
        )
        failed_checks.extend(catalog_failures)
        failed_checks = list(dict.fromkeys(failed_checks))
        gate_path = self.node_run_dir / "gfsec_fima_gate.json"
        gate_payload = {
            "runId": expected_run_id,
            "summaryPath": str(summary_path),
            "coveragePath": str(coverage_path),
            "requiredChecks": required_checks,
            "failedRequiredChecks": failed_checks,
            "plannedPortfolioTotal": planned_portfolios,
            "processedPortfolioTotal": processed_portfolios,
            "successPortfolioTotal": success_portfolios,
            "failurePortfolioTotal": failure_portfolios,
            "minimumPortfolioCoverageRatio": minimum_ratio,
            "portfolioSuccessCoverageRatio": coverage_ratio,
            "plannedProductTotal": planned_products,
            "processedProductTotal": processed_products,
            "successProductTotal": success_products,
            "failureProductTotal": failure_products,
            "previousProductTotal": previous_total,
            "minimumInventoryRetentionRatio": retention_ratio,
            "inventoryRetentionPassed": inventory_ok,
            "officialRebalanceHistoryStatus": summary.get("official_rebalance_history_status"),
            **catalog_metrics,
        }
        atomic_json(gate_path, gate_payload)
        validation_status = "failed" if failed_checks else "passed"
        self.artifacts.extend(
            [
                {"key": "gfsec_fima_summary", "path": str(summary_path), "validationStatus": "passed"},
                {"key": "gfsec_fima_coverage", "path": str(coverage_path), "validationStatus": validation_status},
                {"key": "gfsec_fima_gate", "path": str(gate_path), "validationStatus": validation_status},
            ]
        )
        self.counters.update(
            {
                "plannedPortfolioTotal": planned_portfolios,
                "processedPortfolioTotal": processed_portfolios,
                "successPortfolioTotal": success_portfolios,
                "failurePortfolioTotal": failure_portfolios,
                "plannedStrategyTotal": planned_products,
                "processedStrategyTotal": processed_products,
                "successTotal": success_products,
                "failureTotal": failure_products,
                "portfolioCoveragePermille": round(coverage_ratio * 1000),
                "previousStrategyTotal": previous_total,
                "catalogNewStrategyTotal": catalog_metrics["catalogNewStrategyTotal"],
                "catalogNewStrategyCollectedTotal": catalog_metrics[
                    "catalogNewStrategyCollectedTotal"
                ],
                "catalogBatchMissingStrategyTotal": catalog_metrics[
                    "catalogBatchMissingStrategyTotal"
                ],
            }
        )
        if failed_checks:
            print(f"[ERROR] GFSEC FIMA required gate checks failed: {failed_checks}", flush=True)
            return 20
        self.context_updates.update(
            {
                "GFSEC_FIMA_GATE_PASSED": "1",
                "GFSEC_FIMA_GATE_RUN_ID": expected_run_id,
            }
        )
        if summary.get("official_rebalance_history_status") == "official_endpoint_checked_no_events":
            warning = "广发证券财富管家官方调仓接口已逐组合核验，本批次仍无调仓事件可入库。"
            self.warnings.append(warning)
            print(f"[WARN] {warning}", flush=True)
        if summary.get("catalog_complete") is not True:
            warning = (
                "广发证券财富管家策略目录或目标盈期次目录本轮未能证明完整，"
                f"已保留并采集当前返回产品；catalog_total={int(summary.get('catalog_strategy_total') or 0)}"
            )
            self.warnings.append(warning)
            print(f"[WARN] {warning}", flush=True)
        print("[DONE] GFSEC FIMA exact batch and coverage checks passed.", flush=True)
        return 0

    def gfsec_fima_load(self) -> int:
        if self.args.dry_run:
            return 0
        if os.environ.get("GFSEC_FIMA_GATE_PASSED") != "1":
            print("[ERROR] GFSEC FIMA load requires a passed exact batch gate.", flush=True)
            return 20
        collect_run_id = str(os.environ.get("GFSEC_FIMA_COLLECT_RUN_ID") or "").strip()
        gate_run_id = str(os.environ.get("GFSEC_FIMA_GATE_RUN_ID") or "").strip()
        if not collect_run_id or collect_run_id != gate_run_id:
            print(
                f"[ERROR] GFSEC FIMA load provenance mismatch: collect={collect_run_id}, gate={gate_run_id}",
                flush=True,
            )
            return 20
        database = self.database_root / "analysis_zh_current.sqlite"
        result_path = self.node_run_dir / "gfsec_fima_incremental_load.json"
        summary_path = Path(str(os.environ.get("GFSEC_FIMA_COLLECT_SUMMARY_PATH") or ""))
        if not summary_path.is_file():
            print("[ERROR] GFSEC FIMA exact summary is missing before load.", flush=True)
            return 20
        code = self.run_command(
            self.python_command(
                "load_analysis_zh_current_sqlite.py",
                "--db-path",
                str(database),
                "--schema-path",
                str(self.code_root / "schemas" / "analysis_zh_current.sql"),
                "--keep-existing-db",
                "--channels",
                "gfsec_fima",
                "--strategy-catalog-summary",
                str(summary_path),
            )
        )
        if code != 0:
            self.context_updates["GFSEC_FIMA_LOAD_FAILED"] = "1"
            print(
                f"[ERROR] GFSEC FIMA transactional channel load failed; existing channel data was retained: exit={code}",
                flush=True,
            )
            return code or 20
        summary = read_json(summary_path)
        catalog_ids = {
            str(value or "").strip()
            for value in summary.get("catalog_strategy_ids") or []
            if str(value or "").strip()
        }
        new_ids = {
            str(value or "").strip()
            for value in summary.get("catalog_new_strategy_ids") or []
            if str(value or "").strip()
        }
        counts: dict[str, int] = {}
        with sqlite3.connect(database) as connection:
            for key, table in {
                "strategyTotal": "策略信息",
                "dailyRowsTotal": "策略日度业绩",
                "currentHoldingRows": "策略当前持仓",
                "rebalanceEventTotal": "策略调仓事件",
                "rebalanceFundDeltaTotal": "策略调仓明细",
            }.items():
                counts[key] = int(
                    connection.execute(
                        f'SELECT COUNT(*) FROM "{table}" WHERE "渠道ID"=?',
                        ("gfsec_fima",),
                    ).fetchone()[0]
                )
            loaded_ids = {
                str(row[0] or "").strip()
                for row in connection.execute(
                    'SELECT "渠道策略ID" FROM "策略信息" WHERE "渠道ID"=?',
                    ("gfsec_fima",),
                ).fetchall()
                if str(row[0] or "").strip()
            }
        missing_catalog_ids = sorted(catalog_ids - loaded_ids)
        missing_new_ids = sorted(new_ids - loaded_ids)
        expected_strategies = int(summary.get("strategy_total") or 0)
        if expected_strategies <= 0 or counts["strategyTotal"] != expected_strategies:
            self.context_updates["GFSEC_FIMA_LOAD_FAILED"] = "1"
            print(
                f"[ERROR] GFSEC FIMA post-load strategy count mismatch: expected={expected_strategies}, actual={counts['strategyTotal']}",
                flush=True,
            )
            return 20
        if missing_catalog_ids or missing_new_ids:
            self.context_updates["GFSEC_FIMA_LOAD_FAILED"] = "1"
            print(
                "[ERROR] GFSEC FIMA post-load catalog ID mismatch: "
                f"missing_catalog={missing_catalog_ids}, missing_new={missing_new_ids}",
                flush=True,
            )
            return 20
        catalog_validation = {
            "catalogStrategyTotal": len(catalog_ids),
            "catalogNewStrategyTotal": len(new_ids),
            "loadedCatalogStrategyTotal": len(catalog_ids & loaded_ids),
            "loadedNewStrategyTotal": len(new_ids & loaded_ids),
            "missingCatalogStrategyIds": missing_catalog_ids,
            "missingNewStrategyIds": missing_new_ids,
            "passed": True,
        }
        atomic_json(
            result_path,
            {
                "channelId": "gfsec_fima",
                "runId": collect_run_id,
                "database": str(database),
                "counts": counts,
                "strategyCatalogLoadValidation": catalog_validation,
                "loadedAt": now_text(),
            },
        )
        self.context_updates["GFSEC_FIMA_LOADED"] = "1"
        self.artifacts.append(
            {
                "key": "gfsec_fima_incremental_load",
                "path": str(result_path),
                "validationStatus": "passed",
            }
        )
        self.counters.update(counts)
        self.counters["loadedNewStrategyTotal"] = len(new_ids & loaded_ids)
        print(
            f"[DONE] GFSEC FIMA loaded: strategies={counts['strategyTotal']} "
            f"holdings={counts['currentHoldingRows']} daily={counts['dailyRowsTotal']} "
            f"rebalances={counts['rebalanceEventTotal']}",
            flush=True,
        )
        return 0

    def southern_collect(self) -> int:
        result_path = self.node_run_dir / "southern_collect_result.json"
        collector = (
            self.code_root
            / "节点脚本"
            / "03_南方基金"
            / "01_目录与登录态采集"
            / "src"
            / "southern_daily_update.py"
        )
        command = [
            self.python,
            "-u",
            "-X",
            "utf8",
            str(collector),
            "--workspace-root",
            str(self.workspace_root),
            "--code-root",
            str(self.code_root),
            "--normalized-root",
            str(self.normalized_root),
            "--raw-root",
            str(self.raw_root),
            "--node-run-dir",
            str(self.node_run_dir / "collector"),
            "--run-id",
            self.child_run_id,
            "--daily-run-id",
            self.args.run_id,
            "--db-path",
            str(self.database_root / "analysis_zh_current.sqlite"),
            "--login-wait-seconds",
            str(
                max(
                    15,
                    min(
                        720,
                        int(
                            (
                                self.policy.get("channels", {}).get("southern", {})
                                if isinstance(self.policy.get("channels"), dict)
                                else {}
                            ).get("loginWaitSeconds")
                            or 60
                        ),
                    ),
                )
            ),
            "--result-path",
            str(result_path),
        ]
        replay_summary = str(os.environ.get("SOUTHERN_COLLECTOR_SUMMARY") or "").strip()
        replay_inventory = str(os.environ.get("SOUTHERN_INVENTORY_PATH") or "").strip()
        if replay_summary:
            command.extend(["--collector-summary", replay_summary])
        if replay_inventory:
            command.extend(["--inventory", replay_inventory])
        if self.args.dry_run:
            command.append("--dry-run")
        code = self.run_command(command)
        if self.args.dry_run:
            return code
        if code != 0 or not result_path.is_file():
            print(
                f"[ERROR] 南方基金采集或专项稽核失败，旧渠道数据保持不变：exit={code}",
                flush=True,
            )
            return code or 20
        result = read_json(result_path)
        run_id = str(result.get("run_id") or "").strip()
        summary_path = Path(str(result.get("summary_path") or ""))
        audit_path = Path(str(result.get("audit_report_path") or ""))
        if run_id != self.child_run_id or not summary_path.is_file() or not audit_path.is_file():
            print("[ERROR] 南方基金采集批次血缘或验收产物不完整。", flush=True)
            return 20
        self.context_updates.update(
            {
                "SOUTHERN_COLLECT_RUN_ID": run_id,
                "SOUTHERN_COLLECT_SUMMARY_PATH": str(summary_path),
                "SOUTHERN_COLLECT_AUDIT_PATH": str(audit_path),
            }
        )
        self.artifacts.extend(
            [
                {"key": "southern_collect_result", "path": str(result_path), "validationStatus": "passed"},
                {"key": "southern_collection_summary", "path": str(summary_path), "validationStatus": "passed"},
                {"key": "southern_isolated_audit", "path": str(audit_path), "validationStatus": "passed"},
            ]
        )
        counts = result.get("counts") if isinstance(result.get("counts"), dict) else {}
        coverage = result.get("coverage") if isinstance(result.get("coverage"), dict) else {}
        self.counters.update(
            {
                "strategyTotal": int(result.get("strategy_total") or 0),
                "catalogNewStrategyTotal": int(result.get("catalog_new_strategy_total") or 0),
                "dailyPerformanceRows": int(counts.get("strategy_performance_daily") or 0),
                "currentHoldingRows": int(counts.get("strategy_fund_snapshot") or 0),
                "historicalHoldingRows": int(counts.get("strategy_fund_snapshot_history") or 0),
                "rebalanceEventTotal": int(counts.get("strategy_rebalance_event") or 0),
                "exactBenchmarkStrategyTotal": int(coverage.get("benchmark_exact_split") or 0),
            }
        )
        latest_date = str(result.get("source_latest_nav_date") or "").strip()
        if latest_date:
            self.watermarks["南方基金源端业绩最新日期"] = latest_date
        print(
            f"[DONE] 南方基金完整批次生成：策略={self.counters['strategyTotal']} "
            f"日度={self.counters['dailyPerformanceRows']} 历史仓位={self.counters['historicalHoldingRows']}",
            flush=True,
        )
        return 0

    def southern_gate(self) -> int:
        if self.args.dry_run:
            return 0
        run_id = str(os.environ.get("SOUTHERN_COLLECT_RUN_ID") or "").strip()
        summary_path = Path(str(os.environ.get("SOUTHERN_COLLECT_SUMMARY_PATH") or ""))
        audit_path = Path(str(os.environ.get("SOUTHERN_COLLECT_AUDIT_PATH") or ""))
        if not run_id or not summary_path.is_file() or not audit_path.is_file():
            print("[ERROR] 南方基金批次验收缺少精确采集上下文。", flush=True)
            return 20
        summary = read_json(summary_path)
        audit = read_json(audit_path)
        policy = (
            self.policy.get("channels", {}).get("southern", {})
            if isinstance(self.policy.get("channels"), dict)
            else {}
        )
        strategy_total = int(summary.get("strategy_total") or 0)
        coverage = summary.get("coverage") if isinstance(summary.get("coverage"), dict) else {}
        ratios = {
            "performance": int(coverage.get("performance_with_rows") or 0) / strategy_total if strategy_total else 0.0,
            "currentHolding": int(coverage.get("current_position_complete") or 0) / strategy_total if strategy_total else 0.0,
            "historicalHolding": int(coverage.get("historical_position_complete") or 0) / strategy_total if strategy_total else 0.0,
            "benchmark": int(coverage.get("benchmark_exact_split") or 0) / strategy_total if strategy_total else 0.0,
        }
        thresholds = {
            "performance": float(policy.get("minimumPerformanceStrategyRatio") or 1.0),
            "currentHolding": float(policy.get("minimumCurrentHoldingRatio") or 1.0),
            "historicalHolding": float(policy.get("minimumHistoricalHoldingRatio") or 1.0),
            "benchmark": float(policy.get("minimumExactBenchmarkRatio") or 0.94),
        }
        failures: list[str] = []
        if summary.get("catalog_discovery_complete") is not True:
            failures.append("catalog_discovery_complete")
        if summary.get("catalog_batch_closed") is not True:
            failures.append("catalog_batch_closed")
        if audit.get("status") != "passed" or int(audit.get("error_count") or 0):
            failures.append("isolated_data_audit")
        for key, ratio in ratios.items():
            if ratio + 1e-9 < thresholds[key]:
                failures.append(f"{key}_coverage")
        previous_total = 0
        database = self.database_root / "analysis_zh_current.sqlite"
        if database.is_file():
            try:
                with sqlite3.connect(database, timeout=60) as connection:
                    previous_total = int(
                        connection.execute(
                            'SELECT COUNT(*) FROM "策略信息" WHERE "渠道ID"=?',
                            ("southern",),
                        ).fetchone()[0]
                    )
            except sqlite3.Error:
                previous_total = 0
        retention = float(policy.get("minimumInventoryRetentionRatio") or 0.95)
        if previous_total and strategy_total < previous_total * retention:
            failures.append("strategy_inventory_retention")
        failures = list(dict.fromkeys(failures))
        gate_path = self.node_run_dir / "southern_gate.json"
        atomic_json(
            gate_path,
            {
                "runId": run_id,
                "summaryPath": str(summary_path),
                "auditPath": str(audit_path),
                "strategyTotal": strategy_total,
                "previousStrategyTotal": previous_total,
                "coverageRatios": ratios,
                "coverageThresholds": thresholds,
                "sourceLatestNavDate": summary.get("source_latest_nav_date"),
                "benchmarkMissingStrategyIds": summary.get("benchmark_missing_strategy_ids") or [],
                "failedRequiredChecks": failures,
            },
        )
        self.artifacts.append(
            {"key": "southern_gate", "path": str(gate_path), "validationStatus": "failed" if failures else "passed"}
        )
        self.counters.update(
            {
                "strategyTotal": strategy_total,
                "previousStrategyTotal": previous_total,
                "performanceCoveragePermille": round(ratios["performance"] * 1000),
                "currentHoldingCoveragePermille": round(ratios["currentHolding"] * 1000),
                "historicalHoldingCoveragePermille": round(ratios["historicalHolding"] * 1000),
                "benchmarkCoveragePermille": round(ratios["benchmark"] * 1000),
            }
        )
        if failures:
            print(f"[ERROR] 南方基金必需验收项失败：{failures}", flush=True)
            return 20
        self.context_updates.update({"SOUTHERN_GATE_PASSED": "1", "SOUTHERN_GATE_RUN_ID": run_id})
        missing_benchmark = summary.get("benchmark_missing_strategy_ids") or []
        if missing_benchmark:
            warning = f"南方基金官方源端未披露精确基准的策略保持缺失：{missing_benchmark}。"
            self.warnings.append(warning)
            print(f"[WARN] {warning}", flush=True)
        print("[DONE] 南方基金精确批次、仓位闭合和目录闭合验收通过。", flush=True)
        return 0

    def southern_load(self) -> int:
        if self.args.dry_run:
            return 0
        if os.environ.get("SOUTHERN_GATE_PASSED") != "1":
            print("[ERROR] 南方基金入库要求同批次 gate 已通过。", flush=True)
            return 20
        collect_run_id = str(os.environ.get("SOUTHERN_COLLECT_RUN_ID") or "").strip()
        gate_run_id = str(os.environ.get("SOUTHERN_GATE_RUN_ID") or "").strip()
        summary_path = Path(str(os.environ.get("SOUTHERN_COLLECT_SUMMARY_PATH") or ""))
        if not collect_run_id or collect_run_id != gate_run_id or not summary_path.is_file():
            print("[ERROR] 南方基金入库批次血缘不一致。", flush=True)
            return 20
        database = self.database_root / "analysis_zh_current.sqlite"
        environment = dict(os.environ)
        environment["SOUTHERN_COLLECT_RUN_ID"] = collect_run_id
        code = self.run_command(
            self.python_command(
                "load_analysis_zh_current_sqlite.py",
                "--db-path",
                str(database),
                "--schema-path",
                str(self.code_root / "schemas" / "analysis_zh_current.sql"),
                "--keep-existing-db",
                "--normalized-root",
                str(self.normalized_root),
                "--channels",
                "southern",
                "--strategy-catalog-summary",
                str(summary_path),
            ),
            env=environment,
        )
        if code != 0:
            print(f"[ERROR] 南方基金事务入库失败，旧渠道数据已由事务回滚保留：exit={code}", flush=True)
            return code or 20
        summary = read_json(summary_path)
        expected_counts = summary.get("counts") if isinstance(summary.get("counts"), dict) else {}
        expected = {
            "strategyTotal": int(summary.get("strategy_total") or 0),
            "dailyRows": int(expected_counts.get("strategy_performance_daily") or 0),
            "currentHoldingRows": int(expected_counts.get("strategy_fund_snapshot") or 0),
            "historicalHoldingRows": int(expected_counts.get("strategy_fund_snapshot_history") or 0),
            "rebalanceEventRows": int(expected_counts.get("strategy_rebalance_event") or 0),
            "benchmarkComponentRows": sum(
                1
                for path in (self.normalized_root / "southern" / "strategy_benchmark" / collect_run_id).glob("*.jsonl")
                for line in path.open("r", encoding="utf-8-sig")
                if line.strip()
                for _ in (json.loads(line).get("benchmark_components") or [])
            ),
        }
        with sqlite3.connect(database, timeout=120) as connection:
            actual = {
                "strategyTotal": int(connection.execute('SELECT COUNT(*) FROM "策略信息" WHERE "渠道ID"=?', ("southern",)).fetchone()[0]),
                "dailyRows": int(connection.execute('SELECT COUNT(*) FROM "策略日度业绩" WHERE "渠道ID"=?', ("southern",)).fetchone()[0]),
                "currentHoldingRows": int(connection.execute('SELECT COUNT(*) FROM "策略当前持仓" WHERE "渠道ID"=?', ("southern",)).fetchone()[0]),
                "historicalHoldingRows": int(connection.execute('SELECT COUNT(*) FROM "策略历史持仓" WHERE "渠道ID"=?', ("southern",)).fetchone()[0]),
                "rebalanceEventRows": int(connection.execute('SELECT COUNT(*) FROM "策略调仓事件" WHERE "渠道ID"=?', ("southern",)).fetchone()[0]),
                "benchmarkComponentRows": int(connection.execute('SELECT COUNT(*) FROM "策略业绩基准成分" WHERE "渠道ID"=?', ("southern",)).fetchone()[0]),
            }
            latest_row = connection.execute(
                'SELECT MAX("交易日期"), COUNT(DISTINCT CASE WHEN "交易日期"=(SELECT MAX("交易日期") FROM "策略日度业绩" WHERE "渠道ID"=?) THEN "渠道策略ID" END) FROM "策略日度业绩" WHERE "渠道ID"=?',
                ("southern", "southern"),
            ).fetchone()
        failures = [key for key, value in expected.items() if actual.get(key) != value]
        if str(latest_row[0] or "") != str(summary.get("source_latest_nav_date") or ""):
            failures.append("source_latest_nav_date")
        result_path = self.node_run_dir / "southern_incremental_load.json"
        atomic_json(
            result_path,
            {
                "channelId": "southern",
                "runId": collect_run_id,
                "database": str(database),
                "expected": expected,
                "actual": actual,
                "latestNavDate": latest_row[0],
                "latestNavStrategyTotal": int(latest_row[1] or 0),
                "failedChecks": failures,
                "loadedAt": now_text(),
            },
        )
        self.artifacts.append(
            {"key": "southern_incremental_load", "path": str(result_path), "validationStatus": "failed" if failures else "passed"}
        )
        self.counters.update(actual)
        if failures:
            print(f"[ERROR] 南方基金入库后闭环核对失败：{failures}", flush=True)
            return 20
        self.context_updates["SOUTHERN_LOADED"] = "1"
        print(
            f"[DONE] 南方基金事务入库完成：策略={actual['strategyTotal']} "
            f"日度={actual['dailyRows']} 历史仓位={actual['historicalHoldingRows']}",
            flush=True,
        )
        return 0

    def qieman_collect(self) -> int:
        result_path = self.node_run_dir / "qieman_collect_result.json"
        collector = (
            self.code_root
            / "节点脚本"
            / "03_且慢"
            / "01_目录增量与新策略采集"
            / "src"
            / "qieman_daily_update.py"
        )
        dpapi_input = Path(
            os.environ.get("QIEMAN_DPAPI_INPUT")
            or self.workspace_root / "本机配置" / "qieman_stargate_api_key.dpapi"
        ).resolve()
        if not self.args.dry_run and not dpapi_input.is_file():
            print(
                "[ERROR] 且慢 DPAPI API key 文件缺失；本渠道保留旧数据且不影响其他渠道。",
                flush=True,
            )
            return 20
        channel_policy = (
            self.policy.get("channels", {}).get("qieman", {})
            if isinstance(self.policy.get("channels"), dict)
            else {}
        )
        command = [
            self.python,
            "-u",
            "-X",
            "utf8",
            str(collector),
            "--workspace-root",
            str(self.workspace_root),
            "--code-root",
            str(self.code_root),
            "--normalized-root",
            str(self.normalized_root),
            "--raw-root",
            str(self.raw_root),
            "--node-run-dir",
            str(self.node_run_dir / "collector"),
            "--run-id",
            self.child_run_id,
            "--daily-run-id",
            self.args.run_id,
            "--db-path",
            str(self.database_root / "analysis_zh_current.sqlite"),
            "--dpapi-input",
            str(dpapi_input),
            "--history-concurrency",
            str(max(1, min(8, int(channel_policy.get("historyConcurrency") or 3)))),
            "--incremental-overlap-days",
            str(max(0, min(60, int(channel_policy.get("incrementalOverlapDays") or 7)))),
            "--history-signal-page-size",
            str(max(10, min(100, int(channel_policy.get("historySignalPageSize") or 25)))),
            "--history-regular-page-size",
            str(max(10, min(100, int(channel_policy.get("historyRegularPageSize") or 100)))),
            "--history-request-idle-timeout-seconds",
            str(
                max(
                    30,
                    min(900, int(channel_policy.get("historyRequestIdleTimeoutSeconds") or 120)),
                )
            ),
            "--history-request-total-timeout-seconds",
            str(
                max(
                    30,
                    min(1800, int(channel_policy.get("historyRequestTotalTimeoutSeconds") or 600)),
                )
            ),
            "--history-request-attempts",
            str(max(1, min(8, int(channel_policy.get("historyRequestAttempts") or 4)))),
            "--history-process-batch-size",
            str(max(10, min(200, int(channel_policy.get("historyProcessBatchSize") or 50)))),
            "--history-process-attempts",
            str(max(1, min(12, int(channel_policy.get("historyProcessAttempts") or 4)))),
            "--result-path",
            str(result_path),
        ]
        code = self.run_command(command)
        if self.args.dry_run:
            return code
        if code != 0 or not result_path.is_file():
            print(
                f"[ERROR] 且慢增量采集失败，旧渠道数据保持不变：exit={code}",
                flush=True,
            )
            return code or 20
        result = read_json(result_path)
        actual_run_id = str(result.get("run_id") or "").strip()
        summary_path = Path(str(result.get("summary_path") or ""))
        audit_path = Path(str(result.get("audit_report_path") or ""))
        if actual_run_id != self.child_run_id or not summary_path.is_file() or not audit_path.is_file():
            print(
                "[ERROR] 且慢采集批次血缘或验收产物不完整。",
                flush=True,
            )
            return 20
        self.context_updates.update(
            {
                "QIEMAN_COLLECT_RUN_ID": actual_run_id,
                "QIEMAN_COLLECT_SUMMARY_PATH": str(summary_path),
                "QIEMAN_COLLECT_AUDIT_PATH": str(audit_path),
                "QIEMAN_HISTORY_RUN_DIR": str(result.get("history_run_dir") or ""),
            }
        )
        self.artifacts.extend(
            [
                {"key": "qieman_collect_result", "path": str(result_path), "validationStatus": "passed"},
                {"key": "qieman_collection_summary", "path": str(summary_path), "validationStatus": "passed"},
                {"key": "qieman_isolated_audit", "path": str(audit_path), "validationStatus": "passed"},
            ]
        )
        counts = result.get("counts") if isinstance(result.get("counts"), dict) else {}
        self.counters.update(
            {
                "strategyTotal": int(result.get("strategy_total") or 0),
                "catalogNewStrategyTotal": int(result.get("catalog_new_strategy_total") or 0),
                "catalogNewStrategyCollectedTotal": int(
                    result.get("catalog_new_strategy_collected_total") or 0
                ),
                "catalogBatchMissingStrategyTotal": int(
                    result.get("catalog_batch_missing_strategy_total") or 0
                ),
                "incrementalStrategyTotal": int(result.get("incremental_strategy_total") or 0),
                "bootstrapStrategyTotal": int(result.get("bootstrap_strategy_total") or 0),
                "downloadedNavRows": int(result.get("downloaded_nav_rows") or 0),
                "downloadedHistoryRows": int(result.get("downloaded_history_rows") or 0),
                "nonEmptyNavStrategyTotal": int(result.get("non_empty_nav_strategy_total") or 0),
                "latestNavDateStrategyTotal": int(result.get("latest_nav_date_strategy_total") or 0),
                "retainedHistoryStrategyTotal": int(
                    result.get("retained_history_strategy_total") or 0
                ),
                "historyProcessBatchTotal": int(result.get("history_process_batch_total") or 0),
                "historyProcessLaunchTotal": int(result.get("history_process_launch_total") or 0),
                "historyProcessFailureTotal": int(result.get("history_process_failure_total") or 0),
                "historyProcessRestartTotal": int(result.get("history_process_restart_total") or 0),
                "dailyPerformanceRows": int(counts.get("strategy_performance_daily") or 0),
                "currentHoldingRows": int(counts.get("strategy_fund_snapshot") or 0),
                "rebalanceEventTotal": int(counts.get("strategy_rebalance_event") or 0),
                "rebalanceFundDeltaTotal": int(counts.get("strategy_rebalance_fund_delta") or 0),
            }
        )
        source_latest_nav_date = str(result.get("source_latest_nav_date") or "").strip()
        if source_latest_nav_date:
            self.watermarks["且慢源端业绩最新日期"] = source_latest_nav_date
        if self.counters["retainedHistoryStrategyTotal"]:
            warning = (
                "且慢部分调仓历史在长超时重试后仍不可用，已保留上一成功历史；"
                f"策略数={self.counters['retainedHistoryStrategyTotal']}，"
                "本批官方净值继续独立更新。"
            )
            self.warnings.append(warning)
            print(f"[WARN] {warning}", flush=True)
        print(
            f"[DONE] 且慢增量采集完成：策略={self.counters['strategyTotal']} "
            f"新增={self.counters['catalogNewStrategyTotal']} "
            f"增量策略={self.counters['incrementalStrategyTotal']}",
            flush=True,
        )
        return 0

    def qieman_gate(self) -> int:
        if self.args.dry_run:
            return 0
        run_id = str(os.environ.get("QIEMAN_COLLECT_RUN_ID") or "").strip()
        summary_path = Path(str(os.environ.get("QIEMAN_COLLECT_SUMMARY_PATH") or ""))
        audit_path = Path(str(os.environ.get("QIEMAN_COLLECT_AUDIT_PATH") or ""))
        if not run_id or not summary_path.is_file() or not audit_path.is_file():
            print("[ERROR] 且慢批次验收缺少精确采集上下文。", flush=True)
            return 20
        summary = read_json(summary_path)
        audit = read_json(audit_path)
        channel_policy = (
            self.policy.get("channels", {}).get("qieman", {})
            if isinstance(self.policy.get("channels"), dict)
            else {}
        )
        strategy_total = int(summary.get("strategy_total") or 0)
        discovered_strategy_total = int(
            summary.get("catalog_discovered_strategy_total") or 0
        )
        excluded_internal_total = int(
            summary.get("catalog_excluded_internal_total") or 0
        )
        catalog_strategy_ids = {
            str(value).strip()
            for value in summary.get("catalog_strategy_ids") or []
            if str(value).strip()
        }
        excluded_internal_ids = {
            str(value).strip()
            for value in summary.get("catalog_excluded_internal_ids") or []
            if str(value).strip()
        }
        counts = summary.get("counts") if isinstance(summary.get("counts"), dict) else {}
        coverage = summary.get("coverage") if isinstance(summary.get("coverage"), dict) else {}
        performance_total = int(coverage.get("performance_with_rows") or 0)
        holding_total = int(coverage.get("current_position_complete") or 0)
        benchmark_total = int(coverage.get("benchmark_exact_split") or 0)
        ratios = {
            "performance": performance_total / strategy_total if strategy_total else 0.0,
            "holding": holding_total / strategy_total if strategy_total else 0.0,
            "benchmark": benchmark_total / strategy_total if strategy_total else 0.0,
        }
        nav_freshness = assess_qieman_nav_freshness(summary, channel_policy)
        latest_nav_date = str(nav_freshness.get("sourceLatestNavDate") or "")
        non_empty_nav_total = int(nav_freshness.get("nonEmptyNavStrategyTotal") or 0)
        latest_nav_strategy_total = int(
            nav_freshness.get("latestNavDateStrategyTotal") or 0
        )
        latest_nav_ratio = float(nav_freshness.get("latestNavDateStrategyRatio") or 0)
        fresh_nav_strategy_total = int(
            nav_freshness.get("freshNavDateStrategyTotal") or 0
        )
        fresh_nav_ratio = float(nav_freshness.get("freshNavDateStrategyRatio") or 0)
        minimum_fresh_nav_ratio = float(
            nav_freshness.get("minimumFreshNavDateStrategyRatio") or 0
        )
        thresholds = {
            "performance": float(channel_policy.get("minimumPerformanceStrategyRatio") or 0.99),
            "holding": float(channel_policy.get("minimumCurrentHoldingRatio") or 0.99),
            "benchmark": float(channel_policy.get("minimumExactBenchmarkRatio") or 0.95),
        }
        failures: list[str] = []
        if summary.get("catalog_discovery_complete") is not True:
            failures.append("catalog_discovery_complete")
        if summary.get("catalog_batch_closed") is not True:
            failures.append("catalog_batch_closed")
        if int(summary.get("catalog_batch_missing_strategy_total") or 0):
            failures.append("new_strategy_batch_closure")
        if nav_freshness.get("passed") is not True:
            failures.append("latest_nav_date_coverage")
        if audit.get("status") not in {"passed", "warn"} or int(audit.get("error_count") or 0):
            failures.append("isolated_data_audit")
        if strategy_total <= 0 or int(counts.get("strategy_master") or 0) != strategy_total:
            failures.append("strategy_master_count")
        if (
            discovered_strategy_total != strategy_total + excluded_internal_total
            or excluded_internal_total != len(excluded_internal_ids)
            or len(catalog_strategy_ids) != strategy_total
            or catalog_strategy_ids.intersection(excluded_internal_ids)
        ):
            failures.append("internal_test_catalog_partition")
        for key, ratio in ratios.items():
            if ratio < thresholds[key]:
                failures.append(f"{key}_coverage")

        previous_total = 0
        database = self.database_root / "analysis_zh_current.sqlite"
        if database.is_file():
            uri = f"file:{database.as_posix()}?mode=ro"
            try:
                with sqlite3.connect(uri, uri=True, timeout=60) as connection:
                    previous_total = int(
                        connection.execute(
                            'SELECT COUNT(*) FROM "策略信息" WHERE "渠道ID"=?',
                            ("qieman",),
                        ).fetchone()[0]
                    )
            except sqlite3.Error:
                previous_total = 0
        retention = float(channel_policy.get("minimumInventoryRetentionRatio") or 0.95)
        if previous_total > 0 and strategy_total < previous_total * retention:
            failures.append("strategy_inventory_retention")
        failures = list(dict.fromkeys(failures))
        gate_path = self.node_run_dir / "qieman_gate.json"
        atomic_json(
            gate_path,
            {
                "runId": run_id,
                "summaryPath": str(summary_path),
                "auditPath": str(audit_path),
                "strategyTotal": strategy_total,
                "discoveredStrategyTotal": discovered_strategy_total,
                "excludedInternalStrategyTotal": excluded_internal_total,
                "previousStrategyTotal": previous_total,
                "minimumInventoryRetentionRatio": retention,
                "coverageRatios": ratios,
                "coverageThresholds": thresholds,
                "sourceLatestNavDate": latest_nav_date,
                "minimumFreshNavDate": nav_freshness.get("minimumFreshNavDate"),
                "maximumNavDateLagBusinessDays": int(
                    nav_freshness.get("maximumNavDateLagBusinessDays") or 0
                ),
                "nonEmptyNavStrategyTotal": non_empty_nav_total,
                "latestNavDateStrategyTotal": latest_nav_strategy_total,
                "latestNavDateStrategyRatio": latest_nav_ratio,
                "freshNavDateStrategyTotal": fresh_nav_strategy_total,
                "freshNavDateStrategyRatio": fresh_nav_ratio,
                "minimumFreshNavDateStrategyRatio": minimum_fresh_nav_ratio,
                "navLatestDateCounts": nav_freshness.get("navLatestDateCounts") or {},
                "retainedHistoryStrategyTotal": int(
                    summary.get("retained_history_strategy_total") or 0
                ),
                "auditStatus": audit.get("status"),
                "auditWarningCount": int(audit.get("warning_count") or 0),
                "failedRequiredChecks": failures,
            },
        )
        self.artifacts.append(
            {
                "key": "qieman_gate",
                "path": str(gate_path),
                "validationStatus": "failed" if failures else "passed",
            }
        )
        self.counters.update(
            {
                "strategyTotal": strategy_total,
                "previousStrategyTotal": previous_total,
                "performanceCoveragePermille": round(ratios["performance"] * 1000),
                "holdingCoveragePermille": round(ratios["holding"] * 1000),
                "benchmarkCoveragePermille": round(ratios["benchmark"] * 1000),
                "freshNavDateStrategyTotal": fresh_nav_strategy_total,
                "freshNavDateCoveragePermille": round(fresh_nav_ratio * 1000),
                "auditWarningCount": int(audit.get("warning_count") or 0),
            }
        )
        if failures:
            print(f"[ERROR] 且慢必需验收项失败：{failures}", flush=True)
            return 20
        history_run_dir = Path(str(summary.get("history_run_dir") or ""))
        accepted_state = self.raw_root / "qieman" / "accepted_state.json"
        atomic_json(
            accepted_state,
            {
                "run_id": run_id,
                "history_run_dir": str(history_run_dir),
                "summary_path": str(summary_path),
                "accepted_at": now_text(),
            },
        )
        self.context_updates.update(
            {"QIEMAN_GATE_PASSED": "1", "QIEMAN_GATE_RUN_ID": run_id}
        )
        if summary.get("catalog_complete") is not True:
            warning = "且慢目录为已完成的关键词并集下限，源端未提供可证明的官方总数。"
            self.warnings.append(warning)
            print(f"[WARN] {warning}", flush=True)
        if int(audit.get("warning_count") or 0):
            warning = f"且慢专项稽核保留 {int(audit.get('warning_count') or 0)} 类披露缺口 warning。"
            self.warnings.append(warning)
            print(f"[WARN] {warning}", flush=True)
        lagged_but_accepted_total = max(0, fresh_nav_strategy_total - latest_nav_strategy_total)
        if lagged_but_accepted_total:
            warning = (
                "且慢存在允许窗口内的一工作日披露延迟："
                f"延迟策略={lagged_but_accepted_total}，"
                f"时效窗口覆盖={fresh_nav_strategy_total}/{strategy_total} "
                f"({fresh_nav_ratio:.2%})，门槛={minimum_fresh_nav_ratio:.2%}。"
            )
            self.warnings.append(warning)
            print(f"[WARN] {warning}", flush=True)
        print("[DONE] 且慢精确批次、覆盖率和新增策略闭环验收通过。", flush=True)
        return 0

    def qieman_load(self) -> int:
        if self.args.dry_run:
            return 0
        if os.environ.get("QIEMAN_GATE_PASSED") != "1":
            print("[ERROR] 且慢入库要求同批次 gate 已通过。", flush=True)
            return 20
        collect_run_id = str(os.environ.get("QIEMAN_COLLECT_RUN_ID") or "").strip()
        gate_run_id = str(os.environ.get("QIEMAN_GATE_RUN_ID") or "").strip()
        summary_path = Path(str(os.environ.get("QIEMAN_COLLECT_SUMMARY_PATH") or ""))
        if not collect_run_id or collect_run_id != gate_run_id or not summary_path.is_file():
            print("[ERROR] 且慢入库批次血缘不一致。", flush=True)
            return 20
        database = self.database_root / "analysis_zh_current.sqlite"
        result_path = self.node_run_dir / "qieman_incremental_load.json"
        env = dict(os.environ)
        env["QIEMAN_COLLECT_RUN_ID"] = collect_run_id
        code = self.run_command(
            self.python_command(
                "load_analysis_zh_current_sqlite.py",
                "--db-path",
                str(database),
                "--schema-path",
                str(self.code_root / "schemas" / "analysis_zh_current.sql"),
                "--keep-existing-db",
                "--normalized-root",
                str(self.normalized_root),
                "--channels",
                "qieman",
                "--strategy-catalog-summary",
                str(summary_path),
            ),
            env=env,
        )
        if code != 0:
            self.context_updates["QIEMAN_LOAD_FAILED"] = "1"
            print(
                f"[ERROR] 且慢事务入库失败，旧渠道数据已由事务回滚保留：exit={code}",
                flush=True,
            )
            return code or 20
        summary = read_json(summary_path)
        expected_total = int(summary.get("strategy_total") or 0)
        expected_counts = summary.get("counts") if isinstance(summary.get("counts"), dict) else {}
        expected_signal_events = int(expected_counts.get("signal_strategy_event") or 0)
        expected_signal_instructions = int(expected_counts.get("signal_fund_instruction") or 0)
        expected_historical_holdings = int(expected_counts.get("strategy_fund_snapshot_history") or 0)
        source_latest_nav_date = str(summary.get("source_latest_nav_date") or "").strip()
        expected_latest_nav_strategy_total = int(
            summary.get("latest_nav_date_strategy_total") or 0
        )
        expected_non_empty_nav_strategy_total = int(
            summary.get("non_empty_nav_strategy_total") or 0
        )
        catalog_ids = {str(value) for value in summary.get("catalog_strategy_ids") or [] if str(value)}
        new_ids = {str(value) for value in summary.get("catalog_new_strategy_ids") or [] if str(value)}
        counts: dict[str, int] = {}
        with sqlite3.connect(database) as connection:
            for key, table in {
                "strategyTotal": "策略信息",
                "dailyRowsTotal": "策略日度业绩",
                "currentHoldingRows": "策略当前持仓",
                "historicalHoldingRows": "策略历史持仓",
                "rebalanceEventTotal": "策略调仓事件",
                "rebalanceFundDeltaTotal": "策略调仓明细",
                "signalEventTotal": "信号策略事件",
                "signalInstructionTotal": "信号策略基金指令",
            }.items():
                counts[key] = int(
                    connection.execute(
                        f'SELECT COUNT(*) FROM "{table}" WHERE "渠道ID"=?',
                        ("qieman",),
                    ).fetchone()[0]
                )
            loaded_ids = {
                str(row[0])
                for row in connection.execute(
                    'SELECT "渠道策略ID" FROM "策略信息" WHERE "渠道ID"=?',
                    ("qieman",),
                )
                if str(row[0] or "").strip()
            }
            loaded_latest_nav_date = str(
                connection.execute(
                    'SELECT COALESCE(MAX("交易日期"), \'\') FROM "策略日度业绩" WHERE "渠道ID"=?',
                    ("qieman",),
                ).fetchone()[0]
                or ""
            ).strip()
            loaded_latest_nav_strategy_total = int(
                connection.execute(
                    '''SELECT COUNT(DISTINCT "渠道策略ID")
                       FROM "策略日度业绩"
                       WHERE "渠道ID"=? AND "交易日期"=?''',
                    ("qieman", source_latest_nav_date),
                ).fetchone()[0]
            ) if source_latest_nav_date else 0
            loaded_non_empty_nav_strategy_total = int(
                connection.execute(
                    '''SELECT COUNT(DISTINCT "渠道策略ID")
                       FROM "策略日度业绩"
                       WHERE "渠道ID"=?''',
                    ("qieman",),
                ).fetchone()[0]
            )
        missing_catalog = sorted(catalog_ids - loaded_ids)
        missing_new = sorted(new_ids - loaded_ids)
        performance_freshness_failed = bool(
            not source_latest_nav_date
            or loaded_latest_nav_date != source_latest_nav_date
            or loaded_latest_nav_strategy_total < expected_latest_nav_strategy_total
            or loaded_non_empty_nav_strategy_total < expected_non_empty_nav_strategy_total
        )
        if (
            counts["strategyTotal"] != expected_total
            or counts["historicalHoldingRows"] != expected_historical_holdings
            or counts["signalEventTotal"] != expected_signal_events
            or counts["signalInstructionTotal"] != expected_signal_instructions
            or missing_catalog
            or missing_new
            or performance_freshness_failed
        ):
            self.context_updates["QIEMAN_LOAD_FAILED"] = "1"
            print(
                "[ERROR] 且慢入库后闭环检查失败："
                f"expected={expected_total}, actual={counts['strategyTotal']}, "
                f"historical_holdings={counts['historicalHoldingRows']}/{expected_historical_holdings}, "
                f"signal_events={counts['signalEventTotal']}/{expected_signal_events}, "
                f"signal_instructions={counts['signalInstructionTotal']}/{expected_signal_instructions}, "
                f"latest_nav_date={loaded_latest_nav_date or None}/{source_latest_nav_date or None}, "
                f"latest_nav_strategies="
                f"{loaded_latest_nav_strategy_total}/{expected_latest_nav_strategy_total}, "
                f"non_empty_nav_strategies="
                f"{loaded_non_empty_nav_strategy_total}/{expected_non_empty_nav_strategy_total}, "
                f"missing_catalog={missing_catalog}, missing_new={missing_new}",
                flush=True,
            )
            return 20
        atomic_json(
            result_path,
            {
                "channelId": "qieman",
                "runId": collect_run_id,
                "database": str(database),
                "counts": counts,
                "sourceLatestNavDate": source_latest_nav_date,
                "loadedLatestNavDate": loaded_latest_nav_date,
                "expectedLatestNavStrategyTotal": expected_latest_nav_strategy_total,
                "loadedLatestNavStrategyTotal": loaded_latest_nav_strategy_total,
                "expectedNonEmptyNavStrategyTotal": expected_non_empty_nav_strategy_total,
                "loadedNonEmptyNavStrategyTotal": loaded_non_empty_nav_strategy_total,
                "loadedNewStrategyTotal": len(new_ids & loaded_ids),
                "loadedAt": now_text(),
            },
        )
        self.context_updates["QIEMAN_LOADED"] = "1"
        self.artifacts.append(
            {"key": "qieman_incremental_load", "path": str(result_path), "validationStatus": "passed"}
        )
        self.counters.update(counts)
        self.counters["loadedNewStrategyTotal"] = len(new_ids & loaded_ids)
        self.counters["loadedLatestNavStrategyTotal"] = loaded_latest_nav_strategy_total
        self.watermarks["且慢主库业绩最新日期"] = loaded_latest_nav_date
        print(
            f"[DONE] 且慢已入库：策略={counts['strategyTotal']} "
            f"持仓={counts['currentHoldingRows']} 日度={counts['dailyRowsTotal']} "
            f"历史仓位={counts['historicalHoldingRows']} "
            f"调仓={counts['rebalanceEventTotal']} 信号={counts['signalEventTotal']} "
            f"指令={counts['signalInstructionTotal']}",
            flush=True,
        )
        return 0

    def qieman_history_backfill(self) -> int:
        script = (
            self.code_root
            / "节点脚本"
            / "03_且慢"
            / "04_历史仓位主库回填"
            / "src"
            / "backfill_qieman_historical_holdings.py"
        )
        result_path = self.node_run_dir / "qieman_history_backfill.json"
        command = [
            self.python,
            "-u",
            "-X",
            "utf8",
            str(script),
            "--workspace-root",
            str(self.workspace_root),
            "--code-root",
            str(self.code_root),
            "--db-path",
            str(self.database_root / "analysis_zh_current.sqlite"),
            "--schema-path",
            str(self.code_root / "schemas" / "analysis_zh_current.sql"),
            "--normalized-root",
            str(self.normalized_root),
            "--accepted-state",
            str(self.raw_root / "qieman" / "accepted_state.json"),
            "--result-path",
            str(result_path),
        ]
        if self.args.dry_run:
            command.append("--dry-run")
        code = self.run_command(command)
        if code != 0 or not result_path.is_file():
            return code or 20
        result = read_json(result_path)
        expected = int(result.get("expected_rows") or 0)
        actual = int(result.get("actual_rows") or 0)
        if expected <= 0 or (not self.args.dry_run and actual != expected):
            print(
                f"[ERROR] 且慢历史仓位回填闭环失败：expected={expected}, actual={actual}",
                flush=True,
            )
            return 20
        coverage = result.get("coverage") if isinstance(result.get("coverage"), dict) else {}
        self.artifacts.append(
            {
                "key": "qieman_history_backfill",
                "path": str(result_path),
                "validationStatus": "passed",
            }
        )
        self.counters.update(
            {
                "expectedHistoricalHoldingRows": expected,
                "historicalHoldingRows": actual,
                "historicalHoldingSnapshotTotal": int(coverage.get("snapshotCount") or 0),
                "historicalHoldingStrategyTotal": int(coverage.get("strategyCount") or 0),
                "completeHistoricalHoldingStrategyTotal": int(coverage.get("completeStrategyCount") or 0),
            }
        )
        print(
            "[DONE] 且慢历史仓位主库回填完成："
            f"rows={actual if not self.args.dry_run else expected}, "
            f"strategies={self.counters['historicalHoldingStrategyTotal']}",
            flush=True,
        )
        return 0

    def gf_supplemental_collect(self) -> int:
        planned_channels = configured_gf_supplemental_channels()
        aggregate_path = self.node_run_dir / "gf_supplemental_collect.json"
        aggregate: dict[str, Any] = {
            "runId": self.child_run_id,
            "plannedChannels": list(planned_channels),
            "channels": {},
            "collectedAt": now_text(),
        }
        successful_channels: list[str] = []
        for channel_id in planned_channels:
            result_path = self.node_run_dir / f"{channel_id}_collect_result.json"
            code = self.run_command(
                self.python_command(
                    "collect_official_apps_public.py",
                    "--apps",
                    channel_id,
                    "--workers",
                    "8",
                    "--run-id",
                    self.child_run_id,
                    "--result-summary-path",
                    str(result_path),
                )
            )
            if self.args.dry_run:
                continue
            channel_result: dict[str, Any] = {"commandReturnCode": code, "resultPath": str(result_path)}
            if result_path.is_file():
                result = read_json(result_path)
                summary = result.get(channel_id) if isinstance(result.get(channel_id), dict) else {}
                output_paths = summary.get("output_paths") if isinstance(summary.get("output_paths"), dict) else {}
                summary_path = Path(str(output_paths.get("normalized_summary") or ""))
                coverage_path = Path(str(output_paths.get("exact_coverage") or ""))
                channel_result.update(
                    {
                        "summaryPath": str(summary_path),
                        "coveragePath": str(coverage_path),
                        "strategyTotal": int(summary.get("strategy_total") or 0),
                        "catalogStrategyTotal": int(summary.get("catalog_strategy_total") or 0),
                        "catalogNewStrategyTotal": int(
                            summary.get("catalog_new_strategy_total") or 0
                        ),
                        "catalogNewStrategyCollectedTotal": int(
                            summary.get("catalog_new_strategy_collected_total") or 0
                        ),
                        "catalogBatchMissingStrategyTotal": int(
                            summary.get("catalog_batch_missing_strategy_total") or 0
                        ),
                        "catalogComplete": summary.get("catalog_complete") is True,
                        "catalogBatchClosed": summary.get("catalog_batch_closed") is True,
                        "collectionStatus": summary.get("collection_status"),
                    }
                )
                valid = (
                    code == 0
                    and str(summary.get("run_id") or "") == self.child_run_id
                    and str(summary.get("collection_status") or "").startswith("success")
                    and summary_path.is_file()
                    and coverage_path.is_file()
                )
                if valid:
                    successful_channels.append(channel_id)
                    self.artifacts.extend(
                        [
                            {"key": f"{channel_id}_summary", "path": str(summary_path), "validationStatus": "passed"},
                            {"key": f"{channel_id}_coverage", "path": str(coverage_path), "validationStatus": "passed"},
                        ]
                    )
                else:
                    warning = f"{channel_id} 本轮采集未形成可门禁批次，保留旧库；exit={code}。"
                    self.warnings.append(warning)
                    print(f"[WARN] {warning}", flush=True)
            else:
                warning = f"{channel_id} 本轮采集结果文件缺失，保留旧库；exit={code}。"
                self.warnings.append(warning)
                print(f"[WARN] {warning}", flush=True)
            aggregate["channels"][channel_id] = channel_result

        if self.args.dry_run:
            return 0
        aggregate["successfulChannels"] = successful_channels
        atomic_json(aggregate_path, aggregate)
        self.artifacts.append(
            {"key": "gf_supplemental_collect", "path": str(aggregate_path), "validationStatus": "passed"}
        )
        self.context_updates.update(
            {
                "GF_SUPPLEMENTAL_COLLECT_RUN_ID": self.child_run_id,
                "GF_SUPPLEMENTAL_COLLECT_PATH": str(aggregate_path),
            }
        )
        self.counters.update(
            {
                "plannedChannelTotal": len(planned_channels),
                "successfulChannelTotal": len(successful_channels),
                "failureChannelTotal": len(planned_channels) - len(successful_channels),
                "catalogNewStrategyTotal": sum(
                    int(((aggregate.get("channels") or {}).get(channel_id) or {}).get("catalogNewStrategyTotal") or 0)
                    for channel_id in planned_channels
                ),
                "catalogNewStrategyCollectedTotal": sum(
                    int(
                        ((aggregate.get("channels") or {}).get(channel_id) or {}).get(
                            "catalogNewStrategyCollectedTotal"
                        )
                        or 0
                    )
                    for channel_id in planned_channels
                ),
                "catalogBatchMissingStrategyTotal": sum(
                    int(
                        ((aggregate.get("channels") or {}).get(channel_id) or {}).get(
                            "catalogBatchMissingStrategyTotal"
                        )
                        or 0
                    )
                    for channel_id in planned_channels
                ),
            }
        )
        if not successful_channels:
            print("[ERROR] 广发补充渠道本轮均未形成可用采集批次。", flush=True)
            return 20
        print(
            "[DONE] 广发补充渠道采集完成：" + ", ".join(successful_channels),
            flush=True,
        )
        return 0

    def gf_supplemental_gate(self) -> int:
        if self.args.dry_run:
            return 0
        expected_run_id = str(os.environ.get("GF_SUPPLEMENTAL_COLLECT_RUN_ID") or "").strip()
        aggregate_path = Path(str(os.environ.get("GF_SUPPLEMENTAL_COLLECT_PATH") or ""))
        if not expected_run_id or not aggregate_path.is_file():
            print("[ERROR] 广发补充渠道采集批次上下文缺失。", flush=True)
            return 20
        aggregate = read_json(aggregate_path)
        if str(aggregate.get("runId") or "") != expected_run_id:
            print("[ERROR] 广发补充渠道门禁批次来源不一致。", flush=True)
            return 20
        planned_channels = tuple(
            channel_id
            for channel_id in aggregate.get("plannedChannels", GF_SUPPLEMENTAL_CHANNEL_IDS)
            if channel_id in GF_SUPPLEMENTAL_CHANNEL_IDS
        )
        if not planned_channels:
            print("[ERROR] 广发补充渠道门禁清单为空。", flush=True)
            return 20
        channel_policies = self.policy.get("channels") if isinstance(self.policy.get("channels"), dict) else {}
        accepted: list[str] = []
        assessments: dict[str, Any] = {}
        for channel_id in planned_channels:
            info = (aggregate.get("channels") or {}).get(channel_id) or {}
            summary_path = Path(str(info.get("summaryPath") or ""))
            coverage_path = Path(str(info.get("coveragePath") or ""))
            failures: list[str] = []
            channel_warnings: list[str] = []
            metrics: dict[str, Any] = {}
            if not summary_path.is_file() or not coverage_path.is_file():
                failures.append("exact_summary_or_coverage_missing")
            else:
                summary = read_json(summary_path)
                coverage = read_json(coverage_path)
                if {str(summary.get("run_id") or ""), str(coverage.get("run_id") or "")} != {expected_run_id}:
                    failures.append("batch_provenance_mismatch")
                previous_total = previous_channel_strategy_total(
                    self.normalized_root / channel_id / "collection_summary",
                    summary_path,
                    channel_id,
                )
                assessed_failures, channel_warnings, metrics = assess_gf_supplemental_channel(
                    channel_id,
                    summary,
                    coverage,
                    channel_policies.get(channel_id, {}) if isinstance(channel_policies.get(channel_id), dict) else {},
                    previous_total,
                )
                failures.extend(assessed_failures)
            failures = list(dict.fromkeys(failures))
            assessments[channel_id] = {
                "summaryPath": str(summary_path),
                "coveragePath": str(coverage_path),
                "failedChecks": failures,
                "warnings": channel_warnings,
                **metrics,
            }
            if failures:
                warning = f"{channel_id} 门禁未通过，保留旧库：{failures}"
                self.warnings.append(warning)
                print(f"[WARN] {warning}", flush=True)
            else:
                accepted.append(channel_id)
                self.artifacts.extend(
                    [
                        {"key": f"{channel_id}_gate_summary", "path": str(summary_path), "validationStatus": "passed"},
                        {"key": f"{channel_id}_gate_coverage", "path": str(coverage_path), "validationStatus": "passed"},
                    ]
                )
                for warning_text in channel_warnings:
                    warning = f"{channel_id}: {warning_text}"
                    self.warnings.append(warning)
                    print(f"[WARN] {warning}", flush=True)

        gate_path = self.node_run_dir / "gf_supplemental_gate.json"
        gate_payload = {
            "runId": expected_run_id,
            "acceptedChannels": accepted,
            "assessments": assessments,
            "gatedAt": now_text(),
        }
        atomic_json(gate_path, gate_payload)
        self.artifacts.append(
            {"key": "gf_supplemental_gate", "path": str(gate_path), "validationStatus": "passed"}
        )
        self.counters.update(
            {
                "plannedChannelTotal": len(planned_channels),
                "acceptedChannelTotal": len(accepted),
                "rejectedChannelTotal": len(planned_channels) - len(accepted),
            }
        )
        if not accepted:
            print("[ERROR] 广发补充渠道门禁全部未通过。", flush=True)
            return 20
        self.context_updates.update(
            {
                "GF_SUPPLEMENTAL_GATE_PASSED": "1",
                "GF_SUPPLEMENTAL_GATE_RUN_ID": expected_run_id,
                "GF_SUPPLEMENTAL_ACCEPTED_CHANNELS": json.dumps(accepted, ensure_ascii=False),
                "GF_SUPPLEMENTAL_GATE_PATH": str(gate_path),
            }
        )
        print("[DONE] 广发补充渠道门禁通过：" + ", ".join(accepted), flush=True)
        return 0

    def gf_supplemental_load(self) -> int:
        if self.args.dry_run:
            return 0
        if os.environ.get("GF_SUPPLEMENTAL_GATE_PASSED") != "1":
            print("[ERROR] 广发补充渠道入库要求通过同批次门禁。", flush=True)
            return 20
        collect_run_id = str(os.environ.get("GF_SUPPLEMENTAL_COLLECT_RUN_ID") or "").strip()
        gate_run_id = str(os.environ.get("GF_SUPPLEMENTAL_GATE_RUN_ID") or "").strip()
        if not collect_run_id or collect_run_id != gate_run_id:
            print(f"[ERROR] 广发补充渠道入库批次不一致：collect={collect_run_id}, gate={gate_run_id}", flush=True)
            return 20
        try:
            accepted = json.loads(str(os.environ.get("GF_SUPPLEMENTAL_ACCEPTED_CHANNELS") or "[]"))
        except json.JSONDecodeError:
            accepted = []
        accepted = [item for item in accepted if item in GF_SUPPLEMENTAL_CHANNEL_IDS]
        if not accepted:
            print("[ERROR] 广发补充渠道入库清单为空。", flush=True)
            return 20
        database = self.database_root / "analysis_zh_current.sqlite"
        result_path = self.node_run_dir / "gf_supplemental_incremental_load.json"
        aggregate_path = Path(str(os.environ.get("GF_SUPPLEMENTAL_COLLECT_PATH") or ""))
        aggregate = read_json(aggregate_path)
        summary_paths = {
            channel_id: Path(
                str(((aggregate.get("channels") or {}).get(channel_id) or {}).get("summaryPath") or "")
            )
            for channel_id in accepted
        }
        if any(not path.is_file() for path in summary_paths.values()):
            print("[ERROR] 广发补充渠道精确摘要缺失，禁止事务入库。", flush=True)
            return 20
        command = self.python_command(
            "load_analysis_zh_current_sqlite.py",
            "--db-path",
            str(database),
            "--schema-path",
            str(self.code_root / "schemas" / "analysis_zh_current.sql"),
            "--keep-existing-db",
            "--channels",
            *accepted,
        )
        for summary_path in summary_paths.values():
            command.extend(["--strategy-catalog-summary", str(summary_path)])
        code = self.run_command(command)
        if code != 0:
            self.context_updates["GF_SUPPLEMENTAL_LOAD_FAILED"] = "1"
            print(f"[ERROR] 广发补充渠道事务入库失败，旧渠道数据已保留：exit={code}", flush=True)
            return code or 20
        channel_counts: dict[str, dict[str, int]] = {}
        channel_validations: dict[str, dict[str, Any]] = {}
        with sqlite3.connect(database) as connection:
            for channel_id in accepted:
                counts = {
                    key: int(
                        connection.execute(
                            f'SELECT COUNT(*) FROM "{table}" WHERE "渠道ID"=?',
                            (channel_id,),
                        ).fetchone()[0]
                    )
                    for key, table in {
                        "strategyTotal": "策略信息",
                        "dailyRowsTotal": "策略日度业绩",
                        "intervalRowsTotal": "策略区间业绩",
                        "currentHoldingRows": "策略当前持仓",
                        "rebalanceEventTotal": "策略调仓事件",
                    }.items()
                }
                summary = read_json(summary_paths[channel_id])
                expected = int(summary.get("strategy_total") or 0)
                if expected <= 0 or counts["strategyTotal"] != expected:
                    self.context_updates["GF_SUPPLEMENTAL_LOAD_FAILED"] = "1"
                    print(
                        f"[ERROR] {channel_id} 入库后策略数不一致：expected={expected}, actual={counts['strategyTotal']}",
                        flush=True,
                    )
                    return 20
                catalog_ids = {
                    str(value or "").strip()
                    for value in summary.get("catalog_strategy_ids") or []
                    if str(value or "").strip()
                }
                new_ids = {
                    str(value or "").strip()
                    for value in summary.get("catalog_new_strategy_ids") or []
                    if str(value or "").strip()
                }
                loaded_ids = {
                    str(row[0] or "").strip()
                    for row in connection.execute(
                        'SELECT "渠道策略ID" FROM "策略信息" WHERE "渠道ID"=?',
                        (channel_id,),
                    ).fetchall()
                    if str(row[0] or "").strip()
                }
                missing_catalog_ids = sorted(catalog_ids - loaded_ids)
                missing_new_ids = sorted(new_ids - loaded_ids)
                if missing_catalog_ids or missing_new_ids:
                    self.context_updates["GF_SUPPLEMENTAL_LOAD_FAILED"] = "1"
                    print(
                        f"[ERROR] {channel_id} 入库后目录 ID 不一致："
                        f"missing_catalog={missing_catalog_ids}, missing_new={missing_new_ids}",
                        flush=True,
                    )
                    return 20
                channel_counts[channel_id] = counts
                channel_validations[channel_id] = {
                    "catalogStrategyTotal": len(catalog_ids),
                    "catalogNewStrategyTotal": len(new_ids),
                    "loadedCatalogStrategyTotal": len(catalog_ids & loaded_ids),
                    "loadedNewStrategyTotal": len(new_ids & loaded_ids),
                    "missingCatalogStrategyIds": missing_catalog_ids,
                    "missingNewStrategyIds": missing_new_ids,
                    "passed": True,
                }
                self.context_updates[f"{channel_id.upper()}_LOADED"] = "1"
        atomic_json(
            result_path,
            {
                "runId": collect_run_id,
                "channels": accepted,
                "database": str(database),
                "channelCounts": channel_counts,
                "strategyCatalogLoadValidations": channel_validations,
                "loadedAt": now_text(),
            },
        )
        self.context_updates["GF_SUPPLEMENTAL_LOADED"] = "1"
        self.artifacts.append(
            {"key": "gf_supplemental_incremental_load", "path": str(result_path), "validationStatus": "passed"}
        )
        self.counters.update(
            {
                "loadedChannelTotal": len(accepted),
                "loadedStrategyTotal": sum(item["strategyTotal"] for item in channel_counts.values()),
                "loadedDailyRowsTotal": sum(item["dailyRowsTotal"] for item in channel_counts.values()),
                "loadedCurrentHoldingRows": sum(item["currentHoldingRows"] for item in channel_counts.values()),
                "loadedNewStrategyTotal": sum(
                    item["loadedNewStrategyTotal"] for item in channel_validations.values()
                ),
            }
        )
        print("[DONE] 广发补充渠道事务入库完成：" + ", ".join(accepted), flush=True)
        return 0

    def component_info(self) -> int:
        print("[DONE] This component is executed inside the TTFund composite collector.", flush=True)
        self.warnings.append("component_documentation_node_not_in_daily_dag")
        return 0

    def unified_postprocess(self) -> int:
        return self.run_command(
            self.python_command(
                "run_ttfund_post_update_quality.py",
                "--algorithm-version",
                "standard_rebalance_asset_dual_nav_v10_all_channels_20260528",
                "--deploy-site-dir",
                str(self.report_root),
                "--deploy-page-set",
                "all",
                "--timeout",
                "7200",
                "--fund-nav-incremental-days",
                "3",
                "--fund-nav-workers",
                "12",
                "--fund-lookthrough-workers",
                "8",
                "--fund-lookthrough-stale-days",
                "30",
                "--index-quote-lookback-days",
                "30",
            )
        )

    def postprocess_range(self, start: str | None, stop: str, *extra: str) -> int:
        output = self.node_run_dir / "postprocess"
        command = self.python_command(
            "run_ttfund_post_update_quality.py",
            "--algorithm-version",
            "standard_rebalance_asset_dual_nav_v10_all_channels_20260528",
            "--output-root",
            str(output),
            "--disable-auto-fast-incremental",
            "--skip-deploy-export",
            "--timeout",
            "7200",
            "--fund-nav-incremental-days",
            "3",
            "--fund-nav-workers",
            "12",
            "--fund-lookthrough-workers",
            "8",
            "--fund-lookthrough-stale-days",
            "30",
            "--index-quote-lookback-days",
            "30",
            *extra,
        )
        if start:
            command.extend(["--start-at-step", start])
        command.extend(["--stop-after-step", stop])
        code = self.run_command(command)
        summaries = sorted(output.rglob("post_update_quality_summary.json"), key=lambda path: path.stat().st_mtime, reverse=True)
        if summaries:
            self.artifacts.append({"key": "postprocess_summary", "path": str(summaries[0]), "validationStatus": "passed" if code == 0 else "failed"})
        return code

    def process_load(self) -> int:
        if self.args.dry_run:
            return self.postprocess_range(
                None,
                "02c_build_strategy_benchmark_fee_status",
                "--skip-load-analysis",
                "--skip-fund-lookthrough-update",
                "--skip-index-quote-update",
                "--skip-fund-nav-refresh",
            )
        successful_loads = 0
        clean_noop = False
        if os.environ.get("GFSEC_FIMA_LOADED") == "1":
            successful_loads += 1
            self.counters["gfsecFimaPreloaded"] = 1
        if os.environ.get("QIEMAN_LOADED") == "1":
            successful_loads += 1
            self.counters["qiemanPreloaded"] = 1
        if os.environ.get("SOUTHERN_LOADED") == "1":
            successful_loads += 1
            self.counters["southernPreloaded"] = 1
        ttfund_run_id = str(os.environ.get("TTFUND_COLLECT_RUN_ID") or "").strip()
        ttfund_status = str(os.environ.get("ADVISOR_NODE_STATUS_TTFUND_INCREMENTAL") or "")
        if ttfund_run_id:
            result_path = self.node_run_dir / "ttfund_incremental_load.json"
            command = self.python_command(
                "load_ttfund_incremental_analysis.py",
                "--db-path",
                str(self.database_root / "analysis_zh_current.sqlite"),
                "--schema-path",
                str(self.code_root / "schemas" / "analysis_zh_current.sql"),
                "--normalized-root",
                str(self.normalized_root / "ttfund"),
                "--run-id",
                ttfund_run_id,
                "--result-path",
                str(result_path),
            )
            target_trade_date = str(os.environ.get("TTFUND_TARGET_TRADE_DATE") or "").strip()
            if target_trade_date:
                command.extend(["--target-trade-date", target_trade_date])
            code = self.run_command(command)
            if code == 0 and (result_path.is_file() or self.args.dry_run):
                successful_loads += 1
                self.context_updates["TTFUND_LOADED"] = "1"
                if result_path.is_file():
                    self.artifacts.append(
                        {
                            "key": "ttfund_incremental_load",
                            "path": str(result_path),
                            "validationStatus": "passed",
                        }
                    )
            else:
                self.context_updates["TTFUND_LOAD_FAILED"] = "1"
                warning = f"天天投顾精确批次入库失败，保留原有渠道数据：exit={code}"
                self.warnings.append(warning)
                print(f"[WARN] {warning}", flush=True)
        elif ttfund_status in {"success", "warn"} and os.environ.get("TTFUND_COLLECTION_REQUIRED") == "0":
            clean_noop = True
            self.counters["ttfundNoChange"] = 1

        if os.environ.get("GFFUNDS_GATE_PASSED") == "1":
            collect_run_id = str(os.environ.get("GFFUNDS_COLLECT_RUN_ID") or "").strip()
            performance_run_id = str(os.environ.get("GFFUNDS_PERFORMANCE_RUN_ID") or "").strip()
            result_path = self.node_run_dir / "gffunds_incremental_load.json"
            command = self.python_command(
                "load_gffunds_incremental_analysis.py",
                "--db-path",
                str(self.database_root / "analysis_zh_current.sqlite"),
                "--schema-path",
                str(self.code_root / "schemas" / "analysis_zh_current.sql"),
                "--normalized-root",
                str(self.normalized_root / "gffunds"),
                "--collect-run-id",
                collect_run_id,
                "--result-path",
                str(result_path),
            )
            if performance_run_id:
                command.extend(["--performance-run-id", performance_run_id])
            code = self.run_command(command)
            if code == 0 and (result_path.is_file() or self.args.dry_run):
                load_result = read_json(result_path) if result_path.is_file() else {}
                catalog_validation = (
                    load_result.get("strategy_catalog_load_validation")
                    if isinstance(load_result.get("strategy_catalog_load_validation"), dict)
                    else {}
                )
                if result_path.is_file() and catalog_validation.get("passed") is not True:
                    code = 20
                else:
                    successful_loads += 1
                    self.context_updates["GFFUNDS_LOADED"] = "1"
                    self.counters["gffundsLoadedNewStrategyTotal"] = int(
                        catalog_validation.get("loadedNewStrategyTotal") or 0
                    )
                if result_path.is_file():
                    self.artifacts.append(
                        {
                            "key": "gffunds_incremental_load",
                            "path": str(result_path),
                            "validationStatus": "passed" if code == 0 else "failed",
                        }
                    )
            if code != 0 or not result_path.is_file():
                self.context_updates["GFFUNDS_LOAD_FAILED"] = "1"
                warning = f"广发精确批次入库失败，保留原有渠道数据：exit={code}"
                self.warnings.append(warning)
                print(f"[WARN] {warning}", flush=True)

        if (
            os.environ.get("GFSEC_FIMA_GATE_PASSED") == "1"
            and os.environ.get("GFSEC_FIMA_LOADED") != "1"
        ):
            collect_run_id = str(os.environ.get("GFSEC_FIMA_COLLECT_RUN_ID") or "").strip()
            result_path = self.node_run_dir / "gfsec_fima_incremental_load.json"
            command = self.python_command(
                "load_analysis_zh_current_sqlite.py",
                "--db-path",
                str(self.database_root / "analysis_zh_current.sqlite"),
                "--schema-path",
                str(self.code_root / "schemas" / "analysis_zh_current.sql"),
                "--keep-existing-db",
                "--channels",
                "gfsec_fima",
            )
            summary_path = Path(str(os.environ.get("GFSEC_FIMA_COLLECT_SUMMARY_PATH") or ""))
            if summary_path.is_file():
                command.extend(["--strategy-catalog-summary", str(summary_path)])
            else:
                self.context_updates["GFSEC_FIMA_LOAD_FAILED"] = "1"
                warning = "GFSEC FIMA 重试入库缺少精确摘要，已保留旧渠道数据。"
                self.warnings.append(warning)
                print(f"[WARN] {warning}", flush=True)
                command = []
            code = self.run_command(command) if command else 20
            if code == 0:
                database = self.database_root / "analysis_zh_current.sqlite"
                counts: dict[str, int] = {}
                with sqlite3.connect(database) as connection:
                    for key, table in {
                        "strategyTotal": "策略信息",
                        "dailyRowsTotal": "策略日度业绩",
                        "currentHoldingRows": "策略当前持仓",
                        "rebalanceEventTotal": "策略调仓事件",
                        "rebalanceFundDeltaTotal": "策略调仓明细",
                    }.items():
                        counts[key] = int(
                            connection.execute(
                                f'SELECT COUNT(*) FROM "{table}" WHERE "渠道ID"=?',
                                ("gfsec_fima",),
                            ).fetchone()[0]
                        )
                atomic_json(
                    result_path,
                    {
                        "channelId": "gfsec_fima",
                        "runId": collect_run_id,
                        "database": str(database),
                        "counts": counts,
                        "loadedAt": now_text(),
                    },
                )
                successful_loads += 1
                self.context_updates["GFSEC_FIMA_LOADED"] = "1"
                self.artifacts.append(
                    {
                        "key": "gfsec_fima_incremental_load",
                        "path": str(result_path),
                        "validationStatus": "passed",
                    }
                )
                for key, value in counts.items():
                    self.counters[f"gfsecFima{key[0].upper()}{key[1:]}"] = value
            else:
                self.context_updates["GFSEC_FIMA_LOAD_FAILED"] = "1"
                warning = f"广发证券财富管家精确批次入库失败，保留原有渠道数据：exit={code}"
                self.warnings.append(warning)
                print(f"[WARN] {warning}", flush=True)

        self.counters["successfulChannelLoads"] = successful_loads
        if successful_loads == 0 and not clean_noop:
            print("[ERROR] no channel produced a validated load or clean no-op.", flush=True)
            return 20
        return self.postprocess_range(
            None,
            "02c_build_strategy_benchmark_fee_status",
            "--skip-load-analysis",
            "--skip-fund-lookthrough-update",
            "--skip-index-quote-update",
            "--skip-fund-nav-refresh",
        )

    def fund_nav(self) -> int:
        extra_args = [
            "--skip-load-analysis",
            "--skip-fund-lookthrough-update",
            "--skip-index-quote-update",
        ]
        target_trade_date = str(os.environ.get("TTFUND_TARGET_TRADE_DATE") or "").strip()
        if target_trade_date:
            # The resilient collector already supports --only-nav-before through
            # this target date.  Forwarding the exact batch watermark prevents
            # a resume later in the same daily run from rescanning the entire
            # 27k fund dictionary after that date has already been loaded.
            extra_args.extend(["--target-trade-date", target_trade_date])
        return self.postprocess_range(
            "04_refresh_fund_nav_public",
            "04_refresh_fund_nav_public",
            *extra_args,
        )

    def fund_lookthrough(self) -> int:
        return self.postprocess_range(
            "04b_collect_fund_quarterly_asset_allocation",
            "04f_audit_fund_lookthrough_coverage",
            "--skip-load-analysis",
            "--skip-fund-nav-refresh",
            "--skip-index-quote-update",
        )

    def index_benchmark(self) -> int:
        return self.postprocess_range(
            "09_update_index_daily_quotes",
            "09b_update_ttfund_global_index_quotes",
            "--skip-load-analysis",
            "--skip-fund-nav-refresh",
            "--skip-fund-lookthrough-update",
        )

    def strategy_governance(self) -> int:
        return self.postprocess_range(
            "02d_govern_strategy_lifecycle_and_rebalance",
            "19f_audit_fund_lookthrough_coverage_after_gap_repair",
            "--skip-load-analysis",
            "--skip-fund-nav-refresh",
            "--skip-index-quote-update",
            # The daily DAG creates a validated SQLite backup after data_audit.
            # Avoid an additional full-size snapshot inside performance governance.
            "--skip-performance-governance-backup",
        )

    def public_fund_snapshot(self) -> int:
        database = self.database_root / "analysis_zh_current.sqlite"
        output_root = self.node_run_dir / "out"
        code = self.run_command(
            self.python_command(
                "build_public_fund_performance_snapshot.py",
                "--db-path",
                str(database),
                "--output-root",
                str(output_root),
            )
        )
        if self.args.dry_run:
            return code
        summaries = sorted(
            output_root.glob("*/summary.json"),
            key=lambda path: path.stat().st_mtime_ns,
            reverse=True,
        )
        if code != 0 or not summaries:
            print(
                "[ERROR] public-fund snapshot builder failed or did not produce a summary; "
                "report generation remains blocked.",
                flush=True,
            )
            return code or 3
        summary_path = summaries[0]
        summary = read_json(summary_path)
        snapshot_path = Path(str(summary.get("snapshot_json") or ""))
        expected_rows = int(summary.get("fund_count") or 0)
        required_columns = {
            "基金代码",
            "基准风险资产权重",
            "基准风险资产权重来源",
        }
        try:
            with sqlite3.connect(database, timeout=60) as connection:
                actual_columns = {
                    str(row[1])
                    for row in connection.execute(
                        'PRAGMA table_info("公募基金产品绩效快照")'
                    )
                }
                actual_rows = int(
                    connection.execute(
                        'SELECT COUNT(*) FROM "公募基金产品绩效快照"'
                    ).fetchone()[0]
                    or 0
                )
        except sqlite3.Error as exc:
            print(f"[ERROR] public-fund snapshot verification failed: {exc}", flush=True)
            return 3
        missing_columns = sorted(required_columns - actual_columns)
        if (
            expected_rows <= 0
            or actual_rows != expected_rows
            or missing_columns
            or not snapshot_path.is_file()
            or snapshot_path.stat().st_size <= 2
        ):
            print(
                "[ERROR] public-fund snapshot verification mismatch: "
                f"expected_rows={expected_rows}, actual_rows={actual_rows}, "
                f"missing_columns={missing_columns}, snapshot={snapshot_path}",
                flush=True,
            )
            return 3
        self.counters.update(
            {
                "fundCount": actual_rows,
                "navAnyCount": int(summary.get("nav_any_count") or 0),
                "returnAnyCount": int(summary.get("return_any_count") or 0),
                "riskAnyCount": int(summary.get("risk_any_count") or 0),
                "benchmarkTextCount": int(summary.get("benchmark_text_count") or 0),
                "benchmarkRiskWeightCount": int(
                    summary.get("benchmark_equity_bucket_count") or 0
                ),
            }
        )
        self.artifacts.extend(
            [
                {
                    "key": "public_fund_snapshot_summary",
                    "path": str(summary_path),
                    "validationStatus": "passed",
                },
                {
                    "key": "public_fund_snapshot_json",
                    "path": str(snapshot_path),
                    "validationStatus": "passed",
                },
            ]
        )
        return 0

    def data_audit(self) -> int:
        staging_text = str(os.environ.get("ADVISOR_REPORT_STAGING_ROOT") or "").strip()
        report_scope = str(
            os.environ.get("ADVISOR_REPORT_SCOPE")
            or configured_report_scope(self.policy)
        ).strip().lower()
        audit_policy = (
            self.policy.get("dailyAudit")
            if isinstance(self.policy.get("dailyAudit"), dict)
            else {}
        )
        audit_arguments = [
            "--mode",
            "manual",
            "--report-root",
            staging_text or str(self.report_root),
            "--audit-only",
            "--output-root",
            str(self.node_run_dir / "audit"),
        ]
        if audit_policy.get("runStaticChecks") is False:
            audit_arguments.append("--skip-static")
        if self.args.dry_run:
            self.context_updates["ADVISOR_REPORT_SCOPE"] = report_scope
            if report_scope == "minimal_publish" and staging_text:
                self.context_updates["ADVISOR_MINIMAL_REPORT_SOURCE_ROOT"] = staging_text
                self.context_updates["ADVISOR_REPORT_PROMOTED"] = "0"
            return self.run_command(
                self.python_command("run_project_data_audit_hook.py", *audit_arguments)
            )
        staging_root = Path(staging_text) if staging_text else None
        if staging_root is None or not staging_root.is_dir():
            print("[ERROR] report staging root is missing; no report package was changed.", flush=True)
            return 3
        audit_output = self.node_run_dir / "audit"
        code = self.run_command(
            self.python_command("run_project_data_audit_hook.py", *audit_arguments)
        )
        summaries = sorted(
            audit_output.glob("*/*/hook_summary.json"),
            key=lambda path: path.stat().st_mtime_ns,
            reverse=True,
        )
        if not summaries:
            print("[ERROR] data audit hook summary is missing; formal report was not changed.", flush=True)
            return code or 3
        audit_summary_path = summaries[0]
        audit_summary = read_json(audit_summary_path)
        audit_status = str(audit_summary.get("status") or "")
        self.artifacts.append(
            {
                "key": "data_audit_hook_summary",
                "path": str(audit_summary_path),
                "validationStatus": "passed" if code == 0 and audit_status in {"ok", "warn"} else "failed",
            }
        )
        self.counters["auditErrorCount"] = int(audit_summary.get("staticErrorCount") or 0) + sum(
            1
            for issue in audit_summary.get("issues") or []
            if str(issue.get("severity") or "").lower() == "error"
        )
        if code != 0 or audit_status not in {"ok", "warn"}:
            print(
                f"[ERROR] staging report audit failed: exit={code}, status={audit_status}; formal report unchanged.",
                flush=True,
            )
            return code or 3
        staging_manifest = staging_root / "deployment_manifest.json"
        if not staging_manifest.is_file():
            print(
                "[ERROR] audited staging report has no deployment_manifest.json; formal report unchanged.",
                flush=True,
            )
            return 3
        if audit_status == "warn":
            warning = "最小发布源数据稽核存在业务 warning，已保留到稽核报告，不阻断可用数据发布。"
            self.warnings.append(warning)
            print(f"[WARN] {warning}", flush=True)
        self.context_updates["ADVISOR_REPORT_SCOPE"] = report_scope
        if report_scope == "minimal_publish":
            self.artifacts.append(
                {
                    "key": "audited_minimal_report_source_manifest",
                    "path": str(staging_manifest),
                    "validationStatus": "passed",
                }
            )
            self.context_updates["ADVISOR_MINIMAL_REPORT_SOURCE_ROOT"] = str(staging_root)
            self.context_updates["ADVISOR_REPORT_PROMOTED"] = "0"
            self.counters["formalFullReportPromoted"] = 0
            print(
                "[DONE] 最小发布源数据已通过稽核；正式完整报表目录保持不变。",
                flush=True,
            )
            return 0
        promotion = promote_report_directory(staging_root, self.report_root, self.child_run_id)
        promotion_path = self.node_run_dir / "report_promotion.json"
        atomic_json(promotion_path, promotion)
        self.artifacts.append(
            {
                "key": "report_promotion",
                "path": str(promotion_path),
                "validationStatus": "passed",
            }
        )
        formal_manifest = self.report_root / "deployment_manifest.json"
        self.artifacts.append(
            {
                "key": "deployment_manifest",
                "path": str(formal_manifest),
                "validationStatus": "passed" if formal_manifest.is_file() else "failed",
            }
        )
        if promotion.get("previousCleanupWarning"):
            self.warnings.append(str(promotion["previousCleanupWarning"]))
        self.context_updates["ADVISOR_REPORT_PROMOTED"] = "1"
        return 0

    def report_build(self) -> int:
        report_scope = configured_report_scope(self.policy)
        report_policy = (
            self.policy.get("reports")
            if isinstance(self.policy.get("reports"), dict)
            else {}
        )
        source_parent = self.temp_root / "minimal_report_source"
        staging_root = source_parent / self.child_run_id
        cleanup = cleanup_minimal_report_sources(
            source_parent,
            staging_root,
            retention_days=int(report_policy.get("failedSourceRetentionDays") or 3),
            retain_failed_runs=int(report_policy.get("retainFailedSources") or 2),
            dry_run=self.args.dry_run,
        )
        cleanup_path = self.node_run_dir / "minimal_report_source_cleanup.json"
        atomic_json(cleanup_path, cleanup)
        self.artifacts.append(
            {
                "key": "minimal_report_source_cleanup",
                "path": str(cleanup_path),
                "validationStatus": "passed",
            }
        )
        self.counters["removedOldMinimalReportSources"] = len(cleanup.get("removed") or [])
        if staging_root.exists() and not self.args.dry_run:
            shutil.rmtree(staging_root)
        self.context_updates["ADVISOR_REPORT_STAGING_ROOT"] = str(staging_root)
        self.context_updates["ADVISOR_REPORT_SCOPE"] = report_scope
        page_set = "minimal_publish"
        commands = [
            self.python_command(
                "prepare_analysis_platform_deploy.py",
                "--deploy-dir",
                str(staging_root),
                "--page-set",
                page_set,
            ),
            self.python_command(
                "export_basic_data_pages.py",
                "--algorithm-version",
                "standard_rebalance_asset_dual_nav_v10_all_channels_20260528",
                "--db-path",
                str(self.database_root / "analysis_zh_current.sqlite"),
                "--site-dir",
                str(staging_root / "basic_data"),
            ),
            self.python_command(
                "build_basic_data_report_packs.py",
                "--report-root",
                str(staging_root),
                "--db-path",
                str(self.database_root / "analysis_zh_current.sqlite"),
                "--skip-data-audit",
                "--skip-fund-enrichment",
                "--minimal-publish-only",
            ),
            self.python_command(
                "audit_basic_data_deploy_integrity.py",
                "--report-root",
                str(staging_root),
                "--db-path",
                str(self.database_root / "analysis_zh_current.sqlite"),
            ),
            self.python_command(
                "write_analysis_platform_deploy_manifest.py",
                "--deploy-dir",
                str(staging_root),
                "--page-set",
                page_set,
            ),
        ]
        for index, command in enumerate(commands, start=1):
            print(f"PROGRESS {json.dumps({'completed': index - 1, 'total': len(commands), 'unit': '个报表步骤', 'message': Path(command[4]).name if len(command) > 4 else ''}, ensure_ascii=False)}", flush=True)
            code = self.run_command(command)
            if code != 0:
                return code
        print(
            f"PROGRESS {json.dumps({'completed': len(commands), 'total': len(commands), 'unit': '个报表步骤', 'message': '最小发布源构建完成'}, ensure_ascii=False)}",
            flush=True,
        )
        manifest = staging_root / "deployment_manifest.json"
        if self.args.dry_run:
            return 0
        self.artifacts.append({"key": "staging_deployment_manifest", "path": str(manifest), "validationStatus": "passed" if manifest.is_file() else "failed"})
        return 0 if manifest.is_file() or self.args.dry_run else 3

    def database_backup(self) -> int:
        backup_policy = self.policy.get("backup") if isinstance(self.policy.get("backup"), dict) else {}
        retain = str(
            int(
                os.environ.get("ADVISOR_BACKUP_RETAIN")
                or backup_policy.get("retainSuccessfulVersions")
                or 1
            )
        )
        minimum_free_gib = str(float(backup_policy.get("minimumFreeGiB") or 45))
        result_path = self.node_run_dir / "database_backup_result.json"
        command = self.python_command(
            "backup_successful_analysis_db.py",
            "--db-path",
            str(self.database_root / "analysis_zh_current.sqlite"),
            "--backup-dir",
            str(self.backup_root),
            "--retain",
            retain,
            "--minimum-free-gib",
            minimum_free_gib,
            "--run-id",
            self.args.run_id,
            "--state-db",
            str(self.database_root / "update_state.sqlite"),
            "--require-stage",
            "process_load",
            "--require-stage",
            "data_audit",
            "--result-path",
            str(result_path),
        )
        if self.args.dry_run:
            command.append("--dry-run")
        code = self.run_command(command)
        if result_path.is_file():
            payload = read_json(result_path)
            self.artifacts.append(
                {
                    "key": "database_backup_result",
                    "path": str(result_path),
                    "validationStatus": "passed" if code == 0 else "failed",
                }
            )
            backup_path = str(payload.get("backup_path") or "")
            if backup_path:
                self.context_updates["ADVISOR_DATABASE_BACKUP_PATH"] = backup_path
            self.counters["reusedExistingBackup"] = int(
                bool(
                    payload.get("reused_existing_success")
                    or payload.get("reused_unchanged_source")
                )
            )
        return code

    def publish(self) -> int:
        runtime_config_path = self.workspace_root / "本机配置" / "runtime.local.json"
        runtime_config = read_json(runtime_config_path) if runtime_config_path.is_file() else {}
        edgeone_remote = str(
            os.environ.get("ADVISOR_EDGEONE_PUBLISH_REMOTE")
            or runtime_config.get("edgeOnePublishRemote")
            or ""
        ).strip()
        edgeone_branch = str(
            os.environ.get("ADVISOR_EDGEONE_PUBLISH_BRANCH")
            or runtime_config.get("edgeOnePublishBranch")
            or "main"
        ).strip()
        legacy_edgeone_branch = str(
            os.environ.get("ADVISOR_EDGEONE_LEGACY_SNAPSHOT_BRANCH")
            if os.environ.get("ADVISOR_EDGEONE_LEGACY_SNAPSHOT_BRANCH") is not None
            else runtime_config.get("edgeOneLegacySnapshotBranch", "")
        ).strip()
        report_scope = str(
            os.environ.get("ADVISOR_REPORT_SCOPE")
            or configured_report_scope(self.policy)
        ).strip().lower()
        source_report_root = self.report_root
        if report_scope == "minimal_publish":
            source_text = str(
                os.environ.get("ADVISOR_MINIMAL_REPORT_SOURCE_ROOT") or ""
            ).strip()
            if not source_text:
                print("[ERROR] audited minimal report source context is missing.", flush=True)
                return 3
            source_report_root = Path(source_text).resolve()
            expected_parent = (self.temp_root / "minimal_report_source").resolve()
            if source_report_root.parent != expected_parent:
                print(
                    f"[ERROR] minimal report source is outside the managed temp root: {source_report_root}",
                    flush=True,
                )
                return 3
            if not self.args.dry_run and not source_report_root.is_dir():
                print(f"[ERROR] minimal report source is missing: {source_report_root}", flush=True)
                return 3
        script = self.program("update_and_publish_minimal_set.ps1")
        command = [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-ProjectRoot",
            str(self.code_root),
            "-ReportRoot",
            str(source_report_root),
            "-PublishRoot",
            str(self.publish_root),
            "-SkipDataUpdate",
            "-SkipAudit",
            "-SkipPagesVerify",
            "-AllowDirtyPublishRepo",
            "-RunDirectory",
            str(self.node_run_dir / "publisher"),
            "-CommitMessage",
            f"Daily data update {datetime.now().strftime('%Y-%m-%d')}",
            "-EdgeOneRepositoryUrl",
            edgeone_remote,
            "-EdgeOneRepositoryBranch",
            edgeone_branch,
            "-EdgeOneSnapshotBranch",
            legacy_edgeone_branch,
        ]
        code = self.run_command(command)
        if code != 0 or self.args.dry_run:
            return code
        validation_path = self.publish_root / "package_validation.json"
        manifest_path = self.publish_root / "deployment_manifest.json"
        if not validation_path.is_file() or not manifest_path.is_file():
            print("[ERROR] minimal publish validation artifacts are missing.", flush=True)
            return 3
        validation = read_json(validation_path)
        checks = validation.get("checks") if isinstance(validation.get("checks"), dict) else {}
        policy = validation.get("policy") if isinstance(validation.get("policy"), dict) else {}
        warning_checks = list(
            policy.get("warningOnlyChecks")
            or ["activeCurrentHoldingRankMissingReferenceCount"]
        )
        for name in warning_checks:
            count = int(checks.get(name) or 0)
            self.counters[name] = count
            if count:
                warning = f"最小发布包非阻断质量缺口：{name}={count}"
                self.warnings.append(warning)
                print(f"[WARN] {warning}", flush=True)
        self.artifacts.extend(
            [
                {
                    "key": "minimal_package_validation",
                    "path": str(validation_path),
                    "validationStatus": "passed",
                },
                {
                    "key": "minimal_deployment_manifest",
                    "path": str(manifest_path),
                    "validationStatus": "passed",
                },
            ]
        )
        if report_scope == "minimal_publish" and source_report_root.exists():
            try:
                shutil.rmtree(source_report_root)
                self.context_updates["ADVISOR_MINIMAL_REPORT_SOURCE_CLEANED"] = "1"
            except OSError as exc:
                warning = f"最小发布源临时目录清理失败，将由后续保留策略回收：{type(exc).__name__}: {exc}"
                self.warnings.append(warning)
                print(f"[WARN] {warning}", flush=True)
        return 0

    def pages_verify(self) -> int:
        local_path = self.publish_root / "version.json"
        if self.args.dry_run:
            return 0
        if not local_path.is_file():
            print(f"[ERROR] local publish version is missing: {local_path}", flush=True)
            return 2
        local = json.loads(local_path.read_text(encoding="utf-8-sig"))
        base = os.environ.get("ADVISOR_PAGES_BASE_URL") or "https://mao-70r7.github.io/invest"
        remote_url = base.rstrip("/") + "/version.json?verify=" + str(int(time.time()))
        last_error = ""
        for attempt in range(1, 7):
            try:
                with urlopen(remote_url, timeout=30) as response:  # noqa: S310 - configured Pages endpoint.
                    remote = json.loads(response.read().decode("utf-8-sig"))
                if remote.get("buildId") == local.get("buildId"):
                    path = self.node_run_dir / "pages_verification.json"
                    atomic_json(path, {"local": local, "remote": remote, "url": remote_url, "attempt": attempt})
                    self.artifacts.append({"key": "pages_verification", "path": str(path), "validationStatus": "passed"})
                    return 0
                last_error = f"buildId mismatch local={local.get('buildId')} remote={remote.get('buildId')}"
            except (OSError, URLError, json.JSONDecodeError) as exc:
                last_error = str(exc)
            print(f"[WARN] Pages verification {attempt}/6 failed: {last_error}", flush=True)
            time.sleep(30)
        print(f"[ERROR] Pages verification failed: {last_error}", flush=True)
        return 2

    def runtime_cli(self, command_name: str) -> int:
        runtime_config = self.workspace_root / "本机配置" / "runtime.local.json"
        if not runtime_config.is_file():
            if command_name in {"initialize", "check"}:
                print(f"[DONE] 开发布局无需执行运行工作区 {command_name}。", flush=True)
                return 0
            print(f"[ERROR] {command_name} 只适用于包含 runtime.local.json 的迁移运行工作区。", flush=True)
            return 2
        cli = self.program("runtime_workspace_cli.py")
        return self.run_command(
            [self.python, "-u", "-X", "utf8", str(cli), "--workspace-root", str(self.workspace_root), command_name]
        )

    def migration_package(self) -> int:
        script = self.program("build_runtime_migration_package.py")
        command = [
            self.python,
            "-u",
            "-X",
            "utf8",
            str(script),
            "--code-source",
            "working-tree",
            "--report-root",
            str(self.report_root),
        ]
        device_id = os.environ.get("ADVISOR_PHYSICAL_DEVICE_ID") or os.environ.get("ADVISOR_DEVICE_ID")
        if device_id:
            command.extend(["--physical-device-id", device_id])
        destination = os.environ.get("ADVISOR_MIGRATION_DESTINATION")
        if destination:
            command.extend(["--destination", destination])
        if self.args.dry_run:
            command.append("--dry-run")
        return self.run_command(command)

    def collect_database_watermarks(self) -> None:
        if self.args.dry_run:
            return
        database = self.database_root / "analysis_zh_current.sqlite"
        if not database.is_file():
            return
        definitions = (
            ("策略业绩最新日期", "策略标准业绩净值", "交易日期"),
            ("策略持仓最新日期", "策略当前持仓", "持仓日期"),
            ("策略调仓最新日期", "策略调仓事件", "调仓日期"),
            ("基金净值最新日期", "基金日度净值", "交易日期"),
        )
        try:
            uri = f"file:{database.as_posix()}?mode=ro"
            with sqlite3.connect(uri, uri=True, timeout=60) as connection:
                existing_tables = {
                    str(row[0])
                    for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
                for key, table, column in definitions:
                    if table not in existing_tables:
                        continue
                    columns = {
                        str(row[1])
                        for row in connection.execute(f'PRAGMA table_info("{table}")')
                    }
                    if column not in columns:
                        continue
                    value = connection.execute(f'SELECT MAX("{column}") FROM "{table}"').fetchone()[0]
                    self.watermarks[key] = value
            self.watermarks["主数据库修改时间"] = datetime.fromtimestamp(
                database.stat().st_mtime
            ).astimezone().isoformat(timespec="seconds")
            self.watermarks["主数据库字节数"] = database.stat().st_size
        except (OSError, sqlite3.Error) as exc:
            warning = f"数据库水位读取失败：{type(exc).__name__}: {exc}"
            self.warnings.append(warning)
            print(f"[WARN] {warning}", flush=True)

    def execute(self) -> int:
        actions = {
            "environment_preflight": self.environment_preflight,
            "database_health": self.database_health,
            "source_readiness": self.source_readiness,
            "device_select": self.device_select,
            "ttfund_incremental": self.ttfund_incremental,
            "gffunds_performance": self.gffunds_performance,
            "gffunds_metadata": self.gffunds_metadata,
            "gffunds_collect": self.gffunds_collect,
            "gffunds_gate": self.gffunds_gate,
            "gfsec_fima_collect": self.gfsec_fima_collect,
            "gfsec_fima_gate": self.gfsec_fima_gate,
            "gfsec_fima_load": self.gfsec_fima_load,
            "southern_collect": self.southern_collect,
            "southern_gate": self.southern_gate,
            "southern_load": self.southern_load,
            "qieman_collect": self.qieman_collect,
            "qieman_gate": self.qieman_gate,
            "qieman_load": self.qieman_load,
            "qieman_history_backfill": self.qieman_history_backfill,
            "gf_supplemental_collect": self.gf_supplemental_collect,
            "gf_supplemental_gate": self.gf_supplemental_gate,
            "gf_supplemental_load": self.gf_supplemental_load,
            "component_info": self.component_info,
            "unified_postprocess": self.unified_postprocess,
            "process_load": self.process_load,
            "fund_nav": self.fund_nav,
            "fund_lookthrough": self.fund_lookthrough,
            "index_benchmark": self.index_benchmark,
            "strategy_governance": self.strategy_governance,
            "public_fund_snapshot": self.public_fund_snapshot,
            "data_audit": self.data_audit,
            "report_build": self.report_build,
            "database_backup": self.database_backup,
            "publish": self.publish,
            "pages_verify": self.pages_verify,
            "runtime_initialize": lambda: self.runtime_cli("initialize"),
            "runtime_check": lambda: self.runtime_cli("check"),
            "runtime_update": lambda: self.runtime_cli("update-code"),
            "runtime_rollback": lambda: self.runtime_cli("rollback-code"),
            "migration_package": self.migration_package,
        }
        if self.args.action not in actions:
            raise ValueError(f"unknown bridge action: {self.args.action}")
        code = actions[self.args.action]()
        if code == 0 and self.args.action in {
            "process_load",
            "fund_nav",
            "fund_lookthrough",
            "index_benchmark",
            "strategy_governance",
            "public_fund_snapshot",
            "report_build",
            "data_audit",
            "database_backup",
            "qieman_history_backfill",
        }:
            self.collect_database_watermarks()
        result = {
            "schemaVersion": 1,
            "nodeId": os.environ.get("ADVISOR_NODE_ID"),
            "runId": self.args.run_id,
            "startedAt": self.started_at,
            "finishedAt": now_text(),
            "status": "success" if code == 0 else "failed",
            "returncode": code,
            "artifacts": self.artifacts,
            "counters": self.counters,
            "watermarks": self.watermarks,
            "warnings": self.warnings,
            "error": None if code == 0 else f"action {self.args.action} exited with code {code}",
            "retryable": code in {1, 20, 22, 124},
            "contextUpdates": self.context_updates,
            "validation": {"status": "passed" if code == 0 else "failed", "detail": None if code == 0 else f"exit={code}"},
        }
        atomic_json(self.result_path, result)
        return code


def main() -> None:
    args = parse_args()
    bridge = Bridge(args)
    try:
        raise SystemExit(bridge.execute())
    except Exception as exc:  # noqa: BLE001 - node runner needs a structured failure.
        result = {
            "schemaVersion": 1,
            "nodeId": os.environ.get("ADVISOR_NODE_ID"),
            "runId": args.run_id,
            "startedAt": bridge.started_at,
            "finishedAt": now_text(),
            "status": "failed",
            "returncode": 1,
            "artifacts": bridge.artifacts,
            "counters": bridge.counters,
            "watermarks": bridge.watermarks,
            "warnings": bridge.warnings,
            "error": f"{type(exc).__name__}: {exc}",
            "retryable": False,
            "contextUpdates": bridge.context_updates,
            "validation": {"status": "failed", "detail": str(exc)},
        }
        atomic_json(bridge.result_path, result)
        print(f"[ERROR] {result['error']}", flush=True)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
