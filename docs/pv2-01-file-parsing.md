# PV2-01 — Deterministic CSV/XLSX foundation

## Feature Value Gate v1 — preregistered 2026-10-29

Decision: **Approve for a default-off local synthetic-only library**. Public/customer-data ingestion is **Hold** pending PV2-02 storage lifecycle and PV2-03 identity/authorization. Owner: project engineering; user acceptance remains required.

Supported workflow: Investigate / Decide & Review users prepare a file once, receive one consolidated validation result, and reuse an immutable dataset revision. Simulate will later consume the same explicitly typed inputs. Collaboration remains dependent on real resource authorization.

Baseline is an engineering workflow comparison, not a measured user study: the current product cannot parse a CSV/XLSX file. A developer must (1) inspect the file, (2) map columns/types, (3) convert/rekey a fixture, and (4) create provenance manually. XLSX→CSV conversion is an extra operation when only a CSV importer exists. No claim about minutes saved is preregistered.

Before implementation, freeze the following benchmark targets using only synthetic fixtures:

- Equivalent CSV/XLSX pair, one shared explicit column mapping: **zero manual format conversions or cell edits**, identical normalized hash and exact Decimal values; source hashes remain distinct.
- Three identical submissions under one project/dataset/contract: **one unique revision identity**; a different project must never reuse that identity. This is an identity rule, not persisted-upload deduplication.
- One file containing ambiguous dates, missing required values and ambiguous amounts: **one returned issue collection covering all represented categories**, no iterative repair to discover the next category. Report required clarification, do not silently clean.
- Every accepted data cell: **100% file-hash/table/row/column traceability** including CSV multiline physical line ranges.
- Failed parse followed by valid parse: **no manual reset or lingering worker/temp artifact**; zero LLM calls in all cases.

Test pass rate is only a correctness guard. Report the workflow operations, distinct revisions, format equivalence, and issue-discovery results separately. The comparison does not establish usability or real customer time savings.

Duplication: existing RAG Text/Markdown ingestion is not a tabular parser; existing sales task/schema remain untouched. Reuse Pydantic conventions and project testing, but keep tabular contracts independent of Agent execution state.

Costs: bounded local worker process and standard-library ZIP/XML/CSV parsing, no model/API costs or added package. Maintenance cost: narrow OOXML subset and malformed-file corpus. No promise to parse every workbook. Security cost: untrusted binary/XML processing; hard process limits, bounded archive members and fail-closed unsupported-feature handling are required.

## Contract and limits (policy tabular-v1)

Library only; explicitly enable `ParseSettings(enabled=True)` in trusted local synthetic harnesses. No environment flag, HTTP endpoint, UI integration, database migration or persistent storage. Bytes are supplied with a basename and server-provided project/dataset IDs; these IDs separate identities but do not prove authorization.

Hard ceilings: 2 MiB source; 12 MiB expanded ZIP; 128 ZIP members; 4 MiB/member; compression ratio 100; 4 sheets; 5,000 data rows/sheet; 64 columns; 100,000 cells/file; 4,096 characters/field; 2,000 quality issues; 5-second wall time; 3-second CPU; 512 MiB worker memory guard; 16 MiB serialized result. Limits can only be tightened. Linux uses RLIMIT_AS; this Mac rejects RLIMIT_AS/RLIMIT_DATA, so macOS uses parent-monitored RSS (20 ms polling plus process-monitor overhead) and terminates on overage. This is sampled, may overshoot transiently, and is not a kernel memory sandbox. CPU uses RLIMIT_CPU; wall timeout kills and reaps the worker. A production deployment requires an OS/container hard memory boundary before accepting customer data. No extraction to disk or network resolution. Worker failure, limit, malformed input and timeout use fixed error codes without source content.

CSV: UTF-8 with optional BOM only, comma delimiter, strict quoting, first logical row is header. No sniffing/encoding guessing. Blank cells are null, not zero; textual NA/NULL are not silently erased. NFC and CRLF→LF normalization; header whitespace is trimmed and reported, value whitespace is preserved for text. Empty or duplicate normalized headers fail; malformed width fails.

