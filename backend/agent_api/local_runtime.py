"""Disposable localhost composition for Phase 5.7b integration verification."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
from threading import Lock, RLock
from typing import Callable
from uuid import UUID, uuid4

import psycopg
from psycopg import sql
from pydantic import Field

from backend.planning import DurableAgentRuntime, PlanningRequest
from backend.planning.checkpoint_store import PostgresCheckpointStore
from backend.planning.demo import build_quarter_sales_comparison_plan
from backend.planning.durable_agent_runtime import DurableAgentSettings
from backend.planning.durable_controlled_runtime import (
    DurableControlledPlanner,
    DurableControlledPlannerSettings,
)
from backend.planning.worker_runtime import DurableWorkerRuntime, DurableWorkerSettings
from backend.tools.catalog import build_tool_registry
from backend.tools.executor import ToolExecutor
from backend.tools.models import StrictModel
from scripts.migrate import apply_migrations, connect_kwargs
from scripts.seed_sales import seed_sales

from .auth import InternalAgentApiSettings
from .models import AgentCaller, AgentTaskRequest, AgentTaskResponse
from .service import (
    AgentTaskServiceError,
    DurableAgentTaskController,
    UnavailableAgentTaskController,
)


class LocalAgentServiceSettings(StrictModel):
    enabled: bool = False
    lease_ttl_seconds: int = Field(default=2, ge=1, le=300)
    heartbeat_interval_ms: int = Field(default=50, ge=25, le=60_000)
    operation_timeout_ms: int = Field(default=2_000, ge=100, le=300_000)
    max_steps_per_run: int = Field(default=8, ge=1, le=8)


def load_local_agent_service_settings() -> LocalAgentServiceSettings:
    raw = os.getenv("INTERNAL_AGENT_LOCAL_SERVICE_ENABLED", "false").strip().lower()
    values = {"true": True, "false": False, "1": True, "0": False}
    if raw not in values:
        raise ValueError("Invalid INTERNAL_AGENT_LOCAL_SERVICE_ENABLED value")
    return LocalAgentServiceSettings(enabled=values[raw])


class DeterministicLocalPlanProvider:
    """Fake provider used only by the disposable localhost profile."""

    def __init__(self):
        self._lock = Lock()
        self.calls = 0

    def propose(self, request: PlanningRequest) -> str:
        with self._lock:
            self.calls += 1
        return build_quarter_sales_comparison_plan(request).model_dump_json()


class LocalExecutingAgentTaskController:
    """Serialize local submissions, then run the existing durable runtime."""

    def __init__(
        self,
        delegate: DurableAgentTaskController,
        agent: DurableAgentRuntime,
    ):
        self._delegate = delegate
        self._agent = agent
        self._submission_lock = RLock()

    def submit(
        self,
        request: AgentTaskRequest,
        *,
        idempotency_key: str,
        caller: AgentCaller,
    ) -> AgentTaskResponse:
        # The local profile is intentionally single-flight. This prevents two
        # simultaneous copies of one idempotent request from planning or executing.
        with self._submission_lock:
            accepted = self._delegate.submit(
                request,
                idempotency_key=idempotency_key,
                caller=caller,
            )
            if accepted.status != "completed":
                try:
                    self._agent.run_to_terminal(
                        accepted.task_id,
                        worker_id_prefix=f"local-http-{accepted.task_id.hex[:16]}",
                    )
                except Exception:
                    raise AgentTaskServiceError(
                        status_code=503,
                        code="agent_execution_unavailable",
                        message="Agent task execution is unavailable",
                    ) from None
            current = self._delegate.get(accepted.task_id, caller=caller)
            return current.model_copy(update={"created": accepted.created})

    def get(self, task_id: UUID, *, caller: AgentCaller) -> AgentTaskResponse:
        return self._delegate.get(task_id, caller=caller)


class DisposableLocalAgentRuntime:
    """Own a uniquely named disposable database and the existing Agent stack."""

    _DATABASE_PREFIX = "eac_local_agent_"

    def __init__(
        self,
        *,
        settings: LocalAgentServiceSettings,
        api_settings: InternalAgentApiSettings,
        provider=None,
        admin_kwargs_factory: Callable[[], dict] = connect_kwargs,
    ):
        self.settings = settings
        self.api_settings = api_settings
        self._provider_override = provider
        self._admin_kwargs_factory = admin_kwargs_factory
        self._lifecycle_lock = Lock()
        self._database_name: str | None = None
        self._database_kwargs: dict | None = None
        self._controller = None
        self.provider = None
        self.store = None
        self.agent = None

    @property
    def database_name(self) -> str | None:
        return self._database_name

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._controller is not None:
                return
            if not self.settings.enabled:
                raise ValueError("local_agent_service_disabled")
            if not self.api_settings.enabled:
                raise ValueError("internal_agent_api_disabled")
            admin_kwargs = self._admin_kwargs_factory()
            database_name = self._DATABASE_PREFIX + uuid4().hex
            if not database_name.startswith(self._DATABASE_PREFIX):
                raise RuntimeError("unsafe local database name")
            with psycopg.connect(**admin_kwargs, autocommit=True) as admin:
                admin.execute(
                    sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name))
                )
            self._database_name = database_name
            self._database_kwargs = {**admin_kwargs, "dbname": database_name}
            try:
                with psycopg.connect(
                    **self._database_kwargs, autocommit=True
                ) as database:
                    apply_migrations(database)
                    seed_sales(database)
                self._compose()
            except Exception:
                self._close_unlocked()
                raise

    def close(self) -> None:
        with self._lifecycle_lock:
            self._close_unlocked()

    def _close_unlocked(self) -> None:
        self._controller = None
        self.agent = None
        self.store = None
        self.provider = None
        database_name = self._database_name
        self._database_name = None
        self._database_kwargs = None
        if database_name is None:
            return
        if not database_name.startswith(self._DATABASE_PREFIX):
            raise RuntimeError("refusing to drop non-disposable database")
        admin_kwargs = self._admin_kwargs_factory()
        with psycopg.connect(**admin_kwargs, autocommit=True) as admin:
            admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (database_name,),
            )
            admin.execute(
                sql.SQL("DROP DATABASE {}").format(sql.Identifier(database_name))
            )

    @contextmanager
    def connection_factory(self):
        if self._database_kwargs is None:
            raise RuntimeError("local_agent_database_unavailable")
        connection = psycopg.connect(**self._database_kwargs)
        try:
            yield connection
        finally:
            connection.close()

    def _compose(self) -> None:
        secret = hashlib.sha256(
            b"local-agent-idempotency-v1\0"
            + self.api_settings.api_key.get_secret_value().encode("utf-8")
        ).digest()
        store = PostgresCheckpointStore(
            self.connection_factory,
            schema="agent_runtime",
            idempotency_secret=secret,
        )
        provider = self._provider_override or DeterministicLocalPlanProvider()
        planner = DurableControlledPlanner(
            provider,
            store,
            ToolExecutor(build_tool_registry(self.connection_factory)),
            settings=DurableControlledPlannerSettings(
                enabled=True,
                lease_ttl_seconds=self.settings.lease_ttl_seconds,
            ),
        )
        worker = DurableWorkerRuntime(
            store,
            settings=DurableWorkerSettings(
                enabled=True,
                lease_ttl_seconds=self.settings.lease_ttl_seconds,
                heartbeat_interval_ms=self.settings.heartbeat_interval_ms,
                operation_timeout_ms=self.settings.operation_timeout_ms,
            ),
        )
        agent = DurableAgentRuntime(
            planner,
            worker,
            settings=DurableAgentSettings(
                enabled=True,
                max_steps_per_run=self.settings.max_steps_per_run,
            ),
        )
        delegate = DurableAgentTaskController(agent, store)
        self.provider = provider
        self.store = store
        self.agent = agent
        self._controller = LocalExecutingAgentTaskController(delegate, agent)

    def submit(self, request, *, idempotency_key, caller):
        controller = self._controller or UnavailableAgentTaskController()
        return controller.submit(
            request,
            idempotency_key=idempotency_key,
            caller=caller,
        )

    def get(self, task_id, *, caller):
        controller = self._controller or UnavailableAgentTaskController()
        return controller.get(task_id, caller=caller)


__all__ = [
    "DeterministicLocalPlanProvider",
    "DisposableLocalAgentRuntime",
    "LocalAgentServiceSettings",
    "LocalExecutingAgentTaskController",
    "load_local_agent_service_settings",
]
