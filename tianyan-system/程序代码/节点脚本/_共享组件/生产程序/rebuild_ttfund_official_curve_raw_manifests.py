from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
RAW_ROOT = PROJECT_ROOT / "data" / "raw" / "ttfund" / "official_performance_curve"
NORMALIZED_ROOT = PROJECT_ROOT / "data" / "normalized" / "ttfund" / "strategy_performance_daily"
SUMMARY_ROOT = PROJECT_ROOT / "data" / "normalized" / "ttfund" / "collection_summary"
CHANNEL_ID = "ttfund"
COLLECTOR_NAME = "ttfund_official_performance_curve"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rebuild raw manifests for historical TTFund official performance curve files."
    )
    parser.add_argument("--raw-root", type=Path, default=RAW_ROOT)
    parser.add_argument("--normalized-root", type=Path, default=NORMALIZED_ROOT)
    parser.add_argument("--summary-root", type=Path, default=SUMMARY_ROOT)
    parser.add_argument("--overwrite", action="store_true", help="Rewrite existing _manifest.json files.")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def iter_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                rows.append(value)
    return rows


def source_snapshot_by_strategy(normalized_path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for row in iter_jsonl(normalized_path):
        strategy_id = str(row.get("source_strategy_id") or "").strip()
        snapshot_id = str(row.get("source_snapshot_id") or "").strip()
        if strategy_id and snapshot_id:
            result.setdefault(strategy_id, snapshot_id)
    return result


def fallback_snapshot_id(strategy_id: str, raw_bytes: bytes) -> str:
    digest = hashlib.sha256(strategy_id.encode("utf-8") + b"\n" + raw_bytes).hexdigest()
    return f"ttfund-official_curve-unmatched-{strategy_id}-{digest[:16]}"


def file_captured_at(path: Path) -> str:
    timestamp = path.stat().st_mtime
    return datetime.fromtimestamp(timestamp, timezone.utc).astimezone().isoformat(timespec="seconds")


def rebuild_run_manifest(
    run_dir: Path,
    *,
    normalized_root: Path,
    summary_root: Path,
    overwrite: bool,
    dry_run: bool,
) -> dict[str, Any] | None:
    curves_dir = run_dir / "curves"
    if not curves_dir.is_dir():
        return None
    manifest_path = run_dir / "_manifest.json"
    if manifest_path.exists() and not overwrite:
        return None

    day = run_dir.parent.name
    run_id = run_dir.name
    normalized_path = normalized_root / day / f"{run_id}.jsonl"
    summary = read_json(summary_root / day / f"{run_id}.json")
    captured_at = str(summary.get("captured_at") or "")
    snapshot_by_strategy = source_snapshot_by_strategy(normalized_path)

    raw_snapshots: list[dict[str, Any]] = []
    matched = 0
    for raw_path in sorted(curves_dir.glob("*.json")):
        strategy_id = raw_path.stem
        raw_bytes = raw_path.read_bytes()
        payload = read_json(raw_path)
        snapshot_id = snapshot_by_strategy.get(strategy_id)
        if snapshot_id:
            matched += 1
        raw_snapshots.append(
            {
                "snapshot_id": snapshot_id or fallback_snapshot_id(strategy_id, raw_bytes),
                "channel_id": CHANNEL_ID,
                "collector_name": COLLECTOR_NAME,
                "access_level": "public",
                "captured_at": captured_at or file_captured_at(raw_path),
                "source_url": payload.get("request_url"),
                "http_status": payload.get("status_code"),
                "raw_path": str(raw_path.relative_to(run_dir)),
                "content_type": "application/json",
                "content_hash": hashlib.sha256(raw_bytes).hexdigest(),
                "parse_status": "success" if snapshot_id else "error",
            }
        )

    manifest = {
        "channel_id": CHANNEL_ID,
        "collector_name": COLLECTOR_NAME,
        "run_id": run_id,
        "captured_at": captured_at,
        "raw_snapshots": raw_snapshots,
    }
    if not dry_run:
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return {
        "manifest_path": str(manifest_path),
        "raw_snapshot_count": len(raw_snapshots),
        "matched_normalized_count": matched,
        "normalized_path_exists": normalized_path.exists(),
    }


def main() -> None:
    args = parse_args()
    rebuilt: list[dict[str, Any]] = []
    for curves_dir in sorted(args.raw_root.glob("*/*/curves")):
        result = rebuild_run_manifest(
            curves_dir.parent,
            normalized_root=args.normalized_root,
            summary_root=args.summary_root,
            overwrite=args.overwrite,
            dry_run=args.dry_run,
        )
        if result:
            rebuilt.append(result)
    print(json.dumps({"rebuilt_manifest_count": len(rebuilt), "rebuilt": rebuilt}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
