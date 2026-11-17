# PV2-03b — Local Project Authorization and Isolation

2026-11-17 · local synthetic implementation and acceptance; default off, no public deployment.

## Scope and boundary

PV2-03b adds a server-owned authorization layer after PV2-03a authentication. A live `AuthPrincipal` is resolved from an active, unexpired PostgreSQL session; a browser-supplied user ID, project ID, Demo visitor, internal Agent key or fixed Agent scope is never accepted as proof of product authorization.

The implemented product model is deliberately single-owner and private. `product_auth.project_owners` binds one authenticated user to one `dataset_store.projects` record. It is not collaboration, membership, invitation, role delegation or AssetGrant. Existing Demo, `/chat`, `/rag/chat`, Workspace Bridge and internal Agent routes remain separate and unchanged.

## Authorization paths

- Project creation writes the Dataset Project and active owner relationship in one database transaction. The project execution scope is derived by the server as `product-project:<project UUID>`.
- Project read/write requires a live session, active user, active project and active owner row.
- Asset read/write additionally requires the asset to belong to that project and remain active. An asset locator does not grant access and failures do not reveal whether another user's object exists.
- Product task binding requires an existing durable execution whose server scope exactly matches the project scope. Task read/execute requires the active binding and the same authorization version as the owner.
- Existing internal tasks without a product binding keep their existing fixed internal authorization. They are not converted into product tasks.

All decisions default to deny. The service returns content-free error codes and writes metadata-only audit events containing actor/session IDs, opaque resource IDs, action, decision, reason and authorization version. Audit rows contain no account, password, token, source file name, data values, prompt or result body.

## Revocation propagation

Revoking the owner relationship increments its authorization version and revokes every active project task binding in the same transaction. Subsequent project, asset and task reads fail. Durable queue claim, direct lease acquisition and heartbeat renewal exclude revoked bindings. Database triggers reject new Checkpoint or Receipt writes for a revoked or stale-version product task, so an old Worker cannot publish later state after revocation.

This does not claim recall of data already sent to an external service or cancellation of a non-idempotent external side effect already in progress. Current accepted tests use local synthetic and read-only tools. A future product execution facade must invoke the worker-side guard immediately before each authorized data read/tool call and retain reconciliation for ambiguous external outcomes.

## Migration and feature state

Migration `010_project_authorization.sql` is append-only and creates owner, task binding and authorization audit records plus the revocation write guards. Migrations 001–009 are unchanged. Migration 010 is applied only to disposable test databases in this phase. `PROJECT_AUTHORIZATION_ENABLED=false` by default, there is no public Project/Asset/Task API and no existing application database migration.

## Feature Value Gate v1

- **Decision:** Approve for local foundation; public/customer use remains Hold.
- **Workflow:** establishes the private owner boundary required before Investigate, Simulate or Decide results can use persisted customer datasets.
- **Baseline:** PV2-03a can authenticate a user but cannot distinguish which Project, Asset or task that user may access; every product-data experiment otherwise needs an ad-hoc trusted scope.
- **Preregistered local targets:** 100% denial of synthetic cross-user project/asset/task attempts; one server authorization decision per protected path; one owner revocation operation blocks all later protected reads and bound-task progression; zero identity or sensitive content in audit evidence.
- **Local result:** the targeted suite meets those engineering targets. It does not measure customer time saved or prove production tenancy.
- **Cost/security:** adds a database decision and audit write on protected paths, durable binding state and operational retention duties. Production indexing/load, audit retention, centralized policy observability and incident response remain unvalidated.

## Local evidence

Targeted authorization tests cover migration privacy, strict contracts, Demo separation, two-user negative cases, wrong-project task binding, session revocation, owner revocation, Worker claim/heartbeat denial, stale Checkpoint rejection and metadata-only audit. Existing Durable Agent, internal Agent API and queue regression tests prove unbound internal flows remain compatible.

Final local verification passed **463/463 offline tests** and **154/154 isolated PostgreSQL tests**. These tests use synthetic identities and disposable databases; they are not a production penetration test.

## Remaining release blockers

- No public Project/Asset/Task API, product task submission facade or customer upload path exists.
- Collaboration roles, invitations, resource-specific grants, ownership transfer and emergency admin access are not implemented.
- Production session secrets, HTTPS deployment, email verification/recovery/MFA and centralized abuse protection remain Hold from PV2-03a.
- Object storage/KMS, malware scanning, physical-capacity enforcement, backup/tombstone restoration, derived-data deletion and multi-host lifecycle consistency remain Hold from PV2-02c.
- Production database roles/RLS, connection-pool identity strategy, authorization cache invalidation, audit retention/export and load/race testing remain required.
- Revocation cannot undo information or side effects already transmitted outside the controlled runtime.

PV2-03b therefore completes only the local single-owner authorization foundation. Real customer intake and public deployment remain blocked.
