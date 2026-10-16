# Phase 4.1 — Tool Calling foundation

Phase 4.1 adds an internal, provider-independent execution boundary. It does not connect tools to an LLM and does not expose a public tool endpoint.

## Architecture

1. `ToolCall` accepts only a registered tool name and an argument object. Strict Pydantic models reject coercion and extra fields.
2. `ToolRegistry` holds immutable server-created definitions: name, version, description, strict input/output models and a Python handler. It exposes schemas only for tools granted to the server-owned principal.
3. `ToolExecutor` resolves authorization before parsing arguments or invoking a handler. It validates input, calls only the registered callable, validates output, and returns one strict success/error envelope.
4. Logs contain call/correlation IDs, principal, tool/version, status, fixed error code and latency. They exclude arguments, output, exception text and secrets.

The registry has no code-evaluation, shell, filesystem, HTTP, database or SQL tool. Supplying names such as `run_sql`, or adding `sql` to a valid tool input, fails closed. A future caller must construct `ToolExecutionContext` from authenticated server state; model/user content must never choose its principal or grants.

## First deterministic tool

`get_sales_summary` reads an in-process synthetic fixture (`synthetic-sales-v1`) containing integer-cent sales for Q1/Q2 and north/south. Its input selects only these enumerated periods and regions. Its output provides USD total, order count and average order value. Decimal arithmetic and explicit rounding make results reproducible; there is no network or database access.

Example internal call:

```python
from backend.tools import ToolCall, ToolExecutionContext, build_tool_executor

result = build_tool_executor().execute(
    ToolCall(name="get_sales_summary", arguments={"period": "2026-Q1", "region": "all"}),
    ToolExecutionContext(principal="analytics-copilot", correlation_id="local-demo-001"),
)
```

## Roadmap

- 4.2: provider-independent tool-call request/response parsing and a deterministic orchestration loop, with bounded call count and no automatic SQL.
- 4.3: connect the LLM to server-approved schemas behind an internal feature flag; add tool-choice, injection, malformed-call, retry/idempotency and accounting tests.
- 4.4: add narrowly scoped business data tools. SQL requires a separate read-only design, allowlisted data scope, parser/validator, statement timeout and audit policy before any execution path exists.
- 4.5: integrate a separate tool-enabled API only after an evaluation/release gate, Docker verification and human-in-the-loop policy. Existing `/chat` and `/rag/chat` remain stable contracts.

Phase 4.1 does not establish agent autonomy, safe arbitrary SQL, multi-tenant authorization, durable audit storage or exactly-once side effects.
