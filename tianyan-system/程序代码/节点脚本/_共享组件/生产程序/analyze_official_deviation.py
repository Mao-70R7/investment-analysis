from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "analysis_zh_current.sqlite"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "outputs" / "official_deviation_analysis"
DEFAULT_ALGORITHM_VERSION = "rebalance_asset_fee_dual_nav_v4"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze official performance deviation by fee basis and channel.")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--algorithm-version", default=DEFAULT_ALGORITHM_VERSION)
    return parser.parse_args()


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


def round_or_none(value: float | None, digits: int = 6) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(value, digits)


def median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def mean(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def fetch_dicts(conn: sqlite3.Connection, sql: str, params: list[Any] | tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    return [dict(row) for row in conn.execute(sql, params)]


def create_tables(conn: sqlite3.Connection, algorithm_version: str) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS "策略官方偏差分析" (
            "统一策略ID" TEXT NOT NULL,
            "算法版本" TEXT NOT NULL,
            "渠道ID" TEXT NOT NULL,
            "渠道策略ID" TEXT,
            "策略名称" TEXT,
            "质量等级" TEXT,
            "官方可比记录数" INTEGER,
            "官方起始日期" TEXT,
            "官方结束日期" TEXT,
            "官方区间收益率_百分比" REAL,
            "费后模拟同区间收益率_百分比" REAL,
            "费前模拟同区间收益率_百分比" REAL,
            "App展示默认模拟同区间收益率_百分比" REAL,
            "费后官方偏差_百分点" REAL,
            "费前官方偏差_百分点" REAL,
            "App展示默认官方偏差_百分点" REAL,
            "费后官方绝对偏差_百分点" REAL,
            "费前官方绝对偏差_百分点" REAL,
            "App展示默认绝对偏差_百分点" REAL,
            "费前相对费后改善_百分点" REAL,
            "App展示默认口径" TEXT,
            "官方对比区间规则" TEXT,
            "官方更接近口径" TEXT,
            "偏差方向" TEXT,
            "推断原因" TEXT,
            "优化建议" TEXT,
            "生成时间" TEXT NOT NULL,
            PRIMARY KEY ("统一策略ID", "算法版本")
        );

        CREATE TABLE IF NOT EXISTS "渠道官方偏差分析" (
            "渠道ID" TEXT NOT NULL,
            "算法版本" TEXT NOT NULL,
            "可比策略数" INTEGER NOT NULL,
            "官方样本充足策略数" INTEGER NOT NULL,
            "费后更接近策略数" INTEGER NOT NULL,
            "费前更接近策略数" INTEGER NOT NULL,
            "费前费后接近策略数" INTEGER NOT NULL,
            "费后绝对偏差均值_百分点" REAL,
            "费后绝对偏差中位数_百分点" REAL,
            "费前绝对偏差均值_百分点" REAL,
            "费前绝对偏差中位数_百分点" REAL,
            "App展示默认绝对偏差均值_百分点" REAL,
            "App展示默认绝对偏差中位数_百分点" REAL,
            "最优口径绝对偏差均值_百分点" REAL,
            "费前相对费后平均改善_百分点" REAL,
            "推荐官方口径" TEXT,
            "渠道算法判断" TEXT,
            "下一步优化建议" TEXT,
            "生成时间" TEXT NOT NULL,
            PRIMARY KEY ("渠道ID", "算法版本")
        );
        """
    )
    ensure_columns(
        conn,
        "策略官方偏差分析",
        {
            "App展示默认模拟同区间收益率_百分比": "REAL",
            "App展示默认官方偏差_百分点": "REAL",
            "App展示默认绝对偏差_百分点": "REAL",
            "App展示默认口径": "TEXT",
            "官方对比区间规则": "TEXT",
        },
    )
    ensure_columns(
        conn,
        "渠道官方偏差分析",
        {
            "App展示默认绝对偏差均值_百分点": "REAL",
            "App展示默认绝对偏差中位数_百分点": "REAL",
        },
    )
    conn.execute('DELETE FROM "策略官方偏差分析" WHERE "算法版本" = ?', [algorithm_version])
    conn.execute('DELETE FROM "渠道官方偏差分析" WHERE "算法版本" = ?', [algorithm_version])


def ensure_columns(conn: sqlite3.Connection, table: str, definitions: dict[str, str]) -> None:
    existing = {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}
    for column_name, column_sql in definitions.items():
        if column_name not in existing:
            conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "{column_name}" {column_sql}')


def classify_basis(net_abs: float, gross_abs: float) -> str:
    if abs(net_abs - gross_abs) <= 0.05:
        return "费前费后接近"
    return "费后" if net_abs < gross_abs else "费前"


def build_reason(channel_id: str, official_count: int, net_diff: float, gross_diff: float, basis: str) -> tuple[str, str]:
    best_abs = min(abs(net_diff), abs(gross_diff))
    improvement = abs(net_diff) - abs(gross_diff)
    direction = "模拟高于官方" if net_diff > 0 else "模拟低于官方" if net_diff < 0 else "基本一致"

    reasons: list[str] = []
    suggestions: list[str] = []
    if basis == "费前" and improvement > 0.2:
        reasons.append("官方区间收益更接近费前模拟口径，投顾费日化扣减不能直接解释官方展示值。")
    elif basis == "费后" and improvement < -0.2:
        reasons.append("官方区间收益更接近费后模拟口径，投顾费扣减方向与官方展示较一致。")
    else:
        reasons.append("费前和费后差异对当前偏差解释力有限。")

    if official_count < 30:
        reasons.append(f"官方可比记录仅 {official_count} 条，样本稀疏会放大首尾点误差。")
        suggestions.append("优先补齐官方日度曲线，再做调仓日级误差归因。")
    if best_abs > 3:
        reasons.append("即使用更优费率口径后仍有超过 3pct 偏差，主要矛盾不在投顾费。")
        suggestions.append("逐段复核调仓生效日、现金权重、基金映射、底层基金净值与分红口径。")
    elif best_abs > 1:
        suggestions.append("对该策略跑调仓 T 日、T+1、披露日生效三套候选口径并比较。")

    if channel_id == "gffunds":
        suggestions.append("广发渠道优先验证调仓确认延迟、现金留存和服务费/管理费展示口径。")
    elif channel_id == "ttfund":
        suggestions.append("天天基金渠道优先补采官方完整历史净值曲线，降低登录态快照拼接误差。")
    elif channel_id == "zocaifu":
        suggestions.append("中欧财富渠道优先做逐调仓周期归因，定位个别偏差区间。")

    return "；".join(reasons + [f"费后口径方向：{direction}"]), "；".join(dict.fromkeys(suggestions))


def strategy_rows(conn: sqlite3.Connection, algorithm_version: str, generated_at: str) -> list[dict[str, Any]]:
    rows = fetch_dicts(
        conn,
        """
        SELECT *
        FROM "策略模拟净值质量"
        WHERE "算法版本" = ?
          AND "是否纳入模拟" = 1
          AND "官方可比记录数" >= 2
          AND "官方区间收益率_百分比" IS NOT NULL
          AND COALESCE("App展示同区间收益率_百分比", "模拟费前同区间收益率_百分比", "模拟同区间收益率_百分比") IS NOT NULL
        ORDER BY "渠道ID", "统一策略ID"
        """,
        [algorithm_version],
    )
    results: list[dict[str, Any]] = []
    for row in rows:
        net_diff = to_float(row.get("模拟官方收益差_百分点"))
        gross_diff = to_float(row.get("模拟费前官方收益差_百分点"))
        app_diff = to_float(row.get("App展示官方收益差_百分点"))
        if app_diff is None:
            app_diff = gross_diff
        app_return = to_float(row.get("App展示同区间收益率_百分比"))
        if app_return is None:
            app_return = to_float(row.get("模拟费前同区间收益率_百分比"))
        if net_diff is None or gross_diff is None:
            continue
        net_abs = abs(net_diff)
        gross_abs = abs(gross_diff)
        app_abs = abs(app_diff) if app_diff is not None else None
        basis = classify_basis(net_abs, gross_abs)
        official_count = int(row.get("官方可比记录数") or 0)
        reason, suggestion = build_reason(str(row.get("渠道ID")), official_count, net_diff, gross_diff, basis)
        direction = "费后模拟高于官方" if net_diff > 0 else "费后模拟低于官方" if net_diff < 0 else "基本一致"
        results.append(
            {
                "统一策略ID": row.get("统一策略ID"),
                "算法版本": algorithm_version,
                "渠道ID": row.get("渠道ID"),
                "渠道策略ID": row.get("渠道策略ID"),
                "策略名称": row.get("策略名称"),
                "质量等级": row.get("质量等级"),
                "官方可比记录数": official_count,
                "官方起始日期": row.get("官方起始日期"),
                "官方结束日期": row.get("官方结束日期"),
                "官方区间收益率_百分比": round_or_none(to_float(row.get("官方区间收益率_百分比")), 6),
                "费后模拟同区间收益率_百分比": round_or_none(to_float(row.get("模拟同区间收益率_百分比")), 6),
                "费前模拟同区间收益率_百分比": round_or_none(to_float(row.get("模拟费前同区间收益率_百分比")), 6),
                "App展示默认模拟同区间收益率_百分比": round_or_none(app_return, 6),
                "费后官方偏差_百分点": round_or_none(net_diff, 6),
                "费前官方偏差_百分点": round_or_none(gross_diff, 6),
                "App展示默认官方偏差_百分点": round_or_none(app_diff, 6),
                "费后官方绝对偏差_百分点": round_or_none(net_abs, 6),
                "费前官方绝对偏差_百分点": round_or_none(gross_abs, 6),
                "App展示默认绝对偏差_百分点": round_or_none(app_abs, 6),
                "费前相对费后改善_百分点": round_or_none(net_abs - gross_abs, 6),
                "App展示默认口径": row.get("App展示对比口径") or "费前",
                "官方对比区间规则": row.get("官方对比区间规则"),
                "官方更接近口径": basis,
                "偏差方向": direction,
                "推断原因": reason,
                "优化建议": suggestion,
                "生成时间": generated_at,
            }
        )
    return results


def channel_rows(strategy_items: list[dict[str, Any]], algorithm_version: str, generated_at: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in strategy_items:
        grouped[str(item["渠道ID"])].append(item)

    output: list[dict[str, Any]] = []
    for channel_id, items in sorted(grouped.items()):
        net_abs = [float(item["费后官方绝对偏差_百分点"]) for item in items if item["费后官方绝对偏差_百分点"] is not None]
        gross_abs = [float(item["费前官方绝对偏差_百分点"]) for item in items if item["费前官方绝对偏差_百分点"] is not None]
        app_abs = [float(item["App展示默认绝对偏差_百分点"]) for item in items if item["App展示默认绝对偏差_百分点"] is not None]
        best_abs = [min(float(item["费后官方绝对偏差_百分点"]), float(item["费前官方绝对偏差_百分点"])) for item in items]
        improvements = [float(item["费前相对费后改善_百分点"]) for item in items if item["费前相对费后改善_百分点"] is not None]
        basis_counts = Counter(str(item["官方更接近口径"]) for item in items)
        sufficient_count = sum(1 for item in items if int(item.get("官方可比记录数") or 0) >= 30)
        mean_net = mean(net_abs)
        mean_gross = mean(gross_abs)
        if mean_net is not None and mean_gross is not None:
            if abs(mean_net - mean_gross) <= 0.05:
                recommended_basis = "费前费后差异不显著"
            elif mean_net < mean_gross:
                recommended_basis = "费后"
            else:
                recommended_basis = "费前"
        else:
            recommended_basis = "样本不足"

        if recommended_basis == "费前":
            judgement = "官方展示更接近费前收益，投顾费应保留为另一个产品净值口径，不宜用费后值强行贴官方。"
        elif recommended_basis == "费后":
            judgement = "官方展示更接近费后收益，当前投顾费日化扣减方向有效，剩余偏差优先看调仓和现金。"
        else:
            judgement = "费率口径对渠道偏差解释力有限，需要从调仓生效日、底层净值和官方样本完整性继续拆解。"

        suggestions = []
        if channel_id == "gffunds":
            suggestions.append("优先新增 T+1 调仓生效和现金残留候选算法。")
        elif channel_id == "ttfund":
            suggestions.append("优先补齐官方历史日净值曲线，当前官方样本多为快照拼接。")
        elif channel_id == "zocaifu":
            suggestions.append("官方样本较完整，优先逐调仓周期定位大偏差策略。")
        if mean(best_abs) is not None and (mean(best_abs) or 0) > 1:
            suggestions.append("对绝对偏差超过 3pct 的策略输出逐日误差曲线和最大偏差日期。")

        output.append(
            {
                "渠道ID": channel_id,
                "算法版本": algorithm_version,
                "可比策略数": len(items),
                "官方样本充足策略数": sufficient_count,
                "费后更接近策略数": basis_counts.get("费后", 0),
                "费前更接近策略数": basis_counts.get("费前", 0),
                "费前费后接近策略数": basis_counts.get("费前费后接近", 0),
                "费后绝对偏差均值_百分点": round_or_none(mean_net, 6),
                "费后绝对偏差中位数_百分点": round_or_none(median(net_abs), 6),
                "费前绝对偏差均值_百分点": round_or_none(mean_gross, 6),
                "费前绝对偏差中位数_百分点": round_or_none(median(gross_abs), 6),
                "App展示默认绝对偏差均值_百分点": round_or_none(mean(app_abs), 6),
                "App展示默认绝对偏差中位数_百分点": round_or_none(median(app_abs), 6),
                "最优口径绝对偏差均值_百分点": round_or_none(mean(best_abs), 6),
                "费前相对费后平均改善_百分点": round_or_none(mean(improvements), 6),
                "推荐官方口径": recommended_basis,
                "渠道算法判断": judgement,
                "下一步优化建议": "；".join(suggestions),
                "生成时间": generated_at,
            }
        )
    return output


def insert_rows(conn: sqlite3.Connection, table: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    columns = list(rows[0].keys())
    col_sql = ", ".join(f'"{column}"' for column in columns)
    placeholders = ", ".join("?" for _ in columns)
    conn.executemany(
        f'INSERT OR REPLACE INTO "{table}" ({col_sql}) VALUES ({placeholders})',
        [[row.get(column) for column in columns] for row in rows],
    )


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    columns = list(rows[0].keys())
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def build_report(strategy_items: list[dict[str, Any]], channel_items: list[dict[str, Any]], generated_at: str) -> str:
    lines = [
        "# 官方业绩偏差口径分析",
        "",
        f"- 生成时间：{generated_at}",
        f"- 可比策略数：{len(strategy_items)}",
        "",
        "## 渠道结论",
        "",
    ]
    for item in channel_items:
        lines.append(
            f"- {item['渠道ID']}：推荐口径 {item['推荐官方口径']}；"
            f"费后绝对偏差均值 {item['费后绝对偏差均值_百分点']}pct，"
            f"费前绝对偏差均值 {item['费前绝对偏差均值_百分点']}pct，"
            f"最优口径均值 {item['最优口径绝对偏差均值_百分点']}pct。"
        )
    lines.extend(
        [
            "",
            "## 输出文件",
            "",
            "- `strategy_official_deviation.csv`：策略级费前/费后与官方对比。",
            "- `channel_official_deviation.csv`：渠道级算法口径判断。",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    args = parse_args()
    generated_at = datetime.now().isoformat(timespec="seconds")
    conn = sqlite3.connect(args.db_path)
    try:
        create_tables(conn, args.algorithm_version)
        strategies = strategy_rows(conn, args.algorithm_version, generated_at)
        channels = channel_rows(strategies, args.algorithm_version, generated_at)
        insert_rows(conn, "策略官方偏差分析", strategies)
        insert_rows(conn, "渠道官方偏差分析", channels)
        conn.commit()
    finally:
        conn.close()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.output_dir / "strategy_official_deviation.csv", strategies)
    write_csv(args.output_dir / "channel_official_deviation.csv", channels)
    (args.output_dir / "summary.json").write_text(
        json.dumps(
            {
                "generated_at": generated_at,
                "algorithm_version": args.algorithm_version,
                "strategy_count": len(strategies),
                "channel_count": len(channels),
                "channels": channels,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    (args.output_dir / "official_deviation_report.md").write_text(
        build_report(strategies, channels, generated_at),
        encoding="utf-8",
    )
    print(json.dumps({"outputDir": str(args.output_dir), "strategyCount": len(strategies), "channelCount": len(channels)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
