# PV2-02b — Private Dataset Lifecycle, Deletion and Garbage Collection

## Feature Value Gate v1 — preregistered 2026-11-15

Decision: **Approve for a default-off trusted-local synthetic foundation**. Public/customer lifecycle, automatic scheduling and user-facing restore remain **Hold**.

This work supports Investigate and Decide & Review by preventing expired or deleted Dataset revisions from being used as evidence. It is required infrastructure for Collaborate because project continuation needs bounded retention rather than indefinite raw-file storage. It does not add a product upload, download, recycle-bin or collaboration workflow.

Baseline manual recovery requires an operator to identify due records, prevent new reads, locate two private blobs, remove them, adjust logical quota, inspect abandoned writes/staging files and decide whether an interrupted operation succeeded. These steps are error-prone and can expose partial state.

Pre-registered synthetic engineering targets:

- expiry changes a ready revision to non-readable without deleting bytes or releasing quota: **zero stale reads**;
- one deletion uses mark + resumable completion instead of direct metadata/filesystem edits: **zero manual path lookup and zero manual quota edits**;
- repeated or concurrent completion releases logical quota exactly once: **zero negative or duplicate release**;
- a crash after deleting one blob resumes safely: **zero ready partial revisions and zero blind recreation**;
- one bounded maintenance run handles due expiry, stale write reconciliation and reference-checked GC: **one invocation, fixed item limit, auditable counts**;
- referenced blobs and other projects remain untouched: **zero cross-project/reference loss**;
- synthetic orphan and staging items are reclaimed: **zero known synthetic orphan/staging items after the run**.

These targets measure engineering operations and invariant failures. They do not demonstrate real-user time savings, comprehension or production operator effort.

## Lifecycle contract and state transitions

PV2-02b keeps migration 007 unchanged and adds migration 008. Existing Project/Asset/AssetRevision/DatasetRevision identities and project-scoped duplicate rules remain authoritative.

- `ready → expired`: retention elapsed. AssetRevision and DatasetRevision immediately become unreadable; private blobs and logical quota remain.
- `ready|expired → deleting`: deletion is marked in PostgreSQL before filesystem work. `ready_at` is cleared, so no supported read path can return the revision.
- `deleting → deleted`: both opaque blobs have been removed, a fenced lifecycle operation owns finalization, storage references are cleared and logical quota is released once.
- deletion I/O or database uncertainty leaves Asset/Dataset state `deleting` and lifecycle operation `reconciling` or lease-held `removing`. Re-running completion removes missing files idempotently and resumes from database state.
- failed/pending/reconciling persistence revisions are not made readable by lifecycle maintenance. Stale StorageOperations are passed to the existing hash/size-verifying reconciliation path.

`require_readable` requires Asset, AssetRevision and DatasetRevision to all be active/ready. Expiry and deletion therefore block new reads before any physical cleanup. Physical deletion is final in this phase. “Recovery” means operational continuation after interruption; it does **not** mean user recycle-bin recovery. A user-visible restore window remains Hold.

## Coordination, leases and quota

Normal persistence/reconciliation takes a shared global maintenance lock. Persistence also takes a shared project lifecycle lock. Expiration/GC obtains a non-blocking exclusive maintenance lock; deletion marking/completion obtains the project-exclusive lock. A maintenance task cannot classify a staging file or StorageOperation while a legitimate writer holds the shared lock.

Lifecycle deletion operations have a lease owner, expiry and monotonically increasing fence token. Only the current owner/fence may settle the operation. A crashed worker can be taken over after expiry. Old workers cannot finalize after takeover. Database state is the source of truth; a filesystem success without database finalization remains non-readable and must be resumed.

Logical quota is raw source bytes plus canonical Dataset JSON bytes. It remains charged through `expired`, `deleting` and uncertain states, then is decremented transactionally with the final `deleted` transition. Completion records the exact released count and cannot release it twice. `used_bytes` is checked before settlement rather than clamped, so accounting corruption fails closed.

GC separately reports logical file bytes removed and filesystem allocated bytes reclaimed. These are not project quota. Filesystem block overhead, unknown files, database storage, snapshots and backups remain outside logical quota.

## Garbage collection and bounded maintenance

`PrivateLocalBlobStore.cleanup_unreferenced` accepts a complete server-built set of opaque database references. It scans only recognized private object names and `.staging-*` files inside non-symlink object shards. Unknown names and symlinked shards are not traversed or deleted.

