from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from uuid import UUID

import psycopg
from backend.agent_api.api import create_internal_agent_app
from backend.agent_api.auth import (
    InternalAgentApiSettings,
    load_internal_agent_api_settings,
)
from backend.agent_api.models import (
    AgentCaller,
    AgentTaskProgress,
    AgentTaskRequest,
    AgentTaskResponse,
)
from backend.agent_api.service import (
    AgentTaskServiceError,
    DurableAgentTaskController,
)
from backend.main import app as public_app
from backend.planning.checkpoint_models import (
    ExecutionCreation,
    PersistentCheckpoint,
    PersistentExecution,
)
from backend.planning.checkpoint_recovery import initial_agent_state
from backend.planning.checkpoint_store import canonical_json_sha256
from backend.planning.demo import build_quarter_sales_comparison_plan
from backend.planning.durable_controlled_runtime import DurablePlannerError
from backend.planning.models import AgentState, PlanningRequest, StepExecutionState
from backend.planning.validation import canonical_plan_sha256


KEY = "internal-agent-test-key-32-characters-minimum"
TASK_ID = UUID("12345678-1234-5678-9234-567812345678")
NOW = datetime(2026, 10, 22, tzinfo=UTC)


class AsgiClient:
    def __init__(self, app):
        self.app = app

    def request(self, method, path, *, payload=None, headers=None):
        async def run():
            output = []
            raw_headers = [(b"content-type", b"application/json")]
            raw_headers.extend(
                (key.lower().encode(), value.encode())
                for key, value in (headers or {}).items()
            )
            body = b"" if payload is None else json.dumps(payload).encode()
            sent = False

            async def receive():
                nonlocal sent
                if sent:
                    return {"type": "http.disconnect"}
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}

            async def send(message):
                output.append(message)

            await self.app(
                {
                    "type": "http",
                    "asgi": {"version": "3.0"},
                    "http_version": "1.1",
                    "method": method,
                    "scheme": "http",
                    "path": path,
                    "raw_path": path.encode(),
                    "query_string": b"",
                    "headers": raw_headers,
                    "server": ("test", 80),
                    "client": ("test", 1),
                },
                receive,
                send,
            )
            response_body = b"".join(item.get("body", b"") for item in output)
            return SimpleNamespace(
                status_code=output[0]["status"],
                text=response_body.decode(),
                json=lambda: json.loads(response_body),
            )

        return asyncio.run(run())

    def post(self, path, *, payload, headers=None):
        return self.request("POST", path, payload=payload, headers=headers)

    def get(self, path, *, headers=None):
        return self.request("GET", path, headers=headers)


class FakeController:
    def __init__(self, response):
        self.response = response
        self.submit_calls = []
        self.get_calls = []
        self.error = None

    def submit(self, request, *, idempotency_key, caller):
        self.submit_calls.append((request, idempotency_key, caller))
        if self.error:
            raise self.error
        return self.response

    def get(self, task_id, *, caller):
        self.get_calls.append((task_id, caller))
        if self.error:
            raise self.error
        return self.response.model_copy(update={"created": None})


def queued_response():
    return AgentTaskResponse(
        task_id=TASK_ID,
        status="queued",
        created=True,
        request=default_request(),
        progress=AgentTaskProgress(completed_steps=0, total_steps=3),
        result=None,
        error=None,
        state_version=0,
        policy_version="bounded-plan-policy-v1",
        runtime_version="durable-controlled-planner-v1",
        updated_at=NOW,
    )


def default_request():
    return AgentTaskRequest(
        task="compare_quarterly_sales",
        baseline_period="2026-Q1",
        comparison_period="2026-Q2",
        region="all",
    )


