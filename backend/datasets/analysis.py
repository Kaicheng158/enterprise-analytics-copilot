"""Bounded deterministic calculations over an already validated Dataset."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
import time
from typing import Iterable
from uuid import UUID

from .analysis_models import (
    AggregateSpecV1,
    DatasetAggregateInput,
    DatasetAnalysisFinding,
    DatasetAnalysisSettings,
    DatasetComparisonInput,
    DatasetDerivedMetricInput,
    DatasetFilterV1,
    DatasetProfileInput,
    DatasetQualityInput,
    DatasetRankingInput,
    DatasetResultCell,
    DatasetResultRow,
    DatasetResultValue,
    DatasetSourceSpan,
    DatasetToolResult,
)
from .models import Cell, Column, Dataset, Table
from backend.product_facade.models import DatasetRevisionBinding


NUMERIC_KINDS = frozenset({"decimal", "money"})


class DatasetAnalysisLimit(RuntimeError):
    pass


def _decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise ValueError("nonfinite decimal")
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def _average(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    scale = min(12, max(4, *(max(0, -value.as_tuple().exponent) for value in values)))
    return (sum(values, Decimal(0)) / Decimal(len(values))).quantize(
        Decimal(1).scaleb(-scale), rounding=ROUND_HALF_UP
    )


class DeterministicDatasetAnalyzer:
    def __init__(
        self,
        dataset: Dataset,
        *,
        project_id: UUID,
        binding: DatasetRevisionBinding,
        settings: DatasetAnalysisSettings,
        clock=time.monotonic,
    ):
        if not settings.enabled:
            raise ValueError("dataset analysis is disabled")
        if dataset.status != "validated":
            raise ValueError("dataset requires review")
        self.dataset = dataset
        self.project_id = project_id
        self.binding = binding
        self.settings = settings
        self.clock = clock
        self.started = clock()

    def _deadline(self) -> None:
        if self.clock() - self.started > self.settings.wall_seconds:
            raise DatasetAnalysisLimit("analysis_time_limit")

    def _table(self, table_index: int) -> Table:
        if table_index > len(self.dataset.tables):
            raise ValueError("table_index_invalid")
        table = self.dataset.tables[table_index - 1]
        if len(table.rows) > self.settings.max_rows:
            raise DatasetAnalysisLimit("analysis_row_limit")
        return table

    @staticmethod
    def _column_map(table: Table) -> dict[str, tuple[int, Column]]:
        return {column.name: (index, column) for index, column in enumerate(table.columns)}

    @staticmethod
    def _params_hash(request) -> str:
        payload = json.dumps(
            request.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
        return hashlib.sha256(payload).hexdigest()

    @staticmethod
    def _finding(code: str, severity: str, *fields: str) -> DatasetAnalysisFinding:
        return DatasetAnalysisFinding(code=code, severity=severity, fields=tuple(fields))

    @staticmethod
    def _value(value, kind: str, unit: str | None = None) -> DatasetResultValue:
        if value is None:
            return DatasetResultValue(value=None, kind="null", unit=unit)
        if isinstance(value, Decimal):
            return DatasetResultValue(value=_decimal_text(value), kind="decimal", unit=unit)
        if type(value) is int:
            return DatasetResultValue(value=str(value), kind="integer", unit=unit)
        if isinstance(value, bool):
            return DatasetResultValue(value="true" if value else "false", kind="boolean", unit=unit)
        if isinstance(value, date):
            return DatasetResultValue(value=value.isoformat(), kind="date", unit=unit)
        return DatasetResultValue(value=str(value), kind="text", unit=unit)

    def _source(
        self,
        table: Table,
        table_index: int,
        rows: list[tuple[int, tuple[Cell, ...]]],
        columns: Iterable[str],
    ) -> DatasetSourceSpan:
        row_numbers = [row_number for row_number, _ in rows]
        if row_numbers:
            start, end = min(row_numbers), max(row_numbers)
        else:
            start, end = 2, max(2, len(table.rows) + 1)
        return DatasetSourceSpan(
            project_id=self.project_id,
            asset_id=self.binding.asset_id,
            asset_revision_id=self.binding.asset_revision_id,
            dataset_revision_id=self.binding.dataset_revision_id,
            normalized_sha256=self.dataset.normalized_sha256,
            table_index=table_index,
            sheet=table.sheet,
            row_start=start,
            row_end=end,
            columns=tuple(dict.fromkeys(columns)),
        )

    def _result(
        self,
        request,
        *,
        operation: str,
        status: str,
        scanned: int,
        matched: int,
        rows=(),
        findings=(),
        sources=(),
    ) -> DatasetToolResult:
        return DatasetToolResult(
            operation=operation,
            status=status,
            project_id=self.project_id,
            asset_id=self.binding.asset_id,
            asset_revision_id=self.binding.asset_revision_id,
            dataset_revision_id=self.binding.dataset_revision_id,
            parameters_sha256=self._params_hash(request),
            scanned_rows=scanned,
            matched_rows=matched,
            rows=tuple(rows),
            findings=tuple(findings),
            sources=tuple(sources),
        )

    @staticmethod
    def _parse_literal(column: Column, raw: str):
        if column.kind in NUMERIC_KINDS:
            try:
                value = Decimal(raw)
            except InvalidOperation:
                raise ValueError("invalid decimal") from None
            if not value.is_finite():
                raise ValueError("invalid decimal")
            return value
        if column.kind == "date":
            if len(raw) != 10:
                raise ValueError("ambiguous date")
            try:
                return date.fromisoformat(raw)
            except ValueError:
                raise ValueError("ambiguous date") from None
        if column.kind == "boolean":
            if raw not in {"true", "false"}:
                raise ValueError("invalid boolean")
            return raw == "true"
        return raw

    @staticmethod
    def _cell_value(cell: Cell, column: Column):
        if cell.value is None:
            return None
        return DeterministicDatasetAnalyzer._parse_literal(column, cell.value)

    def _validate_filters(self, filters, columns):
        findings = []
        prepared = []
        for item in filters:
            entry = columns.get(item.field)
            if entry is None:
                findings.append(self._finding("field_not_found", "clarification", item.field))
                continue
            index, column = entry
            if column.kind in {"text", "boolean"} and item.operator not in {
                "eq", "not_eq", "in", "is_null", "not_null"
            }:
                findings.append(self._finding("operator_not_supported", "clarification", item.field))
                continue
            try:
                values = tuple(self._parse_literal(column, value) for value in item.values)
            except ValueError:
                findings.append(self._finding("filter_value_invalid", "clarification", item.field))
                continue
            prepared.append((index, column, item.operator, values))
        return prepared, findings

    @staticmethod
    def _matches(value, operator, expected) -> bool:
        if operator == "is_null":
            return value is None
        if operator == "not_null":
            return value is not None
        if value is None:
            return False
        if operator == "eq":
            return value == expected[0]
        if operator == "not_eq":
            return value != expected[0]
        if operator == "in":
            return value in expected
        if operator == "gt":
            return value > expected[0]
        if operator == "gte":
            return value >= expected[0]
        if operator == "lt":
            return value < expected[0]
        if operator == "lte":
            return value <= expected[0]
        return expected[0] <= value <= expected[1]

    def _select(self, table: Table, filters: tuple[DatasetFilterV1, ...]):
        columns = self._column_map(table)
        prepared, findings = self._validate_filters(filters, columns)
        if findings:
            return [], findings
        selected = []
        for offset, row in enumerate(table.rows, 2):
            self._deadline()
            if all(
                self._matches(self._cell_value(row[index], column), operator, values)
                for index, column, operator, values in prepared
            ):
                selected.append((offset, row))
        return selected, []

    def _require_fields(self, names, columns):
        return [self._finding("field_not_found", "clarification", name) for name in names if name not in columns]

    def profile(self, request: DatasetProfileInput) -> DatasetToolResult:
        table = self._table(request.table_index)
        columns = self._column_map(table)
        fields = request.fields or tuple(columns)
        findings = self._require_fields(fields, columns)
        if findings:
            return self._result(request, operation="profile", status="needs_clarification", scanned=0, matched=0, findings=findings)
        result_rows = []
        for name in fields:
            index, column = columns[name]
            values = [row[index].value for row in table.rows]
            non_null = [value for value in values if value is not None]
            result_rows.append(DatasetResultRow(
                dimensions=(DatasetResultCell(key="field", result=self._value(name, "text")),),
                measures=(
                    DatasetResultCell(key="kind", result=self._value(column.kind, "text")),
                    DatasetResultCell(key="unit", result=self._value(column.unit, "text")),
                    DatasetResultCell(key="row_count", result=self._value(len(values), "integer", "rows")),
                    DatasetResultCell(key="non_null_count", result=self._value(len(non_null), "integer", "rows")),
                    DatasetResultCell(key="null_count", result=self._value(len(values) - len(non_null), "integer", "rows")),
                    DatasetResultCell(key="distinct_count", result=self._value(len(set(non_null)), "integer", "values")),
                ),
            ))
        rows = [(index, row) for index, row in enumerate(table.rows, 2)]
        return self._result(request, operation="profile", status="completed", scanned=len(rows), matched=len(rows), rows=result_rows, sources=(self._source(table, request.table_index, rows, fields),))

    def _aggregate_value(self, spec: AggregateSpecV1, selected, columns):
        if spec.function == "count_rows":
            return len(selected), "count", "integer"
        index, column = columns[spec.field]
        raw_values = [self._cell_value(row[index], column) for _, row in selected]
        values = [value for value in raw_values if value is not None]
        if spec.function == "count_non_null":
            return len(values), "count", "integer"
        if column.kind not in NUMERIC_KINDS:
            raise TypeError(spec.field)
        decimals = list(values)
        if spec.function == "sum":
            value = sum(decimals, Decimal(0)) if decimals else None
        elif spec.function == "average":
            value = _average(decimals)
        elif spec.function == "min":
            value = min(decimals) if decimals else None
        else:
            value = max(decimals) if decimals else None
        return value, column.unit or "number", "decimal"

    def _aggregate_groups(self, request: DatasetAggregateInput):
        table = self._table(request.table_index)
        columns = self._column_map(table)
        requested = list(request.group_by) + [item.field for item in request.aggregations if item.field]
        findings = self._require_fields(requested, columns)
        for item in request.aggregations:
            if item.field and item.function in {"sum", "min", "max", "average"} and item.field in columns and columns[item.field][1].kind not in NUMERIC_KINDS:
                findings.append(self._finding("invalid_field_type", "clarification", item.field))
        valid_sort = set(request.group_by) | {item.result_id for item in request.aggregations}
        for item in request.sort:
            if item.key not in valid_sort:
                findings.append(self._finding("field_not_found", "clarification", item.key))
        selected, filter_findings = self._select(table, request.filters)
        findings.extend(filter_findings)
        if findings:
            return table, columns, selected, findings, []
        groups = {}
        for row_number, row in selected:
            key = tuple(self._cell_value(row[columns[name][0]], columns[name][1]) for name in request.group_by)
            groups.setdefault(key, []).append((row_number, row))
            if len(groups) > self.settings.max_groups:
                return table, columns, selected, [self._finding("group_limit_exceeded", "limitation", *request.group_by)], []
        result_rows = []
        for key, grouped in groups.items() if request.group_by else [((), selected)]:
            dimensions = tuple(
                DatasetResultCell(key=name, result=self._value(value, columns[name][1].kind, columns[name][1].unit))
                for name, value in zip(request.group_by, key)
            )
            measures = []
            for spec in request.aggregations:
                value, unit, kind = self._aggregate_value(spec, grouped, columns)
                measures.append(DatasetResultCell(key=spec.result_id, result=self._value(value, kind, unit)))
            result_rows.append(DatasetResultRow(dimensions=dimensions, measures=tuple(measures)))
        for sort in reversed(request.sort):
            populated, missing = [], []
            for row in result_rows:
                cells = {cell.key: cell.result for cell in row.dimensions + row.measures}
                (missing if cells[sort.key].value is None else populated).append(row)
            def sort_value(row):
                cell = {cell.key: cell.result for cell in row.dimensions + row.measures}[sort.key]
                return Decimal(cell.value) if cell.kind in {"decimal", "integer"} else cell.value
            populated.sort(key=sort_value, reverse=sort.direction == "descending")
            result_rows = populated + missing
        return table, columns, selected, [], result_rows

    def aggregate(self, request: DatasetAggregateInput) -> DatasetToolResult:
        table, columns, selected, findings, rows = self._aggregate_groups(request)
        if findings:
            status = "blocked" if any(item.code == "group_limit_exceeded" for item in findings) else "needs_clarification"
            return self._result(request, operation="aggregate", status=status, scanned=len(table.rows), matched=len(selected), findings=findings)
        notices = []
        if not selected:
            notices.append(self._finding("no_matching_rows", "limitation"))
        if len(rows) > request.limit:
            rows = rows[: request.limit]
            notices.append(self._finding("result_limit_applied", "info"))
        used = list(request.group_by) + [item.field for item in request.aggregations if item.field]
        used += [item.field for item in request.filters]
        return self._result(request, operation="aggregate", status="completed", scanned=len(table.rows), matched=len(selected), rows=rows, findings=notices, sources=(self._source(table, request.table_index, selected, used or tuple(columns)),))

    def _measure_for(self, selected, column_entry, aggregation):
        index, column = column_entry
        values = [self._cell_value(row[index], column) for _, row in selected if row[index].value is not None]
        if aggregation == "count_non_null":
            return Decimal(len(values)), "count"
        if column.kind not in NUMERIC_KINDS:
            raise TypeError(column.name)
        if aggregation == "sum":
            return (sum(values, Decimal(0)) if values else None), column.unit or "number"
        return _average(values), column.unit or "number"

    def compare(self, request: DatasetComparisonInput) -> DatasetToolResult:
        table = self._table(request.table_index)
        columns = self._column_map(table)
        findings = self._require_fields((request.dimension, request.measure), columns)
        if not findings and request.aggregation != "count_non_null" and columns[request.measure][1].kind not in NUMERIC_KINDS:
            findings.append(self._finding("invalid_field_type", "clarification", request.measure))
        selected, filter_findings = self._select(table, request.filters)
        findings.extend(filter_findings)
        if findings:
            return self._result(request, operation="compare", status="needs_clarification", scanned=len(table.rows), matched=len(selected), findings=findings)
        dimension_index, dimension_column = columns[request.dimension]
        try:
            baseline = self._parse_literal(dimension_column, request.baseline)
            comparison = self._parse_literal(dimension_column, request.comparison)
        except ValueError:
            return self._result(request, operation="compare", status="needs_clarification", scanned=len(table.rows), matched=len(selected), findings=(self._finding("filter_value_invalid", "clarification", request.dimension),))
        baseline_rows = [(n, row) for n, row in selected if self._cell_value(row[dimension_index], dimension_column) == baseline]
        comparison_rows = [(n, row) for n, row in selected if self._cell_value(row[dimension_index], dimension_column) == comparison]
        if not baseline_rows or not comparison_rows:
            return self._result(request, operation="compare", status="needs_clarification", scanned=len(table.rows), matched=len(baseline_rows)+len(comparison_rows), findings=(self._finding("no_matching_rows", "clarification", request.dimension),))
        base_value, unit = self._measure_for(baseline_rows, columns[request.measure], request.aggregation)
        compare_value, _ = self._measure_for(comparison_rows, columns[request.measure], request.aggregation)
        derived = None if base_value is None or compare_value is None else compare_value - base_value
        derived_unit = unit
        notices = []
        if request.derived_metric == "percentage_change":
            derived_unit = "%"
            if base_value == 0:
                derived = None
                notices.append(self._finding("zero_denominator", "limitation", request.measure))
            elif base_value is not None and compare_value is not None:
                derived = ((compare_value - base_value) / base_value * Decimal(100)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        row = DatasetResultRow(
            dimensions=(
                DatasetResultCell(key="baseline", result=self._value(baseline, dimension_column.kind, dimension_column.unit)),
                DatasetResultCell(key="comparison", result=self._value(comparison, dimension_column.kind, dimension_column.unit)),
            ),
            measures=(
                DatasetResultCell(key="baseline_value", result=self._value(base_value, "decimal", unit)),
                DatasetResultCell(key="comparison_value", result=self._value(compare_value, "decimal", unit)),
                DatasetResultCell(key=request.derived_metric, result=self._value(derived, "decimal", derived_unit)),
            ),
        )
        used_rows = baseline_rows + comparison_rows
        used_columns = [request.dimension, request.measure] + [item.field for item in request.filters]
        return self._result(request, operation="compare", status="completed", scanned=len(table.rows), matched=len(used_rows), rows=(row,), findings=notices, sources=(self._source(table, request.table_index, used_rows, used_columns),))

    def derive(self, request: DatasetDerivedMetricInput) -> DatasetToolResult:
        table = self._table(request.table_index)
        columns = self._column_map(table)
        findings = self._require_fields((request.numerator_field, request.denominator_field), columns)
        if not findings:
            for field in (request.numerator_field, request.denominator_field):
                if columns[field][1].kind not in NUMERIC_KINDS:
                    findings.append(self._finding("invalid_field_type", "clarification", field))
        selected, filter_findings = self._select(table, request.filters)
        findings.extend(filter_findings)
        if not findings:
            left_unit = columns[request.numerator_field][1].unit or "number"
            right_unit = columns[request.denominator_field][1].unit or "number"
            if left_unit != right_unit:
                findings.append(self._finding("unit_mismatch", "clarification", request.numerator_field, request.denominator_field))
        if findings:
            return self._result(request, operation="derive", status="needs_clarification", scanned=len(table.rows), matched=len(selected), findings=findings)
        numerator, unit = self._measure_for(selected, columns[request.numerator_field], request.aggregation)
        denominator, _ = self._measure_for(selected, columns[request.denominator_field], request.aggregation)
        notices = []
        if request.operation == "difference":
            result = None if numerator is None or denominator is None else numerator - denominator
            result_unit = unit
        else:
            result_unit = "%" if request.operation == "percentage" else "ratio"
            if denominator == 0:
                result = None
                notices.append(self._finding("zero_denominator", "limitation", request.denominator_field))
            elif numerator is None or denominator is None:
                result = None
            else:
                multiplier = Decimal(100) if request.operation == "percentage" else Decimal(1)
                result = (numerator / denominator * multiplier).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        row = DatasetResultRow(measures=(
            DatasetResultCell(key="numerator", result=self._value(numerator, "decimal", unit)),
            DatasetResultCell(key="denominator", result=self._value(denominator, "decimal", unit)),
            DatasetResultCell(key=request.result_id, result=self._value(result, "decimal", result_unit)),
        ))
        used = [request.numerator_field, request.denominator_field] + [item.field for item in request.filters]
        return self._result(request, operation="derive", status="completed", scanned=len(table.rows), matched=len(selected), rows=(row,), findings=notices, sources=(self._source(table, request.table_index, selected, used),))

    def rank(self, request: DatasetRankingInput) -> DatasetToolResult:
        aggregate_request = DatasetAggregateInput(
            binding=request.binding,
            table_index=request.table_index,
            filters=request.filters,
            group_by=(request.dimension,),
            aggregations=(AggregateSpecV1(result_id="rank_value", function=request.aggregation, field=request.measure),),
            sort=(),
            limit=min(request.limit, self.settings.max_result_rows),
        )
        table, columns, selected, findings, rows = self._aggregate_groups(aggregate_request)
        if findings:
            return self._result(request, operation="rank", status="needs_clarification", scanned=len(table.rows), matched=len(selected), findings=findings)
        populated, missing = [], []
        for row in rows:
            value = row.measures[0].result.value
            (missing if value is None else populated).append(row)
        populated.sort(key=lambda row: Decimal(row.measures[0].result.value), reverse=request.direction == "highest")
        ranked = (populated + missing)[: request.limit]
        notices = () if selected else (self._finding("no_matching_rows", "limitation"),)
        used = [request.dimension, request.measure] + [item.field for item in request.filters]
        return self._result(request, operation="rank", status="completed", scanned=len(table.rows), matched=len(selected), rows=ranked, findings=notices, sources=(self._source(table, request.table_index, selected, used),))

    def quality(self, request: DatasetQualityInput) -> DatasetToolResult:
        table = self._table(request.table_index)
        columns = self._column_map(table)
        fields = request.fields or tuple(columns)
        findings = self._require_fields(fields, columns)
        if findings:
            return self._result(request, operation="quality", status="needs_clarification", scanned=0, matched=0, findings=findings)
        rows = []
        for name in fields:
            index, column = columns[name]
            null_count = sum(1 for row in table.rows if row[index].value is None)
            rows.append(DatasetResultRow(
                dimensions=(DatasetResultCell(key="field", result=self._value(name, "text")),),
                measures=(
                    DatasetResultCell(key="null_count", result=self._value(null_count, "integer", "rows")),
                    DatasetResultCell(key="null_rate", result=self._value((Decimal(null_count) / Decimal(len(table.rows)) * Decimal(100)).quantize(Decimal("0.0001")), "decimal", "%")),
                ),
            ))
        issue_findings = tuple(self._finding("dataset_quality_issue", "info") for _ in self.dataset.issues[:32])
        all_rows = [(index, row) for index, row in enumerate(table.rows, 2)]
        return self._result(request, operation="quality", status="completed", scanned=len(all_rows), matched=len(all_rows), rows=rows, findings=issue_findings, sources=(self._source(table, request.table_index, all_rows, fields),))


__all__ = ["DatasetAnalysisLimit", "DeterministicDatasetAnalyzer"]
