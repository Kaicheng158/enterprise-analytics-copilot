# Attribution diagnostics and response semantics preparation

Non-paid implementation only. No Prompt release or v4 candidate created, no endpoint or release gate, no commit/push. Existing v1/v2/v3, historical evidence/verdicts, schema, validator, retrieval/embedding/chunking/config and all frozen manifests remain byte-identical.

## Diagnostics

`backend/rag/attribution_diagnostics.py` adds `DiagnosticGroundedGenerator`, an internal composition wrapper around the unchanged generator. Existing callers continue unchanged; future eval callers explicitly select the wrapper. This additive approach preserves executable historical freeze checks. It does not monkey-patch global provider or prompt state.

Each request has a private capture provider. It forwards the same messages/metadata once and retains the parsed ChatResult transiently. On the original attribution error only, diagnosis replays individual output items through the ORIGINAL `validate_sources`, in original field/item order, to locate the first rejected item. It does not decide whether the request passes: original exception/code is re-raised. No repair, retry, additional API or semantic keyword exemption.

Production default attaches safe `attribution_diagnostic` to the exception and telemetry:
- failed_field, zero-based item_index;
- original error_code and fixed parsing_result;
- matched token count, valid-known/unknown label counts, malformed token count;
- whether literal [user] was recognized.

No item text, raw token strings, document body, rejected answer, credentials or sensitive headers enter production diagnostic telemetry. Existing logs remain body-free. A bare label is classified as missing RECOGNIZED attribution, not automatically diagnosed as “model supplied no citation whatsoever.” Unknown/malformed recognized labels retain rag_invalid_citation; missing attribution retains rag_missing_attribution. Diagnostic labels alone do not assess semantic grounding.

Trusted operator/eval code can explicitly select capture_mode='synthetic_eval'. This additionally exposes `error.synthetic_eval_evidence` with detected labels/tokens, parsing format/membership results, available context labels, parsed rejected structured answer and generation telemetry. It is NOT added to telemetry/logs or public responses. The mode is not selected by user request fields or inferred from text that claims to be synthetic; operators must ensure the corpus is authorized synthetic data. No software classifier guarantees content is non-sensitive.

`eval/rag/diagnostic_evidence.py::save_synthetic_failure` explicitly writes this separate object to a trusted local path, with exclusive creation and mode0600. Existing files/symlinks are not overwritten. Production exceptions cannot use this writer. Structured answer means the schema-validated model object, not original HTTP bytes/headers or unparsed content. This solves diagnostics for future attribution failures only; it cannot recover the already discarded v3 answer.

Example internal use:

```python
generator = DiagnosticGroundedGenerator(provider, settings, capture_mode="synthetic_eval")
try:
    result = generator.generate(query, context)
except ProviderError as error:
    if hasattr(error, "synthetic_eval_evidence"):
        save_synthetic_failure(new_local_path, error)
    raise
```

No frozen historical runner was altered to auto-capture bodies. A future candidate runner can use this wrapper (generator_type can select an isolated candidate generator) and explicitly save authorized local evidence.

## Response field responsibilities — prepared, not activated

`backend/rag/response_semantics_draft.py` supplies reusable server-owned preparation components; no model messages are assembled and no release/version/active selection is changed.

- summary: concise answer to the actual request, no unsupported new claim.
- facts: only supported evidence/user facts and reproducible calculations; each item uses the existing evidence-label or [user] attribution rule. No hypotheses, proposed actions or no-evidence status messages. facts=[] is valid when no relevant supported facts exist.
- interpretation: analysis and clearly labeled hypotheses; also the user's explicitly requested proposed next step/action/recommendation, identified as an investigation/check/validation suggestion, never as an already executed action or verified explanation. A generic proposed investigative method need not pretend to be a source-stated business fact.
- limitations: only constraints materially affecting the current requested answer/action. Empty array is valid. Missing-input descriptions cannot substitute for a requested action, and a business factual assertion remains subject to grounding even outside facts.

No new schema fields are needed. With empty or irrelevant evidence, a supported no-answer explanation can be in summary/limitations and facts can be empty; it must not cite irrelevant material merely to satisfy a format rule. If a response chooses to report actual source content as a fact, its attribution remains required. Validator acceptance is unchanged.

## Few-shot consistency

Two existing synthetic demonstration scenarios are retained in a separate prepared copy. User messages, factual values, summary and original interpretation text remain unchanged. Each demonstrated facts item receives [user], since these facts come from the demonstration user's supplied data; the empty facts example stays empty. Existing example evidence-gathering suggestions are explicitly labeled as proposed next steps and placed in interpretation. limitations retains only the actual evidence gaps. No live regression case/metric/source is added or used as an expected answer.

Historical example tuples and all released/candidate prompt prefixes remain untouched. Tests parse every prepared assistant example with AnalyticsAnswer and run the ACTUAL existing attribution validator with an empty evidence list. They also verify the preserved user/scenario data and unchanged schema fields. This is structural consistency evidence, not a guarantee that a live model follows the examples.

## Verification and evidence

158 offline tests and31 database tests pass. Added tests cover exact failure field/index/order, missing/bare/unknown/malformed references, body-free production/synthetic logs, opt-in capture, error passthrough, exclusive private evidence writes, unchanged acceptance over positive/negative fixtures, empty-facts abstention and few-shot attribution/schema consistency.

Four deterministic fake-provider artifacts under `eval/rag/diagnostics-offline-001/` demonstrate:
- missing: facts[1], rag_missing_attribution;
- unknown: facts[0], rag_invalid_citation with unknown valid-format label;
- malformed: facts[0], rag_invalid_citation with malformed token;
- bare: facts[0], rag_missing_attribution with no recognized bracketed label; rejected answer permits inspecting the actual formatting.

These are NEW synthetic fixtures, not reconstructions of the discarded v3 output. No paid APIs were called. Existing historical files were checksum-verified unchanged and secret scan passed.

## SHA impact and v4 readiness

Diagnostics do not alter messages or Prompt SHA. Prepared field instructions and adjusted few-shot answers WILL change SHA if incorporated into a future prompt. No SHA or v4 release has been assigned to this preparation module. v1/v2/v3 retain their original hashes.

Engineering prerequisites to create a separately versioned v4 candidate are now present: consistent field semantics/examples, unchanged validator, and safe authorized failure evidence. Creating/fixing its final prefix, freezing SHA and testing live behavior remain separate authorized work; no inference of quality improvement or release readiness is made here.