def durable_records(*, completed=False, scope_key="internal-agent:analytics-copilot"):
    plan = build_quarter_sales_comparison_plan(PlanningRequest())
    if completed:
        final = {
            "baseline_period": "2026-Q1",
            "comparison_period": "2026-Q2",
            "region": "all",
            "currency": "USD",
            "baseline_total_sales": "400.00",
            "comparison_total_sales": "600.00",
            "absolute_change": "200.00",
            "growth_rate_percent": "50.00",
            "source_relation": "analytics.sales_orders",
            "dataset_version": "synthetic-sales-db-v1",
        }
        states = (
            StepExecutionState(
                step_id=plan.steps[0].step_id,
                kind="tool",
                status="completed",
                attempts=1,
                output={"total_sales": "400.00"},
                latency_ms=1,
                tool_call_id="tool-1",
            ),
            StepExecutionState(
                step_id=plan.steps[1].step_id,
                kind="tool",
                status="completed",
                attempts=1,
                output={"total_sales": "600.00"},
                latency_ms=1,
                tool_call_id="tool-2",
            ),
            StepExecutionState(
                step_id=plan.steps[2].step_id,
                kind="compute",
                status="completed",
                attempts=1,
                output=final,
                latency_ms=1,
            ),
        )
        state = AgentState(
            plan_id=plan.plan_id,
            status="completed",
            current_step_id=None,
            step_states=states,
            completed_step_ids=tuple(step.step_id for step in states),
            steps_executed=3,
            tool_calls_used=2,
            budget_units_used=3,
            elapsed_ms=3,
            error=None,
        )
        execution_status = "completed"
        version = 6
    else:
        state = initial_agent_state(plan)
        execution_status = "pending"
        version = 0
    execution = PersistentExecution(
        execution_id=TASK_ID,
        scope_key=scope_key,
        idempotency_key_hash="1" * 64,
        request_sha256="2" * 64,
        plan_sha256=canonical_plan_sha256(plan),
        plan_version=plan.version,
        plan=plan,
        policy_version="bounded-plan-policy-v1",
        runtime_version="durable-controlled-planner-v1",
        status=execution_status,
        state_version=version,
        checkpoint_sequence=version,
        lease_owner=None,
        lease_expires_at=None,
        created_at=NOW,
        updated_at=NOW,
    )
    checkpoint = PersistentCheckpoint(
        execution_id=TASK_ID,
        sequence=version,
        state_version=version,
        execution_status=execution_status,
        agent_state=state,
        state_sha256=canonical_json_sha256(state.model_dump(mode="json")),
        created_at=NOW,
    )
    return execution, checkpoint


