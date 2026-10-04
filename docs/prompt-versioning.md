# Phase 2.9 — Immutable prompt releases

The production prefix at the end of Phase 2.8 is frozen as `analytics-v1` in `backend/prompt_analytics_v1.py`. It includes the exact system/contract text, serialized JSON schema, example user messages and serialized example answers. Runtime construction does not regenerate published text from the current Pydantic schema or mutable example builder.

`backend/prompt_registry.py` contains a read-only release map, frozen release records, expected SHA-256 checksums and the deployment-owned `ACTIVE_PROMPT_VERSION`. `backend/prompts.py` selects the active release and returns fresh message dictionaries plus the final user message. The business route contains no version name. The compatibility constants/example helpers support older diagnostic scripts only.

## Publication and rollback

1. Never edit a published release file or replace its registry entry/checksum. For changed instructions, schema text, examples or formatting, add a new release module and a new registry entry with its checksum.
2. Verify the new release and output-schema compatibility, then change only the server-owned active selector and redeploy. There is no client parameter, environment prompt override, remote configuration or runtime management endpoint.
3. To roll back, select a retained release and redeploy. analytics-v1 and analytics-v2 are published; v2 is active, and retained v1 selection is tested. v1 retains its historical blocked evaluation and should not be mistaken for a quality-approved fallback. Keep the output validator/API compatible with a selected release; rollback across a breaking output schema also requires rolling back compatible application code.

Checksums reject accidental in-place content drift before an upstream call (503 / llm_invalid_prompt); immutable tuples, frozen records and a read-only map prevent normal runtime mutation. Source-control review and tests enforce the publication policy. This is not tamper-proof storage: a maintainer who deliberately changes source and its checksum can bypass it.

## Metadata and hash convention

Successful /chat responses, attempt/request logs (including provider failures), and new eval reports/records include readable `prompt_version` and `prompt_sha256`. Configuration errors before a valid prompt exists return a safe configuration error without inventing a valid prompt identity. Messages are assembled once per request and reused across retries.

The hash preserves the earlier eval convention: SHA-256 of UTF-8 `json.dumps(messages, sort_keys=True)` with the final user message replaced by an empty user message. This identifies the fixed prefix, not the private user input. analytics-v1 hash:

`bc480e10f14ae9d6158a70014eb4aef2b2ee0c00137f655e8cb05955a349bfb8`

Eval-only altered prefixes are labelled `unpublished` with their actual hash; they never masquerade as analytics-v1. Historical evidence is not retroactively rewritten. No prompt/user text is added to runtime logs.

## Verification

57 offline tests pass. The release's full message prefix and digest match the saved Phase 2.8 production baseline exactly. Tests cover tampering rejection, immutable registry/records, selection/rollback, user version override rejection, retry metadata consistency, failed-provider logging, and unpublished eval candidates, plus existing uncertainty/injection/structured-output checks. Docker rebuild and health checks verify release packaging. No new paid LLM call is required for this byte-preserving refactor; prior live evals are historical evidence, not newly rerun results.

Stop before Phase 2.10; no new regression framework or prompt UI/database is introduced.

## Phase 2.12 candidate verification and publication

`CANDIDATES` is a separate immutable map used only by the eval CLI. Run the unchanged suite with `python -m eval.run_regression run --candidate analytics-v2 --output <new-evidence-path>`, then bind explicit semantic decisions with the existing review command. Pending review exits nonzero. Candidate collection never changes the active server selector. Production selection rejects a candidate absent from `RELEASES`. Candidate revisions keep their prefix snapshots and distinct SHA-256 evidence; published v1 remains untouched.

The original two-round legacy gate remains historical. Publication was subsequently authorized using separately pre-registered phase2-release-gate-v2 (85% provisional quality threshold, all hard invariants required, no core or repeated severe-quality blockers). Three complete independent rounds passed. analytics-v2 moved unchanged from CANDIDATES into RELEASES, and active selection now uses v2. CANDIDATES is empty. See [final acceptance](phase2-acceptance.md).
