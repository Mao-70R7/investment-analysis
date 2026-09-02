from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sqlite3
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


CHANNEL_ID = "southern"
PUBLIC_SOURCE_URL = "https://m.nffund.com/new/index.html?tabIndex=RoboHomePage"
PUBLISHED_ENTITIES = (
    "strategy_master",
    "strategy_benchmark",
    "strategy_fund_snapshot",
    "strategy_asset_snapshot",
    "strategy_performance_daily",
    "strategy_fund_snapshot_history",
    "strategy_rebalance_event",
    "strategy_rebalance_fund_delta",
    "strategy_notice",
)
INDEX_META = {
    "000300": ("沪深300", "权益"),
    "H00300": ("沪深300全收益", "权益"),
    "000905": ("中证500", "权益"),
    "000906": ("中证800", "权益"),
    "892400": ("MSCI全球指数", "权益"),
    "H00979": ("大宗商品全收益", "商品"),
    "000012": ("上证国债", "固收"),
    "CBA00203": ("中债-综合全价(总值)指数", "固收"),
    "CBA00303": ("中债-总指数(全价)", "固收"),
    "H11001": ("中证全债", "固收"),
    "H11008": ("中证企业债", "固收"),
    "H11015": ("中证短债", "固收"),
    "H11025": ("货币基金", "现金"),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect and normalize one complete Southern advisor batch.")
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument("--code-root", type=Path, required=True)
    parser.add_argument("--normalized-root", type=Path, required=True)
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--node-run-dir", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--daily-run-id", required=True)
    parser.add_argument("--db-path", type=Path, required=True)
    parser.add_argument("--collector-summary", type=Path)
    parser.add_argument("--inventory", type=Path)
    parser.add_argument("--login-wait-seconds", type=int, default=60)
    parser.add_argument("--result-path", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object expected: {path}")
    return value


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def load_normalizer(code_root: Path):
    program = code_root / "节点脚本" / "_共享组件" / "生产程序"
    sys.path.insert(0, str(program))
    path = program / "normalize_southern_live_artifact.py"
    spec = importlib.util.spec_from_file_location("southern_live_normalizer", path)
    if not spec or not spec.loader:
        raise RuntimeError(f"Unable to load Southern normalizer: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def public_catalog(raw_root: Path, run_id: str) -> Path:
    """Discover the official IA049 + IA050 + IA028 catalog union."""

    from playwright.sync_api import sync_playwright

    target = raw_root / CHANNEL_ID / "public_h5" / run_id[:8] / run_id / "strategy_inventory.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(PUBLIC_SOURCE_URL, wait_until="commit", timeout=120_000)
            page.wait_for_function(
                """() => {
                    const app = document.querySelector('#app');
                    return Boolean(app && app.__vue_app__ && app.__vue_app__.config.globalProperties.httpFetch);
                }""",
                timeout=120_000,
            )

            def call(code: str, params: dict[str, Any]) -> Any:
                return page.evaluate(
                    """async ({code, params}) => {
                        const gp = document.querySelector('#app').__vue_app__.config.globalProperties;
                        return await gp.httpFetch.post(code, params);
                    }""",
                    {"code": code, "params": params},
                )

            categories = call("IA049", {}) or {}
            all_050 = call("IA050", {"ignoreLoading": False}) or {}
            all_028 = call("IA028", {"ignoreLoading": True}) or {}
            category_responses: list[tuple[str, dict[str, Any]]] = []
            for item in categories.get("returnlist") or []:
                category_id = str(item.get("classify_id") or "").strip()
                if category_id:
                    category_responses.append(
                        (category_id, call("IA050", {"ignoreLoading": False, "classify_id": category_id}) or {})
                    )
        finally:
            browser.close()

    by_id: dict[str, dict[str, Any]] = {}

    def merge(row: dict[str, Any], source: str, category_id: str = "") -> None:
        strategy_id = str(row.get("iacombcode") or row.get("fundcode") or row.get("combcode") or "").strip()
        if not strategy_id:
            return
        current = by_id.setdefault(
            strategy_id,
            {
                "source_strategy_id": strategy_id,
                "strategy_name": "",
                "sceneno": "",
                "discovery_sources": [],
                "category_ids": [],
            },
        )
        current["strategy_name"] = current["strategy_name"] or row.get("combname") or row.get("fundname") or ""
        current["sceneno"] = current["sceneno"] or str(row.get("sceneno") or "")
        if source not in current["discovery_sources"]:
            current["discovery_sources"].append(source)
        if category_id and category_id not in current["category_ids"]:
            current["category_ids"].append(category_id)

    for row in all_050.get("returnlist") or []:
        merge(row, "IA050_all")
    for category_id, response in category_responses:
        for row in response.get("returnlist") or []:
            merge(row, f"IA050_category_{category_id}", category_id)
    for row in all_028.get("returnlist") or []:
        merge(row, "IA028")
    strategies = sorted(by_id.values(), key=lambda row: row["source_strategy_id"])
    if not strategies or any(not row.get("sceneno") for row in strategies):
        raise RuntimeError("Southern official catalog discovery returned an empty or incomplete strategy inventory.")
    atomic_json(
        target,
        {
            "channel_id": CHANNEL_ID,
            "run_id": run_id,
            "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "strategy_count": len(strategies),
            "discovery_complete": True,
            "discovery_scope": ["IA049", "IA050_all", "IA050_categories", "IA028"],
            "strategies": strategies,
        },
    )
    return target


def run_authenticated_collector(args: argparse.Namespace, inventory_path: Path) -> Path:
    collector = args.code_root / "节点脚本" / "_共享组件" / "生产程序" / "run_southern_live_collect.py"
    if not collector.is_file():
        raise FileNotFoundError(f"Southern authenticated collector is missing: {collector}")
    collector_result = args.node_run_dir / "southern_authenticated_collect.json"
    environment = dict(os.environ)
    environment["SOUTHERN_DAILY_RUN_ID"] = args.daily_run_id
    command = [
        sys.executable,
        "-u",
        "-X",
        "utf8",
        str(collector),
        "--all",
        "--inventory",
        str(inventory_path),
        "--dpapi-input",
        str(args.workspace_root / "本机配置" / "southern_login.dpapi"),
        "--login-wait-seconds",
        str(max(15, min(720, args.login_wait_seconds))),
        "--result-path",
        str(collector_result),
    ]
    completed = subprocess.run(command, cwd=args.workspace_root, env=environment, check=False)
    if completed.returncode != 0:
        state = read_json(collector_result).get("status") if collector_result.is_file() else "collection_failed"
        raise RuntimeError(f"Southern authenticated collection failed: status={state}, exit={completed.returncode}")
    if not collector_result.is_file():
        raise RuntimeError("Southern collector did not write its exact result artifact.")
    return collector_result


def fund_dimension(db_path: Path) -> dict[str, tuple[str | None, str | None]]:
    if not db_path.is_file():
        return {}
    with sqlite3.connect(db_path) as connection:
        try:
            return {
                str(code): (str(name).strip() if name else None, str(fund_type).strip() if fund_type else None)
                for code, name, fund_type in connection.execute(
                    'SELECT "基金代码", "基金名称", "基金类型" FROM "基金信息"'
                )
                if code
            }
        except sqlite3.OperationalError:
            return {}


def existing_strategy_ids(db_path: Path) -> set[str]:
    if not db_path.is_file():
        return set()
    with sqlite3.connect(db_path) as connection:
        try:
            return {
                str(row[0])
                for row in connection.execute(
                    'SELECT "渠道策略ID" FROM "策略信息" WHERE "渠道ID"=?',
                    (CHANNEL_ID,),
                )
            }
        except sqlite3.OperationalError:
            return set()


def benchmark_formula(raw: Any) -> str | None:
    text = re.sub(r"<br\s*/?>", "\n", str(raw or ""), flags=re.I)
    text = re.sub(r"<[^>]+>", "", text).strip()
    match = re.search(r"(?:^|\n)\s*1[、.]\s*([^；;\n]+)", text)
    formula = match.group(1).strip() if match else ""
    if not formula and "*" in text and "%" in text:
        formula = re.split(r"[；;\n]", text, maxsplit=1)[0].strip()
    return formula or None


def benchmark_row(normalizer: Any, artifact_path: Path, strategy_id: str, run_id: str, captured_at: str) -> dict[str, Any] | None:
    artifact = read_json(artifact_path)
    events = artifact.get("events") or []
    market = normalizer.parse_json_response(events, "webIAcombFundMarketQuery")
    info = ((market.get("result") or {}).get("info") or {})
    benchmark_list = info.get("benchmarklist") or []
    if not benchmark_list:
        return None
    components = []
    for item in normalizer.parse_ratio_info(benchmark_list[0].get("ratioinfo")):
        code = str(item.get("fund_code") or "").strip()
        weight = item.get("fund_weight")
        if not code or weight is None:
            continue
        name, asset_type = INDEX_META.get(code, (None, None))
        components.append(
            {"index_code": code, "index_name": name, "index_type": asset_type, "weight": weight}
        )
    exact = bool(components) and abs(sum(float(row["weight"]) for row in components) - 100.0) <= 0.01
    return {
        "channel_id": CHANNEL_ID,
        "source_strategy_id": strategy_id,
        "benchmark_components": components,
        "is_exact_split": exact,
        "confidence_level": "official_exact" if exact else "official_incomplete",
        "source_snapshot_id": f"{CHANNEL_ID}-{strategy_id}-benchmark-{run_id}",
        "run_id": run_id,
        "captured_at": captured_at,
    }


def iter_json_lines(rows: Iterable[dict[str, Any]]) -> Iterable[str]:
    for row in rows:
        yield json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"


def consolidate(
    args: argparse.Namespace,
    inventory_path: Path,
    collector_path: Path,
) -> tuple[Path, Path, dict[str, Any]]:
    inventory = read_json(inventory_path)
    expected_ids = {
        str(row.get("source_strategy_id") or "").strip()
        for row in inventory.get("strategies") or []
        if str(row.get("source_strategy_id") or "").strip()
    }
    collector = read_json(collector_path)
    plan_results = collector.get("plan_results") or []
    result_by_id = {str(row.get("source_strategy_id") or "").strip(): row for row in plan_results}
    collected_ids = set(result_by_id)
    if not expected_ids or expected_ids != collected_ids:
        raise RuntimeError(
            f"Southern catalog batch is not closed: expected={len(expected_ids)} collected={len(collected_ids)} "
            f"missing={sorted(expected_ids - collected_ids)} extra={sorted(collected_ids - expected_ids)}"
        )
    failed = [strategy_id for strategy_id, row in result_by_id.items() if (row.get("validation") or {}).get("passed") is not True]
    if failed:
        raise RuntimeError(f"Southern required official responses are incomplete: {failed}")

    args.node_run_dir.mkdir(parents=True, exist_ok=True)
    run_root_by_entity = {
        entity: args.normalized_root / CHANNEL_ID / entity / args.run_id for entity in PUBLISHED_ENTITIES
    }
    temporary_paths: dict[str, Path] = {}
    handles: dict[str, Any] = {}
    for entity, directory in run_root_by_entity.items():
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{args.run_id}.jsonl"
        temporary = target.with_suffix(target.suffix + f".{os.getpid()}.tmp")
        temporary_paths[entity] = temporary
        handles[entity] = temporary.open("w", encoding="utf-8", newline="\n")

    normalizer = load_normalizer(args.code_root)
    dimension = fund_dimension(args.db_path)
    counts: Counter[str] = Counter()
    performance_ids: set[str] = set()
    current_ids: set[str] = set()
    history_ids: set[str] = set()
    benchmark_ids: set[str] = set()
    latest_dates: dict[str, str] = {}
    current_weight_sums: defaultdict[str, float] = defaultdict(float)
    history_weight_sums: defaultdict[tuple[str, str], float] = defaultdict(float)
    missing_fund_name_rows = 0
    unknown_index_codes: set[str] = set()
    captured_at = str(collector.get("captured_at") or datetime.now().astimezone().isoformat(timespec="seconds"))

    try:
        for strategy_id in sorted(expected_ids):
            artifact_path = Path(str(result_by_id[strategy_id].get("output_path") or ""))
            if not artifact_path.is_file():
                raise FileNotFoundError(f"Southern plan artifact is missing: {artifact_path}")
            normalized, _ = normalizer.normalize(artifact_path, args.run_id, captured_at)
            benchmark = benchmark_row(normalizer, artifact_path, strategy_id, args.run_id, captured_at)
            if benchmark:
                normalized["strategy_benchmark"] = [benchmark]
                if benchmark.get("is_exact_split"):
                    benchmark_ids.add(strategy_id)
                unknown_index_codes.update(
                    str(row.get("index_code"))
                    for row in benchmark.get("benchmark_components") or []
                    if row.get("index_code") not in INDEX_META
                )

            master = normalized.get("strategy_master") or []
            if master:
                artifact = read_json(artifact_path)
                events = artifact.get("events") or []
                info = normalizer.parse_json_response(events, "webIAqueryCombInfo").get("result") or {}
                formula = benchmark_formula((info.get("sortlist") or [{}])[0].get("desc"))
                master[0]["benchmark"] = formula
                master[0]["extra"] = {
                    "provider_department": info.get("provider"),
                    "portfolio_manager": info.get("manager"),
                }
                master[0]["source_snapshot_id"] = f"{CHANNEL_ID}-{strategy_id}-{args.run_id}"
                master[0]["confidence_level"] = "official_exact"

            for entity, rows in normalized.items():
                if entity not in handles:
                    continue
                for row in rows:
                    if entity == "strategy_performance_daily" and not row.get("source_snapshot_id"):
                        row["source_snapshot_id"] = (
                            f"{CHANNEL_ID}-{strategy_id}-performance-{row.get('trade_date') or args.run_id}"
                        )
                    fund_code = str(row.get("fund_code") or "").strip()
                    if fund_code and entity in {
                        "strategy_fund_snapshot",
                        "strategy_fund_snapshot_history",
                        "strategy_rebalance_fund_delta",
                    }:
                        dim_name, dim_type = dimension.get(fund_code, (None, None))
                        if not row.get("fund_name"):
                            row["fund_name"] = dim_name
                        if not row.get("fund_asset_type"):
                            row["fund_asset_type"] = dim_type
                        if not row.get("fund_group_name"):
                            row["fund_group_name"] = dim_type
                        if not row.get("fund_name"):
                            missing_fund_name_rows += 1
                    handles[entity].write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
                    counts[entity] += 1

                    if entity == "strategy_performance_daily":
                        performance_ids.add(strategy_id)
                        date_value = str(row.get("trade_date") or "")[:10]
                        if date_value and date_value > latest_dates.get(strategy_id, ""):
                            latest_dates[strategy_id] = date_value
                    elif entity == "strategy_fund_snapshot":
                        current_ids.add(strategy_id)
                        current_weight_sums[strategy_id] += float(row.get("fund_weight") or 0)
                    elif entity == "strategy_fund_snapshot_history":
                        history_ids.add(strategy_id)
                        history_weight_sums[(strategy_id, str(row.get("position_date") or ""))] += float(row.get("fund_weight") or 0)
    finally:
        for handle in handles.values():
            handle.close()

    for entity, temporary in temporary_paths.items():
        os.replace(temporary, run_root_by_entity[entity] / f"{args.run_id}.jsonl")

    bad_current = {strategy_id: total for strategy_id, total in current_weight_sums.items() if abs(total - 100.0) > 0.05}
    bad_history = {
        f"{strategy_id}|{position_date}": total
        for (strategy_id, position_date), total in history_weight_sums.items()
        if abs(total - 100.0) > 0.05
    }
    source_latest_date = max(latest_dates.values()) if latest_dates else None
    latest_date_counts = dict(Counter(latest_dates.values()))
    failures = []
    if len(performance_ids) != len(expected_ids):
        failures.append("performance_strategy_coverage")
    if len(current_ids) != len(expected_ids):
        failures.append("current_holding_strategy_coverage")
    if len(history_ids) != len(expected_ids):
        failures.append("historical_holding_strategy_coverage")
    if bad_current:
        failures.append("current_holding_weight_closure")
    if bad_history:
        failures.append("historical_holding_weight_closure")
    if missing_fund_name_rows:
        failures.append("historical_fund_name_resolution")
    if unknown_index_codes:
        failures.append("benchmark_index_mapping")

    previous_ids = existing_strategy_ids(args.db_path)
    new_ids = sorted(expected_ids - previous_ids)
    coverage = {
        "performance_with_rows": len(performance_ids),
        "current_position_complete": len(current_ids) - len(bad_current),
        "historical_position_complete": len(history_ids),
        "benchmark_exact_split": len(benchmark_ids),
    }
    summary = {
        "channel_id": CHANNEL_ID,
        "run_id": args.run_id,
        "daily_run_id": args.daily_run_id,
        "captured_at": captured_at,
        "collector_summary_path": str(collector_path),
        "inventory_path": str(inventory_path),
        "catalog_discovery_complete": inventory.get("discovery_complete") is True or len(expected_ids) > 0,
        "catalog_batch_closed": expected_ids == collected_ids,
        "catalog_strategy_ids": sorted(expected_ids),
        "catalog_new_strategy_ids": new_ids,
        "catalog_new_strategy_total": len(new_ids),
        "strategy_total": len(expected_ids),
        "counts": dict(counts),
        "coverage": coverage,
        "source_latest_nav_date": source_latest_date,
        "nav_latest_date_counts": latest_date_counts,
        "latest_nav_date_strategy_total": int(latest_date_counts.get(source_latest_date, 0)) if source_latest_date else 0,
        "non_empty_nav_strategy_total": len(latest_dates),
        "benchmark_missing_strategy_ids": sorted(expected_ids - benchmark_ids),
        "missing_fund_name_rows": missing_fund_name_rows,
        "audit_status": "passed" if not failures else "failed",
    }
    summary_path = args.normalized_root / CHANNEL_ID / "collection_summary" / args.run_id / f"{args.run_id}.json"
    atomic_json(summary_path, summary)
    audit = {
        "status": "passed" if not failures else "failed",
        "error_count": len(failures),
        "warning_count": 1 if len(benchmark_ids) < len(expected_ids) else 0,
        "failed_required_checks": failures,
        "strategy_total": len(expected_ids),
        "coverage": coverage,
        "bad_current_weight_snapshots": bad_current,
        "bad_history_weight_snapshot_count": len(bad_history),
        "bad_history_weight_snapshot_examples": dict(list(bad_history.items())[:20]),
        "benchmark_missing_strategy_ids": sorted(expected_ids - benchmark_ids),
        "unknown_index_codes": sorted(unknown_index_codes),
        "missing_fund_name_rows": missing_fund_name_rows,
    }
    audit_path = args.node_run_dir / "southern_isolated_audit.json"
    atomic_json(audit_path, audit)
    if failures:
        raise RuntimeError(f"Southern isolated data audit failed: {failures}")
    return summary_path, audit_path, summary


def main() -> int:
    args = parse_args()
    args.workspace_root = args.workspace_root.resolve()
    args.code_root = args.code_root.resolve()
    args.normalized_root = args.normalized_root.resolve()
    args.raw_root = args.raw_root.resolve()
    args.node_run_dir = args.node_run_dir.resolve()
    args.db_path = args.db_path.resolve()
    args.result_path = args.result_path.resolve()
    if args.dry_run:
        atomic_json(
            args.result_path,
            {
                "status": "dry_run",
                "run_id": args.run_id,
                "would_discover_public_catalog": args.inventory is None,
                "would_collect_authenticated_catalog": args.collector_summary is None,
            },
        )
        return 0

    inventory_path = args.inventory.resolve() if args.inventory else public_catalog(args.raw_root, args.run_id)
    collector_path = (
        args.collector_summary.resolve()
        if args.collector_summary
        else run_authenticated_collector(args, inventory_path)
    )
    summary_path, audit_path, summary = consolidate(args, inventory_path, collector_path)
    result = {
        "status": "passed",
        "run_id": args.run_id,
        "summary_path": str(summary_path),
        "audit_report_path": str(audit_path),
        "inventory_path": str(inventory_path),
        "collector_summary_path": str(collector_path),
        "strategy_total": summary["strategy_total"],
        "catalog_new_strategy_total": summary["catalog_new_strategy_total"],
        "counts": summary["counts"],
        "coverage": summary["coverage"],
        "source_latest_nav_date": summary["source_latest_nav_date"],
    }
    atomic_json(args.result_path, result)
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr, flush=True)
        raise
