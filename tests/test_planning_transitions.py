import unittest

from backend.planning import (
    StateTransitionError,
    validate_agent_transition,
    validate_step_transition,
)
from backend.planning.models import AgentState, StepExecutionState


def tool_step(status, *, attempts, output=None, latency=0, call_id=None):
    return StepExecutionState(
        step_id="load_sales",
        kind="tool",
        status=status,
        attempts=attempts,
        output=output,
        latency_ms=latency,
        tool_call_id=call_id,
    )


def agent_state(status, step, *, elapsed, current=None):
    completed = (step.step_id,) if step.status == "completed" else ()
    return AgentState(
        plan_id="transition_test",
        status=status,
        current_step_id=current,
        step_states=(step,),
        completed_step_ids=completed,
        steps_executed=len(completed),
        tool_calls_used=step.attempts,
        budget_units_used=step.attempts,
        elapsed_ms=elapsed,
        error=None,
    )


class PlanningTransitionTests(unittest.TestCase):
    def setUp(self):
        self.pending_step = tool_step("pending", attempts=0)
        self.running_step = tool_step("running", attempts=1)
        self.completed_step = tool_step(
            "completed",
            attempts=1,
            output={"total_sales": "400.00"},
            latency=2,
            call_id="call-1",
        )

    def test_valid_step_and_agent_lifecycle(self):
        validate_step_transition(self.pending_step, self.running_step)
        validate_step_transition(self.running_step, self.completed_step)

        pending = agent_state("pending", self.pending_step, elapsed=0)
        running = agent_state(
            "running", self.running_step, elapsed=1, current="load_sales"
        )
        completed = agent_state("completed", self.completed_step, elapsed=3)
        validate_agent_transition(pending, running)
        validate_agent_transition(running, completed)

    def test_terminal_step_cannot_run_or_complete_twice(self):
        for current in (self.running_step, self.completed_step):
            with self.subTest(status=current.status):
                with self.assertRaises(StateTransitionError):
                    validate_step_transition(self.completed_step, current)

    def test_attempt_count_changes_only_when_pending_work_starts(self):
        invalid_running = tool_step("running", attempts=0)
        with self.assertRaises(StateTransitionError):
            validate_step_transition(self.pending_step, invalid_running)

        invalid_skip = tool_step("skipped", attempts=1)
        with self.assertRaises(StateTransitionError):
            validate_step_transition(self.pending_step, invalid_skip)

    def test_identity_latency_and_agent_time_cannot_move_backwards(self):
        wrong_identity = StepExecutionState(
            step_id="other",
            kind="tool",
            status="running",
            attempts=1,
        )
        with self.assertRaises(StateTransitionError):
            validate_step_transition(self.pending_step, wrong_identity)

        old = tool_step("running", attempts=1, latency=5)
        new = tool_step(
            "completed",
            attempts=1,
            output={"ok": True},
            latency=4,
            call_id="call-1",
        )
        with self.assertRaises(StateTransitionError):
            validate_step_transition(old, new)

        pending = agent_state("pending", self.pending_step, elapsed=10)
        running = agent_state(
            "running", self.running_step, elapsed=5, current="load_sales"
        )
        with self.assertRaises(StateTransitionError):
            validate_agent_transition(pending, running)

    def test_terminal_agent_state_cannot_resume(self):
        completed = agent_state("completed", self.completed_step, elapsed=3)
        with self.assertRaises(StateTransitionError):
            validate_agent_transition(completed, completed)

        running = agent_state(
            "running", self.running_step, elapsed=1, current="load_sales"
        )
        with self.assertRaises(StateTransitionError):
            validate_agent_transition(running, running)


if __name__ == "__main__":
    unittest.main()
