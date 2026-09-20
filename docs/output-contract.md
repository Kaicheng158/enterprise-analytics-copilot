# Output contract — Phase 2.4

The model must output one JSON object. `backend/output.py` centrally defines AnalyticsAnswer: all four fields are required, summary is a strict string, and facts/interpretation/limitations are strict lists of strings. Extra fields are forbidden. Empty lists are allowed; empty strings are not rejected by this minimal type contract. Section responsibilities remain:

| Section | Responsibility | Boundary |
|---|---|---|
| summary | Briefly answer the question using the sections below. | No new claims or unsupported conclusion. |
| facts | State relevant supplied information with attribution, and reproducible calculations showing their inputs or calculation. | User-provided information is not independently verified. No guesses, assumptions, hypotheses or inferred causes. Explicitly say when there are no relevant facts. |
| interpretation | Explain what the stated facts may mean; label assumptions and hypotheses. | Connect analysis to facts. Do not turn correlation or a possible explanation into a proven cause. Say when evidence is insufficient. |
| limitations | Identify missing data, ambiguous definitions, uncertainty and verification limits; request the specific information needed. | Do not invent limitations or claim certainty if none further is apparent. |


`backend/prompts.py` selects a published release containing the role, semantic constraints and a frozen JSON-schema snapshot originally derived from AnalyticsAnswer. Schema changes require a new release; current compatibility is tested. The DeepSeek adapter enables `response_format={"type":"json_object"}` and calls the provider-independent parser; field definitions are not duplicated in the adapter or route. This uses JSON mode plus local validation, not provider-enforced JSON Schema. [Official DeepSeek JSON guide](https://api-docs.deepseek.com/guides/json_mode/).

Breaking change from 2.3: `/chat` now returns `answer` as a validated object, not a JSON-encoded string. Existing provider/model/usage/latency/cost metadata remains alongside it. `/docs` exposes AnalyticsAnswer as the nested response schema.

Parsing is strict: malformed/empty JSON, missing fields, wrong types, additional fields, duplicate keys, nonstandard constants, code fences, surrounding prose and incomplete generation are rejected with HTTP 502, code `llm_invalid_output`. There is no automatic repair, coercion, extra model call or retry for invalid output. Invalid upstream envelopes/usage retain `llm_invalid_response`. Raw answers, parser details and credentials are neither returned nor logged on failure. Failed attempts currently log unknown usage/cost; they may still have incurred provider charges.

JSON/type validation does not establish factual accuracy or guarantee that facts exclude hypotheses. Semantic constraints remain prompt instructions; dedicated evaluation remains a later task. Phase 2.5 adds two synthetic few-shot examples whose assistant messages conform to this schema; see [example design](few-shot-examples.md).
