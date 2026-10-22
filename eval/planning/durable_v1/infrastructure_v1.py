from __future__ import annotations

from contextlib import contextmanager
import uuid

import psycopg
from psycopg import sql

from backend.tools.catalog import build_tool_registry
from backend.tools.executor import ToolExecutor
from backend.tools.models import ToolCall, ToolExecutionContext
from scripts.migrate import apply_migrations, connect_kwargs
from scripts.seed_sales import seed_sales


@contextmanager
def isolated_database():
    admin_kwargs = connect_kwargs()
    database_name = "eac_accept_durable_" + uuid.uuid4().hex
    with psycopg.connect(**admin_kwargs, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
    database_kwargs = {**admin_kwargs, "dbname": database_name}

    @contextmanager
    def connection_factory():
        connection = psycopg.connect(**database_kwargs)
        try:
            yield connection
        finally:
            connection.close()

    try:
        with psycopg.connect(**database_kwargs, autocommit=True) as database:
            apply_migrations(database)
            seed_sales(database)
        yield connection_factory
    finally:
        with psycopg.connect(**admin_kwargs, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {}").format(
                    sql.Identifier(database_name)
                )
            )


def check_database(connection_factory) -> dict:
    try:
        with connection_factory() as database:
            select_1 = database.execute("SELECT 1").fetchone()[0] == 1
            rows = database.execute(
                "SELECT count(*) FROM analytics.sales_orders "
                "WHERE dataset_version='synthetic-sales-db-v1'"
            ).fetchone()[0]
            runtime_schema = database.execute(
                "SELECT to_regnamespace('agent_runtime') IS NOT NULL"
            ).fetchone()[0]
        registry = build_tool_registry(connection_factory)
        result = ToolExecutor(registry).execute(
            ToolCall(
                name="get_postgres_sales_summary",
                arguments={"period": "2026-Q1", "region": "all"},
            ),
            ToolExecutionContext(
                principal="analytics-copilot",
                correlation_id="durable-preflight-database",
            ),
        )
        healthy = (
            select_1
            and rows == 8
            and runtime_schema
            and result.status == "success"
            and result.output["total_sales"] == "400.00"
        )
        return {
            "status": "healthy" if healthy else "unavailable",
            "select_1": select_1,
            "dataset_rows": rows,
            "agent_runtime_schema": runtime_schema,
            "tool_status": result.status,
        }
    except Exception:
        return {
            "status": "unavailable",
            "select_1": False,
            "dataset_rows": None,
            "agent_runtime_schema": False,
            "tool_status": "error",
            "error_code": "isolated_database_preflight_failed",
        }


def probe_isolated_database() -> dict:
    with isolated_database() as connection_factory:
        return check_database(connection_factory)
