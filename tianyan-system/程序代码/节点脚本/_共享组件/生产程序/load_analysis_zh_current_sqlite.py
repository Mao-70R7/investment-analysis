from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from business_naming import canonical_advisor_institution


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
NORMALIZED_ROOT = PROJECT_ROOT / "data" / "normalized"
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
DEFAULT_SCHEMA_PATH = PROJECT_ROOT / "schemas" / "analysis_zh_current.sql"
ALIAS_REPORT_DIR = PROJECT_ROOT / "outputs" / "fund_alias_mapping"
DEFAULT_CHANNELS = [
    "zocaifu",
    "gffunds",
    "gfsec_fima",
    "gfsec_robot",
    "ttfund",
    "huaxia_tougu",
    "harvestwm",
    "southern",
    "cmfchina",
    "efundcf",
    "fullgoal",
    "fund99",
    "qieman",
]
ARCHIVED_CHANNELS = ["gfbank_cgb"]
ALL_CHANNELS = [*DEFAULT_CHANNELS, *ARCHIVED_CHANNELS]
TTFUND_FUND_NAV_META_ROOT = PROJECT_ROOT / "data" / "normalized" / "ttfund_fund_nav" / "fund_nav_history_meta"
LATEST_RUN_ONLY_CHANNELS = {
    "zocaifu",
    "huaxia_tougu",
    "harvestwm",
    "southern",
    "cmfchina",
    "efundcf",
    "fullgoal",
    "fund99",
    "qieman",
    "gfsec_robot",
    "gfsec_fima",
    "gfbank_cgb",
}
EXACT_RUN_ENV_BY_CHANNEL = {
    "qieman": "QIEMAN_COLLECT_RUN_ID",
    "gfsec_fima": "GFSEC_FIMA_COLLECT_RUN_ID",
    "gfsec_robot": "GF_SUPPLEMENTAL_COLLECT_RUN_ID",
    "gfbank_cgb": "GF_SUPPLEMENTAL_COLLECT_RUN_ID",
}
TIMESTAMPED_RUN_PATTERN = re.compile(r"^(\d{8}T\d{6}[+-]\d{4})(?:__|$)")

WEIGHT_SUM_TARGET = 100.0
WEIGHT_SUM_TOLERANCE = 1.0
WEIGHT_NORMALIZE_EPSILON = 0.0001
DAILY_RETURN_CONSISTENCY_TOLERANCE_PCT = 0.05
ANALYSIS_SCHEMA_USER_VERSION = 20260811

TTFUND_FUND_GROUP_LABELS = {
    "0": "其他",
    "1": "股票型",
    "2": "货币型",
    "3": "混合型",
    "4": "混合型",
    "6": "债券型",
    "7": "混合型",
    "8": "指数型",
    "a": "QDII",
    "A": "QDII",
}

ANALYSIS_TABLES_TO_RESET = [
    "数据来源清单",
    "基金名称映射",
    "信号策略基金指令",
    "信号策略事件",
    "策略调仓明细",
    "策略调仓事件",
    "策略历史持仓",
    "策略当前持仓分组",
    "策略当前持仓",
    "策略披露风险指标",
    "策略区间业绩",
    "策略日度业绩",
    "策略信息",
    "渠道信息",
]

FUND_NAME_TEXT_REPLACEMENTS = [
    ("富兰克林国海", "国海富兰克林"),
    ("交银施罗德", "交银"),
    ("上投摩根", "上投"),
    ("国泰君安资管", "国君资管"),
    ("中泰资管", "中泰"),
    ("前海开源", "前海"),
    ("景顺长城", "景顺"),
    ("工银瑞信", "工银"),
    ("浦银安盛", "浦银"),
    ("华泰柏瑞", "华泰"),
]

FUND_NAME_REMOVE_TOKENS = [
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
    "指数增强",
    "指数",
    "基金",
]

LOOKUP_SOURCE_PRIORITY = {
    "manual_override": 100,
    "fund_alias_mapping": 95,
    "db_fund_info": 90,
    "fund_nav_meta": 70,
    "normalized_fund_public_dim": 60,
    "normalized_strategy_fund_snapshot": 50,
    "normalized_rebalance_delta": 50,
}

MANUAL_FUND_CODE_OVERRIDES: dict[str, dict[str, str]] = {
    "中欧强债A": {"fund_code": "166008", "standard_name": "中欧增强回报债券(LOF)A"},
    "中欧稳健收益债券C": {"fund_code": "166004", "standard_name": "中欧稳健收益债券C"},
    "中欧周期景气混合发起C": {"fund_code": "014609", "standard_name": "中欧周期景气混合发起式C"},
    "国泰中证沪深港创新药产业ETF发起联接C": {"fund_code": "014118", "standard_name": "国泰中证沪港深创新药产业ETF联接C"},
    "博时纯债A": {"fund_code": "050027", "standard_name": "博时信用债纯债债券A"},
    "东方科技": {"fund_code": "001702", "standard_name": "东方创新科技混合"},
    "前海农业主题C": {"fund_code": "015210", "standard_name": "前海开源沪港深农业主题精选灵活配置混合(LOF)C"},
    "浙商大数据": {"fund_code": "002967", "standard_name": "浙商大数据智选消费混合A"},
    "国君资管500C": {"fund_code": "014156", "standard_name": "国泰君安中证500C"},
}

