# Phase 5.8a — Workspace v4.1 visual parity & UX QA

Date: 2026-10-26 (Australia/Melbourne). Status: implementation and local QA complete; **awaiting user visual acceptance**. No Phase 5.8b API integration, paid call, commit or push.

## Reference and evidence

Design baseline: supplied v4.1 bilingual HTML, v4.1 handoff, desktop/mobile/Settings screenshots, and `phase5_8a_workspace_visual_parity_and_ux_qa.md`. The newly attached HTML SHA-256 is `b717e2099cce6a45d0d80a5094a30a8c9a4da915b2df7a0dcb4cce89a34eec38`, identical to the prior supplied v4.1 reference. Attachment recommendations were treated as design input within the user's explicitly authorized scope.

The review package contains `index.html` with side-by-side **reference / before / after** images for Login, Register, Overview, Analysis & Chat, Visualizations & Reports and Settings: 6 routes × 2 languages × 2 widths (1440 / 390) × 3 stages = **72 screenshots**. Four additional interaction images cover mobile navigation, inline error, long transcript and 768px Settings. Screenshot SHA-256 values and capture manifests accompany the package. Earlier iteration screenshots are retained locally, not substituted for the final set.

Capture viewport heights: desktop 1000px, mobile 844px; full-page PNGs preserve below-fold content rather than crop away problems. All frontend captures come from the actual `frontend/workspace/` server; references come from a separate local rendering of the attached HTML at identical dimensions. Reference screenshots are labelled and are never presented as implementation output.

## Concrete comparison and changes

| View | Before | Implemented refinement | Deliberate difference / remaining gap |
|---|---|---|---|
| Login/Register | Heading and warning dominated; password toggles below fields; primary and secondary actions competed | Branded split layout, accent headline, eyebrow/subtitle, inline password controls, full-width primary action, text recovery/switch links, distinct demo disclosure and skip-login path | Demo placeholders intentionally discourage real credentials; extra disclosure adds height. No registration validation, session or recovery service |
| Overview | Oversized empty hero, three action cards with weak hierarchy | Sample chart illustration in hero, three compact summary cards, separate quick-start and sample-recents panels | Recents explicitly remain examples; no fabricated personal activity, connected status or account data |
| Analysis & Chat | Two columns, large plain composer, no result context | Source panel + transcript/composer + result canvas at 1440; neutral sample badges, wrapped source chip, example question insertion, explicit no-task state, inline empty-input error | No fake queued/running timer or successful execution. Sample chart is explicitly independent of the question; no general intent classifier |
| Visualizations & Reports | Large KPI cards and one oversized chart; little explanatory structure | Compact KPI row, correctly proportioned Q1/Q2 bars, findings and limitations alongside chart, source annotation, secondary related-report card | No meaningless region progress bars, invented validation badge or export success. Full report retains original language and independent content identity |
| Settings | Two sparse cards with small dropdowns | Bordered grouped panels, language choice buttons with pressed state, live UI preview, separate answer/report controls, future preferences rows | No model/tool/budget settings. Report preference remains design-only, not a translation job |
| Shared shell | Ungrouped navigation, plain branding, dense sidebar copy | Seven unchanged destinations with symbols/group labels, stable active state, breadcrumb, bottom demo identity, responsive drawer | Effective source CSS sidebar width is 262px (later v4 override), despite brief mentioning original 242px. We follow the rendered v4.1 reference |
| Mobile / accessibility | Existing basic stacking and focus support | Readable one-column panels, 44px targets, drawer focus containment/Escape/scroll lock/current-route close, clear form errors and translated control labels | Long pages remain vertically scrollable by design. Complete screen-reader/browser-matrix audit is still future work |

## Tokens and boundaries

