"""Strict, versioned product contracts for PV2-04a.

These contracts describe the document-first product boundary.  They do not
extend the legacy synthetic-sales Agent request and they do not authorize or
execute tools, SQL, models, uploads, charts, or reports.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
import re
import unicodedata
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from backend.datasets.models import Column


HEX_SHA256 = r"^[0-9a-f]{64}$"
SAFE_FIELD = re.compile(r"^[^\x00-\x1f\x7f]{1,128}$")


class ProductContract(BaseModel):
    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
    )


class ProductFacadeSettings(ProductContract):
    enabled: bool = False
    policy_version: Literal["local-product-facade-v1"] = "local-product-facade-v1"
    allowed_origin: str = "http://127.0.0.1:8775"
    default_project_quota_bytes: int = Field(default=10 * 1024 * 1024, ge=1, le=100 * 1024 * 1024)
    lifecycle_policy_version: Literal["local-private-v1"] = "local-private-v1"


class ProductDatasetSettings(ProductContract):
    enabled: bool = False
    policy_version: Literal["local-product-dataset-v1"] = "local-product-dataset-v1"
    idempotency_secret: SecretStr = SecretStr("")
    storage_root: str = ""
    max_file_bytes: int = Field(default=2 * 1024 * 1024, ge=1, le=2 * 1024 * 1024)
    max_schema_header_bytes: int = Field(default=8192, ge=128, le=8192)
    max_preview_rows: Literal[5] = 5
    max_preview_columns: Literal[8] = 8


class ProductCapabilities(ProductContract):
    project_access: Literal[True] = True
    dataset_upload: Literal[False] = False
    analysis_execution: Literal[False] = False
    chart_generation: Literal[False] = False
    report_generation: Literal[False] = False


class ProductProjectView(ProductContract):
    contract_version: Literal["product-project-v1"] = "product-project-v1"
    project_id: UUID
    state: Literal["active"] = "active"
    role: Literal["owner"] = "owner"
    authorization_version: int = Field(gt=0)
    quota_bytes: int = Field(gt=0)
    used_bytes: int = Field(ge=0)
    reserved_bytes: int = Field(ge=0)
    lifecycle_policy_version: str = Field(min_length=1, max_length=80)
    capabilities: ProductCapabilities = ProductCapabilities()

    @model_validator(mode="after")
    def quota_is_coherent(self):
        if self.used_bytes + self.reserved_bytes > self.quota_bytes:
            raise ValueError("project quota is incoherent")
        return self


class ProductWorkspaceView(ProductContract):
    response_version: Literal["product-workspace-v1"] = "product-workspace-v1"
    destination: Literal["my-workspace"] = "my-workspace"
    projects: tuple[ProductProjectView, ...]
    capabilities: ProductCapabilities = ProductCapabilities()


class CreatePrivateProjectRequest(ProductContract):
    contract_version: Literal["create-private-project-v1"] = "create-private-project-v1"
    project_kind: Literal["private-analysis"] = "private-analysis"


def _display_name(value: str, *, max_length: int) -> str:
    normalized = " ".join(unicodedata.normalize("NFKC", value).strip().split())
    if (
        normalized != value
        or not normalized
        or len(normalized) > max_length
        or any(ord(character) < 32 or ord(character) == 127 for character in normalized)
    ):
        raise ValueError("invalid display name")
    return value


class CreatePrivateProjectRequestV2(ProductContract):
    contract_version: Literal["create-private-project-v2"] = "create-private-project-v2"
    project_kind: Literal["private-analysis"] = "private-analysis"
    name: str = Field(min_length=1, max_length=120)

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        return _display_name(value, max_length=120)


class ProductCapabilitiesV2(ProductContract):
    project_access: Literal[True] = True
    dataset_upload: bool
    dataset_preview: bool
    analysis_execution: Literal[False] = False
    chart_generation: Literal[False] = False
    report_generation: Literal[False] = False


class ProductProjectViewV2(ProductContract):
    contract_version: Literal["product-project-v2"] = "product-project-v2"
    project_id: UUID
    name: str = Field(min_length=1, max_length=120)
    state: Literal["active"] = "active"
    role: Literal["owner"] = "owner"
    authorization_version: int = Field(gt=0)
    quota_bytes: int = Field(gt=0)
    used_bytes: int = Field(ge=0)
    reserved_bytes: int = Field(ge=0)
    lifecycle_policy_version: str = Field(min_length=1, max_length=80)
    capabilities: ProductCapabilitiesV2

    @model_validator(mode="after")
    def quota_is_coherent(self):
        if self.used_bytes + self.reserved_bytes > self.quota_bytes:
            raise ValueError("project quota is incoherent")
        return self


class ProductWorkspaceViewV2(ProductContract):
    response_version: Literal["product-workspace-v2"] = "product-workspace-v2"
    destination: Literal["my-workspace"] = "my-workspace"
    projects: tuple[ProductProjectViewV2, ...]
    capabilities: ProductCapabilitiesV2


class DatasetUploadMetadata(ProductContract):
    contract_version: Literal["dataset-upload-v1"] = "dataset-upload-v1"
    dataset_name: str = Field(min_length=1, max_length=120)
    filename: str = Field(min_length=1, max_length=128)
    columns: tuple[Column, ...] = Field(min_length=1, max_length=64)

    @field_validator("columns", mode="before")
    @classmethod
    def json_columns(cls, value):
        return tuple(value) if isinstance(value, list) else value

    @field_validator("dataset_name")
    @classmethod
    def valid_dataset_name(cls, value: str) -> str:
        return _display_name(value, max_length=120)

    @field_validator("filename")
    @classmethod
    def safe_filename(cls, value: str) -> str:
        if (
            value != unicodedata.normalize("NFC", value).strip()
            or "/" in value
            or "\\" in value
            or ":" in value
            or value.startswith(".")
            or not value.endswith((".csv", ".xlsx"))
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
        ):
            raise ValueError("invalid filename")
        return value


class DatasetIssueLocationView(ProductContract):
    table_index: int = Field(ge=1)
    sheet: str | None = Field(default=None, max_length=128)
    row: int = Field(ge=1)
    column: int = Field(ge=1, le=64)
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)


class DatasetQualityIssueView(ProductContract):
    code: str = Field(pattern=r"^[a-z][a-z0-9_]{1,79}$")
    category: Literal["blocking_error", "confirmation_required", "warning"]
    location: DatasetIssueLocationView


class DatasetQualitySummary(ProductContract):
    contract_version: Literal["dataset-quality-v1"] = "dataset-quality-v1"
    blocking_errors: tuple[DatasetQualityIssueView, ...] = ()
    confirmations: tuple[DatasetQualityIssueView, ...] = ()
    warnings: tuple[DatasetQualityIssueView, ...] = ()


class DatasetRevisionView(ProductContract):
    response_version: Literal["dataset-revision-v1"] = "dataset-revision-v1"
    project_id: UUID
    asset_id: UUID
    asset_revision_id: UUID
    dataset_revision_id: UUID
    dataset_name: str = Field(min_length=1, max_length=120)
    original_filename: str = Field(min_length=1, max_length=128)
    revision_number: int = Field(gt=0)
    source_format: Literal["csv", "xlsx"]
    source_bytes: int = Field(ge=0)
    source_sha256: str = Field(pattern=HEX_SHA256)
    normalized_sha256: str = Field(pattern=HEX_SHA256)
    dataset_contract_version: str = Field(min_length=1, max_length=80)
    parser_policy_version: str = Field(min_length=1, max_length=80)
    state: Literal["ready"] = "ready"
    reused: bool = False
    quality: DatasetQualitySummary


class DatasetRevisionSummary(ProductContract):
    project_id: UUID
    asset_id: UUID
    asset_revision_id: UUID
    dataset_revision_id: UUID
    dataset_name: str = Field(min_length=1, max_length=120)
    original_filename: str = Field(min_length=1, max_length=128)
    revision_number: int = Field(gt=0)
    source_format: Literal["csv", "xlsx"]
    source_bytes: int = Field(ge=0)
    state: Literal["ready"] = "ready"


class DatasetListResponse(ProductContract):
    response_version: Literal["dataset-list-v1"] = "dataset-list-v1"
    project_id: UUID
    revisions: tuple[DatasetRevisionSummary, ...]


class DatasetPreviewCell(ProductContract):
    value: Literal["[redacted]"] | None
    kind: Literal["text", "decimal", "money", "date", "boolean", "null", "blocked"]
    row: int = Field(ge=2)
    column: int = Field(ge=1, le=64)


class DatasetPreviewColumn(ProductContract):
    name: str = Field(min_length=1, max_length=128)
    kind: Literal["text", "decimal", "money", "date", "boolean"]
    required: bool
    unit: str | None = Field(default=None, max_length=32)


class DatasetPreviewTable(ProductContract):
    table_index: int = Field(ge=1)
    sheet: str | None = Field(default=None, max_length=128)
    columns: tuple[DatasetPreviewColumn, ...] = Field(max_length=8)
    rows: tuple[tuple[DatasetPreviewCell, ...], ...] = Field(max_length=5)
    total_rows: int = Field(ge=0)
    total_columns: int = Field(ge=1)


class DatasetPreviewResponse(ProductContract):
    response_version: Literal["dataset-preview-v1"] = "dataset-preview-v1"
    project_id: UUID
    asset_id: UUID
    asset_revision_id: UUID
    dataset_revision_id: UUID
    values_redacted: Literal[True] = True
    tables: tuple[DatasetPreviewTable, ...] = Field(max_length=4)
    quality: DatasetQualitySummary


class DatasetRevisionBinding(ProductContract):
    asset_id: UUID
    asset_revision_id: UUID
    dataset_revision_id: UUID

    @field_validator("asset_id", "asset_revision_id", "dataset_revision_id", mode="before")
    @classmethod
    def json_uuid(cls, value):
        if isinstance(value, str):
            try:
                return UUID(value)
            except ValueError:
                return value
        return value


class AnalysisFilter(ProductContract):
    field: str = Field(min_length=1, max_length=128)
    operator: Literal["eq", "not_eq", "gt", "gte", "lt", "lte", "in", "between"]
    values: tuple[str, ...] = Field(min_length=1, max_length=20)

    @field_validator("values", mode="before")
    @classmethod
    def json_array_values(cls, value):
        return tuple(value) if isinstance(value, list) else value

    @field_validator("field")
    @classmethod
    def safe_field(cls, value: str) -> str:
        normalized = unicodedata.normalize("NFC", value).strip()
        if normalized != value or not SAFE_FIELD.fullmatch(value):
            raise ValueError("invalid field reference")
        return value

    @field_validator("values")
    @classmethod
    def bounded_values(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value or len(value) > 256 or any(ord(character) < 32 for character in value) for value in values):
            raise ValueError("invalid filter value")
        return values

    @model_validator(mode="after")
    def arity_matches_operator(self):
        if self.operator == "between" and len(self.values) != 2:
            raise ValueError("between requires exactly two values")
        if self.operator != "in" and self.operator != "between" and len(self.values) != 1:
            raise ValueError("operator requires exactly one value")
        return self


class AnalysisIntent(ProductContract):
    operation: Literal["profile", "aggregate", "compare", "trend"]
    measures: tuple[str, ...] = Field(default=(), max_length=8)
    dimensions: tuple[str, ...] = Field(default=(), max_length=8)
    time_field: str | None = Field(default=None, max_length=128)
    filters: tuple[AnalysisFilter, ...] = Field(default=(), max_length=12)

    @field_validator("measures", "dimensions", "filters", mode="before")
    @classmethod
    def json_array_fields(cls, value):
        return tuple(value) if isinstance(value, list) else value

    @field_validator("measures", "dimensions")
    @classmethod
    def safe_fields(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(values)) != len(values):
            raise ValueError("field references must be unique")
        for value in values:
            if unicodedata.normalize("NFC", value).strip() != value or not SAFE_FIELD.fullmatch(value):
                raise ValueError("invalid field reference")
        return values

    @field_validator("time_field")
    @classmethod
    def safe_time_field(cls, value: str | None) -> str | None:
        if value is not None and (unicodedata.normalize("NFC", value).strip() != value or not SAFE_FIELD.fullmatch(value)):
            raise ValueError("invalid time field")
        return value


class AnalysisDeliverables(ProductContract):
    answer_depth: Literal["auto", "short", "full_report"] = "auto"
    chart: bool = False
    report: bool = False

    @model_validator(mode="after")
    def full_report_is_explicit(self):
        if self.answer_depth == "full_report" and not self.report:
            raise ValueError("full_report depth requires report=true")
        return self


class DatasetAnalysisTaskRequestV1(ProductContract):
    contract_version: Literal["dataset-analysis-task-v1"] = "dataset-analysis-task-v1"
    question: str = Field(min_length=1, max_length=2000)
    datasets: tuple[DatasetRevisionBinding, ...] = Field(min_length=1, max_length=4)
    intent: AnalysisIntent
    deliverables: AnalysisDeliverables = AnalysisDeliverables()

    @field_validator("datasets", mode="before")
    @classmethod
    def json_array_datasets(cls, value):
        return tuple(value) if isinstance(value, list) else value

    @field_validator("question")
    @classmethod
    def normalize_question(cls, value: str) -> str:
        normalized = unicodedata.normalize("NFC", value).strip()
        if normalized != value or any(ord(character) < 32 and character not in "\n\t" for character in value):
            raise ValueError("invalid question")
        return value

    @field_validator("datasets")
    @classmethod
    def unique_revisions(cls, values: tuple[DatasetRevisionBinding, ...]):
        if len({value.dataset_revision_id for value in values}) != len(values):
            raise ValueError("dataset revisions must be unique")
        return values


class AuthorizedDatasetRevision(ProductContract):
    project_id: UUID
    asset_id: UUID
    asset_revision_id: UUID
    dataset_revision_id: UUID
    source_format: Literal["csv", "xlsx"]
    source_sha256: str = Field(pattern=HEX_SHA256)
    normalized_sha256: str = Field(pattern=HEX_SHA256)
    dataset_contract_version: str = Field(min_length=1, max_length=80)
    parser_policy_version: str = Field(min_length=1, max_length=80)


class AnalysisContextV1(ProductContract):
    context_version: Literal["dataset-analysis-context-v1"] = "dataset-analysis-context-v1"
    project_id: UUID
    authorization_version: int = Field(gt=0)
    datasets: tuple[AuthorizedDatasetRevision, ...] = Field(min_length=1, max_length=4)
    allowed_operations: tuple[Literal["profile", "aggregate", "compare", "trend"], ...]
    execution_created: Literal[False] = False


class ClarificationQuestion(ProductContract):
    code: Literal["measure_required", "comparison_basis_required", "time_field_required"]
    field: Literal["intent.measures", "intent.dimensions", "intent.time_field"]
    prompt_key: Literal[
        "analysis.clarify.measure",
        "analysis.clarify.comparison_basis",
        "analysis.clarify.time_field",
    ]
    required: Literal[True] = True


class ClarificationBundle(ProductContract):
    contract_version: Literal["analysis-clarification-v1"] = "analysis-clarification-v1"
    mode: Literal["centralized"] = "centralized"
    questions: tuple[ClarificationQuestion, ...] = Field(min_length=1, max_length=4)


class AnalysisContractValidationResponse(ProductContract):
    response_version: Literal["dataset-analysis-validation-v1"] = "dataset-analysis-validation-v1"
    status: Literal["contract_validated", "clarification_required"]
    execution_created: Literal[False] = False
    request: DatasetAnalysisTaskRequestV1
    context: AnalysisContextV1
    clarification: ClarificationBundle | None = None

    @model_validator(mode="after")
    def clarification_matches_status(self):
        if (self.status == "clarification_required") != (self.clarification is not None):
            raise ValueError("clarification must match validation status")
        return self


class AnalysisSourceReference(ProductContract):
    source_version: Literal["dataset-source-v1"] = "dataset-source-v1"
    project_id: UUID
    dataset_revision_id: UUID
    asset_revision_id: UUID
    table_index: int = Field(ge=1)
    sheet: str | None = Field(default=None, max_length=128)
    row_start: int | None = Field(default=None, ge=1)
    row_end: int | None = Field(default=None, ge=1)
    columns: tuple[str, ...] = Field(default=(), max_length=64)
    normalized_sha256: str = Field(pattern=HEX_SHA256)

    @model_validator(mode="after")
    def row_range_is_valid(self):
        if self.row_end is not None and self.row_start is None:
            raise ValueError("row_end requires row_start")
        if self.row_start is not None and self.row_end is not None and self.row_end < self.row_start:
            raise ValueError("invalid row range")
        return self


class CalculationValue(ProductContract):
    value: str = Field(pattern=r"^-?(0|[1-9][0-9]*)(\.[0-9]+)?$")
    unit: str = Field(min_length=1, max_length=32)

    @field_validator("value")
    @classmethod
    def finite_decimal(cls, value: str) -> str:
        try:
            parsed = Decimal(value)
        except InvalidOperation as error:
            raise ValueError("invalid decimal") from error
        if not parsed.is_finite():
            raise ValueError("decimal must be finite")
        return value


class CalculationResult(ProductContract):
    result_version: Literal["deterministic-calculation-v1"] = "deterministic-calculation-v1"
    calculation_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    operation: Literal["profile", "aggregate", "compare", "trend"]
    label: str = Field(min_length=1, max_length=160)
    value: CalculationValue
    sources: tuple[AnalysisSourceReference, ...] = Field(min_length=1, max_length=32)


class ChartSeries(ProductContract):
    label: str = Field(min_length=1, max_length=120)
    values: tuple[str, ...] = Field(min_length=1, max_length=500)
    calculation_ids: tuple[str, ...] = Field(min_length=1, max_length=32)

    @field_validator("values")
    @classmethod
    def numeric_values(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            try:
                parsed = Decimal(value)
            except InvalidOperation as error:
                raise ValueError("chart values must be decimals") from error
            if not parsed.is_finite():
                raise ValueError("chart values must be finite")
        return values


class ChartSpecV1(ProductContract):
    chart_version: Literal["verified-chart-v1"] = "verified-chart-v1"
    chart_type: Literal["bar", "line", "table"]
    title: str = Field(min_length=1, max_length=160)
    category_labels: tuple[str, ...] = Field(min_length=1, max_length=500)
    series: tuple[ChartSeries, ...] = Field(min_length=1, max_length=12)
    sources: tuple[AnalysisSourceReference, ...] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def series_matches_categories(self):
        if any(len(series.values) != len(self.category_labels) for series in self.series):
            raise ValueError("chart series length mismatch")
        return self


class ReportSection(ProductContract):
    heading: str = Field(min_length=1, max_length=160)
    body: str = Field(min_length=1, max_length=4000)
    source_indexes: tuple[int, ...] = Field(default=(), max_length=32)


class AnalysisReportV1(ProductContract):
    report_version: Literal["dataset-analysis-report-v1"] = "dataset-analysis-report-v1"
    title: str = Field(min_length=1, max_length=160)
    summary: str = Field(min_length=1, max_length=2000)
    sections: tuple[ReportSection, ...] = Field(min_length=1, max_length=20)
    sources: tuple[AnalysisSourceReference, ...] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def section_source_indexes_exist(self):
        if any(index < 0 or index >= len(self.sources) for section in self.sections for index in section.source_indexes):
            raise ValueError("report source index is invalid")
        return self


class AnalysisTaskError(ProductContract):
    code: Literal[
        "analysis_failed",
        "analysis_revoked",
        "analysis_reconciliation_required",
    ]
    message: str = Field(min_length=1, max_length=160)


class DatasetAnalysisTaskStatusV1(ProductContract):
    response_version: Literal["dataset-analysis-task-status-v1"] = "dataset-analysis-task-status-v1"
    task_id: UUID
    project_id: UUID
    status: Literal[
        "awaiting_clarification",
        "queued",
        "running",
        "needs_reconciliation",
        "completed",
        "failed",
        "revoked",
    ]
    state_version: int = Field(ge=0)
    updated_at: datetime
    calculations: tuple[CalculationResult, ...] = ()
    charts: tuple[ChartSpecV1, ...] = ()
    report: AnalysisReportV1 | None = None
    error: AnalysisTaskError | None = None

    @model_validator(mode="after")
    def terminal_fields_match_status(self):
        if self.updated_at.tzinfo is None:
            raise ValueError("updated_at must be timezone-aware")
        if self.status == "completed":
            if not self.calculations or self.error is not None:
                raise ValueError("completed task requires calculations and no error")
        elif self.calculations or self.charts or self.report is not None:
            raise ValueError("only completed tasks may expose results")
        if self.status in {"failed", "needs_reconciliation", "revoked"}:
            if self.error is None:
                raise ValueError("stopped task requires a safe error")
        elif self.error is not None:
            raise ValueError("active or completed task cannot expose an error")
        return self


class ProductFacadeFailure(Exception):
    """Content-free failure code safe for the local HTTP boundary."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)
