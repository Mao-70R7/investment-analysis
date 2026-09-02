"""Shared helpers for per-channel strategy catalog discovery and diffing."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Iterable


def dedupe_strategy_ids(values: Iterable[Any]) -> list[str]:
    """Return stable, non-empty strategy IDs without changing source order."""

    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        strategy_id = str(value or "").strip()
        if strategy_id and strategy_id not in seen:
            result.append(strategy_id)
            seen.add(strategy_id)
    return result


def database_candidates(project_root: Path) -> list[Path]:
    """Find the analysis database without assuming one deployment layout."""

    candidates: list[Path] = []
    configured = str(os.environ.get("ADVISOR_DATABASE_ROOT") or "").strip()
    if configured:
        configured_path = Path(configured)
        candidates.append(
            configured_path
            if configured_path.suffix.lower() in {".sqlite", ".db"}
            else configured_path / "analysis_zh_current.sqlite"
        )
    candidates.extend(
        [
            project_root / "data" / "analysis_zh_current.sqlite",
            project_root / "数据库" / "analysis_zh_current.sqlite",
            project_root / "程序代码" / "data" / "analysis_zh_current.sqlite",
        ]
    )
    result: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        resolved = str(candidate.resolve())
        if resolved not in seen:
            result.append(candidate)
            seen.add(resolved)
    return result


def load_local_strategy_ids(project_root: Path, channel_id: str) -> list[str]:
    """Read the current local strategy baseline for one channel.

    A missing/unavailable baseline is deliberately represented as an empty list;
    catalog discovery must remain read-only and must not make the channel fail.
    """

    for db_path in database_candidates(project_root):
        if not db_path.is_file():
            continue
        try:
            with sqlite3.connect(db_path) as connection:
                rows = connection.execute(
                    'SELECT DISTINCT "渠道策略ID" FROM "策略信息" '
                    'WHERE "渠道ID" = ? AND "渠道策略ID" IS NOT NULL',
                    (channel_id,),
                ).fetchall()
        except sqlite3.Error:
            continue
        return sorted(dedupe_strategy_ids(row[0] for row in rows))
    return []


def catalog_diff(catalog_ids: Iterable[Any], local_ids: Iterable[Any]) -> dict[str, Any]:
    """Build a deterministic catalog/local-baseline diff."""

    catalog = sorted(dedupe_strategy_ids(catalog_ids))
    local = set(dedupe_strategy_ids(local_ids))
    new_ids = sorted(set(catalog) - local)
    return {
        "catalog_strategy_ids": catalog,
        "catalog_strategy_total": len(catalog),
        "local_strategy_total": len(local),
        "new_strategy_ids": new_ids,
        "new_strategy_total": len(new_ids),
    }


def reconcile_catalog_batch(
    catalog_ids: Iterable[Any],
    batch_ids: Iterable[Any],
    local_ids: Iterable[Any],
) -> dict[str, Any]:
    """Prove that every catalog strategy, especially every new one, entered the batch.

    Catalog completeness and batch closure are deliberately separate concepts:
    the source collector proves the former, while this helper proves that no ID
    discovered by that collector was lost during detail collection or
    normalization.
    """

    diff = catalog_diff(catalog_ids, local_ids)
    catalog = set(diff["catalog_strategy_ids"])
    batch = set(dedupe_strategy_ids(batch_ids))
    new_ids = set(diff["new_strategy_ids"])
    collected_catalog_ids = sorted(catalog & batch)
    missing_catalog_ids = sorted(catalog - batch)
    collected_new_ids = sorted(new_ids & batch)
    missing_new_ids = sorted(new_ids - batch)
    return {
        **diff,
        "batch_strategy_total": len(batch),
        "catalog_batch_collected_strategy_total": len(collected_catalog_ids),
        "catalog_batch_missing_strategy_total": len(missing_catalog_ids),
        "catalog_batch_missing_strategy_ids": missing_catalog_ids,
        "catalog_batch_closed": not missing_catalog_ids,
        "catalog_new_strategy_collected_total": len(collected_new_ids),
        "catalog_new_strategy_collected_ids": collected_new_ids,
        "catalog_new_strategy_missing_total": len(missing_new_ids),
        "catalog_new_strategy_missing_ids": missing_new_ids,
    }


def load_catalog_manifest(path: Path | None) -> dict[str, Any]:
    """Load an optional device/app discovery manifest safely."""

    if path is None or not path.is_file():
        return {
            "state": "missing",
            "catalog_strategy_ids": [],
            "catalog_strategy_total": 0,
            "catalog_complete": False,
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {
            "state": "invalid",
            "manifest_path": str(path),
            "catalog_strategy_ids": [],
            "catalog_strategy_total": 0,
            "catalog_complete": False,
        }
    if not isinstance(payload, dict):
        payload = {}
    ids = dedupe_strategy_ids(payload.get("catalog_strategy_ids") or payload.get("strategy_ids") or [])
    return {
        **payload,
        "manifest_path": str(path),
        "catalog_strategy_ids": ids,
        "catalog_strategy_total": len(ids),
        "catalog_complete": bool(payload.get("catalog_complete")),
    }
