# PV2-02a — Private Dataset Persistence Foundation

## Feature Value Gate v1 — preregistered 2026-11-13

Decision: **Approve for a default-off, trusted local synthetic harness**. Customer upload, public API and production migration remain **Hold** until PV2-02b lifecycle enforcement and PV2-03 identity/authorization.

This foundation supports Investigate / Decide & Review by preserving one verified source and its deterministic Dataset revision inside a private project boundary. It avoids manually naming/copying duplicate files and gives later analysis a stable revision reference. It is necessary infrastructure for Simulate and Collaborate, but it does not itself provide either workflow.

Baseline: PV2-01 returns an in-memory Dataset. A developer currently has to save the source, invent a filename/version, keep the normalized result, track hashes and manually avoid duplicates. A failed split database/file write has no recovery record.

Pre-registered synthetic engineering targets:

- Three identical submissions to one project/asset create one ready AssetRevision/DatasetRevision and consume quota once. Target: **two duplicate file copies and two manual version records avoided**.
- One changed source creates one new immutable revision and keeps the previous revision addressable. Target: **zero manual rename/version-index steps**.
- Concurrent identical submissions create one revision and one pair of private blobs. Target: **zero duplicate stored bytes and zero quota overcount**.
- Concurrent distinct submissions whose combined size exceeds quota allow only the transaction(s) that fit. Target: **zero quota overshoot**.
- Injected file-write failure releases its reservation and creates no ready revision. Injected final database failure leaves an unreadable reconciliation record; one explicit reconcile finishes or fails safely. Target: **zero visible partial assets, at most one operator reconciliation action, zero blind re-upload/retry**.
- Every returned revision is project-scoped; identical content in another project has a distinct revision identity and storage reference. Target: **zero cross-project reuse**.

These are deterministic workflow-operation counts, not observed user time savings. Real-user task time, confusion and recovery effort remain unmeasured.

Costs and risks: PostgreSQL metadata/locking, private filesystem capacity, fsync latency, reconciliation operations, quotas and lifecycle state. The local adapter is not encrypted object storage and does not satisfy production durability, backup or key-management requirements. No LLM/API cost.

## Contracts and state ownership

Project State is durable product state: project identity, lifecycle policy and asset membership. It is separate from Agent Execution State. Existing Checkpoint/Receipt records can reference a future DatasetRevision but do not grant access, own retention, create projects or keep source files alive.

- `Project`: server-created scope, quota counters, lifecycle policy and state.
- `Asset`: stable project-local logical data source. The synthetic harness supplies a server-trusted asset key; it is not an authorization credential.
- `AssetRevision`: immutable source identity, parse policy/contract, byte count, state and opaque storage reference.
- `DatasetRevision`: immutable normalized Dataset identity and opaque normalized-blob reference, one-to-one with AssetRevision.
- `StorageOperation`: coordination/reservation record. `reserved` and `writing` are not readable. `reconciling` means storage may exist but publication is uncertain. Only `ready` revisions are returned as usable.

Lifecycle states are reserved for PV2-02b: projects/assets are `active`; revisions support `pending`, `ready`, `reconciling`, `failed`, `expired`, `deleting`, `deleted`. This phase only creates pending/ready/reconciling/failed. `retention_until` and deletion markers are metadata placeholders; no expiry job, delete, restore or physical cleanup policy is claimed.

## Storage and transaction coordination

PostgreSQL stores identifiers, hashes, state, policy versions, quotas, sizes and opaque references. Raw CSV/XLSX and canonical Dataset JSON are private blobs. A storage reference is an unguessable structured token resolved only by the adapter; no database/API result exposes an absolute path.

The trusted service parses through PV2-01 first, then takes a project/asset advisory lock. It reuses an existing ready revision before reserving quota. Otherwise it atomically checks `used + reserved + requested <= quota`, increments reserved bytes and inserts pending metadata. The private adapter writes each blob to a random temporary file in its configured root, fsyncs, then atomically publishes the opaque object. Final database settlement verifies the operation/revision, moves reserved to used bytes and marks both revisions ready.

Storage failure removes newly written blobs when safe, releases reservation and records failed; a later identical request may reuse that unpublished operation/reference only after the known cleanup completed. If cleanup itself or final database settlement is unavailable/uncertain, the revision remains non-readable and is marked reconciling when possible. Reconciliation verifies both opaque blobs against expected hashes/sizes before publishing. Missing/mismatched blobs fail closed and release reservation. It never infers success from filenames or retries an unknown write to a different reference.

This is not a distributed transaction or exactly-once guarantee. A process crash immediately after committed reservation leaves a non-ready `writing` operation; explicit reconciliation fails it and releases quota when the expected blobs are absent. A later crash can leave private orphan blobs or a reserved/reconciling operation. PV2-02b must add stale-operation scanning, expiry, physical deletion, restore rules and orphan cleanup. Filesystem and PostgreSQL backups are not coordinated in this phase.

