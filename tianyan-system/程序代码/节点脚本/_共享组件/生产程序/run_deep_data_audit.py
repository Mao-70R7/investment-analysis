from __future__ import annotations

import argparse
import json
import math
import os
import re
import sqlite3
import subprocess
import sys
import time
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

from runtime_workspace import atomic_write_json, atomic_write_text, load_workspace


PROGRAM_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
WORKSPACE_ROOT = PROGRAM_ROOT.parent
SCRIPT_DIR = Path(__file__).resolve().parent

CORE_TABLES = (
    "策略信息",
    "策略当前持仓",
    "策略日度业绩",
    "策略区间业绩",
    "策略调仓事件",
    "策略调仓明细",
    "策略治理标签",
    "基金信息",
    "基金日度净值",
    "基金经济暴露快照",
)
CHILD_TABLES = (
    "策略当前持仓",
    "策略日度业绩",
    "策略区间业绩",
    "策略调仓事件",
    "策略调仓明细",
    "策略治理标签",
)
INVALID_EXPOSURE_LABELS = {"", "-", "--", "未识别", "未分类"}
EQUITY_ASSET_RE = re.compile(r"A股|港股|美股|新兴市场|其他发达市场|海外权益|存托凭证|REIT|权益|股票")
SEVERITY_ORDER = {"error": 0, "warn": 1, "ok": 2}


def now_text() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def run_id_now() -> str:
    return datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z")


def quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def parse_args() -> argparse.Namespace:
    layout = load_workspace(WORKSPACE_ROOT)
    parser = argparse.ArgumentParser(
        description="Run a read-only, cross-domain deep audit of the advisor database and formal report package."
    )
    parser.add_argument("--workspace-root", type=Path, default=layout.workspace_root)
    parser.add_argument("--db-path", type=Path, default=layout.main_db)
    parser.add_argument("--report-root", type=Path, default=layout.report_root)
    parser.add_argument("--output-root", type=Path, default=layout.output_root / "deep_data_audit")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--sample-limit", type=int, default=20)
    parser.add_argument("--skip-quick-check", action="store_true")
    parser.add_argument("--skip-standard-audit", action="store_true")
    parser.add_argument("--skip-public-fund-audit", action="store_true")
    parser.add_argument("--skip-official-channel-audit", action="store_true")
    parser.add_argument(
        "--allow-active-run",
        action="store_true",
        help="Allow read-only inspection while a live daily/main-db lock exists. Default is to stop.",
    )
    parser.add_argument("--fail-on-error", action="store_true")
    return parser.parse_args()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def open_readonly(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def process_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            completed = subprocess.run(
                ["powershell", "-NoProfile", "-Command", f"Get-Process -Id {pid} -ErrorAction Stop | Out-Null"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=10,
            )
            return completed.returncode == 0
        os.kill(pid, 0)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def inspect_locks(lock_root: Path) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    active: list[dict[str, Any]] = []
    if lock_root.is_dir():
        for path in sorted(lock_root.glob("*.lock")):
            payload: dict[str, Any] = {}
            error = ""
            try:
                raw = read_json(path)
                payload = raw if isinstance(raw, dict) else {}
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                error = f"{type(exc).__name__}: {exc}"
            pid = int(payload.get("pid") or 0)
            row = {
                "path": str(path.resolve()),
                "name": path.name,
                "pid": pid,
                "runId": str(payload.get("runId") or ""),
                "nodeId": str(payload.get("nodeId") or ""),
                "processAlive": process_exists(pid),
                "readError": error,
            }
            rows.append(row)
            if row["processAlive"]:
                active.append(row)
    protected = [row for row in active if row["name"] in {"daily_update.lock", "main_db_write.lock"}]
    return {"locks": rows, "activeLocks": active, "protectedActiveLocks": protected}


def table_names(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    }


def table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[1]) for row in conn.execute(f"PRAGMA table_info({quote_identifier(table)})")]


def scalar(conn: sqlite3.Connection, sql: str, params: Iterable[Any] = ()) -> Any:
    row = conn.execute(sql, tuple(params)).fetchone()
    return row[0] if row else None


def rows(conn: sqlite3.Connection, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, tuple(params)).fetchall()]


def add_check(
    checks: list[dict[str, Any]],
    *,
    domain: str,
    name: str,
    status: str,
    current: Any,
    threshold: str,
    detail: str,
    impact: str,
    recommendation: str,
    sample: Any = None,
) -> None:
    checks.append(
        {
            "domain": domain,
            "name": name,
            "status": status,
            "current": current,
            "threshold": threshold,
            "detail": detail,
            "impact": impact,
            "recommendation": recommendation,
            "sample": sample,
        }
    )


def database_overview(
    conn: sqlite3.Connection,
    db_path: Path,
    checks: list[dict[str, Any]],
    *,
    quick_check: bool,
) -> dict[str, Any]:
    started = time.monotonic()
    names = table_names(conn)
    missing = [name for name in CORE_TABLES if name not in names]
    duplicate_columns: list[dict[str, Any]] = []
    schema: dict[str, Any] = {}
    for table in sorted(names):
        columns = table_columns(conn, table)
        duplicates = [name for name, count in Counter(columns).items() if count > 1]
        if duplicates:
            duplicate_columns.append({"table": table, "columns": duplicates})
        schema[table] = {"columns": len(columns)}

    quick_result = "skipped"
    quick_seconds = 0.0
    if quick_check:
        quick_started = time.monotonic()
        quick_result = str(conn.execute("PRAGMA quick_check").fetchone()[0])
        quick_seconds = round(time.monotonic() - quick_started, 3)
    add_check(
        checks,
        domain="database",
        name="SQLite quick_check",
        status="ok" if quick_result in {"ok", "skipped"} else "error",
        current=quick_result,
        threshold="ok",
        detail="数据库物理一致性检查。",
        impact="失败时主库可能损坏，所有页面和分析结论均不可继续使用。",
        recommendation="停止写入并从最近通过 quick_check 的成功备份恢复。",
    )
    add_check(
        checks,
        domain="database",
        name="核心表完整",
        status="ok" if not missing else "error",
        current={"required": len(CORE_TABLES), "missing": missing},
        threshold="缺失 0 张",
        detail="策略、持仓、业绩、调仓、基金净值和经济暴露核心表必须存在。",
        impact="缺表会让相应业务域无法检查或页面直接缺数。",
        recommendation="重新执行版本化迁移和标准入库，禁止用空表绕过。",
    )
    add_check(
        checks,
        domain="database",
        name="字段名唯一",
        status="ok" if not duplicate_columns else "error",
        current={"duplicateTableCount": len(duplicate_columns)},
        threshold="0",
        detail="逐表检查 SQLite 字段名重复。",
        impact="重复字段会造成导出字段错位和页面取值歧义。",
        recommendation="修正建表或迁移脚本后重建受影响表。",
        sample=duplicate_columns[:20],
    )
    user_version = int(scalar(conn, "PRAGMA user_version") or 0)
    migration_rows = (
        int(scalar(conn, 'SELECT COUNT(*) FROM "schema_migrations"') or 0)
        if "schema_migrations" in names
        else 0
    )
    add_check(
        checks,
        domain="database",
        name="结构版本可追溯",
        status="ok" if user_version > 0 and migration_rows > 0 else "warn",
        current={"userVersion": user_version, "migrationRows": migration_rows},
        threshold="user_version>0 且 migrationRows>0",
        detail="结构版本与迁移记录应同时存在。",
        impact="缺少版本号会增加跨机器更新和失败回滚的不确定性。",
        recommendation="保持 PRAGMA user_version 与 schema_migrations 同步。",
    )
    key_counts: dict[str, int] = {}
    for table in CORE_TABLES:
        if table in names:
            key_counts[table] = int(scalar(conn, f"SELECT COUNT(*) FROM {quote_identifier(table)}") or 0)
    page_count = int(scalar(conn, "PRAGMA page_count") or 0)
    page_size = int(scalar(conn, "PRAGMA page_size") or 0)
    return {
        "path": str(db_path.resolve()),
        "bytes": db_path.stat().st_size,
        "mtime": datetime.fromtimestamp(db_path.stat().st_mtime).astimezone().isoformat(timespec="seconds"),
        "sqliteVersion": sqlite3.sqlite_version,
        "tableCount": len(names),
        "pageCount": page_count,
        "pageSize": page_size,
        "estimatedBytes": page_count * page_size,
        "quickCheck": quick_result,
        "quickCheckSeconds": quick_seconds,
        "keyTableRows": key_counts,
        "elapsedSeconds": round(time.monotonic() - started, 3),
        "schema": schema,
    }


