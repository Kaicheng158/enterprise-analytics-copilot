import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.planning.deepseek_planner_adapter import DeepSeekPlannerSettings
from eval.planning.v1.preflight_v1 import run_preflight, write_preflight


class PlannerPreflightV1Tests(unittest.TestCase):
    def healthy_database(self):
        return {
            "status": "healthy",
            "select_1": True,
            "sales_relation": True,
            "dataset_rows": 8,
            "tool_status": "success",
        }

    @patch(
        "eval.planning.v1.preflight_v1.load_deepseek_planner_settings",
        return_value=DeepSeekPlannerSettings(enabled=False, api_key="test-key"),
    )
    def test_ready_report_is_secret_free_and_performs_no_provider_call(self, _settings):
        report = run_preflight(
            offline_tests_passed=True,
            offline_test_count=1,
            database_tests_passed=True,
            database_test_count=1,
            database_probe=self.healthy_database,
        )
        self.assertEqual(report["status"], "READY")
        self.assertEqual(report["paid_api_calls"], 0)
        self.assertTrue(all(report["checks"].values()))
        self.assertTrue(report["runtime"]["api_key_configured"])
        self.assertNotIn("test-key", str(report))

    @patch(
        "eval.planning.v1.preflight_v1.load_deepseek_planner_settings",
        return_value=DeepSeekPlannerSettings(enabled=False, api_key="test-key"),
    )
    def test_database_or_test_failure_blocks_readiness(self, _settings):
        report = run_preflight(
            offline_tests_passed=False,
            offline_test_count=0,
            database_tests_passed=True,
            database_test_count=1,
            database_probe=lambda: {"status": "unavailable"},
        )
        self.assertEqual(report["status"], "NOT_READY")
        self.assertFalse(report["checks"]["offline_tests"])
        self.assertFalse(report["checks"]["database_health"])

    @patch(
        "eval.planning.v1.preflight_v1.load_deepseek_planner_settings",
        return_value=DeepSeekPlannerSettings(enabled=False, api_key="test-key"),
    )
    def test_non_route_objects_do_not_break_public_api_check(self, _settings):
        from eval.planning.v1 import preflight_v1

        original = list(preflight_v1.app.routes)
        try:
            preflight_v1.app.routes.append(object())
            report = run_preflight(
                offline_tests_passed=True,
                offline_test_count=1,
                database_tests_passed=True,
                database_test_count=1,
                database_probe=self.healthy_database,
            )
        finally:
            preflight_v1.app.routes[:] = original
        self.assertTrue(report["checks"]["public_planner_api_absent"])

    def test_evidence_writer_rejects_overwrite_and_wrong_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            wrong_root = Path(temporary)
            with self.assertRaisesRegex(ValueError, "target_unsafe"):
                write_preflight({}, wrong_root / "report.json")


if __name__ == "__main__":
    unittest.main()
