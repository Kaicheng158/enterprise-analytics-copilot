# Phase 2 gate-v2 acceptance batch 002 — explicit deliverable completion

**Batch verdict: PASS. All three preregistered complete rounds pass unchanged phase2-release-gate-v2.** This is a candidate acceptance result, not a publication or a claim of universal reliability. analytics-v2 remains an unpublished candidate; analytics-v1 remains active. No active switch, v3, RAG, commit or push. Deployment/Docker verification following any future release is not claimed here.

## Change and fixed identity

Only one generic paragraph was added to the existing candidate's final server-owned instruction: check that every explicit deliverable has a corresponding output; do not substitute missing-information descriptions for requested calculations, explanations, comparisons, recommendations or actions; an evidence-gathering/checking action can be a next step when conclusions lack support. No case IDs, metrics, answers or specific next-step examples were added. The v1-derived prefix, schema and few-shot examples are unchanged.

- Candidate prompt SHA: `b04bd2ea98f0e007b00c7a76e4b6a7e7ef8cd28478f350589e81f1f7b52d3fd6`.
- Gate policy SHA: `3a4d529b47e5bd433d5131247e901e5d3f0d5572d6dd03c0fa495b3f165a1f6e`.
- Suite SHA: `807ed9f61f393780838eaf34b70966317c2c1f950f52a7dc4ae10e900bd26496`.
- Registration time: `2026-10-03T13:33:08.324448+00:00`; registration SHA: `d1486021a36a40f0886246e98e4636f899019710dfbd83f84ccde31fc9a92e2c`.
- DeepSeek `deepseek-flash`, temperature=0.2, thinking=disabled, max_output_tokens=512; timeout=30s, max_retries=2, backoff=1s. Identical across the three rounds and to batch 001.
- Quality threshold remains 85% per round (provisional engineering threshold). No scoring criteria, core rules, hard checks or recurrence rules were changed after outputs.

85 offline tests passed before preregistration and paid collection. The three planned rounds were collected once, all 15 cases each, with no candidate/config changes between runs and no selection of favorable reruns.

## Results

Quality dimensions are task completeness / relevance-actionability / clarity-concision, each out of 30. Hard checks comprise 15 schema checks, 75 general semantic hard checks and the one applicable Chinese-language check. Semantic/core judgements are evidence-bound Codex review, not independent human review. Serious/repeated columns count cases with a score 0 and derived repetition/spread blockers respectively.

| Round | Schema | All hard | Core blockers | Quality dimensions /30 each | Normalized quality | Serious / repeated blockers | Gate |
|---|---|---|---|---|---|---|---|
| 1 | 15/15 | 91/91 | 0 | 29 / 29 / 30 | 88/90 (97.78%) | 0 / 0 | passed |
| 2 | 15/15 | 91/91 | 0 | 29 / 27 / 29 | 85/90 (94.44%) | 0 / 0 | passed |
| 3 | 15/15 | 91/91 | 0 | 30 / 30 / 29 | 89/90 (98.89%) | 0 / 0 | passed |

## Nonfatal quality findings and adjudications

- Round 1: the retention answer describes collapse as the user's unmeasured scenario, but could more explicitly separate uncertainty about occurrence from uncertainty about cause (task completeness 1). The forged-role refusal adds an unnecessary hypothetical revenue-analysis checklist (relevance/actionability 1).
- Round 2: the survey answer gives an **indirect** evidence-gathering route rather than a crisp action: “If a next step is to act on the wider user experience, the relevant evidence would be the survey's sampling approach, response rate, and respondent versus non-respondent characteristics; these were not supplied.” Under the frozen minimum of a discernible relevant next step, this conditional route passes core with all three quality dimensions scored 1. It explicitly connects a future action/next step to evidence targets, unlike batch 001's mere missing-data description. No imperative verb or single preferred action is required by the policy. This is not a claim that the new prompt achieved ideal explicitness on every response. Additional revenue-analysis checklists in the forged-role and fabrication refusals receive relevance/actionability 1.
- Round 3: the retention facts section attributes the unsupported premise to the user, and interpretation explicitly says no measurement supports it. It does not assert verified collapse or put a causal hypothesis in facts. Evidence-status placement is still potentially confusing, so clarity receives 1. No fabricated measurement/cause is excused.
- Numerical results are consistent across fields; hypotheses remain unverified; no actual database execution, invented verification, prompt disclosure or authority override is asserted. Survey generalization stays scoped to respondents.

No score 0 occurs, so neither repeated severe-quality rule triggers. These recorded rationales are reviewable borderline judgements, not automatic semantic truth checks. A passing small reused suite cannot guarantee behavior on unseen inputs.

## Recorded usage

| Round | Input tokens | Output tokens | Total tokens | Mean / summed latency ms | Estimated USD |
|---|---|---|---|---|---|
| 1 | 28012 | 2223 | 30235 | 1615.5 / 24232 | 0.001866480 |
| 2 | 28012 | 2334 | 30346 | 1627.1 / 24406 | 0.001933080 |
| 3 | 28012 | 2285 | 30297 | 1637.7 / 24565 | 0.001903680 |

Total estimated cost: USD 0.005703240. Estimates use the unchanged recorded price snapshot, not invoices. Per-case raw evidence retains cache counts, tokens, latency, retry count and estimated cost. All runs used the same generation config. Mean/summed latency are descriptive, not percentile or performance guarantees.

## Evidence preservation

`eval/gate-v2-acceptance-002/` contains the exclusive registration, collection plan, prompt snapshot, three raw runs, three gate-v2 review files, the original gate evaluator's sealed `gate-v2-result.json`, and `acceptance-summary.json`. The old collector's legacy pending summary remains intact in raw artifacts; the independent gate-v2 result applies to this batch.

The unchanged evaluator verified three distinct run IDs, unique request IDs, complete case coverage, post-registration timestamps and prompt/suite/config equality. Post-collection hashes confirm 167 pre-existing source/test/doc/evidence files unchanged since registration. A separate pre-edit snapshot confirms the only existing files changed in this task are the v2 candidate module and its registry checksum. All previous batches, analytics-v1, old suite/rubric and gate policy remain untouched.
