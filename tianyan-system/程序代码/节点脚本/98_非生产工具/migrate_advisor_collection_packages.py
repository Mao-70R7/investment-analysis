#!/usr/bin/env python3
"""Migrate standalone Zhongou/Southern collection packages into a Tianyan workspace."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any


CHANNEL_PACKAGES = {
    "zocaifu": "中欧基金投顾数据采集",
    "southern": "南方基金投顾数据采集",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="迁移中欧、南方独立采集包到天眼运行布局")
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument("--archive-root", type=Path, required=True)
    parser.add_argument("--baseline-root", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def require_within(path: Path, root: Path, label: str) -> Path:
    resolved = path.resolve()
    root_resolved = root.resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(f"{label} 越界: {resolved}") from exc
    return resolved


def file_inventory(root: Path) -> list[dict[str, Any]]:
    rows = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        payload = path.read_bytes()
        rows.append({
            "path": path.relative_to(root).as_posix(),
            "size": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        })
    return rows


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def latest_run_id(package_root: Path, channel: str) -> str:
    summary_path = package_root / "official_apps" / channel / "outputs" / "latest_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    run_id = str(summary.get("run_id") or "").strip()
    if not run_id:
        raise ValueError(f"{summary_path} 缺少 run_id")
    return run_id


def latest_run_dir(channel_root: Path, run_id: str) -> Path:
    matches = sorted(path for path in channel_root.glob(f"*/*/{run_id}") if path.is_dir())
    if len(matches) != 1:
        raise ValueError(f"无法唯一定位批次 {run_id}: {channel_root}")
    return matches[0]


def rewrite_output_metadata(
    output_root: Path,
    workspace_root: Path,
    archive_package_root: Path,
    channel: str,
    run_id: str,
) -> None:
    raw_channel_root = workspace_root / "采集数据" / "raw" / channel
    normalized_channel_root = workspace_root / "采集数据" / "normalized" / channel
    raw_run_dir = latest_run_dir(raw_channel_root, run_id)

    summary_path = output_root / "latest_summary.json"
    if summary_path.exists():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        if "raw_dir" in summary:
            summary["raw_dir"] = str(raw_run_dir)
        if "raw_run_dir" in summary:
            summary["raw_run_dir"] = str(raw_run_dir)
        if "normalized_dir" in summary:
            summary["normalized_dir"] = str(normalized_channel_root)
        summary["workspace_layout"] = "tianyan_runtime"
        write_json(summary_path, summary)

    inventory_path = output_root / "source_inventory.json"
    if inventory_path.exists():
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        inventory["raw_run_dir"] = str(raw_run_dir)
        normalized_files = inventory.get("normalized_files")
        if isinstance(normalized_files, dict):
            inventory["normalized_files"] = {
                entity: str(normalized_channel_root / entity / raw_run_dir.parent.name / f"{run_id}.jsonl")
                for entity in normalized_files
            }
        inventory["standalone_package_root"] = str(archive_package_root)
        write_json(inventory_path, inventory)

    sqlite_validation_path = output_root / "sqlite_validation.json"
    if sqlite_validation_path.exists():
        validation = json.loads(sqlite_validation_path.read_text(encoding="utf-8"))
        validation["database"] = str(archive_package_root / "data" / "zocaifu_advisor.sqlite")
        validation["scope"] = "standalone_archive_only_not_tianyan_main_database"
        write_json(sqlite_validation_path, validation)


def main() -> None:
    args = parse_args()
    workspace_root = args.workspace_root.resolve()
    archive_root = require_within(args.archive_root, workspace_root, "归档目录")
    baseline_root = require_within(args.baseline_root, workspace_root, "迁移基线目录")
    code_root = workspace_root / "程序代码"
    if not (workspace_root / "AGENTS.md").is_file() or not code_root.is_dir():
        raise ValueError(f"不是有效的天眼迁移运行布局: {workspace_root}")
    if (workspace_root / "运行状态" / "locks" / "daily_update.lock").exists():
        raise RuntimeError("检测到活动 daily_update.lock，拒绝迁移")

    operations = []
    for channel, package_name in CHANNEL_PACKAGES.items():
        package_root = require_within(archive_root / package_name, archive_root, f"{channel} 独立包")
        raw_source = package_root / "data" / "raw" / channel
        normalized_source = package_root / "data" / "normalized" / channel
        output_source = package_root / "official_apps" / channel / "outputs"
        raw_target = workspace_root / "采集数据" / "raw" / channel
        normalized_target = workspace_root / "采集数据" / "normalized" / channel
        output_target = code_root / "official_apps" / channel / "outputs"
        output_backup = baseline_root / "原目标official_apps输出" / channel
        for source in (raw_source, normalized_source, output_source):
            if not source.is_dir():
                raise FileNotFoundError(source)
        for target in (raw_target, normalized_target, output_backup):
            if target.exists():
                raise FileExistsError(target)
        operations.append({
            "channel": channel,
            "package_name": package_name,
            "package_root": package_root,
            "raw_source": raw_source,
            "normalized_source": normalized_source,
            "output_source": output_source,
            "raw_target": raw_target,
            "normalized_target": normalized_target,
            "output_target": output_target,
            "output_backup": output_backup,
            "run_id": latest_run_id(package_root, channel),
        })

    if args.dry_run:
        print(json.dumps({
            "status": "dry_run",
            "workspace_root": str(workspace_root),
            "operations": [{key: str(value) for key, value in item.items()} for item in operations],
        }, ensure_ascii=False, indent=2))
        return

    package_manifests = {}
    for item in operations:
        channel = item["channel"]
        output_target = item["output_target"]
        item["output_backup"].parent.mkdir(parents=True, exist_ok=True)
        if output_target.exists():
            shutil.move(str(output_target), str(item["output_backup"]))
        shutil.copytree(item["raw_source"], item["raw_target"])
        shutil.copytree(item["normalized_source"], item["normalized_target"])
        shutil.copytree(item["output_source"], output_target)
        rewrite_output_metadata(
            output_target,
            workspace_root,
            item["package_root"],
            channel,
            item["run_id"],
        )

        raw_source_inventory = file_inventory(item["raw_source"])
        raw_target_inventory = file_inventory(item["raw_target"])
        normalized_source_inventory = file_inventory(item["normalized_source"])
        normalized_target_inventory = file_inventory(item["normalized_target"])
        if raw_source_inventory != raw_target_inventory:
            raise RuntimeError(f"{channel} raw 复制后哈希不一致")
        if normalized_source_inventory != normalized_target_inventory:
            raise RuntimeError(f"{channel} normalized 复制后哈希不一致")
        package_inventory = file_inventory(item["package_root"])
        package_manifests[channel] = {
            "package_name": item["package_name"],
            "run_id": item["run_id"],
            "package_file_count": len(package_inventory),
            "package_total_bytes": sum(row["size"] for row in package_inventory),
            "package_files": package_inventory,
            "raw_file_count": len(raw_target_inventory),
            "raw_hash_match": True,
            "normalized_file_count": len(normalized_target_inventory),
            "normalized_hash_match": True,
            "official_output_backup": str(item["output_backup"]),
            "official_output_target": str(output_target),
        }

    manifest = {
        "status": "migrated_not_loaded_to_main_database",
        "workspace_root": str(workspace_root),
        "archive_root": str(archive_root),
        "baseline_root": str(baseline_root),
        "channels": package_manifests,
    }
    write_json(baseline_root / "migration_manifest.json", manifest)
    print(json.dumps({
        "status": manifest["status"],
        "manifest": str(baseline_root / "migration_manifest.json"),
        "channels": {
            channel: {
                "run_id": details["run_id"],
                "package_file_count": details["package_file_count"],
                "raw_file_count": details["raw_file_count"],
                "normalized_file_count": details["normalized_file_count"],
            }
            for channel, details in package_manifests.items()
        },
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
