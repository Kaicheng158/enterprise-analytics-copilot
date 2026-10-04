# Phase 3.6 — Deterministic context builder

Pure local ContextBuilder consumes the actual ranked result dictionaries from Phase 3.5. No database/API calls, file loading, role-message construction or generation occurs in the builder. /chat and all published prompts/config remain unchanged. 3.5 was committed/pushed as b864f10 before development; 3.6 remains uncommitted.

## Format and ordering

Serialize a JSON data envelope with trust=untrusted_evidence and sources. Each source block carries source_label, document_id, chunk_id, original retrieval rank/distance, full source_metadata, and one or more exact text segments with half-open source character offsets. This is not a citation response schema. No system/developer role objects are produced; malicious text is escaped as JSON string data and never executed or interpreted by the builder. JSON framing reduces delimiter ambiguity, but does not by itself solve model prompt injection. A future generation layer must still enforce trusted instructions and treat every source field as untrusted.

Order by rank, distance, document ID, chunk ID, then canonical input as a deterministic final tie-break. Labels are S- plus 16 hex characters derived from document ID, chunk ID, revision and source URI; they are stable across rank/budget changes. Detected label/identity conflicts fail rather than silently alias. Inputs are deep-copied, not mutated. Required provenance/offsets and finite ranks/distances are validated.

## Conservative deduplication

- Exact full-text duplicates can share an included source; every omitted occurrence retains all its provenance and duplicate_of label in the audit sidecar.
- For the same document/revision/source, remove only source-coordinate overlaps whose actual text is exactly equal to already included segments.
- Unmatched overlap text and all unique spans are preserved; different source revisions do not undergo coordinate-based overlap removal. No fuzzy semantic deduplication.
- Every removed interval records its coordinates and covering label. Partial overlap may leave multiple segments; source offsets allow reconstruction without invented joins.
- Original chunk content is never edited in place or fact-corrected. Source boundaries may fall inside words because the original character chunks can do so. Preserve those exact spans instead of repairing language or fabricating continuity.

The audit includes every input's metadata and disposition (included, duplicate, overlap_covered, omitted_budget). It is local traceability data, not an additional context automatically sent to an LLM. Future citation work must deliberately map duplicate aliases if needed.

## Versioned budget

ContextConfig: evidence-json-char-v1, max_chars default 8000; supported 0–1000000 Unicode codepoints. Budget counts the entire final compact serialized JSON, including labels, metadata, escaped characters and framing. It is NOT a token budget or a model-context-window guarantee. No tokenizer dependency or token estimate is introduced. Audit sidecar and the structured mirror of blocks are excluded; only the context string is the budgeted payload.

Include full post-dedupe blocks in rank order. If the next block does not fit, stop; report that block and subsequent inputs as omitted_budget rather than silently dropping facts or skipping to less relevant smaller chunks. No budget-driven substring truncation. Empty input or insufficient budget for the first block yields context="". Labels are not renumbered. A future generation phase should add model-specific token accounting and reserve output tokens before any request.

## Verification

120 offline tests and all 31 database tests passed. New tests cover multiple/empty inputs, duplicate provenance, verified and conflicting overlap, split/full overlap, version isolation, ordering, budget exact/under/zero boundaries, labels, Unicode/combining characters, malicious role-like strings, metadata consistency and no input mutation. Fake provider/database tests remain separate from this pure transformation.

Smoke input is the unchanged saved Phase 3.5 synthetic result; no new retrieval, embedding or LLM call. Budget=3000; used=2059 codepoints. Two blocks included:

| Label | Retrieval rank | Original span | Included span | Distance |
|---|---:|---|---|---:|
| S-8dd1ffdf5fcb34b9 | 1 | [232,643) | [232,643) | 0.4312047111095042 |
| S-657a70590b1f1b58 | 2 | [0,312) | [0,232) | 0.6224643825514311 |

The 80 characters at [232,312) are represented by the first block and explicitly removed from the second; the union of included spans still covers [0,643) with no unique source characters lost. No budget omissions.

[Exact serialized context](phase3-context-smoke.txt) and [full structured result/audit](phase3-context-smoke.json) are saved. Retained source text and original retrieval evidence were not modified.

## Phase 3.7 proposal (not implemented)

Design an independently versioned server-owned RAG prompt and explicit grounded answer/abstention rules without editing analytics-v1/v2. Define source-label validation separately from model assertions; reject nonexistent sources and unsupported claims. Add model-specific input/output token budgeting, offline mocked generation tests and synthetic grounding/injection tests before any authorized live run. Decide any RAG endpoint or response contract separately; do not silently alter current /chat. No ANN work is needed for that step.
