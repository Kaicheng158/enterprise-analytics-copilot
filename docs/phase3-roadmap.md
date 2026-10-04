# Phase 3 — RAG roadmap

3.1 and 3.2 are implemented. Later tasks are proposals, executed on explicit request.

- [x] 3.1 Foundation: inspect actual deployment; reserve internal module contracts and metadata; document paths, storage/provider plan, trust boundaries and evaluation layers. No operational RAG.
- [x] 3.2 PostgreSQL + pgvector storage foundation: pinned PG17.11/pgvector0.8.7 trixie image and digest, verified isolated restore, transactional checksum migrations, empty document/chunk/profile/vector tables and actual vector tests. No selected embedding model/dimension, ingestion or generation. See [storage verification](phase3-storage.md).
- [ ] 3.3 Local ingestion and chunking: bounded text/Markdown support, provenance, hashing/revisions, idempotency and offline fixtures/tests.
- [ ] 3.4 Embedding adapter: select provider/model/dimensions/budget explicitly; add only necessary dependency, vector storage migration and compatibility validation. Live calls need explicit approval.
- [ ] 3.5 Retrieval: exact baseline search, server-owned access scope, filters, top-k and separate retrieval evaluation. Establish authorization before exposing an endpoint.
- [ ] 3.6 Context assembly: token budget, deduplication, provenance and untrusted-content boundaries; no role promotion.
- [ ] 3.7 RAG generation/integration: separately authorize endpoint/prompt/citation contracts and a new versioned release; keep published v1/v2 immutable.
- [ ] 3.8 Layered evaluation and final verification: retrieval, generation and end-to-end evidence, Docker reproducibility, security and existing-chat regression.

Design and baseline: [RAG foundation](phase3-rag-design.md). No frontend, Tool Calling, Agent or LangGraph work is part of the foundation.
