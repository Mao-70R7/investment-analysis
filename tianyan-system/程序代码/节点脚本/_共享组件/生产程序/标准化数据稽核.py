from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import re
import sqlite3
import unicodedata
from collections import Counter, defaultdict
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from business_naming import canonical_advisor_institution


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
DEFAULT_SITE_DIR = PROJECT_ROOT / "site" / "basic_data"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "data_audit"
DEFAULT_RULES_PATH = PROJECT_ROOT / "config" / "数据稽核规则规范.json"
DEFAULT_FIELD_RULES_PATH = PROJECT_ROOT / "config" / "系统字段检查规则.json"

ASSIGNMENT_RE = re.compile(r"=\s*(\{.*\});?\s*$", re.S)
F10_INCEPTION_LABEL_RE = re.compile(r'<label>\s*成立日期[:：]\s*<span>\s*(\d{4}-\d{2}-\d{2})', re.I)
EQUITY_ASSET_RE = re.compile(r"A股|港股|美股|新兴市场|其他发达市场|海外权益|存托凭证|REIT")
DOMESTIC_MSCI_RE = re.compile(r"MSCI\s*(沪深\s*300|中国A股|中国)", re.I)
STRONG_OVERSEAS_TEXT_RE = re.compile(
    r"QDII|海外|港股|美股|恒生|纳斯达克|纳指|标普|S&P|美国|印度|越南|日经|日本|德国|DAX|"
    r"全球(?!版)(?:资产|配置|精选|优选|权益|股票|债券|多元|组合|市场)?",
    re.I,
)
OVERSEAS_BENCHMARK_RE = re.compile(
    r"QDII|海外|港股|美股|恒生|纳斯达克|纳指|标普|S&P|MSCI\s*(全球|发达|新兴|海外)|美国|印度|越南|日经|日本|德国|DAX",
    re.I,
)
DERIVED_ENTITY_FIELDS = {"研报大类资产", "研报A股行业", "行业主题", "行业大类", "权益行业主题", "权益行业大类"}
WEAK_ENTITY_SOURCE_FIELDS = DERIVED_ENTITY_FIELDS | {"基金名称", "基金类型", "基金二级分类", "基金同类分组", "基金分类依据"}
EXPOSURE_SENSITIVE_ENTITY_TYPES = {"资产", "资产大类", "地域", "行业主题", "主题", "风格", "产品形态", "指数"}
NON_ANCHORED_QUANT_ENTITY_TYPES = {"资产", "资产大类", "地域", "行业主题", "主题", "风格", "产品形态", "指数"}
RULE_CATALOG: dict[str, dict[str, Any]] = {}
DISABLED_CHANNEL_IDS: set[str] = set()

CRITICAL_BUSINESS_KEYS = {
    "策略信息": ["统一策略ID"],
    "策略关系": ["子策略ID"],
    "策略当前持仓": ["统一策略ID", "持仓日期", "基金代码", "基金名称"],
    "策略调仓事件": ["渠道ID", "统一策略ID", "调仓日期", "本次仓位日期", "调仓标题", "调仓原因"],
    "策略调仓明细": ["调仓事件ID", "基金代码", "基金名称", "调仓动作"],
    "FOF基金F10基准": ["基金代码"],
    "FOF基准细分分类": ["基金代码"],
    "FOF产品绩效快照": ["基金代码"],
    "公募基金产品绩效快照": ["基金代码"],
    "基金经济暴露快照": ["基金代码"],
    "基金分类快照": ["基金代码", "报告期"],
    "基金季度资产配置": ["基金代码", "报告期"],
    "基金季度股票持仓": ["基金代码", "报告期", "股票代码", "股票名称"],
}
PAGE_DISPLAY_CHANNEL_IDS = {"ttfund", "gffunds", "gfsec_fima", "gfsec_robot", "qieman"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="标准化稽核投顾监控系统数据库和 basic_data 页面数据包。")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--site-dir", type=Path, default=DEFAULT_SITE_DIR)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--rules-path", type=Path, default=DEFAULT_RULES_PATH)
    parser.add_argument("--field-rules-path", type=Path, default=DEFAULT_FIELD_RULES_PATH)
    parser.add_argument("--fail-on-error", action="store_true", help="存在 error 时返回非 0。")
    return parser.parse_args()


def now_text() -> str:
    return datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z")


def business_day_lag(older: Any, newer: Any) -> int | None:
    try:
        start = date.fromisoformat(str(older or "")[:10])
        end = date.fromisoformat(str(newer or "")[:10])
    except ValueError:
        return None
    if start >= end:
        return 0
    lag = 0
    cursor = start
    while cursor < end:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:
            lag += 1
    return lag


def normalize_benchmark_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "").strip()).lower()
    text = text.replace("×", "*").replace("✕", "*").replace("·", "")
    return re.sub(r"[\s,，;；。]+", "", text)


def read_js_object(path: Path) -> Any:
    if path.suffix.lower() == ".gz":
        with gzip.open(path, "rt", encoding="utf-8-sig") as handle:
            text = handle.read()
    else:
        text = path.read_text(encoding="utf-8-sig")
    match = ASSIGNMENT_RE.search(text)
    if not match:
        raise ValueError(f"cannot parse JS data assignment: {path}")
    return json.loads(match.group(1))


def parse_exposure_text(value: Any) -> dict[str, float]:
    if isinstance(value, dict):
        items = value.items()
    else:
        text = str(value or "").strip()
        if not text:
            return {}
        items = []
        for part in re.split(r"[、,，;；]+", text):
            match = re.match(r"^(.+?)([-+]?\d+(?:\.\d+)?)%$", part.strip())
            if match:
                items.append((match.group(1).strip(), match.group(2)))
    output: dict[str, float] = {}
    for key, raw_value in items:
        try:
            number = float(raw_value)
        except (TypeError, ValueError):
            continue
        output[str(key)] = number
    return output


def exposure_sum(value: Any) -> float:
    return round(sum(parse_exposure_text(value).values()), 4)


def exposure_matches(left: Any, right: Any, tolerance: float = 0.051) -> bool:
    left_map = parse_exposure_text(left)
    right_map = parse_exposure_text(right)
    if set(left_map) != set(right_map):
        return False
    return all(abs(left_map[key] - right_map[key]) <= tolerance for key in left_map)


def equity_asset_share(value: Any) -> float:
    return round(sum(v for k, v in parse_exposure_text(value).items() if EQUITY_ASSET_RE.search(k)), 4)


def row_value(row: list[Any], index: dict[str, int], *names: str, default: Any = "") -> Any:
    for name in names:
        pos = index.get(name)
        if pos is not None and 0 <= pos < len(row):
            return row[pos]
    return default


