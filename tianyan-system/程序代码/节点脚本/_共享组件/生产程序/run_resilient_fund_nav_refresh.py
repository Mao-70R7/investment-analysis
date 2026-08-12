from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
COLLECTOR = PROJECT_ROOT / "节点脚本" / "_共享组件" / "生产程序" / "backfill_fund_nav_eastmoney_api_incremental.py"
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
SUMMARY_ROOT = PROJECT_ROOT / "data" / "normalized" / "ttfund_fund_nav" / "collection_summary"
RETRY_ROOT = PROJECT_ROOT / "outputs" / "ttfund_fund_nav_retry"
CN_TZ = timezone(timedelta(hours=8))


def now_cn() -> datetime:
    return datetime.now(CN_TZ)


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp_path.replace(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh fund NAV with isolated low-concurrency retries.")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--target-source", default="all-dict", choices=["positioned", "current-dict", "all-dict"])
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--lookback-days", type=int, default=2)
    parser.add_argument("--only-nav-before")
    parser.add_argument("--end-date")
    parser.add_argument("--retry-rounds", type=int, default=2)
    parser.add_argument("--retry-wait-seconds", type=float, default=15.0)
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--no-sleep", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args()


def list_summary_files() -> set[Path]:
    if not SUMMARY_ROOT.exists():
        return set()
    return {path.resolve() for path in SUMMARY_ROOT.rglob("*.json") if path.is_file()}


def select_completed_summary_path(paths: set[Path]) -> Path | None:
    """Prefer the collector's completed summary over its live progress file."""
    completed = [path for path in paths if not path.name.endswith(".progress.json")]
    candidates = completed or list(paths)
    return max(candidates, key=lambda path: path.stat().st_mtime_ns) if candidates else None


def read_json(path: Path | None) -> dict[str, Any] | None:
    if not path or not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def normalize_code(value: Any) -> str | None:
    digits = "".join(ch for ch in str(value or "").strip() if ch.isdigit())
    return digits.zfill(6) if digits else None


def failure_map(summary: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    payload = summary or {}
    rows = payload.get("failures") or payload.get("failures_sample") or []
    result: dict[str, dict[str, Any]] = {}
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            code = normalize_code(row.get("fund_code"))
            if code:
                result[code] = dict(row)
    return result


def successful_codes(summary: dict[str, Any] | None) -> set[str]:
    payload = summary or {}
    result: set[str] = set()

    for key in ("success_fund_codes", "successful_fund_codes"):
        rows = payload.get(key) or []
        if isinstance(rows, list):
            result.update(code for raw in rows if (code := normalize_code(raw)))

    # Older collector summaries sometimes stored a fund-code map here. Current
    # summaries store a date histogram, so only six-digit keys are accepted.
    latest_dates = payload.get("latest_dates_after") or {}
    if isinstance(latest_dates, dict):
        for raw_code in latest_dates:
            raw_digits = "".join(ch for ch in str(raw_code or "").strip() if ch.isdigit())
            if len(raw_digits) == 6 and (code := normalize_code(raw_code)):
                result.add(code)

    meta_path_text = str(payload.get("normalized_meta_path") or "").strip()
    if not meta_path_text:
        return result
    meta_path = Path(meta_path_text)
    if not meta_path.is_file():
        return result
    try:
        with meta_path.open("r", encoding="utf-8-sig") as handle:
            for line in handle:
                text = line.strip()
                if not text:
                    continue
                row = json.loads(text)
                if not isinstance(row, dict):
                    continue
                code = normalize_code(row.get("\u57fa\u91d1\u4ee3\u7801") or row.get("fund_code"))
                if code:
                    result.add(code)
    except (OSError, json.JSONDecodeError):
        return result
    return result


def status_sets(summary: dict[str, Any] | None, expected: set[str]) -> tuple[set[str], set[str], dict[str, dict[str, Any]]]:
    payload = summary or {}
    success = successful_codes(payload) & expected
    empty = {
        code
        for raw_code in (payload.get("empty_fund_codes") or [])
        if (code := normalize_code(raw_code)) and code in expected
    }
    failed = {code: row for code, row in failure_map(payload).items() if code in expected}
    accounted = success | empty | set(failed)
    for code in expected - accounted:
        failed[code] = {"fund_code": code, "error": "collector_no_result"}
    return success, empty, failed


def load_current_holding_codes(db_path: Path) -> tuple[set[str], str | None]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            '''
            WITH latest AS (
                SELECT "\u7edf\u4e00\u7b56\u7565ID" AS strategy_id,
                       MAX("\u6301\u4ed3\u65e5\u671f") AS holding_date
                FROM "\u7b56\u7565\u5f53\u524d\u6301\u4ed3"
                WHERE "\u6e20\u9053ID" IN ('ttfund', 'gffunds')
                GROUP BY "\u7edf\u4e00\u7b56\u7565ID"
            )
            SELECT DISTINCT h."\u57fa\u91d1\u4ee3\u7801"
            FROM "\u7b56\u7565\u5f53\u524d\u6301\u4ed3" h
            JOIN latest l
              ON l.strategy_id = h."\u7edf\u4e00\u7b56\u7565ID"
             AND l.holding_date = h."\u6301\u4ed3\u65e5\u671f"
            WHERE COALESCE(h."\u57fa\u91d1\u6743\u91cd_\u767e\u5206\u6bd4", 0) > 0
            '''
        ).fetchall()
    except sqlite3.Error as exc:
        return set(), f"{type(exc).__name__}: {exc}"
    finally:
        conn.close()
    return {code for row in rows if (code := normalize_code(row[0]))}, None