`tokens.css` centralizes the supplied navy/blue/background/ink/muted/border/green/red/amber palette, 17px cards, shadow, spacing and control metrics. Accessible text/action variants are intentional: primary white-on-action contrast **5.25:1**, secondary text on white **5.80:1**, positive text **5.40:1**, error text **6.35:1**, and input border on white **3.11:1**. Decorative reference colors remain available. These measured pairs do not constitute certification of every possible contrast combination.

No fonts are downloaded or redistributed. Chinese uses local system fallbacks. Icons remain lightweight symbolic UI glyphs; replacing them with a finalized Figma icon component set is a remaining handoff item.

Existing semantic translation catalogs and locale state are reused. UI language changes only bindings; drafts, previous answers, source identifiers and report body remain unchanged. Example question insertion only fills an empty draft and does not submit. Only the UI locale is persisted. Demo credentials are not read, sent or stored and their DOM fields disappear on leaving the auth view.

The independent frontend retains `connect-src 'none'` and no API transport. The existing `/agent`, backend, database, Agent, flags, prompt releases and frozen evaluation assets remain unchanged. No fake HTTP results or real loading state is invented; Activity state chips are explicitly a design reference. Actual error/retry/polling behavior remains a Phase 5.8b prerequisite.

## Verification

- Existing Python offline suite: **409 PASS**; no database test/migration needed for frontend-only changes.
- Translation/state/security tests: **7 PASS**.
- Existing browser suite: **77 checks PASS**, including 12 routes × 2 locales × 1440/768/390 widths, locale persistence, answer-language independence, report invariance, escaping and network boundaries.
- Added UX suite: **119 checks PASS**, including labels and 44px control targets across 12 routes at desktop/mobile, one page heading, no horizontal overflow, independent confirm-password visibility, translated accessible labels, credentials discarded, drawer focus cycle/current-link close/Escape, inline error focus, long Chinese draft/transcript, long filename, 768px layout, immutable report, disabled export, token contrast and no external/API requests.
- Capture manifests: no horizontal overflow in before/reference/after matrices. Final images were visually reviewed for hierarchy, spacing, wrapping and consistency. No claim of pixel-identical parity or final user approval.
- The added UX test initially checked credentials before hash-route rendering completed. It now waits for rendered page identity; the credential-removal assertion was retained, not weakened.
- No paid API calls. Browser automation allowed only same-origin static GET requests and reported no page/console errors.

## Files and reproduction

Updated: `frontend/workspace/app.js`, `styles.css`, `index.html`, `locales/en.js`, `locales/zh-CN.js`, `tests/browser.mjs` (Settings now uses semantic choice buttons, with the original synchronization assertion retained).

Added: `frontend/workspace/tokens.css`, `tests/capture.mjs`, `tests/ux-qa.mjs`, `tests/build-visual-gallery.mjs`, this report. The original state/i18n implementation and protected modules are unchanged by this round. Roadmap and v4.1 implementation notes link this QA stage.

Serve `frontend/workspace/` only, bound to 127.0.0.1:8774, using the existing documented Python static preview command. Never serve repository root. Node scripts use an existing Playwright/browser installation; `PLAYWRIGHT_MODULE` supports an installed module path without adding runtime dependencies. Capture refuses to overwrite a stage; save earlier attempts separately. Run existing state/browser tests and `tests/ux-qa.mjs` against the loopback preview. Reference capture uses port 8775 with only the unpacked supplied design files.

## Acceptance and next handoff

Review the gallery and live preview, especially mobile density, auth messaging, and Analysis result-panel balance. Remaining visual differences are the simpler hero illustration, symbolic icons, safer neutral badges, accessible darker foregrounds, and reduced nonfunctional controls. Figma should adopt these deliberate differences or provide an agreed replacement, retaining v4.1 navigation and bilingual layouts.

Only after UI acceptance: Phase 5.8b Fake/Mock API bridge, with original idempotency key/payload retention, duplicate-submission lock, authoritative bounded polling, safe unknown-outcome recovery and source/result traceability. Real auth, upload/parsing, saved history, report translation/export and multi-tenant access remain separate backend work.
