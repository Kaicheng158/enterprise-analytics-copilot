"""Frozen-policy helpers for range-aware Dataset Planner evaluation v3."""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import Field

from backend.tools.models import StrictModel

from .dataset_planner_evaluation import (
    DatasetPlannerEvaluationRecord,
    DatasetPlannerEvaluationVerdict,
    DatasetPlannerQualityScores,
    evaluate_dataset_planner_record,
)


class DatasetPlannerV3ScopeExpectation(StrictModel):
    field: str = Field(min_length=1, max_length=128)
    baseline_start: date
    baseline_end: date
    comparison_start: date
    comparison_end: date
    group_by: tuple[str, ...] = Field(default=(), max_length=4)
    baseline_measure: str = Field(min_length=1, max_length=128)
    comparison_measure: str = Field(min_length=1, max_length=128)


class DatasetPlannerV3Assessment(StrictModel):
    evaluation_version: str = "dataset-planner-eval-v3"
    case_id: str
    schema_valid: bool
    authority_preserved: bool
    allowed_tool_only: bool
    scope_semantically_complete: bool
    runtime_completed: bool
    result_correct: bool
    quality: DatasetPlannerQualityScores
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=12)

    def record(self) -> DatasetPlannerEvaluationRecord:
        return DatasetPlannerEvaluationRecord(
            case_id=self.case_id,
            mode="offline_mock",
            hard_invariants={
                "schema": self.schema_valid,
                "authority": self.authority_preserved,
                "allowlist": self.allowed_tool_only,
                "semantic_scope": self.scope_semantically_complete,
            },
            core_task_complete=self.runtime_completed and self.result_correct,
            quality=self.quality,
            evidence_refs=self.evidence_refs,
        )

    def verdict(self) -> DatasetPlannerEvaluationVerdict:
        return evaluate_dataset_planner_record(self.record())


def _iso(value: Any) -> date | None:
    if not isinstance(value, str) or len(value) != 10:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def filters_cover_closed_interval(
    filters: list[dict] | tuple[dict, ...],
    *,
    field: str,
    start: date,
    end: date,
) -> bool:
    """Accept equivalent explicit inclusive ranges, not fixture-specific literals."""

    lower = upper = None
    for item in filters:
        if item.get("field") != field:
            continue
        values = item.get("values", [])
        if item.get("operator") == "between" and len(values) == 2:
            return _iso(values[0]) == start and _iso(values[1]) == end
        if item.get("operator") == "gte" and len(values) == 1:
            lower = _iso(values[0])
        elif item.get("operator") == "lte" and len(values) == 1:
            upper = _iso(values[0])
    return lower == start and upper == end


def operation_matches_compare_v2_scope(
    operation: dict, expectation: DatasetPlannerV3ScopeExpectation
) -> bool:
    arguments = operation.get("arguments", {})
    return (
        operation.get("tool_name") == "compare_dataset_revision_v2"
        and tuple(arguments.get("group_by", ())) == expectation.group_by
        and arguments.get("baseline_measure") == expectation.baseline_measure
        and arguments.get("comparison_measure") == expectation.comparison_measure
        and filters_cover_closed_interval(
            arguments.get("baseline_filters", []),
            field=expectation.field,
            start=expectation.baseline_start,
            end=expectation.baseline_end,
        )
        and filters_cover_closed_interval(
            arguments.get("comparison_filters", []),
            field=expectation.field,
            start=expectation.comparison_start,
            end=expectation.comparison_end,
        )
    )


__all__ = [
    "DatasetPlannerV3Assessment",
    "DatasetPlannerV3ScopeExpectation",
    "filters_cover_closed_interval",
    "operation_matches_compare_v2_scope",
]
