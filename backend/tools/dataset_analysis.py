"""Server-authorized Tool Registry composition for PV2-04c Dataset calculations."""

from __future__ import annotations

from contextlib import contextmanager
import os
from typing import Callable
from uuid import UUID

import psycopg

from backend.auth.models import AuthPrincipal
from backend.authorization import AuthorizationFailure, ProjectAuthorizationService
from backend.datasets.analysis import DatasetAnalysisLimit, DeterministicDatasetAnalyzer
from backend.datasets.analysis_models import (
    DatasetAggregateInput,
    DatasetAnalysisSettings,
    DatasetBindingContext,
    DatasetComparisonInput,
    DatasetDerivedMetricInput,
    DatasetProfileInput,
    DatasetQualityInput,
    DatasetRankingInput,
    DatasetTableFieldCatalog,
    DatasetToolResult,
)
from backend.datasets.persistence import PrivateDatasetPersistenceService
from backend.datasets.persistence_models import PersistenceFailure

from .executor import ToolExecutor
from .registry import ToolAuthorizationPolicy, ToolDefinition, ToolRegistry, ToolRegistryError


DATASET_ANALYSIS_PRINCIPAL = "dataset-analysis-runtime"


def load_dataset_analysis_settings() -> DatasetAnalysisSettings:
    raw = os.getenv("DATASET_ANALYSIS_TOOLS_ENABLED", "false").strip().lower()
    values = {"true": True, "false": False, "1": True, "0": False}
    if raw not in values:
        raise ValueError("Invalid DATASET_ANALYSIS_TOOLS_ENABLED value")
    return DatasetAnalysisSettings(enabled=values[raw])


class AuthorizedDatasetAnalysisRuntime:
    """Bind trusted identity and Project scope outside all model/tool arguments."""

    def __init__(
        self,
        connection_factory: Callable,
        authorization: ProjectAuthorizationService,
        persistence: PrivateDatasetPersistenceService,
        *,
        principal: AuthPrincipal,
        project_id: UUID,
        settings: DatasetAnalysisSettings,
    ):
        self._connections = connection_factory
        self._authorization = authorization
        self._persistence = persistence
        self._principal = principal
        self._project_id = project_id
        self._settings = settings

    @property
    def principal(self) -> AuthPrincipal:
        return self._principal

    @property
    def project_id(self) -> UUID:
        return self._project_id

    @contextmanager
    def _connection(self):
        try:
            with self._connections() as connection:
                if not connection.autocommit:
                    connection.autocommit = True
                yield connection
        except psycopg.Error:
            raise ToolRegistryError("tool_database_error") from None

    def _analyzer_for_binding(self, binding):
        if not self._settings.enabled:
            raise ToolRegistryError("tool_not_authorized")
        try:
            before = self._authorization.require_asset(
                self._principal, self._project_id, binding.asset_id, action="asset_read"
            )
            normalized_sha256 = self._ready_revision_sha(binding)
            dataset = self._persistence.load_dataset(
                project_id=self._project_id,
                dataset_revision_id=binding.dataset_revision_id,
                trusted_local=True,
            )
            if dataset.normalized_sha256 != normalized_sha256:
                raise ToolRegistryError("tool_execution_failed")
            return DeterministicDatasetAnalyzer(
                dataset,
                project_id=self._project_id,
                binding=binding,
                settings=self._settings,
            ), before.authorization_version
        except AuthorizationFailure:
            raise ToolRegistryError("tool_not_authorized") from None
        except PersistenceFailure:
            raise ToolRegistryError("tool_execution_failed") from None

    def _analyzer(self, request):
        return self._analyzer_for_binding(request.binding)

    def binding_context(self, binding) -> DatasetBindingContext:
        """Return only server-verified schema identity needed by a Planner."""

        analyzer, version = self._analyzer_for_binding(binding)
        try:
            after = self._authorization.require_asset(
                self._principal, self._project_id, binding.asset_id, action="asset_read"
            )
        except AuthorizationFailure:
            raise ToolRegistryError("tool_not_authorized") from None
        if after.authorization_version != version:
            raise ToolRegistryError("tool_not_authorized")
        if self._ready_revision_sha(binding) != analyzer.dataset.normalized_sha256:
            raise ToolRegistryError("tool_execution_failed")
        return DatasetBindingContext(
            project_id=self._project_id,
            binding=binding,
            normalized_sha256=analyzer.dataset.normalized_sha256,
            authorization_version=version,
            tables=tuple(
                DatasetTableFieldCatalog(
                    table_index=index,
                    fields=tuple(column.name for column in table.columns),
                )
                for index, table in enumerate(analyzer.dataset.tables, 1)
            ),
        )

    def planner_catalog(
        self,
        binding,
        *,
        metrics: tuple = (),
    ):
        """Expose schema metadata only; never rows, paths or storage references."""

        from backend.planning.dataset_planner_models import (
            DatasetPlannerCatalog,
            DatasetPlannerField,
            DatasetPlannerTable,
        )

        analyzer, version = self._analyzer_for_binding(binding)
        try:
            after = self._authorization.require_asset(
                self._principal, self._project_id, binding.asset_id, action="asset_read"
            )
        except AuthorizationFailure:
            raise ToolRegistryError("tool_not_authorized") from None
        if after.authorization_version != version:
            raise ToolRegistryError("tool_not_authorized")
        if self._ready_revision_sha(binding) != analyzer.dataset.normalized_sha256:
            raise ToolRegistryError("tool_execution_failed")
        return DatasetPlannerCatalog(
            tables=tuple(
                DatasetPlannerTable(
                    table_index=index,
                    fields=tuple(
                        DatasetPlannerField(
                            name=column.name,
                            kind=column.kind,
                            required=column.required,
                            unit=column.unit,
                        )
                        for column in table.columns
                    ),
                )
                for index, table in enumerate(analyzer.dataset.tables, 1)
            ),
            metrics=metrics,
        )

    def require_task_execution(self, execution_id: UUID):
        return self._authorization.require_task_execution(execution_id)

    def require_task_read(self, execution_id: UUID):
        return self._authorization.require_task(
            self._principal,
            self._project_id,
            execution_id,
            action="task_read",
        )

    def _ready_revision_sha(self, binding) -> str:
        with self._connection() as connection:
            row = connection.execute(
                """SELECT dataset.normalized_sha256
                   FROM dataset_store.dataset_revisions dataset
                   JOIN dataset_store.asset_revisions revision
                     ON (revision.project_id,revision.revision_id)=
                        (dataset.project_id,dataset.asset_revision_id)
                   JOIN dataset_store.assets asset
                     ON (asset.project_id,asset.asset_id)=
                        (revision.project_id,revision.asset_id)
                   JOIN dataset_store.projects project ON project.project_id=asset.project_id
                   WHERE dataset.project_id=%s AND dataset.dataset_revision_id=%s
                     AND revision.revision_id=%s AND revision.asset_id=%s
                     AND project.state='active' AND asset.state='active'
                     AND revision.state='ready' AND dataset.state='ready'""",
                (
                    self._project_id,
                    binding.dataset_revision_id,
                    binding.asset_revision_id,
                    binding.asset_id,
                ),
            ).fetchone()
        if row is None:
            raise ToolRegistryError("tool_not_authorized")
        return row[0]

    def _execute(self, request, method: str) -> DatasetToolResult:
        analyzer, version = self._analyzer(request)
        try:
            result = getattr(analyzer, method)(request)
        except DatasetAnalysisLimit:
            raise ToolRegistryError("tool_execution_failed") from None
        try:
            after = self._authorization.require_asset(
                self._principal,
                self._project_id,
                request.binding.asset_id,
                action="asset_read",
            )
        except AuthorizationFailure:
            raise ToolRegistryError("tool_not_authorized") from None
        if after.authorization_version != version:
            raise ToolRegistryError("tool_not_authorized")
        if self._ready_revision_sha(request.binding) != analyzer.dataset.normalized_sha256:
            raise ToolRegistryError("tool_execution_failed")
        return result

    def profile(self, request: DatasetProfileInput) -> DatasetToolResult:
        return self._execute(request, "profile")

    def aggregate(self, request: DatasetAggregateInput) -> DatasetToolResult:
        return self._execute(request, "aggregate")

    def compare(self, request: DatasetComparisonInput) -> DatasetToolResult:
        return self._execute(request, "compare")

    def derive(self, request: DatasetDerivedMetricInput) -> DatasetToolResult:
        return self._execute(request, "derive")

    def rank(self, request: DatasetRankingInput) -> DatasetToolResult:
        return self._execute(request, "rank")

    def quality(self, request: DatasetQualityInput) -> DatasetToolResult:
        return self._execute(request, "quality")