def relationship_checks(
    conn: sqlite3.Connection, names: set[str], checks: list[dict[str, Any]], sample_limit: int
) -> dict[str, Any]:
    metrics: dict[str, Any] = {"orphans": {}, "duplicateGroups": {}}
    if "策略信息" not in names:
        return metrics
    for table in CHILD_TABLES:
        if table not in names:
            continue
        count = int(
            scalar(
                conn,
                f"""
                SELECT COUNT(*)
                FROM {quote_identifier(table)} child
                LEFT JOIN "策略信息" parent
                  ON parent."统一策略ID" = child."统一策略ID"
                WHERE parent."统一策略ID" IS NULL
                """,
            )
            or 0
        )
        metrics["orphans"][table] = count
    orphan_total = sum(metrics["orphans"].values())
    add_check(
        checks,
        domain="relationships",
        name="策略事实表无孤儿记录",
        status="ok" if orphan_total == 0 else "error",
        current={"total": orphan_total, "byTable": metrics["orphans"]},
        threshold="0",
        detail="所有策略事实表的统一策略ID必须能回连策略主表。",
        impact="孤儿记录会导致页面查不到策略主体或统计分母不一致。",
        recommendation="先定位入库批次，再补主表或删除被确认无效的孤儿事实。",
    )

    duplicate_specs = {
        "策略信息.统一策略ID": ('"策略信息"', ('统一策略ID',)),
        "策略信息.渠道策略": ('"策略信息"', ("渠道ID", "渠道策略ID")),
        "策略当前持仓": ('"策略当前持仓"', ("统一策略ID", "持仓日期", "基金代码", "分组名称")),
        "策略日度业绩": ('"策略日度业绩"', ("统一策略ID", "交易日期")),
        "策略区间业绩": ('"策略区间业绩"', ("统一策略ID", "统计日期", "区间代码")),
        "策略调仓事件": (
            '"策略调仓事件"',
            ("统一策略ID", "调仓日期", "本次仓位日期", "调仓标题", "调仓原因"),
        ),
        "策略调仓明细": ('"策略调仓明细"', ("调仓事件ID", "基金代码", "基金名称", "分组名称")),
        "基金日度净值": ('"基金日度净值"', ("基金代码", "交易日期", "净值口径")),
    }
    duplicate_samples: list[dict[str, Any]] = []
    for label, (table_sql, columns) in duplicate_specs.items():
        table = table_sql.strip('"')
        if table not in names or any(column not in table_columns(conn, table) for column in columns):
            continue
        group = ", ".join(quote_identifier(column) for column in columns)
        count = int(
            scalar(
                conn,
                f"SELECT COUNT(*) FROM (SELECT {group}, COUNT(*) c FROM {table_sql} GROUP BY {group} HAVING c>1)",
            )
            or 0
        )
        metrics["duplicateGroups"][label] = count
        if count and len(duplicate_samples) < sample_limit:
            duplicate_samples.extend(
                {
                    "object": label,
                    **row,
                }
                for row in rows(
                    conn,
                    f"SELECT {group}, COUNT(*) AS rows FROM {table_sql} GROUP BY {group} HAVING COUNT(*)>1 LIMIT ?",
                    (sample_limit - len(duplicate_samples),),
                )
            )
    duplicate_total = sum(metrics["duplicateGroups"].values())
    add_check(
        checks,
        domain="relationships",
        name="核心业务键无重复",
        status="ok" if duplicate_total == 0 else "error",
        current={"totalGroups": duplicate_total, "byObject": metrics["duplicateGroups"]},
        threshold="0",
        detail="检查策略、持仓、业绩、调仓和基金净值业务键。",
        impact="重复记录会放大收益、仓位和调仓统计。",
        recommendation="按原始批次和业务键去重，并补充相同键重复写入的门禁。",
        sample=duplicate_samples[:sample_limit],
    )
    return metrics


def channel_metrics(conn: sqlite3.Connection, names: set[str]) -> list[dict[str, Any]]:
    channels: dict[str, dict[str, Any]] = {}

    def ensure(channel: str) -> dict[str, Any]:
        return channels.setdefault(
            channel,
            {
                "channelId": channel,
                "strategies": 0,
                "currentHoldingRows": 0,
                "currentHoldingStrategies": 0,
                "dailyRows": 0,
                "dailyStrategies": 0,
                "dailyLatestDate": "",
                "intervalRows": 0,
                "intervalStrategies": 0,
                "rebalanceEvents": 0,
                "rebalanceStrategies": 0,
                "rebalanceDetails": 0,
            },
        )

    if "策略信息" in names:
        for row in rows(
            conn,
            'SELECT "渠道ID" channel, COUNT(*) strategies FROM "策略信息" GROUP BY "渠道ID" ORDER BY "渠道ID"',
        ):
            ensure(str(row["channel"] or "unknown"))["strategies"] = int(row["strategies"] or 0)
    specs = (
        ("策略当前持仓", "currentHoldingRows", "currentHoldingStrategies", None),
        ("策略日度业绩", "dailyRows", "dailyStrategies", "交易日期"),
        ("策略区间业绩", "intervalRows", "intervalStrategies", None),
        ("策略调仓事件", "rebalanceEvents", "rebalanceStrategies", None),
    )
    for table, row_key, strategy_key, date_column in specs:
        if table not in names:
            continue
        date_expr = f', MAX({quote_identifier(date_column)}) latestDate' if date_column else ""
        for row in rows(
            conn,
            f"""
            SELECT "渠道ID" channel, COUNT(*) rowCount, COUNT(DISTINCT "统一策略ID") strategyCount
                   {date_expr}
            FROM {quote_identifier(table)}
            GROUP BY "渠道ID"
            """,
        ):
            target = ensure(str(row["channel"] or "unknown"))
            target[row_key] = int(row["rowCount"] or 0)
            target[strategy_key] = int(row["strategyCount"] or 0)
            if date_column:
                target["dailyLatestDate"] = str(row["latestDate"] or "")
    if "策略调仓明细" in names:
        for row in rows(
            conn,
            'SELECT "渠道ID" channel, COUNT(*) rowCount FROM "策略调仓明细" GROUP BY "渠道ID"',
        ):
            ensure(str(row["channel"] or "unknown"))["rebalanceDetails"] = int(row["rowCount"] or 0)
    for target in channels.values():
        strategies = int(target["strategies"] or 0)
        for numerator, key in (
            ("currentHoldingStrategies", "holdingCoveragePct"),
            ("dailyStrategies", "dailyCoveragePct"),
            ("intervalStrategies", "intervalCoveragePct"),
            ("rebalanceStrategies", "rebalanceCoveragePct"),
        ):
            target[key] = round(100.0 * int(target[numerator] or 0) / strategies, 2) if strategies else None
    return [channels[key] for key in sorted(channels)]


