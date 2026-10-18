import json
import unittest
import uuid

import psycopg
from psycopg import sql

from backend.tools.catalog import INTERNAL_PRINCIPAL
from backend.tools.exposed_catalog import build_exposed_tool_orchestrator
from backend.tools.models import ToolExecutionContext
from backend.tools.orchestration import ToolOrchestrationInput
from scripts.migrate import apply_migrations, connect_kwargs
from scripts.seed_sales import seed_sales


class PostgresToolExposureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kwargs = connect_kwargs()
        cls.name = "eac_test_exposure_" + uuid.uuid4().hex
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

    def test_production_orchestration_uses_postgres_source(self):
        class Provider:
            def __init__(self):
                self.requests = []

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
                source = request.prior_tool_result.result.output["data_source"]
                return json.dumps(
                    {
                        "action": "final",
                        "final_response": f"Source: {source['kind']}",
                        "tool_call": None,
                    }
                )

        provider = Provider()
        result = build_exposed_tool_orchestrator(
            provider, self.connection_factory
        ).run(
            ToolOrchestrationInput(
                user_message="Report Q2 north orders, sales, and AOV."
            ),
            ToolExecutionContext(
                principal=INTERNAL_PRINCIPAL,
                correlation_id="database-exposure-test",
            ),
        )
        self.assertEqual(
            [item["name"] for item in provider.requests[0].available_tools],
            ["get_postgres_sales_summary"],
        )
        self.assertEqual(result.tool_result.tool_name, "get_postgres_sales_summary")
        self.assertEqual(result.tool_result.output["order_count"], 2)
        self.assertEqual(result.tool_result.output["total_sales"], "300.00")
        self.assertEqual(result.tool_result.output["average_order_value"], "150.00")
        self.assertEqual(result.tool_result.output["data_source"]["kind"], "postgresql")


if __name__ == "__main__":
    unittest.main()
