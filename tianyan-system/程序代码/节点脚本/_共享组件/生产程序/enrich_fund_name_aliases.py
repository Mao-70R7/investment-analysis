from __future__ import annotations

import argparse
import json
import re
import sqlite3
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import requests


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
OUTPUT_DIR = PROJECT_ROOT / "outputs" / "fund_alias_mapping"
SEARCH_URL = "https://fundsuggest.eastmoney.com/FundSearch/api/FundSearchAPI.ashx"
FUND_CATEGORY = "基金"
USER_AGENT = "Mozilla/5.0 advisor-monitor/0.1"

TABLE_DELTA = "策略调仓明细"
TABLE_HOLDING = "策略当前持仓"
TABLE_ALIAS = "基金名称映射"

COMPARE_REMOVE_TOKENS = [
    "混合型",
    "债券型",
    "股票型",
    "混合",
    "债券",
    "股票",
    "发起式",
    "发起",
    "灵活配置",
    "灵活",
    "配置型",
    "（LOF）",
    "(LOF)",
    "LOF",
    "ETF发起式联接",
    "ETF联接",
    "ETF",
    "联接",
    "指数",
    "成份",
    "（QDII）",
    "(QDII)",
    "QDII",
    "基金",
    "货币",
    "产业",
]

SEARCH_REMOVE_TOKENS = [
    "混合",
    "债券",
    "股票",
    "发起式",
    "发起",
    "灵活配置",
    "指数",
    "（LOF）",
    "(LOF)",
    "LOF",
    "（QDII）",
    "(QDII)",
    "QDII",
]

TEXT_REPLACEMENTS = [
    ("富兰克林国海", "国富"),
    ("交银施罗德", "交银"),
    ("上投摩根", "上投"),
    ("国泰君安", "国泰海通"),
    ("中泰资管", "中泰"),
    ("前海开源", "前海"),
    ("景顺长城", "景顺"),
    ("工银瑞信", "工银"),
    ("浦银安盛", "浦银"),
    ("华泰柏瑞", "华泰"),
    ("科创板", "科创"),
]

MANUAL_OVERRIDE_MAP = {
    "万家300增强C": {"fund_code": "002671", "standard_name": "万家沪深300指数增强C"},
    "东方主题": {"fund_code": "400032", "standard_name": "东方主题精选混合"},
    "中欧信用增利债券C": {"fund_code": "166012", "standard_name": "中欧信用增利债券(LOF)C"},
    "上投亚太": {"fund_code": "377016", "standard_name": "摩根亚太优势混合(QDII)A"},
    "上投欧洲": {"fund_code": "006282", "standard_name": "摩根欧洲动力策略股票(QDII)A"},
    "博时中债1-3年国开债C": {"fund_code": "007148", "standard_name": "博时中债1-3年国开行C"},
    "易方达增强回报债B": {"fund_code": "110018", "standard_name": "易方达增强回报债券B"},
    "易方达50ETF联接C": {"fund_code": "007380", "standard_name": "易方达上证50ETF联接基金C"},
    "易方达中债1-3年国开行债券指数A": {"fund_code": "007169", "standard_name": "易方达中债1-3年国开债A"},
    "易方达中债1-3年国开行债券指数C": {"fund_code": "007170", "standard_name": "易方达中债1-3年国开债C"},
    "易方达科创50联接C": {"fund_code": "011609", "standard_name": "易方达上证科创50联接C"},
    "易方达稳健收益债B": {"fund_code": "110008", "standard_name": "易方达稳健收益债券B"},
    "滚钱宝A": {"fund_code": "001211", "standard_name": "中欧滚钱宝货币A"},
    "博时亚洲票息债券(QDII)A": {"fund_code": "050030", "standard_name": "博时亚洲票息收益债券A人民币"},
    "博时亚洲票息债券(QDII)C": {"fund_code": "019480", "standard_name": "博时亚洲票息收益债券C人民币"},
    "博时标普500ETF联接（人民币）A": {"fund_code": "050025", "standard_name": "博时标普500ETF联接A"},
    "博时标普石油天然气勘探及生产精选行业指数发起(QDII)A": {"fund_code": "018851", "standard_name": "博时标普石油天然气勘探及生产精选行业指数发起(QDII)A人民币"},
    "华安四季红A": {"fund_code": "040026", "standard_name": "华安信用四季红债券A"},
    "国富大中华": {"fund_code": "000934", "standard_name": "国富大中华精选混合"},
    "国投300增强C": {"fund_code": "007144", "standard_name": "国投瑞银沪深300指数量化增强C"},
    "天弘创业板ETF联结C": {"fund_code": "001593", "standard_name": "天弘创业板ETF联接C"},
    "安信量化300A": {"fund_code": "003957", "standard_name": "安信量化精选沪深300增强A"},
    "摩根全球多元人民币": {"fund_code": "003629", "standard_name": "摩根全球多元配置(QDII-FOF)人民币A"},
    "宏利印度机会股票(QDII)A": {"fund_code": "006105", "standard_name": "宏利印度股票(QDII)A"},
    "平安0-3年金融债C": {"fund_code": "006933", "standard_name": "平安0-3年期政策性金融债债券C"},
    "广发全球医疗C": {"fund_code": "016280", "standard_name": "广发全球医疗保健指数人民币(QDII)C"},
    "华夏500增强C": {"fund_code": "007995", "standard_name": "华夏中证500指数增强C"},
    "华安国际龙头(DAX)ETF联接A": {"fund_code": "000614", "standard_name": "华安德国(DAX)联接(QDII)A"},
    "泰达印度": {"fund_code": "006105", "standard_name": "宏利印度股票(QDII)A"},
    "工银瑞信印度市场(QDII-LOF-FOF)": {"fund_code": "164824", "standard_name": "工银印度基金人民币"},
    "南方中债1-3年A": {"fund_code": "006491", "standard_name": "南方1-3年国开债A"},
    "富国消费电子ETF联接C": {"fund_code": "015877", "standard_name": "富国中证消费电子主题ETF发起式联接C"},
    "鹏华军工C": {"fund_code": "010364", "standard_name": "鹏华空天军工指数(LOF)C"},
    "永赢迅利E": {"fund_code": "009985", "standard_name": "永赢迅利中高等级短债E"},
    "泰达睿智A": {"fund_code": "003501", "standard_name": "宏利睿智稳健混合A"},
}