def holding_checks(
    conn: sqlite3.Connection, names: set[str], checks: list[dict[str, Any]], sample_limit: int
) -> dict[str, Any]:
    if "策略当前持仓" not in names:
        return {}
    snapshot_rows = rows(
        conn,
        """
        SELECT
          "统一策略ID" strategyId,
          "渠道ID" channelId,
          "持仓日期" holdingDate,
          COUNT(*) holdingRows,
          SUM(CASE WHEN "基金权重_百分比" IS NULL THEN 1 ELSE 0 END) nullWeights,
          SUM(COALESCE("基金权重_百分比",0)) totalWeight,
          MIN(COALESCE("是否精确权重",0)) allExact
        FROM "策略当前持仓"
        GROUP BY "统一策略ID","渠道ID","持仓日期"
        """,
    )
    exact = [row for row in snapshot_rows if int(row["allExact"] or 0) == 1]
    bad_exact = [
        row
        for row in exact
        if row["totalWeight"] is None or abs(float(row["totalWeight"]) - 100.0) > 0.1 or int(row["nullWeights"]) > 0
    ]
    add_check(
        checks,
        domain="holdings",
        name="精确当前持仓权重闭合",
        status="ok" if not bad_exact else "error",
        current={
            "exactSnapshots": len(exact),
            "badSnapshots": len(bad_exact),
            "minTotal": round(min((float(row["totalWeight"]) for row in exact), default=0.0), 6),
            "maxTotal": round(max((float(row["totalWeight"]) for row in exact), default=0.0), 6),
        },
        threshold="每个精确快照 100%±0.1%，空权重 0",
        detail="按策略和持仓日检查精确基金权重闭合。",
        impact="权重不闭合会直接扭曲资产暴露、收益归因和调仓分析。",
        recommendation="回到原始当前仓位响应核对量纲和漏行，禁止归一化掩盖采集缺失。",
        sample=bad_exact[:sample_limit],
    )
    invalid_weights = rows(
        conn,
        """
        SELECT "统一策略ID" strategyId,"渠道ID" channelId,"基金代码" fundCode,
               "基金名称" fundName,"基金权重_百分比" weight
        FROM "策略当前持仓"
        WHERE "基金权重_百分比" IS NOT NULL
          AND ("基金权重_百分比"<0 OR "基金权重_百分比">100)
        LIMIT ?
        """,
        (sample_limit,),
    )
    invalid_weight_count = int(
        scalar(
            conn,
            """
            SELECT COUNT(*) FROM "策略当前持仓"
            WHERE "基金权重_百分比" IS NOT NULL
              AND ("基金权重_百分比"<0 OR "基金权重_百分比">100)
            """,
        )
        or 0
    )
    missing_code_count = int(
        scalar(
            conn,
            """
            SELECT COUNT(*) FROM "策略当前持仓"
            WHERE COALESCE("基金权重_百分比",0)>0
              AND COALESCE(TRIM("基金代码"),'')=''
            """,
        )
        or 0
    )
    add_check(
        checks,
        domain="holdings",
        name="当前持仓权重和基金代码有效",
        status="error" if invalid_weight_count else "warn" if missing_code_count else "ok",
        current={"invalidWeights": invalid_weight_count, "positiveWeightMissingCode": missing_code_count},
        threshold="均为 0",
        detail="权重必须位于0到100之间；正权重基金应有标准代码。",
        impact="异常权重会破坏计算，缺代码会阻断净值和基金画像关联。",
        recommendation="保留原始名称并补码；无法补码的行单列为不可穿透，不得伪造基金代码。",
        sample=invalid_weights,
    )
    return {
        "snapshotCount": len(snapshot_rows),
        "exactSnapshotCount": len(exact),
        "badExactSnapshotCount": len(bad_exact),
        "invalidWeightRows": invalid_weight_count,
        "positiveWeightMissingCodeRows": missing_code_count,
    }


def performance_checks(
    conn: sqlite3.Connection, names: set[str], checks: list[dict[str, Any]], sample_limit: int
) -> dict[str, Any]:
    if "策略日度业绩" not in names:
        return {}
    invalid_nav_count = int(
        scalar(
            conn,
            'SELECT COUNT(*) FROM "策略日度业绩" WHERE "单位净值" IS NOT NULL AND "单位净值"<=0',
        )
        or 0
    )
    extreme_count = int(
        scalar(
            conn,
            """
            SELECT COUNT(*) FROM "策略日度业绩"
            WHERE "日收益率_百分比" IS NOT NULL AND ABS("日收益率_百分比")>50
            """,
        )
        or 0
    )
    samples = rows(
        conn,
        """
        SELECT "统一策略ID" strategyId,"渠道ID" channelId,"交易日期" tradeDate,
               "单位净值" nav,"日收益率_百分比" dailyReturn
        FROM "策略日度业绩"
        WHERE ("单位净值" IS NOT NULL AND "单位净值"<=0)
           OR ("日收益率_百分比" IS NOT NULL AND ABS("日收益率_百分比")>50)
        ORDER BY ABS(COALESCE("日收益率_百分比",0)) DESC
        LIMIT ?
        """,
        (sample_limit,),
    )
    add_check(
        checks,
        domain="performance",
        name="策略日度业绩值域",
        status="error" if invalid_nav_count else "warn" if extreme_count else "ok",
        current={"nonPositiveNavRows": invalid_nav_count, "absDailyReturnOver50PctRows": extreme_count},
        threshold="非正净值 0；极端日收益应有明确证据",
        detail="检查单位净值和极端日收益。",
        impact="异常净值或收益会放大累计收益、回撤和排名。",
        recommendation="逐条回看官方曲线和拆分区段，确认单位与除权口径。",
        sample=samples,
    )
    global_latest = str(scalar(conn, 'SELECT MAX("交易日期") FROM "策略日度业绩"') or "")
    stale_rows: list[dict[str, Any]] = []
    if "策略治理标签" in names and global_latest:
        stale_rows = rows(
            conn,
            """
            WITH latest AS (
              SELECT "统一策略ID",MAX("交易日期") latestDate
              FROM "策略日度业绩"
              GROUP BY "统一策略ID"
            )
            SELECT s."统一策略ID" strategyId,s."渠道ID" channelId,s."策略名称" strategyName,
                   latest.latestDate,
                   CAST(julianday(?) - julianday(latest.latestDate) AS INTEGER) lagDays
            FROM "策略治理标签" s
            LEFT JOIN latest ON latest."统一策略ID"=s."统一策略ID"
            WHERE s."是否纳入常规排名"=1
              AND COALESCE(s."是否业绩停更",0)=0
              AND COALESCE(s."是否缺官方业绩",0)=0
              AND (latest.latestDate IS NULL OR julianday(?) - julianday(latest.latestDate)>5)
            ORDER BY lagDays DESC
            LIMIT ?
            """,
            (global_latest, global_latest, sample_limit),
        )
    stale_count = 0
    if "策略治理标签" in names and global_latest:
        stale_count = int(
            scalar(
                conn,
                """
                WITH latest AS (
                  SELECT "统一策略ID",MAX("交易日期") latestDate
                  FROM "策略日度业绩"
                  GROUP BY "统一策略ID"
                )
                SELECT COUNT(*)
                FROM "策略治理标签" s
                LEFT JOIN latest ON latest."统一策略ID"=s."统一策略ID"
                WHERE s."是否纳入常规排名"=1
                  AND COALESCE(s."是否业绩停更",0)=0
                  AND COALESCE(s."是否缺官方业绩",0)=0
                  AND (latest.latestDate IS NULL OR julianday(?) - julianday(latest.latestDate)>5)
                """,
                (global_latest,),
            )
            or 0
        )
    add_check(
        checks,
        domain="performance",
        name="在榜策略业绩新鲜度",
        status="ok" if stale_count == 0 else "error",
        current={"globalLatestDate": global_latest, "staleActiveStrategies": stale_count},
        threshold="在榜且官方业绩正常的策略滞后不超过5日",
        detail="排除已停更和官方缺业绩策略后检查最新曲线。",
        impact="滞后策略仍进入榜单会让跨产品比较失真。",
        recommendation="补采缺口或在治理标签中基于证据暂停其排名资格。",
        sample=stale_rows,
    )
    return {
        "rows": int(scalar(conn, 'SELECT COUNT(*) FROM "策略日度业绩"') or 0),
        "strategies": int(scalar(conn, 'SELECT COUNT(DISTINCT "统一策略ID") FROM "策略日度业绩"') or 0),
        "firstDate": str(scalar(conn, 'SELECT MIN("交易日期") FROM "策略日度业绩"') or ""),
        "latestDate": global_latest,
        "invalidNavRows": invalid_nav_count,
        "extremeDailyReturnRows": extreme_count,
        "staleActiveStrategies": stale_count,
    }


