import io
import json
import unittest
from collections import deque
from unittest.mock import Mock, patch

from backend.tools.catalog import INTERNAL_PRINCIPAL, build_tool_registry
from backend.tools.deepseek_adapter import DeepSeekToolSettings
from backend.tools.deepseek_runtime import build_deepseek_tool_runtime
from backend.tools.executor import ToolExecutor
from backend.tools.exposed_catalog import (
    build_exposed_tool_orchestrator,
    build_exposed_tool_registry,
)
from backend.tools.exposure import HIDDEN_TOOL_RESPONSE, ToolExposurePolicy, ToolExposureRegistry
from backend.tools.models import ToolCall, ToolExecutionContext
from backend.tools.orchestration import ToolOrchestrationInput
from eval.tools.suite import verify_freeze
from eval.tools.v2.exposure_binding_v2 import (
    SOURCE_BINDING_VERSION,
    exposure_metadata_for_case,
    exposure_profile_for_case,
)
from eval.tools.v2.suite_v2 import load_cases_v2, verify_freeze_v2


class ScriptedProvider:
    def __init__(self, *responses):
        self.responses = deque(responses)
        self.requests = []

    def decide(self, request):
        self.requests.append(request)
        return self.responses.popleft()


def tool_decision(name, arguments):
    return json.dumps(
        {
            "action": "tool_call",
            "final_response": None,
            "tool_call": {"name": name, "arguments": arguments},
        }
    )


def final_decision(message):
    return json.dumps(
        {"action": "final", "final_response": message, "tool_call": None}
    )