def number_value(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def overseas_text_scope(*values: Any) -> str:
    text = " ".join(str(value or "") for value in values if value is not None)
    text = text.replace("兴证全球", "兴证")
    return DOMESTIC_MSCI_RE.sub("", text)


def has_strong_overseas_text(*values: Any) -> bool:
    return bool(STRONG_OVERSEAS_TEXT_RE.search(overseas_text_scope(*values)))


def has_overseas_benchmark_text(value: Any) -> bool:
    return bool(OVERSEAS_BENCHMARK_RE.search(overseas_text_scope(value)))


def load_rule_catalog(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    rules = payload.get("rules") if isinstance(payload, dict) else {}
    return rules if isinstance(rules, dict) else {}


def load_json_object(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_f10_inception_date(path_value: Any) -> str:
    text = str(path_value or "").strip()
    if not text:
        return ""
    path = Path(text)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    if not path.exists():
        return ""
    match = F10_INCEPTION_LABEL_RE.search(path.read_text(encoding="utf-8", errors="ignore"))
    return match.group(1) if match else ""


def infer_rule_id(scope: str, item: str) -> str:
    if item == "数据库缺失":
        return "SQLITE_DB_MISSING"
    if item == "quick_check失败":
        return "SQLITE_QUICK_CHECK"
    if item == "字段名重复":
        return "SQLITE_DUPLICATE_COLUMNS" if scope.startswith("sqlite.") else "PAGE_PACK_DUPLICATE_FIELDS"
    if item == "业务键重复":
        return "SQLITE_BUSINESS_KEY_DUPLICATE" if scope.startswith("sqlite.") else "PAGE_PACK_BUSINESS_KEY_DUPLICATE"
    if item == "行宽不一致":
        return "PAGE_PACK_ROW_WIDTH"
    if item == "整行重复":
        return "PAGE_PACK_FULL_ROW_DUPLICATE"
    if item == "文件缺失":
        return "PAGE_PACK_FILE_MISSING"
    if item == "基金代码重复":
        return "FUND_DETAIL_CODE_DUPLICATE"
    if item == "基金暴露口径异常":
        return "FUND_DETAIL_EXPOSURE_INVALID"
    if item == "基金详情经济暴露未同步":
        return "FUND_DETAIL_EXPOSURE_SNAPSHOT_STALE"
    if item == "经济暴露快照异常":
        return "FUND_ECONOMIC_EXPOSURE_INVALID"
    if item == "派生标签暴露虚高":
        return "AI_DERIVED_ENTITY_HIGH_EXPOSURE"
    if item == "目标盈标签缺强证据":
        return "BUSINESS_QUALITY_TARGET_PROFIT_EVIDENCE"
    return "UNCLASSIFIED_AUDIT_RULE"


def add_issue(
    issues: list[dict[str, Any]],
    severity: str,
    scope: str,
    item: str,
    detail: str,
    sample: Any = None,
    *,
    rule_id: str | None = None,
) -> None:
    resolved_rule_id = rule_id or infer_rule_id(scope, item)
    rule = RULE_CATALOG.get(resolved_rule_id, {})
    issues.append(
        {
            "ruleId": resolved_rule_id,
            "severity": severity,
            "scope": scope,
            "item": item,
            "detail": detail,
            "原因说明": rule.get("原因说明") or "该问题尚未沉淀原因说明，需要补充到 config/数据稽核规则规范.json。",
            "优化建议": rule.get("优化建议") or "确认异常来源后，补充可执行修复建议并更新稽核规则规范。",
            "修复责任脚本": rule.get("修复责任脚本") or "待补充",
            "修复责任节点": rule.get("修复责任节点") or "data_audit",
            "sample": sample,
        }
    )


def duplicate_names(names: list[str]) -> list[str]:
    counts = Counter(names)
    return sorted(name for name, count in counts.items() if count > 1)


def has_target_profit_evidence(text: str) -> bool:
    normalized = str(text or "").strip()
    if not normalized:
        return False
    strong_brand = re.search(r"目标盈|小目标|小赢家|小杏运|步步高|小星愿|小盈加|智盈|智慧目标投|小常乐|常乐", normalized)
    explicit_goal = re.search(
        r"目标收益|收益目标|绝对收益目标|目标止盈|止盈目标|达标即止盈|达标止盈|止盈达标|止盈提醒|达到目标|目标达成|达标退出|达标赎回",
        normalized,
    )
    lifecycle = re.search(
        r"期次|第[零一二三四五六七八九十百千万\d]+期|\d{1,2}期|到期|期满|运作期|封闭期|续作|赎回|退出|发售|发行|自动终止|stopped|两年期|一年期|年中版|新年特供",
        normalized,
        re.I,
    )
    return bool(strong_brand or (explicit_goal and lifecycle))


TARGET_PROFIT_PERIOD_RE = re.compile(r"第?[零〇一二三四五六七八九十百千万\d]{1,5}期|\d{6,8}$")


def normalize_target_profit_series(name: Any) -> str:
    text = str(name or "").strip()
    if not text or not TARGET_PROFIT_PERIOD_RE.search(text):
        return ""
    text = TARGET_PROFIT_PERIOD_RE.sub("", text)
    text = re.sub(r"目标盈\s*\d{6,8}$", "目标盈", text)
    text = re.sub(r"天天\d{1,4}", "天天", text)
    text = re.sub(r"[（）()]([^（）()]{1,16}?版)[（）()]", "", text)
    text = text.replace("年中版", "").replace("新年特供", "")
    text = re.sub(r"\s+", "", text)
    return text.strip()


def audit_compact_rows(
    issues: list[dict[str, Any]],
    pack_name: str,
    fields: list[str],
    rows: list[list[Any]],
    *,
    duplicate_key_fields: list[str] | None = None,
    max_samples: int = 20,
) -> None:
    dup_fields = duplicate_names(fields)
    if dup_fields:
        add_issue(issues, "error", pack_name, "字段名重复", "同一数据包字段名必须唯一。", dup_fields[:max_samples])
    width = len(fields)
    bad_width = [idx for idx, row in enumerate(rows) if len(row) != width]
    if bad_width:
        add_issue(issues, "error", pack_name, "行宽不一致", f"{len(bad_width)} 行字段数不等于 fields 长度 {width}。", bad_width[:max_samples])

    seen: dict[str, int] = {}
    duplicate_rows = []
    for idx, row in enumerate(rows):
        digest = hashlib.sha1(json.dumps(row, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        if digest in seen:
            duplicate_rows.append({"first": seen[digest], "duplicate": idx})
            if len(duplicate_rows) >= max_samples:
                break
        else:
            seen[digest] = idx
    if duplicate_rows:
        add_issue(issues, "warn", pack_name, "整行重复", "同一页面数据包存在完全相同记录。", duplicate_rows)

    if duplicate_key_fields:
        index = {field: pos for pos, field in enumerate(fields)}
        if all(field in index for field in duplicate_key_fields):
            counter: Counter[tuple[Any, ...]] = Counter()
            for row in rows:
                counter[tuple(row[index[field]] if index[field] < len(row) else None for field in duplicate_key_fields)] += 1
            duplicated = [{"key": key, "count": count} for key, count in counter.items() if count > 1 and any(key)]
            if duplicated:
                add_issue(
                    issues,
                    "error",
                    pack_name,
                    "业务键重复",
                    f"业务键 {duplicate_key_fields} 不应重复。",
                    duplicated[:max_samples],
                )


def audit_fund_detail_pack(issues: list[dict[str, Any]], site_dir: Path) -> None:
    path = site_dir / "data" / "fund_detail_pack.js"
    if not path.exists():
        add_issue(issues, "error", "fund_detail_pack", "文件缺失", str(path))
        return
    pack = read_js_object(path)
    fields = pack.get("fundFields") or []
    rows = pack.get("funds") or []
    audit_compact_rows(issues, "fund_detail_pack.funds", fields, rows)
    idx = {field: pos for pos, field in enumerate(fields)}
    valid_code_counter: Counter[str] = Counter()
    for row in rows:
        code = str(row[idx.get("基金代码", 0)] if "基金代码" in idx and idx["基金代码"] < len(row) else "")
        if re.fullmatch(r"\d{6}", code):
            valid_code_counter[code] += 1
    duplicated_codes = [{"基金代码": code, "count": count} for code, count in valid_code_counter.items() if count > 1]
    if duplicated_codes:
        add_issue(issues, "error", "fund_detail_pack.funds", "基金代码重复", "标准 6 位基金代码在基金详情包中必须唯一。", duplicated_codes[:20])
    samples = []
    for row in rows:
        code = str(row[idx.get("基金代码", 0)] or "")
        name = str(row[idx.get("基金名称", 1)] or "")
        asset_text = row[idx["经济资产暴露"]] if "经济资产暴露" in idx and idx["经济资产暴露"] < len(row) else ""
        industry_text = row[idx["经济行业暴露"]] if "经济行业暴露" in idx and idx["经济行业暴露"] < len(row) else ""
        asset_total = exposure_sum(asset_text)
        industry_total = exposure_sum(industry_text)
        equity_share = equity_asset_share(asset_text)
        if asset_text and not (98 <= asset_total <= 102):
            samples.append({"基金代码": code, "基金名称": name, "字段": "经济资产暴露", "合计": asset_total, "值": asset_text})
        if industry_text and (industry_total < -0.0001 or industry_total > equity_share + 0.5):
            samples.append(
                {
                    "基金代码": code,
                    "基金名称": name,
                    "字段": "经济行业暴露",
                    "行业合计": industry_total,
                    "权益资产合计": equity_share,
                    "值": industry_text,
                }
            )
        for label, value in parse_exposure_text(industry_text).items():
            if label in {"-", "未识别", "其他"} or value < -0.0001:
                samples.append({"基金代码": code, "基金名称": name, "字段": "经济行业暴露", "异常项": label, "值": value})
        if len(samples) >= 30:
            break
    if samples:
        add_issue(issues, "error", "fund_detail_pack.funds", "基金暴露口径异常", "经济行业暴露必须是基金总资产口径，且不得超过对应权益资产占比。", samples)

    economic_path = site_dir / "data" / "fund_economic_exposure_pack.js"
    if not economic_path.exists():
        return
    economic_pack = read_js_object(economic_path)
    economic_fields = economic_pack.get("fields") or []
    economic_rows = economic_pack.get("rows") or []
    economic_idx = {field: pos for pos, field in enumerate(economic_fields)}
    economic_by_code = {
        str(row_value(row, economic_idx, "基金代码")): row
        for row in economic_rows
        if re.fullmatch(r"\d{6}", str(row_value(row, economic_idx, "基金代码")))
    }
    stale_samples: list[dict[str, Any]] = []
    for row in rows:
        code = str(row_value(row, idx, "基金代码"))
        economic_row = economic_by_code.get(code)
        if economic_row is None:
            continue
        differences: list[str] = []
        if not exposure_matches(row_value(row, idx, "经济资产暴露"), row_value(economic_row, economic_idx, "经济资产暴露")):
            differences.append("经济资产暴露")
        if not exposure_matches(row_value(row, idx, "经济行业暴露"), row_value(economic_row, economic_idx, "经济行业暴露")):
            differences.append("经济行业暴露")
        field_pairs = [
            ("经济资产大类", "标准资产大类"),
            ("经济资产细类", "标准资产细类"),
            ("穿透方法", "穿透方法"),
            ("经济暴露质量状态", "质量状态"),
        ]
        for detail_field, snapshot_field in field_pairs:
            if str(row_value(row, idx, detail_field)).strip() != str(row_value(economic_row, economic_idx, snapshot_field)).strip():
                differences.append(detail_field)
        if differences:
            stale_samples.append(
                {
                    "基金代码": code,
                    "基金名称": row_value(row, idx, "基金名称"),
                    "差异字段": differences,
                    "详情包经济资产暴露": row_value(row, idx, "经济资产暴露"),
                    "快照经济资产暴露": row_value(economic_row, economic_idx, "经济资产暴露"),
                }
            )
        if len(stale_samples) >= 30:
            break
    if stale_samples:
        add_issue(
            issues,
            "error",
            "fund_detail_pack.funds",
            "基金详情经济暴露未同步",
            "基金详情包必须与同批次基金经济暴露权威包一致，禁止继续展示旧分类或旧穿透结果。",
            stale_samples,
        )


def read_last_js_assignment(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    value_pos = text.rfind(" = ")
    if value_pos < 0:
        raise ValueError(f"cannot parse JS assignment: {path}")
    payload = text[value_pos + 3 :].strip()
    if payload.endswith(";"):
        payload = payload[:-1]
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise TypeError(f"expected object assignment: {path}")
    return value


def audit_detail_analysis_modules(issues: list[dict[str, Any]], site_dir: Path) -> None:
    detail_dir = site_dir / "data" / "details"
    scale_errors: list[dict[str, Any]] = []
    rank_missing: list[dict[str, Any]] = []
    rank_missing_count = 0
    prefixes = ("近一月", "近三月", "近6月", "近1年")
    if detail_dir.is_dir():
        for path in detail_dir.glob("*.js"):
            try:
                detail = read_last_js_assignment(path)
            except Exception as exc:
                add_issue(
                    issues,
                    "error",
                    "strategy_details",
                    "策略详情解析失败",
                    f"cannot parse {path}: {exc}",
                    rule_id="PAGE_PACK_FILE_PARSE_FAILED",
                )
                continue
            snapshots = detail.get("positionSnapshots") or []
            current = next(
                (
                    snapshot
                    for snapshot in snapshots
                    if snapshot.get("id") == "current" or snapshot.get("类型") in {"当前仓位", "当前持仓"}
                ),
                {},
            )
            for holding in current.get("holdings") or []:
                try:
                    weight = float(holding.get("权重") or 0)
                except (TypeError, ValueError):
                    weight = 0
                if weight <= 0:
                    continue
                period_returns: list[float] = []
                missing: list[str] = []
                for prefix in prefixes:
                    try:
                        period_return = float(holding.get(f"{prefix}收益"))
                        period_returns.append(period_return)
                    except (TypeError, ValueError):
                        pass
                    try:
                        rank = float(holding.get(f"{prefix}同类排名"))
                        sample = float(holding.get(f"{prefix}同类样本数"))
                    except (TypeError, ValueError):
                        rank = None
                        sample = None
                    if rank is None or sample is None or sample <= 0 or not 1 <= rank <= sample:
                        missing.append(prefix)
                sample_row = {
                    "统一策略ID": detail.get("id"),
                    "策略名称": (detail.get("summary") or {}).get("策略名称"),
                    "基金代码": holding.get("基金代码"),
                    "基金名称": holding.get("基金名称"),
                }
                if sum(value <= -90 for value in period_returns) >= 3:
                    if len(scale_errors) < 30:
                        scale_errors.append({**sample_row, "区间收益": period_returns})
                if missing:
                    rank_missing_count += 1
                    if len(rank_missing) < 30:
                        rank_missing.append({**sample_row, "缺失区间": missing})
    if scale_errors:
        add_issue(
            issues,
            "error",
            "strategy_details.current_holdings",
            "复权净值量纲断裂",
            "当前持仓基金多个区间同时出现约-99%收益，四区间同类排名和前50%仓位占比不可用。",
            scale_errors,
            rule_id="PAGE_STRATEGY_HOLDING_ADJUSTED_NAV_SCALE_ERROR",
        )
    if rank_missing_count:
        add_issue(
            issues,
            "warn",
            "strategy_details.current_holdings",
            "同类排名覆盖不完整",
            f"{rank_missing_count}条正权重当前持仓缺少至少一个区间同类排名。",
            rank_missing,
            rule_id="PAGE_STRATEGY_HOLDING_RANK_INCOMPLETE",
        )

    manifest_path = site_dir / "data" / "fund_details" / "_manifest.js"
    if manifest_path.is_file():
        try:
            manifest = read_last_js_assignment(manifest_path)
            fund_count = int(manifest.get("fundCount") or 0)
            nav_count = int(manifest.get("navFundCount") or 0)
            if fund_count > nav_count:
                add_issue(
                    issues,
                    "warn",
                    "fund_details.nav",
                    "基金详情走势图缺失",
                    f"{fund_count - nav_count}只基金没有两个以上可用净值点，无法绘制真实走势图。",
                    {"基金详情数": fund_count, "有净值基金数": nav_count, "缺失数": fund_count - nav_count},
                    rule_id="PAGE_FUND_NAV_CHART_MISSING",
                )
        except Exception as exc:
            add_issue(
                issues,
                "error",
                "fund_details._manifest",
                "基金详情清单解析失败",
                f"cannot parse {manifest_path}: {exc}",
                rule_id="PAGE_PACK_FILE_PARSE_FAILED",
            )


def audit_economic_pack(issues: list[dict[str, Any]], site_dir: Path) -> None:
    path = site_dir / "data" / "fund_economic_exposure_pack.json"
    if not path.exists():
        add_issue(issues, "error", "fund_economic_exposure_pack", "文件缺失", str(path))
        return
    pack = json.loads(path.read_text(encoding="utf-8"))
    fields = pack.get("fields") or []
    rows = pack.get("rows") or []
    audit_compact_rows(issues, "fund_economic_exposure_pack.rows", fields, rows, duplicate_key_fields=["基金代码"])
    idx = {field: pos for pos, field in enumerate(fields)}
    samples = []
    for row in rows:
        code = row[idx.get("基金代码", 0)]
        name = row[idx.get("基金名称", 1)]
        asset = row[idx.get("经济资产暴露", -1)] if "经济资产暴露" in idx else {}
        industry = row[idx.get("经济行业暴露", -1)] if "经济行业暴露" in idx else {}
        asset_total = exposure_sum(asset)
        industry_total = exposure_sum(industry)
        equity_share = equity_asset_share(asset)
        if not (98 <= asset_total <= 102):
            samples.append({"基金代码": code, "基金名称": name, "经济资产暴露合计": asset_total, "经济资产暴露": asset})
        if industry and industry_total > equity_share + 0.5:
            samples.append(
                {
                    "基金代码": code,
                    "基金名称": name,
                    "经济行业暴露合计": industry_total,
                    "权益资产占比": equity_share,
                    "经济行业暴露": industry,
                    "经济资产暴露": asset,
                }
            )
        if any(str(k).strip() in {"", "-", "未识别"} or float(v) < -0.0001 for k, v in parse_exposure_text(industry).items()):
            samples.append({"基金代码": code, "基金名称": name, "异常行业项": industry})
        if len(samples) >= 30:
            break
    if samples:
        add_issue(issues, "error", "fund_economic_exposure_pack.rows", "经济暴露快照异常", "经济资产暴露应约等于100%；经济行业暴露必须是基金总资产口径，且不得超过对应权益资产占比。", samples)


def audit_ai_semantic_index(issues: list[dict[str, Any]], site_dir: Path) -> None:
    path = site_dir / "data" / "ai_semantic_index.js"
    if not path.exists():
        add_issue(issues, "error", "ai_semantic_index", "文件缺失", str(path))
        return
    pack = read_js_object(path)
    fund_entities = pack.get("fundEntities") or {}
    fields = fund_entities.get("fields") or []
    rows = fund_entities.get("rows") or []
    audit_compact_rows(issues, "ai_semantic_index.fundEntities", fields, rows, duplicate_key_fields=["基金代码", "基金名称", "实体Key"])
    idx = {field: pos for pos, field in enumerate(fields)}
    samples = []
    for row in rows:
        source_field = str(row_value(row, idx, "来源字段", "sourceField"))
        entity_type = str(row_value(row, idx, "实体类型", "entityType"))
        exposure = number_value(row_value(row, idx, "暴露比例", "exposurePct", default=0))
        invalid_product_source = entity_type in {"产品形态", "指数"} and source_field not in {
            "基金名称", "基金类型", "基金二级分类", "基金同类分组", "基金分类依据"
        }
        if exposure >= 99 and (
            (source_field in WEAK_ENTITY_SOURCE_FIELDS and entity_type in NON_ANCHORED_QUANT_ENTITY_TYPES)
            or invalid_product_source
        ):
            samples.append(
                {
                    "基金代码": row_value(row, idx, "基金代码", default=""),
                    "基金名称": row_value(row, idx, "基金名称", default=""),
                    "entity": row_value(row, idx, "实体名称", "entityName", default=""),
                    "entityType": entity_type,
                    "sourceField": source_field,
                    "exposurePct": exposure,
                    "sourceValue": row_value(row, idx, "来源值", "sourceValue", default=""),
                }
            )
        if len(samples) >= 30:
            break
    if samples:
        add_issue(issues, "error", "ai_semantic_index.fundEntities", "派生标签暴露虚高", "基金名称、基金类型、分类依据或派生标签不能把资产/行业/主题/风格/地域等非锚定实体覆盖成100%。", samples)

    strategy_entities = pack.get("strategyEntities") or {}
    audit_compact_rows(
        issues,
        "ai_semantic_index.strategyEntities",
        strategy_entities.get("fields") or [],
        strategy_entities.get("rows") or [],
        duplicate_key_fields=["统一策略ID", "实体Key"],
    )


def audit_ai_strategy_two_stage_protocol(
    issues: list[dict[str, Any]],
    site_dir: Path,
    field_rules: dict[str, Any],
) -> None:
    rules = field_rules.get("aiStrategyTwoStageChecks") if isinstance(field_rules.get("aiStrategyTwoStageChecks"), dict) else {}
    relative_asset = str(rules.get("asset") or "assets/ai-strategy.js")
    path = site_dir / Path(relative_asset)
    if not path.exists():
        add_issue(
            issues,
            "error",
            "page.ai_strategy.two_stage_protocol",
            "AI选策略脚本缺失",
            str(path),
            rule_id="AI_STRATEGY_TWO_STAGE_PROTOCOL_INVALID",
        )
        return
    text = path.read_text(encoding="utf-8-sig")
    samples: list[dict[str, Any]] = []
    for name in rules.get("requiredFunctions") or []:
        if f"function {name}(" not in text:
            samples.append({"类型": "缺少函数", "值": name})
    for call in rules.get("requiredCallSequence") or []:
        if text.count(str(call)) != 1:
            samples.append({"类型": "调用顺序或次数异常", "值": call, "实际次数": text.count(str(call))})
    for fragment in rules.get("forbiddenPromptFragments") or []:
        if str(fragment) in text:
            samples.append({"类型": "提示词包含禁用片段", "值": fragment})
    if "const businessRoutingCatalog" in text and "const virtualFields" in text:
        route_block = text.split("const businessRoutingCatalog", 1)[1].split("const virtualFields", 1)[0]
        for field in rules.get("businessRouteForbiddenFields") or []:
            if str(field) in route_block:
                samples.append({"类型": "核心业务路由暴露技术字段", "值": field})
    else:
        samples.append({"类型": "业务路由目录无法定位", "值": "businessRoutingCatalog"})
    if "field: fieldLabel(field)" not in text:
        samples.append({"类型": "字段卡未使用业务名称", "值": "fieldLabel(field)"})
    if "for (let attempt = 0; attempt <" in text:
        samples.append({"类型": "存在循环模型重试", "值": "for-attempt"})
    if samples:
        add_issue(
            issues,
            "error",
            "page.ai_strategy.two_stage_protocol",
            "AI选策略未满足两轮业务语义筛选协议",
            "第一轮只能发送核心业务实体，第二轮只能发送相关字段卡；技术字段不得进入模型提示词，且每次查询最多两次模型调用。",
            samples[:50],
            rule_id="AI_STRATEGY_TWO_STAGE_PROTOCOL_INVALID",
        )


def audit_ai_strategy_date_value_normalization(
    issues: list[dict[str, Any]],
    site_dir: Path,
    field_rules: dict[str, Any],
) -> None:
    rules = field_rules.get("aiStrategyDateValueChecks") if isinstance(field_rules.get("aiStrategyDateValueChecks"), dict) else {}
    if not rules:
        return
    relative_asset = str(rules.get("asset") or "assets/ai-strategy.js")
    path = site_dir / Path(relative_asset)
    if not path.exists():
        add_issue(
            issues,
            "error",
            "page.ai_strategy.date_value_normalization",
            "AI选策略日期归一脚本缺失",
            str(path),
            rule_id="AI_STRATEGY_DATE_VALUE_NORMALIZATION_INVALID",
        )
        return
    text = path.read_text(encoding="utf-8-sig")
    samples: list[dict[str, Any]] = []
    for name in rules.get("requiredFunctions") or []:
        if f"function {name}(" not in text:
            samples.append({"类型": "缺少函数", "值": name})
    for fragment in rules.get("requiredFragments") or []:
        if str(fragment) not in text:
            samples.append({"类型": "缺少归一约束", "值": fragment})
    minimum_calls = int(rules.get("minimumNormalizationCallCount") or 1)
    actual_calls = text.count("normalizeParsedDateFilters(parsed);")
    if actual_calls < minimum_calls:
        samples.append({"类型": "归一调用覆盖不足", "要求": minimum_calls, "实际": actual_calls})
    if samples:
        add_issue(
            issues,
            "error",
            "page.ai_strategy.date_value_normalization",
            "AI选策略日期字段值未统一归一",
            "相对日期和明确日期条件都必须在本地转换为 YYYY-MM-DD；模型、本地规则和页面手工调整必须经过同一校验后才能比较。",
            samples[:50],
            rule_id="AI_STRATEGY_DATE_VALUE_NORMALIZATION_INVALID",
        )


def audit_ai_strategy_performance_scope(
    issues: list[dict[str, Any]],
    site_dir: Path,
    field_rules: dict[str, Any],
) -> None:
    rules = field_rules.get("aiStrategyPerformanceScopeChecks") if isinstance(field_rules.get("aiStrategyPerformanceScopeChecks"), dict) else {}
    if not rules:
        return
    relative_asset = str(rules.get("asset") or "assets/ai-strategy.js")
    path = site_dir / Path(relative_asset)
    if not path.exists():
        add_issue(
            issues,
            "error",
            "page.ai_strategy.performance_scope",
            "AI选策略脚本缺失",
            str(path),
            rule_id="AI_STRATEGY_PERFORMANCE_SCOPE_INVALID",
        )
        return
    text = path.read_text(encoding="utf-8-sig")
    samples: list[dict[str, Any]] = []
    for name in rules.get("requiredFunctions") or []:
        if f"function {name}(" not in text:
            samples.append({"类型": "缺少函数", "值": name})
    for fragment in rules.get("requiredFragments") or []:
        if str(fragment) not in text:
            samples.append({"类型": "缺少业绩筛选约束", "值": fragment})
    for fragment in rules.get("forbiddenFragments") or []:
        if str(fragment) in text:
            samples.append({"类型": "仍包含旧错误口径", "值": fragment})
    if samples:
        add_issue(
            issues,
            "error",
            "page.ai_strategy.performance_scope",
            "AI选策略默认候选池或点阵覆盖口径不正确",
            "默认候选池只能以业绩完整为门槛；日期以页面数据日为基准，点阵必须先识别可绘制候选再执行展示上限。",
            samples[:50],
            rule_id="AI_STRATEGY_PERFORMANCE_SCOPE_INVALID",
        )


def audit_sqlite(issues: list[dict[str, Any]], db_path: Path) -> None:
    if not db_path.exists():
        add_issue(issues, "error", "sqlite", "数据库缺失", str(db_path))
        return
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        quick_check = conn.execute("PRAGMA quick_check").fetchone()[0]
        if quick_check != "ok":
            add_issue(issues, "error", "sqlite", "quick_check失败", str(quick_check))
        tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        for table in tables:
            columns = [row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')]
            dup_cols = duplicate_names(columns)
            if dup_cols:
                add_issue(issues, "error", f"sqlite.{table}", "字段名重复", "SQLite 表字段名必须唯一。", dup_cols)
        for table, key_fields in CRITICAL_BUSINESS_KEYS.items():
            if table not in tables:
                continue
            columns = [row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')]
            if not all(field in columns for field in key_fields):
                continue
            where = " AND ".join(f'"{field}" IS NOT NULL AND TRIM(CAST("{field}" AS TEXT)) <> ""' for field in key_fields)
            group = ", ".join(f'"{field}"' for field in key_fields)
            sql = f'SELECT {group}, COUNT(*) AS cnt FROM "{table}" WHERE {where} GROUP BY {group} HAVING cnt > 1 LIMIT 20'
            rows = [dict(row) for row in conn.execute(sql).fetchall()]
            if rows:
                add_issue(issues, "error", f"sqlite.{table}", "业务键重复", f"业务键 {key_fields} 不应重复。", rows)


def audit_strategy_parent_child_relationships(
    issues: list[dict[str, Any]],
    db_path: Path,
    site_dir: Path,
) -> None:
    if not db_path.exists():
        return
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "策略关系" not in tables:
            return
        invalid_rows = [
            dict(row)
            for row in conn.execute(
                '''
                SELECT r.*,
                       EXISTS(SELECT 1 FROM "策略信息" s WHERE s."统一策略ID"=r."子策略ID") AS child_exists,
                       EXISTS(SELECT 1 FROM "策略信息" s WHERE s."统一策略ID"=r."母策略ID") AS parent_exists,
                       CASE WHEN r."官方业绩策略ID" IS NULL THEN NULL ELSE (
                           SELECT COUNT(*) FROM "策略日度业绩" p WHERE p."统一策略ID"=r."官方业绩策略ID"
                       ) END AS official_curve_rows
                FROM "策略关系" r
                WHERE r."子策略ID"=r."母策略ID"
                   OR r."关系状态" NOT IN ('active', 'review')
                   OR r."置信分"<0 OR r."置信分">100
                   OR child_exists=0 OR parent_exists=0
                   OR (r."关系状态"='active' AND r."官方业绩策略ID" IS NOT NULL AND official_curve_rows<2)
                ORDER BY r."子策略ID"
                '''
            )
        ]
        if invalid_rows:
            add_issue(
                issues,
                "error",
                "sqlite.策略关系",
                "策略母子关系或数据域来源无效",
                "母子关系不得自引用，必须指向有效策略；active 官方业绩别名必须有可画披露曲线。",
                invalid_rows[:50],
                rule_id="STRATEGY_PARENT_CHILD_RELATION_INVALID",
            )
        stale_high_confidence_reviews: list[dict[str, Any]] = []
        review_rows = [
            dict(row)
            for row in conn.execute(
                '''
                SELECT *
                FROM "策略关系"
                WHERE "关系状态"='review'
                  AND "置信分">=90
                  AND "官方业绩策略ID" IS NOT NULL
                ORDER BY "子策略ID"
                '''
            )
        ]
        for relation in review_rows:
            try:
                evidence = json.loads(str(relation.get("证据JSON") or "{}"))
            except json.JSONDecodeError:
                continue
            curve = evidence.get("curve") if isinstance(evidence, dict) else {}
            still_positive = (
                isinstance(curve, dict)
                and curve.get("isAlias") is True
                and float(curve.get("matchRatio") or 0) >= 0.99
                and evidence.get("holdingMatched") is True
                and evidence.get("rebalanceMatched") is True
            )
            if not still_positive:
                continue
            source_id = str(relation.get("官方业绩策略ID") or "")
            official_curve_rows = 0
            if "策略日度业绩" in tables:
                official_curve_rows += int(
                    conn.execute(
                        'SELECT COUNT(*) FROM "策略日度业绩" WHERE "统一策略ID"=?',
                        (source_id,),
                    ).fetchone()[0]
                )
            if "策略产品披露净值" in tables:
                official_curve_rows += int(
                    conn.execute(
                        'SELECT COUNT(*) FROM "策略产品披露净值" WHERE "统一策略ID"=? AND "是否可画曲线"=1',
                        (source_id,),
                    ).fetchone()[0]
                )
            if official_curve_rows >= 2:
                stale_high_confidence_reviews.append(
                    {
                        "子策略ID": relation.get("子策略ID"),
                        "母策略ID": relation.get("母策略ID"),
                        "官方业绩策略ID": source_id,
                        "置信分": relation.get("置信分"),
                        "连续不一致次数": relation.get("连续不一致次数"),
                        "证据曲线匹配率": curve.get("matchRatio"),
                        "官方曲线点数": official_curve_rows,
                    }
                )
        if stale_high_confidence_reviews:
            add_issue(
                issues,
                "error",
                "sqlite.策略关系",
                "高置信度母子关系异常停留在review",
                "历史曲线、持仓和调仓证据仍全部一致且母策略官方曲线可用时，不得因机构别名漂移而阻断页面业绩继承。",
                stale_high_confidence_reviews[:50],
                rule_id="STRATEGY_PARENT_CHILD_HIGH_CONFIDENCE_REVIEW_STALE",
            )
        active_aliases = [
            dict(row)
            for row in conn.execute(
                '''
                SELECT "子策略ID", "母策略ID", "官方业绩策略ID"
                FROM "策略关系"
                WHERE "关系状态"='active' AND "官方业绩策略ID" IS NOT NULL
                ORDER BY "子策略ID"
                '''
            )
        ]
    finally:
        conn.close()

    missing_page_rows: list[dict[str, Any]] = []
    detail_dir = site_dir / "data" / "details"
    for relation in active_aliases:
        child_id = str(relation["子策略ID"])
        path = detail_dir / f"{child_id}.js"
        if not path.is_file() and path.with_suffix(path.suffix + ".gz").is_file():
            path = path.with_suffix(path.suffix + ".gz")
        if not path.is_file():
            missing_page_rows.append({**relation, "问题": "详情包缺失", "path": str(path)})
            continue
        try:
            payload = read_js_object(path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            missing_page_rows.append({**relation, "问题": f"详情包解析失败: {exc}", "path": str(path)})
            continue
        page_relation = payload.get("strategyRelation") if isinstance(payload, dict) else {}
        official_curve = ((payload.get("curves") or {}).get("披露业绩") or {}) if isinstance(payload, dict) else {}
        points = official_curve.get("points") if isinstance(official_curve, dict) else []
        warnings = payload.get("curveWarnings") if isinstance(payload, dict) else []
        has_non_independent_disclosure = any(
            "共享母策略" in str(value)
            and ("非本期独立" in str(value) or "不代表本期独立" in str(value))
            for value in (warnings or [])
        )
        if (
            str((page_relation or {}).get("官方业绩策略ID") or "") != str(relation["官方业绩策略ID"])
            or not isinstance(points, list)
            or len(points) < 2
            or not has_non_independent_disclosure
        ):
            missing_page_rows.append(
                {
                    **relation,
                    "问题": "关系、共享曲线或非独立净值说明未同步",
                    "pageOfficialSource": (page_relation or {}).get("官方业绩策略ID"),
                    "curvePoints": len(points) if isinstance(points, list) else 0,
                    "path": str(path),
                }
            )
    if missing_page_rows:
        add_issue(
            issues,
            "error",
            "page.strategy_details.shared_performance",
            "母策略共享业绩详情包缺失或口径未披露",
            "已确认的官方业绩别名必须生成可画曲线，并明确说明不是本期独立净值。",
            missing_page_rows[:50],
            rule_id="PAGE_STRATEGY_SHARED_PERFORMANCE_MISSING",
        )

    summary_path = site_dir / "data" / "basic_summary_core.js"
    if not summary_path.is_file():
        return
    try:
        summary_pack = read_js_object(summary_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        add_issue(
            issues,
            "error",
            "page.basic_summary_core",
            "策略核心包解析失败",
            f"cannot parse {summary_path}: {exc}",
            rule_id="PAGE_PACK_FILE_PARSE_FAILED",
        )
        return
    strategy_rows = summary_pack.get("strategies") or ((summary_pack.get("summary") or {}).get("strategies")) or []
    legacy_bucket_fields = {
        "".join(("基准", "权益分档")),
        "".join(("基准", "权益分档说明")),
        "".join(("广义", "权益分档")),
        "".join(("广义", "权益权重")),
    }
    required_profile_fields = {
        "基准风险资产权重", "基准风险资产权重说明", "基准风险资产权重_百分比", "权益中枢", "固收中枢",
        "基准风险资产中枢", "海外配置中枢", "指数化程度", "主动管理程度", "风险资产偏离", "配置风格标签",
    }
    bucket_semantic_issues: list[dict[str, Any]] = []
    for row in strategy_rows:
        if not isinstance(row, dict):
            continue
        legacy_present = sorted(field for field in legacy_bucket_fields if field in row)
        missing_profile = sorted(field for field in required_profile_fields if field not in row)
        risk_weight = row.get("基准风险资产权重_百分比")
        unknown_weight = row.get("基准资产未映射权重")
        expected_bucket = ""
        try:
            risk_number = float(risk_weight) if risk_weight not in (None, "") else None
            unknown_number = float(unknown_weight) if unknown_weight not in (None, "") else 0.0
            if risk_number is not None and math.isfinite(risk_number) and unknown_number <= 0.01:
                expected_bucket = "L0" if risk_number <= 0.000001 else f"L{min(10, max(1, math.ceil(risk_number / 10.0)))}"
        except (TypeError, ValueError):
            expected_bucket = ""
        actual_bucket = str(row.get("基准风险资产权重") or "")
        bucket_mismatch = bool(expected_bucket and actual_bucket != expected_bucket)
        if legacy_present or missing_profile or bucket_mismatch:
            bucket_semantic_issues.append({
                "统一策略ID": row.get("统一策略ID"),
                "策略名称": row.get("策略名称"),
                "遗留字段": "、".join(legacy_present),
                "缺少画像字段": "、".join(missing_profile),
                "基准风险资产权重_百分比": risk_weight,
                "当前基准风险资产权重": actual_bucket,
                "应为分档": expected_bucket,
            })
    if bucket_semantic_issues:
        add_issue(
            issues,
            "error",
            "page.basic_summary_core.strategy_benchmark_bucket",
            "策略页面基准风险资产权重或配置画像口径不一致",
            "策略页只能保留一个基准风险资产权重，并按权益、商品、另类风险资产合计权重形成L0-L10；配置画像字段必须完整保留，未知值使用空值而不是伪造0。",
            bucket_semantic_issues[:50],
            rule_id="PAGE_STRATEGY_BENCHMARK_BUCKET_SEMANTICS_INVALID",
        )
    strategy_map = {
        str(row.get("统一策略ID")): row
        for row in strategy_rows
        if isinstance(row, dict) and row.get("统一策略ID")
    }
    benchmark_mismatches: list[dict[str, Any]] = []
    benchmark_conflicts: list[dict[str, Any]] = []
    for relation in active_aliases:
        child_id = str(relation["子策略ID"])
        source_id = str(relation["官方业绩策略ID"])
        child = strategy_map.get(child_id) or {}
        source = strategy_map.get(source_id) or {}
        source_has_full_benchmark = (
            str(source.get("基准可用状态") or "") == "文本+曲线"
            and bool(str(source.get("业绩基准说明") or "").strip())
        )
        if not source_has_full_benchmark:
            continue
        child_text = str(child.get("业绩基准说明") or "").strip()
        source_text = str(source.get("业绩基准说明") or "").strip()
        texts_equivalent = bool(
            child_text
            and normalize_benchmark_text(child_text) == normalize_benchmark_text(source_text)
        )
        child_source_id = str(child.get("业绩基准来源策略ID") or "")
        inheritance_mode = str(child.get("业绩基准继承口径") or "")
        source_is_consistent = (
            inheritance_mode == "已验证母子关系共享官方业绩" and child_source_id == source_id
        )
        if child_text and not texts_equivalent:
            benchmark_conflicts.append(
                {
                    **relation,
                    "子策略基准文本": child_text,
                    "母策略基准文本": source_text,
                    "页面基准来源策略ID": child_source_id,
                    "页面基准继承口径": inheritance_mode,
                }
            )
            continue
        if (
            not child
            or str(child.get("基准可用状态") or "") != "文本+曲线"
            or not texts_equivalent
            or not source_is_consistent
        ):
            benchmark_mismatches.append(
                {
                    **relation,
                    "子策略基准状态": child.get("基准可用状态"),
                    "子策略基准文本": child.get("业绩基准说明"),
                    "页面基准来源策略ID": child_source_id,
                    "页面基准继承口径": inheritance_mode,
                    "母策略基准状态": source.get("基准可用状态"),
                }
            )
    if benchmark_mismatches:
        add_issue(
            issues,
            "error",
            "page.basic_summary_core.strategy_relationship_benchmark",
            "共享官方业绩策略的基准口径未同步",
            "母策略已有基准文本和可画曲线时，共享官方业绩的子策略不得被误判为无基准，且基准来源必须可追溯。",
            benchmark_mismatches[:50],
            rule_id="PAGE_STRATEGY_SHARED_BENCHMARK_MISSING",
        )
    if benchmark_conflicts:
        add_issue(
            issues,
            "error",
            "page.basic_summary_core.strategy_relationship_benchmark",
            "共享官方业绩策略存在基准文本冲突",
            "子策略与官方业绩来源策略披露了语义不同的基准文本；为避免错误继承，页面保留原文并阻断发布，需先核实母子关系或源数据。",
            benchmark_conflicts[:50],
            rule_id="PAGE_STRATEGY_SHARED_BENCHMARK_CONFLICT",
        )


def audit_ttfund_strategy_benchmark_curve_freshness(
    issues: list[dict[str, Any]],
    conn: sqlite3.Connection,
) -> None:
    stale_rows = [
        dict(row)
        for row in conn.execute(
            '''
            WITH latest AS (
                SELECT s."统一策略ID", s."渠道策略ID", s."策略名称",
                       MAX(p."交易日期") AS "策略最新日期",
                       COUNT(DISTINCT p."交易日期") AS "策略曲线点数",
                       MAX(CASE WHEN p."基准收益率_百分比" IS NOT NULL THEN p."交易日期" END) AS "基准最新日期"
                FROM "策略信息" s
                JOIN "策略日度业绩" p ON p."统一策略ID" = s."统一策略ID"
                WHERE s."渠道ID" = 'ttfund'
                GROUP BY s."统一策略ID", s."渠道策略ID", s."策略名称"
            )
            SELECT *
            FROM latest
            WHERE "策略最新日期" IS NOT NULL
              AND ("基准最新日期" IS NULL OR "基准最新日期" < "策略最新日期")
            ORDER BY "策略最新日期" DESC, "统一策略ID"
            '''
        )
    ]
    if not stale_rows:
        return

    rule = RULE_CATALOG.get("TTFUND_STRATEGY_BENCHMARK_CURVE_STALE") or {}
    try:
        allowed_lag = max(0, int(rule.get("maxSourceLagBusinessDays", 1)))
    except (TypeError, ValueError):
        allowed_lag = 1
    blocking_rows: list[dict[str, Any]] = []
    allowed_source_lag_rows: list[dict[str, Any]] = []
    incomplete_strategy_rows: list[dict[str, Any]] = []
    for row in stale_rows:
        lag = business_day_lag(row.get("基准最新日期"), row.get("策略最新日期"))
        sample = {**row, "基准滞后工作日": lag}
        if row.get("基准最新日期") is None and int(row.get("策略曲线点数") or 0) < 2:
            incomplete_strategy_rows.append(sample)
            continue
        if row.get("基准最新日期") is None or lag is None or lag > allowed_lag:
            blocking_rows.append(sample)
        else:
            allowed_source_lag_rows.append(sample)

    if blocking_rows:
        add_issue(
            issues,
            "error",
            "sqlite.策略日度业绩",
            "天天策略与基准曲线超出允许源延迟",
            (
                f"{len(blocking_rows)} 个样本缺少基准曲线，或基准曲线滞后超过 "
                f"{allowed_lag} 个工作日。"
            ),
            blocking_rows[:50],
            rule_id="TTFUND_STRATEGY_BENCHMARK_CURVE_STALE",
        )
    if allowed_source_lag_rows:
        add_issue(
            issues,
            "warn",
            "sqlite.策略日度业绩",
            "天天官方基准曲线存在允许范围内源延迟",
            (
                f"{len(allowed_source_lag_rows)} 个样本的官方基准曲线滞后不超过 "
                f"{allowed_lag} 个工作日；保留行情推算策略值并明确披露，不阻断发布。"
            ),
            allowed_source_lag_rows[:50],
            rule_id="TTFUND_STRATEGY_BENCHMARK_CURVE_SOURCE_LAG",
        )
    if incomplete_strategy_rows:
        add_issue(
            issues,
            "warn",
            "sqlite.策略日度业绩",
            "天天新增或不完整策略尚无可比较基准曲线",
            (
                f"{len(incomplete_strategy_rows)} 个策略当前少于 2 个业绩点且没有基准曲线；"
                "不能绘制连续业绩曲线，已排除在业绩完整筛选和排名之外，保留告警等待后续补采。"
            ),
            incomplete_strategy_rows[:50],
            rule_id="TTFUND_INCOMPLETE_STRATEGY_BENCHMARK_MISSING",
        )


def audit_core_fact_semantics(issues: list[dict[str, Any]], db_path: Path) -> None:
    if not db_path.exists():
        return
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}

        if conn.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            add_issue(
                issues,
                "warn",
                "sqlite.schema",
                "外键约束未启用",
                "当前连接的 SQLite foreign_keys=OFF，核心事实表的父子关系不能由数据库强制保证。",
                rule_id="SQLITE_FOREIGN_KEYS_DISABLED",
            )
        if conn.execute("PRAGMA user_version").fetchone()[0] <= 0:
            add_issue(
                issues,
                "warn",
                "sqlite.schema",
                "结构版本未登记",
                "SQLite user_version=0，无法仅凭数据库判断当前结构迁移版本。",
                rule_id="SQLITE_SCHEMA_VERSION_MISSING",
            )

        f10_stats = []
        for table in ("基金F10基准", "FOF基金F10基准"):
            if table not in tables:
                continue
            columns = {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}
            if "F10成立日期" not in columns:
                continue
            row = conn.execute(
                f'''
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN "F10成立日期" GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]' THEN 1 ELSE 0 END) AS valid,
                       SUM(CASE WHEN "F10成立日期" IS NOT NULL AND TRIM("F10成立日期") <> '' THEN 1 ELSE 0 END) AS nonempty
                FROM "{table}"
                '''
            ).fetchone()
            f10_stats.append({"表": table, "总数": row["total"], "有效日期数": row["valid"] or 0, "非空数": row["nonempty"] or 0})
        f10_total = sum(int(row["总数"] or 0) for row in f10_stats)
        f10_valid = sum(int(row["有效日期数"] or 0) for row in f10_stats)
        if f10_total and f10_valid / f10_total < 0.95:
            add_issue(
                issues,
                "error",
                "sqlite.fund_f10",
                "F10成立日期有效率不足",
                f"F10 成立日期有效 {f10_valid}/{f10_total}，无法支撑成立以来历史完整性校验。",
                f10_stats,
                rule_id="FUND_F10_INCEPTION_DATE_INVALID",
            )

        if {"公募基金产品绩效快照", "基金日度净值"}.issubset(tables):
            mismatch_sql = '''
                FROM "公募基金产品绩效快照" p
                JOIN "基金日度净值" n
                  ON n."基金代码" = p."基金代码" AND n."交易日期" = p."净值日期"
                WHERE (p."单位净值" IS NOT NULL AND n."单位净值" IS NOT NULL AND ABS(p."单位净值" - n."单位净值") > 0.0000001)
                   OR (p."累计净值" IS NOT NULL AND n."累计净值" IS NOT NULL AND ABS(p."累计净值" - n."累计净值") > 0.0000001)
            '''
            mismatch_count = conn.execute(f"SELECT COUNT(*) {mismatch_sql}").fetchone()[0]
            if mismatch_count:
                samples = [
                    dict(row)
                    for row in conn.execute(
                        f'''
                        SELECT p."基金代码", p."基金名称", p."净值日期",
                               p."单位净值" AS "快照单位净值", n."单位净值" AS "当日单位净值",
                               p."累计净值" AS "快照累计净值", n."累计净值" AS "当日累计净值"
                        {mismatch_sql}
                        ORDER BY p."基金代码"
                        LIMIT 20
                        '''
                    )
                ]
                add_issue(
                    issues,
                    "error",
                    "sqlite.公募基金产品绩效快照",
                    "净值日期与净值数值错配",
                    f"{mismatch_count} 只基金的快照净值与标注日期对应的日度净值不一致。",
                    samples,
                    rule_id="PUBLIC_FUND_SNAPSHOT_NAV_DATE_VALUE_MISMATCH",
                )

            partial_risk_count = conn.execute(
                '''
                SELECT COUNT(*)
                FROM "公募基金产品绩效快照"
                WHERE "近1年收益率_百分比" IS NULL
                  AND ("近1年最大回撤_百分比" IS NOT NULL OR "近1年年化波动率_百分比" IS NOT NULL)
                '''
            ).fetchone()[0]
            if partial_risk_count:
                samples = [
                    dict(row)
                    for row in conn.execute(
                        '''
                        SELECT "基金代码", "基金名称", "绩效样本起始日", "绩效样本截止日",
                               "近1年收益率_百分比", "近1年最大回撤_百分比", "近1年年化波动率_百分比", "风险数据状态"
                        FROM "公募基金产品绩效快照"
                        WHERE "近1年收益率_百分比" IS NULL
                          AND ("近1年最大回撤_百分比" IS NOT NULL OR "近1年年化波动率_百分比" IS NOT NULL)
                        ORDER BY "基金代码"
                        LIMIT 20
                        '''
                    )
                ]
                add_issue(
                    issues,
                    "error",
                    "sqlite.公募基金产品绩效快照",
                    "固定一年风险字段混入不足一年样本",
                    f"{partial_risk_count} 只基金没有近1年收益，却写入了近1年回撤或波动率。",
                    samples,
                    rule_id="PUBLIC_FUND_ONE_YEAR_RISK_PARTIAL_WINDOW",
                )

        if "策略日度业绩" in tables:
            anomaly_where = '"单位净值" <= 0 OR ABS("日收益率_百分比") > 50'
            anomaly_sql = f'''
                FROM "策略日度业绩" p
                LEFT JOIN "策略治理标签" g ON g."统一策略ID" = p."统一策略ID"
                WHERE ({anomaly_where.replace('"单位净值"', 'p."单位净值"').replace('"日收益率_百分比"', 'p."日收益率_百分比"')})
                  AND (COALESCE(g."是否业绩异常", 0) <> 1 OR COALESCE(g."是否纳入常规排名", 1) <> 0)
            '''
            anomaly_count = conn.execute(f'SELECT COUNT(*) {anomaly_sql}').fetchone()[0]
            if anomaly_count:
                samples = [
                    dict(row)
                    for row in conn.execute(
                        f'''
                        SELECT p."统一策略ID", p."渠道ID", p."渠道策略ID", p."交易日期", p."单位净值",
                               p."日收益率_百分比", p."累计收益率_百分比", p."原始快照ID"
                        FROM "策略日度业绩" p
                        LEFT JOIN "策略治理标签" g ON g."统一策略ID" = p."统一策略ID"
                        WHERE ({anomaly_where.replace('"单位净值"', 'p."单位净值"').replace('"日收益率_百分比"', 'p."日收益率_百分比"')})
                          AND (COALESCE(g."是否业绩异常", 0) <> 1 OR COALESCE(g."是否纳入常规排名", 1) <> 0)
                        ORDER BY p."统一策略ID", p."交易日期"
                        LIMIT 20
                        '''
                    )
                ]
                add_issue(
                    issues,
                    "error",
                    "sqlite.策略日度业绩",
                    "策略净值或日收益异常",
                    f"发现 {anomaly_count} 条非正净值或绝对日收益超过 50% 的策略业绩记录。",
                    samples,
                    rule_id="STRATEGY_DAILY_NAV_RETURN_INVALID",
                )

            gfbank_scale_sql = '''
                FROM "策略日度业绩" p
                WHERE p."渠道ID" = 'gfbank_cgb'
                  AND p."业绩区段类型" IN (
                    'gfbank_authenticated_ui_latest',
                    'gfbank_authenticated_ui_curve_tooltip'
                  )
                  AND p."单位净值" IS NOT NULL
                  AND p."累计收益率_百分比" IS NOT NULL
                  AND ABS(p."单位净值" - (1.0 + p."累计收益率_百分比" / 100.0)) > 0.0001
            '''
            gfbank_scale_count = (
                0
                if "gfbank_cgb" in DISABLED_CHANNEL_IDS
                else conn.execute(f'SELECT COUNT(*) {gfbank_scale_sql}').fetchone()[0]
            )
            if gfbank_scale_count:
                samples = [
                    dict(row)
                    for row in conn.execute(
                        f'''
                        SELECT p."统一策略ID", p."交易日期", p."单位净值",
                               p."累计收益率_百分比", p."原始快照ID"
                        {gfbank_scale_sql}
                        ORDER BY p."统一策略ID", p."交易日期"
                        LIMIT 20
                        '''
                    )
                ]
                add_issue(
                    issues,
                    "error",
                    "sqlite.策略日度业绩",
                    "广发银行策略净值与累计收益率量纲不一致",
                    f"发现 {gfbank_scale_count} 条广发银行登录态业绩的净值与累计收益率不满足同一量纲关系。",
                    samples,
                    rule_id="GFBANK_DAILY_NAV_CUMULATIVE_RETURN_SCALE_MISMATCH",
                )

            if "gfbank_cgb" not in DISABLED_CHANNEL_IDS and "策略基准费率状态" in tables:
                gfbank_false_curve_sql = '''
                    FROM "策略基准费率状态"
                    WHERE "渠道ID" = 'gfbank_cgb'
                      AND "基准曲线状态" = '有日度基准曲线'
                      AND (
                        "基准曲线起始日期" IS NULL
                        OR "基准曲线结束日期" IS NULL
                        OR "基准曲线起始日期" = "基准曲线结束日期"
                      )
                '''
                gfbank_false_curve_count = conn.execute(
                    f'SELECT COUNT(*) {gfbank_false_curve_sql}'
                ).fetchone()[0]
                if gfbank_false_curve_count:
                    samples = [
                        dict(row)
                        for row in conn.execute(
                            f'''
                            SELECT "统一策略ID", "策略名称", "披露净值基准行数",
                                   "日度业绩基准行数", "区间基准行数", "基准曲线状态", "基准可用状态"
                            {gfbank_false_curve_sql}
                            ORDER BY "统一策略ID"
                            LIMIT 20
                            '''
                        )
                    ]
                    add_issue(
                        issues,
                        "error",
                        "sqlite.策略基准费率状态",
                        "广发银行单点基准被误标为可用日度曲线",
                        f"发现 {gfbank_false_curve_count} 个广发银行策略不足两个基准日期却标记为有日度基准曲线。",
                        samples,
                        rule_id="GFBANK_BENCHMARK_SINGLE_POINT_NOT_CURVE",
                    )

            if "策略信息" in tables:
                audit_ttfund_strategy_benchmark_curve_freshness(issues, conn)

        if {"策略当前持仓", "策略信息"}.issubset(tables):
            suspect_sql = '''
                WITH latest AS (
                    SELECT "统一策略ID", MAX("持仓日期") AS holding_date
                    FROM "策略当前持仓"
                    GROUP BY "统一策略ID"
                ), latest_sum AS (
                    SELECT h."统一策略ID", h."渠道ID", h."持仓日期", s."策略名称", s."策略状态",
                           COUNT(*) AS position_count,
                           SUM(CASE WHEN h."基金权重_百分比" IS NULL THEN 1 ELSE 0 END) AS null_weight_count,
                           SUM(COALESCE(h."基金权重_百分比", 0)) AS weight_sum
                    FROM "策略当前持仓" h
                    JOIN latest l ON l."统一策略ID" = h."统一策略ID" AND l.holding_date = h."持仓日期"
                    JOIN "策略信息" s ON s."统一策略ID" = h."统一策略ID"
                    LEFT JOIN "策略治理标签" g ON g."统一策略ID" = h."统一策略ID"
                    WHERE h."渠道ID" IN ('ttfund', 'gffunds')
                      AND COALESCE(s."策略状态", '') <> 'stopped'
                      AND COALESCE(g."是否测试组合", 0) = 0
                      AND COALESCE(g."是否信号类组合", 0) = 0
                      AND COALESCE(g."是否已停止", 0) = 0
                      AND COALESCE(g."是否纳入常规排名", 1) = 1
                    GROUP BY h."统一策略ID", h."渠道ID", h."持仓日期", s."策略名称", s."策略状态"
                ), projected_sum AS (
                    SELECT "统一策略ID",
                           MAX("推算持仓日期") AS projected_date,
                           COUNT(*) AS projected_count,
                           SUM(COALESCE("推算基金权重_百分比", 0)) AS projected_weight_sum
                    FROM "策略当前持仓推算补齐"
                    GROUP BY "统一策略ID"
                )
                SELECT latest_sum.*, projected_sum.projected_date, projected_sum.projected_count, projected_sum.projected_weight_sum
                FROM latest_sum
                LEFT JOIN projected_sum ON projected_sum."统一策略ID" = latest_sum."统一策略ID"
                WHERE (null_weight_count > 0 OR (position_count >= 2 AND weight_sum > 0 AND weight_sum < 50))
                  AND (projected_weight_sum IS NULL OR projected_weight_sum NOT BETWEEN 98 AND 102)
                ORDER BY weight_sum, "统一策略ID"
            '''
            suspects = [dict(row) for row in conn.execute(suspect_sql)]
            if suspects:
                add_issue(
                    issues,
                    "warn",
                    "sqlite.策略当前持仓",
                    "最新持仓权重单位或完整性可疑",
                    f"{len(suspects)} 只在运作策略的最新直接持仓存在空权重，或多基金权重合计低于 50%。",
                    suspects[:20],
                    rule_id="STRATEGY_CURRENT_HOLDING_WEIGHT_SCALE_SUSPECT",
                )

        if "策略调仓事件" in tables:
            projection_rows = [
                dict(row)
                for row in conn.execute(
                    '''
                    SELECT "调仓事件ID", "统一策略ID", "渠道策略ID", "调仓日期", "载荷类型"
                    FROM "策略调仓事件"
                    WHERE "渠道ID"='qieman'
                      AND (
                        "调仓事件ID" LIKE 'qieman-signal-projection-%'
                        OR COALESCE("载荷类型", '') LIKE '%projection%'
                      )
                    LIMIT 30
                    '''
                )
            ]
            if projection_rows:
                add_issue(
                    issues,
                    "error",
                    "sqlite.qieman.signal_projection",
                    "且慢发车兼容投影进入普通官方调仓表",
                    "发车指令比例不是组合存量仓位，兼容投影不得作为普通官方调仓事实入库。",
                    projection_rows,
                    rule_id="QIEMAN_SIGNAL_PROJECTION_OFFICIAL_LEAK",
                )

        if "策略信息" in tables:
            qieman_internal_rows = [
                dict(row)
                for row in conn.execute(
                    '''
                    SELECT "统一策略ID", "渠道策略ID", "策略名称", "策略状态"
                    FROM "策略信息"
                    WHERE "渠道ID"='qieman'
                      AND LOWER(TRIM(COALESCE("策略状态", ''))) IN (
                        'test', 'internal', 'test_or_internal'
                      )
                    ORDER BY "统一策略ID"
                    LIMIT 50
                    '''
                )
            ]
            if qieman_internal_rows:
                add_issue(
                    issues,
                    "error",
                    "sqlite.qieman.internal_test_inventory",
                    "且慢内部或测试策略进入正式业务库存",
                    "源端已明确标记为内部或测试的组合只允许保留原始发现证据，不得进入策略主表、覆盖率分母或页面。",
                    qieman_internal_rows,
                    rule_id="QIEMAN_INTERNAL_TEST_STRATEGY_EXPOSED",
                )

            qieman_master = conn.execute(
                '''
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN COALESCE("投顾机构", '') <> '' THEN 1 ELSE 0 END) AS advisor_total,
                       SUM(CASE WHEN COALESCE("策略类型", '') <> '' THEN 1 ELSE 0 END) AS type_total,
                       SUM(CASE WHEN COALESCE("建议持有时长", '') <> '' THEN 1 ELSE 0 END) AS holding_total,
                       SUM(CASE WHEN "起投金额" IS NOT NULL THEN 1 ELSE 0 END) AS minimum_total,
                       SUM(CASE WHEN COALESCE("投顾费率", '') <> '' THEN 1 ELSE 0 END) AS fee_total,
                       SUM(CASE WHEN COALESCE("业绩基准", '') <> '' THEN 1 ELSE 0 END) AS benchmark_total
                FROM "策略信息"
                WHERE "渠道ID"='qieman'
                '''
            ).fetchone()
            qieman_total = int(qieman_master["total"] or 0)
            if qieman_total:
                qieman_signal_events = (
                    int(
                        conn.execute(
                            '''SELECT COUNT(*) FROM "信号策略事件" WHERE "渠道ID"='qieman' '''
                        ).fetchone()[0]
                    )
                    if "信号策略事件" in tables
                    else 0
                )
                qieman_signal_instructions = (
                    int(
                        conn.execute(
                            '''SELECT COUNT(*) FROM "信号策略基金指令" WHERE "渠道ID"='qieman' '''
                        ).fetchone()[0]
                    )
                    if "信号策略基金指令" in tables
                    else 0
                )
                qieman_signal_orphans = (
                    int(
                        conn.execute(
                            '''SELECT COUNT(*)
                               FROM "信号策略基金指令" i
                               LEFT JOIN "信号策略事件" e
                                 ON e."信号事件ID"=i."信号事件ID"
                               WHERE i."渠道ID"='qieman' AND e."信号事件ID" IS NULL'''
                        ).fetchone()[0]
                    )
                    if {"信号策略事件", "信号策略基金指令"}.issubset(tables)
                    else 0
                )
                if (
                    qieman_signal_events <= 0
                    or qieman_signal_instructions <= 0
                    or qieman_signal_orphans > 0
                ):
                    add_issue(
                        issues,
                        "error",
                        "sqlite.qieman.signal_entities",
                        "且慢发车事件或基金指令未完整进入主库",
                        "且慢发车历史必须保留独立事件和基金指令实体；新增资金分配比例不得写入组合存量权重字段。",
                        {
                            "策略总数": qieman_total,
                            "信号事件数": qieman_signal_events,
                            "信号指令数": qieman_signal_instructions,
                            "孤立指令数": qieman_signal_orphans,
                        },
                        rule_id="QIEMAN_SIGNAL_ENTITY_LOAD_INCOMPLETE",
                    )
                coverage = {
                    "策略总数": qieman_total,
                    "投顾机构": int(qieman_master["advisor_total"] or 0),
                    "策略类型": int(qieman_master["type_total"] or 0),
                    "建议持有时长": int(qieman_master["holding_total"] or 0),
                    "起投金额": int(qieman_master["minimum_total"] or 0),
                    "投顾费率": int(qieman_master["fee_total"] or 0),
                    "业绩基准": int(qieman_master["benchmark_total"] or 0),
                }
                if any(value < qieman_total for key, value in coverage.items() if key != "策略总数"):
                    add_issue(
                        issues,
                        "warn",
                        "sqlite.qieman.master_field_coverage",
                        "且慢部分基础字段仍有官方披露缺口",
                        "仅补齐有精确策略 ID、唯一精确名称、官方文本或登录页同策略证据的字段；其余缺失不做推断。",
                        coverage,
                        rule_id="QIEMAN_MASTER_FIELD_COVERAGE_GAP",
                    )


def audit_public_fund_benchmark_buckets(issues: list[dict[str, Any]], db_path: Path) -> None:
    if not db_path.exists():
        return
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "公募基金产品绩效快照" not in tables:
            return
        columns = {row[1] for row in conn.execute('PRAGMA table_info("公募基金产品绩效快照")')}
        required = {
            "基金代码", "基金名称", "基准风险资产权重", "基准权益权重_百分比", "基准未知权重_百分比",
            "基准权重合计_百分比", "基准风险资产权重来源", "基准映射置信度", "是否使用分类兜底",
            "业绩比较基准", "基准解析说明", "基准债券权重_百分比", "基准货币权重_百分比",
            "基准商品权重_百分比", "基准另类权重_百分比", "基准港股权益权重_百分比",
            "基准海外权益权重_百分比", "基准互斥权重合计_百分比", "非权益比较轨道",
            "正式可比池", "可比池样本资格",
        }
        if not required.issubset(columns):
            return
        rows_with_bucket = [
            dict(row)
            for row in conn.execute(
                """
                SELECT "基金代码", "基金名称", "基准风险资产权重", "基准未知权重_百分比",
                       "业绩比较基准", "基准解析说明"
                FROM "公募基金产品绩效快照"
                WHERE COALESCE("基准未知权重_百分比", 0) > 0.01
                  AND TRIM(COALESCE("基准风险资产权重", '')) <> ''
                ORDER BY "基准未知权重_百分比" DESC, "基金代码"
                LIMIT 50
                """
            )
        ]
        if rows_with_bucket:
            add_issue(
                issues,
                "error",
                "public_fund_benchmark.公募基金产品绩效快照",
                "未知权重仍输出分档",
                "公募基金业绩基准仍有未知权重时，基准风险资产权重必须留空，避免误把未知组件纳入确定分档。",
                rows_with_bucket,
                rule_id="PUBLIC_FUND_BENCHMARK_BUCKET_WITH_UNKNOWN",
            )
        invalid_bucket_rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT "基金代码", "基金名称", "基准风险资产权重", "基准权益权重_百分比",
                       "业绩比较基准", "基准风险资产权重来源"
                FROM "公募基金产品绩效快照"
                WHERE TRIM(COALESCE("基准风险资产权重", '')) <> ''
                  AND "基准风险资产权重" NOT IN ('L0','L1','L2','L3','L4','L5','L6','L7','L8','L9','L10')
                ORDER BY "基金代码"
                LIMIT 50
                """
            )
        ]
        if invalid_bucket_rows:
            add_issue(
                issues,
                "error",
                "public_fund_benchmark.公募基金产品绩效快照",
                "分档格式不统一",
                "公募基金基准风险资产权重必须统一为 L0-L10，不能混用百分比区间文本。",
                invalid_bucket_rows,
                rule_id="PUBLIC_FUND_BENCHMARK_BUCKET_FORMAT_INVALID",
            )
        low_confidence_rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT "基金代码", "基金名称", "基准风险资产权重", "基准权益权重_百分比",
                       "基准权重合计_百分比", "基准映射置信度", "业绩比较基准", "基准解析说明"
                FROM "公募基金产品绩效快照"
                WHERE TRIM(COALESCE("基准风险资产权重", '')) <> ''
                  AND COALESCE("基准映射置信度", '') NOT IN ('高', '中')
                ORDER BY "基金代码"
                LIMIT 50
                """
            )
        ]
        if low_confidence_rows:
            add_issue(
                issues,
                "error",
                "public_fund_benchmark.公募基金产品绩效快照",
                "低置信基准仍输出分档",
                "低置信、未解析或未披露基准不得输出确定的 L0-L10 分档。",
                low_confidence_rows,
                rule_id="PUBLIC_FUND_BENCHMARK_LOW_CONFIDENCE_BUCKET",
            )
        invalid_total_rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT "基金代码", "基金名称", "基准风险资产权重", "基准权益权重_百分比",
                       "基准权重合计_百分比", "基准映射置信度", "业绩比较基准", "基准解析说明"
                FROM "公募基金产品绩效快照"
                WHERE TRIM(COALESCE("基准风险资产权重", '')) <> ''
                  AND ABS(COALESCE("基准互斥权重合计_百分比", 0) - 100) > 0.01
                ORDER BY "基金代码"
                LIMIT 50
                """
            )
        ]
        if invalid_total_rows:
            add_issue(
                issues,
                "error",
                "public_fund_benchmark.公募基金产品绩效快照",
                "基准权重合计异常仍输出分档",
                "基准权重合计小于等于 0 或超过 100% 时不得输出确定分档。",
                invalid_total_rows,
                rule_id="PUBLIC_FUND_BENCHMARK_WEIGHT_TOTAL_INVALID",
            )
        fallback_rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT "基金代码", "基金名称", "基准风险资产权重", "基准风险资产权重来源",
                       "是否使用分类兜底", "业绩比较基准", "基准解析说明"
                FROM "公募基金产品绩效快照"
                WHERE COALESCE("是否使用分类兜底", 0) <> 0
                   OR "基准风险资产权重来源" LIKE '%兜底%'
                ORDER BY "基金代码"
                LIMIT 50
                """
            )
        ]
        if fallback_rows:
            add_issue(
                issues,
                "error",
                "public_fund_benchmark.公募基金产品绩效快照",
                "分类兜底用于基准分档",
                "基金基准风险资产权重只能来自业绩比较基准解析，不能使用基金类型或资产分类兜底。",
                fallback_rows,
                rule_id="PUBLIC_FUND_BENCHMARK_CLASSIFICATION_FALLBACK",
            )
        heuristic_dynamic_rows = [
            dict(row)
            for row in conn.execute(
                '''
                SELECT "基金代码", "基金名称", "基准风险资产权重", "基准权益权重_百分比",
                       "业绩比较基准", "基准解析说明"
                FROM "公募基金产品绩效快照"
                WHERE TRIM(COALESCE("基准风险资产权重", '')) <> ''
                  AND COALESCE("基准解析说明", '') LIKE '%估算%'
                ORDER BY "基金代码" LIMIT 50
                '''
            )
        ]
        if heuristic_dynamic_rows:
            add_issue(
                issues, "error", "public_fund_benchmark.公募基金产品绩效快照",
                "动态基准使用经验值分档",
                "动态基准只有取得当年披露表格或带来源的年度核验权重后才能分档，禁止按目标年份经验估算。",
                heuristic_dynamic_rows, rule_id="BENCHMARK_DYNAMIC_WEIGHT_HEURISTIC_FORBIDDEN",
            )
        semantic_component_rows = [
            dict(row)
            for row in conn.execute(
                '''
                SELECT "基金代码", "基金名称", "业绩比较基准", "基准风险资产权重",
                       "基准权益权重_百分比", "基准债券权重_百分比", "基准货币权重_百分比",
                       "基准商品权重_百分比", "基准未知权重_百分比", "基准解析说明"
                FROM "公募基金产品绩效快照"
                WHERE (
                       ("业绩比较基准" LIKE '%中证货币型基金指数%' AND COALESCE("基准货币权重_百分比", 0) <= 0.01)
                    OR ("业绩比较基准" LIKE '%中证货币基金指数%' AND COALESCE("基准货币权重_百分比", 0) <= 0.01)
                    OR ("业绩比较基准" LIKE '%中债综合全价%' AND COALESCE("基准债券权重_百分比", 0) <= 0.01)
                    OR ("业绩比较基准" LIKE '%中债-综合全价%' AND COALESCE("基准债券权重_百分比", 0) <= 0.01)
                    OR ("业绩比较基准" LIKE '%上证大宗商品股票指数%' AND COALESCE("基准权益权重_百分比", 0) <= 0.01)
                    OR ("业绩比较基准" LIKE '%黄金产业股票指数%' AND COALESCE("基准权益权重_百分比", 0) <= 0.01)
                )
                  AND COALESCE("基准未知权重_百分比", 0) <= 0.01
                ORDER BY "基金代码" LIMIT 50
                '''
            )
        ]
        if semantic_component_rows:
            add_issue(
                issues, "error", "public_fund_benchmark.公募基金产品绩效快照",
                "明确基准成分资产分类冲突",
                "货币基金指数应计入现金，中债债券指数应计入债券，资源产业股票指数应计入权益。",
                semantic_component_rows, rule_id="BENCHMARK_COMPONENT_ASSET_SEMANTIC_CONFLICT",
            )
        unresolved_disclosed_rows = [
            dict(row)
            for row in conn.execute(
                '''
                SELECT "基金代码", "基金名称", "业绩比较基准", "基准风险资产权重",
                       "基准映射置信度", "基准未知权重_百分比", "基准解析说明"
                FROM "公募基金产品绩效快照"
                WHERE "业绩基准获取状态" IN ('已取得未解析', '已取得未完整解析')
                  AND TRIM(COALESCE("基准风险资产权重", '')) = ''
                  AND COALESCE("业绩比较基准", '') LIKE '%指数%'
                ORDER BY "基金代码" LIMIT 50
                '''
            )
        ]
        if unresolved_disclosed_rows:
            add_issue(
                issues, "warn", "public_fund_benchmark.公募基金产品绩效快照",
                "已披露基准尚无法确定权益分档",
                f"{len(unresolved_disclosed_rows)} 条样本已取得指数基准原文，但尚缺当期变量、跨资产指数成分权重或可核验映射。",
                unresolved_disclosed_rows, rule_id="BENCHMARK_DISCLOSED_BUT_UNRESOLVED",
            )

        static_vector_mismatches: list[dict[str, Any]] = []
        try:
            from benchmark_asset_classification import compute_benchmark_asset_mix, is_static_benchmark_formula, load_benchmark_catalog

            catalog = load_benchmark_catalog()
            formula_cache: dict[str, dict[str, Any]] = {}
            fund_field_map = {
                "权益": "基准权益权重_百分比", "债券": "基准债券权重_百分比", "现金": "基准货币权重_百分比",
                "商品": "基准商品权重_百分比", "另类": "基准另类权重_百分比", "其他": "基准未知权重_百分比",
            }
            expected_field_map = {name: f"基准资产大类-{name}" for name in fund_field_map}
            for stored_row in conn.execute('SELECT * FROM "公募基金产品绩效快照"'):
                row = dict(stored_row)
                benchmark = str(row.get("业绩比较基准") or "").strip()
                if str(row.get("基准风险资产权重来源") or "").strip() == "年度披露权重核验":
                    continue
                if not benchmark or not is_static_benchmark_formula(benchmark):
                    continue
                expected = formula_cache.setdefault(benchmark, compute_benchmark_asset_mix(benchmark, catalog))
                if expected.get("基准资产已映射权重") is None and expected.get("基准资产未映射权重") is None:
                    continue
                differences = []
                for name, stored_field in fund_field_map.items():
                    stored_value = float(row.get(stored_field) or 0.0)
                    expected_value = float(expected.get(expected_field_map[name]) or 0.0)
                    if abs(stored_value - expected_value) > 0.011:
                        differences.append(name)
                expected_bucket = str(expected.get("基准风险资产权重") or "")
                if str(row.get("基准风险资产权重") or "") != expected_bucket:
                    differences.append("权益分档")
                if differences:
                    static_vector_mismatches.append(
                        {
                            "产品类型": "公募基金", "产品代码": row.get("基金代码"), "产品名称": row.get("基金名称"),
                            "差异字段": "、".join(differences), "业绩比较基准": benchmark,
                            "当前分档": row.get("基准风险资产权重"), "重算分档": expected_bucket,
                        }
                    )
        except Exception as exc:
            add_issue(
                issues, "error", "public_fund_benchmark.公募基金产品绩效快照",
                "静态基准重算检查失败", f"统一基准解析器未能完成重算检查：{exc}", [],
                rule_id="BENCHMARK_STATIC_VECTOR_MISMATCH",
            )
        if static_vector_mismatches:
            add_issue(
                issues, "error", "public_fund_benchmark.公募基金产品绩效快照",
                "静态基准存储向量与原文重算不一致",
                f"{len(static_vector_mismatches)} 条静态基准的资产向量或权益分档与统一解析器重算结果不一致。",
                static_vector_mismatches[:50], rule_id="BENCHMARK_STATIC_VECTOR_MISMATCH",
            )
        exclusive_vector_rows = [
            dict(row)
            for row in conn.execute(
                '''
                SELECT "基金代码", "基金名称", "基准风险资产权重", "非权益比较轨道", "正式可比池",
                       "基准权益权重_百分比", "基准债券权重_百分比", "基准货币权重_百分比",
                       "基准商品权重_百分比", "基准另类权重_百分比", "基准未知权重_百分比",
                       "基准互斥权重合计_百分比"
                FROM "公募基金产品绩效快照"
                WHERE "可比池样本资格" = '是'
                  AND ABS(COALESCE("基准互斥权重合计_百分比", 0) - 100) > 0.01
                ORDER BY "基金代码" LIMIT 50
                '''
            )
        ]
        if exclusive_vector_rows:
            add_issue(
                issues, "error", "public_fund_benchmark.公募基金产品绩效快照",
                "基准互斥资产向量异常", "进入正式可比池的互斥资产向量必须合计100%±0.01%。",
                exclusive_vector_rows, rule_id="BENCHMARK_EXCLUSIVE_VECTOR_INVALID",
            )
        overlay_rows = [
            dict(row)
            for row in conn.execute(
                '''
                SELECT "基金代码", "基金名称", "基准权益权重_百分比",
                       "基准港股权益权重_百分比", "基准海外权益权重_百分比", "业绩比较基准"
                FROM "公募基金产品绩效快照"
                WHERE COALESCE("基准港股权益权重_百分比", 0) > COALESCE("基准权益权重_百分比", 0) + 0.01
                   OR COALESCE("基准海外权益权重_百分比", 0) > COALESCE("基准权益权重_百分比", 0) + 0.01
                ORDER BY "基金代码" LIMIT 50
                '''
            )
        ]
        if overlay_rows:
            add_issue(
                issues, "error", "public_fund_benchmark.公募基金产品绩效快照",
                "权益地域子项超过总权益", "港股权益和海外权益均不得超过总权益，且不得重复计入互斥资产合计。",
                overlay_rows, rule_id="BENCHMARK_EQUITY_OVERLAY_INVALID",
            )
        semantic_rows = [
            dict(row)
            for row in conn.execute(
                '''
                SELECT "基金代码", "基金名称", "业绩比较基准", "基准风险资产权重", "非权益比较轨道", "正式可比池"
                FROM "公募基金产品绩效快照"
                WHERE ("基金名称" LIKE '%原油%' AND "非权益比较轨道" <> '商品主导')
                   OR ("基金名称" LIKE '%全球美元债%' AND "非权益比较轨道" <> '债券主导')
                   OR ("业绩比较基准" LIKE '%黄金产业股票指数%' AND COALESCE("基准权益权重_百分比", 0) <= 0.01)
                ORDER BY "基金代码" LIMIT 50
                '''
            )
        ]
        if semantic_rows:
            add_issue(
                issues, "error", "public_fund_benchmark.公募基金产品绩效快照",
                "比较轨道业务语义异常", "原油应进入商品主导，全球美元债应进入债券主导，黄金产业股票指数应计入权益。",
                semantic_rows, rule_id="BENCHMARK_COMPARISON_TRACK_SEMANTIC_INVALID",
            )
        if "基金主份额映射" in tables:
            primary_mapping_rows = [
                dict(row)
                for row in conn.execute(
                    '''
                    SELECT "基金家族ID", COUNT(*) AS share_count,
                           SUM(CASE WHEN "是否主份额" = 1 THEN 1 ELSE 0 END) AS primary_count,
                           COUNT(DISTINCT "主基金代码") AS primary_code_count
                    FROM "基金主份额映射"
                    GROUP BY "基金家族ID"
                    HAVING primary_count <> 1 OR primary_code_count <> 1
                    ORDER BY "基金家族ID" LIMIT 50
                    '''
                )
            ]
            if primary_mapping_rows:
                add_issue(
                    issues, "error", "sqlite.基金主份额映射", "基金家族主份额不唯一",
                    "每个基金家族必须有且仅有一个主份额和一个主基金代码。",
                    primary_mapping_rows, rule_id="FUND_PRIMARY_SHARE_MAPPING_INVALID",
                )
        if "策略基准资产配置" in tables:
            strategy_vector_rows = [
                dict(row)
                for row in conn.execute(
                    '''
                    SELECT "统一策略ID", "策略名称", "业绩基准文本", "基准风险资产权重",
                           "非权益比较轨道", "正式可比池", "基准互斥权重合计_百分比",
                           "基准资产大类-权益", "基准港股权益权重", "基准海外权益权重"
                    FROM "策略基准资产配置"
                    WHERE ("可比池样本资格" = '是' AND ABS(COALESCE("基准互斥权重合计_百分比", 0) - 100) > 0.01)
                       OR COALESCE("基准港股权益权重", 0) > COALESCE("基准资产大类-权益", 0) + 0.01
                       OR COALESCE("基准海外权益权重", 0) > COALESCE("基准资产大类-权益", 0) + 0.01
                    ORDER BY "统一策略ID" LIMIT 50
                    '''
                )
            ]
            if strategy_vector_rows:
                add_issue(
                    issues, "error", "sqlite.策略基准资产配置", "策略基准互斥向量或权益子项异常",
                    "策略进入正式可比池时互斥向量必须合计100%±0.01%，港股及海外权益不得超过总权益。",
                    strategy_vector_rows, rule_id="BENCHMARK_EXCLUSIVE_VECTOR_INVALID",
                )


def audit_fof_universe_coverage(issues: list[dict[str, Any]], db_path: Path, site_dir: Path) -> None:
    if not db_path.exists():
        return
    required_tables = ["基金标准分类字典", "基金信息", "FOF基金F10基准", "FOF基准细分分类", "FOF产品绩效快照", "基金日度净值"]
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "基金标准分类字典" not in tables:
            return
        missing_tables = [table for table in required_tables if table not in tables]
        if missing_tables:
            add_issue(
                issues,
                "error",
                "fof_universe",
                "FOF核心表缺失",
                "全市场 FOF 详情页依赖的 SQLite 表缺失。",
                missing_tables,
                rule_id="FOF_UNIVERSE_SQLITE_COVERAGE_MISSING",
            )
            return
        fof_rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT "基金代码", "标准基金名称"
                FROM "基金标准分类字典"
                WHERE "是否FOF" = 1
                ORDER BY "基金代码"
                """
            )
        ]
        fof_codes = [str(row["基金代码"]) for row in fof_rows if re.fullmatch(r"\d{6}", str(row["基金代码"] or ""))]
        if not fof_codes:
            return
        fof_code_set = set(fof_codes)
        for table, label in [
            ("基金信息", "基金基础信息"),
            ("FOF基金F10基准", "FOF F10基准"),
            ("FOF基准细分分类", "FOF基准细分分类"),
            ("FOF产品绩效快照", "FOF绩效快照"),
        ]:
            present = {
                str(row[0])
                for row in conn.execute(
                    f"""
                    SELECT DISTINCT "基金代码"
                    FROM "{table}"
                    WHERE "基金代码" IN (
                      SELECT "基金代码" FROM "基金标准分类字典" WHERE "是否FOF" = 1
                    )
                    """
                )
            }
            missing = sorted(fof_code_set - present)
            if missing:
                add_issue(
                    issues,
                    "error",
                    f"fof_universe.{table}",
                    "FOF全量表覆盖缺口",
                    f"{label}未覆盖全部 FOF 产品，缺失 {len(missing)} / {len(fof_codes)} 只。",
                    missing[:50],
                    rule_id="FOF_UNIVERSE_SQLITE_COVERAGE_MISSING",
                )
        benchmark_missing = [
            dict(row)
            for row in conn.execute(
                """
                SELECT b."基金代码", b."基金名称", b."采集状态"
                FROM "FOF基金F10基准" b
                JOIN "基金标准分类字典" d ON d."基金代码" = b."基金代码"
                WHERE d."是否FOF" = 1 AND COALESCE(b."业绩比较基准", '') = ''
                ORDER BY b."基金代码"
                LIMIT 50
                """
            )
        ]
        if benchmark_missing:
            add_issue(
                issues,
                "error",
                "fof_universe.FOF基金F10基准",
                "FOF基准原文缺失",
                "FOF F10 已有记录但缺少业绩比较基准原文。",
                benchmark_missing,
                rule_id="FOF_UNIVERSE_SQLITE_COVERAGE_MISSING",
            )
        nav_rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT d."基金代码", d."标准基金名称", b."原始HTML路径",
                       COUNT(n."交易日期") AS nav_rows, MIN(n."交易日期") AS first_nav_date,
                       MAX(n."交易日期") AS latest_nav_date,
                       SUM(CASE WHEN n."交易日期" >= '2025-07-01' THEN 1 ELSE 0 END) AS nav_rows_1y
                FROM "基金标准分类字典" d
                LEFT JOIN "FOF基金F10基准" b ON b."基金代码" = d."基金代码"
                LEFT JOIN "基金日度净值" n ON n."基金代码" = d."基金代码"
                WHERE d."是否FOF" = 1
                GROUP BY d."基金代码", d."标准基金名称", b."原始HTML路径"
                HAVING nav_rows = 0 OR COALESCE(nav_rows_1y, 0) < 120
                ORDER BY nav_rows, d."基金代码"
                """
            )
        ]
        confirmed_gaps = []
        unknown_inception = []
        expected_short_history = []
        for row in nav_rows:
            inception_date = read_f10_inception_date(row.get("原始HTML路径"))
            audited = {**row, "原始成立日期": inception_date}
            if inception_date >= "2025-07-01":
                expected_short_history.append(audited)
            elif inception_date:
                confirmed_gaps.append(audited)
            else:
                unknown_inception.append(audited)
        nav_issue_count = len(confirmed_gaps) + len(unknown_inception)
        if nav_issue_count:
            add_issue(
                issues,
                "warn",
                "fof_universe.基金日度净值",
                "FOF历史净值不足",
                f"已排除 {len(expected_short_history)} 只成立未满一年的正常短历史产品；仍有 {len(confirmed_gaps)} 只成熟 FOF 明确缺历史，{len(unknown_inception)} 只因成立日期未披露或未解析需确认。",
                {
                    "成熟产品明确缺口": confirmed_gaps[:40],
                    "成立日期待确认": unknown_inception[:40],
                    "正常短历史产品数": len(expected_short_history),
                },
                rule_id="FOF_UNIVERSE_NAV_HISTORY_INCOMPLETE",
            )

    detail_dir = site_dir / "data" / "fund_details"
    missing_detail_files = [code for code in fof_codes if not (detail_dir / f"{code}.js").exists()]
    if missing_detail_files:
        add_issue(
            issues,
            "error",
            "fof_universe.fund_details",
            "FOF详情页缺失",
            f"全市场 FOF 中有 {len(missing_detail_files)} / {len(fof_codes)} 只缺少基金详情页增强包。",
            missing_detail_files[:80],
            rule_id="FOF_UNIVERSE_DETAIL_PAGE_MISSING",
        )


def audit_strategy_governance_semantics(issues: list[dict[str, Any]], db_path: Path) -> None:
    if not db_path.exists():
        return
    all_rows: list[dict[str, Any]] = []
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "策略治理标签" not in tables or "策略信息" not in tables:
            return
        all_rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT g."统一策略ID", g."渠道ID", g."策略名称", g."投顾机构", g."治理状态", g."分析分组",
                       g."是否目标盈期次", g."是否清盘策略", g."是否业绩停更", g."是否缺官方业绩",
                       g."是否纳入常规排名", g."官方最新业绩日期", g."标准净值截止日期", g."规则说明",
                       s."策略类型", s."策略状态", s."策略描述", s."标签JSON", s."业绩基准"
                FROM "策略治理标签" g
                LEFT JOIN "策略信息" s ON s."统一策略ID" = g."统一策略ID"
                ORDER BY g."投顾机构", g."策略名称"
                """
            )
        ]
        stale_rank_rows = [
            {
                "统一策略ID": row.get("统一策略ID"),
                "策略名称": row.get("策略名称"),
                "投顾机构": row.get("投顾机构"),
                "治理状态": row.get("治理状态"),
                "官方最新业绩日期": row.get("官方最新业绩日期"),
                "标准净值截止日期": row.get("标准净值截止日期"),
                "是否纳入常规排名": row.get("是否纳入常规排名"),
            }
            for row in all_rows
            if (row.get("是否业绩停更") == 1 or row.get("是否缺官方业绩") == 1)
            and row.get("是否纳入常规排名") == 1
        ][:50]
        rows = [
            row
            for row in all_rows
            if row.get("是否目标盈期次") == 1
        ]
    samples = []
    for row in rows:
        evidence_text = " ".join(
            str(row.get(key) or "")
            for key in ["策略名称", "策略类型", "策略状态", "策略描述", "标签JSON", "业绩基准"]
        )
        if has_target_profit_evidence(evidence_text):
            continue
        samples.append(
            {
                "统一策略ID": row.get("统一策略ID"),
                "策略名称": row.get("策略名称"),
                "投顾机构": row.get("投顾机构"),
                "治理状态": row.get("治理状态"),
                "分析分组": row.get("分析分组"),
                "是否纳入常规排名": row.get("是否纳入常规排名"),
                "原始文本摘要": evidence_text[:500],
            }
        )
        if len(samples) >= 50:
            break
    if samples:
        add_issue(
            issues,
            "error",
            "sqlite.策略治理标签",
            "目标盈标签缺强证据",
            "目标盈/期次标签必须由强证据触发，不能由普通止盈止损、以期满足、目标日期到期时间等弱词触发。",
            samples,
            rule_id="BUSINESS_QUALITY_TARGET_PROFIT_EVIDENCE",
        )
    if stale_rank_rows:
        add_issue(
            issues,
            "error",
            "sqlite.策略治理标签",
            "业绩停更或无官方业绩仍进入常规排名",
            "官方业绩停更超过 31 天或没有官方披露业绩的策略不得使用标准模拟净值进入常规排名。",
            stale_rank_rows,
            rule_id="STALE_OFFICIAL_PERFORMANCE_IN_REGULAR_RANK",
        )

    series_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in all_rows:
        base = normalize_target_profit_series(row.get("策略名称"))
        if not base:
            continue
        advisor = canonical_advisor_institution(row.get("投顾机构"))
        series_groups[(advisor, base)].append(row)
    mixed_samples = []
    for (advisor, series_name), group_rows in sorted(series_groups.items()):
        if len(group_rows) < 2:
            continue
        target_rows = [row for row in group_rows if row.get("是否目标盈期次") == 1]
        non_target_rows = [row for row in group_rows if row.get("是否目标盈期次") != 1]
        if target_rows and non_target_rows:
            mixed_samples.append(
                {
                    "投顾机构": advisor,
                    "系列名称": series_name,
                    "期次数": len(group_rows),
                    "目标盈期次数": len(target_rows),
                    "非目标盈期次数": len(non_target_rows),
                    "样本": [
                        {
                            "统一策略ID": row.get("统一策略ID"),
                            "策略名称": row.get("策略名称"),
                            "治理状态": row.get("治理状态"),
                            "分析分组": row.get("分析分组"),
                            "是否目标盈期次": row.get("是否目标盈期次"),
                            "规则说明": row.get("规则说明"),
                        }
                        for row in group_rows[:20]
                    ],
                }
            )
        if len(mixed_samples) >= 20:
            break
    if mixed_samples:
        add_issue(
            issues,
            "error",
            "sqlite.策略治理标签",
            "目标盈同系列标签不一致",
            "同一投顾、同一去期次系列中不能同时存在目标盈期次和非目标盈期次。",
            mixed_samples,
            rule_id="BUSINESS_QUALITY_TARGET_PROFIT_SERIES_CONSISTENCY",
        )


def audit_target_profit_page_consistency(issues: list[dict[str, Any]], db_path: Path, site_dir: Path) -> None:
    pack_path = site_dir / "data" / "target_profit_analysis_pack.js"
    if not db_path.exists() or not pack_path.exists():
        return
    try:
        pack = read_js_object(pack_path)
    except Exception as exc:
        add_issue(issues, "error", "target_profit_analysis_pack", "页面包解析失败", f"cannot parse {pack_path}: {exc}", rule_id="PAGE_PACK_FILE_PARSE_FAILED")
        return
    periods = [row for row in pack.get("periods") or [] if isinstance(row, dict)]
    period_ids = {str(row.get("统一策略ID") or "") for row in periods if row.get("统一策略ID")}
    naming_rows: list[dict[str, Any]] = []
    series_groups: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in periods:
        institution = str(row.get("投顾机构") or "").strip()
        canonical_institution = canonical_advisor_institution(institution)
        if institution and canonical_institution != institution:
            naming_rows.append(
                {
                    "统一策略ID": row.get("统一策略ID"),
                    "策略名称": row.get("策略名称"),
                    "投顾机构": institution,
                    "应归一为": canonical_institution,
                }
            )
        series_name = str(row.get("系列名称") or "").strip()
        series_id = str(row.get("系列ID") or "").strip()
        if canonical_institution and series_name and series_id:
            series_groups[(canonical_institution, series_name)].add(series_id)
    if naming_rows:
        add_issue(
            issues,
            "error",
            "target_profit_analysis_pack.periods",
            "目标盈页面包机构名称未归一",
            f"发现 {len(naming_rows)} 条目标盈期次仍使用历史机构别名，机构统计会被拆分。",
            naming_rows[:50],
            rule_id="PAGE_BUSINESS_NAMING_NOT_CANONICAL",
        )
    split_rows = [
        {"投顾机构": advisor, "系列名称": series_name, "系列ID列表": sorted(series_ids)}
        for (advisor, series_name), series_ids in sorted(series_groups.items())
        if len(series_ids) > 1
    ]
    if split_rows:
        add_issue(
            issues,
            "error",
            "target_profit_analysis_pack.series",
            "目标盈同机构同系列被别名拆分",
            "同一归一机构、同一目标盈系列只能生成一个系列ID。",
            split_rows[:50],
            rule_id="TARGET_PROFIT_SERIES_SPLIT_BY_INSTITUTION_ALIAS",
        )
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "策略治理标签" not in tables:
            return
        display_channels = tuple(sorted(PAGE_DISPLAY_CHANNEL_IDS))
        placeholders = ",".join("?" for _ in display_channels)
        target_rows = [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT g."统一策略ID", g."策略名称", g."投顾机构", g."治理状态", g."分析分组", g."规则说明"
                FROM "策略治理标签" g
                LEFT JOIN "策略信息" s ON s."统一策略ID" = g."统一策略ID"
                WHERE g."是否目标盈期次" = 1
                  AND COALESCE(s."渠道ID", g."渠道ID") IN ({placeholders})
                ORDER BY g."策略名称"
                """,
                display_channels,
            )
        ]
    finally:
        conn.close()
    missing = [row for row in target_rows if str(row.get("统一策略ID") or "") not in period_ids]
    if missing:
        add_issue(
            issues,
            "error",
            "target_profit_analysis_pack.periods",
            "目标盈页面包漏期次",
            "策略治理标签中标记为目标盈期次的策略必须进入目标盈分析页面包，否则系列期次数和生命周期复盘会不完整。",
            missing[:50],
            rule_id="BUSINESS_QUALITY_TARGET_PROFIT_PAGE_PACK_CONSISTENCY",
        )


def audit_qd_limit_page_consistency(issues: list[dict[str, Any]], site_dir: Path) -> None:
    path = site_dir / "qd-fund-detail.html"
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    match = re.search(r"window\.QD_FUND_DATA=(.*?);</script>", text, re.S)
    if not match:
        add_issue(
            issues,
            "error",
            "qd_fund_detail",
            "QD限额页面包解析失败",
            "qd-fund-detail.html 必须内嵌 window.QD_FUND_DATA，供明细页和限额稽核复用。",
            str(path),
            rule_id="BUSINESS_QUALITY_QD_LIMIT_STATUS_CONFLICT",
        )
        return
    try:
        payload = json.loads(match.group(1))
    except Exception as exc:
        add_issue(
            issues,
            "error",
            "qd_fund_detail",
            "QD限额页面包解析失败",
            f"cannot parse {path}: {exc}",
            rule_id="BUSINESS_QUALITY_QD_LIMIT_STATUS_CONFLICT",
        )
        return
    samples = []
    for fund in payload.get("funds") or []:
        if fund.get("limitLevel") != "risk":
            continue
        personal = str(fund.get("personalLimit") or "")
        non_person = str(fund.get("nonPersonLimit") or "")
        purchase_status = str(fund.get("limit") or "")
        if "未设日限额" in personal or "未设日限额" in non_person:
            samples.append(
                {
                    "基金代码": fund.get("code"),
                    "基金名称": fund.get("name"),
                    "个人限额": personal,
                    "非个人限额": non_person,
                    "申购状态": purchase_status,
                    "公告标题": fund.get("limitSourceTitle"),
                }
            )
        if len(samples) >= 20:
            break
    if samples:
        add_issue(
            issues,
            "error",
            "qd_fund_detail.funds",
            "QD限额状态冲突",
            "销售端显示未设日限额的 QD 基金不得被节假日/非交易日暂停申赎公告覆盖为限额风险。",
            samples,
            rule_id="BUSINESS_QUALITY_QD_LIMIT_STATUS_CONFLICT",
        )


def audit_generic_page_packs(issues: list[dict[str, Any]], site_dir: Path) -> None:
    data_dir = site_dir / "data"
    for path in sorted(data_dir.glob("*.js")):
        if path.name in {"fund_detail_pack.js", "ai_semantic_index.js"}:
            continue
        try:
            pack = read_js_object(path)
        except Exception:
            continue
        if isinstance(pack, dict) and "dict" not in pack and isinstance(pack.get("fields"), list) and isinstance(pack.get("rows"), list):
            audit_compact_rows(issues, path.name, pack["fields"], pack["rows"])


def audit_official_performance_image_assets(
    issues: list[dict[str, Any]],
    site_dir: Path,
) -> None:
    """Reject screenshot fallbacks that could be mistaken for a real data curve."""

    details_dir = site_dir / "data" / "details"
    if not details_dir.is_dir():
        return
    detail_paths = sorted(details_dir.glob("*.js"))
    detail_paths.extend(sorted(details_dir.rglob("*.js.gz")))
    references: list[dict[str, Any]] = []
    for path in detail_paths:
        try:
            if path.suffix.lower() == ".gz":
                with gzip.open(path, "rt", encoding="utf-8") as handle:
                    text = handle.read()
            else:
                text = path.read_text(encoding="utf-8")
            value_pos = text.find("=")
            if value_pos < 0:
                continue
            payload_text = text[value_pos + 1 :].strip()
            if payload_text.endswith(";"):
                payload_text = payload_text[:-1]
            payload = json.loads(payload_text)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
        image = payload.get("officialPerformanceImage")
        if not isinstance(image, dict) or not image:
            continue
        references.append(
            {
                "策略ID": str(payload.get("id") or ""),
                "详情文件": str(path),
                "图片引用": str(image.get("url") or "").strip(),
            }
        )
    image_dir = site_dir / "assets" / "gfbank-performance"
    image_assets = [
        str(path)
        for path in sorted(image_dir.glob("*"))
        if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
    ] if image_dir.is_dir() else []
    if references or image_assets:
        add_issue(
            issues,
            "error",
            "strategy_details.officialPerformanceImage",
            "截图被用作策略业绩走势图",
            {
                "详情引用数": len(references),
                "截图资源数": len(image_assets),
                "详情样本": references[:30],
                "资源样本": image_assets[:30],
            },
            rule_id="PAGE_PERFORMANCE_SCREENSHOT_AS_CURVE",
        )


def audit_single_point_interval_returns(
    issues: list[dict[str, Any]],
    site_dir: Path,
) -> None:
    """Reject interval returns inferred from a single curve point."""

    details_dir = site_dir / "data" / "details"
    if not details_dir.is_dir():
        return
    detail_paths = sorted(details_dir.glob("*.js"))
    detail_paths.extend(sorted(details_dir.rglob("*.js.gz")))
    failures: list[dict[str, Any]] = []
    for path in detail_paths:
        try:
            if path.suffix.lower() == ".gz":
                with gzip.open(path, "rt", encoding="utf-8") as handle:
                    text = handle.read()
            else:
                text = path.read_text(encoding="utf-8")
            value_pos = text.find("=")
            payload_text = text[value_pos + 1 :].strip()
            if payload_text.endswith(";"):
                payload_text = payload_text[:-1]
            payload = json.loads(payload_text)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
        strategy_id = str(payload.get("id") or "")
        if not strategy_id.startswith("gfbank_cgb__"):
            continue
        curves = payload.get("curves") or {}
        matrix = {
            str(row.get("口径") or ""): row
            for row in (payload.get("intervalMatrix") or [])
            if isinstance(row, dict)
        }
        for series_name in ("披露业绩", "基准业绩"):
            points = ((curves.get(series_name) or {}).get("points") or [])
            if len(points) >= 2:
                continue
            row = matrix.get(series_name) or {}
            invalid_fields = [
                field
                for field in ("近一周", "近三月", "今年以来")
                if row.get(field) is not None
            ]
            if invalid_fields:
                failures.append(
                    {
                        "策略ID": strategy_id,
                        "口径": series_name,
                        "曲线点数": len(points),
                        "错误区间字段": invalid_fields,
                        "详情文件": str(path),
                    }
                )
        hs300_points = ((curves.get("沪深300业绩") or {}).get("points") or [])
        if len(hs300_points) < 2:
            row = matrix.get("沪深300业绩") or {}
            invalid_fields = [
                field
                for field in ("近一周", "近一月", "近三月", "近6月", "近1年", "今年以来", "成立以来")
                if row.get(field) is not None
            ]
            if invalid_fields:
                failures.append(
                    {
                        "策略ID": strategy_id,
                        "口径": "沪深300业绩",
                        "曲线点数": len(hs300_points),
                        "错误区间字段": invalid_fields,
                        "详情文件": str(path),
                    }
                )
    if failures:
        add_issue(
            issues,
            "error",
            "strategy_details.intervalMatrix",
            "单点曲线被计算为区间收益",
            {
                "失败数": len(failures),
                "失败样本": failures[:30],
            },
            rule_id="PAGE_SINGLE_POINT_DERIVED_INTERVAL_RETURN",
        )


def audit_minimal_publish_cache_guard(
    issues: list[dict[str, Any]],
    site_dir: Path,
) -> None:
    """Require deployed detail pages to invalidate stale build assets and data."""

    runtime_path = site_dir / "assets" / "minimal-publish-runtime.js"
    strategy_page = site_dir / "strategy.html"
    if not runtime_path.is_file() or not strategy_page.is_file():
        return
    runtime_text = runtime_path.read_text(encoding="utf-8")
    page_text = strategy_page.read_text(encoding="utf-8")
    if "minimal-publish-runtime.js" not in page_text:
        return
    required_runtime_tokens = {
        "远端版本检查": "../version.json",
        "禁用版本检查缓存": 'cache: "no-store"',
        "当前构建标识": "buildId",
        "详情文件版本参数": '?v=${encodeURIComponent(buildId)}',
    }
    missing = [name for name, token in required_runtime_tokens.items() if token not in runtime_text]
    runtime_tag_versioned = bool(
        re.search(r'minimal-publish-runtime\.js\?v=[^"\']+', page_text)
    )
    if not runtime_tag_versioned:
        missing.append("页面运行时版本参数")
    if missing:
        add_issue(
            issues,
            "error",
            "minimal_publish.runtime_cache_guard",
            "最小发布页缺少旧版本缓存防护",
            {
                "缺失检查": missing,
                "运行时文件": str(runtime_path),
                "策略详情页": str(strategy_page),
            },
            rule_id="PAGE_STALE_BUILD_CACHE_GUARD_MISSING",
        )


def mixed_ranking_visible_strategy_rows(summary_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return every strategy that is actually addressable from the strategy list.

    Holding/replay completeness affects disclosure and analysis quality, but it is
    not a list-visibility or common-ranking precondition. Ranking admission is
    governed separately by 是否纳入常规排名 and official performance evidence.
    """

    return [row for row in summary_rows if str(row.get("统一策略ID") or "").strip()]


def mixed_ranking_is_guangfa_strategy(row: dict[str, Any]) -> bool:
    """Match the ranking pack's Guangfa ecosystem flag.

    The flag covers Guangfa Fund, Guangfa Bank and Guangfa Securities
    distribution channels.  The advisor institution itself can be a third
    party on the Guangfa Securities shelf, so institution-only matching
    undercounts valid Guangfa-channel strategies.
    """

    if row.get("是否广发") == "是" or row.get("是否广发策略") == "是":
        return True
    return "广发" in f"{row.get('投顾机构') or ''} {row.get('渠道') or ''}"


def mixed_ranking_expected_end_date(db_path: Path) -> str:
    uri = f"{db_path.resolve().as_uri()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as conn:
        def max_date(table: str, column: str) -> str:
            columns = {str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")')}
            if column not in columns:
                return ""
            value = conn.execute(f'SELECT MAX("{column}") FROM "{table}"').fetchone()[0]
            return str(value or "")[:10]

        strategy_date = max_date("策略标准业绩净值", "交易日期") or max_date("策略日度业绩", "交易日期")
        fund_date = max_date("基金日度净值", "交易日期") or max_date("公募基金产品绩效快照", "绩效截止日期")
    candidates = [value for value in (strategy_date, fund_date) if value]
    return min(candidates) if candidates else ""


def audit_mixed_performance_pack(issues: list[dict[str, Any]], site_dir: Path, db_path: Path) -> None:
    pack_path = site_dir / "data" / "mixed_performance_scatter_pack.json"
    summary_path = site_dir / "data" / "basic_summary_core.js"
    if not pack_path.exists() or not summary_path.exists():
        add_issue(
            issues,
            "error",
            "mixed_performance_scatter_pack",
            "页面包缺失",
            f"mixed={pack_path.exists()} summary={summary_path.exists()}",
            rule_id="MIXED_RANKING_STRATEGY_UNIVERSE_MISMATCH",
        )
        return
    try:
        pack = load_json_object(pack_path)
        summary = read_js_object(summary_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        add_issue(
            issues,
            "error",
            "mixed_performance_scatter_pack",
            "页面包解析失败",
            str(exc),
            rule_id="PAGE_PACK_FILE_PARSE_FAILED",
        )
        return

    summary_rows = summary.get("strategies") or []
    expected_rows = mixed_ranking_visible_strategy_rows(summary_rows)
    expected_ids = {str(row.get("统一策略ID") or "").strip() for row in expected_rows if row.get("统一策略ID")}
    canonical_nonrankable_ids = {
        str(row.get("统一策略ID") or "").strip()
        for row in expected_rows
        if row.get("统一策略ID")
        and str(row.get("是否纳入常规排名") or "0").strip().lower() not in {"1", "1.0", "true"}
    }
    mixed_strategy_rows = [row for row in pack.get("rows") or [] if row.get("productType") == "投顾策略"]
    actual_ids = {str(row.get("id") or "").strip() for row in mixed_strategy_rows if row.get("id")}

    source_meta: dict[str, Any] = {}
    declared_nonrankable_ids: set[str] = set()
    source_path_text = str((pack.get("meta") or {}).get("sourceWorkbookPack") or "").strip()
    source_path = Path(source_path_text) if source_path_text else None
    source_load_error = ""
    if source_path is not None:
        if not source_path.is_absolute():
            source_path = site_dir.parent / source_path
        try:
            source_payload = load_json_object(source_path)
            if not isinstance(source_payload, dict):
                raise ValueError("mixed ranking source must be a JSON object")
            source_meta = source_payload.get("meta") if isinstance(source_payload.get("meta"), dict) else {}
            for row in source_payload.get("excludedStrategyRows") or []:
                reason = str(row.get("剔除原因") or "")
                strategy_id = str(row.get("产品代码") or "").strip()
                if strategy_id and reason.startswith("策略列表可见但不具备混排资格；"):
                    declared_nonrankable_ids.add(strategy_id)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            source_load_error = f"{type(exc).__name__}: {exc}"

    expected_rankable_ids = expected_ids - canonical_nonrankable_ids
    source_mismatches: list[dict[str, Any]] = []
    if source_load_error:
        source_mismatches.append({"sourceWorkbookPack": source_path_text, "error": source_load_error})
    if declared_nonrankable_ids - expected_ids:
        source_mismatches.append(
            {
                "invalidDeclaredNonrankable": sorted(declared_nonrankable_ids - expected_ids)[:20],
            }
        )
    if declared_nonrankable_ids != canonical_nonrankable_ids:
        source_mismatches.append(
            {
                "declaredNonrankableMissing": sorted(canonical_nonrankable_ids - declared_nonrankable_ids)[:20],
                "declaredNonrankableExtra": sorted(declared_nonrankable_ids - canonical_nonrankable_ids)[:20],
            }
        )
    if source_meta:
        expected_counts = {
            "strategyListVisibleRowCount": len(expected_ids),
            "strategyListRankingSourceCoveredCount": len(expected_rankable_ids),
            "strategyListNonrankableRowCount": len(canonical_nonrankable_ids),
            "strategyListMissingEligibleRowCount": 0,
        }
        for key, expected_value in expected_counts.items():
            actual_value = source_meta.get(key)
            if actual_value != expected_value:
                source_mismatches.append(
                    {"field": key, "expected": expected_value, "actual": actual_value}
                )

    inactive_ranked_ids = actual_ids & canonical_nonrankable_ids
    if inactive_ranked_ids:
        inactive_rows = [
            {
                "统一策略ID": str(row.get("统一策略ID") or "").strip(),
                "策略名称": row.get("策略名称"),
                "策略治理状态": row.get("策略治理状态"),
                "是否已停止": row.get("是否已停止"),
                "是否纳入常规排名": row.get("是否纳入常规排名"),
            }
            for row in expected_rows
            if str(row.get("统一策略ID") or "").strip() in inactive_ranked_ids
        ]
        add_issue(
            issues,
            "error",
            "mixed_performance_scatter_pack",
            "治理排除策略进入当前排名",
            f"共有 {len(inactive_ranked_ids)} 条是否纳入常规排名=0的策略仍存在于排名页。",
            inactive_rows[:20],
            rule_id="RANKING_INCLUDES_INACTIVE_STRATEGY",
        )

    if expected_rankable_ids != actual_ids or source_mismatches:
        add_issue(
            issues,
            "error",
            "mixed_performance_scatter_pack",
            "策略范围不一致",
            (
                f"策略列表可查询={len(expected_ids)}，治理不具备混排资格={len(canonical_nonrankable_ids)}，"
                f"应进入排名={len(expected_rankable_ids)}，排名页={len(actual_ids)}，"
                f"缺少={len(expected_rankable_ids - actual_ids)}，多出={len(actual_ids - expected_rankable_ids)}"
            ),
            {
                "missing": sorted(expected_rankable_ids - actual_ids)[:20],
                "extra": sorted(actual_ids - expected_rankable_ids)[:20],
                "sourceMismatches": source_mismatches[:20],
            },
            rule_id="MIXED_RANKING_STRATEGY_UNIVERSE_MISMATCH",
        )

    expected_guangfa = sum(
        1
        for row in expected_rows
        if str(row.get("统一策略ID") or "").strip() in expected_rankable_ids
        and mixed_ranking_is_guangfa_strategy(row)
    )
    actual_guangfa = sum(1 for row in mixed_strategy_rows if row.get("isGuangfa"))
    if expected_guangfa != actual_guangfa:
        add_issue(
            issues,
            "error",
            "mixed_performance_scatter_pack",
            "广发策略范围不一致",
            f"策略列表广发={expected_guangfa}，排名页广发={actual_guangfa}",
            rule_id="MIXED_RANKING_STRATEGY_UNIVERSE_MISMATCH",
        )

    broken_links: list[dict[str, str]] = []
    for row in mixed_strategy_rows:
        detail_url = str(row.get("detailUrl") or "").strip()
        linked_id = (parse_qs(urlparse(detail_url).query).get("id") or [""])[0]
        if not linked_id or linked_id != str(row.get("id") or "") or linked_id not in expected_ids:
            broken_links.append({"id": str(row.get("id") or ""), "name": str(row.get("name") or ""), "detailUrl": detail_url})
    if broken_links:
        add_issue(
            issues,
            "error",
            "mixed_performance_scatter_pack",
            "策略详情链接失效",
            f"共 {len(broken_links)} 条策略详情链接未指向策略列表统一策略ID",
            broken_links[:20],
            rule_id="MIXED_RANKING_STRATEGY_DETAIL_LINK_INVALID",
        )

    interval_dates = (pack.get("meta") or {}).get("intervalAsOfDates") or {}
    interval_mismatches: list[dict[str, str]] = []
    for interval in (pack.get("meta") or {}).get("intervals") or []:
        end_dates: list[str] = []
        for row in pack.get("rows") or []:
            date_range = str(((row.get("intervals") or {}).get(interval) or {}).get("range") or "")
            if "~" in date_range and date_range.split("~", 1)[1]:
                end_dates.append(date_range.split("~", 1)[1])
        expected_date = max(end_dates) if end_dates else ""
        actual_date = str(interval_dates.get(interval) or "")
        if not actual_date or (expected_date and actual_date != expected_date):
            interval_mismatches.append({"interval": interval, "meta": actual_date, "rowMax": expected_date})
    if interval_mismatches:
        add_issue(
            issues,
            "error",
            "mixed_performance_scatter_pack",
            "区间截止日不一致",
            "页面区间截止日缺失或与产品区间最大披露日不一致",
            interval_mismatches,
            rule_id="MIXED_RANKING_INTERVAL_AS_OF_INVALID",
        )

    expected_end_date = mixed_ranking_expected_end_date(db_path)
    pack_end_date = str((pack.get("meta") or {}).get("asOfDate") or "")
    if expected_end_date and pack_end_date != expected_end_date:
        add_issue(
            issues,
            "error",
            "mixed_performance_scatter_pack",
            "排名截止日落后于数据库水位",
            f"数据库最新共同可比日={expected_end_date}，排名页截止日={pack_end_date or '缺失'}",
            {
                "expectedAsOfDate": expected_end_date,
                "actualAsOfDate": pack_end_date,
            },
            rule_id="MIXED_RANKING_INTERVAL_AS_OF_INVALID",
        )


def audit_strategy_overseas_classification(issues: list[dict[str, Any]], site_dir: Path) -> None:
    path = site_dir / "data" / "basic_summary_core.js"
    if not path.exists():
        return
    try:
        pack = read_js_object(path)
    except Exception as exc:
        add_issue(
            issues,
            "error",
            "strategy_classification",
            "海外/全球分类证据缺失",
            f"cannot parse {path}: {exc}",
            rule_id="PAGE_PACK_FILE_PARSE_FAILED",
        )
        return
    strategies = pack.get("strategies") if isinstance(pack, dict) else None
    if not isinstance(strategies, list):
        return
    samples: list[dict[str, Any]] = []
    for row in strategies:
        if not isinstance(row, dict):
            continue
        business = str(row.get("业务分类") or row.get("主可比池") or "")
        region = str(row.get("市场地域") or "")
        labels = str(row.get("特殊标签") or "")
        overseas_flag = business == "海外/全球型" or region == "海外/全球" or "海外全球" in labels
        if not overseas_flag:
            continue
        qdii_weight = number_value(row.get("QDII权重"))
        if qdii_weight > 0.01:
            continue
        benchmark = row.get("业绩基准说明") or row.get("业绩基准") or row.get("基准公式解析") or ""
        # Do not use derived fields such as 特殊标签/业务分类依据 as evidence; they can preserve old wrong classifications.
        has_direct_evidence = has_strong_overseas_text(row.get("策略名称"), row.get("披露策略类型"), row.get("策略概念"))
        has_benchmark_evidence = has_overseas_benchmark_text(benchmark)
        if has_direct_evidence or has_benchmark_evidence:
            continue
        samples.append(
            {
                "统一策略ID": row.get("统一策略ID"),
                "策略名称": row.get("策略名称"),
                "业务分类": business,
                "市场地域": region,
                "特殊标签": labels,
                "QDII权重": qdii_weight,
                "业绩基准说明": benchmark,
                "业务分类依据": row.get("业务分类依据"),
            }
        )
        if len(samples) >= 50:
            break
    if samples:
        add_issue(
            issues,
            "error",
            "strategy_classification",
            "海外/全球分类证据缺失",
            "策略被归入海外/全球，但当前 QDII/海外持仓权重为 0，且策略名称或业绩基准缺少强海外证据。",
            samples,
            rule_id="BUSINESS_QUALITY_OVERSEAS_CLASSIFICATION_EVIDENCE",
        )


def non_null_rate(conn: sqlite3.Connection, table: str, field: str, where_clause: str | None = None) -> dict[str, Any]:
    where_sql = f" WHERE {where_clause}" if where_clause else ""
    total = conn.execute(f'SELECT COUNT(*) FROM "{table}"{where_sql}').fetchone()[0]
    if not total:
        return {"total": 0, "nonNull": 0, "rate": 0.0, "where": where_clause or ""}
    non_null_where = f'{where_clause} AND ' if where_clause else ""
    non_null = conn.execute(
        f'SELECT COUNT(*) FROM "{table}" WHERE {non_null_where}"{field}" IS NOT NULL AND TRIM(CAST("{field}" AS TEXT)) <> ""'
    ).fetchone()[0]
    return {"total": total, "nonNull": non_null, "rate": round(non_null / total, 6), "where": where_clause or ""}


def fields_from_page_pack(pack: dict[str, Any], rule: dict[str, Any]) -> list[str]:
    field_array = rule.get("fieldArray")
    object_path = rule.get("objectPath")
    target: Any = pack
    if object_path:
        for part in str(object_path).split("."):
            if isinstance(target, dict):
                target = target.get(part)
            else:
                target = None
                break
    if isinstance(target, dict):
        array_name = field_array or "fields"
        fields = target.get(array_name)
        return [str(field) for field in fields] if isinstance(fields, list) else []
    if isinstance(target, list):
        fields: set[str] = set()
        for row in target[:200]:
            if isinstance(row, dict):
                fields.update(str(key) for key in row.keys())
        return sorted(fields)
    if not object_path and isinstance(pack.get(field_array or ""), list):
        return [str(field) for field in pack.get(field_array or "")]
    return []


def audit_field_dictionary(issues: list[dict[str, Any]], site_dir: Path, rule: dict[str, Any]) -> None:
    file_name = rule.get("file") or "field_dictionary_pack.json"
    path = site_dir / "data" / str(file_name)
    if not path.exists():
        add_issue(issues, "error", "field_dictionary", "字段字典缺失", str(path), rule_id="FIELD_DICTIONARY_PACK_MISSING")
        return
    try:
        pack = load_json_object(path)
    except Exception as exc:
        add_issue(issues, "error", "field_dictionary", "字段字典缺失", f"cannot parse {path}: {exc}", rule_id="FIELD_DICTIONARY_PACK_MISSING")
        return
    entities = pack.get("实体") or []
    min_entity_count = int(rule.get("minEntityCount") or 0)
    if min_entity_count and len(entities) < min_entity_count:
        add_issue(
            issues,
            "error",
            "field_dictionary",
            "字段字典缺失",
            f"实体数量 {len(entities)} 低于规则要求 {min_entity_count}",
            rule_id="FIELD_DICTIONARY_PACK_MISSING",
        )
    entity_by_name = {str(entity.get("实体") or ""): entity for entity in entities if isinstance(entity, dict)}
    missing_entities = [name for name in rule.get("requiredEntities") or [] if name not in entity_by_name]
    if missing_entities:
        add_issue(
            issues,
            "error",
            "field_dictionary",
            "字段字典缺失",
            "缺少必需实体字段字典。",
            missing_entities,
            rule_id="FIELD_DICTIONARY_PACK_MISSING",
        )
    required_field_keys = [str(key) for key in rule.get("requiredFieldKeys") or []]
    duplicate_samples = []
    unavailable_samples = []
    incomplete_samples = []
    for entity_name, entity in entity_by_name.items():
        field_rows = entity.get("字段") or []
        names = [str(row.get("字段名") or "") for row in field_rows if isinstance(row, dict)]
        duplicates = duplicate_names([name for name in names if name])
        if duplicates:
            duplicate_samples.append({"实体": entity_name, "重复字段": duplicates[:20]})
        for row in field_rows:
            if not isinstance(row, dict):
                continue
            missing_keys = [key for key in required_field_keys if key not in row]
            if missing_keys:
                incomplete_samples.append({"实体": entity_name, "字段名": row.get("字段名"), "缺失键": missing_keys})
            if row.get("来源可用") is False:
                unavailable_samples.append(
                    {
                        "实体": entity_name,
                        "字段名": row.get("字段名"),
                        "来源": row.get("来源"),
                    }
                )
            if len(unavailable_samples) >= 30 and len(incomplete_samples) >= 30:
                break
    if duplicate_samples:
        add_issue(
            issues,
            "error",
            "field_dictionary",
            "字段字典字段重复",
            "同一实体字段字典中字段名必须唯一。",
            duplicate_samples[:20],
            rule_id="FIELD_DICTIONARY_DUPLICATE_FIELD",
        )
    if incomplete_samples:
        add_issue(
            issues,
            "warn",
            "field_dictionary",
            "字段字典来源不可用",
            "字段字典字段缺少规则要求的说明键。",
            incomplete_samples[:30],
            rule_id="FIELD_DICTIONARY_SOURCE_UNAVAILABLE",
        )
    if unavailable_samples:
        add_issue(
            issues,
            "warn",
            "field_dictionary",
            "字段字典来源不可用",
            "字段字典中存在不可用来源。",
            unavailable_samples[:30],
            rule_id="FIELD_DICTIONARY_SOURCE_UNAVAILABLE",
        )


def audit_gfsec_legacy_page_scope(issues: list[dict[str, Any]], site_dir: Path) -> None:
    summary_path = site_dir / "data" / "basic_summary_core.js"
    if not summary_path.exists():
        return
    try:
        summary = read_js_object(summary_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return
    rows = [
        row
        for row in summary.get("strategies") or []
        if str(row.get("统一策略ID") or "").startswith("gfsec_robot__")
    ]
    invalid = [
        {
            "统一策略ID": row.get("统一策略ID"),
            "策略名称": row.get("策略名称"),
            "天天当前对客展示": row.get("天天当前对客展示"),
            "天天展示状态": row.get("天天展示状态"),
            "是否历史接口留档": row.get("是否历史接口留档"),
            "是否纳入常规排名": row.get("是否纳入常规排名"),
        }
        for row in rows
        if str(row.get("天天当前对客展示") or "") != "否"
        or int(row.get("是否历史接口留档") or 0) != 1
        or int(row.get("是否纳入常规排名") or 0) != 0
        or "历史接口留档" not in str(row.get("天天展示状态") or "")
    ]
    if invalid:
        add_issue(
            issues,
            "error",
            "page.gfsec_robot.lifecycle_scope",
            "贝塔牛历史接口被误当成当前产品",
            (
                f"页面共导出 {len(rows)} 条贝塔牛历史策略，其中 {len(invalid)} 条未明确标记为历史接口留档、"
                "非对客或仍可能进入常规排名。"
            ),
            invalid[:30],
            rule_id="GFSEC_LEGACY_PAGE_SCOPE_INVALID",
        )


def audit_page_strategy_business_scope(
    issues: list[dict[str, Any]],
    db_path: Path,
    site_dir: Path,
    rules_path: Path,
) -> None:
    summary_path = site_dir / "data" / "basic_summary_core.js"
    if not summary_path.is_file() or not rules_path.is_file():
        return
    try:
        summary = read_js_object(summary_path)
        rules = load_json_object(rules_path)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return
    scope = rules.get("pageStrategyScope") if isinstance(rules.get("pageStrategyScope"), dict) else {}
    active = {str(value).strip() for value in scope.get("activeChannelIds") or [] if str(value).strip()}
    disabled = {str(value).strip() for value in scope.get("disabledChannelIds") or [] if str(value).strip()}
    channel_names = {
        str(source_id).strip(): str(name).strip()
        for source_id, name in (scope.get("canonicalChannelBySourceId") or {}).items()
        if str(source_id).strip() and str(name).strip()
    }
    institution_aliases = {
        str(alias).strip(): str(name).strip()
        for alias, name in (scope.get("canonicalInstitutionAliases") or {}).items()
        if str(alias).strip() and str(name).strip()
    }
    disabled_rows: list[dict[str, Any]] = []
    naming_rows: list[dict[str, Any]] = []
    page_counts: Counter[str] = Counter()
    for row in summary.get("strategies") or []:
        if not isinstance(row, dict):
            continue
        strategy_id = str(row.get("统一策略ID") or "").strip()
        source_id = strategy_id.split("__", 1)[0]
        if source_id:
            page_counts[source_id] += 1
        if source_id in disabled:
            disabled_rows.append(
                {
                    "统一策略ID": strategy_id,
                    "策略名称": row.get("策略名称"),
                    "渠道": row.get("渠道"),
                }
            )
        expected_channel = channel_names.get(source_id)
        actual_channel = str(row.get("渠道") or "").strip()
        institution = str(row.get("投顾机构") or "").strip()
        expected_institution = institution_aliases.get(institution)
        reasons: list[str] = []
        if expected_channel and actual_channel != expected_channel:
            reasons.append(f"渠道应为{expected_channel}")
        if expected_institution and institution != expected_institution:
            reasons.append(f"投顾机构应为{expected_institution}")
        if reasons:
            naming_rows.append(
                {
                    "统一策略ID": strategy_id,
                    "策略名称": row.get("策略名称"),
                    "渠道": actual_channel,
                    "投顾机构": institution,
                    "问题": reasons,
                }
            )
    if disabled_rows:
        add_issue(
            issues,
            "error",
            "page.strategy_scope.disabled_channels",
            "暂停渠道仍出现在当前策略页面",
            f"发现 {len(disabled_rows)} 条暂停渠道策略仍被导出到当前页面。",
            disabled_rows[:30],
            rule_id="PAGE_DISABLED_CHANNEL_EXPOSED",
        )
    database_counts: dict[str, int] = {}
    if active and db_path.is_file():
        placeholders = ",".join("?" for _ in active)
        with sqlite3.connect(db_path) as conn:
            database_counts = {
                str(row[0]): int(row[1] or 0)
                for row in conn.execute(
                    f'''SELECT "渠道ID", COUNT(*) FROM "策略信息"
                        WHERE "渠道ID" IN ({placeholders}) GROUP BY "渠道ID"''',
                    tuple(sorted(active)),
                )
            }
    inventory_mismatches = [
        {
            "渠道ID": channel_id,
            "主库策略数": database_counts.get(channel_id, 0),
            "页面策略数": page_counts.get(channel_id, 0),
        }
        for channel_id in sorted(active)
        if database_counts.get(channel_id, 0) > 0
        and page_counts.get(channel_id, 0) != database_counts.get(channel_id, 0)
    ]
    if inventory_mismatches:
        add_issue(
            issues,
            "error",
            "page.strategy_scope.active_channel_inventory",
            "活动渠道主库库存未完整进入策略页面",
            "活动渠道只要主库存在策略，就必须按统一策略 ID 完整进入页面源包；不能因导出脚本保留旧渠道白名单而静默漏掉新渠道。",
            inventory_mismatches,
            rule_id="PAGE_ACTIVE_CHANNEL_INVENTORY_MISSING",
        )
    if naming_rows:
        add_issue(
            issues,
            "error",
            "page.strategy_scope.business_naming",
            "业务渠道或机构名称未归一",
            f"发现 {len(naming_rows)} 条策略仍使用来源货架名或旧机构别名。",
            naming_rows[:30],
            rule_id="PAGE_BUSINESS_NAMING_NOT_CANONICAL",
        )


def sql_identifier(value: str) -> str:
    return '"' + str(value).replace('"', '""') + '"'


def audit_channel_strategy_coverage_rules(
    issues: list[dict[str, Any]],
    conn: sqlite3.Connection,
    tables: set[str],
    rules: dict[str, Any],
) -> None:
    for rule in rules.get("channelStrategyCoverage") or []:
        if rule.get("enabled") is False:
            continue
        channel_id = str(rule.get("channelId") or "").strip()
        denominator_table = str(rule.get("denominatorTable") or "策略信息").strip()
        id_field = str(rule.get("idField") or "统一策略ID").strip()
        channel_field = str(rule.get("channelField") or "渠道ID").strip()
        if not channel_id or denominator_table not in tables:
            continue
        denominator_columns = {
            str(row[1])
            for row in conn.execute(
                f"PRAGMA table_info({sql_identifier(denominator_table)})"
            )
        }
        if id_field not in denominator_columns or channel_field not in denominator_columns:
            continue
        rule_conditions = rule.get("denominatorConditions") or {}
        if not isinstance(rule_conditions, dict) or any(
            str(field) not in denominator_columns for field in rule_conditions
        ):
            continue
        rule_condition_sql = "".join(
            f" AND d.{sql_identifier(str(field))}=?"
            for field in rule_conditions
        )
        rule_condition_values = list(rule_conditions.values())
        channel_total = int(
            conn.execute(
                f"SELECT COUNT(DISTINCT {sql_identifier(id_field)}) "
                f"FROM {sql_identifier(denominator_table)} "
                f"WHERE {sql_identifier(channel_field)}=?",
                (channel_id,),
            ).fetchone()[0]
            or 0
        )
        if channel_total <= 0 and rule.get("skipWhenChannelAbsent") is True:
            continue
        failures: list[dict[str, Any]] = []
        for metric in rule.get("metrics") or []:
            table = str(metric.get("table") or "").strip()
            label = str(metric.get("name") or table).strip()
            if not table or table not in tables:
                continue
            fact_columns = {
                str(row[1])
                for row in conn.execute(f"PRAGMA table_info({sql_identifier(table)})")
            }
            if id_field not in fact_columns:
                continue
            try:
                minimum_rate = float(metric.get("minimumRate"))
            except (TypeError, ValueError):
                continue

            denominator_join = ""
            denominator_conditions = rule_condition_sql
            denominator_parameters: list[Any] = [channel_id, *rule_condition_values]
            eligibility = metric.get("denominatorEligibility")
            if isinstance(eligibility, dict) and eligibility:
                eligibility_table = str(eligibility.get("table") or "").strip()
                conditions = eligibility.get("conditions")
                if (
                    not eligibility_table
                    or eligibility_table not in tables
                    or not isinstance(conditions, dict)
                    or not conditions
                ):
                    failures.append(
                        {
                            "指标": label,
                            "事实表": table,
                            "配置错误": "分母资格表或资格条件缺失",
                            "分母资格配置": eligibility,
                        }
                    )
                    continue
                eligibility_columns = {
                    str(row[1])
                    for row in conn.execute(
                        f"PRAGMA table_info({sql_identifier(eligibility_table)})"
                    )
                }
                missing_columns = sorted(
                    {id_field, *map(str, conditions)} - eligibility_columns
                )
                if missing_columns:
                    failures.append(
                        {
                            "指标": label,
                            "事实表": table,
                            "配置错误": "分母资格字段缺失",
                            "分母资格表": eligibility_table,
                            "缺失字段": missing_columns,
                        }
                    )
                    continue
                denominator_join = (
                    f" INNER JOIN {sql_identifier(eligibility_table)} e "
                    f"ON e.{sql_identifier(id_field)}=d.{sql_identifier(id_field)}"
                )
                for field, value in conditions.items():
                    denominator_conditions += f" AND e.{sql_identifier(str(field))}=?"
                    denominator_parameters.append(value)

            denominator = int(
                conn.execute(
                    f"SELECT COUNT(DISTINCT d.{sql_identifier(id_field)}) "
                    f"FROM {sql_identifier(denominator_table)} d"
                    f"{denominator_join} "
                    f"WHERE d.{sql_identifier(channel_field)}=?"
                    f"{denominator_conditions}",
                    denominator_parameters,
                ).fetchone()[0]
                or 0
            )
            covered = int(
                conn.execute(
                    f"SELECT COUNT(DISTINCT f.{sql_identifier(id_field)}) "
                    f"FROM {sql_identifier(table)} f "
                    f"INNER JOIN {sql_identifier(denominator_table)} d "
                    f"ON d.{sql_identifier(id_field)}=f.{sql_identifier(id_field)} "
                    f"{denominator_join} "
                    f"WHERE d.{sql_identifier(channel_field)}=?"
                    f"{denominator_conditions}",
                    denominator_parameters,
                ).fetchone()[0]
                or 0
            )
            rate = covered / denominator if denominator > 0 else 0.0
            if denominator <= 0 or rate + 1e-9 < minimum_rate:
                missing_rows = conn.execute(
                    f"SELECT DISTINCT d.{sql_identifier(id_field)} "
                    f"FROM {sql_identifier(denominator_table)} d "
                    f"{denominator_join} "
                    f"WHERE d.{sql_identifier(channel_field)}=? "
                    f"{denominator_conditions} "
                    f"AND NOT EXISTS ("
                    f"SELECT 1 FROM {sql_identifier(table)} f "
                    f"WHERE f.{sql_identifier(id_field)}=d.{sql_identifier(id_field)}"
                    f") ORDER BY d.{sql_identifier(id_field)} LIMIT 30",
                    denominator_parameters,
                ).fetchall()
                failures.append(
                    {
                        "指标": label,
                        "事实表": table,
                        "已覆盖策略": covered,
                        "渠道策略总数": channel_total,
                        "应覆盖策略": denominator,
                        "分母资格说明": (
                            eligibility.get("说明")
                            if isinstance(eligibility, dict)
                            else "渠道全部策略"
                        ),
                        "覆盖率": round(rate, 6),
                        "最低要求": minimum_rate,
                        "缺失策略样本": [str(row[0]) for row in missing_rows],
                    }
                )
        if failures:
            add_issue(
                issues,
                str(rule.get("severity") or "error"),
                f"sqlite.channel_strategy_coverage.{channel_id}",
                "渠道策略级核心数据覆盖不足",
                "渠道核心事实不能只判断全表非空，必须达到规则要求的策略级覆盖率。",
                failures,
                rule_id=str(rule.get("ruleId") or "GFFUNDS_CORE_STRATEGY_COVERAGE_BELOW_THRESHOLD"),
            )


def audit_channel_performance_freshness_rules(
    issues: list[dict[str, Any]],
    conn: sqlite3.Connection,
    tables: set[str],
    rules: dict[str, Any],
) -> None:
    """Check both absolute channel freshness and strategy coverage at that watermark."""

    active_channel_ids = [
        str(value).strip()
        for value in (rules.get("pageStrategyScope") or {}).get("activeChannelIds") or []
        if str(value).strip()
    ]
    for rule in rules.get("channelPerformanceFreshness") or []:
        if rule.get("enabled") is False:
            continue
        channel_id = str(rule.get("channelId") or "").strip()
        table = str(rule.get("table") or "策略日度业绩").strip()
        channel_field = str(rule.get("channelField") or "渠道ID").strip()
        strategy_field = str(rule.get("strategyIdField") or "渠道策略ID").strip()
        date_field = str(rule.get("dateField") or "交易日期").strip()
        if not channel_id or table not in tables:
            continue
        columns = {
            str(row[1])
            for row in conn.execute(f"PRAGMA table_info({sql_identifier(table)})")
        }
        if {channel_field, strategy_field, date_field} - columns:
            continue
        channel_latest = str(
            conn.execute(
                f"SELECT COALESCE(MAX({sql_identifier(date_field)}), '') "
                f"FROM {sql_identifier(table)} WHERE {sql_identifier(channel_field)}=?",
                (channel_id,),
            ).fetchone()[0]
            or ""
        ).strip()
        denominator = int(
            conn.execute(
                f"SELECT COUNT(DISTINCT {sql_identifier(strategy_field)}) "
                f"FROM {sql_identifier(table)} WHERE {sql_identifier(channel_field)}=?",
                (channel_id,),
            ).fetchone()[0]
            or 0
        )
        if denominator <= 0 and rule.get("skipWhenChannelAbsent") is True:
            continue
        latest_total = (
            int(
                conn.execute(
                    f"SELECT COUNT(DISTINCT {sql_identifier(strategy_field)}) "
                    f"FROM {sql_identifier(table)} "
                    f"WHERE {sql_identifier(channel_field)}=? AND {sql_identifier(date_field)}=?",
                    (channel_id, channel_latest),
                ).fetchone()[0]
                or 0
            )
            if channel_latest
            else 0
        )
        latest_rate = latest_total / denominator if denominator else 0.0

        system_latest = channel_latest
        if active_channel_ids:
            placeholders = ",".join("?" for _ in active_channel_ids)
            system_latest = str(
                conn.execute(
                    f"SELECT COALESCE(MAX({sql_identifier(date_field)}), '') "
                    f"FROM {sql_identifier(table)} "
                    f"WHERE {sql_identifier(channel_field)} IN ({placeholders})",
                    tuple(active_channel_ids),
                ).fetchone()[0]
                or ""
            ).strip()
        lag = business_day_lag(channel_latest, system_latest)
        try:
            minimum_rate = float(rule.get("minimumLatestDateRate") or 0.99)
        except (TypeError, ValueError):
            minimum_rate = 0.99
        try:
            maximum_lag = int(rule.get("maximumBusinessDayLagFromSystemLatest") or 1)
        except (TypeError, ValueError):
            maximum_lag = 1
        failed = bool(
            not channel_latest
            or lag is None
            or lag > maximum_lag
            or latest_rate + 1e-9 < minimum_rate
        )
        if not failed:
            continue
        add_issue(
            issues,
            str(rule.get("severity") or "error"),
            f"sqlite.{table}.{channel_id}.freshness",
            "渠道业绩最新日期或最新日策略覆盖不足",
            "渠道业绩必须跟上系统最新交易日允许的工作日差，并保证绝大多数已有业绩策略到达渠道最新日。",
            [
                {
                    "渠道ID": channel_id,
                    "渠道最新业绩日期": channel_latest or None,
                    "系统最新业绩日期": system_latest or None,
                    "相差工作日": lag,
                    "允许最大工作日差": maximum_lag,
                    "有业绩策略数": denominator,
                    "到达渠道最新日策略数": latest_total,
                    "最新日覆盖率": round(latest_rate, 6),
                    "最低最新日覆盖率": minimum_rate,
                }
            ],
            rule_id=str(rule.get("ruleId") or "CHANNEL_PERFORMANCE_FRESHNESS_LOW"),
        )


def audit_channel_curve_coverage_rules(
    issues: list[dict[str, Any]],
    conn: sqlite3.Connection,
    tables: set[str],
    rules: dict[str, Any],
) -> None:
    """Check that a claimed strategy curve has multiple exact disclosed dates."""
    for rule in rules.get("channelCurveCoverage") or []:
        if rule.get("enabled") is False:
            continue
        channel_id = str(rule.get("channelId") or "").strip()
        denominator_table = str(rule.get("denominatorTable") or "策略信息").strip()
        fact_table = str(rule.get("table") or "策略日度业绩").strip()
        id_field = str(rule.get("idField") or "统一策略ID").strip()
        channel_field = str(rule.get("channelField") or "渠道ID").strip()
        date_field = str(rule.get("dateField") or "交易日期").strip()
        section_field = str(rule.get("sectionField") or "业绩区段类型").strip()
        section_type = str(rule.get("sectionType") or "").strip()
        required_fact_fields = [
            str(value).strip()
            for value in (rule.get("requiredFactFields") or [])
            if str(value).strip()
        ]
        if not channel_id or not section_type or not {denominator_table, fact_table}.issubset(tables):
            continue
        denominator_columns = {
            str(row[1])
            for row in conn.execute(f"PRAGMA table_info({sql_identifier(denominator_table)})")
        }
        fact_columns = {
            str(row[1])
            for row in conn.execute(f"PRAGMA table_info({sql_identifier(fact_table)})")
        }
        if not {id_field, channel_field}.issubset(denominator_columns) or not {
            id_field,
            date_field,
            section_field,
        }.issubset(fact_columns):
            continue
        if not set(required_fact_fields).issubset(fact_columns):
            continue
        denominator_conditions = rule.get("denominatorConditions") or {}
        if not isinstance(denominator_conditions, dict) or any(
            str(field) not in denominator_columns for field in denominator_conditions
        ):
            continue
        denominator_condition_sql = "".join(
            f" AND {sql_identifier(str(field))}=?"
            for field in denominator_conditions
        )
        denominator_condition_values = list(denominator_conditions.values())
        joined_denominator_condition_sql = "".join(
            f" AND d.{sql_identifier(str(field))}=?"
            for field in denominator_conditions
        )
        required_nonnull_sql = "".join(
            f" AND f.{sql_identifier(field)} IS NOT NULL"
            for field in required_fact_fields
        )
        try:
            minimum_rate = float(rule.get("minimumRate"))
            minimum_dates = max(2, int(rule.get("minimumDistinctDates") or 2))
        except (TypeError, ValueError):
            continue
        denominator = int(
            conn.execute(
                f"SELECT COUNT(DISTINCT {sql_identifier(id_field)}) "
                f"FROM {sql_identifier(denominator_table)} "
                f"WHERE {sql_identifier(channel_field)}=?"
                f"{denominator_condition_sql}",
                (channel_id, *denominator_condition_values),
            ).fetchone()[0]
            or 0
        )
        covered_rows = conn.execute(
            f"SELECT f.{sql_identifier(id_field)}, COUNT(DISTINCT f.{sql_identifier(date_field)}) AS point_count "
            f"FROM {sql_identifier(fact_table)} f "
            f"INNER JOIN {sql_identifier(denominator_table)} d "
            f"ON d.{sql_identifier(id_field)}=f.{sql_identifier(id_field)} "
            f"WHERE d.{sql_identifier(channel_field)}=? "
            f"AND f.{sql_identifier(section_field)}=? "
            f"{joined_denominator_condition_sql} "
            f"{required_nonnull_sql} "
            f"GROUP BY f.{sql_identifier(id_field)} "
            f"HAVING COUNT(DISTINCT f.{sql_identifier(date_field)})>=?",
            (channel_id, section_type, *denominator_condition_values, minimum_dates),
        ).fetchall()
        covered = len(covered_rows)
        rate = covered / denominator if denominator > 0 else 0.0
        if denominator <= 0 or rate + 1e-9 >= minimum_rate:
            continue
        point_counts = {
            str(row[0]): int(row[1] or 0)
            for row in conn.execute(
                f"SELECT d.{sql_identifier(id_field)}, COUNT(DISTINCT f.{sql_identifier(date_field)}) "
                f"FROM {sql_identifier(denominator_table)} d "
                f"LEFT JOIN {sql_identifier(fact_table)} f "
                f"ON f.{sql_identifier(id_field)}=d.{sql_identifier(id_field)} "
                f"AND f.{sql_identifier(section_field)}=? "
                f"{required_nonnull_sql} "
                f"WHERE d.{sql_identifier(channel_field)}=? "
                f"GROUP BY d.{sql_identifier(id_field)} "
                f"HAVING COUNT(DISTINCT f.{sql_identifier(date_field)})<? "
                f"ORDER BY d.{sql_identifier(id_field)} LIMIT 30",
                (section_type, channel_id, minimum_dates),
            )
        }
        add_issue(
            issues,
            str(rule.get("severity") or "warn"),
            f"sqlite.channel_curve_coverage.{channel_id}",
            "渠道真实业绩曲线覆盖不足",
            f"{channel_id} 仅 {covered}/{denominator} 个策略具备至少 {minimum_dates} 个不同日期的官方曲线点。",
            [
                {
                    "已覆盖策略": covered,
                    "渠道策略总数": denominator,
                    "覆盖率": round(rate, 6),
                    "最低要求": minimum_rate,
                    "最低不同日期数": minimum_dates,
                    "同日必填事实字段": required_fact_fields,
                    "不足策略及点数": point_counts,
                }
            ],
            rule_id=str(rule.get("ruleId") or "CHANNEL_CURVE_COVERAGE_LOW"),
        )


def audit_system_field_rules(issues: list[dict[str, Any]], db_path: Path, site_dir: Path, rules_path: Path) -> None:
    if not rules_path.exists():
        add_issue(issues, "error", "system_field_rules", "字段规则缺失", str(rules_path), rule_id="FIELD_RULE_CONFIG_MISSING")
        return
    try:
        rules = load_json_object(rules_path)
    except Exception as exc:
        add_issue(issues, "error", "system_field_rules", "字段规则缺失", f"cannot parse {rules_path}: {exc}", rule_id="FIELD_RULE_CONFIG_MISSING")
        return

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table_rule in rules.get("sqliteTables") or []:
            table = str(table_rule.get("name") or "")
            if not table:
                continue
            if table not in tables:
                add_issue(issues, "error", f"sqlite.{table}", "核心表缺失", table, rule_id="SQLITE_REQUIRED_TABLE_MISSING")
                continue
            columns = [row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')]
            missing_fields = [field for field in table_rule.get("requiredFields") or [] if field not in columns]
            if missing_fields:
                add_issue(
                    issues,
                    "error",
                    f"sqlite.{table}",
                    "核心字段缺失",
                    "字段规则要求的核心字段不存在。",
                    missing_fields,
                    rule_id="SQLITE_REQUIRED_FIELD_MISSING",
                )
            coverage_samples = []
            min_non_null_filters = table_rule.get("minNonNullRateFilters") or {}
            for field, threshold in (table_rule.get("minNonNullRate") or {}).items():
                if field not in columns:
                    continue
                where_clause = min_non_null_filters.get(field)
                stats = non_null_rate(conn, table, field, str(where_clause) if where_clause else None)
                if stats.get("where") and int(stats.get("total") or 0) == 0:
                    continue
                try:
                    min_rate = float(threshold)
                except (TypeError, ValueError):
                    continue
                if stats["rate"] + 1e-9 < min_rate:
                    sample = {
                        "表": table,
                        "字段": field,
                        "非空率": stats["rate"],
                        "最低要求": min_rate,
                        "非空数": stats["nonNull"],
                        "总数": stats["total"],
                    }
                    if stats.get("where"):
                        sample["适用分母条件"] = stats["where"]
                    coverage_samples.append(sample)
            if coverage_samples:
                add_issue(
                    issues,
                    "warn",
                    f"sqlite.{table}",
                    "字段非空率不足",
                    "关键字段非空率低于系统字段检查规则要求。",
                    coverage_samples,
                    rule_id="SQLITE_FIELD_NON_NULL_RATE_LOW",
                )
        audit_channel_strategy_coverage_rules(issues, conn, tables, rules)
        audit_channel_performance_freshness_rules(issues, conn, tables, rules)
        audit_channel_curve_coverage_rules(issues, conn, tables, rules)

    data_dir = site_dir / "data"
    for pack_rule in rules.get("pagePacks") or []:
        file_name = str(pack_rule.get("file") or "")
        if not file_name:
            continue
        path = data_dir / file_name
        if not path.exists():
            add_issue(issues, "error", f"page_pack.{file_name}", "核心页面包缺失", str(path), rule_id="PAGE_REQUIRED_PACK_MISSING")
            continue
        try:
            pack = read_js_object(path) if path.suffix.lower() == ".js" else load_json_object(path)
        except Exception as exc:
            add_issue(issues, "error", f"page_pack.{file_name}", "核心页面包缺失", f"cannot parse {path}: {exc}", rule_id="PAGE_REQUIRED_PACK_MISSING")
            continue
        fields = fields_from_page_pack(pack, pack_rule)
        missing = [field for field in pack_rule.get("requiredFields") or [] if field not in fields]
        if missing:
            add_issue(
                issues,
                "error",
                f"page_pack.{file_name}",
                "页面核心字段缺失",
                "页面包缺少字段规则要求的核心字段。",
                {"缺失字段": missing, "已识别字段数": len(fields), "objectPath": pack_rule.get("objectPath")},
                rule_id="PAGE_REQUIRED_FIELD_MISSING",
            )

    if isinstance(rules.get("fieldDictionary"), dict):
        audit_field_dictionary(issues, site_dir, rules["fieldDictionary"])


def write_reports(output_dir: Path, report: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "data_audit_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# 数据稽核报告",
        "",
        f"- 生成时间：{report['generatedAt']}",
        f"- 状态：{report['status']}",
        f"- error：{report['summary']['error']}",
        f"- warn：{report['summary']['warn']}",
        "",
        "## 问题列表",
        "",
    ]
    if not report["issues"]:
        lines.append("未发现 error/warn。")
    else:
        for issue in report["issues"][:200]:
            lines.append(f"- [{issue['severity']}] {issue.get('ruleId', 'UNCLASSIFIED_AUDIT_RULE')} / {issue['scope']} / {issue['item']}：{issue['detail']}")
            lines.append(f"  - 原因说明：{issue.get('原因说明', '待补充')}")
            lines.append(f"  - 优化建议：{issue.get('优化建议', '待补充')}")
            lines.append(f"  - 修复责任脚本：{issue.get('修复责任脚本', '待补充')}")
            lines.append(f"  - 修复责任节点：{issue.get('修复责任节点', 'data_audit')}")
            if issue.get("sample") is not None:
                sample = json.dumps(issue["sample"], ensure_ascii=False)
                lines.append(f"  - 样本：{sample[:1200]}")
    (output_dir / "data_audit_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def audit_incremental_gap_summary(issues: list[dict[str, Any]]) -> None:
    root = PROJECT_ROOT / "logs" / "incremental_update"
    summaries = sorted(
        root.rglob("collection_gap_summary.json") if root.exists() else [],
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    if not summaries:
        return
    path = summaries[0]
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        add_issue(
            issues,
            "error",
            "incremental_collection_gap",
            "\u589e\u91cf\u91c7\u96c6\u7f3a\u53e3\u6458\u8981\u65e0\u6cd5\u89e3\u6790",
            f"{path}: {type(exc).__name__}: {exc}",
            rule_id="INCREMENTAL_GAP_SUMMARY_INCONSISTENT",
        )
        return

    mismatches: list[dict[str, Any]] = []
    ttfund = payload.get("ttfund") or {}
    selected = ttfund.get("selected_current_holding_total")
    success = ttfund.get("raw_current_holding_success_total")
    failed = ttfund.get("raw_current_holding_failed_total")
    if not all(isinstance(value, int) for value in (selected, success, failed)) or selected != success + failed:
        mismatches.append(
            {"scope": "ttfund_current_holding", "selected": selected, "success": success, "failed": failed}
        )
    official_summary_text = str(ttfund.get("official_curve_summary_path") or "").strip()
    official_summary_path = Path(official_summary_text) if official_summary_text else None
    if official_summary_path is None or not official_summary_path.is_file():
        mismatches.append(
            {"scope": "ttfund_official_curve", "summary_path": official_summary_text or None}
        )
    elif official_summary_path.is_file():
        try:
            official_payload = json.loads(official_summary_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            official_payload = None
        if isinstance(official_payload, dict) and "source_effective_date" in official_payload:
            disclosure_mismatches: list[dict[str, Any]] = []
            source_effective_date = official_payload.get("source_effective_date")
            source_lag = official_payload.get("source_lag_business_days")
            max_lag = official_payload.get("max_source_lag_business_days")
            state = str(official_payload.get("state") or "")
            if not source_effective_date:
                disclosure_mismatches.append({"scope": "source_effective_date_missing", "state": state})
            if isinstance(source_lag, int) and isinstance(max_lag, int) and source_lag > max_lag:
                disclosure_mismatches.append(
                    {"scope": "source_lag_exceeded", "source_lag": source_lag, "max_source_lag": max_lag}
                )
            if state.startswith("ready") and official_payload.get("failure_class"):
                disclosure_mismatches.append(
                    {
                        "scope": "ready_state_with_failure_class",
                        "state": state,
                        "failure_class": official_payload.get("failure_class"),
                    }
                )
            if disclosure_mismatches:
                add_issue(
                    issues,
                    "error",
                    "incremental_collection_gap.ttfund_official_curve",
                    "天天官方曲线披露日口径异常",
                    "官方曲线必须按活跃策略覆盖率确定自身有效披露日，并在允许的源延迟范围内保留行情推算数据。",
                    {"summary_path": str(official_summary_path), "mismatches": disclosure_mismatches},
                    rule_id="TTFUND_OFFICIAL_CURVE_DISCLOSURE_DATE_INVALID",
                )

    lifecycle_mismatches: list[dict[str, Any]] = []
    planned_strategy_total = ttfund.get("planned_strategy_total")
    definitively_stopped = ttfund.get("definitively_stopped_skipped_total")
    stopped_but_refreshable = ttfund.get("stopped_but_refreshable_total")
    lifecycle_reason_counts = ttfund.get("current_holding_lifecycle_reason_counts")
    lifecycle_values = (
        planned_strategy_total,
        definitively_stopped,
        stopped_but_refreshable,
        lifecycle_reason_counts,
    )
    if any(value is not None for value in lifecycle_values):
        reason_counts_valid = isinstance(lifecycle_reason_counts, dict) and all(
            isinstance(value, int) and value >= 0 for value in lifecycle_reason_counts.values()
        )
        counters_valid = all(
            isinstance(value, int) and value >= 0
            for value in (planned_strategy_total, definitively_stopped, stopped_but_refreshable)
        )
        if not reason_counts_valid or not counters_valid:
            lifecycle_mismatches.append(
                {
                    "scope": "lifecycle_counter_types",
                    "planned_strategy_total": planned_strategy_total,
                    "definitively_stopped": definitively_stopped,
                    "stopped_but_refreshable": stopped_but_refreshable,
                    "reason_counts": lifecycle_reason_counts,
                }
            )
        else:
            reason_total = sum(lifecycle_reason_counts.values())
            active_total = int(lifecycle_reason_counts.get("active_status", 0))
            if reason_total != planned_strategy_total:
                lifecycle_mismatches.append(
                    {
                        "scope": "lifecycle_reason_total",
                        "planned_strategy_total": planned_strategy_total,
                        "reason_total": reason_total,
                    }
                )
            if definitively_stopped + stopped_but_refreshable != planned_strategy_total - active_total:
                lifecycle_mismatches.append(
                    {
                        "scope": "stopped_strategy_partition",
                        "planned_strategy_total": planned_strategy_total,
                        "active_status_total": active_total,
                        "definitively_stopped": definitively_stopped,
                        "stopped_but_refreshable": stopped_but_refreshable,
                    }
                )
    if lifecycle_mismatches:
        add_issue(
            issues,
            "error",
            "incremental_collection_gap.ttfund_lifecycle",
            "天天当前持仓生命周期分类计数不一致",
            "停止售卖策略必须拆分为明确结束和仍需刷新两组，且与全量策略生命周期分类计数守恒。",
            {"summary_path": str(path), "mismatches": lifecycle_mismatches},
            rule_id="TTFUND_CURRENT_HOLDING_LIFECYCLE_SELECTION_INCONSISTENT",
        )

    gffunds = payload.get("gffunds") or {}
    metadata_selected = gffunds.get("metadata_selected_total")
    metadata_success = gffunds.get("metadata_success_total")
    metadata_failed = gffunds.get("metadata_failure_total")
    if not all(isinstance(value, int) for value in (metadata_selected, metadata_success, metadata_failed)) or metadata_selected != metadata_success + metadata_failed:
        mismatches.append(
            {
                "scope": "gffunds_metadata",
                "selected": metadata_selected,
                "success": metadata_success,
                "failed": metadata_failed,
            }
        )

    fund_nav = payload.get("fund_nav") or {}
    target = fund_nav.get("target_fund_total")
    nav_success = fund_nav.get("successful_fund_total")
    empty = fund_nav.get("empty_response_total")
    nav_failed = fund_nav.get("failed_fund_total")
    if not all(isinstance(value, int) for value in (target, nav_success, empty, nav_failed)) or target != nav_success + empty + nav_failed:
        mismatches.append(
            {
                "scope": "holding_fund_nav",
                "target": target,
                "success": nav_success,
                "empty": empty,
                "failed": nav_failed,
            }
        )
    critical_nav_failed = fund_nav.get("critical_current_holding_failed_total")
    nav_gate_status = str(fund_nav.get("gate_status") or "").strip().lower()
    if isinstance(critical_nav_failed, int) and critical_nav_failed > 0:
        add_issue(
            issues,
            "error",
            "incremental_collection_gap.fund_nav",
            "当前持仓基金净值重试后仍失败",
            "公开净值接口经高并发首轮、低并发重试和单线程延时重试后，仍有当前持仓基金未取得增量净值。",
            {
                "summary_path": str(path),
                "failed_total": critical_nav_failed,
                "failed_codes": fund_nav.get("critical_current_holding_failed_codes") or [],
                "gate_status": nav_gate_status,
            },
            rule_id="CURRENT_HOLDING_FUND_NAV_RETRY_EXHAUSTED",
        )
    critical_nav_empty = fund_nav.get("critical_current_holding_empty_total")
    if isinstance(critical_nav_empty, int) and critical_nav_empty > 0:
        add_issue(
            issues,
            "warn",
            "incremental_collection_gap.fund_nav",
            "当前持仓基金净值接口持续空返回",
            "公开净值接口多轮请求均正常返回但没有净值记录，需区分尚未成立、份额失效或源端暂未披露。",
            {
                "summary_path": str(path),
                "empty_total": critical_nav_empty,
                "empty_codes": fund_nav.get("critical_current_holding_empty_codes") or [],
            },
            rule_id="CURRENT_HOLDING_FUND_NAV_SOURCE_EMPTY",
        )
    if mismatches:
        add_issue(
            issues,
            "error",
            "incremental_collection_gap",
            "\u589e\u91cf\u91c7\u96c6\u7f3a\u53e3\u6458\u8981\u8ba1\u6570\u4e0d\u4e00\u81f4",
            "\u589e\u91cf\u7f3a\u53e3\u6458\u8981\u4e0e\u539f\u59cb\u91c7\u96c6\u6210\u529f\u3001\u7a7a\u8fd4\u56de\u6216\u5931\u8d25\u8ba1\u6570\u4e0d\u5b88\u6052\u3002",
            {"summary_path": str(path), "mismatches": mismatches},
            rule_id="INCREMENTAL_GAP_SUMMARY_INCONSISTENT",
        )

    provenance_mismatches: list[dict[str, Any]] = []
    allowed_states = {
        "ttfund": {"current_run", "unavailable"},
        "gffunds": {"current_run", "historical", "unavailable"},
        "fund_nav": {"current_run", "historical", "unavailable"},
    }
    legacy_summary = (
        "metadata_current_run" not in gffunds
        and "collection_current_run" not in gffunds
        and "summary_current_run" not in fund_nav
    )
    for scope, item in (("ttfund", ttfund), ("gffunds", gffunds), ("fund_nav", fund_nav)):
        state = item.get("state")
        if legacy_summary and state == "available":
            continue
        if state is not None and state not in allowed_states[scope]:
            provenance_mismatches.append({"scope": scope, "state": state})
    if gffunds.get("state") == "historical" and (
        gffunds.get("metadata_current_run") or gffunds.get("collection_current_run")
    ):
        provenance_mismatches.append({"scope": "gffunds", "state": "historical", "current_run": True})
    if fund_nav.get("state") == "historical" and fund_nav.get("summary_current_run"):
        provenance_mismatches.append({"scope": "fund_nav", "state": "historical", "current_run": True})
    if provenance_mismatches:
        add_issue(
            issues,
            "error",
            "incremental_collection_gap.run_provenance",
            "增量摘要本次运行来源状态不一致",
            "历史数据水位和本次实际执行结果必须明确区分，未运行的子链路不能显示为本次可用。",
            {"summary_path": str(path), "mismatches": provenance_mismatches},
            rule_id="INCREMENTAL_SUMMARY_RUN_PROVENANCE_INVALID",
        )


def audit_ttfund_incremental_plan_cache_freshness(issues: list[dict[str, Any]]) -> None:
    root = PROJECT_ROOT / "data" / "raw" / "ttfund" / "incremental_update_runs"
    plans = sorted(
        root.glob("*/*/plan.json") if root.exists() else [],
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    if not plans:
        return
    path = plans[0]
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        add_issue(
            issues,
            "warn",
            "ttfund_incremental_plan.cache_freshness",
            "天天详情增量计划缓存来源无法核验",
            f"{path}: {type(exc).__name__}: {exc}",
            rule_id="TTFUND_DETAIL_CACHE_FRESHNESS_SOURCE_INVALID",
        )
        return

    cache_inventory = payload.get("cache_inventory") if isinstance(payload, dict) else None
    cache_inventory = cache_inventory if isinstance(cache_inventory, dict) else {}
    source = cache_inventory.get("detail_freshness_source")
    layout_counts_as_detail = cache_inventory.get("layout_cache_counts_as_detail")
    detail_mtime_total = cache_inventory.get("detail_mtime_strategy_total")
    detail_file_total = cache_inventory.get("detail_file_strategy_total")
    mismatches: list[dict[str, Any]] = []
    if source != "strategyDetailPageData":
        mismatches.append(
            {
                "scope": "detail_freshness_source",
                "expected": "strategyDetailPageData",
                "actual": source,
            }
        )
    if layout_counts_as_detail is not False:
        mismatches.append(
            {
                "scope": "layout_cache_counts_as_detail",
                "expected": False,
                "actual": layout_counts_as_detail,
            }
        )
    if not isinstance(detail_mtime_total, int) or not isinstance(detail_file_total, int):
        mismatches.append(
            {
                "scope": "detail_freshness_counter_types",
                "detail_mtime_strategy_total": detail_mtime_total,
                "detail_file_strategy_total": detail_file_total,
            }
        )
    elif detail_mtime_total > detail_file_total:
        mismatches.append(
            {
                "scope": "detail_freshness_counter_invariant",
                "detail_mtime_strategy_total": detail_mtime_total,
                "detail_file_strategy_total": detail_file_total,
            }
        )
    if mismatches:
        add_issue(
            issues,
            "warn",
            "ttfund_incremental_plan.cache_freshness",
            "天天详情增量计划缓存新鲜度来源不可信",
            "详情失败时生成的布局缓存不得刷新详情冷却期；应仅以有效 strategyDetailPageData 响应缓存判定完整性和新鲜度。",
            {"plan_path": str(path), "mismatches": mismatches},
            rule_id="TTFUND_DETAIL_CACHE_FRESHNESS_SOURCE_INVALID",
        )


def audit_gfbank_recorded_ocr_manifests(issues: list[dict[str, Any]]) -> None:
    raw_root = PROJECT_ROOT / "data" / "raw" / "gfbank_cgb" / "authenticated_ui"
    manifest_paths = sorted(raw_root.rglob("curve_*_manifest.json")) if raw_root.is_dir() else []
    if not manifest_paths:
        return
    latest_parent = max(
        {path.parent for path in manifest_paths},
        key=lambda parent: max(path.stat().st_mtime for path in parent.glob("curve_*_manifest.json")),
    )
    invalid: list[dict[str, Any]] = []
    recorded_manifest_total = 0
    for path in sorted(latest_parent.glob("curve_*_manifest.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            invalid.append({"file": str(path), "error": f"invalid_json: {exc}"})
            continue
        if payload.get("value_source") != "screen_recording_ocr_periodically_verified_against_uiautomator":
            continue
        recorded_manifest_total += 1
        reasons: list[str] = []
        if int(payload.get("failure_total") or 0) > 0:
            reasons.append("failure_total_nonzero")
        if int(payload.get("ocr_verification_total") or 0) < 1:
            reasons.append("exact_verification_missing")
        if int(payload.get("ocr_verification_mismatch_total") or 0) > 0:
            reasons.append("exact_verification_mismatch")
        if int(((payload.get("video_capture") or {}).get("value_conflict_total")) or 0) > 0:
            reasons.append("same_date_value_conflict")
        if payload.get("video_transport_deleted") is not True:
            reasons.append("video_transport_not_deleted")
        if reasons:
            invalid.append(
                {
                    "file": str(path),
                    "strategy_name": payload.get("strategy_name"),
                    "reasons": reasons,
                }
            )
    lingering_videos = sorted(str(path) for path in latest_parent.glob("*.mkv"))
    if lingering_videos:
        invalid.append({"error": "recorded_video_files_still_present", "files": lingering_videos[:20]})
    if recorded_manifest_total and invalid:
        add_issue(
            issues,
            "error",
            "gfbank_authenticated_ui.recorded_ocr",
            "广发银行录屏 OCR 曲线采集清单不满足晋级条件",
            f"最新登录态原始批次有 {len(invalid)} 项录屏 OCR 校验或空间清理异常。",
            invalid[:20],
            rule_id="GFBANK_RECORDED_OCR_CAPTURE_INVALID",
        )


def audit_gfbank_authenticated_cache_merge(issues: list[dict[str, Any]]) -> None:
    cache_dir = PROJECT_ROOT / "official_apps" / "gfbank_cgb" / "authenticated_cache"
    summary_path = cache_dir / "latest_summary.json"
    daily_path = cache_dir / "strategy_performance_daily.jsonl"
    if not summary_path.is_file() or not daily_path.is_file():
        return
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8-sig"))
        daily_rows = [
            json.loads(line)
            for line in daily_path.read_text(encoding="utf-8-sig").splitlines()
            if line.strip()
        ]
    except (OSError, json.JSONDecodeError) as exc:
        add_issue(
            issues,
            "error",
            "gfbank_authenticated_ui.incremental_cache",
            "广发银行登录态增量缓存无法解析",
            "登录态缓存摘要或日度曲线 JSONL 损坏，不能证明历史曲线安全保留。",
            {"summary_path": str(summary_path), "daily_path": str(daily_path), "error": str(exc)},
            rule_id="GFBANK_AUTHENTICATED_CACHE_HISTORY_REGRESSION",
        )
        return
    merge = summary.get("cache_merge")
    if not isinstance(merge, dict):
        return
    key_counts = Counter(
        (str(row.get("source_strategy_id") or ""), str(row.get("trade_date") or ""))
        for row in daily_rows
    )
    duplicate_keys = [
        {"source_strategy_id": key[0], "trade_date": key[1], "count": count}
        for key, count in key_counts.items()
        if all(key) and count > 1
    ]
    expected_daily = int((merge.get("merged_counts") or {}).get("strategy_performance_daily") or 0)
    invalid: list[dict[str, Any]] = []
    if int(merge.get("conflict_total") or 0) > 0:
        invalid.append({"error": "business_value_conflict", "details": (merge.get("conflicts") or [])[:20]})
    if int(merge.get("history_regression_total") or 0) > 0:
        invalid.append({"error": "history_count_regression", "details": (merge.get("history_regressions") or [])[:20]})
    if expected_daily != len(daily_rows):
        invalid.append(
            {
                "error": "summary_daily_count_mismatch",
                "expected": expected_daily,
                "actual": len(daily_rows),
            }
        )
    if duplicate_keys:
        invalid.append({"error": "duplicate_daily_business_keys", "details": duplicate_keys[:20]})
    if invalid:
        add_issue(
            issues,
            "error",
            "gfbank_authenticated_ui.incremental_cache",
            "广发银行登录态增量缓存出现历史缩水或业务键冲突",
            "日常最近区间补点只能合并进已有曲线；同策略同日期冲突、合并后数量缩水或业务键重复都会阻断晋级。",
            invalid,
            rule_id="GFBANK_AUTHENTICATED_CACHE_HISTORY_REGRESSION",
        )


def audit_gfbank_authenticated_entry_and_benchmark_coverage(issues: list[dict[str, Any]]) -> None:
    cache_dir = PROJECT_ROOT / "official_apps" / "gfbank_cgb" / "authenticated_cache"
    master_path = cache_dir / "strategy_master.jsonl"
    daily_path = cache_dir / "strategy_performance_daily.jsonl"
    if not master_path.is_file() or not daily_path.is_file():
        return
    try:
        masters = [
            json.loads(line)
            for line in master_path.read_text(encoding="utf-8-sig").splitlines()
            if line.strip()
        ]
        daily_rows = [
            json.loads(line)
            for line in daily_path.read_text(encoding="utf-8-sig").splitlines()
            if line.strip()
        ]
    except (OSError, json.JSONDecodeError):
        return
    if not masters:
        return

    def strategy_entry(row: dict[str, Any]) -> str:
        extra = row.get("extra") if isinstance(row.get("extra"), dict) else {}
        explicit = str(extra.get("strategy_entry") or "").strip()
        if explicit:
            return explicit
        tags = {str(value).strip() for value in (row.get("tags") or [])}
        for label in ("理财组合", "超级定投", "目标盈"):
            if label in tags:
                return label
        name = str(row.get("strategy_name") or "")
        if "目标盈" in name:
            return "目标盈"
        if "定投" in name:
            return "超级定投"
        return "理财组合"

    entries = Counter(strategy_entry(row) for row in masters)
    expected_entries = {"理财组合", "超级定投", "目标盈"}
    missing_entries = sorted(expected_entries - set(entries))
    if missing_entries:
        add_issue(
            issues,
            "warn",
            "gfbank_authenticated_ui.strategy_entries",
            "广发银行登录态策略入口覆盖不全",
            "广发智投首页的三个策略入口必须分别枚举；允许部分批次先晋级，但缺失入口不能被误报为全量成功。",
            {
                "entry_strategy_counts": dict(entries),
                "missing_strategy_entries": missing_entries,
                "master_path": str(master_path),
            },
            rule_id="GFBANK_AUTHENTICATED_STRATEGY_ENTRY_MISSING",
        )

    performance_masters = [row for row in masters if strategy_entry(row) == "理财组合"]
    benchmark_total = sum(
        1 for row in performance_masters if str(row.get("benchmark") or "").strip()
    )
    benchmark_rate = benchmark_total / len(performance_masters) if performance_masters else 0.0
    curve_dates_by_strategy: dict[str, set[str]] = defaultdict(set)
    for row in daily_rows:
        if (
            row.get("section_type") == "gfbank_authenticated_ui_curve_tooltip"
            and row.get("benchmark_return") is not None
            and row.get("cumulative_return") is not None
            and row.get("trade_date")
        ):
            curve_dates_by_strategy[str(row.get("source_strategy_id") or "")].add(str(row["trade_date"]))
    curve_strategy_total = sum(
        1
        for row in performance_masters
        if len(curve_dates_by_strategy.get(str(row.get("source_strategy_id") or ""), set())) >= 2
    )
    curve_rate = curve_strategy_total / len(performance_masters) if performance_masters else 0.0
    if benchmark_rate + 1e-9 < 0.8 or curve_rate + 1e-9 < 0.8:
        add_issue(
            issues,
            "warn",
            "gfbank_authenticated_ui.benchmark_disclosure",
            "广发银行业绩基准说明或双曲线覆盖不足",
            "基准说明必须来自点击展开的官方文本；双曲线点必须在同一日期同时具备组合和基准累计收益，不得用相邻日期值补齐。",
            {
                "strategy_total": len(masters),
                "performance_disclosure_eligible_strategy_total": len(performance_masters),
                "benchmark_description_strategy_total": benchmark_total,
                "benchmark_description_coverage_rate": round(benchmark_rate, 6),
                "dual_curve_strategy_total": curve_strategy_total,
                "dual_curve_coverage_rate": round(curve_rate, 6),
                "minimum_rate": 0.8,
            },
            rule_id="GFBANK_BENCHMARK_DISCLOSURE_MISSING",
        )

    target_child_ids = {
        str(row.get("source_strategy_id") or "")
        for row in masters
        if strategy_entry(row) == "目标盈"
        and re.search(r"期$", str(row.get("strategy_name") or "").strip())
    }
    misattributed_target_rows = [
        row
        for row in daily_rows
        if str(row.get("source_strategy_id") or "") in target_child_ids
    ]
    if misattributed_target_rows:
        affected_ids = sorted(
            {
                str(row.get("source_strategy_id") or "")
                for row in misattributed_target_rows
            }
        )
        add_issue(
            issues,
            "error",
            "gfbank_authenticated_ui.target_child_performance_lineage",
            "广发银行目标盈子期误挂母策略曲线",
            "官方目标盈子期详情使用 spGroupCode 标识期次，但 MP8769 业绩只按母策略 groupCode 返回；不得把母策略曲线当作子期独立业绩。",
            {
                "affected_strategy_ids": affected_ids[:50],
                "affected_strategy_total": len(affected_ids),
                "misattributed_daily_row_total": len(misattributed_target_rows),
                "handling": "block_promotion_and_keep_parent_curve_as_separate_entity",
            },
            rule_id="GFBANK_TARGET_CHILD_PARENT_CURVE_MISATTRIBUTED",
        )

    target_profit_names = {
        str(row.get("strategy_name") or "").strip()
        for row in masters
        if strategy_entry(row) == "目标盈"
    }
    source_sequence_signature = {
        "幸福小列车目标盈17期",
        "幸福小列车目标盈75期",
        "幸福小列车目标盈77期",
    }
    if source_sequence_signature.issubset(target_profit_names) and "幸福小列车目标盈76期" not in target_profit_names:
        add_issue(
            issues,
            "warn",
            "gfbank_authenticated_ui.target_profit_period_sequence",
            "广发银行目标盈期次存在源页面序号异常",
            "登录态页面在第75期和第77期之间明确显示第17期，且对应实际运作132天；系统保留官方原文，不擅自改写为第76期。",
            {
                "observed_names": sorted(source_sequence_signature),
                "missing_expected_sequence_name": "幸福小列车目标盈76期",
                "handling": "preserve_source_and_flag",
            },
            rule_id="GFBANK_TARGET_PROFIT_PERIOD_SEQUENCE_ANOMALY",
        )


def audit_gfsec_fima_position_history_preview(
    issues: list[dict[str, Any]],
    field_rules: dict[str, Any],
    *,
    preview_root: Path | None = None,
) -> None:
    """Audit the optional, inference-labelled GFSEC FIMA snapshot-history preview.

    The preview is deliberately outside the official rebalance tables.  When it
    exists, the project audit independently checks its files, row counts,
    uniqueness, weight closure and inference boundary instead of trusting the
    producer's validation status alone.
    """
    rule = field_rules.get("gfsecFimaPositionHistoryPreviewChecks")
    if not isinstance(rule, dict):
        return
    if preview_root is None:
        relative_text = str(rule.get("directory") or "outputs/gfsec_fima_position_history").strip().replace("\\", "/")
        relative = Path(relative_text)
        if relative.is_absolute() or ".." in relative.parts:
            add_issue(
                issues,
                "error",
                "gfsec_fima.position_history_preview.config",
                "历史仓位预览规则路径非法",
                f"directory 必须是项目内相对路径，当前={relative_text!r}。",
                rule_id="GFSEC_FIMA_HISTORY_SOURCE_PRESENT",
            )
            return
        preview_root = PROJECT_ROOT / relative
    if not preview_root.is_dir():
        if not bool(rule.get("optional", True)):
            add_issue(
                issues,
                "error",
                "gfsec_fima.position_history_preview",
                "历史仓位预览缺失",
                f"未找到预览目录：{preview_root}",
                rule_id="GFSEC_FIMA_HISTORY_SOURCE_PRESENT",
            )
        return
    run_dirs = sorted(
        {path.parent for path in preview_root.glob("*/summary.json") if path.is_file()},
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    if not run_dirs:
        if not bool(rule.get("optional", True)):
            add_issue(
                issues,
                "error",
                "gfsec_fima.position_history_preview",
                "历史仓位预览缺失",
                f"目录存在但没有可识别的 summary.json：{preview_root}",
                rule_id="GFSEC_FIMA_HISTORY_SOURCE_PRESENT",
            )
        return
    output_dir = run_dirs[0]
    required_files = [str(value) for value in rule.get("requiredFiles") or [] if str(value).strip()]
    missing_files = [name for name in required_files if not (output_dir / name).is_file()]
    if missing_files:
        add_issue(
            issues,
            "error",
            "gfsec_fima.position_history_preview.files",
            "历史仓位预览文件缺失",
            f"最新预览缺少 {len(missing_files)} 个必需文件。",
            {"outputDir": str(output_dir), "missingFiles": missing_files},
            rule_id="GFSEC_FIMA_HISTORY_SOURCE_PRESENT",
        )
        return

    try:
        summary = load_json_object(output_dir / "summary.json")
        validation = load_json_object(output_dir / "validation.json")

        def read_jsonl(name: str) -> list[dict[str, Any]]:
            rows: list[dict[str, Any]] = []
            with (output_dir / name).open("r", encoding="utf-8-sig") as handle:
                for line_number, line in enumerate(handle, start=1):
                    text = line.strip()
                    if not text:
                        continue
                    payload = json.loads(text)
                    if not isinstance(payload, dict):
                        raise ValueError(f"{name}:{line_number} is not a JSON object")
                    rows.append(payload)
            return rows

        state_rows = read_jsonl("state_snapshots.jsonl")
        position_rows = read_jsonl("position_snapshots.jsonl")
        transition_rows = read_jsonl("transition_audit.jsonl")
        candidate_rows = read_jsonl("change_candidates.jsonl")
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
        add_issue(
            issues,
            "error",
            "gfsec_fima.position_history_preview.files",
            "历史仓位预览无法解析",
            str(exc),
            {"outputDir": str(output_dir)},
            rule_id="GFSEC_FIMA_HISTORY_SOURCE_PRESENT",
        )
        return

    validation_checks = {
        str(item.get("check_id") or ""): item
        for item in validation.get("checks") or []
        if isinstance(item, dict) and str(item.get("check_id") or "")
    }
    expected_check_ids = [str(value) for value in rule.get("requiredValidationCheckIds") or [] if str(value).strip()]
    missing_check_ids = [check_id for check_id in expected_check_ids if check_id not in validation_checks]
    if missing_check_ids:
        add_issue(
            issues,
            "error",
            "gfsec_fima.position_history_preview.validation",
            "历史仓位预览校验项缺失",
            f"validation.json 缺少 {len(missing_check_ids)} 个必需校验项。",
            {"outputDir": str(output_dir), "missingCheckIds": missing_check_ids},
            rule_id="GFSEC_FIMA_HISTORY_SOURCE_PRESENT",
        )
    for check_id in expected_check_ids:
        check = validation_checks.get(check_id)
        if not check:
            continue
        status = str(check.get("status") or "").lower()
        if status not in {"warn", "failed", "error"}:
            continue
        catalog_severity = str((RULE_CATALOG.get(check_id) or {}).get("severity") or "warn")
        severity = "error" if status in {"failed", "error"} and catalog_severity == "error" else "warn"
        add_issue(
            issues,
            severity,
            "gfsec_fima.position_history_preview.validation",
            str((RULE_CATALOG.get(check_id) or {}).get("检查对象") or check_id),
            str(check.get("detail") or "预览内部校验未通过。"),
            {"outputDir": str(output_dir), "count": check.get("count")},
            rule_id=check_id,
        )

    counts = summary.get("counts") if isinstance(summary.get("counts"), dict) else {}
    expected_counts = {
        "state_occurrence_count": len(state_rows),
        "position_snapshot_row_count": len(position_rows),
        "transition_count": len(transition_rows),
        "change_candidate_count": len(candidate_rows),
    }
    count_mismatches = {
        key: {"summary": counts.get(key), "actual": actual}
        for key, actual in expected_counts.items()
        if counts.get(key) != actual
    }
    if count_mismatches:
        add_issue(
            issues,
            "error",
            "gfsec_fima.position_history_preview.counts",
            "历史仓位预览计数不闭合",
            "summary.json 计数与 JSONL 实际行数不一致。",
            {"outputDir": str(output_dir), "mismatches": count_mismatches},
            rule_id="GFSEC_FIMA_HISTORY_SOURCE_PRESENT",
        )

    min_weight = float((summary.get("parameters") or {}).get("weight_close_min_pct") or 99.5)
    max_weight = float((summary.get("parameters") or {}).get("weight_close_max_pct") or 100.5)
    bad_weights = [
        {"state_id": row.get("state_id"), "total_weight_pct": row.get("total_weight_pct")}
        for row in state_rows
        if not isinstance(row.get("total_weight_pct"), (int, float))
        or not min_weight <= float(row.get("total_weight_pct")) <= max_weight
    ]
    if bad_weights:
        add_issue(
            issues,
            "error",
            "gfsec_fima.position_history_preview.states",
            "历史仓位预览权重不闭合",
            f"{len(bad_weights)} 个状态权重不在 {min_weight}%-{max_weight}% 内。",
            bad_weights[:20],
            rule_id="GFSEC_FIMA_HISTORY_WEIGHT_CLOSURE",
        )

    position_keys = [(str(row.get("state_id") or ""), str(row.get("fund_code") or "")) for row in position_rows]
    duplicate_position_count = len(position_keys) - len(set(position_keys))
    if duplicate_position_count:
        add_issue(
            issues,
            "error",
            "gfsec_fima.position_history_preview.positions",
            "历史仓位预览业务键重复",
            f"(state_id, fund_code) 重复 {duplicate_position_count} 行。",
            rule_id="GFSEC_FIMA_HISTORY_POSITION_UNIQUENESS",
        )
    transition_ids = [str(row.get("candidate_id") or "") for row in transition_rows]
    duplicate_transition_count = len(transition_ids) - len(set(transition_ids))
    if duplicate_transition_count or any(not value for value in transition_ids):
        add_issue(
            issues,
            "error",
            "gfsec_fima.position_history_preview.transitions",
            "历史仓位预览变化ID异常",
            f"变化 ID 重复 {duplicate_transition_count} 行；空 ID={sum(not value for value in transition_ids)}。",
            rule_id="GFSEC_FIMA_HISTORY_TRANSITION_UNIQUENESS",
        )
    promoted = [
        str(row.get("candidate_id") or "")
        for row in transition_rows
        if bool(row.get("eligible_for_official_rebalance_table"))
    ]
    invalid_candidates = [
        str(row.get("candidate_id") or "")
        for row in candidate_rows
        if not bool(row.get("is_change_candidate")) or bool(row.get("eligible_for_official_rebalance_table"))
    ]
    semantics = summary.get("semantics") if isinstance(summary.get("semantics"), dict) else {}
    if promoted or invalid_candidates or bool(semantics.get("main_database_written")) or bool(semantics.get("official_rebalance_table_written")):
        add_issue(
            issues,
            "error",
            "gfsec_fima.position_history_preview.inference_boundary",
            "历史仓位推断越界",
            "推断候选不得标记为可写正式调仓，预览摘要也不得声明已写主库。",
            {
                "outputDir": str(output_dir),
                "promotedTransitionIds": promoted[:20],
                "invalidCandidateIds": invalid_candidates[:20],
                "semantics": semantics,
            },
            rule_id="GFSEC_FIMA_HISTORY_INFERENCE_BOUNDARY",
        )


def main() -> None:
    global RULE_CATALOG, DISABLED_CHANNEL_IDS
    args = parse_args()
    RULE_CATALOG = load_rule_catalog(args.rules_path)
    try:
        field_rules = load_json_object(args.field_rules_path)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        field_rules = {}
    page_scope = field_rules.get("pageStrategyScope") if isinstance(field_rules.get("pageStrategyScope"), dict) else {}
    DISABLED_CHANNEL_IDS = {
        str(value).strip()
        for value in page_scope.get("disabledChannelIds") or []
        if str(value).strip()
    }
    run_id = now_text()
    output_dir = args.output_root / datetime.now().astimezone().strftime("%Y-%m-%d") / run_id
    issues: list[dict[str, Any]] = []
    audit_sqlite(issues, args.db_path)
    audit_strategy_parent_child_relationships(issues, args.db_path, args.site_dir)
    audit_core_fact_semantics(issues, args.db_path)
    audit_public_fund_benchmark_buckets(issues, args.db_path)
    audit_strategy_governance_semantics(issues, args.db_path)
    audit_target_profit_page_consistency(issues, args.db_path, args.site_dir)
    audit_qd_limit_page_consistency(issues, args.site_dir)
    audit_economic_pack(issues, args.site_dir)
    audit_fund_detail_pack(issues, args.site_dir)
    audit_detail_analysis_modules(issues, args.site_dir)
    audit_fof_universe_coverage(issues, args.db_path, args.site_dir)
    audit_ai_semantic_index(issues, args.site_dir)
    audit_ai_strategy_two_stage_protocol(issues, args.site_dir, field_rules)
    audit_ai_strategy_date_value_normalization(issues, args.site_dir, field_rules)
    audit_ai_strategy_performance_scope(issues, args.site_dir, field_rules)
    audit_generic_page_packs(issues, args.site_dir)
    audit_official_performance_image_assets(issues, args.site_dir)
    audit_single_point_interval_returns(issues, args.site_dir)
    audit_minimal_publish_cache_guard(issues, args.site_dir)
    audit_mixed_performance_pack(issues, args.site_dir, args.db_path)
    audit_strategy_overseas_classification(issues, args.site_dir)
    audit_gfsec_legacy_page_scope(issues, args.site_dir)
    audit_page_strategy_business_scope(issues, args.db_path, args.site_dir, args.field_rules_path)
    audit_system_field_rules(issues, args.db_path, args.site_dir, args.field_rules_path)
    audit_gfsec_fima_position_history_preview(issues, field_rules)
    audit_incremental_gap_summary(issues)
    audit_ttfund_incremental_plan_cache_freshness(issues)
    if "gfbank_cgb" not in DISABLED_CHANNEL_IDS:
        audit_gfbank_recorded_ocr_manifests(issues)
        audit_gfbank_authenticated_cache_merge(issues)
        audit_gfbank_authenticated_entry_and_benchmark_coverage(issues)

    summary = Counter(issue["severity"] for issue in issues)
    status = "error" if summary["error"] else ("warn" if summary["warn"] else "ok")
    report = {
        "version": 1,
        "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "status": status,
        "dbPath": str(args.db_path),
        "siteDir": str(args.site_dir),
        "rulesPath": str(args.rules_path),
        "fieldRulesPath": str(args.field_rules_path),
        "ruleCount": len(RULE_CATALOG),
        "summary": {"error": summary["error"], "warn": summary["warn"], "total": len(issues)},
        "issues": issues,
        "outputDir": str(output_dir),
    }
    write_reports(output_dir, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.fail_on_error and status == "error":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
