# Phase 4.5 Evaluator v2 Live Regression Plan

## Fixed identities

- Suite: `a7830d318bc2a51aa7bb6b23ea36b06f63ac45dc0b1914a031f744d67cf7396a`
- Gate: `8a4ac436dcda1a958af90994b12c9dfd0e1385d91e23170381758b437ad0935d`
- Prompt: `deepseek-tool-decision-v1` / `af1574d65979199045b6ab2cb8267fae23a3bb1887e48d1a53e51ee9ec682dfb`
- Source binding: `tool-source-binding-v1`
- Tool exposure: `tool-exposure-policy-v1`

The plan and runner do not replace or modify v1/v2 historical records. New
rounds use `live-v2-001` through `live-v2-003` and refuse to overwrite an
existing round, summary or preflight artifact.

## Server-owned case exposure

The runner derives the exposure profile only from the frozen v2 case contract.
PostgreSQL-bound cases receive `production`; explicit fixed-fixture cases
receive `evaluation_in_memory`. Ordinary users and model output cannot supply
or override this value. The selected profile, policy version and visible tool
list are recorded in each case's generation metadata.

## Fixed execution and budget

- DeepSeek Flash; temperature `0.2`; thinking disabled; maximum output `512`.
- Three independent rounds of nine live cases.
- At most two provider interactions and one tool execution per case.
- At most 54 provider interactions in the complete run; no automatic retry.
- Historical three-round estimated cost: USD `0.003247314`.
- Conservative estimate at 2×: USD `0.006494628`.
- Hard stop budget: USD `0.02`, with USD `0.0005` reserved before each case.

The runner stops on a serious security execution/invariant failure, incomplete
cost evidence, budget or interaction-limit breach, sensitive evidence, frozen
identity change, or any attempted overwrite.

## Commands

The default command is zero-cost preflight:

```bash
analytics-agent-env/bin/python -m eval.tools.v2.run_live_v2 --mode preflight
```

Paid execution remains disabled until a separate user confirmation. It requires
both the trusted process environment guard and exact command confirmation:

```bash
TOOL_CALLING_LIVE_REGRESSION_ENABLED=true \
analytics-agent-env/bin/python -m eval.tools.v2.run_live_v2 \
  --mode live --confirmation RUN_TOOL_CALLING_V2_LIVE
```

Do not run the historical v1 `eval/tools/run_live.py` for Evaluator v2.

## Preflight result

`preflight-v2-002` passed with zero paid calls. It verified 259 offline tests,
43 isolated database tests, PostgreSQL `SELECT 1`, the database-backed tool
source, all case-to-profile mappings, default-disabled live guard, budget stop,
atomic evidence writing, secret-safe evidence, no public Tool API, both frozen
suite identities, deterministic controls, and prerequisite hashes. The gate is
`NOT_RUN` with only `minimum_live_runs_not_met`, which is expected before paid
execution.

`preflight-v2-failed-001` is retained as a labeled failed preflight attempt. It
made zero paid calls and failed after artifact creation because existing JSON
control evidence was parsed as a strict Python object. The final runner uses
strict JSON parsing and only writes a PASS report after gate validation.
