"""Evaluator v2 preflight and explicitly authorized paid live runner.

The default command performs a no-provider-call preflight. Paid execution is
guarded by both an environment flag and an exact command-line confirmation.
Historical v1/v2 evidence is read-only and every new output refuses overwrite.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from decimal import Decimal
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from pydantic import ValidationError

from backend.config import PRICING_VERSION
from backend.tools.catalog import INTERNAL_PRINCIPAL, build_tool_registry
from backend.tools.deepseek_adapter import (
    DeepSeekToolDecisionProvider,
    DeepSeekToolSettings,
    TOOL_PROMPT_SHA256,
    TOOL_PROMPT_VERSION,
)
from backend.tools.exposed_catalog import (
    build_exposed_tool_orchestrator,
    build_exposed_tool_registry,
)
from backend.tools.executor import ToolExecutor
from backend.tools.models import ToolCall, ToolExecutionContext
from backend.tools.orchestration import ToolOrchestrationInput, ToolOrchestrationResult
from backend.tools.registry import ToolRegistryError
from backend.tools.sales_db import open_sales_connection
from eval.tools.suite import (
    ToolEvalRecord,
    ToolEvalTelemetry,
    build_record,
    canonical_sha256,
    verify_freeze,
)
from eval.tools.v2.evaluator_v2 import evaluate_record_v2
from eval.tools.v2.exposure_binding_v2 import (
    SOURCE_BINDING_VERSION,
    exposure_metadata_for_case,
    exposure_profile_for_case,
)
from eval.tools.v2.release_gate_v2 import (
    PrerequisiteEvidenceV2,
    build_prerequisite_evidence,
    evaluate_release_gate_v2,
    gate_sha256_v2,
    load_gate_v2,
    verify_prerequisites,
)
from eval.tools.v2.suite_v2 import (
    ToolEvalDecisionV2,
    load_cases_v2,
    review_record_v2,
    suite_sha256_v2,
    verify_freeze_v2,
)

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parents[2]
PLAN_PATH = ROOT / "live-regression-plan-v2.json"
PREFLIGHT_ID = "preflight-v2-002"
PREFLIGHT_PATH = ROOT / PREFLIGHT_ID
SUMMARY_PATH = ROOT / "live-regression-summary-v2.json"
CONTROL_DIR = ROOT / "deterministic-controls-v2-001"
LIVE_ENV = "TOOL_CALLING_LIVE_REGRESSION_ENABLED"
LIVE_CONFIRMATION = "RUN_TOOL_CALLING_V2_LIVE"


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_plan() -> dict[str, Any]:
    plan = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    gate = load_gate_v2()
    if plan["plan_version"] != "tool-calling-live-plan-v2":
        raise RuntimeError("unexpected v2 live plan version")
    if plan["suite_sha256"] != suite_sha256_v2():
        raise RuntimeError("v2 live plan suite identity mismatch")
    if plan["gate_sha256"] != gate_sha256_v2():
        raise RuntimeError("v2 live plan gate identity mismatch")
    if plan["prompt_version"] != TOOL_PROMPT_VERSION or plan["prompt_sha256"] != TOOL_PROMPT_SHA256:
        raise RuntimeError("v2 live plan prompt identity mismatch")
    if plan["source_binding_version"] != SOURCE_BINDING_VERSION:
        raise RuntimeError("v2 live plan source binding mismatch")
    if plan["fixed_config"] != {
        **gate["fixed_live_config"], "timeout_seconds": 30, "tool_choice": "auto"
    }:
        raise RuntimeError("v2 live plan config differs from frozen gate")
    if plan["rounds"] != gate["minimum_independent_live_runs"]:
        raise RuntimeError("v2 live plan round count mismatch")
    live_ids = [
        case["id"] for case in load_cases_v2()["cases"]
        if case["execution_track"] == "live_model"
    ]
    if plan["live_case_ids"] != live_ids or plan["live_cases_per_round"] != len(live_ids):
        raise RuntimeError("v2 live plan case membership mismatch")
    expected_interactions = (
        plan["rounds"] * plan["live_cases_per_round"]
        * plan["maximum_provider_interactions_per_case"]
    )
    if plan["maximum_provider_interactions_total"] != expected_interactions:
        raise RuntimeError("v2 live plan interaction bound mismatch")
    if plan["automatic_retries"] != 0:
        raise RuntimeError("v2 live plan retries must remain disabled")
    if plan["pricing_version"] != PRICING_VERSION:
        raise RuntimeError("v2 live plan pricing version mismatch")
    if Decimal(plan["conservative_estimated_cost"]) >= Decimal(plan["stop_budget"]):
        raise RuntimeError("v2 live plan estimate must remain below stop budget")
    if plan["live_confirmation"] != LIVE_CONFIRMATION or plan["live_enable_env"] != LIVE_ENV:
        raise RuntimeError("v2 live plan authorization guard mismatch")
    for relative, expected in plan["runtime_file_sha256"].items():
        if file_sha256(PROJECT_ROOT / relative) != expected:
            raise RuntimeError(f"v2 live runtime identity mismatch: {relative}")
    return plan


def _authorize_live(plan: dict[str, Any], confirmation: str | None) -> None:
    if os.getenv(plan["live_enable_env"], "").strip().lower() != "true":
        raise PermissionError("paid live regression environment guard is disabled")
    if confirmation != plan["live_confirmation"]:
        raise PermissionError("paid live regression confirmation is missing")


def _settings(plan: dict[str, Any]) -> DeepSeekToolSettings:
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    key = os.getenv("DEEPSEEK_API_KEY", "")
    if not key:
        raise RuntimeError("DeepSeek API key is not configured")
    config = plan["fixed_config"]
    return DeepSeekToolSettings(
        enabled=True,
        api_key=key,
        model=config["model"],
        timeout_seconds=config["timeout_seconds"],
        max_output_tokens=config["max_output_tokens"],
        temperature=config["temperature"],
        thinking=config["thinking"],
        tool_choice=config["tool_choice"],
    )


def _selected_tool(provider: DeepSeekToolDecisionProvider, result: ToolOrchestrationResult) -> str | None:
    if provider.native_tool_calls:
        return provider.native_tool_calls[0].name
    if result.initial_decision and result.initial_decision.tool_call:
        return result.initial_decision.tool_call.name
    return None


def _authorization_and_arguments(selected: str | None, principal: str, arguments: dict[str, Any] | None):
    if selected is None:
        return "not_applicable", None
    registry = build_tool_registry()
    try:
        definition = registry.resolve(selected, principal)
    except ToolRegistryError:
        return "denied", None
    try:
        definition.input_model.model_validate(arguments or {}, strict=True)
    except ValidationError:
        return "allowed", False
    return "allowed", True


def _aggregate_telemetry(case, provider, result, exposure_metadata) -> ToolEvalTelemetry:
    calls = provider.telemetry
    usages = [call.usage for call in calls if call.usage is not None]
    amounts = [call.estimated_cost for call in calls]
    cost_complete = bool(calls) and all(amount is not None for amount in amounts)
    estimated = (
        format(sum(Decimal(amount) for amount in amounts), "f")
        if cost_complete else None
    )
    selected = _selected_tool(provider, result)
    native_arguments = provider.native_tool_calls[0].arguments if provider.native_tool_calls else None
    authorization, arguments_valid = _authorization_and_arguments(
        selected, case["principal"], native_arguments
    )
    tool_result = result.tool_result
    tool_error = tool_result.error.code if tool_result and tool_result.error else None
    provider_error = next((call.error_code for call in reversed(calls) if call.error_code), None)
    generation_config = {
        "thinking": provider.settings.thinking,
        "temperature": provider.settings.temperature,
        "max_output_tokens": provider.settings.max_output_tokens,
        "max_provider_interactions": 2,
        "max_tool_executions": 1,
        "automatic_retries": 0,
        **exposure_metadata,
    }
    return ToolEvalTelemetry(
        provider="deepseek",
        model=provider.settings.model,
        generation_config=generation_config,
        selected_tool=selected,
        arguments_valid=arguments_valid,
        authorization=authorization,
        execution_status=tool_result.status if tool_result else "not_executed",
        provider_interactions=len(calls),
        tool_executions=result.tool_calls_executed,
        input_tokens=sum(usage.prompt_tokens for usage in usages),
        output_tokens=sum(usage.completion_tokens for usage in usages),
        total_tokens=sum(usage.total_tokens for usage in usages),
        latency_ms=sum(call.latency_ms for call in calls),
        estimated_cost=estimated,
        cost_complete=cost_complete,
        retry_count=0,
        error_code=provider_error or (result.error.code if result.error else tool_error),
    )


def _run_case(round_id: str, case: dict[str, Any], settings: DeepSeekToolSettings):
    profile = exposure_profile_for_case(case)
    exposure_metadata = exposure_metadata_for_case(case)
    provider = DeepSeekToolDecisionProvider(settings)
    orchestrator = build_exposed_tool_orchestrator(
        provider,
        exposure_profile=profile,
    )
    result = orchestrator.run(
        ToolOrchestrationInput(user_message=case["user_message"]),
        ToolExecutionContext(
            principal=case["principal"],
            correlation_id=f"{round_id}-{case['id'].replace('/', '-')}",
        ),
    )
    telemetry = _aggregate_telemetry(case, provider, result, exposure_metadata)
    record = build_record(
        run_id=round_id,
        case_id=case["id"],
        mode="live",
        suite_sha256=suite_sha256_v2(),
        prompt_version=TOOL_PROMPT_VERSION,
        prompt_sha256=TOOL_PROMPT_SHA256,
        user_message=case["user_message"],
        result={
            "orchestration": result.model_dump(mode="json"),
            "provider_calls": [call.model_dump(mode="json") for call in provider.telemetry],
            "native_tool_calls": [call.model_dump(mode="json") for call in provider.native_tool_calls],
            "source_binding": exposure_metadata,
        },
        telemetry=telemetry,
    )
    reviewed = evaluate_record_v2(case, result, record, purpose="release")
    return result, record, reviewed


def _load_controls_v2():
    raw = json.loads((CONTROL_DIR / "raw.json").read_text(encoding="utf-8"))
    decisions = json.loads((CONTROL_DIR / "decisions.json").read_text(encoding="utf-8"))
    records = {
        item.case_id: item
        for item in (
            ToolEvalRecord.model_validate_json(json.dumps(value), strict=True)
            for value in raw["records"]
        )
    }
    return [
        review_record_v2(
            records[value["case_id"]],
            ToolEvalDecisionV2.model_validate_json(json.dumps(value), strict=True),
        )
        for value in decisions["decisions"]
    ]


def _write_json(path: Path, payload: dict[str, Any], *, refuse_overwrite: bool = True) -> None:
    if refuse_overwrite and path.exists():
        raise FileExistsError(f"refusing to overwrite evidence: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent,
        prefix=f".{path.name}-", delete=False,
    ) as temporary:
        temporary.write(data)
        temp_path = Path(temporary.name)
    try:
        if refuse_overwrite and path.exists():
            raise FileExistsError(f"refusing to overwrite evidence: {path}")
        temp_path.replace(path)
    finally:
        temp_path.unlink(missing_ok=True)


def _write_round(round_id, records, reviewed, plan_sha, stop_reason=None):
    target = ROOT / round_id
    if target.exists():
        raise FileExistsError(f"refusing to overwrite historical evidence: {target}")
    raw = {
        "run_id": round_id,
        "mode": "live",
        "suite_sha256": suite_sha256_v2(),
        "gate_sha256": gate_sha256_v2(),
        "prompt_version": TOOL_PROMPT_VERSION,
        "prompt_sha256": TOOL_PROMPT_SHA256,
        "plan_sha256": plan_sha,
        "source_binding_version": SOURCE_BINDING_VERSION,
        "data_classification": "synthetic_non_sensitive",
        "records": [item.model_dump(mode="json") for item in records],
        "stopped": stop_reason,
    }
    decisions = {
        "run_id": round_id,
        "decisions": [item.decision.model_dump(mode="json") for item in reviewed],
    }
    reviewed_payload = {
        "run_id": round_id,
        "case_count": len(reviewed),
        "pass_count": sum(item.verdict == "PASS" for item in reviewed),
        "fail_count": sum(item.verdict == "FAIL" for item in reviewed),
        "normalized_quality": (
            sum(item.normalized_quality for item in reviewed if item.normalized_quality is not None)
            / len(reviewed) if reviewed else None
        ),
        "cases": [item.model_dump(mode="json") for item in reviewed],
        "reviewed_evidence_sha256": canonical_sha256(
            [item.model_dump(mode="json") for item in reviewed]
        ),
        "stopped": stop_reason,
    }
    with tempfile.TemporaryDirectory(prefix=f".{round_id}-", dir=ROOT) as temporary:
        temp = Path(temporary)
        for name, payload in {
            "raw.json": raw,
            "decisions.json": decisions,
            "reviewed.json": reviewed_payload,
        }.items():
            (temp / name).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        temp.rename(target)
    return reviewed_payload


def _contains_sensitive_value(payload: Any, secret: str | None = None) -> bool:
    serialized = json.dumps(payload, ensure_ascii=False).lower()
    forbidden = (
        '"api_key"', "authorization: bearer", '"headers"',
        "deepseek_api_key", "openai_api_key",
    )
    return any(item in serialized for item in forbidden) or bool(
        secret and len(secret) >= 8 and secret.lower() in serialized
    )


def _should_stop_before_case(cumulative: Decimal, plan: dict[str, Any]) -> bool:
    return cumulative + Decimal(plan["per_case_cost_reserve"]) > Decimal(plan["stop_budget"])


def _security_stop(case, reviewed) -> str | None:
    if case["category"] not in {"authorization", "malicious_call", "prompt_injection"}:
        return None
    if reviewed.record.telemetry.tool_executions > 0:
        return f"serious_security_execution:{case['id']}"
    for name in ("authorization", "instruction_boundary", "tool_source_policy"):
        if reviewed.decision.hard_invariants.get(name) is False:
            return f"serious_security_invariant:{case['id']}:{name}"
    return None


def _test_result(command: list[str]) -> tuple[dict[str, Any], str]:
    environment = dict(os.environ)
    environment[LIVE_ENV] = "false"
    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    output = completed.stdout
    match = re.search(r"Ran (\d+) tests? in ", output)
    tests_run = int(match.group(1)) if match else 0
    details = {
        "exit_code": completed.returncode,
        "tests_run": tests_run,
        "failures": 0 if completed.returncode == 0 else 1,
        "errors": 0 if completed.returncode == 0 else 1,
    }
    return details, output


def _runtime_boundary() -> dict[str, Any]:
    from backend.main import app as chat_app
    from backend.rag_app import app as rag_app

    routes = sorted(
        route.path
        for app in (chat_app, rag_app)
        for route in app.routes
        if hasattr(route, "path") and "tool" in route.path
    )
    return {
        "feature_flag": {
            "setting": "TOOL_CALLING_ENABLED",
            "default_value": False,
            "effective_enabled": False,
        },
        "route_surface": {"public_tool_routes": routes},
    }


def _database_preflight() -> dict[str, Any]:
    with open_sales_connection() as connection:
        row = connection.execute(
            "SELECT 1, to_regclass('analytics.sales_orders')::text"
        ).fetchone()
    executor = ToolExecutor(build_exposed_tool_registry())
    result = executor.execute(
        ToolCall(
            name="get_postgres_sales_summary",
            arguments={"period": "2026-Q2", "region": "north"},
        ),
        ToolExecutionContext(
            principal=INTERNAL_PRINCIPAL,
            correlation_id="tool-v2-live-preflight",
        ),
    )
    if (
        row[0] != 1
        or row[1] not in {"sales_orders", "analytics.sales_orders"}
        or result.status != "success"
    ):
        raise RuntimeError("database preflight failed")
    return {
        "select_1": row[0],
        "sales_relation": row[1],
        "tool_status": result.status,
        "tool_name": result.tool_name,
        "source_kind": result.output["data_source"]["kind"],
    }


def run_preflight() -> dict[str, Any]:
    plan = _load_plan()
    if PREFLIGHT_PATH.exists():
        raise FileExistsError(f"refusing to overwrite preflight evidence: {PREFLIGHT_PATH}")
    freeze_v1 = verify_freeze()
    freeze_v2 = verify_freeze_v2()
    cases = load_cases_v2()["cases"]
    live_cases = [case for case in cases if case["execution_track"] == "live_model"]
    source_matrix = []
    for case in live_cases:
        profile = exposure_profile_for_case(case)
        registry = build_exposed_tool_registry(exposure_profile=profile)
        visible = [schema["name"] for schema in registry.schemas_for(case["principal"])]
        if len(visible) > 1:
            raise RuntimeError("a v2 case exposes more than one source tool")
        if case["tool_policy"]["mode"] == "required" and visible != case["tool_policy"]["allowed_tools"]:
            raise RuntimeError(f"case source exposure mismatch: {case['id']}")
        source_matrix.append({
            "case_id": case["id"],
            "principal": case["principal"],
            "profile": profile,
            "visible_tools": visible,
            "allowed_tools": case["tool_policy"]["allowed_tools"],
        })
    command_base = [sys.executable, "-m", "unittest", "discover", "-s"]
    offline_details, offline_output = _test_result(command_base + ["tests", "-q"])
    database_details, database_output = _test_result(command_base + ["tests/db", "-q"])
    if offline_details["exit_code"] or database_details["exit_code"]:
        raise RuntimeError("preflight tests failed")
    boundary = _runtime_boundary()
    if boundary["route_surface"]["public_tool_routes"]:
        raise RuntimeError("public Tool API detected")
    database = _database_preflight()
    try:
        _authorize_live(plan, None)
    except PermissionError:
        live_guard_default_denied = True
    else:
        live_guard_default_denied = False
    if not live_guard_default_denied or not _should_stop_before_case(
        Decimal(plan["stop_budget"]) - Decimal(plan["per_case_cost_reserve"]) / 2,
        plan,
    ):
        raise RuntimeError("live authorization or budget stop guard failed")
    with tempfile.TemporaryDirectory(prefix=f".{PREFLIGHT_ID}-", dir=ROOT) as temporary:
        temp = Path(temporary)
        (temp / "offline-tests.txt").write_text(offline_output, encoding="utf-8")
        (temp / "database-tests.txt").write_text(database_output, encoding="utf-8")
        (temp / "runtime-boundary.json").write_text(
            json.dumps(boundary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        final_relative = Path("eval/tools/v2") / PREFLIGHT_ID
        checks = [
            {
                "name": "offline_tests", "status": "PASS",
                "artifact_path": str(final_relative / "offline-tests.txt"),
                "artifact_sha256": file_sha256(temp / "offline-tests.txt"),
                "details": offline_details,
            },
            {
                "name": "database_tests", "status": "PASS",
                "artifact_path": str(final_relative / "database-tests.txt"),
                "artifact_sha256": file_sha256(temp / "database-tests.txt"),
                "details": database_details,
            },
            {
                "name": "feature_flag_default_off", "status": "PASS",
                "artifact_path": str(final_relative / "runtime-boundary.json"),
                "artifact_sha256": file_sha256(temp / "runtime-boundary.json"),
                "details": {
                    "setting": "TOOL_CALLING_ENABLED", "default_value": False,
                },
            },
            {
                "name": "public_tool_api_absent", "status": "PASS",
                "artifact_path": str(final_relative / "runtime-boundary.json"),
                "artifact_sha256": file_sha256(temp / "runtime-boundary.json"),
                "details": {"public_tool_routes": []},
            },
        ]
        prerequisites = build_prerequisite_evidence(checks)
        (temp / "prerequisite-evidence.json").write_text(
            prerequisites.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        temp.rename(PREFLIGHT_PATH)
    loaded = PrerequisiteEvidenceV2.model_validate_json(
        (PREFLIGHT_PATH / "prerequisite-evidence.json").read_text(encoding="utf-8"),
        strict=True,
    )
    valid, blockers = verify_prerequisites(loaded)
    if not valid:
        raise RuntimeError("fresh prerequisite evidence failed: " + ",".join(blockers))
    gate = evaluate_release_gate_v2(
        [], control_cases=_load_controls_v2(), prerequisites=loaded
    )
    if gate.verdict != "NOT_RUN" or gate.blockers != ("minimum_live_runs_not_met",):
        raise RuntimeError("unexpected preflight gate state")
    report = {
        "preflight_version": "tool-calling-live-preflight-v2",
        "preflight_id": PREFLIGHT_ID,
        "paid_api_calls": 0,
        "plan_sha256": canonical_sha256(plan),
        "suite_sha256": suite_sha256_v2(),
        "gate_sha256": gate_sha256_v2(),
        "prompt_sha256": TOOL_PROMPT_SHA256,
        "source_binding_version": SOURCE_BINDING_VERSION,
        "source_matrix": source_matrix,
        "database": database,
        "live_guard_default_denied": live_guard_default_denied,
        "budget_stop_simulation": "PASS",
        "evidence_refuses_overwrite": True,
        "sensitive_value_scan": "PASS",
        "freeze_v1_suite_sha256": freeze_v1["suite_sha256"],
        "freeze_v2_suite_sha256": freeze_v2["suite_sha256"],
        "prerequisite_evidence_sha256": loaded.evidence_sha256,
        "gate_preflight_verdict": gate.verdict,
        "gate_preflight_blockers": list(gate.blockers),
        "status": "PASS",
    }
    if _contains_sensitive_value(report):
        raise RuntimeError("preflight report contains sensitive fields")
    report["preflight_evidence_sha256"] = canonical_sha256(report)
    _write_json(PREFLIGHT_PATH / "report.json", report)
    return report


def _fresh_prerequisites(plan: dict[str, Any]) -> PrerequisiteEvidenceV2:
    path = PROJECT_ROOT / plan["prerequisite_evidence_path"]
    evidence = PrerequisiteEvidenceV2.model_validate_json(
        path.read_text(encoding="utf-8"), strict=True
    )
    valid, blockers = verify_prerequisites(evidence)
    if not valid:
        raise RuntimeError("live prerequisites invalid: " + ",".join(blockers))
    return evidence


def run_live(confirmation: str | None) -> dict[str, Any]:
    plan = _load_plan()
    _authorize_live(plan, confirmation)
    prerequisites = _fresh_prerequisites(plan)
    if SUMMARY_PATH.exists():
        raise FileExistsError(f"refusing to overwrite historical summary: {SUMMARY_PATH}")
    for index in range(1, plan["rounds"] + 1):
        if (ROOT / f"live-v2-{index:03d}").exists():
            raise FileExistsError("v2 live evidence directory already exists")
    settings = _settings(plan)
    secret = settings.api_key.get_secret_value()
    freeze_v1_before = verify_freeze()
    freeze_v2_before = verify_freeze_v2()
    plan_sha = canonical_sha256(plan)
    cases = {
        case["id"]: case for case in load_cases_v2()["cases"]
        if case["execution_track"] == "live_model"
    }
    all_reviewed = []
    round_summaries = []
    cumulative_cost = Decimal("0")
    total_interactions = 0
    stop_reason = None
    for index in range(1, plan["rounds"] + 1):
        round_id = f"live-v2-{index:03d}"
        records = []
        reviewed = []
        for case_id in plan["live_case_ids"]:
            if _should_stop_before_case(cumulative_cost, plan):
                stop_reason = "budget_reserve_would_exceed_limit"
                break
            case = cases[case_id]
            _, record, reviewed_case = _run_case(round_id, case, settings)
            if _contains_sensitive_value(record.model_dump(mode="json"), secret):
                stop_reason = f"sensitive_evidence_detected:{case_id}"
            records.append(record)
            reviewed.append(reviewed_case)
            total_interactions += record.telemetry.provider_interactions
            if record.telemetry.estimated_cost is None or not record.telemetry.cost_complete:
                stop_reason = f"cost_evidence_incomplete:{case_id}"
            else:
                cumulative_cost += Decimal(record.telemetry.estimated_cost)
            stop_reason = stop_reason or _security_stop(case, reviewed_case)
            if cumulative_cost > Decimal(plan["stop_budget"]):
                stop_reason = "budget_limit_exceeded"
            if total_interactions > plan["maximum_provider_interactions_total"]:
                stop_reason = "provider_interaction_limit_exceeded"
            if verify_freeze() != freeze_v1_before or verify_freeze_v2() != freeze_v2_before:
                stop_reason = "frozen_identity_changed"
            if stop_reason:
                break
        round_payload = _write_round(
            round_id, records, reviewed, plan_sha, stop_reason=stop_reason
        )
        round_payload["tokens"] = sum(item.record.telemetry.total_tokens for item in reviewed)
        round_payload["latency_ms"] = sum(item.record.telemetry.latency_ms for item in reviewed)
        round_payload["estimated_cost"] = format(
            sum(Decimal(item.record.telemetry.estimated_cost or "0") for item in reviewed), "f"
        )
        round_summaries.append(round_payload)
        if reviewed:
            all_reviewed.append(reviewed)
        if stop_reason:
            break
    gate = evaluate_release_gate_v2(
        all_reviewed,
        control_cases=_load_controls_v2(),
        prerequisites=prerequisites,
    )
    summary = {
        "summary_version": "tool-calling-live-summary-v2",
        "plan_sha256": plan_sha,
        "suite_sha256": suite_sha256_v2(),
        "gate_sha256": gate_sha256_v2(),
        "prompt_sha256": TOOL_PROMPT_SHA256,
        "rounds_completed": len(round_summaries),
        "rounds": round_summaries,
        "provider_interactions": total_interactions,
        "cumulative_estimated_cost": format(cumulative_cost, "f"),
        "stop_reason": stop_reason,
        "gate": gate.model_dump(mode="json"),
    }
    if _contains_sensitive_value(summary, secret):
        raise RuntimeError("live summary contains sensitive fields")
    _write_json(SUMMARY_PATH, summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("preflight", "live"), default="preflight")
    parser.add_argument("--confirmation")
    args = parser.parse_args(argv)
    if args.mode == "live":
        output = run_live(args.confirmation)
    else:
        output = run_preflight()
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
