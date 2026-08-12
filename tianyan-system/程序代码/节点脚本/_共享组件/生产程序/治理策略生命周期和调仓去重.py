from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
import re
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "strategy_lifecycle_rebalance_governance"

REFINED_CHANNELS = ("ttfund", "gffunds", "zocaifu", "gfsec_fima")
CONFIRMED_SIGNAL_STRATEGIES = {
    "ttfund__91NE3OR": "用户确认：砺远+ 是信号服务，按买入/卖出信号和份数管理，不按普通调仓组合解释。",
    "ttfund__LF94Q2M": "用户确认：中欧薪动月月投 是信号服务，按买入/卖出信号和份数管理，不按普通调仓组合解释。",
    "zocaifu__8100000905": "与天天基金中欧薪动月月投同语义，标签含债市信号指导、每月发车，按信号服务单独分析。",
    "gffunds__GFJJ000219": "名称为超级定投家，按分笔管理、低位加倍投入和止盈信号类服务单独分析。",
    "ttfund__YGKZ97T": "名称为广发超级定投家，描述含分笔管理、低位加倍投入和定制化止盈信号。",
    "gffunds__GFJJ001221": "名称为指数100份，按100份和买卖/发车信号类服务单独分析。",
    "ttfund__QOU1RYF": "名称为指数100份，描述含等额划分100份、研判买卖时点和低估发车。",
    "ttfund__IWNVGBF": "描述含总计划资金等额划分为100份、动态发车、跟投指数和带你买卖。",
    "ttfund__QZOUV3Q": "描述含等额划分100份、轮动信号和发车带投。",
    "ttfund__SIWGVYM": "描述含等额划分100份、买卖信号和发车带投。",
    "ttfund__SX1CRWN": "描述含100份分批投、买卖全程信号参考和择时发车。",
    "ttfund__LZM096U": "描述含100份资金智能巡航、发车带投和分笔发车卖出。",
    "ttfund__X5AHNCD": "描述含智能发车、滚动带投、发车止盈和买卖点，按信号服务单独分析。",
}

T_STRATEGY = "策略信息"
T_HOLDING = "策略当前持仓"
T_EVENT = "策略调仓事件"
T_DELTA = "策略调仓明细"
T_DAILY = "策略日度业绩"
T_DISCLOSED_NAV = "策略产品披露净值"
T_STANDARD_NAV = "策略标准业绩净值"
T_GOV = "策略治理标签"
T_DEDUPE = "调仓去重治理记录"

C_SID = "统一策略ID"
C_CHANNEL = "渠道ID"
C_SOURCE_SID = "渠道策略ID"
C_NAME = "策略名称"
C_ADVISOR = "投顾机构"
C_TYPE = "策略类型"
C_STATUS = "策略状态"
C_DESC = "策略描述"
C_TAGS = "标签JSON"
C_EVENT_ID = "调仓事件ID"
C_REB_DATE = "调仓日期"
C_PREV_DATE = "上次仓位日期"
C_THIS_DATE = "本次仓位日期"
C_DISCLOSE_DATE = "披露日期"
C_TITLE = "调仓标题"
C_REASON = "调仓原因"
C_SEQ = "事件序号"
C_EVENT_TIME = "事件时间"
C_PAYLOAD = "载荷类型"
C_CONFIDENCE = "置信度"
C_SOURCE_SNAPSHOT = "原始快照ID"
C_DETAIL_ID = "调仓明细ID"
C_FUND_CODE = "基金代码"
C_FUND_NAME = "基金名称"
C_GROUP = "分组名称"
C_BEFORE = "调前权重_百分比"
C_AFTER = "调后权重_百分比"
C_CHANGE = "权重变化_百分比"
C_ACTION = "调仓动作"
C_HOLD_DATE = "持仓日期"
C_WEIGHT = "基金权重_百分比"
C_TRADE_DATE = "交易日期"


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and math.isnan(value):
            return None
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    if text.endswith("%"):
        text = text[:-1]
    try:
        return float(text)
    except ValueError:
        return None


def parse_date(value: Any) -> str:
    text = clean(value)
    if not text:
        return ""
    match = re.search(r"([12]\d{3})[-/.]?([01]\d)[-/.]?([0-3]\d)", text)
    if not match:
        return text[:10]
    return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"


