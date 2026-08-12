from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from collect_qieman_stargate_proxy import request_proxy, write_jsonl
from probe_qieman_device import active_locks, write_json


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def fetch_benchmark(port: int, code: str) -> tuple[str, Any, str | None]:
    try:
        payload = request_proxy(
            port,
            "GetStrategyBenchmark",
            query={"strategyCode": code},
            timeout=60,
        )
        return code, payload, None
    except Exception as exc:
        return code, None, str(exc)


def load_exact_baseline(
    path: Path | None,
    codes: set[str],
    run_id: str,
) -> dict[str, dict[str, Any]]:
    if path is None or not path.is_file():
        return {}
    baseline: dict[str, dict[str, Any]] = {}
    for source in read_jsonl(path):
        code = str(source.get("source_strategy_id") or "").strip()
        if code not in codes or not bool(source.get("is_exact_split")):
            continue
        row = dict(source)
        source_run_id = str(row.get("run_id") or "").strip() or None
        row["run_id"] = run_id
        row["benchmark_reused_from_baseline"] = True
        row["baseline_source_run_id"] = source_run_id
        baseline[code] = row
    return baseline


def normalize_benchmark_payload(code: str, payload: dict[str, Any], run_id: str) -> dict[str, Any]:
    details = payload.get("indexDetail") if isinstance(payload.get("indexDetail"), list) else []
    components = [
        {
            "index_code": item.get("indexCode"),
            "index_name": item.get("indexName"),
            "index_type": item.get("indexType"),
            "weight": item.get("percent"),
        }
        for item in details
        if isinstance(item, dict)
    ]
    weights = [item.get("weight") for item in components]
    exact = bool(components) and all(isinstance(value, (int, float)) for value in weights)
    weight_sum = sum(float(value) for value in weights) if exact else None
    return {
        "channel_id": "qieman",
        "source_strategy_id": code,
        "benchmark_description": payload.get("indexDescription"),
        "benchmark_components": components,
        "benchmark_weight_sum": round(weight_sum, 8) if weight_sum is not None else None,
        "is_exact_split": bool(exact and abs((weight_sum or 0) - 1) <= 0.001),
        "confidence_level": "official_stargate_exact_benchmark_split",
        "run_id": run_id,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Augment a Qieman StarGate run with exact benchmark splits.")
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--proxy-port", type=int, default=43912)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--baseline-benchmark", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    locks = active_locks()
    if locks:
        raise SystemExit("active production lock; benchmark collection aborted: " + ", ".join(locks))
    run_dir = args.run_dir.resolve()
    master_path = run_dir / "normalized" / "strategy_master.jsonl"
    masters = read_jsonl(master_path)
    codes = sorted({str(row.get("source_strategy_id") or "").strip() for row in masters if row.get("source_strategy_id")})
    baseline = load_exact_baseline(args.baseline_benchmark, set(codes), run_dir.name)
    requested_codes = [code for code in codes if code not in baseline]
    results: dict[str, Any] = {}
    errors: dict[str, str] = {}
    workers = max(1, min(24, args.workers))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(fetch_benchmark, args.proxy_port, code): code for code in requested_codes}
        for future in as_completed(futures):
            code, payload, error = future.result()
            if error:
                errors[code] = error
            else:
                results[code] = payload

    rows_by_code = dict(baseline)
    for code in requested_codes:
        payload = results.get(code)
        if not isinstance(payload, dict):
            continue
        write_json(run_dir / "raw" / "benchmark" / f"{code}.json", payload)
        rows_by_code[code] = normalize_benchmark_payload(code, payload, run_dir.name)
    rows = [rows_by_code[code] for code in codes if code in rows_by_code]
    exact_count = sum(1 for row in rows if bool(row.get("is_exact_split")))
    write_jsonl(run_dir / "normalized" / "strategy_benchmark.jsonl", rows)

    benchmark_by_code = {row["source_strategy_id"]: row for row in rows}
    enriched: list[dict[str, Any]] = []
    for master in masters:
        row = dict(master)
        benchmark = benchmark_by_code.get(str(row.get("source_strategy_id") or ""))
        if benchmark:
            row["benchmark"] = benchmark.get("benchmark_description")
            row.setdefault("extra", {})["benchmark_components"] = benchmark.get("benchmark_components")
        enriched.append(row)
    write_jsonl(run_dir / "normalized" / "strategy_master_enriched.jsonl", enriched)

    summary = {
        "state": "stargate_benchmarks_collected",
        "strategy_count": len(codes),
        "benchmark_request_count": len(requested_codes),
        "benchmark_response_count": len(results),
        "benchmark_baseline_reused_count": len(baseline),
        "baseline_benchmark_path": str(args.baseline_benchmark.resolve()) if args.baseline_benchmark else None,
        "benchmark_row_count": len(rows),
        "exact_benchmark_split_count": exact_count,
        "error_count": len(errors),
        "errors": errors,
        "duplicate_business_keys": len(rows) - len({row["source_strategy_id"] for row in rows}),
        "run_dir": str(run_dir),
    }
    write_json(run_dir / "benchmark_summary.json", summary)
    print(json.dumps({key: value for key, value in summary.items() if key != "errors"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
