from __future__ import annotations

import argparse
import ctypes
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
PROGRAM_ROOT = Path(__file__).resolve().parent
DEFAULT_REPORT_ROOT = PROJECT_ROOT / "site"
DEFAULT_BACKUP_DIR = PROJECT_ROOT / "data" / "backups"
DEFAULT_PAGES_URL = "https://mao-70r7.github.io/invest"
CHILD_SIGNAL_PATTERN = re.compile(
    r"(?i)(\bStep\s+\d+/\d+|^\[\d+/\d+\]|\[INFO\]|\[DONE\]|\[RESULT\]|"
    r"\[WARN\]|\[ERROR\]|\[STAGE\]|\[PROGRESS\]|progress\s+\d+/\d+|"
    r"\"progress\"\s*:\s*\"\d+/\d+\"|sharded_progress|HEARTBEAT|backup_progress)"
)
WRAPPER_TIMESTAMP_PATTERN = re.compile(r"^(?:\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\]\s*)+")


def strip_wrapper_timestamps(text: str) -> str:
    return WRAPPER_TIMESTAMP_PATTERN.sub("", text.strip())


def extract_child_progress(text: str) -> str | None:
    marker_index = text.find("[PROGRESS]")
    if marker_index >= 0:
        return text[marker_index:]
    json_match = re.search(r'\{.*"progress"\s*:\s*"(\d+)/(\d+)".*\}', text)
    if not json_match:
        return None
    completed, total = (int(value) for value in json_match.groups())
    percent = completed * 100.0 / total if total else 100.0
    return f"[PROGRESS] 子任务 | 已完成策略 {completed}/{total} ({percent:.1f}%)"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the resilient daily TTFund and GFFunds incremental update, backup and publish workflow."
    )
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--report-root", type=Path, default=DEFAULT_REPORT_ROOT)
    parser.add_argument("--publish-root", type=Path)
    parser.add_argument("--backup-dir", type=Path, default=DEFAULT_BACKUP_DIR)
    parser.add_argument("--backup-retain", type=int, default=1)
    parser.add_argument("--device-id", default="")
    parser.add_argument("--history-mode", choices=("latest_only", "all_missing", "none"), default="latest_only")
    parser.add_argument("--pages-base-url", default=DEFAULT_PAGES_URL)
    parser.add_argument("--readiness-interval-minutes", type=int, default=30)
    parser.add_argument("--readiness-max-checks", type=int, default=6)
    parser.add_argument("--resume-run-id")
    parser.add_argument("--incremental-extra-bat-args", default="")
    parser.add_argument("--skip-readiness", action="store_true")
    parser.add_argument("--skip-backup", action="store_true")
    parser.add_argument("--skip-publish", action="store_true")
    parser.add_argument("--skip-wait", action="store_true", help="Run one readiness check without sleeping.")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def now_local() -> datetime:
    return datetime.now().astimezone()


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    last_error: PermissionError | None = None
    for attempt in range(15):
        try:
            temp_path.replace(path)
            return
        except PermissionError as exc:
            last_error = exc
            if attempt >= 14:
                break
            time.sleep(min(0.05 * (2**attempt), 1.0))
    if last_error is not None:
        raise last_error


def process_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


class RunLock:
    def __init__(self, path: Path, run_id: str) -> None:
        self.path = path
        self.run_id = run_id

    def _payload(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "pid": os.getpid(),
            "heartbeat_at": now_local().isoformat(timespec="seconds"),
        }

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            try:
                current = json.loads(self.path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError):
                current = {}
            current_pid = int(current.get("pid") or 0)
            heartbeat_text = str(current.get("heartbeat_at") or "")
            try:
                heartbeat = datetime.fromisoformat(heartbeat_text)
            except ValueError:
                heartbeat = now_local() - timedelta(days=1)
            stale = not process_exists(current_pid) and now_local() - heartbeat > timedelta(minutes=10)
            if not stale:
                raise RuntimeError(
                    f"another daily update is active: run_id={current.get('run_id')}, pid={current_pid}"
                )
            self.path.unlink(missing_ok=True)
        descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        try:
            os.write(descriptor, json.dumps(self._payload(), ensure_ascii=False).encode("utf-8"))
        finally:
            os.close(descriptor)

    def heartbeat(self) -> None:
        atomic_write_json(self.path, self._payload())

    def release(self) -> None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        if payload.get("run_id") == self.run_id and int(payload.get("pid") or 0) == os.getpid():
            self.path.unlink(missing_ok=True)


class StateStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, timeout=60)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=60000")
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS daily_update_run (
                run_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                heartbeat_at TEXT NOT NULL,
                current_stage TEXT,
                run_dir TEXT NOT NULL,
                source_target_date TEXT,
                backup_path TEXT,
                error TEXT,
                metadata_json TEXT
            );
            CREATE TABLE IF NOT EXISTS daily_update_stage (
                run_id TEXT NOT NULL,
                stage TEXT NOT NULL,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                started_at TEXT,
                finished_at TEXT,
                returncode INTEGER,
                log_path TEXT,
                error TEXT,
                PRIMARY KEY (run_id, stage)
            );
            """
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def load_run(self, run_id: str) -> sqlite3.Row | None:
        self.conn.row_factory = sqlite3.Row
        return self.conn.execute("SELECT * FROM daily_update_run WHERE run_id=?", (run_id,)).fetchone()

    def create_run(self, run_id: str, run_dir: Path, metadata: dict[str, Any]) -> None:
        timestamp = now_local().isoformat(timespec="seconds")
        self.conn.execute(
            """
            INSERT INTO daily_update_run
                (run_id, status, started_at, heartbeat_at, run_dir, metadata_json)
            VALUES (?, 'created', ?, ?, ?, ?)
            """,
            (run_id, timestamp, timestamp, str(run_dir), json.dumps(metadata, ensure_ascii=False)),
        )
        self.conn.commit()

    def update_run(self, run_id: str, **fields: Any) -> None:
        fields["heartbeat_at"] = now_local().isoformat(timespec="seconds")
        assignments = ", ".join(f"{key}=?" for key in fields)
        self.conn.execute(
            f"UPDATE daily_update_run SET {assignments} WHERE run_id=?",
            (*fields.values(), run_id),
        )
        self.conn.commit()

    def stage_completed(self, run_id: str, stage: str) -> bool:
        row = self.conn.execute(
            "SELECT status FROM daily_update_stage WHERE run_id=? AND stage=?", (run_id, stage)
        ).fetchone()
        return bool(row and row[0] == "completed")

    def load_stage(self, run_id: str, stage: str) -> sqlite3.Row | None:
        self.conn.row_factory = sqlite3.Row
        return self.conn.execute(
            "SELECT * FROM daily_update_stage WHERE run_id=? AND stage=?",
            (run_id, stage),
        ).fetchone()

    def stage_start(self, run_id: str, stage: str, log_path: Path) -> None:
        timestamp = now_local().isoformat(timespec="seconds")
        self.conn.execute(
            """
            INSERT INTO daily_update_stage
                (run_id, stage, status, attempts, started_at, log_path)
            VALUES (?, ?, 'running', 1, ?, ?)
            ON CONFLICT(run_id, stage) DO UPDATE SET
                status='running', attempts=attempts+1, started_at=excluded.started_at,
                finished_at=NULL, returncode=NULL, log_path=excluded.log_path, error=NULL
            """,
            (run_id, stage, timestamp, str(log_path)),
        )
        self.conn.commit()
        self.update_run(run_id, status="running", current_stage=stage)

    def stage_finish(self, run_id: str, stage: str, returncode: int, error: str | None = None) -> None:
        status = "completed" if returncode == 0 else "failed"
        self.conn.execute(
            """
            UPDATE daily_update_stage
            SET status=?, finished_at=?, returncode=?, error=?
            WHERE run_id=? AND stage=?
            """,
            (status, now_local().isoformat(timespec="seconds"), returncode, error, run_id, stage),
        )
        self.conn.commit()


@dataclass
class CommandResult:
    returncode: int
    log_path: Path
    elapsed_seconds: float


class Orchestrator:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.project_root = args.project_root.resolve()
        self.state = StateStore(self.project_root / "data" / "update_state.sqlite")
        resumed = self.state.load_run(args.resume_run_id) if args.resume_run_id else None
        if args.resume_run_id and resumed is None:
            raise RuntimeError(f"resume run not found: {args.resume_run_id}")
        if resumed is not None:
            self.run_id = args.resume_run_id
            self.run_dir = Path(str(resumed["run_dir"]))
        else:
            self.run_id = now_local().strftime("%Y%m%dT%H%M%S%z")
            self.run_dir = (
                self.project_root / "logs" / "daily_update" / now_local().strftime("%Y-%m-%d") / self.run_id
            )
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.events_path = self.run_dir / "events.jsonl"
        self.summary_path = self.run_dir / "summary.json"
        self.console_log_path = self.run_dir / "console.log"
        self.session_started_monotonic = time.monotonic()
        self.lock_heartbeat_failure_total = 0
        self.workflow_steps: list[tuple[str, str]] = []
        if not args.skip_readiness:
            self.workflow_steps.append(("readiness", "数据就绪检查"))
        self.workflow_steps.extend(
            [
                ("incremental", "天天投顾及广发基金增量更新"),
                ("audit", "项目完整数据稽核"),
            ]
        )
        if not args.skip_backup:
            self.workflow_steps.append(("backup", "成功数据库滚动备份"))
        if not args.skip_publish:
            self.workflow_steps.append(("publish", "最小发布集构建及 GitHub 发布"))
        self.workflow_completed: set[str] = set()
        self.workflow_started_at: dict[str, float] = {}
        self.lock = RunLock(self.project_root / "data" / "daily_update.lock", self.run_id)
        if resumed is None:
            metadata = {
                key: str(value) if isinstance(value, Path) else value for key, value in vars(args).items()
            }
            self.state.create_run(self.run_id, self.run_dir, metadata)

    def event(self, event: str, **payload: Any) -> None:
        row = {
            "timestamp": now_local().isoformat(timespec="seconds"),
            "run_id": self.run_id,
            "event": event,
            **payload,
        }
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    @staticmethod
    def format_elapsed(seconds: float) -> str:
        total = max(0, int(seconds))
        hours, remainder = divmod(total, 3600)
        minutes, secs = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

    def console(self, message: str) -> None:
        line = f"[{now_local().strftime('%Y-%m-%d %H:%M:%S')}] {message}"
        print(line, flush=True)
        with self.console_log_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def workflow_label(self, key: str) -> str:
        return dict(self.workflow_steps).get(key, key)

    def print_progress(self, current_key: str, current_label: str | None = None) -> None:
        total = max(1, len(self.workflow_steps))
        completed = len(self.workflow_completed)
        percent = int(completed * 100 / total)
        remaining = max(0, total - completed)
        total_elapsed = self.format_elapsed(time.monotonic() - self.session_started_monotonic)
        step_started = self.workflow_started_at.get(current_key, self.session_started_monotonic)
        step_elapsed = self.format_elapsed(time.monotonic() - step_started)
        completed_labels = [label for key, label in self.workflow_steps if key in self.workflow_completed]
        pending_labels = [label for key, label in self.workflow_steps if key not in self.workflow_completed]
        label = current_label or self.workflow_label(current_key)
        self.console(
            f"[总进度 {percent:3d}% | 已完成 {completed}/{total} | 未完成 {remaining} 步 | "
            f"总耗时 {total_elapsed}] 当前：{label}，当前步骤已执行 {step_elapsed}"
        )
        self.console(f"  已完成步骤：{'、'.join(completed_labels) if completed_labels else '暂无'}")
        self.console(f"  未完成步骤：{'、'.join(pending_labels) if pending_labels else '暂无'}")

    def mark_workflow_completed(self, key: str) -> None:
        if key in dict(self.workflow_steps):
            self.workflow_completed.add(key)

    def heartbeat(self) -> None:
        try:
            self.lock.heartbeat()
        except OSError as exc:
            self.lock_heartbeat_failure_total += 1
            if self.lock_heartbeat_failure_total == 1 or self.lock_heartbeat_failure_total % 12 == 0:
                self.console(
                    "[警告] 运行锁心跳暂时写入失败，核心任务继续执行；"
                    f"累计失败 {self.lock_heartbeat_failure_total} 次：{type(exc).__name__}: {exc}"
                )
        self.state.update_run(self.run_id)

    @staticmethod
    def read_json_file(path: Path) -> dict[str, Any] | None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def find_completed_incremental_summary(self, stage_row: sqlite3.Row) -> Path | None:
        root = self.project_root / "logs" / "incremental_update"
        if not root.exists():
            return None
        try:
            stage_started = datetime.fromisoformat(str(stage_row["started_at"])).timestamp()
        except (TypeError, ValueError):
            stage_started = 0.0
        exact: list[Path] = []
        fallback: list[Path] = []
        for summary_path in root.rglob("summary.json"):
            payload = self.read_json_file(summary_path)
            if not payload or payload.get("status") != "success" or int(payload.get("exitCode", 1)) != 0:
                continue
            if str(payload.get("projectRoot") or "").casefold() != str(self.project_root).casefold():
                continue
            deploy = payload.get("deploy") or {}
            database = payload.get("database") or {}
            post_update = payload.get("postUpdate") or {}
            if deploy.get("status") != "ready" or deploy.get("missing"):
                continue
            if not database.get("exists") or post_update.get("status") != "completed":
                continue
            parent_run_id = str(payload.get("parentRunId") or "").strip()
            if parent_run_id == self.run_id:
                exact.append(summary_path)
                continue
            try:
                child_started = datetime.fromisoformat(str(payload.get("startedAt") or "")).timestamp()
            except (TypeError, ValueError):
                continue
            if stage_started - 120 <= child_started <= stage_started + 36 * 3600:
                fallback.append(summary_path)
        candidates = exact or fallback
        if not candidates:
            return None
        return max(candidates, key=lambda path: path.stat().st_mtime_ns)

    def reconcile_incremental_stage(self) -> bool:
        stage = "01_incremental_update"
        row = self.state.load_stage(self.run_id, stage)
        if row is None or row["status"] == "completed":
            return False
        summary_path = self.find_completed_incremental_summary(row)
        if summary_path is not None:
            payload = self.read_json_file(summary_path) or {}
            target_date = str(((payload.get("ttfund") or {}).get("target_trade_date") or "")).strip()
            run = self.state.load_run(self.run_id)
            expected_date = str(run["source_target_date"] or "").strip() if run else ""
            if expected_date and target_date and target_date != expected_date:
                raise RuntimeError(
                    f"completed child target date mismatch: expected={expected_date}, child={target_date}"
                )
            self.state.stage_finish(self.run_id, stage, 0)
            self.event(
                "stage_reconciled_from_child_summary",
                stage=stage,
                child_summary_path=str(summary_path),
                child_run_id=payload.get("runId"),
            )
            self.console(f"[恢复] 已根据内层成功摘要恢复增量更新阶段：{summary_path}")
            return True
        log_path = Path(str(row["log_path"] or ""))
        if log_path.exists() and time.time() - log_path.stat().st_mtime < 20 * 60:
            raise RuntimeError(
                "incremental stage is still marked running and its log is active; refuse to start a duplicate collector"
            )
        return False

    def terminate_tree(self, process: subprocess.Popen[Any]) -> None:
        if process.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        else:
            process.kill()
        process.wait(timeout=30)

    def run_command(
        self,
        stage: str,
        command: list[str],
        *,
        timeout_seconds: int = 14 * 3600,
        accepted_returncodes: tuple[int, ...] = (0,),
        workflow_key: str,
        display_name: str | None = None,
    ) -> CommandResult:
        log_path = self.run_dir / f"{stage}.log"
        label = display_name or self.workflow_label(workflow_key)
        self.workflow_started_at.setdefault(workflow_key, time.monotonic())
        if self.state.stage_completed(self.run_id, stage):
            self.event("stage_skipped_completed", stage=stage, log_path=str(log_path))
            self.mark_workflow_completed(workflow_key)
            self.console(f"[跳过] {label}：该阶段在本次 run_id 中已经完成。")
            self.print_progress(workflow_key, label)
            return CommandResult(0, log_path, 0.0)
        self.state.stage_start(self.run_id, stage, log_path)
        self.event("stage_started", stage=stage, command=[Path(command[0]).name, *command[1:]])
        self.console(f"[开始] {label}")
        self.console(f"  阶段日志：{log_path}")
        self.print_progress(workflow_key, label)
        if self.args.dry_run:
            log_path.write_text("DRY RUN\n" + subprocess.list2cmdline(command) + "\n", encoding="utf-8")
            self.state.stage_finish(self.run_id, stage, 0)
            self.mark_workflow_completed(workflow_key)
            self.console(f"[完成] {label}（Dry Run）")
            self.print_progress(workflow_key, label)
            return CommandResult(0, log_path, 0.0)

        started = time.monotonic()
        last_progress_report = started
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        with log_path.open("w", encoding="utf-8", errors="replace") as log_handle:
            child_env = os.environ.copy()
            child_env["PYTHONUNBUFFERED"] = "1"
            child_env["ADVISOR_DAILY_RUN_ID"] = self.run_id
            process = subprocess.Popen(
                command,
                cwd=self.project_root,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
                env=child_env,
            )
            try:
                with log_path.open("r", encoding="utf-8", errors="replace") as tail_handle:
                    signal_buffer = ""
                    last_child_progress = ""

                    def emit_child_signals() -> None:
                        nonlocal signal_buffer, last_child_progress
                        chunk = tail_handle.read()
                        if not chunk:
                            return
                        signal_buffer += chunk
                        lines = signal_buffer.splitlines(keepends=True)
                        signal_buffer = ""
                        if lines and not lines[-1].endswith(("\n", "\r")):
                            signal_buffer = lines.pop()
                        for child_line in lines:
                            text = strip_wrapper_timestamps(child_line)
                            if text and CHILD_SIGNAL_PATTERN.search(text):
                                self.console(f"  [子任务] {text[:1000]}")
                                progress_text = extract_child_progress(text)
                                if progress_text:
                                    last_child_progress = progress_text

                    while process.poll() is None:
                        elapsed = time.monotonic() - started
                        if elapsed > timeout_seconds:
                            self.terminate_tree(process)
                            message = f"stage timeout after {timeout_seconds}s"
                            log_handle.write(f"\n[TIMEOUT] {message}\n")
                            self.state.stage_finish(self.run_id, stage, 124, message)
                            self.console(f"[失败] {label}：{message}")
                            raise RuntimeError(f"{stage}: {message}")
                        emit_child_signals()
                        self.heartbeat()
                        if time.monotonic() - last_progress_report >= 30:
                            self.print_progress(workflow_key, label)
                            if last_child_progress:
                                self.console(f"  最近子任务进度：{last_child_progress[:1000]}")
                            last_progress_report = time.monotonic()
                        time.sleep(5)
                    emit_child_signals()
                    if signal_buffer.strip():
                        text = strip_wrapper_timestamps(signal_buffer)
                        if re.search(r"(?i)(\[DONE\]|\[RESULT\]|\[WARN\]|\[ERROR\]|backup_progress)", text):
                            self.console(f"  [子任务] {text[:1000]}")
                    returncode = int(process.returncode or 0)
            except BaseException:
                if process.poll() is None:
                    self.terminate_tree(process)
                raise
        elapsed = round(time.monotonic() - started, 3)
        if returncode not in accepted_returncodes:
            error = f"stage exited with code {returncode}; see {log_path}"
            self.state.stage_finish(self.run_id, stage, returncode, error)
            self.console(f"[失败] {label}：退出码 {returncode}，详见 {log_path}")
            raise RuntimeError(f"{stage}: {error}")
        self.state.stage_finish(self.run_id, stage, returncode)
        self.event("stage_completed", stage=stage, elapsed_seconds=elapsed, log_path=str(log_path))
        if returncode == 0:
            self.mark_workflow_completed(workflow_key)
            self.console(f"[完成] {label}，耗时 {self.format_elapsed(elapsed)}")
            self.print_progress(workflow_key, label)
        else:
            self.console(f"[等待] {label}：本次检查尚未就绪。")
        return CommandResult(returncode, log_path, elapsed)

    def wait_for_source(self) -> str | None:
        if self.args.skip_readiness:
            self.event("readiness_skipped")
            self.console("[跳过] 数据就绪检查。")
            return None
        if self.args.dry_run:
            self.event("readiness_dry_run")
            self.workflow_started_at.setdefault("readiness", time.monotonic())
            self.mark_workflow_completed("readiness")
            self.console("[完成] 数据就绪检查（Dry Run）")
            self.print_progress("readiness", "数据就绪检查（Dry Run）")
            return None
        max_checks = max(1, self.args.readiness_max_checks)
        for attempt in range(1, max_checks + 1):
            output_path = self.run_dir / f"readiness_{attempt:02d}.json"
            stage = f"00_readiness_{attempt:02d}"
            result = self.run_command(
                stage,
                [
                    sys.executable,
                    "-X",
                    "utf8",
                    str(PROGRAM_ROOT / "check_daily_source_readiness.py"),
                    "--db-path",
                    str(self.project_root / "data" / "analysis_zh_current.sqlite"),
                    "--output-path",
                    str(output_path),
                ],
                timeout_seconds=600,
                accepted_returncodes=(0, 3),
                workflow_key="readiness",
                display_name=f"数据就绪检查（第 {attempt}/{max_checks} 次）",
            )
            payload = json.loads(output_path.read_text(encoding="utf-8-sig"))
            if result.returncode == 0 and payload.get("state") == "ready":
                target = str(payload.get("target_trade_date") or "") or None
                self.state.update_run(self.run_id, status="source_ready", source_target_date=target)
                self.event("source_ready", target_trade_date=target, attempt=attempt)
                self.console(f"[就绪] 数据源最新披露日：{target or '未返回日期'}。")
                return target
            reasons = payload.get("reasons") or [payload.get("error")]
            self.event("source_not_ready", attempt=attempt, reasons=reasons)
            self.console(f"[未就绪] 第 {attempt}/{max_checks} 次检查：{'；'.join(str(item) for item in reasons if item)}")
            if self.args.skip_wait or attempt >= max_checks:
                raise TimeoutError(f"source data was not ready after {attempt} readiness checks")
            next_check = now_local() + timedelta(minutes=max(1, self.args.readiness_interval_minutes))
            self.console(f"下一次检查时间：{next_check.strftime('%Y-%m-%d %H:%M:%S')}。")
            last_wait_progress = time.monotonic()
            while now_local() < next_check:
                time.sleep(min(30, max(0.1, (next_check - now_local()).total_seconds())))
                self.heartbeat()
                if time.monotonic() - last_wait_progress >= 60:
                    self.print_progress("readiness", "数据就绪检查（等待下次检查）")
                    last_wait_progress = time.monotonic()
        raise TimeoutError(f"source data was not ready after {max_checks} readiness checks")

    def find_backup_path(self) -> str | None:
        for metadata_path in sorted(self.args.backup_dir.glob("analysis_zh_current_*.json"), reverse=True):
            try:
                payload = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError):
                continue
            if payload.get("run_id") == self.run_id and payload.get("status") == "success":
                return str(payload.get("backup_path") or "") or None
        return None

    def write_summary(self, status: str, error: str | None = None) -> None:
        run = self.state.load_run(self.run_id)
        stages = [
            dict(row)
            for row in self.state.conn.execute(
                "SELECT * FROM daily_update_stage WHERE run_id=? ORDER BY started_at, stage", (self.run_id,)
            )
        ]
        payload = {
            "version": 1,
            "run_id": self.run_id,
            "status": status,
            "started_at": run["started_at"] if run else None,
            "finished_at": now_local().isoformat(timespec="seconds"),
            "source_target_date": run["source_target_date"] if run else None,
            "backup_path": run["backup_path"] if run else None,
            "error": error,
            "run_dir": str(self.run_dir),
            "events_path": str(self.events_path),
            "console_log_path": str(self.console_log_path),
            "workflow_total": len(self.workflow_steps),
            "workflow_completed": [label for key, label in self.workflow_steps if key in self.workflow_completed],
            "stages": stages,
        }
        atomic_write_json(self.summary_path, payload)
        atomic_write_json(self.project_root / "logs" / "daily_update" / "latest.json", payload)

    def execute(self) -> int:
        self.lock.acquire()
        status = "failed_critical"
        error: str | None = None
        try:
            package_lock = self.project_root / "data" / "migration_package.lock"
            if package_lock.exists():
                try:
                    package_payload = json.loads(package_lock.read_text(encoding="utf-8-sig"))
                except (OSError, json.JSONDecodeError):
                    package_payload = {}
                package_pid = int(package_payload.get("pid") or 0)
                if process_exists(package_pid):
                    raise RuntimeError(f"migration package build is active: pid={package_pid}")
                package_lock.unlink(missing_ok=True)
            self.event("run_started", run_dir=str(self.run_dir), resume=bool(self.args.resume_run_id))
            self.console("=" * 78)
            self.console(f"统一增量更新启动，run_id={self.run_id}")
            self.console(f"控制台进度日志：{self.console_log_path}")
            self.console(f"结构化事件日志：{self.events_path}")
            self.console(f"计划步骤（{len(self.workflow_steps)} 步）：{' -> '.join(label for _, label in self.workflow_steps)}")
            self.console("=" * 78)
            self.wait_for_source()
            if self.args.resume_run_id:
                self.reconcile_incremental_stage()
            incremental_command = [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(self.project_root / "run_incremental_update_with_logs.ps1"),
                "-ProjectRoot",
                str(self.project_root),
                "-ReportRoot",
                str(self.args.report_root),
                "-Unattended",
                "-HistoryMode",
                self.args.history_mode,
            ]
            if self.args.device_id:
                incremental_command.extend(["-DeviceId", self.args.device_id])
            if self.args.incremental_extra_bat_args:
                incremental_command.extend(["-ExtraBatArgs", self.args.incremental_extra_bat_args])
            self.run_command(
                "01_incremental_update",
                incremental_command,
                workflow_key="incremental",
            )

            self.run_command(
                "02_data_audit",
                [
                    sys.executable,
                    "-X",
                    "utf8",
                    str(PROGRAM_ROOT / "run_project_data_audit_hook.py"),
                    "--mode",
                    "manual",
                    "--report-root",
                    str(self.args.report_root),
                    "--audit-only",
                ],
                timeout_seconds=4 * 3600,
                workflow_key="audit",
            )

            if not self.args.skip_backup:
                self.run_command(
                    "03_database_backup",
                    [
                        sys.executable,
                        "-X",
                        "utf8",
                        str(PROGRAM_ROOT / "backup_successful_analysis_db.py"),
                        "--db-path",
                        str(self.project_root / "data" / "analysis_zh_current.sqlite"),
                        "--backup-dir",
                        str(self.args.backup_dir),
                        "--retain",
                        str(self.args.backup_retain),
                        "--run-id",
                        self.run_id,
                        "--state-db",
                        str(self.project_root / "data" / "update_state.sqlite"),
                        "--require-stage",
                        "01_incremental_update",
                        "--require-stage",
                        "02_data_audit",
                    ],
                    timeout_seconds=4 * 3600,
                    workflow_key="backup",
                )
                backup_path = self.find_backup_path()
                if not backup_path and not self.args.dry_run:
                    raise RuntimeError("validated backup completed but its metadata was not found")
                self.state.update_run(self.run_id, backup_path=backup_path)

            if not self.args.skip_publish:
                publish_command = [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(PROGRAM_ROOT / "update_and_publish_minimal_set.ps1"),
                    "-ProjectRoot",
                    str(self.project_root),
                    "-ReportRoot",
                    str(self.args.report_root),
                    "-PagesBaseUrl",
                    self.args.pages_base_url,
                    "-SkipDataUpdate",
                    "-SkipAudit",
                    "-AllowDirtyPublishRepo",
                    "-CommitMessage",
                    f"Daily data update {now_local().strftime('%Y-%m-%d')}",
                ]
                if self.args.publish_root:
                    publish_command.extend(["-PublishRoot", str(self.args.publish_root)])
                try:
                    self.run_command(
                        "04_publish",
                        publish_command,
                        timeout_seconds=3 * 3600,
                        workflow_key="publish",
                    )
                except Exception as exc:
                    error = str(exc)
                    status = "data_success_publish_failed"
                    self.state.update_run(
                        self.run_id,
                        status=status,
                        current_stage="04_publish",
                        finished_at=now_local().isoformat(timespec="seconds"),
                        error=error,
                    )
                    self.write_summary(status, error)
                    self.event("run_finished", status=status, error=error)
                    self.console(f"[部分完成] 数据已更新并备份，但发布失败：{error}")
                    self.console(f"执行摘要：{self.summary_path}")
                    return 2

            status = "success"
            self.state.update_run(
                self.run_id,
                status=status,
                current_stage=None,
                finished_at=now_local().isoformat(timespec="seconds"),
                error=None,
            )
            self.write_summary(status)
            self.event("run_finished", status=status)
            self.console("[全部完成] 本次统一增量更新执行成功。")
            self.console(f"总耗时：{self.format_elapsed(time.monotonic() - self.session_started_monotonic)}")
            self.console(f"执行摘要：{self.summary_path}")
            return 0
        except TimeoutError as exc:
            error = str(exc)
            status = "waiting_source_max_checks"
        except Exception as exc:  # noqa: BLE001 - top-level state must always be persisted.
            error = f"{type(exc).__name__}: {exc}"
            status = "failed_critical"
        finally:
            if status not in {"success", "data_success_publish_failed"}:
                if status == "failed_critical":
                    abnormal_flag = self.project_root / "data" / "runtime_health" / "require_full_quick_check.flag"
                    abnormal_flag.parent.mkdir(parents=True, exist_ok=True)
                    abnormal_flag.write_text(
                        json.dumps(
                            {
                                "run_id": self.run_id,
                                "failed_at": now_local().isoformat(timespec="seconds"),
                                "status": status,
                                "error": error,
                            },
                            ensure_ascii=False,
                            indent=2,
                        )
                        + "\n",
                        encoding="utf-8",
                    )
                self.state.update_run(
                    self.run_id,
                    status=status,
                    finished_at=now_local().isoformat(timespec="seconds"),
                    error=error,
                )
                self.write_summary(status, error)
                self.event("run_finished", status=status, error=error)
                self.console(f"[执行结束] 状态={status}，原因={error}")
                self.console(f"执行摘要：{self.summary_path}")
            self.lock.release()
            self.state.close()
        return 3 if status == "waiting_source_max_checks" else 1


def main() -> None:
    args = parse_args()
    orchestrator = Orchestrator(args)
    raise SystemExit(orchestrator.execute())


if __name__ == "__main__":
    main()
