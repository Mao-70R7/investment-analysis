from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import statistics
import time
from collections import Counter
from datetime import datetime, timezone
from http.client import IncompleteRead
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
DEFAULT_SUMMARY_JS = PROJECT_ROOT / "site" / "basic_data" / "data" / "basic_summary.js"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "fof_h1_strategy_ranking"
RANKHANDLER_URL = "https://fund.eastmoney.com/data/rankhandler.aspx"


def q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def clean(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text in {"None", "nan"} else text


def to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if math.isfinite(float(value)):
            return float(value)
        return None
    text = str(value).strip().replace(",", "").replace("%", "")
    if not text or text in {"--", "-"}:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def round_or_none(value: float | None, digits: int = 4) -> float | None:
    return round(value, digits) if value is not None and math.isfinite(value) else None


def pct_return(start_value: float | None, end_value: float | None) -> float | None:
    if start_value is None or end_value is None or start_value <= 0:
        return None
    return (end_value / start_value - 1.0) * 100.0


def annualized_return(return_pct: float | None, start_date: str | None, end_date: str | None) -> float | None:
    if return_pct is None or not start_date or not end_date:
        return None
    try:
        start = datetime.fromisoformat(start_date).date()
        end = datetime.fromisoformat(end_date).date()
    except ValueError:
        return None
    days = max((end - start).days, 1)
    base = 1.0 + return_pct / 100.0
    if base <= 0:
        return None
    return (base ** (365.0 / days) - 1.0) * 100.0


def read_rankhandler_url(url: str, timeout_sec: int) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 advisor-monitor/fof-h1-ranking",
            "Referer": "https://fund.eastmoney.com/data/fundranking.html",
        },
    )
    with urlopen(request, timeout=timeout_sec) as response:
        chunks: list[bytes] = []
        while True:
            try:
                chunk = response.read(65536)
            except IncompleteRead as error:
                if error.partial:
                    chunks.append(error.partial)
                break
            if not chunk:
                break
            chunks.append(chunk)
    return b"".join(chunks).decode("utf-8", "ignore")


def fetch_rankhandler(
    ft: str,
    start_date: str,
    end_date: str,
    timeout_sec: int = 90,
    page_no: int = 1,
    page_size: int = 3000,
    allow_partial_page: bool = False,
) -> tuple[list[str], dict[str, Any]]:
    params = {
        "op": "ph",
        "dt": "kf",
        "ft": ft,
        "rs": "",
        "gs": "0",
        "sc": "dm",
        "st": "asc",
        "sd": start_date,
        "ed": end_date,
        "qdii": "",
        "tabSubtype": ",,,,,",
        "pi": str(page_no),
        "pn": str(page_size),
        "dx": "1",
        "v": str(time.time()),
    }
    url = f"{RANKHANDLER_URL}?{urlencode(params)}"
    best_datas: list[str] = []
    best_meta: dict[str, Any] | None = None
    errors: list[str] = []
    for attempt in range(1, 4):
        try:
            text = read_rankhandler_url(url, timeout_sec)
            datas = re.findall(r'"([^"\\]*(?:\\.[^"\\]*)*)"', text)
            all_records = re.search(r"allRecords:(\d+)", text)
            all_pages = re.search(r"allPages:(\d+)", text)
            all_record_count = int(all_records.group(1)) if all_records else None
            all_pages_count = int(all_pages.group(1)) if all_pages else None
            complete = bool(all_record_count is not None and len(datas) >= all_record_count)
            expected_page_rows = None
            if all_record_count is not None and all_pages_count is not None and all_pages_count > 1:
                expected_page_rows = max(0, min(page_size, all_record_count - (page_no - 1) * page_size))
            page_complete = complete or bool(
                allow_partial_page
                and all_record_count is not None
                and all_pages_count is not None
                and all_pages_count > 1
                and len(datas) >= (expected_page_rows or 1)
            )
            meta = {
                "ft": ft,
                "url": url,
                "pageNo": page_no,
                "pageSize": page_size,
                "returnedRows": len(datas),
                "allRecords": all_record_count,
                "allPages": all_pages_count,
                "attempts": attempt,
                "complete": complete,
                "pageComplete": page_complete,
            }
            if len(datas) > len(best_datas):
                best_datas = datas
                best_meta = meta
            if meta["pageComplete"]:
                return datas, meta
        except Exception as exc:
            errors.append(str(exc))
        time.sleep(1.5 * attempt)
    if best_meta is None:
        raise RuntimeError(f"rankhandler fetch failed for ft={ft}: {'; '.join(errors)}")
    best_meta = dict(best_meta)
    best_meta["errors"] = errors
    return best_datas, best_meta


