from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "fund_standard_classification"
DEFAULT_VERIFIED_OVERRIDES_PATH = PROJECT_ROOT / "config" / "基金分类人工核验.json"

FUND_CODE_SEARCH_URL = "https://fund.eastmoney.com/js/fundcode_search.js"
FUND_SUGGEST_URL = "https://fundsuggest.eastmoney.com/FundSearch/api/FundSearchAPI.ashx"
USER_AGENT = "Mozilla/5.0 advisor-monitor/0.1"

TABLE_NAME = "基金标准分类字典"


@dataclass(frozen=True)
class CatalogFund:
    fund_code: str
    search_abbr: str | None
    fund_name: str | None
    eastmoney_type: str | None
    pinyin: str | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="从天天基金公开数据源补充基金标准分类字典，用于投顾策略分类。"
    )
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--timeout-sec", type=int, default=15)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--progress-every", type=int, default=100)
    parser.add_argument(
        "--skip-suggest",
        action="store_true",
        help="Skip fundsuggest calls and export from the current catalog/dictionary state.",
    )
    parser.add_argument(
        "--only-missing-company",
        action="store_true",
        help="Only call fundsuggest for current-fund rows that still lack company data in the dictionary.",
    )
    parser.add_argument("--limit", type=int, default=None, help="限制增强 fundsuggest 的基金数量，用于试跑。")
    parser.add_argument(
        "--scope",
        choices=["current", "all"],
        default="current",
        help="funsuggest 增强范围：current 只增强当前库使用基金；all 增强天天基金全量代码表。",
    )
    parser.add_argument(
        "--no-update-fund-info",
        action="store_true",
        help="不回填 基金信息 中缺失的基金类型/基金公司。",
    )
    parser.add_argument(
        "--verified-overrides",
        type=Path,
        default=DEFAULT_VERIFIED_OVERRIDES_PATH,
        help="人工核验基金分类覆盖配置；用于保留有明确公开证据的特殊基金分类。",
    )
    return parser.parse_args()


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def normalize_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def sanitize_fund_code(value: Any) -> str | None:
    text = str(value or "").strip()
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) == 6:
        return digits
    if digits and len(digits) < 6:
        return digits.zfill(6)
    return None


def bool_int(value: bool) -> int:
    return 1 if value else 0


def request_text(session: requests.Session, url: str, *, timeout: int, retries: int, params: dict[str, Any] | None = None) -> str:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = session.get(url, params=params, timeout=timeout)
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding or resp.encoding
            return resp.text
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt < retries:
                time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(f"request failed: {url}") from last_error


