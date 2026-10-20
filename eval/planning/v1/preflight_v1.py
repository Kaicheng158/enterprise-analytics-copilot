"""Non-paid readiness checks for a future frozen planner live run."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
import json
from pathlib import Path
from typing import Any

from backend.main import app
from backend.planning import (
    load_controlled_planner_settings,
    load_deepseek_planner_settings,
    load_planning_settings,
)
from eval.tools.v3.infrastructure_guard_v1 import check_database_runtime

from .suite_v1 import ROOT, gate_sha256, suite_sha256, verify_freeze


PREFLIGHT_VERSION = "planner-preflight-v1"
LIVE_EVIDENCE_NAME = "live-r1-v1.json"


def _evidence_target_is_safe(root: Path = ROOT) -> bool:
    resolved_root = root.resolve()
    target = root / LIVE_EVIDENCE_NAME
    return (
        root.exists()
        and root.is_dir()
        and not root.is_symlink()
        and target.parent.resolve() == resolved_root
        and not target.exists()
        and not target.is_symlink()
    )


def _safe_database_health(database_probe: Callable[[], Any]) -> dict[str, Any]:
    try:
        result = database_probe()
    except Exception:
        return {"status": "unavailable", "error_code": "database_runtime_unavailable"}
    if hasattr(result, "model_dump"):
        result = result.model_dump(mode="json")
    if not isinstance(result, dict):
        return {"status": "unavailable", "error_code": "database_runtime_unavailable"}
    allowed = {
        "status": result.get("status"),
        "select_1": result.get("select_1"),
        "sales_relation": result.get("sales_relation"),
        "dataset_rows": result.get("dataset_rows"),
        "tool_status": result.get("tool_status"),
        "failed_stage": result.get("failed_stage"),
        "error_code": result.get("error_code"),
    }
    return allowed


def run_preflight(
    *,
    offline_tests_passed: bool,
    offline_test_count: int,
    database_tests_passed: bool,
    database_test_count: int,
    database_probe: Callable[[], Any] = check_database_runtime,
    evidence_root: Path = ROOT,
) -> dict[str, Any]:
    """Return safe readiness metadata and never call a model provider."""

    freeze = verify_freeze()
    plan = json.loads((ROOT / "live-plan-v1.json").read_text())
    deepseek = load_deepseek_planner_settings()
    bounded = load_planning_settings()
    controlled = load_controlled_planner_settings()
    database = _safe_database_health(database_probe)
    route_paths = {
        path
        for route in app.routes
        if isinstance((path := getattr(route, "path", None)), str)
    }

    reserve = Decimal(plan["maximum_reserved_cost_usd"])
    budget = Decimal(plan["budget_limit_usd"])
    checks = {
        "frozen_identity": (
            freeze["suite_sha256"] == suite_sha256()
            and freeze["gate_sha256"] == gate_sha256()
        ),
        "offline_tests": offline_tests_passed and offline_test_count > 0,
        "database_tests": database_tests_passed and database_test_count > 0,
        "feature_flags_default_off": not bounded.enabled and not controlled.enabled and not deepseek.enabled,
        "public_planner_api_absent": all("plan" not in path for path in route_paths),
        "database_health": (
            database.get("status") == "healthy"
            and database.get("select_1") is True
            and database.get("sales_relation") is True
            and database.get("tool_status") == "success"
        ),
        "api_key_configured": bool(deepseek.api_key.get_secret_value()),
        "cost_reserve_within_budget": reserve <= budget,
        "evidence_path_safe": _evidence_target_is_safe(evidence_root),
    }
    return {
        "preflight_version": PREFLIGHT_VERSION,
        "status": "READY" if all(checks.values()) else "NOT_READY",
        "suite_sha256": suite_sha256(),
        "gate_sha256": gate_sha256(),
        "prompt_sha256": freeze["prompt_sha256"],
        "schema_sha256": freeze["schema_sha256"],
        "checks": checks,
        "test_results": {
            "offline": {"passed": offline_tests_passed, "count": offline_test_count},
            "database": {"passed": database_tests_passed, "count": database_test_count},
        },
        "database": database,
        "cost_plan": {
            "budget_limit_usd": plan["budget_limit_usd"],
            "per_call_reserve_usd": plan["per_call_reserve_usd"],
            "maximum_provider_calls_total": plan["maximum_provider_calls_total"],
            "maximum_reserved_cost_usd": plan["maximum_reserved_cost_usd"],
            "automatic_retries": plan["automatic_retries"],
        },
        "runtime": {
            "provider": plan["fixed_config"]["provider"],
            "model": plan["fixed_config"]["model"],
            "thinking": plan["fixed_config"]["thinking"],
            "max_output_tokens": plan["fixed_config"]["max_output_tokens"],
            "planner_feature_enabled": deepseek.enabled,
            "public_api_enabled": False,
            "api_key_configured": bool(deepseek.api_key.get_secret_value()),
        },
        "paid_api_calls": 0,
        "release_gate_status": "BLOCKED_PENDING_LIVE_ROUNDS",
    }


def write_preflight(report: dict[str, Any], target: Path) -> None:
    """Create a local evidence file once; never overwrite prior evidence."""

    root = ROOT.resolve()
    if target.parent.resolve() != root or target.exists() or target.is_symlink():
        raise ValueError("planner_evidence_target_unsafe")
    target.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
