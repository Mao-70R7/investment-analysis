from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from node_runner import (  # noqa: E402
    NodeExecution,
    NodeRunner,
    acquire_resource_lock,
    atomic_json,
    release_resource_lock,
)
from progress import (  # noqa: E402
    build_pipeline_status,
    format_duration,
    render_pipeline_status,
)
from state_store import StateStore, now_text  # noqa: E402
from workspace import WorkspaceContext, load_workspace  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="天眼系统节点化每日调度器")
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    subparsers = parser.add_subparsers(dest="mode", required=True)
    subparsers.add_parser("daily")
    subparsers.add_parser("initialize")
    subparsers.add_parser("check")
    resume = subparsers.add_parser("resume")
    resume.add_argument("run_id")
    resume.add_argument(
        "--from-node",
        help=(
            "显式复用该节点之前已通过且产物完整的结果，并从指定节点继续。"
            "用于修复代码或规则后的长任务恢复。"
        ),
    )
    resume.add_argument(
        "--to-node",
        help=(
            "在指定节点完成后结束本次定点续跑；必须与 --from-node 一起使用，"
            "用于只恢复到稽核、备份等明确交付边界。"
        ),
    )
    node = subparsers.add_parser("node")
    node.add_argument("node_id")
    node.add_argument("--run-id")
    node.add_argument(
        "--standalone",
        action="store_true",
        help="仅运行目标节点，不执行其依赖；只适用于可独立读取源数据且不直接写主库或发布的节点。",
    )
    return parser.parse_args()


def load_pipeline(workspace: WorkspaceContext) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    path = workspace.node_root / "pipeline.json"
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    definitions: list[dict[str, Any]] = []
    for entry in payload.get("nodes") or []:
        relative = Path(str(entry["directory"]))
        directory = workspace.node_root / relative
        manifest_path = directory / "node.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        pipeline_dependencies = entry.get("dependencies")
        if pipeline_dependencies is None:
            raise ValueError(f"pipeline entry {entry.get('id')} missing dependencies")
        if list(pipeline_dependencies) != list(manifest.get("dependencies") or []):
            raise ValueError(f"pipeline and node manifest dependencies differ: {entry.get('id')}")
        enabled_when = entry.get("enabledWhen")
        if not isinstance(enabled_when, dict) or "daily" not in enabled_when:
            raise ValueError(f"pipeline entry {entry.get('id')} missing enabledWhen.daily")
        manifest["daily"] = bool(enabled_when["daily"])
        manifest["_directory"] = str(directory)
        manifest["_manifest_path"] = str(manifest_path)
        definitions.append(manifest)
    validate_pipeline(definitions)
    ordered_definitions = stable_topological_order(definitions)
    payload["_declaredNodeOrder"] = [str(node["id"]) for node in definitions]
    payload["_executionNodeOrder"] = [str(node["id"]) for node in ordered_definitions]
    return payload, ordered_definitions


