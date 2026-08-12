from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


CODE_ROOT = Path(os.environ.get("ADVISOR_CODE_ROOT") or Path.cwd()).resolve()
if not (CODE_ROOT / "AGENTS.md").is_file():
    raise RuntimeError("ADVISOR_CODE_ROOT or current working directory must be the code root containing AGENTS.md")
sys.path.insert(0, str(CODE_ROOT / "节点脚本" / "_共享组件" / "python_src"))

from advisor_monitor.collectors.gfbank_authenticated_ui import (  # noqa: E402
    CHANNEL_ID,
    CHANNEL_NAME,
    build_normalized,
    captured_at_from_files,
)
from advisor_monitor.collectors.official_apps_public import (  # noqa: E402
    OfficialAppsPublicCollector,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Parse authenticated GF Bank Android UI evidence into the common advisor data model."
    )
    parser.add_argument("--source-dir", type=Path, required=True, help="Directory containing combo*.png and detail*.xml evidence.")
    parser.add_argument("--run-id", default=None)
    parser.add_argument(
        "--preview-dir",
        type=Path,
        default=CODE_ROOT / "outputs" / "gf_channel_probe" / "gfbank_authenticated_ui",
    )
    parser.add_argument(
        "--promote",
        action="store_true",
        help="Write validated rows to normalized channel outputs and official_apps/gfbank_cgb/outputs.",
    )
    return parser.parse_args()


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{datetime.now().timestamp():.0f}.tmp")
    try:
        temp.write_text(text, encoding="utf-8")
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def read_jsonl_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            text = line.strip()
            if text:
                rows.append(json.loads(text))
    return rows


def load_authenticated_cache(
    cache_dir: Path,
    entities: list[str],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any], dict[str, Any]]:
    normalized = {
        entity: read_jsonl_rows(cache_dir / f"{entity}.jsonl")
        for entity in entities
    }
    summary_path = cache_dir / "latest_summary.json"
    inventory_path = cache_dir / "source_inventory.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8-sig")) if summary_path.is_file() else {}
    inventory = json.loads(inventory_path.read_text(encoding="utf-8-sig")) if inventory_path.is_file() else {}
    return normalized, summary, inventory


def values_conflict(first: Any, second: Any, tolerance: float = 1e-6) -> bool:
    if first is None or second is None:
        return False
    try:
        return abs(float(first) - float(second)) > tolerance
    except (TypeError, ValueError):
        return first != second


