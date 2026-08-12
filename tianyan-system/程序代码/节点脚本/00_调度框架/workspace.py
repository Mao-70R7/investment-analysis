from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any


@dataclass(frozen=True)
class WorkspaceContext:
    workspace_root: Path
    code_root: Path
    database_root: Path
    raw_root: Path
    normalized_root: Path
    log_root: Path
    output_root: Path
    lock_root: Path
    temp_root: Path
    report_root: Path
    publish_root: Path
    backup_root: Path
    config: dict[str, Any]

    @property
    def state_db(self) -> Path:
        return self.database_root / "update_state.sqlite"

    @property
    def main_db(self) -> Path:
        return self.database_root / "analysis_zh_current.sqlite"

    @property
    def node_root(self) -> Path:
        return self.code_root / "节点脚本"

    @property
    def legacy_program_root(self) -> Path:
        canonical = self.node_root / "_共享组件" / "生产程序"
        return canonical if canonical.is_dir() else self.code_root / "scripts"


def _relative(root: Path, value: object, key: str) -> Path:
    text = str(value or "").strip().replace("\\", "/")
    if text in {"", "."}:
        if key == "codeRoot":
            return root
        raise ValueError(f"{key} must not be empty")
    pure = PurePosixPath(text)
    if pure.is_absolute() or len(text) >= 2 and text[1] == ":" or ".." in pure.parts:
        raise ValueError(f"{key} must be a safe workspace-relative path: {text}")
    resolved = root.joinpath(*pure.parts).resolve(strict=False)
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"{key} escapes workspace root: {text}")
    return resolved


def load_workspace(workspace_root: Path) -> WorkspaceContext:
    root = workspace_root.resolve()
    config_path = root / "本机配置" / "runtime.local.json"
    config: dict[str, Any] = {}
    if config_path.is_file():
        payload = json.loads(config_path.read_text(encoding="utf-8-sig"))
        if not isinstance(payload, dict):
            raise ValueError(f"runtime config must be an object: {config_path}")
        config = payload

    code_root = _relative(root, config.get("codeRoot", "."), "codeRoot")
    runtime_layout = code_root != root

    def path(key: str, dev_default: str, runtime_default: str) -> Path:
        return _relative(root, config.get(key, runtime_default if runtime_layout else dev_default), key)

    return WorkspaceContext(
        workspace_root=root,
        code_root=code_root,
        database_root=path("databaseRoot", "data", "数据库"),
        raw_root=path("rawRoot", "data/raw", "采集数据/raw"),
        normalized_root=path("normalizedRoot", "data/normalized", "采集数据/normalized"),
        log_root=path("logRoot", "logs", "运行状态/logs"),
        output_root=path("outputRoot", "outputs", "运行状态/outputs"),
        lock_root=path("lockRoot", "data/locks", "运行状态/locks"),
        temp_root=path("tempRoot", "tmp", "运行状态/temp"),
        report_root=path("reportRoot", "site", "结果文件/全市场投顾分析平台"),
        publish_root=path("publishRoot", "minimal_publish", "结果文件/最小发布集"),
        backup_root=path("backupRoot", "data/backups", "数据库备份"),
        config=config,
    )
