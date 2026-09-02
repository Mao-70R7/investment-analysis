from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DB_PATH = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
CHILD_SCRIPT = PROJECT_ROOT / "节点脚本" / "_共享组件" / "生产程序" / "backfill_fund_nav_eastmoney_api_incremental.py"
CN_TZ = timezone(timedelta(hours=8))

T_FUND_SUMMARY = "\u57fa\u91d1\u51c0\u503c\u6982\u51b5"
C_FUND_CODE = "\u57fa\u91d1\u4ee3\u7801"
C_END_DATE = "\u5386\u53f2\u7ed3\u675f\u65e5\u671f"


def q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Eastmoney API fund NAV incremental updates in short batches.")
    parser.add_argument("--db-path", type=Path, default=DB_PATH)
    parser.add_argument("--only-nav-before", default="2026-07-16")
    parser.add_argument("--end-date", default=datetime.now(CN_TZ).date().isoformat())
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--max-batches", type=int, default=200)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--timeout-sec", type=int, default=8)
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--child-timeout-sec", type=int, default=600)
    parser.add_argument("--sleep-between-sec", type=float, default=2.0)
    parser.add_argument("--skip-file", type=Path, default=PROJECT_ROOT / ".tmp" / "fund_nav_api_empty_skip.txt")
    return parser.parse_args()


