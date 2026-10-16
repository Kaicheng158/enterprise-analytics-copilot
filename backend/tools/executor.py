"""Validated fail-closed executor for server-registered tools."""

import logging
import json
import time
import uuid

from pydantic import ValidationError

from .models import ToolCall, ToolError, ToolExecutionContext, ToolExecutionResult
from .registry import ToolRegistry, ToolRegistryError

logger = logging.getLogger("uvicorn.error")

SAFE_MESSAGES = {
    "tool_not_found": "Tool is not registered",
    "tool_not_authorized": "Tool is not authorized",
    "tool_input_invalid": "Tool input is invalid",
    "tool_output_invalid": "Tool output is invalid",
    "tool_database_error": "Tool database query failed",
    "tool_execution_failed": "Tool execution failed",
}


class ToolExecutor:
    def __init__(self, registry: ToolRegistry):
        self._registry = registry

    def execute(self, call: ToolCall, context: ToolExecutionContext) -> ToolExecutionResult:
        started = time.monotonic()
        call_id = str(uuid.uuid4())
        definition = None
        try:
            definition = self._registry.resolve(call.name, context.principal)
            try:
                validated_input = definition.input_model.model_validate(call.arguments, strict=True)
            except ValidationError as error:
                raise ToolRegistryError("tool_input_invalid") from error
            try:
                raw_output = definition.handler(validated_input)
            except ToolRegistryError:
                raise
            except Exception as error:
                raise ToolRegistryError("tool_execution_failed") from error
            try:
                output = definition.output_model.model_validate(raw_output, strict=True)
            except ValidationError as error:
                raise ToolRegistryError("tool_output_invalid") from error
            result = ToolExecutionResult(
                call_id=call_id,
                tool_name=definition.name,
                tool_version=definition.version,
                status="success",
                output=output.model_dump(mode="json"),
                error=None,
                latency_ms=round((time.monotonic() - started) * 1000),
            )
        except ToolRegistryError as error:
            result = ToolExecutionResult(
                call_id=call_id,
                tool_name=call.name,
                tool_version=definition.version if definition else None,
                status="error",
                output=None,
                error=ToolError(code=error.code, message=SAFE_MESSAGES[error.code]),
                latency_ms=round((time.monotonic() - started) * 1000),
            )
        logger.info(
            "tool_execution %s",
            json.dumps(
                {
                    "call_id": result.call_id,
                    "correlation_id": context.correlation_id,
                    "principal": context.principal,
                    "tool_name": result.tool_name,
                    "tool_version": result.tool_version,
                    "status": result.status,
                    "error_code": result.error.code if result.error else None,
                    "latency_ms": result.latency_ms,
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
        return result
