from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

import requests


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"

sys.path.insert(0, str(PROJECT_ROOT / "节点脚本" / "_共享组件" / "生产程序"))
import collect_ttfund_official_performance_curve as curve  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check quote readiness and whether the official curve is within its allowed disclosure lag."
    )
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--output-path", type=Path)
    parser.add_argument("--sample-size", type=int, default=12)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--min-latest-ratio", type=float, default=0.8)
    parser.add_argument("--min-benchmark-ratio", type=float, default=0.75)
    parser.add_argument("--max-source-lag-business-days", type=int, default=1)
    return parser.parse_args()


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp_path.replace(path)


def build_plan(output_path: Path) -> dict[str, Any]:
    command = [
        sys.executable,
        "-X",
        "utf8",
        str(PROJECT_ROOT / "节点脚本" / "_共享组件" / "生产程序" / "build_ttfund_incremental_plan.py"),
        "--history-mode",
        "none",
        "--direct-rebalance-probe-mode",
        "none",
        "--adb-rebalance-fallback-mode",
        "none",
        "--benchmark-detail-repair-mode",
        "none",
        "--current-holding-cooldown-days",
        "999999",
        "--selection-only",
        "--summary-only",
        "--output-path",
        str(output_path),
    ]
    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=240,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"readiness plan failed: {completed.stdout[-2000:]}")
    return json.loads(output_path.read_text(encoding="utf-8-sig"))


def spread_sample(values: list[str], size: int) -> list[str]:
    if len(values) <= size:
        return values
    if size <= 1:
        return values[:1]
    indexes = {round(index * (len(values) - 1) / (size - 1)) for index in range(size)}
    return [values[index] for index in sorted(indexes)]


def select_sample_ids(db_path: Path, sample_size: int) -> tuple[list[str], str | None]:
    latest_dates = curve.load_latest_dates(db_path)
    if not latest_dates:
        strategies = curve.load_strategies(db_path, limit=max(1, sample_size))
        return [str(item["source_strategy_id"]) for item in strategies], None
    local_max = max(latest_dates.values())
    current_ids = sorted(strategy_id for strategy_id, value in latest_dates.items() if value == local_max)
    current_strategies = curve.load_strategies(db_path, current_ids)
    active_ids = sorted(
        str(strategy["source_strategy_id"])
        for strategy in current_strategies
        if curve.classify_quality_scope(strategy) == "active"
    )
    return spread_sample(active_ids or current_ids, max(1, sample_size)), local_max


def fetch_sample(strategy: dict[str, Any], timeout: float) -> dict[str, Any]:
    session = requests.Session()
    session.headers.update({"User-Agent": curve.USER_AGENT, "Accept": "application/json"})
    result = curve.fetch_curve(session, strategy, range_code=curve.DEFAULT_RANGE, timeout=timeout, retries=2)
    daily_rows = curve.build_daily_rows(result, "readiness", "readiness") if result.get("ok") else []
    latest_date = max((str(row.get("trade_date") or "") for row in daily_rows), default=None)
    latest_benchmark_date = max(
        (
            str(row.get("trade_date") or "")
            for row in daily_rows
            if row.get("benchmark_return") is not None
        ),
        default=None,
    )
    return {
        "strategy_id": str(strategy["source_strategy_id"]),
        "ok": bool(daily_rows),
        "latest_date": latest_date,
        "latest_benchmark_date": latest_benchmark_date,
        "status_code": result.get("status_code"),
        "error": result.get("error"),
    }


def assess_readiness(
    *,
    target_trade_date: str | None,
    quote_ratio: float,
    local_max_date: str | None,
    source_effective_date: str | None,
    success_ratio: float,
    latest_ratio: float,
    benchmark_ratio: float,
    min_latest_ratio: float,
    min_benchmark_ratio: float,
    source_lag_business_days: int | None,
    max_source_lag_business_days: int,
) -> dict[str, Any]:
    official_reasons: list[str] = []
    if success_ratio < min_latest_ratio:
        official_reasons.append(f"official curve success ratio is {success_ratio:.4f}")
    if not source_effective_date:
        official_reasons.append("official curve did not expose an effective disclosure date")
    if latest_ratio < min_latest_ratio:
        official_reasons.append(f"official curve latest ratio is {latest_ratio:.4f}")
    if benchmark_ratio < min_benchmark_ratio:
        official_reasons.append(f"official benchmark latest ratio is {benchmark_ratio:.4f}")
    if source_effective_date and local_max_date and source_effective_date < local_max_date:
        official_reasons.append(
            f"official curve date {source_effective_date} is behind local date {local_max_date}"
        )
    if (
        target_trade_date
        and source_lag_business_days is not None
        and source_lag_business_days > max_source_lag_business_days
    ):
        official_reasons.append(
            f"official curve source lag is {source_lag_business_days} business day(s)"
        )

    official_ready = not official_reasons
    quote_ready = bool(target_trade_date) and quote_ratio >= 0.95
    reasons: list[str] = []
    if target_trade_date:
        if not quote_ready:
            reasons.append(f"quote coverage ratio is {quote_ratio:.4f}")
        reasons.extend(official_reasons)
        readiness_mode = "all_sources_current" if not reasons else "waiting"
    elif official_ready:
        readiness_mode = "official_curve_fallback"
    else:
        reasons.extend(
            [
                "quote endpoint did not return a target trade date",
                f"quote coverage ratio is {quote_ratio:.4f}",
            ]
        )
        reasons.extend(official_reasons)
        readiness_mode = "waiting"
    return {
        "state": "ready" if not reasons else "waiting",
        "readiness_mode": readiness_mode,
        "quote_ready": quote_ready,
        "official_ready": official_ready,
        "reasons": reasons,
    }


