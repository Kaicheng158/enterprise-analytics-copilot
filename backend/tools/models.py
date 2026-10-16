"""Strict provider-independent contracts for internal tool execution."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class ToolCall(StrictModel):
    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    arguments: dict[str, Any]


class ToolExecutionContext(StrictModel):
    """Created by trusted server code, never from model/user arguments."""

    principal: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$")
    correlation_id: str = Field(min_length=1, max_length=128)

    @field_validator("correlation_id")
    @classmethod
    def correlation_id_has_no_control_characters(cls, value: str) -> str:
        if any(character.isspace() or ord(character) < 32 for character in value):
            raise ValueError("correlation_id contains unsupported characters")
        return value


class ToolError(StrictModel):
    code: Literal[
        "tool_not_found",
        "tool_not_authorized",
        "tool_input_invalid",
        "tool_output_invalid",
        "tool_execution_failed",
    ]
    message: str


class ToolExecutionResult(StrictModel):
    call_id: str
    tool_name: str
    tool_version: str | None
    status: Literal["success", "error"]
    output: dict[str, Any] | None
    error: ToolError | None
    latency_ms: int = Field(ge=0)

    @field_validator("call_id", "tool_name")
    @classmethod
    def non_blank(cls, value: str) -> str:
        if not value:
            raise ValueError("must not be blank")
        return value

    @model_validator(mode="after")
    def success_and_error_fields_are_consistent(self):
        if self.status == "success" and (self.output is None or self.error is not None):
            raise ValueError("Success requires output and forbids error")
        if self.status == "error" and (self.output is not None or self.error is None):
            raise ValueError("Error requires error and forbids output")
        return self