def rebalance_checks(
    conn: sqlite3.Connection, names: set[str], checks: list[dict[str, Any]], sample_limit: int
) -> dict[str, Any]:
    if "策略调仓事件" not in names or "策略调仓明细" not in names:
        return {}
    orphan_details = int(
        scalar(
            conn,
            """
            SELECT COUNT(*)
            FROM "策略调仓明细" d
            LEFT JOIN "策略调仓事件" e ON e."调仓事件ID"=d."调仓事件ID"
            WHERE e."调仓事件ID" IS NULL
            """,
        )
        or 0
    )
    mismatched_details = int(
        scalar(
            conn,
            """
            SELECT COUNT(*)
            FROM "策略调仓明细" d
            JOIN "策略调仓事件" e ON e."调仓事件ID"=d."调仓事件ID"
            WHERE d."统一策略ID"<>e."统一策略ID"
               OR d."渠道ID"<>e."渠道ID"
               OR d."调仓日期"<>e."调仓日期"
            """,
        )
        or 0
    )
    invalid_weights = int(
        scalar(
            conn,
            """
            SELECT COUNT(*) FROM "策略调仓明细"
            WHERE ("调前权重_百分比" IS NOT NULL AND ("调前权重_百分比"<0 OR "调前权重_百分比">100))
               OR ("调后权重_百分比" IS NOT NULL AND ("调后权重_百分比"<0 OR "调后权重_百分比">100))
            """,
        )
        or 0
    )
    inconsistent_delta = int(
        scalar(
            conn,
            """
            SELECT COUNT(*) FROM "策略调仓明细"
            WHERE "调前权重_百分比" IS NOT NULL
              AND "调后权重_百分比" IS NOT NULL
              AND "权重变化_百分比" IS NOT NULL
              AND ABS(("调后权重_百分比"-"调前权重_百分比")-"权重变化_百分比")>0.1
            """,
        )
        or 0
    )
    issue_total = orphan_details + mismatched_details + invalid_weights + inconsistent_delta
    samples = rows(
        conn,
        """
        SELECT d."调仓明细ID" detailId,d."调仓事件ID" eventId,d."统一策略ID" strategyId,
               d."渠道ID" channelId,d."调仓日期" rebalanceDate,d."基金代码" fundCode,
               d."调前权重_百分比" beforeWeight,d."调后权重_百分比" afterWeight,
               d."权重变化_百分比" deltaWeight
        FROM "策略调仓明细" d
        LEFT JOIN "策略调仓事件" e ON e."调仓事件ID"=d."调仓事件ID"
        WHERE e."调仓事件ID" IS NULL
           OR d."统一策略ID"<>e."统一策略ID"
           OR d."渠道ID"<>e."渠道ID"
           OR d."调仓日期"<>e."调仓日期"
           OR (d."调前权重_百分比" IS NOT NULL AND (d."调前权重_百分比"<0 OR d."调前权重_百分比">100))
           OR (d."调后权重_百分比" IS NOT NULL AND (d."调后权重_百分比"<0 OR d."调后权重_百分比">100))
           OR (
                d."调前权重_百分比" IS NOT NULL AND d."调后权重_百分比" IS NOT NULL
                AND d."权重变化_百分比" IS NOT NULL
                AND ABS((d."调后权重_百分比"-d."调前权重_百分比")-d."权重变化_百分比")>0.1
              )
        LIMIT ?
        """,
        (sample_limit,),
    )
    add_check(
        checks,
        domain="rebalance",
        name="调仓事件明细链路一致",
        status="ok" if issue_total == 0 else "error",
        current={
            "orphanDetails": orphan_details,
            "eventIdentityMismatch": mismatched_details,
            "invalidWeightRows": invalid_weights,
            "inconsistentDeltaRows": inconsistent_delta,
        },
        threshold="均为 0",
        detail="调仓明细必须回连事件，策略/渠道/日期一致，权重变化等于调后减调前。",
        impact="链路错误会造成调仓次数、动作和调仓贡献错误。",
        recommendation="从原始事件和快照重建明细，禁止仅按标题或日期猜测关联。",
        sample=samples,
    )
    event_without_details = int(
        scalar(
            conn,
            """
            SELECT COUNT(*)
            FROM "策略调仓事件" e
            LEFT JOIN "策略调仓明细" d ON d."调仓事件ID"=e."调仓事件ID"
            WHERE d."调仓事件ID" IS NULL
            """,
        )
        or 0
    )
    return {
        "events": int(scalar(conn, 'SELECT COUNT(*) FROM "策略调仓事件"') or 0),
        "details": int(scalar(conn, 'SELECT COUNT(*) FROM "策略调仓明细"') or 0),
        "latestEventDate": str(scalar(conn, 'SELECT MAX("调仓日期") FROM "策略调仓事件"') or ""),
        "eventWithoutDetails": event_without_details,
        "orphanDetails": orphan_details,
        "mismatchedDetails": mismatched_details,
        "invalidWeightRows": invalid_weights,
        "inconsistentDeltaRows": inconsistent_delta,
    }


