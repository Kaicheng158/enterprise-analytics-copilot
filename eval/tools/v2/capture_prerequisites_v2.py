"""Capture hash-bound, secret-free local prerequisite evidence for gate v2."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import tempfile
from pathlib import Path

from backend.main import app as main_app
from backend.rag_app import app as rag_app
from backend.tools.deepseek_adapter import load_deepseek_tool_settings
from eval.tools.v2.release_gate_v2 import build_prerequisite_evidence

ROOT = Path(__file__).resolve().parents[3]
V2 = Path(__file__).resolve().parent
DEFAULT_OUTPUT = V2 / "verification-v2-002"
DEFAULT_EVIDENCE = V2 / "prerequisite-evidence-v2.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_test_log(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    match = re.search(r"Ran (\d+) tests? in ", text)
    if not match or not re.search(r"^OK$", text, re.M):
        raise ValueError(f"test log is not a successful unittest run: {path}")
    return {"exit_code": 0, "tests_run": int(match.group(1)), "failures": 0, "errors": 0}


def routes(app) -> list[str]:
    return sorted({getattr(route, "path", "") for route in app.routes if getattr(route, "path", "")})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline-log", type=Path, required=True)
    parser.add_argument("--database-log", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE)
    args = parser.parse_args()
    if args.output_dir.exists() or args.evidence.exists():
        raise FileExistsError("refusing to overwrite v2 prerequisite evidence")
    offline = parse_test_log(args.offline_log)
    database = parse_test_log(args.database_log)
    settings = load_deepseek_tool_settings()
    all_routes = sorted(set(routes(main_app) + routes(rag_app)))
    public_tool_routes = [route for route in all_routes if "tool" in route.lower() and route != "/openapi.json"]
    runtime = {
        "feature_flag": {
            "setting": "TOOL_CALLING_ENABLED",
            "default_value": False,
            "effective_enabled": settings.enabled,
            "source": "backend/tools/deepseek_adapter.py",
            "source_sha256": sha(ROOT / "backend/tools/deepseek_adapter.py"),
        },
        "route_surface": {
            "routes": all_routes,
            "public_tool_routes": public_tool_routes,
            "main_source_sha256": sha(ROOT / "backend/main.py"),
            "rag_source_sha256": sha(ROOT / "backend/rag/api.py"),
        },
    }
    if settings.enabled or public_tool_routes:
        raise RuntimeError("runtime boundary prerequisite is not satisfied")
    with tempfile.TemporaryDirectory(prefix=".verification-v2-", dir=V2) as temp_name:
        temp = Path(temp_name)
        shutil.copyfile(args.offline_log, temp / "offline-tests.txt")
        shutil.copyfile(args.database_log, temp / "database-tests.txt")
        (temp / "runtime-boundary.json").write_text(json.dumps(runtime, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temp.rename(args.output_dir)
    checks = [
        {
            "name": "offline_tests", "status": "PASS",
            "artifact_path": str((args.output_dir / "offline-tests.txt").relative_to(ROOT)),
            "artifact_sha256": sha(args.output_dir / "offline-tests.txt"), "details": offline,
        },
        {
            "name": "database_tests", "status": "PASS",
            "artifact_path": str((args.output_dir / "database-tests.txt").relative_to(ROOT)),
            "artifact_sha256": sha(args.output_dir / "database-tests.txt"), "details": database,
        },
        {
            "name": "feature_flag_default_off", "status": "PASS",
            "artifact_path": str((args.output_dir / "runtime-boundary.json").relative_to(ROOT)),
            "artifact_sha256": sha(args.output_dir / "runtime-boundary.json"),
            "details": {"setting": "TOOL_CALLING_ENABLED", "default_value": False, "effective_enabled": False},
        },
        {
            "name": "public_tool_api_absent", "status": "PASS",
            "artifact_path": str((args.output_dir / "runtime-boundary.json").relative_to(ROOT)),
            "artifact_sha256": sha(args.output_dir / "runtime-boundary.json"),
            "details": {"public_tool_routes": []},
        },
    ]
    evidence = build_prerequisite_evidence(checks)
    args.evidence.write_text(evidence.model_dump_json(indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"offline_tests": offline["tests_run"], "database_tests": database["tests_run"], "evidence_sha256": evidence.evidence_sha256}, indent=2))


if __name__ == "__main__":
    main()
