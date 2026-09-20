# Output contract — Phase 2.3

The model answer is ordinary text with four lowercase English headings in this order. Headings may be on their own line or followed by a colon and text. Section content follows the user's language. All four sections are required, including when evidence is missing; no JSON or code block is requested.

| Section | Responsibility | Boundary |
|---|---|---|
| summary | Briefly answer the question using the sections below. | No new claims or unsupported conclusion. |
| facts | State relevant supplied information with attribution, and reproducible calculations showing their inputs or calculation. | User-provided information is not independently verified. No guesses, assumptions, hypotheses or inferred causes. Explicitly say when there are no relevant facts. |
| interpretation | Explain what the stated facts may mean; label assumptions and hypotheses. | Connect analysis to facts. Do not turn correlation or a possible explanation into a proven cause. Say when evidence is insufficient. |
| limitations | Identify missing data, ambiguous definitions, uncertainty and verification limits; request the specific information needed. | Do not invent limitations or claim certainty if none further is apparent. |

Implementation: `backend/prompts.py` keeps `OUTPUT_CONTRACT` separate from the core role prompt. The server combines both into one system message before the user message. Clients cannot replace either component.

The HTTP response envelope is unchanged: `answer` remains a string alongside existing usage/latency/cost metadata. This task does not add four API fields, JSON mode, a parser, schema enforcement, repair or retry on formatting violations. The contract is a model instruction, not a guarantee of factuality or formatting; generated responses can still violate it or be truncated. Structured output and dedicated regression evaluation remain later tasks.

Tests verify that the contract reaches the provider in the system role, user content remains separate, no structured-output option is sent, and the ordinary answer text is preserved by the API.

Verification (2026-09-20): 35 offline tests passed; container rebuild succeeded. Two real /chat calls returned HTTP 200 with all four sections and finish_reason=stop. With supplied revenue 100→80, facts showed the reproducible 20% decrease and interpretation treated causes as unconfirmed; without data, the answer stated that no revenue figure could be determined. The initial smoke-check regex incorrectly required standalone headings; inline headings were manually reviewed and the second check accepted them. These two samples are smoke checks, not general compliance guarantees.