def fetch_rankhandler_all_pages(
    ft: str,
    start_date: str,
    end_date: str,
    page_size: int = 3000,
) -> tuple[list[str], dict[str, Any]]:
    datas, first_meta = fetch_rankhandler(
        ft,
        start_date,
        end_date,
        page_size=page_size,
        allow_partial_page=True,
    )
    all_pages = first_meta.get("allPages") or 1
    if all_pages <= 1:
        return datas, first_meta

    all_datas = list(datas)
    page_metas = [first_meta]
    for page_no in range(2, int(all_pages) + 1):
        page_datas, page_meta = fetch_rankhandler(
            ft,
            start_date,
            end_date,
            page_no=page_no,
            page_size=page_size,
            allow_partial_page=True,
        )
        all_datas.extend(page_datas)
        page_metas.append(page_meta)
        time.sleep(0.15)

    all_records = first_meta.get("allRecords")
    meta = dict(first_meta)
    meta.update(
        {
            "returnedRows": len(all_datas),
            "complete": bool(all_records is not None and len(all_datas) >= all_records),
            "pageComplete": all(page.get("pageComplete") for page in page_metas),
            "pageMetas": [
                {
                    "pageNo": page.get("pageNo"),
                    "returnedRows": page.get("returnedRows"),
                    "attempts": page.get("attempts"),
                    "errors": page.get("errors", []),
                }
                for page in page_metas
            ],
        }
    )
    return all_datas, meta


def parse_rank_record(text: str, source_ft: str) -> dict[str, Any] | None:
    parts = text.split(",")
    if len(parts) < 19:
        return None
    return {
        "基金代码": parts[0],
        "排行基金名称": parts[1],
        "拼音缩写": parts[2],
        "净值日期": parts[3],
        "单位净值": to_float(parts[4]),
        "累计净值": to_float(parts[5]),
        "日涨幅_百分比": to_float(parts[6]),
        "近1周收益率_百分比": to_float(parts[7]),
        "近1月收益率_百分比": to_float(parts[8]),
        "近3月收益率_百分比": to_float(parts[9]),
        "近6月收益率_百分比": to_float(parts[10]),
        "近1年收益率_百分比": to_float(parts[11]),
        "近2年收益率_百分比": to_float(parts[12]),
        "近3年收益率_百分比": to_float(parts[13]),
        "今年以来收益率_百分比": to_float(parts[14]),
        "成立以来收益率_百分比": to_float(parts[15]),
        "成立日期": parts[16],
        "上半年区间收益率_百分比": to_float(parts[18]),
        "购买费率": parts[19] if len(parts) > 19 else "",
        "排行接口类型": source_ft,
    }


def load_summary_rows(summary_js: Path) -> list[dict[str, Any]]:
    text = summary_js.read_text(encoding="utf-8")
    prefix = "window.__BASIC_DATA__.summary = "
    if not text.startswith(prefix):
        raise ValueError(f"unexpected summary file format: {summary_js}")
    payload = text[len(prefix) :].strip()
    if payload.endswith(";"):
        payload = payload[:-1]
    summary = json.loads(payload)
    return list(summary.get("strategies") or [])