def main() -> None:
    args = parse_args()
    now = datetime.now().astimezone()
    output_path = args.output_path or (
        PROJECT_ROOT
        / "outputs"
        / "daily_source_readiness"
        / now.strftime("%Y-%m-%d")
        / f"{now.strftime('%Y%m%dT%H%M%S%z')}.json"
    )
    plan_path = output_path.with_name(f"{output_path.stem}_plan.json")
    result: dict[str, Any]
    exit_code = 2
    try:
        plan = build_plan(plan_path)
        remote_probe = plan.get("remote_probe") or {}
        target_trade_date = str(remote_probe.get("max_trade_date") or "") or None
        strategy_total = int((plan.get("local_baseline") or {}).get("db_strategy_total") or 0)
        quote_strategy_total = int(remote_probe.get("quote_strategy_total") or 0)
        quote_ratio = quote_strategy_total / strategy_total if strategy_total else 0.0
        sample_ids, local_max_date = select_sample_ids(args.db_path, args.sample_size)
        strategies = curve.load_strategies(args.db_path, sample_ids)
        sample_rows: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
            futures = [executor.submit(fetch_sample, strategy, args.timeout) for strategy in strategies]
            for future in as_completed(futures):
                sample_rows.append(future.result())
        sample_rows.sort(key=lambda row: row["strategy_id"])

        strategy_by_id = {str(item["source_strategy_id"]): item for item in strategies}
        eligible_sample_ids = {
            strategy_id
            for strategy_id, strategy in strategy_by_id.items()
            if curve.classify_quality_scope(strategy) == "active"
        }
        sample_rows = [row for row in sample_rows if row["strategy_id"] in eligible_sample_ids]
        sample_total = len(sample_rows)
        success_total = sum(1 for row in sample_rows if row["ok"])
        sample_latest_dates = {
            str(row["strategy_id"]): str(row.get("latest_date") or "")
            for row in sample_rows
            if row.get("latest_date")
        }
        source_effective_date = curve.disclosure_date_at_coverage(
            sample_latest_dates,
            eligible_sample_ids,
            args.min_latest_ratio,
        )
        latest_total = sum(
            1
            for row in sample_rows
            if source_effective_date and (row.get("latest_date") or "") >= source_effective_date
        )
        benchmark_total = sum(
            1
            for row in sample_rows
            if source_effective_date and (row.get("latest_benchmark_date") or "") >= source_effective_date
        )
        success_ratio = success_total / sample_total if sample_total else 0.0
        latest_ratio = latest_total / sample_total if sample_total else 0.0
        benchmark_ratio = benchmark_total / sample_total if sample_total else 0.0
        source_lag_business_days = curve.business_day_lag(source_effective_date, target_trade_date)
        assessment = assess_readiness(
            target_trade_date=target_trade_date,
            quote_ratio=quote_ratio,
            local_max_date=local_max_date,
            source_effective_date=source_effective_date,
            success_ratio=success_ratio,
            latest_ratio=latest_ratio,
            benchmark_ratio=benchmark_ratio,
            min_latest_ratio=args.min_latest_ratio,
            min_benchmark_ratio=args.min_benchmark_ratio,
            source_lag_business_days=source_lag_business_days,
            max_source_lag_business_days=args.max_source_lag_business_days,
        )
        state = str(assessment["state"])
        reasons = list(assessment["reasons"])
        exit_code = 0 if state == "ready" else 3
        result = {
            "version": 1,
            "state": state,
            "checked_at": now.isoformat(timespec="seconds"),
            "target_trade_date": target_trade_date,
            "official_effective_date": source_effective_date,
            "source_lag_business_days": source_lag_business_days,
            "max_source_lag_business_days": args.max_source_lag_business_days,
            "readiness_mode": assessment["readiness_mode"],
            "quote_ready": assessment["quote_ready"],
            "official_ready": assessment["official_ready"],
            "local_max_trade_date": local_max_date,
            "quote_strategy_total": quote_strategy_total,
            "strategy_total": strategy_total,
            "quote_ratio": quote_ratio,
            "sample_total": sample_total,
            "sample_success_total": success_total,
            "sample_latest_total": latest_total,
            "sample_benchmark_latest_total": benchmark_total,
            "sample_success_ratio": success_ratio,
            "sample_latest_ratio": latest_ratio,
            "sample_benchmark_latest_ratio": benchmark_ratio,
            "reasons": reasons,
            "sample": sample_rows,
            "plan_path": str(plan_path.resolve()),
        }
    except Exception as exc:  # noqa: BLE001 - readiness errors are structured for retry decisions.
        result = {
            "version": 1,
            "state": "error",
            "checked_at": now.isoformat(timespec="seconds"),
            "error": f"{type(exc).__name__}: {exc}",
            "plan_path": str(plan_path.resolve()),
        }
    atomic_write_json(output_path, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
