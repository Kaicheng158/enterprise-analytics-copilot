# Phase 2 final acceptance — released

analytics-v2 is published and server-owned active. analytics-v1 remains an immutable historical release with its original 14/15, blocked verdict; that historical gate was not cleared or rewritten.

## Release identity and evidence

- analytics-v2 SHA-256: `b04bd2ea98f0e007b00c7a76e4b6a7e7ef8cd28478f350589e81f1f7b52d3fd6`.
- Release uses exactly the accepted candidate prefix, with no prompt, schema, few-shot, suite, gate or generation-setting changes during publication.
- Fixed DeepSeek Flash settings: temperature 0.2, thinking disabled, max_output_tokens 512.
- Pre-registered phase2-release-gate-v2 threshold remains 85%, a provisional engineering threshold, not a universal standard.
- Three independent complete rounds: schema 15/15 and hard checks 91/91 each; zero core blockers and repeated severe-quality blockers; normalized quality 97.78%, 94.44%, 98.89%. All three PASS.
- Full per-case decisions, tokens, latency, estimated cost and hashes: [batch 002 acceptance](phase2-gate-v2-acceptance-002.md), `eval/gate-v2-acceptance-002/`.

## Publication verification

87 offline tests passed, including active v2 checksum, historical v1 rollback and the complete existing provider/error/retry/output/security tests. An older integration assertion that assumed v1's message count now checks the entire active release message sequence and final user role.

Docker rebuilt/restarted successfully. `/health` and `/health/db` return HTTP 200; database probe returns SELECT 1. A real synthetic `/chat` returns HTTP 200, locally validated AnalyticsAnswer, analytics-v2 and the accepted SHA. Correlated attempt/request logs contain consistent tokens, latency, estimated cost and fixed generation settings. OpenAPI component schemas and /chat response match local definitions. Twelve client override fields are rejected with 422, including prompt, version, examples, output contract, response format and generation configuration.

Machine-readable smoke evidence: [phase2-release-smoke.json](phase2-release-smoke.json). No secrets or real sensitive business data are included. The local .env and virtual environment remain ignored; Docker build uses its existing source allowlist.

## Preserved history and limitations

[Prepublication status](phase2-acceptance-prepublication.md) and `phase2-acceptance-status.json` are historical blocked snapshots, not current deployment status. All old gate policies, verdicts and evidence are retained unchanged. Batch reports describe their state at collection time and are not retroactively marked published.

Semantic scoring was evidence-bound Codex review, not independent human review. Round 2's indirect next step received quality deductions. Reusing this small suite during tuning risks overfitting; three passes do not guarantee unseen-input reliability or solve prompt injection. Costs are estimates, not invoices.

Phase 2 is complete. Stop here; no RAG, Tool Calling, Agent or LangGraph work is included.
