from __future__ import annotations

from .suite_v1 import gate_sha256, load_gate, suite_sha256


def evaluate_gate(preflight: dict, live_rounds: list[dict]) -> dict:
    gate = load_gate()
    blockers = []
    if preflight.get("status") != "READY":
        blockers.append("preflight_not_ready")
    if preflight.get("suite_sha256") != suite_sha256():
        blockers.append("suite_identity_mismatch")
    if len(live_rounds) < gate["minimum_independent_live_rounds"]:
        blockers.append("minimum_live_rounds_not_met")
    if live_rounds and any(item.get("verdict") != "PASS" for item in live_rounds):
        blockers.append("live_round_failed")
    if live_rounds and any(not item.get("hard_invariants_100_percent") for item in live_rounds):
        blockers.append("hard_invariant_failure")
    if sum(item.get("core_blockers", 0) for item in live_rounds):
        blockers.append("core_blocker_present")
    if live_rounds and any(
        item.get("normalized_quality", 0) < gate["minimum_round_normalized_quality"]
        for item in live_rounds
    ):
        blockers.append("quality_threshold_not_met")
    if live_rounds and any(
        not item.get("all_cases_meet_quality_threshold", False)
        for item in live_rounds
    ):
        blockers.append("case_quality_threshold_not_met")
    if any(item.get("repeated_severe_quality_issue") for item in live_rounds):
        blockers.append("repeated_severe_quality_issue")
    verdict = "PASS" if not blockers else (
        "BLOCKED" if blockers == ["minimum_live_rounds_not_met"] else "FAIL"
    )
    return {
        "gate_version": gate["gate_version"],
        "suite_sha256": suite_sha256(),
        "gate_sha256": gate_sha256(),
        "live_rounds_received": len(live_rounds),
        "verdict": verdict,
        "blockers": blockers,
    }
