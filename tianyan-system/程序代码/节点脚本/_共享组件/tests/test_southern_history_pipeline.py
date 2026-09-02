from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch


PROGRAM_ROOT = Path(__file__).resolve().parents[1] / "生产程序"
sys.path.insert(0, str(PROGRAM_ROOT))


def load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, PROGRAM_ROOT / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


NORMALIZER = load_module("normalize_southern_live_artifact", "normalize_southern_live_artifact.py")
LOADER = load_module("load_analysis_zh_current_sqlite_southern", "load_analysis_zh_current_sqlite.py")


class SouthernHistoryPipelineTest(unittest.TestCase):
    def test_ratio_info_keeps_official_percentage_point_weights(self) -> None:
        rows = NORMALIZER.parse_ratio_info("000001|40,000002|60")
        self.assertEqual(rows[0]["fund_weight"], 40.0)
        self.assertEqual(rows[1]["fund_weight"], 60.0)

    def test_normalizer_separates_current_and_historical_entities(self) -> None:
        market = {
            "result": {
                "info": {
                    "comblist": [
                        {"date": "20260102", "nav": 1.01, "upratio": 0.1, "regionRatio": 1.0, "ratioinfo": "000001|40,000002|60"},
                        {"date": "20260105", "nav": 1.02, "upratio": 0.2, "regionRatio": 2.0, "ratioinfo": "000001|41,000002|59"},
                    ],
                    "benchmarklist": [
                        {"date": "20260102", "nav": 1.00, "upratio": 0.05, "regionRatio": 0.5},
                        {"date": "20260105", "nav": 1.01, "upratio": 0.10, "regionRatio": 1.0},
                    ],
                }
            }
        }
        comb_info = {
            "result": {
                "combcode": "79",
                "combname": "测试司南组合",
                "enddate": "20260105",
                "sortlist": [
                    {"desc": "测试基准"},
                    {
                        "name": "资产配置",
                        "ratiolist": [{"name": "权益类", "cur_ratio": "100"}],
                        "fundlist": [
                            {"fundcode": "000001", "fundname": "基金一", "name": "权益类", "cur_ratio": "41"},
                            {"fundcode": "000002", "fundname": "基金二", "name": "权益类", "cur_ratio": "59"},
                        ],
                    },
                ],
            }
        }
        artifact = {
            "page_url": "https://trade.southernfund.com/new/iainvest/scene6?combcode=79&SECURE_TOKEN=[TOKEN]",
            "events": [
                {"url": "https://trade.southernfund.com/new/webIAqueryCombInfo", "response_text": json.dumps(comb_info)},
                {"url": "https://trade.southernfund.com/new/webIAcombFundMarketQuery", "response_text": json.dumps(market)},
                {
                    "url": "https://trade.southernfund.com/new/webIAqueryTradeRate",
                    "response_text": json.dumps(
                        {
                            "result": {
                                "inIAServiceRate": "",
                                "ratelist": [
                                    {"cyje": "持有金额", "rate": "费率（年）"},
                                    {"cyje": "100万以上", "rate": "0.30%"},
                                    {"cyje": "100万以下", "rate": "0.50%"},
                                ],
                            }
                        }
                    ),
                },
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "southern_plan_detail-79-test.json"
            source.write_text(json.dumps(artifact), encoding="utf-8")
            normalized, summary = NORMALIZER.normalize(source, "test_run", "2026-08-13T12:00:00+08:00")

        self.assertEqual(len(normalized["strategy_fund_snapshot"]), 2)
        self.assertEqual(len(normalized["strategy_fund_snapshot_history"]), 4)
        self.assertNotIn("strategy_fund_position_daily", normalized)
        self.assertTrue(
            all(row.get("return_unit") == "percent_point" for row in normalized["strategy_performance_daily"])
        )
        self.assertEqual(
            [row["benchmark_return"] for row in normalized["strategy_performance_daily"]],
            [0.5, 1.0],
        )
        self.assertEqual(
            normalized["strategy_master"][0]["advisory_fee_rate"],
            "费率（年）：100万以上 0.30%；100万以下 0.50%",
        )
        self.assertAlmostEqual(sum(row["fund_weight"] for row in normalized["strategy_fund_snapshot"]), 100.0)
        self.assertEqual({row["weight_unit"] for row in normalized["strategy_fund_snapshot_history"]}, {"percent_point"})
        self.assertEqual(summary["history_position_rows"], 4)

    def test_loader_converts_southern_decimal_weight_to_percent(self) -> None:
        self.assertEqual(LOADER.to_weight_percent("southern", 0.375, "decimal"), 37.5)
        self.assertEqual(LOADER.to_weight_percent("southern", 37.5, "percent_point"), 37.5)

    def test_loader_imports_southern_history_entity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            normalized_root = Path(temporary)
            run_id = "20260813T120000+0800"
            entity_dir = normalized_root / "southern" / "strategy_fund_snapshot_history" / "2026-08-13"
            entity_dir.mkdir(parents=True)
            row = {
                "snapshot_id": "southern-79-history-2026-01-05",
                "source_strategy_id": "79",
                "position_date": "2026-01-05",
                "disclosure_date": "2026-01-05",
                "fund_code": "000001",
                "fund_name": "基金一",
                "fund_asset_type": "权益类",
                "fund_weight": 0.375,
                "weight_unit": "decimal",
                "is_precise_weight": True,
                "confidence_level": "official_exact",
                "access_level": "login",
                "raw_record_hash": "hash",
                "source_url": "https://example.invalid/redacted",
            }
            (entity_dir / f"{run_id}.jsonl").write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
            connection = sqlite3.connect(":memory:")
            connection.executescript(
                '''
                CREATE TABLE "策略信息" ("统一策略ID" TEXT, "渠道ID" TEXT, "渠道策略ID" TEXT);
                INSERT INTO "策略信息" VALUES ('southern__79', 'southern', '79');
                CREATE TABLE "策略历史持仓" (
                    "统一策略ID" TEXT, "渠道ID" TEXT, "渠道策略ID" TEXT, "历史快照ID" TEXT,
                    "持仓日期" TEXT, "披露日期" TEXT, "快照阶段" TEXT, "来源事件ID" TEXT,
                    "基金代码" TEXT, "基金名称" TEXT, "资产类型" TEXT, "基金权重_百分比" REAL,
                    "是否精确权重" INTEGER, "置信度" TEXT, "访问级别" TEXT, "原始记录哈希" TEXT,
                    "原始来源URL" TEXT, "采集批次ID" TEXT,
                    UNIQUE("统一策略ID", "历史快照ID", "基金名称")
                );
                CREATE TABLE "数据来源清单" (
                    "统一策略ID" TEXT, "渠道ID" TEXT, "渠道策略ID" TEXT, "文件类型" TEXT,
                    "文件路径" TEXT, "采集批次ID" TEXT, "采集时间" TEXT,
                    PRIMARY KEY("统一策略ID", "文件类型")
                );
                '''
            )
            counters = Counter()
            with patch.object(LOADER, "NORMALIZED_ROOT", normalized_root):
                LOADER.import_channel_historical_holdings(connection, "southern", {}, counters)
            loaded = connection.execute('SELECT "基金权重_百分比" FROM "策略历史持仓"').fetchone()[0]
            connection.close()

        self.assertEqual(loaded, 37.5)
        self.assertEqual(counters["策略历史持仓"], 1)


if __name__ == "__main__":
    unittest.main()
