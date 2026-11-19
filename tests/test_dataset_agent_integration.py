"""Offline PV2-04d Dataset Planner and validation acceptance."""

from __future__ import annotations

import json
from hashlib import sha256
import os
import unittest
from uuid import uuid4

from pydantic import ValidationError

from backend.datasets.analysis_models import (
    DatasetBindingContext,
    DatasetTableFieldCatalog,
)
from backend.planning.dataset_agent_runtime import load_dataset_agent_settings
from backend.planning.dataset_models import DatasetAgentRequest, DatasetPlan
from backend.planning.dataset_provider import DeterministicDatasetPlanProvider
from backend.planning.dataset_validation import DatasetPlanValidator
from backend.planning.durable_plan import parse_durable_plan_json
from backend.product_facade.models import DatasetRevisionBinding


class DatasetAgentIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.project_id = uuid4()
        self.binding = DatasetRevisionBinding(
            asset_id=uuid4(), asset_revision_id=uuid4(), dataset_revision_id=uuid4()
        )
        self.context = DatasetBindingContext(
            project_id=self.project_id,
            binding=self.binding,
            normalized_sha256="a" * 64,
            authorization_version=3,
            tables=(
                DatasetTableFieldCatalog(
                    table_index=1,
                    fields=("region", "period", "revenue", "cost", "note"),
                ),
            ),
        )

    def request(self, *, operations=None):
        return DatasetAgentRequest(
            project_id=self.project_id,
            binding=self.binding,
            operations=operations
            or (
                {
                    "operation_id": "regional_revenue",
                    "tool_name": "aggregate_dataset_revision",
                    "arguments": {
                        "group_by": ["region"],
                        "aggregations": [
                            {"result_id": "revenue", "function": "sum", "field": "revenue"}
                        ],
                        "sort": [{"key": "revenue", "direction": "descending"}],
                    },
                },
                {
                    "operation_id": "quality",
                    "tool_name": "inspect_dataset_quality",
                    "arguments": {"fields": ["revenue", "note"]},
                },
            ),
        )

    def valid_plan(self, request=None):
        request = request or self.request()
        provider = DeterministicDatasetPlanProvider()
        plan = DatasetPlan.model_validate_json(
            provider.propose(request, self.context), strict=True
        )
        return request, provider, plan

    def test_fake_provider_creates_valid_bounded_multi_step_plan(self):
        request, provider, plan = self.valid_plan()
        result = DatasetPlanValidator(settings=load_enabled_settings()).validate(
            plan, request, self.context
        )
        self.assertEqual(result.status, "valid")
        self.assertEqual(result.execution_order, ("analyze_1", "analyze_2", "assemble_results"))
        self.assertEqual(provider.calls, 1)
        self.assertEqual(plan.binding, self.binding)
        self.assertEqual(plan.normalized_sha256, "a" * 64)
        self.assertEqual(
            {step.tool_call.name for step in plan.steps if step.kind == "tool"},
            {"aggregate_dataset_revision", "inspect_dataset_quality"},
        )
        self.assertIsInstance(parse_durable_plan_json(plan.model_dump_json()), DatasetPlan)

    def test_illegal_tool_and_changed_binding_are_rejected(self):
        request, _, plan = self.valid_plan()
        payload = plan.model_dump(mode="json")
        payload["steps"][0]["tool_call"]["name"] = "get_postgres_sales_summary"
        unauthorized = DatasetPlan.model_validate_json(json.dumps(payload), strict=True)
        verdict = DatasetPlanValidator(settings=load_enabled_settings()).validate(
            unauthorized, request, self.context
        )
        self.assertEqual(verdict.status, "invalid")
        self.assertIn("action_not_authorized", {item.code for item in verdict.issues})

        payload = plan.model_dump(mode="json")
        payload["binding"]["asset_id"] = str(uuid4())
        payload["steps"][0]["tool_call"]["arguments"]["binding"]["asset_id"] = payload["binding"]["asset_id"]
        changed = DatasetPlan.model_validate_json(json.dumps(payload), strict=True)
        verdict = DatasetPlanValidator(settings=load_enabled_settings()).validate(
            changed, request, self.context
        )
        self.assertEqual(verdict.status, "invalid")
        self.assertIn("plan_request_mismatch", {item.code for item in verdict.issues})

    def test_wrong_fields_are_reported_together_without_persisting_a_plan(self):
        request = self.request(
            operations=(
                {
                    "operation_id": "bad_aggregate",
                    "tool_name": "aggregate_dataset_revision",
                    "arguments": {
                        "group_by": ["missing_dimension"],
                        "aggregations": [
                            {"result_id": "value", "function": "sum", "field": "missing_measure"}
                        ],
                    },
                },
                {
                    "operation_id": "bad_rank",
                    "tool_name": "rank_dataset_revision",
                    "arguments": {
                        "dimension": "missing_team",
                        "measure": "missing_score",
                    },
                },
            )
        )
        _, _, plan = self.valid_plan(request)
        verdict = DatasetPlanValidator(settings=load_enabled_settings()).validate(
            plan, request, self.context
        )
        self.assertEqual(verdict.status, "invalid")
        missing = {
            field
            for issue in verdict.issues
            if issue.code == "field_not_found"
            for field in issue.fields
        }
        self.assertEqual(
            missing,
            {"missing_dimension", "missing_measure", "missing_team", "missing_score"},
        )

    def test_request_contract_rejects_authority_and_arbitrary_code(self):
        for key, value in (
            ("binding", self.binding.model_dump(mode="json")),
            ("principal", "admin"),
            ("sql", "select 1"),
            ("python", "eval('1')"),
            ("trusted_local", True),
        ):
            with self.subTest(key=key), self.assertRaises(ValidationError):
                self.request(
                    operations=(
                        {
                            "operation_id": "blocked",
                            "tool_name": "profile_dataset_revision",
                            "arguments": {key: value},
                        },
                    )
                )

    def test_evaluation_suite_is_frozen_and_feature_flag_defaults_off(self):
        root = os.path.dirname(os.path.dirname(__file__))
        directory = os.path.join(root, "eval/datasets/agent-v1")
        with open(os.path.join(directory, "freeze-v1.json"), encoding="utf-8") as handle:
            freeze = json.load(handle)
        self.assertEqual(freeze["suite_version"], "dataset-agent-eval-v1")
        self.assertEqual(freeze["case_count"], 8)
        for filename, key in (("cases-v1.json", "cases_sha256"), ("policy-v1.json", "policy_sha256")):
            with open(os.path.join(directory, filename), "rb") as handle:
                self.assertEqual(sha256(handle.read()).hexdigest(), freeze[key])
        previous = os.environ.pop("DATASET_AGENT_ENABLED", None)
        try:
            self.assertFalse(load_dataset_agent_settings().enabled)
        finally:
            if previous is not None:
                os.environ["DATASET_AGENT_ENABLED"] = previous


def load_enabled_settings():
    from backend.planning.dataset_models import DatasetAgentSettings

    return DatasetAgentSettings(enabled=True)


if __name__ == "__main__":
    unittest.main()
