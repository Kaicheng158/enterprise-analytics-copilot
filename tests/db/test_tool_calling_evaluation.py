import unittest
import uuid

import psycopg
from psycopg import sql

from eval.tools.offline_runner import final_decision, run_fake_case, tool_decision
from scripts.migrate import apply_migrations, connect_kwargs
from scripts.seed_sales import seed_sales


class ToolCallingEvaluationDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kwargs = connect_kwargs()
        cls.name = "eac_test_tool_eval_" + uuid.uuid4().hex
        with psycopg.connect(**cls.kwargs, autocommit=True) as admin:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(cls.name)))
        cls.addClassCleanup(cls.cleanup_database)
        cls.database_kwargs = {**cls.kwargs, "dbname": cls.name}
        with psycopg.connect(**cls.database_kwargs, autocommit=True) as database:
            apply_migrations(database)
            seed_sales(database)

    @classmethod
    def cleanup_database(cls):
        with psycopg.connect(**cls.kwargs, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(cls.name)))

    def connection_factory(self):
        return psycopg.connect(**self.database_kwargs)

    def test_fake_evaluation_record_uses_real_read_only_postgres_tool(self):
        provider, result, record = run_fake_case(
            run_id="database-eval-q2-north",
            case_id="selection/postgres_sales_summary",
            user_message="Use stored sales data to summarize Q2 north.",
            decisions=[
                tool_decision(
                    "get_postgres_sales_summary",
                    {"period": "2026-Q2", "region": "north"},
                ),
                final_decision("Q2 north sales were USD 300.00 across 2 orders."),
            ],
            connection_factory=self.connection_factory,
        )
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.tool_result.output["total_sales"], "300.00")
        self.assertEqual(result.tool_result.output["average_order_value"], "150.00")
        self.assertEqual(record.telemetry.selected_tool, "get_postgres_sales_summary")
        self.assertTrue(record.telemetry.arguments_valid)
        self.assertEqual(record.telemetry.execution_status, "success")
        self.assertEqual(provider.requests[1].prior_tool_result.authority, "untrusted_data")