def load_governance(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    table = "策略治理标签"
    cols = [
        "统一策略ID",
        "治理状态",
        "分析分组",
        "是否测试组合",
        "是否信号类组合",
        "是否目标盈期次",
        "是否已停止",
        "是否纳入常规排名",
        "规则说明",
    ]
    sql = f"SELECT {', '.join(q(c) for c in cols)} FROM {q(table)}"
    return {row["统一策略ID"]: dict(row) for row in conn.execute(sql)}


def load_strategy_nav_points(conn: sqlite3.Connection, start_anchor: str, start_min: str, end_anchor: str) -> dict[str, dict[str, Any]]:
    table = "策略标准业绩净值"
    sid = "统一策略ID"
    trade_date = "交易日期"
    nav = "标准费后单位净值"
    start_before_sql = f"""
    WITH p AS (
      SELECT {q(sid)} id, MAX({q(trade_date)}) d
      FROM {q(table)}
      WHERE {q(trade_date)} <= ? AND {q(nav)} IS NOT NULL
      GROUP BY {q(sid)}
    )
    SELECT p.id, p.d, t.{q(nav)} v
    FROM p JOIN {q(table)} t ON t.{q(sid)} = p.id AND t.{q(trade_date)} = p.d
    """
    start_after_sql = f"""
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
    starts = {row["id"]: dict(row) for row in conn.execute(start_before_sql, [start_anchor])}
    fallback_starts = {row["id"]: dict(row) for row in conn.execute(start_after_sql, [start_min, end_anchor])}
    ends = {row["id"]: dict(row) for row in conn.execute(end_sql, [end_anchor])}
    result: dict[str, dict[str, Any]] = {}
    for strategy_id, end in ends.items():
        start = starts.get(strategy_id) or fallback_starts.get(strategy_id)
        if not start:
            continue
        return_pct = pct_return(to_float(start.get("v")), to_float(end.get("v")))
        result[strategy_id] = {
            "标准净值起始日": start.get("d"),
            "标准净值截止日": end.get("d"),
            "标准净值起始值": to_float(start.get("v")),
            "标准净值截止值": to_float(end.get("v")),
            "策略H1收益率_百分比": round_or_none(return_pct),
            "策略H1年化收益率_百分比": round_or_none(annualized_return(return_pct, start.get("d"), end.get("d"))),
            "策略收益覆盖状态": "完整上半年" if start.get("d") <= start_anchor else "非完整上半年",
        }
    return result


def load_benchmark_points(conn: sqlite3.Connection, start_anchor: str, end_anchor: str) -> dict[str, dict[str, Any]]:
    table = "策略日度业绩"
    sid = "统一策略ID"
    trade_date = "交易日期"
    bench = "基准收益率_百分比"
    start_query = f"""
    WITH p AS (
      SELECT {q(sid)} id, MAX({q(trade_date)}) d
      FROM {q(table)}
      WHERE {q(trade_date)} <= ? AND {q(bench)} IS NOT NULL
      GROUP BY {q(sid)}
    )
    SELECT p.id, p.d, t.{q(bench)} v
    FROM p JOIN {q(table)} t ON t.{q(sid)} = p.id AND t.{q(trade_date)} = p.d
    """
    end_query = f"""
    WITH p AS (
      SELECT {q(sid)} id, MAX({q(trade_date)}) d
      FROM {q(table)}
      WHERE {q(trade_date)} <= ? AND {q(bench)} IS NOT NULL
      GROUP BY {q(sid)}
    )
    SELECT p.id, p.d, t.{q(bench)} v
    FROM p JOIN {q(table)} t ON t.{q(sid)} = p.id AND t.{q(trade_date)} = p.d
    """
    starts = {row["id"]: dict(row) for row in conn.execute(start_query, [start_anchor])}
    ends = {row["id"]: dict(row) for row in conn.execute(end_query, [end_anchor])}
    result: dict[str, dict[str, Any]] = {}
    for strategy_id, end in ends.items():
        start = starts.get(strategy_id)
        if not start:
            continue
        start_cum = to_float(start.get("v"))
        end_cum = to_float(end.get("v"))
        if start_cum is None or end_cum is None:
            continue
        bench_return = ((1 + end_cum / 100.0) / (1 + start_cum / 100.0) - 1.0) * 100.0
        result[strategy_id] = {
            "基准起始日": start.get("d"),
            "基准截止日": end.get("d"),
            "基准H1收益率_百分比": round_or_none(bench_return),
        }
    return result


def load_fof_universe(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    table = "基金标准分类字典"
    cols = [
        "基金代码",
        "标准基金名称",
        "天天基金细分类",
        "天天基金大类",
        "天天基金二级分类",
        "基金公司",
        "基金经理",
        "是否QDII",
        "是否FOF",
        "市场地域标签",
        "主动被动标签",
        "投顾资产分类桶",
        "最近更新时间",
    ]
    sql = f"""
    SELECT {', '.join(q(c) for c in cols)}
    FROM {q(table)}
    WHERE {q('是否FOF')} = 1
    ORDER BY {q('基金代码')}
    """
    return {row["基金代码"]: dict(row) for row in conn.execute(sql)}


def fof_bucket(row: dict[str, Any]) -> str:
    text = " ".join(clean(row.get(k)) for k in ["天天基金细分类", "天天基金大类", "天天基金二级分类", "标准基金名称", "市场地域标签"])
    if "QDII" in text or "海外" in text or "全球" in text:
        return "QDII-FOF"
    if "进取" in text:
        return "FOF-进取型"
    if "均衡" in text:
        return "FOF-均衡型"
    if "稳健" in text:
        return "FOF-稳健型"
    return "FOF-其他"


def strategy_bucket(row: dict[str, Any]) -> tuple[str, str]:
    region = clean(row.get("市场地域"))
    business = clean(row.get("业务分类"))
    strategy_name = clean(row.get("策略名称"))
    qdii_weight = to_float(row.get("QDII权重")) or 0.0
    equity_weight = to_float(row.get("权益基金权重"))
    benchmark_equity = to_float(row.get("基准权益权重"))
    eq = equity_weight if equity_weight is not None else benchmark_equity
    text = f"{region} {business} {strategy_name}"
    if qdii_weight >= 30 or "海外" in text or "全球" in text or "QD" in text:
        return "QDII-FOF", f"QDII权重{qdii_weight:.1f}%或地域/名称命中海外"
    if eq is None:
        return "FOF-其他", "缺权益权重，无法稳定映射"
    if eq <= 20:
        return "FOF-稳健型", f"权益权重{eq:.1f}%<=20%"
    if eq <= 60:
        return "FOF-均衡型", f"权益权重{eq:.1f}%介于20%-60%"
    return "FOF-进取型", f"权益权重{eq:.1f}%>60%"


def percentile_rank(value: float | None, pool: list[float]) -> tuple[int | None, int, float | None, float | None]:
    if value is None:
        return None, len(pool), None, None
    valid = sorted([x for x in pool if x is not None and math.isfinite(x)], reverse=True)
    if not valid:
        return None, 0, None, None
    rank = 1 + sum(1 for x in valid if x > value)
    percentile = rank / len(valid)
    beat = 1 - percentile
    return rank, len(valid), beat, percentile


def fetch_rank_records(start_date: str, end_date: str) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    rank_records: dict[str, dict[str, Any]] = {}
    meta_rows: list[dict[str, Any]] = []
    for ft in ["fof", "qdii"]:
        datas, meta = fetch_rankhandler_all_pages(ft, start_date, end_date, page_size=1000)
        meta_rows.append(meta)
        for item in datas:
            parsed = parse_rank_record(item, ft)
            if not parsed:
                continue
            code = parsed["基金代码"]
            if code not in rank_records or ft == "fof":
                rank_records[code] = parsed
    datas, meta = fetch_rankhandler_all_pages("all", start_date, end_date, page_size=1000)
    meta["purpose"] = "补齐 fof/qdii 专项接口未覆盖的本地 FOF 产品收益"
    meta_rows.append(meta)
    for item in datas:
        parsed = parse_rank_record(item, "all")
        if not parsed:
            continue
        code = parsed["基金代码"]
        if code not in rank_records:
            rank_records[code] = parsed
    return rank_records, meta_rows


def build_report_data(args: argparse.Namespace) -> dict[str, Any]:
    conn = sqlite3.connect(args.db_path)
    conn.row_factory = sqlite3.Row
    try:
        summary_rows = load_summary_rows(args.summary_js)
        governance = load_governance(conn)
        nav_points = load_strategy_nav_points(conn, args.start_anchor, args.start_date, args.strategy_end_anchor)
        benchmark_points = load_benchmark_points(conn, args.start_anchor, args.strategy_end_anchor)
        fof_universe = load_fof_universe(conn)
    finally:
        conn.close()

    rank_records, rank_meta = fetch_rank_records(args.start_anchor, args.fund_end_anchor)

    fof_rows: list[dict[str, Any]] = []
    for code, local in fof_universe.items():
        rank = rank_records.get(code)
        bucket = fof_bucket(local)
        interval_return = to_float(rank.get("上半年区间收益率_百分比") if rank else None)
        ytd_return = to_float(rank.get("今年以来收益率_百分比") if rank else None)
        one_month_return = to_float(rank.get("近1月收益率_百分比") if rank else None)
        three_month_return = to_float(rank.get("近3月收益率_百分比") if rank else None)
        six_month_return = to_float(rank.get("近6月收益率_百分比") if rank else None)
        one_year_return = to_float(rank.get("近1年收益率_百分比") if rank else None)
        if interval_return is not None:
            h1_return = interval_return
            data_status = "有排行区间收益"
        elif ytd_return is not None:
            h1_return = ytd_return
            data_status = "区间字段缺失，使用今年以来收益"
        elif six_month_return is not None:
            h1_return = six_month_return
            data_status = "区间/YTD字段缺失，使用近6月收益"
        else:
            h1_return = None
            data_status = "缺排行区间收益"
        row = {
            "基金代码": code,
            "基金名称": clean(local.get("标准基金名称")) or clean(rank.get("排行基金名称") if rank else ""),
            "FOF可比分类": bucket,
            "天天基金细分类": clean(local.get("天天基金细分类")),
            "天天基金大类": clean(local.get("天天基金大类")),
            "天天基金二级分类": clean(local.get("天天基金二级分类")),
            "基金公司": clean(local.get("基金公司")),
            "基金经理": clean(local.get("基金经理")),
            "是否QDII": int(to_float(local.get("是否QDII")) or 0),
            "市场地域标签": clean(local.get("市场地域标签")),
            "主动被动标签": clean(local.get("主动被动标签")),
            "投顾资产分类桶": clean(local.get("投顾资产分类桶")),
            "净值日期": clean(rank.get("净值日期") if rank else ""),
            "单位净值": round_or_none(to_float(rank.get("单位净值") if rank else None), 6),
            "累计净值": round_or_none(to_float(rank.get("累计净值") if rank else None), 6),
            "上半年收益率_百分比": round_or_none(h1_return),
            "排行区间收益率_百分比": round_or_none(interval_return),
            "今年以来收益率_百分比": round_or_none(ytd_return),
            "近1月收益率_百分比": round_or_none(one_month_return),
            "近3月收益率_百分比": round_or_none(three_month_return),
            "近6月收益率_百分比": round_or_none(six_month_return),
            "近1年收益率_百分比": round_or_none(one_year_return),
            "成立日期": clean(rank.get("成立日期") if rank else ""),
            "排行接口类型": clean(rank.get("排行接口类型") if rank else ""),
            "数据状态": data_status,
        }
        fof_rows.append(row)

    pool_by_bucket: dict[str, list[float]] = {}
    for row in fof_rows:
        value = to_float(row.get("上半年收益率_百分比"))
        if value is not None:
            pool_by_bucket.setdefault(row["FOF可比分类"], []).append(value)

    strategy_rows: list[dict[str, Any]] = []
    for base in summary_rows:
        strategy_id = clean(base.get("统一策略ID"))
        if not strategy_id:
            continue
        gov = governance.get(strategy_id, {})
        nav = nav_points.get(strategy_id, {})
        bench = benchmark_points.get(strategy_id, {})
        bucket, bucket_basis = strategy_bucket(base)
        h1_return = to_float(nav.get("策略H1收益率_百分比"))
        rank, pool_count, beat, percentile = percentile_rank(h1_return, pool_by_bucket.get(bucket, []))
        include_rank = gov.get("是否纳入常规排名")
        is_client = clean(base.get("天天当前对客展示")) or "未识别"
        row = {
            "统一策略ID": strategy_id,
            "渠道": clean(base.get("渠道")),
            "投顾机构": clean(base.get("投顾机构")),
            "策略名称": clean(base.get("策略名称")),
            "是否对客": is_client,
            "天天展示状态": clean(base.get("天天展示状态")),
            "天天展示判定依据": clean(base.get("天天展示判定依据")),
            "是否纳入常规排名": int(include_rank) if include_rank is not None and clean(include_rank) != "" else None,
            "治理状态": clean(gov.get("治理状态")),
            "分析分组": clean(gov.get("分析分组")),
            "是否信号类组合": int(gov.get("是否信号类组合") or 0) if gov else None,
            "是否目标盈期次": int(gov.get("是否目标盈期次") or 0) if gov else None,
            "是否已停止": int(gov.get("是否已停止") or 0) if gov else None,
            "风险等级": clean(base.get("风险等级")),
            "业务分类": clean(base.get("业务分类")),
            "研报产品类型": clean(base.get("研报产品类型")),
            "市场地域": clean(base.get("市场地域")),
            "FOF可比分类": bucket,
            "FOF分类依据": bucket_basis,
            "业绩基准": clean(base.get("业绩基准")),
            "权益基金权重_百分比": round_or_none(to_float(base.get("权益基金权重"))),
            "债券基金权重_百分比": round_or_none(to_float(base.get("债券基金权重"))),
            "货币基金权重_百分比": round_or_none(to_float(base.get("货币基金权重"))),
            "混合基金权重_百分比": round_or_none(to_float(base.get("混合基金权重"))),
            "QDII权重_百分比": round_or_none(to_float(base.get("QDII权重"))),
            "策略H1收益率_百分比": round_or_none(h1_return),
            "策略H1年化收益率_百分比": round_or_none(to_float(nav.get("策略H1年化收益率_百分比"))),
            "基准H1收益率_百分比": round_or_none(to_float(bench.get("基准H1收益率_百分比"))),
            "相对基准超额_百分点": round_or_none((h1_return or 0) - to_float(bench.get("基准H1收益率_百分比")) if h1_return is not None and to_float(bench.get("基准H1收益率_百分比")) is not None else None),
            "标准净值起始日": clean(nav.get("标准净值起始日")),
            "标准净值截止日": clean(nav.get("标准净值截止日")),
            "策略收益覆盖状态": clean(nav.get("策略收益覆盖状态")) or "缺标准净值",
            "同类FOF样本数": pool_count,
            "同类FOF排名": rank,
            "击败同类FOF比例": round_or_none(beat, 6),
            "排名位置百分位": round_or_none(percentile, 6),
        }
        strategy_rows.append(row)

    strategy_rows.sort(
        key=lambda row: (
            row.get("FOF可比分类") or "",
            -(to_float(row.get("策略H1收益率_百分比")) if to_float(row.get("策略H1收益率_百分比")) is not None else -999999),
            row.get("投顾机构") or "",
            row.get("策略名称") or "",
        )
    )

    category_rows: list[dict[str, Any]] = []
    for bucket in sorted({row["FOF可比分类"] for row in fof_rows} | {row["FOF可比分类"] for row in strategy_rows}):
        strategies = [row for row in strategy_rows if row["FOF可比分类"] == bucket and to_float(row.get("策略H1收益率_百分比")) is not None]
        client_strategies = [row for row in strategies if row.get("是否对客") == "是"]
        percentiles = [to_float(row.get("排名位置百分位")) for row in strategies if to_float(row.get("排名位置百分位")) is not None]
        returns = [to_float(row.get("策略H1收益率_百分比")) for row in strategies if to_float(row.get("策略H1收益率_百分比")) is not None]
        fund_returns = pool_by_bucket.get(bucket, [])
        category_rows.append(
            {
                "FOF可比分类": bucket,
                "策略数量": len(strategies),
                "对客策略数量": len(client_strategies),
                "FOF产品总数": sum(1 for row in fof_rows if row["FOF可比分类"] == bucket),
                "有收益FOF产品数": len(fund_returns),
                "策略平均H1收益率_百分比": round_or_none(statistics.mean(returns) if returns else None),
                "策略中位数H1收益率_百分比": round_or_none(statistics.median(returns) if returns else None),
                "FOF平均H1收益率_百分比": round_or_none(statistics.mean(fund_returns) if fund_returns else None),
                "FOF中位数H1收益率_百分比": round_or_none(statistics.median(fund_returns) if fund_returns else None),
                "策略平均排名百分位": round_or_none(statistics.mean(percentiles) if percentiles else None, 6),
                "策略中位数排名百分位": round_or_none(statistics.median(percentiles) if percentiles else None, 6),
            }
        )

    missing_fof_rows = [row for row in fof_rows if to_float(row.get("上半年收益率_百分比")) is None]
    strategy_end_counts = Counter(
        clean(row.get("标准净值截止日"))
        for row in strategy_rows
        if to_float(row.get("策略H1收益率_百分比")) is not None
    )
    strategy_end_counts.pop("", None)
    fof_nav_date_counts = Counter(
        clean(row.get("净值日期"))
        for row in fof_rows
        if to_float(row.get("上半年收益率_百分比")) is not None
    )
    fof_nav_date_counts.pop("", None)
    fof_source_counts = Counter(clean(row.get("排行接口类型")) or "无排行记录" for row in fof_rows)
    fof_status_counts = Counter(clean(row.get("数据状态")) or "未识别" for row in fof_rows)
    actual_strategy_end = max(strategy_end_counts.keys()) if strategy_end_counts else ""
    actual_fof_nav_date = max(fof_nav_date_counts.keys()) if fof_nav_date_counts else ""
    meta = {
        "报告名称": "2026年上半年投顾策略-全市场FOF排名报表",
        "生成时间": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "策略收益起始锚点": args.start_anchor,
        "策略收益最早替代起点": args.start_date,
        "策略收益截止锚点": args.strategy_end_anchor,
        "基金收益起始锚点": args.start_anchor,
        "基金收益截止锚点": args.fund_end_anchor,
        "策略总数": len(strategy_rows),
        "有H1收益策略数": sum(1 for row in strategy_rows if to_float(row.get("策略H1收益率_百分比")) is not None),
        "对客策略数": sum(1 for row in strategy_rows if row.get("是否对客") == "是"),
        "本地FOF字典总数": len(fof_rows),
        "有H1收益FOF数": sum(1 for row in fof_rows if to_float(row.get("上半年收益率_百分比")) is not None),
        "缺H1收益FOF数": len(missing_fof_rows),
        "实际策略标准净值最新日": actual_strategy_end,
        "策略20260630覆盖数": strategy_end_counts.get(args.strategy_end_anchor, 0),
        "策略H1收益日期分布": dict(strategy_end_counts.most_common(20)),
        "实际FOF净值最新日": actual_fof_nav_date,
        "FOF20260630覆盖数": fof_nav_date_counts.get(args.fund_end_anchor, 0),
        "FOF净值日期分布": dict(fof_nav_date_counts.most_common(20)),
        "FOF收益来源分布": dict(fof_source_counts.most_common()),
        "FOF数据状态分布": dict(fof_status_counts.most_common()),
        "排行接口": rank_meta,
        "数据来源说明": [
            "投顾策略基础、对客展示、资产权重来自 site/basic_data/data/basic_summary.js。",
            "策略H1收益使用本地 SQLite 策略标准业绩净值的标准费后单位净值计算。",
            "FOF全市场名单来自本地基金标准分类字典；FOF区间收益来自东方财富/天天基金 rankhandler 批量排行接口，以 fof、qdii 专项接口为主，并尝试 all 全市场接口补齐。",
            f"本次策略端实际使用的最新期末标准净值日为 {actual_strategy_end or '无'}，其中 {strategy_end_counts.get(args.strategy_end_anchor, 0)} 个策略使用 {args.strategy_end_anchor} 作为期末净值。",
            f"本次 FOF 端实际使用的最新净值日为 {actual_fof_nav_date or '无'}，其中 {fof_nav_date_counts.get(args.fund_end_anchor, 0)} 只 FOF 使用 {args.fund_end_anchor} 作为净值日期。",
        ],
    }
    return {
        "meta": meta,
        "strategyRows": strategy_rows,
        "fofRows": fof_rows,
        "categoryRows": category_rows,
        "missingFofRows": missing_fof_rows,
        "notes": build_notes(meta),
    }


def build_notes(meta: dict[str, Any]) -> list[dict[str, str]]:
    strategy_anchor = clean(meta.get("策略收益截止锚点"))
    fund_anchor = clean(meta.get("基金收益截止锚点"))
    actual_strategy_end = clean(meta.get("实际策略标准净值最新日"))
    actual_fof_nav_date = clean(meta.get("实际FOF净值最新日"))
    return [
        {"项目": "上半年窗口", "说明": f"名义窗口为 2026-01-01 至 {fund_anchor or '2026-06-30'}；策略端期末锚点为 {strategy_anchor or '2026-06-30'}，本次实际使用最新期末净值日为 {actual_strategy_end or '无'}。"},
        {"项目": "策略收益", "说明": "使用标准费后单位净值：期末净值 / 期初锚点净值 - 1。若策略在 2026 年内新成立，则从首个可得标准净值日起算，并标记为非完整上半年。"},
        {"项目": "基准收益", "说明": "使用策略日度业绩表中披露/补齐的累计基准收益率折算区间收益；缺基准曲线时留空。"},
        {"项目": "FOF产品池", "说明": "全市场 FOF 名单以本地基金标准分类字典 是否FOF=1 为准，避免只保留有收益排行的产品。"},
        {"项目": "FOF收益", "说明": f"优先使用东方财富 rankhandler 按 sd=2025-12-31、ed={fund_anchor or '2026-06-30'} 返回的区间涨幅；先取 fof/qdii 专项接口，再尝试 all 全市场排行接口补齐专项接口未覆盖的本地 FOF，实际最新净值日为 {actual_fof_nav_date or '无'}。"},
        {"项目": "FOF分类", "说明": "FOF-稳健/均衡/进取优先使用天天基金细分类；QDII、海外、全球类 FOF 统一进入 QDII-FOF。"},
        {"项目": "策略映射", "说明": "QDII权重>=30%或海外/全球命中进入 QDII-FOF；非海外策略按权益权重 <=20%、20%-60%、>60% 映射稳健、均衡、进取。"},
        {"项目": "排名公式", "说明": "同类FOF排名 = 1 + 同类FOF中上半年收益高于该策略的产品数；排名位置百分位 = 排名 / 同类FOF样本数；击败比例 = 1 - 排名位置百分位。"},
        {"项目": "是否对客", "说明": "使用页面宽表 天天当前对客展示 字段；非天天渠道没有相反标记时按已有展示口径保留为是。"},
    ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate data JSON for 2026 H1 advisor strategy ranking versus all-market FOF funds.")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--summary-js", type=Path, default=DEFAULT_SUMMARY_JS)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--start-date", default="2026-01-01")
    parser.add_argument("--start-anchor", default="2025-12-31")
    parser.add_argument("--strategy-end-anchor", default="2026-06-30")
    parser.add_argument("--fund-end-anchor", default="2026-06-30")
    parser.add_argument("--run-id", default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_id = args.run_id or datetime.now().strftime("%Y%m%dT%H%M%S")
    output_dir = args.output_root / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    data = build_report_data(args)
    data["meta"]["runId"] = run_id
    output_path = output_dir / "fof_h1_strategy_ranking_data.json"
    output_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    latest_path = args.output_root / "latest_fof_h1_strategy_ranking_data.json"
    latest_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output_path), "latest": str(latest_path), **data["meta"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
