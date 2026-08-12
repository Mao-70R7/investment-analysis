from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


def now_text() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class StateStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, timeout=60)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA busy_timeout=60000")
        self.connection.executescript(
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
            CREATE TABLE IF NOT EXISTS daily_update_node (
                run_id TEXT NOT NULL,
                node_id TEXT NOT NULL,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                started_at TEXT,
                finished_at TEXT,
                input_fingerprint TEXT,
                output_fingerprint TEXT,
                result_path TEXT,
                log_path TEXT,
                returncode INTEGER,
                error TEXT,
                PRIMARY KEY (run_id, node_id)
            );
            CREATE TABLE IF NOT EXISTS daily_update_node_attempt (
                run_id TEXT NOT NULL,
                node_id TEXT NOT NULL,
                attempt INTEGER NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                input_fingerprint TEXT,
                output_fingerprint TEXT,
                returncode INTEGER,
                device_id TEXT,
                log_path TEXT,
                result_path TEXT,
                error TEXT,
                PRIMARY KEY (run_id, node_id, attempt)
            );
            CREATE TABLE IF NOT EXISTS daily_update_artifact (
                run_id TEXT NOT NULL,
                node_id TEXT NOT NULL,
                artifact_key TEXT NOT NULL,
                artifact_path TEXT,
                artifact_hash TEXT,
                watermark TEXT,
                validation_status TEXT,
                metadata_json TEXT,
                PRIMARY KEY (run_id, node_id, artifact_key)
            );
            """
        )
        attempt_columns = {
            str(row["name"])
            for row in self.connection.execute("PRAGMA table_info(daily_update_node_attempt)").fetchall()
        }
        if "input_fingerprint" not in attempt_columns:
            self.connection.execute(
                "ALTER TABLE daily_update_node_attempt ADD COLUMN input_fingerprint TEXT"
            )
        if "output_fingerprint" not in attempt_columns:
            self.connection.execute(
                "ALTER TABLE daily_update_node_attempt ADD COLUMN output_fingerprint TEXT"
            )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def get_run(self, run_id: str) -> sqlite3.Row | None:
        return self.connection.execute("SELECT * FROM daily_update_run WHERE run_id=?", (run_id,)).fetchone()

    def create_run(self, run_id: str, run_dir: Path, metadata: dict[str, Any]) -> None:
        timestamp = now_text()
        self.connection.execute(
            "INSERT INTO daily_update_run(run_id,status,started_at,heartbeat_at,run_dir,metadata_json) "
            "VALUES(?, 'created', ?, ?, ?, ?)",
            (run_id, timestamp, timestamp, str(run_dir), json.dumps(metadata, ensure_ascii=False)),
        )
        self.connection.commit()

    def update_run(self, run_id: str, **fields: Any) -> None:
        fields["heartbeat_at"] = now_text()
        assignments = ",".join(f"{key}=?" for key in fields)
        self.connection.execute(
            f"UPDATE daily_update_run SET {assignments} WHERE run_id=?",
            (*fields.values(), run_id),
        )
        self.connection.commit()

    def interrupt_stale_runs(
        self,
        current_run_id: str,
        reason: str = "previous run ended before recording a final status (manual stop or process exit)",
    ) -> list[str]:
        rows = self.connection.execute(
            "SELECT run_id FROM daily_update_run "
            "WHERE run_id<>? AND status IN ('created','running') ORDER BY started_at",
            (current_run_id,),
        ).fetchall()
        run_ids = [str(row["run_id"]) for row in rows]
        if not run_ids:
            return []
        timestamp = now_text()
        placeholders = ",".join("?" for _ in run_ids)
        parameters = (timestamp, timestamp, reason, *run_ids)
        self.connection.execute(
            f"UPDATE daily_update_run SET status='interrupted',finished_at=?,heartbeat_at=?,"
            f"error=COALESCE(error,?) WHERE run_id IN ({placeholders})",
            parameters,
        )
        node_parameters = (timestamp, reason, *run_ids)
        self.connection.execute(
            f"UPDATE daily_update_node SET status='interrupted',finished_at=?,returncode=130,"
            f"error=COALESCE(error,?) WHERE status='running' AND run_id IN ({placeholders})",
            node_parameters,
        )
        self.connection.execute(
            f"UPDATE daily_update_node_attempt SET status='interrupted',finished_at=?,returncode=130,"
            f"error=COALESCE(error,?) WHERE status='running' AND run_id IN ({placeholders})",
            node_parameters,
        )
        self.connection.commit()
        return run_ids

    def interrupt_running_nodes(
        self,
        run_id: str,
        reason: str = "node was still marked running when this run was resumed",
    ) -> list[str]:
        rows = self.connection.execute(
            "SELECT node_id FROM daily_update_node "
            "WHERE run_id=? AND status='running' ORDER BY started_at,node_id",
            (run_id,),
        ).fetchall()
        node_ids = [str(row["node_id"]) for row in rows]
        if not node_ids:
            return []
        timestamp = now_text()
        self.connection.execute(
            "UPDATE daily_update_node SET status='interrupted',finished_at=?,returncode=130,"
            "error=COALESCE(error,?) WHERE run_id=? AND status='running'",
            (timestamp, reason, run_id),
        )
        self.connection.execute(
            "UPDATE daily_update_node_attempt SET status='interrupted',finished_at=?,returncode=130,"
            "error=COALESCE(error,?) WHERE run_id=? AND status='running'",
            (timestamp, reason, run_id),
        )
        self.connection.commit()
        return node_ids

    def get_node(self, run_id: str, node_id: str) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM daily_update_node WHERE run_id=? AND node_id=?", (run_id, node_id)
        ).fetchone()

    def get_node_attempts(self, run_id: str, node_id: str) -> list[sqlite3.Row]:
        return self.connection.execute(
            "SELECT * FROM daily_update_node_attempt "
            "WHERE run_id=? AND node_id=? ORDER BY attempt DESC",
            (run_id, node_id),
        ).fetchall()

    def start_node(
        self,
        run_id: str,
        node_id: str,
        attempt: int,
        input_fingerprint: str,
        log_path: Path,
        device_id: str | None,
    ) -> None:
        timestamp = now_text()
        self.connection.execute(
            """
            INSERT INTO daily_update_node(
                run_id,node_id,status,attempts,started_at,input_fingerprint,log_path
            ) VALUES(?,?, 'running',?,?,?,?)
            ON CONFLICT(run_id,node_id) DO UPDATE SET
                status='running',attempts=excluded.attempts,started_at=excluded.started_at,
                finished_at=NULL,input_fingerprint=excluded.input_fingerprint,
                output_fingerprint=NULL,result_path=NULL,log_path=excluded.log_path,
                returncode=NULL,error=NULL
            """,
            (run_id, node_id, attempt, timestamp, input_fingerprint, str(log_path)),
        )
        self.connection.execute(
            "INSERT INTO daily_update_node_attempt("
            "run_id,node_id,attempt,status,started_at,input_fingerprint,device_id,log_path) "
            "VALUES(?,?,?, 'running',?,?,?,?)",
            (run_id, node_id, attempt, timestamp, input_fingerprint, device_id, str(log_path)),
        )
        self.update_run(run_id, status="running", current_stage=node_id)
        self.connection.commit()

    def skip_node(
        self,
        run_id: str,
        node_id: str,
        input_fingerprint: str,
        result_path: Path,
        output_fingerprint: str,
        reason: str,
    ) -> None:
        timestamp = now_text()
        existing = self.get_node(run_id, node_id)
        attempts = int(existing["attempts"] or 0) if existing else 0
        self.connection.execute(
            """
            INSERT INTO daily_update_node(
                run_id,node_id,status,attempts,started_at,finished_at,input_fingerprint,
                output_fingerprint,result_path,returncode,error
            ) VALUES(?,?, 'skipped',?,?,?,?,?,?,0,?)
            ON CONFLICT(run_id,node_id) DO UPDATE SET
                status='skipped',finished_at=excluded.finished_at,
                input_fingerprint=excluded.input_fingerprint,
                output_fingerprint=excluded.output_fingerprint,
                result_path=excluded.result_path,returncode=0,error=excluded.error
            """,
            (
                run_id,
                node_id,
                attempts,
                timestamp,
                timestamp,
                input_fingerprint,
                output_fingerprint,
                str(result_path),
                reason,
            ),
        )
        self.connection.commit()

    def finish_node(
        self,
        run_id: str,
        node_id: str,
        attempt: int,
        status: str,
        returncode: int,
        result_path: Path,
        output_fingerprint: str,
        error: str | None,
    ) -> None:
        timestamp = now_text()
        self.connection.execute(
            "UPDATE daily_update_node SET status=?,finished_at=?,returncode=?,result_path=?,"
            "output_fingerprint=?,error=? WHERE run_id=? AND node_id=?",
            (status, timestamp, returncode, str(result_path), output_fingerprint, error, run_id, node_id),
        )
        self.connection.execute(
            "UPDATE daily_update_node_attempt SET status=?,finished_at=?,returncode=?,result_path=?,"
            "output_fingerprint=?,error=? "
            "WHERE run_id=? AND node_id=? AND attempt=?",
            (
                status,
                timestamp,
                returncode,
                str(result_path),
                output_fingerprint,
                error,
                run_id,
                node_id,
                attempt,
            ),
        )
        self.connection.commit()

    def record_artifacts(self, run_id: str, node_id: str, artifacts: list[dict[str, Any]]) -> None:
        for index, artifact in enumerate(artifacts):
            key = str(artifact.get("key") or f"artifact_{index + 1}")
            self.connection.execute(
                "INSERT OR REPLACE INTO daily_update_artifact(" 
                "run_id,node_id,artifact_key,artifact_path,artifact_hash,watermark,validation_status,metadata_json) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    node_id,
                    key,
                    artifact.get("path"),
                    artifact.get("sha256"),
                    artifact.get("watermark"),
                    artifact.get("validationStatus"),
                    json.dumps(artifact, ensure_ascii=False),
                ),
            )
        self.connection.commit()
