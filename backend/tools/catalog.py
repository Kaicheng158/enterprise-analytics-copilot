"""Single composition root for the server-owned tool catalog."""

from .executor import ToolExecutor
from .registry import ToolAuthorizationPolicy, ToolDefinition, ToolRegistry
from .sales import SalesSummaryInput, SalesSummaryOutput, get_sales_summary
from .sales_db import (
    ConnectionFactory,
    PostgresSalesSummaryInput,
    PostgresSalesSummaryOutput,
    make_get_postgres_sales_summary,
    open_sales_connection,
)

INTERNAL_PRINCIPAL = "analytics-copilot"


def build_tool_registry(
    connection_factory: ConnectionFactory = open_sales_connection,
) -> ToolRegistry:
    local_definition = ToolDefinition(
        name="get_sales_summary",
        version="1.0.0",
        description="Aggregate deterministic synthetic sales for an allowed period and region.",
        input_model=SalesSummaryInput,
        output_model=SalesSummaryOutput,
        handler=get_sales_summary,
    )
    database_definition = ToolDefinition(
        name="get_postgres_sales_summary",
        version="1.0.0",
        description="Read a synthetic sales summary from the authorized PostgreSQL table.",
        input_model=PostgresSalesSummaryInput,
        output_model=PostgresSalesSummaryOutput,
        handler=make_get_postgres_sales_summary(connection_factory),
    )
    policy = ToolAuthorizationPolicy(
        grants={
            INTERNAL_PRINCIPAL: frozenset(
                {local_definition.name, database_definition.name}
            )
        }
    )
    return ToolRegistry((local_definition, database_definition), policy)


def build_tool_executor(
    connection_factory: ConnectionFactory = open_sales_connection,
) -> ToolExecutor:
    return ToolExecutor(build_tool_registry(connection_factory))
