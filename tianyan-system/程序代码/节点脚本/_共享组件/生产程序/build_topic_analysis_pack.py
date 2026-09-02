from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from analyze_ai_core_exposure import (
    DB_PATH,
    load_assigned_js,
    load_fund_reference_index,
    load_strategy_nav,
    make_equal_weight_index,
    num,
    normalized_series,
    parse_date,
    raw,
    strategy_rows,
    time_weighted_stats,
)


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_SITE_DIR = PROJECT_ROOT / "site" / "basic_data"
AI_CORE_ENTITY_KEY = "ai_core"
MAX_POINTS_PER_THEME = 1200
DEFAULT_MAX_TREND_STRATEGIES = 140
AI_REFERENCE_INDICES = [
    {
        "code": "000998.SH",
        "name": "中证TMT",
        "role": "A股宽科技参考",
        "reason": "覆盖电子、计算机、通信和传媒等板块，适合观察AI相关策略是否受A股科技主线带动；它不是AI核心筛选条件。",
    },
    {
        "code": "000993.CSI",
        "name": "中证全指信息",
        "role": "信息技术行业参考",
        "reason": "更聚焦信息技术行业，可辅助观察计算机、软件、通信设备等方向的市场环境。",
    },
    {
        "code": "H30318.CSI",
        "name": "TMT150",
        "role": "TMT龙头参考",
        "reason": "代表TMT板块中规模和流动性较好的标的，可与中证TMT形成宽窄口径对照。",
    },
    {
        "code": "000698.SH",
        "name": "科创100",
        "role": "科创成长参考",
        "reason": "科创板中成长属性较强，和半导体、AI硬件、创新科技方向的市场表现关联度较高。",
    },
    {
        "code": "NDX.GI",
        "name": "纳斯达克100",
        "role": "海外科技参考",
        "reason": "海外大型科技股集中度高，可作为全球AI科技风险偏好的参考，不代表国内AI核心暴露。",
    },
]

REFERENCE_INDEX_CATALOG = {
    "中证TMT": {
        "code": "000998.SH",
        "name": "中证TMT",
        "role": "A股宽科技参考",
        "reason": "覆盖电子、计算机、通信和传媒等板块，适合观察宽科技和AI相关主题的市场背景；它不是主题筛选条件。",
    },
    "中证全指信息": {
        "code": "000993.CSI",
        "name": "中证全指信息",
        "role": "信息技术行业参考",
        "reason": "更聚焦信息技术行业，可辅助观察计算机、软件、通信设备等方向的市场环境。",
    },
    "TMT150": {
        "code": "H30318.CSI",
        "name": "TMT150",
        "role": "TMT龙头参考",
        "reason": "代表TMT板块中规模和流动性较好的标的，可与宽科技主题形成对照。",
    },
    "科创100": {
        "code": "000698.SH",
        "name": "科创100",
        "role": "科创成长参考",
        "reason": "科创板成长属性较强，可作为硬科技、半导体和成长主题的市场背景线。",
    },
    "创业板指": {
        "code": "399006.SZ",
        "name": "创业板指",
        "role": "成长风格参考",
        "reason": "创业板成长属性较强，可辅助观察成长、医药和新能源等主题的市场环境。",
    },
    "纳斯达克100": {
        "code": "NDX.GI",
        "name": "纳斯达克100",
        "role": "海外科技参考",
        "reason": "海外大型科技股集中度高，可作为全球科技和美股成长风险偏好的参考。",
    },
    "标普500": {
        "code": "SPX.GI",
        "name": "标普500",
        "role": "美股宽基参考",
        "reason": "代表美国大盘股票整体表现，可与纳指形成宽窄口径对照。",
    },
    "恒生指数": {
        "code": "HSI.HI",
        "name": "恒生指数",
        "role": "港股宽基参考",
        "reason": "代表港股大盘市场表现，可作为港股主题的基础对照。",
    },
    "沪深300": {
        "code": "000300.SH",
        "name": "沪深300",
        "role": "A股大盘参考",
        "reason": "代表A股大盘核心资产，用于观察主题策略相对A股大盘的表现差异。",
    },
    "中证500": {
        "code": "000905.SH",
        "name": "中证500",
        "role": "A股中盘参考",
        "reason": "代表A股中盘股票，可辅助观察中小盘成长主题的市场背景。",
    },
    "中证新能源": {
        "code": "000941.SH",
        "name": "中证新能源",
        "role": "新能源参考",
        "reason": "覆盖新能源产业链相关上市公司，可作为新能源主题策略的市场对照。",
    },
    "中证医药卫生": {
        "code": "000933.SH",
        "name": "中证医药卫生",
        "role": "医药行业参考",
        "reason": "代表医药卫生行业整体表现，可作为医药健康主题的市场对照。",
    },
    "中证内地消费主题": {
        "code": "000942.SH",
        "name": "中证内地消费主题",
        "role": "消费主题参考",
        "reason": "覆盖主要消费行业，可作为消费主题策略的市场对照。",
    },
    "中证红利": {
        "code": "000922.CSI",
        "name": "中证红利",
        "role": "红利风格参考",
        "reason": "代表高股息红利风格，可用于观察红利策略是否受红利因子带动。",
    },
    "上证红利": {
        "code": "000015.SH",
        "name": "上证红利",
        "role": "红利风格参考",
        "reason": "代表上证市场中高股息股票，可与中证红利形成对照。",
    },
    "上海黄金Au99.99": {
        "code": "AU9999.SGE",
        "name": "上海黄金Au99.99",
        "role": "黄金现货参考",
        "reason": "反映国内黄金现货价格变化，可作为黄金主题策略的直接参考。",
    },
    "中证短债": {
        "code": "H11015.CSI",
        "name": "中证短债",
        "role": "短债参考",
        "reason": "代表短久期债券市场表现，可作为短债/中短债策略的市场对照。",
    },
    "中证军工": {
        "code": "399967.SZ",
        "name": "中证军工",
        "role": "军工行业参考",
        "reason": "代表军工行业市场表现，可作为军工主题策略的市场对照。",
    },
}

