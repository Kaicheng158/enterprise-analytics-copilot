import io
import json
import os
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from backend.llm import ProviderError
from backend.tools.catalog import INTERNAL_PRINCIPAL, build_tool_orchestrator
from backend.tools.deepseek_adapter import (
    DeepSeekToolDecisionProvider,
    DeepSeekToolSettings,
    TOOL_PROMPT_SHA256,
    load_deepseek_tool_settings,
)
from backend.tools.deepseek_runtime import build_deepseek_tool_runtime
from backend.tools.models import ToolExecutionContext
from backend.tools.orchestration import ToolOrchestrationInput


def usage(prompt=100, completion=20, hit=40, miss=60):
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
        "prompt_cache_hit_tokens": hit,
        "prompt_cache_miss_tokens": miss,
    }


def api_response(message, finish_reason, token_usage=None):
    return io.BytesIO(
        json.dumps(
            {
                "id": "completion-fixture",
                "model": "deepseek-flash",
                "choices": [
                    {
                        "index": 0,
                        "message": message,
                        "finish_reason": finish_reason,
                    }
                ],
                "usage": token_usage or usage(),
            }
        ).encode()
    )


def tool_response(
    *,
    name="get_sales_summary",
    arguments='{"period":"2026-Q1","region":"all"}',
    call_id="call_official_001",
):
    return api_response(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": arguments},
                }
            ],
        },
        "tool_calls",
    )


def final_response(content="The analysis is complete.", token_usage=None):
    return api_response(
        {"role": "assistant", "content": content},
        "stop",
        token_usage or usage(150, 25, 100, 50),
    )


def enabled_settings(**overrides):
    return DeepSeekToolSettings(
        enabled=True,
        api_key="private-deepseek-key",
        tool_choice="required",
        **overrides,
    )


