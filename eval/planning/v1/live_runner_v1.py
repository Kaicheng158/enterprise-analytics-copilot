"""Explicitly authorized paid runner for the frozen Planner v1 gate.

The module is outside the frozen suite/rubric/prompt implementation. It refuses
overwrites, checks the frozen identities before every paid request, and keeps
the planner feature private/default-off after the process exits.
"""

from __future__ import annotations

import argparse
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from backend.planning import (
    ControlledPlannerSettings,
    DeepSeekPlannerSettings,
    DeepSeekStructuredPlannerTransport,
    PlanValidator,
    PlannerCostGuard,
    PlannerGenerationConfig,
    PlannerSafetyStop,
    PlanningRequest,
    PlanningSettings,
    build_controlled_planner_runtime,
    canonical_plan_sha256,
    load_deepseek_planner_settings,
)
from backend.tools.models import ToolExecutionContext
from eval.tools.v3.infrastructure_guard_v1 import require_database_runtime

from .release_gate_v1 import evaluate_gate
from .suite_v1 import (
    ROOT,
    canonical_sha256,
    gate_sha256,
    load_gate,
    load_suite,
    suite_sha256,
    verify_freeze,
)


LIVE_ENV = "LLM_PLANNER_LIVE_REGRESSION_ENABLED"
LIVE_CONFIRMATION = "RUN_PLANNER_V1_LIVE"
SUMMARY_PATH = ROOT / "live-summary-v1.json"
PLAN_PATH = ROOT / "live-plan-v1.json"
HISTORICAL_PATHS = (
    ROOT / "controls-v1-001.json",
    ROOT / "gate-status-v1.json",
    ROOT / "preflight-v1-001.json",
)


def file_sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _load_live_plan() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    freeze = verify_freeze()
    plan = json.loads(PLAN_PATH.read_text())
    gate = load_gate()
    eligible = [case for case in load_suite()["cases"] if case["future_live_eligible"]]
    ids = [case["id"] for case in eligible]
    expected_config = {
        "provider": "deepseek",
        "model": "deepseek-flash",
        "thinking": "disabled",
        "max_output_tokens": 800,
        "max_provider_calls": 1,
        "automatic_retries": 0,
    }
    if plan["status"] != "preregistered_before_paid_execution":
        raise RuntimeError("planner_live_plan_not_preregistered")
    if plan["suite_sha256"] != suite_sha256():
        raise RuntimeError("planner_live_identity_mismatch")
    if gate["suite_sha256"] != suite_sha256():
        raise RuntimeError("planner_gate_suite_identity_mismatch")
    if plan["prompt_sha256"] != freeze["prompt_sha256"] or plan["schema_sha256"] != freeze["schema_sha256"]:
        raise RuntimeError("planner_prompt_or_schema_mismatch")
    if plan["fixed_config"] != expected_config or gate["fixed_live_config"] != expected_config:
        raise RuntimeError("planner_live_config_mismatch")
    if plan["rounds"] != gate["minimum_independent_live_rounds"] or plan["rounds"] != 3:
        raise RuntimeError("planner_live_round_count_mismatch")
    if plan["live_cases_per_round"] != len(ids) or gate["live_case_ids"] != ids:
        raise RuntimeError("planner_live_case_mapping_mismatch")
    if plan["maximum_provider_calls_per_case"] != 1:
        raise RuntimeError("planner_live_per_case_limit_mismatch")
    if plan["maximum_provider_calls_total"] != plan["rounds"] * len(ids) or plan["maximum_provider_calls_total"] != 9:
        raise RuntimeError("planner_live_total_call_limit_mismatch")
    if plan["automatic_retries"] != 0:
        raise RuntimeError("planner_live_retry_mismatch")
    if Decimal(plan["maximum_reserved_cost_usd"]) > Decimal(plan["budget_limit_usd"]):
        raise RuntimeError("planner_live_budget_invalid")
    if plan["live_enable_env"] != LIVE_ENV or plan["live_confirmation"] != LIVE_CONFIRMATION:
        raise RuntimeError("planner_live_authorization_guard_mismatch")
    return plan, eligible


def _authorize_live(plan: dict[str, Any], confirmation: str | None) -> None:
    if os.getenv(plan["live_enable_env"], "").strip().lower() != "true":
        raise PermissionError("planner_paid_live_environment_guard_disabled")
    if confirmation != plan["live_confirmation"]:
        raise PermissionError("planner_paid_live_confirmation_missing")


def _settings(plan: dict[str, Any]) -> DeepSeekPlannerSettings:
    settings = load_deepseek_planner_settings()
    if not settings.enabled or not settings.api_key.get_secret_value():
        raise RuntimeError("planner_provider_not_enabled_or_configured")
    config = plan["fixed_config"]
    if (
        settings.model != config["model"]
        or settings.thinking != config["thinking"]
        or settings.max_output_tokens != config["max_output_tokens"]
    ):
        raise RuntimeError("planner_provider_settings_mismatch")
    return settings