XLSX: bounded OOXML ZIP with validated workbook/worksheet relationships; no macros, external relationships, formulas/caches, embedded objects, active content, merged cells or hidden sheets/rows/columns. Shared/inline strings and raw numeric/boolean/ISO date cells are supported. Excel numeric date serials are never inferred as calendar dates. Styled values require explicit type review because number formats may encode dates/percent/units. Unsupported structures fail closed rather than disappearing. No formula execution or repair.

Columns use explicit types text/decimal/money/date/boolean and optional explicit unit (money requires a three-letter currency). Without a supplied mapping, columns remain text and report schema confirmation required. Units are metadata, never inferred or converted. Numeric tokens follow an ungrouped decimal grammar; dates are ISO YYYY-MM-DD; ambiguous tokens become blocked cells plus content-free issue codes. No float math or rounding. Preview is opt-in, at most 5 rows / 8 columns, truncated, HTML escaped, and redacted unless a column is explicitly permitted by trusted caller.

Identity: raw SHA-256 fingerprints exact bytes; normalized SHA-256 includes ordered column contract, canonical cells and ordered tables (not file/sheet names or container metadata). Parse-contract hash includes policy version and column mapping. Revision SHA binds project, dataset, raw hash, normalized hash and contract hash. Identical repeat returns identical revision; changed raw bytes create a new provenance revision even when normalized content is identical. No global cache, cross-project data sharing or permission grant.

No logs or automatic evidence export of dataset contents. The full Dataset is a privileged local return value, not a safe public response. Safe errors never include filenames, header names, cell values, exceptions or paths. Preview requires a separate explicit reveal decision.


## Implemented boundary and verification — 2026-10-29

Public local entry: `backend.datasets.parse_file(bytes, FileSpec, ParseSettings(enabled=True))`. The default raises `parsing_disabled`. No route, file path loader, environment switch or Agent integration was added. `parser.parse_core` is an internal algorithm used by deterministic unit tests; untrusted callers must use the guarded service. Column mapping applies to every sheet; heterogeneous workbook schemas need a future explicit per-sheet contract rather than guessing.

Failures discard the partial result. Quality ambiguities return `needs_review` with blocked cells and issue locations; these datasets must not be used for trusted analysis until resolved. Normalized hashes for blocked cells are not proof of content equivalence: raw hash and scoped revision remain authoritative. Quality markers do not erase original source identity. Re-upload persistence/atomic uniqueness is not implemented; this phase proves repeatable identities only.

The full Dataset includes sensitive values and source sheet/column names by design; it must remain inside a privileged service boundary. `preview` defaults to redaction and returns no sheet names; reveal is an explicit trusted-caller decision, not an authorization system. Render previews as text. There is no CSV/export sink; formula-like text remains blocked, not sanitized into a purported safe spreadsheet. No logs are emitted by this module.

Synthetic benchmark command: `analytics-agent-env/bin/python scripts/verify_dataset_foundation.py`.
Evidence: [workflow results](pv2-01-synthetic-workflow.json). Generated fixtures contain no customer data. Results: zero format conversions or cell edits for the equivalent CSV/XLSX pair, one explicit mapping, identical normalized hashes/distinct raw hashes; three repeats yield one revision identity, another project a different identity; all three ambiguity categories are returned in one collection; 6/6 sample cells have source locations; failures recover with zero manual resets and no live worker. This is an engineering workflow result, not observed customer time savings; real user validation remains pending.

Sample normalized rows: 华东 / 1200.5 USD / 2026-10-01; 华南 / 800.25 USD / 2026-10-02. The separate unit fixture preserves `12345678901234567890123456789.1234` without float conversion. Invalid UTF-8 returns `unsupported_encoding`; formula plus cached result returns `formula_or_unsupported_sheet`; ZIP expansion is rejected before parsing; ambiguous date/amount/missing required fields remain review-required rather than guessed.

