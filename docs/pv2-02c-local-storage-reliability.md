# PV2-02c — Local Storage Reliability Acceptance and Production Gap Review

Date: 2026-11-15

Scope: synthetic data, disposable PostgreSQL, single-host private local storage

Decision: **PASS for starting PV2-03 local identity/authorization development; HOLD for customer upload and production release.**

## Feature Value Gate v1

Decision: **Approve this bounded acceptance work**. It supports Investigate and Decide & Review by checking that evidence cannot become readable from partial writes, expired revisions or failed deletion. It is necessary infrastructure for Collaborate because project authorization is unsafe if storage state, project isolation or deletion is unreliable.

The prior operator baseline required separately reasoning about write state, two blobs, logical quota, leases, reconciliation and GC. The acceptance runner now executes one frozen 20-case matrix and returns one content-free verdict. Pre-registered hard targets were: zero expired reads, zero false-ready state, zero cross-project effects, zero valid-blob GC deletion, zero duplicate quota release, stale-worker finalization rejected and repeated reconciliation idempotent. All targets passed in the synthetic harness. This is engineering evidence; it does not establish real-user time savings, production durability or operational SLOs.

No test was added merely to increase the count. Eighteen existing parser/storage/lifecycle tests were selected because each maps to a required failure mode or invariant. Two missing high-risk checks were added: stale deletion-worker fencing after lease takeover, and repeated verified reconciliation.

## Consistency review: PV2-01 → PV2-02a → PV2-02b

- **PV2-01 parsing:** source bytes are validated before persistence; CSV/XLSX share deterministic normalization, exact Decimal handling and versioned parse contracts. Formula caches remain untrusted. Ambiguity remains explicit rather than silently guessed.
- **PV2-02a persistence:** Project/Asset/immutable AssetRevision/DatasetRevision records bind raw hash, normalized hash and parse contract. Duplicate identity is project scoped. Only verified `ready` pairs are returned. Database reservation, opaque blob writes and final publication are coordinated conservatively; uncertainty enters reconciliation.
- **PV2-02b lifecycle:** `expired` and `deleting` revisions fail the read barrier before physical cleanup. Deletion is resumable and fenced; quota is released transactionally once. GC uses a complete database reference set under an exclusive maintenance lock and cannot race a legal active writer.
- Project State remains distinct from Agent Checkpoint/Receipt state. A client-supplied project identifier is still not authorization.

The contracts are internally consistent for the current trusted-local, single-host scope. StorageOperation `state_version` is diagnostic/version metadata; persistence safety currently relies on database row/advisory locks and state predicates rather than a general public CAS API. Lifecycle deletion additionally uses lease ownership and monotonically increasing fence tokens.

## Frozen fault-injection matrix

Suite: `pv2-02c-local-reliability-v1`

SHA-256: `ca31f06afdefc620e2919687a4d90564fa940424f41f9b44deb316b7f07c6eb0`

Definition: `eval/datasets/pv2_02c_suite.py`

Evidence: [PV2-02c reliability evidence](pv2-02c-reliability-evidence.json)

All cases are hard-invariant checks; any single failure makes the local acceptance verdict FAIL.

| Area | Faults / conditions | Required outcome | Result |
|---|---|---|---|
| Parsing | CSV/XLSX variation; formula cache | Same normalized data and Decimal precision; no formula/cache trust | PASS |
| Revision identity | duplicate and changed source | duplicate reuses; changed content creates a new immutable revision | PASS |
| Filesystem write | atomic write failure | no ready publication, partial blob or leaked reservation | PASS |
| Database boundary | connection failure; finalization uncertainty | safe fixed error; verified reconciliation before ready | PASS |
| Process crash | exit after reservation | pending/unreadable state; bounded reconciliation; coherent quota | PASS |
| Upload concurrency | identical writes; quota competition | one revision/blob pair/charge; no quota overshoot | PASS |
| Reconciliation | repeated verified request | no additional state, quota, revision or blob change | PASS |
| Expiry | retention elapsed | zero new reads; quota retained until deletion | PASS |
| Deletion interruption | crash after first blob | remains unreadable; resumes; quota released once | PASS |
| Upload/delete race | project write concurrent with deletion | serialization; other ready revision preserved | PASS |
| GC/write race | maintenance during active writer | maintenance rejected; active object not collected | PASS |
| Lease takeover | expired lease and new fence | old worker rejected; current worker alone can finalize | PASS |
| Duplicate delete | repeated request/completion | idempotent completion; no negative/double quota release | PASS |
| Project isolation | delete in one project | other project metadata, quota and blobs unchanged | PASS |
| GC integrity | referenced and orphan blobs coexist | referenced pair preserved; known orphan removed | PASS |
| Security boundary | flags absent; route inventory | lifecycle default off; no public Dataset upload/lifecycle route | PASS |

Result: **20/20 PASS**, zero paid calls. The runner creates disposable databases through the selected tests and records no source values, paths, credentials or raw exception details.

## Required invariants

