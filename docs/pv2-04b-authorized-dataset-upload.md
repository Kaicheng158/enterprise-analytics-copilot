# PV2-04b Authorized Project and Dataset Upload

Status: local synthetic acceptance complete; uncommitted review state. Public/customer use remains disabled.

## Scope and boundary

PV2-04b adds a second, versioned Product Facade surface without changing the PV2-04a analysis contract or the legacy synthetic-sales Agent API. It composes a live PV2-03a Session, PV2-03b single-owner authorization, the PV2-01 deterministic parser, PV2-02 persistence/lifecycle services and a private local blob adapter. It does not call an LLM, create an Agent execution, compute an analysis, generate a chart/report, or expose a public upload route.

Migration `011_product_dataset_upload.sql` adds only user-facing names and hashed write-idempotency records:

- `product_facade.project_profiles`: Project display name and owner link;
- `product_facade.asset_profiles`: Dataset display name;
- `product_facade.asset_revision_profiles`: the original safe basename for each immutable source revision;
- `product_facade.write_idempotency`: owner/operation-scoped HMAC key hash, canonical request hash and immutable result identifiers.

Raw idempotency keys, Session tokens, CSRF tokens, file values, blob references and filesystem paths are never stored in this schema. Historical migrations 001–010 are unchanged. Migration 011 was applied only to disposable databases.

## API contract

All routes require the default-off localhost Product Facade. The v2 routes additionally require `PRODUCT_DATASET_UPLOAD_ENABLED=true`, a server-held idempotency secret and an absolute private storage root. Browser writes require an exact same-origin `Origin`, live HttpOnly Session, current CSRF token and server-side owner authorization.

| Method | Local route | Result |
|---|---|---|
| GET | `/product-api/v2/workspace` | Named private Projects for the live Session |
| POST | `/product-api/v2/projects` | Idempotent named Project creation |
| GET | `/product-api/v2/projects/{project_id}` | Authorized Project metadata and quota |
| POST | `/product-api/v2/projects/{project_id}/datasets` | Bounded raw CSV/XLSX validation and persistence |
| GET | `/product-api/v2/projects/{project_id}/datasets` | Up to 100 active/ready revision summaries |
| GET | `/product-api/v2/projects/{project_id}/datasets/{dataset_revision_id}` | Exact authorized revision metadata and quality summary |
| GET | `/product-api/v2/projects/{project_id}/datasets/{dataset_revision_id}/preview` | Five-row/eight-column maximum, values redacted |

Project and Dataset writes require `Idempotency-Key` (`16–128` ASCII safe characters). Upload uses the raw file as the HTTP body. `X-Dataset-Metadata` is base64url-encoded UTF-8 JSON conforming to `dataset-upload-v1`; it contains the display name, safe filename and explicit typed Column contract. CSV and XLSX content types are checked but never trusted instead of the file structure. Requests stream into a bounded buffer and stop above 2 MiB before parsing.

The browser cannot submit principal, scope, authorization version, `trusted_local`, SQL, Tool names, blob references or storage paths. `trusted_local=True` exists only inside `AuthorizedDatasetProductService` after Session and Project authorization.

## Idempotency, concurrency and revocation

The raw idempotency key is HMAC-SHA-256 hashed with a server secret. An owner/operation/key advisory lock serializes same-key requests. A canonical request hash includes the Project, versioned metadata and source-file SHA-256:

- same key + same request reuses the recorded Project or Dataset revision;
- same key + changed request returns `409 product_idempotency_conflict`;
- invalid or quality-blocked input creates no storage write and does not consume the key;
- a crash after storage reservation but before idempotency recording is safe because storage identity is deterministic and reconciliation/retry reuses the same revision.

Project creation derives a deterministic UUID from the authenticated user and HMAC key hash, then uses an exact-contract `create_or_get_owned_project` path. Dataset asset identity derives from Project plus normalized Dataset display name. Existing project-wide raw-content/parse-contract deduplication remains authoritative, so duplicate bytes do not reserve quota twice.

An authorization write fence serializes Dataset publication with Project revocation. Revocation and upload use the same Project advisory lock; whichever obtains the lock first completes its authorized state change, and the next operation observes the resulting authorization version. Reads authorize before metadata/blob access and reauthorize before returning. Only active Project/Asset and ready AssetRevision/DatasetRevision rows can be listed, read or previewed.