def load_latest_nav_dates(db_path: Path, codes: set[str]) -> dict[str, str]:
    if not codes:
        return {}
    conn = sqlite3.connect(db_path)
    try:
        placeholders = ",".join("?" for _ in codes)
        rows = conn.execute(
            f'''
            SELECT "\u57fa\u91d1\u4ee3\u7801", MAX("\u4ea4\u6613\u65e5\u671f")
            FROM "\u57fa\u91d1\u65e5\u5ea6\u51c0\u503c"
            WHERE "\u57fa\u91d1\u4ee3\u7801" IN ({placeholders})
            GROUP BY "\u57fa\u91d1\u4ee3\u7801"
            ''',
            tuple(sorted(codes)),
        ).fetchall()
    except sqlite3.Error:
        return {}
    finally:
        conn.close()
    return {
        code: str(latest_date)
        for raw_code, latest_date in rows
        if (code := normalize_code(raw_code)) and latest_date
    }


def run_collector(
    *,
    args: argparse.Namespace,
    label: str,
    run_dir: Path,
    codes: set[str] | None,
    workers: int,
    timeout_sec: int,
    retries: int,
) -> tuple[int, Path | None, dict[str, Any] | None]:
    before = list_summary_files()
    command = [
        sys.executable,
        "-X",
        "utf8",
        str(COLLECTOR),
        "--db-path",
        str(args.db_path.resolve()),
        "--target-source",
        args.target_source,
        "--workers",
        str(max(1, workers)),
        "--timeout-sec",
        str(max(1, timeout_sec)),
        "--retries",
        str(max(0, retries)),
        "--lookback-days",
        str(max(1, args.lookback_days)),
        "--progress-every",
        "25",
    ]
    if args.only_nav_before:
        command.extend(["--only-nav-before", args.only_nav_before])
    if args.end_date:
        command.extend(["--end-date", args.end_date])
    if codes is not None:
        code_path = run_dir / f"{label}_fund_codes.txt"
        code_path.parent.mkdir(parents=True, exist_ok=True)
        code_path.write_text("\n".join(sorted(codes)) + "\n", encoding="utf-8")
        command.extend(["--fund-code-file", str(code_path)])

    print(
        f"[基金净值][{label}] 开始：目标={'全量计划池' if codes is None else len(codes)}，"
        f"并发={workers}，单请求超时={timeout_sec}s，内部重试={retries}",
        flush=True,
    )
    started = time.monotonic()
    completed = subprocess.run(command, cwd=PROJECT_ROOT, check=False)
    created = list_summary_files() - before
    summary_path = select_completed_summary_path(created)
    summary = read_json(summary_path)
    print(
        f"[基金净值][{label}] 完成：exit={completed.returncode}，耗时={time.monotonic() - started:.1f}s，"
        f"summary={summary_path or 'missing'}",
        flush=True,
    )
    return int(completed.returncode), summary_path, summary