def validate_pipeline(nodes: list[dict[str, Any]]) -> None:
    ids = [str(node.get("id") or "") for node in nodes]
    if not ids or any(not node_id for node_id in ids) or len(ids) != len(set(ids)):
        raise ValueError("pipeline node ids must be non-empty and unique")
    known = set(ids)
    allowed_criticality = {"critical", "optional", "publish"}
    allowed_locks = {None, "", "device", "main_db_write", "publish_repo"}
    for node in nodes:
        required = {
            "schemaVersion",
            "id",
            "name",
            "phase",
            "entrypoint",
            "dependencies",
            "criticality",
            "timeoutSeconds",
            "maxProcessAttempts",
            "resourceLock",
            "supportsResume",
            "progressUnit",
            "validator",
        }
        missing = sorted(required - set(node))
        if missing:
            raise ValueError(f"node {node.get('id')} missing fields: {', '.join(missing)}")
        unknown = set(node["dependencies"]) - known
        if unknown:
            raise ValueError(f"node {node['id']} has unknown dependencies: {sorted(unknown)}")
        if node["criticality"] not in allowed_criticality:
            raise ValueError(f"node {node['id']} has invalid criticality: {node['criticality']}")
        if node.get("failureImpact") not in {None, "warning", "channel"}:
            raise ValueError(f"node {node['id']} has invalid failureImpact: {node.get('failureImpact')}")
        if "allowFailedOptionalDependencies" in node and not isinstance(
            node["allowFailedOptionalDependencies"],
            bool,
        ):
            raise ValueError(
                f"node {node['id']} allowFailedOptionalDependencies must be boolean"
            )
        if node["resourceLock"] not in allowed_locks:
            raise ValueError(f"node {node['id']} has invalid resource lock: {node['resourceLock']}")
        if int(node["timeoutSeconds"]) <= 0 or int(node["maxProcessAttempts"]) <= 0:
            raise ValueError(f"node {node['id']} timeout and attempts must be positive")
        if not (Path(str(node["_directory"])) / str(node["entrypoint"])).is_file():
            raise FileNotFoundError(f"node entrypoint missing: {node['id']}")
        if not (Path(str(node["_directory"])) / "SKILL.md").is_file():
            raise FileNotFoundError(f"node documentation missing: {node['id']}")
    visited: set[str] = set()
    visiting: set[str] = set()
    by_id = {node["id"]: node for node in nodes}

    def visit(node_id: str) -> None:
        if node_id in visited:
            return
        if node_id in visiting:
            raise ValueError(f"pipeline dependency cycle at {node_id}")
        visiting.add(node_id)
        for dependency in by_id[node_id]["dependencies"]:
            visit(dependency)
        visiting.remove(node_id)
        visited.add(node_id)

    for node_id in ids:
        visit(node_id)


