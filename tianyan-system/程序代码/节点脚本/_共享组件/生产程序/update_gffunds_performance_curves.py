from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def resolve_project_root() -> Path:
    configured = os.environ.get("ADVISOR_CODE_ROOT")
    if configured:
        return Path(configured).resolve()
    return next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())


PROJECT_ROOT = resolve_project_root()
sys.path.insert(0, str(PROJECT_ROOT / "节点脚本" / "_共享组件" / "python_src"))

from advisor_monitor.collectors.gffunds_public import CHANNEL_ID  # noqa: E402
from advisor_monitor.gffunds_public_jobs import (  # noqa: E402
    find_latest_discovered_strategy_file,
    load_strategy_ids as load_discovered_strategy_ids,
    post_public_json,
)
from advisor_monitor.collectors.official_apps_public import load_gffunds_strategy_ids_from_analysis_db  # noqa: E402
from advisor_monitor.progress import ConsoleProgress  # noqa: E402


def now_local() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update GFFunds official performance curves only.")
    parser.add_argument("--strategy-id", action="append", default=[], help="GFJJ/ZY strategy id. Repeatable.")
    parser.add_argument("--limit", type=int, default=None, help="Limit strategy count for test runs.")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--retry-failed-rounds", type=int, default=2)
    parser.add_argument("--retry-workers", type=int, default=2)
    parser.add_argument("--retry-backoff-seconds", type=float, default=5.0)
    parser.add_argument("--min-usable-ratio", type=float, default=0.95)
    parser.add_argument("--acceptable-business-lag-days", type=int, default=1)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--result-summary-path", type=Path, default=None)
    return parser.parse_args()


def load_strategy_ids(args: argparse.Namespace) -> list[str]:
    if args.strategy_id:
        ids = [str(item).strip().upper() for item in args.strategy_id if str(item).strip()]
    else:
        ids = load_gffunds_strategy_ids_from_analysis_db(PROJECT_ROOT)
        discovered_path = find_latest_discovered_strategy_file(PROJECT_ROOT)
        if discovered_path:
            ids.extend(load_discovered_strategy_ids(discovered_path))
    accepted = []
    for item in ids:
        normalized = str(item).strip().upper()
        if not re.fullmatch(r"(?:GFJJ\d{6}|ZY\d{8})", normalized):
            continue
        if normalized not in accepted:
            accepted.append(normalized)
    ids = accepted
    if args.limit and args.limit > 0:
        ids = ids[: args.limit]
    return ids


def snapshot_id(strategy_id: str, payload: dict[str, Any]) -> str:
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return f"gffunds-yield_since_inception-{strategy_id}-{digest[:16]}"


def collect_one(strategy_id: str, timeout: int) -> tuple[str, dict[str, Any] | None, str | None]:
    payload = post_public_json(
        "get_investadvisor_yield_trend",
        {
            "session_id": "",
            "adv_id": strategy_id,
            "section_type": "7",
            "from_page": "StrategyDetail",
        },
        timeout=timeout,
    )
    if not payload:
        return strategy_id, None, "empty_response"
    if payload.get("RETCODE") != "0000":
        return strategy_id, payload, f"retcode={payload.get('RETCODE')}"
    if not payload.get("adv_yield_trend_list"):
        return strategy_id, payload, "empty_curve"
    return strategy_id, payload, None


def normalized_rows(strategy_id: str, payload: dict[str, Any], run_id: str) -> list[dict[str, Any]]:
    sid = snapshot_id(strategy_id, payload)
    rows: list[dict[str, Any]] = []
    for row in payload.get("adv_yield_trend_list") or []:
        trade_date = str(row.get("yield_date") or "").strip()
        if not trade_date:
            continue
        rows.append(
            {
                "channel_id": CHANNEL_ID,
                "source_strategy_id": strategy_id,
                "trade_date": trade_date,
                "nav": None,
                "daily_return": None,
                "cumulative_return": row.get("yield_rate"),
                "benchmark_return": row.get("base_yield_rate"),
                "index_return": row.get("hs300_rate"),
                "max_drawdown": payload.get("max_drawdown"),
                "source_snapshot_id": sid,
                "run_id": run_id,
                "section_type": "7",
                "section_name": "成立以来",
            }
        )
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def payload_latest_date(payload: dict[str, Any]) -> str:
    dates = [
        str(row.get("yield_date") or "").strip()
        for row in payload.get("adv_yield_trend_list") or []
        if str(row.get("yield_date") or "").strip()
    ]
    return max(dates, default="")


