from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
SCRIPT_DIR = PROJECT_ROOT / "节点脚本" / "_共享组件" / "生产程序"
sys.path.insert(0, str(SCRIPT_DIR))

import load_analysis_zh_current_sqlite as loader  # noqa: E402


DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
DEFAULT_SCHEMA_PATH = PROJECT_ROOT / "schemas" / "analysis_zh_current.sql"
DEFAULT_NORMALIZED_ROOT = PROJECT_ROOT / "data" / "normalized" / "gffunds"
CHANNEL_ID = "gffunds"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Transactionally upsert exact GFFunds runs into analysis DB.")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--schema-path", type=Path, default=DEFAULT_SCHEMA_PATH)
    parser.add_argument("--normalized-root", type=Path, default=DEFAULT_NORMALIZED_ROOT)
    parser.add_argument("--collect-run-id", required=True)
    parser.add_argument("--performance-run-id")
    parser.add_argument("--result-path", type=Path)
    return parser.parse_args()


def exact_summary(normalized_root: Path, run_id: str) -> tuple[Path, dict[str, Any]]:
    matches = sorted((normalized_root / "collection_summary").glob(f"*/{run_id}.json"))
    if not matches:
        raise RuntimeError(f"collection summary not found for run_id={run_id}")
    path = matches[-1]
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if str(payload.get("run_id") or "") != run_id:
        raise RuntimeError(f"collection summary run id mismatch: expected={run_id}, actual={payload.get('run_id')}")
    return path, payload


def exact_run_files(normalized_root: Path, run_id: str) -> dict[str, list[Path]]:
    result: dict[str, list[Path]] = {}
    for entity_dir in sorted(path for path in normalized_root.iterdir() if path.is_dir()):
        matches = sorted(entity_dir.glob(f"*/{run_id}.jsonl"))
        if matches:
            result[entity_dir.name] = matches
    return result


def count_jsonl(path: Path) -> int:
    with path.open("r", encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def merge_entity_files(existing: list[Path], incoming: list[Path]) -> list[Path]:
    """Merge exact-run inputs without silently dropping collector evidence.

    The dedicated performance job normally contains the freshest core-strategy
    curves, while the public collection run can also contain valid curves for
    product instances.  Both inputs are required; the shared loader already
    merges duplicate strategy/date rows deterministically.
    """
    merged: dict[str, Path] = {}
    for path in [*existing, *incoming]:
        merged[str(path.resolve())] = path
    return [merged[key] for key in sorted(merged)]


def scalar(connection: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> Any:
    return connection.execute(sql, params).fetchone()[0]


def write_result(path: Path | None, payload: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main() -> None:
    args = parse_args()
    collect_summary_path, collect_summary = exact_summary(args.normalized_root, args.collect_run_id)
    files_by_entity = exact_run_files(args.normalized_root, args.collect_run_id)
    if "strategy_master" not in files_by_entity:
        raise RuntimeError(f"strategy_master missing for collect_run_id={args.collect_run_id}")
    if sum(count_jsonl(path) for path in files_by_entity["strategy_master"]) <= 0:
        raise RuntimeError(f"strategy_master is empty for collect_run_id={args.collect_run_id}")

    summaries: dict[str, dict[str, Any]] = {args.collect_run_id: collect_summary}
    performance_summary_path: Path | None = None
    if args.performance_run_id:
        performance_summary_path, performance_summary = exact_summary(
            args.normalized_root,
            args.performance_run_id,
        )
        performance_files = exact_run_files(args.normalized_root, args.performance_run_id).get(
            "strategy_performance_daily",
            [],
        )
        if performance_files:
            files_by_entity["strategy_performance_daily"] = merge_entity_files(
                files_by_entity.get("strategy_performance_daily", []),
                performance_files,
            )
            summaries[args.performance_run_id] = performance_summary

    original_entity_files = loader.entity_files
    original_summary_files = loader.summary_files

    def only_exact_files(channel_id: str, entity_name: str) -> list[Path]:
        return files_by_entity.get(entity_name, []) if channel_id == CHANNEL_ID else []

    def only_exact_summaries(channel_id: str) -> dict[str, dict[str, Any]]:
        return summaries if channel_id == CHANNEL_ID else {}

    loader.entity_files = only_exact_files
    loader.summary_files = only_exact_summaries
    try:
        connection = loader.init_db(args.db_path, args.schema_path, keep_existing_db=True)
        try:
            if connection.in_transaction:
                connection.commit()
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.execute("PRAGMA busy_timeout=120000")
            before_strategy_total = int(
                scalar(
                    connection,
                    'SELECT COUNT(*) FROM "策略信息" WHERE "渠道ID"=?',
                    (CHANNEL_ID,),
                )
                or 0
            )
            connection.execute("BEGIN IMMEDIATE")
            try:
                counters = loader.import_channels(connection, [CHANNEL_ID])
                catalog_validations = loader.validate_strategy_catalog_summaries(
                    connection,
                    [collect_summary_path],
                    [CHANNEL_ID],
                )
                after_strategy_total = int(
                    scalar(
                        connection,
                        'SELECT COUNT(*) FROM "策略信息" WHERE "渠道ID"=?',
                        (CHANNEL_ID,),
                    )
                    or 0
                )
                latest_daily_date = scalar(
                    connection,
                    'SELECT MAX("交易日期") FROM "策略日度业绩" WHERE "渠道ID"=?',
                    (CHANNEL_ID,),
                )
                if after_strategy_total < before_strategy_total:
                    raise RuntimeError(
                        "incremental upsert unexpectedly reduced GFFunds strategy inventory: "
                        f"before={before_strategy_total}, after={after_strategy_total}"
                    )
                result = {
                    "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "channel_id": CHANNEL_ID,
                    "collect_run_id": args.collect_run_id,
                    "performance_run_id": args.performance_run_id,
                    "collect_summary_path": str(collect_summary_path.resolve()),
                    "performance_summary_path": (
                        str(performance_summary_path.resolve()) if performance_summary_path else None
                    ),
                    "input_files": {
                        entity: [
                            {"path": str(path.resolve()), "rows": count_jsonl(path)}
                            for path in paths
                        ]
                        for entity, paths in sorted(files_by_entity.items())
                    },
                    "counters": dict(sorted(counters.items())),
                    "strategy_catalog_load_validation": catalog_validations.get(CHANNEL_ID, {}),
                    "strategy_total_before": before_strategy_total,
                    "strategy_total_after": after_strategy_total,
                    "db_latest_strategy_daily_date": latest_daily_date,
                }
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        finally:
            connection.close()
    finally:
        loader.entity_files = original_entity_files
        loader.summary_files = original_summary_files

    write_result(args.result_path, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
