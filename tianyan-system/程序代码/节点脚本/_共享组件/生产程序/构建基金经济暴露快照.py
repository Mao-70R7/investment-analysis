from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_DB = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "outputs" / "fund_economic_exposure_quality"
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "基金分类穿透规则.yaml"
CN_TZ = timezone(timedelta(hours=8))

HIGH_FUND_OTHER_THRESHOLD = 30.0
MANUAL_REVIEW_THRESHOLD = 30.0

DEFAULT_KEYWORDS = {
    "固定收益": [
        "货币",
        "现金",
        "同业存单",
        "短债",
        "中短债",
        "纯债",
        "债券",
        "债券指数",
        "中债",
        "国债",
        "地方债",
        "国开债",
        "国开",
        "政金债",
        "政策性金融债",
        "农发债",
        "进出债",
        "信用债",
        "利率债",
        "固收",
        "可转债",
    ],
    "黄金商品": ["黄金", "上海金", "商品", "白银", "原油", "油气", "豆粕", "有色", "资源"],
    "QDII债券": ["QDII债", "海外债", "美元债", "中资美元债", "亚洲债", "全球债券", "亚太债"],
    "互认基金权益": ["互认基金"],
    "QDII权益": [
        "QDII",
        "纳斯达克",
        "纳指",
        "标普",
        "S&P",
        "道琼斯",
        "恒生",
        "港股",
        "香港",
        "美股",
        "美国",
        "全球股票",
        "新兴市场",
        "越南",
        "印度",
        "东盟",
    ],
    "权益指数ETF联接": [
        "ETF联接",
        "联接",
        "ETF",
        "指数",
        "沪深300",
        "中证500",
        "中证1000",
        "创业板",
        "科创",
        "双创",
        "红利",
        "央企",
    ],
    "FOF_QDII_FOF": ["FOF", "基金中基金", "养老", "目标日期", "目标风险", "稳健养老", "平衡养老"],
}

GENERIC_KEYS = {"基金", "其他", "其它", "未分类", "未知", "待穿透"}
UNRESOLVED_KEYS = {"其他", "未分类", "多资产FOF", "待穿透FOF"}


@dataclass(frozen=True)
class RuleConfig:
    keywords: dict[str, list[str]]
    high_fund_other_threshold: float = HIGH_FUND_OTHER_THRESHOLD
    manual_review_threshold: float = MANUAL_REVIEW_THRESHOLD


def now_cn() -> str:
    return datetime.now(CN_TZ).isoformat(timespec="seconds")


def safe_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def clean_text(value: Any) -> str:
    return str(value or "").strip()


def normalize_code(value: Any) -> str:
    match = re.search(r"(\d{6})", str(value or ""))
    return match.group(1) if match else ""


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
            (table,),
        ).fetchone()
        is not None
    )