def business_day_lag(older: str, newer: str) -> int | None:
    try:
        start = date.fromisoformat(older)
        end = date.fromisoformat(newer)
    except (TypeError, ValueError):
        return None
    if start >= end:
        return 0
    cursor = start
    result = 0
    while cursor < end:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:
            result += 1
    return result


def candidate_raw_roots() -> list[Path]:
    roots: list[Path] = []
    configured = os.environ.get("ADVISOR_RAW_ROOT")
    if configured:
        roots.append(Path(configured).resolve())
    roots.append((PROJECT_ROOT / "data" / "raw").resolve())
    unique: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root).casefold()
        if key not in seen:
            seen.add(key)
            unique.append(root)
    return unique


def find_last_good_payload(strategy_id: str, roots: list[Path]) -> tuple[dict[str, Any] | None, Path | None]:
    candidates: list[Path] = []
    relative = Path(CHANNEL_ID) / "performance_curve"
    for root in roots:
        base = root / relative
        if base.is_dir():
            candidates.extend(
                base.glob(
                    f"*/*/{strategy_id}/get_investadvisor_yield_trend_since_inception.json"
                )
            )
    candidates.sort(key=lambda path: (path.stat().st_mtime_ns, str(path)), reverse=True)
    for path in candidates:
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        if (
            isinstance(payload, dict)
            and payload.get("RETCODE") == "0000"
            and payload.get("adv_yield_trend_list")
        ):
            return payload, path
    return None, None


def load_stopped_strategy_ids() -> set[str]:
    database_path = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
    if not database_path.is_file():
        return set()
    try:
        connection = sqlite3.connect(f"file:{database_path.as_posix()}?mode=ro", uri=True, timeout=30)
        rows = connection.execute(
            'SELECT "渠道策略ID" FROM "策略治理标签" '
            'WHERE "渠道ID"=? AND COALESCE("是否已停止",0)=1',
            (CHANNEL_ID,),
        ).fetchall()
        connection.close()
    except sqlite3.Error:
        return set()
    return {str(row[0]).strip() for row in rows if str(row[0] or "").strip()}


def collect_round(
    strategy_ids: list[str],
    *,
    workers: int,
    timeout: int,
    label: str,
) -> dict[str, tuple[dict[str, Any] | None, str | None]]:
    outcomes: dict[str, tuple[dict[str, Any] | None, str | None]] = {}
    progress = ConsoleProgress(label, len(strategy_ids))
    progress.emit(0, success=0, failed=0, extra=f"并发数 {max(1, workers)}")
    failure_total = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {executor.submit(collect_one, strategy_id, timeout): strategy_id for strategy_id in strategy_ids}
        for index, future in enumerate(as_completed(futures), start=1):
            strategy_id = futures[future]
            try:
                _, payload, error = future.result()
            except Exception as exc:  # noqa: BLE001 - one strategy must not terminate the batch.
                payload = None
                error = f"{type(exc).__name__}: {exc}"
            outcomes[strategy_id] = (payload, error)
            if error or payload is None:
                failure_total += 1
                print(f"[WARN] {label} {index}/{len(strategy_ids)} {strategy_id} failed: {error}", flush=True)
                extra = "本策略业绩曲线获取失败"
            else:
                rows = payload.get("adv_yield_trend_list") or []
                extra = f"曲线点 {len(rows)} | 最新日期 {payload_latest_date(payload) or '-'}"
            progress.emit(
                index,
                success=index - failure_total,
                failed=failure_total,
                current=strategy_id,
                extra=extra,
            )
    return outcomes


