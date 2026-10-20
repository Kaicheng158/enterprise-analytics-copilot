"""Deterministic, side-effect-free validation for bounded execution plans."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json

from .models import (
    Plan,
    PlanValidationResult,
    PlanningError,
    PlanningLimits,
)


VALIDATION_MESSAGES = {
    "step_limit_exceeded": "Plan step limit exceeded",
    "tool_call_limit_exceeded": "Plan tool call limit exceeded",
    "budget_limit_exceeded": "Plan execution budget exceeded",
    "action_not_authorized": "Plan action is not authorized",
    "plan_dependency_unknown": "Plan references an unknown dependency",
    "plan_cycle_detected": "Plan dependency graph contains a cycle",
    "plan_dependency_order_invalid": "Plan dependencies must precede their step",
    "plan_action_mismatch": "Plan actions do not match the requested task scope",
}


@dataclass(frozen=True)
class PlanningPolicy:
    allowed_tools: frozenset[str] = frozenset({"get_postgres_sales_summary"})
    allowed_computations: frozenset[str] = frozenset({"compare_sales_growth"})


def canonical_plan_sha256(plan: Plan) -> str:
    payload = json.dumps(
        plan.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(payload).hexdigest()


class PlanValidator:
    """Validate limits, graph structure, authorization and task/action binding."""

    def __init__(
        self,
        *,
        limits: PlanningLimits | None = None,
        policy: PlanningPolicy | None = None,
    ):
        self._limits = limits or PlanningLimits()
        self._policy = policy or PlanningPolicy()

    def validate(self, plan: Plan) -> PlanValidationResult:
        plan_hash = canonical_plan_sha256(plan)
        tool_steps = [step for step in plan.steps if step.kind == "tool"]
        if len(plan.steps) > self._limits.max_steps:
            return self._invalid(plan_hash, "step_limit_exceeded")
        if len(tool_steps) > self._limits.max_tool_calls:
            return self._invalid(plan_hash, "tool_call_limit_exceeded")
        if len(plan.steps) > self._limits.max_budget_units:
            return self._invalid(plan_hash, "budget_limit_exceeded")

        step_ids = tuple(step.step_id for step in plan.steps)
        known_ids = set(step_ids)
        if any(
            dependency not in known_ids
            for step in plan.steps
            for dependency in step.depends_on
        ):
            return self._invalid(plan_hash, "plan_dependency_unknown")

        topological_order = self._topological_order(plan)
        if topological_order is None:
            return self._invalid(plan_hash, "plan_cycle_detected")
        if topological_order != step_ids:
            return self._invalid(plan_hash, "plan_dependency_order_invalid")

        for step in plan.steps:
            if (
                step.kind == "tool"
                and step.tool_call.name not in self._policy.allowed_tools
            ):
                return self._invalid(plan_hash, "action_not_authorized")
            if (
                step.kind == "compute"
                and step.computation.operation
                not in self._policy.allowed_computations
            ):
                return self._invalid(plan_hash, "action_not_authorized")

        if not self._actions_match_sales_comparison(plan):
            return self._invalid(plan_hash, "plan_action_mismatch")

        return PlanValidationResult(
            status="valid",
            plan_sha256=plan_hash,
            execution_order=step_ids,
            error=None,
        )

    @staticmethod
    def _topological_order(plan: Plan) -> tuple[str, ...] | None:
        order_index = {step.step_id: index for index, step in enumerate(plan.steps)}
        indegree = {step.step_id: len(step.depends_on) for step in plan.steps}
        dependents: dict[str, list[str]] = {step.step_id: [] for step in plan.steps}
        for step in plan.steps:
            for dependency in step.depends_on:
                dependents[dependency].append(step.step_id)
        ready = sorted(
            (step_id for step_id, degree in indegree.items() if degree == 0),
            key=order_index.__getitem__,
        )
        result: list[str] = []
        while ready:
            current = ready.pop(0)
            result.append(current)
            for dependent in sorted(
                dependents[current], key=order_index.__getitem__
            ):
                indegree[dependent] -= 1
                if indegree[dependent] == 0:
                    ready.append(dependent)
                    ready.sort(key=order_index.__getitem__)
        return tuple(result) if len(result) == len(plan.steps) else None

    @staticmethod
    def _actions_match_sales_comparison(plan: Plan) -> bool:
        computation = plan.steps[-1].computation
        steps = {step.step_id: step for step in plan.steps}
        expected_tool_ids = {
            computation.baseline_step_id,
            computation.comparison_step_id,
        }
        actual_tool_ids = {
            step.step_id for step in plan.steps if step.kind == "tool"
        }
        if actual_tool_ids != expected_tool_ids:
            return False
        expected = {
            computation.baseline_step_id: {
                "period": computation.baseline_period,
                "region": computation.region,
            },
            computation.comparison_step_id: {
                "period": computation.comparison_period,
                "region": computation.region,
            },
        }
        return all(
            steps[step_id].kind == "tool"
            and steps[step_id].tool_call.name == "get_postgres_sales_summary"
            and steps[step_id].tool_call.arguments == arguments
            for step_id, arguments in expected.items()
        )

    @staticmethod
    def _invalid(plan_hash: str, code: str) -> PlanValidationResult:
        return PlanValidationResult(
            status="invalid",
            plan_sha256=plan_hash,
            execution_order=(),
            error=PlanningError(code=code, message=VALIDATION_MESSAGES[code]),
        )
