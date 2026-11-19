# PV2-04d.1 — DeepSeek Dataset Planner foundation

Status: local Mock/HTTP foundation complete; real DeepSeek capability remains **unverified / Hold**. No paid provider call was made during PV2-04d.1.

The original `dataset-planner-v1` Prompt, suite, SHA and Mock evidence remain immutable. Final review found that its example dropped explicit month filters and inferred an unspecified year. It was not used for paid evaluation. `dataset-planner-v2` is an independent release that adds general scope/deliverable completeness and missing-year clarification rules; v2 is the only candidate eligible for the PV2-04d.2 smoke.

## Purpose and gates

The AI Agent Engineering objective is to learn and verify the separation between probabilistic planning and deterministic authority/execution. Analytics Copilot supplies the concrete Dataset use case. The model may propose operations; it cannot grant access, bind another Dataset, execute a Tool or publish a result.

Feature Value Gate v1: **Approve as necessary infrastructure**, not as proven user value. A successful future Planner should reduce manual selection of filters, aggregations and multi-step operations, but Mock tests do not demonstrate time saved by real users.

AI Agent Learning Gate v1: **Approve**. Learning targets are structured planning, server-owned context minimization, schema compilation, semantic validation, centralized clarification, injection boundaries, provider failure handling and durable-runtime reuse. The boundary is intentionally not Dynamic Planning, Memory, LangGraph, Multi-Agent, charting or reporting.

## Architecture

```text
natural-language question (untrusted data)
  -> server-authorized Project + immutable DatasetRevision
  -> schema-only DatasetPlannerCatalog (field/type/unit + approved metrics)
  -> dataset-planner-v1 server prompt + response schema
  -> DeepSeek JSON draft (no authority, IDs, rows, paths or results)
  -> server compilation into DatasetAgentRequest + dataset-plan-v1
  -> existing DatasetPlanValidator
  -> PreparedDatasetPlanProvider
  -> existing DurableDatasetAgentRuntime / Queue / Checkpoint / Receipt
  -> six deterministic PV2-04c tools
```

`DeepSeekDatasetPlannerAdapter` uses the existing DeepSeek settings, response/usage contracts and cost guard through a Dataset-specific transport, preserving the historical frozen Planner transport byte-for-byte. It enforces one call, zero retries, JSON mode, explicit `temperature=0`, `thinking=disabled` and `max_output_tokens=800`. `DEEPSEEK_DATASET_PLANNER_ENABLED=false` is independent from the historical sales Planner flags.

The provider receives only the user question, preferred response language and a catalog of table index, field name, field type, required flag, unit and explicitly approved metric definitions. It does not receive raw rows, full files, Project/Asset/Revision UUIDs, normalized hashes, authorization versions, storage references, local paths, credentials or Tool results.

The server compiles an accepted draft by adding the live Project, Revision, normalized SHA-256, authorization version, canonical request hash and exact binding. The unchanged `DatasetPlanValidator` then enforces the six-Tool allowlist, strict Tool input models, known fields, exact request mapping, DAG order/cycles and step/Tool/budget limits. A model response cannot bypass this step.

## Clarification and failure semantics

If a necessary field, date meaning, unit or business metric cannot be identified, the model must return one `needs_clarification` object containing all necessary questions. It must not invent a field or ask for unrelated context. Clarification creates no Durable task.

Invalid JSON, extra/missing fields, illegal Tools, invalid arguments, context/catalog mismatch, truncation, timeout, transport failure and call/budget exhaustion fail closed with fixed codes. There is no automatic JSON repair or provider retry. A syntactically valid Plan with an unknown field, dependency or cycle is rejected by the server Validator before persistence.

## Sanitized example

Request data sent to the provider:

```json
{
  "question": "Compare January 2026 and February 2026 revenue by region and give the overall change.",
  "preferred_language": "en",
  "dataset_catalog": {
    "catalog_version": "dataset-planner-catalog-v1",
    "tables": [{
      "table_index": 1,
      "fields": [
        {"name": "region", "kind": "text", "required": true, "unit": null},
        {"name": "period", "kind": "date", "required": true, "unit": null},
        {"name": "revenue", "kind": "money", "required": true, "unit": "USD"}
      ]
    }],
    "metrics": []
  }
}
```

The model draft contains only operations. The server-generated `dataset-plan-v1` contains three Tool steps before final assembly:

