"""Fail-closed PostgreSQL readiness guard for future paid tool evaluations.

This module is deliberately separate from the frozen v3 evaluator, suite,
gate, prompt, runner, and historical evidence.  It performs no provider call.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from pydantic import Field

from backend.tools.exposed_catalog import build_exposed_tool_registry
from backend.tools.executor import ToolExecutor
from backend.tools.models import StrictModel, ToolCall, ToolExecutionContext
from backend.tools.sales_db import ConnectionFactory, open_sales_connection


GUARD_VERSION = "tool-calling-database-runtime-guard-v1"


class DatabaseRuntimeHealth(StrictModel):
    guard_version: Literal["tool-calling-database-runtime-guard-v1"] = GUARD_VERSION
    status: Literal["healthy", "unavailable"]
    failed_stage: Literal[
        "connection", "select_1", "sales_relation", "sales_dataset", "sales_tool"
    ] | None = None
    select_1: bool = False
    sales_relation: bool = False
    dataset_rows: int | None = Field(default=None, ge=0)
    tool_status: Literal["success", "error", "not_run"] = "not_run"
    error_code: Literal["database_runtime_unavailable"] | None = None


class DatabaseRuntimeUnavailable(RuntimeError):
    """Safe signal that paid provider work must not start or continue."""

    def __init__(self, health: DatabaseRuntimeHealth):
        super().__init__("database_runtime_unavailable")
        self.health = health


ToolProbe = Callable[[], bool]


def _default_tool_probe(connection_factory: ConnectionFactory) -> bool:
    executor = ToolExecutor(build_exposed_tool_registry(connection_factory))
    result = executor.execute(
        ToolCall(
            name="get_postgres_sales_summary",
            arguments={"period": "2026-Q2", "region": "north"},
        ),
        ToolExecutionContext(
            principal="analytics-copilot",
            correlation_id="database-runtime-health",
        ),
    )
    return (
        result.status == "success"
        and result.output is not None
        and result.output.get("data_source", {}).get("kind") == "postgresql"
    )


def check_database_runtime(
    connection_factory: ConnectionFactory = open_sales_connection,
    *,
    tool_probe: ToolProbe | None = None,
) -> DatabaseRuntimeHealth:
    """Check the exact PostgreSQL path required by production tool cases.

    Only safe status metadata is returned. Connection details and exception
    text are intentionally excluded from the result and from telemetry.
    """

    try:
        with connection_factory() as connection:
            row = connection.execute("SELECT 1").fetchone()
            if row is None or row[0] != 1:
                return _failure("select_1")

            relation = connection.execute(
                "SELECT to_regclass('analytics.sales_orders')::text"
            ).fetchone()
            if relation is None or relation[0] not in {
                "sales_orders",
                "analytics.sales_orders",
            }:
                return DatabaseRuntimeHealth(
                    status="unavailable",
                    failed_stage="sales_relation",
                    select_1=True,
                    error_code="database_runtime_unavailable",
                )

            dataset = connection.execute(
                """
                SELECT count(*)::integer
                FROM analytics.sales_orders
                WHERE dataset_version = %s
                """,
                ("synthetic-sales-db-v1",),
            ).fetchone()
            if dataset is None or type(dataset[0]) is not int or dataset[0] <= 0:
                return DatabaseRuntimeHealth(
                    status="unavailable",
                    failed_stage="sales_dataset",
                    select_1=True,
                    sales_relation=True,
                    dataset_rows=dataset[0] if dataset and type(dataset[0]) is int else None,
                    error_code="database_runtime_unavailable",
                )
            dataset_rows = dataset[0]
    except Exception:
        return _failure("connection")

    try:
        probe = tool_probe or (lambda: _default_tool_probe(connection_factory))
        tool_ok = probe()
    except Exception:
        tool_ok = False
    if not tool_ok:
        return DatabaseRuntimeHealth(
            status="unavailable",
            failed_stage="sales_tool",
            select_1=True,
            sales_relation=True,
            dataset_rows=dataset_rows,
            tool_status="error",
            error_code="database_runtime_unavailable",
        )
    return DatabaseRuntimeHealth(
        status="healthy",
        select_1=True,
        sales_relation=True,
        dataset_rows=dataset_rows,
        tool_status="success",
    )


def _failure(stage: Literal["connection", "select_1"]) -> DatabaseRuntimeHealth:
    return DatabaseRuntimeHealth(
        status="unavailable",
        failed_stage=stage,
        error_code="database_runtime_unavailable",
    )


def require_database_runtime(
    connection_factory: ConnectionFactory = open_sales_connection,
    *,
    tool_probe: ToolProbe | None = None,
) -> DatabaseRuntimeHealth:
    """Fail before a paid interaction when production data is unavailable."""

    health = check_database_runtime(connection_factory, tool_probe=tool_probe)
    if health.status != "healthy":
        raise DatabaseRuntimeUnavailable(health)
    return health


def run_case_with_database_guard(run_case, round_id, case, settings):
    """Guard future live cases immediately before their provider interaction."""

    from eval.tools.v3.exposure_binding_v3 import exposure_profile_for_case

    if exposure_profile_for_case(case) == "production":
        require_database_runtime()
    return run_case(round_id, case, settings)
