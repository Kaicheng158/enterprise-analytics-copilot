# rag-grounded-v4 — offline candidate verification

Date: 2026-10-08 Australia/Melbourne. Unpublished candidate; no active selection, paid calls, live regression, endpoint, release gate, commit or push.

Prompt SHA: `ba038c09f78a629fcc0a09404c1f52dec3888e53beefc3edc62efa133cbf1660`

## Exact difference from v3

Preserve v3's original first system instructions/schema, append the already prepared FIELD_RESPONSIBILITIES there, replace only its demonstration block with ATTRIBUTED_EXAMPLES, and preserve the final v3 system reminder/limitation relevance/deliverable completion verbatim. Do not edit historical prompt modules or preparation components.

Facts contain attributed supported/user facts and reproducible calculations. Interpretation can contain labeled hypotheses and explicitly requested proposed actions; proposals are not executed tools or verified causes. Limitations only address material current-task gaps, and missing-input descriptions cannot replace a requested action. No relevant facts permits facts=[]; no-answer status can remain in summary/limitations without invented citations to irrelevant evidence. Actual factual claims remain grounded in all fields.

Unlike v3's additional reminder alone, v4 pairs explicit field placement with consistent demonstrations: user facts use [user], suggestions appear in interpretation, and limitations retain actual gaps. Original demonstration user scenarios/data remain unchanged. This is a design change, not proof of improved model compliance.

## Diagnostics and isolation

`eval/rag/candidate_v4.py` loads the unchanged generator in an isolated namespace and selects v4 there. Existing production generator stays v1; /chat stays analytics-v2. `generate_synthetic` uses the existing DiagnosticGroundedGenerator with explicit synthetic capture and writes new attribution-failure evidence with the existing exclusive mode0600 writer. Parsed rejected answers remain out of telemetry/logs. Existing paths are rejected before a provider call. This module constructs no live providers and has no paid-run command.

Freeze manifest records candidate/preparation/diagnostic dependencies and the unchanged suite SHA. Validator, token-budget code and schema are unchanged; the larger prompt is counted through the same model-token calculation.

## Tests

163 offline tests and31 database tests passed. New tests check exact composition/SHA, attributed examples through the original validator, unchanged active prompt and validation bytecode, empty facts plus proposed action with fake provider, actual synthetic failure capture with v4 metadata, no body logging and no overwrite/retry. Only deterministic fake providers were used, not a real model. Temporary diagnostic evidence is removed by test cleanup.

All pre-existing backend/eval/docs files were checksum-verified unchanged. No historical evidence or verdict rewritten. Stop here for user review before any live regression.
