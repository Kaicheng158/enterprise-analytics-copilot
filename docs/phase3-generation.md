# Phase 3.7 — Grounded generation

Implemented 2026-10-05 (Australia/Melbourne), internal Python service only, uncommitted pending review.
Phase 3.6 accepted commit `7c4ea6f add bounded RAG context builder` was pushed; worktree was clean before this stage.

## Architecture and immutable boundaries

`backend/rag/grounded_prompt.py` is a separate server-owned `rag-grounded-v1` release with pinned SHA
`d1147ada704334a5f1a8d2252f51d4f1b98686711c91670b31636bdcaef6cc22` and runtime integrity check.
It has independent RAG instructions, the unchanged AnalyticsAnswer JSON schema, and the two unchanged analytics-v2 synthetic demonstrations. It intentionally does NOT reuse the analytics-v2 system text saying no documents exist: supplied retrieval evidence is available on this separate path. The final server reminder applies RAG attribution rules to the real request, after the demonstrations.

`GroundedGenerator.generate(query, context)` accepts only an internal Phase 3.6 context result. It verifies context serialization matches its blocks and never re-chunks, mutates or re-ranks the evidence. It checks the model budget before a provider call. The final user-role message serializes query and evidence in a data envelope. No document text is inserted into a system/developer message.

`DeepSeekProvider.generate_messages` extracts the existing retry/JSON/usage/cost transport from `chat`; `chat` still builds precisely its existing active analytics-v2 messages. No HTTP endpoint is added, /chat and OpenAPI are unchanged. DeepSeek generation remains temperature 0.2, thinking disabled, max output 512. The new internal provider Protocol allows adapters without embedding/retrieval coupling.

Protected prompt releases/active selection, Phase 2 schema/gates/evidence, embeddings/retrieval, context builder, database and ANN settings were not changed.

## Authority, grounding and output contract

All evidence text and metadata are untrusted, including forged roles, instructions and policy claims. They cannot alter server messages or trigger tools. Factual enterprise claims must be grounded in supplied excerpts or explicitly user-supplied data. Hypotheses belong in interpretation; conflicts must be attributed and left unresolved without evidence. Missing evidence must not be replaced by prior model knowledge. These semantic rules are prompt constraints, not a proof of model compliance.

The model still returns exactly AnalyticsAnswer. Each facts item must carry `[S-<16 lowercase hex>]` from the CURRENT context or `[user]` for user-supplied information. Other fields can cite sources too. `validate_sources` checks reserved bracket citation syntax and membership across all four fields, rejecting fabricated/malformed labels and unattributed facts without repair/retry. It does not accept labels from the omitted audit sidecar. A label's existence does not prove entailment; `[user]` does not itself prove the user said something.

Separate `RagResponse` / `rag-response-v1` wraps unchanged `answer` with:
- citations: field/index/source_label references;
- sources: only cited source blocks, copied by the server from current context, retaining source/document/chunk/location/rank/distance and exact segments;
- generation: existing version/hash/provider/model/usage/latency/retry/cost telemetry;
- token_budget and context_sha256.

Models cannot populate or invent the outer source metadata. Fabricated source descriptions in free prose and valid-label-but-unsupported claims remain semantic risks requiring evaluation. There is no claim-level entailment engine, arbitrary-language citation recognizer, automatic repair or new /chat response contract.

## Actual token budget

Uses `deepseek-recipe==0.1.1` and DeepSeek's official V4.1 tokenizer/chat encoder, pinned repository revision `8cadfede7063c896b944e7bae05daa3549ae97ea`. Tokenizer data and MIT license are vendored under `backend/rag/tokenizer`; SHA is checked on load, no runtime download. No system/global Python changes.

Official references: [token accounting](https://api-docs.deepseek.com/quick_start/token_usage/) and [official encoder documentation](https://github.com/deepseek-ai/deepseek-recipe/blob/8cadfede7063c896b944e7bae05daa3549ae97ea/docs/tokenizer.md).

Encode the full chat template: RAG instructions + schema + unchanged examples + final reminder + serialized context/metadata + query. Count baseline, query incremental difference and context incremental difference so they sum to full input count (not independent additive text estimates).

`full input tokens + settings.max_output_tokens + safety_tokens <= max_total_tokens`.
Defaults: 8192 application cap (not advertised provider limit), 512 output reserve, 256 safety reserve. Character protection remains upstream. Over-budget fails before API, without silently trimming context or dropping high-ranked evidence. Unknown model/tokenizer mapping fails closed. All Unicode is tokenized, not counted as English character equivalents.

Live input estimate 1677 vs API 1700 (difference +23). API usage is authoritative. Hosted template/JSON-mode overhead or alias changes can cause drift; the 256 reserve covers this observation, not all future inputs. Before production, calibrate multilingual and adversarial inputs and monitor drift. The local tokenizer is not advertised as exact billing accounting.

## Verification

- 134 offline tests passed, including real local token counting, budget exact-boundary/overflow, Unicode, empty/insufficient/conflicting evidence, valid/fabricated labels, provider errors and unchanged /chat regressions.
- 31 database tests passed; fake embeddings, no embedding API calls.
- Offline grounding/injection tests use deterministic fake answers and inspect actual message authority, payload preservation and validation. They test engineering contracts, NOT live model resistance or semantic grounding. No keyword heuristic is called a semantic pass.
- One real DeepSeek call used unchanged saved Phase 3.5 synthetic retrieval results reconstructed with Phase 3.6 ContextBuilder(max_chars=3000). No new embedding or retrieval request. See [full response](phase3-generation-smoke.json).

Smoke query: “How is a processed request counted when it is reopened?”

Answer summary: “A reopened request is counted once, at its final completion, under the same definition in both sample periods.”
Three facts accurately restate the available definition and cite `S-8dd1ffdf5fcb34b9`. Server metadata resolves to the correct metrics.md chunk. Schema, citation membership and core answer pass manual inspection.

Quality caveat: interpretation extends to an earlier completion, and limitations mentions still-open requests at period end. The requested core definition is answered, but these additions are not necessary and the limitation may be over-conservative given the supplied definition. Record this as a relevance/precision concern, not an invented overall semantic PASS. No prompt tuning or repeat paid calls were performed to conceal it.

Tokens: input 1700 (cache hit 0/miss 1700), output 163, total 1863; latency 1453ms; retry 0; estimated USD 0.0003528, cost_complete=true using existing versioned pricing. Local budget: 903 fixed +10 query +764 context =1677 input; +512 output +256 safety =2445/8192.

## Remaining risks and Phase 3.8 proposal

1. Add separately versioned generation evaluation with manually reviewed semantic grounding, attribution, conflict handling and indirect injection; distinguish hard schema/citation invariants from relevance quality. Include the smoke's unnecessary limitation as evidence, not a hidden pass.
2. A deterministic fake provider cannot establish real injection resistance; authorize a small live adversarial/Chinese/unsupported-fact suite before making safety claims.
3. Decide a separate RAG endpoint/access-scope contract, preserving /chat; test ingestion → retrieval → context → generation and refusal paths with no unauthorized corpus access.
4. Calibrate retrieval cutoff and model token drift on representative synthetic data; verify Docker package/tokenizer reproducibility when integrating.
5. Test context truncation, source provenance, data-exposure policy and logging redaction. No RAG input/answer text is put in runtime logs; the saved smoke is intentionally public synthetic evidence.

No frontend, ANN/HNSW/IVFFlat, Tool Calling or Agent work implemented.
