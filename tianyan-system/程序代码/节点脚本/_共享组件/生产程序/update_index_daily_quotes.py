from __future__ import annotations

import argparse
import json
import sqlite3
import time
from http.client import IncompleteRead
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode
from urllib.error import URLError
from urllib.request import Request, urlopen


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
INDEX_DAILY_TABLE = "\u6307\u6570\u65e5\u5ea6\u884c\u60c5"
INDEX_CODE_COLUMN = "\u6307\u6570\u4ee3\u7801"
TRADE_DATE_COLUMN = "\u4ea4\u6613\u65e5\u671f"

INDEX_CONFIG = {
    "000300.SH": {
        "指数名称": "沪深300",
        "东方财富secid": "1.000300",
        "腾讯证券代码": "sh000300",
    },
    "000001.SH": {
        "指数名称": "上证综合",
        "东方财富secid": "1.000001",
        "腾讯证券代码": "sh000001",
    },
    "000015.SH": {
        "指数名称": "上证红利",
        "东方财富secid": "1.000015",
        "腾讯证券代码": "sh000015",
    },
    "000922.CSI": {
        "指数名称": "中证红利",
        "东方财富secid": "1.000922",
        "腾讯证券代码": "sh000922",
    },
    "000906.SH": {
        "指数名称": "中证800",
        "东方财富secid": "1.000906",
        "腾讯证券代码": "sh000906",
    },
    "000905.SH": {
        "指数名称": "中证500",
        "东方财富secid": "1.000905",
        "腾讯证券代码": "sh000905",
    },
    "000852.SH": {
        "指数名称": "中证1000",
        "东方财富secid": "1.000852",
        "腾讯证券代码": "sh000852",
    },
    "000985.CSI": {
        "指数名称": "中证全指",
        "东方财富secid": "1.000985",
        "腾讯证券代码": "sh000985",
    },
    "000993.CSI": {
        "指数名称": "中证全指信息",
        "东方财富secid": "1.000993",
        "腾讯证券代码": "sh000993",
    },
    "399006.SZ": {
        "指数名称": "创业板指",
        "东方财富secid": "0.399006",
        "腾讯证券代码": "sz399006",
    },
    "000698.SH": {
        "指数名称": "科创100",
        "东方财富secid": "1.000698",
        "腾讯证券代码": "sh000698",
    },
    "000171.SH": {
        "指数名称": "中国战略新兴产业",
        "东方财富secid": "1.000171",
        "腾讯证券代码": "sh000171",
    },
    "000918.SH": {
        "指数名称": "沪深300成长",
        "东方财富secid": "1.000918",
        "腾讯证券代码": "sh000918",
    },
    "399967.SZ": {
        "指数名称": "中证军工",
        "东方财富secid": "0.399967",
        "腾讯证券代码": "sz399967",
    },
    "000998.SH": {
        "指数名称": "中证TMT",
        "东方财富secid": "1.000998",
        "腾讯证券代码": "sh000998",
    },
    "000942.SH": {
        "指数名称": "中证内地消费主题",
        "东方财富secid": "1.000942",
        "腾讯证券代码": "sh000942",
    },
    "000933.SH": {
        "指数名称": "中证医药卫生",
        "东方财富secid": "1.000933",
        "腾讯证券代码": "sh000933",
    },
    "000827.SH": {
        "指数名称": "中证环保",
        "东方财富secid": "1.000827",
        "腾讯证券代码": "sh000827",
    },
    "000979.CSI": {
        "指数名称": "中证大宗商品股票",
        "东方财富secid": "1.000979",
        "腾讯证券代码": "sh000979",
    },
    "000012.SH": {
        "指数名称": "上证国债",
        "东方财富secid": "1.000012",
        "腾讯证券代码": "sh000012",
    },
    "HSI.HI": {
        "指数名称": "恒生指数",
        "东方财富secid": "100.HSI",
        "腾讯证券代码": "hkHSI",
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="更新指数日度行情基础表，用于策略详情页指数对照曲线。")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--begin", default="20000101")
    parser.add_argument("--end", default=datetime.now().strftime("%Y%m%d"))
    parser.add_argument("--index-code", default="000300.SH", choices=sorted(INDEX_CONFIG))
    parser.add_argument("--all", action="store_true", help="更新脚本内置的全部指数")
    parser.add_argument("--incremental", action="store_true", help="按数据库已有最新日期增量更新，并保留重叠窗口。")
    parser.add_argument("--lookback-days", type=int, default=10, help="增量更新时回看并覆盖的自然日天数。")
    return parser.parse_args()


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS "指数日度行情" (
            "指数代码" TEXT NOT NULL,
            "指数名称" TEXT NOT NULL,
            "交易日期" TEXT NOT NULL,
            "开盘点位" REAL,
            "收盘点位" REAL,
            "最高点位" REAL,
            "最低点位" REAL,
            "日涨跌幅_百分比" REAL,
            "数据来源" TEXT NOT NULL,
            "采集时间" TEXT NOT NULL,
            PRIMARY KEY ("指数代码", "交易日期")
        )
        """
    )


def fetch_eastmoney_kline(secid: str, begin: str, end: str) -> dict:
    query = urlencode(
        {
            "secid": secid,
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "klt": "101",
            "fqt": "1",
            "beg": begin,
            "end": end,
            "_": int(time.time() * 1000),
        }
    )
    urls = [
        f"https://push2his.eastmoney.com/api/qt/stock/kline/get?{query}",
        f"http://push2his.eastmoney.com/api/qt/stock/kline/get?{query}",
    ]
    last_error: Exception | None = None
    for url in urls:
        for attempt in range(3):
            try:
                return fetch_json_url(url)
            except (IncompleteRead, URLError, TimeoutError, ConnectionError, OSError, json.JSONDecodeError) as exc:
                last_error = exc
                time.sleep(0.8 * (attempt + 1))
    raise RuntimeError(f"指数行情接口请求失败：{last_error}") from last_error


def fetch_json_url(url: str) -> dict:
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://quote.eastmoney.com/",
        },
    )
    with urlopen(request, timeout=30) as response:
        try:
            payload_bytes = response.read()
        except IncompleteRead as exc:
            payload_bytes = exc.partial
        payload = payload_bytes.decode("utf-8")
    return json.loads(payload)


def dashed_date(value: str) -> str:
    return f"{value[:4]}-{value[4:6]}-{value[6:8]}"


def compact_date(value: str) -> str:
    return value.replace("-", "").replace("/", "").replace(".", "")[:8]


def incremental_begin_for_index(
    conn: sqlite3.Connection,
    index_code: str,
    default_begin: str,
    end: str,
    lookback_days: int,
) -> str:
    row = conn.execute(
        f'SELECT MAX("{TRADE_DATE_COLUMN}") FROM "{INDEX_DAILY_TABLE}" WHERE "{INDEX_CODE_COLUMN}" = ?',
        (index_code,),
    ).fetchone()
    latest_value = row[0] if row else None
    if not latest_value:
        return default_begin
    latest = datetime.strptime(compact_date(str(latest_value)), "%Y%m%d")
    default_dt = datetime.strptime(compact_date(default_begin), "%Y%m%d")
    end_dt = datetime.strptime(compact_date(end), "%Y%m%d")
    begin_dt = max(default_dt, latest - timedelta(days=max(0, lookback_days)))
    if begin_dt > end_dt:
        begin_dt = end_dt
    return begin_dt.strftime("%Y%m%d")


def fetch_tencent_kline(symbol: str, begin: str, end: str) -> list[list[str]]:
    start_year = int(begin[:4])
    end_year = int(end[:4])
    result: list[list[str]] = []
    seen_dates: set[str] = set()
    for year in range(start_year, end_year + 1):
        chunk_begin = begin if year == start_year else f"{year}0101"
        chunk_end = end if year == end_year else f"{year}1231"
        param = f"{symbol},day,{dashed_date(chunk_begin)},{dashed_date(chunk_end)},640,qfq"
        url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?{urlencode({'param': param})}"
        data = None
        for attempt in range(3):
            try:
                data = fetch_json_url(url)
                break
            except (IncompleteRead, URLError, TimeoutError, ConnectionError, OSError, json.JSONDecodeError):
                time.sleep(0.8 * (attempt + 1))
        if data is None:
            continue
        node = ((data.get("data") or {}).get(symbol) or {})
        rows = node.get("day") or node.get("qfqday") or []
        for row in rows:
            if row and row[0] not in seen_dates:
                seen_dates.add(row[0])
                result.append(row)
        time.sleep(0.15)
    result.sort(key=lambda item: item[0])
    return result


def as_float(value: str | None) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def update_one(conn: sqlite3.Connection, index_code: str, begin: str, end: str) -> dict:
    config = INDEX_CONFIG[index_code]
    now = datetime.now().isoformat(timespec="seconds")
    source = "腾讯证券日K"
    klines = [
        ",".join([item[0], item[1], item[2], item[3], item[4], "", "", "", ""])
        for item in fetch_tencent_kline(config["腾讯证券代码"], begin, end)
        if len(item) >= 5
    ]
    if not klines:
        source = "东方财富历史K线"
        data = fetch_eastmoney_kline(config["东方财富secid"], begin, end)
        klines = (data.get("data") or {}).get("klines") or []
    rows = []
    previous_close: float | None = None
    for item in klines:
        fields = item.split(",")
        if len(fields) < 9:
            continue
        close_value = as_float(fields[2])
        pct_change = as_float(fields[8])
        if pct_change is None and close_value is not None and previous_close:
            pct_change = (close_value / previous_close - 1.0) * 100.0
        rows.append(
            (
                index_code,
                config["指数名称"],
                fields[0],
                as_float(fields[1]),
                close_value,
                as_float(fields[3]),
                as_float(fields[4]),
                pct_change,
                source,
                now,
            )
        )
        if close_value is not None:
            previous_close = close_value
    try:
        conn.executemany(
            """
            INSERT OR REPLACE INTO "指数日度行情"
            ("指数代码", "指数名称", "交易日期", "开盘点位", "收盘点位", "最高点位", "最低点位", "日涨跌幅_百分比", "数据来源", "采集时间")
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {
        "指数代码": index_code,
        "指数名称": config["指数名称"],
        "写入行数": len(rows),
        "开始日期": rows[0][2] if rows else None,
        "结束日期": rows[-1][2] if rows else None,
        "数据来源": source,
    }


def main() -> None:
    args = parse_args()
    conn = sqlite3.connect(args.db_path)
    try:
        ensure_table(conn)
        index_codes = sorted(INDEX_CONFIG) if args.all else [args.index_code]
        results = []
        for index_code in index_codes:
            begin = compact_date(args.begin)
            end = compact_date(args.end)
            if args.incremental:
                begin = incremental_begin_for_index(conn, index_code, begin, end, args.lookback_days)
            result = update_one(conn, index_code, begin, end)
            result["更新模式"] = "incremental" if args.incremental else "full"
            result["请求开始日期"] = dashed_date(begin)
            result["请求结束日期"] = dashed_date(end)
            results.append(result)
    finally:
        conn.close()
    print(
        json.dumps(
            {
                "数据库": str(args.db_path),
                "增量回看天数": max(0, args.lookback_days) if args.incremental else None,
                "结果": results,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
