"""Strict, provider-independent contracts for deterministic Dataset analysis."""

from __future__ import annotations

import re
import unicodedata
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from backend.product_facade.models import DatasetRevisionBinding, ProductContract


SAFE_NAME = re.compile(r"^[^\x00-\x1f\x7f]{1,128}$")


def _safe_name(value: str) -> str:
    if value != unicodedata.normalize("NFC", value).strip() or not SAFE_NAME.fullmatch(value):
        raise ValueError("invalid field reference")
    return value


class DatasetAnalysisSettings(ProductContract):
    enabled: bool = False
    policy_version: Literal["deterministic-dataset-analysis-v1"] = (
        "deterministic-dataset-analysis-v1"
    )
    max_rows: int = Field(default=5000, ge=1, le=5000)
    max_groups: int = Field(default=200, ge=1, le=200)
    max_result_rows: int = Field(default=200, ge=1, le=200)
    max_filters: int = Field(default=12, ge=0, le=12)
    max_aggregations: int = Field(default=8, ge=1, le=8)
    wall_seconds: float = Field(default=2.0, gt=0, le=5.0)


class DatasetTableFieldCatalog(ProductContract):
    table_index: int = Field(ge=1, le=4)
    fields: tuple[str, ...] = Field(min_length=1, max_length=256)


class DatasetBindingContext(ProductContract):
    context_version: Literal["dataset-binding-context-v1"] = "dataset-binding-context-v1"
    project_id: UUID
    binding: DatasetRevisionBinding
    normalized_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    authorization_version: int = Field(gt=0)
    tables: tuple[DatasetTableFieldCatalog, ...] = Field(min_length=1, max_length=4)


