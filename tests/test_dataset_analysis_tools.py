"""Offline deterministic PV2-04c calculation and Tool Registry tests."""

from __future__ import annotations

from types import SimpleNamespace
import unittest
from uuid import uuid4

from pydantic import ValidationError

from backend.datasets.analysis import DeterministicDatasetAnalyzer
from backend.datasets.analysis_models import (
    AggregateSpecV1,
    DatasetAggregateInput,
    DatasetAnalysisSettings,
    DatasetComparisonInput,
    DatasetDerivedMetricInput,
    DatasetFilterV1,
    DatasetProfileInput,
    DatasetQualityInput,
    DatasetRankingInput,
)
from backend.datasets.models import Column, FileSpec, ParseSettings
from backend.datasets.parser import parse_core
from backend.product_facade.models import DatasetRevisionBinding
from backend.tools.dataset_analysis import (
    DATASET_ANALYSIS_PRINCIPAL,
    build_dataset_analysis_registry,
    load_dataset_analysis_settings,
)
from backend.tools.registry import ToolRegistryError
from tests.dataset_fixtures import workbook


class DatasetAnalysisToolTests(unittest.TestCase):
    def setUp(self):
        self.project_id = uuid4()
        self.binding = DatasetRevisionBinding(
            asset_id=uuid4(), asset_revision_id=uuid4(), dataset_revision_id=uuid4()
        )
        self.columns = (
            Column(name="region"),
            Column(name="period", kind="date"),
            Column(name="revenue", kind="money", unit="USD"),
            Column(name="cost", kind="money", unit="USD"),
            Column(name="orders", kind="decimal"),
            Column(name="note"),
        )
        self.rows = [
            ["region", "period", "revenue", "cost", "orders", "note"],
            ["North", "2026-01-31", "100.25", "60.00", "2", "ok"],
            ["South", "2026-01-31", "-20.25", "30.00", "1", ""],
            ["North", "2026-02-28", "120.00", "80.00", "3", "ok"],
            ["South", "2026-02-28", "0", "0", "0", "ok"],
        ]
        csv = ("\n".join(",".join(row) for row in self.rows) + "\n").encode()
        self.dataset = parse_core(
            csv,
            FileSpec(
                project_id=str(self.project_id),
                dataset_id="synthetic_finance",
                filename="finance.csv",
                columns=self.columns,
            ),
            ParseSettings(enabled=True),
        )

    def analyzer(self, dataset=None, **settings):
        return DeterministicDatasetAnalyzer(
            dataset or self.dataset,
            project_id=self.project_id,
            binding=self.binding,
            settings=DatasetAnalysisSettings(enabled=True, **settings),
        )

    def test_profile_and_quality_preserve_types_units_nulls_and_sources(self):
        profile = self.analyzer().profile(
            DatasetProfileInput(binding=self.binding, fields=("revenue", "note"))
        )
        self.assertEqual(profile.status, "completed")
        self.assertEqual(profile.rows[0].measures[0].result.value, "money")
        self.assertEqual(profile.rows[0].measures[1].result.value, "USD")
        self.assertEqual(profile.rows[1].measures[4].result.value, "1")
        self.assertEqual(profile.sources[0].columns, ("revenue", "note"))
        quality = self.analyzer().quality(
            DatasetQualityInput(binding=self.binding, fields=("note",))
        )
        self.assertEqual(quality.rows[0].measures[0].result.value, "1")
        self.assertEqual(quality.rows[0].measures[1].result.value, "25")

    def test_filter_group_aggregate_sort_is_exact_and_repeatable(self):
        request = DatasetAggregateInput(
            binding=self.binding,
            filters=(DatasetFilterV1(field="period", operator="between", values=("2026-01-01", "2026-12-31")),),
            group_by=("region",),
            aggregations=(
                AggregateSpecV1(result_id="sales", function="sum", field="revenue"),
                AggregateSpecV1(result_id="average_orders", function="average", field="orders"),
                AggregateSpecV1(result_id="rows", function="count_rows"),
            ),
            sort=({"key": "sales", "direction": "descending"},),
        )
        first = self.analyzer().aggregate(request)
        second = self.analyzer().aggregate(request)
        self.assertEqual(first, second)
        self.assertEqual([row.dimensions[0].result.value for row in first.rows], ["North", "South"])
        self.assertEqual(first.rows[0].measures[0].result.value, "220.25")
        self.assertEqual(first.rows[1].measures[0].result.value, "-20.25")
        self.assertEqual(first.rows[0].measures[0].result.unit, "USD")
        self.assertEqual(first.matched_rows, 4)

    def test_comparison_absolute_percentage_and_zero_denominator(self):
        absolute = self.analyzer().compare(DatasetComparisonInput(
            binding=self.binding,
            dimension="period",
            measure="revenue",
            baseline="2026-01-31",
            comparison="2026-02-28",
            derived_metric="absolute_change",
        ))
        values = {cell.key: cell.result for cell in absolute.rows[0].measures}
        self.assertEqual(values["baseline_value"].value, "80")
        self.assertEqual(values["comparison_value"].value, "120")
        self.assertEqual(values["absolute_change"].value, "40")
        percentage = self.analyzer().compare(DatasetComparisonInput(
            binding=self.binding,
            filters=(DatasetFilterV1(field="region", operator="eq", values=("South",)),),
            dimension="period",
            measure="revenue",
            baseline="2026-01-31",
            comparison="2026-02-28",
            derived_metric="percentage_change",
        ))
        self.assertEqual(percentage.rows[0].measures[2].result.value, "-100")
        zero = self.analyzer().compare(DatasetComparisonInput(
            binding=self.binding,
            filters=(DatasetFilterV1(field="region", operator="eq", values=("South",)),),
            dimension="period",
            measure="cost",
            baseline="2026-02-28",
            comparison="2026-01-31",
            derived_metric="percentage_change",
        ))
        self.assertIsNone(zero.rows[0].measures[2].result.value)
        self.assertEqual(zero.findings[0].code, "zero_denominator")

    def test_derived_metric_rejects_mixed_units_and_handles_zero(self):
        mixed_columns = self.columns + (Column(name="eur", kind="money", unit="EUR"),)
        mixed_rows = [row + (["eur"] if index == 0 else ["1.00"]) for index, row in enumerate(self.rows)]
        data = ("\n".join(",".join(row) for row in mixed_rows) + "\n").encode()
        dataset = parse_core(data, FileSpec(project_id=str(self.project_id), dataset_id="mixed", filename="mixed.csv", columns=mixed_columns), ParseSettings(enabled=True))
        mismatch = self.analyzer(dataset).derive(DatasetDerivedMetricInput(
            binding=self.binding,
            numerator_field="revenue",
            denominator_field="eur",
            operation="ratio",
            result_id="revenue_ratio",
        ))
        self.assertEqual((mismatch.status, mismatch.findings[0].code), ("needs_clarification", "unit_mismatch"))
        zero = self.analyzer().derive(DatasetDerivedMetricInput(
            binding=self.binding,
            filters=(
                DatasetFilterV1(field="period", operator="eq", values=("2026-02-28",)),
                DatasetFilterV1(field="region", operator="eq", values=("South",)),
            ),
            numerator_field="revenue",
            denominator_field="cost",
            operation="ratio",
            result_id="margin_ratio",
        ))
        self.assertIsNone(zero.rows[0].measures[2].result.value)
        self.assertEqual(zero.findings[0].code, "zero_denominator")

    def test_ranking_is_bounded_and_stable(self):
        request = DatasetRankingInput(
            binding=self.binding,
            dimension="region",
            measure="revenue",
            aggregation="sum",
            direction="highest",
            limit=1,
        )
        result = self.analyzer().rank(request)
        self.assertEqual(len(result.rows), 1)
        self.assertEqual(result.rows[0].dimensions[0].result.value, "North")
        self.assertEqual(result.rows[0].measures[0].result.value, "220.25")

    def test_ratio_missing_numeric_values_and_ranking_ties_are_deterministic(self):
        columns = (
            Column(name="team"),
            Column(name="revenue", kind="money", unit="USD"),
            Column(name="cost", kind="money", unit="USD"),
        )
        rows = [
            ["team", "revenue", "cost"],
            ["A", "10", "2"],
            ["B", "10", "4"],
            ["C", "", "0"],
        ]
        dataset = parse_core(
            ("\n".join(",".join(row) for row in rows) + "\n").encode(),
            FileSpec(
                project_id=str(self.project_id),
                dataset_id="ratio_ties",
                filename="ratio-ties.csv",
                columns=columns,
            ),
            ParseSettings(enabled=True),
        )
        analyzer = self.analyzer(dataset)
        aggregate = analyzer.aggregate(DatasetAggregateInput(
            binding=self.binding,
            aggregations=(
                AggregateSpecV1(result_id="revenue_sum", function="sum", field="revenue"),
                AggregateSpecV1(result_id="revenue_average", function="average", field="revenue"),
                AggregateSpecV1(result_id="rows", function="count_rows"),
                AggregateSpecV1(result_id="non_null_revenue", function="count_non_null", field="revenue"),
            ),
        ))
        measures = {cell.key: cell.result.value for cell in aggregate.rows[0].measures}
        self.assertEqual(
            measures,
            {
                "revenue_sum": "20",
                "revenue_average": "10",
                "rows": "3",
                "non_null_revenue": "2",
            },
        )
        ratio = analyzer.derive(DatasetDerivedMetricInput(
            binding=self.binding,
            numerator_field="revenue",
            denominator_field="cost",
            operation="ratio",
            result_id="revenue_cost_ratio",
        ))
        self.assertEqual(ratio.rows[0].measures[2].result.value, "3.3333")
        self.assertEqual(ratio.rows[0].measures[2].result.unit, "ratio")
        ranking_request = DatasetRankingInput(
            binding=self.binding,
            dimension="team",
            measure="revenue",
            aggregation="sum",
            direction="highest",
            limit=3,
        )
        first = analyzer.rank(ranking_request)
        second = self.analyzer(dataset).rank(ranking_request)
        self.assertEqual(first, second)
        self.assertEqual(
            [row.dimensions[0].result.value for row in first.rows],
            ["A", "B", "C"],
        )
        self.assertEqual(
            [row.measures[0].result.value for row in first.rows],
            ["10", "10", None],
        )

    def test_group_limit_and_invalid_aggregation_type_fail_closed(self):
        grouped = self.analyzer(max_groups=1).aggregate(DatasetAggregateInput(
            binding=self.binding,
            group_by=("region",),
            aggregations=(AggregateSpecV1(result_id="rows", function="count_rows"),),
        ))
        self.assertEqual((grouped.status, grouped.findings[0].code), ("blocked", "group_limit_exceeded"))
        invalid = self.analyzer().aggregate(DatasetAggregateInput(
            binding=self.binding,
            aggregations=(AggregateSpecV1(result_id="bad", function="sum", field="note"),),
        ))
        self.assertEqual((invalid.status, invalid.findings[0].code), ("needs_clarification", "invalid_field_type"))

    def test_missing_field_ambiguous_date_and_text_ordering_are_centralized(self):
        missing = self.analyzer().aggregate(DatasetAggregateInput(
            binding=self.binding,
            group_by=("missing",),
            aggregations=(AggregateSpecV1(result_id="sales", function="sum", field="unknown"),),
        ))
        self.assertEqual(missing.status, "needs_clarification")
        self.assertEqual({item.fields[0] for item in missing.findings}, {"missing", "unknown"})
        ambiguous = self.analyzer().aggregate(DatasetAggregateInput(
            binding=self.binding,
            filters=(DatasetFilterV1(field="period", operator="eq", values=("01/02/2026",)),),
            aggregations=(AggregateSpecV1(result_id="rows", function="count_rows"),),
        ))
        self.assertEqual(ambiguous.findings[0].code, "filter_value_invalid")
        unsupported = self.analyzer().aggregate(DatasetAggregateInput(
            binding=self.binding,
            filters=(DatasetFilterV1(field="region", operator="gt", values=("North",)),),
            aggregations=(AggregateSpecV1(result_id="rows", function="count_rows"),),
        ))
        self.assertEqual(unsupported.findings[0].code, "operator_not_supported")

    def test_xlsx_produces_same_calculation_as_csv(self):
        xlsx_dataset = parse_core(
            workbook(self.rows),
            FileSpec(project_id=str(self.project_id), dataset_id="synthetic_finance", filename="finance.xlsx", columns=self.columns),
            ParseSettings(enabled=True),
        )
        request = DatasetAggregateInput(
            binding=self.binding,
            aggregations=(AggregateSpecV1(result_id="sales", function="sum", field="revenue"),),
        )
        self.assertEqual(
            self.analyzer().aggregate(request).rows,
            self.analyzer(xlsx_dataset).aggregate(request).rows,
        )

    def test_strict_contract_rejects_authority_sql_code_and_invalid_arity(self):
        base = {"binding": self.binding.model_dump(), "aggregations": [{"result_id": "rows", "function": "count_rows"}]}
        for extra in ({"sql": "select 1"}, {"python": "eval('1')"}, {"trusted_local": True}, {"principal": "admin"}):
            with self.subTest(extra=extra), self.assertRaises(ValidationError):
                DatasetAggregateInput.model_validate({**base, **extra})
        with self.assertRaises(ValidationError):
            DatasetFilterV1(field="region", operator="between", values=("a",))

    def test_registry_exposes_only_six_registered_tools_to_internal_principal(self):
        result = self.analyzer().profile(DatasetProfileInput(binding=self.binding, fields=("region",)))
        runtime = SimpleNamespace(**{name: (lambda _request, value=result: value) for name in ("profile", "aggregate", "compare", "derive", "rank", "quality")})
        registry = build_dataset_analysis_registry(runtime)
        self.assertEqual(len(registry.schemas_for(DATASET_ANALYSIS_PRINCIPAL)), 6)
        self.assertEqual(registry.schemas_for("user-supplied"), ())
        with self.assertRaises(ToolRegistryError) as error:
            registry.resolve("aggregate_dataset_revision", "user-supplied")
        self.assertEqual(error.exception.code, "tool_not_authorized")

    def test_feature_flag_defaults_off(self):
        import os
        previous = os.environ.pop("DATASET_ANALYSIS_TOOLS_ENABLED", None)
        try:
            self.assertFalse(load_dataset_analysis_settings().enabled)
        finally:
            if previous is not None:
                os.environ["DATASET_ANALYSIS_TOOLS_ENABLED"] = previous


if __name__ == "__main__":
    unittest.main()
