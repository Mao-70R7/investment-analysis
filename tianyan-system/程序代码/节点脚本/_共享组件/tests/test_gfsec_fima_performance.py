from __future__ import annotations

import sys
import unittest
from pathlib import Path


PYTHON_SRC = Path(__file__).resolve().parents[1] / "python_src"
sys.path.insert(0, str(PYTHON_SRC))

from advisor_monitor.collectors.official_apps_public import (  # noqa: E402
    OfficialAppsPublicCollector,
    gfsec_fima_daily_curve_rows,
    gfsec_fima_official_curve_rows,
)


class GfsecFimaPerformanceTest(unittest.TestCase):
    def test_official_cumulative_curve_preserves_exact_values_and_dates(self) -> None:
        rows = gfsec_fima_official_curve_rows(
            {
                "portfolioYields": [
                    {"busiDate": 1785254400000, "totalYield": 0.126123},
                    {"busiDate": 1785340800000, "totalYield": 0.126181},
                    {"busiDate": 1785427200000, "totalYield": 0.126229},
                ],
                "indexYields": [
                    {"busiDate": 1785254400000, "totalYield": 0.0647},
                    {"busiDate": 1785427200000, "totalYield": 0.0650},
                ],
            }
        )

        self.assertEqual([row["trade_date"] for row in rows], ["2026-07-29", "2026-07-30", "2026-07-31"])
        self.assertEqual(rows[-1]["nav"], 1.126229)
        self.assertEqual(rows[-1]["cumulative_return_pct"], 12.6229)
        self.assertEqual(rows[-1]["benchmark_cumulative_return_pct"], 6.5)
        self.assertIsNone(rows[1]["benchmark_cumulative_return_pct"])
        self.assertIsNone(rows[0]["daily_return_pct"])
        self.assertAlmostEqual(rows[1]["daily_return_pct"], 0.00515042)

    def test_daily_yield_is_compounded_instead_of_used_as_cumulative_return(self) -> None:
        rows = gfsec_fima_daily_curve_rows(
            {
                "data": [
                    {
                        "busiDate": "2026-07-03",
                        "portfolioDayYield": 0.03,
                        "indexDayYield": 0.01,
                    },
                    {
                        "busiDate": "2026-07-01",
                        "portfolioDayYield": 0.01,
                        "indexDayYield": 0.02,
                    },
                    {
                        "busiDate": "2026-07-02",
                        "portfolioDayYield": -0.02,
                        "indexDayYield": None,
                    },
                ]
            }
        )

        self.assertEqual([row["trade_date"] for row in rows], ["2026-07-01", "2026-07-02", "2026-07-03"])
        self.assertEqual(rows[0]["nav"], 1.0)
        self.assertEqual(rows[1]["nav"], 0.98)
        self.assertEqual(rows[2]["nav"], 1.0094)
        self.assertEqual(rows[2]["cumulative_return_pct"], 0.94)
        self.assertEqual(rows[1]["daily_return_pct"], -2.0)
        self.assertIsNone(rows[1]["benchmark_cumulative_return_pct"])
        self.assertEqual(rows[2]["benchmark_cumulative_return_pct"], 1.0)

    def test_daily_endpoint_is_paged_until_advertised_total(self) -> None:
        collector = OfficialAppsPublicCollector.__new__(OfficialAppsPublicCollector)
        collector.gfsec_fima_daily_page_size = 2
        requested_pages: list[int] = []

        def fake_fetch(endpoint, params, raw_name):
            page = int(params["pageNum"])
            requested_pages.append(page)
            all_rows = [
                {"busiDate": "2026-07-05", "portfolioDayYield": 0.01},
                {"busiDate": "2026-07-04", "portfolioDayYield": 0.02},
                {"busiDate": "2026-07-03", "portfolioDayYield": 0.03},
                {"busiDate": "2026-07-02", "portfolioDayYield": 0.04},
                {"busiDate": "2026-07-01", "portfolioDayYield": 0.05},
            ]
            start = (page - 1) * 2
            return {
                "ok": True,
                "payload": {"total": 5, "data": all_rows[start : start + 2]},
                "snapshot_id": f"snapshot-{page}",
                "source_url": f"https://example.test?page={page}",
            }

        collector.gfsec_fima_fetch = fake_fetch

        result = collector.gfsec_fima_fetch_daily_performance("DEMO")

        self.assertTrue(result["ok"])
        self.assertEqual(requested_pages, [1, 2, 3])
        self.assertEqual(result["row_count"], 5)
        self.assertEqual(result["page_count"], 3)
        self.assertEqual(
            [row["__source_snapshot_id"] for row in result["payload"]["data"]],
            ["snapshot-1", "snapshot-1", "snapshot-2", "snapshot-2", "snapshot-3"],
        )

    def test_portfolio_collection_uses_official_curve_and_tolerates_ancillary_failures(self) -> None:
        collector = OfficialAppsPublicCollector.__new__(OfficialAppsPublicCollector)
        collector.day = "2026-08-03"
        calls: list[tuple[str, dict]] = []

        def fake_fetch(endpoint, params, raw_name):
            calls.append((endpoint, params))
            if "portfolioAndIndexYield" in endpoint:
                return {
                    "ok": True,
                    "payload": {
                        "portfolioYields": [
                            {"busiDate": 1785427200000, "totalYield": 0.126229}
                        ],
                        "indexYields": [],
                    },
                    "snapshot_id": "curve-snapshot",
                }
            return {
                "ok": endpoint.endswith("portfolioMixInfo"),
                "payload": {},
                "snapshot_id": None,
            }

        collector.gfsec_fima_fetch = fake_fetch
        collector.gfsec_fima_fetch_daily_performance = lambda code: {
            "ok": False,
            "payload": {},
            "error": "temporary failure",
        }

        result = collector.collect_gfsec_fima_portfolio("MMEGF")

        self.assertTrue(result["success"])
        self.assertEqual(result["performance_source"], "official_cumulative_curve")
        self.assertEqual(result["official_curve_row_count"], 1)
        curve_call = next(call for call in calls if "portfolioAndIndexYield" in call[0])
        self.assertEqual(curve_call[1]["startDate"], "2000-01-01")
        self.assertEqual(curve_call[1]["endDate"], "2026-08-03")

    def test_official_curve_retries_truncated_long_response_by_year(self) -> None:
        collector = OfficialAppsPublicCollector.__new__(OfficialAppsPublicCollector)
        collector.day = "2026-08-03"
        calls: list[dict] = []

        def fake_fetch(endpoint, params, raw_name):
            calls.append(dict(params))
            if Path(raw_name).name == "official_curve.json":
                return {
                    "ok": False,
                    "payload": None,
                    "snapshot_id": "truncated",
                    "source_url": "https://example.invalid/full",
                    "error": "parse_status=failed",
                }
            year = int(params["startDate"][:4])
            return {
                "ok": True,
                "payload": {
                    "indexCode": "INDEX",
                    "indexName": "官方基准",
                    "portfolioYields": [
                        {"busiDate": f"{year}-01-02", "totalYield": (year - 2024) / 100}
                    ],
                    "indexYields": [
                        {"busiDate": f"{year}-01-02", "totalYield": (year - 2024) / 200}
                    ],
                },
                "snapshot_id": f"snapshot-{year}",
                "source_url": f"https://example.invalid/{year}",
            }

        collector.gfsec_fima_fetch = fake_fetch

        result = collector.gfsec_fima_fetch_official_curve("MMEGF", "2025-06-01")

        self.assertTrue(result["ok"])
        self.assertTrue(result["windowed_fallback"])
        self.assertEqual(result["row_count"], 2)
        self.assertEqual(calls[1], {"startDate": "2025-06-01", "endDate": "2025-12-31"})
        self.assertEqual(calls[2], {"startDate": "2026-01-01", "endDate": "2026-08-03"})
        rows = gfsec_fima_official_curve_rows(result["payload"])
        self.assertEqual([row["trade_date"] for row in rows], ["2025-01-02", "2026-01-02"])
        self.assertEqual(rows[-1]["cumulative_return_pct"], 2.0)


if __name__ == "__main__":
    unittest.main()
