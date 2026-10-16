import json
import unittest
from decimal import Decimal
from unittest.mock import patch

from pydantic import ValidationError

from backend.tools.catalog import INTERNAL_PRINCIPAL, build_tool_executor, build_tool_registry
from backend.tools.models import ToolCall, ToolError, ToolExecutionContext, ToolExecutionResult
from backend.tools.registry import ToolAuthorizationPolicy, ToolDefinition, ToolRegistry
from backend.tools.sales import SalesSummaryInput, SalesSummaryOutput, get_sales_summary


class ToolCallingFoundationTests(unittest.TestCase):
    def setUp(self):
        self.executor = build_tool_executor()
        self.context = ToolExecutionContext(
            principal=INTERNAL_PRINCIPAL,
            correlation_id="offline-test-001",
        )

    def execute(self, period="2026-Q1", region="all"):
        return self.executor.execute(
            ToolCall(name="get_sales_summary", arguments={"period": period, "region": region}),
            self.context,
        )

    def test_sales_summary_is_exact_and_deterministic(self):
        first = self.execute()
        second = self.execute()
        self.assertEqual(first.status, "success")
        self.assertEqual(first.output, second.output)
        self.assertEqual(first.output["order_count"], 4)
        self.assertEqual(first.output["total_sales"], "400.00")
        self.assertEqual(first.output["average_order_value"], "100.00")
        self.assertEqual(first.output["data_version"], "synthetic-sales-v1")
        north = self.execute("2026-Q2", "north")
        self.assertEqual((north.output["order_count"], north.output["total_sales"]), (2, "300.00"))

    def test_direct_handler_output_is_strict(self):
        output = get_sales_summary(SalesSummaryInput(period="2026-Q2", region="south"))
        self.assertIsInstance(output, SalesSummaryOutput)
        self.assertEqual(output.total_sales, Decimal("300.00"))
        self.assertEqual(output.average_order_value, Decimal("150.00"))

    def test_models_reject_extra_fields_and_coercion(self):
        invalid = [
            {"name": "get_sales_summary", "arguments": {}, "extra": True},
            {"name": 1, "arguments": {}},
            {"name": "get_sales_summary", "arguments": "{}"},
        ]
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                ToolCall.model_validate(payload)
        for arguments in [
            {"period": "2026-Q3", "region": "all"},
            {"period": "2026-Q1", "region": "east"},
            {"period": "2026-Q1", "region": "all", "sql": "SELECT 1"},
        ]:
            result = self.executor.execute(
                ToolCall(name="get_sales_summary", arguments=arguments), self.context
            )
            self.assertEqual(result.error.code, "tool_input_invalid")

    def test_unregistered_and_unauthorized_tools_fail_closed(self):
        missing = self.executor.execute(
            ToolCall(name="run_sql", arguments={"query": "SELECT 1"}), self.context
        )
        self.assertEqual(missing.error.code, "tool_not_found")
        denied = self.executor.execute(
            ToolCall(name="get_sales_summary", arguments={"period": "2026-Q1"}),
            ToolExecutionContext(principal="untrusted-caller", correlation_id="denied-001"),
        )
        self.assertEqual(denied.error.code, "tool_not_authorized")
        self.assertIsNone(denied.output)

    def test_output_validation_and_handler_exception_are_sanitized(self):
        def invalid_output(_):
            return {"secret": "must-not-leak"}

        def failing_handler(_):
            raise RuntimeError("private failure detail")

        for handler, code in [
            (invalid_output, "tool_output_invalid"),
            (failing_handler, "tool_execution_failed"),
        ]:
            definition = ToolDefinition(
                "fixture", "1.0.0", "Offline fixture", SalesSummaryInput,
                SalesSummaryOutput, handler,
            )
            executor = type(self.executor)(
                ToolRegistry(
                    (definition,),
                    ToolAuthorizationPolicy({INTERNAL_PRINCIPAL: frozenset({"fixture"})}),
                )
            )
            with self.assertLogs("uvicorn.error", level="INFO") as logs:
                result = executor.execute(
                    ToolCall(name="fixture", arguments={"period": "2026-Q1", "region": "all"}),
                    self.context,
                )
            self.assertEqual(result.error.code, code)
            self.assertNotIn("private", str(logs.output).lower())
            self.assertNotIn("must-not-leak", str(logs.output))

    def test_logs_contain_metadata_but_no_arguments_or_output(self):
        with self.assertLogs("uvicorn.error", level="INFO") as logs:
            result = self.execute()
        record = json.loads(logs.records[-1].getMessage().split(" ", 1)[1])
        self.assertEqual(record["call_id"], result.call_id)
        self.assertEqual(record["tool_name"], "get_sales_summary")
        self.assertEqual(record["status"], "success")
        self.assertNotIn("arguments", record)
        self.assertNotIn("output", record)
        self.assertNotIn("400.00", str(record))

    def test_registry_exposes_only_authorized_strict_schemas(self):
        registry = build_tool_registry()
        self.assertEqual(registry.schemas_for("untrusted-caller"), ())
        schemas = registry.schemas_for(INTERNAL_PRINCIPAL)
        self.assertEqual(len(schemas), 1)
        self.assertEqual(schemas[0]["name"], "get_sales_summary")
        self.assertFalse(schemas[0]["input_schema"]["additionalProperties"])
        self.assertFalse(schemas[0]["output_schema"]["additionalProperties"])

    def test_duplicate_or_unknown_policy_registration_rejected(self):
        definition = ToolDefinition(
            "get_sales_summary", "1.0.0", "Synthetic fixture",
            SalesSummaryInput, SalesSummaryOutput, get_sales_summary,
        )
        with self.assertRaises(ValueError):
            ToolRegistry((definition, definition), ToolAuthorizationPolicy({}))
        with self.assertRaises(ValueError):
            ToolRegistry((definition,), ToolAuthorizationPolicy({"x": frozenset({"run_sql"})}))

    def test_context_is_strict_and_correlation_id_is_safe(self):
        with self.assertRaises(ValidationError):
            ToolExecutionContext(principal="analytics-copilot", correlation_id="has space")
        with self.assertRaises(ValidationError):
            ToolExecutionContext.model_validate(
                {"principal": "analytics-copilot", "correlation_id": "x", "admin": True}
            )

    def test_result_envelope_cannot_claim_inconsistent_state(self):
        base = {
            "call_id": "call",
            "tool_name": "get_sales_summary",
            "tool_version": "1.0.0",
            "latency_ms": 0,
        }
        error = ToolError(code="tool_execution_failed", message="Tool execution failed")
        for fields in [
            {"status": "success", "output": None, "error": None},
            {"status": "success", "output": {}, "error": error},
            {"status": "error", "output": {}, "error": error},
            {"status": "error", "output": None, "error": None},
        ]:
            with self.subTest(fields=fields), self.assertRaises(ValidationError):
                ToolExecutionResult(**base, **fields)

    def test_foundation_adds_no_public_route(self):
        from backend.main import app as chat_app
        from backend.rag_app import app as rag_app

        for app in (chat_app, rag_app):
            paths = {route.path for route in app.routes if hasattr(route, "path")}
            self.assertFalse(any("tool" in path for path in paths))
            self.assertIn("/chat", paths)


if __name__ == "__main__":
    unittest.main()
