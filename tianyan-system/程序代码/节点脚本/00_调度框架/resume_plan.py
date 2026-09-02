from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

from node_runner import _lock_owner_active, file_sha256, fingerprint
from workspace import WorkspaceContext, load_workspace


COMPLETED_RUN_STATUSES = {"success", "success_with_warning"}
REUSABLE_NODE_STATUSES = {"success", "warn"}


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _daily_nodes(workspace: WorkspaceContext) -> list[dict[str, Any]]:
    pipeline_path = workspace.node_root / "pipeline.json"
    pipeline = _read_json(pipeline_path)
    nodes: list[dict[str, Any]] = []
    for entry in pipeline.get("nodes") or []:
        enabled_when = entry.get("enabledWhen")
        if not isinstance(enabled_when, dict) or not bool(enabled_when.get("daily")):
            continue
        directory = workspace.node_root / Path(str(entry.get("directory") or ""))
        manifest = _read_json(directory / "node.json")
        node_id = str(manifest.get("id") or entry.get("id") or "").strip()
        if not node_id:
            continue
        nodes.append(
            {
                "id": node_id,
                "name": str(manifest.get("name") or node_id),
                "phase": str(manifest.get("phase") or ""),
                "supportsResume": bool(manifest.get("supportsResume")),
                "criticality": str(manifest.get("criticality") or "critical"),
            }
        )
    return nodes


def _open_state_readonly(path: Path) -> sqlite3.Connection:
    if not path.is_file() or path.stat().st_size <= 0:
        raise FileNotFoundError(f"update state database is missing: {path}")
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _latest_real_run(
    connection: sqlite3.Connection,
    requested_run_id: str | None,
) -> sqlite3.Row | None:
    if requested_run_id:
        rows = connection.execute(
            "SELECT * FROM daily_update_run WHERE run_id=?",
            (requested_run_id,),
        ).fetchall()
    else:
        rows = connection.execute(
            "SELECT * FROM daily_update_run ORDER BY started_at DESC LIMIT 50"
        ).fetchall()
    for row in rows:
        try:
            metadata = json.loads(str(row["metadata_json"] or "{}"))
        except json.JSONDecodeError:
            metadata = {}
        if bool(metadata.get("dryRun")):
            continue
        return row
    return None


def _safe_run_dir(workspace: WorkspaceContext, value: object) -> Path | None:
    text = str(value or "").strip()
    if not text:
        return None
    candidate = Path(text).resolve(strict=False)
    log_root = workspace.log_root.resolve(strict=False)
    try:
        candidate.relative_to(log_root)
    except ValueError:
        return None
    return candidate


def _lock_state(workspace: WorkspaceContext, run_id: str) -> tuple[bool, bool, int | None]:
    lock_path = workspace.lock_root / "daily_update.lock"
    lock = _read_json(lock_path)
    if str(lock.get("runId") or "") != run_id:
        return False, False, None
    try:
        pid = int(lock.get("pid") or 0)
    except (TypeError, ValueError):
        pid = 0
    active = _lock_owner_active(lock)
    return active, not active, pid or None


def _validated_reusable_result(
    node: dict[str, Any],
    row: dict[str, Any],
    run_id: str,
) -> bool:
    status = str(row.get("status") or "")
    if status not in REUSABLE_NODE_STATUSES:
        return False
    result_path = Path(str(row.get("result_path") or ""))
    if not result_path.is_file():
        return False
    result = _read_json(result_path)
    if str(result.get("nodeId") or "") != str(node["id"]):
        return False
    if str(result.get("runId") or "") != run_id:
        return False
    if str(result.get("status") or "") != status:
        return False
    validation = result.get("validation")
    if not isinstance(validation, dict) or validation.get("status") != "passed":
        return False
    expected_output = str(row.get("output_fingerprint") or "")
    if expected_output and fingerprint(result) != expected_output:
        return False
    artifacts = result.get("artifacts")
    if not isinstance(artifacts, list):
        return False
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            return False
        if artifact.get("validationStatus") == "failed":
            return False
        artifact_path = Path(str(artifact.get("path") or ""))
        if not artifact_path.exists():
            return False
        if artifact_path.is_file():
            expected_hash = str(artifact.get("sha256") or "").strip()
            if expected_hash and file_sha256(artifact_path) != expected_hash:
                return False
    return True


