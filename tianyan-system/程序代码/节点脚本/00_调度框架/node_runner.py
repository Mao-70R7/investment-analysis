from __future__ import annotations

import hashlib
import json
import os
import queue
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from progress import (
    build_pipeline_status,
    parse_progress,
    render_pipeline_status,
    render_progress,
)
from state_store import StateStore
from workspace import WorkspaceContext


def now_text() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def fingerprint(payload: Any) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def is_ephemeral_runtime_context_key(key: str) -> bool:
    return key.startswith("ADVISOR_NODE_STATUS_") or key in {
        "TTFUND_INCREMENTAL_RUN_ID",
        "TTFUND_INCREMENTAL_SUMMARY_PATH",
        "TTFUND_COLLECTION_REQUIRED",
        "TTFUND_TARGET_TRADE_DATE",
        "TTFUND_COLLECT_RUN_ID",
        "TTFUND_LOADED",
        "TTFUND_LOAD_FAILED",
        "GFFUNDS_PERFORMANCE_RUN_ID",
        "GFFUNDS_PERFORMANCE_SUMMARY_PATH",
        "GFFUNDS_COLLECT_RUN_ID",
        "GFFUNDS_COLLECT_SUMMARY_PATH",
        "GFFUNDS_COLLECT_COVERAGE_PATH",
        "GFFUNDS_GATE_PASSED",
        "GFFUNDS_GATE_RUN_ID",
        "GFFUNDS_LOADED",
        "GFFUNDS_LOAD_FAILED",
        "GFSEC_FIMA_COLLECT_RUN_ID",
        "GFSEC_FIMA_COLLECT_SUMMARY_PATH",
        "GFSEC_FIMA_COLLECT_COVERAGE_PATH",
        "GFSEC_FIMA_GATE_PASSED",
        "GFSEC_FIMA_GATE_RUN_ID",
        "GFSEC_FIMA_LOADED",
        "GFSEC_FIMA_LOAD_FAILED",
        "ADVISOR_REPORT_STAGING_ROOT",
        "ADVISOR_REPORT_PROMOTED",
        "ADVISOR_DATABASE_BACKUP_PATH",
    }


def _process_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        process_query_limited_information = 0x1000
        error_access_denied = 5
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return ctypes.get_last_error() == error_access_denied
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _process_start_time(pid: int) -> float | None:
    if pid <= 0 or os.name != "nt":
        return None
    import ctypes

    class FileTime(ctypes.Structure):
        _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.GetProcessTimes.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(FileTime),
        ctypes.POINTER(FileTime),
        ctypes.POINTER(FileTime),
        ctypes.POINTER(FileTime),
    ]
    kernel32.GetProcessTimes.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    creation = FileTime()
    exit_time = FileTime()
    kernel_time = FileTime()
    user_time = FileTime()
    try:
        if not kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time),
        ):
            return None
    finally:
        kernel32.CloseHandle(handle)
    windows_ticks = (int(creation.high) << 32) | int(creation.low)
    return windows_ticks / 10_000_000.0 - 11_644_473_600.0


def _lock_owner_active(payload: dict[str, Any]) -> bool:
    try:
        pid = int(payload.get("pid") or 0)
    except (TypeError, ValueError):
        return False
    if not _process_exists(pid):
        return False
    observed_start = _process_start_time(pid)
    try:
        recorded_start = float(payload.get("processStartedAtEpoch"))
    except (TypeError, ValueError):
        recorded_start = None
    if observed_start is not None and recorded_start is not None:
        return abs(observed_start - recorded_start) <= 2.0
    acquired_text = str(payload.get("acquiredAt") or "").strip()
    if observed_start is not None and acquired_text:
        try:
            acquired_epoch = datetime.fromisoformat(acquired_text).timestamp()
        except ValueError:
            acquired_epoch = None
        if acquired_epoch is not None and observed_start > acquired_epoch + 2.0:
            return False
    return True


