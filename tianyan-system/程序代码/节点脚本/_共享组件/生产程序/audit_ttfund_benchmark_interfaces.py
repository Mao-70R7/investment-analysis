import json
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DB_PATH = ROOT / "data" / "analysis_zh_current.sqlite"
OUTPUT_DIR = ROOT / "outputs" / "basic_data_readiness"


INDEX_PATTERNS = [
    "沪深300",
    "中证800",
    "中证500",
    "中证1000",
    "中证全债",
    "中证综合债",
    "中债综合",
    "中债总全价",
    "中债新综合",
    "中证纯债",
    "中证短债",
    "中证货币基金",
    "货币基金指数",
    "货币市场基金指数",
    "中证偏股",
    "中证股票基金",
    "中证债券基金",
    "中证普通债券型基金",
    "创业板",
    "科创100",
    "中国战略新兴产业",
    "中证科技",
    "国证成长",
    "国证价值",
    "MSCI",
    "南华商品",
    "上证国债",
    "中证国债",
    "中证军工",
    "中证环保",
    "中证TMT",
    "中证医药",
    "中证内地消费",
]


DETAIL_KEYS_OF_INTEREST = [
    "strategyRate",
    "provisionType",
    "basicCalFormulaRemark",
    "risk",
    "investTerm",
    "minBuy",
    "tgName",
    "logoName",
    "partnerId",
    "wealthNo",
    "label1",
    "label2",
    "label3",
]


