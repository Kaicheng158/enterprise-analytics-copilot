import unittest

from eval.planning.durable_v1.infrastructure_v1 import probe_isolated_database


class DurablePlannerPreflightDatabaseTests(unittest.TestCase):
    def test_disposable_database_has_migrations_seed_and_authorized_tool(self):
        result = probe_isolated_database()
        self.assertEqual(result["status"], "healthy")
        self.assertTrue(result["select_1"])
        self.assertEqual(result["dataset_rows"], 8)
        self.assertTrue(result["agent_runtime_schema"])
        self.assertEqual(result["tool_status"], "success")


if __name__ == "__main__":
    unittest.main()