def build_resume_plan(
    workspace_root: Path,
    *,
    run_id: str | None = None,
) -> dict[str, Any]:
    workspace = load_workspace(workspace_root)
    nodes = _daily_nodes(workspace)
    plan: dict[str, Any] = {
        "schemaVersion": 1,
        "available": False,
        "active": False,
        "staleLock": False,
        "runId": "",
        "runStatus": "not_found",
        "currentStage": "",
        "suggestedFromNode": "",
        "suggestedFromNodeName": "",
        "suggestedNodeSupportsResume": False,
        "completedNodes": 0,
        "totalNodes": len(nodes),
        "lastCheckpointAt": "",
        "runDir": "",
        "reason": "没有找到可检查的真实更新批次",
    }
    try:
        connection = _open_state_readonly(workspace.state_db)
    except (FileNotFoundError, sqlite3.Error) as exc:
        plan["reason"] = str(exc)
        return plan
    try:
        row = _latest_real_run(connection, run_id)
        if row is None:
            return plan
        selected_run_id = str(row["run_id"] or "")
        run_status = str(row["status"] or "")
        current_stage = str(row["current_stage"] or "")
        run_dir = _safe_run_dir(workspace, row["run_dir"])
        active, stale_lock, lock_pid = _lock_state(workspace, selected_run_id)
        node_rows = {
            str(item["node_id"]): dict(item)
            for item in connection.execute(
                "SELECT node_id,status,attempts,started_at,finished_at,returncode,result_path,error,"
                "output_fingerprint "
                "FROM daily_update_node WHERE run_id=?",
                (selected_run_id,),
            ).fetchall()
        }
        checkpoint = (
            _read_json(run_dir / "resume_checkpoint.json")
            if run_dir is not None
            else {}
        )
        last_checkpoint_at = str(
            checkpoint.get("updatedAt") or row["heartbeat_at"] or ""
        )

        preferred_node_id = ""
        if current_stage:
            current_row = node_rows.get(current_stage) or {}
            if str(current_row.get("status") or "") not in REUSABLE_NODE_STATUSES:
                preferred_node_id = current_stage
        if not preferred_node_id:
            checkpoint_node = str(checkpoint.get("nextResumeNode") or "")
            if checkpoint_node:
                checkpoint_row = node_rows.get(checkpoint_node) or {}
                if str(checkpoint_row.get("status") or "") not in REUSABLE_NODE_STATUSES:
                    preferred_node_id = checkpoint_node
        suggested_node: dict[str, Any] | None = None
        completed_nodes = 0
        for node in nodes:
            if preferred_node_id and node["id"] == preferred_node_id:
                suggested_node = node
                break
            node_row = node_rows.get(node["id"]) or {}
            if not _validated_reusable_result(node, node_row, selected_run_id):
                suggested_node = node
                break
            completed_nodes += 1

        plan.update(
            {
                "active": active,
                "staleLock": stale_lock,
                "lockPid": lock_pid,
                "runId": selected_run_id,
                "runStatus": run_status,
                "currentStage": current_stage,
                "completedNodes": completed_nodes,
                "lastCheckpointAt": last_checkpoint_at,
                "runDir": str(run_dir or ""),
            }
        )
        if run_status in COMPLETED_RUN_STATUSES:
            plan["reason"] = f"最近真实批次已经完成：{run_status}"
            return plan
        if active:
            plan["reason"] = "最近批次仍有有效调度进程，禁止并发续作或重做"
            return plan
        if suggested_node is None:
            plan["reason"] = "未发现需要重新执行的节点"
            return plan
        plan.update(
            {
                "available": True,
                "suggestedFromNode": suggested_node["id"],
                "suggestedFromNodeName": suggested_node["name"],
                "suggestedNodeSupportsResume": bool(suggested_node["supportsResume"]),
                "reason": "将复用此前已验证节点，并从首个未完成或失败节点继续",
            }
        )
        return plan
    finally:
        connection.close()