def stable_topological_order(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return dependency-safe nodes while preserving declared order where possible."""

    remaining = list(nodes)
    ordered: list[dict[str, Any]] = []
    completed: set[str] = set()
    while remaining:
        ready_index = next(
            (
                index
                for index, node in enumerate(remaining)
                if set(node.get("dependencies") or []) <= completed
            ),
            None,
        )
        if ready_index is None:
            unresolved = {
                str(node.get("id") or ""): sorted(
                    set(node.get("dependencies") or []) - completed
                )
                for node in remaining
            }
            raise ValueError(f"pipeline has unresolved dependencies: {unresolved}")
        node = remaining.pop(ready_index)
        ordered.append(node)
        completed.add(str(node["id"]))
    return ordered


def validate_execution_order(nodes: list[dict[str, Any]]) -> None:
    """Fail before the first node when a selected execution slice is incomplete."""

    completed: set[str] = set()
    for node in nodes:
        missing = sorted(set(node.get("dependencies") or []) - completed)
        if missing:
            raise ValueError(
                f"node {node['id']} execution plan is missing prior dependencies: {missing}"
            )
        completed.add(str(node["id"]))


def classify_run(
    nodes: list[dict[str, Any]],
    results: dict[str, NodeExecution],
    runtime_context: dict[str, str],
) -> tuple[str, list[str], list[str], list[str]]:
    critical_failures: list[str] = []
    publish_failures: list[str] = []
    channel_failures: list[str] = []
    warnings: list[str] = []
    for node in nodes:
        result = results.get(str(node["id"]))
        if result is None:
            if node["criticality"] == "publish":
                publish_failures.append(str(node["id"]))
            else:
                critical_failures.append(str(node["id"]))
            continue
        failed = result.status in {"failed", "interrupted"} or result.returncode != 0
        skipped = result.status == "skipped"
        if result.status == "warn":
            warnings.append(str(node["id"]))
        if not failed and not skipped:
            continue
        if node["criticality"] == "critical":
            critical_failures.append(str(node["id"]))
        elif node["criticality"] == "publish":
            publish_failures.append(str(node["id"]))
        elif node.get("failureImpact") == "channel":
            channel_failures.append(str(node["id"]))
        else:
            warnings.append(str(node["id"]))
    for channel in ("TTFUND", "GFFUNDS"):
        if runtime_context.get(f"{channel}_LOAD_FAILED") == "1":
            channel_failures.append(f"{channel.lower()}_load")
    critical_failures = list(dict.fromkeys(critical_failures))
    publish_failures = list(dict.fromkeys(publish_failures))
    channel_failures = list(dict.fromkeys(channel_failures))
    warnings = list(dict.fromkeys(warnings))
    if critical_failures:
        return "failed_critical", critical_failures, publish_failures, channel_failures
    if publish_failures:
        return "data_success_publish_failed", critical_failures, publish_failures, channel_failures
    if channel_failures:
        return "partial_success", critical_failures, publish_failures, channel_failures
    if warnings:
        return "success_with_warning", critical_failures, publish_failures, channel_failures
    return "success", critical_failures, publish_failures, channel_failures


def dependency_blockers(
    node: dict[str, Any],
    by_id: dict[str, dict[str, Any]],
    results: dict[str, NodeExecution],
    dependencies: list[str] | None = None,
) -> list[str]:
    allow_failed_optional = bool(node.get("allowFailedOptionalDependencies"))
    blockers: list[str] = []
    checked_dependencies = (
        list(node.get("dependencies") or [])
        if dependencies is None
        else dependencies
    )
    for dependency in checked_dependencies:
        result = results[dependency]
        dependency_manifest = by_id[dependency]
        unhealthy = (
            result.status in {"failed", "interrupted", "skipped"}
            or result.returncode != 0
        )
        if not unhealthy:
            continue
        if dependency_manifest["criticality"] == "optional" and allow_failed_optional:
            continue
        blockers.append(str(dependency))
    return blockers


class Orchestrator:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.workspace = load_workspace(args.workspace_root)
        self.pipeline, self.nodes = load_pipeline(self.workspace)
        self.by_id = {node["id"]: node for node in self.nodes}
        resume_from_node = getattr(args, "from_node", None)
        resume_to_node = getattr(args, "to_node", None)
        if resume_from_node:
            if args.mode != "resume":
                raise ValueError("--from-node is only valid in resume mode")
            if resume_from_node not in self.by_id:
                raise ValueError(f"unknown resume start node: {resume_from_node}")
            if not self.by_id[resume_from_node].get("daily", True):
                raise ValueError(f"resume start node is not enabled in daily pipeline: {resume_from_node}")
        if resume_to_node:
            if args.mode != "resume":
                raise ValueError("--to-node is only valid in resume mode")
            if not resume_from_node:
                raise ValueError("--to-node requires --from-node")
            if resume_to_node not in self.by_id:
                raise ValueError(f"unknown resume end node: {resume_to_node}")
            if not self.by_id[resume_to_node].get("daily", True):
                raise ValueError(f"resume end node is not enabled in daily pipeline: {resume_to_node}")
            daily_ids = [str(node["id"]) for node in self.nodes if node.get("daily", True)]
            if daily_ids.index(str(resume_to_node)) < daily_ids.index(str(resume_from_node)):
                raise ValueError("resume end node must not precede resume start node")
        resumed = args.run_id if args.mode == "resume" else None
        self.run_id = resumed or (args.run_id if args.mode == "node" and args.run_id else datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z"))
        self.state = StateStore(self.workspace.state_db)
        existing = self.state.get_run(self.run_id)
        if resumed and existing is None:
            self.state.close()
            raise RuntimeError(f"resume run not found: {self.run_id}")
        if resumed and existing is not None:
            stored_run_dir = Path(str(existing["run_dir"] or "")).resolve()
            log_root = self.workspace.log_root.resolve()
            try:
                stored_run_dir.relative_to(log_root)
            except ValueError as exc:
                self.state.close()
                raise RuntimeError(f"resume run directory is outside log root: {stored_run_dir}") from exc
            self.run_dir = stored_run_dir
        else:
            self.run_dir = self.workspace.log_root / "daily_update" / datetime.now().strftime("%Y-%m-%d") / self.run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.console_path = self.run_dir / "console.log"
        self.events_path = self.run_dir / "events.jsonl"
        self.summary_path = self.run_dir / "summary.json"
        self.summary_md_path = self.run_dir / "summary.md"
        self.resume_checkpoint_path = self.run_dir / "resume_checkpoint.json"
        self.resume_checkpoint_history_path = self.run_dir / "resume_checkpoints.jsonl"
        recovery_config = self.pipeline.get("recovery")
        if not isinstance(recovery_config, dict):
            recovery_config = {}
        self.checkpoint_interval_seconds = max(
            60,
            int(recovery_config.get("checkpointIntervalSeconds") or 600),
        )
        if resumed and existing is not None:
            metadata = json.loads(str(existing["metadata_json"] or "{}"))
            if bool(metadata.get("dryRun")) != bool(args.dry_run):
                self.state.close()
                raise RuntimeError(
                    "resume mode mismatch: dry-run results cannot be reused by a real run, or vice versa"
                )
        if existing is None:
            self.state.create_run(
                self.run_id,
                self.run_dir,
                {
                    "mode": args.mode,
                    "dryRun": args.dry_run,
                    "standalone": bool(getattr(args, "standalone", False)),
                    "pipelineVersion": self.pipeline.get("version"),
                },
            )
        self.runtime_context: dict[str, str] = {}
        self.results: dict[str, NodeExecution] = {}
        self.active_plan_nodes: list[dict[str, Any]] = []
        self.started = time.monotonic()

    def close(self) -> None:
        self.state.close()

    def console(self, message: str) -> None:
        line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}"
        print(line, flush=True)
        with self.console_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def event(self, name: str, **payload: Any) -> None:
        row = {"timestamp": now_text(), "runId": self.run_id, "event": name, **payload}
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    def recovery_checkpoint(
        self,
        *,
        reason: str,
        manifest: dict[str, Any] | None = None,
        attempt: int | None = None,
        elapsed_seconds: int | None = None,
        progress: dict[str, Any] | None = None,
        pipeline_status: dict[str, Any] | None = None,
        log_path: Path | None = None,
        run_status: str = "running",
    ) -> None:
        """Persist one compact, atomic recovery marker without changing business data."""

        nodes = list(self.active_plan_nodes)
        reusable_statuses = {"success", "warn"}
        next_resume_node = ""
        for node in nodes:
            result = self.results.get(str(node["id"]))
            if result is None or result.status not in reusable_statuses:
                next_resume_node = str(node["id"])
                break
        if manifest is not None:
            next_resume_node = str(manifest.get("id") or next_resume_node)
        processed_ids = list(self.results)
        reusable_ids = [
            node_id
            for node_id, result in self.results.items()
            if result.status in reusable_statuses
        ]
        current_node = None
        if manifest is not None:
            current_node = {
                "id": str(manifest.get("id") or ""),
                "name": str(manifest.get("name") or ""),
                "phase": str(manifest.get("phase") or ""),
                "attempt": attempt,
                "elapsedSeconds": elapsed_seconds,
                "supportsResume": bool(manifest.get("supportsResume")),
                "progress": progress or {},
                "logPath": str(log_path or ""),
            }
        if pipeline_status is None:
            overall_percent = round(
                len(processed_ids) * 100.0 / max(1, len(nodes)),
                1,
            )
            pipeline_status = {
                "completedNodes": len(processed_ids),
                "totalNodes": len(nodes),
                "overallPercent": overall_percent,
            }
        timestamp = now_text()
        payload = {
            "schemaVersion": 1,
            "runId": self.run_id,
            "mode": self.args.mode,
            "status": run_status,
            "updatedAt": timestamp,
            "reason": reason,
            "checkpointIntervalSeconds": self.checkpoint_interval_seconds,
            "nextResumeNode": next_resume_node if run_status not in {"success", "success_with_warning"} else "",
            "processedNodeIds": processed_ids,
            "reusableNodeIds": reusable_ids,
            "progress": pipeline_status,
            "currentNode": current_node,
            "recoverySemantics": {
                "completedNodes": "续作时重新校验 node_result 和产物后复用",
                "currentNode": "优先使用节点自身检查点；无内部检查点时只重启当前节点",
            },
        }
        atomic_json(self.resume_checkpoint_path, payload)
        history_row = {
            "timestamp": timestamp,
            "runId": self.run_id,
            "reason": reason,
            "status": run_status,
            "nextResumeNode": payload["nextResumeNode"],
            "processedNodes": len(processed_ids),
            "totalNodes": len(nodes),
            "overallPercent": pipeline_status.get("overallPercent"),
            "currentNode": current_node.get("id") if current_node else "",
            "attempt": attempt,
        }
        with self.resume_checkpoint_history_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(history_row, ensure_ascii=False) + "\n")
        self.event(
            "recovery_checkpoint",
            reason=reason,
            nextResumeNode=payload["nextResumeNode"],
            currentNode=history_row["currentNode"],
            overallPercent=history_row["overallPercent"],
        )
        if reason == "interval":
            self.console(
                f"[10分钟恢复点] 已保存；中断后建议从 {payload['nextResumeNode'] or '收尾'} 继续，"
                f"恢复点={self.resume_checkpoint_path}"
            )

    def safe_recovery_checkpoint(self, **kwargs: Any) -> None:
        try:
            self.recovery_checkpoint(**kwargs)
        except Exception as exc:  # noqa: BLE001 - checkpoint metadata must never stop the update.
            self.console(
                "[恢复点警告] 恢复点写入失败，主任务继续："
                f"{type(exc).__name__}: {exc}"
            )

    def selected_nodes(self) -> list[dict[str, Any]]:
        if self.args.mode in {"daily", "resume"}:
            selected = [node for node in self.nodes if node.get("daily", True)]
            resume_to_node = str(getattr(self.args, "to_node", "") or "")
            if self.args.mode == "resume" and resume_to_node:
                end_index = next(
                    index for index, node in enumerate(selected) if node["id"] == resume_to_node
                )
                selected = selected[: end_index + 1]
            return selected
        if self.args.mode == "initialize":
            ids = set(self.pipeline.get("initializeNodes") or [])
        elif self.args.mode == "check":
            ids = set(self.pipeline.get("checkNodes") or [])
        elif self.args.mode == "node":
            if self.args.node_id not in self.by_id:
                raise ValueError(f"unknown node: {self.args.node_id}")
            if getattr(self.args, "standalone", False):
                target = self.by_id[self.args.node_id]
                if target.get("resourceLock") in {"main_db_write", "publish_repo"}:
                    raise ValueError(
                        f"node {self.args.node_id} cannot run standalone because it writes the main database or publish repository"
                    )
                ids = {self.args.node_id}
                return [node for node in self.nodes if node["id"] in ids]
            ids = set()

            def add(node_id: str) -> None:
                for dependency in self.by_id[node_id]["dependencies"]:
                    add(dependency)
                ids.add(node_id)

            add(self.args.node_id)
        else:
            ids = set()
        return [node for node in self.nodes if node["id"] in ids]

    def run(self) -> int:
        nodes = self.selected_nodes()
        self.active_plan_nodes = list(nodes)
        if not hasattr(self, "checkpoint_interval_seconds"):
            self.checkpoint_interval_seconds = 600
        if not hasattr(self, "resume_checkpoint_path"):
            self.resume_checkpoint_path = self.run_dir / "resume_checkpoint.json"
        if not hasattr(self, "resume_checkpoint_history_path"):
            self.resume_checkpoint_history_path = self.run_dir / "resume_checkpoints.jsonl"
        standalone = self.args.mode == "node" and getattr(self.args, "standalone", False)
        if not standalone:
            validate_execution_order(nodes)
        self.console("=" * 78)
        self.console(f"天眼系统节点调度启动，run_id={self.run_id}，mode={self.args.mode}")
        self.console(f"计划节点：{len(nodes)}；日志：{self.run_dir}")
        declared_order = list(self.pipeline.get("_declaredNodeOrder") or [])
        execution_order = list(self.pipeline.get("_executionNodeOrder") or [])
        if declared_order and declared_order != execution_order:
            moved = [
                node_id
                for index, node_id in enumerate(execution_order)
                if index >= len(declared_order) or declared_order[index] != node_id
            ]
            self.console(
                "[依赖顺序修正] 已在启动阶段按 DAG 自动调整节点顺序："
                + ", ".join(moved)
            )
        if self.args.mode == "node" and getattr(self.args, "standalone", False):
            self.console("[单节点诊断] 已跳过依赖节点，仅验证目标节点；本次结果不会进入主库或发布流程。")
        self.console("=" * 78)
        self.event("run_started", mode=self.args.mode, nodes=[node["id"] for node in nodes])
        duration_estimates = {
            str(node["id"]): estimate
            for node in nodes
            if (
                estimate := self.state.recent_node_duration_seconds(str(node["id"]))
            ) is not None
        }
        self.console(
            f"[耗时基线] 最近真实成功批次覆盖 {len(duration_estimates)}/{len(nodes)} 个节点；"
            "无样本节点使用同批节点中位耗时，仍无依据时显示待估算。"
        )
        runner = NodeRunner(
            self.workspace,
            self.state,
            self.run_id,
            self.run_dir,
            self.console,
            self.event,
            dry_run=self.args.dry_run,
            plan_nodes=nodes,
            duration_estimates=duration_estimates,
            plan_started_monotonic=self.started,
            recovery_checkpoint=self.safe_recovery_checkpoint,
            checkpoint_interval_seconds=self.checkpoint_interval_seconds,
        )
        completed = 0
        resume_from_node = (
            str(getattr(self.args, "from_node", "") or "")
            if self.args.mode == "resume"
            else ""
        )
        restoring_upstream = bool(resume_from_node)
        if restoring_upstream:
            self.console(
                f"[定点续跑] 将重新校验并复用 {resume_from_node} 之前的成功节点，"
                f"从 {resume_from_node} 开始实际执行。"
            )
        final_status = "success"
        error: str | None = None
        critical_failures: list[str] = []
        publish_failures: list[str] = []
        channel_failures: list[str] = []
        self.state.update_run(self.run_id, status="running", current_stage=None, error=None)
        self.safe_recovery_checkpoint(reason="run_started")
        try:
            for node_index, node in enumerate(nodes):
                if restoring_upstream and node["id"] == resume_from_node:
                    restoring_upstream = False
                standalone_target = (
                    self.args.mode == "node"
                    and getattr(self.args, "standalone", False)
                    and node["id"] == self.args.node_id
                )
                required_dependencies = [] if standalone_target else node["dependencies"]
                missing_dependencies = [
                    dependency for dependency in required_dependencies if dependency not in self.results
                ]
                if missing_dependencies:
                    raise RuntimeError(f"node {node['id']} dependencies were not executed: {missing_dependencies}")
                dependency_hashes = {
                    dependency: self.results[dependency].output_fingerprint
                    for dependency in required_dependencies
                }
                elapsed_now = int(time.monotonic() - self.started)
                self.console(
                    render_pipeline_status(
                        "整体进度",
                        node,
                        build_pipeline_status(
                            nodes,
                            node_index,
                            None,
                            node_elapsed_seconds=0,
                            total_elapsed_seconds=elapsed_now,
                            duration_estimates=duration_estimates,
                        ),
                    )
                )
                self.safe_recovery_checkpoint(
                    reason="node_started",
                    manifest=node,
                    elapsed_seconds=0,
                    pipeline_status=build_pipeline_status(
                        nodes,
                        node_index,
                        None,
                        node_elapsed_seconds=0,
                        total_elapsed_seconds=elapsed_now,
                        duration_estimates=duration_estimates,
                    ),
                )
                if restoring_upstream:
                    execution = runner.restore_previous(node)
                    self.console(
                        f"[定点续跑] 已复用并验证上游节点：{node['id']}，"
                        f"状态={execution.status}"
                    )
                    self.event(
                        "node_reused",
                        nodeId=node["id"],
                        status=execution.status,
                        resultPath=str(execution.result_path),
                        recoveryStartNode=resume_from_node,
                    )
                else:
                    blocking_dependencies = dependency_blockers(
                        node,
                        self.by_id,
                        self.results,
                        required_dependencies,
                    )
                    if blocking_dependencies:
                        execution = runner.skip(
                            node,
                            dependency_hashes,
                            "依赖未成功，未执行本节点：" + ", ".join(blocking_dependencies),
                        )
                    else:
                        execution = runner.run(
                            node,
                            dependency_hashes,
                            self.runtime_context,
                            allow_skip=self.args.mode in {"resume", "node"},
                        )
                self.results[node["id"]] = execution
                if execution.status in {"success", "warn"}:
                    self.runtime_context.update(execution.context_updates)
                status_key = "ADVISOR_NODE_STATUS_" + "".join(
                    character if character.isalnum() else "_"
                    for character in str(node["id"]).upper()
                )
                self.runtime_context[status_key] = execution.status
                completed += 1
                self.event(
                    "node_finished",
                    nodeId=node["id"],
                    status=execution.status,
                    returncode=execution.returncode,
                    resultPath=str(execution.result_path),
                )
                self.safe_recovery_checkpoint(reason="node_finished")
            final_status, critical_failures, publish_failures, channel_failures = classify_run(
                nodes,
                self.results,
                self.runtime_context,
            )
            failure_parts = []
            if critical_failures:
                failure_parts.append("critical=" + ",".join(critical_failures))
            if publish_failures:
                failure_parts.append("publish=" + ",".join(publish_failures))
            if channel_failures:
                failure_parts.append("channel=" + ",".join(channel_failures))
            error = "; ".join(failure_parts) or None
        except Exception as exc:  # noqa: BLE001 - top-level result must always be persisted.
            final_status = "failed_critical"
            error = f"{type(exc).__name__}: {exc}"
            self.console(f"[调度失败] {error}")
        elapsed = int(time.monotonic() - self.started)
        summary = {
            "schemaVersion": 1,
            "pipelineVersion": self.pipeline.get("version"),
            "runId": self.run_id,
            "mode": self.args.mode,
            "dryRun": bool(self.args.dry_run),
            "standalone": bool(getattr(self.args, "standalone", False)),
            "resumeFromNode": str(getattr(self.args, "from_node", "") or ""),
            "resumeToNode": str(getattr(self.args, "to_node", "") or ""),
            "status": final_status,
            "finishedAt": now_text(),
            "elapsedSeconds": elapsed,
            "progress": {
                "completedNodes": completed,
                "totalNodes": len(nodes),
                "overallPercent": round(completed * 100.0 / max(1, len(nodes)), 1),
            },
            "completedNodes": list(self.results),
            "nodeResults": {key: str(value.result_path) for key, value in self.results.items()},
            "runtimeContext": self.runtime_context,
            "failedCriticalNodes": critical_failures,
            "failedPublishNodes": publish_failures,
            "failedChannelNodes": channel_failures,
            "warningNodes": [
                key
                for key, value in self.results.items()
                if value.status == "warn"
                or (
                    self.by_id[key]["criticality"] == "optional"
                    and self.by_id[key].get("failureImpact") != "channel"
                    and (
                        value.status in {"failed", "interrupted", "skipped"}
                        or value.returncode != 0
                    )
                )
            ],
            "skippedNodes": [
                key
                for key, value in self.results.items()
                if value.status == "skipped"
            ],
            "channels": {
                "ttfund": {
                    "collectionStatus": self.runtime_context.get("ADVISOR_NODE_STATUS_TTFUND_INCREMENTAL"),
                    "loadStatus": (
                        "failed"
                        if self.runtime_context.get("TTFUND_LOAD_FAILED") == "1"
                        else "loaded"
                        if self.runtime_context.get("TTFUND_LOADED") == "1"
                        else "not_required"
                        if self.runtime_context.get("TTFUND_COLLECTION_REQUIRED") == "0"
                        else "not_run"
                    ),
                },
                "gffunds": {
                    "collectionStatus": self.runtime_context.get("ADVISOR_NODE_STATUS_GFFUNDS_GATE"),
                    "loadStatus": (
                        "failed"
                        if self.runtime_context.get("GFFUNDS_LOAD_FAILED") == "1"
                        else "loaded"
                        if self.runtime_context.get("GFFUNDS_LOADED") == "1"
                        else "not_run"
                    ),
                },
                "gfsec_fima": {
                    "collectionStatus": self.runtime_context.get("ADVISOR_NODE_STATUS_GFSEC_FIMA_GATE"),
                    "loadStatus": (
                        "failed"
                        if self.runtime_context.get("GFSEC_FIMA_LOAD_FAILED") == "1"
                        else "loaded"
                        if self.runtime_context.get("GFSEC_FIMA_LOADED") == "1"
                        else "not_run"
                    ),
                },
                "gfsec_robot": {
                    "collectionStatus": self.runtime_context.get("ADVISOR_NODE_STATUS_GF_SUPPLEMENTAL_GATE"),
                    "loadStatus": (
                        "failed"
                        if self.runtime_context.get("GF_SUPPLEMENTAL_LOAD_FAILED") == "1"
                        else "loaded"
                        if self.runtime_context.get("GFSEC_ROBOT_LOADED") == "1"
                        else "retained_previous"
                    ),
                },
            },
            "error": error,
        }
        atomic_json(self.summary_path, summary)
        summary_lines = [
            "# 每日节点调度摘要",
            "",
            f"- 运行批次：`{self.run_id}`",
            f"- 模式：`{self.args.mode}`",
            f"- 状态：`{final_status}`",
            f"- 完成节点：{completed}/{len(nodes)}",
            f"- 耗时：{elapsed} 秒",
            f"- 错误：{error or '无'}",
            "",
            "## 节点结果",
            "",
        ]
        for node in nodes:
            result = self.results.get(node["id"])
            summary_lines.append(
                f"- `{node['id']}`：{result.status if result else '未执行'}"
                + (f"，结果 `{result.result_path}`" if result else "")
            )
        self.summary_md_path.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")
        self.state.update_run(
            self.run_id,
            status=final_status,
            current_stage=None,
            finished_at=now_text(),
            error=error,
        )
        self.safe_recovery_checkpoint(reason="run_finished", run_status=final_status)
        self.event("run_finished", status=final_status, error=error)
        status_counts = {
            status: sum(1 for result in self.results.values() if result.status == status)
            for status in ("success", "warn", "failed", "interrupted", "skipped")
        }
        self.console(
            f"[最终结果] 状态={final_status} | 整体完成度="
            f"{completed * 100.0 / max(1, len(nodes)):.1f}%（{completed}/{len(nodes)}节点） | "
            f"成功={status_counts['success']} | 警告={status_counts['warn']} | "
            f"失败={status_counts['failed'] + status_counts['interrupted']} | "
            f"跳过={status_counts['skipped']} | 总耗时={format_duration(elapsed)}"
        )
        delivery_status = {
            node_id: self.results[node_id].status if node_id in self.results else "未执行"
            for node_id in ("data_audit", "database_backup", "publish", "pages_verify")
        }
        self.console(
            "[交付闭环] "
            f"数据稽核={delivery_status['data_audit']} | "
            f"数据库备份={delivery_status['database_backup']} | "
            f"发布={delivery_status['publish']} | "
            f"在线版本验证={delivery_status['pages_verify']}"
        )
        self.console(f"执行摘要：{self.summary_path}")
        if final_status in {"success", "success_with_warning"}:
            return 0
        if final_status == "failed_critical":
            return 1
        return 2


def main() -> None:
    args = parse_args()
    orchestrator: Orchestrator | None = None
    global_lock: tuple[Path, str] | None = None
    try:
        orchestrator = Orchestrator(args)
        if args.mode in {"daily", "resume", "node"}:
            global_lock = acquire_resource_lock(orchestrator.workspace, "daily_update", orchestrator.run_id, "orchestrator")
            interrupted = orchestrator.state.interrupt_stale_runs(orchestrator.run_id)
            if interrupted:
                orchestrator.console(
                    "[状态修复] 已将先前未正常收尾的运行标记为 interrupted："
                    + ", ".join(interrupted)
                )
                orchestrator.event("stale_runs_interrupted", runIds=interrupted)
            if args.mode == "resume":
                interrupted_nodes = orchestrator.state.interrupt_running_nodes(orchestrator.run_id)
                if interrupted_nodes:
                    orchestrator.console(
                        "[状态修复] 已将本批次残留 running 节点标记为 interrupted："
                        + ", ".join(interrupted_nodes)
                    )
                    orchestrator.event("running_nodes_interrupted", nodeIds=interrupted_nodes)
        raise SystemExit(orchestrator.run())
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - startup failures must be concise and actionable.
        print(f"[调度启动失败] {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(2)
    finally:
        if global_lock:
            release_resource_lock(*global_lock)
        if orchestrator is not None:
            orchestrator.close()


if __name__ == "__main__":
    main()
