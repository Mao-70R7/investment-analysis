from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "outputs" / "basic_data_readiness"
CHANNEL_ID = "ttfund"

DETAIL_PREFIXES = (
    "strategyDetailPageData",
    "ttfund-layout-cache-advicer-strategy-detail-matter-",
)


@dataclass
class DetailBenchmark:
    strategy_id: str
    strategy_name: str | None
    benchmark: str
    fee_text: str | None
    source_path: Path
    mtime: float
    priority: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill TTFund disclosed benchmark text from complete App detail cache files."
    )
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--cache-root", action="append", type=Path, default=[])
    parser.add_argument("--app-result-root", action="append", type=Path, default=[])
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing benchmark/fee text.")
    return parser.parse_args()


def load_json(path: Path) -> Any | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        if isinstance(payload, str):
            inner = payload.strip()
            if inner and inner[0] in "{[":
                return json.loads(inner)
        return payload
    except (OSError, json.JSONDecodeError):
        return None


def recursive_nodes(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from recursive_nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from recursive_nodes(child)


def norm_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text in {"--", "null", "None"}:
        return None
    return text


def to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text or text == "--":
        return None
    if text.endswith("%"):
        text = text[:-1]
    try:
        return float(text)
    except ValueError:
        return None


def format_fee(rate: Any, provision_type: Any) -> str | None:
    numeric = to_float(rate)
    unit = norm_text(provision_type) or ""
    if numeric is None and not unit:
        return None
    if numeric is None:
        return unit or None
    text = f"{numeric:.2f}%"
    return f"{text}{unit}" if unit else text


def extract_benchmark(payload: dict[str, Any]) -> str | None:
    candidates = (
        "basicCalFormulaRemark",
        "basicCalFormula",
        "benchmark",
        "benchmarkDesc",
        "benchmarkRemark",
        "standardDesc",
        "业绩比较基准",
        "业绩基准",
    )
    extend_info = payload.get("tgExtendInfo")
    for container in (extend_info, payload):
        if not isinstance(container, dict):
            continue
        for key in candidates:
            text = norm_text(container.get(key))
            if text:
                return text
    for node in recursive_nodes(payload):
        for key in candidates:
            text = norm_text(node.get(key))
            if text:
                return text
    return None


def strategy_id_from_path(path: Path, payload: dict[str, Any]) -> str | None:
    text = path.name
    if text.startswith("strategyDetailPageData"):
        tail = text[len("strategyDetailPageData") :]
        return tail.split("_", 1)[0].strip() or None
    marker = "ttfund-layout-cache-advicer-strategy-detail-matter-"
    if text.startswith(marker):
        tail = text[len(marker) :]
        return tail.split("-", 1)[0].strip() or None
    extend_info = payload.get("tgExtendInfo") if isinstance(payload, dict) else None
    return norm_text((extend_info or {}).get("tgCode") or payload.get("tgCode"))


def iter_detail_files(roots: list[Path]) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*.0"):
            if path.name.startswith(DETAIL_PREFIXES):
                files.append(path)
    return files


def collect_benchmarks(roots: list[Path]) -> dict[str, DetailBenchmark]:
    by_strategy: dict[str, DetailBenchmark] = {}
    for path in iter_detail_files(roots):
        payload = load_json(path)
        if not isinstance(payload, dict):
            continue
        extend_info = payload.get("tgExtendInfo")
        if not isinstance(extend_info, dict) or not extend_info:
            continue
        benchmark = extract_benchmark(payload)
        if not benchmark:
            continue
        strategy_id = strategy_id_from_path(path, payload)
        if not strategy_id:
            continue
        priority = 2 if path.name.startswith("strategyDetailPageData") else 1
        item = DetailBenchmark(
            strategy_id=strategy_id,
            strategy_name=norm_text(extend_info.get("tgName") or extend_info.get("name")),
            benchmark=benchmark,
            fee_text=format_fee(extend_info.get("strategyRate"), extend_info.get("provisionType")),
            source_path=path,
            mtime=path.stat().st_mtime,
            priority=priority,
        )
        current = by_strategy.get(strategy_id)
        if current is None or (item.priority, item.mtime) > (current.priority, current.mtime):
            by_strategy[strategy_id] = item
    return by_strategy


def merge_app_drive_results(by_strategy: dict[str, DetailBenchmark], roots: list[Path]) -> None:
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("result.json"):
            payload = load_json(path)
            if not isinstance(payload, dict):
                continue
            if not payload.get("benchmark_text_ok"):
                continue
            benchmark = norm_text(payload.get("benchmark_text"))
            strategy_id = norm_text(payload.get("strategy_id"))
            if not benchmark or not strategy_id:
                continue
            item = DetailBenchmark(
                strategy_id=strategy_id,
                strategy_name=norm_text(payload.get("strategy_name")),
                benchmark=benchmark,
                fee_text=norm_text(payload.get("service_fee_text")),
                source_path=path,
                mtime=path.stat().st_mtime,
                priority=3 if payload.get("benchmark_text_source") == "visible_ui" else 2,
            )
            current = by_strategy.get(strategy_id)
            if current is None or (item.priority, item.mtime) > (current.priority, current.mtime):
                by_strategy[strategy_id] = item


def main() -> None:
    args = parse_args()
    roots = args.cache_root or [
        PROJECT_ROOT / "data" / "raw" / "device_cache",
        PROJECT_ROOT / "data" / "raw" / "ttfund" / "loggedin_cache",
    ]
    app_result_roots = args.app_result_root or [
        PROJECT_ROOT / "data" / "raw" / "ttfund" / "app_drive",
    ]
    benchmarks = collect_benchmarks(roots)
    merge_app_drive_results(benchmarks, app_result_roots)

    conn = sqlite3.connect(args.db_path)
    conn.row_factory = sqlite3.Row
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    updated: list[dict[str, Any]] = []
    skipped_existing = 0
    missing_strategy = 0
    try:
        for strategy_id, item in sorted(benchmarks.items()):
            unified_id = f"{CHANNEL_ID}__{strategy_id}"
            row = conn.execute(
                'SELECT "统一策略ID", "策略名称", "投顾费率", "业绩基准" FROM "策略信息" WHERE "统一策略ID"=?',
                (unified_id,),
            ).fetchone()
            if row is None:
                missing_strategy += 1
                continue
            has_benchmark = bool(norm_text(row["业绩基准"]))
            has_fee = bool(norm_text(row["投顾费率"]))
            if has_benchmark and (has_fee or not item.fee_text) and not args.overwrite:
                skipped_existing += 1
                continue
            benchmark_value = item.benchmark if (args.overwrite or not has_benchmark) else row["业绩基准"]
            fee_value = item.fee_text if (item.fee_text and (args.overwrite or not has_fee)) else row["投顾费率"]
            conn.execute(
                '''
                UPDATE "策略信息"
                SET "业绩基准"=?,
                    "投顾费率"=?,
                    "原始快照ID"=COALESCE("原始快照ID", ?),
                    "最近入库时间"=?
                WHERE "统一策略ID"=?
                ''',
                (benchmark_value, fee_value, str(item.source_path), now, unified_id),
            )
            updated.append(
                {
                    "统一策略ID": unified_id,
                    "策略代码": strategy_id,
                    "策略名称": row["策略名称"] or item.strategy_name,
                    "业绩基准": benchmark_value,
                    "投顾费率": fee_value,
                    "来源文件": str(item.source_path),
                }
            )
        conn.commit()
    finally:
        conn.close()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    output_path = args.output_dir / "ttfund_benchmark_backfill_from_detail_cache.json"
    summary = {
        "generated_at": now,
        "cache_roots": [str(root) for root in roots],
        "app_result_roots": [str(root) for root in app_result_roots],
        "extracted_strategy_total": len(benchmarks),
        "updated_total": len(updated),
        "skipped_existing_total": skipped_existing,
        "missing_strategy_total": missing_strategy,
        "updated_examples": updated[:30],
    }
    output_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output_path": str(output_path), **summary}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
