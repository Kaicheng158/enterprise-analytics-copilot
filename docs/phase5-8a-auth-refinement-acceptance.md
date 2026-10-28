# Phase 5.8a Auth Visual Refinement — 2026-10-28

Status: approved proposal A implemented locally; awaiting final visual confirmation. No commit/push.

## Implemented scope

- Shared `brand()` renders a translation binding: 数据分析助手 / Analytics Copilot. The temporary diamond is removed from auth and Workspace sidebar. Navigation structure is unchanged.
- The redundant auth-art descriptor “企业数据分析助手 / ENTERPRISE ANALYTICS COPILOT” is removed. Only the formal shared text brand remains.
- Auth-only cloud/sky/mist gradient, three synthetic metric/chart/insight cards, white form card. Actual chart renderer and existing component/color tokens are reused. No shared token changed.
- Login: 欢迎回来 / Welcome back with approved subtitle. Register: 体验注册流程 / Explore the registration demo, explicitly no real account creation. Switching links also say demo/preview.
- Account label replaces work email. `demo@example.com` is a placeholder, never a supported real account claim. Helper explicitly states no real authentication. No username/phone support is implied.
- Desktop split; 768px/390px single-column priority form. Register may scroll, preserving usable controls. Native language selector retains keyboard accessibility, larger width and padding.
- Fields are not credential forms. Entering Demo reads no input values, submits nothing, authenticates nobody and discards credential DOM. Locale switching only updates UI bindings. Password visibility remains local.

## Figma status and native handoff

Figma v5: https://www.figma.com/design/WPjZjK4xZtfRx47ekBjmeh

A read-only edit-availability check hit the Starter MCP call limit before creation. **No Figma page/frame was created or changed; no historical frame overwritten.** Actual frontend screenshots are not native Figma designs.

The complete original design specification is preserved in `auth-proposal-a-design-spec.md` (historical proposal wording); this acceptance document supersedes its register wording and describes the approved implementation. Native designer handoff:

1. Add a new page `03 · Auth Proposal A · 2026-10-28`; retain pages5:2 and5:1000 and original auth frames5:3/5:35.
2. Create12 frames: Login/Register ×EN/中文 ×1440/768/390. Use auto-layout for header/form stacks, and a separate relative composition frame for floating cards.
3. Define components: shared pure-text brand language variants, field/password visibility, language picker default/focus/open, primary Demo CTA, synthetic badge, Metric/Chart/Insight cards. Reuse current Workspace token names and semantic roles. Do not add fake authenticated/error/success states.
4. Use existing Inter/system/PingFang font family behavior; validate actual available Figma font metrics manually. Input46px, CTA48px, touch minimum44px; card17px and controls10px radius; existing shadow. No global color substitution.
5. Decorative colors only: cloud#F8FBFF, sky#E5F1FF, mist#F1EFF9. Action#4164D4, ink#1A2944, secondary#526782, chart renderer colors, line#E6EBF3 and warning pair remain existing Workspace values.
6. Desktop composition: main max1312px,32px inner gutters,80px gap; brand/form1.15fr/1fr; form max500px/padding36×40. Floating cards metric290px at10/0, chart360px at112/177, insight340px at0/449, subtle rotations -1.5/.7/-.6 degrees; verify no content overlap. Under1100px reduce gap32px and chart width330px; under1000px remove decoration and use centered form. 390px gutters16px/card padding26×22.
7. Register title and links must use current Demo copy above, not historical “Create your account”. Prototype entry links only navigate to explicit Demo Overview. Native Figma synchronization remains pending quota availability/manual import.

## Verification

- Offline Python:409 PASS (no paid API).
- Frontend unit/i18n/security:7 PASS.
- Browser route/locale:77 PASS.
- UX,44px targets, contrast, keyboard:119 PASS.
- Mobile long conversation/header regression:21 PASS.
- Existing bilingual alignment flows:PASS.
- New auth refinement matrix:12/12 PASS at1440/768/390, EN/中文, Login/Register. Includes shared brand on Overview, no icon, explicit register disclaimer, language preservation, no overflow, minimum44px controls, credential-value getter trap during Demo entry, no credential persistence, no forbidden requests/page errors.
- Final auth screenshots:12; complete broader visual capture:40. Login→Overview comparisons use current actual screenshots on both sides.
- Text/control contrast inherited checks: action white5.25:1, ink white14.54:1, secondary white5.80:1, positive white5.40:1, control outline white3.11:1. New colors are decorative backdrop only.
- Git whitespace check passed. Scoped key/private-key/local-absolute-path scan had no matches. No tracked diffs in backend,eval,frontend/agent or dependency configuration.

A first visual review identified metric-card content partly behind the chart. Removed the redundant amount-range line (amounts remain on the chart) and compacted metric spacing; final targeted auth checks and screenshots rerun. Prior evidence remains outside the final bundle as earlier iteration evidence.

## Files

`frontend/workspace/app.js`, `styles.css`, `locales/en.js`, `locales/zh-CN.js`, new `tests/auth-refinement.mjs`; `docs/auth-proposal-a-design-spec.md` and this report. Earlier uncommitted phase work remains preserved.

## Remaining limits

Native Figma sync and exact font/render comparison pending. Desktop Chrome automation is not a physical-device keyboard/VoiceOver audit. No real login/register, recovery or account API exists. All backend/Agent/database/flags/frozen assets unchanged. Stop for visual approval before any further integration.

## Local visual-review handoff

The approved Workspace frontend is served from `frontend/workspace/` on loopback only. Review Login at `http://127.0.0.1:8774/#login`, Register at `#register`, and Overview at `#overview`. The language selector is available on Login/Register and the Workspace top bar. Enter demo workspace performs local hash navigation only; no credential values are read or submitted. The Figma native-sync todo and the handoff specification above remain open.

## Final detail revision

- Removed the duplicate Login/Register kicker so each page has one primary heading.
- Consolidated the authentication boundary into one low-weight `auth-safety-note`. It is isolated from the form content so a future real-auth implementation can remove that single component after its security copy and behavior are replaced.
- Replaced Show/Hide text with token-aligned eye/eye-off SVG controls. The controls remain 44×44px, keyboard operable, visibly focused, and expose localized accessible names plus `aria-pressed` state.
- Removed the secondary “Preview without logging in” action because it duplicated the primary “Enter demo workspace” hash navigation. One explicit Demo entry remains.
- The Demo route still reads, sends, validates and stores no credential values. No backend, database, Agent, API, Feature Flag or frozen evidence was changed.

Git closeout review found no blocking frontend or Demo-boundary issue. Phase 5.8a is accepted locally; native Figma synchronization remains an explicit pending handoff item.
