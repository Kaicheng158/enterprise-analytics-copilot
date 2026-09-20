# Prompt architecture — Phase 2.1–2.3

POST /chat -> validated user_message -> provider -> build_messages(user_message) -> fixed system role + user role -> DeepSeek.

backend/prompts.py owns SYSTEM_PROMPT_V1, OUTPUT_CONTRACT and the provider-independent message builder. Prompt content is source-controlled and deployed with the application; it is not supplied by an ordinary API client or environment variable. The provider transports messages; the route validates user input. Fresh message objects are created for each call.

V1 defines Enterprise Analytics Copilot's analytical role and goal, prohibits invented facts/data/sources/results, asks for missing information, distinguishes assumptions from verified causes, and accurately states that the chat has no business-data tools. The PostgreSQL health check does not give the model database access.

The named V1 constant and dedicated module provide a clear location for future versions. Version registry, selection, structured responses, few-shot examples and prompt evaluation infrastructure remain future tasks.

Boundary: rejecting a system_message field prevents replacement of the server-owned role. It does not prove resistance to semantic prompt injection or guarantee factual accuracy. Those require later Phase 2 tasks and evaluation. Current tests verify role assembly and the API boundary only.

Compatibility: user_message and legacy message are accepted; system_message is rejected with 422, including null. LLM_SYSTEM_MESSAGE is ignored.

Phase 2.3 appends the [ordinary-text output contract](output-contract.md) to the core role prompt in the same server-owned system message. The API answer remains text; no response parser or structured-output mode is enabled.
