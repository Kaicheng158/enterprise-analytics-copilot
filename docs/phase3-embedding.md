# Phase 3.4 — Document embedding provider and vector persistence

## Implemented scope

Independent EmbeddingProvider document-only interface and OpenAIEmbeddingProvider using the official HTTPS /v1/embeddings endpoint. Fixed model text-embedding-3-small and 1536 dimensions; server settings reject other values. API key is loaded from root .env (environment takes precedence), hidden from configuration repr and never included in logs/evidence. .env.example contains placeholders only. Uses existing dependencies plus Python standard-library HTTP; no SDK/package installation, schema migration, Docker change or /chat integration.

No query embedding implementation, retrieval, RAG generation, HNSW or similarity search was added. Removing the previously reserved embed_query declaration keeps the implemented provider contract document-only; a future separately approved capability can extend it.

## Explicit invocation

```sh
analytics-agent-env/bin/python -m scripts.embed_document --tenant synthetic-phase33 --document-id DOCUMENT_UUID --revision REVISION_HASH
```

This operator-only command makes paid calls for missing vectors. Supply a single explicit tenant/document/revision; there is no automatic corpus-wide backfill. Tenant arguments are not a replacement for authorization. Current API Docker build still excludes the RAG package, and the command runs in the local project venv.

## Validation and bounded requests

Each request sends at most 16 nonblank documents, with a conservative 8191 UTF-8-byte limit per input (not a tokenizer estimate). Existing 600-character chunks fit; unusually large configured chunks can be rejected and must not be silently truncated. Explicit dimensions and float output format are sent. Response model, count, unique index coverage, order, dimensions, finite numeric values/float32 range, nonzero vector and integer usage totals are checked before persistence. The HTTP response is size-limited, and credential-bearing redirects are refused.

At most two retries with exponential backoff/jitter for rate-limit/selected transient server errors. Authentication, permissions, bad requests and known insufficient-quota errors are not retried. Ambiguous network/timeouts are not retried automatically. Error bodies and headers are never logged; only fixed safe codes are exposed. 30-second HTTP timeout is a transport timeout, not a hard whole-document deadline.

## Profile and persistence

Stable profile UUID derives from provider/model/revision/dimensions/preprocessing and cosine distance. The revision label is explicitly api-alias:text-embedding-3-small: it identifies the requested API alias, not a guaranteed immutable upstream weights snapshot. Preprocessing is document-raw-utf8-v1; preserve input text and provider float output, with no extra normalization or prompt. Future preprocessing/model changes require a distinct profile and re-embedding. No assumption that equal dimensions mean compatible vector spaces.

Read only current/ready source revisions; validate chunk hashes and identify missing (tenant,chunk,profile) rows. Skip already-persisted vectors without API calls. Batch network calls occur outside database transactions. Before committing, lock/recheck current source and chunk snapshot; a concurrent source change safely discards the result. Profile identity is checked. All missing vectors for the document commit atomically with profile registration. Provider/validation/DB failures leave no partial document vector set. Existing vectors and historical revisions are retained.

Unique keys prevent duplicate persisted vectors under concurrency, but parallel workers can both incur an API charge before insert conflict resolution. Likewise, a paid successful call followed by DB failure may need re-execution. This is persistence idempotency, not exactly-once external billing. No distributed job/lease or durable billing ledger is claimed.

## Telemetry and cost

Safe per-request logs contain provider, model, dimensions, status, tokens, latency, retry count, estimated USD cost, price version and cost-complete flag. They omit keys, headers, source text and vector arrays. Price snapshot: $0.02 / 1M input tokens, openai-embedding-2026-10-04. Retried requests mark total cost incomplete because failed-attempt usage may be unknown; displayed success cost covers known usage only. Prior successful batch logs remain available even if later batches fail. Estimates are not invoices.

Official references: [create embeddings](https://developers.openai.com/api/reference/resources/embeddings/methods/create), [model/pricing](https://developers.openai.com/api/docs/models/text-embedding-3-small).

## Verification

Before any live request: 106 offline tests and 18 explicit database tests passed. All offline adapter tests mock HTTP; persistence tests use deterministic fake providers in disposable databases. Tests cover output order, invalid vectors/count/index/model/usage, config and input limits, secret-safe logging, bounded retry/no-retry errors, profile separation, reuse, stale sources, DB rollback and multi-batch atomicity.

Only then one real request embedded the existing synthetic unicode.txt chunk:

- Model: text-embedding-3-small; stored dimensions: 1536.
- Input tokens: 150; latency: 2794 ms; retry count: 0.
- Estimated cost: USD 0.000003.
- Stored vectors: 1; profile 1418489a-d2c4-5bbc-a533-b4ba9178927e.
- Second persistence run: inserted 0, reused 1; provider call explicitly blocked to verify no network use.
- Full safe evidence: [phase3-embedding-smoke.json](phase3-embedding-smoke.json).

This verifies API shape and persistence, not retrieval relevance or generation quality. The English synthetic document was not backfilled; no need to spend on a larger live batch for this acceptance.

Phase 2 releases/active version/schema/gates/evidence/generation config and /chat remain unchanged. No commit/push. Stop before query embedding or retrieval.