class ToolExposurePolicyTests(unittest.TestCase):
    def setUp(self):
        self.context = ToolExecutionContext(
            principal=INTERNAL_PRINCIPAL,
            correlation_id="tool-exposure-test",
        )
        self.request = ToolOrchestrationInput(
            user_message="Report the Q2 north sales summary."
        )

    def test_production_and_evaluation_profiles_expose_one_authoritative_source(self):
        production = build_exposed_tool_registry()
        evaluation = build_exposed_tool_registry(
            exposure_profile="evaluation_in_memory"
        )
        self.assertEqual(
            [schema["name"] for schema in production.schemas_for(INTERNAL_PRINCIPAL)],
            ["get_postgres_sales_summary"],
        )
        self.assertEqual(
            [schema["name"] for schema in evaluation.schemas_for(INTERNAL_PRINCIPAL)],
            ["get_sales_summary"],
        )
        self.assertEqual(production.schemas_for("untrusted-caller"), ())

    def test_exposure_cannot_grant_an_unauthorized_or_unknown_tool(self):
        registry = build_tool_registry()
        for principal, tool_name in [
            ("untrusted-caller", "get_postgres_sales_summary"),
            (INTERNAL_PRINCIPAL, "run_sql"),
        ]:
            with self.subTest(principal=principal, tool_name=tool_name):
                with self.assertRaisesRegex(
                    ValueError, "unregistered or unauthorized"
                ):
                    ToolExposureRegistry(
                        registry,
                        ToolExposurePolicy(
                            grants={principal: frozenset({tool_name})}
                        ),
                    )

    def test_hidden_tool_guess_is_rejected_before_execution(self):
        provider = ScriptedProvider(
            tool_decision(
                "get_sales_summary", {"period": "2026-Q2", "region": "north"}
            )
        )
        connection_factory = Mock(side_effect=AssertionError("must not query"))
        result = build_exposed_tool_orchestrator(
            provider, connection_factory
        ).run(self.request, self.context)
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.final_response, HIDDEN_TOOL_RESPONSE)
        self.assertEqual(result.tool_calls_executed, 0)
        self.assertIsNone(result.tool_result)
        connection_factory.assert_not_called()
        self.assertEqual(
            [item["name"] for item in provider.requests[0].available_tools],
            ["get_postgres_sales_summary"],
        )

    def test_evaluation_profile_can_run_local_fixture_without_database(self):
        provider = ScriptedProvider(
            tool_decision(
                "get_sales_summary", {"period": "2026-Q2", "region": "north"}
            ),
            final_decision("Q2 north fixture sales were USD 300.00."),
        )
        connection_factory = Mock(side_effect=AssertionError("must not query"))
        result = build_exposed_tool_orchestrator(
            provider,
            connection_factory,
            exposure_profile="evaluation_in_memory",
        ).run(self.request, self.context)
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.tool_result.tool_name, "get_sales_summary")
        self.assertEqual(result.tool_result.output["total_sales"], "300.00")
        connection_factory.assert_not_called()

    def test_second_tool_call_still_reaches_existing_call_limit_check(self):
        provider = ScriptedProvider(
            tool_decision(
                "get_sales_summary", {"period": "2026-Q2", "region": "north"}
            ),
            tool_decision(
                "get_sales_summary", {"period": "2026-Q1", "region": "all"}
            ),
        )
        result = build_exposed_tool_orchestrator(
            provider, exposure_profile="evaluation_in_memory"
        ).run(self.request, self.context)
        self.assertEqual(result.status, "error")
        self.assertEqual(result.error.code, "tool_call_limit_exceeded")
        self.assertEqual(result.tool_calls_executed, 1)

    def test_executor_authorization_remains_independent_from_model_exposure(self):
        registry = build_exposed_tool_registry()
        executor = ToolExecutor(registry)
        hidden_but_authorized = executor.execute(
            ToolCall(
                name="get_sales_summary",
                arguments={"period": "2026-Q1", "region": "all"},
            ),
            self.context,
        )
        denied = executor.execute(
            ToolCall(
                name="get_sales_summary",
                arguments={"period": "2026-Q1", "region": "all"},
            ),
            ToolExecutionContext(
                principal="untrusted-caller",
                correlation_id="tool-exposure-denied",
            ),
        )
        self.assertEqual(hidden_but_authorized.status, "success")
        self.assertEqual(denied.error.code, "tool_not_authorized")

    def test_deepseek_runtime_sends_only_postgres_tool_in_production(self):
        settings = DeepSeekToolSettings(
            enabled=True,
            api_key="mock-only-key",
            tool_choice="auto",
        )
        orchestrator, _ = build_deepseek_tool_runtime(settings=settings)
        response = io.BytesIO(
            json.dumps(
                {
                    "model": "deepseek-flash",
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "No stored query is needed.",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 50,
                        "completion_tokens": 5,
                        "total_tokens": 55,
                        "prompt_cache_hit_tokens": 0,
                        "prompt_cache_miss_tokens": 50,
                    },
                }
            ).encode()
        )
        with patch("backend.tools.deepseek_adapter.urlopen", return_value=response) as mocked:
            result = orchestrator.run(self.request, self.context)
        self.assertEqual(result.status, "completed")
        payload = json.loads(mocked.call_args.args[0].data)
        self.assertEqual(
            [tool["function"]["name"] for tool in payload["tools"]],
            ["get_postgres_sales_summary"],
        )

    def test_mock_deepseek_cannot_execute_a_hidden_local_tool(self):
        settings = DeepSeekToolSettings(
            enabled=True,
            api_key="mock-only-key",
            tool_choice="auto",
        )
        connection_factory = Mock(side_effect=AssertionError("must not query"))
        orchestrator, _ = build_deepseek_tool_runtime(
            connection_factory=connection_factory,
            settings=settings,
        )
        response = io.BytesIO(
            json.dumps(
                {
                    "model": "deepseek-flash",
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call_hidden_local_001",
                                        "type": "function",
                                        "function": {
                                            "name": "get_sales_summary",
                                            "arguments": json.dumps(
                                                {
                                                    "period": "2026-Q2",
                                                    "region": "north",
                                                }
                                            ),
                                        },
                                    }
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 60,
                        "completion_tokens": 10,
                        "total_tokens": 70,
                        "prompt_cache_hit_tokens": 0,
                        "prompt_cache_miss_tokens": 60,
                    },
                }
            ).encode()
        )
        with patch("backend.tools.deepseek_adapter.urlopen", return_value=response) as mocked:
            result = orchestrator.run(self.request, self.context)
        payload = json.loads(mocked.call_args.args[0].data)
        self.assertEqual(
            [tool["function"]["name"] for tool in payload["tools"]],
            ["get_postgres_sales_summary"],
        )
        self.assertEqual(result.final_response, HIDDEN_TOOL_RESPONSE)
        self.assertEqual(result.tool_calls_executed, 0)
        connection_factory.assert_not_called()

    def test_frozen_v2_cases_bind_to_one_server_owned_source_profile(self):
        cases = load_cases_v2()["cases"]
        profiles = {case["id"]: exposure_profile_for_case(case) for case in cases}
        self.assertEqual(profiles["grounding/tool_result"], "production")
        self.assertEqual(
            profiles["selection/in_memory_sales_summary"],
            "evaluation_in_memory",
        )
        for case in cases:
            metadata = exposure_metadata_for_case(case)
            self.assertEqual(
                metadata["tool_source_binding_version"], SOURCE_BINDING_VERSION
            )
            self.assertEqual(len(metadata["visible_tools"]), 1)

    def test_historical_v1_and_v2_freezes_still_verify(self):
        self.assertEqual(
            verify_freeze()["suite_sha256"],
            "4f0c8dcfe75dae98d06deac25fff6a2ce1cb743a5f3d5f7d0bd1f2553ad20958",
        )
        self.assertEqual(
            verify_freeze_v2()["suite_sha256"],
            "a7830d318bc2a51aa7bb6b23ea36b06f63ac45dc0b1914a031f744d67cf7396a",
        )


if __name__ == "__main__":
    unittest.main()