TOPIC_CONFIGS: list[dict[str, Any]] = [
    {
        "id": "ai_core",
        "entityKey": "ai_core",
        "name": "AI核心",
        "group": "科技成长",
        "defaultThreshold": 50,
        "minFundCount": 20,
        "maxTrendStrategies": 160,
        "benchmarks": ["中证TMT", "中证全指信息", "TMT150", "科创100", "纳斯达克100"],
        "dedicated": True,
        "grain": "严格AI产业链",
        "description": "只看有明确AI、大模型、算力、光模块、半导体等证据的基金持仓，不把泛泛科技/TMT直接算作AI核心。",
    },
    {"id": "technology", "entityKey": "technology", "name": "科技宽口径", "group": "科技成长", "defaultThreshold": 30, "minFundCount": 80, "maxTrendStrategies": 140, "benchmarks": ["中证TMT", "中证全指信息", "TMT150", "科创100"], "grain": "宽科技", "description": "覆盖科技、TMT、数字经济、通信、半导体等宽科技方向，适合观察科技类配置而非单一AI主题。"},
    {"id": "semiconductor", "entityKey": "semiconductor", "name": "半导体/芯片", "group": "科技成长", "defaultThreshold": 10, "minFundCount": 20, "maxTrendStrategies": 120, "benchmarks": ["中证全指信息", "科创100", "中证TMT"], "grain": "细分行业", "description": "只统计半导体、芯片、集成电路等明确证据，不把全部电子行业自动纳入。"},
    {"id": "new_energy", "entityKey": "new_energy", "name": "新能源", "group": "产业主题", "defaultThreshold": 20, "minFundCount": 30, "maxTrendStrategies": 120, "benchmarks": ["中证新能源", "创业板指"], "grain": "产业链主题", "description": "覆盖新能源、光伏、风电、储能、锂电、新能源车和电网等明确产业链证据。"},
    {"id": "pharma", "entityKey": "pharma", "name": "医药健康", "group": "产业主题", "defaultThreshold": 20, "minFundCount": 40, "maxTrendStrategies": 120, "benchmarks": ["中证医药卫生", "创业板指"], "grain": "行业主题", "description": "覆盖医药、创新药、CXO、医疗器械和中药等医药健康方向。"},
    {"id": "consumption", "entityKey": "consumption", "name": "消费", "group": "产业主题", "defaultThreshold": 20, "minFundCount": 40, "maxTrendStrategies": 120, "benchmarks": ["中证内地消费主题", "沪深300"], "grain": "行业主题", "description": "覆盖食品饮料、白酒、家电、旅游酒店等消费方向。"},
    {"id": "military", "entityKey": "military", "name": "军工/航空航天", "group": "产业主题", "defaultThreshold": 10, "minFundCount": 20, "maxTrendStrategies": 100, "benchmarks": ["中证军工", "科创100"], "grain": "细分行业", "description": "覆盖军工、航空航天等明确行业主题证据。"},
    {"id": "dividend", "entityKey": "dividend", "name": "红利/高股息", "group": "风格策略", "defaultThreshold": 20, "minFundCount": 50, "maxTrendStrategies": 120, "benchmarks": ["中证红利", "上证红利", "沪深300"], "grain": "风格主题", "description": "覆盖红利、高股息、红利低波等明确风格证据。"},
    {"id": "central_soe", "entityKey": "central_soe", "name": "央国企/中特估", "group": "风格策略", "defaultThreshold": 15, "minFundCount": 30, "maxTrendStrategies": 120, "benchmarks": ["中证红利", "沪深300"], "grain": "风格主题", "description": "覆盖央国企、中特估等价值风格证据，适合与红利主题一起观察。"},
    {"id": "gold", "entityKey": "gold", "name": "黄金", "group": "商品资产", "defaultThreshold": 10, "minFundCount": 8, "maxTrendStrategies": 100, "benchmarks": ["上海黄金Au99.99"], "grain": "资产主题", "description": "只统计黄金ETF、黄金联接或明确黄金资产证据，不把全部商品资产自动等同黄金。"},
    {"id": "oil_gas", "entityKey": "oil_gas", "name": "石油/天然气", "group": "商品资产", "defaultThreshold": 5, "minFundCount": 8, "maxTrendStrategies": 80, "benchmarks": ["沪深300"], "grain": "行业主题", "description": "统计石油、油气、天然气、原油等明确主题证据；能源宽口径不自动等同石油。"},
    {"id": "us_equity", "entityKey": "us_equity", "name": "美股", "group": "海外资产", "defaultThreshold": 20, "minFundCount": 20, "maxTrendStrategies": 120, "benchmarks": ["标普500", "纳斯达克100"], "grain": "地域资产", "description": "覆盖美股宽口径，不等同于纳指；纳指作为美股子主题单独提供。"},
    {"id": "nasdaq100", "entityKey": "nasdaq100", "name": "纳指/纳斯达克100", "group": "海外资产", "defaultThreshold": 10, "minFundCount": 10, "maxTrendStrategies": 100, "benchmarks": ["纳斯达克100", "标普500"], "grain": "指数主题", "description": "只统计纳斯达克100相关基金或明确指数证据。"},
    {"id": "hk_equity", "entityKey": "hk_equity", "name": "港股", "group": "海外资产", "defaultThreshold": 20, "minFundCount": 30, "maxTrendStrategies": 120, "benchmarks": ["恒生指数"], "grain": "地域资产", "description": "覆盖港股宽口径，不等同于恒生科技；恒生科技作为港股子主题单独提供。"},
    {"id": "hstech", "entityKey": "hstech", "name": "恒生科技", "group": "海外资产", "defaultThreshold": 10, "minFundCount": 8, "maxTrendStrategies": 100, "benchmarks": ["恒生指数"], "grain": "指数主题", "description": "只统计恒生科技相关基金或明确指数证据。"},
    {"id": "short_bond", "entityKey": "short_bond", "name": "短债/中短债", "group": "固收资产", "defaultThreshold": 50, "minFundCount": 80, "maxTrendStrategies": 120, "benchmarks": ["中证短债"], "grain": "资产主题", "description": "统计短债、中短债、短期债等明确固收工具证据。"},
    {"id": "overseas_bond", "entityKey": "overseas_bond", "name": "海外债券", "group": "固收资产", "defaultThreshold": 20, "minFundCount": 8, "maxTrendStrategies": 100, "benchmarks": ["标普500"], "grain": "资产主题", "description": "统计海外债、亚洲债、美元债等明确海外固收证据。"},
    {"id": "convertible_bond", "entityKey": "convertible_bond", "name": "可转债", "group": "固收资产", "defaultThreshold": 5, "minFundCount": 6, "maxTrendStrategies": 80, "benchmarks": ["沪深300"], "grain": "资产主题", "description": "统计可转债、转债等明确资产证据，样本相对较小。"},
]