def build_recommended_resume_plan(workspace_root: Path) -> dict[str, Any]:
    """Prefer the most advanced resumable run from the latest business day.

    A short, newer run can otherwise hide an earlier run that already finished
    collection and processing.  A currently active newest run always wins the
    safety decision and blocks another launch.
    """

    workspace = load_workspace(workspace_root)
    try:
        connection = _open_state_readonly(workspace.state_db)
    except (FileNotFoundError, sqlite3.Error):
        return build_resume_plan(workspace_root)
    try:
        rows = connection.execute(
            "SELECT run_id,status,started_at,metadata_json FROM daily_update_run "
            "ORDER BY started_at DESC LIMIT 50"
        ).fetchall()
        real_rows: list[sqlite3.Row] = []
        for row in rows:
            try:
                metadata = json.loads(str(row["metadata_json"] or "{}"))
            except json.JSONDecodeError:
                metadata = {}
            if not bool(metadata.get("dryRun")):
                real_rows.append(row)
        if not real_rows:
            return build_resume_plan(workspace_root)
        latest = real_rows[0]
        latest_run_id = str(latest["run_id"])
        latest_plan = build_resume_plan(workspace_root, run_id=latest_run_id)
        latest_plan.update(
            {
                "selectionPolicy": "most_advanced_same_business_day",
                "latestRunId": latest_run_id,
                "latestRunStatus": str(latest["status"] or ""),
                "latestCompletedNodes": int(latest_plan.get("completedNodes") or 0),
                "latestSuggestedFromNode": str(
                    latest_plan.get("suggestedFromNode") or ""
                ),
                "isLatestRun": True,
                "candidateCount": 1,
            }
        )
        if latest_plan.get("active") or str(latest["status"] or "") in COMPLETED_RUN_STATUSES:
            return latest_plan
        latest_day = str(latest["started_at"] or "")[:10]
        candidate_plans: list[dict[str, Any]] = []
        for row in real_rows:
            if str(row["started_at"] or "")[:10] != latest_day:
                continue
            if str(row["status"] or "") in COMPLETED_RUN_STATUSES:
                continue
            candidate = build_resume_plan(
                workspace_root,
                run_id=str(row["run_id"]),
            )
            if candidate.get("active"):
                return latest_plan
            if candidate.get("available"):
                candidate_plans.append(candidate)
        if not candidate_plans:
            return latest_plan
        recommended = max(
            candidate_plans,
            key=lambda item: (
                int(item.get("completedNodes") or 0),
                str(item.get("lastCheckpointAt") or ""),
            ),
        )
        recommended.update(
            {
                "selectionPolicy": "most_advanced_same_business_day",
                "latestRunId": latest_run_id,
                "latestRunStatus": str(latest["status"] or ""),
                "latestCompletedNodes": int(latest_plan.get("completedNodes") or 0),
                "latestSuggestedFromNode": str(
                    latest_plan.get("suggestedFromNode") or ""
                ),
                "isLatestRun": str(recommended.get("runId") or "") == latest_run_id,
                "candidateCount": len(candidate_plans),
                "workspaceHasStaleLock": any(
                    bool(item.get("staleLock")) for item in candidate_plans
                ),
            }
        )
        if not recommended["isLatestRun"]:
            recommended["reason"] = (
                "同日存在多个未完成批次；为避免重复采集，推荐已完成节点更多的断点"
            )
        return recommended
    finally:
        connection.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="检查每日更新最近批次的续作计划")
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument("--run-id")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    plan = (
        build_resume_plan(args.workspace_root, run_id=args.run_id)
        if args.run_id
        else build_recommended_resume_plan(args.workspace_root)
    )
    if args.json:
        print(json.dumps(plan, ensure_ascii=False))
    else:
        print(json.dumps(plan, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
