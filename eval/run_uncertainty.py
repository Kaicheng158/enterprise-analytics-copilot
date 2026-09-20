"""Small Phase 2.6 collection script; semantic grading requires recorded review."""
import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from backend.llm import LLMProvider, ProviderError, get_provider
from backend.prompts import build_messages
from backend.prompt_registry import prompt_metadata

CASES = Path(__file__).with_name("uncertainty_cases.json")


def collect(provider: LLMProvider, cases: list[dict]) -> dict:
    metadata = prompt_metadata(build_messages(""))
    records = []
    for case in cases:
        start = time.monotonic()
        record = {**metadata, "case_id": case["id"], "verdict": "pending_review"}
        try:
            result = provider.chat(case["input"])
            record.update(result.model_dump())
            record["execution_status"] = "success"
        except ProviderError as error:
            # Never serialize arbitrary exception text or raw provider responses.
            record.update(execution_status="error", error_code=error.code,
                          verdict="execution_error", usage=None, estimated_cost=None,
                          cost_complete=False, latency_ms=round((time.monotonic()-start)*1000))
        records.append(record)
    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        **metadata,
        "cases_sha256": hashlib.sha256(json.dumps(cases, sort_keys=True).encode()).hexdigest(),
        "review_method": "Manual semantic review against each expected_behavior and failure_condition; no keyword or numeric-confidence grading.",
        "records": records,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run six synthetic cases using the configured real provider (incurs usage).")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists; preserve previous evidence and choose a new path")
    cases = json.loads(CASES.read_text())
    result = collect(get_provider(), cases)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(f"Collected {len(result['records'])} cases; semantic review remains pending.")