def load_reference_index(con: sqlite3.Connection, code: str, start: date, end: date) -> list[tuple[str, float]]:
    rows = con.execute(
        """
        select 交易日期, 收盘点位
        from 指数日度行情
        where 指数代码 = ? and 交易日期 >= ? and 交易日期 <= ?
        order by 交易日期
        """,
        (code, start.isoformat(), end.isoformat()),
    ).fetchall()
    return normalized_series([(raw(d), float(v)) for d, v in rows if num(v)])


def read_summary_from_site(site_dir: Path) -> dict[str, Any]:
    for name in ("basic_summary_core.js", "basic_summary.js"):
        path = site_dir / "data" / name
        if path.exists():
            payload = load_assigned_js(path)
            return payload.get("summary", payload)
    raise FileNotFoundError(f"missing basic summary pack under {site_dir / 'data'}")


def ai_descendant_keys(ai_pack: dict[str, Any], root_key: str = AI_CORE_ENTITY_KEY) -> set[str]:
    catalog = ai_pack.get("entityCatalog", [])
    children: dict[str, list[str]] = defaultdict(list)
    for entity in catalog:
        key = raw(entity.get("key"))
        for parent in entity.get("parentKeys") or []:
            children[raw(parent)].append(key)
    out = {root_key}
    stack = list(children.get(root_key, []))
    while stack:
        key = stack.pop()
        if not key or key in out:
            continue
        out.add(key)
        stack.extend(children.get(key, []))
    return out