def fund_checks(
    conn: sqlite3.Connection, names: set[str], checks: list[dict[str, Any]], sample_limit: int
) -> dict[str, Any]:
    required = {"策略当前持仓", "基金信息", "基金日度净值"}
    if not required.issubset(names):
        return {}
    code_predicate = (
        "LENGTH(TRIM(h.\"基金代码\"))=6 "
        "AND TRIM(h.\"基金代码\") NOT GLOB '*[^0-9]*' "
        "AND COALESCE(h.\"基金权重_百分比\",0)>0"
    )
    missing_info = int(
        scalar(
            conn,
            f"""
            SELECT COUNT(DISTINCT TRIM(h."基金代码"))
            FROM "策略当前持仓" h
            LEFT JOIN "基金信息" f ON f."基金代码"=TRIM(h."基金代码")
            WHERE {code_predicate} AND f."基金代码" IS NULL
            """,
        )
        or 0
    )
    missing_nav = int(
        scalar(
            conn,
            f"""
            SELECT COUNT(DISTINCT TRIM(h."基金代码"))
            FROM "策略当前持仓" h
            LEFT JOIN (SELECT DISTINCT "基金代码" FROM "基金日度净值") n
              ON n."基金代码"=TRIM(h."基金代码")
            WHERE {code_predicate} AND n."基金代码" IS NULL
            """,
        )
        or 0
    )
    missing_exposure = 0
    if "基金经济暴露快照" in names:
        missing_exposure = int(
            scalar(
                conn,
                f"""
                SELECT COUNT(DISTINCT TRIM(h."基金代码"))
                FROM "策略当前持仓" h
                LEFT JOIN (SELECT DISTINCT "基金代码" FROM "基金经济暴露快照") x
                  ON x."基金代码"=TRIM(h."基金代码")
                WHERE {code_predicate} AND x."基金代码" IS NULL
                """,
            )
            or 0
        )
    samples = rows(
        conn,
        f"""
        SELECT DISTINCT TRIM(h."基金代码") fundCode,h."基金名称" fundName,
               CASE WHEN f."基金代码" IS NULL THEN 1 ELSE 0 END missingFundInfo,
               CASE WHEN n."基金代码" IS NULL THEN 1 ELSE 0 END missingNav,
               CASE WHEN x."基金代码" IS NULL THEN 1 ELSE 0 END missingExposure
        FROM "策略当前持仓" h
        LEFT JOIN "基金信息" f ON f."基金代码"=TRIM(h."基金代码")
        LEFT JOIN (SELECT DISTINCT "基金代码" FROM "基金日度净值") n
          ON n."基金代码"=TRIM(h."基金代码")
        LEFT JOIN (SELECT DISTINCT "基金代码" FROM "基金经济暴露快照") x
          ON x."基金代码"=TRIM(h."基金代码")
        WHERE {code_predicate}
          AND (f."基金代码" IS NULL OR n."基金代码" IS NULL OR x."基金代码" IS NULL)
        LIMIT ?
        """,
        (sample_limit,),
    )
    add_check(
        checks,
        domain="funds",
        name="当前持仓基金可关联",
        status="error" if missing_info or missing_nav else "warn" if missing_exposure else "ok",
        current={
            "missingFundInfo": missing_info,
            "missingNav": missing_nav,
            "missingEconomicExposure": missing_exposure,
        },
        threshold="均为 0",
        detail="当前正权重标准基金代码必须能关联基金主表、净值和经济暴露。",
        impact="缺主表/净值会阻断收益计算；缺暴露会削弱资产和行业分析。",
        recommendation="按基金代码补采真实净值与公开披露；无法取得时保留缺口状态。",
        sample=samples,
    )
    invalid_nav = int(
        scalar(
            conn,
            'SELECT COUNT(*) FROM "基金日度净值" WHERE "单位净值" IS NOT NULL AND "单位净值"<=0',
        )
        or 0
    )
    future_nav = int(
        scalar(
            conn,
            'SELECT COUNT(*) FROM "基金日度净值" WHERE date("交易日期")>date(?)',
            (date.today().isoformat(),),
        )
        or 0
    )
    add_check(
        checks,
        domain="funds",
        name="基金净值值域与日期",
        status="ok" if invalid_nav == 0 and future_nav == 0 else "error",
        current={"nonPositiveNavRows": invalid_nav, "futureDateRows": future_nav},
        threshold="均为 0",
        detail="非货币单位净值不得非正，净值日期不得晚于当前日期。",
        impact="异常净值会污染基金收益、策略模拟净值和调仓评价。",
        recommendation="核对原始接口单位、货币基金口径和交易日期解析。",
    )
    return {
        "fundInfoRows": int(scalar(conn, 'SELECT COUNT(*) FROM "基金信息"') or 0),
        "fundNavRows": int(scalar(conn, 'SELECT COUNT(*) FROM "基金日度净值"') or 0),
        "fundNavFunds": int(scalar(conn, 'SELECT COUNT(DISTINCT "基金代码") FROM "基金日度净值"') or 0),
        "fundNavFirstDate": str(scalar(conn, 'SELECT MIN("交易日期") FROM "基金日度净值"') or ""),
        "fundNavLatestDate": str(scalar(conn, 'SELECT MAX("交易日期") FROM "基金日度净值"') or ""),
        "missingFundInfo": missing_info,
        "missingNav": missing_nav,
        "missingExposure": missing_exposure,
        "invalidNavRows": invalid_nav,
        "futureNavRows": future_nav,
    }


def parse_number_map(value: Any) -> tuple[dict[str, float], str]:
    if value in (None, ""):
        return {}, ""
    try:
        payload = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        return {}, f"{type(exc).__name__}: {exc}"
    if not isinstance(payload, dict):
        return {}, "not_object"
    result: dict[str, float] = {}
    for key, item in payload.items():
        if isinstance(item, bool):
            continue
        try:
            number = float(item)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            result[str(key).strip()] = number
    return result, ""


def exposure_checks(
    conn: sqlite3.Connection, names: set[str], checks: list[dict[str, Any]], sample_limit: int
) -> dict[str, Any]:
    if "基金经济暴露快照" not in names:
        return {}
    exposure_rows = rows(
        conn,
        """
        WITH latest AS (
          SELECT "基金代码",MAX("报告期") reportDate
          FROM "基金经济暴露快照"
          GROUP BY "基金代码"
        )
        SELECT x."基金代码" fundCode,x."基金名称" fundName,x."报告期" reportDate,
               x."经济资产暴露JSON" assetJson,x."经济行业暴露JSON" industryJson,
               x."质量状态" qualityStatus
        FROM "基金经济暴露快照" x
        JOIN latest ON latest."基金代码"=x."基金代码" AND latest.reportDate=x."报告期"
        """,
    )
    parse_errors: list[dict[str, Any]] = []
    asset_sum_outliers: list[dict[str, Any]] = []
    invalid_labels: list[dict[str, Any]] = []
    industry_over_equity: list[dict[str, Any]] = []
    for row in exposure_rows:
        assets, asset_error = parse_number_map(row.get("assetJson"))
        industries, industry_error = parse_number_map(row.get("industryJson"))
        if asset_error or industry_error:
            parse_errors.append(
                {
                    "fundCode": row.get("fundCode"),
                    "assetError": asset_error,
                    "industryError": industry_error,
                }
            )
            continue
        asset_sum = sum(assets.values())
        if assets and not 98.0 <= asset_sum <= 102.0:
            asset_sum_outliers.append(
                {"fundCode": row.get("fundCode"), "fundName": row.get("fundName"), "assetSum": round(asset_sum, 4)}
            )
        bad = sorted(label for label in set(assets) | set(industries) if label in INVALID_EXPOSURE_LABELS)
        if bad:
            invalid_labels.append({"fundCode": row.get("fundCode"), "labels": bad})
        equity_share = sum(value for label, value in assets.items() if EQUITY_ASSET_RE.search(label))
        industry_sum = sum(industries.values())
        if industries and equity_share > 0 and industry_sum > equity_share + 0.5:
            industry_over_equity.append(
                {
                    "fundCode": row.get("fundCode"),
                    "fundName": row.get("fundName"),
                    "equityShare": round(equity_share, 4),
                    "industrySum": round(industry_sum, 4),
                }
            )
    status = (
        "error"
        if parse_errors or invalid_labels or industry_over_equity
        else "warn"
        if asset_sum_outliers
        else "ok"
    )
    add_check(
        checks,
        domain="exposure",
        name="基金经济暴露口径",
        status=status,
        current={
            "latestFunds": len(exposure_rows),
            "jsonParseErrors": len(parse_errors),
            "assetSumOutside98To102": len(asset_sum_outliers),
            "invalidLabelFunds": len(invalid_labels),
            "industryOverEquityFunds": len(industry_over_equity),
        },
        threshold="JSON可解析；资产约100%；无无效标签；行业不超过权益资产",
        detail="按每只基金最新报告期检查总资产口径和行业约束。",
        impact="口径错误会直接误导策略资产、行业和主题暴露。",
        recommendation="从基金经济暴露快照修复资产与行业量纲；标签只能作证据，不能制造100%暴露。",
        sample={
            "parseErrors": parse_errors[:sample_limit],
            "assetSumOutliers": asset_sum_outliers[:sample_limit],
            "invalidLabels": invalid_labels[:sample_limit],
            "industryOverEquity": industry_over_equity[:sample_limit],
        },
    )
    return {
        "latestFunds": len(exposure_rows),
        "jsonParseErrors": len(parse_errors),
        "assetSumOutliers": len(asset_sum_outliers),
        "invalidLabelFunds": len(invalid_labels),
        "industryOverEquityFunds": len(industry_over_equity),
    }


