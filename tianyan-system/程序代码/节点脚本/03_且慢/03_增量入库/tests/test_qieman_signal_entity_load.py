from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from collections import defaultdict
from pathlib import Path


CODE_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
PRODUCTION_ROOT = CODE_ROOT / "节点脚本" / "_共享组件" / "生产程序"
sys.path.insert(0, str(PRODUCTION_ROOT))
SPEC = importlib.util.spec_from_file_location(
    "qieman_test_loader",
    PRODUCTION_ROOT / "load_analysis_zh_current_sqlite.py",
)
assert SPEC and SPEC.loader
loader = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(loader)


class QiemanSignalEntityLoadTest(unittest.TestCase):
    def test_exact_benchmark_components_preserve_official_index_codes_and_weights(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            conn = loader.init_db(
                root / "analysis.sqlite",
                CODE_ROOT / "schemas" / "analysis_zh_current.sql",
                keep_existing_db=False,
            )
            conn.execute(
                '''INSERT INTO "渠道信息" ("渠道ID", "渠道名称") VALUES ('qieman', '且慢')'''
            )
            conn.execute(
                '''INSERT INTO "策略信息" (
                       "统一策略ID", "渠道ID", "渠道策略ID", "策略名称"
                   ) VALUES ('qieman__ZH044931', 'qieman', 'ZH044931', '启明睿-低波增强')'''
            )
            loaded = loader.load_strategy_benchmark_components(
                conn,
                "qieman",
                "qieman__ZH044931",
                "ZH044931",
                [
                    {
                        "index_code": "000300.SH",
                        "index_name": "沪深300",
                        "index_type": "STOCK",
                        "weight": 0.10,
                    },
                    {
                        "index_code": "CBA00103.CS",
                        "index_name": "中债新综合全价",
                        "index_type": "BOND",
                        "weight": 0.90,
                    },
                ],
                is_exact_split=True,
                confidence_level="official_stargate_exact_benchmark_split",
                source_snapshot_id="qieman-benchmark-ZH044931",
                captured_at="2026-08-12T21:06:16+08:00",
            )
            rows = conn.execute(
                '''SELECT "指数代码", "权重_百分比", "是否精确拆分", "原始快照ID"
                   FROM "策略业绩基准成分"
                   WHERE "统一策略ID"='qieman__ZH044931'
                   ORDER BY "指数代码"'''
            ).fetchall()
            self.assertEqual(loaded, 2)
            self.assertEqual(
                [tuple(row) for row in rows],
                [
                    ("000300.SH", 10.0, 1, "qieman-benchmark-ZH044931"),
                    ("CBA00103.CS", 90.0, 1, "qieman-benchmark-ZH044931"),
                ],
            )
            conn.close()

    def test_official_historical_snapshot_loads_as_separate_position_fact(self) -> None:
        run_id = "20260810T171049+0800__qieman_collect__attempt_01"
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            normalized = root / "normalized"
            history_dir = normalized / "qieman" / "strategy_fund_snapshot_history" / run_id
            history_dir.mkdir(parents=True)
            rows = [
                {
                    "channel_id": "qieman",
                    "source_strategy_id": "SI_TEST",
                    "snapshot_id": "qieman-history-SI_TEST-1-post",
                    "position_date": "2026-08-05",
                    "disclosure_date": "2026-08-05",
                    "snapshot_phase": "post_rebalance",
                    "source_event_id": "qieman-SI_TEST-1",
                    "fund_code": code,
                    "fund_name": name,
                    "fund_asset_type": "MUTUAL_FUND",
                    "fund_weight": 0.5,
                    "is_precise_weight": True,
                    "confidence_level": "official_full_post_position",
                    "access_level": "official_signed_page_protocol",
                    "raw_record_hash": code,
                    "source_url": "https://qieman.com/pmdj/v1/pomodels/SI_TEST/adjustments",
                }
                for code, name in (("000001", "示例基金A"), ("000002", "示例基金B"))
            ]
            (history_dir / f"{run_id}.jsonl").write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                encoding="utf-8",
            )

            previous_root = loader.NORMALIZED_ROOT
            previous_run = os.environ.get("QIEMAN_COLLECT_RUN_ID")
            loader.NORMALIZED_ROOT = normalized
            os.environ["QIEMAN_COLLECT_RUN_ID"] = run_id
            try:
                conn = loader.init_db(
                    root / "analysis.sqlite",
                    CODE_ROOT / "schemas" / "analysis_zh_current.sql",
                    keep_existing_db=False,
                )
                conn.execute(
                    '''INSERT INTO "渠道信息" ("渠道ID", "渠道名称") VALUES ('qieman', '且慢')'''
                )
                conn.execute(
                    '''INSERT INTO "策略信息" (
                           "统一策略ID", "渠道ID", "渠道策略ID", "策略名称"
                       ) VALUES ('qieman__SI_TEST', 'qieman', 'SI_TEST', '测试发车组合')'''
                )
                counters: dict[str, int] = defaultdict(int)
                loader.import_channel_historical_holdings(
                    conn,
                    "qieman",
                    {run_id: {"captured_at": "2026-08-10T17:10:49+08:00"}},
                    counters,
                )
                stored = conn.execute(
                    '''SELECT "历史快照ID", COUNT(*) AS rows, SUM("基金权重_百分比") AS weight_sum,
                              MIN("是否精确权重") AS all_exact
                       FROM "策略历史持仓" GROUP BY "历史快照ID"'''
                ).fetchone()
                self.assertEqual(tuple(stored), ("qieman-history-SI_TEST-1-post", 2, 100.0, 1))
                self.assertEqual(counters["策略历史持仓"], 2)
                conn.close()
            finally:
                loader.NORMALIZED_ROOT = previous_root
                if previous_run is None:
                    os.environ.pop("QIEMAN_COLLECT_RUN_ID", None)
                else:
                    os.environ["QIEMAN_COLLECT_RUN_ID"] = previous_run

    def test_instruction_ratio_is_not_used_as_portfolio_weight(self) -> None:
        run_id = "20260810T171049+0800__qieman_collect__attempt_01"
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            normalized = root / "normalized"
            event_dir = normalized / "qieman" / "signal_strategy_event" / run_id
            instruction_dir = normalized / "qieman" / "signal_fund_instruction" / run_id
            event_dir.mkdir(parents=True)
            instruction_dir.mkdir(parents=True)
            event = {
                "channel_id": "qieman",
                "signal_event_id": "qieman-signal-SI_TEST-1",
                "source_event_id": 1,
                "source_signal_id": 2,
                "source_strategy_id": "SI_TEST",
                "strategy_name": "测试发车组合",
                "signal_date": "2026-08-05",
                "signal_time": "2026-08-05 10:22:10",
                "signal_title": "发车买入",
                "signal_reason": "官方信号",
                "signal_summary": "买入",
                "has_exact_pre_position": True,
                "has_exact_post_position": True,
                "pre_weight_sum": 1.0,
                "post_weight_sum": 1.0,
                "official_turnover_rate": 0.15,
            }
            instruction = {
                "channel_id": "qieman",
                "signal_event_id": event["signal_event_id"],
                "signal_instruction_id": f'{event["signal_event_id"]}-1',
                "source_strategy_id": "SI_TEST",
                "signal_date": "2026-08-05",
                "fund_code": "000001",
                "fund_name": "示例基金",
                "raw_action": "buy",
                "before_portfolio_weight": 0.40,
                "after_portfolio_weight": 0.55,
                "instruction_ratio": 0.50,
                "instruction_ratio_semantics": "official_new_cash_distribution_ratio_not_portfolio_weight",
                "portfolio_weight_source": "official_full_pre_post_snapshots",
            }
            (event_dir / f"{run_id}.jsonl").write_text(
                json.dumps(event, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            (instruction_dir / f"{run_id}.jsonl").write_text(
                json.dumps(instruction, ensure_ascii=False) + "\n", encoding="utf-8"
            )

            previous_root = loader.NORMALIZED_ROOT
            previous_run = os.environ.get("QIEMAN_COLLECT_RUN_ID")
            loader.NORMALIZED_ROOT = normalized
            os.environ["QIEMAN_COLLECT_RUN_ID"] = run_id
            try:
                conn = loader.init_db(
                    root / "analysis.sqlite",
                    CODE_ROOT / "schemas" / "analysis_zh_current.sql",
                    keep_existing_db=False,
                )
                conn.execute(
                    '''INSERT INTO "渠道信息" ("渠道ID", "渠道名称") VALUES ('qieman', '且慢')'''
                )
                conn.execute(
                    '''INSERT INTO "策略信息" (
                           "统一策略ID", "渠道ID", "渠道策略ID", "策略名称"
                       ) VALUES ('qieman__SI_TEST', 'qieman', 'SI_TEST', '测试发车组合')'''
                )
                counters: dict[str, int] = defaultdict(int)
                loader.import_channel_signal_entities(
                    conn,
                    "qieman",
                    {run_id: {"captured_at": "2026-08-10T17:10:49+08:00"}},
                    counters,
                )
                row = conn.execute(
                    '''SELECT "调前权重_百分比", "调后权重_百分比",
                              "权重变化_百分点", "新增资金分配比例_百分比",
                              "指令比例口径"
                       FROM "信号策略基金指令"'''
                ).fetchone()
                self.assertEqual(tuple(row[:4]), (40.0, 55.0, 15.0, 50.0))
                self.assertEqual(
                    row[4], "official_new_cash_distribution_ratio_not_portfolio_weight"
                )
                self.assertEqual(counters["信号策略事件"], 1)
                self.assertEqual(counters["信号策略基金指令"], 1)
                conn.close()
            finally:
                loader.NORMALIZED_ROOT = previous_root
                if previous_run is None:
                    os.environ.pop("QIEMAN_COLLECT_RUN_ID", None)
                else:
                    os.environ["QIEMAN_COLLECT_RUN_ID"] = previous_run


if __name__ == "__main__":
    unittest.main()