def read_json(path: Path) -> Any:
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def recursive_nodes(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from recursive_nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from recursive_nodes(child)


def latest_ttfund_run_dir() -> Path:
    root = ROOT / "data" / "raw" / "ttfund" / "loggedin_cache"
    candidates = sorted(root.glob("*/*/_manifest.json"))
    if not candidates:
        raise FileNotFoundError("no ttfund loggedin raw manifest found")
    return candidates[-1].parent


def latest_strategy_master() -> Path:
    root = ROOT / "data" / "normalized" / "ttfund" / "strategy_master"
    candidates = sorted(root.glob("*/*.jsonl"))
    if not candidates:
        raise FileNotFoundError("no ttfund normalized strategy_master found")
    return candidates[-1]


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def load_quote_rows(raw_run_dir: Path) -> list[dict[str, Any]]:
    rows = []
    for path in sorted((raw_run_dir / "quotes").glob("batch_*.json")):
        payload = read_json(path)
        data = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(data, dict):
            rows.extend(data.get("Data") or data.get("Datas") or data.get("datas") or data.get("list") or [])
        elif isinstance(data, list):
            rows.extend(data)
        elif isinstance(payload, dict):
            rows.extend(payload.get("Data") or payload.get("Datas") or payload.get("datas") or payload.get("list") or [])
    return [row for row in rows if isinstance(row, dict)]


def classify_cache_file(path: Path) -> str:
    name = path.name
    if name.startswith("strategyDetailPageData") or name.startswith("ttfund-layout-cache-advicer-strategy-detail-matter-"):
        return "detail"
    if name.startswith("layout_tougu-scroll-view") or name.startswith("saveAllAdvisersInfokey") or name.startswith("home-vuex_"):
        return "home"
    return "other"


def extract_detail_by_strategy(raw_run_dir: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for path in sorted((raw_run_dir / "device_cache").rglob("*.0")):
        if classify_cache_file(path) != "detail":
            continue
        payload = read_json(path)
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                continue
        if not isinstance(payload, dict):
            continue
        sid = None
        for pattern in [
            re.compile(r"strategyDetailPageData(?P<sid>[A-Za-z0-9]+)_"),
            re.compile(r"ttfund-layout-cache-advicer-strategy-detail-matter-(?P<sid>[A-Za-z0-9]+)-"),
        ]:
            match = pattern.search(path.name)
            if match:
                sid = match.group("sid")
                break
        if sid:
            result[sid] = payload
    return result


def detail_field_coverage(detail_by_strategy: dict[str, dict[str, Any]]) -> dict[str, int]:
    coverage = Counter()
    for payload in detail_by_strategy.values():
        extend = payload.get("tgExtendInfo") if isinstance(payload, dict) else None
        if not isinstance(extend, dict):
            continue
        for key in DETAIL_KEYS_OF_INTEREST:
            value = extend.get(key)
            if value not in (None, "", [], {}):
                coverage[key] += 1
    return dict(coverage)


def interesting_keys(rows: list[dict[str, Any]]) -> dict[str, Any]:
    key_counts = Counter()
    non_empty = Counter()
    sample_values: dict[str, list[Any]] = defaultdict(list)
    for row in rows:
        for key, value in row.items():
            key_counts[key] += 1
            if value not in (None, "", [], {}):
                non_empty[key] += 1
                if len(sample_values[key]) < 3:
                    sample_values[key].append(value)
    interesting = {}
    for key, count in key_counts.most_common():
        lower = key.lower()
        if any(token in lower for token in ["bench", "base", "rate", "fee", "risk", "index", "syl", "yield", "draw", "tg", "name"]):
            interesting[key] = {
                "rows": count,
                "non_empty": non_empty.get(key, 0),
                "samples": sample_values.get(key, []),
            }
    return interesting


def benchmark_index_mentions(rows: list[dict[str, Any]]) -> dict[str, Any]:
    texts = [str(row.get("benchmark") or "") for row in rows if row.get("benchmark")]
    counts = {pattern: sum(1 for text in texts if pattern in text) for pattern in INDEX_PATTERNS}
    split_tokens = Counter()
    for text in texts:
        clean = re.sub(r"[0-9.]+%?|收益率|指数|业绩比较基准|基准|[×*+（）()=：:<>\n\r、，。；;]", " ", text)
        for token in re.split(r"\s+", clean):
            token = token.strip()
            if len(token) >= 3 and not re.fullmatch(r"[A-Za-z0-9.]+", token):
                split_tokens[token] += 1
    return {
        "benchmark_text_total": len(texts),
        "known_pattern_counts": counts,
        "top_unstructured_tokens": [
            {"token": token, "count": count}
            for token, count in split_tokens.most_common(50)
        ],
        "examples": texts[:30],
    }


def index_table_summary() -> list[dict[str, Any]]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = [
        dict(row)
        for row in conn.execute(
            '''
            SELECT "指数代码" AS code,
                   "指数名称" AS name,
                   COUNT(*) AS rows,
                   MIN("交易日期") AS start_date,
                   MAX("交易日期") AS end_date
            FROM "指数日度行情"
            GROUP BY "指数代码", "指数名称"
            ORDER BY rows DESC
            '''
        )
    ]
    conn.close()
    return rows


def main() -> None:
    raw_run_dir = latest_ttfund_run_dir()
    master_path = latest_strategy_master()
    master_rows = load_jsonl(master_path)
    quote_rows = load_quote_rows(raw_run_dir)
    detail_by_strategy = extract_detail_by_strategy(raw_run_dir)

    detail_ids = set(detail_by_strategy)
    quote_ids = {str(row.get("TGCODE") or "").strip() for row in quote_rows if row.get("TGCODE")}
    master_ids = {str(row.get("source_strategy_id") or "").strip() for row in master_rows}

    result = {
        "raw_run_dir": str(raw_run_dir),
        "strategy_master": str(master_path),
        "master_total": len(master_rows),
        "quote_total": len(quote_rows),
        "quote_strategy_total": len(quote_ids),
        "detail_cache_strategy_total": len(detail_ids),
        "master_with_detail_cache": len(master_ids & detail_ids),
        "master_without_detail_cache": len(master_ids - detail_ids),
        "detail_field_coverage": detail_field_coverage(detail_by_strategy),
        "quote_interesting_keys": interesting_keys(quote_rows),
        "benchmark_index_mentions": benchmark_index_mentions(master_rows),
        "index_table_summary": index_table_summary(),
    }
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / "ttfund_benchmark_interface_audit.json"
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output_path": str(output_path), **result}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