ASCII_SHARE_SUFFIXES = {"A", "B", "C", "D", "E", "F", "I", "Y"}
CURRENCY_MARKERS = ("人民币", "美元", "现汇", "现钞")
FUZZY_SCORE_THRESHOLD = 0.9
FUZZY_MARGIN_THRESHOLD = 0.05


@dataclass
class MatchResult:
    source_name: str
    matched: bool
    fund_code: str | None
    standard_name: str | None
    match_method: str | None
    confidence: str | None
    note: str | None
    raw_candidates: list[dict[str, Any]]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Resolve missing fund codes by exact Eastmoney fund name/alias matches.")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH, help="Analysis SQLite path.")
    parser.add_argument("--channel-id", default="zocaifu", help="Only enrich missing names for one channel. Default: zocaifu.")
    parser.add_argument("--limit", type=int, default=None, help="Optional limit for a test run.")
    parser.add_argument("--refresh", action="store_true", help="Re-query names already stored in 基金名称映射.")
    parser.add_argument("--sleep-ms", type=int, default=80, help="Delay between requests in milliseconds.")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR, help="Directory for enrichment reports.")
    return parser.parse_args()


def now_local() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def sanitize_fund_code(value: Any) -> str | None:
    if value is None:
        return None
    digits = "".join(ch for ch in str(value).strip() if ch.isdigit())
    if len(digits) == 6:
        return digits
    if digits and len(digits) < 6:
        return digits.zfill(6)
    return None


def normalize_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def ensure_alias_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        f'''
        CREATE TABLE IF NOT EXISTS "{TABLE_ALIAS}" (
            "映射名称" TEXT PRIMARY KEY,
            "基金代码" TEXT NOT NULL,
            "标准基金名称" TEXT,
            "匹配方式" TEXT NOT NULL,
            "匹配来源" TEXT NOT NULL,
            "置信度" TEXT NOT NULL,
            "更新时间" TEXT NOT NULL
        )
        '''
    )


