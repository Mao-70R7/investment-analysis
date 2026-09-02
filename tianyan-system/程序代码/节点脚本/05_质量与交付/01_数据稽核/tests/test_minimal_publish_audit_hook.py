from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SCRIPT_PATH = next(
    parent / "_共享组件" / "生产程序" / "运行项目数据稽核hook.py"
    for parent in Path(__file__).resolve().parents
    if (parent / "_共享组件" / "生产程序" / "运行项目数据稽核hook.py").is_file()
)
sys.path.insert(0, str(SCRIPT_PATH.parent))
SPEC = importlib.util.spec_from_file_location("project_data_audit_hook", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class MinimalPublishAuditHookTests(unittest.TestCase):
    def build_package(self, root: Path) -> Path:
        report_root = root / "minimal"
        report_root.mkdir()
        payload = report_root / "payload.txt"
        payload.write_text("verified", encoding="utf-8")
        payload_bytes = payload.read_bytes()
        (report_root / "package_validation.json").write_text(
            json.dumps(
                {
                    "status": "ready",
                    "checks": {"blocking": 0},
                    "policy": {"blockingZeroChecks": ["blocking"]},
                }
            ),
            encoding="utf-8",
        )
        (report_root / "deployment_manifest.json").write_text(
            json.dumps(
                {
                    "buildId": "minimal-test",
                    "fileCount": 1,
                    "totalBytes": len(payload_bytes),
                    "files": [
                        {
                            "path": "payload.txt",
                            "size": len(payload_bytes),
                            "sha256": hashlib.sha256(payload_bytes).hexdigest(),
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        (report_root / "version.json").write_text(
            json.dumps({"buildId": "minimal-test"}),
            encoding="utf-8",
        )
        return report_root

    def test_valid_minimal_package_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report_root = self.build_package(root)

            _, report = MODULE.audit_minimal_publish_package(
                report_root,
                output_root=root / "audit",
                run_id="valid",
            )

            self.assertEqual(report["status"], "ok")
            self.assertEqual(report["summary"]["error"], 0)
            self.assertEqual(report["summary"]["verifiedFileCount"], 1)
            self.assertTrue(
                MODULE.is_materialized_minimal_publish_package(report_root)
            )

    def test_manifest_hash_mismatch_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report_root = self.build_package(root)
            (report_root / "payload.txt").write_text("changed", encoding="utf-8")

            _, report = MODULE.audit_minimal_publish_package(
                report_root,
                output_root=root / "audit",
                run_id="invalid",
            )

            self.assertEqual(report["status"], "error")
            self.assertEqual(report["issues"][0]["ruleId"], "PAGE_REQUIRED_PACK_MISSING")
            self.assertIn("大小不一致", report["issues"][0]["detail"])

    def test_nonzero_blocking_check_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report_root = self.build_package(root)
            validation_path = report_root / "package_validation.json"
            validation = json.loads(validation_path.read_text(encoding="utf-8"))
            validation["checks"]["blocking"] = 1
            validation_path.write_text(json.dumps(validation), encoding="utf-8")

            _, report = MODULE.audit_minimal_publish_package(
                report_root,
                output_root=root / "audit",
                run_id="blocking",
            )

            self.assertEqual(report["status"], "error")
            self.assertIn("阻断检查不为 0", report["issues"][0]["detail"])

    def test_uncompressed_staging_source_is_not_treated_as_final_package(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report_root = Path(temporary)
            (report_root / "deployment_manifest.json").write_text(
                json.dumps(
                    {
                        "pageSet": "minimal_publish",
                        "status": "ready",
                        "basicData": {"exists": True},
                    }
                ),
                encoding="utf-8",
            )

            self.assertFalse(
                MODULE.is_materialized_minimal_publish_package(report_root)
            )

    def test_page_name_array_routes_materialized_package_to_minimal_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report_root = self.build_package(Path(temporary))
            manifest_path = report_root / "deployment_manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["pageSet"] = ["策略列表", "策略详情", "基金详情"]
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

            self.assertEqual(
                MODULE.resolve_manifest_page_set(report_root, "auto"),
                "minimal_publish",
            )

    def test_nonzero_standard_audit_still_writes_hook_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report_root = root / "staging"
            (report_root / "basic_data" / "data").mkdir(parents=True)
            output_root = root / "hook"
            args = SimpleNamespace(
                mode="manual",
                report_root=report_root,
                audit_only=True,
                skip_static=True,
                fail_on_warn=False,
                output_root=output_root,
                page_set="basic_data",
            )

            def fake_run(command, **_kwargs):
                audit_root = Path(command[command.index("--output-root") + 1])
                if not audit_root.is_absolute():
                    audit_root = MODULE.PROJECT_ROOT / audit_root
                audit_dir = audit_root / "2026-08-12" / "failed-audit"
                audit_dir.mkdir(parents=True)
                (audit_dir / "data_audit_report.json").write_text(
                    json.dumps(
                        {
                            "status": "error",
                            "summary": {"error": 1, "warn": 0, "total": 1},
                            "issues": [
                                {
                                    "severity": "error",
                                    "ruleId": "SQLITE_REQUIRED_FIELD_MISSING",
                                    "scope": "sqlite.test",
                                    "item": "required field missing",
                                }
                            ],
                        }
                    ),
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(command, 2)

            with (
                patch.object(MODULE, "parse_args", return_value=args),
                patch.object(MODULE, "now_text", return_value="hook-run"),
                patch.object(MODULE, "run", side_effect=fake_run),
                patch.object(
                    MODULE,
                    "read_rule_catalog",
                    return_value={"SQLITE_REQUIRED_FIELD_MISSING": {}},
                ),
                self.assertRaises(SystemExit) as raised,
            ):
                MODULE.main()

            self.assertEqual(raised.exception.code, 2)
            summaries = list(output_root.glob("*/*/hook_summary.json"))
            self.assertEqual(len(summaries), 1)
            summary = json.loads(summaries[0].read_text(encoding="utf-8"))
            self.assertEqual(summary["status"], "error")
            self.assertEqual(summary["auditSummary"]["error"], 1)
            self.assertTrue(summary["auditReportPath"].endswith("data_audit_report.json"))


if __name__ == "__main__":
    unittest.main()
