"""Provider-independent Compare Tool v2 semantics and v1 compatibility."""

from __future__ import annotations

import unittest
from uuid import uuid4

from pydantic import ValidationError

from backend.datasets.analysis import DeterministicDatasetAnalyzer
from backend.datasets.analysis_models import (
    DatasetAnalysisSettings,
    DatasetComparisonInput,
    DatasetComparisonV2Input,
)
from backend.datasets.models import Column, FileSpec, ParseSettings
from backend.datasets.parser import parse_core
from backend.product_facade.models import DatasetRevisionBinding


class DatasetCompareV2Tests(unittest.TestCase):
    def setUp(self):
        self.project_id = uuid4()
        self.binding = DatasetRevisionBinding(
            asset_id=uuid4(), asset_revision_id=uuid4(), dataset_revision_id=uuid4()
        )

    def analyzer(self, rows, columns):
        data = ("\n".join(",".join(row) for row in rows) + "\n").encode()
        dataset = parse_core(
            data,
            FileSpec(
                project_id=str(self.project_id),
                dataset_id="compare_v2_synthetic",
                filename="compare-v2.csv",
                columns=columns,
            ),
            ParseSettings(enabled=True),
        )
        return DeterministicDatasetAnalyzer(
            dataset,
            project_id=self.project_id,
            binding=self.binding,
            settings=DatasetAnalysisSettings(enabled=True),
        )

    @staticmethod
    def filters(start, end, *extra):
        return (
            {"field": "period", "operator": "between", "values": [start, end]},
            *extra,
        )

    def finance(self):
        columns = (
            Column(name="region"),
            Column(name="period", kind="date"),
            Column(name="revenue", kind="money", unit="USD"),
            Column(name="cost", kind="money", unit="USD"),
        )
        rows = [
            ["region", "period", "revenue", "cost"],
            ["North", "2025-12-31", "90", "50"],
            ["North", "2026-01-05", "40.10", "20"],
            ["North", "2026-01-31", "60.15", "40"],
            ["South", "2026-01-15", "-20.25", "30"],
            ["North", "2026-02-28", "120", "80"],
            ["South", "2026-02-28", "0", "0"],
            ["West", "2026-02-28", "15", "10"],
        ]
        return self.analyzer(rows, columns)

    def request(self, **updates):
        values = {
            "binding": self.binding,
            "baseline_filters": self.filters("2026-01-01", "2026-01-31"),
            "comparison_filters": self.filters("2026-02-01", "2026-02-28"),
            "baseline_measure": "revenue",
            "comparison_measure": "revenue",
            "aggregation": "sum",
            "derived_metric": "absolute_change",
        }
        values.update(updates)
        return DatasetComparisonV2Input(**values)

    def test_natural_month_range_decimal_and_sources_are_repeatable(self):
        request = self.request()
        first = self.finance().compare_v2(request)
        second = self.finance().compare_v2(request)
        self.assertEqual(first, second)
        values = {item.key: item.result for item in first.rows[0].measures}
        self.assertEqual(values["baseline_value"].value, "80")
        self.assertEqual(values["comparison_value"].value, "135")
        self.assertEqual(values["absolute_change"].value, "55")
        self.assertEqual(values["absolute_change"].unit, "USD")
        self.assertEqual(first.matched_rows, 6)
        self.assertEqual(len(first.sources), 2)
        self.assertEqual(first.parameters_sha256, second.parameters_sha256)

    def test_group_alignment_missing_group_negative_and_true_zero(self):
        result = self.finance().compare_v2(self.request(group_by=("region",)))
        rows = {
            row.dimensions[0].result.value: {
                item.key: item.result.value for item in row.measures
            }
            for row in result.rows
        }
        self.assertEqual(rows["North"], {
            "baseline_value": "100.25",
            "comparison_value": "120",
            "absolute_change": "19.75",
        })
        self.assertEqual(rows["South"], {
            "baseline_value": "-20.25",
            "comparison_value": "0",
            "absolute_change": "20.25",
        })
        self.assertEqual(rows["West"], {
            "baseline_value": None,
            "comparison_value": "15",
            "absolute_change": None,
        })
        self.assertIn(
            "comparison_group_missing_baseline",
            {item.code for item in result.findings},
        )

    def test_percentage_zero_denominator_is_not_confused_with_missing(self):
        result = self.finance().compare_v2(
            self.request(
                baseline_filters=(
                    {"field": "period", "operator": "eq", "values": ["2026-02-28"]},
                    {"field": "region", "operator": "eq", "values": ["South"]},
                ),
                comparison_filters=(
                    {"field": "period", "operator": "eq", "values": ["2026-01-15"]},
                    {"field": "region", "operator": "eq", "values": ["South"]},
                ),
                derived_metric="percentage_change",
            )
        )
        values = {item.key: item.result.value for item in result.rows[0].measures}
        self.assertEqual(values["baseline_value"], "0")
        self.assertEqual(values["comparison_value"], "-20.25")
        self.assertIsNone(values["percentage_change"])
        self.assertIn("zero_denominator", {item.code for item in result.findings})

        negative_baseline = self.finance().compare_v2(
            self.request(
                baseline_filters=(
                    {"field": "period", "operator": "eq", "values": ["2026-01-15"]},
                    {"field": "region", "operator": "eq", "values": ["South"]},
                ),
                comparison_filters=(
                    {"field": "period", "operator": "eq", "values": ["2026-02-28"]},
                    {"field": "region", "operator": "eq", "values": ["South"]},
                ),
                derived_metric="percentage_change",
            )
        )
        negative_values = {
            item.key: item.result.value
            for item in negative_baseline.rows[0].measures
        }
        self.assertEqual(negative_values["baseline_value"], "-20.25")
        self.assertEqual(negative_values["comparison_value"], "0")
        self.assertEqual(negative_values["percentage_change"], "-100")

    def test_daily_cross_month_cross_year_and_category_scopes(self):
        analyzer = self.finance()
        custom_cross_month = analyzer.compare_v2(
            self.request(
                baseline_filters=self.filters("2026-01-15", "2026-02-15"),
                comparison_filters=self.filters("2026-02-16", "2026-02-28"),
            )
        )
        custom_values = {
            item.key: item.result.value
            for item in custom_cross_month.rows[0].measures
        }
        self.assertEqual(custom_values["baseline_value"], "39.9")
        self.assertEqual(custom_values["comparison_value"], "135")
        self.assertEqual(custom_values["absolute_change"], "95.1")
        cross_year = analyzer.compare_v2(
            self.request(
                baseline_filters=self.filters("2025-12-01", "2025-12-31"),
                comparison_filters=self.filters("2026-01-01", "2026-01-31"),
            )
        )
        values = {item.key: item.result.value for item in cross_year.rows[0].measures}
        self.assertEqual(values["baseline_value"], "90")
        self.assertEqual(values["comparison_value"], "80")
        self.assertEqual(values["absolute_change"], "-10")
        quarter = analyzer.compare_v2(
            self.request(
                baseline_filters=self.filters("2025-10-01", "2025-12-31"),
                comparison_filters=self.filters("2026-01-01", "2026-03-31"),
            )
        )
        quarter_values = {
            item.key: item.result.value for item in quarter.rows[0].measures
        }
        self.assertEqual(quarter_values["baseline_value"], "90")
        self.assertEqual(quarter_values["comparison_value"], "215")
        self.assertEqual(quarter_values["absolute_change"], "125")
        category = analyzer.compare_v2(
            self.request(
                baseline_filters=({"field": "region", "operator": "eq", "values": ["North"]},),
                comparison_filters=({"field": "region", "operator": "eq", "values": ["South"]},),
            )
        )
        category_values = {item.key: item.result.value for item in category.rows[0].measures}
        self.assertEqual(category_values["baseline_value"], "310.25")
        self.assertEqual(category_values["comparison_value"], "-20.25")

    def test_null_measure_and_unit_mismatch_are_explicit(self):
        columns = (
            Column(name="period", kind="date"),
            Column(name="usd", kind="money", unit="USD"),
            Column(name="eur", kind="money", unit="EUR"),
        )
        rows = [
            ["period", "usd", "eur"],
            ["2026-01-31", "", "10"],
            ["2026-02-28", "20", "12"],
        ]
        analyzer = self.analyzer(rows, columns)
        null_result = analyzer.compare_v2(
            DatasetComparisonV2Input(
                binding=self.binding,
                baseline_filters=({"field": "period", "operator": "eq", "values": ["2026-01-31"]},),
                comparison_filters=({"field": "period", "operator": "eq", "values": ["2026-02-28"]},),
                baseline_measure="usd",
                comparison_measure="usd",
                derived_metric="absolute_change",
            )
        )
        self.assertIsNone(null_result.rows[0].measures[0].result.value)
        self.assertIsNone(null_result.rows[0].measures[2].result.value)
        mismatch = analyzer.compare_v2(
            DatasetComparisonV2Input(
                binding=self.binding,
                baseline_filters=({"field": "period", "operator": "eq", "values": ["2026-01-31"]},),
                comparison_filters=({"field": "period", "operator": "eq", "values": ["2026-02-28"]},),
                baseline_measure="usd",
                comparison_measure="eur",
                derived_metric="absolute_change",
            )
        )
        self.assertEqual((mismatch.status, mismatch.findings[0].code), ("needs_clarification", "unit_mismatch"))

    def test_contract_rejects_identical_scopes_and_arbitrary_execution(self):
        with self.assertRaises(ValidationError):
            self.request(
                comparison_filters=self.filters("2026-01-01", "2026-01-31")
            )
        payload = self.request().model_dump(mode="json")
        for key, value in (("sql", "select 1"), ("python", "eval('1')")):
            with self.subTest(key=key), self.assertRaises(ValidationError):
                DatasetComparisonV2Input.model_validate({**payload, key: value})
        too_many_filters = tuple(
            {"field": "region", "operator": "eq", "values": [f"R{index}"]}
            for index in range(7)
        )
        with self.assertRaises(ValidationError):
            self.request(
                baseline_filters=too_many_filters,
                comparison_filters=tuple(reversed(too_many_filters)),
            )

    def test_compare_v1_historical_behavior_is_unchanged(self):
        result = self.finance().compare(
            DatasetComparisonInput(
                binding=self.binding,
                dimension="period",
                measure="revenue",
                baseline="2025-12-31",
                comparison="2026-02-28",
                derived_metric="absolute_change",
            )
        )
        values = {item.key: item.result.value for item in result.rows[0].measures}
        self.assertEqual(values, {
            "baseline_value": "90",
            "comparison_value": "135",
            "absolute_change": "45",
        })


if __name__ == "__main__":
    unittest.main()
