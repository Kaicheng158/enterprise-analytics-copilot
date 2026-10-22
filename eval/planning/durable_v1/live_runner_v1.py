"""Explicitly authorized live runner for frozen durable Planner acceptance."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Callable

from backend.planning import (
    DeepSeekPlannerSettings,
    DeepSeekStructuredPlannerTransport,
    DurableAgentSettings,
    DurableControlledPlannerSettings,
    DurableDeepSeekIntegrationSettings,
    DurablePlannerError,
    DurableWorkerSettings,
    PlanValidator,
    PlannerCostGuard,
    PlanningRequest,
    build_durable_deepseek_agent_runtime,
)
from backend.planning.checkpoint_store import PostgresCheckpointStore
from backend.planning.deepseek_planner_adapter import load_deepseek_planner_settings

from .infrastructure_v1 import check_database, isolated_database
from .release_gate_v1 import evaluate_gate
from .suite_v1 import (
    ROOT,
    file_sha256,
    gate_sha256,
    live_plan_sha256,
    load_gate,
    suite_sha256,
    validate_suite,
    verify_freeze,
)


LIVE_ENV = "DURABLE_PLANNER_LIVE_ACCEPTANCE_ENABLED"
LIVE_CONFIRMATION = "RUN_DURABLE_PLANNER_V1_LIVE"
PREFLIGHT_PATH = ROOT / "preflight-v1-001.json"
SUMMARY_PATH = ROOT / "live-summary-v1.json"
PROTECTED_HISTORY = ROOT.parent / "v1" / "freeze-v1.json"


def _load_execution_plan() -> tuple[dict, list[dict]]:
    freeze = verify_freeze()
    _, suite, gate, plan = validate_suite()
    expected_config = {
        "provider": "deepseek",
        "model": "deepseek-flash",
        "thinking": "disabled",
        "max_output_tokens": 800,
        "max_provider_calls": 1,
        "automatic_retries": 0,
    }
    if plan["status"] != "preregistered_before_paid_execution":
        raise RuntimeError("durable_live_plan_not_preregistered")
    if plan["fixed_config"] != expected_config or gate["fixed_live_config"] != expected_config:
        raise RuntimeError("durable_live_config_mismatch")
    if plan["live_enable_env"] != LIVE_ENV or plan["live_confirmation"] != LIVE_CONFIRMATION:
        raise RuntimeError("durable_live_authorization_guard_mismatch")
    if plan["rounds"] != 2 or plan["maximum_provider_calls_total"] != 6:
        raise RuntimeError("durable_live_call_plan_mismatch")
    if Decimal(plan["maximum_reserved_cost_usd"]) > Decimal(plan["budget_limit_usd"]):
        raise RuntimeError("durable_live_budget_invalid")
    if freeze["suite_sha256"] != suite_sha256():
        raise RuntimeError("durable_live_suite_identity_mismatch")
    return plan, suite["cases"]


def _authorize_live(plan: dict, confirmation: str | None) -> None:
    if os.getenv(plan["live_enable_env"], "").strip().lower() != "true":
        raise PermissionError("durable_planner_paid_environment_guard_disabled")
    if confirmation != plan["live_confirmation"]:
        raise PermissionError("durable_planner_paid_confirmation_missing")


def _provider_settings() -> DeepSeekPlannerSettings:
    local = load_deepseek_planner_settings()
    if not local.api_key.get_secret_value():
        raise RuntimeError("durable_planner_provider_not_configured")
    return DeepSeekPlannerSettings(
        enabled=True,
        api_key=local.api_key,
        model="deepseek-flash",
        timeout_seconds=30,
        max_output_tokens=800,
        thinking="disabled",
    )


def _runtime_settings() -> DurableDeepSeekIntegrationSettings:
    return DurableDeepSeekIntegrationSettings(
        planner=DurableControlledPlannerSettings(enabled=True, lease_ttl_seconds=10),
        worker=DurableWorkerSettings(
            enabled=True,
            lease_ttl_seconds=10,
            heartbeat_interval_ms=1_000,
            operation_timeout_ms=30_000,
        ),
        agent=DurableAgentSettings(enabled=True, max_steps_per_run=8),
    )


def _review(case, creation, duplicate, result, provider, store) -> dict:
    plan = creation.execution.plan
    request = PlanningRequest.model_validate(case["request"], strict=True)
    validation = PlanValidator().validate(plan)
    tool_steps = [step for step in plan.steps if step.kind == "tool"]
    exact_arguments = [step.tool_call.arguments for step in tool_steps] == [
        {"period": request.baseline_period, "region": request.region},
        {"period": request.comparison_period, "region": request.region},
    ]
    output = result.output.model_dump(mode="json") if result.output else None
    expected = case["expected"]
    accuracy = bool(
        output
        and all(output[key] == value for key, value in expected.items())
        and output["baseline_period"] == request.baseline_period
        and output["comparison_period"] == request.comparison_period
        and output["region"] == request.region
    )
    receipts = store.list_receipts(creation.execution.execution_id)
    audit = store.audit_execution_consistency(creation.execution.execution_id)
    hard = {
        "plan_schema_valid": validation.status == "valid",
        "request_binding": (
            plan.task == request.task
            and plan.baseline_period == request.baseline_period
            and plan.comparison_period == request.comparison_period
            and plan.region == request.region
        ),
        "tool_authorization": bool(tool_steps)
        and {step.tool_call.name for step in tool_steps}
        == {"get_postgres_sales_summary"},
        "execution_bounds": len(plan.steps) == 3 and len(tool_steps) == 2 and exact_arguments,
        "provider_call_limit": provider.calls == 1,
        "idempotent_submission": (
            creation.created
            and not duplicate.created
            and creation.execution.execution_id == duplicate.execution.execution_id
            and provider.calls == 1
        ),
        "durable_completion": result.status == "completed" and result.output is not None,
        "receipt_consistency": (
            len(receipts) == 3
            and all(item.status == "succeeded" for item in receipts)
            and audit.completed_receipt_count == 3
        ),
        "result_accuracy": accuracy,
        "source_consistency": bool(
            output
            and output["currency"] == "USD"
            and output["source_relation"] == "analytics.sales_orders"
            and output["dataset_version"] == "synthetic-sales-db-v1"
        ),
    }
    quality = {
        "task_completeness": 2 if hard["durable_completion"] and accuracy else 0,
        "plan_relevance_minimality": 2 if hard["execution_bounds"] else 0,
        "description_clarity": 2 if all(step.description.strip() for step in plan.steps) else 0,
    }
    normalized = sum(quality.values()) / (2 * len(quality))
    core_blocker = not hard["durable_completion"] or not hard["result_accuracy"]
    verdict = "PASS" if all(hard.values()) and not core_blocker and normalized >= 0.85 else "FAIL"
    return {
        "hard_invariants": hard,
        "core_blocker": core_blocker,
        "quality": quality,
        "normalized_quality": normalized,
        "verdict": verdict,
    }


def _write_json(path: Path, payload: dict) -> None:
    if path.exists() or path.is_symlink() or path.parent.resolve() != ROOT.resolve():
        raise FileExistsError("durable_planner_evidence_target_unsafe_or_exists")
    data = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=ROOT, delete=False) as handle:
        handle.write(data)
        temporary = Path(handle.name)
    try:
        os.chmod(temporary, 0o600)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _contains_secret(payload: Any, secret: str) -> bool:
    serialized = json.dumps(payload, ensure_ascii=False)
    return secret in serialized or any(
        item in serialized for item in ('"api_key"', '"headers"', "Authorization", "Bearer ")
    )


def _round_result(records: list[dict]) -> dict:
    gate = load_gate()
    hard = all(all(item["review"]["hard_invariants"].values()) for item in records)
    blockers = sum(bool(item["review"]["core_blocker"]) for item in records)
    quality = sum(item["review"]["normalized_quality"] for item in records) / len(records)
    all_cases_meet_quality = all(
        item["review"]["normalized_quality"] >= gate["minimum_case_normalized_quality"]
        for item in records
    )
    return {
        "verdict": "PASS"
        if hard
        and blockers == 0
        and quality >= gate["minimum_round_normalized_quality"]
        and all_cases_meet_quality
        else "FAIL",
        "hard_invariants_100_percent": hard,
        "core_blockers": blockers,
        "normalized_quality": quality,
        "all_cases_meet_quality_threshold": all_cases_meet_quality,
        "repeated_severe_quality_issue": False,
    }


def run_live(
    confirmation: str | None,
    *,
    opener_factory: Callable[[], Callable] | None = None,
) -> dict:
    plan, cases = _load_execution_plan()
    _authorize_live(plan, confirmation)
    preflight = json.loads(PREFLIGHT_PATH.read_text())
    if preflight.get("status") != "READY":
        raise RuntimeError("durable_planner_preflight_not_ready")
    if SUMMARY_PATH.exists() or any((ROOT / f"live-v1-{index:03d}.json").exists() for index in (1, 2)):
        raise FileExistsError("durable_planner_live_evidence_already_exists")
    settings = _provider_settings()
    secret = settings.api_key.get_secret_value()
    protected_hash = file_sha256(PROTECTED_HISTORY)
    guard = PlannerCostGuard(
        budget_usd=Decimal(plan["budget_limit_usd"]),
        per_call_reserve_usd=Decimal(plan["per_call_reserve_usd"]),
        max_calls=plan["maximum_provider_calls_total"],
    )
    rounds = []
    stop_reason = None
    for round_index in (1, 2):
        run_id = f"live-v1-{round_index:03d}"
        records = []
        round_calls_before = guard.calls_started
        with isolated_database() as connection_factory:
            for case in cases:
                if verify_freeze()["suite_sha256"] != suite_sha256() or file_sha256(PROTECTED_HISTORY) != protected_hash:
                    stop_reason = "durable_planner_frozen_or_historical_identity_changed"
                    break
                if check_database(connection_factory).get("status") != "healthy":
                    stop_reason = "durable_planner_isolated_database_unavailable"
                    break
                request = PlanningRequest.model_validate(case["request"], strict=True)
                opener = opener_factory() if opener_factory else None
                kwargs = {"cost_guard": guard}
                if opener is not None:
                    kwargs["opener"] = opener
                transport = DeepSeekStructuredPlannerTransport(settings, **kwargs)
                store = PostgresCheckpointStore(
                    connection_factory,
                    schema="agent_runtime",
                    idempotency_secret=b"durable-live-v1-server-owned-secret",
                )
                agent, provider = build_durable_deepseek_agent_runtime(
                    transport,
                    store,
                    settings=_runtime_settings(),
                    connection_factory=connection_factory,
                )
                key = f"{run_id}-{case['id'].replace('/', '-')}"
                try:
                    creation = agent.submit(
                        request,
                        scope_key="acceptance:analytics-copilot",
                        idempotency_key=key,
                        now=datetime.now(UTC),
                    )
                    duplicate = agent.submit(
                        request,
                        scope_key="acceptance:analytics-copilot",
                        idempotency_key=key,
                        now=datetime.now(UTC),
                    )
                    result = agent.run_to_terminal(
                        creation.execution.execution_id,
                        worker_id_prefix=f"accept-{round_index}",
                    )
                    review = _review(case, creation, duplicate, result, provider, store)
                    record = {
                        "run_id": run_id,
                        "case_id": case["id"],
                        "request": case["request"],
                        "generation": provider.last_response.model_dump(mode="json"),
                        "plan_sha256": creation.execution.plan_sha256,
                        "execution_id": str(creation.execution.execution_id),
                        "final_result": result.output.model_dump(mode="json") if result.output else None,
                        "review": review,
                    }
                except DurablePlannerError as error:
                    record = {
                        "run_id": run_id,
                        "case_id": case["id"],
                        "request": case["request"],
                        "generation": provider.last_response.model_dump(mode="json") if provider.last_response else None,
                        "error_code": error.code,
                        "review": {
                            "hard_invariants": {name: False for name in case["hard_invariants"]},
                            "core_blocker": True,
                            "quality": {name: 0 for name in case["quality_dimensions"]},
                            "normalized_quality": 0,
                            "verdict": "FAIL",
                        },
                    }
                    if guard.stopped:
                        stop_reason = "durable_planner_live_guard_stopped"
                if _contains_secret(record, secret):
                    raise RuntimeError("durable_planner_sensitive_evidence_detected")
                records.append(record)
                if stop_reason:
                    break
        round_result = _round_result(records) if len(records) == len(cases) else {
            "verdict": "FAIL",
            "hard_invariants_100_percent": False,
            "core_blockers": len(cases) - len(records),
            "normalized_quality": 0,
            "repeated_severe_quality_issue": False,
        }
        round_payload = {
            "evidence_version": "durable-planner-live-evidence-v1",
            "run_id": run_id,
            "suite_sha256": suite_sha256(),
            "gate_sha256": gate_sha256(),
            "live_plan_sha256": live_plan_sha256(),
            "records": records,
            "tokens": {
                "input": sum((item.get("generation") or {}).get("usage", {}).get("input_tokens", 0) for item in records),
                "output": sum((item.get("generation") or {}).get("usage", {}).get("output_tokens", 0) for item in records),
                "total": sum((item.get("generation") or {}).get("usage", {}).get("total_tokens", 0) for item in records),
            },
            "latency_ms": sum((item.get("generation") or {}).get("latency_ms", 0) for item in records),
            "estimated_cost_usd": format(sum(Decimal((item.get("generation") or {}).get("estimated_cost_usd") or "0") for item in records), "f"),
            "result": round_result,
            "stop_reason": stop_reason,
            "paid_api_calls": guard.calls_started - round_calls_before,
        }
        _write_json(ROOT / f"{run_id}.json", round_payload)
        rounds.append(round_payload)
        if stop_reason:
            break
    gate = evaluate_gate(preflight, [item["result"] for item in rounds])
    if stop_reason:
        gate = {**gate, "verdict": "FAIL", "blockers": [*gate["blockers"], stop_reason]}
    summary = {
        "summary_version": "durable-planner-live-summary-v1",
        "suite_sha256": suite_sha256(),
        "gate_sha256": gate_sha256(),
        "live_plan_sha256": live_plan_sha256(),
        "rounds_completed": len(rounds),
        "provider_calls": guard.calls_started,
        "tokens": {
            key: sum(item["tokens"][key] for item in rounds)
            for key in ("input", "output", "total")
        },
        "latency_ms": sum(item["latency_ms"] for item in rounds),
        "estimated_cost_usd": format(guard.spent_usd, "f"),
        "stop_reason": stop_reason,
        "rounds": [
            {"run_id": item["run_id"], "result": item["result"], "tokens": item["tokens"], "latency_ms": item["latency_ms"], "estimated_cost_usd": item["estimated_cost_usd"]}
            for item in rounds
        ],
        "gate": gate,
    }
    if _contains_secret(summary, secret):
        raise RuntimeError("durable_planner_summary_contains_secret")
    _write_json(SUMMARY_PATH, summary)
    return summary
