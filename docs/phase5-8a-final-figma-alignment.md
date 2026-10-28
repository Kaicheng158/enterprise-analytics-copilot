# Phase 5.8a Final Figma Alignment

Date: 2026-10-27. Status: implementation and local checks complete; awaiting user visual acceptance. No commit/push or API integration.

## Approved design decisions implemented

1. Mobile has four persistent core destinations: Overview, Analysis & Chat, Visualizations & Reports, Data Library. Short visual labels preserve space; accessible names retain full page names. More opens the existing keyboard-trapped drawer; Tasks, Settings and Account remain reachable alongside all seven entries. The drawer makes background content, including bottom navigation, inert.
2. Login/Register use a desktop 45% brand / 55% form split with an explicitly synthetic chart. At 390px, the brand panel is removed for a simple single-column form. Locale control, Demo warnings and no-auth behavior remain. Inputs are never read or transmitted.
3. Analysis starts with source and output-language settings in the right panel. A user-requested Demo answer changes it to a clearly labeled independent sample visualization, not a real task result. There is no actual successful Agent state or fabricated completed task. UI locale changes preserve messages and chosen output language. Reload resets transient Demo state.
4. Task status remains “No task submitted.” Completed examples are not promoted to actual status. Real result-driven behavior is deferred until an authorized API integration exists.

## Evidence basis and limits

Sources: v4.1 handoff and prior QA; user-confirmed four decisions; local v5 builder source; previously confirmed Figma page inventory and canvas overview.

Figma: https://www.figma.com/design/WPjZjK4xZtfRx47ekBjmeh?node-id=5-2

The prior native metadata request reached the Starter MCP quota. Exact native frame spacing, type metrics, asset placement, post-import edits and full pixel parity remain **pending manual verification**. No guessed frame measurements are represented as inspected. The approved mobile navigation intentionally differs from the v5 builder's Settings bottom tab: Data Library is now the fourth core entry, with Settings under More. Contrast-safe colors, 44px touch targets, actual bilingual resources and honest synthetic labels are retained rather than copied from unsafe or purely illustrative prototype states.

## Test results

- Python offline: 409 PASS.
- Translation/state/security unit tests: 7 PASS.
- Browser route/locale matrix: 77 PASS.
- Existing UX/accessibility checks: 119 PASS.
- Targeted long-transcript/header/skip-link checks: 21 PASS.
- New alignment suite: English and Chinese end-to-end scenarios PASS: four core destinations, three More destinations, current indicators, drawer background inertness, settings→Demo preview transition, explicit unsubmitted state, composer above fixed navigation, locale isolation, transient reload, desktop/mobile auth layout.
- Capture matrix: 10 routes × 2 languages × 2 widths = 40 full-page screenshots; no horizontal overflow. Routes: Login, Register, Overview, Analysis, Visualizations, Settings, Data Library, Tasks, Full Report, Account.
- No browser page errors or prohibited network requests in tested flows.
- No paid API or database tests/migrations were run. Database and real Agent behavior were not part of this frontend-only verification.

An initial new test caught the existing first-child CSS rule keeping the mobile brand panel visible. The selector was fixed and the new suite rerun successfully; assertions were not weakened. A later test strengthens exact no-submission status and bottom-navigation overlap checks.

## Changed files in this alignment

- `frontend/workspace/app.js`: mobile core navigation, More label, auth chart, state-driven settings/sample panel and consistent report-language option.
- `frontend/workspace/styles.css`: brand split, mobile single column, persistent navigation, safe-area spacing.
- `frontend/workspace/locales/en.js`, `zh-CN.js`: matching navigation/settings labels.
- `frontend/workspace/tests/alignment.mjs`: new bilingual integration regression.
- `frontend/workspace/tests/browser.mjs`, `ux-qa.mjs`: scope existing drawer selectors now that navigation appears in two places.
- `frontend/workspace/tests/capture.mjs`: optional full ten-page capture, preserving reference/default matrix.
- This report; earlier reports remain historical snapshots.

## Visual acceptance checklist

- [x] Seven routes remain reachable on mobile; four core entries stay visible.
- [x] English/Chinese route title never letter-stacks in long conversations.
- [x] Desktop brand split and mobile single column with locale/Demo notices.
- [x] Analysis before/after Demo states are differentiated without fabricated execution.
- [x] Task state remains unsubmitted, chart and report remain synthetic.
- [x] Keyboard drawer and skip-link behavior, language isolation and safe text rendering preserved.
- [x] Backend, `/agent`, database, feature flags and frozen evidence unchanged.
- [ ] User visual acceptance of desktop/mobile screenshots.
- [ ] Native Figma detail comparison/manual verification when available.
- [ ] Physical mobile device/soft keyboard and native browser zoom review (automated CSS viewport stress is not a substitute).

Full-page screenshots show the fixed bottom bar at the original viewport boundary, not at the end of the long document; dedicated viewport captures illustrate the actual visible layout. This is screenshot behavior, not a mid-document navigation component.

## Closeout update — 2026-10-28

The user accepted the overall visual direction and requested the final Auth detail revisions, which now pass their targeted and existing frontend checks. Phase 5.8a is ready for Git closeout. Native Figma Auth synchronization, exact native-frame measurements and physical-device review remain pending and are not reported as completed.

## Next step

Stop for UI approval. Do not implement the Fake/Mock bridge or real task results until approved. Future integration must use server task state and provenance rather than message count; the current message-count branch is explicitly local Demo-only state.