- Immutable revisions and project-scoped duplicate identities: PASS.
- Cross-project metadata/blob isolation: PASS in the service/test boundary; real principal authorization is not yet present.
- Expired or deleting revision reads: zero in tested paths.
- Failed/uncertain writes becoming `ready`: zero.
- Logical quota release: exactly once for repeated, interrupted and taken-over deletion.
- Referenced blob deletion by GC: zero.
- Recovery consistency: interruption resumes from database state; missing or mismatched blobs fail closed.
- Stale worker writes: an old deletion fence cannot finalize after takeover.

## Durable Agent lease-test investigation

The existing crash-recovery test previously slept 1.05 seconds for a one-second lease. A scheduler/database-clock boundary could correctly leave the lease active and surface `lease_unavailable` as `paused`. Before correction, the unchanged test passed 20/20 repetitions in this review (and 15/15 in PV2-02b), but the fixed 50 ms timing margin remained inherently flaky.

The test-only setup now proves the crash left an active lease, then deterministically expires that lease in the disposable database before restart. It preserves the original assertions: one completed tool call before the crash, no replay of that step, execution completion and exact final result. No timeout was extended, no assertion was weakened and no case was skipped. The corrected test passed once verbosely plus 20/20 repeated runs.

## Current applicability and hard limits

Accepted use is limited to a trusted server process, one local host, synthetic/non-customer data, an absolute private storage root and disposable PostgreSQL validation. Feature flags remain off. Host administrators and same-user local processes are outside the isolation boundary.

Logical quota measures raw plus canonical Dataset bytes. It does not reserve filesystem blocks, prevent disk exhaustion, account for filesystem overhead/database/WAL/backups/orphans in real time, or provide a per-volume capacity guarantee. Local permissions are not encryption. `fsync` and atomic same-filesystem publication are tested locally but do not establish multi-host or cloud durability.

Physical deletion covers the two current private blobs. It does not erase snapshots, backups, exports, future RAG chunks/embeddings, caches, reports or logs outside this service. No user recycle bin or restore-after-physical-delete claim exists.

## Production gaps and release blockers

| Capability | Status | Production/customer-upload decision |
|---|---|---|
| Real identity, secure session and project/asset authorization | Missing; PV2-03 | **Blocker** |
| Private production object storage with conditional operations and multi-host semantics | Missing | **Blocker** |
| Encryption at rest, KMS/key rotation and secret/access policy | Missing | **Blocker** |
| Malware/file safety scanning and quarantine before validated/ready | Missing | **Blocker** |
| Physical capacity monitoring/enforcement and disk-full safety policy | Missing | **Blocker** |
| Scheduled reconciliation/expiry/GC with leader election, alerts and runbooks | Manual bounded command only | **Blocker** |
| Backup/restore drills, deletion tombstone replay and recovery objectives | Missing | **Blocker** |
| Derived-data dependency registry and deletion propagation | Missing; required before derived stores consume customer assets | **Blocker when those stores are enabled** |
| Multi-host fencing/clock/storage consistency and load/failover tests | Not established | **Blocker for multi-instance release** |
| User recycle bin and self-service restore | Hold | May follow MVP if product promises do not include restore |
| Geo-redundancy/advanced retention tiers | Undecided | May follow initial release subject to declared SLO/compliance scope |

Passing PV2-02c does not change these blockers or authorize a production migration.

## Recommended PV2-03 scope

PV2-03 may proceed locally against this accepted storage foundation, while customer upload remains disabled:

1. Establish server-authenticated principal and secure session contracts; Demo identity cannot authorize Dataset access.
2. Implement private single-owner Project authorization first, then explicit project membership only if required. Keep AssetGrant separate from Project membership so project sharing does not imply raw-file access.
3. Enforce authorization in every Project/Asset/Revision metadata read, content read, deletion/retention action and task submission. `project_id`, `asset_id`, scope strings and idempotency keys are locators, never credentials.
4. Bind Agent tasks to principal, project and immutable DatasetRevision IDs. Recheck authorization before Worker read and before result publication; revocation must stop later access without reviving old grants.
5. Add content-free audit events for grant/deny/revoke and sensitive lifecycle actions; avoid file names, source values, internal paths and secret/session values.
6. Freeze negative tests before enabling any route: cross-user enumeration, cross-project access, revoked access, CSRF/session misuse, idempotency across principals, queued-task revocation and error-detail leakage.
7. Keep upload/download routes and production migrations disabled until the production blockers above have separate approval and evidence.

## Verification

- Frozen reliability matrix: **20/20 PASS**.
- Complete offline suite: **453/453 PASS** (13.766 seconds final rerun).
- Complete isolated PostgreSQL suite: **142/142 PASS** (61.008 seconds final rerun).
- Corrected Durable Agent lease crash/recovery: verbose run plus **20/20 repetitions PASS**.
- Migrations remain contiguous 001–008; migrations 007/008 were not applied to the existing application database.
- No paid LLM or Figma call; no customer data or production database used.

The local PV2-02 foundation therefore meets the technical prerequisite to begin PV2-03 development, but remains **Hold** for any real-customer intake or public release.