def latest_daily_summary(log_root: Path) -> tuple[Path | None, dict[str, Any]]:
    candidates: list[Path] = []
    daily_root = log_root / "daily_update"
    if daily_root.is_dir():
        for day in daily_root.iterdir():
            if not day.is_dir():
                continue
            for run in day.iterdir():
                path = run / "summary.json"
                if path.is_file():
                    candidates.append(path)
    if not candidates:
        return None, {}
    latest = max(candidates, key=lambda path: path.stat().st_mtime_ns)
    try:
        payload = read_json(latest)
        return latest, payload if isinstance(payload, dict) else {}
    except (OSError, ValueError, json.JSONDecodeError):
        return latest, {}


def production_checks(layout: Any, checks: list[dict[str, Any]]) -> dict[str, Any]:
    summary_path, summary = latest_daily_summary(layout.log_root)
    status = str(summary.get("status") or "missing")
    dry_run = bool(summary.get("dryRun"))
    completed = list(summary.get("completedNodes") or [])
    add_check(
        checks,
        domain="production",
        name="最近日更批次可追溯",
        status="error" if not summary_path or dry_run or status in {"failed", "error"} else "warn" if "warning" in status else "ok",
        current={
            "path": str(summary_path.resolve()) if summary_path else "",
            "status": status,
            "dryRun": dry_run,
            "completedNodes": len(completed),
        },
        threshold="真实运行、非失败、摘要存在",
        detail="检查最近根级日更摘要，排除嵌套子步骤摘要。",
        impact="无法确认最近成功批次时，页面和数据库水位可能不是同一次运行。",
        recommendation="从唯一入口重跑失败节点并核对每个节点的 node_result.validation。",
    )
    backup_files = sorted(layout.backup_root.glob("*.sqlite"), key=lambda path: path.stat().st_mtime_ns, reverse=True)
    metadata_files = sorted(layout.backup_root.glob("*.json"), key=lambda path: path.stat().st_mtime_ns, reverse=True)
    add_check(
        checks,
        domain="production",
        name="成功数据库备份数量受控",
        status="ok" if len(backup_files) == 1 and metadata_files else "warn",
        current={"sqliteBackups": len(backup_files), "metadataFiles": len(metadata_files)},
        threshold="1 个成功库且有元数据",
        detail="检查正式备份目录，不删除任何文件。",
        impact="无成功备份会降低主库损坏后的恢复能力；过多备份会占用大量空间。",
        recommendation="只保留最近一个通过 quick_check 的成功备份并保留元数据。",
    )
    return {
        "latestDailySummaryPath": str(summary_path.resolve()) if summary_path else "",
        "latestDailySummary": summary,
        "backupFiles": [
            {
                "path": str(path.resolve()),
                "bytes": path.stat().st_size,
                "mtime": datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(timespec="seconds"),
            }
            for path in backup_files
        ],
        "backupMetadata": [str(path.resolve()) for path in metadata_files],
    }


def parse_json_from_output(text: str) -> Any:
    stripped = text.strip()
    if not stripped:
        return None
    positions = [index for index, char in enumerate(stripped) if char in "[{"]
    for index in positions:
        try:
            return json.loads(stripped[index:])
        except json.JSONDecodeError:
            continue
    return None


def run_component(
    *,
    name: str,
    script: Path,
    arguments: list[str],
    cwd: Path,
    timeout_seconds: int,
) -> dict[str, Any]:
    command = [sys.executable, "-X", "utf8", str(script.relative_to(cwd)), *arguments]
    started = time.monotonic()
    print(
        f"[DEEP-AUDIT][COMPONENT][START] name={name} timeoutSeconds={timeout_seconds}",
        flush=True,
    )
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
            check=False,
        )
        print(
            "[DEEP-AUDIT][COMPONENT][DONE] "
            f"name={name} exitCode={completed.returncode} "
            f"elapsedSeconds={round(time.monotonic() - started, 3)}",
            flush=True,
        )
        return {
            "name": name,
            "command": command,
            "exitCode": completed.returncode,
            "elapsedSeconds": round(time.monotonic() - started, 3),
            "stdoutTail": completed.stdout[-4000:],
            "stderrTail": completed.stderr[-4000:],
            "parsedStdout": parse_json_from_output(completed.stdout),
        }
    except subprocess.TimeoutExpired as exc:
        print(
            "[DEEP-AUDIT][COMPONENT][TIMEOUT] "
            f"name={name} elapsedSeconds={round(time.monotonic() - started, 3)}",
            flush=True,
        )
        return {
            "name": name,
            "command": command,
            "exitCode": 124,
            "elapsedSeconds": round(time.monotonic() - started, 3),
            "stdoutTail": str(exc.stdout or "")[-4000:],
            "stderrTail": str(exc.stderr or "")[-4000:],
            "error": "timeout",
        }


def newest_file(root: Path, name: str) -> Path | None:
    candidates = list(root.rglob(name)) if root.is_dir() else []
    return max(candidates, key=lambda path: path.stat().st_mtime_ns) if candidates else None


