import io
import json
import unittest
from unittest.mock import patch

from backend.tools.catalog import INTERNAL_PRINCIPAL, build_tool_orchestrator
from backend.tools.deepseek_adapter import DeepSeekToolDecisionProvider, DeepSeekToolSettings
from backend.tools.models import ToolExecutionContext
from backend.tools.orchestration import ToolOrchestrationInput
from eval.tools.offline_runner import record_deepseek_mock_case


def response(message, finish_reason, prompt_tokens, completion_tokens):
    return io.BytesIO(
        json.dumps(
            {
                "model": "deepseek-flash",
                "choices": [{"message": message, "finish_reason": finish_reason}],
                "usage": {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": prompt_tokens + completion_tokens,
                    "prompt_cache_hit_tokens": 0,
                    "prompt_cache_miss_tokens": prompt_tokens,
                },
            }
        ).encode("utf-8")
    )


class ToolCallingEvaluationMockApiTests(unittest.TestCase):
    def test_mock_api_run_records_redacted_usage_cost_and_execution(self):
        user_message = "Summarize the fixed synthetic Q1 sales fixture."
        provider = DeepSeekToolDecisionProvider(
            DeepSeekToolSettings(
                enabled=True,
                api_key="mock-secret-never-recorded",
                tool_choice="required",
            )
        )
        responses = [
            response(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_eval_mock_001",
                            "type": "function",
                            "function": {
                                "name": "get_sales_summary",
                                "arguments": json.dumps(
                                    {"period": "2026-Q1", "region": "all"}
                                ),
                            },
                        }
                    ],
                },
                "tool_calls",
                100,
                12,
            ),
            response(
                {
                    "role": "assistant",
                    "content": "Q1 sales were USD 400.00 across 4 orders.",
                },
                "stop",
                140,
                18,
            ),
        ]
        with patch("backend.tools.deepseek_adapter.urlopen", side_effect=responses):
            result = build_tool_orchestrator(provider).run(
                ToolOrchestrationInput(user_message=user_message),
                ToolExecutionContext(
                    principal=INTERNAL_PRINCIPAL,
                    correlation_id="tool-eval-mock-001",
                ),
            )
        record = record_deepseek_mock_case(
            run_id="tool-eval-mock-001",
            case_id="telemetry/token_cost",
            user_message=user_message,
            result=result,
            provider=provider,
        )
        self.assertEqual(record.mode, "mock_api")
        self.assertEqual(record.telemetry.provider, "deepseek")
        self.assertEqual(record.telemetry.selected_tool, "get_sales_summary")
        self.assertTrue(record.telemetry.arguments_valid)
        self.assertEqual(record.telemetry.execution_status, "success")
        self.assertEqual(record.telemetry.provider_interactions, 2)
        self.assertEqual(record.telemetry.tool_executions, 1)
        self.assertEqual(record.telemetry.total_tokens, 270)
        self.assertTrue(record.telemetry.cost_complete)
        self.assertIsNotNone(record.telemetry.estimated_cost)
        serialized = record.model_dump_json()
        self.assertNotIn("mock-secret-never-recorded", serialized)
        self.assertNotIn("Authorization", serialized)
