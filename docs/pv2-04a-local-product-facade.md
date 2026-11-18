# PV2-04a — Local Product Facade & Dataset Analysis Contract

2026-11-18 · Local implementation awaiting review. This document does not authorize a public route, customer upload, production migration, model call or Dataset analysis execution.

## Feature Value Gate v1

- **Decision:** Approve for the local integration foundation; customer-facing value remains unproven and public use remains Hold.
- **Product lines:** Investigate and Decide & Review. This phase supplies the protected handoff from a logged-in private Project to a revision-bound analysis request. It is necessary infrastructure for Simulate and Collaborate but does not claim either workflow is available.
- **Baseline:** the Auth app, Project Authorization service, Dataset metadata and Durable Runtime can be called only as separate trusted modules. There is no single browser-safe origin, no file-analysis request contract, and no centralized clarification/result/chart/report contract. A client integration could otherwise be tempted to send user/scope/SQL/Tool fields or reuse the four-field synthetic sales task.
- **Value hypothesis:** one same-origin server boundary should remove client handling of principal/scope/authorization claims; one validation request should return all currently detectable missing analysis requirements together; validation should create zero paid/model/Tool/Durable work.
- **Pre-registered local metrics:** (1) zero client-controlled identity, scope, SQL, Tool or authorization fields accepted; (2) cross-user/revoked/expired paths return no Project or Dataset metadata; (3) one validation response contains every deterministically detectable missing measure/comparison/time requirement; (4) validation creates zero `agent_runtime.executions`; (5) legacy sales Agent requests and normal `/chat`/`/rag/chat` remain unchanged.
- **Measured local result:** the synthetic integration scenario reaches authenticated private Project creation and ready-revision contract validation through one localhost origin. Two missing comparison requirements are returned in one clarification bundle, and the execution count remains zero. Cross-user, expired Session, invalid CSRF, unready revision and revoked owner checks fail closed. This is engineering evidence, not a measured reduction in real-user completion time.
- **Duplication/cost:** reuses PV2-03a Auth Session, PV2-03b Project Authorization and PV2-02 Dataset metadata. New code is limited to strict contracts, metadata composition and a local facade. It introduces another default-off ASGI entrypoint and contract maintenance; no model/API cost is added.
- **Security/privacy cost:** the facade exposes only owned Project quota/state and authorized revision identity/hashes, never blob paths or Dataset rows. It still depends on local PostgreSQL credentials and the pre-production local storage boundary.

## Runtime boundary

`backend.local_product_facade_app:app` is an independent ASGI entrypoint. It reuses the existing PV2-03a `/auth-api/*` routes and static Workspace at the same configured origin, then adds `/product-api/*`. It is not imported by the normal API, RAG app, Workspace Fake/Mock bridge or Compose.

All three flags are independently default-off:

- `LOCAL_AUTH_ENABLED=false`
- `PROJECT_AUTHORIZATION_ENABLED=false`
- `PRODUCT_FACADE_ENABLED=false`

The facade accepts only an HTTP loopback origin. The existing Auth middleware enforces loopback peer and Host. Product writes additionally require exact same-origin `Origin`, optional Fetch Metadata must be `same-origin`, and the live opaque Session plus current CSRF token are resolved by the server. The browser cannot submit a principal, user ID, scope, authorization version or trusted-local flag.

Routes implemented in PV2-04a:

| Route | Behavior | Explicitly absent |
|---|---|---|
| `GET /product-api/config` | local policy/capability receipt | no secret, identity or dynamic enablement |
| `GET /product-api/workspace` | active private Projects for the live Session | no collaboration or asset contents |
| `POST /product-api/projects` | server-ID private Project + owner relationship using existing authorization transaction | no organization, sharing or client quota selection |
| `GET /product-api/projects/{project_id}` | owned active Project metadata | cross-user and missing are indistinguishable |
| `POST /product-api/projects/{project_id}/analysis-contracts/validate` | authorize active Asset and exact ready immutable revisions; validate/clarify the future request | no blob read, task, Tool, SQL, model, chart or report generation |

No migration is added. Migrations 001–010 remain unchanged and tests apply them only to a unique disposable database. The existing application database is not migrated.

## Versioned contracts

