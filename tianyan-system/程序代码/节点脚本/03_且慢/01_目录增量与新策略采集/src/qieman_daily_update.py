from __future__ import annotations

import argparse
import json
import os
import queue
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


PUBLISHED_ENTITIES = (
    "strategy_master",
    "strategy_benchmark",
    "strategy_fund_snapshot",
    "strategy_performance_daily",
    "strategy_rebalance_event",
    "strategy_rebalance_fund_delta",
    "strategy_fund_snapshot_history",
    "signal_strategy_event",
    "signal_fund_instruction",
    "signal_rebalance_projection_event",
    "signal_rebalance_projection_delta",
    "strategy_coverage",
    "strategy_incomplete_requested_data",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect a safe incremental Qieman batch and publish a complete normalized snapshot.")
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument("--code-root", type=Path, required=True)
    parser.add_argument("--normalized-root", type=Path, required=True)
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--node-run-dir", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--daily-run-id", required=True)
    parser.add_argument("--db-path", type=Path, required=True)
    parser.add_argument("--dpapi-input", type=Path, required=True)
    parser.add_argument("--bootstrap-history-run-dir", type=Path)
    parser.add_argument("--bootstrap-public-master", type=Path)
    parser.add_argument("--bootstrap-search-master", type=Path)
    parser.add_argument("--bootstrap-ui-runs-root", type=Path)
    parser.add_argument("--history-concurrency", type=int, default=3)
    parser.add_argument("--incremental-overlap-days", type=int, default=7)
    parser.add_argument("--history-signal-page-size", type=int, default=25)
    parser.add_argument("--history-regular-page-size", type=int, default=100)
    parser.add_argument("--history-request-idle-timeout-seconds", type=int, default=120)
    parser.add_argument("--history-request-total-timeout-seconds", type=int, default=600)
    parser.add_argument("--history-request-attempts", type=int, default=4)
    parser.add_argument("--result-path", type=Path, required=True)
    return parser.parse_args()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def atomic_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + f".{os.getpid()}.tmp")
    shutil.copy2(source, temporary)
    temporary.replace(target)


def atomic_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    temporary.replace(path)


def is_internal_or_test_strategy(row: dict[str, Any]) -> bool:
    extra = row.get("extra") if isinstance(row.get("extra"), dict) else {}
    return bool(extra.get("is_test_or_internal")) or str(row.get("status") or "").strip() == "test_or_internal"


def strategy_master_field_coverage(rows: Iterable[dict[str, Any]]) -> dict[str, int]:
    materialized = list(rows)
    fields = (
        "advisor_name",
        "strategy_type",
        "risk_level",
        "launch_date",
        "suggested_holding_period",
        "minimum_amount",
        "advisory_fee_rate",
        "benchmark",
        "source_url",
    )
    return {
        field: sum(row.get(field) is not None and str(row.get(field)).strip() != "" for row in materialized)
        for field in fields
    }


def merge_benchmark_rows(
    previous_rows: Iterable[dict[str, Any]],
    current_rows: Iterable[dict[str, Any]],
    discovered_ids: set[str],
) -> list[dict[str, Any]]:
    """Retain prior exact official splits when the current response is partial/inexact."""

    previous = {
        str(row.get("source_strategy_id") or "").strip(): row
        for row in previous_rows
        if str(row.get("source_strategy_id") or "").strip() in discovered_ids
    }
    current = {
        str(row.get("source_strategy_id") or "").strip(): row
        for row in current_rows
        if str(row.get("source_strategy_id") or "").strip() in discovered_ids
    }
    merged: list[dict[str, Any]] = []
    for strategy_id in sorted(set(previous) | set(current)):
        old = previous.get(strategy_id)
        new = current.get(strategy_id)
        if new and bool(new.get("is_exact_split")):
            merged.append(new)
        elif old and bool(old.get("is_exact_split")):
            merged.append(old)
        elif new:
            merged.append(new)
        elif old:
            merged.append(old)
    return merged


def latest_entity_file(normalized_root: Path, entity: str, excluded_run_id: str) -> Path | None:
    root = normalized_root / "qieman" / entity
    if not root.is_dir():
        return None
    files = [path for path in root.glob("*/*.jsonl") if path.stem != excluded_run_id]
    return max(files, key=lambda path: (path.stem, path.stat().st_mtime_ns)) if files else None


