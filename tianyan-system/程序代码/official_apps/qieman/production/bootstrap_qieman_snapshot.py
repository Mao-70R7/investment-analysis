from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


ENTITIES = (
    "strategy_master",
    "strategy_benchmark",
    "strategy_fund_snapshot",
    "strategy_performance_daily",
    "strategy_rebalance_event",
    "strategy_rebalance_fund_delta",
    "strategy_fund_snapshot_history",
    "signal_strategy_event",
    "signal_fund_instruction",
    "signal_rebalance_projection_event",
    "signal_rebalance_projection_delta",
    "strategy_coverage",
    "strategy_incomplete_requested_data",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Promote one already-audited Qieman full snapshot into the runtime normalized layout.")
    parser.add_argument("--source-run-dir", type=Path, required=True)
    parser.add_argument("--enriched-master", type=Path, required=True)
    parser.add_argument("--catalog-summary", type=Path, required=True)
    parser.add_argument("--normalized-root", type=Path, required=True)
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--db-path", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--result-path", type=Path, required=True)
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"object expected: {path}")
    return value


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def atomic_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + f".{os.getpid()}.tmp")
    shutil.copy2(source, temporary)
    temporary.replace(target)


def current_ids(db_path: Path) -> set[str]:
    if not db_path.is_file():
        return set()
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=60) as connection:
        return {
            str(row[0]).strip()
            for row in connection.execute(
                'SELECT "渠道策略ID" FROM "策略信息" WHERE "渠道ID"=?',
                ("qieman",),
            )
            if str(row[0] or "").strip()
        }


def main() -> None:
    args = parse_args()
    source_run = args.source_run_dir.resolve()
    normalized_root = args.normalized_root.resolve()
    run_id = str(args.run_id).strip()
    quality = read_json(source_run / "normalized_quality_report.json")
    audit = read_json(source_run / "qieman_data_audit_report.json")
    history = read_json(source_run / "summary.json")
    catalog = read_json(args.catalog_summary.resolve())
    if audit.get("status") not in {"passed", "warn"} or int(audit.get("error_count") or 0):
        raise SystemExit("source Qieman snapshot has not passed its isolated audit")
    masters = list(iter_jsonl(args.enriched_master.resolve()))
    ids = sorted({str(row.get("source_strategy_id") or "").strip() for row in masters if row.get("source_strategy_id")})
    if not ids or len(ids) != len(masters):
        raise SystemExit("enriched Qieman master has blank or duplicate IDs")
    if int(quality.get("catalog_strategy_count") or 0) != len(ids):
        raise SystemExit("enriched master count differs from audited source snapshot")

    published: dict[str, str] = {}
    for entity in ENTITIES:
        source = args.enriched_master.resolve() if entity == "strategy_master" else source_run / "normalized" / f"{entity}.jsonl"
        if not source.is_file():
            raise SystemExit(f"missing source entity: {entity}")
        target = normalized_root / "qieman" / entity / run_id / f"{run_id}.jsonl"
        atomic_copy(source, target)
        published[entity] = str(target)

    old_ids = current_ids(args.db_path.resolve())
    new_ids = sorted(set(ids) - old_ids)
    query_summaries = catalog.get("query_summaries") if isinstance(catalog.get("query_summaries"), list) else []
    discovery_complete = bool(query_summaries) and all(
        item.get("pages") and item["pages"][-1].get("has_more") is False
        for item in query_summaries
        if isinstance(item, dict)
    )
    summary = {
        "state": "qieman_bootstrap_snapshot_promoted",
        "channel_id": "qieman",
        "run_id": run_id,
        "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "catalog_complete": False,
        "catalog_boundary": catalog.get("catalog_boundary"),
        "catalog_discovery_complete": discovery_complete,
        "catalog_batch_closed": history.get("state") == "signed_history_catalog_complete"
        and int(history.get("completeStrategyCount") or 0) == len(ids),
        "strategy_total": len(ids),
        "catalog_strategy_total": len(ids),
        "catalog_strategy_ids": ids,
        "catalog_new_strategy_total": len(new_ids),
        "catalog_new_strategy_ids": new_ids,
        "catalog_new_strategy_collected_total": len(new_ids),
        "catalog_new_strategy_collected_ids": new_ids,
        "catalog_batch_missing_strategy_total": 0,
        "catalog_batch_missing_strategy_ids": [],
        "incremental_strategy_total": 0,
        "bootstrap_strategy_total": len(ids),
        "counts": quality.get("counts") or {},
        "coverage": quality.get("coverage") or {},
        "audit_status": audit.get("status"),
        "audit_error_count": int(audit.get("error_count") or 0),
        "audit_warning_count": int(audit.get("warning_count") or 0),
        "history_run_dir": str(source_run),
        "normalized_paths": published,
        "quality_report_path": str(source_run / "normalized_quality_report.json"),
        "audit_report_path": str(source_run / "qieman_data_audit_report.json"),
    }
    summary_path = normalized_root / "qieman" / "collection_summary" / run_id / f"{run_id}.json"
    atomic_json(summary_path, summary)
    accepted_state = args.raw_root.resolve() / "qieman" / "accepted_state.json"
    atomic_json(
        accepted_state,
        {
            "run_id": run_id,
            "history_run_dir": str(source_run),
            "summary_path": str(summary_path),
            "accepted_at": summary["captured_at"],
            "bootstrap_external_source": True,
        },
    )
    result = {**summary, "summary_path": str(summary_path), "accepted_state_path": str(accepted_state)}
    atomic_json(args.result_path.resolve(), result)
    print(json.dumps({key: value for key, value in result.items() if key not in {"catalog_strategy_ids", "normalized_paths"}}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