Remaining limits: deliberately narrow OOXML subset (styled numeric/date formats require review; merged, hidden, formula, macro, external-link and unsupported workbook features rejected), UTF-8/comma CSV only; no locale/encoding sniffing, formula evaluation, XLS support, percentage conversion or numeric date inference. Process isolation is not a network/filesystem permission sandbox; parser contains no external fetch/extraction, but public untrusted operation needs hardened deployment isolation, concurrency admission and a broader adversarial corpus. Mac RSS enforcement is sampled. Preview is not a permissions product.

Next dependency: PV2-02 needs scoped immutable blob/revision storage, atomic duplicate handling, quotas, retention/deletion/recovery and authorized previews. PV2-03 supplies actual identity and resource authorization. File analysis must consume validated datasets and explicit units; no legacy sales AgentTaskRequest expansion is implied. No UI upload, API, storage, migration, model, chart or report was implemented here. Feature Value Gate remains Approve for the local synthetic foundation; public/customer intake remains Hold.

## Final local test record

- Full offline suite: **437/437 PASS** (414 existing + 23 dataset tests), zero skips/failures; final run 14.163 seconds.
- Existing isolated PostgreSQL suite: **110/110 PASS**, zero skips/failures; 51.027 seconds. No application migration or database configuration change.
- Synthetic workflow benchmark: all preregistered engineering targets above passed; no paid calls. CLI stdout contains only explicitly generated synthetic examples.
- `git diff --check`: clean. Scoped changed/untracked file scan found no real local secret values, key patterns, private absolute paths, `.env`, backup, cache or private evidence. Scan is bounded to this change, not a claim of universal secret detection.
- Existing frontend, Bridge, Agent, API, dependencies, flags, prompts and historical evaluation files have no diff. Baseline HEAD remains `74940fc`; no commit/push performed.

Changed-file inventory:

- New `backend/datasets/__init__.py`, `models.py`, `parser.py`, `service.py`: separate contracts, deterministic parsing, bounded local process and redacted preview.
- New `tests/dataset_fixtures.py`, `tests/test_dataset_parsing.py`: generated OOXML fixtures and positive/adversarial/resource/recovery checks.
- New `scripts/verify_dataset_foundation.py`: reproducible synthetic workflow benchmark.
- New `docs/pv2-01-file-parsing.md`, `docs/pv2-01-synthetic-workflow.json`: preregistration, contract, limitations and explicit synthetic evidence.
- Updated `README.md`, `docs/product-roadmap-v2.md`: additive implementation status/index only, user acceptance pending.


## Final review addendum — 2026-11-13

Review retained the original synthetic workflow evidence and prior test record. The supported XLSX subset additionally rejects phonetic annotations (rather than concatenating pronunciation into values), nonzero row/column inherited styles (rather than bypassing ambiguity review), and unknown sheetData children. Cell-local nonzero styles still require review. Empty definedNames, strict OOXML namespaces, heterogeneous sheet schemas and many otherwise valid Excel features remain unsupported. No general Excel interoperability claim is made.

Memory and timing limitations: Mac RSS is sampled and may overshoot; worker startup, IPC transfer and parent Dataset validation are outside a hard OS-enforced end-to-end deadline. RLIMIT_AS on other platforms has not been exercised by this Mac acceptance. Bounded bytes/results reduce exposure but do not replace deployment isolation or admission control. The parser never creates/extracts data files; cleanup covers child processes and IPC handles. Full Dataset/model dumps and Pydantic structured validation details must not be logged; fixed public ParseFailure codes are the safe error boundary.

README, Architecture and Product Roadmap now record local foundation completion while PV2-02 persistence remains unimplemented. No paid API, Figma, production migration or existing runtime/flag change is included.

Final rerun: **440/440 offline PASS** (12.778 s; three additional XLSX review tests) and **110/110 isolated database PASS** (48.997 s), without skips. The initial database attempt failed because Docker daemon was stopped; starting existing Docker/PostgreSQL restored health and the full rerun passed. No volume removal, configuration edit or production migration was used. The synthetic workflow rerun exactly matched the original JSON; that evidence was not overwritten. Scoped review covered all 12 changed/new files and found no real secret, private absolute path, backup or unexpected runtime/frozen-asset diff. Architecture is now included in the changed-file inventory. Local PV2-01 is complete; PV2-02 remains planned only.
