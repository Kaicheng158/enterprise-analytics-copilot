# Few-shot examples — Phase 2.5

`backend/examples.py` owns exactly two synthetic user/assistant exchanges. It has no provider imports or configuration. Assistant responses are constructed with AnalyticsAnswer and serialized as compact JSON; the same strict parser used for real answers validates them in tests. Immutable strings/tuples store the examples, and each request gets fresh message dictionaries.

| Example | Distinct failure mode | Intended behavior |
|---|---|---|
| Fictional shop, 100 → 80 orders; owner suspects price | Turning a supplied hypothesis into a fact or proven cause | Attribute supplied counts, show the 20% calculation in facts, place the unverified price hypothesis in interpretation, and identify evidence needed. |
| Fictional subscription service, alleged churn increase with no measurements | Accepting an unsupported premise and inventing magnitude or cause | State that the increase, magnitude and cause are unknown; keep facts empty; request a definition and comparable numerator/denominator counts before investigating causes. |

Assembly is: system (role + contract + schema), example user, example assistant, example user, example assistant, real user. The system marks demonstrations as synthetic and instructs the model not to reuse their data as facts about the current user. Examples cannot be supplied through the public request schema. They are demonstrations, not user conversation history.

Both examples retain all four output fields. They avoid real companies, personal data, redundant scenarios and extraneous background. Assistant JSON is serialized compactly; no arbitrary token trimming is applied. Examples add input tokens on every request, and actual provider usage/cost logging continues to account for them. No tokenizer dependency or hard token budget is added in this task.

Verification: 41 offline tests cover schema validity, distinct evidence patterns, role order through the adapter, final user preservation, fresh-message isolation and rejection of client-provided examples, alongside previous API/error/retry tests. These checks establish wiring and fixture quality, not a general hallucination or injection-resistance evaluation. No paid model call was needed for this task.