def norm_text(value: Any) -> str:
    return re.sub(r"\s+", " ", clean(value))


def norm_code(value: Any) -> str:
    text = clean(value)
    digits = "".join(ch for ch in text if ch.isdigit())
    if digits and len(digits) <= 6:
        return digits.zfill(6)
    return text


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def fetch_all(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        f'''
        CREATE TABLE IF NOT EXISTS "{T_GOV}" (
            "{C_SID}" TEXT PRIMARY KEY,
            "{C_CHANNEL}" TEXT,
            "{C_SOURCE_SID}" TEXT,
            "{C_NAME}" TEXT,
            "{C_ADVISOR}" TEXT,
            "治理状态" TEXT,
            "分析分组" TEXT,
            "是否测试组合" INTEGER NOT NULL DEFAULT 0,
            "是否信号类组合" INTEGER NOT NULL DEFAULT 0,
            "是否目标盈期次" INTEGER NOT NULL DEFAULT 0,
            "是否已停止" INTEGER NOT NULL DEFAULT 0,
            "是否清盘策略" INTEGER NOT NULL DEFAULT 0,
            "是否业绩停更" INTEGER NOT NULL DEFAULT 0,
            "是否缺官方业绩" INTEGER NOT NULL DEFAULT 0,
            "是否业绩异常" INTEGER NOT NULL DEFAULT 0,
            "是否纳入常规排名" INTEGER NOT NULL DEFAULT 1,
            "是否单独分析" INTEGER NOT NULL DEFAULT 0,
            "官方最新业绩日期" TEXT,
            "标准净值截止日期" TEXT,
            "业绩停更天数" INTEGER,
            "业绩分析截止日期" TEXT,
            "持仓处理方式" TEXT,
            "调仓展示方式" TEXT,
            "规则说明" TEXT,
            "原始持仓权重合计" REAL,
            "原始持仓行数" INTEGER,
            "原始持仓空权重行数" INTEGER,
            "最近调仓日期" TEXT,
            "生成时间" TEXT
        )
        '''
    )
    for column, column_type in [
        ("是否清盘策略", "INTEGER NOT NULL DEFAULT 0"),
        ("是否业绩停更", "INTEGER NOT NULL DEFAULT 0"),
        ("是否缺官方业绩", "INTEGER NOT NULL DEFAULT 0"),
        ("是否业绩异常", "INTEGER NOT NULL DEFAULT 0"),
        ("官方最新业绩日期", "TEXT"),
        ("标准净值截止日期", "TEXT"),
        ("业绩停更天数", "INTEGER"),
    ]:
        columns = {row[1] for row in conn.execute(f'PRAGMA table_info("{T_GOV}")')}
        if column not in columns:
            conn.execute(f'ALTER TABLE "{T_GOV}" ADD COLUMN "{column}" {column_type}')
    conn.execute(
        f'''
        CREATE TABLE IF NOT EXISTS "{T_DEDUPE}" (
            "治理批次ID" TEXT,
            "{C_CHANNEL}" TEXT,
            "{C_SID}" TEXT,
            "{C_NAME}" TEXT,
            "{C_REB_DATE}" TEXT,
            "{C_THIS_DATE}" TEXT,
            "{C_TITLE}" TEXT,
            "保留事件ID" TEXT,
            "删除事件ID" TEXT,
            "删除调仓明细数" INTEGER,
            "重复事件数" INTEGER,
            "保留原因" TEXT,
            "生成时间" TEXT
        )
        '''
    )