def acquire_resource_lock(workspace: WorkspaceContext, name: str, run_id: str, node_id: str) -> tuple[Path, str]:
    workspace.lock_root.mkdir(parents=True, exist_ok=True)
    path = workspace.lock_root / f"{name}.lock"
    token = fingerprint({"pid": os.getpid(), "runId": run_id, "nodeId": node_id, "time": time.time()})
    for _ in range(2):
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                current = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError):
                current = {}
            if _lock_owner_active(current):
                raise RuntimeError(
                    f"resource lock is active: {name}, run={current.get('runId')}, node={current.get('nodeId')}"
                )
            path.unlink(missing_ok=True)
            continue
        try:
            os.write(
                descriptor,
                json.dumps(
                    {
                        "pid": os.getpid(),
                        "processStartedAtEpoch": _process_start_time(os.getpid()),
                        "runId": run_id,
                        "nodeId": node_id,
                        "token": token,
                        "acquiredAt": now_text(),
                    },
                    ensure_ascii=False,
                ).encode("utf-8"),
            )
        finally:
            os.close(descriptor)
        return path, token
    raise RuntimeError(f"unable to acquire resource lock: {name}")


def release_resource_lock(path: Path, token: str) -> None:
    try:
        current = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        current = {}
    if current.get("token") == token:
        path.unlink(missing_ok=True)


@dataclass(frozen=True)
class NodeExecution:
    node_id: str
    status: str
    returncode: int
    result_path: Path
    output_fingerprint: str
    context_updates: dict[str, str]
    error: str | None


