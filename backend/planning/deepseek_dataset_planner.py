"""Default-off DeepSeek adapter that compiles untrusted drafts into dataset-plan-v1."""

from __future__ import annotations

from hashlib import sha256
from http.client import HTTPException as HTTPTransportError
import json
import os
from pathlib import Path
import socket
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request
from urllib.request import urlopen

from dotenv import load_dotenv
from pydantic import SecretStr, ValidationError

from backend.datasets.analysis_models import DatasetBindingContext
from backend.tools.models import ToolCall

from .dataset_models import (
    AssembleDatasetResultsSpec,
    DatasetAgentRequest,
    DatasetPlan,
    DatasetPlanIssue,
    DatasetPlanStep,
)
from .dataset_planner_models import (
    DatasetPlannerCatalog,
    DatasetPlannerDraft,
    DatasetPlannerOutcome,
    DatasetPlannerQuestion,
    DatasetPlannerTelemetry,
)
from .dataset_planner_prompt import (
    DATASET_PLANNER_PROMPT_SHA256,
    DATASET_PLANNER_SCHEMA_SHA256,
    build_dataset_planner_generation_request,
)
from .dataset_validation import DatasetPlanValidator, canonical_dataset_request_sha256
from .deepseek_planner_adapter import (
    MAX_HTTP_RESPONSE_BYTES,
    DeepSeekPlannerSettings,
    DeepSeekStructuredPlannerTransport,
)
from .planner_safety import PlannerCostGuard, PlannerSafetyStop
from .structured_provider import PlannerGenerationConfig, PlannerProviderError
from backend.config import PROVIDERS


