"""Run the frozen PV2-02c matrix and emit content-free JSON evidence."""
import io
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from eval.datasets.pv2_02c_suite import (
    CASES,
    FROZEN_SUITE_SHA256,
    SUITE_VERSION,
    assert_frozen,
)


class EvidenceResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.passed_ids: set[str] = set()

    def addSuccess(self, test):
        super().addSuccess(test)
        self.passed_ids.add(test.id())


def main() -> int:
    assert_frozen()
    targets = [case["test"] for case in CASES]
    suite = unittest.defaultTestLoader.loadTestsFromNames(targets)
    hidden_output = io.StringIO()
    result = unittest.TextTestRunner(
        stream=hidden_output,
        verbosity=0,
        resultclass=EvidenceResult,
    ).run(suite)
    case_results = [
        {
            "case_id": case["case_id"],
            "hard_invariants": case["hard_invariants"],
            "verdict": "PASS" if case["test"] in result.passed_ids else "FAIL",
        }
        for case in CASES
    ]
    evidence = {
        "evidence_version": "pv2-02c-local-reliability-evidence-v1",
        "suite_version": SUITE_VERSION,
        "suite_sha256": FROZEN_SUITE_SHA256,
        "scope": "synthetic data; disposable PostgreSQL; local private storage",
        "production_release_claim": False,
        "paid_calls": 0,
        "case_count": len(CASES),
        "passed": len(result.passed_ids),
        "failed": len(CASES) - len(result.passed_ids),
        "verdict": "PASS" if result.wasSuccessful() and len(result.passed_ids) == len(CASES) else "FAIL",
        "cases": case_results,
    }
    print(json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if evidence["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