def rows_as_dicts(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def parse_json_object(value: Any) -> dict[str, float]:
    if not value:
        return {}
    try:
        data = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    output: dict[str, float] = {}
    for key, raw in data.items():
        label = str(key).strip()
        if not label or label in {"-", "--", "未识别", "未分类"}:
            continue
        number = safe_float(raw)
        if abs(number) >= 0.0001:
            output[label] = number
    return output


def normalize_json_array_text(value: Any) -> str:
    if not value:
        return "[]"
    try:
        data = json.loads(str(value))
    except json.JSONDecodeError:
        return "[]"
    if not isinstance(data, list):
        return "[]"
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def load_rule_config(config_path: Path = DEFAULT_CONFIG) -> RuleConfig:
    keywords = {key: list(value) for key, value in DEFAULT_KEYWORDS.items()}
    high_threshold = HIGH_FUND_OTHER_THRESHOLD
    manual_threshold = MANUAL_REVIEW_THRESHOLD
    if not config_path.exists():
        return RuleConfig(keywords, high_threshold, manual_threshold)
    try:
        import yaml  # type: ignore
    except ImportError:
        return RuleConfig(keywords, high_threshold, manual_threshold)
    with config_path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    thresholds = data.get("阈值") or {}
    high_threshold = safe_float(thresholds.get("基金其他高占比阈值"), high_threshold)
    manual_threshold = safe_float(thresholds.get("人工补充占比阈值"), manual_threshold)
    for item in data.get("优先级") or []:
        if not isinstance(item, dict):
            continue
        code = clean_text(item.get("规则代码"))
        item_keywords = [clean_text(v) for v in item.get("关键词") or [] if clean_text(v)]
        if not item_keywords:
            continue
        if code == "固收优先":
            keywords["固定收益"] = item_keywords
        elif code == "黄金商品":
            keywords["黄金商品"] = item_keywords
        elif code == "QDII债券":
            keywords["QDII债券"] = item_keywords
        elif code == "互认基金权益":
            keywords["互认基金权益"] = item_keywords
        elif code == "QDII权益":
            keywords["QDII权益"] = item_keywords
        elif code == "权益指数ETF联接":
            keywords["权益指数ETF联接"] = item_keywords
        elif code == "FOF_QDII_FOF":
            keywords["FOF_QDII_FOF"] = item_keywords
    return RuleConfig(keywords, high_threshold, manual_threshold)


def flag(row: dict[str, Any] | None, key: str) -> bool:
    if not row:
        return False
    return safe_float(row.get(key)) > 0


def source_text(
    code: str,
    dictionary_row: dict[str, Any] | None,
    snapshot_row: dict[str, Any] | None,
    holding_row: dict[str, Any] | None,
) -> str:
    pieces = [code]
    for row in (dictionary_row, snapshot_row, holding_row):
        if not row:
            continue
        for key in (
            "标准基金名称",
            "基金名称",
            "天天基金细分类",
            "天天基金大类",
            "天天基金二级分类",
            "标准资产大类",
            "标准资产细类",
            "市场地域标签",
            "主动被动标签",
            "投顾资产分类桶",
            "跟踪指数_名称推断",
            "基金类型",
            "二级分类",
            "资产类型",
            "分组名称",
            "主题标签JSON",
        ):
            value = row.get(key)
            if value:
                pieces.append(str(value))
    return " ".join(pieces)


def matched_keywords(text: str, keywords: list[str]) -> list[str]:
    upper_text = text.upper()
    found = []
    for word in keywords:
        if word and word.upper() in upper_text:
            found.append(word)
    return found


def fixed_match_text(text: str) -> str:
    return text.replace("自由现金流", "自由现流").replace("现金流", "现流")


def is_commodity_equity_theme_text(text: str) -> bool:
    text = clean_text(text)
    if not text:
        return False
    commodity_terms = ("黄金", "贵金属", "白银", "原油", "油气", "有色", "资源", "煤炭")
    equity_context_terms = (
        "股票",
        "股",
        "产业",
        "行业",
        "主题",
        "混合",
        "优选",
        "精选",
        "矿业",
        "金属",
        "能源",
        "开采",
    )
    return any(token in text for token in commodity_terms) and any(token in text for token in equity_context_terms)


def infer_fixed_detail(text: str, dictionary_row: dict[str, Any] | None) -> str:
    text = fixed_match_text(text)
    if any(token in text for token in ("货币", "现金")) or flag(dictionary_row, "是否货币基金"):
        return "货币"
    if "同业存单" in text:
        return "同业存单"
    if any(token in text for token in ("短债", "中短债")) or flag(dictionary_row, "是否短债"):
        return "短债"
    if any(token in text for token in ("海外债", "美元债", "中资美元债", "亚洲债", "全球债券", "亚太债")):
        return "海外债券"
    if any(token in text for token in ("国开", "国开债", "政金", "政金债", "政策性金融债", "农发债", "进出债")):
        return "政策性金融债"
    if "信用债" in text:
        return "信用债"
    if "可转债" in text or flag(dictionary_row, "是否可转债"):
        return "可转债"
    if "纯债" in text or flag(dictionary_row, "是否纯债"):
        return "纯债"
    if any(token in text for token in ("中债", "债券指数", "债指")):
        return "债券指数"
    return "债券"


def fixed_target_asset(detail: str) -> str:
    return {
        "货币": "货币及现金",
        "同业存单": "同业存单",
        "短债": "短债",
        "海外债券": "海外债券",
        "政策性金融债": "政策性金融债",
        "信用债": "信用债",
        "可转债": "可转债",
        "纯债": "债券",
        "债券指数": "债券",
    }.get(detail, "债券")


def infer_commodity_detail(text: str, dictionary_row: dict[str, Any] | None) -> str:
    if is_commodity_equity_theme_text(text):
        return "非商品权益主题"
    if "黄金" in text or "上海金" in text or flag(dictionary_row, "是否商品黄金"):
        return "黄金"
    if "白银" in text:
        return "白银"
    if any(token in text for token in ("原油", "油气")):
        return "原油油气"
    return "商品"


def infer_equity_bucket(text: str, dictionary_row: dict[str, Any] | None) -> str:
    region = clean_text((dictionary_row or {}).get("市场地域标签"))
    text_upper = text.upper()
    if any(token in text for token in ("港股", "香港", "恒生", "H股")):
        return "港股"
    if any(token in text_upper for token in ("NASDAQ", "S&P", "SP500")) or any(
        token in text for token in ("纳斯达克", "纳指", "标普", "道琼斯", "美国", "美股")
    ):
        return "美股"
    if any(token in text for token in ("越南", "印度", "巴西", "东盟", "新兴市场")):
        return "新兴市场"
    if any(token in text for token in ("全球", "海外", "欧洲", "德国", "日本", "发达市场")) or "海外" in region:
        return "海外权益"
    return "A股"


def classify_fund(
    code: str,
    dictionary_row: dict[str, Any] | None,
    snapshot_row: dict[str, Any] | None,
    holding_row: dict[str, Any] | None,
    rules: RuleConfig,
) -> dict[str, Any]:
    text = source_text(code, dictionary_row, snapshot_row, holding_row)
    fixed_text = fixed_match_text(text)
    fixed_hits = matched_keywords(fixed_text, rules.keywords["固定收益"])
    commodity_hits = matched_keywords(text, rules.keywords["黄金商品"])
    commodity_equity_theme = is_commodity_equity_theme_text(text)
    qdii_bond_hits = matched_keywords(text, rules.keywords["QDII债券"])
    mutual_recognition_hits = matched_keywords(text, rules.keywords["互认基金权益"])
    qdii_equity_hits = matched_keywords(text, rules.keywords["QDII权益"])
    equity_index_hits = matched_keywords(text, rules.keywords["权益指数ETF联接"])
    fof_hits = matched_keywords(text, rules.keywords["FOF_QDII_FOF"])

    if fixed_hits or flag(dictionary_row, "是否货币基金") or flag(dictionary_row, "是否债券基金"):
        detail = infer_fixed_detail(fixed_text, dictionary_row)
        return {
            "category": "fixed_income",
            "standard_big": "固收",
            "standard_small": detail,
            "target_asset": fixed_target_asset(detail),
            "matched_rule": "固收优先",
            "matched_keywords": fixed_hits,
        }
    if not commodity_equity_theme and (commodity_hits or flag(dictionary_row, "是否商品黄金")):
        detail = infer_commodity_detail(text, dictionary_row)
        return {
            "category": "commodity",
            "standard_big": "商品",
            "standard_small": detail,
            "target_asset": "黄金" if detail == "黄金" else detail,
            "matched_rule": "黄金商品",
            "matched_keywords": commodity_hits,
        }
    if qdii_bond_hits:
        return {
            "category": "qdii_bond",
            "standard_big": "固收",
            "standard_small": "海外债券",
            "target_asset": "海外债券",
            "matched_rule": "QDII债券",
            "matched_keywords": qdii_bond_hits,
        }
    if mutual_recognition_hits and (
        flag(dictionary_row, "是否权益基金")
        or clean_text((dictionary_row or {}).get("标准资产大类")) == "权益"
    ):
        bucket = infer_equity_bucket(text, dictionary_row)
        return {
            "category": "mutual_recognition_equity",
            "standard_big": "权益",
            "standard_small": bucket if bucket != "A股" else "海外权益",
            "target_asset": bucket if bucket != "A股" else "海外权益",
            "matched_rule": "互认基金权益",
            "matched_keywords": mutual_recognition_hits,
        }
    if flag(dictionary_row, "是否QDII") or qdii_equity_hits:
        bucket = infer_equity_bucket(text, dictionary_row)
        return {
            "category": "qdii_equity",
            "standard_big": "权益",
            "standard_small": bucket if bucket != "A股" else "海外权益",
            "target_asset": bucket if bucket != "A股" else "海外权益",
            "matched_rule": "QDII权益",
            "matched_keywords": qdii_equity_hits,
        }
    if flag(dictionary_row, "是否ETF联接") or flag(dictionary_row, "是否ETF") or flag(dictionary_row, "是否指数基金") or equity_index_hits:
        bucket = infer_equity_bucket(text, dictionary_row)
        return {
            "category": "equity_index",
            "standard_big": "权益",
            "standard_small": "权益指数/ETF联接",
            "target_asset": bucket,
            "matched_rule": "权益指数ETF联接",
            "matched_keywords": equity_index_hits,
        }
    if flag(dictionary_row, "是否FOF") or fof_hits:
        target = "多资产FOF"
        standard_big = "FOF/多资产"
        standard_small = "QDII-FOF" if flag(dictionary_row, "是否QDII") else "FOF"
        if any(token in text for token in ("债", "固收", "货币", "现金")):
            target = "债券"
            standard_big = "固收"
            standard_small = "FOF-固收"
        elif any(token in text for token in ("权益", "股票", "港股", "美股", "纳斯达克", "恒生", "指数")):
            target = infer_equity_bucket(text, dictionary_row)
            standard_big = "权益"
            standard_small = "FOF-权益"
        return {
            "category": "fof",
            "standard_big": standard_big,
            "standard_small": standard_small,
            "target_asset": target,
            "matched_rule": "FOF_QDII_FOF",
            "matched_keywords": fof_hits,
        }

    standard_big = clean_text((dictionary_row or {}).get("标准资产大类")) or clean_text((snapshot_row or {}).get("基金类型")) or "未分类"
    standard_small = clean_text((dictionary_row or {}).get("标准资产细类")) or clean_text((snapshot_row or {}).get("二级分类")) or standard_big
    target_asset = infer_target_from_standard(standard_big, standard_small, text, dictionary_row)
    return {
        "category": "standard",
        "standard_big": standard_big,
        "standard_small": standard_small,
        "target_asset": target_asset,
        "matched_rule": "标准分类兜底",
        "matched_keywords": [],
    }


def infer_target_from_standard(
    standard_big: str,
    standard_small: str,
    text: str,
    dictionary_row: dict[str, Any] | None,
) -> str:
    combined = f"{standard_big} {standard_small} {text}"
    if is_commodity_equity_theme_text(combined):
        return infer_equity_bucket(combined, dictionary_row)
    if any(token in combined for token in ("货币", "现金")):
        return "货币及现金"
    if any(token in combined for token in ("债", "固收", "同业存单")):
        return fixed_target_asset(infer_fixed_detail(combined, dictionary_row))
    if any(token in combined for token in ("黄金", "商品", "原油", "油气", "白银")):
        return "黄金" if "黄金" in combined else "商品"
    if any(token in combined for token in ("权益", "股票", "指数", "ETF", "混合")):
        return infer_equity_bucket(combined, dictionary_row)
    if "FOF" in combined or "基金中基金" in combined:
        return "多资产FOF"
    return "其他"


def translate_asset_key(key: str) -> str:
    text = clean_text(key)
    if not text:
        return "其他"
    if text in GENERIC_KEYS:
        return "基金" if text == "基金" else "其他"
    if any(token in text for token in ("货币", "现金", "存款")):
        return "货币及现金"
    if "海外债" in text:
        return "海外债券"
    if any(token in text for token in ("债", "固收", "同业存单")):
        return "债券"
    if is_commodity_equity_theme_text(text):
        if "港" in text or "H股" in text:
            return "港股"
        if "美股" in text or "美国" in text:
            return "美股"
        return "A股"
    if "黄金" in text:
        return "黄金"
    if any(token in text for token in ("商品", "原油", "油气", "白银")):
        return "商品"
    if "港" in text or "H股" in text:
        return "港股"
    if "美股" in text or "美国" in text:
        return "美股"
    if "新兴" in text:
        return "新兴市场"
    if "海外权益" in text or "发达" in text:
        return "海外权益"
    if "存托" in text:
        return "存托凭证"
    if "股" in text or "权益" in text:
        return "A股"
    return text


def add_weight(target: dict[str, float], key: str, value: float) -> None:
    if abs(value) < 0.0001:
        return
    target[key] = target.get(key, 0.0) + value


def normalize_exposure(exposure: dict[str, float]) -> dict[str, float]:
    cleaned = {key: value for key, value in exposure.items() if key and abs(value) >= 0.0001}
    total = sum(cleaned.values())
    if total <= 0:
        return {}
    return {key: round(value * 100.0 / total, 4) for key, value in sorted(cleaned.items())}


EQUITY_ASSET_KEYS = {"A股", "港股", "美股", "新兴市场", "其他发达市场", "海外权益", "存托凭证", "REIT"}


def equity_asset_share(exposure: dict[str, float]) -> float:
    total = 0.0
    for key, value in exposure.items():
        translated = translate_asset_key(key)
        if translated in EQUITY_ASSET_KEYS or "权益" in translated or "股票" in translated:
            total += safe_float(value)
    return round(total, 4)


def fund_level_industry_exposure(
    asset_exposure: dict[str, float],
    raw_industry_exposure: dict[str, float],
) -> dict[str, float]:
    cleaned: dict[str, float] = {}
    for key, value in raw_industry_exposure.items():
        label = clean_text(key)
        share = safe_float(value)
        if not label or label in {"-", "--", "未识别", "未分类", "其他"} or share <= 0.0001:
            continue
        cleaned[label] = cleaned.get(label, 0.0) + share
    if not cleaned:
        return {}

    equity_share = equity_asset_share(asset_exposure)
    if equity_share <= 0:
        return {}

    industry_total = sum(cleaned.values())
    if industry_total <= 0:
        return {}
    if industry_total <= equity_share + 0.5:
        return {key: round(value, 4) for key, value in sorted(cleaned.items()) if value > 0.0001}

    scaled = {key: value * equity_share / industry_total for key, value in cleaned.items()}
    return {key: round(value, 4) for key, value in sorted(scaled.items()) if value > 0.0001}


THEME_INDUSTRY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("黄金产业链", ("黄金产业", "黄金股票", "黄金股", "贵金属股票", "金矿", "矿业黄金")),
    ("油气产业链", ("油气股票", "油气产业", "石油天然气", "油气开采", "能源股票")),
    ("煤炭", ("煤炭",)),
    ("有色金属", ("有色金属", "稀土", "有色")),
    ("半导体", ("半导体", "芯片", "集成电路")),
    ("人工智能", ("人工智能", "AI")),
    ("互联网", ("互联网", "中概互联", "中国互联网", "海外互联网")),
    ("科技", ("科技", "科创", "纳斯达克", "纳指", "恒生科技")),
    ("医药", ("医药", "医疗", "生物医药", "创新药")),
    ("消费", ("消费", "食品饮料", "白酒", "家电")),
    ("新能源", ("新能源", "光伏", "锂电", "电池", "电动车")),
    ("军工", ("军工", "国防")),
    ("金融", ("银行", "证券", "金融", "保险")),
    ("红利", ("红利", "股息")),
    ("海外宽基", ("标普500", "S&P500", "SP500", "道琼斯", "发达市场", "全球股票")),
    ("港股宽基", ("恒生指数", "港股通", "香港市场")),
    ("A股宽基", ("沪深300", "中证500", "中证1000", "创业板", "上证", "深证", "A500")),
)