def latest_partial_stargate_run_dir(node_run_dir: Path, current_run_id: str) -> Path | None:
    """Find reusable raw responses from an earlier attempt of this same daily node run."""

    attempt_dir = node_run_dir.resolve().parent
    node_root = attempt_dir.parent
    candidates: list[Path] = []
    for sibling_attempt in node_root.glob("attempt_*"):
        if not sibling_attempt.is_dir() or sibling_attempt.resolve() == attempt_dir:
            continue
        stargate_root = sibling_attempt / "collector" / "stargate"
        if not stargate_root.is_dir():
            continue
        for candidate in stargate_root.iterdir():
            if (
                candidate.is_dir()
                and candidate.name != current_run_id
                and (candidate / "raw").is_dir()
                and any((candidate / "raw").rglob("*.json"))
            ):
                candidates.append(candidate.resolve())
    return max(candidates, key=lambda path: path.stat().st_mtime_ns) if candidates else None


def latest_partial_history_run_dir(
    raw_root: Path,
    daily_run_id: str,
    current_run_id: str,
) -> Path | None:
    """Select the richest reusable history attempt from this same daily run."""

    history_root = raw_root.resolve() / "qieman" / "signed_history"
    if not history_root.is_dir():
        return None
    prefix = f"{daily_run_id}__qieman_collect__attempt_"
    candidates: list[tuple[int, int, int, Path]] = []
    for candidate in history_root.glob(f"{prefix}*"):
        if (
            not candidate.is_dir()
            or candidate.name == current_run_id
            or not (
                (candidate / "checkpoint.json").is_file()
                or (candidate / "summary.json").is_file()
            )
            or not (candidate / "raw").is_dir()
        ):
            continue
        nav_ids = {path.stem for path in (candidate / "raw" / "nav").glob("*.json")}
        history_ids = {
            path.stem
            for entity in ("regular_adjustments", "signal_adjustments")
            for path in (candidate / "raw" / entity).glob("*.json")
        }
        complete_pair_count = len(nav_ids & history_ids)
        raw_json_count = len(nav_ids) + len(history_ids)
        if raw_json_count:
            candidates.append(
                (
                    complete_pair_count,
                    raw_json_count,
                    candidate.stat().st_mtime_ns,
                    candidate.resolve(),
                )
            )
    return max(candidates, key=lambda item: (item[0], item[1], item[2]))[3] if candidates else None


def existing_qieman_ids(db_path: Path) -> set[str]:
    if not db_path.is_file():
        return set()
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True, timeout=60) as connection:
            return {
                str(row[0]).strip()
                for row in connection.execute(
                    'SELECT "渠道策略ID" FROM "策略信息" WHERE "渠道ID"=?',
                    ("qieman",),
                )
                if str(row[0] or "").strip()
            }
    except sqlite3.Error:
        return set()


def free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        return int(server.getsockname()[1])


def relay_stream(stream: Any, output_queue: queue.Queue[str | None]) -> None:
    try:
        for line in iter(stream.readline, ""):
            output_queue.put(line.rstrip("\r\n"))
    finally:
        output_queue.put(None)