def load_skip_codes(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {line.strip().zfill(6) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()}


def append_skip_codes(path: Path, codes: list[str]) -> None:
    if not codes:
        return
    existing = load_skip_codes(path)
    new_codes = [code for code in sorted(set(codes)) if code not in existing]
    if not new_codes:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for code in new_codes:
            handle.write(code + "\n")


def stale_counts(conn: sqlite3.Connection, cutoff: str, skip_codes: set[str]) -> dict[str, int]:
    total = conn.execute(f"SELECT COUNT(*) FROM {q(T_FUND_SUMMARY)}").fetchone()[0]
    stale = conn.execute(
        f"""
        SELECT COUNT(*)
        FROM {q(T_FUND_SUMMARY)}
        WHERE {q(C_END_DATE)} IS NULL OR {q(C_END_DATE)} < ?
        """,
        [cutoff],
    ).fetchone()[0]
    if skip_codes:
        placeholders = ",".join("?" for _ in skip_codes)
        eligible = conn.execute(
            f"""
            SELECT COUNT(*)
            FROM {q(T_FUND_SUMMARY)}
            WHERE ({q(C_END_DATE)} IS NULL OR {q(C_END_DATE)} < ?)
              AND {q(C_FUND_CODE)} NOT IN ({placeholders})
            """,
            [cutoff, *sorted(skip_codes)],
        ).fetchone()[0]
    else:
        eligible = stale
    latest = conn.execute(f"SELECT MAX({q(C_END_DATE)}) FROM {q(T_FUND_SUMMARY)}").fetchone()[0]
    current = conn.execute(
        f"SELECT COUNT(*) FROM {q(T_FUND_SUMMARY)} WHERE {q(C_END_DATE)} >= ?",
        [cutoff],
    ).fetchone()[0]
    return {"total": int(total), "stale": int(stale), "eligible": int(eligible), "current": int(current), "max_date": latest}


def write_next_batch_codes(
    conn: sqlite3.Connection,
    *,
    cutoff: str,
    skip_codes: set[str],
    batch_size: int,
    code_path: Path,
) -> int:
    params: list[Any] = [cutoff]
    skip_sql = ""
    if skip_codes:
        placeholders = ",".join("?" for _ in skip_codes)
        skip_sql = f" AND {q(C_FUND_CODE)} NOT IN ({placeholders})"
        params.extend(sorted(skip_codes))
    params.append(batch_size)
    rows = conn.execute(
        f"""
        SELECT {q(C_FUND_CODE)}
        FROM {q(T_FUND_SUMMARY)}
        WHERE ({q(C_END_DATE)} IS NULL OR {q(C_END_DATE)} < ?)
        {skip_sql}
        ORDER BY COALESCE({q(C_END_DATE)}, '0000-00-00') DESC, {q(C_FUND_CODE)}
        LIMIT ?
        """,
        params,
    ).fetchall()
    code_path.parent.mkdir(parents=True, exist_ok=True)
    code_path.write_text("\n".join(str(row[0]).zfill(6) for row in rows) + ("\n" if rows else ""), encoding="utf-8")
    return len(rows)


def stale_codes_for_batch(conn: sqlite3.Connection, cutoff: str, code_path: Path) -> list[str]:
    codes = [line.strip().zfill(6) for line in code_path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    if not codes:
        return []
    stale_codes: list[str] = []
    for index in range(0, len(codes), 500):
        chunk = codes[index : index + 500]
        placeholders = ",".join("?" for _ in chunk)
        rows = conn.execute(
            f"""
            SELECT {q(C_FUND_CODE)}
            FROM {q(T_FUND_SUMMARY)}
            WHERE {q(C_FUND_CODE)} IN ({placeholders})
              AND ({q(C_END_DATE)} IS NULL OR {q(C_END_DATE)} < ?)
            """,
            [*chunk, cutoff],
        ).fetchall()
        stale_codes.extend(str(row[0]).zfill(6) for row in rows)
    return sorted(set(stale_codes))


def latest_child_summary(since_ts: float) -> Path | None:
    root = PROJECT_ROOT / "data" / "normalized" / "ttfund_fund_nav" / "collection_summary"
    if not root.exists():
        return None
    candidates = [
        path
        for path in root.glob("*/*.json")
        if path.stat().st_mtime >= since_ts - 2 and "T" in path.stem
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def main() -> None:
    args = parse_args()
    run_id = datetime.now(CN_TZ).strftime("%Y%m%dT%H%M%S%z")
    batch_root = PROJECT_ROOT / ".tmp" / "fund_nav_api_batches" / run_id
    log_root = PROJECT_ROOT / "logs" / "fund_nav_api_incremental_batches" / datetime.now(CN_TZ).date().isoformat() / run_id
    log_root.mkdir(parents=True, exist_ok=True)
    summary_jsonl = log_root / "summary.jsonl"

    conn = sqlite3.connect(args.db_path)
    try:
        for batch_no in range(1, args.max_batches + 1):
            skip_codes = load_skip_codes(args.skip_file)
            before = stale_counts(conn, args.only_nav_before, skip_codes)
            if before["eligible"] <= 0:
                break
            code_path = batch_root / f"batch_{batch_no:04d}.txt"
            code_count = write_next_batch_codes(
                conn,
                cutoff=args.only_nav_before,
                skip_codes=skip_codes,
                batch_size=max(1, args.batch_size),
                code_path=code_path,
            )
            if code_count <= 0:
                break

            child_log = log_root / f"batch_{batch_no:04d}.log"
            cmd = [
                sys.executable,
                "-X",
                "utf8",
                str(CHILD_SCRIPT),
                "--fund-code-file",
                str(code_path),
                "--only-nav-before",
                args.only_nav_before,
                "--end-date",
                args.end_date,
                "--workers",
                str(args.workers),
                "--timeout-sec",
                str(args.timeout_sec),
                "--retries",
                str(args.retries),
                "--progress-every",
                str(max(1, args.batch_size)),
                "--commit-every",
                "100",
                "--no-raw-files",
            ]
            started = time.time()
            timeout = False
            returncode: int | None = None
            with child_log.open("w", encoding="utf-8") as handle:
                handle.write(json.dumps({"batch": batch_no, "before": before, "code_count": code_count}, ensure_ascii=False) + "\n")
                handle.flush()
                try:
                    completed = subprocess.run(
                        cmd,
                        cwd=PROJECT_ROOT,
                        stdout=handle,
                        stderr=subprocess.STDOUT,
                        timeout=max(60, args.child_timeout_sec),
                        check=False,
                    )
                    returncode = completed.returncode
                except subprocess.TimeoutExpired:
                    timeout = True
                    returncode = 124
                    handle.write(f"\n[TIMEOUT] child exceeded {args.child_timeout_sec}s\n")

            child_summary_path = latest_child_summary(started)
            child_summary: dict[str, Any] = {}
            if child_summary_path and child_summary_path.exists():
                try:
                    child_summary = json.loads(child_summary_path.read_text(encoding="utf-8"))
                    append_skip_codes(args.skip_file, child_summary.get("empty_fund_codes") or [])
                except Exception as error:  # noqa: BLE001
                    child_summary = {"summary_parse_error": str(error), "path": str(child_summary_path)}

            checked_but_stale = stale_codes_for_batch(conn, args.only_nav_before, code_path) if returncode == 0 else []
            append_skip_codes(args.skip_file, checked_but_stale)
            after = stale_counts(conn, args.only_nav_before, load_skip_codes(args.skip_file))
            record = {
                "batch": batch_no,
                "code_count": code_count,
                "returncode": returncode,
                "timeout": timeout,
                "before": before,
                "after": after,
                "child_summary_path": str(child_summary_path) if child_summary_path else None,
                "child_counters": child_summary.get("counters"),
                "empty_added": len(child_summary.get("empty_fund_codes") or []),
                "checked_but_stale_added": len(checked_but_stale),
                "elapsed_seconds": round(time.time() - started, 3),
            }
            with summary_jsonl.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            print(json.dumps(record, ensure_ascii=False), flush=True)

            if returncode != 0:
                break
            if after["eligible"] >= before["eligible"] and not child_summary.get("empty_fund_codes") and not checked_but_stale:
                break
            if args.sleep_between_sec > 0:
                time.sleep(args.sleep_between_sec)
    finally:
        conn.close()

    print(json.dumps({"summary_jsonl": str(summary_jsonl), "skip_file": str(args.skip_file)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
