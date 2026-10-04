# Phase 3.3 — Local document ingestion and deterministic chunking

## Scope and execution

Operator-only Text/Markdown ingestion, not a public upload endpoint. The HTTP application, analytics-v2, Phase 2 schemas/gates/evidence/config and Docker API image are unchanged. No embedding calls, embedding rows, retrieval or generation. Run migrations explicitly before ingestion:

```sh
analytics-agent-env/bin/python -m scripts.migrate
analytics-agent-env/bin/python -m scripts.ingest_local --root data/synthetic/rag --namespace phase33-synthetic --tenant synthetic-phase33 metrics.md
```

Root, namespace and tenant are trusted operator inputs, not client-provided authorization. Keep namespace-to-root mapping stable and never reuse one namespace for unrelated directories. This CLI does not implement authentication, RLS or a general filesystem sandbox. Runtime Docker packaging remains unchanged; execute ingestion with the local project virtual environment. Requirements are unchanged.

## Source handling

Only .txt/.md/.markdown relative paths below the explicit root are accepted. Reject absolute/traversal/hidden paths, symlinks at root or below it, unsupported extensions, nonregular files, missing/unreadable files, NUL, invalid UTF-8 and files above 1 MiB. Directory-FD traversal with O_NOFOLLOW avoids symlink check/open races; O_NONBLOCK prevents FIFO reads from hanging. Compare size/mtime/ctime before and after bounded reading to reject observed concurrent edits. Operators must use a trusted root and parent path; this is not a guarantee against a malicious filesystem administrator or deliberately preserved timestamps.

Decode UTF-8 strictly, accepting/removing a leading UTF-8 BOM. Preserve Unicode and original newline characters without NFC/NFKC normalization. Empty/whitespace-only files fail safely before persistence and do not invalidate an already-current revision. Fixed error codes avoid exposing source text or host paths.

## Algorithm and versioned config

structure-char-v1 defaults: max_chars=600, overlap_chars=80. These are Unicode codepoints, NOT tokens, bytes or grapheme clusters. CLI overrides are validated and included in canonical config SHA-256. Changing splitter semantics requires a new algorithm version; no silent reinterpretation of old snapshots.

Greedily choose the last paragraph/ATX-heading/fenced-block-start boundary within the size budget that advances beyond the preceding chunk. Keep headings with following body when a prior boundary fits; blank lines inside fenced blocks do not count as paragraph boundaries. If no suitable boundary fits (e.g. long paragraph/code), hard-split at the character limit. Adjacent chunks have exact configured overlap. Small chunks may be under the limit; this is intentionally not a full Markdown AST parser (no table/list/Setext semantic parser). Oversized structures and grapheme sequences may span chunks, but valid Unicode codepoints are never cut into invalid bytes. No text is discarded: offset coverage reaches the exact source-text length.

Metadata includes half-open char_start/char_end in decoded text, 1-based line_start/line_end (LF counting, including CRLF), section_at_start, config hash and ordinal. Overlap can start inside the previous section, so section_at_start is a location aid, not a unique semantic label for the entire chunk. locator is chars:start:end. Source-relative URI is local://namespace/encoded-path; absolute host paths are not stored. Document metadata retains raw-byte and decoded-text SHA-256, source bytes, loader version, relative path and complete config. Chunk hashes cover exact UTF-8-encoded chunk text.

## Identity, revisions and transactions

Document ID = UUIDv5 of canonical [tenant, source URI]. Revision = SHA-256 of loader version, raw-byte content hash and complete config hash. Chunk ID = UUIDv5 of document ID, revision, ordinal, offsets and content hash. Identical bytes/config/source/tenant give identical identities; a BOM/newline-byte-only change also creates a source revision by design.

Ingestion loads one file snapshot, then serializes same-document writes with a transaction advisory lock. Insert document + all chunks + ready/current status atomically; errors roll back everything. The current selector points to the last successfully ingested snapshot, not an automatic filesystem watcher. A newer on-disk change after loading requires another ingest.

Repeated ingestion checks the stored revision and exact chunk data, reuses it without duplicate rows, and rejects detected inconsistencies instead of silently repairing. Source/config changes keep the stable document ID but create a new immutable snapshot and atomically clear the previous current flag. Reverting to known bytes/config reactivates the existing revision without duplication. Old chunks are retained for audit; future retrieval must explicitly select current/ready revisions. Storage does not enforce immutability against direct privileged SQL; this is a pipeline policy.

003_ingestion.sql adds is_current and a partial unique index allowing only one current revision per tenant/document, plus a unique tenant/source/revision index. Existing parent FK/chunk ordinal uniqueness already provide parent lookup indexes. Identity updates remain NO ACTION while children reference them; delete cascades document → chunks → any future vectors. This phase does not write vectors. Tenant FKs are integrity constraints, not read authorization.

## Verification

98 offline tests and 10 database tests pass. Coverage includes English, Chinese/emoji/combining Unicode/BOM, empty/invalid/oversized text, read failures/observed edits, symlinks/FIFO/path escape, long paragraphs, code fences, exact overlap/coverage, repeat/config/source-change/reversion, concurrent ingestion, integrity mismatch, failure rollback, current uniqueness and delete/update constraints. Database tests use independent temporary databases which are removed.

Real CLI smoke imported two clearly labelled synthetic files into tenant synthetic-phase33:

| Source | Ready/current document revisions | Chunks | Decoded source chars |
|---|---:|---:|---:|
| metrics.md | 1 | 3 | 922 |
| unicode.txt | 1 | 1 | 197 |

metrics.md spans: [0,312), [232,643), [563,922); unicode.txt: [0,197). Repeat imports reused both revisions. documents has 2 synthetic rows, document_chunks has 4, chunk_embeddings stays empty. Full saved rows/text/metadata: [smoke evidence](phase3-ingestion-smoke.json). These fixtures are not production business data. Health/database health both return 200; no paid chat or embedding call was made.

The accepted 3.1/3.2 state was pushed as 6280465 before work began. 3.3 remains uncommitted by request. A private pre-migration backup is retained at backups/phase33-before.dump and excluded from Git.

## Next: Phase 3.4 proposal

Explicitly choose embedding provider/model, exact revision, dimensions, query/document modes, data policy and budget first. Implement the existing provider abstraction with offline test doubles, finite/count/dimension validation and bounded retry/telemetry. Persist vectors per selected profile and immutable chunk identity; reuse matching embeddings and never mix vector spaces. Start paid tests only after explicit approval; no retrieval or generation in that step.
