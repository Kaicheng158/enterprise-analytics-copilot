import unittest
from unittest.mock import Mock, patch

from eval.tools.v3.infrastructure_guard_v1 import (
    DatabaseRuntimeHealth,
    DatabaseRuntimeUnavailable,
)
from eval.tools.v3.run_live_v3_recovery import DatabaseGuardedProvider


class DatabaseGuardedProviderTests(unittest.TestCase):
    def test_checks_before_every_provider_interaction(self):
        delegate = Mock()
        delegate.decide.side_effect = ["first", "second"]
        provider = DatabaseGuardedProvider(delegate)
        healthy = DatabaseRuntimeHealth(
            status="healthy",
            select_1=True,
            sales_relation=True,
            dataset_rows=8,
            tool_status="success",
        )
        with patch(
            "eval.tools.v3.run_live_v3_recovery.require_database_runtime",
            return_value=healthy,
        ) as guard:
            self.assertEqual(provider.decide(object()), "first")
            self.assertEqual(provider.decide(object()), "second")
        self.assertEqual(guard.call_count, 2)
        self.assertEqual(delegate.decide.call_count, 2)

    def test_database_failure_stops_before_provider_call(self):
        delegate = Mock()
        provider = DatabaseGuardedProvider(delegate)
        unavailable = DatabaseRuntimeHealth(
            status="unavailable",
            failed_stage="connection",
            error_code="database_runtime_unavailable",
        )
        with patch(
            "eval.tools.v3.run_live_v3_recovery.require_database_runtime",
            side_effect=DatabaseRuntimeUnavailable(unavailable),
        ):
            with self.assertRaises(DatabaseRuntimeUnavailable):
                provider.decide(object())
        delegate.decide.assert_not_called()
        self.assertEqual(provider.infrastructure_failure, unavailable)


if __name__ == "__main__":
    unittest.main()
