import io
import json
import os
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from backend.tools.deepseek_adapter import DeepSeekToolSettings
from eval.tools.v3.run_live_v3 import (
    LIVE_CONFIRMATION,
    LIVE_ENV,
    _authorize_live,
    _contains_sensitive_value,
    _load_plan,
    _load_controls_v3,
    _run_case,
    _numeric_parser_preflight,
    _tool_permission_preflight,
    _verify_historical_protection,
    _should_stop_before_case,
    _write_json,
)
from eval.tools.v3.suite_v3 import load_cases_v3, suite_sha256_v3


def api_response(message, finish_reason, prompt=100, completion=20):
    return io.BytesIO(
        json.dumps(
            {
                "model": "deepseek-flash",
                "choices": [
                    {
                        "message": message,
                        "finish_reason": finish_reason,
                    }
                ],
                "usage": {
                    "prompt_tokens": prompt,
                    "completion_tokens": completion,
                    "total_tokens": prompt + completion,
                    "prompt_cache_hit_tokens": 0,
                    "prompt_cache_miss_tokens": prompt,
                },
            }
        ).encode()
    )


def tool_response(name, arguments):
    return api_response(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_v3_runner_001",
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(arguments),
                    },
                }
            ],
        },
        "tool_calls",
    )


def final_response(text):
    return api_response(
        {"role": "assistant", "content": text},
        "stop",
        prompt=150,
        completion=30,
    )


class ToolCallingLiveRunnerV3Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = {case["id"]: case for case in load_cases_v3()["cases"]}

    def settings(self):
        return DeepSeekToolSettings(
            enabled=True,
            api_key="mock-only-key",
            tool_choice="auto",
            temperature=0.2,
            thinking="disabled",
            max_output_tokens=512,
        )

    def test_plan_is_hash_bound_to_frozen_v3_and_fixed_limits(self):
        plan = _load_plan()
        self.assertEqual(plan["suite_sha256"], suite_sha256_v3())
        self.assertEqual(plan["rounds"], 3)
        self.assertEqual(plan["live_cases_per_round"], 9)
        self.assertEqual(plan["maximum_provider_interactions_total"], 54)
        self.assertEqual(plan["automatic_retries"], 0)
        self.assertEqual(plan["fixed_config"]["model"], "deepseek-flash")
        self.assertLess(
            Decimal(plan["conservative_estimated_cost"]),
            Decimal(plan["stop_budget"]),
        )

    def test_paid_mode_requires_both_server_environment_and_exact_confirmation(self):
        plan = _load_plan()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(LIVE_ENV, None)
            with self.assertRaises(PermissionError):
                _authorize_live(plan, LIVE_CONFIRMATION)
        with patch.dict(os.environ, {LIVE_ENV: "true"}):
            with self.assertRaises(PermissionError):
                _authorize_live(plan, "wrong")
            _authorize_live(plan, LIVE_CONFIRMATION)

    def test_budget_reserve_stops_before_exceeding_limit(self):
        plan = _load_plan()
        self.assertFalse(_should_stop_before_case(Decimal("0"), plan))
        self.assertTrue(_should_stop_before_case(Decimal("0.0198"), plan))

    def test_evidence_write_is_atomic_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "evidence.json"
            _write_json(target, {"status": "PASS"})
            self.assertEqual(json.loads(target.read_text()), {"status": "PASS"})
            with self.assertRaises(FileExistsError):
                _write_json(target, {"status": "changed"})
            self.assertEqual(json.loads(target.read_text()), {"status": "PASS"})

    def test_redaction_guard_detects_secrets_but_allows_authorization_verdict(self):
        self.assertFalse(
            _contains_sensitive_value({"authorization": "allowed", "status": "PASS"})
        )
        self.assertTrue(_contains_sensitive_value({"api_key": "private"}))
        self.assertTrue(
            _contains_sensitive_value({"message": "hidden-value"}, "hidden-value")
        )


    def test_preflight_controls_parser_permissions_and_historical_protection(self):
        numeric = _numeric_parser_preflight()
        self.assertEqual(set(numeric["negative_controls"]), {
            "wrong_amount", "missing_metric", "fabricated_extra",
        })
        self.assertTrue(all(
            item["verdict"] == "FAIL"
            for item in numeric["negative_controls"].values()
        ))
        permissions = _tool_permission_preflight()
        self.assertEqual(permissions["production_visible_tools"], ["get_postgres_sales_summary"])
        self.assertEqual(permissions["evaluation_visible_tools"], ["get_sales_summary"])
        self.assertEqual(permissions["untrusted_visible_tools"], [])
        protection = _verify_historical_protection()
        self.assertEqual(protection["protected_files"], 74)
        self.assertEqual(protection["changed_files"], [])
        controls = _load_controls_v3()
        self.assertEqual(len(controls), 7)
        self.assertTrue(all(item.verdict == "PASS" for item in controls))

    def test_mock_live_case_uses_only_in_memory_profile_and_passes_v3(self):
        case = self.cases["selection/in_memory_sales_summary"]
        responses = [
            tool_response(
                "get_sales_summary", {"period": "2026-Q1", "region": "all"}
            ),
            final_response(
                "Q1 total sales: USD 400.00; order count: 4; "
                "average order value: USD 100.00."
            ),
        ]
        with patch("backend.tools.deepseek_adapter.urlopen", side_effect=responses) as mocked:
            result, record, reviewed = _run_case(
                "mock-live-v3", case, self.settings()
            )
        first_payload = json.loads(mocked.call_args_list[0].args[0].data)
        self.assertEqual(
            [tool["function"]["name"] for tool in first_payload["tools"]],
            ["get_sales_summary"],
        )
        self.assertEqual(result.tool_result.tool_name, "get_sales_summary")
        self.assertEqual(record.telemetry.selected_tool, "get_sales_summary")
        self.assertEqual(
            record.telemetry.generation_config["tool_exposure_profile"],
            "evaluation_in_memory",
        )
        self.assertEqual(reviewed.verdict, "PASS")

    def test_wrong_source_attempt_is_recorded_even_when_server_blocks_execution(self):
        case = self.cases["grounding/tool_result"]
        response = tool_response(
            "get_sales_summary", {"period": "2026-Q2", "region": "north"}
        )
        with patch("backend.tools.deepseek_adapter.urlopen", return_value=response) as mocked:
            result, record, reviewed = _run_case(
                "mock-source-fail-v3", case, self.settings()
            )
        payload = json.loads(mocked.call_args.args[0].data)
        self.assertEqual(
            [tool["function"]["name"] for tool in payload["tools"]],
            ["get_postgres_sales_summary"],
        )
        self.assertEqual(record.telemetry.selected_tool, "get_sales_summary")
        self.assertEqual(record.telemetry.authorization, "allowed")
        self.assertEqual(result.tool_calls_executed, 0)
        self.assertFalse(reviewed.decision.hard_invariants["tool_source_policy"])
        self.assertEqual(reviewed.verdict, "FAIL")


if __name__ == "__main__":
    unittest.main()