def infer_theme_industry_exposure(
    text: str,
    asset_exposure: dict[str, float],
    classification: dict[str, Any],
) -> dict[str, float]:
    equity_share = equity_asset_share(asset_exposure)
    if equity_share < 5:
        return {}
    compact_text = clean_text(text).upper().replace(" ", "")
    if not compact_text:
        return {}

    for label, tokens in THEME_INDUSTRY_RULES:
        if any(clean_text(token).upper().replace(" ", "") in compact_text for token in tokens):
            return {label: round(equity_share, 4)}

    target_asset = clean_text(classification.get("target_asset"))
    is_index_like = any(token in compact_text for token in ("指数", "ETF", "联接", "QDII"))
    if not is_index_like:
        return {}
    if target_asset in {"港股", "美股", "海外权益", "新兴市场"}:
        return {f"{target_asset}宽基": round(equity_share, 4)}
    if target_asset == "A股":
        return {"A股宽基": round(equity_share, 4)}
    return {}


def normalize_raw_exposure(exposure: dict[str, float]) -> dict[str, float]:
    output: dict[str, float] = {}
    for key, value in exposure.items():
        add_weight(output, translate_asset_key(key), safe_float(value))
    return output


def raw_generic_share(exposure: dict[str, float]) -> float:
    return round(sum(value for key, value in exposure.items() if translate_asset_key(key) in {"基金", "其他"}), 4)