def build_standard_ai_fund_map(
    ai_pack: dict[str, Any],
    root_key: str = AI_CORE_ENTITY_KEY,
) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, dict[str, Any]], dict[str, Any]]:
    """Build AI fund exposure strictly from ai_semantic_index fundEntities."""

    ai_keys = ai_descendant_keys(ai_pack, root_key)
    fund_rows = ai_pack.get("fundEntities", {}).get("rows", [])
    by_fund: dict[tuple[str, str], dict[str, Any]] = {}
    by_code: dict[str, dict[str, Any]] = {}
    child_counts: dict[str, int] = defaultdict(int)

    for row in fund_rows:
        code = raw(row[0]).strip()
        name = raw(row[1]).strip()
        entity_key = raw(row[2]).strip()
        if entity_key not in ai_keys:
            continue
        exposure = num(row[5], 0) or 0
        if exposure <= 0:
            continue
        entity_name = raw(row[3])
        source_field = raw(row[8])
        source_value = raw(row[9])
        evidence = raw(row[11])
        rule_id = raw(row[12])
        key = (code, name)
        item = by_fund.setdefault(
            key,
            {
                "code": code,
                "name": name,
                "is_core": False,
                "exposurePct": 0.0,
                "hits": [],
                "entityKeys": set(),
            },
        )
        item["entityKeys"].add(entity_key)
        hit = " | ".join(part for part in [
            f"{entity_name}{exposure:.2f}%",
            f"{source_field}:{source_value}" if source_field or source_value else "",
            evidence,
            rule_id,
        ] if part)
        if hit:
            item["hits"].append(hit)
        if entity_key == root_key:
            item["is_core"] = True
            item["exposurePct"] = max(float(item["exposurePct"]), exposure)
        else:
            child_counts[entity_key] += 1
            item["exposurePct"] = max(float(item["exposurePct"]), exposure)

    for item in by_fund.values():
        item["hits"] = sorted(set(item["hits"]))[:8]
        item["entityKeys"] = sorted(item["entityKeys"])
        if item["exposurePct"] > 0:
            item["is_core"] = True
        code = raw(item.get("code"))
        if code:
            existing = by_code.get(code)
            if not existing or float(item["exposurePct"]) > float(existing.get("exposurePct") or 0):
                by_code[code] = item

    quality = {
        "source": "ai_semantic_index.fundEntities",
        "rootEntity": root_key,
        "entityKeys": sorted(ai_keys),
        "aiFundCount": len(by_fund),
        "aiFundCodeCount": len(by_code),
        "childCounts": dict(sorted(child_counts.items())),
        "semanticIndexVersion": ai_pack.get("version"),
        "semanticIndexGeneratedAt": ai_pack.get("generatedAt"),
        "ruleVersions": sorted(set(raw(row[13]) for row in fund_rows if len(row) > 13 and raw(row[13]))),
    }
    return by_fund, by_code, quality


