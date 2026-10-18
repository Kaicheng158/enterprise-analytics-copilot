"""Server-owned tool visibility policy, independent from execution authorization.

The historical registry and executor remain the authority for registration and
principal grants. This module adds a narrower model-facing visibility boundary:
an exposed tool must also be registered and authorized, while trusted internal
code may still call an authorized hidden tool directly through the executor.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from types import MappingProxyType
from typing import Mapping

from .orchestration import (
    ToolCallDecision,
    ToolDecisionProvider,
    ToolProviderRequest,
    parse_tool_decision,
)
from .registry import ToolRegistry, ToolRegistryError

logger = logging.getLogger("uvicorn.error")

HIDDEN_TOOL_RESPONSE = "The requested operation is not available in this context."


@dataclass(frozen=True)
class ToolExposurePolicy:
    """Names a server-owned subset of authorized tools visible to each principal."""

    grants: Mapping[str, frozenset[str]]
    version: str = "tool-exposure-policy-v1"

    def __post_init__(self):
        normalized = {
            principal: frozenset(names)
            for principal, names in self.grants.items()
        }
        object.__setattr__(self, "grants", MappingProxyType(normalized))

    def allows(self, principal: str, tool_name: str) -> bool:
        return tool_name in self.grants.get(principal, frozenset())


class ToolExposureRegistry:
    """Filter provider-visible schemas without replacing Registry authorization."""

    def __init__(self, registry: ToolRegistry, policy: ToolExposurePolicy):
        for principal, names in policy.grants.items():
            for name in names:
                try:
                    registry.resolve(name, principal)
                except ToolRegistryError as error:
                    raise ValueError(
                        "Exposure references an unregistered or unauthorized tool"
                    ) from error
        self._registry = registry
        self._policy = policy

    @property
    def policy_version(self) -> str:
        return self._policy.version

    def is_exposed(self, principal: str, tool_name: str) -> bool:
        return self._policy.allows(principal, tool_name)

    def schemas_for(self, principal: str) -> tuple[dict, ...]:
        return tuple(
            schema
            for schema in self._registry.schemas_for(principal)
            if self._policy.allows(principal, schema["name"])
        )

    def resolve(self, name: str, principal: str):
        """Delegate authorization unchanged for trusted direct Executor calls."""

        return self._registry.resolve(name, principal)


class ExposureEnforcingProvider:
    """Reject guessed hidden tools before the Orchestrator can execute them."""

    def __init__(self, provider: ToolDecisionProvider):
        self._provider = provider

    def decide(self, request: ToolProviderRequest) -> str:
        raw = self._provider.decide(request)
        try:
            decision = parse_tool_decision(raw)
        except ValueError:
            # Preserve the existing orchestrator's invalid-decision handling.
            return raw
        if decision.action != "tool_call":
            return raw
        if request.remaining_tool_calls == 0:
            # Preserve the existing one-call limit verdict on a second request.
            return raw
        visible = {schema["name"] for schema in request.available_tools}
        if decision.tool_call.name in visible:
            return raw
        logger.warning(
            "tool_exposure_rejection %s",
            json.dumps(
                {
                    "event": "hidden_tool_request_rejected",
                    "policy_version": "tool-exposure-policy-v1",
                    "tool_name": decision.tool_call.name,
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
        return ToolCallDecision(
            action="final",
            final_response=HIDDEN_TOOL_RESPONSE,
            tool_call=None,
        ).model_dump_json()