def is_generic_key(key: str) -> bool:
    return translate_asset_key(key) in {"基金", "其他"}


def asset_exposure_from_asset_row(
    row: dict[str, Any] | None,
    classification: dict[str, Any],
    text: str,
    dictionary_row: dict[str, Any] | None,
) -> dict[str, float]:
    if not row:
        return {}
    output: dict[str, float] = {}
    stock = safe_float(row.get("股票占比_百分比"))
    if stock > 0:
        add_weight(output, infer_equity_bucket(text, dictionary_row), stock)
    bond = safe_float(row.get("债券占比_百分比"))
    if bond > 0:
        target = classification["target_asset"] if classification["category"] in {"fixed_income", "qdii_bond"} else "债券"
        add_weight(output, target, bond)
    cash = safe_float(row.get("现金占比_百分比"))
    if cash > 0:
        add_weight(output, "货币及现金", cash)
    fund = safe_float(row.get("基金占比_百分比"))
    if fund > 0:
        add_weight(output, "基金", fund)
    commodity = safe_float(row.get("商品占比_百分比"))
    if commodity > 0:
        add_weight(output, classification["target_asset"] if classification["category"] == "commodity" else "商品", commodity)
    cdr = safe_float(row.get("存托凭证占比_百分比"))
    if cdr > 0:
        add_weight(output, "存托凭证", cdr)
    other = safe_float(row.get("其他占比_百分比"))
    if other > 0:
        add_weight(output, "其他", other)
    return output


