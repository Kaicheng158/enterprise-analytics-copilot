# Phase 5.8b — Safe Workspace Fake/Mock Agent Integration

## Feature Value Gate v1 record

- **Decision:** Approve for local Fake/Mock implementation only.
- **Primary line:** Decide & Review. It is also necessary UI infrastructure for later Investigate results; it does not implement Data Detective, Simulate, Follow-through or Collaboration.
- **User workflow:** choose a supported quarterly sales comparison in Analysis & Chat → submit once → observe the persisted task → inspect a verified chart and source metadata.
- **Baseline work/error:** the Phase 5.8a Workspace could only display fixed examples. The separate legacy console required a local internal key in the browser, created a new idempotency key per click, and had no safe lost-response/refresh recovery in the Workspace. Users could mistake samples for executed results or duplicate a task after uncertainty.
- **Expected reduction:** one in-Workspace structured submission instead of switching surfaces and handling a credential; zero duplicate task/Planner executions from double click or same-key recovery; no manual copying of verified totals/source into the chart; no new submission on locale change.
- **Pre-registered acceptance:** double click produces one POST; a lost POST response is recovered with byte-equivalent body and the same idempotency key; refresh with a known task ID performs GET only; older state versions never overwrite newer state; terminal states schedule no further polling; network loss preserves the last trusted task; 401/403/409/422/429/502/503 and network failures map to safe catalog text; EN/中文 switching produces zero task requests; completed values and source match the strict Agent response.
- **Duplication:** reuses Agent API models, fixed server identity/scope, Durable Runtime, queue/Worker, Checkpoint/Receipt, PostgreSQL sales Tool and response contract. The Bridge adds only browser-safe same-origin mediation and client state handling.
- **Development/operations cost:** one default-off localhost app, frontend transport/state tests, isolated database integration and a bounded polling client. It adds another local process profile that must be started and stopped deliberately.
- **Runtime/cost:** local PostgreSQL and Worker resources only; Fake Planner makes no paid model call. No production SLO or scale claim.
- **Security cost/boundary:** localhost-only, synthetic dataset, server-held internal key, strict task schema, default-off flags and no arbitrary Prompt/SQL/tool/model fields. This is not authentication, multi-user authorization or a public endpoint.

## Architecture

`backend.workspace_bridge_app:app` is an independent FastAPI entrypoint. It serves `frontend/workspace/` and two same-origin task routes. The browser sends only the strict `AgentTaskRequest` and `Idempotency-Key`; it never receives or stores `X-Internal-Agent-Key`. The Bridge uses the existing `InternalAgentAuthorizer` to create the fixed server principal/scope, then delegates to the existing managed disposable runtime with `DeterministicLocalPlanProvider`.

The local runtime creates a uniquely named disposable PostgreSQL database, applies existing migrations/seed, queues the task, and runs the existing bounded Worker supervisor. Checkpoint/Receipt, Lease/CAS, idempotency, Tool authorization and reconciliation behavior remain authoritative. The normal `/chat`, `/rag/chat`, internal Agent apps and Compose profiles are unchanged.

All four controls default off or remain operator-controlled: `WORKSPACE_AGENT_BRIDGE_ENABLED`, `INTERNAL_AGENT_API_ENABLED`, `PERSISTENT_AGENT_QUEUE_ENABLED`, and `AGENT_WORKER_SUPERVISOR_ENABLED`. The app enforces a loopback ASGI peer and loopback `Host`, rejects cross-site Fetch Metadata, requires an exact same-origin `Origin` on API writes, emits no CORS allowlist, and ignores forwarded browser headers. The Workspace CSP limits connections to `'self'`.

## Browser behavior

Analysis & Chat exposes period/region selects for `compare_quarterly_sales`; there is no arbitrary prompt box. The client writes the payload and generated idempotency key to session storage before POST. A lost response remains `submission_unknown` and can only be recovered with that same payload/key. A known task ID resumes by GET after refresh.

Polling is bounded and uses state-version/update-time ordering. Terminal states stop polling. Network failure keeps the last validated response and requires an explicit status retry. Tasks shows only the current browser-session task because no authorized list endpoint exists. Visualizations switches from labelled sample content to validated task data only after a completed strict response. It shows `analytics.sales_orders`, `synthetic-sales-db-v1` and the task reference; it does not claim a full report was generated.

## Explicit exclusions

No real authentication/registration, upload, arbitrary chat, SQL, user-selected Tool/model, paid LLM, task listing, cancellation/reconciliation action, full report generation, multi-user collaboration, Decision Lab or external side effect is added. Figma and frozen Prompt/evaluation evidence are untouched.

## Local verification — 2026-10-28

- Python offline suite: **414/414 PASS**, including explicit hostile Host, missing Origin, cross-Origin and cross-site Fetch Metadata rejection.
- Isolated PostgreSQL suite: **110/110 PASS**; the Bridge-specific database tests verify one durable task for duplicate same-key requests, three completed receipts, strict source-backed result, and conflict for a changed request under the same key.
- Frontend unit suite: **14/14 PASS** for duplicate suppression, byte-equivalent same-key recovery, refresh/status recovery, terminal and configured-bound polling stops, monotonic ordering, network preservation, safe error mapping, i18n state isolation and same-origin/no-secret boundaries.
- Browser regression: **77 checks PASS** at 1440/768/390 and EN/中文; UX/accessibility/contrast suite **118 checks PASS**; auth regression **12 checks PASS**; final visual suite **21 checks PASS**.
- Real localhost Bridge smoke with Fake Planner and a disposable PostgreSQL database: one POST, bounded GET polling, `completed` at 3/3 steps, verified `analytics.sales_orders` / `synthetic-sales-db-v1` provenance, EN→中文 switch with no new Agent request, no browser internal key and usable 390px result. The disposable database was removed at shutdown.

The acceptance evidence measures the preregistered mechanics directly: double submit uses one POST, an unknown POST outcome reuses the exact body/key, refresh performs GET only once a task ID is known, stale versions cannot replace newer state, network loss preserves trusted data, and terminal results do not continue polling. This proves the local Fake/Mock slice only; it does not establish real authentication, production reliability, multi-user isolation or document-first value.

## Current status and remaining risk

Local implementation, product review and verification are complete. All relevant flags stay false in `.env.example`, the normal application/Compose configuration does not expose the Bridge, and no paid model was called.

Remaining product gaps are real identity/workspace authorization, authorized task listing and cross-device recovery, cancellation/operator reconciliation UX, production process management/metrics, uploads, arbitrary business questions, complete reports and public deployment controls. Session storage recovery is intentionally limited to the current browser tab. A later document-first task must define a new strict request/API and pass its own Feature Value Gate; it must not expand this four-field sales contract.
