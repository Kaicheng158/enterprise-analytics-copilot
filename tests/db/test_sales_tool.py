import json
import unittest
import uuid
from unittest.mock import Mock

import psycopg
from psycopg import sql

from backend.tools.catalog import INTERNAL_PRINCIPAL, build_tool_executor, build_tool_registry
from backend.tools.models import ToolCall, ToolExecutionContext
from backend.tools.orchestration import (
    ToolOrchestrationInput,
    ToolOrchestrator,
    ToolProviderRequest,
)
from scripts.migrate import apply_migrations, connect_kwargs
from scripts.seed_sales import seed_sales


class PostgresSalesToolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kwargs = connect_kwargs()
        cls.name = "eac_test_sales_" + uuid.uuid4().hex
        with psycopg.connect(**cls.kwargs, autocommit=True) as admin:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(cls.name)))
        cls.addClassCleanup(cls.cleanup_database)
        cls.database_kwargs = {**cls.kwargs, "dbname": cls.name}
        with psycopg.connect(**cls.database_kwargs, autocommit=True) as database:
            apply_migrations(database)

    @classmethod
    def cleanup_database(cls):
        with psycopg.connect(**cls.kwargs, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(cls.name)))

    def connection_factory(self):
        return psycopg.connect(**self.database_kwargs)

    def setUp(self):
        with psycopg.connect(**self.database_kwargs, autocommit=True) as database:
            database.execute("TRUNCATE analytics.sales_orders")
            seed_sales(database)
        self.executor = build_tool_executor(self.connection_factory)
        self.context = ToolExecutionContext(
            principal=INTERNAL_PRINCIPAL,
            correlation_id="database-test",
        )

    def execute(self, period, region="all", context=None):
        return self.executor.execute(
            ToolCall(
                name="get_postgres_sales_summary",
                arguments={"period": period, "region": region},
            ),
            context or self.context,
        )

    def test_migration_shape_constraints_and_index(self):
        with psycopg.connect(**self.database_kwargs) as database:
            columns = database.execute(
                """SELECT column_name,data_type,is_nullable
                   FROM information_schema.columns
                   WHERE table_schema='analytics' AND table_name='sales_orders'
                   ORDER BY ordinal_position"""
            ).fetchall()
            indexes = database.execute(
                "SELECT indexname FROM pg_indexes WHERE schemaname='analytics' AND tablename='sales_orders'"
            ).fetchall()
            constraints = " ".join(
                row[0]
                for row in database.execute(
                    """SELECT pg_get_constraintdef(oid)
                       FROM pg_constraint
                       WHERE conrelid='analytics.sales_orders'::regclass"""
                ).fetchall()
            )
            public_access = database.execute(
                """SELECT has_schema_privilege('public','analytics','USAGE'),
                          has_table_privilege('public','analytics.sales_orders','SELECT')"""
            ).fetchone()
        self.assertEqual(
            [column[0] for column in columns],
            [
                "order_id", "dataset_version", "period", "region", "currency",
                "amount_cents", "occurred_on", "created_at",
            ],
        )
        self.assertIn(("sales_orders_summary_filter",), indexes)
        self.assertIn("amount_cents >= 0", constraints)
        self.assertIn("region", constraints)
        self.assertIn("currency", constraints)
        self.assertEqual(public_access, (False, False))

    def test_seed_is_idempotent(self):
        with psycopg.connect(**self.database_kwargs, autocommit=True) as database:
            database.execute("TRUNCATE analytics.sales_orders")
            self.assertEqual(seed_sales(database), 8)
            self.assertEqual(seed_sales(database), 0)
            self.assertEqual(
                database.execute("SELECT count(*) FROM analytics.sales_orders").fetchone()[0],
                8,
            )

    def test_different_filters_return_exact_results_and_source(self):
        q1 = self.execute("2026-Q1", "all")
        q2_north = self.execute("2026-Q2", "north")
        self.assertEqual(q1.status, "success")
        self.assertEqual(
            (q1.output["order_count"], q1.output["total_sales"], q1.output["average_order_value"]),
            (4, "400.00", "100.00"),
        )
        self.assertEqual(
            (q2_north.output["order_count"], q2_north.output["total_sales"], q2_north.output["average_order_value"]),
            (2, "300.00", "150.00"),
        )
        self.assertEqual(q1.output["currency"], "USD")
        self.assertEqual(
            q1.output["data_source"],
            {
                "kind": "postgresql",
                "relation": "analytics.sales_orders",
                "dataset_version": "synthetic-sales-db-v1",
                "read_only": True,
            },
        )

    def test_no_data_returns_zero_summary(self):
        result = self.execute("2026-Q3", "south")
        self.assertEqual(result.status, "success")
        self.assertEqual(
            (result.output["order_count"], result.output["total_sales"], result.output["average_order_value"]),
            (0, "0.00", None),
        )

    def test_matched_zero_value_order_has_zero_aov(self):
        with psycopg.connect(**self.database_kwargs, autocommit=True) as database:
            database.execute(
                """INSERT INTO analytics.sales_orders
                   (order_id,dataset_version,period,region,currency,amount_cents,occurred_on)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                (
                    "00000000-0000-4000-8000-000000000301",
                    "synthetic-sales-db-v1",
                    "2026-Q3",
                    "north",
                    "USD",
                    0,
                    "2026-07-01",
                ),
            )
        result = self.execute("2026-Q3", "north")
        self.assertEqual(result.status, "success")
        self.assertEqual(
            (result.output["order_count"], result.output["total_sales"], result.output["average_order_value"]),
            (1, "0.00", "0.00"),
        )

    def test_unauthorized_access_does_not_query(self):
        connection_factory = Mock(side_effect=AssertionError("must not connect"))
        executor = build_tool_executor(connection_factory)
        denied = executor.execute(
            ToolCall(
                name="get_postgres_sales_summary",
                arguments={"period": "2026-Q1", "region": "all"},
            ),
            ToolExecutionContext(
                principal="untrusted-caller", correlation_id="denied-database-test"
            ),
        )
        self.assertEqual(denied.error.code, "tool_not_authorized")
        self.assertIsNone(denied.output)
        connection_factory.assert_not_called()

    def test_illegal_parameters_and_sql_injection_fail_before_query(self):
        attacks = [
            {"period": "2026-Q1' OR '1'='1", "region": "all"},
            {"period": "2026-Q1", "region": "north'; DROP TABLE analytics.sales_orders;--"},
            {"period": "2026-Q1", "region": "all", "sql": "SELECT * FROM pg_roles"},
        ]
        for arguments in attacks:
            with self.subTest(arguments=arguments):
                result = self.executor.execute(
                    ToolCall(name="get_postgres_sales_summary", arguments=arguments),
                    self.context,
                )
                self.assertEqual(result.error.code, "tool_input_invalid")
        with psycopg.connect(**self.database_kwargs) as database:
            self.assertEqual(
                database.execute("SELECT count(*) FROM analytics.sales_orders").fetchone()[0],
                8,
            )

    def test_database_failure_is_sanitized(self):
        def unavailable():
            raise psycopg.OperationalError("private database detail")

        executor = build_tool_executor(unavailable)
        with self.assertLogs("uvicorn.error", level="INFO") as logs:
            result = executor.execute(
                ToolCall(
                    name="get_postgres_sales_summary",
                    arguments={"period": "2026-Q1", "region": "all"},
                ),
                self.context,
            )
        self.assertEqual(result.error.code, "tool_database_error")
        self.assertEqual(result.error.message, "Tool database query failed")
        self.assertNotIn("private database detail", json.dumps(result.model_dump(mode="json")))
        self.assertNotIn("private database detail", str(logs.output))

    def test_fake_provider_orchestrates_real_postgres_tool(self):
        class DatabaseFakeProvider:
            def __init__(self):
                self.requests: list[ToolProviderRequest] = []

            def decide(self, request):
                self.requests.append(request)
                if request.prior_tool_result is None:
                    return json.dumps(
                        {
                            "action": "tool_call",
                            "final_response": None,
                            "tool_call": {
                                "name": "get_postgres_sales_summary",
                                "arguments": {
                                    "period": "2026-Q2",
                                    "region": "north",
                                },
                            },
                        }
                    )
                output = request.prior_tool_result.result.output
                return json.dumps(
                    {
                        "action": "final",
                        "final_response": (
                            f"Q2 north sales were USD {output['total_sales']} "
                            f"across {output['order_count']} orders."
                        ),
                        "tool_call": None,
                    }
                )

        provider = DatabaseFakeProvider()
        registry = build_tool_registry(self.connection_factory)
        result = ToolOrchestrator(
            registry, build_tool_executor(self.connection_factory), provider
        ).run(
            ToolOrchestrationInput(
                user_message="Summarize Q2 north sales from PostgreSQL."
            ),
            self.context,
        )
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.tool_result.status, "success")
        self.assertEqual(result.tool_result.output["total_sales"], "300.00")
        self.assertEqual(result.tool_result.output["order_count"], 2)
        self.assertEqual(
            result.final_response,
            "Q2 north sales were USD 300.00 across 2 orders.",
        )
        self.assertEqual(provider.requests[1].prior_tool_result.authority, "untrusted_data")


if __name__ == "__main__":
    unittest.main()
