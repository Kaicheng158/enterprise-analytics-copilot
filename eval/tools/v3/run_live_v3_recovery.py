"""Independent guarded rerun of the frozen Evaluator v3 live plan."""

from __future__ import annotations

import argparse
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from backend.tools.deepseek_adapter import (
    DeepSeekToolDecisionProvider,
    DeepSeekToolSettings,
    TOOL_PROMPT_SHA256,
    TOOL_PROMPT_VERSION,
)
from backend.tools.exposed_catalog import build_exposed_tool_orchestrator
from backend.tools.models import ToolExecutionContext
from backend.tools.orchestration import ToolOrchestrationInput
from eval.tools.suite import build_record, canonical_sha256
from eval.tools.v3.evaluator_v3 import evaluate_record_v3
from eval.tools.v3.exposure_binding_v3 import (
    SOURCE_BINDING_VERSION,
    exposure_metadata_for_case,
    exposure_profile_for_case,
)
from eval.tools.v3.infrastructure_guard_v1 import (
    DatabaseRuntimeHealth,
    DatabaseRuntimeUnavailable,
    require_database_runtime,
)
from eval.tools.v3.release_gate_v3 import evaluate_release_gate_v3, gate_sha256_v3
from eval.tools.v3.run_infrastructure_preflight_v3 import (
    REPORT_DIR as INFRA_REPORT_DIR,
    _verify_historical_live_v3,
    file_sha256,
)
from eval.tools.v3.run_live_v3 import (
    LIVE_CONFIRMATION,
    _aggregate_telemetry,
    _authorize_live,
    _contains_sensitive_value,
    _fresh_prerequisites,
    _load_controls_v3,
    _load_plan as _load_original_plan,
    _security_stop,
    _settings,
    _should_stop_before_case,
    _verify_historical_protection,
    _write_json,
    _write_round,
)
from eval.tools.v3.suite_v3 import load_cases_v3, suite_sha256_v3, verify_freeze_v3
from eval.tools.v2.suite_v2 import verify_freeze_v2
from eval.tools.suite import verify_freeze


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parents[2]
PLAN_PATH = ROOT / "live-regression-recovery-plan-v3.json"
SUMMARY_PATH = ROOT / "live-regression-recovery-summary-v3.json"
RUN_IDS = tuple(f"live-v3-recovery-{index:03d}" for index in range(1, 4))


class DatabaseGuardedProvider:
    """Check PostgreSQL immediately before each paid provider interaction."""

    def __init__(self, delegate: DeepSeekToolDecisionProvider):
        self.delegate = delegate
        self.health_checks: list[DatabaseRuntimeHealth] = []
        self.infrastructure_failure: DatabaseRuntimeHealth | None = None

    @property
    def settings(self):
        return self.delegate.settings

    @property
    def telemetry(self):
        return self.delegate.telemetry

    @property
    def native_tool_calls(self):
        return self.delegate.native_tool_calls

    def decide(self, request):
        try:
            health = require_database_runtime()
        except DatabaseRuntimeUnavailable as error:
            self.infrastructure_failure = error.health
            self.health_checks.append(error.health)
            raise
        self.health_checks.append(health)
        return self.delegate.decide(request)


def _load_recovery_plan() -> tuple[dict[str, Any], dict[str, Any]]:
    original = _load_original_plan()
    plan = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    if plan["plan_version"] != "tool-calling-live-recovery-plan-v3":
        raise RuntimeError("unexpected recovery plan version")
    if plan["original_plan_sha256"] != canonical_sha256(original):
        raise RuntimeError("original frozen plan identity mismatch")
    for field, expected in {
        "suite_sha256": suite_sha256_v3(),
        "gate_sha256": gate_sha256_v3(),
        "evaluator_sha256": file_sha256(ROOT / "evaluator_v3.py"),
        "prompt_sha256": TOOL_PROMPT_SHA256,
        "source_binding_version": SOURCE_BINDING_VERSION,
    }.items():
        if plan[field] != expected:
            raise RuntimeError(f"recovery plan identity mismatch: {field}")
    if plan["fixed_config"] != original["fixed_config"]:
        raise RuntimeError("recovery model configuration changed")
    if plan["live_case_ids"] != original["live_case_ids"]:
        raise RuntimeError("recovery cases changed")
    if plan["rounds"] != 3 or plan["run_ids"] != list(RUN_IDS):
        raise RuntimeError("recovery run count or IDs changed")
    if plan["stop_budget"] != "0.02" or plan["automatic_retries"] != 0:
        raise RuntimeError("recovery budget/retry policy changed")
    if plan["runner_sha256"] != file_sha256(Path(__file__)):
        raise RuntimeError("recovery runner identity mismatch")
    if plan["guard_sha256"] != file_sha256(ROOT / "infrastructure_guard_v1.py"):
        raise RuntimeError("runtime health guard identity mismatch")
    report_path = INFRA_REPORT_DIR / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report_hash = report.pop("evidence_sha256")
    if report_hash != canonical_sha256(report) or report.get("status") != "PASS":
        raise RuntimeError("infrastructure preflight is not valid")
    if plan["infrastructure_preflight_sha256"] != report_hash:
        raise RuntimeError("recovery plan preflight identity mismatch")
    return plan, original


