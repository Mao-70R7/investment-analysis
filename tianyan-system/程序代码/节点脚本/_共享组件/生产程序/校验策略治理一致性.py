from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "outputs" / "strategy_governance_quality"
RULE_FILE = PROJECT_ROOT / "config" / "策略治理规则说明.yaml"

TABLE_STRATEGY = "策略信息"
TABLE_GOVERNANCE = "策略治理标签"
TABLE_CURRENT_HOLDING = "策略当前持仓"
TABLE_PROJECTED_HOLDING = "策略当前持仓推算补齐"
TABLE_SIGNAL_EVENT = "信号策略事件"
TABLE_SIGNAL_INSTRUCTION = "信号策略基金指令"
TABLE_REBALANCE_EVENT = "策略调仓事件"

REQUIRED_TABLES = (
    TABLE_STRATEGY,
    TABLE_GOVERNANCE,
    TABLE_CURRENT_HOLDING,
    TABLE_PROJECTED_HOLDING,
    TABLE_SIGNAL_EVENT,
    TABLE_SIGNAL_INSTRUCTION,
    TABLE_REBALANCE_EVENT,
)

C_SID = "统一策略ID"
C_CHANNEL = "渠道ID"
C_SOURCE_SID = "渠道策略ID"
C_NAME = "策略名称"
C_ADVISOR = "投顾机构"
C_TYPE = "策略类型"
C_STATUS = "策略状态"
C_DESC = "策略描述"
C_TAGS = "标签JSON"
C_GOV_STATUS = "治理状态"
C_GROUP = "分析分组"
C_IS_TEST = "是否测试组合"
C_IS_SIGNAL = "是否信号类组合"
C_IS_TARGET = "是否目标盈期次"
C_IS_STOPPED = "是否已停止"
C_INCLUDE_RANK = "是否纳入常规排名"
C_SEPARATE = "是否单独分析"
C_PERFORMANCE_DATE = "业绩分析截止日期"
C_HOLDING_METHOD = "持仓处理方式"
C_REBALANCE_METHOD = "调仓展示方式"
C_RULE_DESC = "规则说明"
C_RAW_WEIGHT_SUM = "原始持仓权重合计"
C_RAW_HOLDING_ROWS = "原始持仓行数"
C_RAW_NULL_WEIGHT_ROWS = "原始持仓空权重行数"
C_RECENT_REBALANCE_DATE = "最近调仓日期"

C_HOLDING_DATE = "持仓日期"
C_DISCLOSURE_DATE = "披露日期"
C_FUND_CODE = "基金代码"
C_FUND_NAME = "基金名称"
C_WEIGHT = "基金权重_百分比"

C_PROJECTED_DATE = "推算持仓日期"
C_PROJECTED_WEIGHT = "推算基金权重_百分比"
C_PROJECTED_SOURCE = "推算来源"
C_CONFIDENCE = "置信度"
C_AUDIT_RESULT = "稽核结论"
C_PROJECTED_REBALANCE_DATE = "最新调仓日期"

C_SIGNAL_DATE = "信号日期"
C_SIGNAL_EVENT_ID = "信号事件ID"
C_INSTRUCTION_DIRECTION = "指令方向"
C_REBALANCE_DATE = "调仓日期"
C_THIS_POSITION_DATE = "本次仓位日期"

WEIGHT_TARGET = 100.0
WEIGHT_TOLERANCE = 1.0
PERFORMANCE_STALE_DAYS = 5
HOLDING_STALE_DAYS = 5
REBALANCE_STALE_DAYS = 180
SAMPLE_LIMIT = 80

PRIMARY_STATUS_PRIORITY = (
    "测试/剔除",
    "信号服务",
    "已停止/到期",
    "目标盈期次",
    "持仓不完整",
    "常规运行",
)

TYPICAL_STRATEGIES = {
    "ttfund__M06GNPI": "药师指数趋势轮动",
    "gffunds__GFJJ000219": "超级定投家",
    "ttfund__9L7OPLP": "月宝指数",
    "ttfund__4URFCUG": "全球汉堡",
}
TYPICAL_ORDER = {sid: index for index, sid in enumerate(TYPICAL_STRATEGIES)}

TEST_RE = re.compile(r"测试|test|内部测试|演示", re.I)
STOPPED_RE = re.compile(r"stopped|终止|停止|下架|到期|期满|已止盈|清盘|结束|暂停|非对客|隐藏", re.I)
SIGNAL_RE = re.compile(
    r"信号类|信号服务|按份|份数|债市信号指导|买入信号|卖出信号|建议信号|止盈信号|买卖全程信号|"
    r"超级定投家|指数100份|智能发车|滚动带投|发车带投|带你买卖"
)


def is_target_profit_text(text: str) -> bool:
    normalized = clean(text)
    if not normalized:
        return False
    strong_brand = re.search(r"目标盈|小目标|小赢家|步步高|小星愿|小盈加|智盈|智慧目标投|小常乐|常乐", normalized)
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="只读校验策略治理/持仓闭合一致性，并输出 JSON/Markdown 报告。")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="分析库路径，默认 data/analysis_zh_current.sqlite")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="报告输出目录")
    parser.add_argument(
        "--strict-exit-code",
        action="store_true",
        help="存在治理一致性问题时返回 1；默认仅生成报告并返回 0。",
    )
    return parser.parse_args()


def quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def qcol(alias: str, column: str) -> str:
    return f"{alias}.{quote_identifier(column)}"


def clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
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


def to_int(value: Any, default: int = 0) -> int:
    number = to_float(value)
    if number is None:
        return default
    return int(number)


def round_or_none(value: Any, digits: int = 6) -> float | None:
    number = to_float(value)
    if number is None or not math.isfinite(number):
        return None
    return round(number, digits)


def parse_date(value: Any) -> date | None:
    text = clean(value)
    if not text:
        return None
    match = re.search(r"([12]\d{3})[-/.]?([01]\d)[-/.]?([0-3]\d)", text)
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def date_text(value: Any) -> str:
    parsed = parse_date(value)
    return parsed.isoformat() if parsed else clean(value)[:10]


def days_lag(reference: date | None, value: Any) -> int | None:
    parsed = parse_date(value)
    if not reference or not parsed:
        return None
    return (reference - parsed).days


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def connect_readonly(db_path: Path) -> sqlite3.Connection:
    resolved = db_path.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"数据库不存在: {resolved}")
    conn = sqlite3.connect(f"{resolved.as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def fetch_all(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def validate_required_tables(conn: sqlite3.Connection) -> list[str]:
    return [table for table in REQUIRED_TABLES if not table_exists(conn, table)]


def index_rows_by_strategy(rows: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    by_sid: dict[str, dict[str, Any]] = {}
    duplicates: list[dict[str, Any]] = []
    seen = Counter(clean(row.get(C_SID)) for row in rows if clean(row.get(C_SID)))
    for row in rows:
        sid = clean(row.get(C_SID))
        if not sid:
            continue
        if sid not in by_sid:
            by_sid[sid] = row
        if seen[sid] > 1:
            duplicates.append(row)
    return by_sid, duplicates


def load_strategy_rows(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    rows = fetch_all(conn, f"SELECT * FROM {quote_identifier(TABLE_STRATEGY)}")
    by_sid, _ = index_rows_by_strategy(rows)
    return by_sid


def load_governance_rows(conn: sqlite3.Connection) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    rows = fetch_all(conn, f"SELECT * FROM {quote_identifier(TABLE_GOVERNANCE)}")
    return index_rows_by_strategy(rows)


def load_current_holding_stats(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    sql = f"""
    WITH latest AS (
        SELECT {quote_identifier(C_SID)} AS strategy_id,
               MAX({quote_identifier(C_HOLDING_DATE)}) AS latest_holding_date
        FROM {quote_identifier(TABLE_CURRENT_HOLDING)}
        GROUP BY {quote_identifier(C_SID)}
    )
    SELECT {qcol("h", C_SID)} AS strategy_id,
           MAX({qcol("h", C_CHANNEL)}) AS channel_id,
           MAX({qcol("h", C_SOURCE_SID)}) AS channel_strategy_id,
           latest.latest_holding_date AS latest_date,
           MAX({qcol("h", C_DISCLOSURE_DATE)}) AS latest_disclosure_date,
           COUNT(*) AS row_count,
           COUNT(DISTINCT COALESCE(NULLIF(TRIM({qcol("h", C_FUND_CODE)}), ''), {qcol("h", C_FUND_NAME)})) AS fund_count,
           SUM(CASE WHEN {qcol("h", C_WEIGHT)} IS NOT NULL THEN 1 ELSE 0 END) AS weighted_row_count,
           SUM(CASE WHEN {qcol("h", C_WEIGHT)} IS NULL THEN 1 ELSE 0 END) AS null_weight_row_count,
           SUM(CASE WHEN COALESCE({qcol("h", C_WEIGHT)}, 0) > 0 THEN 1 ELSE 0 END) AS positive_weight_row_count,
           SUM(COALESCE({qcol("h", C_WEIGHT)}, 0)) AS weight_sum
    FROM {quote_identifier(TABLE_CURRENT_HOLDING)} h
    JOIN latest
      ON latest.strategy_id = {qcol("h", C_SID)}
     AND latest.latest_holding_date = {qcol("h", C_HOLDING_DATE)}
    GROUP BY {qcol("h", C_SID)}, latest.latest_holding_date
    """
    return {clean(row["strategy_id"]): row for row in fetch_all(conn, sql)}


def load_projected_holding_stats(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    sql = f"""
    WITH latest AS (
        SELECT {quote_identifier(C_SID)} AS strategy_id,
               MAX({quote_identifier(C_PROJECTED_DATE)}) AS latest_projected_date
        FROM {quote_identifier(TABLE_PROJECTED_HOLDING)}
        GROUP BY {quote_identifier(C_SID)}
    )
    SELECT {qcol("h", C_SID)} AS strategy_id,
           MAX({qcol("h", C_CHANNEL)}) AS channel_id,
           MAX({qcol("h", C_SOURCE_SID)}) AS channel_strategy_id,
           latest.latest_projected_date AS latest_date,
           COUNT(*) AS row_count,
           COUNT(DISTINCT {qcol("h", C_FUND_CODE)}) AS fund_count,
           COUNT(*) AS weighted_row_count,
           0 AS null_weight_row_count,
           SUM(CASE WHEN COALESCE({qcol("h", C_PROJECTED_WEIGHT)}, 0) > 0 THEN 1 ELSE 0 END) AS positive_weight_row_count,
           SUM(COALESCE({qcol("h", C_PROJECTED_WEIGHT)}, 0)) AS weight_sum,
           MAX({qcol("h", C_PROJECTED_SOURCE)}) AS projected_source,
           MAX({qcol("h", C_CONFIDENCE)}) AS confidence,
           MAX({qcol("h", C_AUDIT_RESULT)}) AS audit_result,
           MAX({qcol("h", C_PROJECTED_REBALANCE_DATE)}) AS latest_rebalance_date
    FROM {quote_identifier(TABLE_PROJECTED_HOLDING)} h
    JOIN latest
      ON latest.strategy_id = {qcol("h", C_SID)}
     AND latest.latest_projected_date = {qcol("h", C_PROJECTED_DATE)}
    GROUP BY {qcol("h", C_SID)}, latest.latest_projected_date
    """
    return {clean(row["strategy_id"]): row for row in fetch_all(conn, sql)}


def load_signal_stats(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    event_rows = fetch_all(
        conn,
        f"""
        SELECT {quote_identifier(C_SID)} AS strategy_id,
               COUNT(*) AS signal_event_count,
               MAX({quote_identifier(C_SIGNAL_DATE)}) AS latest_signal_date
        FROM {quote_identifier(TABLE_SIGNAL_EVENT)}
        GROUP BY {quote_identifier(C_SID)}
        """,
    )
    instruction_rows = fetch_all(
        conn,
        f"""
        SELECT {quote_identifier(C_SID)} AS strategy_id,
               COUNT(*) AS signal_instruction_count,
               SUM(CASE WHEN {quote_identifier(C_INSTRUCTION_DIRECTION)} LIKE '%买%' THEN 1 ELSE 0 END) AS buy_instruction_count,
               SUM(CASE WHEN {quote_identifier(C_INSTRUCTION_DIRECTION)} LIKE '%卖%' THEN 1 ELSE 0 END) AS sell_instruction_count,
               SUM(CASE WHEN {quote_identifier(C_INSTRUCTION_DIRECTION)} LIKE '%加%' THEN 1 ELSE 0 END) AS add_instruction_count,
               SUM(CASE WHEN {quote_identifier(C_INSTRUCTION_DIRECTION)} LIKE '%减%' THEN 1 ELSE 0 END) AS reduce_instruction_count
        FROM {quote_identifier(TABLE_SIGNAL_INSTRUCTION)}
        GROUP BY {quote_identifier(C_SID)}
        """,
    )
    stats: dict[str, dict[str, Any]] = {}
    for row in event_rows:
        sid = clean(row.get("strategy_id"))
        stats.setdefault(sid, {}).update(row)
    for row in instruction_rows:
        sid = clean(row.get("strategy_id"))
        stats.setdefault(sid, {}).update(row)
    return stats


def load_rebalance_stats(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    rows = fetch_all(
        conn,
        f"""
        SELECT {quote_identifier(C_SID)} AS strategy_id,
               COUNT(*) AS rebalance_event_count,
               MAX({quote_identifier(C_REBALANCE_DATE)}) AS latest_rebalance_date,
               MAX({quote_identifier(C_THIS_POSITION_DATE)}) AS latest_position_date
        FROM {quote_identifier(TABLE_REBALANCE_EVENT)}
        GROUP BY {quote_identifier(C_SID)}
        """,
    )
    return {clean(row["strategy_id"]): row for row in rows}


def holding_closure(stats: dict[str, Any] | None, source: str) -> dict[str, Any]:
    if not stats:
        return {
            "source": source,
            "status": "缺失",
            "is_closed": False,
            "is_comparable": False,
            "reason": "未找到持仓记录",
        }
    row_count = to_int(stats.get("row_count"))
    weighted_rows = to_int(stats.get("weighted_row_count"))
    positive_rows = to_int(stats.get("positive_weight_row_count"))
    weight_sum = to_float(stats.get("weight_sum")) or 0.0
    if row_count <= 0:
        status, reason = "缺失", "未找到持仓记录"
    elif weighted_rows <= 0:
        status, reason = "无有效权重", "最新持仓存在基金行但基金权重均为空"
    elif positive_rows <= 0 or abs(weight_sum) < 1e-9:
        status, reason = "无正权重", "最新持仓基金权重合计为 0"
    elif abs(weight_sum - WEIGHT_TARGET) <= WEIGHT_TOLERANCE:
        status, reason = "闭合", "基金权重合计接近 100%"
    else:
        status, reason = "不闭合", f"基金权重合计为 {weight_sum:.6f}%，偏离 100%"
    is_closed = status == "闭合"
    return {
        "source": source,
        "status": status,
        "is_closed": is_closed,
        "is_comparable": is_closed,
        "reason": reason,
        "date": clean(stats.get("latest_date")),
        "row_count": row_count,
        "fund_count": to_int(stats.get("fund_count")),
        "weighted_row_count": weighted_rows,
        "null_weight_row_count": to_int(stats.get("null_weight_row_count")),
        "positive_weight_row_count": positive_rows,
        "weight_sum": round(weight_sum, 6),
    }


def holding_comparability(direct: dict[str, Any], projected: dict[str, Any]) -> dict[str, Any]:
    direct_date = parse_date(direct.get("date"))
    projected_date = parse_date(projected.get("date"))
    if direct["is_closed"] and projected["is_closed"]:
        if projected_date and (not direct_date or projected_date >= direct_date):
            return {
                "status": "原始与推算均闭合",
                "is_comparable": True,
                "effective_source": "推算补齐持仓",
                "effective_date": projected.get("date"),
            }
        return {
            "status": "原始与推算均闭合",
            "is_comparable": True,
            "effective_source": "原始当前持仓",
            "effective_date": direct.get("date"),
        }
    if direct["is_closed"]:
        return {"status": "原始当前持仓闭合", "is_comparable": True, "effective_source": "原始当前持仓", "effective_date": direct.get("date")}
    if projected["is_closed"]:
        return {"status": "推算补齐后闭合", "is_comparable": True, "effective_source": "推算补齐持仓", "effective_date": projected.get("date")}
    if direct["status"] == "缺失" and projected["status"] == "缺失":
        reason = "原始当前持仓和推算补齐持仓均缺失"
    else:
        reason = f"原始当前持仓{direct['status']}；推算补齐持仓{projected['status']}"
    return {"status": "不可比", "is_comparable": False, "effective_source": "", "effective_date": "", "reason": reason}


def join_clean(parts: list[Any]) -> str:
    return " ".join(clean(part) for part in parts if clean(part))


def strategy_keyword_text(strategy: dict[str, Any], include_description: bool = True) -> str:
    parts = [
        strategy.get(C_NAME),
        strategy.get(C_TYPE),
        strategy.get(C_STATUS),
        strategy.get(C_TAGS),
        strategy.get(C_ADVISOR),
    ]
    if include_description:
        parts.append(strategy.get(C_DESC))
    return join_clean(parts)


def governance_state_text(governance: dict[str, Any]) -> str:
    return join_clean([governance.get(C_GOV_STATUS), governance.get(C_GROUP)])


def stored_status_category(governance: dict[str, Any]) -> str:
    text = governance_state_text(governance)
    if not text:
        return "未生成"
    if "测试" in text or "剔除" in text:
        return "测试/剔除"
    if "信号" in text:
        return "信号服务"
    if "已停止" in text or "停止" in text or "到期" in text or "期满" in text or "stopped" in text.lower():
        return "已停止/到期"
    if "目标盈" in text or "期次" in text:
        return "目标盈期次"
    if "权重未完整" in text or "持仓不完整" in text or "权重缺" in text:
        return "持仓不完整"
    if "正常" in text or "常规" in text:
        return "常规运行"
    return "未识别"


def derive_primary_status(
    strategy: dict[str, Any],
    governance: dict[str, Any],
    signal: dict[str, Any],
    direct: dict[str, Any],
) -> dict[str, Any]:
    state_text = governance_state_text(governance)
    strategy_text = strategy_keyword_text(strategy)
    test_text = join_clean([strategy.get(C_NAME), strategy.get(C_STATUS), state_text])
    stopped_text = join_clean([strategy.get(C_NAME), strategy.get(C_STATUS), state_text])
    signal_text = join_clean([strategy_text, state_text])
    if governance:
        target_text = join_clean([strategy.get(C_NAME), strategy.get(C_TYPE), strategy.get(C_STATUS), strategy.get(C_TAGS), state_text])
    else:
        target_text = strategy_text

    is_test = to_int(governance.get(C_IS_TEST)) == 1 or bool(TEST_RE.search(test_text))
    is_signal = (
        to_int(governance.get(C_IS_SIGNAL)) == 1
        or to_int(signal.get("signal_event_count")) > 0
        or to_int(signal.get("signal_instruction_count")) > 0
        or bool(SIGNAL_RE.search(signal_text))
    )
    is_stopped = to_int(governance.get(C_IS_STOPPED)) == 1 or bool(STOPPED_RE.search(stopped_text))
    is_target = is_target_profit_text(target_text)
    is_holding_incomplete = (
        stored_status_category(governance) == "持仓不完整"
        or (direct.get("status") not in ("闭合", "缺失") and not is_test and not is_signal and not is_stopped)
        or (direct.get("status") in ("无有效权重", "无正权重") and not is_test and not is_signal and not is_stopped)
    )

    active: list[str] = []
    if is_test:
        active.append("测试/剔除")
    if not is_test and is_signal:
        active.append("信号服务")
    if not is_test and not is_signal and is_stopped:
        active.append("已停止/到期")
    if not is_test and not is_signal and is_target:
        active.append("目标盈期次")
    if not is_test and not is_signal and not is_stopped and is_holding_incomplete:
        active.append("持仓不完整")
    if not active:
        active.append("常规运行")

    priority_index = {status: index for index, status in enumerate(PRIMARY_STATUS_PRIORITY)}
    active = sorted(set(active), key=lambda status: priority_index.get(status, 999))
    return {
        "primary_status": active[0],
        "candidate_statuses": active,
        "is_test": is_test,
        "is_signal": is_signal,
        "is_stopped": is_stopped,
        "is_target_profit": is_target,
        "is_holding_incomplete": is_holding_incomplete,
    }


def strategy_value(sid: str, strategy: dict[str, Any], governance: dict[str, Any], column: str) -> Any:
    return strategy.get(column) if clean(strategy.get(column)) else governance.get(column)


def include_regular_rank(governance: dict[str, Any]) -> bool:
    if not governance:
        return True
    return to_int(governance.get(C_INCLUDE_RANK), default=1) == 1


def build_sample(
    sid: str,
    strategy: dict[str, Any],
    governance: dict[str, Any],
    derived: dict[str, Any],
    direct: dict[str, Any],
    projected: dict[str, Any],
    comparable: dict[str, Any],
    signal: dict[str, Any],
    rebalance: dict[str, Any],
    reason: str,
    source: str = "",
) -> dict[str, Any]:
    return {
        "统一策略ID": sid,
        "渠道ID": clean(strategy_value(sid, strategy, governance, C_CHANNEL)),
        "渠道策略ID": clean(strategy_value(sid, strategy, governance, C_SOURCE_SID)),
        "策略名称": clean(strategy_value(sid, strategy, governance, C_NAME)),
        "投顾机构": clean(strategy_value(sid, strategy, governance, C_ADVISOR)),
        "治理状态": clean(governance.get(C_GOV_STATUS)),
        "分析分组": clean(governance.get(C_GROUP)),
        "存储主状态": stored_status_category(governance),
        "优先级主状态": derived["primary_status"],
        "命中状态": "、".join(derived["candidate_statuses"]),
        "是否纳入常规排名": to_int(governance.get(C_INCLUDE_RANK), default=1),
        "原始持仓状态": direct["status"],
        "原始持仓日期": clean(direct.get("date")),
        "原始持仓权重合计": direct.get("weight_sum"),
        "原始持仓行数": direct.get("row_count"),
        "推算持仓状态": projected["status"],
        "推算持仓日期": clean(projected.get("date")),
        "推算权重合计": projected.get("weight_sum"),
        "持仓可比状态": comparable["status"],
        "业绩截止日期": date_text(governance.get(C_PERFORMANCE_DATE)),
        "最新调仓日期": date_text(rebalance.get("latest_rebalance_date") or governance.get(C_RECENT_REBALANCE_DATE)),
        "信号事件数": to_int(signal.get("signal_event_count")),
        "信号指令数": to_int(signal.get("signal_instruction_count")),
        "问题来源": source,
        "问题说明": reason,
    }


def sample_sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
    sid = clean(row.get("统一策略ID"))
    return (TYPICAL_ORDER.get(sid, 999), clean(row.get("渠道ID")), clean(row.get("策略名称")), sid)


def limited(rows: list[dict[str, Any]], limit: int = SAMPLE_LIMIT) -> list[dict[str, Any]]:
    return sorted(rows, key=sample_sort_key)[:limit]


def max_date(values: list[Any]) -> date | None:
    parsed = [item for item in (parse_date(value) for value in values) if item]
    return max(parsed) if parsed else None


def build_report(conn: sqlite3.Connection, db_path: Path) -> dict[str, Any]:
    missing_tables = validate_required_tables(conn)
    if missing_tables:
        raise RuntimeError("缺少必要表: " + "、".join(missing_tables))

    strategies = load_strategy_rows(conn)
    governance, duplicate_governance_rows = load_governance_rows(conn)
    current_holding = load_current_holding_stats(conn)
    projected_holding = load_projected_holding_stats(conn)
    signal_stats = load_signal_stats(conn)
    rebalance_stats = load_rebalance_stats(conn)

    all_sids = sorted(set(strategies) | set(governance))
    records: list[dict[str, Any]] = []
    missing_governance_samples: list[dict[str, Any]] = []
    mismatch_samples: list[dict[str, Any]] = []
    priority_resolution_samples: list[dict[str, Any]] = []
    target_profit_regular_samples: list[dict[str, Any]] = []
    signal_portfolio_samples: list[dict[str, Any]] = []
    holding_nonclosed_samples: list[dict[str, Any]] = []
    hard_not_comparable_samples: list[dict[str, Any]] = []
    requires_projection_samples: list[dict[str, Any]] = []

    status_counts = Counter()
    stored_status_counts = Counter()
    comparability_counts = Counter()

    for sid in all_sids:
        strategy = strategies.get(sid, {})
        gov = governance.get(sid, {})
        signal = signal_stats.get(sid, {})
        rebalance = rebalance_stats.get(sid, {})
        direct = holding_closure(current_holding.get(sid), "原始当前持仓")
        projected = holding_closure(projected_holding.get(sid), "推算补齐持仓")
        comparable = holding_comparability(direct, projected)
        derived = derive_primary_status(strategy, gov, signal, direct)
        stored_category = stored_status_category(gov)
        include_rank = include_regular_rank(gov)

        status_counts[derived["primary_status"]] += 1
        stored_status_counts[stored_category] += 1
        comparability_counts[comparable["status"]] += 1

        base_record = {
            "sid": sid,
            "strategy": strategy,
            "governance": gov,
            "signal": signal,
            "rebalance": rebalance,
            "direct": direct,
            "projected": projected,
            "comparable": comparable,
            "derived": derived,
            "include_rank": include_rank,
        }
        records.append(base_record)

        if not gov:
            missing_governance_samples.append(
                build_sample(sid, strategy, gov, derived, direct, projected, comparable, signal, rebalance, "策略信息存在但未生成治理标签")
            )
            continue

        if stored_category not in ("未生成", derived["primary_status"]):
            mismatch_samples.append(
                build_sample(
                    sid,
                    strategy,
                    gov,
                    derived,
                    direct,
                    projected,
                    comparable,
                    signal,
                    rebalance,
                    f"存储主状态为“{stored_category}”，按优先级应为“{derived['primary_status']}”",
                )
            )

        if len(derived["candidate_statuses"]) > 1:
            priority_resolution_samples.append(
                build_sample(
                    sid,
                    strategy,
                    gov,
                    derived,
                    direct,
                    projected,
                    comparable,
                    signal,
                    rebalance,
                    "同一策略命中多个治理信号，需要依赖主治理优先级取唯一状态",
                )
            )

        if derived["is_target_profit"] and stored_category == "常规运行" and include_rank:
            target_profit_regular_samples.append(
                build_sample(
                    sid,
                    strategy,
                    gov,
                    derived,
                    direct,
                    projected,
                    comparable,
                    signal,
                    rebalance,
                    "命中目标盈/止盈/目标收益标签，但治理状态仍为常规运行且纳入常规排名",
                )
            )

        signal_has_portfolio_holding = derived["is_signal"] and (
            direct["is_closed"]
            or projected["is_closed"]
            or (to_int(direct.get("row_count")) >= 2 and (to_float(direct.get("weight_sum")) or 0) >= 50)
        )
        if signal_has_portfolio_holding:
            signal_portfolio_samples.append(
                build_sample(
                    sid,
                    strategy,
                    gov,
                    derived,
                    direct,
                    projected,
                    comparable,
                    signal,
                    rebalance,
                    "治理为信号服务，但当前持仓/推算持仓呈现闭合或近似闭合的普通组合仓位",
                )
            )

        if direct["status"] not in ("闭合", "缺失"):
            holding_nonclosed_samples.append(
                build_sample(
                    sid,
                    strategy,
                    gov,
                    derived,
                    direct,
                    projected,
                    comparable,
                    signal,
                    rebalance,
                    direct["reason"],
                    source="原始当前持仓",
                )
            )
        if projected["status"] not in ("闭合", "缺失"):
            holding_nonclosed_samples.append(
                build_sample(
                    sid,
                    strategy,
                    gov,
                    derived,
                    direct,
                    projected,
                    comparable,
                    signal,
                    rebalance,
                    projected["reason"],
                    source="推算补齐持仓",
                )
            )

        regular_scope = include_rank and not derived["is_test"] and not derived["is_signal"] and not derived["is_stopped"]
        if regular_scope and not comparable["is_comparable"]:
            hard_not_comparable_samples.append(
                build_sample(
                    sid,
                    strategy,
                    gov,
                    derived,
                    direct,
                    projected,
                    comparable,
                    signal,
                    rebalance,
                    clean(comparable.get("reason")) or "纳入常规排名，但最新持仓无法形成可比权重",
                )
            )
        elif regular_scope and not direct["is_closed"] and projected["is_closed"]:
            requires_projection_samples.append(
                build_sample(
                    sid,
                    strategy,
                    gov,
                    derived,
                    direct,
                    projected,
                    comparable,
                    signal,
                    rebalance,
                    "纳入常规排名；原始当前持仓不可比，需要使用推算补齐持仓才能闭合比较",
                )
            )

    perf_ref = max_date([record["governance"].get(C_PERFORMANCE_DATE) for record in records])
    holding_ref = max_date(
        [record["direct"].get("date") for record in records]
        + [record["projected"].get("date") for record in records]
    )
    rebalance_ref = max_date(
        [
            record["rebalance"].get("latest_rebalance_date") or record["governance"].get(C_RECENT_REBALANCE_DATE)
            for record in records
        ]
    )

    performance_lag_samples: list[dict[str, Any]] = []
    holding_lag_samples: list[dict[str, Any]] = []
    rebalance_lag_samples: list[dict[str, Any]] = []
    for record in records:
        derived = record["derived"]
        if not record["include_rank"] or derived["is_test"] or derived["is_signal"] or derived["is_stopped"]:
            continue
        sid = record["sid"]
        strategy = record["strategy"]
        gov = record["governance"]
        direct = record["direct"]
        projected = record["projected"]
        comparable = record["comparable"]
        signal = record["signal"]
        rebalance = record["rebalance"]

        perf_date = gov.get(C_PERFORMANCE_DATE)
        perf_lag = days_lag(perf_ref, perf_date)
        if perf_ref and (perf_lag is None or perf_lag > PERFORMANCE_STALE_DAYS):
            performance_lag_samples.append(
                build_sample(
                    sid,
                    strategy,
                    gov,
                    derived,
                    direct,
                    projected,
                    comparable,
                    signal,
                    rebalance,
                    f"业绩日期 {date_text(perf_date) or '缺失'} 落后参照日 {perf_ref.isoformat()} 超过 {PERFORMANCE_STALE_DAYS} 天",
                    source="业绩日期",
                )
            )

        effective_holding_date = comparable.get("effective_date") or direct.get("date") or projected.get("date")
        holding_lag = days_lag(holding_ref, effective_holding_date)
        if holding_ref and (holding_lag is None or holding_lag > HOLDING_STALE_DAYS):
            holding_lag_samples.append(
                build_sample(
                    sid,
                    strategy,
                    gov,
                    derived,
                    direct,
                    projected,
                    comparable,
                    signal,
                    rebalance,
                    f"持仓日期 {date_text(effective_holding_date) or '缺失'} 落后参照日 {holding_ref.isoformat()} 超过 {HOLDING_STALE_DAYS} 天",
                    source="持仓日期",
                )
            )

        rebalance_date = rebalance.get("latest_rebalance_date") or gov.get(C_RECENT_REBALANCE_DATE)
        rebalance_lag = days_lag(rebalance_ref, rebalance_date)
        if rebalance_ref and (rebalance_lag is None or rebalance_lag > REBALANCE_STALE_DAYS):
            rebalance_lag_samples.append(
                build_sample(
                    sid,
                    strategy,
                    gov,
                    derived,
                    direct,
                    projected,
                    comparable,
                    signal,
                    rebalance,
                    f"调仓日期 {date_text(rebalance_date) or '缺失'} 落后参照日 {rebalance_ref.isoformat()} 超过 {REBALANCE_STALE_DAYS} 天",
                    source="调仓日期",
                )
            )

    typical_details: list[dict[str, Any]] = []
    record_by_sid = {record["sid"]: record for record in records}
    for sid, expected_name in TYPICAL_STRATEGIES.items():
        record = record_by_sid.get(sid)
        if not record:
            typical_details.append({"统一策略ID": sid, "预期策略名称": expected_name, "覆盖状态": "当前库未找到"})
            continue
        typical_details.append(
            build_sample(
                sid,
                record["strategy"],
                record["governance"],
                record["derived"],
                record["direct"],
                record["projected"],
                record["comparable"],
                record["signal"],
                record["rebalance"],
                "典型策略治理明细",
            )
        )

    issue_counts = {
        "缺失治理标签策略数": len(missing_governance_samples),
        "治理标签重复行数": len(duplicate_governance_rows),
        "存储主状态与优先级不一致策略数": len(mismatch_samples),
        "目标盈标签但仍常规运行策略数": len(target_profit_regular_samples),
        "信号类策略显示普通组合仓位策略数": len(signal_portfolio_samples),
        "最新持仓权重不闭合记录数": len(holding_nonclosed_samples),
        "纳入常规排名但持仓硬不可比策略数": len(hard_not_comparable_samples),
        "纳入常规排名且需推算才可比策略数": len(requires_projection_samples),
        "业绩日期滞后策略数": len(performance_lag_samples),
        "持仓日期滞后策略数": len(holding_lag_samples),
        "调仓日期滞后策略数": len(rebalance_lag_samples),
    }
    strict_issue_count = sum(issue_counts.values())

    return {
        "generated_at": now_iso(),
        "db_path": str(db_path.expanduser().resolve()),
        "rule_file": str(RULE_FILE),
        "read_tables": list(REQUIRED_TABLES),
        "rule_summary": {
            "primary_status_priority": list(PRIMARY_STATUS_PRIORITY),
            "weight_target_pct": WEIGHT_TARGET,
            "weight_tolerance_pct_point": WEIGHT_TOLERANCE,
            "performance_stale_days": PERFORMANCE_STALE_DAYS,
            "holding_stale_days": HOLDING_STALE_DAYS,
            "rebalance_stale_days": REBALANCE_STALE_DAYS,
        },
        "summary": {
            "strategy_rows": len(strategies),
            "governance_rows": len(governance),
            "strategy_union_count": len(all_sids),
            "primary_status_counts_by_priority": dict(status_counts),
            "stored_status_category_counts": dict(stored_status_counts),
            "holding_comparability_counts": dict(comparability_counts),
            "reference_dates": {
                "performance": perf_ref.isoformat() if perf_ref else "",
                "holding": holding_ref.isoformat() if holding_ref else "",
                "rebalance": rebalance_ref.isoformat() if rebalance_ref else "",
            },
            "issue_counts": issue_counts,
            "strict_issue_count": strict_issue_count,
        },
        "primary_status_uniqueness": {
            "is_unique_after_priority": len(duplicate_governance_rows) == 0,
            "status_priority": list(PRIMARY_STATUS_PRIORITY),
            "duplicate_governance_rows": len(duplicate_governance_rows),
            "missing_governance_rows": len(missing_governance_samples),
            "priority_resolution_needed_count": len(priority_resolution_samples),
            "stored_primary_mismatch_count": len(mismatch_samples),
            "missing_governance_samples": limited(missing_governance_samples),
            "priority_resolution_samples": limited(priority_resolution_samples),
            "stored_primary_mismatch_samples": limited(mismatch_samples),
        },
        "target_profit_regular_samples": limited(target_profit_regular_samples),
        "signal_strategy_with_portfolio_holding_samples": limited(signal_portfolio_samples),
        "latest_holding_weight_nonclosed_samples": limited(holding_nonclosed_samples),
        "regular_rank_holding_comparability": {
            "hard_not_comparable_count": len(hard_not_comparable_samples),
            "hard_not_comparable_samples": limited(hard_not_comparable_samples),
            "requires_projection_count": len(requires_projection_samples),
            "requires_projection_samples": limited(requires_projection_samples),
        },
        "stale_date_samples": {
            "performance": limited(performance_lag_samples),
            "holding": limited(holding_lag_samples),
            "rebalance": limited(rebalance_lag_samples),
        },
        "typical_strategy_details": typical_details,
    }


def md_escape(value: Any) -> str:
    text = clean(value)
    text = text.replace("|", "\\|").replace("\r", " ").replace("\n", " ")
    return text if text else "-"


def md_table(rows: list[dict[str, Any]], columns: list[tuple[str, str]], limit: int = 20) -> str:
    if not rows:
        return "无。"
    selected = rows[:limit]
    header = "| " + " | ".join(label for _, label in columns) + " |"
    divider = "| " + " | ".join("---" for _ in columns) + " |"
    body = [
        "| " + " | ".join(md_escape(row.get(key)) for key, _ in columns) + " |"
        for row in selected
    ]
    suffix = []
    if len(rows) > limit:
        suffix.append(f"\n仅展示前 {limit} 条，完整样本见 JSON。")
    return "\n".join([header, divider, *body, *suffix])


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    issue_counts = summary["issue_counts"]
    common_columns = [
        ("统一策略ID", "策略ID"),
        ("策略名称", "策略名称"),
        ("治理状态", "治理状态"),
        ("优先级主状态", "优先级主状态"),
        ("是否纳入常规排名", "常规排名"),
        ("原始持仓状态", "原始持仓"),
        ("推算持仓状态", "推算持仓"),
        ("问题说明", "问题说明"),
    ]
    date_columns = [
        ("统一策略ID", "策略ID"),
        ("策略名称", "策略名称"),
        ("治理状态", "治理状态"),
        ("业绩截止日期", "业绩日"),
        ("原始持仓日期", "原始持仓日"),
        ("推算持仓日期", "推算持仓日"),
        ("最新调仓日期", "最新调仓日"),
        ("问题说明", "问题说明"),
    ]

    lines = [
        "# 策略治理/持仓闭合一致性报告",
        "",
        f"- 生成时间：{report['generated_at']}",
        f"- 数据库：`{report['db_path']}`",
        f"- 规则说明：`{report['rule_file']}`",
        f"- 读取表：{'、'.join(report['read_tables'])}",
        "",
        "## 总览",
        "",
        f"- 策略信息行数：{summary['strategy_rows']}",
        f"- 治理标签策略数：{summary['governance_rows']}",
        f"- 主治理状态优先级：{' > '.join(report['rule_summary']['primary_status_priority'])}",
        f"- 参照日期：业绩 {summary['reference_dates']['performance'] or '-'}；持仓 {summary['reference_dates']['holding'] or '-'}；调仓 {summary['reference_dates']['rebalance'] or '-'}",
        f"- Strict 问题计数：{summary['strict_issue_count']}",
        "",
        "### 问题计数",
        "",
        md_table([{"检查项": key, "数量": value} for key, value in issue_counts.items()], [("检查项", "检查项"), ("数量", "数量")], limit=50),
        "",
        "### 优先级主状态分布",
        "",
        md_table(
            [{"主状态": key, "策略数": value} for key, value in summary["primary_status_counts_by_priority"].items()],
            [("主状态", "主状态"), ("策略数", "策略数")],
            limit=50,
        ),
        "",
        "## 主治理状态唯一性",
        "",
        f"- 按优先级是否可落到唯一主状态：{'是' if report['primary_status_uniqueness']['is_unique_after_priority'] else '否'}",
        f"- 缺失治理标签：{report['primary_status_uniqueness']['missing_governance_rows']}",
        f"- 重复治理标签行：{report['primary_status_uniqueness']['duplicate_governance_rows']}",
        f"- 命中多个治理信号、需优先级裁决：{report['primary_status_uniqueness']['priority_resolution_needed_count']}",
        f"- 存储主状态与优先级不一致：{report['primary_status_uniqueness']['stored_primary_mismatch_count']}",
        "",
        "### 存储主状态与优先级不一致样本",
        "",
        md_table(report["primary_status_uniqueness"]["stored_primary_mismatch_samples"], common_columns),
        "",
        "## 目标盈标签但仍常规运行",
        "",
        md_table(report["target_profit_regular_samples"], common_columns),
        "",
        "## 信号类策略显示普通组合仓位",
        "",
        md_table(report["signal_strategy_with_portfolio_holding_samples"], common_columns),
        "",
        "## 最新持仓权重不闭合样本",
        "",
        md_table(report["latest_holding_weight_nonclosed_samples"], common_columns),
        "",
        "## 纳入常规排名但持仓不可比",
        "",
        f"- 硬不可比：{report['regular_rank_holding_comparability']['hard_not_comparable_count']}",
        f"- 需要推算才可比：{report['regular_rank_holding_comparability']['requires_projection_count']}",
        "",
        "### 硬不可比样本",
        "",
        md_table(report["regular_rank_holding_comparability"]["hard_not_comparable_samples"], common_columns),
        "",
        "### 需要推算才可比样本",
        "",
        md_table(report["regular_rank_holding_comparability"]["requires_projection_samples"], common_columns),
        "",
        "## 策略最新业绩/持仓/调仓日期滞后",
        "",
        "### 业绩日期滞后",
        "",
        md_table(report["stale_date_samples"]["performance"], date_columns),
        "",
        "### 持仓日期滞后",
        "",
        md_table(report["stale_date_samples"]["holding"], date_columns),
        "",
        "### 调仓日期滞后",
        "",
        md_table(report["stale_date_samples"]["rebalance"], date_columns),
        "",
        "## 典型策略明细",
        "",
        md_table(report["typical_strategy_details"], common_columns + [("信号事件数", "信号事件"), ("信号指令数", "信号指令")], limit=20),
        "",
    ]
    return "\n".join(lines)


def write_outputs(report: dict[str, Any], output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "latest_report.json"
    md_path = output_dir / "latest_report.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return {"json": json_path, "markdown": md_path}


def main() -> int:
    args = parse_args()
    try:
        with connect_readonly(args.db) as conn:
            report = build_report(conn, args.db)
        paths = write_outputs(report, args.output_dir)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"JSON: {paths['json']}")
    print(f"Markdown: {paths['markdown']}")
    print(f"Strict issue count: {report['summary']['strict_issue_count']}")
    if args.strict_exit_code and report["summary"]["strict_issue_count"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
