from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path


SCRIPT_PATH = next(
    path
    for parent in Path(__file__).resolve().parents
    for path in (parent / "_共享组件" / "生产程序").glob("*母子关系.py")
    if path.is_file()
)
SPEC = importlib.util.spec_from_file_location("strategy_parent_child_relationships", SCRIPT_PATH)
assert SPEC and SPEC.loader
sys.path.insert(0, str(SCRIPT_PATH.parent))
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class StrategyParentChildRelationshipTests(unittest.TestCase):
    def test_identifies_shared_official_curve_with_multiple_strong_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            normalized = root / "normalized" / "gffunds"
            master_path = normalized / "strategy_master" / "2026-07-29" / "run.jsonl"
            performance_path = normalized / "strategy_performance_daily" / "2026-07-29" / "run.jsonl"
            master_path.parent.mkdir(parents=True)
            performance_path.parent.mkdir(parents=True)
            common_extra = {"protocol_name": "幸福小心愿目标盈策略说明书"}
            master_rows = [
                {
                    "channel_id": "gffunds",
                    "source_strategy_id": "GFJJ001507",
                    "strategy_name": "幸福小心愿目标盈",
                    "advisor_name": "广发基金",
                    "last_seen_at": "2026-07-29T01:00:00+08:00",
                    "extra": {**common_extra, "ds_adv_type": "2"},
                },
                {
                    "channel_id": "gffunds",
                    "source_strategy_id": "ZY00000056",
                    "strategy_name": "小心愿目标盈第19期",
                    "advisor_name": "广发基金投顾",
                    "last_seen_at": "2026-07-29T01:00:00+08:00",
                    "extra": {**common_extra, "ds_adv_type": "3"},
                },
            ]
            master_path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in master_rows), encoding="utf-8")
            performance_rows = []
            start = date(2026, 1, 1)
            for offset in range(130):
                trade_date = (start + timedelta(days=offset)).isoformat()
                for strategy_id in ["GFJJ001507", "ZY00000056"]:
                    performance_rows.append(
                        {
                            "source_strategy_id": strategy_id,
                            "trade_date": trade_date,
                            "cumulative_return": round(offset * 0.01, 4),
                        }
                    )
            performance_path.write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in performance_rows),
                encoding="utf-8",
            )

            database = root / "analysis.sqlite"
            conn = sqlite3.connect(database)
            try:
                conn.executescript(
                    '''
                    CREATE TABLE "策略信息" ("统一策略ID" TEXT PRIMARY KEY);
                    CREATE TABLE "策略当前持仓" (
                        "统一策略ID" TEXT, "渠道ID" TEXT, "持仓日期" TEXT,
                        "基金代码" TEXT, "基金名称" TEXT, "基金权重_百分比" REAL
                    );
                    CREATE TABLE "策略调仓事件" ("统一策略ID" TEXT, "渠道ID" TEXT, "调仓日期" TEXT);
                    '''
                )
                conn.executemany('INSERT INTO "策略信息" VALUES (?)', [("gffunds__GFJJ001507",), ("gffunds__ZY00000056",)])
                for strategy_id in ["gffunds__GFJJ001507", "gffunds__ZY00000056"]:
                    conn.execute(
                        'INSERT INTO "策略当前持仓" VALUES (?, ?, ?, ?, ?, ?)',
                        (strategy_id, "gffunds", "2026-03-31", "000001", "测试基金", 100.0),
                    )
                    conn.execute(
                        'INSERT INTO "策略调仓事件" VALUES (?, ?, ?)',
                        (strategy_id, "gffunds", "2026-03-31"),
                    )
                detected = MODULE.build_relationships(conn, normalized)
                self.assertEqual(len(detected), 1)
                self.assertEqual(detected[0]["child"], "gffunds__ZY00000056")
                self.assertEqual(detected[0]["parent"], "gffunds__GFJJ001507")
                self.assertTrue(detected[0]["official_alias"])
                self.assertGreaterEqual(detected[0]["score"], 90)
                self.assertEqual(detected[0]["evidence"]["childAdvisorName"], "广发基金投顾")
                self.assertEqual(detected[0]["evidence"]["parentAdvisorName"], "广发基金")
                self.assertEqual(detected[0]["evidence"]["canonicalAdvisorName"], "广发基金")
                MODULE.ensure_table(conn)
                conn.execute(
                    '''
                    INSERT INTO "策略关系" (
                        "子策略ID", "母策略ID", "渠道ID", "关系类型", "官方业绩策略ID", "持仓策略ID", "调仓策略ID",
                        "置信度", "置信分", "关系状态", "证据JSON", "规则版本", "连续不一致次数", "首次识别时间", "最近复核时间"
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''',
                    (
                        "gffunds__ZY00000056",
                        "gffunds__GFJJ001507",
                        "gffunds",
                        "母策略期次",
                        "gffunds__GFJJ001507",
                        "gffunds__GFJJ001507",
                        "gffunds__GFJJ001507",
                        "high",
                        100.0,
                        "review",
                        "{}",
                        "old_rule",
                        6,
                        "2026-07-29T01:00:00+08:00",
                        "2026-08-06T01:00:00+08:00",
                    ),
                )
                counts = MODULE.persist_relationships(conn, detected)
                self.assertEqual(counts["officialAliases"], 1)
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM "策略关系"').fetchone()[0], 1)
                state = conn.execute(
                    'SELECT "关系状态", "连续不一致次数", "规则版本" FROM "策略关系" WHERE "子策略ID"=?',
                    ("gffunds__ZY00000056",),
                ).fetchone()
                self.assertEqual(state, ("active", 0, MODULE.RULE_VERSION))
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