class DatasetFilterV1(ProductContract):
    field: str = Field(min_length=1, max_length=128)
    operator: Literal[
        "eq", "not_eq", "gt", "gte", "lt", "lte", "in", "between", "is_null", "not_null"
    ]
    values: tuple[str, ...] = Field(default=(), max_length=20)

    @field_validator("field")
    @classmethod
    def valid_field(cls, value: str) -> str:
        return _safe_name(value)

    @field_validator("values", mode="before")
    @classmethod
    def json_values(cls, value):
        return tuple(value) if isinstance(value, list) else value

    @field_validator("values")
    @classmethod
    def bounded_values(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(
            not value
            or len(value) > 256
            or value != unicodedata.normalize("NFC", value)
            or any(ord(character) < 32 for character in value)
            for value in values
        ):
            raise ValueError("invalid filter value")
        return values

    @model_validator(mode="after")
    def operator_arity(self):
        if self.operator in {"is_null", "not_null"} and self.values:
            raise ValueError("null operator takes no values")
        if self.operator == "between" and len(self.values) != 2:
            raise ValueError("between requires two values")
        if self.operator == "in" and not self.values:
            raise ValueError("in requires values")
        if self.operator not in {"is_null", "not_null", "between", "in"} and len(self.values) != 1:
            raise ValueError("operator requires one value")
        return self


class AggregateSpecV1(ProductContract):
    result_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    function: Literal["count_rows", "count_non_null", "sum", "min", "max", "average"]
    field: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("field")
    @classmethod
    def valid_field(cls, value: str | None) -> str | None:
        return None if value is None else _safe_name(value)

    @model_validator(mode="after")
    def field_matches_function(self):
        if (self.function == "count_rows") != (self.field is None):
            raise ValueError("only count_rows omits field")
        return self


class ResultSortV1(ProductContract):
    key: str = Field(min_length=1, max_length=128)
    direction: Literal["ascending", "descending"] = "ascending"

    @field_validator("key")
    @classmethod
    def valid_key(cls, value: str) -> str:
        return _safe_name(value)


class DatasetToolInput(ProductContract):
    binding: DatasetRevisionBinding
    table_index: int = Field(default=1, ge=1, le=4)


class DatasetProfileInput(DatasetToolInput):
    fields: tuple[str, ...] = Field(default=(), max_length=32)

    @field_validator("fields", mode="before")
    @classmethod
    def json_fields(cls, value):
        return tuple(value) if isinstance(value, list) else value

    @field_validator("fields")
    @classmethod
    def valid_fields(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(values)) != len(values):
            raise ValueError("fields must be unique")
        return tuple(_safe_name(value) for value in values)


class DatasetAggregateInput(DatasetToolInput):
    filters: tuple[DatasetFilterV1, ...] = Field(default=(), max_length=12)
    group_by: tuple[str, ...] = Field(default=(), max_length=4)
    aggregations: tuple[AggregateSpecV1, ...] = Field(min_length=1, max_length=8)
    sort: tuple[ResultSortV1, ...] = Field(default=(), max_length=3)
    limit: int = Field(default=100, ge=1, le=200)

    @field_validator("filters", "group_by", "aggregations", "sort", mode="before")
    @classmethod
    def json_arrays(cls, value):
        return tuple(value) if isinstance(value, list) else value

    @field_validator("group_by")
    @classmethod
    def valid_group_fields(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(values)) != len(values):
            raise ValueError("group fields must be unique")
        return tuple(_safe_name(value) for value in values)

    @model_validator(mode="after")
    def unique_results(self):
        if len({item.result_id for item in self.aggregations}) != len(self.aggregations):
            raise ValueError("aggregate result ids must be unique")
        return self


class DatasetComparisonInput(DatasetToolInput):
    filters: tuple[DatasetFilterV1, ...] = Field(default=(), max_length=12)
    dimension: str = Field(min_length=1, max_length=128)
    measure: str = Field(min_length=1, max_length=128)
    aggregation: Literal["sum", "average", "count_non_null"] = "sum"
    baseline: str = Field(min_length=1, max_length=256)
    comparison: str = Field(min_length=1, max_length=256)
    derived_metric: Literal["absolute_change", "percentage_change"]

    @field_validator("filters", mode="before")
    @classmethod
    def json_filters(cls, value):
        return tuple(value) if isinstance(value, list) else value

    @field_validator("dimension", "measure")
    @classmethod
    def valid_fields(cls, value: str) -> str:
        return _safe_name(value)


class DatasetDerivedMetricInput(DatasetToolInput):
    filters: tuple[DatasetFilterV1, ...] = Field(default=(), max_length=12)
    numerator_field: str = Field(min_length=1, max_length=128)
    denominator_field: str = Field(min_length=1, max_length=128)
    aggregation: Literal["sum", "average"] = "sum"
    operation: Literal["difference", "ratio", "percentage"]
    result_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")

    @field_validator("filters", mode="before")
    @classmethod
    def json_filters(cls, value):
        return tuple(value) if isinstance(value, list) else value

    @field_validator("numerator_field", "denominator_field")
    @classmethod
    def valid_fields(cls, value: str) -> str:
        return _safe_name(value)


class DatasetRankingInput(DatasetToolInput):
    filters: tuple[DatasetFilterV1, ...] = Field(default=(), max_length=12)
    dimension: str = Field(min_length=1, max_length=128)
    measure: str = Field(min_length=1, max_length=128)
    aggregation: Literal["sum", "average", "count_non_null"] = "sum"
    direction: Literal["highest", "lowest"] = "highest"
    limit: int = Field(default=10, ge=1, le=100)

    @field_validator("filters", mode="before")
    @classmethod
    def json_filters(cls, value):
        return tuple(value) if isinstance(value, list) else value

    @field_validator("dimension", "measure")
    @classmethod
    def valid_fields(cls, value: str) -> str:
        return _safe_name(value)


class DatasetQualityInput(DatasetToolInput):
    fields: tuple[str, ...] = Field(default=(), max_length=32)

    @field_validator("fields", mode="before")
    @classmethod
    def json_fields(cls, value):
        return tuple(value) if isinstance(value, list) else value

    @field_validator("fields")
    @classmethod
    def valid_fields(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(values)) != len(values):
            raise ValueError("fields must be unique")
        return tuple(_safe_name(value) for value in values)


class DatasetResultValue(ProductContract):
    value: str | None = Field(default=None, max_length=4096)
    kind: Literal["text", "decimal", "integer", "date", "boolean", "null"]
    unit: str | None = Field(default=None, max_length=32)

    @model_validator(mode="after")
    def null_is_explicit(self):
        if (self.value is None) != (self.kind == "null"):
            raise ValueError("null result is inconsistent")
        return self


class DatasetResultCell(ProductContract):
    key: str = Field(min_length=1, max_length=128)
    result: DatasetResultValue


class DatasetResultRow(ProductContract):
    dimensions: tuple[DatasetResultCell, ...] = ()
    measures: tuple[DatasetResultCell, ...] = ()

    @model_validator(mode="after")
    def unique_keys(self):
        keys = [cell.key for cell in self.dimensions + self.measures]
        if not keys or len(keys) != len(set(keys)):
            raise ValueError("result row keys must be nonempty and unique")
        return self


class DatasetAnalysisFinding(ProductContract):
    code: Literal[
        "field_not_found",
        "invalid_field_type",
        "filter_value_invalid",
        "operator_not_supported",
        "unit_mismatch",
        "zero_denominator",
        "no_matching_rows",
        "group_limit_exceeded",
        "result_limit_applied",
        "dataset_quality_issue",
    ]
    severity: Literal["clarification", "limitation", "info"]
    fields: tuple[str, ...] = Field(default=(), max_length=8)


class DatasetSourceSpan(ProductContract):
    source_version: Literal["dataset-analysis-source-v1"] = "dataset-analysis-source-v1"
    project_id: UUID
    asset_id: UUID
    asset_revision_id: UUID
    dataset_revision_id: UUID
    normalized_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    table_index: int = Field(ge=1, le=4)
    sheet: str | None = Field(default=None, max_length=128)
    row_start: int = Field(ge=2)
    row_end: int = Field(ge=2)
    columns: tuple[str, ...] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def valid_range(self):
        if self.row_end < self.row_start:
            raise ValueError("invalid source range")
        return self


class DatasetToolResult(ProductContract):
    result_version: Literal["dataset-analysis-tool-result-v1"] = (
        "dataset-analysis-tool-result-v1"
    )
    policy_version: Literal["deterministic-dataset-analysis-v1"] = (
        "deterministic-dataset-analysis-v1"
    )
    operation: Literal["profile", "aggregate", "compare", "derive", "rank", "quality"]
    status: Literal["completed", "needs_clarification", "blocked"]
    project_id: UUID
    asset_id: UUID
    asset_revision_id: UUID
    dataset_revision_id: UUID
    parameters_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    scanned_rows: int = Field(ge=0, le=5000)
    matched_rows: int = Field(ge=0, le=5000)
    rows: tuple[DatasetResultRow, ...] = Field(default=(), max_length=200)
    findings: tuple[DatasetAnalysisFinding, ...] = Field(default=(), max_length=64)
    sources: tuple[DatasetSourceSpan, ...] = Field(default=(), max_length=32)

    @model_validator(mode="after")
    def status_is_coherent(self):
        if self.status == "needs_clarification" and not any(
            item.severity == "clarification" for item in self.findings
        ):
            raise ValueError("clarification status requires a clarification finding")
        if self.status == "completed" and not self.sources:
            raise ValueError("completed result requires sources")
        return self


__all__ = [name for name in globals() if name.startswith("Dataset") or name in {"AggregateSpecV1", "ResultSortV1"}]
