# Phase 3 — RAG roadmap

3.1–3.4 are implemented. Later tasks are proposals, executed on explicit request.

- [x] 3.1 Foundation: inspect actual deployment; reserve internal module contracts and metadata; document paths, storage/provider plan, trust boundaries and evaluation layers. No operational RAG.
- [x] 3.2 PostgreSQL + pgvector storage foundation: pinned PG17.11/pgvector0.8.7 trixie image and digest, verified isolated restore, transactional checksum migrations, empty document/chunk/profile/vector tables and actual vector tests. No selected embedding model/dimension, ingestion or generation. See [storage verification](phase3-storage.md).
- [x] 3.3 Local ingestion and chunking: secure bounded UTF-8 loader, structure-char-v1 (600/80), deterministic source/config revisions, transactional document/chunk persistence, current-revision indexes, 98 offline + 10 DB tests and real synthetic CLI smoke. See [ingestion design/results](phase3-ingestion.md).
- [x] 3.4 Document embedding and persistence: OpenAI text-embedding-3-small / 1536, isolated provider, validation/retry/safe telemetry, atomic profile-bound vector writes and reuse. 106 offline + 18 DB tests passed before one authorized synthetic live request. No schema change or new dependency; no query embedding. See [acceptance](phase3-embedding.md).
- [ ] 3.5 Retrieval: exact baseline search, server-owned access scope, filters, top-k and separate retrieval evaluation. Establish authorization before exposing an endpoint.
- [ ] 3.6 Context assembly: token budget, deduplication, provenance and untrusted-content boundaries; no role promotion.
- [ ] 3.7 RAG generation/integration: separately authorize endpoint/prompt/citation contracts and a new versioned release; keep published v1/v2 immutable.
- [ ] 3.8 Layered evaluation and final verification: retrieval, generation and end-to-end evidence, Docker reproducibility, security and existing-chat regression.

Design and baseline: [RAG foundation](phase3-rag-design.md). No frontend, Tool Calling, Agent or LangGraph work is part of the foundation.
