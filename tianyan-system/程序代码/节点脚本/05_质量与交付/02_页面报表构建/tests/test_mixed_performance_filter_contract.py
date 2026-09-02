from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


CODE_ROOT = Path(__file__).resolve().parents[4]
SCRIPT = CODE_ROOT / "节点脚本" / "_共享组件" / "生产程序" / "build_mixed_performance_scatter_pack.py"
SPEC = importlib.util.spec_from_file_location("mixed_performance_filter_contract", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class MixedPerformanceFilterContractTests(unittest.TestCase):
    def test_unbucketed_strategy_zero_placeholders_do_not_become_l0(self) -> None:
        row = {
            "产品类型": "投顾策略",
            "产品ID": "qieman__sample",
            "产品代码": "sample",
            "产品名称": "未分档样本",
            "基准风险资产权重": "未分档",
            "基准权益权重": 0,
            "基准商品权重": 0,
            "基准另类权重": 0,
            "基准未知权重": 0,
        }
        built = MODULE.build_row(row)
        self.assertIsNotNone(built)
        self.assertEqual(built["broadEquityBucket"], "")
        self.assertIsNone(built["broadEquityWeight"])

    def test_strategy_uses_explicit_audited_bucket(self) -> None:
        row = {
            "产品类型": "投顾策略",
            "产品ID": "ttfund__sample",
            "产品代码": "sample",
            "产品名称": "明确分档样本",
            "有基准": "是",
            "基准风险资产权重": "L4",
            "基准权益权重": 0,
            "基准商品权重": 0,
            "基准另类权重": 0,
            "基准未知权重": 0,
        }
        built = MODULE.build_row(row)
        self.assertIsNotNone(built)
        self.assertEqual(built["broadEquityBucket"], "L4")

    def test_strategy_without_benchmark_rejects_l0_placeholder(self) -> None:
        row = {
            "产品类型": "投顾策略",
            "产品ID": "qieman__sample",
            "产品代码": "sample",
            "产品名称": "未披露基准样本",
            "有基准": "否",
            "基准风险资产权重": "L0",
            "基准风险资产权重_百分比": 0,
            "基准海外权重": 0,
        }
        built = MODULE.build_row(row)
        self.assertIsNotNone(built)
        self.assertEqual(built["broadEquityBucket"], "")
        self.assertIsNone(built["broadEquityWeight"])

    def test_strategy_explicitly_without_comparison_benchmark_is_unbucketed(self) -> None:
        row = {
            "产品类型": "投顾策略",
            "产品ID": "ttfund__sample",
            "产品代码": "sample",
            "产品名称": "不设基准样本",
            "有基准": "是",
            "业绩比较基准": "不设置业绩比较基准",
            "基准风险资产权重": "L0",
            "基准风险资产权重_百分比": 0,
        }
        built = MODULE.build_row(row)
        self.assertIsNotNone(built)
        self.assertEqual(built["broadEquityBucket"], "")
        self.assertIsNone(built["broadEquityWeight"])


if __name__ == "__main__":
    unittest.main()