def default_exposure(classification: dict[str, Any]) -> dict[str, float]:
    return {classification["target_asset"]: 100.0} if classification.get("target_asset") else {"其他": 100.0}


def remap_economic_exposure(
    raw_exposure: dict[str, float],
    classification: dict[str, Any],
) -> tuple[dict[str, float], list[str]]:
    raw = normalize_raw_exposure(raw_exposure)
    if not raw:
        return normalize_exposure(default_exposure(classification)), ["缺少季报资产暴露，使用标准分类兜底"]

    category = classification["category"]
    target_asset = classification["target_asset"]
    output: dict[str, float] = {}
    notes: list[str] = []
    generic = 0.0
    for key, value in raw.items():
        if is_generic_key(key):
            generic += value
            continue
        mapped = key
        if category in {"fixed_income", "qdii_bond"} and key == "债券":
            mapped = target_asset
        elif category == "commodity" and key in {"基金", "其他", "商品", "黄金"}:
            mapped = target_asset
        elif category in {"qdii_equity", "mutual_recognition_equity", "equity_index"} and key in {"A股", "海外权益"}:
            mapped = target_asset
        add_weight(output, mapped, value)

    if generic > 0:
        add_weight(output, target_asset, generic)
        notes.append(f"季报基金/其他 {generic:.2f}% 已重映射为 {target_asset}")

    if not output:
        output = default_exposure(classification)
        notes.append("原始资产暴露无法落桶，使用标准分类兜底")
    return normalize_exposure(output), notes


def confidence_and_status(
    classification: dict[str, Any],
    raw_exposure: dict[str, float],
    economic_exposure: dict[str, float],
    source_snapshot: bool,
    rules: RuleConfig,
) -> tuple[str, str]:
    generic_share = raw_generic_share(raw_exposure)
    category = classification["category"]
    target_asset = classification["target_asset"]
    unresolved_share = sum(value for key, value in economic_exposure.items() if key in UNRESOLVED_KEYS)

    if not economic_exposure or unresolved_share >= rules.manual_review_threshold:
        return "低", "需人工补充"
    if category == "fof" and target_asset == "多资产FOF" and generic_share >= rules.manual_review_threshold:
        return "低", "需人工补充"
    if generic_share >= rules.high_fund_other_threshold:
        return ("中" if category in {"fof", "standard"} else "高"), "基金/其他已重映射"
    if not source_snapshot:
        return "中", "标准分类兜底"
    if category == "standard" and target_asset == "其他":
        return "低", "需人工补充"
    return "高", "通过"