class NodeRunner:
    def __init__(
        self,
        workspace: WorkspaceContext,
        state: StateStore,
        run_id: str,
        run_dir: Path,
        console: Callable[[str], None],
        event: Callable[..., None] | None = None,
        dry_run: bool = False,
        plan_nodes: list[dict[str, Any]] | None = None,
        duration_estimates: dict[str, float] | None = None,
        plan_started_monotonic: float | None = None,
        recovery_checkpoint: Callable[..., None] | None = None,
        checkpoint_interval_seconds: int = 600,
    ) -> None:
        self.workspace = workspace
        self.state = state
        self.run_id = run_id
        self.run_dir = run_dir
        self.console = console
        self.event = event
        self.dry_run = dry_run
        self.plan_nodes = list(plan_nodes or [])
        self.plan_index_by_node = {
            str(node.get("id") or ""): index
            for index, node in enumerate(self.plan_nodes)
        }
        self.duration_estimates = dict(duration_estimates or {})
        self.plan_started_monotonic = (
            float(plan_started_monotonic)
            if plan_started_monotonic is not None
            else time.monotonic()
        )
        self.recovery_checkpoint = recovery_checkpoint
        self.checkpoint_interval_seconds = max(60, int(checkpoint_interval_seconds))
        signatures = []
        source_roots = (
            workspace.node_root,
            workspace.code_root / "config",
            workspace.code_root / "schemas",
            workspace.code_root / "basic_data",
        )
        source_suffixes = {
            ".bat",
            ".css",
            ".csv",
            ".html",
            ".js",
            ".json",
            ".ps1",
            ".py",
            ".sql",
            ".txt",
            ".yaml",
            ".yml",
        }
        for root in source_roots:
            if not root.is_dir():
                continue
            for path in root.rglob("*"):
                if (
                    path.is_file()
                    and path.suffix.lower() in source_suffixes
                    and "__pycache__" not in path.parts
                    and "98_非生产工具" not in path.parts
                    and "99_兼容入口" not in path.parts
                ):
                    signatures.append(
                        (
                            path.relative_to(workspace.code_root).as_posix(),
                            file_sha256(path),
                        )
                    )
        self.code_fingerprint = fingerprint(sorted(signatures))

    def _input_fingerprint(self, manifest: dict[str, Any], dependency_results: dict[str, str]) -> str:
        return fingerprint(
            {
                "node": manifest,
                "dependencies": dependency_results,
                "workspaceConfig": self.workspace.config,
                "codeFingerprint": self.code_fingerprint,
            }
        )

    def _load_previous(
        self,
        manifest: dict[str, Any],
        *,
        input_hash: str | None,
        explicit_recovery: bool = False,
    ) -> NodeExecution | None:
        current = self.state.get_node(self.run_id, manifest["id"])
        candidates: list[dict[str, Any]] = []
        seen_result_paths: set[str] = set()
        if current:
            current_candidate = dict(current)
            candidates.append(current_candidate)
            seen_result_paths.add(str(current_candidate.get("result_path") or ""))
        if explicit_recovery:
            for attempt in self.state.get_node_attempts(self.run_id, manifest["id"]):
                candidate = dict(attempt)
                result_path_text = str(candidate.get("result_path") or "")
                if result_path_text and result_path_text not in seen_result_paths:
                    candidates.append(candidate)
                    seen_result_paths.add(result_path_text)
        for candidate in candidates:
            previous = self._validate_previous_candidate(
                manifest,
                candidate,
                input_hash=input_hash,
                explicit_recovery=explicit_recovery,
            )
            if previous is not None:
                return previous
        return None

    def _validate_previous_candidate(
        self,
        manifest: dict[str, Any],
        row: dict[str, Any],
        *,
        input_hash: str | None,
        explicit_recovery: bool,
    ) -> NodeExecution | None:
        row_status = str(row["status"])
        preserved_skip = explicit_recovery and row_status == "skipped"
        preserved_optional_failure = (
            explicit_recovery
            and row_status == "failed"
            and str(manifest.get("criticality") or "") == "optional"
        )
        preserved_non_success = preserved_skip or preserved_optional_failure
        if row_status not in {"success", "warn"} and not preserved_non_success:
            return None
        if input_hash is not None and row["input_fingerprint"] != input_hash:
            return None
        result_path = Path(str(row["result_path"] or ""))
        if not result_path.is_file():
            return None
        try:
            result = json.loads(result_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return None
        if str(result.get("nodeId") or "") != str(manifest["id"]):
            return None
        if str(result.get("runId") or "") != self.run_id:
            return None
        if row.get("attempt") is not None and int(result.get("attempt") or -1) != int(row["attempt"]):
            return None
        if str(result.get("status") or "") != row_status:
            return None
        if row.get("returncode") is not None and int(result.get("returncode") or 0) != int(row["returncode"]):
            return None
        if result.get("validation", {}).get("status") != "passed" and not preserved_optional_failure:
            return None
        current_output_hash = fingerprint(result)
        expected_output_hash = str(row.get("output_fingerprint") or "")
        if expected_output_hash and expected_output_hash != current_output_hash:
            return None
        if not expected_output_hash and not explicit_recovery:
            return None
        for artifact in result.get("artifacts") or []:
            if artifact.get("validationStatus") == "failed":
                if preserved_optional_failure:
                    # Failed optional artifacts are deliberately not consumed:
                    # failed nodes do not restore contextUpdates.  Older failed
                    # attempts may not have hashes, so only the immutable
                    # node_result fingerprint is authoritative for preserving
                    # the historical failure state.
                    continue
                return None
            artifact_path = Path(str(artifact.get("path") or ""))
            if artifact_path and not artifact_path.exists():
                return None
            if artifact_path.is_file():
                expected_hash = str(artifact.get("sha256") or "").strip()
                if not expected_hash or file_sha256(artifact_path) != expected_hash:
                    return None
        return NodeExecution(
            node_id=manifest["id"],
            status=str(row["status"]),
            returncode=int(row["returncode"] or 0),
            result_path=result_path,
            output_fingerprint=current_output_hash,
            context_updates={str(k): str(v) for k, v in (result.get("contextUpdates") or {}).items()},
            error=row["error"],
        )

    def _valid_previous(self, manifest: dict[str, Any], input_hash: str) -> NodeExecution | None:
        return self._load_previous(manifest, input_hash=input_hash)

    def restore_previous(self, manifest: dict[str, Any]) -> NodeExecution:
        """Restore a validated result for an explicit from-node recovery.

        The operator has deliberately selected the first node that must be
        rebuilt, so upstream input fingerprints may differ after a repair.
        Successful and deliberately skipped states retain strict validation.
        A failed state is reusable only for an optional node, and its
        failure remains failed so dependency degradation and final
        classification stay truthful. Its failed artifacts are never consumed;
        the node result fingerprint remains mandatory. Successful artifact
        paths and hashes are still checked before reuse.
        """
        previous = self._load_previous(
            manifest,
            input_hash=None,
            explicit_recovery=True,
        )
        if previous is None:
            raise RuntimeError(
                f"node {manifest['id']} has no intact validated result to reuse; "
                "resume from this node or an earlier node"
            )
        return previous

    def skip(
        self,
        manifest: dict[str, Any],
        dependency_results: dict[str, str],
        reason: str,
    ) -> NodeExecution:
        node_id = str(manifest["id"])
        input_hash = self._input_fingerprint(manifest, dependency_results)
        result_path = self.run_dir / "nodes" / node_id / "skipped" / "node_result.json"
        result = {
            "schemaVersion": 1,
            "nodeId": node_id,
            "runId": self.run_id,
            "startedAt": now_text(),
            "finishedAt": now_text(),
            "status": "skipped",
            "returncode": 0,
            "inputFingerprint": input_hash,
            "artifacts": [],
            "counters": {},
            "warnings": [reason],
            "error": reason,
            "retryable": False,
            "contextUpdates": {},
            "validation": {"status": "passed", "detail": reason},
        }
        atomic_json(result_path, result)
        output_hash = fingerprint(result)
        self.state.skip_node(
            self.run_id,
            node_id,
            input_hash,
            result_path,
            output_hash,
            reason,
        )
        self.state.record_artifacts(
            self.run_id,
            node_id,
            [
                {
                    "key": "node_result",
                    "path": str(result_path),
                    "sha256": file_sha256(result_path),
                    "validationStatus": "passed",
                }
            ],
        )
        self.console(f"[节点跳过] {node_id} {manifest['name']}：{reason}")
        return NodeExecution(
            node_id=node_id,
            status="skipped",
            returncode=0,
            result_path=result_path,
            output_fingerprint=output_hash,
            context_updates={},
            error=reason,
        )

    def run(
        self,
        manifest: dict[str, Any],
        dependency_results: dict[str, str],
        runtime_context: dict[str, str],
        *,
        allow_skip: bool,
    ) -> NodeExecution:
        node_id = str(manifest["id"])
        node_name = str(manifest["name"])
        input_hash = self._input_fingerprint(manifest, dependency_results)
        if allow_skip:
            previous = self._valid_previous(manifest, input_hash)
            if previous:
                self.console(f"[节点跳过] {node_id} {node_name}：已完成且输入输出校验仍有效。")
                return previous

        max_attempts = max(1, int(manifest.get("maxProcessAttempts") or 1))
        timeout_seconds = max(1, int(manifest.get("timeoutSeconds") or 3600))
        resource_name = str(manifest.get("resourceLock") or "").strip()
        resource: tuple[Path, str] | None = None
        try:
            if resource_name:
                resource = acquire_resource_lock(self.workspace, resource_name, self.run_id, node_id)
                self.console(f"[资源锁] {node_id} 已取得 {resource_name}")
            return self._run_attempts(
                manifest,
                dependency_results,
                runtime_context,
                input_hash,
                max_attempts,
                timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001 - framework failures must be persisted per node.
            return self._record_framework_failure(
                manifest,
                input_hash,
                f"{type(exc).__name__}: {exc}",
            )
        finally:
            if resource:
                release_resource_lock(*resource)

    def _record_framework_failure(
        self,
        manifest: dict[str, Any],
        input_hash: str,
        error: str,
    ) -> NodeExecution:
        node_id = str(manifest["id"])
        self.state.interrupt_running_nodes(
            self.run_id,
            reason=f"framework exception before retry record: {error}",
        )
        existing = self.state.get_node(self.run_id, node_id)
        attempt = (int(existing["attempts"] or 0) if existing else 0) + 1
        attempt_dir = self.run_dir / "nodes" / node_id / f"attempt_{attempt:02d}"
        attempt_dir.mkdir(parents=True, exist_ok=True)
        log_path = attempt_dir / "console.log"
        result_path = attempt_dir / "node_result.json"
        self.state.start_node(self.run_id, node_id, attempt, input_hash, log_path, None)
        log_path.write_text(error + "\n", encoding="utf-8")
        result = {
            "schemaVersion": 1,
            "nodeId": node_id,
            "runId": self.run_id,
            "attempt": attempt,
            "startedAt": now_text(),
            "finishedAt": now_text(),
            "status": "failed",
            "returncode": 1,
            "inputFingerprint": input_hash,
            "artifacts": [],
            "counters": {},
            "warnings": [],
            "error": error,
            "retryable": False,
            "contextUpdates": {},
            "validation": {"status": "failed", "detail": error},
        }
        atomic_json(result_path, result)
        output_hash = fingerprint(result)
        self.state.finish_node(
            self.run_id,
            node_id,
            attempt,
            "failed",
            1,
            result_path,
            output_hash,
            error,
        )
        self.state.record_artifacts(
            self.run_id,
            node_id,
            [
                {
                    "key": "node_result",
                    "path": str(result_path),
                    "sha256": file_sha256(result_path),
                    "validationStatus": "failed",
                }
            ],
        )
        self.console(f"[节点框架失败] {node_id} {manifest['name']}：{error}")
        return NodeExecution(
            node_id=node_id,
            status="failed",
            returncode=1,
            result_path=result_path,
            output_fingerprint=output_hash,
            context_updates={},
            error=error,
        )

    def _run_attempts(
        self,
        manifest: dict[str, Any],
        dependency_results: dict[str, str],
        runtime_context: dict[str, str],
        input_hash: str,
        max_attempts: int,
        timeout_seconds: int,
    ) -> NodeExecution:
        del dependency_results
        node_id = str(manifest["id"])
        node_name = str(manifest["name"])
        last_execution: NodeExecution | None = None
        existing = self.state.get_node(self.run_id, node_id)
        previous_attempts = int(existing["attempts"] or 0) if existing else 0
        for process_attempt in range(1, max_attempts + 1):
            attempt = previous_attempts + process_attempt
            attempt_dir = self.run_dir / "nodes" / node_id / f"attempt_{attempt:02d}"
            attempt_dir.mkdir(parents=True, exist_ok=True)
            log_path = attempt_dir / "console.log"
            result_path = attempt_dir / "node_result.json"
            artifacts_path = attempt_dir / "artifacts.json"
            self.state.start_node(
                self.run_id,
                node_id,
                attempt,
                input_hash,
                log_path,
                runtime_context.get("ADVISOR_DEVICE_ID"),
            )
            started_at = now_text()
            started_monotonic = time.monotonic()
            entrypoint = Path(str(manifest["_directory"])) / str(manifest["entrypoint"])
            command = [
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(entrypoint),
                "-WorkspaceRoot",
                str(self.workspace.workspace_root),
                "-RunId",
                self.run_id,
                "-NodeRunDir",
                str(attempt_dir),
            ]
            if self.dry_run:
                command.append("-DryRun")
            environment = {
                key: value
                for key, value in os.environ.items()
                if not is_ephemeral_runtime_context_key(key)
            }
            environment.update(runtime_context)
            environment.update(
                {
                    "ADVISOR_WORKSPACE_ROOT": str(self.workspace.workspace_root),
                    "ADVISOR_CODE_ROOT": str(self.workspace.code_root),
                    "ADVISOR_NODE_ROOT": str(self.workspace.node_root),
                    "ADVISOR_LEGACY_PROGRAM_ROOT": str(self.workspace.legacy_program_root),
                    "ADVISOR_DATABASE_ROOT": str(self.workspace.database_root),
                    "ADVISOR_RAW_ROOT": str(self.workspace.raw_root),
                    "ADVISOR_NORMALIZED_ROOT": str(self.workspace.normalized_root),
                    "ADVISOR_LOG_ROOT": str(self.workspace.log_root),
                    "ADVISOR_LOCK_ROOT": str(self.workspace.lock_root),
                    "ADVISOR_TEMP_ROOT": str(self.workspace.temp_root),
                    "ADVISOR_OUTPUT_ROOT": str(self.workspace.output_root),
                    "ADVISOR_REPORT_ROOT": str(self.workspace.report_root),
                    "ADVISOR_PUBLISH_ROOT": str(self.workspace.publish_root),
                    "ADVISOR_BACKUP_ROOT": str(self.workspace.backup_root),
                    "ADVISOR_DAILY_RUN_ID": self.run_id,
                    "ADVISOR_NODE_ID": node_id,
                    "ADVISOR_NODE_RUN_DIR": str(attempt_dir),
                    "ADVISOR_RECOVERY_CHECKPOINT_PATH": str(
                        self.run_dir / "resume_checkpoint.json"
                    ),
                    "ADVISOR_RECOVERY_CHECKPOINT_INTERVAL_SECONDS": str(
                        self.checkpoint_interval_seconds
                    ),
                    "PYTHONUTF8": "1",
                    "PYTHONIOENCODING": "utf-8",
                    "PYTHONUNBUFFERED": "1",
                }
            )
            python_paths = [
                str(self.workspace.node_root / "_共享组件" / "python_src"),
                str(self.workspace.legacy_program_root),
            ]
            if environment.get("PYTHONPATH"):
                python_paths.append(environment["PYTHONPATH"])
            environment["PYTHONPATH"] = os.pathsep.join(python_paths)
            self.console(
                f"[节点开始] {node_id} {node_name}，"
                f"本次尝试 {process_attempt}/{max_attempts}，累计尝试 {attempt}"
            )
            returncode = 1
            error: str | None = None
            with log_path.open("w", encoding="utf-8-sig") as log_handle:
                log_handle.write("COMMAND " + subprocess.list2cmdline(command) + "\n")
                process: subprocess.Popen[str] | None = None
                reader: threading.Thread | None = None
                try:
                    process = subprocess.Popen(
                        command,
                        cwd=self.workspace.code_root,
                        env=environment,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                    )
                    assert process.stdout is not None
                    output_queue: queue.Queue[str | None] = queue.Queue()

                    def pump_output() -> None:
                        assert process.stdout is not None
                        for output_line in process.stdout:
                            output_queue.put(output_line)
                        output_queue.put(None)

                    reader = threading.Thread(target=pump_output, name=f"node-output-{node_id}", daemon=True)
                    reader.start()
                    stream_closed = False
                    latest_progress: dict[str, Any] | None = None
                    next_heartbeat = time.monotonic() + 30
                    next_recovery_checkpoint = (
                        time.monotonic() + self.checkpoint_interval_seconds
                    )
                    while True:
                        now = time.monotonic()
                        if now - started_monotonic > timeout_seconds:
                            subprocess.run(
                                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                capture_output=True,
                                check=False,
                            )
                            error = f"node timeout after {timeout_seconds}s"
                            returncode = 124
                            try:
                                process.wait(timeout=30)
                            except subprocess.TimeoutExpired:
                                error = f"node timeout after {timeout_seconds}s and process tree did not exit"
                            break
                        try:
                            line = output_queue.get(timeout=1)
                        except queue.Empty:
                            line = ""
                        if line is None:
                            stream_closed = True
                        elif line:
                            text = line.rstrip("\r\n")
                            log_handle.write(text + "\n")
                            log_handle.flush()
                            progress = parse_progress(text)
                            if progress:
                                latest_progress = progress
                                self.console(render_progress(node_name, progress))
                                if self.event:
                                    self.event("node_progress", nodeId=node_id, attempt=attempt, **progress)
                            elif any(token in text for token in ("[ERROR]", "[WARN]", "[DONE]", "[RESULT]", "[失败]", "[完成]")):
                                self.console(f"  {text}")
                        if now >= next_heartbeat and process.poll() is None:
                            elapsed = int(now - started_monotonic)
                            uses_device = str(manifest.get("resourceLock") or "") == "device"
                            device = (
                                runtime_context.get("ADVISOR_DEVICE_ID") or "未选定"
                                if uses_device
                                else "不适用"
                            )
                            self.state.update_run(self.run_id, status="running", current_stage=node_id)
                            plan_index = self.plan_index_by_node.get(node_id)
                            pipeline_status: dict[str, Any] | None = None
                            if plan_index is not None and self.plan_nodes:
                                pipeline_status = build_pipeline_status(
                                    self.plan_nodes,
                                    plan_index,
                                    latest_progress,
                                    node_elapsed_seconds=elapsed,
                                    total_elapsed_seconds=now - self.plan_started_monotonic,
                                    duration_estimates=self.duration_estimates,
                                )
                                self.console(
                                    render_pipeline_status(
                                        "整体心跳",
                                        manifest,
                                        pipeline_status,
                                        device=device,
                                        log_path=str(log_path),
                                    )
                                )
                            else:
                                self.console(
                                    f"[节点心跳] {node_id} 已运行 {elapsed}s，"
                                    f"设备={device}，日志={log_path}"
                                )
                            if self.event:
                                heartbeat_payload = {
                                    "nodeId": node_id,
                                    "attempt": attempt,
                                    "elapsedSeconds": elapsed,
                                    "device": device,
                                }
                                if pipeline_status:
                                    heartbeat_payload.update(pipeline_status)
                                self.event("node_heartbeat", **heartbeat_payload)
                            if (
                                self.recovery_checkpoint is not None
                                and now >= next_recovery_checkpoint
                            ):
                                try:
                                    self.recovery_checkpoint(
                                        reason="interval",
                                        manifest=manifest,
                                        attempt=attempt,
                                        elapsed_seconds=elapsed,
                                        progress=latest_progress,
                                        pipeline_status=pipeline_status,
                                        log_path=log_path,
                                    )
                                except Exception as checkpoint_exc:  # noqa: BLE001 - recovery metadata must not stop data work.
                                    self.console(
                                        "[恢复点警告] 10分钟恢复点写入失败，节点继续运行："
                                        f"{type(checkpoint_exc).__name__}: {checkpoint_exc}"
                                    )
                                next_recovery_checkpoint = (
                                    now + self.checkpoint_interval_seconds
                                )
                            next_heartbeat = now + 30
                        if process.poll() is not None and stream_closed:
                            returncode = int(process.returncode or 0)
                            break
                except Exception as exc:  # noqa: BLE001 - persist every process-level failure.
                    error = f"{type(exc).__name__}: {exc}"
                    returncode = 1
                finally:
                    if reader is not None:
                        reader.join(timeout=5)
                    if process is not None and process.stdout is not None:
                        process.stdout.close()

            result: dict[str, Any]
            result_error: str | None = None
            if result_path.is_file():
                try:
                    result = json.loads(result_path.read_text(encoding="utf-8-sig"))
                except (OSError, json.JSONDecodeError) as exc:
                    result_error = f"invalid node result: {exc}"
                    result = {"validation": {"status": "failed", "detail": result_error}}
            else:
                result_error = "node_result.json is missing"
                result = {"validation": {"status": "failed", "detail": result_error}}
            validation = result.get("validation") if isinstance(result.get("validation"), dict) else {}
            validation_status = str(validation.get("status") or ("passed" if returncode == 0 else "failed"))
            if result_error:
                validation_status = "failed"
                error = result_error
                if returncode == 0:
                    returncode = 3
            if returncode == 0 and validation_status != "passed":
                returncode = 3
                error = str(validation.get("detail") or "node output validation failed")
            artifacts = result.get("artifacts") if isinstance(result.get("artifacts"), list) else []
            artifact_errors = []
            if not self.dry_run:
                for artifact in artifacts:
                    path_text = str(artifact.get("path") or "").strip()
                    artifact_path = Path(path_text) if path_text else None
                    if not artifact_path:
                        artifact_errors.append(f"artifact path missing: {artifact.get('key') or 'unknown'}")
                    elif artifact.get("validationStatus") == "failed":
                        artifact_errors.append(f"failed artifact: {artifact.get('key') or artifact_path}")
                    elif not artifact_path.exists():
                        artifact_errors.append(f"missing artifact: {artifact_path}")
                    elif artifact_path.is_file() and not artifact.get("sha256"):
                        artifact["sha256"] = file_sha256(artifact_path)
            if returncode == 0 and artifact_errors:
                returncode = 3
                error = "; ".join(artifact_errors)
                validation_status = "failed"
            status = "success" if returncode == 0 else "failed"
            if returncode == 0 and result.get("warnings"):
                status = "warn"
            result.setdefault("schemaVersion", 1)
            result.setdefault("nodeId", node_id)
            result.setdefault("runId", self.run_id)
            result.setdefault("attempt", attempt)
            result.setdefault("startedAt", started_at)
            result.setdefault("finishedAt", now_text())
            result.setdefault("artifacts", artifacts)
            result.setdefault("counters", {})
            result.setdefault("warnings", [])
            result.setdefault("retryable", returncode in {1, 20, 22, 124})
            result.setdefault("contextUpdates", {})
            result["status"] = status
            result["returncode"] = returncode
            result["inputFingerprint"] = input_hash
            if error:
                result["error"] = error
            if returncode != 0:
                result["validation"] = {"status": "failed", "detail": error or f"exit={returncode}"}
            atomic_json(result_path, result)
            result_watermark = result.get("watermarks") if isinstance(result.get("watermarks"), dict) else {}
            persisted_artifacts = list(artifacts)
            persisted_artifacts.append(
                {
                    "key": "node_result",
                    "path": str(result_path),
                    "sha256": file_sha256(result_path),
                    "watermark": json.dumps(result_watermark, ensure_ascii=False, sort_keys=True),
                    "validationStatus": validation_status,
                }
            )
            atomic_json(
                artifacts_path,
                {"nodeId": node_id, "runId": self.run_id, "artifacts": persisted_artifacts},
            )
            output_hash = fingerprint(result)
            self.state.finish_node(
                self.run_id,
                node_id,
                attempt,
                status,
                returncode,
                result_path,
                output_hash,
                error or result.get("error"),
            )
            self.state.record_artifacts(self.run_id, node_id, persisted_artifacts)
            last_execution = NodeExecution(
                node_id=node_id,
                status=status,
                returncode=returncode,
                result_path=result_path,
                output_fingerprint=output_hash,
                context_updates={str(k): str(v) for k, v in (result.get("contextUpdates") or {}).items()},
                error=error or result.get("error"),
            )
            elapsed = int(time.monotonic() - started_monotonic)
            self.console(f"[节点结束] {node_id} {node_name}，状态={status}，耗时={elapsed}s")
            if returncode == 0:
                return last_execution
            retryable = bool(result.get("retryable", returncode in {1, 20, 22, 124}))
            if not retryable or process_attempt >= max_attempts:
                break
            self.console(
                f"[节点重试] {node_id} 将进行本次第 {process_attempt + 1} 次、"
                f"累计第 {attempt + 1} 次进程级重试。"
            )
        assert last_execution is not None
        return last_execution