CHANNEL_METADATA: dict[str, dict[str, str | None]] = {
    "huaxia_tougu": {
        "渠道ID": "huaxia_tougu",
        "渠道名称": "华夏投顾/华夏财富查理智投",
        "渠道类型": "fund_company",
        "官方站点": "https://www.amcfortune.com/superfund/fundList.shtml",
        "登录要求": "none",
        "备注": "当前来自华夏财富公开查理智投页面及公开调仓/收益接口。",
    },
    "zocaifu": {
        "渠道ID": "zocaifu",
        "渠道名称": "中欧财富/中欧钱滚滚",
        "渠道类型": "wealth_subsidiary",
        "官方站点": "https://www.zocaifu.com/",
        "登录要求": "partial",
        "备注": "当前主要来自公开接口与 H5 数据。",
    },
    "gffunds": {
        "渠道ID": "gffunds",
        "渠道名称": "广发基金",
        "渠道类型": "fund_company",
        "官方站点": "https://gfwx.gffunds.com.cn/html5app/invest-advisor",
        "登录要求": "partial",
        "备注": "当前主要来自公开投顾接口。",
    },
    "gfsec_fima": {
        "渠道ID": "gfsec_fima",
        "渠道名称": "广发证券",
        "渠道类型": "broker_app",
        "官方站点": "https://robot.gf.com.cn/api/robot",
        "登录要求": "none",
        "备注": "匿名公开接口可获取财富管家产品目录、官方当前模型基金仓位与权重、业绩及调仓接口核验结果；模型仓位不是客户账户实际持仓。",
    },
    "gfsec_robot": {
        "渠道ID": "gfsec_robot",
        "渠道名称": "广发证券",
        "渠道类型": "broker_app",
        "官方站点": "https://robot.gf.com.cn/asset/#/moneystrategy?channel=ytjapp",
        "登录要求": "partial",
        "备注": "匿名 robot 接口可获取策略主数据、披露收益风险、货币策略日度收益和公开推荐基金清单；推荐基金清单不等同于策略当前持仓。",
    },
    "gfbank_cgb": {
        "渠道ID": "gfbank_cgb",
        "渠道名称": "广发银行发现精彩",
        "渠道类型": "bank_app",
        "官方站点": "https://wap.cgbchina.com.cn/",
        "登录要求": "required",
        "备注": "APK 静态资源可见基金、定投、智能投顾模块入口；匿名公开 H5 未发现投顾策略主数据、持仓或调仓接口。",
    },
    "ttfund": {
        "渠道ID": "ttfund",
        "渠道名称": "天天基金/投顾",
        "渠道类型": "third_party",
        "官方站点": "https://fund.eastmoney.com/",
        "登录要求": "required",
        "备注": "当前主要来自登录态缓存与公开区间业绩快照。",
    },
    "harvestwm": {
        "渠道ID": "harvestwm",
        "渠道名称": "嘉实财富",
        "渠道类型": "wealth_subsidiary",
        "官方站点": "https://www.harvestwm.cn/product/customize_acco",
        "登录要求": "required",
        "备注": "公开站可采集投顾公告和部分组合披露，基金级持仓、业绩和调仓需会员中心或 App。",
    },
    "southern": {
        "渠道ID": "southern",
        "渠道名称": "南方基金/司南投顾",
        "渠道类型": "fund_company",
        "官方站点": "https://www.nffund.com/new/snzt/index.html",
        "登录要求": "required",
        "备注": "当前公开入口仅为司南投顾介绍页，策略和交易数据在登录后系统。",
    },
    "cmfchina": {
        "渠道ID": "cmfchina",
        "渠道名称": "招商基金/招财乐投顾",
        "渠道类型": "fund_company",
        "官方站点": "https://www.cmfchina.com/web/investmentadvisory/index.html",
        "登录要求": "partial",
        "备注": "官网公开页可采集精选策略卡片，基金级持仓和调仓明细未公开。",
    },
    "efundcf": {
        "渠道ID": "efundcf",
        "渠道名称": "易方达财富/e钱包",
        "渠道类型": "fund_company",
        "官方站点": "https://www.efundcf.com.cn/lm/tgfw/tgcl/",
        "登录要求": "required",
        "备注": "公开页仅披露投顾策略分类，具体产品、持仓、业绩和调仓需 App/交易端。",
    },
    "fullgoal": {
        "渠道ID": "fullgoal",
        "渠道名称": "富国基金/富钱包星投顾",
        "渠道类型": "fund_company",
        "官方站点": "https://www.fullgoal.com.cn/mobile/tougu/",
        "登录要求": "required",
        "备注": "公开策略说明书披露资产类别仓位区间、风险、基准和费率，未披露基金级持仓。",
    },
    "fund99": {
        "渠道ID": "fund99",
        "渠道名称": "汇添富基金/现金宝投顾",
        "渠道类型": "fund_company",
        "官方站点": "https://qy.99fund.com/info/investment_adviser.htm",
        "登录要求": "required",
        "备注": "公开帮助页可采集投顾策略名称和服务费率，基金级数据需登录“我的投顾”。",
    },
    "qieman": {
        "渠道ID": "qieman",
        "渠道名称": "且慢/盈米基金",
        "渠道类型": "third_party",
        "官方站点": "https://qieman.com/app",
        "登录要求": "required",
        "备注": "授权只读接口采集关键词目录下限、官方当前仓位、精确基准、净值和普通调仓；发车信号及历史仓位扩展实体保留在标准化层。",
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Load current analysis-layer tables into a local SQLite database.")
    parser.add_argument(
        "--db-path",
        type=Path,
        default=DEFAULT_DB_PATH,
        help="Target SQLite database path.",
    )
    parser.add_argument(
        "--schema-path",
        type=Path,
        default=DEFAULT_SCHEMA_PATH,
        help="Analysis-layer schema SQL path.",
    )
    parser.add_argument(
        "--channels",
        nargs="*",
        default=DEFAULT_CHANNELS,
        help="Channels to load. Default: all registered channels with available normalized data.",
    )
    parser.add_argument(
        "--keep-existing-db",
        action="store_true",
        help="Keep existing database file and upsert into it instead of recreating it.",
    )
    parser.add_argument(
        "--normalized-root",
        type=Path,
        default=NORMALIZED_ROOT,
        help="Runtime normalized data root. Defaults to the development-layout junction.",
    )
    parser.add_argument(
        "--strategy-catalog-summary",
        action="append",
        type=Path,
        default=[],
        help=(
            "Exact collection summary whose catalog_strategy_ids must all exist "
            "in the channel before the transaction is committed. Repeat per channel."
        ),
    )
    return parser.parse_args()


def init_db(db_path: Path, schema_path: Path, keep_existing_db: bool) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists() and not keep_existing_db:
        db_path.unlink()
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.executescript(schema_path.read_text(encoding="utf-8"))
    ensure_schema_governance(conn)
    ensure_signal_schema_columns(conn)
    return conn


def ensure_schema_governance(conn: sqlite3.Connection) -> None:
    conn.execute(f"PRAGMA user_version = {ANALYSIS_SCHEMA_USER_VERSION}")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS "schema_migrations" (
          "version" INTEGER PRIMARY KEY,
          "name" TEXT NOT NULL,
          "applied_at" TEXT NOT NULL
        )
        """
    )


def ensure_signal_schema_columns(conn: sqlite3.Connection) -> None:
    """Migrate pre-existing signal tables without dropping historical rows."""

    required_columns = {
        "信号策略事件": {
            "信号摘要": "TEXT",
            "预计确认日": "TEXT",
            "买入模式": "TEXT",
            "买入金额": "REAL",
            "转换模式": "TEXT",
            "是否精确调前仓位": "INTEGER",
            "是否精确调后仓位": "INTEGER",
            "调前权重合计_百分比": "REAL",
            "调后权重合计_百分比": "REAL",
            "官方换手率_百分比": "REAL",
            "原始事件ID": "TEXT",
            "原始信号ID": "TEXT",
            "访问级别": "TEXT",
            "置信度": "TEXT",
        },
        "信号策略基金指令": {
            "指令金额": "REAL",
            "新增资金分配比例_百分比": "REAL",
            "指令比例口径": "TEXT",
            "组合权重来源": "TEXT",
            "目标基金代码": "TEXT",
            "目标基金名称": "TEXT",
            "原始动作文本": "TEXT",
            "原始记录哈希": "TEXT",
            "置信度": "TEXT",
        },
    }
    for table_name, columns in required_columns.items():
        existing = {
            str(row[1])
            for row in conn.execute(f'PRAGMA table_info("{table_name}")')
        }
        for column_name, column_type in columns.items():
            if column_name not in existing:
                conn.execute(
                    f'ALTER TABLE "{table_name}" ADD COLUMN "{column_name}" {column_type}'
                )
    conn.execute(
        """
        INSERT OR IGNORE INTO "schema_migrations" ("version", "name", "applied_at")
        VALUES (?, ?, ?)
        """,
        (
            ANALYSIS_SCHEMA_USER_VERSION,
            "analysis_zh_current_qieman_signal_entities",
            datetime.now().astimezone().isoformat(timespec="seconds"),
        ),
    )


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rows.append(json.loads(line))
    return rows


def iter_jsonl(path: Path):
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def load_alias_report_rows(channels: list[str]) -> list[dict[str, Any]]:
    if not ALIAS_REPORT_DIR.exists():
        return []

    rows: list[dict[str, Any]] = []
    for path in sorted(ALIAS_REPORT_DIR.glob("*.json")):
        payload = load_json(path)
        if payload.get("channel_id") not in channels:
            continue
        for item in payload.get("matched") or []:
            mapping_name = str(item.get("source_name") or "").strip()
            fund_code = str(item.get("fund_code") or "").strip()
            if not mapping_name or not fund_code:
                continue
            rows.append(
                {
                    "映射名称": mapping_name,
                    "基金代码": fund_code,
                    "标准基金名称": item.get("standard_name"),
                    "匹配方式": item.get("match_method") or "report_import",
                    "匹配来源": "fund_alias_mapping_report",
                    "置信度": item.get("confidence") or "medium",
                    "更新时间": payload.get("generated_at") or payload.get("run_id") or path.stem,
                }
            )
    return rows


def unified_strategy_id(channel_id: str, source_strategy_id: str) -> str:
    return f"{channel_id}__{source_strategy_id}"


def json_text(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def to_percent(channel_id: str, value: Any) -> float | None:
    if value is None or value == "":
        return None
    number = float(value)
    if channel_id in {"zocaifu", "huaxia_tougu", "qieman"}:
        return round(number * 100, 6)
    return number


def to_display_percent(value: Any, *, absolute: bool = False) -> float | None:
    if value is None or value == "":
        return None
    text = str(value).strip().replace("%", "").replace(",", "")
    if not text or text == "--":
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    if absolute:
        number = abs(number)
    return round(number, 6)


def decimal_to_display_percent(value: Any, *, absolute: bool = False) -> float | None:
    number = to_float(value)
    if number is None:
        return None
    if absolute:
        number = abs(number)
    return round(number * 100, 6)


def millis_date_text(value: Any) -> str | None:
    number = to_float(value)
    if number is None:
        return None
    try:
        return datetime.fromtimestamp(number / 1000).date().isoformat()
    except (OSError, OverflowError, ValueError):
        return None


def parse_yyyymmdd(value: Any) -> str | None:
    text = str(value or "").strip()
    if not re.fullmatch(r"\d{8}", text):
        return None
    return f"{text[:4]}-{text[4:6]}-{text[6:8]}"


def date_prefix(value: Any) -> str | None:
    text = str(value or "").strip()
    match = re.search(r"\d{4}-\d{2}-\d{2}", text)
    if match:
        return match.group(0)
    return parse_yyyymmdd(text)


def normalize_bool(value: Any) -> int | None:
    if value is None:
        return None
    return 1 if bool(value) else 0


def normalize_path(path: Path) -> str:
    return str(path.resolve())


def normalize_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def normalize_fund_group_name(value: Any) -> str | None:
    text = normalize_text(value)
    if not text:
        return None
    return TTFUND_FUND_GROUP_LABELS.get(text, text)


def is_public_recommendation_list_not_holding(row: dict[str, Any]) -> bool:
    confidence = str(row.get("confidence_level") or "")
    if "not_holding" in confidence:
        return True
    if row.get("recommendation_endpoint") and normalize_bool(row.get("is_precise_weight")) == 0:
        return True
    return False


def normalize_date_text(value: Any) -> str | None:
    text = normalize_text(value)
    if not text:
        return None
    max_year = datetime.now().year + 1
    patterns = [
        r"(?<!\d)([12]\d{3})[-/.年]\s*([01]?\d)[-/.月]\s*([0-3]?\d)(?:日)?(?!\d)",
        r"(?<!\d)([12]\d{3})([01]\d)([0-3]\d)(?!\d)",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            year = int(match.group(1))
            month = int(match.group(2))
            day = int(match.group(3))
            if year < 1990 or year > max_year:
                continue
            try:
                datetime(year, month, day)
            except ValueError:
                continue
            return f"{year:04d}-{month:02d}-{day:02d}"
    return None


def sanitize_fund_code(value: Any) -> str | None:
    text = normalize_text(value)
    if not text:
        return None
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) == 6:
        return digits
    if digits and len(digits) < 6:
        return digits.zfill(6)
    return text


def to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and math.isnan(value):
            return None
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


def parse_amount_from_text(value: Any) -> float | None:
    text = normalize_text(value)
    if not text:
        return None
    match = re.search(r"([0-9][0-9,]*(?:\.\d+)?)", text)
    if not match:
        return None
    return to_float(match.group(1))


def minimum_amount_from_master(row: dict[str, Any]) -> float | None:
    extra = row.get("extra")
    if isinstance(extra, dict):
        from_text = parse_amount_from_text(extra.get("minimum_amount_text"))
        if from_text is not None:
            return from_text
    return to_float(row.get("minimum_amount"))


def normalize_fund_name(value: Any) -> str | None:
    text = normalize_text(value)
    if text is None:
        return None
    normalized = text.replace("（", "(").replace("）", ")").replace("　", " ")
    normalized = re.sub(r"\s+", "", normalized)
    for source, target in FUND_NAME_TEXT_REPLACEMENTS:
        normalized = normalized.replace(source, target)
    return normalized or None


def canonical_fund_name(value: Any) -> str | None:
    normalized = normalize_fund_name(value)
    if normalized is None:
        return None
    canonical = normalized
    for token in FUND_NAME_REMOVE_TOKENS:
        canonical = canonical.replace(token, "")
    return canonical or None


def reset_analysis_tables(conn: sqlite3.Connection, channels: list[str] | None = None) -> None:
    conn.execute("PRAGMA foreign_keys = OFF;")
    try:
        for table_name in ANALYSIS_TABLES_TO_RESET:
            if channels is None:
                conn.execute(f'DELETE FROM "{table_name}"')
                continue
            table_cols = {str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table_name}")')}
            if "渠道ID" not in table_cols:
                continue
            placeholders = ",".join("?" for _ in channels)
            conn.execute(f'DELETE FROM "{table_name}" WHERE "渠道ID" IN ({placeholders})', tuple(channels))
    finally:
        conn.execute("PRAGMA foreign_keys = ON;")


def register_lookup_candidate(
    bucket: dict[str, dict[str, dict[str, Any]]],
    key: str | None,
    fund_code: Any,
    fund_name: Any,
    source: str,
) -> None:
    normalized_key = normalize_text(key)
    normalized_code = sanitize_fund_code(fund_code)
    normalized_name = normalize_text(fund_name)
    if not normalized_key or not normalized_code or not normalized_name:
        return
    by_code = bucket.setdefault(normalized_key, {})
    entry = by_code.setdefault(
        normalized_code,
        {
            "fund_code": normalized_code,
            "standard_name": normalized_name,
            "source": source,
            "count": 0,
        },
    )
    entry["count"] = int(entry.get("count") or 0) + 1
    current_priority = LOOKUP_SOURCE_PRIORITY.get(str(entry.get("source") or ""), 0)
    incoming_priority = LOOKUP_SOURCE_PRIORITY.get(source, 0)
    if incoming_priority > current_priority:
        entry["source"] = source
    if len(normalized_name) > len(str(entry.get("standard_name") or "")):
        entry["standard_name"] = normalized_name


def pick_lookup_candidate(
    bucket: dict[str, dict[str, dict[str, Any]]],
    key: str | None,
) -> dict[str, Any] | None:
    normalized_key = normalize_text(key)
    if not normalized_key:
        return None
    by_code = bucket.get(normalized_key) or {}
    if not by_code:
        return None
    if len(by_code) == 1:
        return next(iter(by_code.values()))
    ranked = sorted(
        by_code.values(),
        key=lambda item: (int(item.get("count") or 0), len(str(item.get("standard_name") or ""))),
        reverse=True,
    )
    if len(ranked) >= 2 and int(ranked[0].get("count") or 0) >= int(ranked[1].get("count") or 0) * 2:
        return ranked[0]
    return None


def seed_fund_lookup(
    exact_bucket: dict[str, dict[str, dict[str, Any]]],
    canonical_bucket: dict[str, dict[str, dict[str, Any]]],
    fund_code: Any,
    fund_name: Any,
    source: str,
) -> None:
    normalized_name = normalize_fund_name(fund_name)
    canonical_name = canonical_fund_name(fund_name)
    register_lookup_candidate(exact_bucket, normalized_name, fund_code, fund_name, source)
    register_lookup_candidate(canonical_bucket, canonical_name, fund_code, fund_name, source)


def load_existing_fund_lookup(
    conn: sqlite3.Connection,
    exact_bucket: dict[str, dict[str, dict[str, Any]]],
    canonical_bucket: dict[str, dict[str, dict[str, Any]]],
) -> None:
    queries = [
        ('SELECT "基金代码", "基金名称" FROM "基金信息"', "db_fund_info"),
        ('SELECT "基金代码", "映射名称" FROM "基金名称映射"', "fund_alias_mapping"),
        ('SELECT "基金代码", "标准基金名称" FROM "基金名称映射"', "fund_alias_mapping"),
        ('SELECT "基金代码", "基金名称" FROM "基金净值概况"', "fund_nav_meta"),
    ]
    for sql, source in queries:
        try:
            rows = conn.execute(sql).fetchall()
        except sqlite3.OperationalError:
            continue
        for fund_code, fund_name in rows:
            seed_fund_lookup(exact_bucket, canonical_bucket, fund_code, fund_name, source)


def load_file_based_fund_lookup(
    channels: list[str],
    exact_bucket: dict[str, dict[str, dict[str, Any]]],
    canonical_bucket: dict[str, dict[str, dict[str, Any]]],
) -> None:
    entity_configs = [
        ("fund_public_dim", "fund_code", "fund_name", "normalized_fund_public_dim"),
        ("strategy_fund_snapshot", "fund_code", "fund_name", "normalized_strategy_fund_snapshot"),
        ("strategy_rebalance_fund_delta", "fund_code", "fund_name", "normalized_rebalance_delta"),
    ]
    for channel_id in channels:
        for entity_name, code_key, name_key, source in entity_configs:
            for path in entity_files(channel_id, entity_name):
                if path.stat().st_size == 0:
                    continue
                for row in load_jsonl(path):
                    seed_fund_lookup(exact_bucket, canonical_bucket, row.get(code_key), row.get(name_key), source)

    if TTFUND_FUND_NAV_META_ROOT.exists():
        for path in sorted(TTFUND_FUND_NAV_META_ROOT.glob("*/*.jsonl")):
            if path.stat().st_size == 0:
                continue
            for row in load_jsonl(path):
                seed_fund_lookup(exact_bucket, canonical_bucket, row.get("fund_code"), row.get("fund_name"), "fund_nav_meta")


def build_local_fund_lookup(
    conn: sqlite3.Connection,
    channels: list[str],
) -> tuple[dict[str, dict[str, dict[str, Any]]], dict[str, dict[str, dict[str, Any]]]]:
    exact_bucket: dict[str, dict[str, dict[str, Any]]] = {}
    canonical_bucket: dict[str, dict[str, dict[str, Any]]] = {}
    load_existing_fund_lookup(conn, exact_bucket, canonical_bucket)
    load_file_based_fund_lookup(channels, exact_bucket, canonical_bucket)
    for raw_name, payload in MANUAL_FUND_CODE_OVERRIDES.items():
        seed_fund_lookup(
            exact_bucket,
            canonical_bucket,
            payload.get("fund_code"),
            payload.get("standard_name") or raw_name,
            "manual_override",
        )
    return exact_bucket, canonical_bucket


def resolve_fund_identity(
    raw_fund_code: Any,
    raw_fund_name: Any,
    raw_match_status: Any,
    fund_alias_lookup: dict[str, dict[str, Any]],
    exact_bucket: dict[str, dict[str, dict[str, Any]]],
    canonical_bucket: dict[str, dict[str, dict[str, Any]]],
) -> tuple[str | None, str | None, str]:
    resolved_fund_code = sanitize_fund_code(raw_fund_code)
    resolved_fund_name = normalize_text(raw_fund_name)
    resolved_match_status = normalize_text(raw_match_status) or "missing"
    if resolved_fund_code:
        return resolved_fund_code, resolved_fund_name, resolved_match_status
    if not resolved_fund_name:
        return None, None, resolved_match_status

    manual_match = MANUAL_FUND_CODE_OVERRIDES.get(resolved_fund_name)
    if manual_match:
        return (
            sanitize_fund_code(manual_match.get("fund_code")),
            normalize_text(manual_match.get("standard_name")) or resolved_fund_name,
            "manual_override",
        )

    exact_match = pick_lookup_candidate(exact_bucket, normalize_fund_name(resolved_fund_name))
    if exact_match:
        return (
            sanitize_fund_code(exact_match.get("fund_code")),
            normalize_text(exact_match.get("standard_name")) or resolved_fund_name,
            f'local_exact:{exact_match.get("source")}',
        )

    alias_row = fund_alias_lookup.get(resolved_fund_name)
    alias_code = sanitize_fund_code(alias_row.get("基金代码") if alias_row else None)
    if alias_code:
        match_method = normalize_text(alias_row.get("匹配方式")) or "alias"
        return (
            alias_code,
            normalize_text(alias_row.get("标准基金名称")) or resolved_fund_name,
            f"alias:{match_method}",
        )

    canonical_match = pick_lookup_candidate(canonical_bucket, canonical_fund_name(resolved_fund_name))
    if canonical_match:
        return (
            sanitize_fund_code(canonical_match.get("fund_code")),
            normalize_text(canonical_match.get("standard_name")) or resolved_fund_name,
            f'local_canonical:{canonical_match.get("source")}',
        )

    return None, resolved_fund_name, resolved_match_status


def merge_daily_row(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing)
    for key, value in incoming.items():
        if value in (None, ""):
            continue
        merged[key] = value
    return merged


def collect_daily_performance_rows(
    conn: sqlite3.Connection,
    channel_id: str,
    summaries: dict[str, dict[str, Any]],
    counters: dict[str, int],
) -> list[dict[str, Any]]:
    rows_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for path in entity_files(channel_id, "strategy_performance_daily"):
        run_id = path.stem
        captured_at = summaries.get(run_id, {}).get("captured_at")
        for row in load_jsonl(path):
            strategy_id = row["source_strategy_id"]
            unified_id = unified_strategy_id(channel_id, strategy_id)
            trade_date = normalize_date_text(row.get("trade_date"))
            if not trade_date:
                counters["策略日度业绩_缺失交易日期"] += 1
                continue
            mapped = {
                "统一策略ID": unified_id,
                "渠道ID": channel_id,
                "渠道策略ID": strategy_id,
                "交易日期": trade_date,
                "单位净值": to_float(row.get("nav")),
                "日收益率_百分比": to_percent(channel_id, row.get("daily_return")),
                "累计收益率_百分比": to_percent(channel_id, row.get("cumulative_return")),
                "基准收益率_百分比": to_percent(channel_id, row.get("benchmark_return")),
                "指数收益率_百分比": to_percent(channel_id, row.get("index_return")),
                "最大回撤_百分比": to_percent(channel_id, row.get("max_drawdown")),
                "业绩区段名称": row.get("section_name"),
                "业绩区段类型": row.get("section_type") or row.get("source_type"),
                "原始快照ID": row.get("source_snapshot_id"),
            }
            key = (unified_id, trade_date)
            if key in rows_by_key:
                rows_by_key[key] = merge_daily_row(rows_by_key[key], mapped)
                counters["策略日度业绩_同日合并"] += 1
            else:
                rows_by_key[key] = mapped
            upsert_source(
                conn,
                {
                    "统一策略ID": unified_id,
                    "渠道ID": channel_id,
                    "渠道策略ID": strategy_id,
                    "文件类型": "strategy_performance_daily",
                    "文件路径": normalize_path(path),
                    "采集批次ID": run_id,
                    "采集时间": captured_at,
                },
            )
    return list(rows_by_key.values())


def repair_daily_performance_rows(
    rows: list[dict[str, Any]],
    counters: dict[str, int],
) -> list[dict[str, Any]]:
    rows_by_strategy: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        rows_by_strategy[str(row["统一策略ID"])].append(dict(row))

    repaired: list[dict[str, Any]] = []
    for strategy_rows in rows_by_strategy.values():
        strategy_rows.sort(key=lambda row: str(row["交易日期"]))
        previous_nav: float | None = None
        for row in strategy_rows:
            nav = to_float(row.get("单位净值"))
            source_nav_missing = nav is None
            daily_return_pct = to_float(row.get("日收益率_百分比"))
            cumulative_return_pct = to_float(row.get("累计收益率_百分比"))

            if nav is None and cumulative_return_pct is not None and cumulative_return_pct > -100:
                nav = round(1.0 + cumulative_return_pct / 100.0, 8)
                row["单位净值"] = nav
                counters["策略日度业绩_按累计收益补净值"] += 1
            elif nav is None and previous_nav is not None and daily_return_pct is not None:
                nav = round(previous_nav * (1.0 + daily_return_pct / 100.0), 8)
                row["单位净值"] = nav
                counters["策略日度业绩_按日收益补净值"] += 1

            if cumulative_return_pct is None and nav is not None:
                cumulative_return_pct = round((nav - 1.0) * 100.0, 8)
                row["累计收益率_百分比"] = cumulative_return_pct
                counters["策略日度业绩_按净值补累计收益"] += 1

            if nav is not None and previous_nav not in (None, 0):
                implied_daily_return_pct = round((nav / previous_nav - 1.0) * 100.0, 8)
                if daily_return_pct is None:
                    daily_return_pct = implied_daily_return_pct
                    row["日收益率_百分比"] = daily_return_pct
                    counters["策略日度业绩_按相邻净值补日收益"] += 1
                elif (
                    not source_nav_missing
                    and abs(implied_daily_return_pct - daily_return_pct) > DAILY_RETURN_CONSISTENCY_TOLERANCE_PCT
                ):
                    if str(row.get("渠道ID") or "").strip().lower() == "qieman":
                        # A small number of Qieman rows expose a dailyReturn whose
                        # sign conflicts with the official adjacent NAVs.  Keep the
                        # source value in the immutable normalized evidence, but use
                        # the deterministic NAV-derived return in the analytical DB.
                        # This avoids publishing an impossible NAV/return pair while
                        # preserving the source discrepancy for audit and replay.
                        daily_return_pct = implied_daily_return_pct
                        row["日收益率_百分比"] = daily_return_pct
                        counters["策略日度业绩_且慢按相邻净值纠正日收益"] += 1
                    else:
                        counters["策略日度业绩_日收益与净值不一致未覆盖"] += 1

            if nav is not None and nav > 0:
                previous_nav = nav

        valid_nav_count = sum(
            1
            for row in strategy_rows
            if (to_float(row.get("单位净值")) or 0.0) > 0
        )
        running_peak: float | None = None
        running_max_drawdown = 0.0
        for row in strategy_rows:
            nav = to_float(row.get("单位净值"))
            if nav is None or nav <= 0:
                repaired.append(row)
                continue
            # A single observation can only produce a mechanical zero
            # drawdown.  Keep it undisclosed unless the source itself supplied
            # a value; otherwise downstream pages may misread zero as a
            # complete historical risk statistic.
            if valid_nav_count < 2:
                repaired.append(row)
                continue
            running_peak = nav if running_peak is None else max(running_peak, nav)
            drawdown_pct = (1.0 - nav / running_peak) * 100.0 if running_peak else 0.0
            running_max_drawdown = max(running_max_drawdown, drawdown_pct)
            existing_max_drawdown = to_float(row.get("最大回撤_百分比"))
            expected_max_drawdown = round(running_max_drawdown, 8)
            if existing_max_drawdown is None:
                row["最大回撤_百分比"] = expected_max_drawdown
                counters["策略日度业绩_按净值链补最大回撤"] += 1
            elif existing_max_drawdown < running_max_drawdown - 1e-6:
                row["最大回撤_百分比"] = expected_max_drawdown
                counters["策略日度业绩_修正过小最大回撤"] += 1
            repaired.append(row)
    repaired.sort(key=lambda row: (str(row["统一策略ID"]), str(row["交易日期"])))
    return repaired

def upsert_fund_alias(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO "基金信息" (
            "基金代码", "基金名称", "数据来源"
        ) VALUES (?, ?, ?)
        """,
        [
            row["基金代码"],
            row.get("标准基金名称") or row.get("映射名称") or row["基金代码"],
            "fund_alias_mapping",
        ],
    )
    conn.execute(
        """
        INSERT INTO "基金名称映射" (
            "映射名称", "基金代码", "标准基金名称", "匹配方式", "匹配来源", "置信度", "更新时间"
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT("映射名称") DO UPDATE SET
            "基金代码"=excluded."基金代码",
            "标准基金名称"=excluded."标准基金名称",
            "匹配方式"=excluded."匹配方式",
            "匹配来源"=excluded."匹配来源",
            "置信度"=excluded."置信度",
            "更新时间"=excluded."更新时间"
        """,
        [
            row["映射名称"],
            row["基金代码"],
            row.get("标准基金名称"),
            row.get("匹配方式"),
            row.get("匹配来源"),
            row.get("置信度"),
            row.get("更新时间"),
        ],
    )


def load_fund_alias_lookup(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    try:
        rows = conn.execute(
            '''
            SELECT "映射名称", "基金代码", "标准基金名称", "匹配方式", "置信度"
            FROM "基金名称映射"
            '''
        ).fetchall()
    except sqlite3.OperationalError:
        return result

    for mapping_name, fund_code, standard_name, match_method, confidence in rows:
        key = str(mapping_name or "").strip()
        if not key:
            continue
        result[key] = {
            "基金代码": fund_code,
            "标准基金名称": standard_name,
            "匹配方式": match_method,
            "置信度": confidence,
        }
    return result


def entity_files(channel_id: str, entity_name: str) -> list[Path]:
    root = NORMALIZED_ROOT / channel_id / entity_name
    if not root.exists():
        return []
    files = sorted(root.glob("*/*.jsonl"))
    if channel_id in LATEST_RUN_ONLY_CHANNELS and files:
        exact_run_id = normalize_text(os.environ.get(EXACT_RUN_ENV_BY_CHANNEL.get(channel_id, "")))
        if exact_run_id:
            return [path for path in files if path.stem == exact_run_id]
        timestamped_files = [
            path for path in files if TIMESTAMPED_RUN_PATTERN.match(path.stem)
        ]
        candidates = timestamped_files or files
        latest_path = max(
            candidates,
            key=lambda path: (
                TIMESTAMPED_RUN_PATTERN.match(path.stem).group(1)
                if TIMESTAMPED_RUN_PATTERN.match(path.stem)
                else "",
                path.stem,
            ),
        )
        files = [path for path in files if path.stem == latest_path.stem]
    return files


def summary_files(channel_id: str) -> dict[str, dict[str, Any]]:
    root = NORMALIZED_ROOT / channel_id / "collection_summary"
    summaries: dict[str, dict[str, Any]] = {}
    if not root.exists():
        root_summaries = []
    else:
        root_summaries = sorted(root.glob("*/*.json"))
    for path in root_summaries:
        summaries[path.stem] = load_json(path)
    fallback = PROJECT_ROOT / "official_apps" / channel_id / "outputs" / "latest_summary.json"
    if fallback.exists():
        payload = load_json(fallback)
        run_id = normalize_text(payload.get("run_id"))
        if run_id:
            summaries.setdefault(run_id, payload)
    return summaries


def disclosed_risk_rows_from_master(
    channel_id: str,
    unified_id: str,
    strategy_id: str,
    row: dict[str, Any],
    captured_at: Any,
) -> list[dict[str, Any]]:
    extra = row.get("extra")
    if not isinstance(extra, dict):
        return []

    rows: list[dict[str, Any]] = []
    if channel_id == "ttfund":
        card_drawdown = to_display_percent(extra.get("home_draw_down"), absolute=True)
        if card_drawdown is not None:
            rows.append(
                {
                    "统一策略ID": unified_id,
                    "渠道ID": channel_id,
                    "渠道策略ID": strategy_id,
                    "统计日期": date_prefix(extra.get("server_time")) or date_prefix(captured_at),
                    "区间代码": "official_card",
                    "区间名称": "官方卡片回撤",
                    "官方收益率_百分比": None,
                    "官方最大回撤_百分比": card_drawdown,
                    "官方波动率_百分比": None,
                    "官方夏普": None,
                    "官方基准收益率_百分比": None,
                    "数据来源字段": "strategy_master.extra.home_draw_down",
                    "原始快照ID": row.get("source_snapshot_id"),
                }
            )
    elif channel_id in {"gfsec_robot", "gfsec_fima"}:
        performance = extra.get("performance") if isinstance(extra.get("performance"), dict) else {}
        if not performance:
            return rows
        stat_date = (
            millis_date_text(performance.get("busiDate"))
            or date_prefix(row.get("performance_busi_date"))
            or date_prefix(captured_at)
        )
        interval_defs = [
            ("1d", "官方近1日", "yield1d", None),
            ("1m", "官方近1月", "yield1m", "maxDrawDown1m"),
            ("3m", "官方近3月", "yield3m", "maxDrawDown3m"),
            ("6m", "官方近6月", "yield6m", "maxDrawDown6m"),
            ("1y", "官方近1年", "yield1y", "maxDrawDown1y"),
            ("2y", "官方近2年", "yield2y", "maxDrawDown2y"),
            ("3y", "官方近3年", "yield3y", "maxDrawDown3y"),
            (
                "std",
                "官方披露累计",
                "totalYield" if channel_id == "gfsec_fima" else "yield",
                "maxDraw" if channel_id == "gfsec_fima" else "maxDrawDown",
            ),
        ]
        for interval_code, interval_name, return_field, drawdown_field in interval_defs:
            official_return = decimal_to_display_percent(performance.get(return_field))
            official_drawdown = (
                decimal_to_display_percent(performance.get(drawdown_field), absolute=True)
                if drawdown_field
                else None
            )
            if official_return is None and official_drawdown is None:
                continue
            rows.append(
                {
                    "统一策略ID": unified_id,
                    "渠道ID": channel_id,
                    "渠道策略ID": strategy_id,
                    "统计日期": stat_date,
                    "区间代码": interval_code,
                    "区间名称": interval_name,
                    "官方收益率_百分比": official_return,
                    "官方最大回撤_百分比": official_drawdown,
                    "官方波动率_百分比": (
                        decimal_to_display_percent(
                            performance.get("annualVolatility")
                            if channel_id == "gfsec_fima"
                            else performance.get("volatilityRatio"),
                            absolute=True,
                        )
                        if interval_code == "std"
                        else None
                    ),
                    "官方夏普": to_float(performance.get("sharpRatio")) if interval_code == "std" else None,
                    "官方基准收益率_百分比": None,
                    "数据来源字段": f"strategy_master.extra.performance.{return_field}",
                    "原始快照ID": row.get("source_snapshot_id"),
                }
            )
    return rows


def import_gffunds_raw_disclosed_risk_metrics(
    conn: sqlite3.Connection,
    counters: dict[str, int],
) -> None:
    root = PROJECT_ROOT / "data" / "raw" / "gffunds" / "public_api"
    if not root.exists():
        return
    for path in sorted(root.glob("*/*/products/*/get_investadvisor_yield_trend_since_inception.json")):
        strategy_id = path.parent.name.split("_", 1)[0]
        try:
            payload = load_json(path)
        except json.JSONDecodeError:
            counters["策略披露风险指标_坏JSON跳过"] += 1
            continue
        trend_rows = payload.get("adv_yield_trend_list") or []
        latest_row = trend_rows[-1] if trend_rows else {}
        max_drawdown = to_display_percent(payload.get("max_drawdown"), absolute=True)
        if max_drawdown is None:
            continue
        inserted = upsert_disclosed_risk_metric(
            conn,
            {
                "统一策略ID": unified_strategy_id("gffunds", strategy_id),
                "渠道ID": "gffunds",
                "渠道策略ID": strategy_id,
                "统计日期": date_prefix(latest_row.get("yield_date")) or date_prefix(payload.get("adv_setupdate")),
                "区间代码": "std",
                "区间名称": "官方成立以来",
                "官方收益率_百分比": to_display_percent(latest_row.get("yield_rate")),
                "官方最大回撤_百分比": max_drawdown,
                "官方波动率_百分比": to_display_percent(payload.get("volatility"), absolute=True),
                "官方夏普": to_display_percent(payload.get("sharpe_ratio")),
                "官方基准收益率_百分比": to_display_percent(latest_row.get("base_yield_rate")),
                "数据来源字段": "gffunds.get_investadvisor_yield_trend.max_drawdown",
                "原始快照ID": normalize_path(path),
            },
        )
        if inserted:
            counters["策略披露风险指标"] += 1
        else:
            counters["策略披露风险指标_策略缺失跳过"] += 1


def import_zocaifu_raw_disclosed_risk_metrics(
    conn: sqlite3.Connection,
    counters: dict[str, int],
) -> None:
    root = PROJECT_ROOT / "data" / "raw" / "zocaifu" / "public_api"
    if not root.exists():
        return
    return_field_by_interval = {
        "1y": "lastYearRate",
        "std": "totalRate",
    }
    interval_code_map = {
        "lastYear": "1y",
        "total": "std",
    }
    for path in sorted(root.glob("*/*/products/*/productDetailV2.json")):
        try:
            payload = load_json(path)
        except json.JSONDecodeError:
            counters["策略披露风险指标_坏JSON跳过"] += 1
            continue
        detail = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(detail, dict):
            continue
        strategy_id = str(detail.get("fofId") or path.parent.name.split("_", 1)[0])
        stat_date = parse_yyyymmdd(detail.get("navDate")) or date_prefix(detail.get("currentTime"))
        self_extend = detail.get("selfFofExtendMap") if isinstance(detail.get("selfFofExtendMap"), dict) else {}
        index_extend = detail.get("indexFofExtendMap") if isinstance(detail.get("indexFofExtendMap"), dict) else {}
        for risk_row in detail.get("fofRiskIndexList") or []:
            raw_interval = str(risk_row.get("indexIntervalName") or "").strip()
            interval_code = interval_code_map.get(raw_interval)
            info = risk_row.get("fofRiskIndexInfoVo") or {}
            max_drawdown = to_display_percent(info.get("drawdown"), absolute=True)
            if interval_code is None or max_drawdown is None:
                continue
            return_field = return_field_by_interval.get(interval_code)
            inserted = upsert_disclosed_risk_metric(
                conn,
                {
                    "统一策略ID": unified_strategy_id("zocaifu", strategy_id),
                    "渠道ID": "zocaifu",
                    "渠道策略ID": strategy_id,
                    "统计日期": stat_date,
                    "区间代码": interval_code,
                    "区间名称": risk_row.get("indexIntervalDesc") or ("官方成立以来" if interval_code == "std" else "官方近一年"),
                    "官方收益率_百分比": to_display_percent(self_extend.get(return_field)) if return_field else None,
                    "官方最大回撤_百分比": max_drawdown,
                    "官方波动率_百分比": to_display_percent(info.get("waveRate"), absolute=True),
                    "官方夏普": to_display_percent(info.get("sharpeRatio")),
                    "官方基准收益率_百分比": to_display_percent(index_extend.get(return_field)) if return_field else None,
                    "数据来源字段": f"zocaifu.fofRiskIndexList.{raw_interval}.drawdown",
                    "原始快照ID": normalize_path(path),
                },
            )
            if inserted:
                counters["策略披露风险指标"] += 1
            else:
                counters["策略披露风险指标_策略缺失跳过"] += 1


def import_raw_disclosed_risk_metrics(
    conn: sqlite3.Connection,
    channels: list[str],
    counters: dict[str, int],
) -> None:
    if "gffunds" in channels:
        import_gffunds_raw_disclosed_risk_metrics(conn, counters)
    if "zocaifu" in channels:
        import_zocaifu_raw_disclosed_risk_metrics(conn, counters)


def upsert_channel(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO "渠道信息" (
            "渠道ID", "渠道名称", "渠道类型", "官方站点", "登录要求", "备注"
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT("渠道ID") DO UPDATE SET
            "渠道名称"=excluded."渠道名称",
            "渠道类型"=excluded."渠道类型",
            "官方站点"=excluded."官方站点",
            "登录要求"=excluded."登录要求",
            "备注"=excluded."备注",
            "更新时间"=CURRENT_TIMESTAMP
        """,
        [
            row["渠道ID"],
            row["渠道名称"],
            row.get("渠道类型"),
            row.get("官方站点"),
            row.get("登录要求"),
            row.get("备注"),
        ],
    )


def upsert_strategy(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO "策略信息" (
            "统一策略ID", "渠道ID", "渠道策略ID", "策略名称", "投顾机构", "策略类型",
            "风险等级", "成立日期", "建议持有时长", "起投金额", "投顾费率", "业绩基准",
            "标签JSON", "策略状态", "策略描述", "原始来源URL", "原始快照ID",
            "首次入库时间", "最近入库时间"
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT("统一策略ID") DO UPDATE SET
            "策略名称"=COALESCE(excluded."策略名称", "策略信息"."策略名称"),
            "投顾机构"=COALESCE(excluded."投顾机构", "策略信息"."投顾机构"),
            "策略类型"=COALESCE(excluded."策略类型", "策略信息"."策略类型"),
            "风险等级"=COALESCE(excluded."风险等级", "策略信息"."风险等级"),
            "成立日期"=COALESCE(excluded."成立日期", "策略信息"."成立日期"),
            "建议持有时长"=COALESCE(excluded."建议持有时长", "策略信息"."建议持有时长"),
            "起投金额"=COALESCE(excluded."起投金额", "策略信息"."起投金额"),
            "投顾费率"=COALESCE(excluded."投顾费率", "策略信息"."投顾费率"),
            "业绩基准"=COALESCE(excluded."业绩基准", "策略信息"."业绩基准"),
            "标签JSON"=COALESCE(excluded."标签JSON", "策略信息"."标签JSON"),
            "策略状态"=COALESCE(excluded."策略状态", "策略信息"."策略状态"),
            "策略描述"=COALESCE(excluded."策略描述", "策略信息"."策略描述"),
            "原始来源URL"=COALESCE(excluded."原始来源URL", "策略信息"."原始来源URL"),
            "原始快照ID"=COALESCE(excluded."原始快照ID", "策略信息"."原始快照ID"),
            "首次入库时间"=COALESCE("策略信息"."首次入库时间", excluded."首次入库时间"),
            "最近入库时间"=excluded."最近入库时间"
        """,
        [
            row["统一策略ID"],
            row["渠道ID"],
            row["渠道策略ID"],
            row["策略名称"],
            row.get("投顾机构"),
            row.get("策略类型"),
            row.get("风险等级"),
            row.get("成立日期"),
            row.get("建议持有时长"),
            row.get("起投金额"),
            row.get("投顾费率"),
            row.get("业绩基准"),
            row.get("标签JSON"),
            row.get("策略状态"),
            row.get("策略描述"),
            row.get("原始来源URL"),
            row.get("原始快照ID"),
            row.get("首次入库时间"),
            row.get("最近入库时间"),
        ],
    )


def upsert_daily_performance(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO "策略日度业绩" (
            "统一策略ID", "渠道ID", "渠道策略ID", "交易日期", "单位净值",
            "日收益率_百分比", "累计收益率_百分比", "基准收益率_百分比",
            "指数收益率_百分比", "最大回撤_百分比", "业绩区段名称", "业绩区段类型", "原始快照ID"
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT("统一策略ID", "交易日期") DO UPDATE SET
            "单位净值"=COALESCE(excluded."单位净值", "策略日度业绩"."单位净值"),
            "日收益率_百分比"=COALESCE(excluded."日收益率_百分比", "策略日度业绩"."日收益率_百分比"),
            "累计收益率_百分比"=COALESCE(excluded."累计收益率_百分比", "策略日度业绩"."累计收益率_百分比"),
            "基准收益率_百分比"=COALESCE(excluded."基准收益率_百分比", "策略日度业绩"."基准收益率_百分比"),
            "指数收益率_百分比"=COALESCE(excluded."指数收益率_百分比", "策略日度业绩"."指数收益率_百分比"),
            "最大回撤_百分比"=COALESCE(excluded."最大回撤_百分比", "策略日度业绩"."最大回撤_百分比"),
            "业绩区段名称"=COALESCE(excluded."业绩区段名称", "策略日度业绩"."业绩区段名称"),
            "业绩区段类型"=COALESCE(excluded."业绩区段类型", "策略日度业绩"."业绩区段类型"),
            "原始快照ID"=COALESCE(excluded."原始快照ID", "策略日度业绩"."原始快照ID")
        """,
        [
            row["统一策略ID"],
            row["渠道ID"],
            row["渠道策略ID"],
            row["交易日期"],
            row.get("单位净值"),
            row.get("日收益率_百分比"),
            row.get("累计收益率_百分比"),
            row.get("基准收益率_百分比"),
            row.get("指数收益率_百分比"),
            row.get("最大回撤_百分比"),
            row.get("业绩区段名称"),
            row.get("业绩区段类型"),
            row.get("原始快照ID"),
        ],
    )


def upsert_interval_performance(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO "策略区间业绩" (
            "统一策略ID", "渠道ID", "渠道策略ID", "统计日期", "区间代码", "区间名称",
            "策略收益率_百分比", "基准收益率_百分比", "原始快照ID"
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT("统一策略ID", "统计日期", "区间代码") DO UPDATE SET
            "区间名称"=COALESCE(excluded."区间名称", "策略区间业绩"."区间名称"),
            "策略收益率_百分比"=COALESCE(excluded."策略收益率_百分比", "策略区间业绩"."策略收益率_百分比"),
            "基准收益率_百分比"=COALESCE(excluded."基准收益率_百分比", "策略区间业绩"."基准收益率_百分比"),
            "原始快照ID"=COALESCE(excluded."原始快照ID", "策略区间业绩"."原始快照ID")
        """,
        [
            row["统一策略ID"],
            row["渠道ID"],
            row["渠道策略ID"],
            row["统计日期"],
            row["区间代码"],
            row["区间名称"],
            row.get("策略收益率_百分比"),
            row.get("基准收益率_百分比"),
            row.get("原始快照ID"),
        ],
    )


def upsert_disclosed_risk_metric(conn: sqlite3.Connection, row: dict[str, Any]) -> bool:
    exists = conn.execute(
        'SELECT 1 FROM "策略信息" WHERE "统一策略ID"=? LIMIT 1',
        (row["统一策略ID"],),
    ).fetchone()
    if exists is None:
        return False
    conn.execute(
        """
        INSERT INTO "策略披露风险指标" (
            "统一策略ID", "渠道ID", "渠道策略ID", "统计日期", "区间代码", "区间名称",
            "官方收益率_百分比", "官方最大回撤_百分比", "官方波动率_百分比", "官方夏普",
            "官方基准收益率_百分比", "数据来源字段", "原始快照ID"
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT("统一策略ID", "区间代码", "数据来源字段") DO UPDATE SET
            "渠道ID"=excluded."渠道ID",
            "渠道策略ID"=excluded."渠道策略ID",
            "统计日期"=excluded."统计日期",
            "区间名称"=excluded."区间名称",
            "官方收益率_百分比"=excluded."官方收益率_百分比",
            "官方最大回撤_百分比"=excluded."官方最大回撤_百分比",
            "官方波动率_百分比"=excluded."官方波动率_百分比",
            "官方夏普"=excluded."官方夏普",
            "官方基准收益率_百分比"=excluded."官方基准收益率_百分比",
            "原始快照ID"=excluded."原始快照ID"
        """,
        [
            row["统一策略ID"],
            row["渠道ID"],
            row["渠道策略ID"],
            row.get("统计日期"),
            row["区间代码"],
            row.get("区间名称"),
            row.get("官方收益率_百分比"),
            row.get("官方最大回撤_百分比"),
            row.get("官方波动率_百分比"),
            row.get("官方夏普"),
            row.get("官方基准收益率_百分比"),
            row["数据来源字段"],
            row.get("原始快照ID"),
        ],
    )
    return True


def upsert_current_holding_group(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO "策略当前持仓分组" (
            "统一策略ID", "渠道ID", "渠道策略ID", "持仓日期", "披露日期",
            "分组名称", "分组权重_百分比", "基金数量", "原始快照ID"
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT("统一策略ID", "持仓日期", "分组名称") DO UPDATE SET
            "披露日期"=excluded."披露日期",
            "分组权重_百分比"=excluded."分组权重_百分比",
            "基金数量"=excluded."基金数量",
            "原始快照ID"=excluded."原始快照ID"
        """,
        [
            row["统一策略ID"],
            row["渠道ID"],
            row["渠道策略ID"],
            row["持仓日期"],
            row.get("披露日期"),
            row["分组名称"],
            row.get("分组权重_百分比"),
            row.get("基金数量"),
            row.get("原始快照ID"),
        ],
    )


def upsert_current_holding(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO "策略当前持仓" (
            "统一策略ID", "渠道ID", "渠道策略ID", "持仓日期", "披露日期",
            "基金代码", "基金名称", "资产类型", "分组名称", "基金权重_百分比", "分组权重_百分比",
            "基金净值", "基金净值日期", "最新日涨幅_百分比", "操作标记", "是否精确权重",
            "置信度", "访问级别", "原始快照ID"
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT("统一策略ID", "持仓日期", "基金名称") DO UPDATE SET
            "披露日期"=excluded."披露日期",
            "基金代码"=excluded."基金代码",
            "资产类型"=excluded."资产类型",
            "分组名称"=excluded."分组名称",
            "基金权重_百分比"=excluded."基金权重_百分比",
            "分组权重_百分比"=excluded."分组权重_百分比",
            "基金净值"=excluded."基金净值",
            "基金净值日期"=excluded."基金净值日期",
            "最新日涨幅_百分比"=excluded."最新日涨幅_百分比",
            "操作标记"=excluded."操作标记",
            "是否精确权重"=excluded."是否精确权重",
            "置信度"=excluded."置信度",
            "访问级别"=excluded."访问级别",
            "原始快照ID"=excluded."原始快照ID"
        """,
        [
            row["统一策略ID"],
            row["渠道ID"],
            row["渠道策略ID"],
            row["持仓日期"],
            row.get("披露日期"),
            row.get("基金代码"),
            row["基金名称"],
            row.get("资产类型"),
            row.get("分组名称"),
            row.get("基金权重_百分比"),
            row.get("分组权重_百分比"),
            row.get("基金净值"),
            row.get("基金净值日期"),
            row.get("最新日涨幅_百分比"),
            row.get("操作标记"),
            row.get("是否精确权重", 0),
            row.get("置信度"),
            row.get("访问级别"),
            row.get("原始快照ID"),
        ],
    )


def upsert_rebalance_event(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO "策略调仓事件" (
            "调仓事件ID", "统一策略ID", "渠道ID", "渠道策略ID", "调仓日期",
            "上次仓位日期", "本次仓位日期", "披露日期", "调仓标题", "调仓原因",
            "上次仓位日期是否推断", "事件序号", "事件时间", "载荷类型", "置信度", "原始快照ID"
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT("调仓事件ID") DO UPDATE SET
            "统一策略ID"=excluded."统一策略ID",
            "渠道ID"=excluded."渠道ID",
            "渠道策略ID"=excluded."渠道策略ID",
            "调仓日期"=excluded."调仓日期",
            "上次仓位日期"=excluded."上次仓位日期",
            "本次仓位日期"=excluded."本次仓位日期",
            "披露日期"=excluded."披露日期",
            "调仓标题"=excluded."调仓标题",
            "调仓原因"=excluded."调仓原因",
            "上次仓位日期是否推断"=excluded."上次仓位日期是否推断",
            "事件序号"=excluded."事件序号",
            "事件时间"=excluded."事件时间",
            "载荷类型"=excluded."载荷类型",
            "置信度"=excluded."置信度",
            "原始快照ID"=excluded."原始快照ID"
        """,
        [
            row["调仓事件ID"],
            row["统一策略ID"],
            row["渠道ID"],
            row["渠道策略ID"],
            row["调仓日期"],
            row.get("上次仓位日期"),
            row.get("本次仓位日期"),
            row.get("披露日期"),
            row.get("调仓标题"),
            row.get("调仓原因"),
            row.get("上次仓位日期是否推断"),
            row.get("事件序号"),
            row.get("事件时间"),
            row.get("载荷类型"),
            row.get("置信度"),
            row.get("原始快照ID"),
        ],
    )


def upsert_rebalance_delta(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO "策略调仓明细" (
            "调仓明细ID", "调仓事件ID", "统一策略ID", "渠道ID", "渠道策略ID",
            "调仓日期", "披露日期", "调仓标题", "基金代码", "基金名称", "分组名称",
            "调前权重_百分比", "调后权重_百分比", "权重变化_百分比", "调仓动作",
            "基金代码匹配状态", "分组调前权重_百分比", "分组调后权重_百分比", "原始快照ID"
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT("调仓明细ID") DO UPDATE SET
            "调仓事件ID"=excluded."调仓事件ID",
            "统一策略ID"=excluded."统一策略ID",
            "渠道ID"=excluded."渠道ID",
            "渠道策略ID"=excluded."渠道策略ID",
            "调仓日期"=excluded."调仓日期",
            "披露日期"=excluded."披露日期",
            "调仓标题"=excluded."调仓标题",
            "基金代码"=excluded."基金代码",
            "基金名称"=excluded."基金名称",
            "分组名称"=excluded."分组名称",
            "调前权重_百分比"=excluded."调前权重_百分比",
            "调后权重_百分比"=excluded."调后权重_百分比",
            "权重变化_百分比"=excluded."权重变化_百分比",
            "调仓动作"=excluded."调仓动作",
            "基金代码匹配状态"=excluded."基金代码匹配状态",
            "分组调前权重_百分比"=excluded."分组调前权重_百分比",
            "分组调后权重_百分比"=excluded."分组调后权重_百分比",
            "原始快照ID"=excluded."原始快照ID"
        """,
        [
            row["调仓明细ID"],
            row["调仓事件ID"],
            row["统一策略ID"],
            row["渠道ID"],
            row["渠道策略ID"],
            row["调仓日期"],
            row.get("披露日期"),
            row.get("调仓标题"),
            row.get("基金代码"),
            row["基金名称"],
            row.get("分组名称"),
            row.get("调前权重_百分比"),
            row.get("调后权重_百分比"),
            row.get("权重变化_百分比"),
            row.get("调仓动作"),
            row.get("基金代码匹配状态"),
            row.get("分组调前权重_百分比"),
            row.get("分组调后权重_百分比"),
            row.get("原始快照ID"),
        ],
    )


def upsert_fund_info(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO "基金信息" (
            "基金代码", "基金名称", "基金公司", "基金类型", "跟踪指数",
            "主题标签JSON", "最新净值", "最新净值日期", "基金状态", "数据来源"
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT("基金代码") DO UPDATE SET
            "基金名称"=excluded."基金名称",
            "基金公司"=excluded."基金公司",
            "基金类型"=excluded."基金类型",
            "跟踪指数"=excluded."跟踪指数",
            "主题标签JSON"=excluded."主题标签JSON",
            "最新净值"=excluded."最新净值",
            "最新净值日期"=excluded."最新净值日期",
            "基金状态"=excluded."基金状态",
            "数据来源"=excluded."数据来源",
            "最近更新时间"=CURRENT_TIMESTAMP
        """,
        [
            row["基金代码"],
            row["基金名称"],
            row.get("基金公司"),
            row.get("基金类型"),
            row.get("跟踪指数"),
            row.get("主题标签JSON"),
            row.get("最新净值"),
            row.get("最新净值日期"),
            row.get("基金状态"),
            row.get("数据来源"),
        ],
    )


def upsert_source(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO "数据来源清单" (
            "统一策略ID", "渠道ID", "渠道策略ID", "文件类型", "文件路径", "采集批次ID", "采集时间"
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT("统一策略ID", "文件类型") DO UPDATE SET
            "渠道ID"=excluded."渠道ID",
            "渠道策略ID"=excluded."渠道策略ID",
            "文件路径"=excluded."文件路径",
            "采集批次ID"=excluded."采集批次ID",
            "采集时间"=excluded."采集时间"
        """,
        [
            row["统一策略ID"],
            row["渠道ID"],
            row["渠道策略ID"],
            row["文件类型"],
            row["文件路径"],
            row.get("采集批次ID"),
            row.get("采集时间"),
        ],
    )


def upsert_historical_holding(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO "策略历史持仓" (
            "统一策略ID", "渠道ID", "渠道策略ID", "历史快照ID", "持仓日期", "披露日期",
            "快照阶段", "来源事件ID", "基金代码", "基金名称", "资产类型", "基金权重_百分比",
            "是否精确权重", "置信度", "访问级别", "原始记录哈希", "原始来源URL", "采集批次ID"
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT("统一策略ID", "历史快照ID", "基金名称") DO UPDATE SET
            "持仓日期"=excluded."持仓日期",
            "披露日期"=excluded."披露日期",
            "快照阶段"=excluded."快照阶段",
            "来源事件ID"=excluded."来源事件ID",
            "基金代码"=excluded."基金代码",
            "资产类型"=excluded."资产类型",
            "基金权重_百分比"=excluded."基金权重_百分比",
            "是否精确权重"=excluded."是否精确权重",
            "置信度"=excluded."置信度",
            "访问级别"=excluded."访问级别",
            "原始记录哈希"=excluded."原始记录哈希",
            "原始来源URL"=excluded."原始来源URL",
            "采集批次ID"=excluded."采集批次ID"
        """,
        [
            row["统一策略ID"],
            row["渠道ID"],
            row["渠道策略ID"],
            row["历史快照ID"],
            row["持仓日期"],
            row.get("披露日期"),
            row.get("快照阶段"),
            row.get("来源事件ID"),
            row.get("基金代码"),
            row["基金名称"],
            row.get("资产类型"),
            row.get("基金权重_百分比"),
            row.get("是否精确权重", 0),
            row.get("置信度"),
            row.get("访问级别"),
            row.get("原始记录哈希"),
            row.get("原始来源URL"),
            row.get("采集批次ID"),
        ],
    )


def import_channel_historical_holdings(
    conn: sqlite3.Connection,
    channel_id: str,
    summaries: dict[str, dict[str, Any]],
    counters: dict[str, int],
) -> None:
    """Load official historical position snapshots without treating signal ratios as holdings."""

    strategy_map = {
        str(row[0]): str(row[1])
        for row in conn.execute(
            'SELECT "渠道策略ID", "统一策略ID" FROM "策略信息" WHERE "渠道ID"=?',
            (channel_id,),
        )
    }
    for path in entity_files(channel_id, "strategy_fund_snapshot_history"):
        run_id = path.stem
        captured_at = summaries.get(run_id, {}).get("captured_at")
        source_ids: set[str] = set()
        for row in iter_jsonl(path):
            source_strategy_id = str(row.get("source_strategy_id") or "").strip()
            unified_id = strategy_map.get(source_strategy_id)
            if not unified_id:
                counters["策略历史持仓_策略缺失跳过"] += 1
                continue
            snapshot_id = str(row.get("snapshot_id") or "").strip()
            position_date = normalize_date_text(row.get("position_date"))
            fund_name = normalize_text(row.get("fund_name"))
            if not snapshot_id or not position_date or not fund_name:
                counters["策略历史持仓_业务键缺失跳过"] += 1
                continue
            upsert_historical_holding(
                conn,
                {
                    "统一策略ID": unified_id,
                    "渠道ID": channel_id,
                    "渠道策略ID": source_strategy_id,
                    "历史快照ID": snapshot_id,
                    "持仓日期": position_date,
                    "披露日期": normalize_date_text(row.get("disclosure_date")),
                    "快照阶段": normalize_text(row.get("snapshot_phase")),
                    "来源事件ID": normalize_text(row.get("source_event_id")),
                    "基金代码": sanitize_fund_code(row.get("fund_code")),
                    "基金名称": fund_name,
                    "资产类型": normalize_fund_group_name(row.get("fund_asset_type")),
                    "基金权重_百分比": to_percent(channel_id, row.get("fund_weight")),
                    "是否精确权重": normalize_bool(row.get("is_precise_weight")) or 0,
                    "置信度": normalize_text(row.get("confidence_level")),
                    "访问级别": normalize_text(row.get("access_level")),
                    "原始记录哈希": normalize_text(row.get("raw_record_hash")),
                    "原始来源URL": normalize_text(row.get("source_url")),
                    "采集批次ID": run_id,
                },
            )
            source_ids.add(source_strategy_id)
            counters["策略历史持仓"] += 1
        for source_strategy_id in source_ids:
            upsert_source(
                conn,
                {
                    "统一策略ID": strategy_map[source_strategy_id],
                    "渠道ID": channel_id,
                    "渠道策略ID": source_strategy_id,
                    "文件类型": "strategy_fund_snapshot_history",
                    "文件路径": normalize_path(path),
                    "采集批次ID": run_id,
                    "采集时间": captured_at,
                },
            )


def signal_direction(raw_action: Any) -> str:
    action = str(raw_action or "").strip().lower()
    if action in {"buy", "subscribe", "purchase"}:
        return "买入"
    if action in {"sell", "redeem", "redemption"}:
        return "卖出"
    if action in {"add", "increase", "increase_position"}:
        return "加仓"
    if action in {"reduce", "decrease", "decrease_position"}:
        return "减仓"
    if action in {"convert", "switch"}:
        return "转换"
    return str(raw_action or "").strip() or "未分类"


def insert_dynamic_row(
    conn: sqlite3.Connection,
    table_name: str,
    row: dict[str, Any],
) -> None:
    columns = list(row)
    column_sql = ",".join(f'"{name}"' for name in columns)
    placeholders = ",".join("?" for _ in columns)
    conn.execute(
        f'INSERT OR REPLACE INTO "{table_name}" ({column_sql}) VALUES ({placeholders})',
        [row.get(name) for name in columns],
    )


def import_channel_signal_entities(
    conn: sqlite3.Connection,
    channel_id: str,
    summaries: dict[str, dict[str, Any]],
    counters: dict[str, int],
) -> None:
    """Load official signal entities without projecting instructions as holdings."""

    event_files = entity_files(channel_id, "signal_strategy_event")
    instruction_files = entity_files(channel_id, "signal_fund_instruction")
    if not event_files and not instruction_files:
        return

    strategy_map = {
        str(row[0]): {
            "统一策略ID": str(row[1]),
            "策略名称": row[2],
            "投顾机构": row[3],
        }
        for row in conn.execute(
            '''SELECT "渠道策略ID", "统一策略ID", "策略名称", "投顾机构"
               FROM "策略信息" WHERE "渠道ID"=?''',
            (channel_id,),
        )
    }
    source_events: dict[str, tuple[dict[str, Any], Path, str | None]] = {}
    for path in event_files:
        run_id = path.stem
        captured_at = summaries.get(run_id, {}).get("captured_at")
        for row in load_jsonl(path):
            event_id = str(row.get("signal_event_id") or "").strip()
            source_strategy_id = str(row.get("source_strategy_id") or "").strip()
            if not event_id or source_strategy_id not in strategy_map:
                counters["信号策略事件_策略缺失跳过"] += 1
                continue
            source_events[event_id] = (row, path, captured_at)

    instruction_rows: list[dict[str, Any]] = []
    event_stats: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "指令数": 0,
            "买入指令数": 0,
            "卖出指令数": 0,
            "加仓指令数": 0,
            "减仓指令数": 0,
            "净买入权重_百分点": 0.0,
            "总调整强度_百分点": 0.0,
            "精确权重指令数": 0,
        }
    )
    for path in instruction_files:
        run_id = path.stem
        captured_at = summaries.get(run_id, {}).get("captured_at")
        for row in load_jsonl(path):
            event_id = str(row.get("signal_event_id") or "").strip()
            source_strategy_id = str(row.get("source_strategy_id") or "").strip()
            event_context = source_events.get(event_id)
            strategy = strategy_map.get(source_strategy_id)
            if not event_context or not strategy:
                counters["信号策略基金指令_事件或策略缺失跳过"] += 1
                continue
            event_row = event_context[0]
            before = decimal_to_display_percent(row.get("before_portfolio_weight"))
            after = decimal_to_display_percent(row.get("after_portfolio_weight"))
            change = round(after - before, 6) if before is not None and after is not None else None
            direction = signal_direction(row.get("raw_action"))
            mapped = {
                "信号指令ID": row.get("signal_instruction_id"),
                "信号事件ID": event_id,
                "统一策略ID": strategy["统一策略ID"],
                "渠道ID": channel_id,
                "渠道策略ID": source_strategy_id,
                "策略名称": strategy.get("策略名称") or event_row.get("strategy_name"),
                "信号日期": normalize_date_text(row.get("signal_date") or event_row.get("signal_date")),
                "信号时间": row.get("signal_time") or event_row.get("signal_time"),
                "基金代码": sanitize_fund_code(row.get("fund_code") or row.get("source_fund_code")),
                "基金名称": row.get("fund_name") or row.get("source_fund_name"),
                "分组名称": None,
                "天天基金资产类型": None,
                "指令方向": direction,
                "调前权重_百分比": before,
                "调后权重_百分比": after,
                "权重变化_百分点": change,
                "指令强度_百分点": abs(change) if change is not None else None,
                "指令金额": to_float(row.get("instruction_amount")),
                "新增资金分配比例_百分比": decimal_to_display_percent(row.get("instruction_ratio")),
                "指令比例口径": row.get("instruction_ratio_semantics"),
                "组合权重来源": row.get("portfolio_weight_source"),
                "目标基金代码": sanitize_fund_code(row.get("target_fund_code")),
                "目标基金名称": row.get("target_fund_name"),
                "原始动作文本": row.get("raw_action"),
                "原始记录哈希": row.get("raw_record_hash"),
                "置信度": row.get("confidence_level"),
                "原始动作码": None,
                "数据状态": (
                    "官方完整调前调后仓位"
                    if before is not None and after is not None
                    else "官方信号，未披露存量组合权重"
                ),
                "生成时间": captured_at,
            }
            instruction_rows.append(mapped)
            stats = event_stats[event_id]
            stats["指令数"] += 1
            direction_counter = {
                "买入": "买入指令数",
                "卖出": "卖出指令数",
                "加仓": "加仓指令数",
                "减仓": "减仓指令数",
            }.get(direction)
            if direction_counter:
                stats[direction_counter] += 1
            if change is not None:
                stats["净买入权重_百分点"] += change
                stats["总调整强度_百分点"] += abs(change)
                stats["精确权重指令数"] += 1

    for event_id, (row, path, captured_at) in source_events.items():
        source_strategy_id = str(row.get("source_strategy_id") or "").strip()
        strategy = strategy_map[source_strategy_id]
        stats = event_stats[event_id]
        exact_count = int(stats.pop("精确权重指令数") or 0)
        stats["净买入权重_百分点"] = (
            round(float(stats["净买入权重_百分点"]), 6) if exact_count else None
        )
        stats["总调整强度_百分点"] = (
            round(float(stats["总调整强度_百分点"]), 6) if exact_count else None
        )
        source_event_id = row.get("source_event_id")
        mapped = {
            "信号事件ID": event_id,
            "统一策略ID": strategy["统一策略ID"],
            "渠道ID": channel_id,
            "渠道策略ID": source_strategy_id,
            "策略名称": strategy.get("策略名称") or row.get("strategy_name"),
            "投顾机构": strategy.get("投顾机构"),
            "信号日期": normalize_date_text(row.get("signal_date")),
            "信号时间": row.get("signal_time"),
            "信号标题": row.get("signal_title"),
            "信号原因": row.get("signal_reason"),
            "信号摘要": row.get("signal_summary"),
            "预计确认日": normalize_date_text(row.get("expected_confirm_day")),
            "买入模式": row.get("buy_mode"),
            "买入金额": to_float(row.get("buy_total_amount")),
            "转换模式": row.get("convert_mode"),
            "是否精确调前仓位": normalize_bool(row.get("has_exact_pre_position")),
            "是否精确调后仓位": normalize_bool(row.get("has_exact_post_position")),
            "调前权重合计_百分比": decimal_to_display_percent(row.get("pre_weight_sum")),
            "调后权重合计_百分比": decimal_to_display_percent(row.get("post_weight_sum")),
            "官方换手率_百分比": decimal_to_display_percent(row.get("official_turnover_rate")),
            "原始事件ID": None if source_event_id is None else str(source_event_id),
            "原始信号ID": None if row.get("source_signal_id") is None else str(row.get("source_signal_id")),
            "访问级别": row.get("access_level"),
            "置信度": row.get("confidence_level"),
            "原始快照路径": normalize_path(path),
            "原始事件序号": int(source_event_id) if str(source_event_id or "").isdigit() else None,
            "信号评价结论": "尚未执行基金净值方向评价",
            "生成时间": captured_at,
            **stats,
        }
        insert_dynamic_row(conn, "信号策略事件", mapped)
        counters["信号策略事件"] += 1
        upsert_source(
            conn,
            {
                "统一策略ID": strategy["统一策略ID"],
                "渠道ID": channel_id,
                "渠道策略ID": source_strategy_id,
                "文件类型": "signal_strategy_event",
                "文件路径": normalize_path(path),
                "采集批次ID": path.stem,
                "采集时间": captured_at,
            },
        )

    for row in instruction_rows:
        insert_dynamic_row(conn, "信号策略基金指令", row)
        counters["信号策略基金指令"] += 1

    counters["信号策略数"] += len(
        {row["统一策略ID"] for row in instruction_rows}
        | {
            strategy_map[str(row.get("source_strategy_id"))]["统一策略ID"]
            for row, _path, _captured_at in source_events.values()
        }
    )


def make_delta_id(
    event_id: str,
    fund_identity: str,
    group_name: str | None,
    before_weight: Any,
    after_weight: Any,
    weight_delta: Any,
    action_type: str | None,
) -> str:
    raw = "|".join(
        [
            event_id or "",
            fund_identity or "",
            group_name or "",
            "" if before_weight is None else str(before_weight),
            "" if after_weight is None else str(after_weight),
            "" if weight_delta is None else str(weight_delta),
            action_type or "",
        ]
    )
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    return f"{event_id}-{digest}"


def rebalance_event_dedupe_key(row: dict[str, Any]) -> tuple[str, ...]:
    return (
        normalize_text(row.get("渠道ID")) or "",
        normalize_text(row.get("统一策略ID")) or "",
        normalize_date_text(row.get("调仓日期")) or "",
        normalize_date_text(row.get("上次仓位日期")) or "",
        normalize_date_text(row.get("本次仓位日期")) or "",
        normalize_text(row.get("调仓标题")) or "",
        normalize_text(row.get("调仓原因")) or "",
        "" if row.get("事件序号") is None else str(row.get("事件序号")),
        normalize_text(row.get("事件时间")) or "",
    )


def rebalance_delta_fund_identity(row: dict[str, Any]) -> str:
    return (
        sanitize_fund_code(row.get("基金代码"))
        or canonical_fund_name(row.get("基金名称"))
        or normalize_fund_name(row.get("基金名称"))
        or ""
    )


def rebalance_delta_signature_value(value: Any) -> Any:
    number = to_float(value)
    if number is not None:
        return round(number, 6)
    return normalize_text(value) or ""


def rebalance_detail_signature(rows: list[dict[str, Any]]) -> str:
    items = [
        (
            rebalance_delta_fund_identity(row),
            normalize_text(row.get("基金名称")) or "",
            normalize_text(row.get("分组名称")) or "",
            rebalance_delta_signature_value(row.get("调前权重_百分比")),
            rebalance_delta_signature_value(row.get("调后权重_百分比")),
            rebalance_delta_signature_value(row.get("权重变化_百分比")),
            normalize_text(row.get("调仓动作")) or "",
            rebalance_delta_signature_value(row.get("分组调前权重_百分比")),
            rebalance_delta_signature_value(row.get("分组调后权重_百分比")),
        )
        for row in rows
    ]
    payload = json.dumps(sorted(items), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def rebalance_event_row_score(row: dict[str, Any], detail_count: int) -> tuple[int, int, int, int, str]:
    return (
        detail_count,
        1 if normalize_text(row.get("原始快照ID")) else 0,
        len(normalize_text(row.get("调仓原因")) or ""),
        len(normalize_text(row.get("调仓标题")) or ""),
        normalize_text(row.get("调仓事件ID")) or "",
    )


def merge_rebalance_event_row(primary: dict[str, Any], secondary: dict[str, Any]) -> dict[str, Any]:
    merged = dict(primary)
    for key, value in secondary.items():
        if merged.get(key) in (None, "") and value not in (None, ""):
            merged[key] = value
    return merged


def canonicalize_rebalance_events_and_deltas(
    event_context: dict[str, dict[str, Any]],
    delta_rows: list[dict[str, Any]],
    counters: dict[str, int],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows_by_event: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in delta_rows:
        rows_by_event[str(row["调仓事件ID"])].append(row)

    grouped: dict[tuple[tuple[str, ...], str], list[dict[str, Any]]] = defaultdict(list)
    passthrough_groups: list[list[dict[str, Any]]] = []
    for event in event_context.values():
        event_id = str(event["调仓事件ID"])
        event_deltas = rows_by_event.get(event_id, [])
        if not event_deltas:
            passthrough_groups.append([event])
            continue
        grouped[(rebalance_event_dedupe_key(event), rebalance_detail_signature(event_deltas))].append(event)

    id_map: dict[str, str] = {}
    canonical_events: list[dict[str, Any]] = []
    duplicate_event_count = 0
    for events in list(grouped.values()) + passthrough_groups:
        best = max(
            events,
            key=lambda row: rebalance_event_row_score(row, len(rows_by_event.get(str(row["调仓事件ID"]), []))),
        )
        canonical = dict(best)
        for event in events:
            if event is not best:
                canonical = merge_rebalance_event_row(canonical, event)
        canonical_id = str(canonical["调仓事件ID"])
        canonical_events.append(canonical)
        for event in events:
            raw_id = str(event["调仓事件ID"])
            id_map[raw_id] = canonical_id
            if raw_id != canonical_id:
                duplicate_event_count += 1

    if duplicate_event_count:
        counters["策略调仓事件_重复快照折叠"] += duplicate_event_count

    canonical_delta_by_id: dict[str, dict[str, Any]] = {}
    rewritten_delta_count = 0
    for row in delta_rows:
        canonical_event_id = id_map.get(str(row["调仓事件ID"]), str(row["调仓事件ID"]))
        rewritten = dict(row)
        if canonical_event_id != str(row["调仓事件ID"]):
            rewritten["调仓事件ID"] = canonical_event_id
            rewritten_delta_count += 1
        rewritten["调仓明细ID"] = make_delta_id(
            event_id=canonical_event_id,
            fund_identity=rebalance_delta_fund_identity(rewritten),
            group_name=normalize_text(rewritten.get("分组名称")),
            before_weight=to_float(rewritten.get("调前权重_百分比")),
            after_weight=to_float(rewritten.get("调后权重_百分比")),
            weight_delta=to_float(rewritten.get("权重变化_百分比")),
            action_type=normalize_text(rewritten.get("调仓动作")),
        )
        existing = canonical_delta_by_id.get(rewritten["调仓明细ID"])
        if existing is not None:
            canonical_delta_by_id[rewritten["调仓明细ID"]] = merge_rebalance_delta_row(existing, rewritten)
            counters["策略调仓明细_重复快照折叠"] += 1
        else:
            canonical_delta_by_id[rewritten["调仓明细ID"]] = rewritten

    if rewritten_delta_count:
        counters["策略调仓明细_重复事件ID重写"] += rewritten_delta_count

    canonical_events.sort(
        key=lambda row: (
            str(row["统一策略ID"]),
            str(row["调仓日期"]),
            "" if row.get("事件序号") is None else str(row.get("事件序号")),
            str(row["调仓事件ID"]),
        )
    )
    canonical_delta_rows = sorted(
        canonical_delta_by_id.values(),
        key=lambda row: (str(row["统一策略ID"]), str(row["调仓日期"]), str(row["调仓事件ID"]), str(row["调仓明细ID"])),
    )
    return canonical_events, canonical_delta_rows


def delta_row_score(row: dict[str, Any]) -> int:
    score = 0
    if sanitize_fund_code(row.get("基金代码")):
        score += 100
    status = normalize_text(row.get("基金代码匹配状态")) or ""
    if status.startswith("manual_override"):
        score += 30
    elif status.startswith("local_exact"):
        score += 20
    elif status.startswith("alias:"):
        score += 15
    elif status.startswith("local_canonical"):
        score += 10
    if normalize_text(row.get("原始快照ID")):
        score += 1
    return score


def merge_rebalance_delta_row(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    primary, secondary = (incoming, existing) if delta_row_score(incoming) >= delta_row_score(existing) else (existing, incoming)
    merged = dict(primary)
    for key, value in secondary.items():
        if merged.get(key) in (None, "") and value not in (None, ""):
            merged[key] = value
    return merged


def normalize_weight_field(
    rows: list[dict[str, Any]],
    field_name: str,
    counter_name: str,
    counters: dict[str, int],
) -> None:
    values: list[float] = []
    for row in rows:
        value = to_float(row.get(field_name))
        if value is not None and value > 0:
            values.append(value)
    if not values:
        return
    total = sum(values)
    if abs(total - WEIGHT_SUM_TARGET) <= WEIGHT_NORMALIZE_EPSILON:
        return
    if abs(total - WEIGHT_SUM_TARGET) > WEIGHT_SUM_TOLERANCE:
        return
    factor = WEIGHT_SUM_TARGET / total
    for row in rows:
        value = to_float(row.get(field_name))
        if value is None:
            continue
        if value > 0:
            row[field_name] = round(value * factor, 6)
        elif abs(value) <= WEIGHT_NORMALIZE_EPSILON:
            row[field_name] = 0.0
    counters[counter_name] += 1


def collect_rebalance_delta_rows(
    conn: sqlite3.Connection,
    channel_id: str,
    summaries: dict[str, dict[str, Any]],
    event_context: dict[str, dict[str, Any]],
    fund_alias_lookup: dict[str, dict[str, Any]],
    exact_bucket: dict[str, dict[str, dict[str, Any]]],
    canonical_bucket: dict[str, dict[str, dict[str, Any]]],
    counters: dict[str, int],
) -> list[dict[str, Any]]:
    rows_by_delta_id: dict[str, dict[str, Any]] = {}
    for path in entity_files(channel_id, "strategy_rebalance_fund_delta"):
        run_id = path.stem
        captured_at = summaries.get(run_id, {}).get("captured_at")
        for row in load_jsonl(path):
            event_id = row["rebalance_event_id"]
            context = event_context.get(event_id)
            if context is None:
                counters["策略调仓明细_缺失事件上下文"] += 1
                continue

            raw_fund_name = normalize_text(row.get("fund_name") or row.get("fund_name_raw"))
            resolved_fund_code, resolved_fund_name, resolved_match_status = resolve_fund_identity(
                row.get("fund_code"),
                raw_fund_name,
                row.get("fund_code_resolve_status"),
                fund_alias_lookup,
                exact_bucket,
                canonical_bucket,
            )
            if resolved_fund_code and not sanitize_fund_code(row.get("fund_code")):
                if resolved_match_status == "manual_override":
                    counters["策略调仓明细_手工补码命中"] += 1
                elif resolved_match_status.startswith("local_exact"):
                    counters["策略调仓明细_本地精确补码命中"] += 1
                elif resolved_match_status.startswith("alias:"):
                    counters["策略调仓明细_别名补码命中"] += 1
                elif resolved_match_status.startswith("local_canonical"):
                    counters["策略调仓明细_本地标准名补码命中"] += 1

            before_weight = to_percent(channel_id, row.get("before_weight"))
            after_weight = to_percent(channel_id, row.get("after_weight"))
            weight_delta = to_percent(channel_id, row.get("weight_delta"))
            group_name = normalize_fund_group_name(row.get("fund_group_name"))
            action_type = normalize_text(row.get("action_type"))
            fund_identity = (
                sanitize_fund_code(resolved_fund_code)
                or canonical_fund_name(resolved_fund_name or raw_fund_name)
                or normalize_fund_name(resolved_fund_name or raw_fund_name)
                or ""
            )
            delta_id = make_delta_id(
                event_id=event_id,
                fund_identity=fund_identity,
                group_name=group_name,
                before_weight=before_weight,
                after_weight=after_weight,
                weight_delta=weight_delta,
                action_type=action_type,
            )
            mapped = {
                "调仓明细ID": delta_id,
                "调仓事件ID": event_id,
                "统一策略ID": context["统一策略ID"],
                "渠道ID": context["渠道ID"],
                "渠道策略ID": context["渠道策略ID"],
                "调仓日期": context["调仓日期"],
                "披露日期": context.get("披露日期"),
                "调仓标题": context.get("调仓标题"),
                "基金代码": resolved_fund_code,
                "基金名称": resolved_fund_name or raw_fund_name or sanitize_fund_code(resolved_fund_code) or "未知基金",
                "分组名称": group_name,
                "调前权重_百分比": before_weight,
                "调后权重_百分比": after_weight,
                "权重变化_百分比": weight_delta,
                "调仓动作": action_type,
                "基金代码匹配状态": resolved_match_status,
                "分组调前权重_百分比": to_percent(channel_id, row.get("fund_group_weight_before")),
                "分组调后权重_百分比": to_percent(channel_id, row.get("fund_group_weight_after")),
                "原始快照ID": row.get("source_snapshot_id") or context.get("原始快照ID"),
            }
            existing = rows_by_delta_id.get(delta_id)
            if existing is not None:
                rows_by_delta_id[delta_id] = merge_rebalance_delta_row(existing, mapped)
                counters["策略调仓明细_重复折叠"] += 1
            else:
                rows_by_delta_id[delta_id] = mapped

            upsert_source(
                conn,
                {
                    "统一策略ID": context["统一策略ID"],
                    "渠道ID": context["渠道ID"],
                    "渠道策略ID": context["渠道策略ID"],
                    "文件类型": "strategy_rebalance_fund_delta",
                    "文件路径": normalize_path(path),
                    "采集批次ID": run_id,
                    "采集时间": captured_at,
                },
            )

    rows_by_event: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows_by_delta_id.values():
        rows_by_event[str(row["调仓事件ID"])].append(row)

    cleaned_rows: list[dict[str, Any]] = []
    for event_rows in rows_by_event.values():
        normalize_weight_field(event_rows, "调前权重_百分比", "策略调仓明细_调前权重归一化事件", counters)
        normalize_weight_field(event_rows, "调后权重_百分比", "策略调仓明细_调后权重归一化事件", counters)
        for row in event_rows:
            before_weight = to_float(row.get("调前权重_百分比"))
            after_weight = to_float(row.get("调后权重_百分比"))
            if before_weight is not None and after_weight is not None:
                row["权重变化_百分比"] = round(after_weight - before_weight, 6)
            cleaned_rows.append(row)

    cleaned_rows.sort(key=lambda row: (str(row["统一策略ID"]), str(row["调仓日期"]), str(row["调仓事件ID"]), str(row["调仓明细ID"])))
    return cleaned_rows


def collect_current_holdings(
    channel_id: str,
    fund_alias_lookup: dict[str, dict[str, Any]],
    exact_bucket: dict[str, dict[str, dict[str, Any]]],
    canonical_bucket: dict[str, dict[str, dict[str, Any]]],
    counters: dict[str, int],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    files = entity_files(channel_id, "strategy_fund_snapshot")
    for file_order, path in enumerate(files):
        rows = load_jsonl(path)
        for row in rows:
            if is_public_recommendation_list_not_holding(row):
                counters["策略当前持仓_推荐清单跳过"] += 1
                continue
            strategy_id = row["source_strategy_id"]
            unified_id = unified_strategy_id(channel_id, strategy_id)
            position_date = normalize_date_text(row.get("position_date")) or ""
            current = result.get(unified_id)
            should_replace = False
            if current is None:
                should_replace = True
            elif position_date > current["position_date"]:
                should_replace = True
            elif position_date == current["position_date"] and file_order > current["file_order"]:
                should_replace = True

            resolved_fund_code, resolved_fund_name, resolved_match_status = resolve_fund_identity(
                row.get("fund_code"),
                row.get("fund_name"),
                row.get("fund_code_resolve_status"),
                fund_alias_lookup,
                exact_bucket,
                canonical_bucket,
            )
            if resolved_fund_code and not sanitize_fund_code(row.get("fund_code")):
                counters["策略当前持仓_名称补码命中"] += 1

            extra = row.get("extra") if isinstance(row.get("extra"), dict) else {}
            payload = {
                "统一策略ID": unified_id,
                "渠道ID": channel_id,
                "渠道策略ID": strategy_id,
                "持仓日期": position_date,
                "披露日期": normalize_date_text(row.get("disclosure_date")),
                "基金代码": resolved_fund_code,
                "基金名称": resolved_fund_name or row.get("fund_name"),
                "资产类型": normalize_fund_group_name(row.get("fund_asset_type")),
                "分组名称": normalize_fund_group_name(row.get("fund_group_name")),
                "基金权重_百分比": to_percent(channel_id, row.get("fund_weight")),
                "分组权重_百分比": to_percent(channel_id, row.get("group_weight")),
                "基金净值": row.get("fund_nav"),
                "基金净值日期": normalize_date_text(row.get("fund_nav_date")),
                "最新日涨幅_百分比": to_percent(
                    channel_id,
                    row.get("latest_fund_daily_rate")
                    if row.get("latest_fund_daily_rate") is not None
                    else extra.get("daily_return"),
                ),
                "操作标记": row.get("operation_type"),
                "是否精确权重": normalize_bool(row.get("is_precise_weight")) or 0,
                "置信度": row.get("confidence_level"),
                "访问级别": row.get("access_level"),
                "原始快照ID": row.get("source_snapshot_id") or row.get("snapshot_id"),
            }

            if should_replace:
                result[unified_id] = {
                    "position_date": position_date,
                    "file_order": file_order,
                    "rows": [payload],
                }
            elif position_date == current["position_date"] and file_order == current["file_order"]:
                current["rows"].append(payload)
    return result


def group_rows_for_current_holdings(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row.get("分组名称") or "未分组"].append(row)

    results: list[dict[str, Any]] = []
    for group_name, items in grouped.items():
        group_weight = next((item.get("分组权重_百分比") for item in items if item.get("分组权重_百分比") is not None), None)
        if group_weight is None:
            weights = [float(item["基金权重_百分比"]) for item in items if item.get("基金权重_百分比") is not None]
            group_weight = round(sum(weights), 6) if weights else None
        first = items[0]
        results.append(
            {
                "统一策略ID": first["统一策略ID"],
                "渠道ID": first["渠道ID"],
                "渠道策略ID": first["渠道策略ID"],
                "持仓日期": first["持仓日期"],
                "披露日期": first.get("披露日期"),
                "分组名称": group_name,
                "分组权重_百分比": group_weight,
                "基金数量": len(items),
                "原始快照ID": first.get("原始快照ID"),
            }
        )
    return sorted(results, key=lambda row: (row["统一策略ID"], -(row.get("分组权重_百分比") or 0), row["分组名称"]))


def backfill_current_holding_weights_from_rebalance(conn: sqlite3.Connection, channel_id: str) -> int:
    conn.execute(
        """
        WITH valid_events AS (
            SELECT e."统一策略ID", e."调仓事件ID", e."调仓日期", COALESCE(e."事件序号", 0) AS event_seq
            FROM "策略调仓事件" e
            JOIN "策略调仓明细" d ON d."调仓事件ID" = e."调仓事件ID"
            WHERE e."渠道ID" = ?
            GROUP BY e."统一策略ID", e."调仓事件ID", e."调仓日期", e."事件序号"
            HAVING SUM(CASE WHEN d."调后权重_百分比" IS NOT NULL THEN 1 ELSE 0 END) > 0
               AND SUM(COALESCE(d."调后权重_百分比", 0)) BETWEEN 99 AND 101
        ),
        ranked_weights AS (
            SELECT h.rowid AS holding_rowid,
                   d."调后权重_百分比" AS backfilled_weight,
                   ROW_NUMBER() OVER (
                       PARTITION BY h.rowid
                       ORDER BY v."调仓日期" DESC, v.event_seq ASC, v."调仓事件ID" DESC
                   ) AS rn
            FROM "策略当前持仓" h
            JOIN valid_events v
              ON v."统一策略ID" = h."统一策略ID"
             AND v."调仓日期" <= h."持仓日期"
            JOIN "策略调仓明细" d
              ON d."调仓事件ID" = v."调仓事件ID"
             AND (
                 (h."基金代码" IS NOT NULL AND h."基金代码" <> '' AND d."基金代码" = h."基金代码")
                 OR (h."基金名称" IS NOT NULL AND h."基金名称" <> '' AND d."基金名称" = h."基金名称")
             )
            WHERE h."渠道ID" = ?
              AND h."基金权重_百分比" IS NULL
              AND d."调后权重_百分比" IS NOT NULL
        )
        UPDATE "策略当前持仓"
           SET "基金权重_百分比" = (
                   SELECT backfilled_weight
                   FROM ranked_weights
                   WHERE ranked_weights.holding_rowid = "策略当前持仓".rowid
                     AND ranked_weights.rn = 1
               ),
               "是否精确权重" = 0,
               "置信度" = CASE
                   WHEN COALESCE("置信度", '') = '' THEN 'rebalance_weight_backfill'
                   WHEN "置信度" LIKE '%rebalance_weight_backfill%' THEN "置信度"
                   ELSE "置信度" || '+rebalance_weight_backfill'
               END
         WHERE rowid IN (
             SELECT holding_rowid
             FROM ranked_weights
             WHERE rn = 1
         )
        """,
        (channel_id, channel_id),
    )
    return int(conn.execute("SELECT changes()").fetchone()[0] or 0)


def import_channels(conn: sqlite3.Connection, channels: list[str]) -> dict[str, int]:
    counters: dict[str, int] = defaultdict(int)
    for row in load_alias_report_rows(channels):
        upsert_fund_alias(conn, row)
        counters["基金名称映射"] += 1

    fund_alias_lookup = load_fund_alias_lookup(conn)
    exact_bucket, canonical_bucket = build_local_fund_lookup(conn, channels)

    event_context: dict[str, dict[str, Any]] = {}

    for channel_id in channels:
        metadata = CHANNEL_METADATA[channel_id]
        upsert_channel(conn, metadata)
        counters["渠道信息"] += 1

        summaries = summary_files(channel_id)

        for path in entity_files(channel_id, "strategy_master"):
            run_id = path.stem
            captured_at = summaries.get(run_id, {}).get("captured_at")
            for row in load_jsonl(path):
                strategy_id = row["source_strategy_id"]
                unified_id = unified_strategy_id(channel_id, strategy_id)
                upsert_strategy(
                    conn,
                    {
                        "统一策略ID": unified_id,
                        "渠道ID": channel_id,
                        "渠道策略ID": strategy_id,
                        "策略名称": row["strategy_name"],
                        "投顾机构": canonical_advisor_institution(row.get("advisor_name")),
                        "策略类型": row.get("strategy_type"),
                        "风险等级": row.get("risk_level"),
                        "成立日期": normalize_date_text(row.get("launch_date")),
                        "建议持有时长": row.get("suggested_holding_period"),
                        "起投金额": minimum_amount_from_master(row),
                        "投顾费率": row.get("advisory_fee_rate"),
                        "业绩基准": row.get("benchmark"),
                        "标签JSON": json_text(row.get("tags") or []),
                        "策略状态": row.get("status"),
                        "策略描述": row.get("strategy_description"),
                        "原始来源URL": row.get("source_url"),
                        "原始快照ID": row.get("source_snapshot_id"),
                        "首次入库时间": row.get("first_seen_at"),
                        "最近入库时间": row.get("last_seen_at"),
                    },
                )
                upsert_source(
                    conn,
                    {
                        "统一策略ID": unified_id,
                        "渠道ID": channel_id,
                        "渠道策略ID": strategy_id,
                        "文件类型": "strategy_master",
                        "文件路径": normalize_path(path),
                        "采集批次ID": run_id,
                        "采集时间": captured_at,
                    },
                )
                counters["策略信息"] += 1
                for risk_row in disclosed_risk_rows_from_master(
                    channel_id,
                    unified_id,
                    strategy_id,
                    row,
                    captured_at,
                ):
                    if upsert_disclosed_risk_metric(conn, risk_row):
                        counters["策略披露风险指标"] += 1
                    else:
                        counters["策略披露风险指标_策略缺失跳过"] += 1

        daily_rows = repair_daily_performance_rows(
            collect_daily_performance_rows(conn, channel_id, summaries, counters),
            counters,
        )
        for row in daily_rows:
            upsert_daily_performance(conn, row)
            counters["策略日度业绩"] += 1

        for path in entity_files(channel_id, "strategy_performance_interval"):
            run_id = path.stem
            captured_at = summaries.get(run_id, {}).get("captured_at")
            for row in load_jsonl(path):
                strategy_id = row["source_strategy_id"]
                unified_id = unified_strategy_id(channel_id, strategy_id)
                upsert_interval_performance(
                    conn,
                    {
                        "统一策略ID": unified_id,
                        "渠道ID": channel_id,
                        "渠道策略ID": strategy_id,
                        "统计日期": normalize_date_text(row["as_of_date"]),
                        "区间代码": row["interval_code"],
                        "区间名称": row["interval_label"],
                        "策略收益率_百分比": to_percent(channel_id, row.get("return_value")),
                        "基准收益率_百分比": to_percent(channel_id, row.get("benchmark_return")),
                        "原始快照ID": row.get("source_snapshot_id"),
                    },
                )
                upsert_source(
                    conn,
                    {
                        "统一策略ID": unified_id,
                        "渠道ID": channel_id,
                        "渠道策略ID": strategy_id,
                        "文件类型": "strategy_performance_interval",
                        "文件路径": normalize_path(path),
                        "采集批次ID": run_id,
                        "采集时间": captured_at,
                    },
                )
                counters["策略区间业绩"] += 1

        channel_event_context: dict[str, dict[str, Any]] = {}
        for path in entity_files(channel_id, "strategy_rebalance_event"):
            run_id = path.stem
            captured_at = summaries.get(run_id, {}).get("captured_at")
            for row in load_jsonl(path):
                strategy_id = row["source_strategy_id"]
                unified_id = unified_strategy_id(channel_id, strategy_id)
                rebalance_date = normalize_date_text(row.get("rebalance_date"))
                if not rebalance_date:
                    counters["策略调仓事件_缺失调仓日期跳过"] += 1
                    continue
                mapped = {
                    "调仓事件ID": row["rebalance_event_id"],
                    "统一策略ID": unified_id,
                    "渠道ID": channel_id,
                    "渠道策略ID": strategy_id,
                    "调仓日期": rebalance_date,
                    "上次仓位日期": normalize_date_text(row.get("previous_position_date")),
                    "本次仓位日期": normalize_date_text(row.get("new_position_date")),
                    "披露日期": normalize_date_text(row.get("disclosure_date")),
                    "调仓标题": row.get("event_title"),
                    "调仓原因": row.get("event_reason"),
                    "上次仓位日期是否推断": normalize_bool(row.get("previous_position_date_is_inferred")),
                    "事件序号": row.get("event_sequence"),
                    "事件时间": row.get("event_time"),
                    "载荷类型": row.get("payload_type"),
                    "置信度": row.get("confidence_level"),
                    "原始快照ID": row.get("source_snapshot_id"),
                }
                event_context[mapped["调仓事件ID"]] = mapped
                channel_event_context[mapped["调仓事件ID"]] = mapped
                upsert_source(
                    conn,
                    {
                        "统一策略ID": unified_id,
                        "渠道ID": channel_id,
                        "渠道策略ID": strategy_id,
                        "文件类型": "strategy_rebalance_event",
                        "文件路径": normalize_path(path),
                        "采集批次ID": run_id,
                        "采集时间": captured_at,
                    },
                )
                counters["策略调仓事件_原始"] += 1

        rebalance_delta_rows = collect_rebalance_delta_rows(
            conn,
            channel_id,
            summaries,
            event_context,
            fund_alias_lookup,
            exact_bucket,
            canonical_bucket,
            counters,
        )
        canonical_events, rebalance_delta_rows = canonicalize_rebalance_events_and_deltas(
            channel_event_context,
            rebalance_delta_rows,
            counters,
        )
        for row in canonical_events:
            upsert_rebalance_event(conn, row)
            counters["策略调仓事件"] += 1
        for row in rebalance_delta_rows:
            upsert_rebalance_delta(conn, row)
            counters["策略调仓明细"] += 1

        current_holdings = collect_current_holdings(
            channel_id,
            fund_alias_lookup,
            exact_bucket,
            canonical_bucket,
            counters,
        )
        for unified_id, payload in current_holdings.items():
            rows = payload["rows"]
            if channel_id == "gffunds":
                rows = [row for row in rows if (row.get("基金权重_百分比") or 0) > 0]
            for row in rows:
                upsert_current_holding(conn, row)
                counters["策略当前持仓"] += 1
            for group_row in group_rows_for_current_holdings(rows):
                upsert_current_holding_group(conn, group_row)
                counters["策略当前持仓分组"] += 1

        import_channel_historical_holdings(conn, channel_id, summaries, counters)

        backfilled = backfill_current_holding_weights_from_rebalance(conn, channel_id)
        counters["策略当前持仓_调仓权重回填"] += backfilled

        for path in entity_files(channel_id, "fund_public_dim"):
            for row in load_jsonl(path):
                fund_code = sanitize_fund_code(row.get("fund_code"))
                if not fund_code:
                    counters["基金信息_缺失基金代码"] += 1
                    continue
                upsert_fund_info(
                    conn,
                    {
                        "基金代码": fund_code,
                        "基金名称": normalize_text(row.get("fund_name")) or row["fund_name"],
                        "基金公司": row.get("fund_company"),
                        "基金类型": row.get("fund_type"),
                        "跟踪指数": row.get("tracking_index"),
                        "主题标签JSON": json_text(row.get("theme_tags")),
                        "最新净值": row.get("latest_nav"),
                        "最新净值日期": normalize_date_text(row.get("latest_nav_date")),
                        "基金状态": row.get("status"),
                        "数据来源": row.get("source") or f"{channel_id}_fund_public_dim",
                    },
                )
                counters["基金信息"] += 1
        import_channel_signal_entities(conn, channel_id, summaries, counters)
    import_raw_disclosed_risk_metrics(conn, channels, counters)
    return counters


def query_scalar(conn: sqlite3.Connection, sql: str) -> int:
    return int(conn.execute(sql).fetchone()[0])


def validate_strategy_catalog_summaries(
    conn: sqlite3.Connection,
    summary_paths: list[Path],
    channels: list[str],
) -> dict[str, dict[str, Any]]:
    """Verify catalog and newly discovered strategy IDs inside the load transaction."""

    allowed_channels = set(channels)
    validations: dict[str, dict[str, Any]] = {}
    for summary_path in summary_paths:
        if not summary_path.is_file():
            raise RuntimeError(f"strategy catalog summary not found: {summary_path}")
        summary = json.loads(summary_path.read_text(encoding="utf-8-sig"))
        if not isinstance(summary, dict):
            raise RuntimeError(f"strategy catalog summary is not an object: {summary_path}")
        channel_id = str(summary.get("channel_id") or "").strip()
        if not channel_id or channel_id not in allowed_channels:
            raise RuntimeError(
                "strategy catalog summary channel mismatch: "
                f"path={summary_path}, channel={channel_id}, allowed={sorted(allowed_channels)}"
            )
        catalog_ids = sorted(
            {
                str(value or "").strip()
                for value in summary.get("catalog_strategy_ids") or []
                if str(value or "").strip()
            }
        )
        new_ids = sorted(
            {
                str(value or "").strip()
                for value in summary.get("catalog_new_strategy_ids") or []
                if str(value or "").strip()
            }
        )
        invalid_new_ids = sorted(set(new_ids) - set(catalog_ids))
        if invalid_new_ids:
            raise RuntimeError(
                f"new strategy IDs are outside catalog for {channel_id}: {invalid_new_ids}"
            )
        loaded_ids = {
            str(row[0] or "").strip()
            for row in conn.execute(
                'SELECT "渠道策略ID" FROM "策略信息" WHERE "渠道ID"=?',
                (channel_id,),
            ).fetchall()
            if str(row[0] or "").strip()
        }
        missing_catalog_ids = sorted(set(catalog_ids) - loaded_ids)
        missing_new_ids = sorted(set(new_ids) - loaded_ids)
        source_latest_nav_date = str(summary.get("source_latest_nav_date") or "").strip()
        expected_latest_nav_strategy_total = int(
            summary.get("latest_nav_date_strategy_total") or 0
        )
        loaded_latest_nav_date = str(
            conn.execute(
                'SELECT COALESCE(MAX("交易日期"), \'\') FROM "策略日度业绩" WHERE "渠道ID"=?',
                (channel_id,),
            ).fetchone()[0]
            or ""
        ).strip()
        loaded_latest_nav_strategy_total = 0
        if source_latest_nav_date:
            loaded_latest_nav_strategy_total = int(
                conn.execute(
                    '''SELECT COUNT(DISTINCT "渠道策略ID")
                       FROM "策略日度业绩"
                       WHERE "渠道ID"=? AND "交易日期"=?''',
                    (channel_id, source_latest_nav_date),
                ).fetchone()[0]
            )
        performance_freshness_passed = bool(
            not source_latest_nav_date
            or (
                loaded_latest_nav_date == source_latest_nav_date
                and loaded_latest_nav_strategy_total >= expected_latest_nav_strategy_total
            )
        )
        validations[channel_id] = {
            "summaryPath": str(summary_path.resolve()),
            "catalogStrategyTotal": len(catalog_ids),
            "catalogNewStrategyTotal": len(new_ids),
            "loadedCatalogStrategyTotal": len(set(catalog_ids) & loaded_ids),
            "loadedNewStrategyTotal": len(set(new_ids) & loaded_ids),
            "missingCatalogStrategyIds": missing_catalog_ids,
            "missingNewStrategyIds": missing_new_ids,
            "sourceLatestNavDate": source_latest_nav_date or None,
            "loadedLatestNavDate": loaded_latest_nav_date or None,
            "expectedLatestNavStrategyTotal": expected_latest_nav_strategy_total,
            "loadedLatestNavStrategyTotal": loaded_latest_nav_strategy_total,
            "performanceFreshnessPassed": performance_freshness_passed,
            "passed": not missing_catalog_ids
            and not missing_new_ids
            and performance_freshness_passed,
        }
        if missing_catalog_ids or missing_new_ids or not performance_freshness_passed:
            raise RuntimeError(
                f"strategy catalog load verification failed for {channel_id}: "
                f"missing_catalog={missing_catalog_ids}, missing_new={missing_new_ids}, "
                f"source_latest_nav_date={source_latest_nav_date or None}, "
                f"loaded_latest_nav_date={loaded_latest_nav_date or None}, "
                f"latest_nav_strategy_total="
                f"{loaded_latest_nav_strategy_total}/{expected_latest_nav_strategy_total}"
            )
    return validations


def main() -> None:
    args = parse_args()
    global NORMALIZED_ROOT
    NORMALIZED_ROOT = args.normalized_root.resolve()
    unknown_channels = [channel for channel in args.channels if channel not in CHANNEL_METADATA]
    if unknown_channels:
        raise SystemExit(f"Unsupported channels: {', '.join(unknown_channels)}")

    conn = init_db(args.db_path, args.schema_path, args.keep_existing_db)
    try:
        if conn.in_transaction:
            conn.commit()
        conn.execute("PRAGMA busy_timeout=120000")
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("BEGIN IMMEDIATE")
        try:
            if args.keep_existing_db:
                reset_channels = None if set(args.channels) == set(ALL_CHANNELS) else list(args.channels)
                reset_analysis_tables(conn, reset_channels)
            counters = import_channels(conn, args.channels)
            catalog_validations = validate_strategy_catalog_summaries(
                conn,
                args.strategy_catalog_summary,
                args.channels,
            )
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.execute("PRAGMA foreign_keys=ON")

        print(f"loaded database: {args.db_path}")
        print(f"channels: {', '.join(args.channels)}")
        print("ingest counters:")
        for key in sorted(counters):
            print(f"  {key}={counters[key]}")
        if catalog_validations:
            print("strategy catalog load validations:")
            for channel_id, validation in sorted(catalog_validations.items()):
                print(
                    f"  {channel_id}: catalog={validation['catalogStrategyTotal']} "
                    f"new={validation['catalogNewStrategyTotal']} passed={validation['passed']}"
                )

        print("table row counts:")
        table_names = [
            "渠道信息",
            "策略信息",
            "策略日度业绩",
            "策略区间业绩",
            "策略披露风险指标",
            "策略当前持仓分组",
            "策略当前持仓",
            "策略历史持仓",
            "策略调仓事件",
            "策略调仓明细",
            "信号策略事件",
            "信号策略基金指令",
            "基金信息",
            "数据来源清单",
        ]
        for table_name in table_names:
            count = query_scalar(conn, f'SELECT COUNT(*) FROM "{table_name}"')
            print(f"  {table_name}={count}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
