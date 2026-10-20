"""Shared cost and stop guard for future paid planner evaluation."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation


class PlannerSafetyStop(RuntimeError):
    pass


class PlannerCostGuard:
    def __init__(
        self,
        *,
        budget_usd: Decimal = Decimal("0.02"),
        per_call_reserve_usd: Decimal = Decimal("0.002"),
        max_calls: int = 9,
    ):
        if budget_usd <= 0 or per_call_reserve_usd <= 0:
            raise ValueError("planner cost limits must be positive")
        if max_calls < 1:
            raise ValueError("planner call limit must be positive")
        self.budget_usd = budget_usd
        self.per_call_reserve_usd = per_call_reserve_usd
        self.max_calls = max_calls
        self.calls_started = 0
        self.spent_usd = Decimal("0")
        self._pending = False
        self.stopped = False

    def begin_call(self) -> None:
        if self.stopped or self._pending:
            raise PlannerSafetyStop("planner_live_guard_stopped")
        if self.calls_started >= self.max_calls:
            self.stopped = True
            raise PlannerSafetyStop("planner_call_budget_exceeded")
        if self.spent_usd + self.per_call_reserve_usd > self.budget_usd:
            self.stopped = True
            raise PlannerSafetyStop("planner_cost_budget_exceeded")
        self.calls_started += 1
        self._pending = True

    def complete_call(self, estimated_cost_usd: str) -> None:
        if not self._pending:
            raise PlannerSafetyStop("planner_cost_evidence_missing")
        try:
            amount = Decimal(estimated_cost_usd)
        except (InvalidOperation, TypeError):
            self.stopped = True
            self._pending = False
            raise PlannerSafetyStop("planner_cost_evidence_invalid") from None
        self._pending = False
        if amount < 0:
            self.stopped = True
            raise PlannerSafetyStop("planner_cost_evidence_invalid")
        self.spent_usd += amount
        if amount > self.per_call_reserve_usd or self.spent_usd > self.budget_usd:
            self.stopped = True
            raise PlannerSafetyStop("planner_cost_budget_exceeded")

    def abort_call(self) -> None:
        self._pending = False
        self.stopped = True
