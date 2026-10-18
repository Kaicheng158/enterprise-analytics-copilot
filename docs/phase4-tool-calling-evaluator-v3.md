# Phase 4.5 Tool Calling Evaluator v3

## Scope

Evaluator v3 is an independent, frozen candidate for future Tool Calling release
evaluation. It changes numeric-claim parsing only. The DeepSeek prompt, tools,
source-exposure rules, 16 cases, hard invariants, quality dimensions, 85% case and
round thresholds, and three-run release policy remain unchanged.

Evaluator v1/v2 suites, gates, SHA values, raw evidence, decisions, and historical
verdicts remain immutable. The v2 gate verdict remains `FAIL`.

## Confirmed root cause

The diagnosis used the original `raw.json` answers and v2 parsing records from all
three live rounds. The three repeated failures had correct tool selection, correct
tool output, and exact business values in the final answer:

- `selection/postgres_sales_summary`: total `300.00`, 2 orders, AOV `150.00`;
- `selection/in_memory_sales_summary`: total `400.00`, 4 orders, AOV `100.00`;
- `grounding/tool_result`: total `300.00`, 2 orders, AOV `150.00`.

The answers used Markdown such as `**Total sales:** **$300.00 USD**`. Evaluator v2
applied its typed field expressions directly to the Markdown source. Formatting
markers between the field label, separator, and amount prevented a typed match.
The fallback currency parser still classified the amounts as supported
`unclassified_currency_amount` values, so grounding passed, while completion failed
because typed `total_sales`, `order_count`, and AOV claims appeared absent.

## v3 parsing contract

Before classification, v3 removes formatting-only Markdown markers and normalizes
line endings/non-breaking spaces. It then recognizes documented equivalent labels:

- total sales: total sales, sales total, total revenue, revenue total, gross sales,
  sales, or revenue;
- AOV: average/avg order value or amount, AOV, average sales/revenue per order;
- order count: order count, number of orders, orders, or `<number> orders`;
- currency: `$300.00`, `US$300.00`, `USD 300.00`, or `300.00 USD`.

This is classification, not repair. Values are not changed or inferred. A typed
value must still match user/tool evidence. Missing required metrics, wrong totals,
wrong AOV/order count, and extra unsupported currency claims remain failures.
Periods, UUIDs, versions, latency, tokens, and telemetry cost remain separate from
business metrics.

## Frozen identities

- Evaluator suite SHA-256:
  `78035ed0e09499eb2dbb2fbe9c52cac56476db414c3446b44927891980b3df3f`
- Release gate SHA-256:
  `ce548ce798b5c2f3ad9fc2e14c483b2adc4d4c91b304375c4970dbf0ee49d497`
- Prompt remains `deepseek-tool-decision-v1`:
  `af1574d65979199045b6ab2cb8267fae23a3bb1887e48d1a53e51ee9ec682dfb`

## Historical replay

All 27 v2 live answers were replayed without provider calls and without modifying
source evidence:

| Evaluation | PASS | FAIL |
|---|---:|---:|
| Original v2 verdict | 18 | 9 |
| Diagnostic v3 replay | 27 | 0 |

The nine changes are exactly the three repeated Markdown cases across three rounds.
The other 18 verdicts are unchanged. Every v3 replay record is explicitly
`historical_diagnostic_replay` and `release_eligible=false`; this replay does not
replace the v2 verdict or satisfy the v3 release gate.

## Verification

- 269 offline tests pass.
- 43 isolated database tests pass.
- Targeted tests reject wrong totals, wrong AOV, wrong order count, missing required
  metrics, and an additional fabricated amount.
- The v3 protection manifest verifies 74 v1/v2 artifacts by SHA-256.
- No provider call was made and no prompt was changed.

## Remaining risks

The parser is intentionally bounded and may not recognize future wording, locale,
currency, or table formats outside the documented contract. Regex-based semantic
classification also remains less robust than a future typed final-response contract.
Expanding aliases requires a new version and negative tests to avoid overmatching.

## Next live acceptance plan

Create a v3-specific preflight and runner bound to the frozen v3 suite/gate and the
existing server-owned case-to-profile exposure policy. Before paid execution, verify
offline/database tests, default-off feature flag, no public Tool API, evidence output
paths, redaction, stop controls, and exact runtime hashes. Then run three independent
9-case DeepSeek Flash rounds with temperature 0.2, thinking disabled, 512 output
tokens, at most two provider interactions, one tool execution, zero retries, and a
total USD 0.02 stop budget. Fresh suite-bound v3 evidence is required; historical
replay cannot be promoted into release evidence.