def discover_missing_names(conn: sqlite3.Connection, channel_id: str, refresh: bool) -> list[str]:
    sql = f'''
        WITH missing_names AS (
            SELECT "基金名称" AS fund_name
            FROM "{TABLE_DELTA}"
            WHERE "渠道ID" = ?
              AND ("基金代码" IS NULL OR TRIM("基金代码") = '')
              AND "基金名称" IS NOT NULL
              AND TRIM("基金名称") <> ''
            UNION
            SELECT "基金名称" AS fund_name
            FROM "{TABLE_HOLDING}"
            WHERE "渠道ID" = ?
              AND ("基金代码" IS NULL OR TRIM("基金代码") = '')
              AND "基金名称" IS NOT NULL
              AND TRIM("基金名称") <> ''
        )
        SELECT DISTINCT fund_name
        FROM missing_names
        {'' if refresh else f'WHERE fund_name NOT IN (SELECT "映射名称" FROM "{TABLE_ALIAS}")'}
        ORDER BY fund_name
    '''
    return [row[0] for row in conn.execute(sql, [channel_id, channel_id]).fetchall()]


def parse_aliases(candidate: dict[str, Any]) -> list[str]:
    fund_base = candidate.get("FundBaseInfo") or {}
    other_name = fund_base.get("OTHERNAME") or ""
    return [item.strip() for item in other_name.split(",") if item.strip()]


def candidate_texts(candidate: dict[str, Any]) -> list[str]:
    texts = [normalize_text(candidate.get("NAME"))]
    texts.extend(parse_aliases(candidate))
    return [text for text in texts if text]


def candidate_snapshot(candidate: dict[str, Any]) -> dict[str, Any]:
    fund_base = candidate.get("FundBaseInfo") or {}
    return {
        "code": candidate.get("CODE"),
        "name": candidate.get("NAME"),
        "other_names": parse_aliases(candidate),
        "fund_type": fund_base.get("FTYPE"),
    }


def normalize_match_text(value: Any) -> str | None:
    text = normalize_text(value)
    if text is None:
        return None
    normalized = text.replace("（", "(").replace("）", ")").replace("　", " ")
    normalized = re.sub(r"\s+", "", normalized)
    for source, target in TEXT_REPLACEMENTS:
        normalized = normalized.replace(source, target)
    return normalized or None


def strip_tokens(text: str, tokens: list[str]) -> str:
    result = text
    for token in tokens:
        result = result.replace(token, "")
    return result


def canonical_key(value: Any) -> str | None:
    normalized = normalize_match_text(value)
    if normalized is None:
        return None
    simplified = strip_tokens(normalized, COMPARE_REMOVE_TOKENS)
    simplified = re.sub(r"[()\-_/]", "", simplified)
    return simplified or None


def split_canonical_core_and_suffix(value: Any) -> tuple[str | None, str | None]:
    key = canonical_key(value)
    if key is None:
        return None, None
    # Only treat ASCII share-class suffixes like A/C/E/F/I/Y as detachable.
    # Python's str.isalpha() also returns True for Chinese characters, which
    # breaks containment matching for names such as "上投亚太".
    if re.fullmatch(r"[A-Za-z]", key[-1:]):
        return key[:-1] or None, key[-1]
    return key, None


def extract_ascii_share_suffix(value: Any) -> str | None:
    key = canonical_key(value)
    if key and key[-1:] in ASCII_SHARE_SUFFIXES:
        return key[-1]
    return None


def has_currency_marker(value: Any) -> bool:
    normalized = normalize_match_text(value)
    if normalized is None:
        return False
    return any(marker in normalized for marker in CURRENCY_MARKERS)


def similarity_score(source_name: str, candidate_text: str) -> float:
    source_key = canonical_key(source_name) or ""
    candidate_key = canonical_key(candidate_text) or ""
    if not source_key or not candidate_key:
        return 0.0
    ratio = SequenceMatcher(None, source_key, candidate_key).ratio()
    if source_key in candidate_key or candidate_key in source_key:
        ratio += 0.1
    if candidate_key.startswith(source_key) or source_key.startswith(candidate_key):
        ratio += 0.15
    return round(ratio, 6)


def best_candidate_text_score(source_name: str, candidate: dict[str, Any]) -> tuple[float, str | None]:
    best_score = 0.0
    best_text: str | None = None
    source_has_currency = has_currency_marker(source_name)
    source_suffix = extract_ascii_share_suffix(source_name)
    for text in candidate_texts(candidate):
        score = similarity_score(source_name, text)
        candidate_suffix = extract_ascii_share_suffix(text)
        if source_suffix and candidate_suffix and candidate_suffix != source_suffix:
            score -= 0.12
        if not source_has_currency and has_currency_marker(text):
            score -= 0.12
        if score > best_score:
            best_score = score
            best_text = text
    return best_score, best_text


