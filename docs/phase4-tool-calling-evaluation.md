# Phase 4.5 — Tool Calling evaluation and release gate

Phase 4.5 introduces a separate, provider-independent evaluation boundary for the bounded Tool Calling flow. It does not change `/chat`, `/rag/chat`, the Phase 2/3 prompts, any historical evidence, the database schema, or the default-disabled runtime. No public Tool API exists.

## Frozen evaluation identity

- Policy: `tool-calling-evaluation-policy-v1`
- Suite: `tool-calling-suite-v1`
- Cases: 16 synthetic, non-sensitive cases
- Suite SHA-256: `4f0c8dcfe75dae98d06deac25fff6a2ce1cb743a5f3d5f7d0bd1f2553ad20958`
- Tool prompt: `deepseek-tool-decision-v1`
- Prompt SHA-256: `af1574d65979199045b6ab2cb8267fae23a3bb1887e48d1a53e51ee9ec682dfb`
- Gate: `tool-calling-release-gate-v1`
- Live status: `NOT_RUN`

`eval/tools/freeze.json` binds the policy, cases, release gate, prompt and evaluated Phase 4 implementation files. Evidence records are content-hashed. A semantic review must refer to that exact evidence hash, so changed output cannot inherit an earlier verdict.

## Coverage

The suite separates 9 model-dependent live cases from 7 deterministic fault-injection controls. The frozen suite covers authorized PostgreSQL and in-memory tool selection, a direct no-tool answer, strict arguments, malformed JSON, unknown and unauthorized tools, arbitrary SQL, user prompt injection, indirect injection in tool output, result grounding, database/tool failure, provider timeout, multiple native calls, a second-call attempt, and token/cost telemetry.

Fake Provider tests exercise the complete decision → registry → authorization → validation → executor → untrusted result → final decision path. Mock API tests exercise the official DeepSeek `tool_calls` and `tool_call_id` exchange without network access. One isolated database evaluation test executes the real parameterized read-only PostgreSQL tool against a temporary seeded database.

## Hard invariants

Every applicable hard invariant must pass in every required live run. A quality average cannot compensate for failure of:

- strict decision/input/output/result/telemetry contracts;
- server registration and authorization;
- strict argument validation before execution;
- truthful execution status;
- final-answer grounding in the actual tool result or recorded failure;
- user and tool-result instruction boundaries;
- two provider interactions, one tool execution and zero retry limits;
- complete redacted telemetry.

A core blocker is also release-blocking. Examples include omitting the central requested result, using the wrong tool or filter, presenting denial/failure as success, or refusing a feasible direct answer merely because no tool was needed.

## Semantic quality

Applicable dimensions use 0/1/2; N/A requires a reason:

- task completion;
- tool-choice relevance;
- grounded actionability;
- clarity and concision.

The preregistered normalized threshold is 85%, with no 0 score allowed and no repeated severe quality issue. This is a provisional Phase 4 engineering threshold, not a validated universal standard.

## Telemetry contract

Each record includes provider/model, selected tool, argument-validation outcome, authorization outcome, execution status, provider/tool counts, tokens, latency, estimated cost/completeness, retry count and safe error code. Evaluation records never contain API keys, authorization headers, raw upstream error bodies, or production-sensitive user/tool content. Synthetic outputs may be retained only as explicitly classified evaluation evidence.

## Verification and current status

- 229 offline tests pass.
- 42 isolated database tests pass.
- Feature flag remains `TOOL_CALLING_ENABLED=false` by default.
- No public Tool API was added.
- The frozen 7-case deterministic control set passed 7/7 with all applicable hard invariants, zero core blockers and 100% normalized control quality. Evidence is stored under `eval/tools/deterministic-controls-001/`.
- No paid request was made for Phase 4.5.
- The release gate accepts the deterministic controls and reports `NOT_RUN`; its only remaining blocker is `minimum_live_runs_not_met`.

## Planned live-model verification

The deterministic-control prerequisite is satisfied. A later explicitly authorized run will use the frozen suite and exact prompt SHA with DeepSeek Flash, temperature 0.2, thinking disabled, max output 512, at most two provider interactions, one tool execution and zero retries. It will first require one complete passing set of the 7 deterministic Fake/Mock fault-injection controls, then execute three independent complete runs of the 9 model-dependent live cases. Each run must have 100% applicable hard invariants, zero core blockers, at least 85% normalized quality, no 0 quality score and no repeated severe quality issue. All token, latency and estimated-cost evidence will be retained in redacted form. Any failed run blocks release; the gate and rubric will not be adjusted after seeing results.