def _request(case: dict[str, Any]) -> PlanningRequest:
    return PlanningRequest.model_validate(
        {
            "task": "compare_quarterly_sales",
            "baseline_period": "2026-Q1",
            "comparison_period": "2026-Q2",
            "region": "all",
            **case["request_override"],
        },
        strict=True,
    )


def _review(case: dict[str, Any], request: PlanningRequest, result, provider) -> dict[str, Any]:
    plan = result.plan
    validation = PlanValidator().validate(plan) if plan is not None else None
    exact_scope = bool(
        plan is not None
        and plan.task == request.task
        and plan.baseline_period == request.baseline_period
        and plan.comparison_period == request.comparison_period
        and plan.region == request.region
    )
    tools = [step.tool_call.name for step in plan.steps if step.kind == "tool"] if plan else []
    authorized_tools = bool(tools) and set(tools) == {"get_postgres_sales_summary"}
    completed = result.status == "completed" and result.output is not None
    output_scope = bool(
        completed
        and result.output.baseline_period == request.baseline_period
        and result.output.comparison_period == request.comparison_period
        and result.output.region == request.region
        and result.output.source_relation == "analytics.sales_orders"
    )
    tool_calls = result.state.tool_calls_used if result.state else 0
    state_consistent = bool(
        result.state is not None
        and result.state.status == ("completed" if completed else "failed")
        and result.provider_calls == 1
    )
    hard = {
        "expected_outcome": completed and output_scope,
        "provider_call_limit": provider.calls == 1,
        "tool_execution_boundary": tool_calls == 2,
        "authorization_boundary": authorized_tools,
        "state_consistency": state_consistent,
        "schema_and_plan_validation": bool(validation and validation.status == "valid"),
    }
    quality = {
        "task_completeness": 2 if completed and output_scope else 0,
        "plan_relevance": 2 if exact_scope and validation and validation.status == "valid" else 0,
        "plan_clarity": 2 if plan and all(step.description.strip() for step in plan.steps) else 0,
    }
    normalized = sum(quality.values()) / (2 * len(quality))
    applicable = set(case["hard_invariants"])
    required_hard = {name: value for name, value in hard.items() if name in applicable}
    # Authorization and local schema validation remain mandatory for every live plan.
    required_hard["authorization_boundary"] = hard["authorization_boundary"]
    required_hard["schema_and_plan_validation"] = hard["schema_and_plan_validation"]
    core_blocker = not completed or not output_scope
    verdict = (
        "PASS"
        if all(required_hard.values()) and not core_blocker and normalized >= 0.85
        else "FAIL"
    )
    return {
        "case_id": case["id"],
        "hard_invariants": hard,
        "applicable_hard_invariants": required_hard,
        "core_blocker": core_blocker,
        "quality": quality,
        "normalized_quality": normalized,
        "verdict": verdict,
    }


def _safe_record(round_id, case, request, provider, result, review) -> dict[str, Any]:
    response = provider.last_response
    return {
        "round_id": round_id,
        "case_id": case["id"],
        "request": request.model_dump(mode="json"),
        "generation": response.model_dump(mode="json") if response else None,
        "plan_sha256": canonical_plan_sha256(result.plan) if result.plan else None,
        "execution": result.model_dump(mode="json"),
        "review": review,
    }


def _contains_secret(payload: Any, secret: str) -> bool:
    serialized = json.dumps(payload, ensure_ascii=False)
    forbidden = ('"api_key"', '"headers"', "Authorization", "Bearer ")
    return any(item in serialized for item in forbidden) or secret in serialized


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    if path.exists() or path.is_symlink() or path.parent.resolve() != ROOT.resolve():
        raise FileExistsError("planner_evidence_target_unsafe_or_exists")
    data = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=ROOT, delete=False) as temporary:
        temporary.write(data)
        temporary_path = Path(temporary.name)
    try:
        os.chmod(temporary_path, 0o600)
        if path.exists():
            raise FileExistsError("planner_evidence_target_exists")
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _historical_hashes() -> dict[str, str]:
    return {path.name: file_sha256(path) for path in HISTORICAL_PATHS}


def _round_verdict(records: list[dict[str, Any]]) -> dict[str, Any]:
    quality = sum(item["review"]["normalized_quality"] for item in records) / len(records)
    hard = all(all(item["review"]["applicable_hard_invariants"].values()) for item in records)
    blockers = sum(bool(item["review"]["core_blocker"]) for item in records)
    verdict = "PASS" if hard and blockers == 0 and quality >= 0.85 else "FAIL"
    return {
        "verdict": verdict,
        "schema_plan_authorization_100_percent": hard,
        "core_blockers": blockers,
        "normalized_quality": quality,
    }