def merge_row_fields(previous: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(previous)
    merged.update({key: value for key, value in incoming.items() if value is not None})
    return merged


def canonicalize_authenticated_strategy_aliases(
    existing: dict[str, list[dict[str, Any]]],
    incoming: dict[str, list[dict[str, Any]]],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]], list[dict[str, str]]]:
    """Map shortened list-card names back to an observed official detail name."""
    anchors_by_list_name: dict[str, list[dict[str, Any]]] = {}
    all_masters = [*existing.get("strategy_master", []), *incoming.get("strategy_master", [])]
    for row in all_masters:
        extra = row.get("extra") if isinstance(row.get("extra"), dict) else {}
        if not extra.get("detail_observed"):
            continue
        list_name = str(extra.get("list_strategy_name") or "").strip()
        if list_name:
            anchors_by_list_name.setdefault(list_name, []).append(row)
    alias_map: dict[str, tuple[str, str]] = {}
    remaps: list[dict[str, str]] = []
    for row in all_masters:
        extra = row.get("extra") if isinstance(row.get("extra"), dict) else {}
        if extra.get("detail_observed"):
            continue
        alias_id = str(row.get("source_strategy_id") or "")
        alias_name = str(row.get("strategy_name") or "").strip()
        candidates = anchors_by_list_name.get(alias_name, [])
        canonical_ids = {
            (str(candidate.get("source_strategy_id") or ""), str(candidate.get("strategy_name") or ""))
            for candidate in candidates
            if candidate.get("source_strategy_id") and candidate.get("strategy_name")
        }
        if alias_id and len(canonical_ids) == 1:
            canonical_id, canonical_name = next(iter(canonical_ids))
            if alias_id != canonical_id:
                alias_map[alias_id] = (canonical_id, canonical_name)
                remaps.append(
                    {
                        "alias_source_strategy_id": alias_id,
                        "alias_strategy_name": alias_name,
                        "canonical_source_strategy_id": canonical_id,
                        "canonical_strategy_name": canonical_name,
                    }
                )

    def remap(dataset: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
        result: dict[str, list[dict[str, Any]]] = {}
        for entity, rows in dataset.items():
            result[entity] = []
            for original in rows:
                row = dict(original)
                alias = alias_map.get(str(row.get("source_strategy_id") or ""))
                if alias:
                    row["source_strategy_id"] = alias[0]
                    if entity == "strategy_master":
                        row["strategy_name"] = alias[1]
                result[entity].append(row)
        return result

    return remap(existing), remap(incoming), remaps


def merge_authenticated_normalized(
    existing: dict[str, list[dict[str, Any]]],
    incoming: dict[str, list[dict[str, Any]]],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """Merge a partial authenticated capture without shrinking validated history."""
    existing, incoming, alias_remaps = canonicalize_authenticated_strategy_aliases(existing, incoming)
    entities = sorted(set(existing) | set(incoming))
    merged: dict[str, list[dict[str, Any]]] = {}
    conflicts: list[dict[str, Any]] = []
    stale_interval_rows_ignored = 0
    interval_window_updates = 0

    def merge_by_key(
        entity: str,
        key_fields: tuple[str, ...],
        conflict_fields: tuple[str, ...] = (),
    ) -> list[dict[str, Any]]:
        by_key: dict[tuple[str, ...], dict[str, Any]] = {}
        for source_name, rows in (("existing", existing.get(entity, [])), ("incoming", incoming.get(entity, []))):
            for index, row in enumerate(rows):
                key = tuple(str(row.get(field) or "") for field in key_fields)
                if not all(key):
                    key = (*key, source_name, str(index))
                previous = by_key.get(key)
                if previous is not None:
                    differing = [
                        field
                        for field in conflict_fields
                        if values_conflict(previous.get(field), row.get(field))
                    ]
                    if differing:
                        conflicts.append(
                            {
                                "entity": entity,
                                "key": list(key),
                                "fields": differing,
                                "existing": {field: previous.get(field) for field in differing},
                                "incoming": {field: row.get(field) for field in differing},
                            }
                        )
                    candidate = merge_row_fields(previous, row)
                    if entity == "strategy_master":
                        previous_extra = previous.get("extra") if isinstance(previous.get("extra"), dict) else {}
                        incoming_extra = row.get("extra") if isinstance(row.get("extra"), dict) else {}
                        previous_has_detail = bool(previous_extra.get("detail_observed"))
                        incoming_has_detail = bool(incoming_extra.get("detail_observed"))
                        if previous_has_detail and not incoming_has_detail:
                            # A partial capture sees several list cards but opens
                            # only the requested product.  Card-only facts must
                            # never downgrade a previously validated detail row.
                            candidate = merge_row_fields(row, previous)
                            candidate["last_seen_at"] = max(
                                str(previous.get("last_seen_at") or ""),
                                str(row.get("last_seen_at") or ""),
                            ) or None
                            candidate["run_id"] = row.get("run_id") or previous.get("run_id")
                        merged_extra = merge_row_fields(previous_extra, incoming_extra)
                        if previous_has_detail and not incoming_has_detail:
                            merged_extra = merge_row_fields(incoming_extra, previous_extra)
                        merged_extra["detail_observed"] = previous_has_detail or incoming_has_detail
                        candidate["extra"] = merged_extra
                    if entity == "strategy_performance_daily":
                        previous_is_curve = previous.get("section_type") == "gfbank_authenticated_ui_curve_tooltip"
                        incoming_is_curve = row.get("section_type") == "gfbank_authenticated_ui_curve_tooltip"
                        if previous_is_curve and not incoming_is_curve:
                            candidate = merge_row_fields(row, previous)
                    by_key[key] = candidate
                else:
                    by_key[key] = dict(row)
        return list(by_key.values())

    merged["strategy_master"] = merge_by_key("strategy_master", ("source_strategy_id",))
    master_by_id = {
        str(row.get("source_strategy_id")): row
        for row in merged["strategy_master"]
        if row.get("source_strategy_id")
    }
    for strategy_id, row in master_by_id.items():
        prior_rows = [
            item
            for item in [*existing.get("strategy_master", []), *incoming.get("strategy_master", [])]
            if str(item.get("source_strategy_id")) == strategy_id
        ]
        first_seen = sorted(str(item.get("first_seen_at")) for item in prior_rows if item.get("first_seen_at"))
        last_seen = sorted(str(item.get("last_seen_at")) for item in prior_rows if item.get("last_seen_at"))
        if first_seen:
            row["first_seen_at"] = first_seen[0]
        if last_seen:
            row["last_seen_at"] = last_seen[-1]

    merged["strategy_performance_daily"] = merge_by_key(
        "strategy_performance_daily",
        ("source_strategy_id", "trade_date"),
        ("nav", "cumulative_return", "benchmark_return"),
    )
    curve_rows_by_strategy: dict[str, list[dict[str, Any]]] = {}
    for row in merged["strategy_performance_daily"]:
        if row.get("section_type") == "gfbank_authenticated_ui_curve_tooltip":
            curve_rows_by_strategy.setdefault(str(row.get("source_strategy_id")), []).append(row)
    for rows in curve_rows_by_strategy.values():
        previous_nav: float | None = None
        for row in sorted(rows, key=lambda item: str(item.get("trade_date") or "")):
            nav = row.get("nav")
            row["daily_return"] = (
                round((float(nav) / previous_nav - 1.0) * 100.0, 8)
                if nav is not None and previous_nav not in (None, 0.0)
                else None
            )
            previous_nav = float(nav) if nav is not None else None

    interval_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for source_name, rows in (
        ("existing", existing.get("strategy_performance_interval", [])),
        ("incoming", incoming.get("strategy_performance_interval", [])),
    ):
        for row in rows:
            key = (str(row.get("source_strategy_id") or ""), str(row.get("interval_code") or ""))
            previous = interval_by_key.get(key)
            if previous is None:
                interval_by_key[key] = dict(row)
                continue
            previous_date = str(previous.get("as_of_date") or "")
            incoming_date = str(row.get("as_of_date") or "")
            previous_start = str(previous.get("interval_start_date") or "")
            incoming_start = str(row.get("interval_start_date") or "")
            if source_name == "incoming" and incoming_date and previous_date and incoming_date < previous_date:
                stale_interval_rows_ignored += 1
                continue
            same_window = incoming_date == previous_date and incoming_start == previous_start
            if incoming_date == previous_date and incoming_start and previous_start and incoming_start != previous_start:
                interval_window_updates += 1
            if same_window:
                differing = [
                    field
                    for field in ("return_value", "benchmark_return")
                    if values_conflict(previous.get(field), row.get(field))
                ]
                if differing:
                    conflicts.append(
                        {
                            "entity": "strategy_performance_interval",
                            "key": list(key),
                            "fields": differing,
                            "existing": {field: previous.get(field) for field in differing},
                            "incoming": {field: row.get(field) for field in differing},
                        }
                    )
            interval_by_key[key] = merge_row_fields(previous, row)
    merged["strategy_performance_interval"] = list(interval_by_key.values())

    merged["app_public_entry"] = merge_by_key(
        "app_public_entry",
        ("channel_id", "source_url", "access_level"),
    )
    handled = {
        "strategy_master",
        "strategy_performance_daily",
        "strategy_performance_interval",
        "app_public_entry",
    }
    for entity in entities:
        if entity in handled:
            continue
        rows = [*existing.get(entity, []), *incoming.get(entity, [])]
        by_payload = {
            json.dumps(row, ensure_ascii=False, sort_keys=True): dict(row)
            for row in rows
        }
        merged[entity] = list(by_payload.values())

    for entity, rows in merged.items():
        rows.sort(
            key=lambda row: (
                str(row.get("source_strategy_id") or ""),
                str(row.get("trade_date") or row.get("as_of_date") or ""),
                str(row.get("interval_code") or row.get("source_url") or ""),
            )
        )
    existing_counts = {entity: len(existing.get(entity, [])) for entity in entities}
    incoming_counts = {entity: len(incoming.get(entity, [])) for entity in entities}
    merged_counts = {entity: len(merged.get(entity, [])) for entity in entities}
    regressions = [
        {
            "entity": entity,
            "existing_count": existing_counts[entity],
            "merged_count": merged_counts[entity],
        }
        for entity in entities
        if merged_counts[entity] < existing_counts[entity]
        and not (
            entity == "strategy_master"
            and existing_counts[entity] - merged_counts[entity] <= len(alias_remaps)
        )
    ]
    return merged, {
        "mode": "business_key_incremental_merge",
        "existing_counts": existing_counts,
        "incoming_counts": incoming_counts,
        "merged_counts": merged_counts,
        "conflict_total": len(conflicts),
        "conflicts": conflicts[:50],
        "history_regression_total": len(regressions),
        "history_regressions": regressions,
        "stale_interval_rows_ignored": stale_interval_rows_ignored,
        "interval_window_updates": interval_window_updates,
        "strategy_alias_remap_total": len(alias_remaps),
        "strategy_alias_remaps": alias_remaps,
        "intentional_alias_consolidation_total": max(
            0,
            existing_counts.get("strategy_master", 0) - merged_counts.get("strategy_master", 0),
        ),
    }


def refresh_summary_from_merged(
    summary: dict[str, Any],
    normalized: dict[str, list[dict[str, Any]]],
    merge_diagnostics: dict[str, Any],
) -> None:
    strategies = normalized.get("strategy_master", [])
    daily_rows = normalized.get("strategy_performance_daily", [])
    interval_rows = normalized.get("strategy_performance_interval", [])
    strategy_total = len(strategies)
    curve_strategy_ids = {
        str(row.get("source_strategy_id"))
        for row in daily_rows
        if row.get("section_type") == "gfbank_authenticated_ui_curve_tooltip"
    }
    detail_strategy_total = sum(
        1
        for row in strategies
        if isinstance(row.get("extra"), dict)
        and (
            row["extra"].get("detail_observed")
            or row["extra"].get("performance_disclosure_status")
        )
    )
    performance_eligible_strategies = [
        row
        for row in strategies
        if str((row.get("extra") or {}).get("strategy_entry") or "理财组合") == "理财组合"
    ]
    performance_eligible_ids = {
        str(row.get("source_strategy_id")) for row in performance_eligible_strategies
    }
    interval_strategy_total = len(
        {
            str(row.get("source_strategy_id"))
            for row in interval_rows
            if row.get("return_value") is not None
        }
    )
    detail_ratio = detail_strategy_total / strategy_total if strategy_total else 0.0
    curve_ratio = (
        len(curve_strategy_ids & performance_eligible_ids) / len(performance_eligible_ids)
        if performance_eligible_ids else 1.0
    )
    interval_ratio = (
        len(
            {
                str(row.get("source_strategy_id"))
                for row in interval_rows
                if row.get("return_value") is not None
            }
            & performance_eligible_ids
        )
        / len(performance_eligible_ids)
        if performance_eligible_ids else 1.0
    )
    benchmark_description_total = sum(
        1 for row in strategies if str(row.get("benchmark") or "").strip()
    )
    benchmark_eligible_total = len(performance_eligible_ids)
    benchmark_eligible_covered = sum(
        1
        for row in performance_eligible_strategies
        if str(row.get("benchmark") or "").strip()
    )
    benchmark_description_ratio = (
        benchmark_eligible_covered / benchmark_eligible_total
        if benchmark_eligible_total else 1.0
    )
    strategy_entry_counts: dict[str, int] = {}
    for row in strategies:
        extra = row.get("extra") if isinstance(row.get("extra"), dict) else {}
        entry_label = str(extra.get("strategy_entry") or "理财组合")
        strategy_entry_counts[entry_label] = strategy_entry_counts.get(entry_label, 0) + 1
    summary.update(
        {
            "strategy_total": strategy_total,
            "daily_performance_rows": len(daily_rows),
            "curve_point_total": sum(
                1
                for row in daily_rows
                if row.get("section_type") == "gfbank_authenticated_ui_curve_tooltip"
            ),
            "curve_strategy_total": len(curve_strategy_ids),
            "curve_strategy_coverage_ratio": round(curve_ratio, 6),
            "interval_performance_rows": len(interval_rows),
            "detail_coverage_ratio": round(detail_ratio, 6),
            "interval_strategy_coverage_ratio": round(interval_ratio, 6),
            "benchmark_description_strategy_total": benchmark_description_total,
            "benchmark_description_eligible_strategy_total": benchmark_eligible_total,
            "benchmark_description_coverage_ratio": round(benchmark_description_ratio, 6),
            "strategy_entry_counts": strategy_entry_counts,
            "strategy_master_ok": strategy_total > 0,
            "daily_performance_ok": bool(daily_rows),
            "interval_performance_ok": bool(interval_rows),
            "collection_status": (
                "success_authenticated_ui_full_curve"
                if detail_ratio >= 0.999 and curve_ratio >= 0.999
                else "success_authenticated_ui_full_detail"
                if detail_ratio >= 0.999
                else "success_authenticated_ui_partial"
            ),
            "cache_merge": merge_diagnostics,
        }
    )


def merge_authenticated_inventory(
    previous: dict[str, Any],
    incoming: dict[str, Any],
    merge_diagnostics: dict[str, Any],
) -> dict[str, Any]:
    merged = {**previous, **incoming}
    for field in ("source_files", "preserved_raw_files"):
        values = [*previous.get(field, []), *incoming.get(field, [])]
        merged[field] = list(dict.fromkeys(str(value) for value in values))
    merged["cache_merge"] = merge_diagnostics
    merged["validated_capture_batches"] = int(previous.get("validated_capture_batches") or 0) + 1
    return merged


def write_preview(run_dir: Path, normalized: dict[str, list[dict[str, Any]]], payload: dict[str, Any]) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    for entity, rows in normalized.items():
        atomic_write_text(
            run_dir / f"{entity}.jsonl",
            "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        )
    atomic_write_text(run_dir / "summary.json", json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def write_authenticated_cache(
    cache_dir: Path,
    normalized: dict[str, list[dict[str, Any]]],
    summary: dict[str, Any],
    inventory: dict[str, Any],
) -> None:
    """Keep the last validated login-state facts separate from public refreshes."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    for entity, rows in normalized.items():
        atomic_write_text(
            cache_dir / f"{entity}.jsonl",
            "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        )
    atomic_write_text(cache_dir / "latest_summary.json", json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    atomic_write_text(cache_dir / "source_inventory.json", json.dumps(inventory, ensure_ascii=False, indent=2) + "\n")


def copy_safe_evidence(source_dir: Path, raw_dir: Path) -> list[Path]:
    selected = sorted(
        {
            *source_dir.glob("combo*.png"),
            *source_dir.glob("combo*.xml"),
            *source_dir.glob("detail*.xml"),
            *source_dir.glob("detail*.png"),
            *source_dir.glob("curve*.xml"),
            *source_dir.glob("curve*.json"),
            *source_dir.glob("special*.png"),
            *source_dir.glob("special*.xml"),
            *source_dir.glob("gfzt*.png"),
            *source_dir.glob("gfzt*.xml"),
            *source_dir.glob("zt*.png"),
            *source_dir.glob("zt*.xml"),
            *source_dir.glob("capture_summary.json"),
        }
    )
    raw_dir.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    for source in selected:
        target = raw_dir / source.name
        shutil.copy2(source, target)
        copied.append(target)
    return copied


def read_capture_batch_diagnostics(source_dir: Path) -> dict[str, Any] | None:
    """Expose partial batch failures without discarding successfully validated strategies."""
    path = source_dir / "capture_summary.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "summary_file": path.name,
            "summary_read_error": f"{type(exc).__name__}: {exc}",
            "partial_failure": None,
        }
    requested = [str(value) for value in payload.get("requested_strategy_names", []) if value]
    captured = [str(value) for value in payload.get("captured_strategy_names", []) if value]
    missing = [str(value) for value in payload.get("missing_requested_strategy_names", []) if value]
    requested_entries = [str(value) for value in payload.get("strategy_entries_requested", []) if value]
    missing_entries = [str(value) for value in payload.get("missing_strategy_entries", []) if value]
    failures = [value for value in payload.get("failures", []) if isinstance(value, dict)]
    failure_total = int(payload.get("failure_total") or len(failures))
    return {
        "summary_file": path.name,
        "run_id": payload.get("run_id"),
        "status": payload.get("status"),
        "requested_strategy_total": len(requested),
        "captured_strategy_total": len(captured),
        "requested_strategy_names": requested,
        "captured_strategy_names": captured,
        "missing_requested_strategy_names": missing,
        "strategy_entries_requested": requested_entries,
        "captured_strategy_counts_by_entry": payload.get("captured_strategy_counts_by_entry") or {},
        "missing_strategy_entries": missing_entries,
        "benchmark_disclosure_total": int(payload.get("benchmark_disclosure_total") or 0),
        "benchmark_disclosure_success_total": int(payload.get("benchmark_disclosure_success_total") or 0),
        "benchmark_disclosures": payload.get("benchmark_disclosures") or [],
        "failure_total": failure_total,
        "failures": failures,
        "partial_failure": bool(failure_total or missing or missing_entries),
    }


def validate_curve_capture_manifests(source_dir: Path) -> tuple[list[str], list[dict[str, Any]]]:
    errors: list[str] = []
    summaries: list[dict[str, Any]] = []
    for path in sorted(source_dir.glob("curve_*_manifest.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            errors.append("curve_capture_manifest_invalid_json")
            continue
        summary = {
            "file": path.name,
            "strategy_name": payload.get("strategy_name"),
            "value_source": payload.get("value_source"),
            "failure_total": int(payload.get("failure_total") or 0),
            "verification_total": int(payload.get("ocr_verification_total") or 0),
            "verification_mismatch_total": int(payload.get("ocr_verification_mismatch_total") or 0),
            "video_transport_deleted": payload.get("video_transport_deleted"),
            "video_value_conflict_total": int(
                ((payload.get("video_capture") or {}).get("value_conflict_total")) or 0
            ),
        }
        summaries.append(summary)
        if summary["failure_total"] > 0:
            errors.append("curve_capture_manifest_reports_failure")
        if summary["verification_mismatch_total"] > 0:
            errors.append("recorded_ocr_exact_verification_mismatch")
        if summary["value_source"] == "screen_recording_ocr_periodically_verified_against_uiautomator":
            if summary["verification_total"] < 1:
                errors.append("recorded_ocr_missing_exact_verification")
            if summary["video_transport_deleted"] is not True:
                errors.append("recorded_ocr_video_transport_not_deleted")
            if summary["video_value_conflict_total"] > 0:
                errors.append("recorded_ocr_same_date_value_conflict")
    if list(source_dir.glob("*.mkv")):
        errors.append("recorded_ocr_video_file_still_present")
    return sorted(set(errors)), summaries


def main() -> int:
    args = parse_args()
    source_dir = args.source_dir.resolve()
    if not source_dir.is_dir():
        raise FileNotFoundError(source_dir)
    image_paths = sorted(source_dir.glob("combo*.png"))
    detail_paths = sorted(source_dir.glob("detail*.xml"))
    curve_point_paths = sorted(source_dir.glob("curve*.xml"))
    special_entry_paths = sorted(source_dir.glob("special*.xml"))
    evidence_paths = [*image_paths, *detail_paths, *curve_point_paths, *special_entry_paths]
    if not image_paths:
        raise RuntimeError("no combo*.png screenshots found")
    if not detail_paths and not special_entry_paths:
        raise RuntimeError("no detail*.xml or special*.xml captures found")

    captured_at = captured_at_from_files(evidence_paths)
    run_id = args.run_id or datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z")
    run_dir = args.preview_dir / run_id
    capture_summary: dict[str, Any] | None = None
    capture_summary_path = source_dir / "capture_summary.json"
    if capture_summary_path.is_file():
        try:
            loaded_summary = json.loads(capture_summary_path.read_text(encoding="utf-8-sig"))
            if isinstance(loaded_summary, dict):
                capture_summary = loaded_summary
        except (OSError, json.JSONDecodeError):
            # The existing batch diagnostics below reports the malformed file;
            # normalization remains able to preserve independently valid XML.
            capture_summary = None
    normalized, diagnostics = build_normalized(
        image_paths=image_paths,
        detail_paths=detail_paths,
        curve_point_paths=curve_point_paths,
        special_entry_paths=special_entry_paths,
        capture_summary=capture_summary,
        run_id=run_id,
        captured_at=captured_at,
    )
    capture_manifest_errors, capture_manifest_summaries = validate_curve_capture_manifests(source_dir)
    diagnostics["curve_capture_manifests"] = capture_manifest_summaries
    diagnostics["capture_batch"] = read_capture_batch_diagnostics(source_dir)
    validation_errors: list[str] = []
    validation_errors.extend(capture_manifest_errors)
    if diagnostics["strategy_total"] <= 0:
        validation_errors.append("strategy_master_empty")
    if diagnostics["detail_total"] <= 0 and diagnostics["special_entry_strategy_total"] <= 0:
        validation_errors.append("strategy_detail_empty")
    if diagnostics["duplicate_strategy_names"]:
        validation_errors.append("duplicate_strategy_names")
    if (
        diagnostics["minimum_product_ocr_confidence"] is not None
        and diagnostics["minimum_product_ocr_confidence"] < 0.85
    ):
        validation_errors.append("product_ocr_confidence_too_low")
    if diagnostics["nav_cumulative_return_mismatches"]:
        validation_errors.append("nav_cumulative_return_scale_mismatch")
    if curve_point_paths and diagnostics["curve_point_total"] != len(curve_point_paths):
        validation_errors.append("curve_point_xml_parse_incomplete")
    if diagnostics["curve_point_conflicts"]:
        validation_errors.append("curve_point_value_conflict")
    if diagnostics["curve_latest_detail_mismatches"]:
        validation_errors.append("curve_latest_point_not_aligned_with_detail")
    if curve_point_paths and any(
        int(count) < 2
        for count in diagnostics["curve_point_counts_by_strategy"].values()
    ):
        validation_errors.append("curve_has_fewer_than_two_distinct_dates")

    detail_coverage_ratio = (
        (diagnostics["detail_strategy_total"] + diagnostics["special_entry_strategy_total"])
        / diagnostics["strategy_total"]
        if diagnostics["strategy_total"]
        else 0.0
    )
    interval_coverage_ratio = (
        len(
            {
                row["source_strategy_id"]
                for row in normalized["strategy_performance_interval"]
                if row.get("return_value") is not None
            }
        )
        / diagnostics["strategy_total"]
        if diagnostics["strategy_total"]
        else 0.0
    )
    summary = {
        "channel_id": CHANNEL_ID,
        "channel_name": CHANNEL_NAME,
        "run_id": run_id,
        "captured_at": captured_at.isoformat(timespec="seconds"),
        "collection_status": (
            "success_authenticated_ui_full_curve"
            if not validation_errors and detail_coverage_ratio >= 0.999 and diagnostics["curve_strategy_coverage_ratio"] >= 0.999
            else "success_authenticated_ui_full_detail"
            if not validation_errors and detail_coverage_ratio >= 0.999
            else "success_authenticated_ui_partial"
            if not validation_errors
            else "failed_validation"
        ),
        "holding_penetration_status": "authenticated_strategy_master_and_detail_performance_no_verified_fund_holdings",
        "strategy_total": diagnostics["strategy_total"],
        "current_holding_rows": 0,
        "rebalance_event_total": 0,
        "daily_performance_rows": diagnostics["daily_performance_rows"],
        "curve_point_total": diagnostics["curve_point_total"],
        "curve_strategy_total": diagnostics["curve_strategy_total"],
        "curve_strategy_coverage_ratio": diagnostics["curve_strategy_coverage_ratio"],
        "interval_performance_rows": diagnostics["interval_performance_rows"],
        "benchmark_description_strategy_total": diagnostics["benchmark_description_strategy_total"],
        "benchmark_description_eligible_strategy_total": diagnostics[
            "benchmark_description_eligible_strategy_total"
        ],
        "benchmark_description_coverage_ratio": diagnostics["benchmark_description_coverage_ratio"],
        "strategy_entry_counts": diagnostics["strategy_entry_counts"],
        "detail_coverage_ratio": round(detail_coverage_ratio, 6),
        "interval_strategy_coverage_ratio": round(interval_coverage_ratio, 6),
        "strategy_master_ok": diagnostics["strategy_total"] > 0,
        "daily_performance_ok": diagnostics["daily_performance_rows"] > 0,
        "interval_performance_ok": diagnostics["interval_performance_rows"] > 0,
        "fund_level_position_ok": False,
        "rebalance_event_ok": False,
        "rebalance_fund_delta_ok": False,
        "known_gap": (
            "登录态页面已按广发智投入口提取理财组合、超级定投和目标盈中本批实际可见的策略列表、详情及页面明确披露的区间收益；"
            + (
                "成立以来走势图已通过可滑动提示框取得多个结构化历史点；"
                if diagnostics["curve_point_total"] > 0
                else "尚未取得成立以来走势图历史点；"
            )
            + (
                "业绩基准说明已通过详情页披露弹层取得；"
                if diagnostics["benchmark_description_coverage_ratio"] >= 0.999
                else "业绩基准说明仍有策略未取得；"
            )
            + "尚未取得渠道官方策略ID、基金代码与权重、调仓事件和调仓明细。"
            "不得将基金备选库或列表卡片推断为当前持仓。"
        ),
        "diagnostics": diagnostics,
        "validation_errors": validation_errors,
        "source_dir": str(source_dir),
        "promoted": False,
    }
    inventory = {
        "method": "使用外接真机逐一进入广发智投的理财组合、超级定投和目标盈入口，以登录态 UI 截图识别策略卡片并解析 UIAutomator 详情页 XML；成立以来走势图可用 UIAutomator 逐点精读，或用 scrcpy 录制固定触控序列并 OCR 读取明确提示文字，同时周期性与 UIAutomator 精确文本核对。曲线线条像素不参与收益反推。",
        "source_files": [str(path) for path in evidence_paths],
        "access_level": "login",
        "device_package": "com.cgbchina.xpt",
        "field_boundaries": {
            "strategy_master": "可用",
            "strategy_performance_daily": (
                "成立以来走势图触控提示框明确披露并通过精确锚点校验的日期、组合涨跌幅和基准涨跌幅"
                if diagnostics["curve_point_total"] > 0
                else "详情页明确披露的最新净值点"
            ),
            "strategy_performance_interval": "详情页近1月、近6月、近1年、成立以来标签的明确披露值",
            "strategy_fund_snapshot": "不可用",
            "strategy_rebalance_event": "不可用",
            "strategy_rebalance_fund_delta": "不可用",
        },
    }
    write_preview(run_dir, normalized, {**summary, "inventory": inventory})

    if args.promote:
        if validation_errors:
            print(json.dumps({"status": "blocked", "errors": validation_errors, "preview_dir": str(run_dir)}, ensure_ascii=False, indent=2))
            return 2
        cache_dir = CODE_ROOT / "official_apps" / CHANNEL_ID / "authenticated_cache"
        existing_normalized, _existing_summary, existing_inventory = load_authenticated_cache(
            cache_dir,
            list(normalized),
        )
        promoted_normalized, merge_diagnostics = merge_authenticated_normalized(
            existing_normalized,
            normalized,
        )
        if merge_diagnostics["conflict_total"] or merge_diagnostics["history_regression_total"]:
            merge_errors = []
            if merge_diagnostics["conflict_total"]:
                merge_errors.append("authenticated_cache_business_value_conflict")
            if merge_diagnostics["history_regression_total"]:
                merge_errors.append("authenticated_cache_history_regression")
            summary["validation_errors"] = merge_errors
            summary["cache_merge"] = merge_diagnostics
            write_preview(run_dir, normalized, {**summary, "inventory": inventory})
            print(
                json.dumps(
                    {
                        "status": "blocked",
                        "errors": merge_errors,
                        "cache_merge": merge_diagnostics,
                        "preview_dir": str(run_dir),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 2
        raw_dir = CODE_ROOT / "data" / "raw" / CHANNEL_ID / "authenticated_ui" / captured_at.date().isoformat() / run_id
        copied = copy_safe_evidence(source_dir, raw_dir)
        inventory["preserved_raw_files"] = [str(path) for path in copied]
        inventory = merge_authenticated_inventory(existing_inventory, inventory, merge_diagnostics)
        refresh_summary_from_merged(summary, promoted_normalized, merge_diagnostics)
        collector = OfficialAppsPublicCollector(CODE_ROOT, run_id=run_id)
        collector.run_at = captured_at
        collector.day = captured_at.date().isoformat()
        collector.captured_at = captured_at.isoformat(timespec="seconds")
        collector.write_normalized_entities(CHANNEL_ID, promoted_normalized)
        summary["promoted"] = True
        write_authenticated_cache(cache_dir, promoted_normalized, summary, inventory)
        summary["output_paths"] = collector.write_app_outputs(
            CHANNEL_ID,
            promoted_normalized,
            summary,
            inventory,
        )
        write_preview(run_dir, promoted_normalized, {**summary, "inventory": inventory})

    print(
        json.dumps(
            {
                "status": "success" if not validation_errors else "failed_validation",
                "preview_dir": str(run_dir),
                "promoted": bool(args.promote and not validation_errors),
                "diagnostics": diagnostics,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if not validation_errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