class DatasetPlannerError(RuntimeError):
    """Sanitized planning failure; provider content is never embedded in errors."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class DeepSeekDatasetPlannerTransport(DeepSeekStructuredPlannerTransport):
    """Dataset-only transport with explicit temperature; historical transport stays frozen."""

    def generate(self, request):
        if self.calls >= 1:
            raise PlannerProviderError("planner_provider_call_limit")
        if (
            request.config.provider != "deepseek"
            or request.config.model != self.settings.model
            or request.config.max_output_tokens != self.settings.max_output_tokens
            or request.config.thinking != self.settings.thinking
            or request.config.retry_count != 0
        ):
            raise PlannerProviderError("planner_generation_config_mismatch")
        self.cost_guard.begin_call()
        self.calls += 1
        payload = {
            "model": self.settings.model,
            "messages": [item.model_dump(mode="json") for item in request.messages],
            "response_format": {"type": "json_object"},
            "stream": False,
            "thinking": {"type": self.settings.thinking},
            "max_tokens": self.settings.max_output_tokens,
            "temperature": request.config.temperature,
        }
        http_request = Request(
            PROVIDERS["deepseek"]["chat_url"],
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": "Bearer " + self.settings.api_key.get_secret_value(),
                "Content-Type": "application/json",
            },
            method="POST",
        )
        started = self._clock()
        try:
            with self._opener(http_request, timeout=self.settings.timeout_seconds) as response:
                raw = response.read(MAX_HTTP_RESPONSE_BYTES + 1)
            if len(raw) > MAX_HTTP_RESPONSE_BYTES:
                raise PlannerProviderError("planner_provider_response_too_large")
            data = json.loads(raw.decode("utf-8"))
            result = self._parse(data, round((self._clock() - started) * 1000))
            cost = self._conservative_cost(result.usage)
            self.cost_guard.complete_call(cost)
            return result.model_copy(update={"estimated_cost_usd": cost})
        except PlannerSafetyStop:
            raise
        except (HTTPError, URLError, TimeoutError, socket.timeout, HTTPTransportError):
            self.cost_guard.abort_call()
            raise PlannerProviderError("planner_provider_transport_error") from None
        except (json.JSONDecodeError, UnicodeError, KeyError, TypeError, ValueError, IndexError):
            self.cost_guard.abort_call()
            raise PlannerProviderError("planner_provider_response_invalid") from None
        except PlannerProviderError:
            self.cost_guard.abort_call()
            raise


def load_deepseek_dataset_planner_settings() -> DeepSeekPlannerSettings:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    raw = os.getenv("DEEPSEEK_DATASET_PLANNER_ENABLED", "false").strip().lower()
    values = {"true": True, "false": False, "1": True, "0": False}
    if raw not in values:
        raise ValueError("Invalid DEEPSEEK_DATASET_PLANNER_ENABLED value")
    return DeepSeekPlannerSettings(
        enabled=values[raw],
        api_key=os.getenv("DEEPSEEK_API_KEY", ""),
    )


class DeepSeekDatasetPlannerAdapter:
    """One-call Dataset Planner. It grants no identity and executes no Tool."""

    def __init__(
        self,
        settings: DeepSeekPlannerSettings,
        *,
        validator: DatasetPlanValidator,
        cost_guard: PlannerCostGuard,
        opener: Callable = urlopen,
        config: PlannerGenerationConfig | None = None,
    ):
        if not settings.enabled:
            raise ValueError("dataset_planner_disabled")
        self.settings = settings
        self.validator = validator
        self.config = config or PlannerGenerationConfig(
            model=settings.model,
            max_output_tokens=settings.max_output_tokens,
            timeout_seconds=settings.timeout_seconds,
            thinking=settings.thinking,
        )
        self.transport = DeepSeekDatasetPlannerTransport(
            settings,
            cost_guard=cost_guard,
            opener=opener,
        )
        self.calls = 0
        self.last_generation_request = None
        self.last_response = None

    def plan(
        self,
        question: DatasetPlannerQuestion,
        context: DatasetBindingContext,
        catalog: DatasetPlannerCatalog,
    ) -> DatasetPlannerOutcome:
        self._validate_server_context(question, context, catalog)
        if self.calls >= 1:
            raise DatasetPlannerError("dataset_planner_call_limit")
        generation_request = build_dataset_planner_generation_request(
            question, catalog, self.config
        )
        self.calls += 1
        self.last_generation_request = generation_request
        try:
            response = self.transport.generate(generation_request)
        except (PlannerProviderError, PlannerSafetyStop) as error:
            raise DatasetPlannerError(str(error)) from None
        self.last_response = response
        if response.finish_reason != "stop":
            raise DatasetPlannerError("dataset_planner_incomplete_output")
        if response.usage is None or response.estimated_cost_usd is None:
            raise DatasetPlannerError("dataset_planner_telemetry_missing")
        try:
            draft = DatasetPlannerDraft.model_validate_json(response.content, strict=True)
        except (ValidationError, ValueError):
            raise DatasetPlannerError("dataset_planner_invalid_output") from None
        telemetry = DatasetPlannerTelemetry(
            prompt_sha256=DATASET_PLANNER_PROMPT_SHA256,
            schema_sha256=DATASET_PLANNER_SCHEMA_SHA256,
            request_id=response.request_id,
            usage=response.usage,
            latency_ms=response.latency_ms,
            estimated_cost_usd=response.estimated_cost_usd,
            generation_config=self.config,
        )
        question_hash = self._question_sha256(question, catalog)
        if draft.outcome == "needs_clarification":
            return DatasetPlannerOutcome(
                status="needs_clarification",
                question_sha256=question_hash,
                clarifications=draft.clarifications,
                telemetry=telemetry,
            )
        try:
            request, plan = self._compile(draft, question, context)
        except ValidationError:
            raise DatasetPlannerError("dataset_planner_semantic_invalid") from None
        verdict = self.validator.validate(plan, request, context)
        if verdict.status == "invalid":
            return DatasetPlannerOutcome(
                status="rejected",
                question_sha256=question_hash,
                request=request,
                issues=verdict.issues,
                telemetry=telemetry,
            )
        return DatasetPlannerOutcome(
            status="planned",
            question_sha256=question_hash,
            request=request,
            plan=plan,
            telemetry=telemetry,
        )

    @staticmethod
    def _validate_server_context(
        question: DatasetPlannerQuestion,
        context: DatasetBindingContext,
        catalog: DatasetPlannerCatalog,
    ) -> None:
        if question.project_id != context.project_id or question.binding != context.binding:
            raise DatasetPlannerError("dataset_planner_context_mismatch")
        context_fields = {
            table.table_index: tuple(table.fields) for table in context.tables
        }
        catalog_fields = {
            table.table_index: tuple(field.name for field in table.fields)
            for table in catalog.tables
        }
        if context_fields != catalog_fields:
            raise DatasetPlannerError("dataset_planner_catalog_mismatch")

    @staticmethod
    def _question_sha256(
        question: DatasetPlannerQuestion, catalog: DatasetPlannerCatalog
    ) -> str:
        public_identity = {
            "question": question.model_dump(mode="json"),
            "catalog": catalog.model_dump(mode="json"),
        }
        encoded = json.dumps(
            public_identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return sha256(encoded).hexdigest()

    @staticmethod
    def _compile(
        draft: DatasetPlannerDraft,
        question: DatasetPlannerQuestion,
        context: DatasetBindingContext,
    ) -> tuple[DatasetAgentRequest, DatasetPlan]:
        request = DatasetAgentRequest(
            project_id=context.project_id,
            binding=context.binding,
            operations=tuple(
                {
                    "operation_id": item.operation_id,
                    "tool_name": item.tool_name,
                    "arguments": item.arguments,
                }
                for item in draft.operations
            ),
        )
        step_ids = {
            operation.operation_id: f"analyze_{index}"
            for index, operation in enumerate(draft.operations, 1)
        }
        steps = []
        for operation, requested in zip(draft.operations, request.operations, strict=True):
            validated = request.tool_input(requested)
            steps.append(
                DatasetPlanStep(
                    step_id=step_ids[operation.operation_id],
                    kind="tool",
                    description=operation.description,
                    depends_on=tuple(
                        step_ids.get(dependency, dependency)
                        for dependency in operation.depends_on
                    ),
                    request_operation_id=operation.operation_id,
                    tool_call=ToolCall(
                        name=operation.tool_name,
                        arguments=validated.model_dump(mode="json"),
                    ),
                )
            )
        source_ids = tuple(step.step_id for step in steps)
        steps.append(
            DatasetPlanStep(
                step_id="assemble_results",
                kind="compute",
                description="Assemble validated Dataset calculations and limitations",
                depends_on=source_ids,
                computation=AssembleDatasetResultsSpec(source_step_ids=source_ids),
            )
        )
        request_hash = canonical_dataset_request_sha256(request, context)
        plan = DatasetPlan(
            plan_id=f"dataset-llm-{request_hash[:12]}",
            project_id=context.project_id,
            binding=context.binding,
            normalized_sha256=context.normalized_sha256,
            authorization_version=context.authorization_version,
            request_sha256=request_hash,
            steps=tuple(steps),
        )
        return request, plan


class PreparedDatasetPlanProvider:
    """Pass one already validated Adapter result into the existing Durable Runtime."""

    def __init__(self, outcome: DatasetPlannerOutcome):
        if outcome.status != "planned" or outcome.request is None or outcome.plan is None:
            raise ValueError("prepared Dataset plan must be planned")
        self.outcome = outcome
        self.calls = 0

    def propose(self, request: DatasetAgentRequest, context: DatasetBindingContext) -> str:
        self.calls += 1
        if request != self.outcome.request:
            raise DatasetPlannerError("prepared_dataset_request_mismatch")
        plan = self.outcome.plan
        if (
            plan.project_id != context.project_id
            or plan.binding != context.binding
            or plan.normalized_sha256 != context.normalized_sha256
            or plan.authorization_version != context.authorization_version
        ):
            raise DatasetPlannerError("prepared_dataset_context_mismatch")
        return plan.model_dump_json()


__all__ = [
    "DatasetPlannerError",
    "DeepSeekDatasetPlannerTransport",
    "DeepSeekDatasetPlannerAdapter",
    "PreparedDatasetPlanProvider",
    "load_deepseek_dataset_planner_settings",
]