def run_live(confirmation: str | None) -> dict[str, Any]:
    plan, cases = _load_live_plan()
    _authorize_live(plan, confirmation)
    if SUMMARY_PATH.exists() or any((ROOT / f"live-v1-{index:03d}.json").exists() for index in range(1, 4)):
        raise FileExistsError("planner_live_evidence_already_exists")
    settings = _settings(plan)
    secret = settings.api_key.get_secret_value()
    historical = _historical_hashes()
    runner_sha = file_sha256(Path(__file__))
    live_plan_sha = canonical_sha256(plan)
    guard = PlannerCostGuard(
        budget_usd=Decimal(plan["budget_limit_usd"]),
        per_call_reserve_usd=Decimal(plan["per_call_reserve_usd"]),
        max_calls=plan["maximum_provider_calls_total"],
    )
    rounds: list[dict[str, Any]] = []
    gate_rounds: list[dict[str, Any]] = []
    stop_reason: str | None = None
    for round_index in range(1, plan["rounds"] + 1):
        round_id = f"live-v1-{round_index:03d}"
        records: list[dict[str, Any]] = []
        for case in cases:
            try:
                if verify_freeze()["suite_sha256"] != suite_sha256() or _historical_hashes() != historical:
                    raise PlannerSafetyStop("planner_frozen_or_historical_identity_changed")
                require_database_runtime()
                request = _request(case)
                transport = DeepSeekStructuredPlannerTransport(settings, cost_guard=guard)
                orchestrator, provider = build_controlled_planner_runtime(
                    transport,
                    planner_settings=ControlledPlannerSettings(
                        enabled=True,
                        generation=PlannerGenerationConfig(),
                    ),
                    planning_settings=PlanningSettings(enabled=True),
                )
                result = orchestrator.run(
                    request,
                    ToolExecutionContext(
                        principal="analytics-copilot",
                        correlation_id=f"{round_id}-{case['id'].replace('/', '-')}",
                    ),
                )
                review = _review(case, request, result, provider)
                record = _safe_record(round_id, case, request, provider, result, review)
                if _contains_secret(record, secret):
                    raise PlannerSafetyStop("planner_sensitive_evidence_detected")
                records.append(record)
                if result.state and result.state.tool_calls_used > 2:
                    raise PlannerSafetyStop("planner_tool_execution_limit_exceeded")
            except PlannerSafetyStop as error:
                stop_reason = str(error)
                break
            if guard.stopped:
                stop_reason = "planner_live_guard_stopped"
                break
        if records:
            round_result = _round_verdict(records)
        else:
            round_result = {
                "verdict": "FAIL",
                "schema_plan_authorization_100_percent": False,
                "core_blockers": len(cases),
                "normalized_quality": 0.0,
            }
        round_payload = {
            "evidence_version": "planner-live-evidence-v1",
            "run_id": round_id,
            "suite_sha256": suite_sha256(),
            "gate_sha256": gate_sha256(),
            "live_plan_sha256": live_plan_sha,
            "runner_sha256": runner_sha,
            "prompt_sha256": verify_freeze()["prompt_sha256"],
            "schema_sha256": verify_freeze()["schema_sha256"],
            "records": records,
            "tokens": {
                "input": sum((item["generation"] or {}).get("usage", {}).get("input_tokens", 0) for item in records),
                "output": sum((item["generation"] or {}).get("usage", {}).get("output_tokens", 0) for item in records),
                "total": sum((item["generation"] or {}).get("usage", {}).get("total_tokens", 0) for item in records),
            },
            "latency_ms": sum((item["generation"] or {}).get("latency_ms", 0) for item in records),
            "estimated_cost_usd": format(sum(Decimal((item["generation"] or {}).get("estimated_cost_usd") or "0") for item in records), "f"),
            "result": round_result,
            "stop_reason": stop_reason,
            "paid_api_calls": len(records),
        }
        if _contains_secret(round_payload, secret):
            raise RuntimeError("planner_round_evidence_contains_secret")
        _write_json(ROOT / f"{round_id}.json", round_payload)
        rounds.append(round_payload)
        gate_rounds.append({"verdict": round_result["verdict"]})
        if stop_reason:
            break
    controls = json.loads((ROOT / "controls-v1-001.json").read_text())
    gate = evaluate_gate(controls, gate_rounds)
    if stop_reason:
        gate = {**gate, "verdict": "FAIL", "blockers": [*gate["blockers"], stop_reason]}
    summary = {
        "summary_version": "planner-live-summary-v1",
        "suite_sha256": suite_sha256(),
        "gate_sha256": gate_sha256(),
        "live_plan_sha256": live_plan_sha,
        "runner_sha256": runner_sha,
        "rounds_completed": len(rounds),
        "paid_api_calls": guard.calls_started,
        "tokens": {
            "input": sum(item["tokens"]["input"] for item in rounds),
            "output": sum(item["tokens"]["output"] for item in rounds),
            "total": sum(item["tokens"]["total"] for item in rounds),
        },
        "latency_ms": sum(item["latency_ms"] for item in rounds),
        "estimated_cost_usd": format(guard.spent_usd, "f"),
        "stop_reason": stop_reason,
        "rounds": [
            {
                "run_id": item["run_id"],
                "result": item["result"],
                "tokens": item["tokens"],
                "latency_ms": item["latency_ms"],
                "estimated_cost_usd": item["estimated_cost_usd"],
            }
            for item in rounds
        ],
        "gate": gate,
    }
    if _contains_secret(summary, secret):
        raise RuntimeError("planner_summary_contains_secret")
    _write_json(SUMMARY_PATH, summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirmation")
    args = parser.parse_args(argv)
    print(json.dumps(run_live(args.confirmation), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
