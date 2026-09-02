from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


CODE_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
SRC_ROOT = CODE_ROOT / "节点脚本" / "03_广发证券" / "01_财富管家采集" / "src"
sys.path.insert(0, str(SRC_ROOT))

from gfsec_fima_position_history import analyze  # noqa: E402


CHINA_TZ = timezone(timedelta(hours=8))


class GfsecFimaPositionHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.raw_root = self.root / "raw" / "gfsec_fima" / "public_api"
        self.db_path = self.root / "analysis.sqlite"
        connection = sqlite3.connect(self.db_path)
        connection.execute(
            'CREATE TABLE "基金日度净值" ('
            '"基金代码" TEXT NOT NULL, '
            '"交易日期" TEXT NOT NULL, '
            '"日收益率_百分比" REAL, '
            'PRIMARY KEY ("基金代码", "交易日期")'
            ')'
        )
        connection.commit()
        connection.close()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def write_observation(
        self,
        *,
        day: str,
        run_stamp: str,
        portfolio_code: str = "P001",
        strategy_name: str = "测试策略",
        generated_at: datetime,
        allocation_id: int,
        positions: list[tuple[str, str, float]],
        alternatives: list[dict[str, object]] | None = None,
    ) -> Path:
        directory = self.raw_root / day / f"{run_stamp}__gfsec_fima_collect__attempt_01" / "portfolio" / portfolio_code
        directory.mkdir(parents=True, exist_ok=True)
        products = []
        for index, (code, name, ratio) in enumerate(positions):
            products.append(
                {
                    "productCode": code,
                    "productName": name,
                    "productSource": "公募",
                    "configRatio": ratio,
                    "shareRatio": ratio,
                    "underCurrency": False,
                    "alternativeProducts": alternatives if index == 0 else None,
                }
            )
        allocation = {
            "id": allocation_id,
            "portfolioCode": portfolio_code,
            "productAllocations": [
                {
                    "assetName": "混合资产",
                    "mainProducts": products,
                }
            ],
            "effectiveDate": None,
        }
        mix = {
            "portfolioCode": portfolio_code,
            "strategyName": strategy_name,
            "createTime": int(generated_at.timestamp() * 1000),
        }
        (directory / "current_allocation.json").write_text(
            json.dumps(allocation, ensure_ascii=False),
            encoding="utf-8",
        )
        (directory / "portfolio_mix.json").write_text(
            json.dumps(mix, ensure_ascii=False),
            encoding="utf-8",
        )
        (directory / "rebalances.json").write_text(
            json.dumps({"total": 0, "data": []}, ensure_ascii=False),
            encoding="utf-8",
        )
        return directory

    def insert_returns(self, rows: list[tuple[str, str, float]]) -> None:
        connection = sqlite3.connect(self.db_path)
        connection.executemany(
            'INSERT INTO "基金日度净值" ("基金代码", "交易日期", "日收益率_百分比") VALUES (?, ?, ?)',
            rows,
        )
        connection.commit()
        connection.close()

    def test_persisted_membership_change_is_candidate_but_never_official_event(self) -> None:
        self.write_observation(
            day="2026-07-29",
            run_stamp="20260729T200000+0800",
            generated_at=datetime(2026, 7, 29, 13, tzinfo=CHINA_TZ),
            allocation_id=1,
            positions=[("000001", "基金一", 0.5), ("000002", "基金二", 0.5)],
            alternatives=[{"productCode": "999999", "productName": "备选基金"}],
        )
        self.write_observation(
            day="2026-07-30",
            run_stamp="20260730T200000+0800",
            generated_at=datetime(2026, 7, 30, 13, tzinfo=CHINA_TZ),
            allocation_id=2,
            positions=[("000001", "基金一", 1.0)],
        )
        self.write_observation(
            day="2026-07-31",
            run_stamp="20260731T200000+0800",
            generated_at=datetime(2026, 7, 30, 13, tzinfo=CHINA_TZ),
            allocation_id=2,
            positions=[("000001", "基金一", 1.0)],
        )

        invalid = self.raw_root / "2026-07-31" / "invalid" / "portfolio" / "BROKEN"
        invalid.mkdir(parents=True)
        (invalid / "current_allocation.json").write_text("{not-json", encoding="utf-8")
        empty = self.raw_root / "2026-07-31" / "empty" / "portfolio" / "EMPTY"
        empty.mkdir(parents=True)
        (empty / "current_allocation.json").write_text("{}", encoding="utf-8")

        bundle = analyze(self.raw_root, db_path=self.db_path)

        self.assertEqual(bundle.summary["counts"]["state_occurrence_count"], 2)
        self.assertEqual(bundle.summary["counts"]["position_snapshot_row_count"], 3)
        self.assertEqual(bundle.summary["counts"]["alternative_product_references_excluded"], 1)
        self.assertEqual(bundle.summary["counts"]["invalid_json_files"], 1)
        self.assertEqual(bundle.summary["counts"]["empty_payload_files"], 1)
        self.assertEqual(len(bundle.candidates), 1)
        candidate = bundle.candidates[0]
        self.assertEqual(candidate["classification"], "composition_change_candidate")
        self.assertEqual(candidate["candidate_confidence"], "high_inferred_window")
        self.assertFalse(candidate["eligible_for_official_rebalance_table"])
        self.assertEqual(candidate["removed_positions"][0]["fund_code"], "000002")
        self.assertEqual(candidate["persistence"]["same_composition_observation_count"], 2)
        self.assertNotIn("999999", {row["fund_code"] for row in bundle.position_snapshots})
        self.assertEqual(bundle.validation["status"], "warn")

    def test_no_trade_nav_drift_is_not_promoted_to_change_candidate(self) -> None:
        self.insert_returns(
            [
                ("000001", "2026-07-29", 1.0),
                ("000002", "2026-07-29", 0.0),
            ]
        )
        self.write_observation(
            day="2026-07-29",
            run_stamp="20260729T200000+0800",
            generated_at=datetime(2026, 7, 29, 13, tzinfo=CHINA_TZ),
            allocation_id=1,
            positions=[("000001", "基金一", 0.5), ("000002", "基金二", 0.5)],
        )
        expected_first = 0.5 * 1.01 / (0.5 * 1.01 + 0.5)
        self.write_observation(
            day="2026-07-30",
            run_stamp="20260730T200000+0800",
            generated_at=datetime(2026, 7, 30, 13, tzinfo=CHINA_TZ),
            allocation_id=2,
            positions=[("000001", "基金一", expected_first), ("000002", "基金二", 1.0 - expected_first)],
        )

        bundle = analyze(self.raw_root, db_path=self.db_path)

        self.assertEqual(len(bundle.transitions), 1)
        self.assertEqual(bundle.transitions[0]["classification"], "market_drift")
        self.assertFalse(bundle.transitions[0]["is_change_candidate"])
        self.assertEqual(bundle.candidates, [])
        self.assertAlmostEqual(
            bundle.transitions[0]["drift_model"]["drift_residual_half_l1_pct"],
            0.0,
            places=5,
        )

    def test_unexplained_same_membership_weight_change_is_review_candidate(self) -> None:
        self.insert_returns(
            [
                ("000001", "2026-07-29", 0.0),
                ("000002", "2026-07-29", 0.0),
            ]
        )
        self.write_observation(
            day="2026-07-29",
            run_stamp="20260729T200000+0800",
            generated_at=datetime(2026, 7, 29, 13, tzinfo=CHINA_TZ),
            allocation_id=1,
            positions=[("000001", "基金一", 0.5), ("000002", "基金二", 0.5)],
        )
        self.write_observation(
            day="2026-07-30",
            run_stamp="20260730T200000+0800",
            generated_at=datetime(2026, 7, 30, 13, tzinfo=CHINA_TZ),
            allocation_id=2,
            positions=[("000001", "基金一", 0.6), ("000002", "基金二", 0.4)],
        )

        bundle = analyze(self.raw_root, db_path=self.db_path)

        self.assertEqual(len(bundle.candidates), 1)
        self.assertEqual(bundle.candidates[0]["classification"], "weight_reallocation_candidate")
        self.assertEqual(bundle.candidates[0]["candidate_confidence"], "medium_inferred_window")
        self.assertFalse(bundle.candidates[0]["eligible_for_official_rebalance_table"])
        self.assertAlmostEqual(
            bundle.candidates[0]["drift_model"]["drift_residual_half_l1_pct"],
            10.0,
            places=5,
        )

    def test_reused_old_run_id_does_not_create_out_of_order_transition(self) -> None:
        self.write_observation(
            day="2026-07-30",
            run_stamp="20260730T002340+0800",
            generated_at=datetime(2026, 7, 29, 13, tzinfo=CHINA_TZ),
            allocation_id=1,
            positions=[("000001", "基金一", 0.5), ("000002", "基金二", 0.5)],
        )
        reused = self.write_observation(
            day="2026-08-05",
            run_stamp="20260730T002340+0800",
            generated_at=datetime(2026, 8, 5, 13, tzinfo=CHINA_TZ),
            allocation_id=2,
            positions=[("000001", "基金一", 0.6), ("000002", "基金二", 0.4)],
        )
        (reused / "portfolio_mix.json").unlink()
        self.write_observation(
            day="2026-08-06",
            run_stamp="20260805T231259+0800",
            generated_at=datetime(2026, 8, 5, 13, tzinfo=CHINA_TZ),
            allocation_id=2,
            positions=[("000001", "基金一", 0.6), ("000002", "基金二", 0.4)],
        )

        bundle = analyze(self.raw_root, db_path=self.db_path)

        self.assertEqual(bundle.summary["counts"]["capture_precision_directory_date_run_id_mismatch"], 1)
        self.assertEqual(bundle.summary["counts"]["state_occurrence_count"], 2)
        self.assertEqual(bundle.summary["counts"]["transition_count"], 1)
        second_state = bundle.state_snapshots[1]
        self.assertEqual(second_state["source_observation_count"], 2)
        self.assertEqual(second_state["model_generated_from_at"], "2026-08-05T13:00:00.000+08:00")

    def test_rebalance_transport_error_is_not_counted_as_verified_empty(self) -> None:
        directory = self.write_observation(
            day="2026-07-29",
            run_stamp="20260729T200000+0800",
            generated_at=datetime(2026, 7, 29, 13, tzinfo=CHINA_TZ),
            allocation_id=1,
            positions=[("000001", "基金一", 1.0)],
        )
        (directory / "rebalances.json").write_text(
            json.dumps({"transport_error": "TLS EOF", "url": "https://example.invalid/rebalances"}),
            encoding="utf-8",
        )

        bundle = analyze(self.raw_root, db_path=self.db_path)

        self.assertEqual(bundle.summary["counts"]["rebalance_official_endpoint_transport_error"], 1)
        self.assertNotIn("rebalance_official_endpoint_empty", bundle.summary["counts"])
        coverage = next(
            item
            for item in bundle.validation["checks"]
            if item["check_id"] == "GFSEC_FIMA_HISTORY_REBALANCE_ENDPOINT_COVERAGE"
        )
        self.assertEqual(coverage["status"], "warn")
        self.assertEqual(coverage["count"], 1)

    def test_weight_closure_failure_fails_internal_validation(self) -> None:
        self.write_observation(
            day="2026-07-29",
            run_stamp="20260729T200000+0800",
            generated_at=datetime(2026, 7, 29, 13, tzinfo=CHINA_TZ),
            allocation_id=1,
            positions=[("000001", "基金一", 0.4), ("000002", "基金二", 0.4)],
        )

        bundle = analyze(self.raw_root, db_path=self.db_path)

        self.assertEqual(bundle.validation["status"], "failed")
        closure_check = next(
            item for item in bundle.validation["checks"] if item["check_id"] == "GFSEC_FIMA_HISTORY_WEIGHT_CLOSURE"
        )
        self.assertEqual(closure_check["status"], "failed")
        self.assertEqual(closure_check["count"], 1)


if __name__ == "__main__":
    unittest.main()