def standard_ai_info_for_fund(
    code: str,
    name: str,
    by_fund: dict[tuple[str, str], dict[str, Any]],
    by_code: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    exact = by_fund.get((raw(code).strip(), raw(name).strip()))
    if exact:
        return exact
    by_code_match = by_code.get(raw(code).strip())
    if by_code_match:
        return by_code_match
    return {"code": raw(code), "name": raw(name), "is_core": False, "exposurePct": 0.0, "hits": [], "entityKeys": []}


def cap_exposure_total(value: float) -> float:
    return round(max(0.0, min(100.0, value)), 6)


def normalize_fund_contributions(funds: list[dict[str, Any]], total: float) -> None:
    if total > 100 and funds:
        scale = 100.0 / total
        for fund in funds:
            fund["weight"] = round((num(fund.get("weight"), 0) or 0) * scale, 6)
    else:
        for fund in funds:
            fund["weight"] = round(num(fund.get("weight"), 0) or 0, 6)
    funds.sort(key=lambda item: item["weight"], reverse=True)


def read_current_ai_exposure_from_site(
    site_dir: Path,
    ai_fund_map: dict[tuple[str, str], dict[str, Any]],
    ai_fund_by_code: dict[str, dict[str, Any]],
) -> tuple[dict[str, float], dict[str, list[dict[str, Any]]]]:
    ai_pack = load_assigned_js(site_dir / "data" / "ai_semantic_index.js")
    fields = ai_pack.get("fields", [])
    idx = {name: i for i, name in enumerate(fields)}
    required = ["统一策略ID", "基金代码", "基金名称", "权重"]
    missing = [field for field in required if field not in idx]
    if missing:
        raise ValueError(f"ai_semantic_index.js 缺少字段：{', '.join(missing)}")
    exposure: dict[str, float] = defaultdict(float)
    funds: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in ai_pack.get("rows", []):
        sid = raw(row[idx["统一策略ID"]])
        code = raw(row[idx["基金代码"]])
        name = raw(row[idx["基金名称"]])
        weight = num(row[idx["权重"]], 0) or 0
        if not sid or weight <= 0:
            continue
        info = standard_ai_info_for_fund(code, name, ai_fund_map, ai_fund_by_code)
        if not info["is_core"]:
            continue
        entity_exposure = num(info.get("exposurePct"), 0) or 0
        contribution = weight * entity_exposure / 100
        if contribution <= 0:
            continue
        exposure[sid] += contribution
        funds[sid].append(
            {
                "code": code,
                "name": name,
                "weight": contribution,
                "holdingWeight": weight,
                "entityExposurePct": entity_exposure,
                "hits": "；".join(info.get("hits") or []),
            }
        )
    for sid in list(funds):
        normalize_fund_contributions(funds[sid], exposure.get(sid, 0.0))
        exposure[sid] = cap_exposure_total(exposure.get(sid, 0.0))
    return dict(exposure), funds


def event_exposures_from_standard_entities(
    con: sqlite3.Connection,
    ai_fund_map: dict[tuple[str, str], dict[str, Any]],
    ai_fund_by_code: dict[str, dict[str, Any]],
    start: date,
    end: date,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
    # Include events before the window so the opening state can be carried into the first day.
    lookback = start - timedelta(days=730)
    rows = con.execute(
        """
        select 调仓事件ID, 统一策略ID, 调仓日期, 基金代码, 基金名称, 调后权重_百分比
        from 策略调仓明细
        where 调仓日期 >= ? and 调仓日期 <= ?
        order by 统一策略ID, 调仓日期, 调仓事件ID
        """,
        (lookback.isoformat(), end.isoformat()),
    )
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for event_id, sid, d, code, name, after_weight in rows:
        sid = raw(sid)
        dt = parse_date(d)
        if not sid or not dt:
            continue
        weight = num(after_weight, 0) or 0
        key = (sid, dt.isoformat(), raw(event_id))
        item = grouped.setdefault(key, {"sid": sid, "date": dt, "event_id": raw(event_id), "ai_weight": 0.0, "total_after": 0.0, "funds": []})
        if weight > 0:
            item["total_after"] += weight
        info = standard_ai_info_for_fund(raw(code), raw(name), ai_fund_map, ai_fund_by_code)
        entity_exposure = num(info.get("exposurePct"), 0) or 0
        contribution = weight * entity_exposure / 100
        if weight > 0 and info["is_core"] and contribution > 0:
            item["ai_weight"] += contribution
            item["funds"].append({
                "code": raw(code),
                "name": raw(name),
                "weight": contribution,
                "holdingWeight": weight,
                "entityExposurePct": entity_exposure,
                "hits": "；".join(info.get("hits") or []),
            })

    events_by_strategy: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in grouped.values():
        normalize_fund_contributions(item["funds"], item["ai_weight"])
        item["ai_weight"] = cap_exposure_total(item["ai_weight"])
        item["total_after"] = round(item["total_after"], 6)
        events_by_strategy[item["sid"]].append(item)

    for sid in list(events_by_strategy):
        events_by_strategy[sid].sort(key=lambda x: (x["date"], x["event_id"]))

    snapshots: dict[str, list[dict[str, Any]]] = defaultdict(list)
    evidence: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for sid, events in events_by_strategy.items():
        opening = None
        for item in events:
            if item["date"] <= start:
                opening = item
            elif item["date"] <= end:
                snapshots[sid].append(item)
        if opening:
            synthetic = {**opening, "date": start, "source": f"窗口起点沿用{opening['date'].isoformat()}调仓后仓位"}
            snapshots[sid].insert(0, synthetic)
        for item in snapshots[sid]:
            if item["funds"]:
                evidence[sid].append(item)
    return snapshots, evidence


def js_assignment(path: Path, lhs: str, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = f"{lhs} = {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))};\n"
    path.write_text(text, encoding="utf-8")


def theme_script_name(theme_id: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in raw(theme_id))
    return f"topic_{safe or 'theme'}.js"


def benchmark_configs(names: list[str]) -> list[dict[str, str]]:
    return [REFERENCE_INDEX_CATALOG[name] for name in names if name in REFERENCE_INDEX_CATALOG]


def theme_logic(config: dict[str, Any], entity_quality: dict[str, Any]) -> dict[str, str]:
    name = raw(config.get("name"))
    grain = raw(config.get("grain") or "标准实体主题")
    return {
        "基金识别": f"只使用标准实体索引中能回溯到结构化分类、资产暴露、行业主题、明确指数或基金名称证据的基金；当前主题为「{name}」，粒度为「{grain}」。",
        "证据要求": "策略主题暴露必须能回溯到底层持仓基金及持仓权重；模型不参与生成基金或策略标签。",
        "排除宽口径": "相邻概念不会自动互相替代。例如美股不等同纳指，能源不等同石油，宽科技不等同AI核心。",
        "暴露计算": f"把策略每次持仓中「{name}」相关基金的仓位加总，并按最近一年持仓持续时间计算平均暴露；曾经达到高仓位的策略记录峰值暴露。",
        "数据质量": f"标准实体基金数 {entity_quality.get('aiFundCodeCount', 0)} 只；低于主题最低样本要求的实体不会进入正式主题页。",
    }


def add_ai_compat_fields(row: dict[str, Any]) -> None:
    row["AI核心均值暴露"] = row["主题均值暴露"]
    row["AI核心峰值暴露"] = row["主题峰值暴露"]
    row["当前AI核心暴露"] = row["当前主题暴露"]
    row["首次AI核心暴露日期"] = row["首次主题暴露日期"]
    row["主要AI核心基金"] = row["主要主题基金"]


def build_theme(
    con: sqlite3.Connection,
    config: dict[str, Any],
    ai_pack: dict[str, Any],
    strategies: dict[str, dict[str, Any]],
    start: date,
    end: date,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    root_key = raw(config.get("entityKey") or config.get("id"))
    default_threshold = float(config.get("defaultThreshold") or 20)
    min_fund_count = int(config.get("minFundCount") or 1)
    max_trend = int(config.get("maxTrendStrategies") or DEFAULT_MAX_TREND_STRATEGIES)
    theme_name = raw(config.get("name") or root_key)

    theme_fund_map, theme_fund_by_code, entity_quality = build_standard_ai_fund_map(ai_pack, root_key)
    if entity_quality.get("aiFundCodeCount", 0) < min_fund_count:
        return None, {
            "id": config.get("id") or root_key,
            "name": theme_name,
            "group": config.get("group") or "其他",
            "reason": f"标准实体基金数 {entity_quality.get('aiFundCodeCount', 0)} 只，低于最低样本要求 {min_fund_count} 只。",
        }

    current_exposure, current_funds = read_current_ai_exposure_from_site(Path(config["_siteDir"]), theme_fund_map, theme_fund_by_code)
    snapshots, evidence = event_exposures_from_standard_entities(con, theme_fund_map, theme_fund_by_code, start, end)

    selected: list[dict[str, Any]] = []
    points: list[dict[str, Any]] = []
    theme_fund_codes: set[str] = set()
    theme_fund_evidence: dict[str, dict[str, Any]] = {}

    for sid, strategy in strategies.items():
        year_return = num(strategy.get("近1年"))
        if year_return is None:
            continue
        stats = time_weighted_stats(snapshots.get(sid, []), start, end)
        current_value = current_exposure.get(sid, 0.0)
        peak = max(stats["peak"], current_value)
        peak_date = stats["peak_date"]
        if current_value >= peak:
            peak_date = raw(strategy.get("最新持仓日") or end.isoformat())

        evidence_funds: dict[str, dict[str, Any]] = {}
        for item in evidence.get(sid, []):
            for fund in item.get("funds", []):
                key = raw(fund.get("code")) or raw(fund.get("name"))
                if key and (key not in evidence_funds or fund["weight"] > evidence_funds[key]["weight"]):
                    evidence_funds[key] = fund
        for fund in current_funds.get(sid, []):
            key = raw(fund.get("code")) or raw(fund.get("name"))
            if key and (key not in evidence_funds or fund["weight"] > evidence_funds[key]["weight"]):
                evidence_funds[key] = fund

        top_funds = sorted(evidence_funds.values(), key=lambda x: x["weight"], reverse=True)[:8]
        for fund in top_funds:
            code = raw(fund.get("code"))
            if code:
                theme_fund_codes.add(code)
                theme_fund_evidence[code] = {
                    "基金代码": code,
                    "基金名称": raw(fund.get("name")),
                    "主题暴露": num(fund.get("entityExposurePct"), 0) or 0,
                    "命中依据": raw(fund.get("hits")),
                }

        row = {
            "统一策略ID": sid,
            "策略名称": raw(strategy.get("策略名称")),
            "投顾机构": raw(strategy.get("投顾机构")),
            "渠道": raw(strategy.get("渠道")),
            "风险等级": raw(strategy.get("风险等级")),
            "业务分类": raw(strategy.get("业务分类")),
            "研报产品类型": raw(strategy.get("研报产品类型")),
            "近1年收益": year_return,
            "近6月收益": num(strategy.get("近6月"), 0) or 0,
            "最大回撤": num(strategy.get("最大回撤"), 0) or 0,
            "夏普比率": num(strategy.get("夏普比率"), 0) or 0,
            "主题名称": theme_name,
            "主题均值暴露": stats["mean"],
            "主题峰值暴露": peak,
            "当前主题暴露": current_value,
            "筛选暴露值": max(stats["mean"], peak),
            "暴露覆盖天数": stats["days"],
            "暴露快照数": stats["points"],
            "首次主题暴露日期": stats["first_ai_date"],
            "峰值日期": peak_date,
            "主要主题基金": top_funds,
        }
        row["是否入选"] = row["主题均值暴露"] >= default_threshold or row["主题峰值暴露"] >= default_threshold
        if root_key == AI_CORE_ENTITY_KEY:
            add_ai_compat_fields(row)
        points.append(row)
        if row["是否入选"]:
            selected.append(row)

    selected.sort(key=lambda r: (r["主题均值暴露"], r["主题峰值暴露"], r["近1年收益"]), reverse=True)
    points.sort(key=lambda r: (r["是否入选"], r["主题均值暴露"], r["主题峰值暴露"], r["近1年收益"]), reverse=True)
    points = points[:MAX_POINTS_PER_THEME]

    selected_ids = [row["统一策略ID"] for row in selected]
    trend_candidates = sorted(
        [row for row in points if max(num(row.get("主题均值暴露"), 0) or 0, num(row.get("主题峰值暴露"), 0) or 0, num(row.get("当前主题暴露"), 0) or 0) > 0],
        key=lambda row: (max(num(row.get("主题均值暴露"), 0) or 0, num(row.get("主题峰值暴露"), 0) or 0, num(row.get("当前主题暴露"), 0) or 0), num(row.get("近1年收益"), 0) or 0),
        reverse=True,
    )
    trend_strategy_ids = []
    seen_trend_ids = set()
    for sid in [*selected_ids, *[row["统一策略ID"] for row in trend_candidates]]:
        if sid and sid not in seen_trend_ids:
            trend_strategy_ids.append(sid)
            seen_trend_ids.add(sid)
        if len(trend_strategy_ids) >= max_trend:
            break

    strategy_nav = load_strategy_nav(con, trend_strategy_ids, start, end)
    selected_index = make_equal_weight_index({sid: rows for sid, rows in strategy_nav.items() if sid in set(selected_ids)})
    fund_index = load_fund_reference_index(con, sorted(theme_fund_codes), start, end)

    benchmark_series: list[tuple[dict[str, str], list[tuple[str, float]]]] = []
    for benchmark in benchmark_configs(config.get("benchmarks") or []):
        series = load_reference_index(con, benchmark["code"], start, end)
        if series:
            benchmark_series.append((benchmark, series))

    trend_rows: list[dict[str, Any]] = []
    for series_name, series, series_type in [
        ("入选策略等权净值", selected_index, "策略组合"),
        (f"{theme_name}基金池等权参考", fund_index, "基金池"),
    ]:
        for d, value in series:
            trend_rows.append({"日期": d, "系列": series_name, "指数点位": round(value, 6), "类型": series_type})

    for benchmark, series in benchmark_series:
        for d, value in series:
            trend_rows.append({
                "日期": d,
                "系列": benchmark["name"],
                "指数点位": round(value, 6),
                "类型": "参考指数",
                "指数代码": benchmark["code"],
            })

    strategy_trend_rows: list[dict[str, Any]] = []
    strategy_name_by_id = {row["统一策略ID"]: row["策略名称"] for row in points}
    for sid, series in strategy_nav.items():
        strategy_name = strategy_name_by_id.get(sid)
        if not strategy_name:
            continue
        for d, value in normalized_series(series):
            strategy_trend_rows.append({
                "日期": d,
                "系列": strategy_name,
                "统一策略ID": sid,
                "指数点位": round(value, 6),
                "类型": "单只策略",
            })

    summary = {
        "入选策略数": len(selected),
        "均值达标策略数": sum(1 for row in points if row["主题均值暴露"] >= default_threshold),
        "峰值达标策略数": sum(1 for row in points if row["主题峰值暴露"] >= default_threshold),
        "当前达标策略数": sum(1 for row in points if row["当前主题暴露"] >= default_threshold),
        "点阵样本数": len(points),
        "主题基金数": len(theme_fund_codes),
        "标准实体主题基金数": entity_quality.get("aiFundCodeCount", 0),
    }
    if root_key == AI_CORE_ENTITY_KEY:
        summary["AI核心基金数"] = summary["主题基金数"]
        summary["标准实体AI基金数"] = summary["标准实体主题基金数"]

    return {
        "id": config.get("id") or root_key,
        "name": theme_name,
        "group": config.get("group") or "其他",
        "rootEntityKey": root_key,
        "rootEntityLabel": theme_name,
        "description": config.get("description") or "",
        "grain": config.get("grain") or "标准实体主题",
        "dedicated": bool(config.get("dedicated")),
        "defaultThreshold": default_threshold,
        "threshold": f"最近一年平均或峰值「{theme_name}」暴露达到{default_threshold:g}%",
        "summary": summary,
        "sourceEntityIndex": entity_quality,
        "logic": theme_logic(config, entity_quality),
        "selected": selected,
        "points": points,
        "trend": trend_rows,
        "strategyTrend": strategy_trend_rows,
        "benchmarks": [benchmark for benchmark, _series in benchmark_series],
        "fundEvidence": sorted(theme_fund_evidence.values(), key=lambda x: x["基金代码"]),
    }, None


def build_manifest(pack: dict[str, Any]) -> dict[str, Any]:
    themes = []
    for theme in pack.get("themes") or []:
        summary = theme.get("summary") or {}
        themes.append({
            "id": theme.get("id"),
            "name": theme.get("name"),
            "group": theme.get("group"),
            "rootEntityKey": theme.get("rootEntityKey"),
            "description": theme.get("description"),
            "grain": theme.get("grain"),
            "dedicated": theme.get("dedicated"),
            "defaultThreshold": theme.get("defaultThreshold"),
            "summary": summary,
            "script": f"./data/topics/{theme_script_name(theme.get('id'))}",
        })
    return {
        "version": pack.get("version"),
        "generatedAt": pack.get("generatedAt"),
        "dataUpdatedTo": pack.get("dataUpdatedTo"),
        "window": pack.get("window"),
        "themes": themes,
        "skippedThemes": pack.get("skippedThemes") or [],
    }


def write_split_topic_packs(site_dir: Path, pack: dict[str, Any]) -> None:
    data_dir = site_dir / "data"
    topic_dir = data_dir / "topics"
    topic_dir.mkdir(parents=True, exist_ok=True)
    expected = set()
    for theme in pack.get("themes") or []:
        filename = theme_script_name(theme.get("id"))
        expected.add(filename)
        text = (
            "window.__BASIC_TOPIC_ANALYSIS_THEME_PACKS__ = window.__BASIC_TOPIC_ANALYSIS_THEME_PACKS__ || {};\n"
            f"window.__BASIC_TOPIC_ANALYSIS_THEME_PACKS__[{json.dumps(theme.get('id'), ensure_ascii=False)}] = "
            f"{json.dumps(theme, ensure_ascii=False, separators=(',', ':'))};\n"
        )
        (topic_dir / filename).write_text(text, encoding="utf-8")
    for path in topic_dir.glob("topic_*.js"):
        if path.name not in expected:
            path.unlink()
    js_assignment(data_dir / "topic_analysis_manifest.js", "window.__BASIC_TOPIC_ANALYSIS_MANIFEST__", build_manifest(pack))


def build_pack(db_path: Path = DB_PATH, site_dir: Path = DEFAULT_SITE_DIR) -> dict[str, Any]:
    summary = read_summary_from_site(site_dir)
    overview = summary.get("overview", {})
    end = parse_date(overview.get("数据更新至")) or date.today()
    start = end - timedelta(days=365)

    con = sqlite3.connect(str(db_path))
    try:
        ai_pack = load_assigned_js(site_dir / "data" / "ai_semantic_index.js")
        strategies = strategy_rows(summary)
        themes: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for config in TOPIC_CONFIGS:
            theme_config = {**config, "_siteDir": str(site_dir)}
            theme, skip = build_theme(con, theme_config, ai_pack, strategies, start, end)
            if theme:
                themes.append(theme)
            if skip:
                skipped.append(skip)

        first_quality = (themes[0].get("sourceEntityIndex") if themes else {}) or {}
        return {
            "version": 3,
            "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
            "dataUpdatedTo": overview.get("数据更新至"),
            "window": {"start": start.isoformat(), "end": end.isoformat(), "days": (end - start).days + 1},
            "sourceEntityIndex": {
                "semanticIndexVersion": ai_pack.get("version"),
                "semanticIndexGeneratedAt": ai_pack.get("generatedAt"),
                "ruleVersions": first_quality.get("ruleVersions") or [],
            },
            "themes": themes,
            "skippedThemes": skipped,
        }
    finally:
        con.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="构建主题分析页面数据包。")
    parser.add_argument("--db-path", type=Path, default=DB_PATH)
    parser.add_argument("--site-dir", type=Path, default=DEFAULT_SITE_DIR)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    pack = build_pack(args.db_path, args.site_dir)
    js_assignment(args.site_dir / "data" / "topic_analysis_pack.js", "window.__BASIC_TOPIC_ANALYSIS_PACK__", pack)
    write_split_topic_packs(args.site_dir, pack)
    ai_theme = next((theme for theme in pack["themes"] if theme.get("id") == "ai_core"), pack["themes"][0] if pack["themes"] else {})
    print(json.dumps({
        "输出": str(args.site_dir / "data" / "topic_analysis_manifest.js"),
        "主题数": len(pack["themes"]),
        "跳过主题数": len(pack.get("skippedThemes") or []),
        "AI核心入选策略数": (ai_theme.get("summary") or {}).get("入选策略数"),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
