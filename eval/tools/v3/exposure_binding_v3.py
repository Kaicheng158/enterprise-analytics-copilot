"""Server-owned exposure-profile binding for future frozen v3 live runs.

This file does not alter the frozen suite or evaluator. It maps each existing
case contract to one of the product exposure profiles before a provider request
is built, so source choice is enforced by the server rather than by prompting.
"""

from typing import Any

from backend.tools.exposed_catalog import ToolExposureProfile

SOURCE_BINDING_VERSION = "tool-source-binding-v1"

POSTGRES_TOOL = "get_postgres_sales_summary"
IN_MEMORY_TOOL = "get_sales_summary"


def exposure_profile_for_case(case: dict[str, Any]) -> ToolExposureProfile:
    policy = case["tool_policy"]
    mode = policy["mode"]
    allowed = frozenset(policy["allowed_tools"])
    if mode == "required":
        if allowed == {POSTGRES_TOOL}:
            return "production"
        if allowed == {IN_MEMORY_TOOL}:
            return "evaluation_in_memory"
        raise ValueError("Required v3 case has no supported authoritative source")
    if allowed:
        raise ValueError("Non-required v3 case cannot expose an allowed source")
    # Forbidden and N/A cases retain the production catalog. The evaluator then
    # verifies that the model does not execute a tool when the case forbids it.
    return "production"


def exposure_metadata_for_case(case: dict[str, Any]) -> dict[str, Any]:
    profile = exposure_profile_for_case(case)
    visible = (
        [POSTGRES_TOOL]
        if profile == "production"
        else [IN_MEMORY_TOOL]
    )
    return {
        "tool_source_binding_version": SOURCE_BINDING_VERSION,
        "tool_exposure_policy_version": "tool-exposure-policy-v1",
        "tool_exposure_profile": profile,
        "visible_tools": visible,
    }
