import unittest
from contextlib import contextmanager
from unittest.mock import patch

from eval.tools.v3.infrastructure_guard_v1 import (
    DatabaseRuntimeUnavailable,
    check_database_runtime,
    require_database_runtime,
    run_case_with_database_guard,
)
from eval.tools.v3.suite_v3 import load_cases_v3


class Result:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class Connection:
    def __init__(self, *, select_one=(1,), relation=("sales_orders",), dataset=(8,)):
        self.rows = [select_one, relation, dataset]
        self.closed = False

    def execute(self, _query, _parameters=None):
        return Result(self.rows.pop(0))

    def close(self):
        self.closed = True


def factory(connection=None, error=None):
    @contextmanager
    def open_connection():
        if error:
            raise error
        try:
            yield connection
        finally:
            connection.close()

    return open_connection


class DatabaseRuntimeGuardTests(unittest.TestCase):
    def test_healthy_path_checks_database_dataset_tool_and_closes_connection(self):
        connection = Connection()
        health = check_database_runtime(
            factory(connection), tool_probe=lambda: True
        )
        self.assertEqual(health.status, "healthy")
        self.assertEqual(health.dataset_rows, 8)
        self.assertEqual(health.tool_status, "success")
        self.assertTrue(connection.closed)

    def test_connection_failure_is_safe_and_fail_closed(self):
        health = check_database_runtime(
            factory(error=OSError("secret host details")), tool_probe=lambda: True
        )
        self.assertEqual(health.status, "unavailable")
        self.assertEqual(health.failed_stage, "connection")
        self.assertEqual(health.error_code, "database_runtime_unavailable")
        self.assertNotIn("secret", health.model_dump_json())

    def test_missing_relation_and_empty_dataset_fail_before_tool_probe(self):
        relation = check_database_runtime(
            factory(Connection(relation=(None,))), tool_probe=lambda: True
        )
        self.assertEqual(relation.failed_stage, "sales_relation")
        dataset = check_database_runtime(
            factory(Connection(dataset=(0,))), tool_probe=lambda: True
        )
        self.assertEqual(dataset.failed_stage, "sales_dataset")

    def test_tool_failure_is_not_mistaken_for_database_readiness(self):
        health = check_database_runtime(
            factory(Connection()), tool_probe=lambda: False
        )
        self.assertEqual(health.failed_stage, "sales_tool")
        self.assertEqual(health.tool_status, "error")

    def test_require_raises_safe_stop_signal(self):
        with self.assertRaises(DatabaseRuntimeUnavailable) as raised:
            require_database_runtime(
                factory(error=ConnectionError("private endpoint")),
                tool_probe=lambda: True,
            )
        self.assertEqual(str(raised.exception), "database_runtime_unavailable")

    def test_future_production_case_stops_before_provider_work(self):
        called = False

        def paid_case(*_args):
            nonlocal called
            called = True

        production_case = next(
            case
            for case in load_cases_v3()["cases"]
            if case["id"] == "selection/postgres_sales_summary"
        )
        with patch(
            "eval.tools.v3.infrastructure_guard_v1.require_database_runtime",
            side_effect=DatabaseRuntimeUnavailable(
                check_database_runtime(
                    factory(error=ConnectionError("offline")),
                    tool_probe=lambda: True,
                )
            ),
        ):
            with self.assertRaises(DatabaseRuntimeUnavailable):
                run_case_with_database_guard(
                    paid_case, "future-round", production_case, object()
                )
        self.assertFalse(called)


if __name__ == "__main__":
    unittest.main()
