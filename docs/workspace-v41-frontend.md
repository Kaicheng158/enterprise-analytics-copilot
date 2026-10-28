# Workspace v4.1 — independent bilingual frontend foundation

Current increment: [Phase 5.8a visual parity and UX QA](phase5-8a-workspace-visual-qa.md), completed locally on 2026-10-26 and awaiting user visual acceptance. The original foundation verification below remains historical; current browser/UX evidence and deliberate design differences are in that report. No Fake API bridge is connected.

Current design baseline: supplied `enterprise_analytics_workspace_v4_1_bilingual.html` and `enterprise_analytics_workspace_v4_1_handoff.md` (2026-10-25). The v4 HTML/handoff remain historical references. This supersedes the sales-only four-view UI navigation, not the existing backend contracts or protected releases. Figma editable layers have not been updated by this implementation.

## Scope and repository audit

The checked-out `main` baseline is `263629c`. Existing `frontend/agent/` remains an unchanged, loopback-only quarterly-sales console. The backend serves three explicit `/agent` assets; it does not serve arbitrary frontend modules. Therefore v4.1 lives independently under `frontend/workspace/`, without changing server routes, CORS, feature flags or database state.

| Capability | Actual state | v4.1 implementation / gap |
|---|---|---|
| Workspace navigation and UI language | Old console has one English task form | Seven v4.1 navigation entries, hash routes, separate Login/Register, contextual Add data/Preview/Full report; English/Chinese resource keys |
| Login/Register/My Account | No account/session/auth API; internal shared key is not user identity | Demo-only entry, password visibility, planned recovery, no input reading/transmission/storage; no authenticated claims |
| Settings | No preference API | UI locale saved locally; new-answer and report-language preferences remain separate in memory |
| Analysis & Chat | Strict quarterly-sales Agent API, separate `/chat` and `/rag/chat`; no unified chat/task language contract | Fixed synthetic demonstration, preserved conversation/draft; no API connection or automatic execution |
| Visualizations & Reports | Sales result exists; no report persistence/export API | Sample arithmetic chart and immutable English sample report; export disabled |
| Data Library / Add data | Controlled local text ingestion/RAG exists; no browser upload/CSV/XLSX asset API | Local filename only, static table/source/lineage preview; no file bytes read |
| Activity & Tasks | POST and per-ID GET exist; no list/history/reconciliation endpoint | Honest empty state and safe recovery guidance, no invented running tasks |

Navigation is **Overview → Analysis & Chat → Visualizations & Reports → Data Library → Activity & Tasks → Settings → My Account**. Add data is an action, not a duplicate navigation destination. Sales comparison is an existing sample template within the document-first product, not a claim of general-purpose analysis.

## Architecture and language semantics

- Dependency-free native ES modules: `app.js` composes DOM components; `state.js` separates content from UI; `i18n.js` resolves semantic keys from `locales/en.js` and `locales/zh-CN.js`. No DOM text walking, MutationObserver or source-text lookup maps.
- Namespaced keys cover navigation, auth, overview, analysis, source/preview, charts/report UI, library, task states, settings, account and safe error messages. Catalog parity and placeholders are tested; unknown locale falls back to English. Missing keys throw during development instead of silently leaking key IDs.
- Locale updates only registered UI bindings; inputs, draft, existing answers, sample data and report body remain intact. Dynamic content uses `textContent`, not HTML interpolation.
- Only `analytics-ui-locale-v41` containing `en` or `zh-CN` is persisted. Storage denial falls back to session-only operation. No identity, credential, filename, prompt or report content is stored.
- `answerLanguage` affects only the next fixed synthetic reply. Auto language detection is a demo heuristic, not a real model instruction policy. `reportLanguage` is a design preference without a generation API. The sample report has independent `language: en` and never silently translates.
- Data fields (`period`, `region`, `revenue_usd`), IDs, filenames and monetary source values remain unchanged. Future date/number presentation should use `Intl`; precise business arithmetic remains server-owned.
- CSP denies all `connect-src`, form submission, external scripts and objects. No internal key input, backend transport, paid provider or authentication exists in this independent preview.
- Mobile navigation uses inert background, focus trapping, Escape/close, current-page marking and a skip link. Browser Back/Forward uses hash routing. No extra navigation is added.

This is a bounded UI foundation, not a pixel-complete port of every demonstration interaction: no simulated task timer, report-depth selection, natural-language intent classifier or false upload success is included. This avoids implying real backend capabilities. Existing `/agent` remains available under its existing separately authorized configuration.

## Run and verify

From repository root, using the existing Python virtual environment:

```sh
analytics-agent-env/bin/python -m http.server 8774 --bind 127.0.0.1 --directory frontend/workspace
```

Open `http://127.0.0.1:8774/#login`. Serve only this directory, never repository root (which contains `.env`). This is a localhost preview, not a deployable identity service. No flags need to be enabled. Stop with Ctrl-C.

With Node available:

```sh
node --test frontend/workspace/tests/state.test.js
node frontend/workspace/tests/browser.mjs
```

