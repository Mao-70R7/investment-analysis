from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from analyze_ai_core_exposure import load_assigned_js
from business_naming import canonical_advisor_institution


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
DEFAULT_SITE_DIR = PROJECT_ROOT / "site" / "basic_data"
DEFAULT_ALGORITHM_VERSION = "standard_rebalance_asset_dual_nav_v10_all_channels_20260528"
PACK_VERSION = 2
TARGET_BUSINESS = "目标盈系列产品"
DISPLAY_STRATEGY_CHANNEL_IDS = ("gffunds", "ttfund")


def raw(value: Any) -> str:
    return "" if value is None else str(value)


def num(value: Any, default: float | None = None) -> float | None:
    if value is None or value == "":
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def round_or_none(value: Any, digits: int = 4) -> float | None:
    number = num(value)
    return round(number, digits) if number is not None else None


def first_number(*values: Any) -> float | None:
    for value in values:
        number = num(value)
        if number is not None:
            return number
    return None


def parse_date(value: Any) -> date | None:
    text = raw(value)[:10]
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def beijing_now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def js_assignment(path: Path, lhs: str, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = f"{lhs} = {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))};\n"
    if path.exists():
        try:
            if path.read_text(encoding="utf-8") == text:
                return
        except UnicodeDecodeError:
            pass
    path.write_text(text, encoding="utf-8")


def median(values: list[float | None]) -> float | None:
    arr = sorted(v for v in values if v is not None and math.isfinite(v))
    if not arr:
        return None
    mid = len(arr) // 2
    return arr[mid] if len(arr) % 2 else (arr[mid - 1] + arr[mid]) / 2


def percentile(values: list[float | None], p: float) -> float | None:
    arr = sorted(v for v in values if v is not None and math.isfinite(v))
    if not arr:
        return None
    if len(arr) == 1:
        return arr[0]
    pos = (len(arr) - 1) * p
    low = math.floor(pos)
    high = math.ceil(pos)
    if low == high:
        return arr[low]
    return arr[low] * (high - pos) + arr[high] * (pos - low)


def majority(rows: list[dict[str, Any]], field: str, fallback: str = "未分类") -> str:
    counts = Counter(raw(row.get(field)) or fallback for row in rows)
    return counts.most_common(1)[0][0] if counts else fallback


def highest_risk(rows: list[dict[str, Any]]) -> str:
    order = {
        "R0 现金/超低波": 0,
        "R1 低波": 1,
        "R2 稳健收益": 2,
        "R3 均衡稳健": 3,
        "R4 均衡成长": 4,
        "R5 权益/进取": 5,
    }
    best = sorted(rows, key=lambda row: order.get(raw(row.get("风险等级")), -1), reverse=True)
    return raw(best[0].get("风险等级")) if best else "未分类"


def chinese_issue_number(text: str) -> int | None:
    digits = "零一二三四五六七八九"
    values = {char: index for index, char in enumerate(digits)}
    values.update({"〇": 0, "两": 2})
    if not text:
        return None
    if text.isdigit():
        return int(text)
    if text == "十":
        return 10
    if "十" in text:
        left, _, right = text.partition("十")
        tens = values.get(left, 1) if left else 1
        ones = values.get(right, 0) if right else 0
        return tens * 10 + ones
    total = 0
    for char in text:
        if char not in values:
            return None
        total = total * 10 + values[char]
    return total if total > 0 else None


def extract_issue_number(name: Any) -> int | None:
    text = raw(name)
    patterns = [
        r"(?:第)?([0-9]{1,5})\s*期",
        r"(?:第)([零〇一二三四五六七八九十两]{1,8})\s*期",
        r"目标盈\s*([0-9]{6,8})",
        r"([0-9]{6,8})$",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        token = match.group(1)
        if token.isdigit():
            value = int(token)
            if value > 10000:
                return value
            return value
        value = chinese_issue_number(token)
        if value is not None:
            return value
    return None


def extract_issue_variant(name: Any) -> str:
    text = raw(name)
    variants = []
    variants.extend(re.findall(r"[（(]([^）)]{1,16}?版)[）)]", text))
    if "年中版" in text and "年中版" not in variants:
        variants.append("年中版")
    if "天天" in text:
        variants.append("天天")
    return "、".join(dict.fromkeys(item.strip() for item in variants if item.strip()))


def normalize_target_subseries_name(name: Any) -> str:
    text = raw(name)
    cleaned = re.sub(r"第?[零一二三四五六七八九十百千万\d]{1,5}期", "", text)
    cleaned = re.sub(r"\d{1,4}期", "", cleaned)
    cleaned = re.sub(r"目标盈\s*\d{6,8}$", "目标盈", cleaned)
    cleaned = re.sub(r"\d{6,8}$", "", cleaned)
    cleaned = re.sub(r"天天\d{1,4}", "天天", cleaned)
    cleaned = re.sub(r"\s+", "", cleaned)
    cleaned = re.sub(r"[\\\-_—]+$", "", cleaned)
    cleaned = re.sub(r"（\s*）", "", cleaned)
    return cleaned.strip() or text


def normalize_target_series_name(name: Any) -> str:
    text = normalize_target_subseries_name(name)
    cleaned = re.sub(r"[（(][^）)]{1,16}?版[）)]", "", text)
    cleaned = cleaned.replace("天天", "")
    cleaned = re.sub(r"年中版", "", cleaned)
    cleaned = re.sub(r"\s+", "", cleaned)
    cleaned = re.sub(r"[\\\-_—]+$", "", cleaned)
    cleaned = re.sub(r"（\s*）", "", cleaned)
    return cleaned.strip() or text


def series_id(advisor: str, series_name: str) -> str:
    key = f"{advisor or '未识别机构'}|{series_name}"
    digest = hashlib.md5(key.encode("utf-8")).hexdigest()[:12]
    return f"target_{digest}"


def normalize_target_advisor(advisor: Any) -> str:
    text = raw(advisor).strip() or "未识别机构"
    aliases = [
        (r"华夏投顾.*", "华夏投顾"),
        (r"中欧财富.*|中欧钱滚滚.*", "中欧财富投顾"),
        (r"富国基金.*", "富国基金-富国星投顾"),
        (r"南方基金.*", "南方基金-司南投顾"),
        (r"广发基金.*", "广发基金"),
    ]
    for pattern, value in aliases:
        if re.search(pattern, text):
            return value
    return text


def extract_goal_pct(row: dict[str, Any]) -> float | None:
    pieces = [
        row.get("策略名称"),
        row.get("策略概念"),
        row.get("策略描述"),
        row.get("标签"),
        row.get("业务分类依据"),
        row.get("业绩基准说明"),
    ]
    text = " ".join(raw(item) for item in pieces if item)
    patterns = [
        r"(?:目标(?:收益|盈)?|止盈(?:目标|线)?|达到目标|小目标)[^0-9%]{0,12}(\d+(?:\.\d+)?)\s*(?:%|％|个点|点)",
        r"(\d+(?:\.\d+)?)\s*(?:%|％|个点|点)[^，。；;]{0,12}(?:目标|止盈|达标)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            value = num(match.group(1))
            if value is not None and 0 < value <= 40:
                return round(value, 4)
    return None


def load_summary(site_dir: Path) -> dict[str, Any]:
    candidates = [
        site_dir / "data" / "basic_summary_core.js",
        site_dir / "data" / "basic_summary.js",
    ]
    for path in candidates:
        if path.exists():
            payload = load_assigned_js(path)
            return payload.get("summary", payload)
    raise FileNotFoundError(f"missing basic summary pack under {site_dir / 'data'}")


def is_target_profit_product_text(text: str) -> bool:
    normalized = raw(text)
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


def target_rows(summary: dict[str, Any]) -> list[dict[str, Any]]:
    rows = summary.get("strategies") or []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        strategy_id = raw(row.get("统一策略ID"))
        governance_text = " ".join(raw(row.get(field)) for field in ["策略治理状态", "分析分组", "治理状态", "规则说明"])
        text = " ".join(raw(row.get(field)) for field in ["策略名称", "披露策略类型", "策略类型", "策略描述", "标签", "业务分类依据", "天天展示状态"]) + " " + governance_text
        governance_target = "目标盈" in governance_text
        if raw(row.get("业务分类")) == TARGET_BUSINESS or governance_target or is_target_profit_product_text(text):
            if strategy_id and strategy_id in seen:
                continue
            seen.add(strategy_id)
            out.append(row)
    return out


def load_governance_target_rows(con: sqlite3.Connection) -> list[dict[str, Any]]:
    tables = {raw(row[0]) for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "策略治理标签" not in tables or "策略信息" not in tables:
        return []
    placeholders = ",".join("?" for _ in DISPLAY_STRATEGY_CHANNEL_IDS)
    return [
        dict(row)
        for row in con.execute(
            f'''
            SELECT g."统一策略ID",
                   COALESCE(s."策略名称", g."策略名称") AS "策略名称",
                   COALESCE(s."投顾机构", g."投顾机构") AS "投顾机构",
                   COALESCE(s."渠道ID", g."渠道ID") AS "渠道",
                   s."策略类型" AS "研报产品类型",
                   s."策略类型",
                   s."策略状态" AS "运作状态",
                   s."策略状态",
                   s."策略描述",
                   s."标签JSON" AS "标签",
                   s."风险等级",
                   s."成立日期",
                   s."业绩基准" AS "业绩基准说明",
                   g."治理状态" AS "策略治理状态",
                   g."治理状态",
                   g."分析分组",
                   g."规则说明",
                   g."规则说明" AS "业务分类依据",
                   g."官方最新业绩日期" AS "最新业绩日期"
            FROM "策略治理标签" g
            LEFT JOIN "策略信息" s ON s."统一策略ID" = g."统一策略ID"
            WHERE g."是否目标盈期次" = 1
              AND COALESCE(s."渠道ID", g."渠道ID") IN ({placeholders})
            ORDER BY g."策略名称", g."统一策略ID"
            ''',
            DISPLAY_STRATEGY_CHANNEL_IDS,
        )
    ]


def merge_governance_target_rows(
    summary_rows: list[dict[str, Any]],
    governance_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged_by_id = {
        raw(row.get("统一策略ID")): dict(row)
        for row in summary_rows
        if raw(row.get("统一策略ID"))
    }
    order = [raw(row.get("统一策略ID")) for row in summary_rows if raw(row.get("统一策略ID"))]
    governance_fields = ("策略治理状态", "治理状态", "分析分组", "规则说明", "业务分类依据")
    for governance_row in governance_rows:
        strategy_id = raw(governance_row.get("统一策略ID"))
        if not strategy_id:
            continue
        if strategy_id not in merged_by_id:
            merged_by_id[strategy_id] = dict(governance_row)
            order.append(strategy_id)
            continue
        merged = {**governance_row, **merged_by_id[strategy_id]}
        for field in governance_fields:
            if raw(governance_row.get(field)):
                merged[field] = governance_row[field]
        merged_by_id[strategy_id] = merged
    return [merged_by_id[strategy_id] for strategy_id in order]


def batch_values(items: list[str], size: int = 400) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def load_nav_curves(con: sqlite3.Connection, sids: list[str], algorithm_version: str) -> tuple[dict[str, list[tuple[str, float]]], dict[str, str]]:
    official: dict[str, dict[str, float]] = defaultdict(dict)
    standard: dict[str, dict[str, float]] = defaultdict(dict)
    for batch in batch_values(sids):
        placeholders = ",".join(["?"] * len(batch))
        for sid, d, nav in con.execute(
            f"""
            select 统一策略ID, 交易日期, 披露单位净值
            from 策略产品披露净值
            where 统一策略ID in ({placeholders})
              and 是否可画曲线 = 1
              and 披露单位净值 is not null
              and 披露单位净值 > 0
            order by 统一策略ID, 交易日期
            """,
            batch,
        ):
            value = num(nav)
            if value and value > 0:
                official[raw(sid)][raw(d)] = value
        params = [*batch, algorithm_version]
        for sid, d, nav in con.execute(
            f"""
            select 统一策略ID, 交易日期, 标准费前单位净值
            from 策略标准业绩净值
            where 统一策略ID in ({placeholders})
              and 算法版本 = ?
              and 标准费前单位净值 is not null
              and 标准费前单位净值 > 0
            order by 统一策略ID, 交易日期
            """,
            params,
        ):
            value = num(nav)
            if value and value > 0:
                standard[raw(sid)][raw(d)] = value
    out: dict[str, list[tuple[str, float]]] = {}
    source: dict[str, str] = {}
    for sid in sids:
        source_map = official.get(sid) if len(official.get(sid, {})) >= 2 else standard.get(sid, {})
        if len(source_map) >= 2:
            out[sid] = sorted(source_map.items())
            source[sid] = "官方披露净值" if len(official.get(sid, {})) >= 2 else "标准回放净值"
        else:
            out[sid] = []
            source[sid] = "缺少可用净值"
    return out, source


def trim_terminal_flat_tail(series: list[tuple[str, float]], row: dict[str, Any]) -> list[tuple[str, float]]:
    if len(series) < 3:
        return series
    status_text = " ".join(raw(row.get(field)) for field in ["运作状态", "策略状态", "天天展示状态", "策略治理状态"])
    if not re.search(r"终止|到期|期满|清盘|stopped|已结束|非对客|下架", status_text, flags=re.I):
        return series
    last = len(series) - 1
    while last > 1 and abs((series[last][1] or 0) - (series[last - 1][1] or 0)) <= 1e-8:
        last -= 1
    # Keep one terminal point so the chart shows the stop level without a long flat tail.
    keep_to = min(len(series) - 1, last + 1)
    return series[: keep_to + 1]


def load_strategy_meta(con: sqlite3.Connection, sids: list[str]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if not sids:
        return out
    for batch in batch_values(sids):
        placeholders = ",".join(["?"] * len(batch))
        for row in con.execute(
            f"""
            select 统一策略ID, 策略类型, 策略状态, 策略描述, 标签JSON, 业绩基准
            from 策略信息
            where 统一策略ID in ({placeholders})
            """,
            batch,
        ):
            sid, strategy_type, strategy_status, description, tags_json, benchmark = row
            out[raw(sid)] = {
                "策略类型": raw(strategy_type),
                "策略状态": raw(strategy_status),
                "策略描述": raw(description),
                "标签JSON": raw(tags_json),
                "业绩基准": raw(benchmark),
            }
    return out


def sample_curve(points: list[dict[str, Any]], limit: int = 180) -> list[dict[str, Any]]:
    if len(points) <= limit:
        return points
    keep = {0, len(points) - 1}
    step = (len(points) - 1) / (limit - 1)
    for i in range(limit):
        keep.add(round(i * step))
    return [points[i] for i in sorted(keep)]


def curve_metrics(series: list[tuple[str, float]]) -> dict[str, Any]:
    if len(series) < 2:
        return {
            "firstDate": "",
            "lastDate": "",
            "days": None,
            "lifecycleReturn": None,
            "peakReturn": None,
            "maxDrawdown": None,
            "currentDrawdown": None,
            "points": [],
        }
    first_date = parse_date(series[0][0])
    last_date = parse_date(series[-1][0])
    base = series[0][1]
    peak_nav = base
    max_dd = 0.0
    current_dd = 0.0
    points: list[dict[str, Any]] = []
    peak_return = -10**9
    for d, nav in series:
        if nav > peak_nav:
            peak_nav = nav
        drawdown = (peak_nav - nav) / peak_nav * 100 if peak_nav else 0.0
        max_dd = max(max_dd, drawdown)
        current_dd = drawdown
        ret = (nav / base - 1) * 100 if base else 0.0
        peak_return = max(peak_return, ret)
        dt = parse_date(d)
        day_no = (dt - first_date).days if dt and first_date else 0
        points.append({"date": d, "day": day_no, "returnPct": round(ret, 4)})
    return {
        "firstDate": series[0][0],
        "lastDate": series[-1][0],
        "days": (last_date - first_date).days + 1 if first_date and last_date else None,
        "lifecycleReturn": round(points[-1]["returnPct"], 4),
        "peakReturn": round(peak_return, 4),
        "maxDrawdown": round(max_dd, 4),
        "currentDrawdown": round(current_dd, 4),
        "points": sample_curve(points),
    }


def trailing_return(series: list[tuple[str, float]], days: int) -> float | None:
    if len(series) < 2:
        return None
    last_dt = parse_date(series[-1][0])
    last_nav = num(series[-1][1])
    if not last_dt or last_nav is None:
        return None
    target_dt = last_dt - timedelta(days=days)
    base_nav: float | None = None
    for d, nav in reversed(series):
        dt = parse_date(d)
        nav_value = num(nav)
        if dt and dt <= target_dt and nav_value is not None and nav_value > 0:
            base_nav = nav_value
            break
    if base_nav is None or base_nav <= 0:
        return None
    return round((last_nav / base_nav - 1) * 100, 4)


def classify_lifecycle_status(row: dict[str, Any], metrics: dict[str, Any], goal_pct: float | None, data_updated_to: str | None = None) -> tuple[str, str]:
    status_text = " ".join(raw(row.get(field)) for field in ["运作状态", "策略状态", "天天展示状态"])
    peak_return = num(metrics.get("peakReturn"))
    current_display = raw(row.get("天天当前对客展示")) == "是"
    display_text = raw(row.get("天天展示状态"))
    last_dt = parse_date(metrics.get("lastDate"))
    data_dt = parse_date(data_updated_to)
    recent_nav = bool(last_dt and data_dt and (data_dt - last_dt).days <= 45)
    if re.search(r"已止盈|止盈成功|止盈完成|止盈退出", status_text):
        return "已止盈", "状态字段明确披露止盈。"
    if current_display and not re.search(r"终止|到期|期满|清盘|stopped|已结束|非对客|下架", status_text, flags=re.I):
        return "运行中观察", "当前对客展示为是，且状态字段未命中终止/到期。"
    if any(word in status_text for word in ["终止", "到期", "期满", "清盘", "stopped"]):
        return "已终止/期满", "状态或展示字段命中终止、到期、期满、stopped 等明确终止口径。"
    if "预约" in status_text:
        return "预约期", "状态字段显示预约期。"
    if current_display or any(word in status_text for word in ["正常", "公开披露", "运行", "开放窗口"]):
        return "运行中观察", "对客展示或运作状态显示正常运行/公开披露。"
    if display_text and not re.search(r"终止|非对客|结束|隐藏|下架", display_text) and "展示口径" in display_text:
        return "运行中观察", "渠道展示口径有效，且未命中终止/非对客。"
    if recent_nav and not re.search(r"终止|到期|期满|清盘|stopped|已结束|非对客|下架", status_text, flags=re.I):
        return "运行中观察", f"净值最近更新至 {metrics.get('lastDate')}，距离数据更新日不超过45天，且无终止字段。"
    if goal_pct is not None and peak_return is not None and peak_return >= goal_pct:
        return "历史收益曾达目标", "可解析目标收益率，且生命周期峰值收益曾达到目标。"
    return raw(row.get("运作状态")) or "未披露", "未命中运行、预约、止盈或终止的明确字段。"


def is_active_status(status: str) -> bool:
    return status in {"运行中观察", "预约期"} or status in {"正常运作", "公开披露"}


def build_period_rows(
    rows: list[dict[str, Any]],
    curves: dict[str, list[tuple[str, float]]],
    sources: dict[str, str],
    strategy_meta: dict[str, dict[str, Any]] | None = None,
    data_updated_to: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    periods: list[dict[str, Any]] = []
    curve_pack: dict[str, list[dict[str, Any]]] = {}
    strategy_meta = strategy_meta or {}
    for row in rows:
        sid = raw(row.get("统一策略ID"))
        row = {**strategy_meta.get(sid, {}), **row}
        source_row = row
        raw_advisor = raw(row.get("投顾机构")) or "未识别机构"
        advisor = canonical_advisor_institution(raw_advisor) or "未识别机构"
        advisor_group = normalize_target_advisor(advisor)
        s_name = normalize_target_series_name(row.get("策略名称"))
        subseries_name = normalize_target_subseries_name(row.get("策略名称"))
        sid_series = series_id(advisor_group, s_name)
        trimmed_curve = trim_terminal_flat_tail(curves.get(sid, []), source_row)
        metrics = curve_metrics(trimmed_curve)
        goal_pct = extract_goal_pct(source_row)
        lifecycle_status, lifecycle_basis = classify_lifecycle_status(source_row, metrics, goal_pct, data_updated_to)
        curve_pack[sid] = metrics.get("points") or []
        period = {
            "统一策略ID": sid,
            "系列ID": sid_series,
            "系列名称": s_name,
            "子系列名称": subseries_name,
            "期次序号": extract_issue_number(row.get("策略名称")),
            "期次版本": extract_issue_variant(row.get("策略名称")),
            "策略名称": raw(row.get("策略名称")),
            "投顾机构": advisor,
            "投顾机构原始值": raw_advisor,
            "系列机构": advisor_group,
            "渠道": raw(row.get("渠道")),
            "风险等级": raw(row.get("风险等级")),
            "研报产品类型": raw(row.get("研报产品类型")),
            "成立日期": raw(row.get("成立日期")),
            "运作状态": raw(row.get("运作状态")),
            "天天当前对客展示": raw(row.get("天天当前对客展示")),
            "天天展示状态": raw(row.get("天天展示状态")),
            "生命周期状态": lifecycle_status,
            "生命周期状态依据": lifecycle_basis,
            "数据完整性": raw(row.get("数据完整性")),
            "最新业绩日期": raw(row.get("最新业绩日期") or row.get("收益数据截至") or row.get("最新业绩日")),
            "最新持仓日": raw(row.get("最新持仓日")),
            "持仓来源": raw(row.get("持仓来源")),
            "持仓基金数": round_or_none(row.get("持仓基金数"), 0),
            "目标收益率": goal_pct,
            "生命周期收益": metrics.get("lifecycleReturn"),
            "生命周期峰值收益": metrics.get("peakReturn"),
            "生命周期最大回撤": metrics.get("maxDrawdown"),
            "当前回撤": round_or_none(row.get("当前回撤")),
            "生命周期当前回撤": metrics.get("currentDrawdown"),
            "生命周期天数": metrics.get("days"),
            "净值起始日": metrics.get("firstDate"),
            "净值结束日": metrics.get("lastDate"),
            "曲线来源": sources.get(sid) or "缺少可用净值",
            "近三月": round_or_none(first_number(row.get("近三月"), row.get("近3月"), trailing_return(trimmed_curve, 90))),
            "近6月": round_or_none(row.get("近6月")),
            "近1年": round_or_none(row.get("近1年")),
            "累计收益率": round_or_none(row.get("累计收益率")),
            "最大回撤": round_or_none(row.get("最大回撤")),
            "年化收益": round_or_none(row.get("年化收益")),
            "波动率": round_or_none(row.get("波动率")),
            "夏普比率": round_or_none(row.get("夏普比率")),
            "权益基金权重": round_or_none(row.get("权益基金权重")),
            "债券基金权重": round_or_none(row.get("债券基金权重")),
            "货币基金权重": round_or_none(row.get("货币基金权重")),
            "混合基金权重": round_or_none(row.get("混合基金权重")),
            "QDII权重": round_or_none(row.get("QDII权重")),
            "业务分类依据": raw(row.get("业务分类依据")),
        }
        periods.append(period)
    periods.sort(key=lambda item: (item.get("投顾机构") or "", item.get("系列名称") or "", item.get("成立日期") or ""))
    return periods, curve_pack


def aggregate_series(periods: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in periods:
        grouped[raw(row.get("系列ID"))].append(row)
    out: list[dict[str, Any]] = []
    for sid, rows in grouped.items():
        rows_sorted = sorted(rows, key=lambda row: raw(row.get("成立日期")))
        representative = sorted(
            rows,
            key=lambda row: (
                1 if is_active_status(raw(row.get("生命周期状态"))) else 0,
                num(row.get("近1年"), -10**9) or -10**9,
                raw(row.get("成立日期")),
            ),
            reverse=True,
        )[0]
        goal_rows = [row for row in rows if num(row.get("目标收益率")) is not None]
        goal_hit_rows = [
            row
            for row in goal_rows
            if num(row.get("生命周期峰值收益")) is not None
            and num(row.get("目标收益率")) is not None
            and (num(row.get("生命周期峰值收益")) or 0) >= (num(row.get("目标收益率")) or 0)
        ]
        finished_rows = [row for row in rows if raw(row.get("生命周期状态")) in {"已止盈", "已终止/期满", "历史收益曾达目标"}]
        series_row = {
            "系列ID": sid,
            "系列名称": raw(representative.get("系列名称")),
            "投顾机构": raw(representative.get("投顾机构")),
            "渠道": majority(rows, "渠道"),
            "风险等级": highest_risk(rows),
            "研报产品类型": majority(rows, "研报产品类型"),
            "期次数": len(rows),
            "可评价期次数": len([row for row in rows if num(row.get("生命周期收益")) is not None]),
            "运行中期次数": len([row for row in rows if is_active_status(raw(row.get("生命周期状态")))]),
            "已止盈期次数": len([row for row in rows if raw(row.get("生命周期状态")) == "已止盈"]),
            "已终止期次数": len([row for row in rows if raw(row.get("生命周期状态")) == "已终止/期满"]),
            "有目标收益期次数": len(goal_rows),
            "收益曾达目标期次数": len(goal_hit_rows),
            "达标率": round(len(goal_hit_rows) / len(goal_rows) * 100, 4) if goal_rows else None,
            "首期成立日": raw(rows_sorted[0].get("成立日期")),
            "最近期成立日": raw(rows_sorted[-1].get("成立日期")),
            "代表期次": raw(representative.get("策略名称")),
            "代表策略ID": raw(representative.get("统一策略ID")),
            "中位生命周期收益": round_or_none(median([num(row.get("生命周期收益")) for row in rows])),
            "中位生命周期最大回撤": round_or_none(median([num(row.get("生命周期最大回撤")) for row in rows])),
            "中位生命周期天数": round_or_none(median([num(row.get("生命周期天数")) for row in rows]), 0),
            "中位近1年": round_or_none(median([num(row.get("近1年")) for row in rows])),
            "中位近6月": round_or_none(median([num(row.get("近6月")) for row in rows])),
            "中位近三月": round_or_none(median([num(row.get("近三月")) for row in rows])),
            "中位权益权重": round_or_none(median([num(row.get("权益基金权重")) for row in rows])),
            "中位债券权重": round_or_none(median([num(row.get("债券基金权重")) for row in rows])),
            "中位QDII权重": round_or_none(median([num(row.get("QDII权重")) for row in rows])),
            "完成期次中位收益": round_or_none(median([num(row.get("生命周期收益")) for row in finished_rows])),
            "期次ID列表": [raw(row.get("统一策略ID")) for row in rows_sorted],
        }
        out.append(series_row)
    out.sort(key=lambda item: (num(item.get("期次数"), 0) or 0, num(item.get("中位生命周期收益"), -10**9) or -10**9), reverse=True)
    return out


def distribution(rows: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    counts = Counter(raw(row.get(field)) or "未披露" for row in rows)
    return [{"名称": name, "数量": count} for name, count in counts.most_common()]


def advisor_stats(series_rows: list[dict[str, Any]], periods: list[dict[str, Any]]) -> list[dict[str, Any]]:
    period_by_advisor: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in periods:
        period_by_advisor[raw(row.get("投顾机构")) or "未识别机构"].append(row)
    series_by_advisor: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in series_rows:
        series_by_advisor[raw(row.get("投顾机构")) or "未识别机构"].append(row)
    out: list[dict[str, Any]] = []
    for advisor, rows in period_by_advisor.items():
        srows = series_by_advisor.get(advisor, [])
        goal_rows = [row for row in rows if num(row.get("目标收益率")) is not None]
        hit_rows = [
            row
            for row in goal_rows
            if num(row.get("生命周期峰值收益")) is not None
            and num(row.get("生命周期峰值收益")) >= num(row.get("目标收益率"))
        ]
        out.append(
            {
                "投顾机构": advisor,
                "系列数": len(srows),
                "期次数": len(rows),
                "运行中期次数": len([row for row in rows if is_active_status(raw(row.get("生命周期状态")))]),
                "已止盈期次数": len([row for row in rows if raw(row.get("生命周期状态")) == "已止盈"]),
                "达标率": round(len(hit_rows) / len(goal_rows) * 100, 4) if goal_rows else None,
                "中位生命周期收益": round_or_none(median([num(row.get("生命周期收益")) for row in rows])),
                "中位最大回撤": round_or_none(median([num(row.get("生命周期最大回撤")) for row in rows])),
                "中位持有天数": round_or_none(median([num(row.get("生命周期天数")) for row in rows]), 0),
            }
        )
    out.sort(key=lambda row: (row["期次数"], num(row.get("中位生命周期收益"), -10**9) or -10**9), reverse=True)
    return out


def build_overview(summary: dict[str, Any], series_rows: list[dict[str, Any]], periods: list[dict[str, Any]]) -> dict[str, Any]:
    evaluable = [row for row in periods if num(row.get("生命周期收益")) is not None]
    active = [row for row in periods if is_active_status(raw(row.get("生命周期状态")))]
    stopped = [row for row in periods if raw(row.get("生命周期状态")) == "已止盈"]
    goal_rows = [row for row in periods if num(row.get("目标收益率")) is not None]
    goal_hit = [
        row
        for row in goal_rows
        if num(row.get("生命周期峰值收益")) is not None and num(row.get("生命周期峰值收益")) >= num(row.get("目标收益率"))
    ]
    overview = summary.get("overview") or {}
    return {
        "数据更新至": overview.get("数据更新至"),
        "基础数据刷新时间": overview.get("数据刷新时间") or overview.get("生成时间"),
        "目标盈期次数": len(periods),
        "目标盈系列数": len(series_rows),
        "可评价期次数": len(evaluable),
        "运行中期次数": len(active),
        "已止盈期次数": len(stopped),
        "有目标收益期次数": len(goal_rows),
        "收益曾达目标期次数": len(goal_hit),
        "目标收益达标率": round(len(goal_hit) / len(goal_rows) * 100, 4) if goal_rows else None,
        "中位生命周期收益": round_or_none(median([num(row.get("生命周期收益")) for row in evaluable])),
        "生命周期收益P25": round_or_none(percentile([num(row.get("生命周期收益")) for row in evaluable], 0.25)),
        "生命周期收益P75": round_or_none(percentile([num(row.get("生命周期收益")) for row in evaluable], 0.75)),
        "中位生命周期最大回撤": round_or_none(median([num(row.get("生命周期最大回撤")) for row in evaluable])),
        "中位持有天数": round_or_none(median([num(row.get("生命周期天数")) for row in evaluable]), 0),
        "覆盖机构数": len({raw(row.get("投顾机构")) for row in periods if raw(row.get("投顾机构"))}),
    }


def build_pack(db_path: Path = DEFAULT_DB_PATH, site_dir: Path = DEFAULT_SITE_DIR) -> dict[str, Any]:
    summary = load_summary(site_dir)
    summary_rows = target_rows(summary)
    algorithm_version = raw((summary.get("overview") or {}).get("算法版本")) or DEFAULT_ALGORITHM_VERSION
    data_updated_to = raw((summary.get("overview") or {}).get("数据更新至")) or None
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        rows = merge_governance_target_rows(summary_rows, load_governance_target_rows(con))
        sids = [raw(row.get("统一策略ID")) for row in rows if raw(row.get("统一策略ID"))]
        curves, sources = load_nav_curves(con, sids, algorithm_version)
        strategy_meta = load_strategy_meta(con, sids)
    finally:
        con.close()
    periods, curve_pack = build_period_rows(rows, curves, sources, strategy_meta, data_updated_to)
    series_rows = aggregate_series(periods)
    pack = {
        "version": PACK_VERSION,
        "generatedAt": beijing_now_iso(),
        "algorithmVersion": algorithm_version,
        "overview": build_overview(summary, series_rows, periods),
        "analysisGuide": {
            "定位": "目标盈是按期次发行、以达到目标收益或到期退出为核心运营特征的投顾产品，分析时需要同时看系列口径和期次口径。",
            "系列口径": "同一投顾机构、同一产品名称去掉期次号、发行批次号、“天天”和版本括号后视为一个系列；期次版本仍保留在明细中，便于追溯多元版、全球版、年中版等差异。",
            "期次口径": "每一期保留独立成立日、运行状态、净值曲线、生命周期收益和最大回撤，用于核验真实运行结果。",
            "状态口径": "明确止盈、终止、到期、期满、stopped 等字段优先；当前仍对客展示、展示口径有效或净值最近仍更新且无终止字段的期次判为运行中观察。",
            "达标口径": "如果披露状态含止盈，记为已止盈；如果能解析出目标收益率且生命周期峰值收益达到目标，记为历史收益曾达目标。没有明确目标收益率时不强行计算达标率。",
            "曲线口径": "运行中期次画到最新可得净值；已终止/期满期次裁掉终止后长期不再变化的横线尾段，只保留真实生命周期变化。",
        },
        "quality": {
            "目标盈识别来源": "沿用基础数据包中的业务分类字段，不在本页面重新改写分类。",
            "净值覆盖期次数": len([row for row in periods if raw(row.get("曲线来源")) != "缺少可用净值"]),
            "目标收益可解析期次数": len([row for row in periods if num(row.get("目标收益率")) is not None]),
            "官方披露曲线期次数": len([row for row in periods if raw(row.get("曲线来源")) == "官方披露净值"]),
            "标准回放曲线期次数": len([row for row in periods if raw(row.get("曲线来源")) == "标准回放净值"]),
        },
        "series": series_rows,
        "periods": periods,
        "curves": curve_pack,
        "advisorStats": advisor_stats(series_rows, periods),
        "riskStats": distribution(periods, "风险等级"),
        "typeStats": distribution(periods, "研报产品类型"),
        "statusStats": distribution(periods, "生命周期状态"),
    }
    return pack


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build target-profit analysis pack for basic_data site.")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--site-dir", type=Path, default=DEFAULT_SITE_DIR)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    pack = build_pack(db_path=args.db_path, site_dir=args.site_dir)
    js_assignment(args.site_dir / "data" / "target_profit_analysis_pack.js", "window.__BASIC_TARGET_PROFIT_ANALYSIS_PACK__", pack)
    print(
        json.dumps(
            {
                "输出文件": str(args.site_dir / "data" / "target_profit_analysis_pack.js"),
                "目标盈期次数": pack["overview"]["目标盈期次数"],
                "目标盈系列数": pack["overview"]["目标盈系列数"],
                "净值覆盖期次数": pack["quality"]["净值覆盖期次数"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