```json
{
  "version": "dataset-plan-v1",
  "task": "analyze_dataset_revision",
  "project_id": "<server-owned UUID>",
  "binding": "<server-owned immutable Revision binding>",
  "normalized_sha256": "<server-verified SHA-256>",
  "authorization_version": 4,
  "steps": [
    {
      "step_id": "analyze_1",
      "kind": "tool",
      "tool_call": {
        "name": "aggregate_dataset_revision",
        "arguments": {
          "binding": "<server-owned binding>",
          "table_index": 1,
          "filters": [{"field": "period", "operator": "eq", "values": ["2026-01-31"]}],
          "group_by": ["region"],
          "aggregations": [{"result_id": "revenue", "function": "sum", "field": "revenue"}]
        }
      }
    },
    {
      "step_id": "analyze_2",
      "kind": "tool",
      "tool_call": {
        "name": "aggregate_dataset_revision",
        "arguments": {
          "binding": "<server-owned binding>",
          "table_index": 1,
          "filters": [{"field": "period", "operator": "eq", "values": ["2026-02-28"]}],
          "group_by": ["region"],
          "aggregations": [{"result_id": "revenue", "function": "sum", "field": "revenue"}]
        }
      }
    },
    {
      "step_id": "analyze_3",
      "kind": "tool",
      "tool_call": {
        "name": "compare_dataset_revision",
        "arguments": {
          "binding": "<server-owned binding>",
          "table_index": 1,
          "dimension": "period",
          "measure": "revenue",
          "aggregation": "sum",
          "baseline": "2026-01-31",
          "comparison": "2026-02-28",
          "derived_metric": "absolute_change"
        }
      }
    },
    {"step_id": "assemble_results", "kind": "compute", "depends_on": ["analyze_1", "analyze_2", "analyze_3"]}
  ]
}
```

The disposable PostgreSQL execution returned January `North 100.25 USD / South -20.25 USD`, February `North 120 USD / South 0 USD`, and overall `80 USD → 120 USD`, absolute change `40 USD`. A request that says only “January and February” without a year now returns one clarification rather than choosing 2026. The exact machine evidence is in `pv2-04d1-mock-acceptance-v2.json`; identifiers above are deliberately redacted.

## Evaluation and real-model preregistration

`dataset-planner-eval-v2` contains eleven frozen cases, adding explicit grouped-period scope and ambiguous-year behavior while retaining the v1 security/provider coverage. Hard invariants cannot be averaged away. Core task completion is separate. Semantic quality scores task completeness, relevance and clarity on 0/1/2, with a provisional normalized threshold of 85% and allowance for multiple reasonable Plans.

Frozen v1 identities are retained in `eval/datasets/planner-v1`. The v2 live candidate identities are:

- Prompt SHA-256: `e7e8b4a830cf2ba414fd1825c1d8a41e4eab8e74da94e104ca5b3e0423f10c8e`
- Response schema SHA-256: `16c1a04565b7e00cee9e69205d5c94fe2499a36207b2ee22a969c818211cfed5`
- Suite SHA-256: `f36719fb781379973bcacecfe7352c579c3cef781d2ce78ee94786e8dcf47070`
- Policy SHA-256: `7b684a4d4c0f70d97b383bff3b2276eb45edff1074b2814607319dafbb2e6651`
- Live plan SHA-256: `1d55fe1edd4bb49ccf72ead84e165abbe5d7654b5d0075ab61ac6a64b4147388`

The future v2 smoke is one round of four fixed cases, at most four DeepSeek calls, zero retries and USD 0.01 total budget. Hard invariants, schema and core completion must be 100%; normalized quality must be at least 85%. Any authority escape, sensitive disclosure, unregistered execution, budget breach or evidence failure stops the run. Passing it would demonstrate only a small live smoke, not production reliability or a release gate.

## Verified and not verified

Verified locally with Mock HTTP and synthetic/disposable PostgreSQL:

- bilingual question packaging and schema-only context;
- valid Plan compilation plus server identity stamping;
- independent finance CSV and people/XLSX structures;
- centralized clarification without a Durable task;
- illegal Tool/field/dependency/cycle and injection rejection;
- invalid JSON/schema, timeout and truncation safe stop;
- Mock DeepSeek draft through the existing Durable Runtime and real deterministic Dataset tools.

Final local verification passed **502/502 offline tests**, **186/186 disposable PostgreSQL tests**, **11/11 focused PV2-04d.1 tests**, and **21/21 historical frozen-Planner compatibility checks**. The ten Mock evaluation cases are mapped to concrete test evidence in the machine-readable acceptance record. Paid provider calls: zero.

Not verified: real DeepSeek planning quality/stability, production data privacy, human approval, cancellation, dynamic replanning, arbitrary business metrics, rate-limit behavior under load, hard process isolation or public product integration.
