# Phase 3.5 — Query embedding and exact vector retrieval

Phase 3.4 was committed and pushed as d3203e1 before work began; the working tree was clean. Phase 3.5 remains uncommitted.

## Contract and boundaries

QueryEmbeddingProvider is independent of the OpenAI adapter. Query and document vectors use exactly the same text-embedding-3-small / 1536 profile, raw text preprocessing and profile UUID 1418489a-d2c4-5bbc-a533-b4ba9178927e. The retained preprocessing label document-raw-utf8-v1 describes the identical query path too. No role prefix or normalization change. An API alias is not a guarantee of immutable upstream weights.

Operator CLI: python -m scripts.retrieve_local --tenant TENANT --document-id UUID --query TEXT --top-k 3 --max-distance 0.7. Repeat --document-id to authorize multiple documents. Scope must come from a trusted operator; these arguments do not implement enterprise authentication or RLS. No public endpoint was added.

Parameterized SQL filters tenant, allowed document IDs, current/ready revisions and matching profile/dimensions before distance calculation. It materializes the eligible rows and computes exact pgvector cosine distance (<=>), then filters and sorts. No approximate index, similarity-search service or database write is used. Query vectors are transient.

Results contain chunk_id, document_id, content, source_metadata (URI/title/revision/locator/chunk metadata), rank and distance. Source text remains untrusted data. Returned chunk hashes are checked; corruption fails closed. Sorting is distance ASC, document_id ASC, chunk_id ASC, with no rounding before ranking. Stable ordering applies to the same vector and database snapshot, not future provider outputs.

Top-K: integer 1–100, default 5. max_distance: inclusive finite [0,2], or None. None means no cutoff and returns nearest candidates, not guaranteed relevant results. A chosen cutoff permits an empty result; no padding to K. Similarity = 1 - distance. The smoke cutoff 0.7 is illustrative and uncalibrated; choose production cutoffs with labeled relevant and irrelevant queries later.

An empty authorized/current/profile-matched corpus returns [] without an API call. Other embedding profiles are never used as fallback. Stored-profile or query-profile mismatches fail; provider errors remain errors, not empty-result successes. A fresh SQL snapshot after the network call rechecks current revisions, avoiding historical results if a source changed during query embedding.

/chat, published prompts, Phase 2 schema/gates/evidence/generation config, migrations and dependencies remain unchanged. No context builder, citation response, RAG generation or HNSW/IVFFlat index. Local CLI only; API Docker packaging is unchanged.

## Verification and real evidence

109 offline tests and 31 database tests passed before live requests. Deterministic fake vectors test relevant/unrelated fixture queries, Top-K, inclusive cutoff, empty corpus, multiple documents, stable ties, current revisions, scope/profile isolation, provider errors, corruption and database consistency. These tests prove control flow and SQL behavior, not real-model semantic quality.

The pre-call plan is phase3-retrieval-smoke-plan.json. Existing synthetic corpus: two documents, four chunks. One preparation request embedded the three missing English chunks (189 tokens, 1354 ms, estimated USD 0.00000378); the existing Chinese vector was reused.

Query: How is a processed request counted when it is reopened?

Fixed Top-K=3, maximum distance=0.7. One query request: 11 tokens, 1035 ms, zero retries, estimated USD 0.00000022. Total query/retrieval latency: 1057 ms. Total preparation plus query estimated cost: USD 0.000004; estimates are not invoices.

| Rank | Source | Locator | Cosine distance |
|---|---|---|---:|
| 1 | metrics.md | chars:232:643 | 0.4312047111095042 |
| 2 | metrics.md | chars:0:312 | 0.6224643825514311 |

The first result contains the requested rule: reopened requests count once at final completion. Only two results met the cutoff; no third was forced. Full IDs, contents, metadata and usage are retained in [smoke evidence](phase3-retrieval-smoke.json). Query execution did not change embedding row counts; no ANN indexes exist. No paid irrelevant-query sweep or threshold tuning was performed. One successful query does not establish general retrieval quality; irrelevant-query abstention was tested using deterministic fixtures only.

Stop before Phase 3.6/context building. No Phase 3.5 commit or push.