def fetch_eastmoney_catalog(timeout: int, retries: int) -> dict[str, CatalogFund]:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Referer": "https://fund.eastmoney.com/"})
    text = request_text(session, FUND_CODE_SEARCH_URL, timeout=timeout, retries=retries)
    match = re.search(r"var\s+r\s*=\s*(\[.*\])\s*;?\s*$", text, re.S)
    if not match:
        raise RuntimeError("cannot parse fundcode_search.js")
    rows = json.loads(match.group(1))
    result: dict[str, CatalogFund] = {}
    for row in rows:
        if not isinstance(row, list) or len(row) < 5:
            continue
        code = sanitize_fund_code(row[0])
        if not code:
            continue
        result[code] = CatalogFund(
            fund_code=code,
            search_abbr=normalize_text(row[1]),
            fund_name=normalize_text(row[2]),
            eastmoney_type=normalize_text(row[3]),
            pinyin=normalize_text(row[4]),
        )
    return result


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        f'''
        CREATE TABLE IF NOT EXISTS "{TABLE_NAME}" (
            "基金代码" TEXT PRIMARY KEY,
            "标准基金名称" TEXT,
            "拼音缩写" TEXT,
            "拼音全称" TEXT,
            "天天基金细分类" TEXT,
            "天天基金大类" TEXT,
            "天天基金二级分类" TEXT,
            "基金公司" TEXT,
            "基金公司ID" TEXT,
            "基金经理" TEXT,
            "基金经理ID" TEXT,
            "其他名称JSON" TEXT,
            "主题标签JSON" TEXT,
            "是否当前库使用" INTEGER NOT NULL DEFAULT 0,
            "是否货币基金" INTEGER NOT NULL DEFAULT 0,
            "是否债券基金" INTEGER NOT NULL DEFAULT 0,
            "是否权益基金" INTEGER NOT NULL DEFAULT 0,
            "是否混合基金" INTEGER NOT NULL DEFAULT 0,
            "是否指数基金" INTEGER NOT NULL DEFAULT 0,
            "是否ETF" INTEGER NOT NULL DEFAULT 0,
            "是否ETF联接" INTEGER NOT NULL DEFAULT 0,
            "是否指数增强" INTEGER NOT NULL DEFAULT 0,
            "是否QDII" INTEGER NOT NULL DEFAULT 0,
            "是否FOF" INTEGER NOT NULL DEFAULT 0,
            "是否LOF" INTEGER NOT NULL DEFAULT 0,
            "是否REITs" INTEGER NOT NULL DEFAULT 0,
            "是否商品黄金" INTEGER NOT NULL DEFAULT 0,
            "是否短债" INTEGER NOT NULL DEFAULT 0,
            "是否纯债" INTEGER NOT NULL DEFAULT 0,
            "是否可转债" INTEGER NOT NULL DEFAULT 0,
            "标准资产大类" TEXT,
            "标准资产细类" TEXT,
            "市场地域标签" TEXT,
            "主动被动标签" TEXT,
            "投顾资产分类桶" TEXT,
            "跟踪指数_名称推断" TEXT,
            "分类来源" TEXT NOT NULL,
            "增强来源" TEXT,
            "置信度" TEXT NOT NULL,
            "最近更新时间" TEXT NOT NULL
        )
        '''
    )
    conn.execute(f'CREATE INDEX IF NOT EXISTS "idx_{TABLE_NAME}_大类" ON "{TABLE_NAME}"("标准资产大类")')
    conn.execute(f'CREATE INDEX IF NOT EXISTS "idx_{TABLE_NAME}_投顾桶" ON "{TABLE_NAME}"("投顾资产分类桶")')
    conn.execute(f'CREATE INDEX IF NOT EXISTS "idx_{TABLE_NAME}_当前使用" ON "{TABLE_NAME}"("是否当前库使用")')


def discover_current_funds(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        '''
        WITH codes AS (
            SELECT "基金代码" FROM "基金信息"
            UNION
            SELECT "基金代码" FROM "基金日度净值"
            UNION
            SELECT "基金代码" FROM "策略当前持仓"
            UNION
            SELECT "基金代码" FROM "策略调仓明细"
        )
        SELECT
            c."基金代码" AS fund_code,
            MAX(NULLIF(TRIM(i."基金名称"), '')) AS info_name,
            MAX(NULLIF(TRIM(i."基金类型"), '')) AS info_type,
            MAX(NULLIF(TRIM(i."基金公司"), '')) AS info_company
        FROM codes c
        LEFT JOIN "基金信息" i ON i."基金代码" = c."基金代码"
        WHERE c."基金代码" IS NOT NULL AND TRIM(c."基金代码") <> ''
        GROUP BY c."基金代码"
        '''
    ).fetchall()
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        code = sanitize_fund_code(row[0])
        if code:
            result[code] = {"fund_name": row[1], "fund_type": row[2], "fund_company": row[3]}
    return result


def split_type(value: str | None) -> tuple[str | None, str | None]:
    text = normalize_text(value)
    if not text:
        return None, None
    if "-" in text:
        major, sub = text.split("-", 1)
        return normalize_text(major), normalize_text(sub)
    return text, None


def infer_region(name: str, fund_type: str) -> str:
    text = f"{name} {fund_type}".upper()
    if any(token in text for token in ["QDII", "海外", "全球", "环球"]):
        if any(token in text for token in ["香港", "港股", "沪港深", "恒生", "大中华", "中国香港"]):
            return "港股/大中华"
        if any(token in text for token in ["美国", "标普", "纳斯达克", "NASDAQ", "S&P", "SP500", "500ETF"]):
            return "美国"
        if any(token in text for token in ["印度"]):
            return "印度"
        if any(token in text for token in ["欧洲", "德国", "DAX"]):
            return "欧洲"
        if any(token in text for token in ["亚太", "亚洲", "日本"]):
            return "亚太"
        return "全球/海外"
    if any(token in text for token in ["香港", "港股", "沪港深", "恒生"]):
        return "港股/大中华"
    return "国内"


