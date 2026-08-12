from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SCRIPT_PATH = next(
    parent / "_共享组件" / "生产程序" / "update_gffunds_performance_curves.py"
    for parent in Path(__file__).resolve().parents
    if (parent / "_共享组件" / "生产程序" / "update_gffunds_performance_curves.py").is_file()
)
SPEC = importlib.util.spec_from_file_location("update_gffunds_performance_curves", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class GFFundsPerformanceTest(unittest.TestCase):
    def test_business_day_lag_ignores_weekend(self) -> None:
        self.assertEqual(MODULE.business_day_lag("2026-07-17", "2026-07-20"), 1)
        self.assertEqual(MODULE.business_day_lag("2026-07-20", "2026-07-20"), 0)

    def test_find_last_good_payload_ignores_newer_empty_curve(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base = root / MODULE.CHANNEL_ID / "performance_curve"
            valid_path = (
                base
                / "2026-07-22"
                / "old-run"
                / "GFJJ000001"
                / "get_investadvisor_yield_trend_since_inception.json"
            )
            empty_path = (
                base
                / "2026-07-23"
                / "new-run"
                / "GFJJ000001"
                / "get_investadvisor_yield_trend_since_inception.json"
            )
            valid_path.parent.mkdir(parents=True)
            empty_path.parent.mkdir(parents=True)
            valid_payload = {
                "RETCODE": "0000",
                "adv_yield_trend_list": [{"yield_date": "2026-07-22", "yield_rate": "1.2"}],
            }
            valid_path.write_text(json.dumps(valid_payload), encoding="utf-8")
            empty_path.write_text(
                json.dumps({"RETCODE": "0000", "adv_yield_trend_list": []}),
                encoding="utf-8",
            )
            payload, source_path = MODULE.find_last_good_payload("GFJJ000001", [root])
            self.assertEqual(payload, valid_payload)
            self.assertEqual(source_path, valid_path)

    def test_collect_round_isolates_single_strategy_failure(self) -> None:
        payload = {
            "RETCODE": "0000",
            "adv_yield_trend_list": [{"yield_date": "2026-07-22", "yield_rate": "1.2"}],
        }

        def fake_collect(strategy_id: str, _timeout: int):
            if strategy_id == "GFJJ000002":
                return strategy_id, None, "empty_response"
            return strategy_id, payload, None

        with patch.object(MODULE, "collect_one", side_effect=fake_collect):
            outcomes = MODULE.collect_round(
                ["GFJJ000001", "GFJJ000002"],
                workers=2,
                timeout=1,
                label="test",
            )
        self.assertIsNone(outcomes["GFJJ000001"][1])
        self.assertEqual(outcomes["GFJJ000002"][1], "empty_response")

    def test_strategy_inventory_unions_database_and_discovery_ids(self) -> None:
        args = SimpleNamespace(strategy_id=[], limit=None)
        discovered = Path("discovered.json")
        with (
            patch.object(
                MODULE,
                "load_gffunds_strategy_ids_from_analysis_db",
                return_value=["GFJJ000001", "ZY00000001", "GFFUNDS_BAD"],
            ),
            patch.object(MODULE, "find_latest_discovered_strategy_file", return_value=discovered),
            patch.object(
                MODULE,
                "load_discovered_strategy_ids",
                return_value=["GFJJ000001", "GFJJ000002", "ZY00000002", "ZY_BAD"],
            ),
        ):
            strategy_ids = MODULE.load_strategy_ids(args)
        self.assertEqual(
            strategy_ids,
            ["GFJJ000001", "ZY00000001", "GFJJ000002", "ZY00000002"],
        )


if __name__ == "__main__":
    unittest.main()