def component_audits(
    *,
    args: argparse.Namespace,
    layout: Any,
    run_dir: Path,
    checks: list[dict[str, Any]],
) -> dict[str, Any]:
    components: dict[str, Any] = {}
    component_root = run_dir / "components"
    component_root.mkdir(parents=True, exist_ok=True)
    if not args.skip_standard_audit:
        output_root = component_root / "standard_audit"
        result = run_component(
            name="standard_audit",
            script=SCRIPT_DIR / "标准化数据稽核.py",
            arguments=[
                "--db-path",
                str(args.db_path.resolve()),
                "--site-dir",
                str(args.report_root.resolve() / "basic_data"),
                "--output-root",
                str(output_root.resolve()),
            ],
            cwd=PROGRAM_ROOT,
            timeout_seconds=1200,
        )
        report_path = newest_file(output_root, "data_audit_report.json")
        report = read_json(report_path) if report_path else {}
        result["reportPath"] = str(report_path.resolve()) if report_path else ""
        result["report"] = report
        components["standardAudit"] = result
        status = str(report.get("status") or ("error" if result["exitCode"] else "warn"))
        add_check(
            checks,
            domain="authoritative_audits",
            name="项目标准化数据稽核",
            status=status if status in {"ok", "pass", "warn", "error"} else "error",
            current=report.get("summary") or {"exitCode": result["exitCode"]},
            threshold="error=0",
            detail="运行项目现有79+条正式规则，结果作为最终业务判断的权威门禁。",
            impact="存在error时正式页面包或主库至少有一项不可接受的不一致。",
            recommendation="按报告中的责任脚本和根因逐条处理，不弱化规则。",
            sample=[
                {
                    "ruleId": issue.get("ruleId"),
                    "severity": issue.get("severity"),
                    "scope": issue.get("scope"),
                    "item": issue.get("item"),
                    "detail": issue.get("detail"),
                }
                for issue in list(report.get("issues") or [])[: args.sample_limit]
            ],
        )
    if not args.skip_public_fund_audit:
        output_root = component_root / "public_fund"
        result = run_component(
            name="public_fund_audit",
            script=SCRIPT_DIR / "audit_public_fund_data_validity.py",
            arguments=["--db-path", str(args.db_path.resolve()), "--output-root", str(output_root.resolve())],
            cwd=PROGRAM_ROOT,
            timeout_seconds=900,
        )
        report_path = newest_file(output_root, "audit_summary.json")
        report = read_json(report_path) if report_path else {}
        result["reportPath"] = str(report_path.resolve()) if report_path else ""
        result["report"] = report
        components["publicFundAudit"] = result
        status = str(report.get("status") or ("error" if result["exitCode"] else "warn"))
        add_check(
            checks,
            domain="authoritative_audits",
            name="全市场公募基金有效性稽核",
            status=status if status in {"pass", "warn", "error"} else "error",
            current=report.get("summary") or report.get("issueSummary") or {"exitCode": result["exitCode"]},
            threshold="error=0",
            detail="检查公募基金净值点、固定区间完整性、基准向量、主份额及抽样重算。",
            impact="错误会污染策略持仓基金的收益、同类排名和风险比较。",
            recommendation="按公募基金稽核报告修复源数据或计算窗口。",
        )
    if not args.skip_official_channel_audit:
        output_root = component_root / "official_channels"
        result = run_component(
            name="official_channel_audit",
            script=SCRIPT_DIR / "audit_official_app_channels_quality.py",
            arguments=[
                "--db-path",
                str(args.db_path.resolve()),
                "--normalized-root",
                str(layout.normalized_root.resolve()),
                "--output-root",
                str(output_root.resolve()),
            ],
            cwd=PROGRAM_ROOT,
            timeout_seconds=900,
        )
        report_path = newest_file(output_root, "official_app_channel_quality_summary.json")
        report = read_json(report_path) if report_path else {}
        result["reportPath"] = str(report_path.resolve()) if report_path else ""
        result["report"] = report
        components["officialChannelAudit"] = result
        channel_rows = list(report.get("channels") or report.get("渠道") or [])
        channel_summary = report.get("渠道汇总") or {}
        if not isinstance(channel_summary, dict):
            channel_summary = {}
        reported_problem_items = sum(
            int(row.get("问题策略数") or row.get("异常策略数") or row.get("problemStrategyCount") or 0)
            for row in channel_summary.values()
            if isinstance(row, dict)
        )
        source_mismatch_count = sum(
            1
            for row in channel_summary.values()
            if isinstance(row, dict)
            for item in list(row.get("原始与入库核对") or [])
            if isinstance(item, dict) and str(item.get("核对结论") or "") == "不一致"
        )
        status = (
            "error"
            if result["exitCode"] or not report_path
            else "warn"
            if reported_problem_items or source_mismatch_count
            else "ok"
        )
        add_check(
            checks,
            domain="authoritative_audits",
            name="官方渠道原始数据与主库一致性",
            status=status,
            current={
                "channels": len(channel_rows),
                "reportedProblemItems": reported_problem_items,
                "sourceDbMismatchObjects": source_mismatch_count,
                "exitCode": result["exitCode"],
            },
            threshold="组件成功且问题条目 0",
            detail="逐渠道核对normalized原始批次、主库行数、当前持仓和基金净值关联；同一策略的多类问题分别计数。",
            impact="渠道原始数与主库不一致会造成漏入库、历史沿用或错误覆盖。",
            recommendation="区分历史累计与当前批次口径后，按渠道门禁重新采集/验收；仅替换通过门禁的批次。",
        )
    integrity_result = run_component(
        name="page_integrity",
        script=SCRIPT_DIR / "audit_basic_data_deploy_integrity.py",
        arguments=["--report-root", str(args.report_root.resolve()), "--db-path", str(args.db_path.resolve())],
        cwd=PROGRAM_ROOT,
        timeout_seconds=600,
    )
    integrity_payload = integrity_result.get("parsedStdout")
    integrity_result["report"] = integrity_payload if isinstance(integrity_payload, dict) else {}
    components["pageIntegrity"] = integrity_result
    integrity_status = str((integrity_payload or {}).get("status") or "error")
    add_check(
        checks,
        domain="authoritative_audits",
        name="正式页面与主库完整性",
        status="ok" if integrity_status == "passed" else "error",
        current={
            "status": integrity_status,
            "failures": len((integrity_payload or {}).get("failures") or []),
            "warnings": len((integrity_payload or {}).get("warnings") or []),
        },
        threshold="passed",
        detail="核对策略数、详情文件、近30日调仓事件和洞察包。",
        impact="失败会导致页面漏策略、详情断链或调仓统计与主库不一致。",
        recommendation="重建受影响页面包并重新生成部署清单。",
        sample=(integrity_payload or {}).get("failures") or (integrity_payload or {}).get("warnings"),
    )
    return components


def page_package_checks(report_root: Path, checks: list[dict[str, Any]]) -> dict[str, Any]:
    manifest_path = report_root / "deployment_manifest.json"
    manifest: dict[str, Any] = {}
    if manifest_path.is_file():
        try:
            payload = read_json(manifest_path)
            manifest = payload if isinstance(payload, dict) else {}
        except (OSError, ValueError, json.JSONDecodeError):
            manifest = {}
    manifest_status = str(manifest.get("status") or "missing")
    missing = list(manifest.get("missing") or [])
    add_check(
        checks,
        domain="pages",
        name="正式部署清单就绪",
        status="ok" if manifest_status == "ready" and not missing else "error",
        current={"status": manifest_status, "missing": missing},
        threshold="ready 且 missing=[]",
        detail="正式结果目录必须有完整部署清单。",
        impact="清单缺失或不完整时页面发布不可审计。",
        recommendation="完成页面包构建、完整性检查后重新生成清单。",
    )
    quality_path = report_root / "basic_data" / "data" / "data_quality_pack.json"
    quality: dict[str, Any] = {}
    if quality_path.is_file():
        try:
            payload = read_json(quality_path)
            quality = payload if isinstance(payload, dict) else {}
        except (OSError, ValueError, json.JSONDecodeError):
            quality = {}
    quality_status = str(quality.get("status") or quality.get("状态") or "missing")
    quality_checks = list(quality.get("checks") or [])
    return {
        "reportRoot": str(report_root.resolve()),
        "manifestPath": str(manifest_path.resolve()),
        "manifest": manifest,
        "qualityPackPath": str(quality_path.resolve()),
        "qualityStatus": quality_status,
        "qualityChecks": quality_checks,
    }


def rollup_status(checks: list[dict[str, Any]]) -> tuple[str, dict[str, int]]:
    counts = Counter(str(check.get("status") or "error") for check in checks)
    normalized = {
        "error": int(counts.get("error", 0)),
        "warn": int(counts.get("warn", 0)),
        "ok": int(counts.get("ok", 0) + counts.get("pass", 0) + counts.get("passed", 0)),
        "total": len(checks),
    }
    status = "error" if normalized["error"] else "warn" if normalized["warn"] else "ok"
    return status, normalized