def _run_case_guarded(
    round_id: str,
    case: dict[str, Any],
    settings: DeepSeekToolSettings,
):
    profile = exposure_profile_for_case(case)
    exposure_metadata = exposure_metadata_for_case(case)
    delegate = DeepSeekToolDecisionProvider(settings)
    postgres_required = "get_postgres_sales_summary" in case["tool_policy"]["allowed_tools"]
    provider = DatabaseGuardedProvider(delegate) if postgres_required else delegate
    orchestrator = build_exposed_tool_orchestrator(provider, exposure_profile=profile)
    result = orchestrator.run(
        ToolOrchestrationInput(user_message=case["user_message"]),
        ToolExecutionContext(
            principal=case["principal"],
            correlation_id=f"{round_id}-{case['id'].replace('/', '-')}",
        ),
    )
    telemetry = _aggregate_telemetry(case, provider, result, exposure_metadata)
    health_checks = (
        [item.model_dump(mode="json") for item in provider.health_checks]
        if isinstance(provider, DatabaseGuardedProvider)
        else []
    )
    record = build_record(
        run_id=round_id,
        case_id=case["id"],
        mode="live",
        suite_sha256=suite_sha256_v3(),
        prompt_version=TOOL_PROMPT_VERSION,
        prompt_sha256=TOOL_PROMPT_SHA256,
        user_message=case["user_message"],
        result={
            "orchestration": result.model_dump(mode="json"),
            "provider_calls": [item.model_dump(mode="json") for item in provider.telemetry],
            "native_tool_calls": [item.model_dump(mode="json") for item in provider.native_tool_calls],
            "source_binding": exposure_metadata,
            "database_runtime_health_checks": health_checks,
        },
        telemetry=telemetry,
    )
    reviewed = evaluate_record_v3(case, result, record, purpose="release")
    failure = provider.infrastructure_failure if isinstance(provider, DatabaseGuardedProvider) else None
    return record, reviewed, failure


def run_live(confirmation: str | None) -> dict[str, Any]:
    plan, original = _load_recovery_plan()
    _authorize_live(original, confirmation)
    prerequisites = _fresh_prerequisites(original)
    if SUMMARY_PATH.exists() or any((ROOT / run_id).exists() for run_id in RUN_IDS):
        raise FileExistsError("recovery evidence already exists")
    settings = _settings(original)
    secret = settings.api_key.get_secret_value()
    freeze_before = (verify_freeze(), verify_freeze_v2(), verify_freeze_v3())
    history_before = (_verify_historical_protection(), _verify_historical_live_v3())
    cases = {
        case["id"]: case
        for case in load_cases_v3()["cases"]
        if case["execution_track"] == "live_model"
    }
    reviewed_rounds = []
    round_summaries = []
    cumulative_cost = Decimal("0")
    interactions = 0
    stop_reason = None
    plan_sha = canonical_sha256(plan)
    for round_id in RUN_IDS:
        records = []
        reviewed = []
        for case_id in plan["live_case_ids"]:
            if _should_stop_before_case(cumulative_cost, plan):
                stop_reason = "budget_reserve_would_exceed_limit"
                break
            case = cases[case_id]
            record, review, infrastructure_failure = _run_case_guarded(
                round_id, case, settings
            )
            records.append(record)
            reviewed.append(review)
            interactions += record.telemetry.provider_interactions
            if infrastructure_failure is not None:
                stop_reason = (
                    f"database_runtime_unavailable:{case_id}:"
                    f"{infrastructure_failure.failed_stage}"
                )
            if record.telemetry.estimated_cost is None or not record.telemetry.cost_complete:
                stop_reason = stop_reason or f"cost_evidence_incomplete:{case_id}"
            else:
                cumulative_cost += Decimal(record.telemetry.estimated_cost)
            stop_reason = stop_reason or _security_stop(case, review)
            if cumulative_cost > Decimal(plan["stop_budget"]):
                stop_reason = "budget_limit_exceeded"
            if interactions > plan["maximum_provider_interactions_total"]:
                stop_reason = "provider_interaction_limit_exceeded"
            if _contains_sensitive_value(record.model_dump(mode="json"), secret):
                stop_reason = f"sensitive_evidence_detected:{case_id}"
            if (
                (verify_freeze(), verify_freeze_v2(), verify_freeze_v3()) != freeze_before
                or (_verify_historical_protection(), _verify_historical_live_v3()) != history_before
            ):
                stop_reason = "frozen_or_historical_identity_changed"
            if stop_reason:
                break
        payload = _write_round(round_id, records, reviewed, plan_sha, stop_reason)
        payload["tokens"] = sum(item.record.telemetry.total_tokens for item in reviewed)
        payload["latency_ms"] = sum(item.record.telemetry.latency_ms for item in reviewed)
        payload["estimated_cost"] = format(
            sum(Decimal(item.record.telemetry.estimated_cost or "0") for item in reviewed),
            "f",
        )
        round_summaries.append(payload)
        if reviewed:
            reviewed_rounds.append(reviewed)
        if stop_reason:
            break
    gate = evaluate_release_gate_v3(
        reviewed_rounds,
        control_cases=_load_controls_v3(),
        prerequisites=prerequisites,
    )
    summary = {
        "summary_version": "tool-calling-live-recovery-summary-v3",
        "plan_sha256": plan_sha,
        "original_plan_sha256": plan["original_plan_sha256"],
        "suite_sha256": suite_sha256_v3(),
        "gate_sha256": gate_sha256_v3(),
        "prompt_sha256": TOOL_PROMPT_SHA256,
        "run_ids": list(RUN_IDS),
        "rounds_completed": len(round_summaries),
        "rounds": round_summaries,
        "provider_interactions": interactions,
        "cumulative_estimated_cost": format(cumulative_cost, "f"),
        "stop_reason": stop_reason,
        "gate": gate.model_dump(mode="json"),
    }
    if _contains_sensitive_value(summary, secret):
        raise RuntimeError("recovery summary contains sensitive fields")
    _write_json(SUMMARY_PATH, summary)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirmation")
    args = parser.parse_args()
    print(json.dumps(run_live(args.confirmation), ensure_ascii=False, indent=2, sort_keys=True))