class InternalAgentApiTests(unittest.TestCase):
    @staticmethod
    def enabled_app(controller):
        return create_internal_agent_app(
            settings=InternalAgentApiSettings(enabled=True, api_key=KEY),
            controller=controller,
        )

    def test_feature_defaults_off_and_requires_strong_key_when_enabled(self):
        with patch.dict(os.environ, {}, clear=True), patch(
            "backend.agent_api.auth.load_dotenv"
        ):
            settings = load_internal_agent_api_settings()
        self.assertFalse(settings.enabled)
        with self.assertRaises(ValueError):
            InternalAgentApiSettings(enabled=True, api_key="short")

    def test_internal_routes_are_not_added_to_existing_public_app(self):
        public_paths = {getattr(route, "path", "") for route in public_app.routes}
        self.assertNotIn("/internal/agent/tasks", public_paths)
        internal = create_internal_agent_app(
            settings=InternalAgentApiSettings(enabled=False)
        )
        internal_paths = {getattr(route, "path", "") for route in internal.routes}
        self.assertIn("/internal/agent/tasks", internal_paths)

    def test_disabled_and_bad_credentials_never_call_controller(self):
        fake = FakeController(queued_response())
        disabled = create_internal_agent_app(
            settings=InternalAgentApiSettings(enabled=False), controller=fake
        )
        response = AsgiClient(disabled).post(
            "/internal/agent/tasks",
            payload=default_request().model_dump(mode="json"),
            headers={"Idempotency-Key": "request-123", "X-Internal-Agent-Key": KEY},
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"]["code"], "agent_api_disabled")
        enabled = AsgiClient(self.enabled_app(fake))
        for headers in (
            {"Idempotency-Key": "request-123"},
            {"Idempotency-Key": "request-123", "X-Internal-Agent-Key": "wrong"},
        ):
            with self.subTest(headers=headers):
                response = enabled.post(
                    "/internal/agent/tasks",
                    payload=default_request().model_dump(mode="json"),
                    headers=headers,
                )
                self.assertEqual(response.status_code, 401)
        self.assertEqual(fake.submit_calls, [])

    def test_submit_validates_request_and_uses_server_owned_caller(self):
        fake = FakeController(queued_response())
        client = AsgiClient(self.enabled_app(fake))
        headers = {
            "Idempotency-Key": "request-123",
            "X-Internal-Agent-Key": KEY,
        }
        response = client.post(
            "/internal/agent/tasks",
            payload={
                "task": "compare_quarterly_sales",
                "baseline_period": "2026-Q1",
                "comparison_period": "2026-Q2",
                "region": "all",
            },
            headers=headers,
        )
        self.assertEqual(response.status_code, 202)
        parsed = AgentTaskResponse.model_validate_json(response.text, strict=True)
        self.assertEqual(parsed.task_id, TASK_ID)
        request, key, caller = fake.submit_calls[0]
        self.assertEqual(request, default_request())
        self.assertEqual(key, "request-123")
        self.assertEqual(caller, AgentCaller())
        for override in (
            "provider",
            "model",
            "prompt",
            "tools",
            "sql",
            "scope_key",
            "principal",
        ):
            with self.subTest(override=override):
                body = default_request().model_dump(mode="json")
                body[override] = "client-controlled"
                self.assertEqual(
                    client.post(
                        "/internal/agent/tasks", payload=body, headers=headers
                    ).status_code,
                    422,
                )
        self.assertEqual(len(fake.submit_calls), 1)

    def test_idempotency_and_request_validation_fail_before_controller(self):
        fake = FakeController(queued_response())
        client = AsgiClient(self.enabled_app(fake))
        valid_key = {"X-Internal-Agent-Key": KEY, "Idempotency-Key": "request-123"}
        bodies = [
            {},
            {"baseline_period": "2026-Q1", "comparison_period": "2026-Q1"},
            {"region": "everywhere"},
            {"task": "run_arbitrary_sql"},
        ]
        for body in bodies:
            with self.subTest(body=body):
                self.assertEqual(
                    client.post(
                        "/internal/agent/tasks", payload=body, headers=valid_key
                    ).status_code,
                    422,
                )
        for key in (None, "short", "bad key", "x" * 201):
            headers = {"X-Internal-Agent-Key": KEY}
            if key is not None:
                headers["Idempotency-Key"] = key
            with self.subTest(key=key):
                self.assertEqual(
                    client.post(
                        "/internal/agent/tasks",
                        payload=default_request().model_dump(mode="json"),
                        headers=headers,
                    ).status_code,
                    422,
                )
        self.assertEqual(fake.submit_calls, [])

    def test_status_query_and_openapi_use_standard_contract(self):
        fake = FakeController(queued_response())
        client = AsgiClient(self.enabled_app(fake))
        headers = {"X-Internal-Agent-Key": KEY}
        response = client.get(f"/internal/agent/tasks/{TASK_ID}", headers=headers)
        self.assertEqual(response.status_code, 200)
        parsed = AgentTaskResponse.model_validate_json(response.text, strict=True)
        self.assertIsNone(parsed.created)
        self.assertEqual(fake.get_calls, [(TASK_ID, AgentCaller())])
        self.assertEqual(
            client.get("/internal/agent/tasks/not-a-uuid", headers=headers).status_code,
            422,
        )
        schema = client.get("/openapi.json").json()
        response_schema = schema["paths"][f"/internal/agent/tasks/{{task_id}}"][
            "get"
        ]["responses"]["200"]["content"]["application/json"]["schema"]
        self.assertEqual(response_schema["$ref"], "#/components/schemas/AgentTaskResponse")

    def test_service_errors_are_normalized_and_do_not_leak(self):
        fake = FakeController(queued_response())
        fake.error = AgentTaskServiceError(
            status_code=599,
            code="private_internal_failure",
            message="PRIVATE database body",
        )
        client = AsgiClient(self.enabled_app(fake))
        with self.assertLogs("uvicorn.error", level="INFO") as logs:
            response = client.get(
                f"/internal/agent/tasks/{TASK_ID}",
                headers={"X-Internal-Agent-Key": KEY},
            )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"]["code"], "agent_request_failed")
        self.assertNotIn("PRIVATE", response.text + str(logs.output))
        self.assertNotIn(KEY, response.text + str(logs.output))


class DurableAgentTaskControllerTests(unittest.TestCase):
    def test_submit_reuses_existing_durable_agent_and_fixed_scope(self):
        execution, checkpoint = durable_records()
        agent = Mock()
        agent.submit.return_value = ExecutionCreation(
            execution=execution, checkpoint=checkpoint, created=True
        )
        controller = DurableAgentTaskController(agent, Mock(), clock=lambda: NOW)
        response = controller.submit(
            default_request(),
            idempotency_key="request-123",
            caller=AgentCaller(),
        )
        self.assertEqual(response.status, "queued")
        self.assertTrue(response.created)
        called_request = agent.submit.call_args.args[0]
        self.assertEqual(called_request, PlanningRequest())
        self.assertEqual(
            agent.submit.call_args.kwargs["scope_key"],
            "internal-agent:analytics-copilot",
        )

    def test_completed_status_returns_validated_result(self):
        execution, checkpoint = durable_records(completed=True)
        store = Mock()
        store.get_execution.return_value = execution
        store.get_latest_checkpoint.return_value = checkpoint
        response = DurableAgentTaskController(Mock(), store).get(
            TASK_ID, caller=AgentCaller()
        )
        self.assertEqual(response.status, "completed")
        self.assertEqual(str(response.result.growth_rate_percent), "50.00")
        self.assertEqual(response.progress.completed_steps, 3)

    def test_cross_scope_is_not_found_and_idempotency_conflict_is_safe(self):
        execution, checkpoint = durable_records(scope_key="another-tenant")
        store = Mock()
        store.get_execution.return_value = execution
        store.get_latest_checkpoint.return_value = checkpoint
        controller = DurableAgentTaskController(Mock(), store)
        with self.assertRaises(AgentTaskServiceError) as captured:
            controller.get(TASK_ID, caller=AgentCaller())
        self.assertEqual((captured.exception.status_code, captured.exception.code), (404, "agent_task_not_found"))

        agent = Mock()
        agent.submit.side_effect = DurablePlannerError("idempotency_key_conflict")
        controller = DurableAgentTaskController(agent, Mock(), clock=lambda: NOW)
        with self.assertRaises(AgentTaskServiceError) as captured:
            controller.submit(
                default_request(),
                idempotency_key="request-123",
                caller=AgentCaller(),
            )
        self.assertEqual((captured.exception.status_code, captured.exception.code), (409, "agent_idempotency_conflict"))

    def test_status_snapshot_version_mismatch_fails_safely(self):
        execution, checkpoint = durable_records()
        store = Mock()
        store.get_execution.return_value = execution.model_copy(
            update={"state_version": 1, "checkpoint_sequence": 1}
        )
        store.get_latest_checkpoint.return_value = checkpoint
        controller = DurableAgentTaskController(Mock(), store)
        with self.assertRaises(AgentTaskServiceError) as captured:
            controller.get(TASK_ID, caller=AgentCaller())
        self.assertEqual(
            (captured.exception.status_code, captured.exception.code),
            (409, "agent_status_changed"),
        )

    def test_stopped_statuses_have_standardized_safe_errors(self):
        execution, checkpoint = durable_records()
        for persisted, exposed, code in (
            ("reconciling", "needs_reconciliation", "agent_reconciliation_required"),
            ("failed", "failed", "agent_execution_failed"),
        ):
            with self.subTest(persisted=persisted):
                store = Mock()
                store.get_execution.return_value = execution.model_copy(
                    update={"status": persisted}
                )
                store.get_latest_checkpoint.return_value = checkpoint.model_copy(
                    update={"execution_status": persisted}
                )
                response = DurableAgentTaskController(Mock(), store).get(
                    TASK_ID, caller=AgentCaller()
                )
                self.assertEqual(response.status, exposed)
                self.assertEqual(response.error.code, code)
                self.assertIsNone(response.result)

    def test_forged_server_caller_is_rejected_before_runtime(self):
        forged = AgentCaller.model_construct(principal="attacker", scope_key="attacker")
        agent = Mock()
        controller = DurableAgentTaskController(agent, Mock(), clock=lambda: NOW)
        with self.assertRaises(AgentTaskServiceError) as captured:
            controller.submit(
                default_request(),
                idempotency_key="request-123",
                caller=forged,
            )
        self.assertEqual(captured.exception.code, "agent_forbidden")
        agent.submit.assert_not_called()

    def test_database_failure_is_normalized(self):
        agent = Mock()
        agent.submit.side_effect = psycopg.OperationalError("PRIVATE database body")
        controller = DurableAgentTaskController(agent, Mock(), clock=lambda: NOW)
        with self.assertRaises(AgentTaskServiceError) as captured:
            controller.submit(
                default_request(),
                idempotency_key="request-123",
                caller=AgentCaller(),
            )
        self.assertEqual(
            (captured.exception.status_code, captured.exception.code),
            (503, "agent_persistence_unavailable"),
        )
        self.assertNotIn("PRIVATE", str(captured.exception))


if __name__ == "__main__":
    unittest.main()
