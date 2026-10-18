# Phase 4.5 Tool Exposure Policy

## Finding

The frozen Evaluator v2 contract is explicit: `grounding/tool_result` binds
`get_postgres_sales_summary` as the authoritative source. The former runtime
nevertheless sent both sales tools to DeepSeek. The user message alone did not
distinguish the in-memory fixture from the PostgreSQL source, so model choice
was ambiguous and the server did not enforce the case-bound source policy.

This is a product composition defect, not a reason to rewrite the frozen v2
suite or its historical verdicts.

## Server-owned exposure profiles

- `production`: exposes only `get_postgres_sales_summary` to the model.
- `evaluation_in_memory`: exposes only `get_sales_summary`, and is selected by
  trusted evaluation code for an explicitly bound local-fixture case.
- No profile exposes both tools to the model at once.
- A client message cannot select or override the profile.

`ToolExposurePolicy` is narrower than authorization. An exposed tool must be
registered and authorized for the principal. The existing Registry and
Executor continue to make the independent authorization decision. Trusted
server code may directly execute an authorized hidden fixture tool; a model
that guesses a hidden tool name is rejected before execution.

## Historical compatibility

The v1 registry, executor, orchestrator, catalog, prompt, suite, evidence and
verdict remain unchanged. The exposure boundary is implemented in new modules,
and the disabled-by-default DeepSeek runtime now uses the production profile.
Historical v1/v2 hashes remain the reference for their original evaluations.

## Next live regression

A future Evaluator v2 live runner should choose the server-owned profile from
the frozen case contract before making a provider call: production/PostgreSQL
for stored or case-bound PostgreSQL cases, and `evaluation_in_memory` only for
explicit fixed-fixture cases. It must never derive this policy from model text
or a client-supplied tool name. `eval/tools/v2/exposure_binding_v2.py` provides
that deterministic mapping and records binding/profile/visible-tool metadata
without changing the frozen suite, rubric, gate or historical result.
