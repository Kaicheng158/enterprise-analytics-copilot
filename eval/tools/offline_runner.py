"""Deterministic Fake Provider helpers for Tool Calling evaluation tests."""

from __future__ import annotations

import json
from decimal import Decimal
from collections import deque
from typing import Any

from backend.tools.catalog import INTERNAL_PRINCIPAL, build_tool_orchestrator
from backend.tools.deepseek_adapter import TOOL_PROMPT_SHA256, TOOL_PROMPT_VERSION
from backend.tools.models import ToolExecutionContext
from backend.tools.orchestration import ToolOrchestrationInput, ToolProviderRequest
from eval.tools.suite import ToolEvalTelemetry, build_record, suite_sha256


class ScriptedToolDecisionProvider:
    """Evaluation-only provider. Decisions are fixed before execution."""

    def __init__(self, decisions: list[dict[str, Any] | str], *, fail_at: int | None = None):
        self._decisions = deque(decisions)
        self.fail_at = fail_at
        self.requests: list[ToolProviderRequest] = []

    def decide(self, request: ToolProviderRequest) -> str:
        self.requests.append(request)
        if self.fail_at == len(self.requests):
            raise TimeoutError("synthetic provider timeout")
        if not self._decisions:
            raise RuntimeError("no scripted decision")
        decision = self._decisions.popleft()
        return decision if isinstance(decision, str) else json.dumps(decision)


def tool_decision(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "action": "tool_call",
        "final_response": None,
        "tool_call": {"name": name, "arguments": arguments},
    }


def final_decision(response: str) -> dict[str, Any]:
    return {"action": "final", "final_response": response, "tool_call": None}


def run_fake_case(
    *,
    run_id: str,
    case_id: str,
    user_message: str,
    decisions: list[dict[str, Any] | str],
    principal: str = INTERNAL_PRINCIPAL,
    fail_at: int | None = None,
    connection_factory=None,
):
    provider = ScriptedToolDecisionProvider(decisions, fail_at=fail_at)
    kwargs = {} if connection_factory is None else {"connection_factory": connection_factory}
    result = build_tool_orchestrator(provider, **kwargs).run(
        ToolOrchestrationInput(user_message=user_message),
        ToolExecutionContext(principal=principal, correlation_id=run_id),
    )
    selected = (
        result.initial_decision.tool_call.name
        if result.initial_decision and result.initial_decision.tool_call
        else None
    )
    tool_result = result.tool_result
    arguments_valid = None
    authorization = "not_applicable"
    if result.initial_decision and result.initial_decision.tool_call:
        code = tool_result.error.code if tool_result and tool_result.error else None
        if code == "tool_input_invalid":
            arguments_valid = False
        elif code not in {"tool_not_found", "tool_not_authorized"}:
            arguments_valid = True
        authorization = (
            "denied" if code in {"tool_not_found", "tool_not_authorized"} else "allowed"
        )
    error_code = (
        result.error.code
        if result.error
        else tool_result.error.code if tool_result and tool_result.error else None
    )
    telemetry = ToolEvalTelemetry(
        provider="fake",
        model="scripted-tool-provider-v1",
        generation_config={"mode": "deterministic_script", "automatic_retries": 0},
        selected_tool=selected,
        arguments_valid=arguments_valid,
        authorization=authorization,
        execution_status=tool_result.status if tool_result else "not_executed",
        provider_interactions=result.provider_turns,
        tool_executions=result.tool_calls_executed,
        input_tokens=0,
        output_tokens=0,
        total_tokens=0,
        latency_ms=tool_result.latency_ms if tool_result else 0,
        estimated_cost="0",
        cost_complete=True,
        retry_count=0,
        error_code=error_code,
    )
    record = build_record(
        run_id=run_id,
        case_id=case_id,
        mode="offline_fake",
        suite_sha256=suite_sha256(),
        prompt_version=TOOL_PROMPT_VERSION,
        prompt_sha256=TOOL_PROMPT_SHA256,
        user_message=user_message,
        result=result.model_dump(mode="json"),
        telemetry=telemetry.model_dump(mode="json"),
    )
    return provider, result, record


def record_deepseek_mock_case(
    *, run_id: str, case_id: str, user_message: str, result, provider
):
    """Convert one mocked DeepSeek run into the same strict evidence contract."""

    selected = (
        result.initial_decision.tool_call.name
        if result.initial_decision and result.initial_decision.tool_call
        else None
    )
    tool_result = result.tool_result
    tool_code = tool_result.error.code if tool_result and tool_result.error else None
    arguments_valid = None
    authorization = "not_applicable"
    if selected:
        arguments_valid = False if tool_code == "tool_input_invalid" else (
            None if tool_code in {"tool_not_found", "tool_not_authorized"} else True
        )
        authorization = (
            "denied" if tool_code in {"tool_not_found", "tool_not_authorized"} else "allowed"
        )
    calls = provider.telemetry
    usages = [call.usage for call in calls if call.usage is not None]
    amounts = [call.estimated_cost for call in calls]
    cost_complete = bool(calls) and all(amount is not None for amount in amounts)
    estimated_cost = (
        format(sum(Decimal(amount) for amount in amounts), "f")
        if cost_complete else None
    )
    provider_error = next(
        (call.error_code for call in reversed(calls) if call.error_code), None
    )
    error_code = provider_error or (result.error.code if result.error else tool_code)
    telemetry = ToolEvalTelemetry(
        provider="deepseek",
        model=provider.settings.model,
        generation_config={
            "thinking": provider.settings.thinking,
            "temperature": provider.settings.temperature,
            "max_output_tokens": provider.settings.max_output_tokens,
            "max_provider_interactions": 2,
            "max_tool_executions": 1,
            "automatic_retries": 0,
        },
        selected_tool=selected,
        arguments_valid=arguments_valid,
        authorization=authorization,
        execution_status=tool_result.status if tool_result else "not_executed",
        provider_interactions=len(calls),
        tool_executions=result.tool_calls_executed,
        input_tokens=sum(usage.prompt_tokens for usage in usages),
        output_tokens=sum(usage.completion_tokens for usage in usages),
        total_tokens=sum(usage.total_tokens for usage in usages),
        latency_ms=sum(call.latency_ms for call in calls),
        estimated_cost=estimated_cost,
        cost_complete=cost_complete,
        retry_count=0,
        error_code=error_code,
    )
    return build_record(
        run_id=run_id,
        case_id=case_id,
        mode="mock_api",
        suite_sha256=suite_sha256(),
        prompt_version=TOOL_PROMPT_VERSION,
        prompt_sha256=TOOL_PROMPT_SHA256,
        user_message=user_message,
        result=result.model_dump(mode="json"),
        telemetry=telemetry,
    )
