"""Zero-provider-call readiness checks for durable Planner live acceptance."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
import json
import os
from pathlib import Path
from typing import Any

from backend.main import app
from backend.planning import (
    load_deepseek_planner_settings,
    load_durable_agent_settings,
    load_durable_controlled_planner_settings,
    load_durable_worker_settings,
)

from .infrastructure_v1 import probe_isolated_database
from .suite_v1 import (
    ROOT,
    file_sha256,
    gate_sha256,
    live_plan_sha256,
    load_live_plan,
    suite_sha256,
    verify_freeze,
)


PREFLIGHT_VERSION = "durable-planner-preflight-v1"
DEFAULT_EVIDENCE_NAME = "preflight-v1-001.json"


def _evidence_target_is_safe(root: Path, name: str) -> bool:
    target = root / name
    return (
        root.exists()
        and root.is_dir()
        and not root.is_symlink()
        and target.parent.resolve() == root.resolve()
        and not target.exists()
        and not target.is_symlink()
    )


def _safe_database_probe(probe: Callable[[], Any]) -> dict[str, Any]:
    try:
        value = probe()
    except Exception:
        value = None
    if not isinstance(value, dict):
        return {"status": "unavailable", "error_code": "isolated_database_preflight_failed"}
    return {
        key: value.get(key)
        for key in (
            "status",
            "select_1",
            "dataset_rows",
            "agent_runtime_schema",
            "tool_status",
            "error_code",
        )
    }


def run_preflight(
    *,
    offline_tests_passed: bool,
    offline_test_count: int,
    database_tests_passed: bool,
    database_test_count: int,
    database_probe: Callable[[], Any] = probe_isolated_database,
    evidence_root: Path = ROOT,
    evidence_name: str = DEFAULT_EVIDENCE_NAME,
) -> dict[str, Any]:
    freeze = verify_freeze()
    live_plan = load_live_plan()
    deepseek = load_deepseek_planner_settings()
    durable_planner = load_durable_controlled_planner_settings()
    worker = load_durable_worker_settings()
    agent = load_durable_agent_settings()
    database = _safe_database_probe(database_probe)
    routes = {
        path
        for route in app.routes
        if isinstance((path := getattr(route, "path", None)), str)
    }
    reserve = Decimal(live_plan["maximum_reserved_cost_usd"])
    budget = Decimal(live_plan["budget_limit_usd"])
    historical_path = ROOT.parent / "v1" / "freeze-v1.json"
    checks = {
        "frozen_identity": (
            freeze["suite_sha256"] == suite_sha256()
            and freeze["gate_sha256"] == gate_sha256()
            and freeze["live_plan_sha256"] == live_plan_sha256()
        ),
        "offline_tests": offline_tests_passed and offline_test_count > 0,
        "database_tests": database_tests_passed and database_test_count > 0,
        "feature_flags_default_off": (
            not deepseek.enabled
            and not durable_planner.enabled
            and not worker.enabled
            and not agent.enabled
            and os.getenv(live_plan["live_enable_env"], "").strip().lower()
            not in {"true", "1"}
        ),
        "public_agent_api_absent": all(
            "plan" not in path and "agent" not in path for path in routes
        ),
        "isolated_database_health": (
            database.get("status") == "healthy"
            and database.get("select_1") is True
            and database.get("dataset_rows") == 8
            and database.get("agent_runtime_schema") is True
            and database.get("tool_status") == "success"
        ),
        "api_key_configured": bool(deepseek.api_key.get_secret_value()),
        "cost_reserve_within_budget": reserve <= budget,
        "evidence_path_safe": _evidence_target_is_safe(
            evidence_root, evidence_name
        ),
        "historical_evidence_unchanged": (
            historical_path.exists()
            and freeze["protected_historical_sha256"]
            == file_sha256(historical_path)
        ),
    }
    return {
        "preflight_version": PREFLIGHT_VERSION,
        "status": "READY" if all(checks.values()) else "NOT_READY",
        "suite_sha256": suite_sha256(),
        "gate_sha256": gate_sha256(),
        "live_plan_sha256": live_plan_sha256(),
        "prompt_version": freeze["prompt_version"],
        "prompt_sha256": freeze["prompt_sha256"],
        "schema_sha256": freeze["schema_sha256"],
        "checks": checks,
        "test_results": {
            "offline": {"passed": offline_tests_passed, "count": offline_test_count},
            "database": {"passed": database_tests_passed, "count": database_test_count},
        },
        "database": database,
        "cost_plan": {
            "budget_limit_usd": live_plan["budget_limit_usd"],
            "per_call_reserve_usd": live_plan["per_call_reserve_usd"],
            "maximum_provider_calls_total": live_plan["maximum_provider_calls_total"],
            "maximum_reserved_cost_usd": live_plan["maximum_reserved_cost_usd"],
            "automatic_retries": live_plan["automatic_retries"],
        },
        "runtime": {
            "provider": live_plan["fixed_config"]["provider"],
            "model": live_plan["fixed_config"]["model"],
            "thinking": live_plan["fixed_config"]["thinking"],
            "max_output_tokens": live_plan["fixed_config"]["max_output_tokens"],
            "feature_flags_enabled": False,
            "public_api_enabled": False,
            "api_key_configured": bool(deepseek.api_key.get_secret_value()),
        },
        "paid_api_calls": 0,
        "release_gate_status": "BLOCKED_PENDING_LIVE_ROUNDS",
    }


def write_preflight(report: dict[str, Any], target: Path) -> None:
    if target.parent.resolve() != ROOT.resolve() or target.exists() or target.is_symlink():
        raise ValueError("durable_planner_evidence_target_unsafe")
    target.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