`DatasetAnalysisTaskRequestV1` is separate from `AgentTaskRequest`. It binds a normalized question to one-to-four exact `asset_id + asset_revision_id + dataset_revision_id` triples, one allowed intent (`profile`, `aggregate`, `compare`, `trend`) and explicit deliverables. Extra fields are forbidden. Standard JSON arrays/UUID strings are converted only at the declared typed fields; arbitrary coercion is not enabled.

The server builds `AnalysisContextV1` from the path Project, current authorization version and ready revision metadata. `execution_created=false` is mandatory. Missing deterministic requirements are returned once in `ClarificationBundle` with stable prompt keys. Column existence/type and business-definition checks require loading the private Dataset in PV2-04c and are deliberately not claimed here.

Future-only strict contracts are frozen for:

- task state: awaiting clarification, queued, running, reconciliation, completed, failed or revoked;
- deterministic Decimal calculation results with row/column/revision sources;
- bounded bar/line/table Chart Specs derived from calculation IDs;
- versioned report sections whose source indexes must resolve;
- content-free terminal errors.

These contracts do not make those features available. A completed task cannot exist without calculations, and non-completed tasks cannot expose result/chart/report fields.

## Revocation and failure rules

- Expired/revoked Session: 401.
- Missing/wrong CSRF or cross-site write: 403.
- Cross-user, revoked owner, inactive Asset or unavailable Revision: content-free 404.
- Disabled/unavailable facade/authorization/storage: sanitized 503.
- Authorization version changes during validation: 409 and no context is accepted.
- Invalid/extra body fields: sanitized 422 listing field names only, never submitted values.
- Revocation after a future task is created must rely on PV2-03b binding, queue/Heartbeat and database write guards. PV2-04a creates no task.

## Downstream ownership

### PV2-04b — authorized upload and preview

Adds the authenticated Project-scoped multipart/stream boundary, invokes the existing parser and persistence service internally with trusted-local authority, and returns bounded redacted preview/revision metadata. It must never accept `trusted_local` or blob paths from the browser. Upload, duplicate recovery and lifecycle read barriers belong there.

### PV2-04c — deterministic computation

Loads only the pinned authorized Dataset revisions and implements an allowlisted schema/profile/filter/group/aggregate/compare/trend engine with Decimal/date/unit rules. It verifies requested fields and business definitions. No arbitrary SQL/Python/Tool name enters the request.

### PV2-04d — Durable Agent integration

Creates a new execution only after contract/schema/clarification readiness, derives `product-project:<UUID>` server-side, binds the execution before queue eligibility and rechecks access before Dataset read, Tool work and result publication. Existing Checkpoint/Receipt/Lease/CAS/Reconciliation remain authoritative.

### PV2-04e — chart and report delivery

Builds Chart Specs and short/full versioned reports only from validated calculations and source references. It adds authorized task/result reads and UI rendering; it does not modify the legacy Workspace sales bridge or `/chat`.

## Verification and limits

Targeted offline tests cover strict request/output invariants, forbidden client authority fields, centralized clarification, chart/report source contracts, default-off settings and legacy request separation. Disposable PostgreSQL tests cover one-origin Auth + Product composition, private Project creation/list/read, exact ready Revision binding, cross-user denial, Session expiry, CSRF, cross-site writes, invalid body redaction, owner revocation and zero execution creation.

Local verification passed **469/469 offline tests** and, on the final complete run, **159/159 isolated PostgreSQL tests**. The first complete database run had one pre-existing one-second-Lease crash-recovery timing test return `paused`; no Product Facade code touches that runtime. This was not the former PV2-02c fixed-sleep expiry defect: the test already performs deterministic database expiry before takeover. The unchanged test and assertions then passed **20/20** targeted repetitions and the final full 159-test run. The one-second runtime Lease remains a recorded test-load sensitivity rather than being concealed by a skip, longer wait or weaker assertion.

The content-free machine-readable summary is [PV2-04a synthetic acceptance evidence](pv2-04a-synthetic-acceptance.json). It contains no account, Session, Dataset values, blob references or local paths.

Real upload, Dataset preview, computation, task persistence, chart rendering, report generation, collaboration, public access, production identity/storage and customer files remain unimplemented. Production blockers from PV2-02c and PV2-03 remain Hold.
