"""Offline contract and boundary tests for PV2-04a."""

from datetime import UTC, datetime
from pathlib import Path
import unittest
import uuid

from pydantic import ValidationError

from backend.agent_api.models import AgentTaskRequest
from backend.product_facade.models import (
    AnalysisDeliverables,
    AnalysisIntent,
    AnalysisReportV1,
    AnalysisSourceReference,
    ChartSeries,
    ChartSpecV1,
    DatasetAnalysisTaskRequestV1,
    DatasetAnalysisTaskStatusV1,
    DatasetRevisionBinding,
    ProductFacadeSettings,
    ReportSection,
)
from backend.product_facade.service import LocalProductFacadeService
from backend.product_facade.settings import validate_product_facade_settings


class ProductFacadeContractTests(unittest.TestCase):
    def binding(self):
        return DatasetRevisionBinding(
            asset_id=uuid.uuid4(),
            asset_revision_id=uuid.uuid4(),
            dataset_revision_id=uuid.uuid4(),
        )

    def request(self, **overrides):
        values = {
            "question": "Compare quarterly revenue by region.",
            "datasets": (self.binding(),),
            "intent": AnalysisIntent(
                operation="compare", measures=("revenue",), dimensions=("quarter",)
            ),
            "deliverables": AnalysisDeliverables(answer_depth="short", chart=True),
        }
        values.update(overrides)
        return DatasetAnalysisTaskRequestV1(**values)

    def test_dataset_analysis_contract_is_independent_and_strict(self):
        request = self.request()
        self.assertEqual(request.contract_version, "dataset-analysis-task-v1")
        self.assertEqual(request.intent.operation, "compare")
        for forbidden in ("principal", "scope", "sql", "tool_name", "authorized"):
            payload = request.model_dump()
            payload[forbidden] = "attacker-controlled"
            with self.subTest(forbidden=forbidden), self.assertRaises(ValidationError):
                DatasetAnalysisTaskRequestV1(**payload)

        with self.assertRaises(ValidationError):
            AgentTaskRequest(
                task="compare_quarter_sales",
                baseline_period="2025-Q1",
                comparison_period="2025-Q2",
                region="all",
                datasets=[],
            )

    def test_request_rejects_duplicate_revision_and_ambiguous_report_contract(self):
        binding = self.binding()
        with self.assertRaises(ValidationError):
            DatasetAnalysisTaskRequestV1(
                question="Profile the data.",
                datasets=(binding, binding),
                intent=AnalysisIntent(operation="profile"),
            )
        with self.assertRaises(ValidationError):
            AnalysisDeliverables(answer_depth="full_report", report=False)

    def test_centralized_clarification_collects_all_missing_requirements(self):
        request = self.request(
            intent=AnalysisIntent(operation="trend"),
            deliverables=AnalysisDeliverables(),
        )
        clarification = LocalProductFacadeService._clarification(request)
        self.assertIsNotNone(clarification)
        self.assertEqual(clarification.mode, "centralized")
        self.assertEqual(
            {question.code for question in clarification.questions},
            {"measure_required", "time_field_required"},
        )
        ready = self.request(intent=AnalysisIntent(operation="trend", measures=("revenue",), time_field="date"))
        self.assertIsNone(LocalProductFacadeService._clarification(ready))

    def test_chart_report_and_task_status_enforce_verified_result_shape(self):
        project_id = uuid.uuid4()
        source = AnalysisSourceReference(
            project_id=project_id,
            dataset_revision_id=uuid.uuid4(),
            asset_revision_id=uuid.uuid4(),
            table_index=1,
            row_start=2,
            row_end=4,
            columns=("revenue",),
            normalized_sha256="a" * 64,
        )
        chart = ChartSpecV1(
            chart_type="bar",
            title="Revenue",
            category_labels=("Q1", "Q2"),
            series=(
                ChartSeries(
                    label="Revenue",
                    values=("100.00", "120.00"),
                    calculation_ids=("revenue_by_quarter",),
                ),
            ),
            sources=(source,),
        )
        report = AnalysisReportV1(
            title="Quarterly comparison",
            summary="Revenue increased.",
            sections=(ReportSection(heading="Result", body="Verified calculation.", source_indexes=(0,)),),
            sources=(source,),
        )
        self.assertEqual((chart.chart_version, report.report_version), ("verified-chart-v1", "dataset-analysis-report-v1"))
        with self.assertRaises(ValidationError):
            ChartSpecV1(
                chart_type="bar",
                title="Bad",
                category_labels=("Q1", "Q2"),
                series=(ChartSeries(label="Revenue", values=("100",), calculation_ids=("x",)),),
                sources=(source,),
            )
        with self.assertRaises(ValidationError):
            DatasetAnalysisTaskStatusV1(
                task_id=uuid.uuid4(),
                project_id=project_id,
                status="completed",
                state_version=1,
                updated_at=datetime.now(UTC),
            )

    def test_local_settings_are_default_off_and_reject_remote_origin(self):
        settings = validate_product_facade_settings(ProductFacadeSettings())
        self.assertFalse(settings.enabled)
        with self.assertRaisesRegex(ValueError, "loopback"):
            validate_product_facade_settings(
                ProductFacadeSettings(allowed_origin="https://product.example.com")
            )

    def test_facade_is_independent_and_existing_apps_publish_no_product_routes(self):
        from backend.local_product_facade_app import app as product_app
        from backend.main import app as main_app
        from backend.rag_app import app as rag_app
        from backend.workspace_bridge_app import app as bridge_app

        product_paths = {route.path for route in product_app.routes if hasattr(route, "path")}
        self.assertIn("/auth-api/session", product_paths)
        self.assertIn("/product-api/workspace", product_paths)
        for existing in (main_app, rag_app, bridge_app):
            paths = {route.path for route in existing.routes if hasattr(route, "path")}
            self.assertFalse(any(path.startswith("/product-api/") for path in paths))
        env_example = (Path(__file__).parents[1] / ".env.example").read_text()
        self.assertIn("PRODUCT_FACADE_ENABLED=false", env_example)


if __name__ == "__main__":
    unittest.main()
