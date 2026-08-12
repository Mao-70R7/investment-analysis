from __future__ import annotations

import sys
import unittest
from pathlib import Path


PYTHON_SRC = Path(__file__).resolve().parents[1] / "python_src"
sys.path.insert(0, str(PYTHON_SRC))

from advisor_monitor.collectors.official_apps_public import (  # noqa: E402
    OfficialAppsPublicCollector,
    gfsec_robot_curve_candidates,
    gfsec_robot_curve_disclosure_match,
    gfsec_robot_curve_rows,
)


class GfsecRobotPerformanceTest(unittest.TestCase):
    def test_curve_rows_preserve_official_cumulative_values_and_compound_daily_return(self) -> None:
        rows = gfsec_robot_curve_rows(
            "gfsec_robot",
            "demo.strategy",
            {
                "data": {
                    "rows": [
                        {"date": "20260802", "totalYield": 0.2, "baselineYield": 0.1},
                        {"date": "20260801", "totalYield": 0.1, "baselineYield": 0.05},
                    ]
                }
            },
            "snapshot-1",
        )

        self.assertEqual([row["trade_date"] for row in rows], ["2026-08-01", "2026-08-02"])
        self.assertEqual(rows[0]["nav"], 1.1)
        self.assertEqual(rows[1]["cumulative_return"], 20.0)
        self.assertEqual(rows[1]["benchmark_return"], 10.0)
        self.assertAlmostEqual(rows[1]["daily_return"], 9.090909, places=6)

    def test_detail_disclosed_url_is_rendered_before_generated_fallbacks(self) -> None:
        detail = {
            "id": "allocation.risk3p12",
            "others": {
                "createDate": "2016-06-20",
                "performance": {
                    "url": "/api/robot/assetallocation/1.0.0/strategy/model/yield"
                    "?strategyType=wj&startDate=${startDate}&endDate=${endDate}"
                },
            },
        }

        candidates = gfsec_robot_curve_candidates(detail, "2026-08-03")

        self.assertEqual(candidates[0]["kind"], "detail_disclosed_url")
        self.assertIn("strategyType=wj", candidates[0]["url"])
        self.assertIn("startDate=2016-06-20", candidates[0]["url"])
        self.assertIn("endDate=2026-08-03", candidates[0]["url"])
        self.assertEqual(candidates[1]["kind"], "production_apk_centerproxy_model_yield")
        self.assertIn("info.gf.com.cn/api/1.0.0/centerproxy/ytj", candidates[1]["url"])
        self.assertIn("startDate=20160620", candidates[1]["url"])
        self.assertIn("endDate=20260803", candidates[1]["url"])
        self.assertEqual(len(candidates), 4)

    def test_generated_curve_requires_exact_match_with_detail_disclosure(self) -> None:
        detail = {
            "id": "biotech.theme",
            "others": {
                "performance": {
                    "busiDate": "2023-08-01",
                    "yield": 1.035976,
                }
            },
        }
        matching_rows = [
            {"trade_date": "2023-08-01", "cumulative_return": 103.5976}
        ]
        wrong_rows = [
            {"trade_date": "2023-08-01", "cumulative_return": 25.7868}
        ]

        self.assertEqual(
            gfsec_robot_curve_disclosure_match(detail, matching_rows, require_exact_point=True),
            (True, "matched_detail_disclosure"),
        )
        accepted, note = gfsec_robot_curve_disclosure_match(
            detail, wrong_rows, require_exact_point=True
        )
        self.assertFalse(accepted)
        self.assertIn("curve_disclosure_difference_pct", note)

    def test_strategy_master_prefers_disclosed_benchmark_formula_over_generic_label(self) -> None:
        row = OfficialAppsPublicCollector.gfsec_robot_strategy_master_row(
            "gfsec_robot",
            "广发证券易淘金/贝塔牛理财",
            {
                "id": "allocation.risk3p12",
                "name": "全球猎手",
                "others": {
                    "performance": {
                        "baseLine": "业绩基准",
                        "baseText": "30%上证综指+70%中证全债",
                    }
                },
            },
            "https://robot.gf.com.cn/api/robot/investment/2.0.0",
        )

        self.assertEqual(row["benchmark"], "30%上证综指+70%中证全债")


if __name__ == "__main__":
    unittest.main()
