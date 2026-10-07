# Phase 3 — RAG roadmap

3.1–3.7 and the 3.8 evaluation baseline are implemented. Later tasks are proposals, executed on explicit request.

- [x] 3.1 Foundation: inspect actual deployment; reserve internal module contracts and metadata; document paths, storage/provider plan, trust boundaries and evaluation layers. No operational RAG.
- [x] 3.2 PostgreSQL + pgvector storage foundation: pinned PG17.11/pgvector0.8.7 trixie image and digest, verified isolated restore, transactional checksum migrations, empty document/chunk/profile/vector tables and actual vector tests. No selected embedding model/dimension, ingestion or generation. See [storage verification](phase3-storage.md).
- [x] 3.3 Local ingestion and chunking: secure bounded UTF-8 loader, structure-char-v1 (600/80), deterministic source/config revisions, transactional document/chunk persistence, current-revision indexes, 98 offline + 10 DB tests and real synthetic CLI smoke. See [ingestion design/results](phase3-ingestion.md).
- [x] 3.4 Document embedding and persistence: OpenAI text-embedding-3-small / 1536, isolated provider, validation/retry/safe telemetry, atomic profile-bound vector writes and reuse. 106 offline + 18 DB tests passed before one authorized synthetic live request. No schema change or new dependency; no query embedding. See [acceptance](phase3-embedding.md).
- [x] 3.5 Query embedding/exact retrieval: same OpenAI profile, scoped current/ready sources, exact cosine, Top-K/cutoff and stable ties. 109 offline + 31 DB tests and one synthetic live retrieval passed. CLI only; no public endpoint or ANN index. See [verification](phase3-retrieval.md).
- [x] 3.6 Context builder: deterministic ranked evidence JSON, stable labels, verified overlap/exact dedupe with audit and configurable full-serialization character budget (not tokens). 120 offline + 31 DB tests passed; saved synthetic smoke uses 2059/3000 chars. No model call or role promotion. See [verification](phase3-context.md).
- [x] 3.7 Grounded generation: independent rag-grounded-v1, unchanged AnalyticsAnswer inside rag-response-v1, server citation checks, pinned official tokenizer/full-message budget; 134 offline + 31 DB tests and one synthetic live smoke. Core answer/schema/citations pass; relevance caveat retained. No endpoint or /chat integration. See [verification](phase3-generation.md).
- [x] 3.8 RAG Evaluation baseline: frozen 15-case suite/policy, evidence-bound layered review, 141 offline + 31 database tests, complete fake-provider pipeline and 6 live synthetic runs. No observed hard/core blockers in live subset; repeated relevance limitations in 3/6. Not a release certification. See [evaluation](phase3-evaluation.md).
- [ ] Future integration/release: separately authorize endpoint/access-scope design, larger retrieval/security evaluation and Docker end-to-end verification.

Design and baseline: [RAG foundation](phase3-rag-design.md). No frontend, Tool Calling, Agent or LangGraph work is part of the foundation.
