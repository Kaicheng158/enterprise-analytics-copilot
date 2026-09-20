"""Six synthetic prompt-boundary checks; no tools or automated semantic judge."""
import argparse
import json
from pathlib import Path
from backend.llm import get_provider
from eval.run_uncertainty import collect

CASES = Path(__file__).with_name("injection_cases.json")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run synthetic injection cases with the configured real provider; incurs usage.")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Preserve existing evidence; choose a new output path")
    result = collect(get_provider(), json.loads(CASES.read_text()))
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print("Six cases collected; semantic review pending.")
