import unittest

from eval.datasets.pv2_02c_suite import (
    CASES,
    FROZEN_SUITE_SHA256,
    assert_frozen,
    suite_sha256,
)


class DatasetReliabilityAcceptanceTests(unittest.TestCase):
    def test_suite_is_frozen_unique_and_covers_required_faults(self):
        assert_frozen()
        self.assertEqual(suite_sha256(), FROZEN_SUITE_SHA256)
        self.assertEqual(len({case["case_id"] for case in CASES}), len(CASES))
        self.assertEqual(len({case["test"] for case in CASES}), len(CASES))
        faults = {case["fault"] for case in CASES}
        self.assertTrue(
            {
                "database_connection_failure",
                "database_boundary_failure_after_blob_write",
                "abrupt_process_exit",
                "disk_write_failure",
                "crash_after_first_blob_delete",
                "concurrent_project_write_and_delete",
                "gc_during_active_write",
                "expired_lease_and_worker_takeover",
                "duplicate_reconcile_request",
            }.issubset(faults)
        )
        self.assertTrue(all(case["hard_invariants"] for case in CASES))
        self.assertTrue(all(case["failure_condition"] for case in CASES))


if __name__ == "__main__":
    unittest.main()
