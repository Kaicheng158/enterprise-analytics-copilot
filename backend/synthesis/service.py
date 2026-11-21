"""Trusted synthesis over completed, authorized and receipt-backed Agent results."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
import re
from typing import Callable

from backend.datasets.analysis_models import DatasetSourceSpan, DatasetToolResult
from backend.planning.checkpoint_store import canonical_json_sha256
from backend.product_facade.models import (
    AnalysisReportV1,
    AnalysisSourceReference,
    CalculationValue,
    ChartSeries,
    ChartSpecV1,
    ReportSection,
)

from .models import (
    CalculationDimensionV2,
    ReceiptReferenceV1,
    ResultSynthesizer,
    SynthesisFailure,
    SynthesisTraceV1,
    TrustedAnswerV1,
    TrustedCalculationV2,
    TrustedSynthesisRequestV1,
    TrustedSynthesisResponseV1,
    TrustedSynthesisSettings,
)
from .validator import TrustedResultValidator


SAFE_ID = re.compile(r"[^a-z0-9_]+")
NUMERIC_KINDS = frozenset({"decimal", "integer"})


def _source(source: DatasetSourceSpan) -> AnalysisSourceReference:
    return AnalysisSourceReference(
        project_id=source.project_id,
        dataset_revision_id=source.dataset_revision_id,
        asset_revision_id=source.asset_revision_id,
        table_index=source.table_index,
        sheet=source.sheet,
        row_start=source.row_start,
        row_end=source.row_end,
        columns=source.columns,
        normalized_sha256=source.normalized_sha256,
    )


def _unique_sources(items):
    result = []
    seen = set()
    for item in items:
        key = canonical_json_sha256(item.model_dump(mode="json"))
        if key not in seen:
            seen.add(key)
            result.append(item)
    return tuple(result)


def _safe_calculation_id(operation_index: int, row_index: int, measure_key: str) -> str:
    key = SAFE_ID.sub("_", measure_key.lower()).strip("_") or "value"
    return f"c{operation_index}_{row_index}_{key}"[:64]


class TrustedResultSynthesisService:
    """Create presentation artifacts without recalculating verified tool output."""

    def __init__(
        self,
        agent_runtime,
        checkpoint_store,
        analysis_runtime,
        synthesizer: ResultSynthesizer,
        *,
        settings: TrustedSynthesisSettings | None = None,
        validator: TrustedResultValidator | None = None,
        before_publish: Callable[[], None] | None = None,
    ):
        self.settings = settings or TrustedSynthesisSettings()
        if not self.settings.enabled:
            raise ValueError("trusted_result_synthesis_disabled")
        self._agent = agent_runtime
        self._store = checkpoint_store
        self._analysis = analysis_runtime
        self._synthesizer = synthesizer
        self._validator = validator or TrustedResultValidator()
        self._before_publish = before_publish

    def synthesize(self, request: TrustedSynthesisRequestV1) -> TrustedSynthesisResponseV1:
        task = self._task(request.task_id)
        result = task.result
        before = self._binding_context(result.binding)
        execution = self._store.get_execution(request.task_id)
        if execution.status != "completed":
            raise SynthesisFailure("synthesis_task_not_completed")
        receipts = self._verified_receipts(request.task_id, result, execution)
        calculations = self._calculations(result.results)
        draft = self._synthesizer.synthesize(request, calculations)
        charts = self._charts(calculations)
        mode = self._mode(request, calculations, charts)
        if request.output_mode == "chart" and not charts:
            raise SynthesisFailure("synthesis_chart_unavailable")
        answer = TrustedAnswerV1(
            summary=draft.summary,
            claims=draft.claims,
            limitations=draft.limitations,
        )
        trace_sources = _unique_sources(
            source for calculation in calculations for source in calculation.sources
        )
        trace = SynthesisTraceV1(
            task_id=request.task_id,
            project_id=result.project_id,
            dataset_revision_id=result.binding.dataset_revision_id,
            normalized_sha256=result.normalized_sha256,
            plan_id=result.plan_id,
            plan_sha256=execution.plan_sha256,
            state_version=execution.state_version,
            receipts=receipts,
            sources=trace_sources,
        )
        report = (
            self._report(answer, trace_sources, calculations)
            if mode == "full_report"
            else None
        )
        response = TrustedSynthesisResponseV1(
            mode=mode,
            answer=answer,
            calculations=calculations,
            charts=charts[: self.settings.max_charts] if mode != "short_answer" else (),
            report=report,
            trace=trace,
        )
        self._validator.validate(response)
        if self._before_publish is not None:
            self._before_publish()
        after = self._binding_context(result.binding)
        if after != before:
            raise SynthesisFailure("synthesis_authorization_changed")
        final_task = self._task(request.task_id)
        if final_task.state_version != task.state_version or final_task.result != result:
            raise SynthesisFailure("synthesis_task_changed")
        self._validator.validate(response)
        return response

    def authorize_read(self, response: TrustedSynthesisResponseV1) -> None:
        task = self._task(response.trace.task_id)
        if task.state_version != response.trace.state_version:
            raise SynthesisFailure("synthesis_task_changed")
        context = self._binding_context(task.result.binding)
        if context.normalized_sha256 != response.trace.normalized_sha256:
            raise SynthesisFailure("synthesis_dataset_changed")
        self._validator.validate(response)

    def _task(self, task_id):
        try:
            task = self._agent.get(task_id)
        except Exception:
            raise SynthesisFailure("synthesis_not_authorized") from None
        if task.status != "completed" or task.result is None:
            code = (
                "synthesis_reconciliation_required"
                if task.status == "needs_reconciliation"
                else "synthesis_task_not_completed"
            )
            raise SynthesisFailure(code)
        if task.result.status != "completed":
            raise SynthesisFailure("synthesis_result_not_publishable")
        return task

    def _binding_context(self, binding):
        try:
            return self._analysis.binding_context(binding)
        except Exception:
            raise SynthesisFailure("synthesis_not_authorized") from None

    def _verified_receipts(self, task_id, result, execution):
        receipts = self._store.list_receipts(task_id)
        if not receipts or any(item.status != "succeeded" for item in receipts):
            raise SynthesisFailure("synthesis_receipt_incomplete")
        expected_steps = tuple(step.step_id for step in execution.plan.steps)
        if tuple(item.step_id for item in receipts) != expected_steps:
            raise SynthesisFailure("synthesis_receipt_sequence_mismatch")
        for receipt in receipts:
            if canonical_json_sha256(receipt.output) != receipt.output_sha256:
                raise SynthesisFailure("synthesis_receipt_hash_mismatch")
        assembly_receipt = next(
            item for item in receipts if item.step_id == execution.plan.steps[-1].step_id
        )
        if canonical_json_sha256(result.model_dump(mode="json")) != assembly_receipt.output_sha256:
            raise SynthesisFailure("synthesis_final_result_mismatch")
        return tuple(
            ReceiptReferenceV1(
                step_id=item.step_id,
                action_name=item.action_name,
                input_sha256=item.input_sha256,
                output_sha256=item.output_sha256,
                tool_call_id=item.tool_call_id,
            )
            for item in receipts
        )

    def _calculations(self, results: tuple[DatasetToolResult, ...]):
        calculations = []
        for operation_index, result in enumerate(results, 1):
            sources = _unique_sources(_source(item) for item in result.sources)
            limitation_codes = tuple(
                dict.fromkeys(
                    item.code for item in result.findings if item.severity == "limitation"
                )
            )
            for row_index, row in enumerate(result.rows, 1):
                dimensions = tuple(
                    CalculationDimensionV2(
                        key=item.key,
                        value=item.result.value if item.result.value is not None else "[null]",
                    )
                    for item in row.dimensions
                )
                for measure in row.measures:
                    if measure.result.kind not in NUMERIC_KINDS and measure.result.kind != "null":
                        continue
                    value = (
                        None
                        if measure.result.value is None
                        else CalculationValue(
                            value=measure.result.value,
                            unit=measure.result.unit or ("count" if measure.result.kind == "integer" else "number"),
                        )
                    )
                    calculations.append(
                        TrustedCalculationV2(
                            calculation_id=_safe_calculation_id(
                                operation_index, row_index, measure.key
                            ),
                            operation=result.operation,
                            label=measure.key.replace("_", " "),
                            value=value,
                            dimensions=dimensions,
                            parameters_sha256=result.parameters_sha256,
                            sources=sources,
                            limitation_codes=limitation_codes,
                        )
                    )
        if not calculations or len(calculations) > self.settings.max_calculations:
            raise SynthesisFailure("synthesis_calculation_limit")
        return tuple(calculations)

    @staticmethod
    def _dimension_label(calculation):
        return " | ".join(f"{item.key}={item.value}" for item in calculation.dimensions)

    def _charts(self, calculations):
        groups = defaultdict(list)
        for calculation in calculations:
            if calculation.value is not None:
                groups[(calculation.parameters_sha256, calculation.label, calculation.value.unit)].append(calculation)
        charts = []
        for (_, label, _), items in groups.items():
            if len(items) < 2:
                continue
            if any(not item.dimensions for item in items):
                continue
            categories = tuple(self._dimension_label(item) for item in items)
            if len(categories) != len(set(categories)):
                continue
            first_dimension_values = [item.dimensions[0].value for item in items]
            is_dates = len(items[0].dimensions) == 1
            if is_dates:
                try:
                    for value in first_dimension_values:
                        date.fromisoformat(value)
                except ValueError:
                    is_dates = False
            sources = _unique_sources(source for item in items for source in item.sources)
            charts.append(
                ChartSpecV1(
                    chart_type="line" if is_dates else "bar",
                    title=label.title(),
                    category_labels=categories,
                    series=(
                        ChartSeries(
                            label=label,
                            values=tuple(item.value.value for item in items),
                            calculation_ids=tuple(item.calculation_id for item in items),
                        ),
                    ),
                    sources=sources,
                )
            )
        if charts:
            return tuple(charts)
        same_unit = defaultdict(list)
        for item in calculations:
            if item.value is not None:
                same_unit[item.value.unit].append(item)
        candidates = max(same_unit.values(), key=len, default=[])
        if len(candidates) < 2:
            return ()
        candidates = candidates[:20]
        return (
            ChartSpecV1(
                chart_type="table",
                title="Verified calculations",
                category_labels=tuple(item.label for item in candidates),
                series=(
                    ChartSeries(
                        label=candidates[0].value.unit,
                        values=tuple(item.value.value for item in candidates),
                        calculation_ids=tuple(item.calculation_id for item in candidates),
                    ),
                ),
                sources=_unique_sources(source for item in candidates for source in item.sources),
            ),
        )

    @staticmethod
    def _mode(request, calculations, charts):
        if request.output_mode == "short_answer":
            return "short_answer"
        if request.output_mode == "chart":
            return "answer_with_chart"
        if request.output_mode == "full_report":
            return "full_report"
        if len(calculations) >= 6:
            return "full_report"
        return "answer_with_chart" if charts else "short_answer"

    @staticmethod
    def _report(answer, sources, calculations):
        calculations_by_id = {item.calculation_id: item for item in calculations}
        source_indexes = {
            canonical_json_sha256(item.model_dump(mode="json")): index
            for index, item in enumerate(sources)
        }
        sections = []
        for index, claim in enumerate(answer.claims, 1):
            referenced_sources = []
            for reference in claim.value_references:
                for source in calculations_by_id[reference.calculation_id].sources:
                    source_index = source_indexes[
                        canonical_json_sha256(source.model_dump(mode="json"))
                    ]
                    if source_index not in referenced_sources:
                        referenced_sources.append(source_index)
            sections.append(
                ReportSection(
                    heading=f"Finding {index}",
                    body=claim.text,
                    source_indexes=tuple(referenced_sources),
                )
            )
        if answer.limitations:
            sections.append(
                ReportSection(
                    heading="Limitations",
                    body="; ".join(answer.limitations),
                )
            )
        return AnalysisReportV1(
            title="Trusted dataset analysis report",
            summary=answer.summary,
            sections=tuple(sections),
            sources=sources,
        )


__all__ = ["TrustedResultSynthesisService"]
