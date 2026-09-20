# Prompt architecture — Phase 2.1–2.5

POST /chat -> validated user_message -> provider -> build_messages(user_message) -> fixed system role + user role -> DeepSeek.

backend/prompts.py owns SYSTEM_PROMPT_V1, OUTPUT_CONTRACT and the provider-independent message builder. Prompt content is source-controlled and deployed with the application; it is not supplied by an ordinary API client or environment variable. The provider transports messages; the route validates user input. Fresh message objects are created for each call.

V1 defines Enterprise Analytics Copilot's analytical role and goal, prohibits invented facts/data/sources/results, asks for missing information, distinguishes assumptions from verified causes, and accurately states that the chat has no business-data tools. The PostgreSQL health check does not give the model database access.

The named V1 constant and dedicated module provide a clear location for future versions. Phase 2.9 now provides immutable release selection; see prompt-versioning.md. Broader prompt regression infrastructure remains a later task.

Boundary: rejecting a system_message field prevents replacement of the server-owned role. It does not prove resistance to semantic prompt injection or guarantee factual accuracy. Those require later Phase 2 tasks and evaluation. Current tests verify role assembly and the API boundary only.

Compatibility: user_message and legacy message are accepted; system_message is rejected with 422, including null. LLM_SYSTEM_MESSAGE is ignored.

Phase 2.4 supersedes ordinary-text formatting: the system message combines role, semantic contract and the JSON schema generated from backend/output.py. The adapter enables JSON mode and delegates parsing to that provider-independent module. The validated AnalyticsAnswer becomes ChatResult.answer and is reflected in OpenAPI.

Phase 2.5 inserts two [server-owned synthetic demonstrations](few-shot-examples.md) from backend/examples.py between the system message and final user message. Fresh messages are assembled for each call; examples are shared across providers and cannot be overridden by clients.

Phase 2.7 adds general boundaries for user/quoted data, internal-instruction disclosure and truthful JSON refusals. Six-case evidence is documented in injection-boundaries.md; these prompt instructions are not a security enforcement guarantee.

Phase 2.9 supersedes runtime generation described above: backend/prompts.py selects a frozen prefix from backend/prompt_registry.py. Published content, including schema and examples, lives in backend/prompt_analytics_v1.py; it is not regenerated on requests. See [release and rollback rules](prompt-versioning.md).
