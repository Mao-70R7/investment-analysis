from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
MODULE_PATH = ROOT / "collect_qieman_stargate_proxy.py"
SPEC = importlib.util.spec_from_file_location("collect_qieman_stargate_proxy", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

BENCHMARK_MODULE_PATH = ROOT / "augment_qieman_stargate_benchmarks.py"
BENCHMARK_SPEC = importlib.util.spec_from_file_location(
    "augment_qieman_stargate_benchmarks",
    BENCHMARK_MODULE_PATH,
)
assert BENCHMARK_SPEC and BENCHMARK_SPEC.loader
BENCHMARK_MODULE = importlib.util.module_from_spec(BENCHMARK_SPEC)
BENCHMARK_SPEC.loader.exec_module(BENCHMARK_MODULE)


class FakeProxySocket:
    def __init__(self, chunks: list[bytes] | None = None, error: Exception | None = None) -> None:
        self.chunks = list(chunks or [])
        self.error = error

    def __enter__(self) -> "FakeProxySocket":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def settimeout(self, _timeout: int) -> None:
        return None

    def sendall(self, _payload: bytes) -> None:
        return None

    def shutdown(self, _how: int) -> None:
        return None

    def recv(self, _size: int) -> bytes:
        if self.error is not None:
            error = self.error
            self.error = None
            raise error
        return self.chunks.pop(0) if self.chunks else b""


class CollectQiemanStargateProxyTests(unittest.TestCase):
    def test_request_proxy_retries_after_connection_reset(self) -> None:
        response = json.dumps({"state": "ok", "payload": {"rows": [1]}}).encode("utf-8")
        sockets = [
            FakeProxySocket(error=ConnectionResetError(10054, "reset")),
            FakeProxySocket(chunks=[response, b""]),
        ]
        with (
            mock.patch.object(MODULE.socket, "create_connection", side_effect=sockets) as create_connection,
            mock.patch.object(MODULE.time, "sleep") as sleep,
        ):
            payload = MODULE.request_proxy(
                43912,
                "BatchGetStrategiesComposition",
                body={"strategyCodes": ["ZH1"]},
                max_attempts=2,
                retry_delay_seconds=0.01,
            )
        self.assertEqual(payload, {"rows": [1]})
        self.assertEqual(create_connection.call_count, 2)
        sleep.assert_called_once_with(0.01)

    def test_payload_with_resume_reuses_valid_raw_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            resume = root / "previous"
            current = root / "current"
            relative = Path("raw") / "composition" / "batch_0001.json"
            (resume / relative).parent.mkdir(parents=True)
            (resume / relative).write_text('{"ZH1":{"混合基金":{}}}', encoding="utf-8")
            current.mkdir()
            fetch = mock.Mock(side_effect=AssertionError("cached payload should avoid a network request"))
            payload, reused = MODULE.payload_with_resume(current, resume, relative, fetch)
            self.assertTrue(reused)
            self.assertIn("ZH1", payload)
            fetch.assert_not_called()
            self.assertEqual(json.loads((current / relative).read_text(encoding="utf-8")), payload)

    def test_exact_benchmark_baseline_reuse_excludes_inexact_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            baseline_path = Path(temporary) / "strategy_benchmark.jsonl"
            baseline_path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "source_strategy_id": "ZH_EXACT",
                                "is_exact_split": True,
                                "run_id": "old-run",
                                "benchmark_components": [{"weight": 1.0}],
                            }
                        ),
                        json.dumps(
                            {"source_strategy_id": "ZH_PARTIAL", "is_exact_split": False, "run_id": "old-run"}
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            baseline = BENCHMARK_MODULE.load_exact_baseline(
                baseline_path,
                {"ZH_EXACT", "ZH_PARTIAL", "ZH_NEW"},
                "new-run",
            )
        self.assertEqual(set(baseline), {"ZH_EXACT"})
        self.assertEqual(baseline["ZH_EXACT"]["run_id"], "new-run")
        self.assertEqual(baseline["ZH_EXACT"]["baseline_source_run_id"], "old-run")

    def test_test_strategy_name_is_explicit_and_does_not_match_channel_exclusive(self) -> None:
        self.assertTrue(MODULE.is_test_strategy_name("且慢专用测试组合-经典组合"))
        self.assertTrue(MODULE.is_test_strategy_name("测试组合策略说明书staging"))
        self.assertTrue(MODULE.is_test_strategy_name("稳稳的幸福TEST"))
        self.assertFalse(MODULE.is_test_strategy_name("启明睿-进取(长江专属)"))

    def test_future_position_date_is_not_strict_complete(self) -> None:
        holdings, assessment = MODULE.normalize_composition(
            "ZH_TEST",
            {
                "指数基金": {
                    "持有成分": [
                        {
                            "基金代码": "000001",
                            "基金名称": "示例基金",
                            "持仓占比": "100.00%",
                            "最新更新时间": "2026-08-10 00:00:00",
                        }
                    ]
                }
            },
            "2026-08-09T22:00:00+08:00",
            "run-1",
        )
        self.assertEqual(len(holdings), 1)
        self.assertTrue(assessment["position_date_in_future"])
        self.assertFalse(assessment["strict_complete"])
        self.assertEqual(holdings[0]["confidence_level"], "official_stargate_composition_partial_date")

    def test_exact_current_composition_requires_uniform_date_and_full_weight(self) -> None:
        holdings, assessment = MODULE.normalize_composition(
            "ZH_OK",
            {
                "混合基金": {
                    "持有成分": [
                        {
                            "基金代码": "000001",
                            "基金名称": "基金一",
                            "持仓占比": "40.00%",
                            "最新更新时间": "2026-08-07 00:00:00",
                        },
                        {
                            "基金代码": "000002",
                            "基金名称": "基金二",
                            "持仓占比": "60.00%",
                            "最新更新时间": "2026-08-07 00:00:00",
                        },
                    ]
                }
            },
            "2026-08-09T22:00:00+08:00",
            "run-1",
        )
        self.assertEqual(len(holdings), 2)
        self.assertEqual(assessment["position_date"], "2026-08-07")
        self.assertEqual(assessment["weight_sum"], 1.0)
        self.assertTrue(assessment["strict_complete"])

    def test_strategy_details_only_promote_explicit_exact_fields(self) -> None:
        detail = MODULE.normalize_detail_fields(
            {
                "策略代码": "ZH013136",
                "策略名称": "我要稳稳的幸福",
                "策略管理人": "盈米基金",
                "策略类型": "基金投顾",
                "建议持有时长": "1年以上",
                "起投金额": "1000元",
                "投顾服务费率": "0.50%/年",
                "策略详情链接": "https://qieman.com/portfolio/ZH013136",
                "其他推断文本": "不应进入结构化字段",
            }
        )
        self.assertEqual(detail["source_strategy_id"], "ZH013136")
        self.assertEqual(detail["advisor_name"], "盈米基金")
        self.assertEqual(detail["strategy_type"], "基金投顾")
        self.assertEqual(detail["suggested_holding_period"], "1年以上")
        self.assertEqual(detail["minimum_amount"], 1000.0)
        self.assertEqual(detail["advisory_fee_rate"], "0.50%/年")
        self.assertNotIn("其他推断文本", detail)

    def test_strategy_details_reject_non_http_source_url(self) -> None:
        detail = MODULE.normalize_detail_fields({"策略代码": "ZH1", "策略详情链接": "javascript:alert(1)"})
        self.assertNotIn("source_url", detail)


if __name__ == "__main__":
    unittest.main()
