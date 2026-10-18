"""Free infrastructure preflight after the preserved v3 live failure.

No model provider is constructed or called. The report refuses overwrite and
contains only safe operational metadata.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from eval.tools.suite import canonical_sha256
from eval.tools.v3.infrastructure_guard_v1 import (
    GUARD_VERSION,
    check_database_runtime,
)
from eval.tools.v3.release_gate_v3 import gate_sha256_v3
from eval.tools.v3.suite_v3 import suite_sha256_v3, verify_freeze_v3


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parents[2]
REPORT_ID = "infrastructure-diagnostics-v3-001"
REPORT_DIR = ROOT / REPORT_ID
PROTECTION_PATH = ROOT / "historical-live-v3-protection.json"


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _command(command: list[str]) -> tuple[int, str]:
    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    return completed.returncode, completed.stdout


def _test(command: list[str]) -> tuple[dict[str, Any], str]:
    code, output = _command(command)
    summary = next(
        (line.strip() for line in reversed(output.splitlines()) if line.strip()),
        "no output",
    )
    return {"exit_code": code, "summary": summary}, output


def _verify_historical_live_v3() -> dict[str, Any]:
    manifest = json.loads(PROTECTION_PATH.read_text(encoding="utf-8"))
    changed = []
    for relative, expected in manifest["files"].items():
        path = PROJECT_ROOT / relative
        if not path.is_file() or file_sha256(path) != expected:
            changed.append(relative)
    if changed:
        raise RuntimeError("historical v3 evidence changed: " + ",".join(changed))
    return {
        "manifest_sha256": file_sha256(PROTECTION_PATH),
        "protected_files": len(manifest["files"]),
        "changed_files": changed,
    }


def _docker_status() -> dict[str, Any]:
    daemon_code, server = _command(["docker", "info", "--format", "{{.ServerVersion}}"])
    inspect_code, health = _command(
        [
            "docker",
            "inspect",
            "enterprise-analytics-copilot-postgres-1",
            "--format",
            "{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}",
        ]
    )
    state, separator, health_state = health.strip().partition("|")
    return {
        "daemon_reachable": daemon_code == 0,
        "server_version": server.strip() if daemon_code == 0 else None,
        "postgres_inspect_ok": inspect_code == 0 and bool(separator),
        "postgres_state": state if inspect_code == 0 else "unavailable",
        "postgres_health": health_state if inspect_code == 0 else "unavailable",
    }


def run() -> dict[str, Any]:
    if REPORT_DIR.exists():
        raise FileExistsError(f"refusing to overwrite evidence: {REPORT_DIR}")
    verify_freeze_v3()
    historical = _verify_historical_live_v3()
    docker = _docker_status()
    health = check_database_runtime()
    command = [sys.executable, "-m", "unittest", "discover", "-s"]
    offline, offline_output = _test(command + ["tests", "-q"])
    database, database_output = _test(command + ["tests/db", "-q"])
    status = "PASS" if (
        docker["daemon_reachable"]
        and docker["postgres_state"] == "running"
        and docker["postgres_health"] == "healthy"
        and health.status == "healthy"
        and offline["exit_code"] == 0
        and database["exit_code"] == 0
        and not historical["changed_files"]
    ) else "FAIL"
    report = {
        "report_version": "tool-calling-infrastructure-diagnostics-v3-v1",
        "report_id": REPORT_ID,
        "paid_api_calls": 0,
        "status": status,
        "guard_version": GUARD_VERSION,
        "guard_sha256": file_sha256(ROOT / "infrastructure_guard_v1.py"),
        "suite_sha256": suite_sha256_v3(),
        "gate_sha256": gate_sha256_v3(),
        "frozen_v3": "PASS",
        "historical_live_v3": historical,
        "docker": docker,
        "agent_database_runtime": health.model_dump(mode="json"),
        "offline_tests": offline,
        "database_tests": database,
        "live_guard_default": "disabled",
        "future_paid_execution_rule": (
            "check at run start and immediately before every case whose "
            "server-owned exposure profile is production; stop before provider call"
        ),
    }
    if status != "PASS":
        raise RuntimeError("infrastructure preflight failed")
    report["evidence_sha256"] = canonical_sha256(report)
    with tempfile.TemporaryDirectory(prefix=f".{REPORT_ID}-", dir=ROOT) as temporary:
        target = Path(temporary)
        (target / "offline-tests.txt").write_text(offline_output, encoding="utf-8")
        (target / "database-tests.txt").write_text(database_output, encoding="utf-8")
        (target / "report.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        target.rename(REPORT_DIR)
    return report


if __name__ == "__main__":
    print(json.dumps(run(), indent=2, sort_keys=True))
