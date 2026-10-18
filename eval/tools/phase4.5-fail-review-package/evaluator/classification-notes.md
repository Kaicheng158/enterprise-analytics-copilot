# Reviewer and error-classification rules

## Authoritative implementation snapshots

- `run_live.py` contains the live semantic reviewer, numeric-support parser, and evidence writer.
- `suite.py` loads and validates the frozen cases and policy.
- `release_gate.py` applies the frozen gate to deterministic and live evidence.
- Exact frozen rules and thresholds are in `../frozen/policy.json` and `../frozen/release-gate-v1.json`.

The source files in this package are byte-for-byte copies. They are provided for inspection and do not change the evaluator, rubric, gate, prompt, or historical verdict.

## Implemented classifications

The live reviewer in `run_live.py` evaluates each case across these hard invariants: schema validity, authorization, argument validity, execution truth, grounding, security, and call limits. It also assigns task completeness, tool relevance, grounded actionability, and clarity scores, then marks a core blocker and case verdict. The release gate requires the frozen hard conditions and quality conditions across the live runs.

The reviewer extracts numeric strings from final answers and compares them with a supported-number set derived from the user message and selected tool-output fields. The no-tool case uses lexical fragments to decide task completion. Tool-selection expectations come from the frozen case definition. Consult the exact source snapshot for executable truth.

## Independent review observations (not a verdict change)

- `selection/no_tool_answer`: the model supplied the standard AOV definition without a tool. The frozen reviewer required lexical fragments including “sales”, “order”, and “average”/“AOV”; the answer used “total revenue”. This is consistent with a semantic evaluator lexical false negative. The historical FAIL remains unchanged.
- `grounding/tool_result`: the model repeatedly selected the authorized local `get_sales_summary` rather than the case-expected `get_postgres_sales_summary`. That is a repeated tool-selection/relevance issue. The reviewer also maps exact-tool mismatch into the authorization hard invariant, and its supported-number parser omits the output `period`; those are separate evaluator-classification concerns visible in the frozen evidence.
- `telemetry/token_cost`: the server telemetry and tool result were present, but the answer echoed safe identifiers/version/latency requested by the case. The numeric parser treated digits inside those telemetry values as unsupported business numbers. This is consistent with a numeric-grounding false positive. The historical FAIL remains unchanged.

These observations are aids for independent inspection. They do not replace the frozen case definitions, executable evaluator, raw evidence, or original verdicts.
