# Phase 3 — RAG roadmap

3.1–3.11 local engineering acceptance is complete. Public deployment is not included. Historical entries below retain their original milestone evidence; the final acceptance supersedes progress status only.

- [x] 3.1 Foundation: inspect actual deployment; reserve internal module contracts and metadata; document paths, storage/provider plan, trust boundaries and evaluation layers. No operational RAG.
- [x] 3.2 PostgreSQL + pgvector storage foundation: pinned PG17.11/pgvector0.8.7 trixie image and digest, verified isolated restore, transactional checksum migrations, empty document/chunk/profile/vector tables and actual vector tests. No selected embedding model/dimension, ingestion or generation. See [storage verification](phase3-storage.md).
- [x] 3.3 Local ingestion and chunking: secure bounded UTF-8 loader, structure-char-v1 (600/80), deterministic source/config revisions, transactional document/chunk persistence, current-revision indexes, 98 offline + 10 DB tests and real synthetic CLI smoke. See [ingestion design/results](phase3-ingestion.md).
- [x] 3.4 Document embedding and persistence: OpenAI text-embedding-3-small / 1536, isolated provider, validation/retry/safe telemetry, atomic profile-bound vector writes and reuse. 106 offline + 18 DB tests passed before one authorized synthetic live request. No schema change or new dependency; no query embedding. See [acceptance](phase3-embedding.md).
- [x] 3.5 Query embedding/exact retrieval: same OpenAI profile, scoped current/ready sources, exact cosine, Top-K/cutoff and stable ties. 109 offline + 31 DB tests and one synthetic live retrieval passed. CLI only; no public endpoint or ANN index. See [verification](phase3-retrieval.md).
- [x] 3.6 Context builder: deterministic ranked evidence JSON, stable labels, verified overlap/exact dedupe with audit and configurable full-serialization character budget (not tokens). 120 offline + 31 DB tests passed; saved synthetic smoke uses 2059/3000 chars. No model call or role promotion. See [verification](phase3-context.md).
- [x] 3.7 Grounded generation: independent rag-grounded-v1, unchanged AnalyticsAnswer inside rag-response-v1, server citation checks, pinned official tokenizer/full-message budget; 134 offline + 31 DB tests and one synthetic live smoke. Core answer/schema/citations pass; relevance caveat retained. No endpoint or /chat integration. See [verification](phase3-generation.md).
- [x] 3.8 RAG Evaluation baseline: frozen 15-case suite/policy, evidence-bound layered review, 141 offline + 31 database tests, complete fake-provider pipeline and 6 live synthetic runs. No observed hard/core blockers in live subset; repeated relevance limitations in 3/6. Not a release certification. See [evaluation](phase3-evaluation.md).
- [x] 3.8 Final baseline/candidate evaluation: preserved v1–v4 and frozen suite; minimal independent v5 conditional-premise rule; one15-case real regression, no hard/core blockers,95.56% descriptive quality.
- [x] 3.9 Independent release gate: preregistered hard/core100%, provisional85% quality and separate stability reporting; v5 PASS. One-run evidence only.
- [x] 3.10 Default-disabled /rag/chat: existing pipeline/contract, server-owned document scope, safe errors/logging, source/version tracking; existing /chat untouched.
- [x] 3.11 Local enablement after gate PASS: Docker rebuild, real synthetic endpoint smoke, health/db/RAG200, schema/source/version/logs and client boundaries validated.174 offline+31DB tests. No production fixture rows retained. See [final acceptance](phase3-final-acceptance.md).
- [ ] Later/public release: broader corpus calibration, repeated-run stability and multiuser authorization are outside this local milestone.

Design and baseline: [RAG foundation](phase3-rag-design.md). No frontend, Tool Calling, Agent or LangGraph work is part of the foundation.


### Phase 3 closeout — 2026-10-16

First local acceptance is complete, not repeated-run stability verification or public production certification. README now documents the disabled default, explicit local enablement, empty server-owned document allowlist and non-paid test commands. Preserve all historical prompt SHAs, frozen cases/rubrics and raw/reviewed evidence. No new paid regression or Phase 4 development in closeout.