class DeepSeekToolAdapterTests(unittest.TestCase):
    def setUp(self):
        self.context = ToolExecutionContext(
            principal=INTERNAL_PRINCIPAL,
            correlation_id="deepseek-tool-mock-001",
        )
        self.request = ToolOrchestrationInput(
            user_message="Summarize Q1 sales for all regions."
        )

    def run_flow(self, responses, *, settings=None, context=None):
        provider = DeepSeekToolDecisionProvider(settings or enabled_settings())
        orchestrator = build_tool_orchestrator(provider)
        with patch(
            "backend.tools.deepseek_adapter.urlopen", side_effect=responses
        ) as mocked:
            result = orchestrator.run(self.request, context or self.context)
        return provider, result, mocked

    def test_official_tool_calls_and_tool_call_id_round_trip(self):
        provider, result, mocked = self.run_flow(
            [
                tool_response(),
                final_response(
                    "Q1 sales were USD 400.00 across 4 orders, averaging USD 100.00."
                ),
            ]
        )
        self.assertEqual(mocked.call_count, 2)
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.tool_result.status, "success")
        self.assertEqual(result.tool_result.output["total_sales"], "400.00")
        self.assertEqual(result.tool_calls_executed, 1)

        first = json.loads(mocked.call_args_list[0].args[0].data)
        self.assertEqual(first["model"], "deepseek-flash")
        self.assertEqual(first["thinking"], {"type": "disabled"})
        self.assertEqual(first["tool_choice"], "required")
        self.assertEqual(
            {tool["function"]["name"] for tool in first["tools"]},
            {"get_sales_summary", "get_postgres_sales_summary"},
        )
        self.assertEqual(first["messages"][0]["role"], "system")
        self.assertNotIn("private-deepseek-key", json.dumps(first))

        second = json.loads(mocked.call_args_list[1].args[0].data)
        self.assertEqual(
            [message["role"] for message in second["messages"]],
            ["system", "user", "assistant", "tool"],
        )
        assistant, tool = second["messages"][-2:]
        self.assertEqual(assistant["tool_calls"][0]["id"], "call_official_001")
        self.assertEqual(tool["tool_call_id"], "call_official_001")
        evidence = json.loads(tool["content"])
        self.assertEqual(evidence["authority"], "untrusted_data")
        self.assertEqual(evidence["result"]["output"]["total_sales"], "400.00")
        self.assertEqual(second["tool_choice"], "none")

        self.assertEqual(len(provider.native_tool_calls), 1)
        self.assertEqual(provider.native_tool_calls[0].tool_call_id, "call_official_001")
        summary = provider.telemetry_summary()
        self.assertEqual(summary.api_calls, 2)
        self.assertEqual(summary.input_tokens, 250)
        self.assertEqual(summary.output_tokens, 45)
        self.assertEqual(summary.total_tokens, 295)
        self.assertIsNotNone(summary.estimated_cost)
        self.assertTrue(summary.cost_complete)
        self.assertEqual(provider.telemetry[0].prompt_sha256, TOOL_PROMPT_SHA256)
        self.assertEqual(provider.telemetry[0].retry_count, 0)

    def test_no_tool_response_completes_in_one_api_call(self):
        provider, result, mocked = self.run_flow(
            [final_response("No tool is needed for this request.")],
            settings=DeepSeekToolSettings(
                enabled=True,
                api_key="private-deepseek-key",
                tool_choice="auto",
            ),
        )
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.tool_calls_executed, 0)
        self.assertEqual(result.final_response, "No tool is needed for this request.")
        self.assertEqual(provider.telemetry_summary().api_calls, 1)

    def test_server_can_force_one_authorized_tool_with_official_choice_shape(self):
        settings = enabled_settings(forced_tool_name="get_sales_summary")
        provider, _, mocked = self.run_flow(
            [tool_response(), final_response("Fixture response.")], settings=settings
        )
        body = json.loads(mocked.call_args_list[0].args[0].data)
        self.assertEqual(
            body["tool_choice"],
            {
                "type": "function",
                "function": {"name": "get_sales_summary"},
            },
        )
        self.assertEqual(provider.telemetry_summary().api_calls, 2)

    def test_required_tool_choice_cannot_silently_become_direct_answer(self):
        provider, result, mocked = self.run_flow(
            [final_response("Unexpected direct answer.")]
        )
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(result.status, "error")
        self.assertEqual(result.error.code, "tool_provider_failed")
        self.assertEqual(
            provider.telemetry[-1].error_code,
            "tool_provider_required_call_missing",
        )

    def test_invalid_arguments_and_multiple_calls_fail_closed(self):
        multiple = api_response(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "get_sales_summary",
                            "arguments": '{"period":"2026-Q1","region":"all"}',
                        },
                    },
                    {
                        "id": "call_2",
                        "type": "function",
                        "function": {
                            "name": "get_sales_summary",
                            "arguments": '{"period":"2026-Q2","region":"all"}',
                        },
                    },
                ],
            },
            "tool_calls",
        )
        for response, code in [
            (tool_response(arguments="not-json"), "tool_provider_invalid_arguments"),
            (tool_response(arguments='["not","an","object"]'), "tool_provider_invalid_arguments"),
            (multiple, "tool_provider_multiple_calls"),
        ]:
            with self.subTest(code=code):
                provider, result, mocked = self.run_flow([response])
                self.assertEqual(mocked.call_count, 1)
                self.assertEqual(result.status, "error")
                self.assertEqual(result.error.code, "tool_provider_failed")
                self.assertEqual(provider.telemetry[-1].status, "error")
                self.assertEqual(provider.telemetry[-1].error_code, code)
                self.assertIsNotNone(provider.telemetry[-1].usage)
                self.assertIsNotNone(provider.telemetry[-1].estimated_cost)

    def test_unknown_invalid_and_unauthorized_tools_are_executor_errors(self):
        scenarios = [
            (
                tool_response(name="run_sql", arguments='{"query":"SELECT 1"}'),
                self.context,
                "tool_not_found",
            ),
            (
                tool_response(arguments='{"period":"2026-Q1","region":"east"}'),
                self.context,
                "tool_input_invalid",
            ),
            (
                tool_response(),
                ToolExecutionContext(
                    principal="untrusted-caller",
                    correlation_id="deepseek-tool-denied",
                ),
                "tool_not_authorized",
            ),
        ]
        for response, context, expected in scenarios:
            with self.subTest(expected=expected):
                provider, result, mocked = self.run_flow(
                    [response, final_response("The tool request could not be completed.")],
                    context=context,
                )
                self.assertEqual(mocked.call_count, 2)
                self.assertEqual(result.status, "completed")
                self.assertEqual(result.tool_result.error.code, expected)
                self.assertIsNone(result.tool_result.output)

    def test_second_tool_call_is_rejected_after_two_api_calls(self):
        provider, result, mocked = self.run_flow(
            [tool_response(), tool_response(call_id="call_forbidden_002")]
        )
        self.assertEqual(mocked.call_count, 2)
        self.assertEqual(result.status, "error")
        self.assertEqual(result.error.code, "tool_call_limit_exceeded")
        self.assertEqual(result.tool_calls_executed, 1)
        self.assertEqual(provider.telemetry_summary().api_calls, 2)

    def test_timeout_and_http_failures_are_not_retried(self):
        failures = [
            (TimeoutError(), "tool_provider_timeout"),
            (
                HTTPError("https://api.deepseek.com", 401, "private", {}, None),
                "tool_provider_authentication_failed",
            ),
            (
                HTTPError("https://api.deepseek.com", 429, "private", {}, None),
                "tool_provider_rate_limited",
            ),
        ]
        for failure, code in failures:
            with self.subTest(code=code):
                provider, result, mocked = self.run_flow([failure])
                self.assertEqual(mocked.call_count, 1)
                self.assertEqual(result.status, "error")
                self.assertEqual(result.error.code, "tool_provider_failed")
                self.assertEqual(provider.telemetry[-1].error_code, code)
                self.assertEqual(provider.telemetry[-1].retry_count, 0)

    def test_malformed_provider_response_is_sanitized_without_retry(self):
        malformed = io.BytesIO(
            json.dumps(
                {
                    "model": "deepseek-flash",
                    "choices": [],
                    "usage": usage(),
                    "private_detail": "must-not-leak",
                }
            ).encode()
        )
        provider, result, mocked = self.run_flow([malformed])
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(result.status, "error")
        self.assertEqual(result.error.code, "tool_provider_failed")
        self.assertEqual(
            provider.telemetry[-1].error_code, "tool_provider_invalid_response"
        )
        self.assertNotIn("must-not-leak", result.model_dump_json())

    def test_safe_logs_exclude_key_user_arguments_output_and_final_text(self):
        marker = "private-user-and-final-marker"
        request = ToolOrchestrationInput(user_message=marker)
        provider = DeepSeekToolDecisionProvider(enabled_settings())
        orchestrator = build_tool_orchestrator(provider)
        with patch(
            "backend.tools.deepseek_adapter.urlopen",
            side_effect=[tool_response(), final_response(marker)],
        ), self.assertLogs("uvicorn.error", level="INFO") as logs:
            result = orchestrator.run(request, self.context)
        combined = " ".join(record.getMessage() for record in logs.records)
        self.assertEqual(result.final_response, marker)
        self.assertNotIn(marker, combined)
        self.assertNotIn("private-deepseek-key", combined)
        self.assertNotIn("2026-Q1", combined)
        self.assertNotIn("400.00", combined)

    def test_feature_flag_defaults_off_and_build_fails_before_network(self):
        with patch.dict(os.environ, {"TOOL_CALLING_ENABLED": "false"}), patch(
            "backend.tools.deepseek_adapter.urlopen"
        ) as network:
            settings = load_deepseek_tool_settings()
            self.assertFalse(settings.enabled)
            with self.assertRaisesRegex(ValueError, "tool_calling_disabled"):
                build_deepseek_tool_runtime(settings=settings)
            network.assert_not_called()

    def test_no_public_api_was_added(self):
        from backend.main import app as chat_app
        from backend.rag_app import app as rag_app

        for app in (chat_app, rag_app):
            paths = {route.path for route in app.routes if hasattr(route, "path")}
            self.assertFalse(any("tool" in path for path in paths))


if __name__ == "__main__":
    unittest.main()