def load_holding_stats(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    if not table_exists(conn, T_HOLDING):
        return {}
    rows = fetch_all(
        conn,
        f'''
        SELECT "{C_SID}" AS sid,
               MAX("{C_HOLD_DATE}") AS latest_holding_date,
               COUNT(*) AS holding_rows,
               SUM(CASE WHEN "{C_WEIGHT}" IS NULL THEN 1 ELSE 0 END) AS null_weight_rows,
               SUM(COALESCE("{C_WEIGHT}", 0)) AS raw_weight_sum
        FROM "{T_HOLDING}"
        WHERE "{C_CHANNEL}" IN ({",".join("?" for _ in REFINED_CHANNELS)})
        GROUP BY "{C_SID}"
        ''',
        REFINED_CHANNELS,
    )
    return {row["sid"]: row for row in rows}


def load_rebalance_stats(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    if not table_exists(conn, T_EVENT):
        return {}
    rows = fetch_all(
        conn,
        f'''
        SELECT "{C_SID}" AS sid, MAX("{C_REB_DATE}") AS latest_rebalance_date, COUNT(*) AS rebalance_events
        FROM "{T_EVENT}"
        WHERE "{C_CHANNEL}" IN ({",".join("?" for _ in REFINED_CHANNELS)})
        GROUP BY "{C_SID}"
        ''',
        REFINED_CHANNELS,
    )
    return {row["sid"]: row for row in rows}


def latest_performance_dates(conn: sqlite3.Connection, tables: tuple[str, ...]) -> dict[str, str]:
    dates: dict[str, str] = {}
    for table in tables:
        if not table_exists(conn, table):
            continue
        for row in fetch_all(
            conn,
            f'''
            SELECT "{C_SID}" AS sid, MAX("{C_TRADE_DATE}") AS max_date
            FROM "{table}"
            WHERE "{C_CHANNEL}" IN ({",".join("?" for _ in REFINED_CHANNELS)})
            GROUP BY "{C_SID}"
            ''',
            REFINED_CHANNELS,
        ):
            sid = clean(row.get("sid"))
            date = parse_date(row.get("max_date"))
            if sid and date and date > dates.get(sid, ""):
                dates[sid] = date
    return dates


def invalid_performance_strategy_ids(conn: sqlite3.Connection) -> set[str]:
    if not table_exists(conn, T_DAILY):
        return set()
    rows = conn.execute(
        f'''
        SELECT DISTINCT "{C_SID}"
        FROM "{T_DAILY}"
        WHERE "单位净值" <= 0 OR ABS("日收益率_百分比") > 50
        '''
    )
    return {clean(row[0]) for row in rows if clean(row[0])}


def is_target_profit_text(text: str) -> bool:
    normalized = clean(text)
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


TARGET_PERIOD_RE = re.compile(r"第?[零〇一二三四五六七八九十百千万\d]{1,5}期|\d{6,8}$")


def normalize_target_series_name(name: str) -> str:
    text = clean(name)
    if not text or not TARGET_PERIOD_RE.search(text):
        return ""
    text = TARGET_PERIOD_RE.sub("", text)
    text = re.sub(r"目标盈\s*\d{6,8}$", "目标盈", text)
    text = re.sub(r"天天\d{1,4}", "天天", text)
    text = re.sub(r"[（）()]([^（）()]{1,16}?版)[（）()]", "", text)
    text = text.replace("年中版", "").replace("新年特供", "")
    text = re.sub(r"\s+", "", text)
    return text.strip()


def target_series_key(row: dict[str, Any]) -> str:
    base = normalize_target_series_name(clean(row.get(C_NAME)))
    if not base:
        return ""
    advisor = clean(row.get(C_ADVISOR)) or "未识别机构"
    return f"{advisor}|{base}"


def is_stopped_status(text: str) -> bool:
    return bool(re.search(r"stopped|终止|停止|下架|到期|期满|已止盈|清盘|结束|暂停|非对客|隐藏", text, re.I))


def is_signal_strategy(sid: str, text: str) -> bool:
    if sid in CONFIRMED_SIGNAL_STRATEGIES:
        return True
    if re.search(r"信号类|信号服务|按份|份数|债市信号指导|买入信号|卖出信号|建议信号|止盈信号|买卖全程信号", text):
        return True
    if re.search(r"超级定投家|指数100份", text):
        return True
    if re.search(r"智能发车|滚动带投|发车带投|带你买卖", text) and re.search(r"买卖点|买卖|卖出|止盈|信号", text):
        return True
    has_100_parts = bool(re.search(r"100份|等额划分|分笔管理|分批投", text))
    has_signal_action = bool(re.search(r"发车|买卖|带投|止盈|加倍投入|信号", text))
    return has_100_parts and has_signal_action


def build_governance_rows(
    conn: sqlite3.Connection,
    run_id: str,
    as_of_date: date | None = None,
    stale_performance_days: int = 31,
) -> list[dict[str, Any]]:
    holding_stats = load_holding_stats(conn)
    rebalance_stats = load_rebalance_stats(conn)
    official_perf_dates = latest_performance_dates(conn, (T_DAILY, T_DISCLOSED_NAV))
    standard_perf_dates = latest_performance_dates(conn, (T_STANDARD_NAV,))
    invalid_perf_ids = invalid_performance_strategy_ids(conn)
    if as_of_date is None:
        available_dates = [date.fromisoformat(value) for value in official_perf_dates.values() if value]
        as_of_date = max(available_dates) if available_dates else datetime.now().date()
    stale_cutoff = as_of_date - timedelta(days=max(1, stale_performance_days))
    placeholders = ",".join("?" for _ in REFINED_CHANNELS)
    rows = fetch_all(
        conn,
        f'''
        SELECT * FROM "{T_STRATEGY}"
        WHERE "{C_CHANNEL}" IN ({placeholders})
        ''',
        REFINED_CHANNELS,
    )
    output: list[dict[str, Any]] = []
    generated = now_iso()
    direct_target_series_keys: set[str] = set()
    for row in rows:
        sid = clean(row.get(C_SID))
        name = clean(row.get(C_NAME))
        text = " ".join(
            clean(row.get(key))
            for key in (C_NAME, C_TYPE, C_STATUS, C_DESC, C_TAGS, C_ADVISOR)
        )
        is_test = bool(re.search(r"测试|test|内部测试|演示", name, re.I))
        is_signal = False if is_test else is_signal_strategy(sid, text)
        if not is_signal and is_target_profit_text(text):
            key = target_series_key(row)
            if key:
                direct_target_series_keys.add(key)

    for row in rows:
        sid = clean(row.get(C_SID))
        name = clean(row.get(C_NAME))
        status = clean(row.get(C_STATUS))
        text = " ".join(
            clean(row.get(key))
            for key in (C_NAME, C_TYPE, C_STATUS, C_DESC, C_TAGS, C_ADVISOR)
        )
        hold = holding_stats.get(sid, {})
        reb = rebalance_stats.get(sid, {})
        raw_weight_sum = to_float(hold.get("raw_weight_sum")) or 0.0
        holding_rows = int(hold.get("holding_rows") or 0)
        null_weight_rows = int(hold.get("null_weight_rows") or 0)
        latest_reb = parse_date(reb.get("latest_rebalance_date"))
        latest_perf = official_perf_dates.get(sid, "")
        latest_standard_perf = standard_perf_dates.get(sid, "")
        latest_perf_date = date.fromisoformat(latest_perf) if latest_perf else None
        performance_stale_days = (as_of_date - latest_perf_date).days if latest_perf_date else None
        is_performance_stale = bool(latest_perf_date and latest_perf_date < stale_cutoff)
        missing_official_performance = not bool(latest_perf_date)
        has_performance_anomaly = sid in invalid_perf_ids

        is_test = bool(re.search(r"测试|test|内部测试|演示", name, re.I))
        is_signal = False if is_test else is_signal_strategy(sid, text)
        is_target = False if is_signal else is_target_profit_text(text)
        inherited_target = False
        if not is_test and not is_signal and not is_target:
            inherited_target = target_series_key(row) in direct_target_series_keys
            is_target = inherited_target
        explicit_stopped = is_stopped_status(status) or (status == "stopped")
        is_stopped = explicit_stopped or is_performance_stale
        missing_current_weights = holding_rows > 0 and raw_weight_sum < 90

        if is_test:
            state = "测试组合剔除"
            group = "测试/非正式"
            include_rank = 0
            separate = 1
            holding_method = "剔除常规分析，仅保留原始记录追溯"
            rebalance_method = "不纳入常规调仓榜单"
            rule = "策略名称命中“测试/test”等非正式组合关键词。"
        elif is_signal:
            state = "信号类策略"
            group = "信号服务"
            include_rank = 0
            separate = 1
            holding_method = "以信号清单、候选基金池和份数/买卖指令展示，不把候选基金等同为真实组合权重"
            rebalance_method = "按买入/卖出信号时间线展示，普通调仓胜率和换手率仅作参考"
            rule = CONFIRMED_SIGNAL_STRATEGIES.get(sid) or "命中信号类/按份数管理语义。"
        elif is_target and is_stopped:
            state = "已清盘目标盈期次（业绩停更）" if is_performance_stale else "已停止目标盈期次"
            group = "目标盈期次-已停止"
            include_rank = 0
            separate = 1
            holding_method = "停止后不滚动到最新持仓；保留停止/到期前最后披露状态"
            rebalance_method = "按期次生命周期复盘目标达成、回撤、到期收益，不与正常开放组合混排"
            if is_performance_stale:
                rule = f"截至{as_of_date.isoformat()}，官方业绩停在{latest_perf}，超过{stale_performance_days}天未更新，按清盘/停止策略治理。"
            else:
                rule = "同系列目标盈继承：同一投顾、同一去期次系列已有明确目标盈证据。" if inherited_target else "策略状态为 stopped/终止，且名称、标签或描述命中目标盈品牌、期次或明确目标收益/达标止盈机制。"
        elif is_target:
            state = "目标盈期次"
            group = "目标盈期次-运行中"
            include_rank = 0
            separate = 1
            holding_method = "运行中目标盈期次按生命周期复盘，不混入常规策略排名；持仓保留当前可得披露或推算口径"
            rebalance_method = "按目标盈生命周期、目标收益达成、回撤和到期/止盈边界展示，不与普通组合调仓混排"
            rule = "同系列目标盈继承：同一投顾、同一去期次系列已有明确目标盈证据。" if inherited_target else "名称、标签或描述命中目标盈品牌、期次或明确目标收益/达标止盈机制；运行中也单独进入目标盈分析。"
        elif is_stopped:
            state = "已清盘策略（业绩停更）" if is_performance_stale else "已停止策略"
            group = "已清盘/业绩停更" if is_performance_stale else "已停止-其他"
            include_rank = 0
            separate = 1
            holding_method = "停止后不滚动到最新持仓；保留停止前最后披露状态"
            rebalance_method = "从常规实时榜单剔除，进入历史运作复盘"
            rule = (
                f"截至{as_of_date.isoformat()}，官方业绩停在{latest_perf}，超过{stale_performance_days}天未更新；标准模拟净值不得替代官方业绩判断生命周期。"
                if is_performance_stale
                else "策略状态命中 stopped/终止/停止/到期等关键词。"
            )
        elif missing_official_performance:
            state = "官方业绩未披露"
            group = "业绩缺失-不排名"
            include_rank = 0
            separate = 1
            holding_method = "保留原始持仓记录，但无官方披露业绩时不生成可排名收益"
            rebalance_method = "可保留事件追溯，不进入收益和调仓质量排名"
            rule = "未取得策略日度业绩或产品披露净值；标准模拟净值不能替代官方披露业绩进入排名。"
        elif has_performance_anomaly:
            state = "官方业绩曲线异常"
            group = "业绩异常-隔离"
            include_rank = 0
            separate = 1
            holding_method = "保留原始持仓与异常业绩记录用于追溯，不生成可排名收益"
            rebalance_method = "可保留事件追溯，不进入收益和调仓质量排名"
            rule = "官方策略日度业绩存在非正净值或绝对日收益超过50%的异常点，隔离至数据源复核。"
        elif missing_current_weights:
            state = "当前基金权重未完整披露"
            group = "权重缺披露-需推算"
            include_rank = 1
            separate = 0
            holding_method = "App 未披露基金级当前占比时，优先使用最新调仓后权重并按基金复权收益滚动到最新净值日"
            rebalance_method = "仍按普通调仓事件展示，但标注当前持仓来自推算"
            rule = "原始当前持仓基金权重合计低于 90%，且非测试/信号/停止策略。"
        else:
            state = "正常运行"
            group = "常规运行"
            include_rank = 1
            separate = 0
            holding_method = "优先使用 App 直接披露基金级当前持仓；必要时用最后调仓后权重滚动补齐"
            rebalance_method = "按普通调仓事件、调仓原因、基金级变动和调仓后收益展示"
            rule = "未命中测试、信号、停止、目标盈期次或当前权重异常规则。"

        output.append(
            {
                C_SID: sid,
                C_CHANNEL: row.get(C_CHANNEL),
                C_SOURCE_SID: row.get(C_SOURCE_SID),
                C_NAME: name,
                C_ADVISOR: row.get(C_ADVISOR),
                "治理状态": state,
                "分析分组": group,
                "是否测试组合": int(is_test),
                "是否信号类组合": int(is_signal),
                "是否目标盈期次": int(is_target),
                "是否已停止": int(is_stopped),
                "是否清盘策略": int(is_performance_stale),
                "是否业绩停更": int(is_performance_stale),
                "是否缺官方业绩": int(missing_official_performance),
                "是否业绩异常": int(has_performance_anomaly),
                "是否纳入常规排名": int(include_rank),
                "是否单独分析": int(separate),
                "官方最新业绩日期": latest_perf,
                "标准净值截止日期": latest_standard_perf,
                "业绩停更天数": performance_stale_days,
                "业绩分析截止日期": latest_perf,
                "持仓处理方式": holding_method,
                "调仓展示方式": rebalance_method,
                "规则说明": rule,
                "原始持仓权重合计": round(raw_weight_sum, 6),
                "原始持仓行数": holding_rows,
                "原始持仓空权重行数": null_weight_rows,
                "最近调仓日期": latest_reb,
                "生成时间": generated,
            }
        )
    return output


def replace_governance_rows(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> None:
    conn.execute(f'DELETE FROM "{T_GOV}"')
    if not rows:
        return
    columns = list(rows[0].keys())
    sql = f'INSERT INTO "{T_GOV}" ({",".join(f"""\"{c}\"""" for c in columns)}) VALUES ({",".join("?" for _ in columns)})'
    conn.executemany(sql, [[row.get(c) for c in columns] for row in rows])


def business_event_key(row: dict[str, Any]) -> tuple[str, ...]:
    return (
        clean(row.get(C_CHANNEL)),
        clean(row.get(C_SID)),
        parse_date(row.get(C_REB_DATE)),
        parse_date(row.get(C_THIS_DATE)),
        norm_text(row.get(C_TITLE)),
        norm_text(row.get(C_REASON)),
    )


def detail_score(rows: list[dict[str, Any]]) -> tuple[float, int, int, int]:
    after_values = [to_float(row.get(C_AFTER)) or 0.0 for row in rows]
    before_values = [to_float(row.get(C_BEFORE)) or 0.0 for row in rows]
    positive_after = [value for value in after_values if value > 0]
    after_sum = sum(positive_after)
    before_sum = sum(value for value in before_values if value > 0)
    if positive_after:
        after_close = -abs(after_sum - 100.0)
    else:
        after_close = -100.0
    before_close = -abs(before_sum - 100.0) if before_sum > 0 else -100.0
    precision = 0
    for row in rows:
        for key in (C_BEFORE, C_AFTER, C_CHANGE):
            text = clean(row.get(key))
            if "." in text:
                precision += min(6, len(text.split(".", 1)[1].rstrip("0")))
    coded = sum(1 for row in rows if norm_code(row.get(C_FUND_CODE)))
    return (after_close + before_close * 0.25, len(rows), coded, precision)


def event_score(event: dict[str, Any], details: list[dict[str, Any]]) -> tuple[float, int, int, int, int, str]:
    ds = detail_score(details)
    source = clean(event.get(C_SOURCE_SNAPSHOT))
    current_cache_bonus = 1 if "strategy_adjustment_cache-" in source and "history" not in source else 0
    return (
        ds[0],
        ds[1],
        ds[2],
        ds[3],
        current_cache_bonus,
        clean(event.get(C_EVENT_ID)),
    )


def load_event_details(conn: sqlite3.Connection, event_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    if not event_ids:
        return {}
    placeholders = ",".join("?" for _ in event_ids)
    rows = fetch_all(conn, f'SELECT * FROM "{T_DELTA}" WHERE "{C_EVENT_ID}" IN ({placeholders})', tuple(event_ids))
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[clean(row.get(C_EVENT_ID))].append(row)
    return grouped


def merge_event_missing_fields(conn: sqlite3.Connection, keep: dict[str, Any], duplicates: list[dict[str, Any]]) -> None:
    updates: dict[str, Any] = {}
    for field in (C_PREV_DATE, C_THIS_DATE, C_DISCLOSE_DATE, C_TITLE, C_REASON, C_SEQ, C_EVENT_TIME, C_PAYLOAD, C_CONFIDENCE, C_SOURCE_SNAPSHOT):
        if clean(keep.get(field)):
            continue
        for item in duplicates:
            value = item.get(field)
            if clean(value):
                updates[field] = value
                break
    if not updates:
        return
    assignments = ", ".join(f'"{field}"=?' for field in updates)
    conn.execute(
        f'UPDATE "{T_EVENT}" SET {assignments} WHERE "{C_EVENT_ID}"=?',
        [*updates.values(), keep[C_EVENT_ID]],
    )


def dedupe_rebalance_events(
    conn: sqlite3.Connection,
    run_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not table_exists(conn, T_EVENT) or not table_exists(conn, T_DELTA):
        return [], []
    placeholders = ",".join("?" for _ in REFINED_CHANNELS)
    events = fetch_all(
        conn,
        f'''
        SELECT e.*, s."{C_NAME}" AS strategy_name
        FROM "{T_EVENT}" e
        LEFT JOIN "{T_STRATEGY}" s ON s."{C_SID}" = e."{C_SID}"
        WHERE e."{C_CHANNEL}" IN ({placeholders})
        ''',
        REFINED_CHANNELS,
    )
    grouped: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        key = business_event_key(event)
        if key[1] and key[2]:
            grouped[key].append(event)

    records: list[dict[str, Any]] = []
    recovery_backup: list[dict[str, Any]] = []
    generated = now_iso()
    for key, group in grouped.items():
        if len(group) <= 1:
            continue
        event_ids = [clean(row.get(C_EVENT_ID)) for row in group if clean(row.get(C_EVENT_ID))]
        detail_map = load_event_details(conn, event_ids)
        keep = max(group, key=lambda row: event_score(row, detail_map.get(clean(row.get(C_EVENT_ID)), [])))
        keep_id = clean(keep.get(C_EVENT_ID))
        losers = [row for row in group if clean(row.get(C_EVENT_ID)) != keep_id]
        loser_ids = [clean(row.get(C_EVENT_ID)) for row in losers if clean(row.get(C_EVENT_ID))]
        if not loser_ids:
            continue
        recovery_backup.append(
            {
                "businessKey": list(key),
                "keptEventBeforeMerge": keep,
                "deletedEvents": losers,
                "deletedDetailRows": [
                    detail
                    for event_id in loser_ids
                    for detail in detail_map.get(event_id, [])
                ],
            }
        )
        delete_detail_count = sum(len(detail_map.get(event_id, [])) for event_id in loser_ids)
        merge_event_missing_fields(conn, keep, losers)
        conn.execute(
            f'DELETE FROM "{T_DELTA}" WHERE "{C_EVENT_ID}" IN ({",".join("?" for _ in loser_ids)})',
            tuple(loser_ids),
        )
        conn.execute(
            f'DELETE FROM "{T_EVENT}" WHERE "{C_EVENT_ID}" IN ({",".join("?" for _ in loser_ids)})',
            tuple(loser_ids),
        )
        records.append(
            {
                "治理批次ID": run_id,
                C_CHANNEL: key[0],
                C_SID: key[1],
                C_NAME: clean(keep.get("strategy_name")),
                C_REB_DATE: key[2],
                C_THIS_DATE: key[3],
                C_TITLE: key[4],
                "保留事件ID": keep_id,
                "删除事件ID": "|".join(loser_ids),
                "删除调仓明细数": delete_detail_count,
                "重复事件数": len(group),
                "保留原因": f"score={event_score(keep, detail_map.get(keep_id, []))}; detail_rows={len(detail_map.get(keep_id, []))}",
                "生成时间": generated,
            }
        )
    if records:
        columns = list(records[0].keys())
        sql = f'INSERT INTO "{T_DEDUPE}" ({",".join(f"""\"{c}\"""" for c in columns)}) VALUES ({",".join("?" for _ in columns)})'
        conn.executemany(sql, [[row.get(c) for c in columns] for row in records])
    return records, recovery_backup


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_gzip_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.tmp")
    try:
        with gzip.open(temporary_path, "wt", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, default=str))
                handle.write("\n")
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)


def build_summary(conn: sqlite3.Connection, governance_rows: list[dict[str, Any]], dedupe_rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_state: dict[str, int] = defaultdict(int)
    for row in governance_rows:
        by_state[clean(row.get("治理状态"))] += 1
    duplicate_groups_after = 0
    if table_exists(conn, T_EVENT):
        duplicate_groups_after = int(
            conn.execute(
                f'''
                WITH g AS (
                  SELECT "{C_CHANNEL}", "{C_SID}", "{C_REB_DATE}", COALESCE("{C_THIS_DATE}", '') AS this_date,
                         COALESCE("{C_TITLE}", '') AS title, COALESCE("{C_REASON}", '') AS reason, COUNT(*) AS n
                  FROM "{T_EVENT}"
                  WHERE "{C_CHANNEL}" IN ({",".join("?" for _ in REFINED_CHANNELS)})
                  GROUP BY 1,2,3,4,5,6
                  HAVING COUNT(*) > 1
                )
                SELECT COUNT(*) FROM g
                ''',
                REFINED_CHANNELS,
            ).fetchone()[0]
            or 0
        )
    return {
        "generatedAt": now_iso(),
        "governanceRows": len(governance_rows),
        "governanceByState": dict(sorted(by_state.items())),
        "dedupeGroupsHandled": len(dedupe_rows),
        "dedupeEventsRemoved": sum(max(0, int(row.get("重复事件数") or 0) - 1) for row in dedupe_rows),
        "dedupeDeltaRowsRemoved": sum(int(row.get("删除调仓明细数") or 0) for row in dedupe_rows),
        "duplicateGroupsAfter": duplicate_groups_after,
        "refinedChannels": list(REFINED_CHANNELS),
        "signalStrategies": CONFIRMED_SIGNAL_STRATEGIES,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="治理策略生命周期标签并按业务事件键清理天天/广发调仓重复。")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--as-of-date", type=date.fromisoformat, help="生命周期判断日期，默认使用库内官方业绩最大日期。")
    parser.add_argument("--stale-performance-days", type=int, default=31)
    parser.add_argument("--governance-only", action="store_true", help="只更新策略治理标签，不执行调仓事件去重。")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_id = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z")
    output_dir = args.output_root / datetime.now().strftime("%Y-%m-%d") / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(args.db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = OFF")
        ensure_tables(conn)
        governance_rows = build_governance_rows(conn, run_id, args.as_of_date, args.stale_performance_days)
        if args.dry_run:
            dedupe_rows = []
            summary = {
                "generatedAt": now_iso(),
                "dryRun": True,
                "governanceRows": len(governance_rows),
                "sampleGovernance": governance_rows[:20],
            }
        else:
            replace_governance_rows(conn, governance_rows)
            if args.governance_only:
                dedupe_rows, recovery_backup = [], []
            else:
                dedupe_rows, recovery_backup = dedupe_rebalance_events(conn, run_id)
            recovery_backup_path: Path | None = None
            if recovery_backup:
                recovery_backup_path = output_dir / "调仓去重删除行恢复备份.jsonl.gz"
                # The compact table-level backup is written before commit. If
                # this write fails, the SQLite context rolls the deletions back.
                write_gzip_jsonl(recovery_backup_path, recovery_backup)
            conn.commit()
            summary = build_summary(conn, governance_rows, dedupe_rows)
            summary.update(
                {
                    "dedupeRecoveryBackup": str(recovery_backup_path) if recovery_backup_path else None,
                    "dedupeRecoveryBackupBytes": (
                        recovery_backup_path.stat().st_size if recovery_backup_path else 0
                    ),
                }
            )
        conn.execute("PRAGMA foreign_keys = ON")
    write_csv(output_dir / "策略治理标签.csv", governance_rows)
    write_csv(output_dir / "调仓去重治理记录.csv", dedupe_rows)
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"状态": "dry_run" if args.dry_run else "ok", "输出目录": str(output_dir), **summary}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
