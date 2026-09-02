from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from collect_qieman_stargate_proxy import request_proxy
from probe_qieman_device import active_locks


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    temporary.replace(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect only missing or inexact Qieman benchmark responses.")
    parser.add_argument("--master-path", type=Path, required=True)
    parser.add_argument("--existing-benchmark-path", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--proxy-port", type=int, default=43912)
    parser.add_argument("--workers", type=int, default=4)
    return parser.parse_args()


def fetch_one(port: int, code: str) -> tuple[str, Any, str | None]:
    try:
        return code, request_proxy(
            port,
            "GetStrategyBenchmark",
            query={"strategyCode": code},
            timeout=90,
        ), None
    except Exception as exc:
        return code, None, str(exc)


def normalize_benchmark(code: str, payload: dict[str, Any], run_id: str) -> dict[str, Any]:
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
    numeric = bool(components) and all(isinstance(value, (int, float)) for value in weights)
    weight_sum = sum(float(value) for value in weights) if numeric else None
    exact = bool(numeric and abs((weight_sum or 0) - 1) <= 0.001)
    return {
        "channel_id": "qieman",
        "source_strategy_id": code,
        "benchmark_description": payload.get("indexDescription"),
        "benchmark_components": components,
        "benchmark_weight_sum": round(weight_sum, 8) if weight_sum is not None else None,
        "is_exact_split": exact,
        "confidence_level": "official_stargate_exact_benchmark_split",
        "run_id": run_id,
    }


def main() -> None:
    args = parse_args()
    locks = active_locks()
    if locks:
        raise SystemExit("active production lock; Qieman benchmark gap collection aborted: " + ", ".join(locks))
    masters = read_jsonl(args.master_path.resolve())
    existing = read_jsonl(args.existing_benchmark_path.resolve())
    master_codes = {
        str(row.get("source_strategy_id") or "").strip()
        for row in masters
        if row.get("source_strategy_id")
    }
    existing_by_code = {
        str(row.get("source_strategy_id") or "").strip(): row
        for row in existing
        if str(row.get("source_strategy_id") or "").strip() in master_codes
    }
    gap_codes = sorted(
        code for code in master_codes
        if code not in existing_by_code or not existing_by_code[code].get("is_exact_split")
    )
    run_id = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z") + "-stargate-benchmark-gaps"
    run_dir = args.output_root.resolve() / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    workers = max(1, min(8, args.workers))
    responses: dict[str, dict[str, Any]] = {}
    errors: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(fetch_one, args.proxy_port, code): code for code in gap_codes}
        for future in as_completed(futures):
            code, payload, error = future.result()
            if error:
                errors[code] = error
            elif isinstance(payload, dict):
                responses[code] = payload
                write_json(run_dir / "raw" / "benchmark" / f"{code}.json", payload)
            else:
                errors[code] = "response is not an object"

    merged_by_code = dict(existing_by_code)
    new_rows: list[dict[str, Any]] = []
    for code, payload in responses.items():
        row = normalize_benchmark(code, payload, run_id)
        new_rows.append(row)
        merged_by_code[code] = row
    merged = [merged_by_code[code] for code in sorted(merged_by_code)]
    write_jsonl(run_dir / "normalized" / "strategy_benchmark_new.jsonl", sorted(new_rows, key=lambda row: row["source_strategy_id"]))
    write_jsonl(run_dir / "normalized" / "strategy_benchmark_merged.jsonl", merged)
    exact_codes = {code for code, row in merged_by_code.items() if row.get("is_exact_split")}
    response_codes = set(merged_by_code)
    summary = {
        "state": "qieman_stargate_benchmark_gaps_collected",
        "run_id": run_id,
        "production_database_written": False,
        "daily_update_pipeline_touched": False,
        "master_strategy_count": len(master_codes),
        "requested_gap_count": len(gap_codes),
        "new_response_count": len(responses),
        "new_exact_split_count": sum(row.get("is_exact_split") for row in new_rows),
        "merged_response_count": len(response_codes),
        "merged_exact_split_count": len(exact_codes),
        "remaining_missing_response_count": len(master_codes - response_codes),
        "remaining_inexact_response_count": len(response_codes - exact_codes),
        "error_count": len(errors),
        "errors": errors,
        "run_dir": str(run_dir),
    }
    write_json(run_dir / "summary.json", summary)
    print(json.dumps({key: value for key, value in summary.items() if key != "errors"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
