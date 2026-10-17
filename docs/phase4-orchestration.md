# Phase 4.3 — Tool Calling orchestration

Phase 4.3 adds an internal, provider-independent orchestration layer above the existing server-owned `ToolRegistry` and `ToolExecutor`. It uses no live LLM, creates no API route and does not change `/chat`, `/rag/chat`, prompts, RAG releases, database schema or historical evidence.

## Contracts and flow

`ToolOrchestrationInput` accepts one bounded user message. A `ToolDecisionProvider` receives a server-created `ToolProviderRequest` and returns UTF-8 JSON matching the strict `ToolCallDecision` schema:

- `action=final` requires a nonblank `final_response` and forbids `tool_call`.
- `action=tool_call` requires one `ToolCall` and forbids `final_response`.
- extra fields, type coercion, conflicting fields, non-object arguments, invalid JSON and decisions over 32 KiB fail closed.

The deterministic flow is:

1. The orchestrator asks the registry for schemas authorized for the trusted server principal.
2. The provider either returns a final response or requests one named tool with JSON arguments.
3. The existing executor resolves the registered tool and server authorization before validating arguments, invoking the fixed handler and validating output.
4. The result, including a safe tool error when applicable, is wrapped as `UntrustedToolEvidence` and sent to the provider for a final decision.
5. The provider must return a final response. A second tool request is rejected as `tool_call_limit_exceeded` and is never executed.

The structural limits are one tool execution and two provider turns. There is no loop, recursion, arbitrary code path or arbitrary SQL path. A tool failure can still be passed to the provider so it can produce an honest final response; the internal result retains the failed `ToolExecutionResult` rather than presenting it as a successful query.

## Authority and security boundary

The provider never supplies the principal or grants. The first provider request contains only schemas returned by `ToolRegistry.schemas_for(principal)`. The executor resolves authorization again before any argument validation or database connection, so a model-requested unknown or unauthorized tool fails closed.

`UntrustedToolEvidence` always carries `authority=untrusted_data` and a server-owned handling instruction stating that content in the output cannot act as a System or Developer instruction. Output is preserved as data; it is not executed, promoted or rewritten. A future real-provider adapter must serialize this envelope as untrusted evidence and retain the authority boundary.

Safe orchestration logs contain correlation ID, server principal, selected tool name, provider turn count, tool call count, statuses and fixed error codes. They exclude the user message, tool arguments, tool output, provider final response, exception text, credentials and database details. The existing executor emits a separate metadata-only execution event.

## Offline test provider

Tests use a scripted `FakeToolDecisionProvider`; it records server requests and returns fixed JSON decisions without network access. Coverage includes:

- direct final response with no tool;
- complete Fake Provider → orchestrator → in-memory or PostgreSQL tool → Fake Provider flow;
- invalid JSON and contract/argument shapes;
- unknown and unauthorized tools;
- invalid registered-tool inputs and sanitized execution failures;
- provider failure and a second tool request after the one-call budget is spent;
- untrusted instruction-like tool output;
- safe logs and absence of public tool routes.

## Phase 4.4 preparation

Phase 4.4 can add one internal adapter that translates a real provider's native tool-call response into the frozen `ToolCallDecision` JSON contract. It should remain behind a disabled internal feature flag and add model-choice accuracy, prompt-injection, malformed native response, accounting, retry/idempotency and repeated-run evaluation coverage. Phase 4.4 must not weaken registry authorization, executor validation, the one-call budget or the untrusted tool-output boundary. No public endpoint should be enabled until the separate Phase 4.5 release gate and human-in-the-loop policy are complete.