def main() -> int:
    args = parse_args()
    started = time.monotonic()
    generated = now_cn()
    run_id = generated.strftime("%Y%m%d_%H%M%S")
    run_dir = (args.run_dir or (RETRY_ROOT / generated.strftime("%Y%m%d") / run_id)).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    current_codes, current_scope_error = load_current_holding_codes(args.db_path.resolve())

    primary_rc, primary_path, primary = run_collector(
        args=args,
        label="首轮",
        run_dir=run_dir,
        codes=None,
        workers=max(1, args.workers),
        timeout_sec=15,
        retries=2,
    )
    primary_targets = int(((primary or {}).get("counters") or {}).get("targets") or 0)
    primary_success = successful_codes(primary)
    primary_empty = {
        code for raw in ((primary or {}).get("empty_fund_codes") or []) if (code := normalize_code(raw))
    }
    primary_failures = failure_map(primary)
    primary_accounted = set(primary_success) | primary_empty | set(primary_failures)
    current_latest_dates = load_latest_nav_dates(args.db_path.resolve(), current_codes)
    required_current_codes = {
        code
        for code in current_codes
        if not args.only_nav_before or (current_latest_dates.get(code) or "") < args.only_nav_before
    }
    current_codes_missing_from_plan = required_current_codes - primary_accounted

    final_success = set(primary_success)
    final_empty = set(primary_empty)
    final_failures = dict(primary_failures)
    for code in current_codes_missing_from_plan:
        final_failures[code] = {"fund_code": code, "error": "current_holding_fund_missing_from_primary_plan"}
    final_latest_dates = dict((primary or {}).get("latest_dates_after") or {})
    candidates = set(primary_failures) | (primary_empty & current_codes) | current_codes_missing_from_plan
    retry_summaries: list[dict[str, Any]] = []

    if primary_rc != 0 or not isinstance(primary, dict):
        candidates |= current_codes

    retry_profiles = [(4, 30, 3), (1, 45, 4)]
    for round_index in range(1, max(0, args.retry_rounds) + 1):
        if not candidates:
            break
        if not args.no_sleep:
            wait_seconds = max(0.0, args.retry_wait_seconds * round_index)
            print(f"[基金净值][重试{round_index}] 等待 {wait_seconds:.0f}s 后重试剩余失败项。", flush=True)
            time.sleep(wait_seconds)
        workers, timeout_sec, retries = retry_profiles[min(round_index - 1, len(retry_profiles) - 1)]
        rc, summary_path, summary = run_collector(
            args=args,
            label=f"重试{round_index}",
            run_dir=run_dir,
            codes=candidates,
            workers=workers,
            timeout_sec=timeout_sec,
            retries=retries,
        )
        success, empty, failed = status_sets(summary, candidates)
        for code in success:
            final_success.add(code)
            final_empty.discard(code)
            final_failures.pop(code, None)
        final_latest_dates.update((summary or {}).get("latest_dates_after") or {})
        for code in empty:
            final_success.discard(code)
            final_empty.add(code)
            final_failures.pop(code, None)
        for code, detail in failed.items():
            final_success.discard(code)
            final_empty.discard(code)
            final_failures[code] = detail
        retry_summaries.append(
            {
                "round": round_index,
                "target_total": len(candidates),
                "success_total": len(success),
                "empty_total": len(empty),
                "failed_total": len(failed),
                "returncode": rc,
                "summary_path": str(summary_path) if summary_path else None,
            }
        )
        candidates = set(failed) | (empty & current_codes)

    critical_scope_available = current_scope_error is None
    critical_failed = sorted(set(final_failures) & current_codes) if critical_scope_available else sorted(final_failures)
    critical_empty = sorted(final_empty & current_codes) if critical_scope_available else []
    gate_status = "failed" if primary_rc != 0 or current_scope_error or critical_failed else "passed"
    all_failure_rows = [final_failures[code] for code in sorted(final_failures)]
    primary_counters = (primary or {}).get("counters") or {}
    summary_paths = [primary_path] + [Path(item["summary_path"]) for item in retry_summaries if item.get("summary_path")]
    daily_rows = sum(
        int((((read_json(path) or {}).get("counters") or {}).get("daily_rows") or 0))
        for path in summary_paths
        if path
    )
    effective_target_total = primary_targets + len(current_codes_missing_from_plan)
    summary = {
        "state": "completed" if gate_status == "passed" else "failed",
        "collector": "resilient_fund_nav_refresh",
        "run_id": f"{run_id}_resilient",
        "generated_at": now_cn().isoformat(timespec="seconds"),
        "db_path": str(args.db_path.resolve()),
        "target_source": args.target_source,
        "only_nav_before": args.only_nav_before,
        "end_date": args.end_date,
        "workers": args.workers,
        "counters": {
            "targets": effective_target_total,
            "success": max(0, effective_target_total - len(final_empty) - len(final_failures)),
            "empty": len(final_empty),
            "failed": len(final_failures),
            "daily_rows": daily_rows,
        },
        "latest_dates_after": final_latest_dates,
        "empty_fund_codes": sorted(final_empty),
        "failures": all_failure_rows,
        "failures_sample": all_failure_rows[:200],
        "primary_returncode": primary_rc,
        "primary_summary_path": str(primary_path) if primary_path else None,
        "primary_failed_total": len(primary_failures),
        "primary_empty_total": len(primary_empty),
        "retry_rounds": retry_summaries,
        "recovered_failed_total": len(set(primary_failures) - set(final_failures) - final_empty),
        "current_holding_fund_total": len(current_codes),
        "current_holding_required_refresh_total": len(required_current_codes),
        "current_holding_missing_from_primary_plan_total": len(current_codes_missing_from_plan),
        "current_holding_missing_from_primary_plan_codes": sorted(current_codes_missing_from_plan),
        "current_holding_scope_error": current_scope_error,
        "critical_current_holding_failed_total": len(critical_failed),
        "critical_current_holding_failed_codes": critical_failed,
        "critical_current_holding_empty_total": len(critical_empty),
        "critical_current_holding_empty_codes": critical_empty,
        "gate_status": gate_status,
        "run_dir": str(run_dir),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "source": "eastmoney_f10_lsjz_api",
        "incremental_from_existing": True,
        "primary_counters": primary_counters,
    }
    aggregate_path = SUMMARY_ROOT / generated.strftime("%Y%m%d") / f"{run_id}_resilient.json"
    atomic_write_json(aggregate_path, summary)
    atomic_write_json(run_dir / "resilient_fund_nav_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    if gate_status != "passed":
        print(
            f"[基金净值] 门禁失败：仍有 {len(critical_failed)} 只当前持仓基金接口采集失败，停止后续发布。",
            flush=True,
        )
        return 2
    if critical_empty:
        print(
            f"[基金净值] 提示：{len(critical_empty)} 只当前持仓基金接口持续空返回，已保留缺口但不伪造净值。",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
