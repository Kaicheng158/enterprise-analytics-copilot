"""Single composition root for the Phase 4.1 server-owned tool catalog."""

from .executor import ToolExecutor
from .registry import ToolAuthorizationPolicy, ToolDefinition, ToolRegistry
from .sales import SalesSummaryInput, SalesSummaryOutput, get_sales_summary

INTERNAL_PRINCIPAL = "analytics-copilot"


def build_tool_registry() -> ToolRegistry:
    definition = ToolDefinition(
        name="get_sales_summary",
        version="1.0.0",
        description="Aggregate deterministic synthetic sales for an allowed period and region.",
        input_model=SalesSummaryInput,
        output_model=SalesSummaryOutput,
        handler=get_sales_summary,
    )
    policy = ToolAuthorizationPolicy(
        grants={INTERNAL_PRINCIPAL: frozenset({definition.name})}
    )
    return ToolRegistry((definition,), policy)


def build_tool_executor() -> ToolExecutor:
    return ToolExecutor(build_tool_registry())