Project-wide duplicate identity is `(project_id, source_sha256, parse_contract_sha256)`. A repeated source using the same parse contract returns the original Asset/Revision even if the trusted harness proposes a different asset key. The same raw bytes under a changed parse contract intentionally create a new revision and currently duplicate the raw blob; physical content-object reference counting is deferred. Different raw bytes that normalize identically remain separate provenance revisions. Ready duplicates are reused only after both private blobs pass stored size/hash verification.

Quota counts logical raw bytes plus canonical Dataset JSON bytes. Reservations cover these bytes while writing/reconciling. Filesystem block overhead, database storage, backups and orphan blobs after a total database outage are not measured by the counter. These omissions block production quota claims and belong to PV2-02b/production storage design.

## Trust and privacy boundary

No HTTP route, Workspace integration or environment-enabled production path is added. `DatasetPersistenceSettings.enabled` defaults false and the service additionally requires `trusted_local=True`. Client-provided project IDs never prove authorization. Tests use generated synthetic data, unique temporary databases and temporary storage roots.

The service emits fixed error codes. It does not log source bytes, Dataset values, filenames, paths or database exception text. Full Dataset JSON remains private data with encryption at rest still unaddressed; it is not evidence. Synthetic evidence records counts, hashes and states only.

The local adapter enforces private directory/file modes and rejects symlinked object shards, but provides no application-layer encryption, malware scanning, cross-host durability or coordinated backup. Its root is trusted server configuration. Same-user local processes and host administrators are outside this prototype's isolation claim.

## Local implementation and evidence

Migration `007_private_dataset_storage.sql` creates a separate `dataset_store` schema and five metadata tables. It was applied only to uniquely named disposable databases. `backend.datasets.persistence` composes the unchanged PV2-01 parser, PostgreSQL reservation/metadata transactions and opaque local blobs. `DatasetPersistenceSettings.enabled` defaults false; no environment or HTTP entry was added.

Synthetic evidence is [recorded separately](pv2-02a-synthetic-persistence.json). Three identical submissions produced one revision and two reuse results; one changed source became revision 2; an injected interruption required one explicit verified reconciliation; reservations returned to zero. The evidence contains no source rows, filenames, storage references or paths. Unit/database fault tests separately cover different-project identity, cross-asset concurrent duplicates, quota races, write failure, missing blob, database connection failure, invalid Dataset and privacy-safe result/error rendering.

Actual results measure engineering operations only: duplicate copies/version-index steps were zero in the harness, and known interruption recovery required one reconcile call. They do not show how much time real users save or whether the workflow is understandable.

## Explicit PV2-02b dependencies

PV2-02b must implement stale reservation/operation discovery, scheduled reconciliation, retention enforcement, deletion tombstones, physical deletion, restore/recycle-bin policy, orphan/staging cleanup, content-object accounting, backup/restore semantics and storage quota coverage beyond logical content bytes. Encryption/key management, production object storage and antivirus/content scanning require separate deployment decisions. PV2-03 remains required for real identity and authorization. No customer file intake, public upload, download, preview API or Agent execution is enabled by PV2-02a.

## Final local verification — 2026-11-15

- **446/446 offline tests PASS** (13.170 s; 14.89 s wall time), including six private-adapter tests for permissions, atomic failure cleanup, post-publication failure, duplicate reference, symlink rejection and default-off contracts.
- **127/127 isolated PostgreSQL tests PASS** (60.527 s; 62.02 s wall time), including seventeen PV2-02a database tests. They cover formal migration/public privilege revocation, CSV/XLSX, immutable updates, project-wide and concurrent deduplication, cross-project separation, quota competition, known write failure/retry, a process crash immediately after reservation, cleanup uncertainty, finalization uncertainty, missing/corrupt blobs, database failure, trust boundaries and sensitive-output checks.
- Synthetic evidence reran with an exact byte-for-byte JSON match. No model/Figma call occurred.
- A read-only query confirmed the existing application database has **0** applied rows for `007_private_dataset_storage.sql`. All migration tests created and dropped uniquely named disposable databases; no production/application migration ran.
- Scoped scan reviewed all 13 changed/new files: no local secret value, key/private-path pattern, tracked `.env`, virtual environment, cache, backup or protected runtime/frozen-asset change. Historical ignored backup files already present under `backups/` were not touched or tracked.
- `git diff --check` passes. The final review started from synchronized `main`/`origin/main` at `0a8d401`; only the 13 scoped PV2-02a files listed below were eligible for the final commit.

Modified/new files:

- `migrations/007_private_dataset_storage.sql`: formal isolated metadata/quota/operation schema.
- `backend/datasets/persistence_models.py`, `private_storage.py`, `persistence.py`, `__init__.py`: contracts, opaque private blobs and coordination service.
- `tests/test_private_dataset_storage.py`, `tests/db/test_private_dataset_persistence.py`: offline, database, concurrency, fault and privacy coverage.
- `scripts/verify_dataset_persistence.py`, `docs/pv2-02a-synthetic-persistence.json`: reproducible synthetic evidence.
- `docs/pv2-02a-private-dataset-persistence.md`, `README.md`, `docs/architecture.md`, `docs/product-roadmap-v2.md`: boundaries, current status and PV2-02b dependencies.