def select_unique_fuzzy_candidate(source_name: str, candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    source_prefix = (normalize_match_text(source_name) or "")[:2]
    scored: list[tuple[float, dict[str, Any], str | None]] = []
    for candidate in candidates:
        score, best_text = best_candidate_text_score(source_name, candidate)
        if best_text is None:
            continue
        normalized_best = normalize_match_text(best_text) or ""
        if source_prefix and source_prefix not in normalized_best:
            score -= 0.08
        scored.append((score, candidate, best_text))
    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    top_score, top_candidate, _ = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else 0.0
    if top_score >= FUZZY_SCORE_THRESHOLD and (top_score - second_score) >= FUZZY_MARGIN_THRESHOLD:
        return top_candidate
    return None


def build_search_keys(source_name: str) -> list[str]:
    keys: list[str] = []

    def add_key(value: str | None) -> None:
        if value is None:
            return
        key = value.strip()
        if len(key) < 2 or key in keys:
            return
        keys.append(key)

    normalized = normalize_match_text(source_name)
    add_key(source_name)
    add_key(normalized)
    if normalized:
        stripped = strip_tokens(normalized, SEARCH_REMOVE_TOKENS)
        add_key(stripped)
        if normalized[-1:].isalpha():
            add_key(normalized[:-1])
        if stripped and stripped[-1:].isalpha():
            add_key(stripped[:-1])
    return keys[:5]


def fetch_candidates(session: requests.Session, key: str) -> list[dict[str, Any]]:
    response = session.get(SEARCH_URL, params={"m": "1", "key": key}, timeout=20)
    response.raise_for_status()
    payload = response.json()
    return [item for item in (payload.get("Datas") or []) if item.get("CATEGORYDESC") == FUND_CATEGORY]


def dedupe_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    seen: set[tuple[str | None, str | None]] = set()
    for candidate in candidates:
        key = (sanitize_fund_code(candidate.get("CODE")), normalize_text(candidate.get("NAME")))
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def select_unique_candidate(
    matches: list[dict[str, Any]],
    *,
    allow_prefer_front_end: bool = False,
) -> dict[str, Any] | None:
    by_code: dict[str, dict[str, Any]] = {}
    for candidate in matches:
        fund_code = sanitize_fund_code(candidate.get("CODE"))
        if fund_code:
            by_code.setdefault(fund_code, candidate)
    if len(by_code) == 1:
        return next(iter(by_code.values()))
    if not allow_prefer_front_end:
        return None
    front_end = [
        candidate
        for candidate in by_code.values()
        if "后端" not in (normalize_text(candidate.get("NAME")) or "")
    ]
    if len(front_end) == 1:
        return front_end[0]
    return None


def build_match_result(
    *,
    source_name: str,
    candidate: dict[str, Any],
    candidates: list[dict[str, Any]],
    match_method: str,
    confidence: str,
    note: str,
) -> MatchResult:
    return MatchResult(
        source_name=source_name,
        matched=True,
        fund_code=sanitize_fund_code(candidate.get("CODE")),
        standard_name=normalize_text(candidate.get("NAME")),
        match_method=match_method,
        confidence=confidence,
        note=note,
        raw_candidates=[candidate_snapshot(item) for item in candidates[:10]],
    )


def build_manual_override_result(
    *,
    source_name: str,
    fund_code: str,
    standard_name: str | None,
    candidates: list[dict[str, Any]],
    note: str,
) -> MatchResult:
    return MatchResult(
        source_name=source_name,
        matched=True,
        fund_code=sanitize_fund_code(fund_code),
        standard_name=normalize_text(standard_name) or source_name,
        match_method="manual_override_explicit",
        confidence="high",
        note=note,
        raw_candidates=[candidate_snapshot(item) for item in candidates[:10]],
    )


def resolve_name(session: requests.Session, source_name: str) -> MatchResult:
    candidates: list[dict[str, Any]] = []
    errors: list[str] = []
    for key in build_search_keys(source_name):
        try:
            candidates.extend(fetch_candidates(session, key))
        except Exception as exc:
            errors.append(f"{key}: {exc}")
    candidates = dedupe_candidates(candidates)
    if not candidates and errors:
        raise RuntimeError(errors[0])

    manual_override = MANUAL_OVERRIDE_MAP.get(source_name)
    if manual_override:
        manual_code = str(manual_override["fund_code"])
        manual_candidate = next(
            (item for item in candidates if sanitize_fund_code(item.get("CODE")) == manual_code),
            None,
        )
        if manual_candidate is not None:
            return build_match_result(
                source_name=source_name,
                candidate=manual_candidate,
                candidates=candidates,
                match_method="manual_verified_candidate",
                confidence="high",
                note="人工校验后命中候选代码",
            )
        return build_manual_override_result(
            source_name=source_name,
            fund_code=manual_code,
            standard_name=manual_override.get("standard_name"),
            candidates=candidates,
            note="人工校验指定历史代码",
        )

    exact_name = [item for item in candidates if normalize_text(item.get("NAME")) == source_name]
    matched_exact_name = select_unique_candidate(exact_name)
    if matched_exact_name is not None:
        return build_match_result(
            source_name=source_name,
            candidate=matched_exact_name,
            candidates=candidates,
            match_method="eastmoney_exact_name",
            confidence="high",
            note="唯一正式名称命中",
        )
    if len(exact_name) > 1:
        return MatchResult(
            source_name=source_name,
            matched=False,
            fund_code=None,
            standard_name=None,
            match_method=None,
            confidence=None,
            note="正式名称多结果，放弃补码",
            raw_candidates=[candidate_snapshot(item) for item in exact_name[:10]],
        )

    exact_other = [item for item in candidates if source_name in parse_aliases(item)]
    matched_exact_other = select_unique_candidate(exact_other, allow_prefer_front_end=True)
    if matched_exact_other is not None:
        multiple_codes = len({sanitize_fund_code(item.get("CODE")) for item in exact_other if sanitize_fund_code(item.get("CODE"))}) > 1
        return build_match_result(
            source_name=source_name,
            candidate=matched_exact_other,
            candidates=candidates,
            match_method="eastmoney_exact_othername_front_end" if multiple_codes else "eastmoney_exact_othername",
            confidence="medium",
            note="别名仅前后端重复，优先前端份额" if multiple_codes else "唯一别名命中",
        )
    if len(exact_other) > 1:
        return MatchResult(
            source_name=source_name,
            matched=False,
            fund_code=None,
            standard_name=None,
            match_method=None,
            confidence=None,
            note="别名多结果，放弃补码",
            raw_candidates=[candidate_snapshot(item) for item in exact_other[:10]],
        )

    source_key = canonical_key(source_name)
    canonical_hits = [
        item
        for item in candidates
        if source_key is not None and any(canonical_key(text) == source_key for text in candidate_texts(item))
    ]
    matched_canonical = select_unique_candidate(canonical_hits, allow_prefer_front_end=True)
    if matched_canonical is not None:
        multiple_codes = len({sanitize_fund_code(item.get("CODE")) for item in canonical_hits if sanitize_fund_code(item.get("CODE"))}) > 1
        return build_match_result(
            source_name=source_name,
            candidate=matched_canonical,
            candidates=candidates,
            match_method="eastmoney_canonical_unique_front_end" if multiple_codes else "eastmoney_canonical_unique",
            confidence="medium",
            note="规范化后仅前后端重复，优先前端份额" if multiple_codes else "规范化后唯一命中",
        )

    source_core, source_suffix = split_canonical_core_and_suffix(source_name)
    containment_hits = []
    if source_core and len(source_core) >= 4:
        for item in candidates:
            matched = False
            for text in candidate_texts(item):
                candidate_core, candidate_suffix = split_canonical_core_and_suffix(text)
                if candidate_core is None:
                    continue
                if source_suffix and candidate_suffix and candidate_suffix != source_suffix:
                    continue
                if source_core in candidate_core or candidate_core in source_core:
                    matched = True
                    break
            if matched:
                containment_hits.append(item)
    matched_containment = select_unique_fuzzy_candidate(source_name, containment_hits)
    if matched_containment is not None:
        multiple_codes = len({sanitize_fund_code(item.get("CODE")) for item in containment_hits if sanitize_fund_code(item.get("CODE"))}) > 1
        return build_match_result(
            source_name=source_name,
            candidate=matched_containment,
            candidates=candidates,
            match_method="eastmoney_canonical_contains_front_end" if multiple_codes else "eastmoney_canonical_contains",
            confidence="medium",
            note="规范化包含和相似度复核后唯一命中",
        )

    return MatchResult(
        source_name=source_name,
        matched=False,
        fund_code=None,
        standard_name=None,
        match_method=None,
        confidence=None,
        note="未找到唯一可靠匹配",
        raw_candidates=[candidate_snapshot(item) for item in candidates[:10]],
    )


def upsert_aliases(conn: sqlite3.Connection, rows: list[MatchResult], update_time: str) -> None:
    for row in rows:
        if not row.matched or not row.fund_code:
            continue
        conn.execute(
            f'''
            INSERT INTO "{TABLE_ALIAS}" (
                "映射名称", "基金代码", "标准基金名称", "匹配方式", "匹配来源", "置信度", "更新时间"
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT("映射名称") DO UPDATE SET
                "基金代码"=excluded."基金代码",
                "标准基金名称"=excluded."标准基金名称",
                "匹配方式"=excluded."匹配方式",
                "匹配来源"=excluded."匹配来源",
                "置信度"=excluded."置信度",
                "更新时间"=excluded."更新时间"
            ''',
            [
                row.source_name,
                row.fund_code,
                row.standard_name,
                row.match_method,
                "eastmoney_search_api",
                row.confidence,
                update_time,
            ],
        )


def delete_aliases_for_names(conn: sqlite3.Connection, source_names: list[str]) -> None:
    if not source_names:
        return
    placeholders = ",".join("?" for _ in source_names)
    conn.execute(f'DELETE FROM "{TABLE_ALIAS}" WHERE "映射名称" IN ({placeholders})', source_names)


def write_report(output_dir: Path, channel_id: str, run_id: str, results: list[MatchResult], summary: Counter[str]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / f"{run_id}_{channel_id}.json"
    payload = {
        "channel_id": channel_id,
        "run_id": run_id,
        "generated_at": now_local(),
        "summary": dict(summary),
        "matched": [
            {
                "source_name": row.source_name,
                "fund_code": row.fund_code,
                "standard_name": row.standard_name,
                "match_method": row.match_method,
                "confidence": row.confidence,
                "note": row.note,
            }
            for row in results
            if row.matched
        ],
        "unmatched": [
            {
                "source_name": row.source_name,
                "note": row.note,
                "raw_candidates": row.raw_candidates,
            }
            for row in results
            if not row.matched
        ],
    }
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    args = parse_args()
    conn = sqlite3.connect(args.db_path)
    ensure_alias_table(conn)
    names = discover_missing_names(conn, args.channel_id, args.refresh)
    if args.limit and args.limit > 0:
        names = names[: args.limit]
    if not names:
        print("no_missing_names")
        conn.close()
        return

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    results: list[MatchResult] = []
    summary: Counter[str] = Counter()
    sleep_seconds = max(args.sleep_ms, 0) / 1000.0

    for index, name in enumerate(names, start=1):
        try:
            result = resolve_name(session, name)
        except Exception as exc:
            result = MatchResult(
                source_name=name,
                matched=False,
                fund_code=None,
                standard_name=None,
                match_method=None,
                confidence=None,
                note=f"请求失败: {exc}",
                raw_candidates=[],
            )
        results.append(result)
        key = result.match_method if result.matched else result.note or "unmatched"
        summary[key] += 1
        if index % 50 == 0 or index == len(names):
            print(f"progress {index}/{len(names)} {dict(summary)}")
        if sleep_seconds:
            time.sleep(sleep_seconds)

    update_time = now_local()
    matched_rows = [row for row in results if row.matched and row.fund_code]
    if args.refresh:
        delete_aliases_for_names(conn, names)
    upsert_aliases(conn, matched_rows, update_time)
    conn.commit()

    run_id = datetime.now().strftime("%Y%m%dT%H%M%S")
    write_report(args.output_dir, args.channel_id, run_id, results, summary)

    print(f"channel_id={args.channel_id}")
    print(f"missing_name_total={len(names)}")
    print(f"matched_total={len(matched_rows)}")
    print(f"inserted_table={TABLE_ALIAS}")
    print(f"db={args.db_path.resolve()}")
    print(f"report_dir={args.output_dir.resolve()}")
    conn.close()


if __name__ == "__main__":
    main()
