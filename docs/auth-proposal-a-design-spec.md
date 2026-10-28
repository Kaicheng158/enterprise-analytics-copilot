# Login / Register Visual Refinement — proposal A

2026-10-27 · Design only · awaiting visual approval

## Evidence and Figma status

Reviewed existing Workspace tokens, styles and design documentation, latest actual frontend screenshots, Figma v5 native metadata (Login 5:3, Register 5:35, Overview 5:72), and the supplied BrainX image. The further Figma component/font discovery call hit the Starter MCP limit. **No Figma nodes were changed.** These are standalone rendered design proposals and manual Figma handoff specifications, not screenshots of new Figma frames or an implemented frontend.

BrainX informs atmosphere only: cloud-white backgrounds and light floating cards. No BrainX name, logo, illustrations, metrics or exact composition is reused. Three distinct cards show the existing synthetic Q1 USD 400 → Q2 USD 600 sample: metric, two-column chart and evidence-limited AI insight preview. No causal claim, live AI or task completion is implied.

## Shared palette

| Token / color | Login + Register | Existing Workspace |
|---|---|---|
| action #4164D4 | Primary CTA, links, focus, emphasized headline | Existing action/focus token; unchanged |
| blue #4D74ED | Main Q2 bar | Existing chart/brand emphasis |
| blue2 #729AFF | Secondary Q1 bar | Existing secondary blue |
| ink #1A2944 | Brand, headings, numbers | Existing foreground |
| text-secondary #526782 | Labels, explanations, source metadata | Existing accessible secondary text |
| white #FFFFFF | Form and floating cards | Existing white surfaces |
| bg #F5F7FB | Transition/gallery surface | Existing workspace background |
| line #E6EBF3 | Card outlines | Existing decorative separators |
| control-border #8393AD | Inputs and language switch | Existing accessible control outline |
| positive-text #087959 | +50% sample change | Existing positive value |
| warning-text #80571C / surface-warning #FFFAEE | Demo warning | Existing warning pair |
| NEW auth-cloud #F8FBFF | Background only | No change |
| NEW auth-sky #E5F1FF | Soft upper-left background gradient only | No change |
| NEW auth-mist #F1EFF9 | Subtle lower-center gradient only | No change |

No shared token changes proposed or performed. New colors are scoped to decorative auth backgrounds and never replace action/status/chart colors. Do not use muted #71819A for small essential copy. The dark existing Overview sidebar/hero stays unchanged; the form and chart system make the transition coherent without a site-wide recolor.

## Typography, layout and components

Use current CSS font stack: Inter, -apple-system, BlinkMacSystemFont, Segoe UI, PingFang SC, Microsoft YaHei, sans-serif. Figma should use Inter for Latin and PingFang SC for Chinese if available; fallback substitution must be checked manually, not described as identical rendering.

1440px: header horizontal padding 64px, vertical 32px. Pure text brand 22px/700; no mark/icon. Chinese brand 数据分析助手, English Analytics Copilot. Main width max1312px, inner padding32px, 80px column gap, columns1.15fr/1fr. Form max500px, padding36px40px, radius17px, line1px, existing shadow `0 14px 40px rgba(17,35,66,.055)`. Heading30px; brand story48px. Form content is vertically stacked, not fixed-height text boxes. Cards use existing radius17px and shadow. Inputs46px high/radius10px, primary48px high. Language control44px high,16px horizontal padding,20px gap to chevron.

Three cards: metric270px at left12/top0 with -2° decorative rotation; chart360px at left126/top123 with1°; insight340px left0/top390 with -1°. Height515px overall. Rotation is decorative only, never motion. Preserve visible source metadata. The source is static `sales_2026_sample.csv`, not a database-backed authenticated source.

768px: single column, max600px with32px gutters; remove decorative card/story region. 390px:16px outer gutters,22px form padding,18px text brand,28px heading; language control remains at top. Registration may scroll vertically; never shrink controls to fit one screen. No fixed-position auth controls.

## Copy and account support

Login zh: 欢迎回来 / 登录 Analytics Copilot，继续探索数据中的答案。
Login en: Welcome back / Sign in to Analytics Copilot and continue exploring answers in your data.
Register zh: 创建你的工作台 / 探索演示工作台，不会创建真实账号。
Register en: Create your workspace / Explore the demo. No account will be created.

Field label: 账号 / Account. Display example `demo@example.com`. Helper: 邮箱格式仅作演示；当前不支持任何真实账号认证。 / Email format example only. No real account is supported yet.

The backend supports no real account format today. Email-format UI is a proposed future design, not implemented validation. Username/phone support must not be advertised. Demo CTA: 进入演示工作台 / Enter demo workspace, never “登录成功” or “注册成功”. Do not read, validate, submit or save credentials. Recovery remains Planned. No fabricated privacy agreement or acceptance checkbox.

Production implementation after approval should map copy to namespaced translation keys (`auth.account`, `auth.accountHint`, `auth.welcome`, `brand.name`, etc.) instead of DOM text replacement. UI locale must preserve input, answer/report language and existing content.

## States for Figma handoff

Create a separate proposed page; retain v5 historical frames. Frame matrix: Login/Register × EN/中文 ×1440/768/390 =12. Use auto-layout for headers, form stacks and card content, absolute placement only for decorative floating-card composition.

Components: text brand (language variants); language control (default/focus/open); form field (default/focus/password visible); primary Demo button (default/hover/focus); sample badge; source footer; metric/chart/insight card. Registration confirmation field shares password behavior. For this design-only preview, fields and links are static visual specimens, not operable authentication controls. Prototype interactions should navigate only to clearly marked Demo scenes, never simulate real success.

No actual validation/server errors are claimed. Future account errors, loading, rate limit and credentials policy require real auth contract before designing behavior as available.

## Accessibility verification

Measured ratios: action/white5.25:1; ink/white14.54:1; secondary/white5.80:1; positive/white5.40:1; warning/background6.11:1; secondary/sky5.07:1; ink/mist12.78:1. Control-border/white3.11:1. Thin card boundaries are decorative, not sole control indicators.

12 rendered variants have no horizontal overflow at1440/768/390. Font stack is identical to Workspace; actual native font substitution in Figma remains a manual check. Static proposal checks are not application regression or a full accessibility audit. On implementation:44px targets, visible focus, associated labels, keyboard order, password-show accessible name, reduced-motion preference and no credential storage.

## Login → Overview comparison

Side-by-side boards show proposed Login against an unchanged actual Overview screenshot. Common action blue, typography stack, foreground, cards, chart palette and synthetic labels create continuity. Deliberate difference: auth has a soft ambient gradient; Overview retains operational sidebar/hero. Existing Overview still has its prior logo and English brand. If this text-only bilingual brand is approved, a narrowly scoped shared brand component update should follow; no tokens or navigation need changing. Do not claim that brand change is already implemented.

## Approval and next step

Approve the atmosphere, three-card composition, brand wording, form density and email-format Demo wording first. Then implement only auth plus the approved shared text-brand scope and rerun existing frontend regressions. No backend, API, database, flags, Agent or frozen evaluation changes; no paid API; no commit/push in this design round.
