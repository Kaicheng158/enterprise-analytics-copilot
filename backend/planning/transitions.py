"""Deterministic guards for step and agent state transitions."""

from __future__ import annotations

from .models import AgentState, StepExecutionState


class StateTransitionError(ValueError):
    """Raised when an executor attempts an illegal state transition."""


STEP_TRANSITIONS = {
    "pending": frozenset({"running", "failed", "skipped"}),
    "running": frozenset({"completed", "failed"}),
    "completed": frozenset(),
    "failed": frozenset(),
    "skipped": frozenset(),
}

AGENT_TRANSITIONS = {
    "pending": frozenset({"running", "failed"}),
    "running": frozenset({"running", "completed", "failed"}),
    "completed": frozenset(),
    "failed": frozenset(),
}


def validate_step_transition(
    previous: StepExecutionState,
    current: StepExecutionState,
) -> None:
    if previous.step_id != current.step_id or previous.kind != current.kind:
        raise StateTransitionError("step identity cannot change")
    if current.status not in STEP_TRANSITIONS[previous.status]:
        raise StateTransitionError(
            f"illegal step transition: {previous.status}->{current.status}"
        )
    if current.attempts < previous.attempts:
        raise StateTransitionError("step attempts cannot decrease")
    if previous.status == "pending" and current.status == "running":
        if previous.attempts != 0 or current.attempts != 1:
            raise StateTransitionError("running transition must start first attempt")
    elif current.attempts != previous.attempts:
        raise StateTransitionError("attempt count may only change when work starts")
    if current.latency_ms < previous.latency_ms:
        raise StateTransitionError("step latency cannot decrease")


def validate_agent_transition(previous: AgentState, current: AgentState) -> None:
    if previous == current:
        raise StateTransitionError("agent transition cannot be a no-op")
    if previous.plan_id != current.plan_id:
        raise StateTransitionError("plan identity cannot change")
    if current.status not in AGENT_TRANSITIONS[previous.status]:
        raise StateTransitionError(
            f"illegal agent transition: {previous.status}->{current.status}"
        )
    if current.elapsed_ms < previous.elapsed_ms:
        raise StateTransitionError("elapsed time cannot decrease")
    if current.steps_executed < previous.steps_executed:
        raise StateTransitionError("completed step count cannot decrease")
    if current.tool_calls_used < previous.tool_calls_used:
        raise StateTransitionError("tool call count cannot decrease")
    if current.budget_units_used < previous.budget_units_used:
        raise StateTransitionError("budget usage cannot decrease")
    if current.completed_step_ids[: len(previous.completed_step_ids)] != (
        previous.completed_step_ids
    ):
        raise StateTransitionError("completed steps must be append-only")

    previous_steps = tuple(
        (step.step_id, step.kind) for step in previous.step_states
    )
    current_steps = tuple((step.step_id, step.kind) for step in current.step_states)
    if previous_steps != current_steps:
        raise StateTransitionError("agent step identities cannot change")
    for old_step, new_step in zip(previous.step_states, current.step_states):
        if old_step == new_step:
            continue
        validate_step_transition(old_step, new_step)
