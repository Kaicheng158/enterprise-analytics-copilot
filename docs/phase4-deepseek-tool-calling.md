# Phase 4.4 — DeepSeek Tool Calling adapter

Phase 4.4 connects the internal Phase 4.3 orchestration contract to DeepSeek Chat Completions Tool Calls. It remains disabled by default, has no public API route, and does not change `/chat`, `/rag/chat`, analytics prompts, RAG prompts, database schema or historical evaluation evidence.

Protocol references:

- [DeepSeek Tool Calls guide](https://api-docs.deepseek.com/guides/tool_calls/)
- [DeepSeek Chat Completions API](https://api-docs.deepseek.com/api/create-chat-completion/)

## Adapter flow

`DeepSeekToolDecisionProvider` converts authorized server registry schemas to official `tools=[{"type":"function",...}]` definitions. Its first non-streaming request uses `thinking=disabled` and either server-selected `auto`/`required` choice or an explicit server-forced function. A forced function is accepted only when it appears in the current principal's authorized schemas.

When DeepSeek returns `finish_reason=tool_calls`, the adapter requires exactly one function call, validates the call ID/name, bounds and parses the JSON argument object, and maps it to the provider-independent `ToolCallDecision`. The existing orchestrator and executor then perform authorization plus strict registered-tool input/output validation.

For the second request, the adapter sends the validated assistant `tool_calls` message followed by a `role=tool` message with the exact provider `tool_call_id`. Its content is the server-created `UntrustedToolEvidence` envelope. The original tool definitions remain present for protocol continuity, but `tool_choice=none` prevents another request. The orchestrator separately rejects any returned second tool call. There are at most two HTTP calls and no adapter retry.

## Failure and security behavior

- Invalid or non-object arguments, malformed responses, response-size violations and incomplete/truncated completions fail closed.
- Multiple native `tool_calls` fail before any tool execution.
- Unknown tools, unauthorized tools and valid-JSON but invalid registered-tool arguments are rejected by the existing registry/executor boundary.
- Authentication, balance, access, rate-limit, timeout, network and upstream failures map to fixed safe codes. Upstream bodies, headers, exception details and keys are not returned or logged.
- Feature flag `TOOL_CALLING_ENABLED` defaults to `false`. No current route loads this runtime, so setting the flag alone does not create public access.
- Tool output stays in the `tool` role and retains `authority=untrusted_data`; content inside it cannot become a System or Developer instruction through the adapter.

## Telemetry

Every model request logs only safe metadata: internal request ID, call index/phase, provider/model, finish reason, token usage, latency, fixed error code, zero retry count, prompt version/SHA, generation settings, pricing version/tier and estimated cost. It excludes user text, tool arguments, tool output, final answer, API key and sensitive headers.

The adapter exposes an internal run summary with total API calls, input/output/total tokens, summed request latency and cost. Cost is marked complete only when cache hit/miss counts allow every request to be priced.

## Verification

- 210 offline tests pass, including Fake Provider and mocked DeepSeek protocol/error coverage.
- 41 database tests pass, including Mock DeepSeek API → orchestrator → PostgreSQL tool → Mock DeepSeek API.
- One real, non-sensitive DeepSeek smoke test completed with two API requests and zero retry. The server forced the authorized `get_postgres_sales_summary` function for `2026-Q2` / `north`; the database result was 2 orders, USD 300.00 total and USD 150.00 AOV. The model returned a grounded final answer.
- The live run used 899 input tokens, 163 output tokens, 1,062 total tokens, 2,790 ms summed model latency, and estimated USD 0.00023265. The default flag was false before and after the process-local test.

The sanitized record is [phase4-tool-calling-smoke.json](phase4-tool-calling-smoke.json). One successful smoke run establishes protocol compatibility only; it is not a release gate or repeated-run stability evidence.

## Phase 4.5 preparation

Before any endpoint is enabled, Phase 4.5 should freeze a tool-calling evaluation suite and release gate covering selection accuracy, authorization, tool-output injection, malformed and multiple calls, provider failures, repeat stability, cost limits and human approval rules. A separate tool-enabled API can then be added behind explicit local release configuration without changing `/chat` or `/rag/chat`.
