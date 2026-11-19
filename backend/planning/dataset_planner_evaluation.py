"""Evidence-bound semantic review contracts for Dataset Planner evaluation v1."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from backend.tools.models import StrictModel


class DatasetPlannerQualityScores(StrictModel):
    task_completeness: int | None = Field(default=None, ge=0, le=2)
    relevance: int | None = Field(default=None, ge=0, le=2)
    clarity: int | None = Field(default=None, ge=0, le=2)

    def normalized(self) -> float:
        scores = [value for value in self.model_dump().values() if value is not None]
        return 1.0 if not scores else sum(scores) / (2 * len(scores))


class DatasetPlannerEvaluationRecord(StrictModel):
    evaluation_version: Literal["dataset-planner-eval-record-v1"] = (
        "dataset-planner-eval-record-v1"
    )
    case_id: str = Field(min_length=1, max_length=96)
    mode: Literal["offline_mock", "live_deepseek"]
    hard_invariants: dict[str, bool]
    core_task_complete: bool
    quality: DatasetPlannerQualityScores
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=12)


class DatasetPlannerEvaluationVerdict(StrictModel):
    verdict_version: Literal["dataset-planner-eval-verdict-v1"] = (
        "dataset-planner-eval-verdict-v1"
    )
    case_id: str
    hard_pass: bool
    core_pass: bool
    normalized_quality: float = Field(ge=0, le=1)
    verdict: Literal["PASS", "FAIL"]

    @model_validator(mode="after")
    def verdict_is_derived(self):
        expected = (
            "PASS"
            if self.hard_pass and self.core_pass and self.normalized_quality >= 0.85
            else "FAIL"
        )
        if self.verdict != expected:
            raise ValueError("Dataset Planner verdict is inconsistent")
        return self


def evaluate_dataset_planner_record(
    record: DatasetPlannerEvaluationRecord,
) -> DatasetPlannerEvaluationVerdict:
    hard_pass = bool(record.hard_invariants) and all(record.hard_invariants.values())
    quality = record.quality.normalized()
    verdict = "PASS" if hard_pass and record.core_task_complete and quality >= 0.85 else "FAIL"
    return DatasetPlannerEvaluationVerdict(
        case_id=record.case_id,
        hard_pass=hard_pass,
        core_pass=record.core_task_complete,
        normalized_quality=quality,
        verdict=verdict,
    )


__all__ = [
    "DatasetPlannerEvaluationRecord",
    "DatasetPlannerEvaluationVerdict",
    "DatasetPlannerQualityScores",
    "evaluate_dataset_planner_record",
]
