from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = next(
    parent / "_共享组件" / "生产程序" / "标准化数据稽核.py"
    for parent in Path(__file__).resolve().parents
    if (parent / "_共享组件" / "生产程序" / "标准化数据稽核.py").is_file()
)
SPEC = importlib.util.spec_from_file_location("standard_data_audit_gfsec_history", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class GfsecFimaPositionHistoryAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_catalog = MODULE.RULE_CATALOG
        MODULE.RULE_CATALOG = {
            "GFSEC_FIMA_HISTORY_SOURCE_PARSE": {
                "severity": "warn",
                "检查对象": "历史源解析",
                "原因说明": "fixture",
                "优化建议": "fixture",
                "修复责任脚本": "fixture",
            },
            "GFSEC_FIMA_HISTORY_EFFECTIVE_DATE_DISCLOSURE": {
                "severity": "warn",
                "检查对象": "生效日披露",
                "原因说明": "fixture",
                "优化建议": "fixture",
                "修复责任脚本": "fixture",
            },
            "GFSEC_FIMA_HISTORY_INFERENCE_BOUNDARY": {
                "severity": "error",
                "检查对象": "推断边界",
                "原因说明": "fixture",
                "优化建议": "fixture",
                "修复责任脚本": "fixture",
            },
        }

    def tearDown(self) -> None:
        MODULE.RULE_CATALOG = self.original_catalog

    @staticmethod
    def write_bundle(preview_root: Path, *, promoted: bool = False) -> dict[str, object]:
        output = preview_root / "20260809T120000+0800"
        output.mkdir(parents=True)
        summary = {
            "parameters": {"weight_close_min_pct": 99.5, "weight_close_max_pct": 100.5},
            "semantics": {"main_database_written": False, "official_rebalance_table_written": False},
            "counts": {
                "state_occurrence_count": 1,
                "position_snapshot_row_count": 1,
                "transition_count": 1,
                "change_candidate_count": 1,
            },
        }
        validation = {
            "status": "warn",
            "checks": [
                {
                    "check_id": "GFSEC_FIMA_HISTORY_SOURCE_PARSE",
                    "status": "warn",
                    "detail": "one source file was excluded",
                    "count": 1,
                },
                {
                    "check_id": "GFSEC_FIMA_HISTORY_EFFECTIVE_DATE_DISCLOSURE",
                    "status": "warn",
                    "detail": "effectiveDate is not disclosed",
                    "count": 0,
                },
                {
                    "check_id": "GFSEC_FIMA_HISTORY_INFERENCE_BOUNDARY",
                    "status": "passed",
                    "detail": "not promoted",
                    "count": 0,
                },
            ],
        }
        transition = {
            "candidate_id": "candidate-1",
            "eligible_for_official_rebalance_table": promoted,
            "is_change_candidate": True,
        }
        files: dict[str, object] = {
            "summary.json": summary,
            "validation.json": validation,
            "state_snapshots.jsonl": [{"state_id": "state-1", "total_weight_pct": 100.0}],
            "position_snapshots.jsonl": [{"state_id": "state-1", "fund_code": "000001"}],
            "transition_audit.jsonl": [transition],
            "change_candidates.jsonl": [transition],
        }
        for name, payload in files.items():
            path = output / name
            if name.endswith(".jsonl"):
                path.write_text(
                    "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in payload),
                    encoding="utf-8",
                )
            else:
                path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        (output / "summary.md").write_text("# fixture\n", encoding="utf-8")
        return {
            "requiredFiles": [
                "summary.json",
                "summary.md",
                "validation.json",
                "state_snapshots.jsonl",
                "position_snapshots.jsonl",
                "transition_audit.jsonl",
                "change_candidates.jsonl",
            ],
            "requiredValidationCheckIds": [
                "GFSEC_FIMA_HISTORY_SOURCE_PARSE",
                "GFSEC_FIMA_HISTORY_EFFECTIVE_DATE_DISCLOSURE",
                "GFSEC_FIMA_HISTORY_INFERENCE_BOUNDARY",
            ],
        }

    def test_internal_warnings_are_promoted_to_project_audit_issues(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            preview_root = Path(temporary) / "preview"
            check_rule = self.write_bundle(preview_root)
            issues: list[dict] = []

            MODULE.audit_gfsec_fima_position_history_preview(
                issues,
                {"gfsecFimaPositionHistoryPreviewChecks": check_rule},
                preview_root=preview_root,
            )

        self.assertEqual(
            [item["ruleId"] for item in issues],
            [
                "GFSEC_FIMA_HISTORY_SOURCE_PARSE",
                "GFSEC_FIMA_HISTORY_EFFECTIVE_DATE_DISCLOSURE",
            ],
        )
        self.assertTrue(all(item["severity"] == "warn" for item in issues))

    def test_independent_audit_blocks_candidate_promotion_to_official_table(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            preview_root = Path(temporary) / "preview"
            check_rule = self.write_bundle(preview_root, promoted=True)
            issues: list[dict] = []

            MODULE.audit_gfsec_fima_position_history_preview(
                issues,
                {"gfsecFimaPositionHistoryPreviewChecks": check_rule},
                preview_root=preview_root,
            )

        boundary = [item for item in issues if item["ruleId"] == "GFSEC_FIMA_HISTORY_INFERENCE_BOUNDARY"]
        self.assertEqual(len(boundary), 1)
        self.assertEqual(boundary[0]["severity"], "error")


if __name__ == "__main__":
    unittest.main(verbosity=2)
