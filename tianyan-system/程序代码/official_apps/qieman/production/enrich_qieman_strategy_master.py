from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


SAFE_RETAINED_FIELDS = (
    "advisor_name",
    "strategy_type",
    "suggested_holding_period",
    "minimum_amount",
    "advisory_fee_rate",
    "source_url",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Safely enrich Qieman strategy master facts with exact lineage.")
    parser.add_argument("--base-master", type=Path, required=True)
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--previous-master", type=Path)
    parser.add_argument("--public-master", type=Path)
    parser.add_argument("--search-master", type=Path)
    parser.add_argument("--ui-runs-root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    return parser.parse_args()


def read_jsonl(path: Path | None) -> list[dict[str, Any]]:
    if path is None or not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    temporary.replace(path)


def clean_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def parse_holding_period(*values: Any) -> str | None:
    text = "\n".join(str(value or "") for value in values)
    patterns = (
        r"(?:建议持有|适合持有|适合长期持有|建议持仓)\s*([0-9一二三四五六七八九十半]+\s*(?:天|周|个?月|年)(?:以上|左右|起)?)",
        r"投资期限建议\s*[:：]?\s*([0-9一二三四五六七八九十半]+\s*(?:天|周|个?月|年)(?:以上|左右|起)?)",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return re.sub(r"\s+", "", match.group(1))
    return None


def public_holding_period(row: dict[str, Any]) -> str | None:
    direct = clean_text(row.get("suggested_holding_period"))
    if direct:
        return direct
    extra = row.get("extra") if isinstance(row.get("extra"), dict) else {}
    metrics = extra.get("public_card_metrics") if isinstance(extra.get("public_card_metrics"), list) else []
    for metric in metrics:
        if not isinstance(metric, dict):
            continue
        if "持有" in str(metric.get("key") or ""):
            value = clean_text(metric.get("text"))
            if value:
                return value
    return parse_holding_period(row.get("strategy_description"), extra.get("availability_text"))


def ui_evidence(root: Path | None, master_names: dict[str, str]) -> dict[str, dict[str, Any]]:
    evidence: dict[str, dict[str, Any]] = {}
    if root is None or not root.is_dir():
        return evidence
    for path in sorted(root.glob("*-strategy-scroll/*/ocr.json")):
        strategy_id = path.parent.name.strip()
        if strategy_id not in master_names:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        rows = []
        for page in payload.get("pages") or []:
            if not isinstance(page, dict):
                continue
            rows.extend(item for item in page.get("rows") or [] if isinstance(item, dict))
        texts = [clean_text(item.get("text")) for item in rows]
        texts = [text for text in texts if text]
        joined = "\n".join(texts)
        if master_names[strategy_id] not in joined:
            continue
        item: dict[str, Any] = {"source_path": str(path.resolve())}
        advisor = re.search(r"本策略由\s*([^|\n]{2,30}?)\s*提供", joined)
        if advisor:
            item["advisor_name"] = advisor.group(1).strip()
        holding = parse_holding_period(joined)
        if holding:
            item["suggested_holding_period"] = holding
        purchase = re.search(
            r"([0-9]+(?:\.[0-9]+)?)\s*元起购[^\n]*?投顾服务费\s*[:：]?\s*([0-9]+(?:\.[0-9]+)?%\s*/\s*年)",
            joined,
        )
        if purchase:
            item["minimum_amount"] = float(purchase.group(1))
            item["minimum_amount_text"] = f"{purchase.group(1)}元起购"
            item["advisory_fee_rate"] = re.sub(r"\s+", "", purchase.group(2))
        if len(item) > 1:
            evidence[strategy_id] = item
    return evidence


def promote_official_detail_lineage(
    row: dict[str, Any],
    extra: dict[str, Any],
    lineage: dict[str, str],
    field_sources: Counter[str],
) -> None:
    official_detail_lineage = (
        extra.get("stargate_detail_lineage")
        if isinstance(extra.get("stargate_detail_lineage"), dict)
        else {}
    )
    for field, source in official_detail_lineage.items():
        if row.get(field) not in (None, ""):
            lineage.setdefault(field, str(source))
            field_sources[f"{field}:stargate_detail"] += 1


def main() -> None:
    args = parse_args()
    masters = read_jsonl(args.base_master.resolve())
    benchmarks = read_jsonl(args.benchmark.resolve())
    previous = read_jsonl(args.previous_master.resolve() if args.previous_master else None)
    public = read_jsonl(args.public_master.resolve() if args.public_master else None)
    search = read_jsonl(args.search_master.resolve() if args.search_master else None)

    ids = [clean_text(row.get("source_strategy_id")) for row in masters]
    if not masters or any(not value for value in ids) or len(set(ids)) != len(ids):
        raise SystemExit("base master is empty or has blank/duplicate source_strategy_id")
    name_counts = Counter(clean_text(row.get("strategy_name")) for row in masters)
    master_names = {str(row["source_strategy_id"]): str(row.get("strategy_name") or "") for row in masters}
    previous_by_id = {str(row.get("source_strategy_id")): row for row in previous if row.get("source_strategy_id")}
    benchmark_by_id = {str(row.get("source_strategy_id")): row for row in benchmarks if row.get("source_strategy_id")}
    public_by_id = {
        str(row.get("source_strategy_id")): row
        for row in public
        if row.get("source_strategy_id") and str(row.get("source_strategy_id")) in set(ids)
    }
    search_by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in search:
        name = clean_text(row.get("strategy_name"))
        if name:
            search_by_name[name].append(row)
    ui_by_id = ui_evidence(args.ui_runs_root.resolve() if args.ui_runs_root else None, master_names)

    field_sources: Counter[str] = Counter()
    output: list[dict[str, Any]] = []
    for base in masters:
        row = dict(base)
        strategy_id = str(row["source_strategy_id"])
        name = clean_text(row.get("strategy_name"))
        extra = dict(row.get("extra") or {}) if isinstance(row.get("extra"), dict) else {}
        lineage = dict(extra.get("enrichment_lineage") or {}) if isinstance(extra.get("enrichment_lineage"), dict) else {}
        promote_official_detail_lineage(row, extra, lineage, field_sources)

        prior = previous_by_id.get(strategy_id, {})
        for field in SAFE_RETAINED_FIELDS:
            if row.get(field) in (None, "") and prior.get(field) not in (None, ""):
                row[field] = prior[field]
                lineage[field] = "previous_accepted_qieman_snapshot"
                field_sources[f"{field}:previous"] += 1

        benchmark = benchmark_by_id.get(strategy_id)
        if benchmark and benchmark.get("is_exact_split") and clean_text(benchmark.get("benchmark_description")):
            row["benchmark"] = clean_text(benchmark.get("benchmark_description"))
            extra["benchmark_components"] = benchmark.get("benchmark_components") or []
            extra["benchmark_is_exact_split"] = True
            lineage["benchmark"] = "official_stargate_exact_benchmark_split"
            field_sources["benchmark:stargate"] += 1

        public_row = public_by_id.get(strategy_id)
        if public_row:
            for field in ("advisor_name", "strategy_type", "source_url"):
                if row.get(field) in (None, "") and public_row.get(field) not in (None, ""):
                    row[field] = public_row[field]
                    lineage[field] = "public_curated_exact_strategy_id"
                    field_sources[f"{field}:public"] += 1
            holding = public_holding_period(public_row)
            if row.get("suggested_holding_period") in (None, "") and holding:
                row["suggested_holding_period"] = holding
                lineage["suggested_holding_period"] = "public_curated_exact_strategy_id"
                field_sources["suggested_holding_period:public"] += 1

        if name and name_counts[name] == 1 and len(search_by_name.get(name, [])) == 1:
            search_row = search_by_name[name][0]
            for field in ("advisor_name", "risk_level", "suggested_holding_period"):
                if row.get(field) in (None, "") and search_row.get(field) not in (None, ""):
                    row[field] = search_row[field]
                    lineage[field] = "authenticated_search_unique_exact_name"
                    field_sources[f"{field}:search"] += 1

        if row.get("suggested_holding_period") in (None, ""):
            holding = parse_holding_period(
                row.get("strategy_description"),
                extra.get("strategy_detail_text"),
            )
            if holding:
                row["suggested_holding_period"] = holding
                lineage["suggested_holding_period"] = "official_strategy_text_exact_parse"
                field_sources["suggested_holding_period:text"] += 1

        ui = ui_by_id.get(strategy_id)
        if ui:
            for field in ("advisor_name", "suggested_holding_period", "minimum_amount", "advisory_fee_rate"):
                if row.get(field) in (None, "") and ui.get(field) not in (None, ""):
                    row[field] = ui[field]
                    lineage[field] = "authenticated_app_ocr_exact_strategy_id"
                    field_sources[f"{field}:ui"] += 1
            if ui.get("minimum_amount_text"):
                extra["minimum_amount_text"] = ui["minimum_amount_text"]
            extra.setdefault("app_ui_evidence_paths", []).append(ui["source_path"])

        extra["enrichment_lineage"] = lineage
        row["extra"] = extra
        row["run_id"] = args.run_id
        output.append(row)

    output.sort(key=lambda item: str(item["source_strategy_id"]))
    write_jsonl(args.output.resolve(), output)
    coverage_fields = (
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
    report = {
        "state": "qieman_strategy_master_enriched",
        "run_id": args.run_id,
        "strategy_count": len(output),
        "coverage": {
            field: sum(row.get(field) not in (None, "") for row in output)
            for field in coverage_fields
        },
        "field_sources": dict(sorted(field_sources.items())),
        "input_counts": {
            "base_master": len(masters),
            "benchmark": len(benchmarks),
            "previous_master": len(previous),
            "public_master": len(public),
            "search_master": len(search),
            "ui_exact_strategy": len(ui_by_id),
        },
        "boundaries": {
            "advisor": "Only official StarGate detail, exact strategy ID, unique exact strategy name, prior accepted fact, or exact app UI evidence is used.",
            "strategy_type": "Never inferred from holdings or benchmark.",
            "fee_and_minimum": "Only official StarGate detail or exact authenticated app UI evidence is promoted; missing values remain null.",
            "benchmark": "Only official exact component splits whose weights close to 100% are promoted.",
        },
        "output": str(args.output.resolve()),
    }
    write_json(args.report.resolve(), report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
