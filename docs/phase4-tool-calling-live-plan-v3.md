# Phase 4.5 Evaluator v3 Live Acceptance Plan

## Frozen identities

- Suite: `tool-calling-suite-v3`
- Suite SHA-256: `78035ed0e09499eb2dbb2fbe9c52cac56476db414c3446b44927891980b3df3f`
- Gate: `tool-calling-release-gate-v3`
- Gate SHA-256: `ce548ce798b5c2f3ad9fc2e14c483b2adc4d4c91b304375c4970dbf0ee49d497`
- Evaluator SHA-256: `2e1ddaaf6703ad467ea470fd23047101edb94838e4d1b4d055bf33bfb8ab8aed`
- Prompt: `deepseek-tool-decision-v1`
- Prompt SHA-256: `af1574d65979199045b6ab2cb8267fae23a3bb1887e48d1a53e51ee9ec682dfb`
- Tool source binding: `tool-source-binding-v1`
- Tool exposure policy: `tool-exposure-policy-v1`

The v1/v2/v3 frozen evaluator artifacts and all v1/v2 historical raw evidence,
decisions, SHA values, and verdicts are read-only. The v2 Gate remains `FAIL`.

## Fixed live configuration

- Provider/model: DeepSeek / `deepseek-flash`
- Temperature: `0.2`
- Thinking: `disabled`
- Maximum output tokens: `512`
- Tool choice: `auto`
- Timeout: 30 seconds
- Maximum provider interactions: 2 per case, 54 total
- Maximum tool executions: 1 per case
- Automatic retries: 0
- Rounds: 3 independent rounds
- Cases: the same frozen 9 live-model cases per round
- Total stop budget: USD 0.02
- Conservative estimate: USD 0.006416496, based on two times the immutable v2
  three-round actual cost of USD 0.003208248

## Preflight controls

Preflight makes no paid provider request. It verifies:

1. all offline and isolated database tests pass;
2. the seven deterministic controls are suite-bound and all pass;
3. PostgreSQL responds to `SELECT 1`, the synthetic sales relation exists, and
   the PostgreSQL sales tool returns successfully;
4. the production profile exposes only the PostgreSQL tool, the evaluation
   profile exposes only the fixed in-memory tool, and an untrusted principal sees
   no tool;
5. a model-guessed hidden tool is blocked by server exposure policy while trusted
   direct Executor authorization remains an independent boundary;
6. Markdown/equivalent-field positive parser controls pass and wrong amount,
   missing metric, and fabricated-extra-amount controls fail;
7. feature flag remains default-off and no public Tool API exists;
8. paid mode requires both the environment guard and exact confirmation;
9. budget reserve, interaction limits, atomic refuse-overwrite evidence storage,
   redaction, and v1/v2/v3 historical hashes remain intact.

## Formal execution

After explicit authorization, the runner performs all 27 cases in fixed order.
Every case stores its model decisions, native tool call, validated arguments,
server authorization/exposure metadata, tool result, final response, hard/core and
quality verdicts, token usage, latency, and estimated cost. Evidence is synthetic,
written atomically, and refuses overwrite.

Execution stops immediately for a serious security execution, failed security
invariant, incomplete cost evidence, detected secret, changed frozen identity,
interaction-limit breach, or budget/reserve breach. Ordinary semantic case failures
are retained and execution continues according to the preregistered plan.

The release gate requires all seven deterministic controls, hash-bound preflight
prerequisites, three independent complete live rounds, 100% applicable hard
invariants, zero core blockers, no zero quality dimension, at least 85% quality per
case and per round, and no repeated cross-run severe failure.

## Final preflight result

`preflight-v3-002` passed with zero paid calls:

- 277 offline tests and 43 isolated database tests passed;
- deterministic controls: 7/7 PASS;
- PostgreSQL `SELECT 1`, sales relation, and read-only sales tool passed;
- permissions, source exposure, parser positive/negative controls, budget stop,
  atomic evidence storage, redaction, default-off guard, and route checks passed;
- all 74 protected v1/v2 historical artifacts retained their SHA-256 values;
- Gate state is `NOT_RUN` with only `minimum_live_runs_not_met`, as expected before
  the paid regression;
- plan SHA-256:
  `f815d3e78d170ddef42c086ab3d90602a752e69da6192c2ac1920619eb50a878`;
- preflight evidence SHA-256:
  `f311594131f26502d16d65ac16c05ffa3bca1d30d215d79930289ad8aaa8e65e`.

The live runner verifies that the current plan hash exactly matches this passing
preflight before it permits a provider request.
