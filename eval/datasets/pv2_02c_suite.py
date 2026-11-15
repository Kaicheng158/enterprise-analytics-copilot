"""Frozen PV2-02c local reliability acceptance matrix."""
import hashlib
import json


SUITE_VERSION = "pv2-02c-local-reliability-v1"
FROZEN_SUITE_SHA256 = "ca31f06afdefc620e2919687a4d90564fa940424f41f9b44deb316b7f07c6eb0"


CASES = (
    {
        "case_id": "parse_csv_xlsx_decimal_equivalence",
        "layer": "parsing",
        "fault": "format_variation",
        "test": "tests.test_dataset_parsing.DatasetParsingTests.test_csv_xlsx_equivalence_and_exact_decimal",
        "hard_invariants": ["deterministic_normalization", "decimal_precision"],
        "failure_condition": "Equivalent CSV/XLSX data normalizes differently or loses Decimal precision.",
    },
    {
        "case_id": "parse_formula_cache_untrusted",
        "layer": "parsing",
        "fault": "untrusted_formula_cache",
        "test": "tests.test_dataset_parsing.DatasetParsingTests.test_formula_cache_never_trusted",
        "hard_invariants": ["no_formula_execution", "no_cached_formula_trust"],
        "failure_condition": "A formula or cached formula value becomes trusted Dataset data.",
    },
    {
        "case_id": "immutable_revision_and_duplicate_identity",
        "layer": "persistence",
        "fault": "duplicate_and_changed_source",
        "test": "tests.db.test_private_dataset_persistence.PrivateDatasetPersistenceTests.test_normal_save_duplicate_and_changed_revision",
        "hard_invariants": ["immutable_revision", "project_scoped_duplicate_identity"],
        "failure_condition": "A duplicate creates another revision or changed content mutates the old revision.",
    },
    {
        "case_id": "atomic_disk_write_failure",
        "layer": "storage",
        "fault": "disk_write_failure",
        "test": "tests.db.test_private_dataset_persistence.PrivateDatasetPersistenceTests.test_storage_failure_releases_quota_and_never_publishes",
        "hard_invariants": ["no_false_ready", "reservation_released", "no_partial_blob"],
        "failure_condition": "A failed blob write publishes ready state, leaks reservation, or leaves a visible partial object.",
    },
    {
        "case_id": "database_finalization_uncertainty",
        "layer": "persistence",
        "fault": "database_boundary_failure_after_blob_write",
        "test": "tests.db.test_private_dataset_persistence.PrivateDatasetPersistenceTests.test_uncertain_finalization_requires_verified_reconciliation",
        "hard_invariants": ["no_false_ready", "verified_reconciliation_only"],
        "failure_condition": "Uncertain finalization publishes without verification or loses its recovery record.",
    },
    {
        "case_id": "process_crash_after_reservation",
        "layer": "persistence",
        "fault": "abrupt_process_exit",
        "test": "tests.db.test_private_dataset_persistence.PrivateDatasetPersistenceTests.test_process_crash_after_reservation_never_publishes_and_reconciles",
        "hard_invariants": ["no_false_ready", "bounded_recovery", "quota_consistency"],
        "failure_condition": "A reserved-but-unwritten revision becomes readable or cannot be reconciled safely.",
    },
    {
        "case_id": "concurrent_duplicate_upload",
        "layer": "persistence",
        "fault": "concurrent_identical_write",
        "test": "tests.db.test_private_dataset_persistence.PrivateDatasetPersistenceTests.test_concurrent_duplicate_is_one_revision_and_one_quota_charge",
        "hard_invariants": ["one_revision", "one_quota_charge", "one_blob_pair"],
        "failure_condition": "Concurrent identical submissions duplicate revisions, blobs, or quota.",
    },
    {
        "case_id": "concurrent_quota_competition",
        "layer": "persistence",
        "fault": "concurrent_distinct_writes_over_quota",
        "test": "tests.db.test_private_dataset_persistence.PrivateDatasetPersistenceTests.test_concurrent_quota_reservation_never_overshoots",
        "hard_invariants": ["quota_never_exceeded", "reservation_settled"],
        "failure_condition": "Concurrent reservations exceed quota or leave reserved bytes behind.",
    },
    {
        "case_id": "repeated_reconciliation",
        "layer": "persistence",
        "fault": "duplicate_reconcile_request",
        "test": "tests.db.test_private_dataset_persistence.PrivateDatasetPersistenceTests.test_repeated_verified_reconciliation_is_idempotent",
        "hard_invariants": ["idempotent_reconciliation", "no_duplicate_quota_or_blob"],
        "failure_condition": "Repeating verified reconciliation changes state, quota, revision count, or blob count.",
    },
    {
        "case_id": "expired_revision_read_barrier",
        "layer": "lifecycle",
        "fault": "retention_expiry",
        "test": "tests.db.test_private_dataset_lifecycle.PrivateDatasetLifecycleTests.test_expiration_blocks_reads_without_releasing_quota",
        "hard_invariants": ["zero_expired_reads", "quota_retained_until_delete"],
        "failure_condition": "An expired revision remains readable or prematurely releases quota.",
    },
    {
        "case_id": "delete_interruption_recovery",
        "layer": "lifecycle",
        "fault": "crash_after_first_blob_delete",
        "test": "tests.db.test_private_dataset_lifecycle.PrivateDatasetLifecycleTests.test_crash_after_first_blob_delete_resumes_without_double_release",
        "hard_invariants": ["unreadable_partial_delete", "resumable_delete", "quota_once"],
        "failure_condition": "Interrupted deletion becomes ready, cannot resume, or releases quota more than once.",
    },
    {
        "case_id": "upload_delete_serialization",
        "layer": "lifecycle",
        "fault": "concurrent_project_write_and_delete",
        "test": "tests.db.test_private_dataset_lifecycle.PrivateDatasetLifecycleTests.test_deletion_waits_for_project_write_and_preserves_other_ready_revision",
        "hard_invariants": ["no_write_delete_race", "valid_revision_preserved"],
        "failure_condition": "Deletion races a valid write or damages another ready revision.",
    },
    {
        "case_id": "gc_active_writer_exclusion",
        "layer": "lifecycle",
        "fault": "gc_during_active_write",
        "test": "tests.db.test_private_dataset_lifecycle.PrivateDatasetLifecycleTests.test_stale_storage_operation_is_reconciled_but_active_writer_blocks_maintenance",
        "hard_invariants": ["active_writer_not_collected", "stale_write_reconciled"],
        "failure_condition": "GC takes over an active write or stale state bypasses verified reconciliation.",
    },
    {
        "case_id": "lease_expiry_stale_worker_fencing",
        "layer": "lifecycle",
        "fault": "expired_lease_and_worker_takeover",
        "test": "tests.db.test_private_dataset_lifecycle.PrivateDatasetLifecycleTests.test_expired_lease_takeover_fences_old_worker_finalization",
        "hard_invariants": ["stale_worker_rejected", "new_fence_required", "quota_once"],
        "failure_condition": "An old worker finalizes after takeover or changes quota/state with a stale fence.",
    },
    {
        "case_id": "repeat_delete_quota_once",
        "layer": "lifecycle",
        "fault": "duplicate_delete_request",
        "test": "tests.db.test_private_dataset_lifecycle.PrivateDatasetLifecycleTests.test_delete_is_idempotent_and_releases_quota_once",
        "hard_invariants": ["idempotent_delete", "quota_once", "no_negative_quota"],
        "failure_condition": "Repeated deletion releases quota twice or changes a completed operation.",
    },
    {
        "case_id": "cross_project_delete_isolation",
        "layer": "authorization_boundary",
        "fault": "delete_in_one_project",
        "test": "tests.db.test_private_dataset_lifecycle.PrivateDatasetLifecycleTests.test_cross_project_delete_never_affects_other_project",
        "hard_invariants": ["cross_project_isolation", "other_project_blob_preserved"],
        "failure_condition": "Deleting one project changes another project's revision, quota, or blobs.",
    },
    {
        "case_id": "gc_reference_integrity",
        "layer": "storage",
        "fault": "orphan_and_referenced_blobs_coexist",
        "test": "tests.db.test_private_dataset_lifecycle.PrivateDatasetLifecycleTests.test_garbage_collection_preserves_references_and_removes_orphans",
        "hard_invariants": ["no_valid_blob_deletion", "orphan_removed"],
        "failure_condition": "GC deletes a referenced blob or retains the known synthetic orphan.",
    },
    {
        "case_id": "database_disconnect_fails_closed",
        "layer": "lifecycle",
        "fault": "database_connection_failure",
        "test": "tests.db.test_private_dataset_lifecycle.PrivateDatasetLifecycleTests.test_default_off_trust_validation_and_database_failure",
        "hard_invariants": ["fixed_safe_error", "no_filesystem_side_effect"],
        "failure_condition": "A database failure leaks details or performs lifecycle side effects.",
    },
    {
        "case_id": "feature_flags_default_off",
        "layer": "security_boundary",
        "fault": "missing_server_enablement",
        "test": "tests.test_dataset_lifecycle.DatasetLifecycleOfflineTests.test_lifecycle_contract_is_strict_and_default_off",
        "hard_invariants": ["lifecycle_default_off", "strict_configuration"],
        "failure_condition": "Lifecycle maintenance enables by default or accepts unknown configuration.",
    },
    {
        "case_id": "no_public_dataset_route",
        "layer": "security_boundary",
        "fault": "route_inventory",
        "test": "tests.test_dataset_lifecycle.DatasetLifecycleOfflineTests.test_existing_api_has_no_dataset_lifecycle_route",
        "hard_invariants": ["no_public_upload", "no_public_lifecycle_api"],
        "failure_condition": "The existing application exposes a Dataset upload or lifecycle route.",
    },
)


def canonical_suite() -> bytes:
    return json.dumps(
        {"suite_version": SUITE_VERSION, "cases": CASES},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def suite_sha256() -> str:
    return hashlib.sha256(canonical_suite()).hexdigest()


def assert_frozen() -> None:
    if suite_sha256() != FROZEN_SUITE_SHA256:
        raise RuntimeError("pv2_02c_suite_hash_mismatch")