def infer_tracking_index(name: str, fund_type: str) -> str | None:
    text = name or ""
    if "指数" not in text and "ETF" not in text and "联接" not in text and "LOF" not in text and "指数" not in fund_type:
        return None
    patterns = [
        r"(中证[0-9A-Za-z一-龥]+?)(?:ETF|指数|联接|增强|发起|A|C|I|Y|人民币|美元|基金|$)",
        r"(沪深300)",
        r"(上证[0-9A-Za-z一-龥]+?)(?:ETF|指数|联接|增强|发起|A|C|基金|$)",
        r"(创业板[0-9A-Za-z一-龥]*?)(?:ETF|指数|联接|增强|A|C|基金|$)",
        r"(科创[0-9A-Za-z一-龥]*?)(?:ETF|指数|联接|增强|A|C|基金|$)",
        r"(标普[0-9A-Za-z一-龥]+?)(?:ETF|指数|联接|A|C|人民币|美元|基金|$)",
        r"(纳斯达克[0-9A-Za-z一-龥]*?)(?:ETF|指数|联接|A|C|人民币|美元|基金|$)",
        r"(恒生[0-9A-Za-z一-龥]*?)(?:ETF|指数|联接|A|C|基金|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return None


def derive_classification(name: str | None, eastmoney_type: str | None, fallback_type: str | None) -> dict[str, Any]:
    fund_name = name or ""
    type_text = eastmoney_type or fallback_type or ""
    major, sub = split_type(type_text)
    full = f"{fund_name} {type_text}".upper()

    is_money = "货币" in type_text or "货币" in fund_name
    is_bond = "债券" in type_text or "债" in type_text or "债券" in fund_name or "短债" in fund_name or "纯债" in fund_name
    is_equity = "股票" in type_text or "股票" in fund_name or "权益" in type_text or "权益" in fund_name
    is_mixed = "混合" in type_text or "混合" in fund_name
    is_index = "指数" in type_text or "指数" in fund_name or "ETF" in full or "联接" in fund_name
    is_etf = "ETF" in full
    is_link = "ETF联接" in fund_name or "联接" in fund_name or "联结" in fund_name
    is_enhanced = "增强" in fund_name or "增强" in type_text
    is_qdii = "QDII" in full or "海外" in type_text or "海外" in fund_name
    is_fof = "FOF" in full or "基金中基金" in fund_name
    is_lof = "LOF" in full
    is_reits = "REIT" in full or "REITS" in full or "基础设施" in fund_name
    is_commodity = any(token in fund_name for token in ["黄金", "白银", "原油", "油气", "商品"]) or "商品" in type_text
    is_short_bond = "短债" in fund_name or "中短债" in type_text
    is_pure_bond = "纯债" in fund_name or "长债" in type_text or type_text == "债券型-长债"
    is_convertible = "可转债" in fund_name or "转债" in fund_name

    if is_reits:
        asset_major = "另类"
        asset_sub = "REITs"
        bucket = "商品/另类"
    elif is_commodity:
        asset_major = "另类"
        asset_sub = "商品/黄金"
        bucket = "商品/另类"
    elif is_qdii:
        asset_major = "海外"
        if is_bond:
            asset_sub = "海外债券"
        elif is_commodity:
            asset_sub = "海外商品"
        elif is_fof:
            asset_sub = "QDII-FOF"
        else:
            asset_sub = "海外权益/混合"
        bucket = "海外/QDII"
    elif is_money:
        asset_major = "现金"
        asset_sub = "货币基金"
        bucket = "现金类"
    elif is_bond and not is_mixed and not is_equity:
        asset_major = "债券"
        if is_short_bond:
            asset_sub = "短债"
        elif is_convertible:
            asset_sub = "可转债"
        elif is_pure_bond:
            asset_sub = "纯债/长债"
        elif sub:
            asset_sub = f"债券-{sub}"
        else:
            asset_sub = "普通债券"
        bucket = "债券类"
    elif is_equity and not is_mixed:
        asset_major = "权益"
        asset_sub = "指数权益" if is_index else "主动权益"
        bucket = "权益类"
    elif is_fof:
        asset_major = "多资产"
        asset_sub = type_text or "FOF"
        bucket = "FOF/多资产"
    elif is_mixed:
        asset_major = "混合"
        asset_sub = sub or "混合"
        bucket = "混合/多资产"
    elif is_index:
        asset_major = "权益"
        asset_sub = "指数"
        bucket = "权益类"
    else:
        asset_major = "其他"
        asset_sub = type_text or None
        bucket = "其他/未知"

    if is_enhanced:
        active_passive = "指数增强/策略指数"
    elif is_index or is_etf or is_link:
        active_passive = "被动指数"
    elif is_fof:
        active_passive = "FOF配置"
    else:
        active_passive = "主动管理"

    confidence = "高" if eastmoney_type else ("中" if fallback_type else "低")
    return {
        "major_type": major,
        "sub_type": sub,
        "is_money": bool_int(is_money),
        "is_bond": bool_int(is_bond),
        "is_equity": bool_int(is_equity),
        "is_mixed": bool_int(is_mixed),
        "is_index": bool_int(is_index),
        "is_etf": bool_int(is_etf),
        "is_link": bool_int(is_link),
        "is_enhanced": bool_int(is_enhanced),
        "is_qdii": bool_int(is_qdii),
        "is_fof": bool_int(is_fof),
        "is_lof": bool_int(is_lof),
        "is_reits": bool_int(is_reits),
        "is_commodity": bool_int(is_commodity),
        "is_short_bond": bool_int(is_short_bond),
        "is_pure_bond": bool_int(is_pure_bond),
        "is_convertible": bool_int(is_convertible),
        "asset_major": asset_major,
        "asset_sub": asset_sub,
        "region": infer_region(fund_name, type_text),
        "active_passive": active_passive,
        "advisor_bucket": bucket,
        "tracking_index_inferred": infer_tracking_index(fund_name, type_text),
        "confidence": confidence,
    }


def fetch_suggest_one(code: str, timeout: int, retries: int) -> tuple[str, dict[str, Any] | None, str | None]:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Referer": "https://fund.eastmoney.com/"})
    params = {"m": "1", "key": code}
    try:
        text = request_text(session, FUND_SUGGEST_URL, timeout=timeout, retries=retries, params=params)
        payload = json.loads(text)
        for item in payload.get("Datas") or []:
            if str(item.get("CODE") or item.get("_id") or "").strip() == code:
                return code, item, None
        return code, None, "not_found"
    except Exception as exc:  # noqa: BLE001
        return code, None, str(exc)


def enrich_suggest(
    codes: list[str],
    workers: int,
    timeout: int,
    retries: int,
    progress_every: int,
    label: str,
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    result: dict[str, dict[str, Any]] = {}
    errors: dict[str, str] = {}
    total = len(codes)
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        future_map = {
            executor.submit(fetch_suggest_one, code, timeout, retries): code
            for code in codes
        }
        for done, future in enumerate(as_completed(future_map), 1):
            code, item, error = future.result()
            if item:
                result[code] = item
            elif error:
                errors[code] = error
            if progress_every > 0 and (done % progress_every == 0 or done == total):
                print(
                    f"[{label}] {done}/{total} done, success={len(result)}, errors={len(errors)}",
                    flush=True,
                )
    return result, errors


def parse_suggest(item: dict[str, Any] | None) -> dict[str, Any]:
    if not item:
        return {
            "company": None,
            "company_id": None,
            "managers": None,
            "manager_ids": None,
            "other_names": [],
            "themes": [],
            "suggest_type": None,
        }
    base = item.get("FundBaseInfo") or {}
    other_names = [part.strip() for part in str(base.get("OTHERNAME") or "").split(",") if part.strip()]
    themes = []
    for tag in item.get("ZTJJInfo") or []:
        name = normalize_text(tag.get("TTYPENAME"))
        if name:
            themes.append({"主题代码": tag.get("TTYPE"), "主题名称": name})
    return {
        "company": normalize_text(base.get("JJGS")),
        "company_id": normalize_text(base.get("JJGSID")),
        "managers": normalize_text(base.get("JJJL")),
        "manager_ids": normalize_text(base.get("JJJLID")),
        "other_names": other_names,
        "themes": themes,
        "suggest_type": normalize_text(base.get("FTYPE")),
    }


def load_verified_overrides(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("基金分类人工核验") or []
    result: dict[str, dict[str, Any]] = {}
    for item in rows:
        code = sanitize_fund_code(item.get("基金代码"))
        if not code:
            continue
        fields = item.get("字段") or {}
        if not isinstance(fields, dict):
            continue
        result[code] = {
            "fields": fields,
            "evidence": item.get("核验依据") or [],
        }
    return result


def upsert_rows(
    conn: sqlite3.Connection,
    catalog: dict[str, CatalogFund],
    current_funds: dict[str, dict[str, Any]],
    suggest: dict[str, dict[str, Any]],
    verified_overrides: dict[str, dict[str, Any]],
    timestamp: str,
) -> list[dict[str, Any]]:
    all_codes = sorted(set(catalog) | set(current_funds) | set(verified_overrides))
    rows_for_csv: list[dict[str, Any]] = []
    sql = f'''
        INSERT INTO "{TABLE_NAME}" (
            "基金代码", "标准基金名称", "拼音缩写", "拼音全称", "天天基金细分类", "天天基金大类", "天天基金二级分类",
            "基金公司", "基金公司ID", "基金经理", "基金经理ID", "其他名称JSON", "主题标签JSON", "是否当前库使用",
            "是否货币基金", "是否债券基金", "是否权益基金", "是否混合基金", "是否指数基金", "是否ETF", "是否ETF联接",
            "是否指数增强", "是否QDII", "是否FOF", "是否LOF", "是否REITs", "是否商品黄金", "是否短债", "是否纯债",
            "是否可转债", "标准资产大类", "标准资产细类", "市场地域标签", "主动被动标签", "投顾资产分类桶",
            "跟踪指数_名称推断", "分类来源", "增强来源", "置信度", "最近更新时间"
        ) VALUES ({",".join(["?"] * 40)})
        ON CONFLICT("基金代码") DO UPDATE SET
            "标准基金名称"=excluded."标准基金名称",
            "拼音缩写"=excluded."拼音缩写",
            "拼音全称"=excluded."拼音全称",
            "天天基金细分类"=excluded."天天基金细分类",
            "天天基金大类"=excluded."天天基金大类",
            "天天基金二级分类"=excluded."天天基金二级分类",
            "基金公司"=COALESCE(NULLIF(excluded."基金公司", ''), "基金公司"),
            "基金公司ID"=COALESCE(NULLIF(excluded."基金公司ID", ''), "基金公司ID"),
            "基金经理"=COALESCE(NULLIF(excluded."基金经理", ''), "基金经理"),
            "基金经理ID"=COALESCE(NULLIF(excluded."基金经理ID", ''), "基金经理ID"),
            "其他名称JSON"=CASE
                WHEN excluded."其他名称JSON" IS NOT NULL AND excluded."其他名称JSON" <> '[]' THEN excluded."其他名称JSON"
                ELSE "其他名称JSON"
            END,
            "主题标签JSON"=CASE
                WHEN excluded."主题标签JSON" IS NOT NULL AND excluded."主题标签JSON" <> '[]' THEN excluded."主题标签JSON"
                ELSE "主题标签JSON"
            END,
            "是否当前库使用"=excluded."是否当前库使用",
            "是否货币基金"=excluded."是否货币基金",
            "是否债券基金"=excluded."是否债券基金",
            "是否权益基金"=excluded."是否权益基金",
            "是否混合基金"=excluded."是否混合基金",
            "是否指数基金"=excluded."是否指数基金",
            "是否ETF"=excluded."是否ETF",
            "是否ETF联接"=excluded."是否ETF联接",
            "是否指数增强"=excluded."是否指数增强",
            "是否QDII"=excluded."是否QDII",
            "是否FOF"=excluded."是否FOF",
            "是否LOF"=excluded."是否LOF",
            "是否REITs"=excluded."是否REITs",
            "是否商品黄金"=excluded."是否商品黄金",
            "是否短债"=excluded."是否短债",
            "是否纯债"=excluded."是否纯债",
            "是否可转债"=excluded."是否可转债",
            "标准资产大类"=excluded."标准资产大类",
            "标准资产细类"=excluded."标准资产细类",
            "市场地域标签"=excluded."市场地域标签",
            "主动被动标签"=excluded."主动被动标签",
            "投顾资产分类桶"=excluded."投顾资产分类桶",
            "跟踪指数_名称推断"=excluded."跟踪指数_名称推断",
            "分类来源"=excluded."分类来源",
            "增强来源"=COALESCE(excluded."增强来源", "增强来源"),
            "置信度"=excluded."置信度",
            "最近更新时间"=excluded."最近更新时间"
    '''
    for code in all_codes:
        cat = catalog.get(code)
        local = current_funds.get(code) or {}
        sug = parse_suggest(suggest.get(code))
        fund_name = (cat.fund_name if cat else None) or local.get("fund_name")
        eastmoney_type = cat.eastmoney_type if cat else None
        fallback_type = sug.get("suggest_type") or local.get("fund_type")
        derived = derive_classification(fund_name, eastmoney_type, fallback_type)
        company = sug.get("company") or local.get("fund_company")
        raw_type = eastmoney_type or fallback_type
        class_source = "天天基金_fundcode_search" if cat else "本地基金信息/名称推断"
        enhance_source = "天天基金_fundsuggest" if code in suggest else None
        row = {
            "基金代码": code,
            "标准基金名称": fund_name,
            "拼音缩写": cat.search_abbr if cat else None,
            "拼音全称": cat.pinyin if cat else None,
            "天天基金细分类": raw_type,
            "天天基金大类": derived["major_type"],
            "天天基金二级分类": derived["sub_type"],
            "基金公司": company,
            "基金公司ID": sug.get("company_id"),
            "基金经理": sug.get("managers"),
            "基金经理ID": sug.get("manager_ids"),
            "其他名称JSON": json.dumps(sug.get("other_names") or [], ensure_ascii=False),
            "主题标签JSON": json.dumps(sug.get("themes") or [], ensure_ascii=False),
            "是否当前库使用": bool_int(code in current_funds),
            "是否货币基金": derived["is_money"],
            "是否债券基金": derived["is_bond"],
            "是否权益基金": derived["is_equity"],
            "是否混合基金": derived["is_mixed"],
            "是否指数基金": derived["is_index"],
            "是否ETF": derived["is_etf"],
            "是否ETF联接": derived["is_link"],
            "是否指数增强": derived["is_enhanced"],
            "是否QDII": derived["is_qdii"],
            "是否FOF": derived["is_fof"],
            "是否LOF": derived["is_lof"],
            "是否REITs": derived["is_reits"],
            "是否商品黄金": derived["is_commodity"],
            "是否短债": derived["is_short_bond"],
            "是否纯债": derived["is_pure_bond"],
            "是否可转债": derived["is_convertible"],
            "标准资产大类": derived["asset_major"],
            "标准资产细类": derived["asset_sub"],
            "市场地域标签": derived["region"],
            "主动被动标签": derived["active_passive"],
            "投顾资产分类桶": derived["advisor_bucket"],
            "跟踪指数_名称推断": derived["tracking_index_inferred"],
            "分类来源": class_source,
            "增强来源": enhance_source,
            "置信度": derived["confidence"],
            "最近更新时间": timestamp,
        }
        override = verified_overrides.get(code)
        if override:
            for key, value in (override.get("fields") or {}).items():
                if key in row:
                    row[key] = value
            row["分类来源"] = "人工核验公开资料"
            row["增强来源"] = "基金分类人工核验.json"
            row["置信度"] = "高"
            row["最近更新时间"] = timestamp
        values = [row[key] for key in row]
        conn.execute(sql, values)
        rows_for_csv.append(row)
    return rows_for_csv


def update_fund_info(conn: sqlite3.Connection) -> dict[str, int]:
    before = conn.total_changes
    conn.execute(
        f'''
        UPDATE "基金信息"
        SET
            "基金名称" = COALESCE(NULLIF(TRIM("基金信息"."基金名称"), ''), (
                SELECT "标准基金名称" FROM "{TABLE_NAME}" d WHERE d."基金代码" = "基金信息"."基金代码"
            )),
            "基金公司" = COALESCE(NULLIF(TRIM("基金信息"."基金公司"), ''), (
                SELECT "基金公司" FROM "{TABLE_NAME}" d WHERE d."基金代码" = "基金信息"."基金代码"
            )),
            "基金类型" = COALESCE(NULLIF(TRIM("基金信息"."基金类型"), ''), (
                SELECT "天天基金细分类" FROM "{TABLE_NAME}" d WHERE d."基金代码" = "基金信息"."基金代码"
            )),
            "跟踪指数" = COALESCE(NULLIF(TRIM("基金信息"."跟踪指数"), ''), (
                SELECT "跟踪指数_名称推断" FROM "{TABLE_NAME}" d WHERE d."基金代码" = "基金信息"."基金代码"
            )),
            "主题标签JSON" = COALESCE(NULLIF(TRIM("基金信息"."主题标签JSON"), ''), (
                SELECT "主题标签JSON" FROM "{TABLE_NAME}" d
                WHERE d."基金代码" = "基金信息"."基金代码" AND d."主题标签JSON" <> '[]'
            )),
            "最近更新时间" = (
                SELECT "最近更新时间" FROM "{TABLE_NAME}" d WHERE d."基金代码" = "基金信息"."基金代码"
            )
        WHERE EXISTS (SELECT 1 FROM "{TABLE_NAME}" d WHERE d."基金代码" = "基金信息"."基金代码")
        '''
    )
    return {"fund_info_rows_touched": conn.total_changes - before}


def export_outputs(output_dir: Path, rows: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "fund_standard_classification.csv"
    if rows:
        with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


def load_dictionary_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    cursor = conn.execute(f'SELECT * FROM "{TABLE_NAME}" ORDER BY "基金代码"')
    columns = [desc[0] for desc in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def db_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    def one(sql: str) -> dict[str, Any]:
        conn.row_factory = sqlite3.Row
        return dict(conn.execute(sql).fetchone())

    def many(sql: str) -> list[dict[str, Any]]:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(sql).fetchall()]

    return {
        "dictionary_coverage": one(
            f'''
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN "是否当前库使用"=1 THEN 1 ELSE 0 END) AS current_used,
                SUM(CASE WHEN "天天基金细分类" IS NOT NULL THEN 1 ELSE 0 END) AS has_type,
                SUM(CASE WHEN "基金公司" IS NOT NULL THEN 1 ELSE 0 END) AS has_company,
                SUM(CASE WHEN "基金经理" IS NOT NULL THEN 1 ELSE 0 END) AS has_manager,
                SUM(CASE WHEN "主题标签JSON" IS NOT NULL AND "主题标签JSON" <> '[]' THEN 1 ELSE 0 END) AS has_theme_tags,
                SUM(CASE WHEN "跟踪指数_名称推断" IS NOT NULL THEN 1 ELSE 0 END) AS has_tracking_index_inferred
            FROM "{TABLE_NAME}"
            '''
        ),
        "current_used_coverage": one(
            f'''
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN "天天基金细分类" IS NOT NULL THEN 1 ELSE 0 END) AS has_type,
                SUM(CASE WHEN "基金公司" IS NOT NULL THEN 1 ELSE 0 END) AS has_company,
                SUM(CASE WHEN "基金经理" IS NOT NULL THEN 1 ELSE 0 END) AS has_manager,
                SUM(CASE WHEN "主题标签JSON" IS NOT NULL AND "主题标签JSON" <> '[]' THEN 1 ELSE 0 END) AS has_theme_tags,
                SUM(CASE WHEN "跟踪指数_名称推断" IS NOT NULL THEN 1 ELSE 0 END) AS has_tracking_index_inferred
            FROM "{TABLE_NAME}"
            WHERE "是否当前库使用"=1
            '''
        ),
        "advisor_bucket_counts_current": many(
            f'''
            SELECT COALESCE("投顾资产分类桶", '未分类') AS bucket, COUNT(*) AS n
            FROM "{TABLE_NAME}"
            WHERE "是否当前库使用"=1
            GROUP BY bucket
            ORDER BY n DESC
            '''
        ),
        "eastmoney_type_top_current": many(
            f'''
            SELECT COALESCE("天天基金细分类", '未匹配') AS type, COUNT(*) AS n
            FROM "{TABLE_NAME}"
            WHERE "是否当前库使用"=1
            GROUP BY type
            ORDER BY n DESC
            LIMIT 30
            '''
        ),
        "flags_current": one(
            f'''
            SELECT
                SUM("是否指数基金") AS index_funds,
                SUM("是否ETF") AS etf_funds,
                SUM("是否ETF联接") AS etf_link_funds,
                SUM("是否指数增强") AS enhanced_index_funds,
                SUM("是否QDII") AS qdii_funds,
                SUM("是否FOF") AS fof_funds,
                SUM("是否REITs") AS reits_funds,
                SUM("是否商品黄金") AS commodity_funds
            FROM "{TABLE_NAME}"
            WHERE "是否当前库使用"=1
            '''
        ),
    }


def main() -> None:
    args = parse_args()
    timestamp = now_iso()
    output_dir = OUTPUT_ROOT / datetime.now().strftime("%Y%m%dT%H%M%S")

    catalog = fetch_eastmoney_catalog(args.timeout_sec, args.retries)
    conn = sqlite3.connect(args.db_path)
    ensure_table(conn)
    current_funds = discover_current_funds(conn)
    verified_overrides = load_verified_overrides(args.verified_overrides)

    if args.scope == "all":
        enrich_codes = sorted(catalog)
    else:
        enrich_codes = sorted(current_funds)
    if args.only_missing_company:
        existing_company_codes = {
            row[0]
            for row in conn.execute(
                f'''
                SELECT "基金代码"
                FROM "{TABLE_NAME}"
                WHERE "是否当前库使用"=1
                  AND NULLIF(TRIM("基金公司"), '') IS NOT NULL
                '''
            )
        }
        enrich_codes = [code for code in enrich_codes if code not in existing_company_codes]
    if args.limit and args.limit > 0:
        enrich_codes = enrich_codes[: args.limit]
    if args.skip_suggest:
        enrich_codes = []

    suggest: dict[str, dict[str, Any]] = {}
    errors: dict[str, str] = {}
    batch_size = max(1, args.batch_size)
    batch_total = (len(enrich_codes) + batch_size - 1) // batch_size
    for batch_no, start in enumerate(range(0, len(enrich_codes), batch_size), 1):
        batch = enrich_codes[start : start + batch_size]
        print(f"[fundsuggest] batch {batch_no}/{batch_total}, codes={len(batch)}", flush=True)
        batch_suggest, batch_errors = enrich_suggest(
            batch,
            args.workers,
            args.timeout_sec,
            args.retries,
            args.progress_every,
            f"fundsuggest batch {batch_no}",
        )
        suggest.update(batch_suggest)
        errors.update(batch_errors)
    upsert_rows(conn, catalog, current_funds, suggest, verified_overrides, timestamp)
    update_summary = {"fund_info_rows_touched": 0}
    if not args.no_update_fund_info:
        update_summary = update_fund_info(conn)
    conn.commit()
    rows = load_dictionary_rows(conn)

    summary = {
        "generated_at": timestamp,
        "db_path": str(args.db_path),
        "output_dir": str(output_dir),
        "eastmoney_catalog_total": len(catalog),
        "current_fund_total": len(current_funds),
        "suggest_target_total": len(enrich_codes),
        "suggest_success_total": len(suggest),
        "suggest_error_total": len(errors),
        "suggest_error_examples": dict(list(errors.items())[:30]),
        "verified_override_total": len(verified_overrides),
        "update_summary": update_summary,
        "db_summary": db_summary(conn),
    }
    export_outputs(output_dir, rows, summary)
    conn.close()

    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