def latest_by_code(rows: list[dict[str, Any]], code_key: str, report_key: str, tie_key: str = "") -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        code = normalize_code(row.get(code_key))
        if not code:
            continue
        report = clean_text(row.get(report_key))
        tie = clean_text(row.get(tie_key)) if tie_key else ""
        current = result.get(code)
        if current is None:
            result[code] = row
            continue
        current_sort = (clean_text(current.get(report_key)), clean_text(current.get(tie_key)) if tie_key else "")
        if (report, tie) >= current_sort:
            result[code] = row
    return result


def load_dictionary(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    if not table_exists(conn, "基金标准分类字典"):
        return {}
    rows = rows_as_dicts(conn, 'SELECT * FROM "基金标准分类字典"')
    return {normalize_code(row.get("基金代码")): row for row in rows if normalize_code(row.get("基金代码"))}


def load_latest_asset(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    if not table_exists(conn, "基金季度资产配置"):
        return {}
    rows = rows_as_dicts(conn, 'SELECT * FROM "基金季度资产配置"')
    return latest_by_code(rows, "基金代码", "报告期", "采集时间")


def load_latest_snapshot(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    if not table_exists(conn, "基金分类快照"):
        return {}
    rows = rows_as_dicts(conn, 'SELECT * FROM "基金分类快照"')
    return latest_by_code(rows, "基金代码", "报告期", "生成时间")


def load_current_holding_summary(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    if not table_exists(conn, "策略当前持仓"):
        return {}
    rows = rows_as_dicts(
        conn,
        """
        WITH latest AS (
          SELECT "统一策略ID", MAX("持仓日期") AS latest_date
          FROM "策略当前持仓"
          WHERE "基金代码" GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
          GROUP BY "统一策略ID"
        )
        SELECT h."基金代码",
               MAX(h."基金名称") AS "基金名称",
               SUM(CASE WHEN COALESCE(h."基金权重_百分比", 0) > 0 THEN COALESCE(h."基金权重_百分比", 0) ELSE 0 END) AS "当前持仓权重_百分比",
               COUNT(DISTINCT h."统一策略ID") AS "当前持仓策略数",
               MAX(h."持仓日期") AS "最新持仓日期"
        FROM "策略当前持仓" h
        JOIN latest l ON l."统一策略ID" = h."统一策略ID" AND l.latest_date = h."持仓日期"
        WHERE h."基金代码" GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
        GROUP BY h."基金代码"
        """,
    )
    return {normalize_code(row.get("基金代码")): row for row in rows if normalize_code(row.get("基金代码"))}


def choose_fund_name(
    dictionary_row: dict[str, Any] | None,
    snapshot_row: dict[str, Any] | None,
    holding_row: dict[str, Any] | None,
) -> str:
    for row, key in (
        (snapshot_row, "基金名称"),
        (dictionary_row, "标准基金名称"),
        (holding_row, "基金名称"),
    ):
        if row and clean_text(row.get(key)):
            return clean_text(row.get(key))
    return ""


def build_row(
    code: str,
    dictionary_row: dict[str, Any] | None,
    asset_row: dict[str, Any] | None,
    snapshot_row: dict[str, Any] | None,
    holding_row: dict[str, Any] | None,
    rules: RuleConfig,
    generated_at: str,
) -> dict[str, Any]:
    classification = classify_fund(code, dictionary_row, snapshot_row, holding_row, rules)
    text = source_text(code, dictionary_row, snapshot_row, holding_row)
    raw_exposure = parse_json_object((snapshot_row or {}).get("资产暴露JSON"))
    raw_source = "基金分类快照.资产暴露JSON" if raw_exposure else ""
    if not raw_exposure:
        raw_exposure = asset_exposure_from_asset_row(asset_row, classification, text, dictionary_row)
        raw_source = "基金季度资产配置" if raw_exposure else "标准分类兜底"
    economic_exposure, notes = remap_economic_exposure(raw_exposure, classification)
    raw_industry_exposure = parse_json_object((snapshot_row or {}).get("行业暴露JSON"))
    economic_industry_exposure = fund_level_industry_exposure(economic_exposure, raw_industry_exposure)
    inferred_theme_exposure = False
    if not economic_industry_exposure:
        inferred_exposure = infer_theme_industry_exposure(text, economic_exposure, classification)
        if inferred_exposure:
            economic_industry_exposure = inferred_exposure
            inferred_theme_exposure = True
            notes.append("缺少原始行业持仓，按基金名称/跟踪指数识别主题暴露；该口径不是底层行业穿透")
    if raw_industry_exposure and economic_industry_exposure and not inferred_theme_exposure:
        raw_industry_total = sum(safe_float(value) for value in raw_industry_exposure.values())
        economic_industry_total = sum(economic_industry_exposure.values())
        equity_share = equity_asset_share(economic_exposure)
        if raw_industry_total > equity_share + 0.5:
            notes.append(
                f"行业暴露已按权益资产占比折算为总资产口径：原行业合计{raw_industry_total:.2f}%/"
                f"权益资产{equity_share:.2f}%/经济行业合计{economic_industry_total:.2f}%"
            )
    elif raw_industry_exposure and not economic_industry_exposure:
        notes.append("原始行业暴露未写入经济行业暴露：经济权益资产占比为0或行业标签不可用")
    confidence, quality_status = confidence_and_status(
        classification,
        raw_exposure,
        economic_exposure,
        source_snapshot=bool(snapshot_row),
        rules=rules,
    )
    name = choose_fund_name(dictionary_row, snapshot_row, holding_row)
    report = clean_text((snapshot_row or {}).get("报告期")) or clean_text((asset_row or {}).get("报告期")) or clean_text(
        (holding_row or {}).get("最新持仓日期")
    )
    method = classification["matched_rule"]
    if raw_generic_share(raw_exposure) > 0:
        method = f"{method}+基金/其他重映射"
    evidence_parts = [
        f"来源={raw_source}",
        f"命中规则={classification['matched_rule']}",
    ]
    if classification["matched_keywords"]:
        evidence_parts.append(f"关键词={','.join(classification['matched_keywords'])}")
    if notes:
        evidence_parts.extend(notes)
    if dictionary_row and clean_text(dictionary_row.get("跟踪指数_名称推断")):
        evidence_parts.append(f"跟踪指数={clean_text(dictionary_row.get('跟踪指数_名称推断'))}")
    if dictionary_row:
        standard_desc = "/".join(
            part
            for part in (
                clean_text(dictionary_row.get("标准资产大类")),
                clean_text(dictionary_row.get("标准资产细类")),
                clean_text(dictionary_row.get("投顾资产分类桶")),
            )
            if part
        )
        if standard_desc:
            evidence_parts.append(f"标准分类={standard_desc}")

    return {
        "基金代码": code,
        "基金名称": name,
        "报告期": report,
        "标准资产大类": classification["standard_big"],
        "标准资产细类": classification["standard_small"],
        "经济资产暴露JSON": json.dumps(economic_exposure, ensure_ascii=False, sort_keys=True),
        "经济行业暴露JSON": json.dumps(economic_industry_exposure, ensure_ascii=False, sort_keys=True),
        "主题标签JSON": normalize_json_array_text((snapshot_row or {}).get("主题标签JSON") or (dictionary_row or {}).get("主题标签JSON")),
        "穿透层级": 2 if economic_industry_exposure else (1 if raw_source != "标准分类兜底" else 0),
        "穿透方法": method,
        "证据说明": "；".join(evidence_parts),
        "置信度": confidence,
        "质量状态": quality_status,
        "生成时间": generated_at,
        "原始资产暴露JSON": json.dumps(normalize_raw_exposure(raw_exposure), ensure_ascii=False, sort_keys=True),
        "原始基金其他占比": raw_generic_share(raw_exposure),
        "当前持仓权重_百分比": round(safe_float((holding_row or {}).get("当前持仓权重_百分比")), 4),
        "当前持仓策略数": int(safe_float((holding_row or {}).get("当前持仓策略数"))),
    }


ECONOMIC_SNAPSHOT_SCHEMA = """
DROP TABLE IF EXISTS "基金经济暴露快照";
CREATE TABLE "基金经济暴露快照" (
  "基金代码" TEXT PRIMARY KEY,
  "基金名称" TEXT,
  "报告期" TEXT,
  "标准资产大类" TEXT,
  "标准资产细类" TEXT,
  "经济资产暴露JSON" TEXT NOT NULL,
  "经济行业暴露JSON" TEXT NOT NULL,
  "主题标签JSON" TEXT NOT NULL,
  "穿透层级" INTEGER NOT NULL,
  "穿透方法" TEXT NOT NULL,
  "证据说明" TEXT,
  "置信度" TEXT NOT NULL,
  "质量状态" TEXT NOT NULL,
  "生成时间" TEXT NOT NULL,
  "原始资产暴露JSON" TEXT,
  "原始基金其他占比" REAL NOT NULL DEFAULT 0,
  "当前持仓权重_百分比" REAL NOT NULL DEFAULT 0,
  "当前持仓策略数" INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS "idx_基金经济暴露快照_报告期" ON "基金经济暴露快照"("报告期");
CREATE INDEX IF NOT EXISTS "idx_基金经济暴露快照_质量状态" ON "基金经济暴露快照"("质量状态");
"""


def write_snapshot_table(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> None:
    conn.executescript(ECONOMIC_SNAPSHOT_SCHEMA)
    conn.executemany(
        """
        INSERT INTO "基金经济暴露快照"
        ("基金代码","基金名称","报告期","标准资产大类","标准资产细类",
         "经济资产暴露JSON","经济行业暴露JSON","主题标签JSON","穿透层级","穿透方法",
         "证据说明","置信度","质量状态","生成时间","原始资产暴露JSON","原始基金其他占比",
         "当前持仓权重_百分比","当前持仓策略数")
        VALUES (:基金代码,:基金名称,:报告期,:标准资产大类,:标准资产细类,
         :经济资产暴露JSON,:经济行业暴露JSON,:主题标签JSON,:穿透层级,:穿透方法,
         :证据说明,:置信度,:质量状态,:生成时间,:原始资产暴露JSON,:原始基金其他占比,
         :当前持仓权重_百分比,:当前持仓策略数)
        """,
        rows,
    )
    conn.commit()


def build_rows(conn: sqlite3.Connection, rules: RuleConfig) -> list[dict[str, Any]]:
    dictionary = load_dictionary(conn)
    latest_asset = load_latest_asset(conn)
    latest_snapshot = load_latest_snapshot(conn)
    current_holding = load_current_holding_summary(conn)
    active_dictionary_codes = {
        code for code, row in dictionary.items() if flag(row, "是否当前库使用") and (code in latest_snapshot or code in latest_asset or code in current_holding)
    }
    universe = set(latest_snapshot) | set(latest_asset) | set(current_holding) | active_dictionary_codes
    generated_at = now_cn()
    rows = [
        build_row(
            code,
            dictionary.get(code),
            latest_asset.get(code),
            latest_snapshot.get(code),
            current_holding.get(code),
            rules,
            generated_at,
        )
        for code in sorted(universe)
    ]
    rows.sort(key=lambda row: (-safe_float(row.get("当前持仓权重_百分比")), row["基金代码"]))
    return rows


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    status_counter = Counter(row["质量状态"] for row in rows)
    method_counter = Counter(row["穿透方法"] for row in rows)
    held_rows = [row for row in rows if safe_float(row.get("当前持仓权重_百分比")) > 0]
    total_weight = sum(safe_float(row.get("当前持仓权重_百分比")) for row in held_rows)
    covered_weight = sum(
        safe_float(row.get("当前持仓权重_百分比"))
        for row in held_rows
        if row.get("经济资产暴露JSON") and row.get("质量状态") != "需人工补充"
    )
    return {
        "生成时间": now_cn(),
        "基金数": len(rows),
        "当前持仓基金数": len(held_rows),
        "当前持仓权重合计": round(total_weight, 4),
        "当前持仓可用暴露权重": round(covered_weight, 4),
        "当前持仓加权覆盖率": round(covered_weight / total_weight * 100, 4) if total_weight else 0,
        "质量状态分布": dict(status_counter),
        "穿透方法Top": dict(method_counter.most_common(20)),
        "需人工补充样本": [
            {
                "基金代码": row["基金代码"],
                "基金名称": row["基金名称"],
                "当前持仓权重_百分比": row["当前持仓权重_百分比"],
                "标准资产大类": row["标准资产大类"],
                "标准资产细类": row["标准资产细类"],
                "证据说明": row["证据说明"],
            }
            for row in rows
            if row["质量状态"] == "需人工补充"
        ][:50],
    }


def write_build_outputs(output_dir: Path, summary: dict[str, Any]) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "构建基金经济暴露快照_summary.json"
    md_path = output_dir / "构建基金经济暴露快照_summary.md"
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# 基金经济暴露快照构建摘要",
        "",
        f"- 生成时间：{summary['生成时间']}",
        f"- 基金数：{summary['基金数']}",
        f"- 当前持仓基金数：{summary['当前持仓基金数']}",
        f"- 当前持仓加权覆盖率：{summary['当前持仓加权覆盖率']}%",
        "",
        "## 质量状态分布",
        "",
        "| 状态 | 数量 |",
        "| --- | ---: |",
    ]
    for status, count in summary["质量状态分布"].items():
        lines.append(f"| {status} | {count} |")
    lines.extend(["", "## 需人工补充样本", "", "| 基金代码 | 基金名称 | 当前持仓权重 | 标准细类 |", "| --- | --- | ---: | --- |"])
    for row in summary["需人工补充样本"][:20]:
        lines.append(
            f"| {row['基金代码']} | {row['基金名称']} | {row['当前持仓权重_百分比']} | {row['标准资产细类']} |"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"json": str(json_path), "md": str(md_path)}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="构建基金经济暴露快照，修正基金/其他、固收指数、ETF联接、FOF 和 QDII 暴露。")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite 分析库路径，默认 data/analysis_zh_current.sqlite")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="运行摘要输出目录")
    parser.add_argument("--dry-run", action="store_true", help="只计算摘要，不写入 SQLite 表和输出文件")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="基金分类穿透规则 YAML")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rules = load_rule_config(args.config)
    with sqlite3.connect(args.db) as conn:
        conn.row_factory = sqlite3.Row
        rows = build_rows(conn, rules)
        summary = summarize(rows)
        if args.dry_run:
            summary["状态"] = "dry_run"
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return
        write_snapshot_table(conn, rows)
    output_paths = write_build_outputs(args.output_dir, summary)
    print(
        json.dumps(
            {
                "状态": "completed",
                "基金数": summary["基金数"],
                "当前持仓加权覆盖率": summary["当前持仓加权覆盖率"],
                "输出": output_paths,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
