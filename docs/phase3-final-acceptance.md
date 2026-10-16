# Phase 3.8–3.11 local acceptance — 2026-10-15

## Outcome

Completed a single authorized 15-case v5 live regression, froze and applied an independent release gate, integrated a default-disabled RAG endpoint, then enabled it locally only after gate PASS and verified Docker with one additional synthetic live smoke. No further prompt tuning, paid regression repetitions, commit or push.

- Local/GitHub main before work: `62e81fe771f6d2d521f7cfa106bd369644ee10c9`, synchronized. Prior v4 files remain uncommitted and preserved.
- V5 SHA: `d81ef6b9862fbac388a1a79c48898b68fac0fea1c307241a95f96c243581afb3`.
- Single added rule: conditional conclusions require all necessary premises; possible existence alone cannot imply unobserved quantities/direction/relationships. No case-specific metrics or answers.
- V1–v4 content/SHA/evidence, frozen suite/rubric, analytics-v2, original main.py, retrieval/embedding/context/validator/generation config unchanged. Existing freeze checks and pre-run historical hash snapshot verified.
- Candidate manifest retains its pre-run status as a snapshot. `backend/rag/local_release.json` is the separate local acceptance receipt; no claim of public production release.

## Tests and live evidence

- 174 offline tests passed, including fail-closed gate, HTTP validation/error mapping, server-owned scope, receipt tamper detection, actual context/generator composition and body-free telemetry.
- 31 isolated database tests passed. Existing database data/schema untouched.
- V5 live:15/15 valid answers; schema/grounding/citations/security100%; core blockers0. Retrieval hit@3 12/12 applicable,14 expected chunks,0 misses; three cases have no expected relevant source.
- Quality: completeness30/30, relevance26/30, clarity30/30;86/90=95.56%. Minor residual relevance issues: empty, indirect_injection, forged_roles, multi_source. Three limitations-field issues remain. No further tuning performed.
- V4 unsupported additional-region/higher-total conclusion absent. Deliverables explicitly proposes collecting segment/event data and comparing it. Empty/irrelevant context returns facts=[].
- Regression: embedding448 tokens, generation28224 input/2296 output, median generation1671ms, estimated USD0.002666048. Full per-case traces: `eval/rag/v5-live-001/`; manual assistant evidence-bound reviews: `v5-live-001-decisions.json` and `v5-live-001-reviewed.json`.
- Docker: default-disabled503 verified before enabling. Health/db/RAG200; OpenAPI RagResponse; valid source IDs/version/SHA; six client override attempts422; success telemetry includes generation/embedding tokens, source labels, latency and estimated total cost without query/body.
- One real smoke used isolated synthetic40/50 evidence, returned+10/+25%,limitations=[]; temporary database removed and original database restored. Full result/log metadata: `phase3-local-acceptance.json`.

## Independent gate

`eval/rag/release-gate-v1.json` was written and SHA-pinned before v5 live calls. Existing historical policy remains unchanged.

- Every applicable schema,grounding,citation,security invariant100%; no core blockers; full15 cases and evidence hashes required.
- Quality is separately reported and must meet provisional85%; no0/unknown dimension. Higher averages cannot compensate hard/core failure.
- Offline/database tests must pass, candidate/suite/runtime must match.
- Stability separately reported: one complete real run only. Repeated-run stability not established. This is local engineering acceptance, not production certification or proof of injection immunity.
- Result: `eval/rag/v5-release-gate-result.json`, PASS. Policy SHA `ba0f11d6f4cba962f97055dcc9201c48cbaffc6d20d3cb774af04e8c58427441`.

## Local endpoint

POST `http://127.0.0.1:8765/rag/chat`, body `{"query":"..."}`. Response remains rag-response-v1; original /chat and analytics-v2 unchanged. No client scope/model/context/prompt override.

Base compose remains default disabled. Explicit local startup:

```sh
docker compose -f compose.yaml -f compose.rag-local.yaml up -d --build
```

`compose.rag-local.yaml` pins a passing receipt, enables loopback access, and sets tenant `local-rag` with an empty document allowlist. This deliberately grants no knowledge-base access. Operators must explicitly configure tenant and authorized document IDs for approved ingested data. No public deployment or authentication/multiuser authorization system is implied. A flag alone cannot bypass the required receipt. Use base compose without the local override to disable RAG.

Embedding/retrieval and generation reuse the existing components; the isolated generator module selects v5 without mutating historical module globals. The new `backend/rag_app.py` entrypoint adds the RAG router. Original `backend/main.py` bytes are retained. No new Python dependency.

## Remaining work / limits

No blocking failure for this local acceptance. User review and eventual commit/push remain pending. Before any public/multiuser release, separate authentication/authorization and deployment approval are required. Broader corpus relevance calibration and repeated-run stability are future work, not silently claimed complete. Residual prompt quality issues are documented and accepted by the preregistered gate, not fixed through another prompt iteration.
