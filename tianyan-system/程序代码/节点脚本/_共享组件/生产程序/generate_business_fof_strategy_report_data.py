from __future__ import annotations

import argparse
import json
import math
import sqlite3
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from generate_fof_h1_strategy_rank_data import (
    DEFAULT_DB_PATH,
    DEFAULT_OUTPUT_ROOT,
    fetch_rankhandler_all_pages,
    parse_rank_record,
    pct_return,
    q,
    round_or_none,
    to_float,
)


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_SOURCE_JSON = DEFAULT_OUTPUT_ROOT / "20260630_recheck_20260702" / "fof_h1_strategy_ranking_data.json"
DEFAULT_REPORT_ROOT = PROJECT_ROOT / "outputs" / "fof_strategy_business_report"

INTERVALS = [
    {"key": "h1", "label": "2026上半年", "start": "2025-12-31", "end": "2026-06-30"},
    {"key": "1m", "label": "近1月", "start": "2026-05-30", "end": "2026-06-30"},
    {"key": "3m", "label": "近3月", "start": "2026-03-31", "end": "2026-06-30"},
    {"key": "6m", "label": "近6月", "start": "2025-12-31", "end": "2026-06-30"},
    {"key": "1y", "label": "近1年", "start": "2025-06-30", "end": "2026-06-30"},
]

POSITION_ORDER = ["稳健型", "均衡型", "进取型", "海外/QDII", "其他"]
ASSET_DETAIL_ORDER = ["现金/低波", "稳健型", "均衡偏债", "均衡型", "均衡偏股", "高权益/进取", "海外/QDII", "其他/需复核"]