PV2-03b currently has a single active owner relationship, which is the only effective Asset grant. Collaborative `AssetGrant` records do not yet exist and are not implied by these routes.

## Validation and preview semantics

The existing parser remains the source of truth for BOM/UTF-8, Unicode normalization, exact Decimal text, ISO dates, explicit money units, CSV row widths, XLSX ZIP/XML limits, macro/OOXML feature rejection, external-link rejection and bounded worker time/memory. A security correction now classifies formula-style values before typed conversion across every column type; legitimate negative Decimal values remain valid.

Quality findings are returned together and contain codes plus table/sheet/row/column locations, never raw cell values:

- `blocking_errors`: formula-like content and missing required values;
- `confirmations`: schema/type/date/number/boolean/style ambiguity;
- `warnings`: safe normalization notices.

Any blocking or confirmation item stops persistence. The authorized preview exposes schema names/types, row/column locations, total shape and quality codes; every non-null cell value is the literal `[redacted]`. It never returns a blob reference or local path. This conservative redaction is the PV2-04b local acceptance default, not a permanent product requirement. A later version must design an explicitly authorized, policy-aware preview for the owning user without weakening project/revision authorization, field sensitivity controls, auditability or response limits.

## Feature Value Gate v1

Decision: **Approve as necessary infrastructure** for Investigate first, then Simulate and Decide & Review; immutable revision identity also supports future Collaborate. It does not independently prove user value.

Pre-registered engineering proxies and observed synthetic results:

| Metric | Acceptance target | Observed |
|---|---:|---:|
| Complete authenticated browser path from Project creation to redacted preview | 1 path | 1 path |
| Client-supplied internal authority fields required | 0 | 0 |
| Additional quota for same-project duplicate file | 0 bytes | 0 bytes |
| Resources created by concurrent same-key repeat | 1 revision | 1 revision |
| Interrupted writes published as ready before reconciliation | 0 | 0 |
| Unauthorized cross-user/cross-project reads | 0 | 0 |

These are safety/workflow proxies, not a user study. No claim is made about minutes saved. A later usability baseline must compare the upload/clarification flow with manual file cleaning, CLI ingestion and repeated identifier copying.

## Verification

- **475/475 offline tests passed**.
- **171/171 disposable PostgreSQL tests passed**, including 12 PV2-04b database cases.
- Focused parser/upload/database set passed 43/43 before the full runs.
- A real localhost browser signed in with a synthetic account, created one private named Project, uploaded a synthetic XLSX, received one ready immutable revision and displayed a two-row/three-column preview with every value redacted. It then uploaded the equivalent CSV. Both revisions produced the same normalized SHA-256 and different source SHA-256 values. Each response retained its correct revision-level original filename. No LLM/Figma call occurred.
- The interrupted-write test observed `writing`, zero ready revisions and no idempotency result; bounded reconciliation marked it failed, and the same request/key then produced exactly one ready revision.
- CSV/XLSX synthetic equivalents produced equal normalized hashes and different source hashes.

The content-limited evidence is [PV2-04b synthetic acceptance](pv2-04b-synthetic-acceptance.json). It contains no credentials, Session/CSRF tokens, raw rows, blob references, storage paths or account identifiers.

## Remaining limits and PV2-04c conditions

This is not production upload. Production object storage, encryption/KMS, malware quarantine, physical-capacity enforcement, backup/tombstone recovery, scheduled monitoring, derived-data deletion and multi-host consistency remain **Hold**. The local harness requires an explicit schema, supports only 2 MiB CSV/XLSX, has no resumable/chunked upload, currently redacts all preview values, and exposes no user deletion/restore flow. An authorized real-value preview remains a future product contract. Validation failures do not consume an idempotency key. Dataset aliases are not created for project-wide duplicate content.

PV2-04c may begin locally only by reading exact authorized active/ready revisions through this service, using deterministic allowlisted calculations and preserving Decimal/unit/source locations. It must not accept SQL/Tool authority from the client, bypass quality confirmations, or bind an Agent task before the PV2-04d atomic authorization/execution design. Customer upload and production release remain blocked independently of PV2-04c.