def build_dataset_analysis_registry(runtime: AuthorizedDatasetAnalysisRuntime) -> ToolRegistry:
    definitions = (
        ToolDefinition(
            name="profile_dataset_revision",
            version="1.0.0",
            description="Profile fields and null/distinct counts in one exact authorized Dataset revision.",
            input_model=DatasetProfileInput,
            output_model=DatasetToolResult,
            handler=runtime.profile,
        ),
        ToolDefinition(
            name="aggregate_dataset_revision",
            version="1.0.0",
            description="Apply allowlisted filters, grouping, exact aggregation and bounded sorting to one authorized Dataset revision.",
            input_model=DatasetAggregateInput,
            output_model=DatasetToolResult,
            handler=runtime.aggregate,
        ),
        ToolDefinition(
            name="compare_dataset_revision",
            version="1.0.0",
            description="Compare two explicit dimension values using one numeric measure and an exact derived change.",
            input_model=DatasetComparisonInput,
            output_model=DatasetToolResult,
            handler=runtime.compare,
        ),
        ToolDefinition(
            name="derive_dataset_metric",
            version="1.0.0",
            description="Derive a bounded difference, ratio or percentage from two compatible aggregated fields.",
            input_model=DatasetDerivedMetricInput,
            output_model=DatasetToolResult,
            handler=runtime.derive,
        ),
        ToolDefinition(
            name="rank_dataset_revision",
            version="1.0.0",
            description="Rank dimension groups by one allowlisted aggregate with a bounded result count.",
            input_model=DatasetRankingInput,
            output_model=DatasetToolResult,
            handler=runtime.rank,
        ),
        ToolDefinition(
            name="inspect_dataset_quality",
            version="1.0.0",
            description="Report deterministic null and parser-quality evidence for an authorized Dataset revision.",
            input_model=DatasetQualityInput,
            output_model=DatasetToolResult,
            handler=runtime.quality,
        ),
    )
    return ToolRegistry(
        definitions,
        ToolAuthorizationPolicy(
            grants={DATASET_ANALYSIS_PRINCIPAL: frozenset(item.name for item in definitions)}
        ),
    )


def build_dataset_analysis_executor(runtime: AuthorizedDatasetAnalysisRuntime) -> ToolExecutor:
    return ToolExecutor(build_dataset_analysis_registry(runtime))


__all__ = [
    "AuthorizedDatasetAnalysisRuntime",
    "DATASET_ANALYSIS_PRINCIPAL",
    "build_dataset_analysis_executor",
    "build_dataset_analysis_registry",
    "load_dataset_analysis_settings",
]