def clean(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text in {"None", "nan"} else text


def median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def mean(values: list[float]) -> float | None:
    return statistics.mean(values) if values else None


def business_position(bucket: str) -> str:
    mapping = {
        "FOF-稳健型": "稳健型",
        "FOF-均衡型": "均衡型",
        "FOF-进取型": "进取型",
        "QDII-FOF": "海外/QDII",
        "FOF-其他": "其他",
    }
    return mapping.get(clean(bucket), clean(bucket) or "其他")


def asset_detail_bucket(row: dict[str, Any]) -> tuple[str, str]:
    qdii = to_float(row.get("QDII权重_百分比")) or 0.0
    equity = to_float(row.get("权益基金权重_百分比"))
    text = " ".join(clean(row.get(k)) for k in ["策略名称", "市场地域", "业务分类", "FOF可比分类"])
    if qdii >= 30 or "海外" in text or "全球" in text or "QDII" in text:
        return "海外/QDII", "海外资产或QDII特征较明显"
    if equity is None:
        return "其他/需复核", "缺少权益占比，暂不细分"
    if equity <= 5:
        return "现金/低波", "权益占比不超过5%"
    if equity <= 20:
        return "稳健型", "权益占比5%-20%"
    if equity <= 40:
        return "均衡偏债", "权益占比20%-40%"
    if equity <= 60:
        return "均衡型", "权益占比40%-60%"
    if equity <= 80:
        return "均衡偏股", "权益占比60%-80%"
    return "高权益/进取", "权益占比超过80%"


def special_tags(row: dict[str, Any], position: str, fof_sample_count: int | None = None) -> str:
    tags: list[str] = []
    if int(row.get("是否信号类组合") or 0) == 1:
        tags.append("信号类")
    if int(row.get("是否目标盈期次") or 0) == 1:
        tags.append("目标盈")
    if int(row.get("是否已停止") or 0) == 1:
        tags.append("已停止")
    if position == "海外/QDII":
        tags.append("海外")
    if fof_sample_count is not None and fof_sample_count < 30:
        tags.append("样本偏少")
    return "、".join(tags) if tags else "常规"


def load_strategy_interval_returns(conn: sqlite3.Connection, strategy_ids: list[str]) -> dict[str, dict[str, dict[str, Any]]]:
    table = "策略标准业绩净值"
    sid = "统一策略ID"
    trade_date = "交易日期"
    nav = "标准费后单位净值"
    result: dict[str, dict[str, dict[str, Any]]] = {strategy_id: {} for strategy_id in strategy_ids}
    id_set = set(strategy_ids)
    for interval in INTERVALS:
        start = interval["start"]
        end = interval["end"]
        start_sql = f"""
        WITH p AS (
          SELECT {q(sid)} id, MAX({q(trade_date)}) d
          FROM {q(table)}
          WHERE {q(trade_date)} <= ? AND {q(nav)} IS NOT NULL
          GROUP BY {q(sid)}
        )
        SELECT p.id, p.d, t.{q(nav)} v
        FROM p JOIN {q(table)} t ON t.{q(sid)} = p.id AND t.{q(trade_date)} = p.d
        """
        fallback_start_sql = f"""
        WITH p AS (
          SELECT {q(sid)} id, MIN({q(trade_date)}) d
          FROM {q(table)}
          WHERE {q(trade_date)} >= ? AND {q(trade_date)} <= ? AND {q(nav)} IS NOT NULL
          GROUP BY {q(sid)}
        )
        SELECT p.id, p.d, t.{q(nav)} v
        FROM p JOIN {q(table)} t ON t.{q(sid)} = p.id AND t.{q(trade_date)} = p.d
        """
        end_sql = f"""
        WITH p AS (
          SELECT {q(sid)} id, MAX({q(trade_date)}) d
          FROM {q(table)}
          WHERE {q(trade_date)} <= ? AND {q(nav)} IS NOT NULL
          GROUP BY {q(sid)}
        )
        SELECT p.id, p.d, t.{q(nav)} v
        FROM p JOIN {q(table)} t ON t.{q(sid)} = p.id AND t.{q(trade_date)} = p.d
        """
        starts = {row["id"]: dict(row) for row in conn.execute(start_sql, [start]) if row["id"] in id_set}
        fallback_starts = {row["id"]: dict(row) for row in conn.execute(fallback_start_sql, [start, end]) if row["id"] in id_set}
        ends = {row["id"]: dict(row) for row in conn.execute(end_sql, [end]) if row["id"] in id_set}
        for strategy_id in strategy_ids:
            start_row = starts.get(strategy_id) or fallback_starts.get(strategy_id)
            end_row = ends.get(strategy_id)
            ret = pct_return(to_float(start_row.get("v") if start_row else None), to_float(end_row.get("v") if end_row else None))
            result[strategy_id][interval["key"]] = {
                "return": round_or_none(ret),
                "startDate": clean(start_row.get("d") if start_row else ""),
                "endDate": clean(end_row.get("d") if end_row else ""),
            }
    return result


def fetch_fof_rank_records() -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for ft in ["fof", "qdii"]:
        datas, _meta = fetch_rankhandler_all_pages(ft, "2025-12-31", "2026-06-30", page_size=1000)
        for item in datas:
            parsed = parse_rank_record(item, ft)
            if not parsed:
                continue
            code = clean(parsed.get("基金代码"))
            if code and (code not in records or ft == "fof"):
                records[code] = parsed
    return records


def fof_interval_returns(row: dict[str, Any], rank: dict[str, Any] | None) -> dict[str, float | None]:
    values = {
        "h1": to_float((rank or {}).get("上半年区间收益率_百分比")),
        "1m": to_float((rank or {}).get("近1月收益率_百分比")),
        "3m": to_float((rank or {}).get("近3月收益率_百分比")),
        "6m": to_float((rank or {}).get("近6月收益率_百分比")),
        "1y": to_float((rank or {}).get("近1年收益率_百分比")),
    }
    if values["h1"] is None:
        values["h1"] = to_float(row.get("上半年收益率_百分比")) or to_float((rank or {}).get("今年以来收益率_百分比")) or values["6m"]
    return values


def percentile_rank(value: float | None, pool: list[float]) -> tuple[int | None, float | None, float | None, str]:
    if value is None:
        return None, None, None, "缺少收益"
    valid = [x for x in pool if x is not None and math.isfinite(x)]
    if not valid:
        return None, None, None, "缺少同类FOF样本"
    rank = 1 + sum(1 for x in valid if x > value)
    beat = sum(1 for x in valid if value > x) / len(valid)
    percentile = rank / len(valid)
    if len(valid) < 30:
        status = "样本偏少，仅供参考"
    else:
        status = "可排名"
    return rank, round_or_none(beat, 6), round_or_none(percentile, 6), status


def sort_key(value: str, order: list[str]) -> tuple[int, str]:
    return (order.index(value) if value in order else len(order), value)


def build_report_data(args: argparse.Namespace) -> dict[str, Any]:
    source = json.loads(args.source_json.read_text(encoding="utf-8"))
    strategy_rows = source["strategyRows"]
    fof_rows = source["fofRows"]

    conn = sqlite3.connect(args.db_path)
    conn.row_factory = sqlite3.Row
    try:
        strategy_returns = load_strategy_interval_returns(conn, [clean(row.get("统一策略ID")) for row in strategy_rows])
    finally:
        conn.close()

    rank_records = fetch_fof_rank_records()

    fof_enriched: list[dict[str, Any]] = []
    fof_pools: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for row in fof_rows:
        position = business_position(clean(row.get("FOF可比分类")))
        rank = rank_records.get(clean(row.get("基金代码")))
        interval_values = fof_interval_returns(row, rank)
        item = {
            "产品类型": "FOF产品",
            "产品代码": clean(row.get("基金代码")),
            "产品名称": clean(row.get("基金名称")),
            "产品定位分类": position,
            "产品公开分类": clean(row.get("天天基金细分类")) or position,
            "数据状态": "有收益" if interval_values["h1"] is not None else "缺少收益",
        }
        for interval in INTERVALS:
            value = interval_values[interval["key"]]
            item[f"{interval['label']}收益率"] = round_or_none(value)
            if value is not None:
                fof_pools[position][interval["key"]].append(value)
        fof_enriched.append(item)

    strategies_enriched: list[dict[str, Any]] = []
    for row in strategy_rows:
        sid = clean(row.get("统一策略ID"))
        position = business_position(clean(row.get("FOF可比分类")))
        asset_detail, asset_basis = asset_detail_bucket(row)
        item = {
            "产品类型": "投顾策略",
            "产品代码": sid,
            "产品名称": clean(row.get("策略名称")),
            "机构": clean(row.get("投顾机构")),
            "是否对客": clean(row.get("是否对客")) or "未识别",
            "产品定位分类": position,
            "资产细分分类": asset_detail,
            "资产细分依据": asset_basis,
            "特殊标签": special_tags(row, position),
            "分类建议": "纳入同类排名" if position not in {"其他"} else "暂不纳入同类排名",
        }
        for interval in INTERVALS:
            ret = strategy_returns.get(sid, {}).get(interval["key"], {}).get("return")
            pool = fof_pools[position][interval["key"]]
            rank, beat, percentile, status = percentile_rank(ret, pool)
            item[f"{interval['label']}收益率"] = round_or_none(ret)
            item[f"{interval['label']}同类FOF数"] = len(pool)
            item[f"{interval['label']}同类排名"] = rank
            item[f"{interval['label']}击败比例"] = beat
            item[f"{interval['label']}排名状态"] = status
        item["特殊标签"] = special_tags(row, position, item.get("2026上半年同类FOF数"))
        strategies_enriched.append(item)

    category_summary: list[dict[str, Any]] = []
    for position in sorted({row["产品定位分类"] for row in strategies_enriched + fof_enriched}, key=lambda x: sort_key(x, POSITION_ORDER)):
        strategies = [row for row in strategies_enriched if row["产品定位分类"] == position]
        client = [row for row in strategies if row["是否对客"] == "是"]
        funds = [row for row in fof_enriched if row["产品定位分类"] == position]
        h1_strategy_returns = [to_float(row.get("2026上半年收益率")) for row in strategies if to_float(row.get("2026上半年收益率")) is not None]
        h1_fund_returns = [to_float(row.get("2026上半年收益率")) for row in funds if to_float(row.get("2026上半年收益率")) is not None]
        category_summary.append(
            {
                "产品定位分类": position,
                "投顾策略数": len(strategies),
                "对客策略数": len(client),
                "FOF产品数": len(funds),
                "有收益FOF数": len(h1_fund_returns),
                "2026上半年策略中位收益": round_or_none(median(h1_strategy_returns)),
                "2026上半年FOF中位收益": round_or_none(median(h1_fund_returns)),
                "样本判断": "适合排名" if len(h1_fund_returns) >= 30 else "样本偏少，仅供参考",
            }
        )

    interval_summary: list[dict[str, Any]] = []
    for interval in INTERVALS:
        label = interval["label"]
        for position in POSITION_ORDER:
            strategies = [row for row in strategies_enriched if row["产品定位分类"] == position]
            funds = [row for row in fof_enriched if row["产品定位分类"] == position]
            strategy_values = [to_float(row.get(f"{label}收益率")) for row in strategies if to_float(row.get(f"{label}收益率")) is not None]
            fund_values = [to_float(row.get(f"{label}收益率")) for row in funds if to_float(row.get(f"{label}收益率")) is not None]
            beats = [to_float(row.get(f"{label}击败比例")) for row in strategies if to_float(row.get(f"{label}击败比例")) is not None]
            interval_summary.append(
                {
                    "收益区间": label,
                    "产品定位分类": position,
                    "策略数量": len(strategy_values),
                    "FOF数量": len(fund_values),
                    "策略中位收益": round_or_none(median(strategy_values)),
                    "FOF中位收益": round_or_none(median(fund_values)),
                    "平均击败比例": round_or_none(mean(beats), 6),
                    "样本判断": "适合排名" if len(fund_values) >= 30 else "样本偏少，仅供参考",
                }
            )

    cross_counter: Counter[tuple[str, str]] = Counter()
    for row in strategies_enriched:
        cross_counter[(row["产品定位分类"], row["资产细分分类"])] += 1
    classification_matrix = []
    for position in POSITION_ORDER:
        matrix_row = {"产品定位分类": position}
        for detail in ASSET_DETAIL_ORDER:
            matrix_row[detail] = cross_counter.get((position, detail), 0)
        classification_matrix.append(matrix_row)

    top_rank_rows = sorted(
        [
            row
            for row in strategies_enriched
            if to_float(row.get("2026上半年击败比例")) is not None and row.get("2026上半年排名状态") == "可排名"
        ],
        key=lambda row: (-(to_float(row.get("2026上半年击败比例")) or -1), -(to_float(row.get("2026上半年收益率")) or -999)),
    )[:40]

    return {
        "meta": {
            "报告名称": "2026年上半年投顾策略与FOF多口径对比报告",
            "生成时间": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "统计截止日": "2026-06-30",
            "投顾策略数": len(strategies_enriched),
            "对客策略数": sum(1 for row in strategies_enriched if row["是否对客"] == "是"),
            "FOF产品数": len(fof_enriched),
            "有收益FOF数": sum(1 for row in fof_enriched if row["数据状态"] == "有收益"),
            "主排名口径": "产品定位可比口径",
            "主排名说明": "投顾策略与FOF产品先归入稳健、均衡、进取、海外等可比组，再在同组内比较收益表现。",
        },
        "intervals": INTERVALS,
        "categorySummary": category_summary,
        "intervalSummary": interval_summary,
        "classificationMatrix": classification_matrix,
        "strategyRows": strategies_enriched,
        "fofRows": fof_enriched,
        "topRankRows": top_rank_rows,
        "businessNotes": [
            {"主题": "主排名口径", "说明": "使用产品定位可比口径。它适合回答同一类产品中，投顾策略相对全市场FOF处在什么位置。"},
            {"主题": "辅助解释口径", "说明": "资产细分分类用于解释投顾策略自身的权益暴露高低，不单独作为FOF全市场排名口径。"},
            {"主题": "样本边界", "说明": "海外/QDII组的FOF收益样本偏少，排名只作为参考，不建议作为强结论。"},
            {"主题": "多区间解读", "说明": "单一区间收益容易受市场阶段影响，报告同时展示近1月、近3月、近6月、近1年和上半年表现。"},
        ],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate business-facing multi-classification FOF strategy report data.")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--source-json", type=Path, default=DEFAULT_SOURCE_JSON)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_REPORT_ROOT)
    parser.add_argument("--run-id", default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_id = args.run_id or datetime.now().strftime("%Y%m%dT%H%M%S")
    output_dir = args.output_root / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    data = build_report_data(args)
    data["meta"]["runId"] = run_id
    output_path = output_dir / "fof_strategy_business_report_data.json"
    output_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    latest_path = args.output_root / "latest_fof_strategy_business_report_data.json"
    latest_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output_path), "latest": str(latest_path), **data["meta"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
