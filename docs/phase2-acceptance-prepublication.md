# Phase 2.12 — Final verification: blocked

**Latest update:** scope/relevance candidate: schema 15/15 each round, semantic 14/15, 14/15, 15/15 at unchanged config. See [latest failures](phase2-scope-relevance-verification.md). Earlier semantic-consistency batch: [retained evidence](phase2-semantic-consistency-verification.md). Previous experiment: 13/15, 13/15, 11/15; [retained evidence](phase2-fixed-config-verification.md). The historical snapshot below describes the preceding r13 candidate, not the current SHA.

Phase 2 is **not accepted**. The active server-owned release remains **analytics-v1**, immutable, with its historical **14/15, release gate blocked** result. No v2 promotion or Docker v2 acceptance is claimed.

## Candidate and evidence

The current **analytics-v2 candidate** retains the complete v1 prefix and adds a server-owned current-request boundary after the demonstration exchanges, before the real user message. It reiterates existing fact/hypothesis, scope and output boundaries while requiring missing items to be absent from the current request and relevant to an unresolved question. No case-specific metrics or answers were added. It is eval-only in `CANDIDATES`; active selection only accepts published `RELEASES`.

Current candidate SHA-256: `7a11b97055be89595280eb3de5ba72ff1b14890b23c2f6f1b5bc6447ce1a1a22`.

All original case inputs, expected behaviors, failure conditions and review policy remain unchanged. Raw runs, evidence-bound semantic decisions, reviewed reports and candidate prefix snapshots are retained in `eval/`. r5 reuses the r4 prefix unchanged. Separate rounds are separate paid DeepSeek requests; a new candidate restarts the two-pass requirement.

| Round | Semantic passes | Gate | Prompt SHA prefix |
|---|---|---|---|
| r1 | 11/15 | blocked | `a42720ee27f7` |
| r2 | 12/15 | blocked | `01264cbac908` |
| r3 | 12/15 | blocked | `5240b9b5feea` |
| r4 | 15/15 | passed | `8c02c1fb1958` |
| r5 | 12/15 | blocked | `8c02c1fb1958` |
| r6 | 8/15 | blocked | `71d32f81f729` |
| r7 | 12/15 | blocked | `63fc7997630a` |
| r8 | 13/15 | blocked | `8de4ead06fac` |
| r9 | 13/15 | blocked | `17cfc60684be` |
| r10 | 12/15 | blocked | `58f08e6902d6` |
| r11 | 10/15 | blocked | `300e5c5e6a7a` |
| r12 | 11/15 | blocked | `756bae27eb2c` |
| r13 | 12/15 | blocked | `7a11b97055be` |

r4 passed once, but its independent same-SHA stability run r5 failed. No other candidate has passed twice. Latest r13 failures: an explicit causal request lacked the expected evidence request; supplied sales counts attracted an unrelated currency requirement; the limited-analysis response failed local structured-output validation. Invalid output was safely rejected without repair and was not marked a semantic pass. The invalid answer text is not captured by the existing adapter; its usage/cost remains unknown rather than zero.

## Verification and remaining work

- 69 offline deterministic tests passed. These are not live semantic-regression passes.
- v1 source and original suite/case files unchanged relative to Git HEAD.
- v2 candidate has an independent checksum; HTTP clients cannot activate or select it.
- Full live evidence includes provider/model/runtime config, prompt and suite hashes, verdict, usage/cache, latency and estimated cost where available. See `phase2-acceptance-status.json` for round summaries; raw reports retain per-case details.
- Publication, active switch and post-switch Docker /chat, logs, metadata, OpenAPI and client-boundary verification remain pending because the two-pass prerequisite is unmet. Historical v1 integration results remain in `phase2-integration.md`.
- No completion commit/push was made: the user explicitly conditioned it on all acceptance checks passing. Current work/evidence remains local for continued candidate work.

## Limits

Semantic review is by Codex, not independent human review. Repeated tuning against this same small suite risks overfitting; it is not an independent holdout evaluation. Even two future full passes would be limited evidence, not a guarantee of accuracy or security. Estimated costs are not bills and exclude unknown usage. No RAG, tools or Agent implementation was started.
