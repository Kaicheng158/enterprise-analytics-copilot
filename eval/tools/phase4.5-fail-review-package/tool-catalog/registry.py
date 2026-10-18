"""Server-owned tool definitions, registry and authorization policy."""

from dataclasses import dataclass
import re
from types import MappingProxyType
from typing import Callable, Mapping

from pydantic import BaseModel


class ToolRegistryError(ValueError):
    """Fixed safe error code; no tool arguments or output are retained."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    version: str
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    handler: Callable[[BaseModel], BaseModel | dict]

    def schema(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "input_schema": self.input_model.model_json_schema(),
            "output_schema": self.output_model.model_json_schema(),
        }


@dataclass(frozen=True)
class ToolAuthorizationPolicy:
    grants: Mapping[str, frozenset[str]]

    def __post_init__(self):
        normalized = {
            principal: frozenset(tools)
            for principal, tools in self.grants.items()
        }
        object.__setattr__(self, "grants", MappingProxyType(normalized))

    def allows(self, principal: str, tool_name: str) -> bool:
        return tool_name in self.grants.get(principal, frozenset())


class ToolRegistry:
    def __init__(self, definitions: tuple[ToolDefinition, ...], policy: ToolAuthorizationPolicy):
        mapping: dict[str, ToolDefinition] = {}
        for definition in definitions:
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", definition.name):
                raise ValueError("Invalid registered tool name")
            if definition.name in mapping:
                raise ValueError("Duplicate tool registration")
            if not definition.version or not definition.description:
                raise ValueError("Incomplete tool registration")
            if not issubclass(definition.input_model, BaseModel) or not issubclass(
                definition.output_model, BaseModel
            ):
                raise ValueError("Tool models must be Pydantic models")
            if not callable(definition.handler):
                raise ValueError("Tool handler must be callable")
            mapping[definition.name] = definition
        registered = frozenset(mapping)
        if any(not granted <= registered for granted in policy.grants.values()):
            raise ValueError("Authorization references an unregistered tool")
        self._definitions = MappingProxyType(mapping)
        self._policy = policy

    def resolve(self, name: str, principal: str) -> ToolDefinition:
        definition = self._definitions.get(name)
        if definition is None:
            raise ToolRegistryError("tool_not_found")
        if not self._policy.allows(principal, name):
            raise ToolRegistryError("tool_not_authorized")
        return definition

    def schemas_for(self, principal: str) -> tuple[dict, ...]:
        return tuple(
            self._definitions[name].schema()
            for name in sorted(self._definitions)
            if self._policy.allows(principal, name)
        )
