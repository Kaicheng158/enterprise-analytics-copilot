from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from backend.planning.structured_provider import (
    PLAN_RESPONSE_SCHEMA_SHA256,
    PLANNER_PROMPT_SHA256,
    PLANNER_PROMPT_VERSION,
)


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parents[2]
POLICY_PATH = ROOT / "policy-v1.json"
CASES_PATH = ROOT / "cases-v1.json"
GATE_PATH = ROOT / "release-gate-v1.json"
LIVE_PLAN_PATH = ROOT / "live-plan-v1.json"
FREEZE_PATH = ROOT / "freeze-v1.json"


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return sha256(payload).hexdigest()


def file_sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def load_policy() -> dict:
    return json.loads(POLICY_PATH.read_text())


def load_suite() -> dict:
    return json.loads(CASES_PATH.read_text())


def load_gate() -> dict:
    return json.loads(GATE_PATH.read_text())


def load_live_plan() -> dict:
    return json.loads(LIVE_PLAN_PATH.read_text())


def suite_sha256() -> str:
    return canonical_sha256({"policy": load_policy(), "suite": load_suite()})


def gate_sha256() -> str:
    return canonical_sha256(load_gate())


def live_plan_sha256() -> str:
    return canonical_sha256(load_live_plan())


def validate_suite() -> tuple[dict, dict, dict, dict]:
    policy, suite, gate, live_plan = (
        load_policy(),
        load_suite(),
        load_gate(),
        load_live_plan(),
    )
    if policy["policy_version"] != "durable-planner-evaluation-policy-v1":
        raise ValueError("durable planner policy version mismatch")
    if suite["suite_version"] != "durable-planner-suite-v1":
        raise ValueError("durable planner suite version mismatch")
    if gate["gate_version"] != "durable-planner-release-gate-v1":
        raise ValueError("durable planner gate version mismatch")
    if live_plan["plan_version"] != "durable-planner-live-plan-v1":
        raise ValueError("durable planner live plan version mismatch")
    cases = suite["cases"]
    ids = [case["id"] for case in cases]
    if len(ids) != 3 or len(set(ids)) != 3 or gate["live_case_ids"] != ids:
        raise ValueError("durable planner case set mismatch")
    for case in cases:
        if not set(case["hard_invariants"]) <= set(policy["hard_invariants"]):
            raise ValueError("unknown durable planner hard invariant")
        if not set(case["quality_dimensions"]) <= set(policy["quality_dimensions"]):
            raise ValueError("unknown durable planner quality dimension")
    if (
        live_plan["rounds"] != 2
        or gate["minimum_independent_live_rounds"] != 2
        or live_plan["rounds"] != gate["minimum_independent_live_rounds"]
    ):
        raise ValueError("durable planner round count mismatch")
    if live_plan["live_cases_per_round"] != len(cases):
        raise ValueError("durable planner case count mismatch")
    if live_plan["maximum_provider_calls_total"] != 6:
        raise ValueError("durable planner provider-call bound mismatch")
    if live_plan["fixed_config"] != gate["fixed_live_config"]:
        raise ValueError("durable planner fixed config mismatch")
    if live_plan["automatic_retries"] != 0:
        raise ValueError("durable planner retry policy mismatch")
    return policy, suite, gate, live_plan


def verify_freeze() -> dict:
    freeze = json.loads(FREEZE_PATH.read_text())
    if (
        freeze["suite_sha256"] != suite_sha256()
        or freeze["gate_sha256"] != gate_sha256()
        or freeze["live_plan_sha256"] != live_plan_sha256()
        or freeze["prompt_version"] != PLANNER_PROMPT_VERSION
        or freeze["prompt_sha256"] != PLANNER_PROMPT_SHA256
        or freeze["schema_sha256"] != PLAN_RESPONSE_SCHEMA_SHA256
    ):
        raise ValueError("durable planner freeze identity mismatch")
    for relative, expected in freeze["file_sha256"].items():
        if file_sha256(PROJECT_ROOT / relative) != expected:
            raise ValueError("frozen durable planner file changed: " + relative)
    return freeze
