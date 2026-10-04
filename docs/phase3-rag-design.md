# Phase 3.1 — RAG foundation

## Verified baseline (2026-10-04)

Starting commit: e4038fd; working tree was clean. analytics-v2 remains active with SHA b04bd2ea98f0e007b00c7a76e4b6a7e7ef8cd28478f350589e81f1f7b52d3fd6. v1/v2 releases, public AnalyticsAnswer schema, prompt assembly, generation config, gate policies and all historical evaluation remain untouched.

Compose runs healthy FastAPI and PostgreSQL services. API binds localhost:8765; database binds localhost:5432, uses postgres:17 and the persistent postgres_data volume. Actual server is PostgreSQL 17.11. Read-only queries against pg_available_extensions and pg_extension both returned zero rows for vector: pgvector is neither available nor enabled. No database writes or container changes were performed.

Existing backend contains chat/provider, config/pricing, database health and prompt/output modules. Requirements include FastAPI 0.141.1, psycopg/psycopg-binary 3.3.5, Pydantic 2.13.5, python-dotenv 1.2.3 and uvicorn 0.52.4 with pinned transitive dependencies. No embedding SDK, vector adapter, ingestion or retrieval implementation existed. Requirements remain unchanged.

## Implemented boundary, not a working RAG pipeline

backend/rag contains only frozen internal data records and typing.Protocol declarations: ingestion, chunking, embedding, retrieval and context. Nothing imports these from the HTTP or LLM path. There are no routes, concrete adapters, orchestration, storage tables, automatic initialization, generated vectors, corpus or network calls. Protocols are extension contracts, not runtime input validation or security enforcement. Do not instantiate them as functional implementations.

The existing Docker source allowlist only includes backend/*.py, so these unused nested declarations are not yet packaged. When a later phase actually wires RAG into runtime, explicitly update the allowlist and verify packaging. No Docker or /chat behavior changes in 3.1.

## Planned ingestion path

Authorized local source → allowlist/size/type validation → extract text and provenance → content hash/document revision → deterministic versioned chunks → selected embedding adapter → transactional PostgreSQL document/chunk/vector storage → mark revision ready. Start with explicit local UTF-8 text/Markdown files; PDF/OCR/connectors and uploads are later scope.

Future ingestion must be idempotent, detect duplicate content, retain failed/incomplete status, and prevent partially embedded revisions from retrieval. New document versions replace visibility atomically; deletion must remove associated chunks/vectors and respect retention policy. Never crawl URLs from documents or run embedded code. Source paths/URIs must not expose secrets or unrestricted local filesystem access.

## Planned online retrieval path

Authenticated server scope + user query → compatible query embedding → authorization/profile-filtered similarity search → ranked evidence hits → deduplication and bounded context blocks with source locators. Stop here until generation is separately approved. Context blocks are data, not role-bearing instructions. Empty authorized results must remain empty; no fallback to another tenant or embedding profile.

A later RAG integration must use a separate reviewed/versioned prompt contract and citation design; never edit published v1/v2 or silently append context to today's /chat.

## PostgreSQL + pgvector plan

Use the existing PostgreSQL service with an explicitly pinned PostgreSQL-17-compatible pgvector image/extension, after backup and restore verification. Keep the current persistent data intact; do not delete volumes or change the PostgreSQL major version. Install server extension binaries then enable vector in the intended database through a reviewed migration. A Python package alone does not install server support.

Start with documents/chunks and embedding-profile metadata migrations. Choose embedding model/dimensions and distance metric before defining vector columns; do not invent vectors or assume chat DeepSeek Flash is an embedding model. Keep vector sets from different profiles separate. Start with exact search for a small corpus; consider approximate indexes only after measuring retrieval recall/latency. Reference: [official pgvector installation and querying documentation](https://github.com/pgvector/pgvector).

## Embedding provider abstraction

Separate EmbeddingProvider from LLMProvider. Preserve document-vs-query operations and output ordering. Profile binds provider/model/revision/dimensions/preprocessing (including normalization and asymmetric modes). Future adapters validate counts, dimensions and finite values, expose safe failures and usage/latency/cost metadata, and manage bounded retries. Provider credentials stay server-owned. Provider/model/budget choice remains open; no dependency or paid API is needed for 3.1.

## Document and chunk metadata

Current internal records reserve document ID/revision/tenant/title/source URI/content hash/text; chunk ID/document revision/tenant/ordinal/text/source locator/chunker version/content hash. Embedding profile is separate from source identity. Source locators must be derived during extraction, never invented by the model.

Future persistence additionally needs media type, ingestion timestamps/status/error code, owner/access policy, effective business date where supplied, parent foreign keys, chunk offsets/page/section, embedding profile and embedding status. IDs and hashes need a documented deterministic policy; source hash and extracted-text hash must be distinguished. These are a storage plan, not an implemented migration. Documents/chunks carry no fabricated business records today.

## Security boundary

Retrieved and quoted content is untrusted data, never system/developer authority. Do not obey embedded requests to change instructions, disclose prompts/secrets, invoke tools or alter output format. Provenance does not make a source true. Delimit evidence and retain attribution; prompt boundaries alone do not solve injection.

AccessScope must come from a future trusted authorization layer, not client-provided tenant/doc IDs. Empty allowed IDs means deny all. Apply tenant/document/profile filters before ranking and recheck provenance during context assembly. Current project has no enterprise auth; do not expose retrieval until enforcement exists. Avoid raw sensitive text in telemetry; separate synthetic evaluation fixtures from any production corpus. Future tests must verify cross-tenant exclusion and embedded-instruction handling.

## Layered evaluation plan

- Retrieval: versioned synthetic corpus, queries and relevance labels; recall@k/MRR, irrelevant/empty results, access isolation, revision/deletion handling, deterministic ingestion, profile mismatch and latency.
- Generation (later): fixed evidence bundles; attribution/citation correctness, unsupported claims, conflicts, uncertainty and injection boundaries, schema validity. Freeze new rubrics independently; preserve Phase 2 gates and verdicts.
- End-to-end (later): query through authorized corpus to answer; task completion, groundedness, abstention, cost/latency and regression of existing chat behavior. Separate offline deterministic tests from live paid experiments; obtain approval before paid embedding tests.

No quality claims are made from scaffold imports. Phase 3.1 acceptance is isolation plus preservation of all existing offline tests, not retrieval correctness.

## Phase 3.2 update

The baseline above records the pre-change state. PostgreSQL now uses the pinned PG17.11/pgvector0.8.7 trixie image; vector is enabled by versioned migrations. Four empty RAG tables provide metadata and dimension-validated vector storage without choosing an embedding model. The vector column uses per-row profile checks rather than a fixed vector(n) typmod. See [backup, migrations and tests](phase3-storage.md). RAG module contracts remain disconnected from /chat.

## Phase 3.3 update

Local operator CLI now implements controlled Text/Markdown loading, versioned character chunking and atomic document/chunk storage. Readiness/current selection and source/config revisions are explicit. No embedding/retrieval/generation or /chat integration. See [ingestion contracts and verification](phase3-ingestion.md).

## Phase 3.4 update

Document-only embedding now uses OpenAI text-embedding-3-small at 1536 dimensions, with profile-bound atomic vector persistence and safe telemetry. Existing tables suffice; no schema or /chat change. Only one authorized synthetic live call followed all passing offline/database tests. Query embedding and retrieval remain unimplemented. See [embedding acceptance](phase3-embedding.md).