The browser harness requires an existing Playwright installation and browser. `PLAYWRIGHT_MODULE` can point to an installed module; `BROWSER_CHANNEL=chrome` uses an existing Chrome. It rejects external/API requests and tests twelve routes × two locales × 1440/768/390 widths, overflow, password controls, drawer navigation, locale synchronization/persistence, draft retention, independent answer language, immutable report text and HTML-safe rendering. Optional `SCREENSHOT_DIR` writes local synthetic screenshots outside repository evidence.

## Existing API boundary and integration prerequisites

Do not send UI/answer/report locale fields to the current strict `AgentTaskRequest`. It accepts only `task`, `baseline_period`, `comparison_period`, `region`; caller identity/scope remain server-owned. POST requires `X-Internal-Agent-Key` and `Idempotency-Key`. GET by task UUID returns authoritative status, step counts, state version and optional source-backed result. There is no task-list endpoint, named-step stream, auth probe or report translation contract.

Before wiring this shell to the API, complete a separately tested transport/state integration:

1. Create one immutable payload/idempotency key per logical submission; lock duplicate sends. Locale changes never submit or regenerate a key.
2. Preserve unknown POST outcomes, key and payload. No automatic POST retry or blind rerun. Recover using the same key/body under an explicit policy; query a known task ID via GET.
3. One bounded polling owner per task, cancellation of browser waits, monotonic `state_version`, and no stale-response overwrite. Timeouts mean unknown/paused, not server cancellation or failure.
4. Map verified HTTP/domain errors to safe catalog messages. Handle 422 arrays without displaying raw inputs, headers, credential echoes or arbitrary backend message text.
5. Preserve `needs_reconciliation` as a stopped state requiring authorized review; no retry button for unknown side effects. Keep shared key in memory only.
6. Preserve Decimal strings, source relation, dataset version, task ID and region/currency semantics. Chart labels may translate; business data may not.

These are still pending for the unchanged legacy console. The new static shell does not claim to fix its network/idempotency behavior by avoiding network calls.

## Figma / frontend handoff sequence

1. Recreate v4.1 as editable frames in the existing Figma file; retain historical frames. Review this bounded implementation against the supplied HTML, not the superseded sales-only brief.
2. Tokenize navy/blue/neutral/warning colors, 17px cards, type scale, spacing, 44px targets; build navigation, auth fields/password control, locale selectors, buttons, cards, chart/source panels and empty/error variants. Verify contrast and Chinese line-height with design review.
3. Deliver EN/ZH desktop 1440 and mobile 390 routes, drawers, focus states, overflow tables, long filenames, credential-demo notices and disabled export. Maintain separate content-language annotations on conversations/reports.
4. Implement the safe Fake Agent API bridge only after its integration boundary is authorized; reuse existing task and runtime contracts. Resolve deployment/same-origin delivery without silently changing backend routes or CSP.
5. Add real upload/CSV/XLSX parsing, verified dataset APIs, report persistence and identity in separate backend workstreams. PDF/DOCX, connectors, export, RAG task integration and multi-tenant access remain later work.

No backend, database, flags, frozen prompts or evaluation assets are part of this change. No commit/push or paid call is authorized in this round.

## Verification record — 2026-10-25

- Existing Python offline suite: **409/409 PASS**. Database tests were not rerun because this round changes no backend, persistence or database integration.
- New Node resource/state/boundary suite: **7/7 PASS**.
- Chrome browser harness: **77 checks PASS**, including 72 route/locale/viewport combinations (12 routes × 2 locales × 3 widths), plus interaction checks; zero page/console errors and zero external/backend requests. Desktop/mobile screenshots were visually inspected locally.
- The initial browser pass exposed a test navigation race and a `data:` favicon CSP violation. The harness now waits for rendered route identity; the favicon is a same-origin SVG. Assertions and CSP restrictions were retained.
- `git diff --check` passed; tracked backend, legacy frontend, requirements and test/evaluation assets have no changes. New files were checked for key patterns and personal absolute paths; none found. No credentials or synthetic private evaluation evidence were copied.
- This is browser smoke coverage, not a complete accessibility, production authentication or API integration audit. New UI is not exposed by the existing FastAPI routes and has not been published or committed.



## Phase 5.8a closeout — 2026-10-28

The incremental frontend refinement is accepted for local closeout after the final Login/Register detail review. The current implementation has one heading, one Demo entry, a low-weight no-auth safety boundary, token-aligned eye/eye-off controls and the shared bilingual text brand. Figma native Auth synchronization and exact native-frame comparison remain pending; no claim of completed Figma synchronization is made. Phase 5.8b transport work has not started.

Product Vision v2 的产品链路、阶段依赖与数据边界分别见 [愿景](product-vision-v2.md)、[Roadmap v2](product-roadmap-v2.md) 和 [架构初稿](project-data-architecture-v2.md)。本地 Workspace 继续保持 Demo 状态，不能把规划中的文件分析、真实身份或项目协作描述为已实现。
