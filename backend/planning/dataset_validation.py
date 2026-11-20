"""Fail-closed validation for versioned Dataset Agent plans."""

from __future__ import annotations

from hashlib import sha256
import json

from pydantic import ValidationError

from backend.datasets.analysis_models import (
    DatasetAggregateInput,
    DatasetBindingContext,
    DatasetComparisonInput,
    DatasetComparisonV2Input,
    DatasetDerivedMetricInput,
    DatasetProfileInput,
    DatasetQualityInput,
    DatasetRankingInput,
)

from .dataset_models import (
    DATASET_TOOL_INPUTS,
    DATASET_TOOL_NAMES,
    DatasetAgentRequest,
    DatasetAgentSettings,
    DatasetPlan,
    DatasetPlanIssue,
    DatasetPlanValidationResult,
)
from .validation import canonical_plan_sha256


def canonical_dataset_request_sha256(
    request: DatasetAgentRequest, context: DatasetBindingContext
) -> str:
    payload = {
        "request": request.model_dump(mode="json"),
        "dataset_identity": {
            "normalized_sha256": context.normalized_sha256,
            "authorization_version": context.authorization_version,
        },
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


class DatasetPlanValidator:
    def __init__(self, *, settings: DatasetAgentSettings):
        self.settings = settings

    def validate(
        self,
        plan: DatasetPlan,
        request: DatasetAgentRequest,
        context: DatasetBindingContext,
    ) -> DatasetPlanValidationResult:
        plan_hash = canonical_plan_sha256(plan)
        issues: list[DatasetPlanIssue] = []
        tool_steps = [step for step in plan.steps if step.kind == "tool"]
        if len(plan.steps) > self.settings.max_steps:
            issues.append(DatasetPlanIssue(code="step_limit_exceeded"))
        if len(tool_steps) > self.settings.max_tool_calls:
            issues.append(DatasetPlanIssue(code="tool_call_limit_exceeded"))
        if len(plan.steps) > self.settings.max_budget_units:
            issues.append(DatasetPlanIssue(code="budget_limit_exceeded"))

        request_hash = canonical_dataset_request_sha256(request, context)
        if (
            plan.project_id != request.project_id
            or plan.project_id != context.project_id
            or plan.binding != request.binding
            or plan.binding != context.binding
            or plan.normalized_sha256 != context.normalized_sha256
            or plan.authorization_version != context.authorization_version
            or plan.request_sha256 != request_hash
        ):
            issues.append(DatasetPlanIssue(code="plan_request_mismatch"))

        identifiers = tuple(step.step_id for step in plan.steps)
        known = set(identifiers)
        if any(dep not in known for step in plan.steps for dep in step.depends_on):
            issues.append(DatasetPlanIssue(code="plan_dependency_unknown"))
        else:
            order = self._topological_order(plan)
            if order is None:
                issues.append(DatasetPlanIssue(code="plan_cycle_detected"))
            elif order != identifiers:
                issues.append(DatasetPlanIssue(code="plan_dependency_order_invalid"))

        expected = {item.operation_id: item for item in request.operations}
        actual_ids = [step.request_operation_id for step in tool_steps]
        if actual_ids != [item.operation_id for item in request.operations]:
            issues.append(DatasetPlanIssue(code="plan_request_mismatch"))

        table_fields = {item.table_index: set(item.fields) for item in context.tables}
        for step in tool_steps:
            operation_id = step.request_operation_id
            operation = expected.get(operation_id)
            if operation is None or step.tool_call.name not in DATASET_TOOL_NAMES:
                issues.append(
                    DatasetPlanIssue(
                        code="action_not_authorized", operation_id=operation_id
                    )
                )
                continue
            if step.tool_call.name != operation.tool_name:
                issues.append(
                    DatasetPlanIssue(
                        code="plan_request_mismatch", operation_id=operation_id
                    )
                )
                continue
            model = DATASET_TOOL_INPUTS[step.tool_call.name]
            try:
                validated = model.model_validate(step.tool_call.arguments, strict=True)
            except ValidationError:
                issues.append(
                    DatasetPlanIssue(
                        code="tool_input_invalid", operation_id=operation_id
                    )
                )
                continue
            expected_input = request.tool_input(operation)
            if validated != expected_input or validated.binding != context.binding:
                issues.append(
                    DatasetPlanIssue(
                        code="plan_request_mismatch", operation_id=operation_id
                    )
                )
                continue
            allowed = table_fields.get(validated.table_index)
            if allowed is None:
                issues.append(
                    DatasetPlanIssue(
                        code="tool_input_invalid", operation_id=operation_id
                    )
                )
                continue
            missing = tuple(sorted(self._referenced_fields(validated) - allowed))
            if missing:
                issues.append(
                    DatasetPlanIssue(
                        code="field_not_found",
                        operation_id=operation_id,
                        fields=missing,
                    )
                )

        deduplicated = []
        seen = set()
        for issue in issues:
            key = json.dumps(issue.model_dump(mode="json"), sort_keys=True)
            if key not in seen:
                seen.add(key)
                deduplicated.append(issue)
        if deduplicated:
            return DatasetPlanValidationResult(
                status="invalid",
                plan_sha256=plan_hash,
                execution_order=(),
                issues=tuple(deduplicated),
            )
        return DatasetPlanValidationResult(
            status="valid",
            plan_sha256=plan_hash,
            execution_order=identifiers,
            issues=(),
        )

    @staticmethod
    def _referenced_fields(validated) -> set[str]:
        fields: set[str] = {item.field for item in getattr(validated, "filters", ())}
        if isinstance(validated, (DatasetProfileInput, DatasetQualityInput)):
            fields.update(validated.fields)
        elif isinstance(validated, DatasetAggregateInput):
            fields.update(validated.group_by)
            fields.update(item.field for item in validated.aggregations if item.field)
            valid_sort = set(validated.group_by) | {
                item.result_id for item in validated.aggregations
            }
            fields.update(item.key for item in validated.sort if item.key not in valid_sort)
        elif isinstance(validated, DatasetComparisonInput):
            fields.update((validated.dimension, validated.measure))
        elif isinstance(validated, DatasetComparisonV2Input):
            fields.update(validated.group_by)
            fields.update(
                (validated.baseline_measure, validated.comparison_measure)
            )
            fields.update(item.field for item in validated.baseline_filters)
            fields.update(item.field for item in validated.comparison_filters)
        elif isinstance(validated, DatasetDerivedMetricInput):
            fields.update((validated.numerator_field, validated.denominator_field))
        elif isinstance(validated, DatasetRankingInput):
            fields.update((validated.dimension, validated.measure))
        return fields

    @staticmethod
    def _topological_order(plan: DatasetPlan) -> tuple[str, ...] | None:
        index = {step.step_id: position for position, step in enumerate(plan.steps)}
        indegree = {step.step_id: len(step.depends_on) for step in plan.steps}
        dependents = {step.step_id: [] for step in plan.steps}
        for step in plan.steps:
            for dependency in step.depends_on:
                dependents[dependency].append(step.step_id)
        ready = sorted(
            (step_id for step_id, degree in indegree.items() if degree == 0),
            key=index.__getitem__,
        )
        result: list[str] = []
        while ready:
            current = ready.pop(0)
            result.append(current)
            for dependent in sorted(dependents[current], key=index.__getitem__):
                indegree[dependent] -= 1
                if indegree[dependent] == 0:
                    ready.append(dependent)
                    ready.sort(key=index.__getitem__)
        return tuple(result) if len(result) == len(plan.steps) else None


__all__ = ["DatasetPlanValidator", "canonical_dataset_request_sha256"]
