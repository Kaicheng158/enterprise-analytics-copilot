"""Provider-independent Dataset plan boundary with a deterministic Fake provider."""

from __future__ import annotations

from typing import Protocol

from backend.datasets.analysis_models import DatasetBindingContext
from backend.tools.models import ToolCall

from .dataset_models import (
    AssembleDatasetResultsSpec,
    DatasetAgentRequest,
    DatasetPlan,
    DatasetPlanStep,
)
from .dataset_validation import canonical_dataset_request_sha256


class DatasetPlanProvider(Protocol):
    def propose(
        self, request: DatasetAgentRequest, context: DatasetBindingContext
    ) -> str: ...


class DeterministicDatasetPlanProvider:
    """Fake/Mock provider used by offline and isolated-database acceptance."""

    def __init__(self):
        self.calls = 0

    def propose(
        self, request: DatasetAgentRequest, context: DatasetBindingContext
    ) -> str:
        self.calls += 1
        tool_steps = []
        for index, operation in enumerate(request.operations, 1):
            validated = request.tool_input(operation)
            tool_steps.append(
                DatasetPlanStep(
                    step_id=f"analyze_{index}",
                    kind="tool",
                    description=f"Run authorized Dataset operation {index}",
                    request_operation_id=operation.operation_id,
                    tool_call=ToolCall(
                        name=operation.tool_name,
                        arguments=validated.model_dump(mode="json"),
                    ),
                )
            )
        source_ids = tuple(step.step_id for step in tool_steps)
        final = DatasetPlanStep(
            step_id="assemble_results",
            kind="compute",
            description="Consolidate verified calculations and clarification needs",
            depends_on=source_ids,
            computation=AssembleDatasetResultsSpec(source_step_ids=source_ids),
        )
        plan = DatasetPlan(
            plan_id=f"dataset-{canonical_dataset_request_sha256(request, context)[:16]}",
            project_id=request.project_id,
            binding=request.binding,
            normalized_sha256=context.normalized_sha256,
            authorization_version=context.authorization_version,
            request_sha256=canonical_dataset_request_sha256(request, context),
            steps=(*tool_steps, final),
        )
        return plan.model_dump_json()


__all__ = ["DatasetPlanProvider", "DeterministicDatasetPlanProvider"]