def start_proxy(node_exe: str, script: Path, dpapi_input: Path, env: dict[str, str]) -> tuple[subprocess.Popen[str], int]:
    port = free_local_port()
    proxy_env = dict(env)
    proxy_env.update(
        {
            "QIEMAN_DPAPI_INPUT": str(dpapi_input.resolve()),
            "QIEMAN_AUTH_PORT": str(port),
            "QIEMAN_AUTH_TIMEOUT_MS": str(6 * 60 * 60 * 1000),
        }
    )
    process = subprocess.Popen(
        [node_exe, str(script)],
        cwd=str(script.parent),
        env=proxy_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert process.stdout is not None
    lines: queue.Queue[str | None] = queue.Queue()
    threading.Thread(target=relay_stream, args=(process.stdout, lines), daemon=True).start()
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        if process.poll() is not None and lines.empty():
            raise RuntimeError(f"Qieman API proxy exited before readiness: exit={process.returncode}")
        try:
            line = lines.get(timeout=1)
        except queue.Empty:
            continue
        if line is None:
            continue
        print(f"[QIEMAN_PROXY] {line}", flush=True)
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if payload.get("state") == "dpapi_key_loaded" and int(payload.get("localPort") or 0) == port:
            return process, port
        if payload.get("state") == "error":
            raise RuntimeError(f"Qieman API proxy failed: {payload.get('stage')}: {payload.get('message')}")
    raise RuntimeError("Qieman API proxy readiness timed out")


def close_proxy(process: subprocess.Popen[str], port: int) -> None:
    try:
        message = json.dumps({"action": "close"}).encode("utf-8")
        with socket.create_connection(("127.0.0.1", port), timeout=5) as client:
            client.sendall(message)
            client.shutdown(socket.SHUT_WR)
            client.recv(4096)
        process.wait(timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


def run_command(command: list[str], *, cwd: Path, env: dict[str, str]) -> None:
    display = " ".join(f'"{value}"' if " " in value else value for value in command)
    print(f"[QIEMAN_COMMAND] {display}", flush=True)
    process = subprocess.Popen(
        command,
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert process.stdout is not None
    for line in process.stdout:
        print(line.rstrip("\r\n"), flush=True)
    code = process.wait()
    if code != 0:
        raise RuntimeError(f"Qieman command failed: exit={code}, command={command[0]} {Path(command[1]).name if len(command) > 1 else ''}")


def valid_optional(path: Path | None) -> Path | None:
    return path.resolve() if path is not None and path.is_file() else None


def main() -> None:
    args = parse_args()
    workspace_root = args.workspace_root.resolve()
    code_root = args.code_root.resolve()
    normalized_root = args.normalized_root.resolve()
    raw_root = args.raw_root.resolve()
    node_run_dir = args.node_run_dir.resolve()
    db_path = args.db_path.resolve()
    dpapi_input = args.dpapi_input.resolve()
    run_id = str(args.run_id).strip()
    if not run_id or not dpapi_input.is_file():
        raise SystemExit("Qieman run ID or DPAPI key file is missing")

    probe_root = code_root / "official_apps" / "qieman" / "authenticated_probe"
    production_root = code_root / "official_apps" / "qieman" / "production"
    required = {
        "proxy": probe_root / "qieman_stargate_sms_session.js",
        "catalog": probe_root / "collect_qieman_stargate_proxy.py",
        "benchmark": probe_root / "augment_qieman_stargate_benchmarks.py",
        "history": probe_root / "collect_qieman_signed_history_catalog.js",
        "normalize": probe_root / "normalize_qieman_signed_history_catalog.py",
        "audit": probe_root / "audit_qieman_signed_history_catalog.py",
        "enrich": production_root / "enrich_qieman_strategy_master.py",
    }
    missing = [str(path) for path in required.values() if not path.is_file()]
    if missing:
        raise SystemExit("missing Qieman production dependencies: " + ", ".join(missing))

    child_env = dict(os.environ)
    child_env.update(
        {
            "PYTHONUTF8": "1",
            "QIEMAN_ALLOWED_DAILY_RUN_ID": str(args.daily_run_id).strip(),
            "QIEMAN_ALLOW_DEVICE_LOCK": "1",
        }
    )
    python_exe = child_env.get("ADVISOR_PYTHON_EXE") or sys.executable
    node_exe = child_env.get("ADVISOR_NODE_EXE") or "node"
    previous_master = latest_entity_file(normalized_root, "strategy_master", run_id)
    previous_benchmark = latest_entity_file(normalized_root, "strategy_benchmark", run_id)
    resume_stargate_run_dir = latest_partial_stargate_run_dir(node_run_dir, run_id)
    resume_benchmark = (
        resume_stargate_run_dir / "normalized" / "strategy_benchmark.jsonl"
        if resume_stargate_run_dir
        else None
    )
    benchmark_baseline = resume_benchmark if resume_benchmark and resume_benchmark.is_file() else previous_benchmark
    resume_history_run_dir = latest_partial_history_run_dir(
        raw_root,
        str(args.daily_run_id).strip(),
        run_id,
    )
    baseline_run_dir: Path | None = None
    accepted_state = raw_root / "qieman" / "accepted_state.json"
    if accepted_state.is_file():
        candidate = Path(str(read_json(accepted_state).get("history_run_dir") or ""))
        if candidate.is_dir():
            baseline_run_dir = candidate.resolve()
    if baseline_run_dir is None and args.bootstrap_history_run_dir and args.bootstrap_history_run_dir.is_dir():
        baseline_run_dir = args.bootstrap_history_run_dir.resolve()

    proxy: subprocess.Popen[str] | None = None
    proxy_port = 0
    try:
        proxy, proxy_port = start_proxy(node_exe, required["proxy"], dpapi_input, child_env)
        stargate_root = node_run_dir / "stargate"
        catalog_command = [
            python_exe,
            "-X",
            "utf8",
            str(required["catalog"]),
            "--proxy-port",
            str(proxy_port),
            "--output-root",
            str(stargate_root),
            "--run-id",
            run_id,
        ]
        if resume_stargate_run_dir:
            catalog_command.extend(["--resume-run-dir", str(resume_stargate_run_dir)])
            print(f"[QIEMAN_RESUME] reusing validated raw responses from {resume_stargate_run_dir}", flush=True)
        run_command(catalog_command, cwd=probe_root, env=child_env)
        metadata_dir = stargate_root / run_id
        benchmark_command = [
            python_exe,
            "-X",
            "utf8",
            str(required["benchmark"]),
            "--run-dir",
            str(metadata_dir),
            "--proxy-port",
            str(proxy_port),
            "--workers",
            "12",
        ]
        if benchmark_baseline:
            benchmark_command.extend(["--baseline-benchmark", str(benchmark_baseline)])
        run_command(benchmark_command, cwd=probe_root, env=child_env)

        base_master_path = metadata_dir / "normalized" / "strategy_master.jsonl"
        benchmark_path = metadata_dir / "normalized" / "strategy_benchmark.jsonl"
        discovered_base_rows = list(iter_jsonl(base_master_path))
        discovered_ids = {
            str(row.get("source_strategy_id") or "").strip()
            for row in discovered_base_rows
            if str(row.get("source_strategy_id") or "").strip()
        }
        merged_benchmarks = merge_benchmark_rows(
            iter_jsonl(previous_benchmark) if previous_benchmark else (),
            iter_jsonl(benchmark_path),
            discovered_ids,
        )
        atomic_jsonl(benchmark_path, merged_benchmarks)

        enriched_master = metadata_dir / "normalized" / "strategy_master_enriched.jsonl"
        enrichment_report = metadata_dir / "strategy_master_enrichment_report.json"
        enrich_command = [
            python_exe,
            "-X",
            "utf8",
            str(required["enrich"]),
            "--base-master",
            str(metadata_dir / "normalized" / "strategy_master.jsonl"),
            "--benchmark",
            str(metadata_dir / "normalized" / "strategy_benchmark.jsonl"),
            "--output",
            str(enriched_master),
            "--report",
            str(enrichment_report),
            "--run-id",
            run_id,
        ]
        if previous_master:
            enrich_command.extend(["--previous-master", str(previous_master)])
        for flag, path in (
            ("--public-master", valid_optional(args.bootstrap_public_master)),
            ("--search-master", valid_optional(args.bootstrap_search_master)),
        ):
            if path:
                enrich_command.extend([flag, str(path)])
        if args.bootstrap_ui_runs_root and args.bootstrap_ui_runs_root.is_dir():
            enrich_command.extend(["--ui-runs-root", str(args.bootstrap_ui_runs_root.resolve())])
        run_command(enrich_command, cwd=production_root, env=child_env)
        discovered_master_rows = list(iter_jsonl(enriched_master))
        excluded_internal_rows = [row for row in discovered_master_rows if is_internal_or_test_strategy(row)]
        eligible_master_rows = [row for row in discovered_master_rows if not is_internal_or_test_strategy(row)]
        if not eligible_master_rows:
            raise RuntimeError("Qieman catalog contains no eligible non-test strategies")
        atomic_copy(enriched_master, metadata_dir / "normalized" / "strategy_master_discovered.jsonl")
        atomic_jsonl(
            metadata_dir / "normalized" / "strategy_master_excluded_internal.jsonl",
            excluded_internal_rows,
        )
        atomic_jsonl(metadata_dir / "normalized" / "strategy_master.jsonl", eligible_master_rows)

        history_root = raw_root / "qieman" / "signed_history"
        history_run_dir = history_root / run_id
        history_command = [
            node_exe,
            str(required["history"]),
            "--catalog-path",
            str(metadata_dir / "normalized" / "strategy_master.jsonl"),
            "--output-root",
            str(history_root),
            "--lock-dir",
            str(workspace_root / "运行状态" / "locks"),
            "--concurrency",
            str(max(1, min(8, args.history_concurrency))),
            "--incremental-overlap-days",
            str(max(0, min(60, args.incremental_overlap_days))),
            "--signal-page-size",
            str(max(10, min(100, args.history_signal_page_size))),
            "--regular-page-size",
            str(max(10, min(100, args.history_regular_page_size))),
            "--request-idle-timeout-ms",
            str(max(30, min(900, args.history_request_idle_timeout_seconds)) * 1000),
            "--request-total-timeout-ms",
            str(
                max(
                    max(30, min(900, args.history_request_idle_timeout_seconds)),
                    min(1800, args.history_request_total_timeout_seconds),
                )
                * 1000
            ),
            "--request-attempts",
            str(max(1, min(8, args.history_request_attempts))),
            "--run-id",
            run_id,
        ]
        if baseline_run_dir:
            history_command.extend(["--baseline-run-dir", str(baseline_run_dir)])
        if resume_history_run_dir:
            history_command.extend(["--fallback-run-dir", str(resume_history_run_dir)])
            print(
                f"[QIEMAN_HISTORY_RESUME] reusing completed raw history files from {resume_history_run_dir}",
                flush=True,
            )
        run_command(history_command, cwd=probe_root, env=child_env)
        history_summary_path = history_run_dir / "summary.json"
        if not history_summary_path.is_file():
            raise RuntimeError(
                "Qieman signed-history collector exited without summary.json; "
                f"partial checkpoint retained at {history_run_dir / 'checkpoint.json'}"
            )
        history_precheck = read_json(history_summary_path)
        if (
            history_precheck.get("state") != "signed_history_catalog_complete"
            or int(history_precheck.get("resultStrategyCount") or 0) != len(eligible_master_rows)
        ):
            raise RuntimeError(
                "Qieman signed-history collector did not close the catalog: "
                f"state={history_precheck.get('state')}, "
                f"results={history_precheck.get('resultStrategyCount')}, "
                f"expected={len(eligible_master_rows)}"
            )
        run_command(
            [
                python_exe,
                "-X",
                "utf8",
                str(required["normalize"]),
                "--run-dir",
                str(history_run_dir),
                "--metadata-dir",
                str(metadata_dir / "normalized"),
                "--benchmark-path",
                str(metadata_dir / "normalized" / "strategy_benchmark.jsonl"),
                "--db-path",
                str(db_path),
            ],
            cwd=probe_root,
            env=child_env,
        )
        run_command(
            [python_exe, "-X", "utf8", str(required["audit"]), "--run-dir", str(history_run_dir)],
            cwd=probe_root,
            env=child_env,
        )
    finally:
        if proxy is not None:
            close_proxy(proxy, proxy_port)

    history_run_dir = raw_root / "qieman" / "signed_history" / run_id
    metadata_dir = node_run_dir / "stargate" / run_id
    audit = read_json(history_run_dir / "qieman_data_audit_report.json")
    quality = read_json(history_run_dir / "normalized_quality_report.json")
    stargate = read_json(metadata_dir / "summary.json")
    history = read_json(history_run_dir / "summary.json")
    enrichment = read_json(metadata_dir / "strategy_master_enrichment_report.json")
    if int(audit.get("error_count") or 0) != 0 or audit.get("status") not in {"passed", "warn"}:
        raise RuntimeError(f"Qieman isolated audit failed: status={audit.get('status')}, errors={audit.get('error_count')}")

    masters = list(iter_jsonl(history_run_dir / "normalized" / "strategy_master.jsonl"))
    catalog_ids = sorted({str(row.get("source_strategy_id") or "").strip() for row in masters if row.get("source_strategy_id")})
    previous_ids = (
        {str(row.get("source_strategy_id") or "").strip() for row in iter_jsonl(previous_master) if row.get("source_strategy_id")}
        if previous_master
        else existing_qieman_ids(db_path)
    )
    new_ids = sorted(set(catalog_ids) - previous_ids)
    complete_history_ids = {
        str(row.get("strategyCode") or "").strip()
        for row in history.get("results") or []
        if row.get("complete") and str(row.get("strategyCode") or "").strip()
    }
    missing_new_ids = sorted(set(new_ids) - complete_history_ids)
    query_summaries = stargate.get("query_summaries") if isinstance(stargate.get("query_summaries"), list) else []
    discovery_complete = bool(query_summaries) and all(
        item.get("pages")
        and item["pages"][-1].get("has_more") is False
        for item in query_summaries
        if isinstance(item, dict)
    )
    batch_closed = (
        history.get("state") == "signed_history_catalog_complete"
        and int(history.get("completeStrategyCount") or 0) == len(catalog_ids)
        and not missing_new_ids
        and int(quality.get("catalog_strategy_count") or 0) == len(catalog_ids)
    )
    if not discovery_complete or not batch_closed:
        raise RuntimeError(
            "Qieman catalog batch did not close: "
            f"discovery_complete={discovery_complete}, batch_closed={batch_closed}, missing_new={missing_new_ids}"
        )

    published: dict[str, str] = {}
    for entity in PUBLISHED_ENTITIES:
        source = history_run_dir / "normalized" / f"{entity}.jsonl"
        if not source.is_file():
            raise RuntimeError(f"Qieman normalized entity is missing: {entity}")
        target = normalized_root / "qieman" / entity / run_id / f"{run_id}.jsonl"
        atomic_copy(source, target)
        published[entity] = str(target)

    summary = {
        "state": "qieman_daily_incremental_collected",
        "channel_id": "qieman",
        "run_id": run_id,
        "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "catalog_complete": False,
        "catalog_boundary": stargate.get("catalog_boundary"),
        "catalog_discovery_complete": discovery_complete,
        "catalog_batch_closed": batch_closed,
        "strategy_total": len(catalog_ids),
        "catalog_strategy_total": len(catalog_ids),
        "catalog_strategy_ids": catalog_ids,
        "catalog_discovered_strategy_total": len(discovered_master_rows),
        "catalog_excluded_internal_total": len(excluded_internal_rows),
        "catalog_excluded_internal_ids": sorted(
            str(row.get("source_strategy_id") or "").strip()
            for row in excluded_internal_rows
            if str(row.get("source_strategy_id") or "").strip()
        ),
        "catalog_new_strategy_total": len(new_ids),
        "catalog_new_strategy_ids": new_ids,
        "catalog_new_strategy_collected_total": len(set(new_ids) & complete_history_ids),
        "catalog_new_strategy_collected_ids": sorted(set(new_ids) & complete_history_ids),
        "catalog_batch_missing_strategy_total": len(missing_new_ids),
        "catalog_batch_missing_strategy_ids": missing_new_ids,
        "incremental_strategy_total": int(history.get("incrementalStrategyCount") or 0),
        "bootstrap_strategy_total": int(history.get("bootstrapStrategyCount") or 0),
        "downloaded_nav_rows": int(history.get("downloadedNavRows") or 0),
        "downloaded_history_rows": int(history.get("downloadedHistoryRows") or 0),
        "source_latest_nav_date": history.get("sourceLatestNavDate"),
        "non_empty_nav_strategy_total": int(history.get("nonEmptyNavStrategyCount") or 0),
        "latest_nav_date_strategy_total": int(
            history.get("navAtSourceLatestDateStrategyCount") or 0
        ),
        "latest_nav_date_strategy_ratio": float(
            history.get("navAtSourceLatestDateRatio") or 0
        ),
        "retained_history_strategy_total": int(
            history.get("retainedHistoryStrategyCount") or 0
        ),
        "retained_history_strategy_ids": sorted(
            str(value)
            for value in history.get("retainedHistoryStrategyIds") or []
            if str(value)
        ),
        "counts": quality.get("counts") or {},
        "coverage": quality.get("coverage") or {},
        "master_field_coverage": strategy_master_field_coverage(masters),
        "audit_status": audit.get("status"),
        "audit_error_count": int(audit.get("error_count") or 0),
        "audit_warning_count": int(audit.get("warning_count") or 0),
        "history_run_dir": str(history_run_dir),
        "baseline_history_run_dir": str(baseline_run_dir) if baseline_run_dir else None,
        "normalized_paths": published,
        "quality_report_path": str(history_run_dir / "normalized_quality_report.json"),
        "audit_report_path": str(history_run_dir / "qieman_data_audit_report.json"),
        "enrichment_report_path": str(metadata_dir / "strategy_master_enrichment_report.json"),
    }
    summary_path = normalized_root / "qieman" / "collection_summary" / run_id / f"{run_id}.json"
    atomic_json(summary_path, summary)
    result = {
        **summary,
        "summary_path": str(summary_path),
        "accepted_state_candidate": {
            "run_id": run_id,
            "history_run_dir": str(history_run_dir),
            "summary_path": str(summary_path),
        },
    }
    atomic_json(args.result_path.resolve(), result)
    print(json.dumps({key: value for key, value in result.items() if key not in {"catalog_strategy_ids", "normalized_paths"}}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
