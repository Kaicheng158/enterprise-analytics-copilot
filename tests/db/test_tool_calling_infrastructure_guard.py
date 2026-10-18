import unittest

from eval.tools.v3.infrastructure_guard_v1 import check_database_runtime


class DatabaseRuntimeGuardIntegrationTests(unittest.TestCase):
    def test_actual_agent_database_and_sales_tool_are_healthy(self):
        health = check_database_runtime()
        self.assertEqual(health.status, "healthy")
        self.assertTrue(health.select_1)
        self.assertTrue(health.sales_relation)
        self.assertGreater(health.dataset_rows, 0)
        self.assertEqual(health.tool_status, "success")


if __name__ == "__main__":
    unittest.main()
