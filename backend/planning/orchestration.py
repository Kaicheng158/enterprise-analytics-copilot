"""One-provider-turn planning boundary followed by bounded local execution."""

from __future__ import annotations

from typing import Protocol

from pydantic import ValidationError

from backend.tools.models import ToolExecutionContext

from .executor import BoundedPlanExecutor
from .models import (
    Plan,
    PlanExecutionResult,
    PlanningError,
    PlanningRequest,
)


MAX_PLAN_BYTES = 32_768


class PlanProvider(Protocol):
    def propose(self, request: PlanningRequest) -> str: ...


class BoundedPlanningOrchestrator:
    def __init__(self, provider: PlanProvider, executor: BoundedPlanExecutor):
        self._provider = provider
        self._executor = executor

    def run(
        self,
        request: PlanningRequest,
        context: ToolExecutionContext,
    ) -> PlanExecutionResult:
        try:
            raw = self._provider.propose(request)
            if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_PLAN_BYTES:
                raise ValueError("plan_invalid")
            plan = Plan.model_validate_json(raw, strict=True)
        except (ValidationError, ValueError, TypeError, UnicodeError):
            return self._rejected("plan_invalid", "Provider plan is invalid")
        except Exception:
            return self._rejected(
                "plan_provider_error", "Planning provider request failed"
            )
        if (
            plan.task != request.task
            or plan.baseline_period != request.baseline_period
            or plan.comparison_period != request.comparison_period
            or plan.region != request.region
        ):
            return self._rejected(
                "plan_request_mismatch", "Provider plan does not match the request"
            )
        return self._executor.execute(plan, context, provider_calls=1)

    @staticmethod
    def _rejected(code, message):
        error = PlanningError(code=code, message=message)
        return PlanExecutionResult(
            status="failed",
            plan=None,
            state=None,
            state_history=(),
            output=None,
            error=error,
            provider_calls=1,
        )
