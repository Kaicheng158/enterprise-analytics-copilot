# Phase 5.7e local sales Agent product slice

## Product boundary

This phase connects existing components into one locally usable, default-off product path for the only supported Agent task: comparing two quarters in the seeded synthetic sales dataset. It does not accept arbitrary natural-language business questions and does not imply general Agent capability.

The path is:

1. a loopback-only browser console submits the fixed `compare_quarterly_sales` request;
2. the existing internal Agent API validates the request, shared-key credential, server-owned principal/scope and idempotency key;
3. `DeepSeekProductPlanProvider` invokes a fresh instance of the existing `DeepSeekStructuredPlannerTransport` for each new task under one process-level call/cost guard;
4. the existing structured provider, request binding and `PlanValidator` reject invalid or unauthorized Plans before persistence;
5. the existing PostgreSQL execution queue persists the task and returns `queued`;
6. the existing bounded Worker supervisor claims it and runs the durable Worker, authorized PostgreSQL sales tools, Checkpoints and Receipts;
7. the browser polls the existing task-status endpoint and renders the strict `SalesGrowthComparison`, including its source relation and dataset version.

No parallel Planner, Tool executor, queue, state machine or result contract was created. The small multi-request provider wrapper exists because the evaluated DeepSeek transport is intentionally single-call; it serializes calls and creates one real transport per new idempotent task while sharing the server-owned total budget.

## Local interaction

The independent entrypoint is `backend.product_agent_app:app`. It is not imported by the normal `/chat` or `/rag/chat` applications and is not included in Compose. To use it deliberately, an operator must configure every existing guard plus the new product guard:

- `LOCAL_AGENT_PRODUCT_ENABLED=true`;
- `INTERNAL_AGENT_API_ENABLED=true` and a strong `INTERNAL_AGENT_API_KEY`;
- `LLM_PLANNER_ENABLED=true` and a local `DEEPSEEK_API_KEY`;
- `DURABLE_LLM_PLANNER_ENABLED=true`;
- `DURABLE_WORKER_RUNTIME_ENABLED=true`;
- `DURABLE_AGENT_RUNTIME_ENABLED=true`;
- `PERSISTENT_AGENT_QUEUE_ENABLED=true`;
- `AGENT_WORKER_SUPERVISOR_ENABLED=true`.

Start it only on loopback, for example with `uvicorn backend.product_agent_app:app --host 127.0.0.1 --port 8773`, then open `http://127.0.0.1:8773/agent`. The page asks for the local internal key at request time and does not store it in browser storage. The API still converts that credential into a fixed server-owned identity and scope; neither can be supplied by the page.

The runtime creates, migrates, seeds and later removes a uniquely named disposable PostgreSQL database. It does not migrate the existing application database. A deliberately enabled real Planner request incurs DeepSeek API usage; this phase's tests use Mock HTTP and make no paid calls.

## Data evidence and RAG boundary

The returned calculation is grounded in two authorized read-only calls to `analytics.sales_orders`. The response reports `source_relation=analytics.sales_orders` and `dataset_version=synthetic-sales-db-v1`, while Checkpoints and Receipts preserve how the result was produced.

RAG is not invoked for this fixed database calculation. The existing RAG endpoint, retrieval security boundary, grounded prompt releases and citations remain unchanged. A future document-backed Agent task should call the existing scoped `RagService` as a bounded step rather than duplicate retrieval or generation.

## Safety and tests

Offline tests exercise default-off settings, all-guard activation, budget validation, multiple requests through the real adapter protocol with Mock HTTP, loopback-only UI serving and absence of embedded/persisted credentials. Disposable-database tests exercise Agent authentication, real request serialization, strict Plan validation, rejection of arbitrary request fields without a Planner call, asynchronous queue/Worker execution, source-backed results, task polling, three durable Receipts and idempotent repeat without another Planner call. Full verification passed 409 offline tests and 108 isolated database tests.

The full repository suites must remain the release evidence; this local product slice has no independent live release gate. No frozen Prompt, suite, rubric, verdict or historical evidence is modified.

## Current limitations

- No paid end-to-end smoke was run in this phase, so live provider availability is unverified for this composition.
- The UI supports only explicit period and region fields for synthetic sales; it is not a chat interface.
- The shared-key browser flow is suitable only for a controlled local preview, not multiuser production authentication.
- Planner budget telemetry is process-local and resets on restart.
- The disposable database means tasks do not survive stopping the local profile.
- There is no cancellation, operator reconciliation UI, durable metrics export or public deployment.
- RAG and the sales Agent are separate product paths; no combined answer is claimed.

## Final review — 2026-10-23

Final verification passed 409 offline tests and 108 isolated database tests. The two product database tests now create independent runtimes and assert their own call/token counts; neither depends on test execution order. Docker API and PostgreSQL containers were healthy. No paid provider request, frozen artifact change or production database migration was performed.

The same console is also served by `backend.managed_agent_app:app` for a zero-paid Fake Planner preview. Enable only `INTERNAL_AGENT_API_ENABLED`, `PERSISTENT_AGENT_QUEUE_ENABLED` and `AGENT_WORKER_SUPERVISOR_ENABLED`, configure a temporary local internal access key, and bind Uvicorn to `127.0.0.1:8773`. This profile uses Fake planning and an isolated disposable database; it must not be described as a live DeepSeek demonstration. All committed flag defaults remain false.
