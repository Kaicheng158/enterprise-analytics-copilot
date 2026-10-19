"""Server-owned deterministic Phase 5.1 demonstration plan."""

from .models import (
    CompareSalesGrowthSpec,
    Plan,
    PlanningRequest,
    PlanStep,
)
from backend.tools.models import ToolCall


def build_quarter_sales_comparison_plan(request: PlanningRequest) -> Plan:
    baseline_id = "load_baseline_sales"
    comparison_id = "load_comparison_sales"
    return Plan(
        plan_id="quarter_sales_comparison",
        task=request.task,
        baseline_period=request.baseline_period,
        comparison_period=request.comparison_period,
        region=request.region,
        steps=(
            PlanStep(
                step_id=baseline_id,
                kind="tool",
                description="Load the baseline quarter sales summary.",
                tool_call=ToolCall(
                    name="get_postgres_sales_summary",
                    arguments={
                        "period": request.baseline_period,
                        "region": request.region,
                    },
                ),
            ),
            PlanStep(
                step_id=comparison_id,
                kind="tool",
                description="Load the comparison quarter sales summary.",
                tool_call=ToolCall(
                    name="get_postgres_sales_summary",
                    arguments={
                        "period": request.comparison_period,
                        "region": request.region,
                    },
                ),
            ),
            PlanStep(
                step_id="calculate_growth",
                kind="compute",
                description="Validate comparable scope and calculate sales growth.",
                depends_on=(baseline_id, comparison_id),
                computation=CompareSalesGrowthSpec(
                    baseline_step_id=baseline_id,
                    comparison_step_id=comparison_id,
                    baseline_period=request.baseline_period,
                    comparison_period=request.comparison_period,
                    region=request.region,
                ),
            ),
        ),
    )