The service calls GC only while holding the exclusive global maintenance lock. Because every valid writer commits its opaque references before creating staging/object files and must hold the shared lock during writes, no legal active write can be mistaken for an orphan. A crash releases the database session lock; its unreferenced staging/object can then be removed. GC never relies only on age or pathname.

`run_maintenance` is explicit, finite and repeatable. It expires due revisions, identifies bounded stale StorageOperations, resumes eligible lifecycle deletions, then reacquires the exclusive lock and removes reference-checked orphans/staging files. It returns counts, quota-independent physical reclamation and a `truncated` flag. No scheduler, daemon or Compose service is enabled.

The optional command `scripts/run_dataset_maintenance.py` requires both `PRIVATE_DATASET_LIFECYCLE_ENABLED=true` and `--trusted-local-synthetic`, an absolute private root and an explicit item limit. The checked-in default is false. It is an operator-only local entrypoint, not an HTTP API.

## Audit, privacy and residual copies

Migration 008 adds content-free lifecycle audit events containing fixed event/result/error codes, UUID references, counts and byte totals. It stores no filenames, paths, source rows, Dataset values or database exception text. API keys and raw content are never evidence.

Deletion covers the two blobs owned by this local Dataset revision. It does not prove deletion from filesystem snapshots, backups, exported/downloaded copies, logs created outside this service, RAG chunks/vectors, reports or other future derived stores. Backup tombstone propagation, derivative dependency cleanup and restore drills belong to PV2-02c/related product stages. Until then the product must not claim global erasure.

## Synthetic verification scope

The repeatable evidence generator creates a uniquely named disposable PostgreSQL database and temporary private root, applies migrations 001–008 there, and drops both resources. It records only counts/states and is saved in [synthetic lifecycle evidence](pv2-02b-synthetic-lifecycle.json).

The evidence demonstrates one exact quota release, one expired revision, one stale write failed closed, one orphan and one staging file reclaimed, two referenced blobs preserved, zero reserved bytes and no paid calls. Fault tests additionally cover concurrent deletion, mid-delete crash/restart, disk failure, database failure, active-writer exclusion, maintenance bounds, symlink-safe GC and cross-project isolation.

## Explicit PV2-02c dependencies and risks

PV2-02c must address production object storage, encryption/KMS, malware scanning, multi-host durability, storage-provider conditional deletion, physical capacity enforcement, scheduled execution with leader election/monitoring, stuck-operation alerts, backup/tombstone propagation, restore drills and dependency cleanup for future chunks/embeddings/reports. A user recycle bin needs an explicit retention window, authorization, quota rules and restoration tests before it can leave Hold.

PV2-03 real identity and resource authorization still blocks customer data. The local filesystem adapter trusts the configured host account and cannot isolate same-user processes or administrators. There is no public upload/download/lifecycle route, no real customer data, no production migration and no background process in this phase.

## Local verification — 2026-11-15

- **452/452 offline tests PASS** (13.188 s). New coverage includes strict/default-off contracts, fail-closed manual entrypoint, absence of a public lifecycle route, reference-aware bounded GC and symlink-shard rejection.
- **140/140 isolated PostgreSQL tests PASS** (85.380 s). Thirteen lifecycle cases cover migration permissions, expiry/read blocking, repeated and concurrent deletion, project write/delete locking, crash/restart, disk/database failure, quota settlement, stale-write reconciliation, active-writer fencing, maintenance limits, audit fields and cross-project isolation.
- **25/25 focused lifecycle/storage tests PASS** (7.015 s).
- The first full database run encountered one pre-existing Durable Agent timing assertion. The test intentionally retains a one-second lease after a simulated post-checkpoint crash, then uses a fixed 1.05-second sleep before takeover; a narrow scheduling or clock boundary can therefore correctly yield `lease_unavailable`, surfaced by the outer runtime as `paused`. The unchanged test passed immediately in isolation, in the complete 140-test rerun, and in 15/15 additional isolated repetitions. Lease/CAS assertions, runtime code and test thresholds were not weakened or skipped. This is classified as an existing narrow test-timing risk rather than a PV2-02b lifecycle regression.
- The synthetic evidence generator reran with an exact JSON match. It used a uniquely named disposable database and temporary storage root, then removed both. No paid model or Figma call occurred.
- Read-only application-database inspection returned no applied rows for migrations 007 or 008. Migration ordering is contiguous through 008; only disposable databases received the new schema.
- `git diff --check` passes. Scoped scans found no API key, secret, local absolute path, tracked `.env`, backup, virtual environment, cache or frozen evaluation change. The 18 local files remain uncommitted for review.
