import json
import unittest
from decimal import Decimal
from urllib.error import URLError
from unittest.mock import Mock, patch
import os

from pydantic import SecretStr

from backend.planning.deepseek_planner_adapter import (
    DeepSeekPlannerSettings,
    DeepSeekStructuredPlannerTransport,
    load_deepseek_planner_settings,
)
from backend.planning.demo import build_quarter_sales_comparison_plan
from backend.planning.models import PlanningRequest
from backend.planning.planner_safety import PlannerCostGuard, PlannerSafetyStop
from backend.planning.structured_provider import (
    PlannerGenerationConfig,
    PlannerProviderError,
    build_planner_generation_request,
)


class FakeHttpResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8") if not isinstance(payload, bytes) else payload
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self, limit):
        return self.payload[:limit]


def response_payload(content, *, finish_reason="stop"):
    return {
        "id": "planner-mock-1",
        "model": "deepseek-flash",
        "choices": [{
            "index": 0,
            "finish_reason": finish_reason,
            "message": {"role": "assistant", "content": content},
        }],
        "usage": {"prompt_tokens": 500, "completion_tokens": 400, "total_tokens": 900},
    }


class DeepSeekPlannerAdapterTests(unittest.TestCase):
    def setUp(self):
        self.request = PlanningRequest()
        self.plan = build_quarter_sales_comparison_plan(self.request)
        self.generation = build_planner_generation_request(self.request)
        self.settings = DeepSeekPlannerSettings(
            enabled=True,
            api_key=SecretStr("test-only-key-not-real"),
        )

    def transport(self, opener, guard=None):
        return DeepSeekStructuredPlannerTransport(
            self.settings,
            cost_guard=guard or PlannerCostGuard(),
            opener=opener,
        )

    def test_mock_http_uses_json_mode_and_returns_safe_usage_cost(self):
        captured = {}
        def opener(request, timeout):
            captured["url"] = request.full_url
            captured["headers"] = dict(request.header_items())
            captured["payload"] = json.loads(request.data)
            captured["timeout"] = timeout
            return FakeHttpResponse(response_payload(self.plan.model_dump_json()))

        guard = PlannerCostGuard()
        result = self.transport(opener, guard).generate(self.generation)
        self.assertEqual(result.content, self.plan.model_dump_json())
        self.assertEqual(result.finish_reason, "stop")
        self.assertEqual(result.usage.total_tokens, 900)
        self.assertEqual(result.estimated_cost_usd, "0.00063")
        self.assertEqual(guard.calls_started, 1)
        self.assertEqual(guard.spent_usd, Decimal("0.00063"))
        self.assertEqual(captured["payload"]["response_format"], {"type": "json_object"})
        self.assertEqual(captured["payload"]["thinking"], {"type": "disabled"})
        self.assertEqual(captured["payload"]["max_tokens"], 800)
        self.assertNotIn("temperature", captured["payload"])
        self.assertNotIn("test-only-key-not-real", json.dumps(captured["payload"]))
        self.assertEqual(captured["timeout"], 30)

    def test_environment_loader_defaults_off_without_exposing_key(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-only-key"}, clear=False):
            os.environ.pop("LLM_PLANNER_ENABLED", None)
            settings = load_deepseek_planner_settings()
        self.assertFalse(settings.enabled)
        self.assertEqual(settings.api_key.get_secret_value(), "test-only-key")
        self.assertNotIn("test-only-key", repr(settings))

        with patch.dict(os.environ, {"LLM_PLANNER_ENABLED": "invalid"}):
            with self.assertRaisesRegex(ValueError, "Invalid LLM_PLANNER_ENABLED"):
                load_deepseek_planner_settings()

    def test_disabled_missing_key_and_generation_mismatch_fail_before_http(self):
        opener = Mock(side_effect=AssertionError("HTTP must not run"))
        with self.assertRaisesRegex(ValueError, "llm_planner_disabled"):
            DeepSeekStructuredPlannerTransport(
                DeepSeekPlannerSettings(enabled=False),
                cost_guard=PlannerCostGuard(), opener=opener,
            )
        with self.assertRaisesRegex(ValueError, "not_configured"):
            DeepSeekStructuredPlannerTransport(
                DeepSeekPlannerSettings(enabled=True),
                cost_guard=PlannerCostGuard(), opener=opener,
            )
        transport = self.transport(opener)
        mismatched = build_planner_generation_request(
            self.request,
            PlannerGenerationConfig(max_output_tokens=512),
        )
        with self.assertRaisesRegex(PlannerProviderError, "config_mismatch"):
            transport.generate(mismatched)
        opener.assert_not_called()

    def test_invalid_or_transport_response_stops_without_retry(self):
        for opener in (
            Mock(return_value=FakeHttpResponse(b"not-json")),
            Mock(side_effect=URLError("private network failure")),
        ):
            with self.subTest(opener=opener):
                guard = PlannerCostGuard()
                transport = self.transport(opener, guard)
                with self.assertRaises(PlannerProviderError):
                    transport.generate(self.generation)
                self.assertEqual(opener.call_count, 1)
                self.assertEqual(transport.calls, 1)
                self.assertTrue(guard.stopped)

    def test_budget_guard_stops_before_http_and_never_retries(self):
        opener = Mock(side_effect=AssertionError("HTTP must not run"))
        guard = PlannerCostGuard(
            budget_usd=Decimal("0.001"),
            per_call_reserve_usd=Decimal("0.002"),
        )
        transport = self.transport(opener, guard)
        with self.assertRaisesRegex(PlannerSafetyStop, "cost_budget"):
            transport.generate(self.generation)
        opener.assert_not_called()
        self.assertEqual(transport.calls, 0)
        self.assertTrue(guard.stopped)

    def test_shared_guard_enforces_total_call_limit(self):
        guard = PlannerCostGuard(max_calls=1)
        first = self.transport(
            Mock(return_value=FakeHttpResponse(response_payload(self.plan.model_dump_json()))),
            guard,
        )
        first.generate(self.generation)
        second_opener = Mock(side_effect=AssertionError("HTTP must not run"))
        second = self.transport(second_opener, guard)
        with self.assertRaisesRegex(PlannerSafetyStop, "call_budget"):
            second.generate(self.generation)
        second_opener.assert_not_called()


if __name__ == "__main__":
    unittest.main()