def md_escape(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    else:
        text = str(value)
    return text.replace("|", "\\|").replace("\n", "<br>")


def markdown_table(headers: list[str], data_rows: list[list[Any]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    lines.extend("| " + " | ".join(md_escape(value) for value in row) + " |" for row in data_rows)
    return lines


def render_report(report: dict[str, Any]) -> str:
    summary = report["summary"]
    checks = list(report["checks"])
    findings = sorted(
        (check for check in checks if check["status"] in {"error", "warn"}),
        key=lambda item: (SEVERITY_ORDER.get(item["status"], 9), item["domain"], item["name"]),
    )
    lines = [
        "# 投顾系统深度数据检查报告",
        "",
        f"- 生成时间：{report['generatedAt']}",
        f"- 总体状态：**{report['status']}**",
        f"- 数据库：`{report['database']['path']}`",
        f"- 正式页面：`{report['pages']['reportRoot']}`",
        f"- 检查项：{summary['total']}；error={summary['error']}，warn={summary['warn']}，ok={summary['ok']}",
        f"- SQLite quick_check：{report['database']['quickCheck']}（{report['database']['quickCheckSeconds']} 秒）",
        "",
        "## 结论",
        "",
    ]
    if report["status"] == "error":
        lines.append("当前数据存在阻断级问题，不建议把受影响页面或指标作为完整、可比的正式结论。")
    elif report["status"] == "warn":
        lines.append("核心结构未发现阻断错误，但仍有需披露的质量缺口；使用相关页面或指标时应保留限制说明。")
    else:
        lines.append("本次深度检查未发现阻断或警告项。")
    lines.extend(["", "## 主要问题", ""])
    if not findings:
        lines.append("- 无。")
    else:
        lines.extend(
            markdown_table(
                ["级别", "领域", "检查项", "当前值", "影响", "建议"],
                [
                    [
                        item["status"],
                        item["domain"],
                        item["name"],
                        item["current"],
                        item["impact"],
                        item["recommendation"],
                    ]
                    for item in findings
                ],
            )
        )
    lines.extend(["", "## 渠道覆盖总览", ""])
    channel_rows = report["channels"]
    lines.extend(
        markdown_table(
            [
                "渠道",
                "策略",
                "持仓策略/行",
                "日度策略/行",
                "日度最新日",
                "区间策略/行",
                "调仓策略/事件/明细",
            ],
            [
                [
                    row["channelId"],
                    row["strategies"],
                    f"{row['currentHoldingStrategies']}/{row['currentHoldingRows']}",
                    f"{row['dailyStrategies']}/{row['dailyRows']}",
                    row["dailyLatestDate"],
                    f"{row['intervalStrategies']}/{row['intervalRows']}",
                    f"{row['rebalanceStrategies']}/{row['rebalanceEvents']}/{row['rebalanceDetails']}",
                ]
                for row in channel_rows
            ],
        )
    )
    lines.extend(["", "## 全部检查项", ""])
    lines.extend(
        markdown_table(
            ["状态", "领域", "检查项", "当前值", "门槛", "说明"],
            [
                [
                    item["status"],
                    item["domain"],
                    item["name"],
                    item["current"],
                    item["threshold"],
                    item["detail"],
                ]
                for item in sorted(checks, key=lambda item: (item["domain"], item["name"]))
            ],
        )
    )
    standard = ((report.get("components") or {}).get("standardAudit") or {}).get("report") or {}
    standard_issues = list(standard.get("issues") or [])
    lines.extend(["", "## 标准化稽核明细", ""])
    if not standard_issues:
        lines.append("- 本次未产生标准化稽核 issue，或运行时跳过该组件。")
    else:
        for issue in standard_issues:
            lines.extend(
                [
                    f"### {issue.get('severity', '')}: {issue.get('ruleId', '')} / {issue.get('item', '')}",
                    "",
                    f"- 范围：{issue.get('scope', '')}",
                    f"- 详情：{issue.get('detail', '')}",
                    f"- 原因：{issue.get('原因说明', '')}",
                    f"- 影响：{issue.get('影响说明', issue.get('影响页面', '见详情与规则说明'))}",
                    f"- 建议：{issue.get('优化建议', '')}",
                    f"- 责任脚本：{issue.get('修复责任脚本', '')}",
                    "",
                ]
            )
    lines.extend(
        [
            "## 关键规模",
            "",
            *markdown_table(
                ["对象", "行数"],
                [[name, count] for name, count in report["database"]["keyTableRows"].items()],
            ),
            "",
            "## 证据文件",
            "",
            f"- JSON报告：`{report['artifacts']['json']}`",
            f"- Markdown报告：`{report['artifacts']['markdown']}`",
            f"- 标准化稽核：`{((report.get('components') or {}).get('standardAudit') or {}).get('reportPath', '')}`",
            f"- 公募基金稽核：`{((report.get('components') or {}).get('publicFundAudit') or {}).get('reportPath', '')}`",
            f"- 官方渠道稽核：`{((report.get('components') or {}).get('officialChannelAudit') or {}).get('reportPath', '')}`",
            f"- 页面清单：`{report['pages']['manifestPath']}`",
            f"- 最近日更摘要：`{report['production']['latestDailySummaryPath']}`",
            "",
            "## 口径限制",
            "",
            "- 本脚本只读主库和页面包，不修复、不归一化、不删除数据。",
            "- 无仓位或无调仓不自动等同于采集失败；渠道口径由现有标准化稽核和渠道门禁共同判定。",
            "- 官方未披露的数据保留为真实缺口，不使用推荐清单、候选池或模拟结果冒充实际持仓和调仓。",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    args.workspace_root = args.workspace_root.resolve()
    layout = load_workspace(args.workspace_root)
    args.db_path = args.db_path.resolve()
    args.report_root = args.report_root.resolve()
    args.output_root = args.output_root.resolve()
    run_id = args.run_id.strip() or run_id_now()
    run_dir = args.output_root / datetime.now().astimezone().strftime("%Y-%m-%d") / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    lock_state = inspect_locks(layout.lock_root)
    if lock_state["protectedActiveLocks"] and not args.allow_active_run:
        payload = {
            "generatedAt": now_text(),
            "status": "blocked",
            "reason": "active production lock; deep audit did not start",
            "locks": lock_state,
        }
        atomic_write_json(run_dir / "deep_data_audit_report.json", payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 3
    if not args.db_path.is_file():
        raise FileNotFoundError(f"analysis database missing: {args.db_path}")
    if not (args.report_root / "basic_data").is_dir():
        raise FileNotFoundError(f"formal basic_data directory missing: {args.report_root / 'basic_data'}")

    checks: list[dict[str, Any]] = []
    started = time.monotonic()
    total_stages = 8

    def progress(completed: int, action: str) -> None:
        print(
            "[DEEP-AUDIT][PROGRESS] "
            f"completedStages={completed} totalStages={total_stages} current={action}",
            flush=True,
        )

    progress(0, "数据库完整性、表结构与外键关系")
    with open_readonly(args.db_path) as conn:
        names = table_names(conn)
        database = database_overview(
            conn,
            args.db_path,
            checks,
            quick_check=not args.skip_quick_check,
        )
        relationships = relationship_checks(conn, names, checks, max(1, args.sample_limit))
        progress(1, "渠道覆盖与业务主键")
        channels = channel_metrics(conn, names)
        progress(2, "当前持仓、仓位占比与快照一致性")
        holdings = holding_checks(conn, names, checks, max(1, args.sample_limit))
        progress(3, "业绩、调仓事件与调仓明细")
        performance = performance_checks(conn, names, checks, max(1, args.sample_limit))
        rebalances = rebalance_checks(conn, names, checks, max(1, args.sample_limit))
        progress(4, "基金净值、基金关联与经济暴露")
        funds = fund_checks(conn, names, checks, max(1, args.sample_limit))
        exposures = exposure_checks(conn, names, checks, max(1, args.sample_limit))

    progress(5, "生产运行、备份与正式页面包")
    production = production_checks(layout, checks)
    pages = page_package_checks(args.report_root, checks)
    progress(6, "标准稽核、公募基金、官方渠道及页面完整性组件")
    components = component_audits(args=args, layout=layout, run_dir=run_dir, checks=checks)
    progress(7, "汇总问题并生成JSON和Markdown报告")
    status, summary = rollup_status(checks)
    json_path = run_dir / "deep_data_audit_report.json"
    markdown_path = run_dir / "deep_data_audit_report.md"
    report = {
        "version": 1,
        "generatedAt": now_text(),
        "status": status,
        "summary": summary,
        "elapsedSeconds": round(time.monotonic() - started, 3),
        "workspaceRoot": str(args.workspace_root),
        "locks": lock_state,
        "database": database,
        "channels": channels,
        "relationships": relationships,
        "holdings": holdings,
        "performance": performance,
        "rebalances": rebalances,
        "funds": funds,
        "exposures": exposures,
        "production": production,
        "pages": pages,
        "components": components,
        "checks": checks,
        "artifacts": {"json": str(json_path.resolve()), "markdown": str(markdown_path.resolve())},
    }
    atomic_write_json(json_path, report)
    atomic_write_text(markdown_path, render_report(report))
    latest_summary = {
        "generatedAt": report["generatedAt"],
        "status": status,
        "summary": summary,
        "elapsedSeconds": report["elapsedSeconds"],
        "reportJson": str(json_path.resolve()),
        "reportMarkdown": str(markdown_path.resolve()),
    }
    atomic_write_json(args.output_root / "latest_summary.json", latest_summary)
    atomic_write_text(args.output_root / "latest_report_path.txt", str(markdown_path.resolve()) + "\n")
    progress(8, "完成")
    print(json.dumps(latest_summary, ensure_ascii=False, indent=2))
    return 1 if args.fail_on_error and status == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