def main() -> None:
    args = parse_args()
    strategy_ids = load_strategy_ids(args)
    if not strategy_ids:
        raise SystemExit("No GFFunds strategy ids found.")

    run_at = now_local()
    day = run_at.strftime("%Y-%m-%d")
    run_id = args.run_id or run_at.strftime("%Y%m%dT%H%M%S%z")
    captured_at = run_at.isoformat(timespec="seconds")

    canonical_raw_root = PROJECT_ROOT / "data" / "raw"
    raw_dir = canonical_raw_root / CHANNEL_ID / "performance_curve" / day / run_id
    normalized_path = (
        PROJECT_ROOT
        / "data"
        / "normalized"
        / CHANNEL_ID
        / "strategy_performance_daily"
        / day
        / f"{run_id}.jsonl"
    )
    summary_path = (
        PROJECT_ROOT
        / "data"
        / "normalized"
        / CHANNEL_ID
        / "collection_summary"
        / day
        / f"{run_id}.json"
    )

    print(f"[INFO] GFFunds performance curve update run_id={run_id}")
    print(
        f"[INFO] strategy ids={len(strategy_ids)} workers={args.workers} "
        f"retry_rounds={max(0, args.retry_failed_rounds)} retry_workers={max(1, args.retry_workers)}",
        flush=True,
    )

    all_rows: list[dict[str, Any]] = []
    latest_dates: dict[str, str] = {}
    payloads: dict[str, dict[str, Any]] = {}
    source_modes: dict[str, str] = {}
    attempt_errors: dict[str, list[str]] = {strategy_id: [] for strategy_id in strategy_ids}

    initial = collect_round(
        strategy_ids,
        workers=max(1, args.workers),
        timeout=args.timeout,
        label="广发基金业绩曲线首轮",
    )
    remaining: dict[str, str] = {}
    for strategy_id, (payload, error) in initial.items():
        if payload is not None and not error:
            payloads[strategy_id] = payload
            source_modes[strategy_id] = "fresh_initial"
        else:
            remaining[strategy_id] = error or "unknown"
            attempt_errors[strategy_id].append(error or "unknown")
    initial_failure_total = len(remaining)

    retry_recovered_ids: list[str] = []
    for retry_round in range(1, max(0, args.retry_failed_rounds) + 1):
        if not remaining:
            break
        delay = max(0.0, args.retry_backoff_seconds) * retry_round
        print(
            f"[INFO] failed-target retry round {retry_round}/{args.retry_failed_rounds}: "
            f"targets={len(remaining)} workers={max(1, args.retry_workers)} backoff={delay:.1f}s",
            flush=True,
        )
        if delay:
            time.sleep(delay)
        outcomes = collect_round(
            list(remaining),
            workers=max(1, args.retry_workers),
            timeout=args.timeout,
            label=f"广发业绩失败项重试第{retry_round}轮",
        )
        next_remaining: dict[str, str] = {}
        for strategy_id, (payload, error) in outcomes.items():
            if payload is not None and not error:
                payloads[strategy_id] = payload
                source_modes[strategy_id] = f"fresh_retry_{retry_round}"
                retry_recovered_ids.append(strategy_id)
            else:
                next_remaining[strategy_id] = error or "unknown"
                attempt_errors[strategy_id].append(error or "unknown")
        remaining = next_remaining

    source_failures = [
        {
            "strategy_id": strategy_id,
            "error": error,
            "attempt_errors": attempt_errors.get(strategy_id) or [error],
        }
        for strategy_id, error in sorted(remaining.items())
    ]
    reused_last_good: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    raw_roots = candidate_raw_roots()
    for strategy_id, error in sorted(remaining.items()):
        payload, source_path = find_last_good_payload(strategy_id, raw_roots)
        if payload is None or source_path is None:
            unresolved.append({"strategy_id": strategy_id, "error": error})
            continue
        payloads[strategy_id] = payload
        source_modes[strategy_id] = "last_good"
        reused_last_good.append(
            {
                "strategy_id": strategy_id,
                "source_path": str(source_path),
                "latest_trade_date": payload_latest_date(payload) or None,
                "source_error": error,
            }
        )

    for strategy_id in strategy_ids:
        payload = payloads.get(strategy_id)
        if payload is None:
            continue
        raw_path = raw_dir / strategy_id / "get_investadvisor_yield_trend_since_inception.json"
        atomic_json(raw_path, payload)
        if source_modes[strategy_id] == "last_good":
            reuse = next(item for item in reused_last_good if item["strategy_id"] == strategy_id)
            atomic_json(raw_path.with_name("last_good_reuse_metadata.json"), reuse)
        rows = normalized_rows(strategy_id, payload, run_id)
        all_rows.extend(rows)
        latest_dates[strategy_id] = payload_latest_date(payload)

    all_rows.sort(key=lambda row: (row["source_strategy_id"], row["trade_date"]))
    write_jsonl(normalized_path, all_rows)

    reference_latest_date = max((value for value in latest_dates.values() if value), default=None)
    stopped_ids = load_stopped_strategy_ids()
    active_lagging_one_day: list[str] = []
    active_lagging_more: list[dict[str, Any]] = []
    if reference_latest_date:
        for strategy_id, latest_date in sorted(latest_dates.items()):
            if strategy_id in stopped_ids or not latest_date:
                continue
            lag = business_day_lag(latest_date, reference_latest_date)
            if lag is None or lag <= 0:
                continue
            if lag <= max(0, args.acceptable_business_lag_days):
                active_lagging_one_day.append(strategy_id)
            else:
                active_lagging_more.append(
                    {
                        "strategy_id": strategy_id,
                        "latest_trade_date": latest_date,
                        "business_lag_days": lag,
                    }
                )

    usable_total = len(payloads)
    usable_ratio = usable_total / len(strategy_ids)
    blocked_reasons: list[str] = []
    if usable_total == 0:
        blocked_reasons.append("no usable strategy curve")
    if usable_ratio < max(0.0, min(1.0, args.min_usable_ratio)):
        blocked_reasons.append(
            f"usable ratio {usable_ratio:.2%} below threshold {args.min_usable_ratio:.2%}"
        )
    warnings: list[str] = []
    if reused_last_good:
        warnings.append(f"{len(reused_last_good)}只策略源端暂时失败，已复用最近成功曲线")
    if unresolved:
        warnings.append(f"{len(unresolved)}只策略本轮无可用新曲线，保留数据库既有历史")
    if active_lagging_one_day:
        warnings.append(f"{len(active_lagging_one_day)}只正常策略较本批最新披露少1个交易日，按允许口径继续")
    if active_lagging_more:
        warnings.append(f"{len(active_lagging_more)}只正常策略披露落后超过1个交易日，已列入缺口")

    state = "blocked" if blocked_reasons else ("ready_with_warnings" if warnings else "ready")
    summary = {
        "channel_id": CHANNEL_ID,
        "channel_name": "广发基金",
        "run_id": run_id,
        "captured_at": captured_at,
        "collector": "update_gffunds_performance_curves",
        "state": state,
        "strategy_total": len(strategy_ids),
        "fresh_success_total": len(payloads) - len(reused_last_good),
        "initial_failure_total": initial_failure_total,
        "retry_recovered_total": len(retry_recovered_ids),
        "retry_recovered_ids": retry_recovered_ids,
        "source_failure_total": len(source_failures),
        "source_failures": source_failures,
        "reused_last_good_total": len(reused_last_good),
        "reused_last_good": reused_last_good,
        "success_total": usable_total,
        "usable_total": usable_total,
        "usable_ratio": usable_ratio,
        "failure_total": len(unresolved),
        "failures": unresolved,
        "daily_rows_total": len(all_rows),
        "latest_trade_date": reference_latest_date,
        "acceptable_business_lag_days": args.acceptable_business_lag_days,
        "active_lagging_one_day_total": len(active_lagging_one_day),
        "active_lagging_one_day_ids": active_lagging_one_day,
        "active_lagging_more_total": len(active_lagging_more),
        "active_lagging_more": active_lagging_more,
        "stopped_strategy_total": len(stopped_ids & set(strategy_ids)),
        "raw_dir": str(raw_dir),
        "normalized_path": str(normalized_path),
        "summary_path": str(summary_path),
        "warnings": warnings,
        "blocked_reasons": blocked_reasons,
    }
    atomic_json(summary_path, summary)
    if args.result_summary_path:
        atomic_json(args.result_summary_path.resolve(), summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)

    if blocked_reasons:
        raise SystemExit(20)


if __name__ == "__main__":
    main()
