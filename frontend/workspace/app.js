import { translate, readLocale, saveLocale } from './i18n.js';
import { createState, navigation, routes, sampleRows, addDemoAnswer, setUiLocale } from './state.js';

let storage;
try { storage = window.localStorage; } catch { storage = null; }
const state = createState(readLocale(storage));
let bindings = [];
const root = document.querySelector('#app');
const t = (key, values) => translate(state.locale, key, values);
function el(tag, className = '', text) {
  const node = document.createElement(tag);
  node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
function text(tag, key, className = '', values) {
  const node = el(tag, className);
  const update = () => { node.textContent = t(key, values); };
  bindings.push(update); update(); return node;
}
function attr(node, name, key) {
  const update = () => node.setAttribute(name, t(key));
  bindings.push(update); update();
}
function button(key, action, secondary = false) {
  const node = text('button', key, secondary ? 'button secondary' : 'button');
  node.type = 'button'; node.addEventListener('click', action); return node;
}
function icon(symbol, className = 'icon') {
  const node = el('span', className, symbol); node.setAttribute('aria-hidden', 'true'); return node;
}
function passwordIcon(isHidden) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('class', 'password-icon');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('focusable', 'false');
  const add = (tag, attributes) => {
    const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
    Object.entries(attributes).forEach(([name, value]) => node.setAttribute(name, value));
    svg.append(node);
  };
  add('path', { d: 'M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6Z' });
  add('circle', { cx: '12', cy: '12', r: '2.5' });
  if (!isHidden) add('path', { d: 'm3 3 18 18' });
  return svg;
}
function brand() {
  return text('div', 'brand.name', 'brand');
}
function sectionHead(key, badgeKey) {
  const head = el('div', 'section-head'); head.append(text('h2', key));
  if (badgeKey) head.append(text('span', badgeKey, 'badge neutral'));
  return head;
}
function changeLocale(locale) {
  setUiLocale(state, locale);
  const saved = saveLocale(storage, state.locale); localize();
  if (!saved) announce('errors.storage');
}
const route = () => routes.includes(location.hash.slice(1)) ? location.hash.slice(1) : 'login';
function go(page) { location.hash = page; }
function localeSelect(id) {
  const select = el('select'); select.id = id; attr(select, 'aria-label', 'settings.ui');
  for (const [code, label] of [['en', 'English'], ['zh-CN', '简体中文']]) {
    const option = el('option', '', label); option.value = code; select.append(option);
  }
  bindings.push(() => { select.value = state.locale; }); select.value = state.locale;
  select.addEventListener('change', () => {
    changeLocale(select.value);
  }); return select;
}
function localize() {
  document.documentElement.lang = state.locale; document.title = t('app.title');
  bindings.forEach(update => update());
}
function announce(key) {
  const node = document.querySelector('#notice');
  const dismiss = button('ui.dismiss', () => node.replaceChildren(), true);
  node.replaceChildren(text('span', key), dismiss);
}
function heading(main, key, description, kicker) {
  if (kicker) main.append(text('p', kicker, 'kicker'));
  const title = text('h1', key); title.tabIndex = -1; title.id = 'page-title';
  main.append(title);
  if (description) main.append(text('p', description, 'description'));
}
function link(key, page, secondary = false) { return button(key, () => go(page), secondary); }
function card(...children) { const node = el('section', 'card'); node.append(...children); return node; }
function field(id, key, type = 'text') {
  const wrapper = el('div', 'field'); const label = text('label', key); label.htmlFor = id;
  const input = el('input'); input.id = id; input.type = type; input.autocomplete = 'off';
  input.spellcheck = false; input.maxLength = 128;
  attr(input, 'placeholder', 'ui.authPlaceholder');
  const control = el('div', type === 'password' ? 'password-control' : 'input-control');
  control.append(input); wrapper.append(label, control);
  if (type === 'password') {
    const toggle = el('button', 'password-toggle'); toggle.type = 'button';
    const update = () => {
      toggle.replaceChildren(passwordIcon(input.type === 'password'));
      toggle.setAttribute('aria-label', t(input.type === 'password' ? 'auth.show' : 'auth.hide'));
      toggle.setAttribute('aria-pressed', String(input.type !== 'password'));
    };
    toggle.setAttribute('aria-controls', id);
    bindings.push(update); update();
    toggle.addEventListener('click', () => { input.type = input.type === 'password' ? 'text' : 'password'; update(); });
    control.append(toggle);
  }
  return wrapper;
}
function auth(main, page) {
  const header = el('header', 'auth-header'); header.append(brand(), localeSelect('auth-locale'));
  const layout = el('div', 'auth-layout');
  const art = card(); art.classList.add('auth-art');
  // Separate text bindings preserve styled emphasis without HTML strings.
  const headline = el('h2'); headline.append(text('span', 'ui.authLead'), text('span', 'ui.authAccent', 'accent'));
  art.append(headline, text('p', 'auth.description'));
  const floating = el('div', 'auth-floating');
  const metricCard = card(sectionHead('auth.sampleMetric', 'ui.sampleOnly'), el('p', 'metric', 'USD 600'), el('p', 'positive', '+50% · Q1 → Q2'));
  metricCard.classList.add('auth-float-metric');
  const chartCard = card(sectionHead('auth.sampleChart'), chart(), el('p', 'small muted source-id', 'sales_2026_sample.csv'));
  chartCard.classList.add('auth-float-chart');
  const insight = card(sectionHead('auth.sampleInsight', 'ui.sampleOnly'), text('p', 'auth.sampleInsightBody', 'small muted'));
  insight.classList.add('auth-float-insight'); floating.append(metricCard, chartCard, insight); art.append(floating);
  layout.append(art);
  const pane = card(); pane.classList.add('auth-pane');
  heading(pane, page === 'register' ? 'auth.register' : 'auth.login', page === 'register' ? 'auth.registerSubtitle' : 'ui.authSubtitle');
  const fields = el('div', 'auth-fields');
  if (page === 'register') fields.append(field('demo-name', 'auth.name'));
  const account = field('demo-email', 'auth.email', 'text');
  const accountInput = account.querySelector('input'); accountInput.inputMode = 'email';
  accountInput.placeholder = 'demo@example.com';
  bindings.push(() => { accountInput.placeholder = 'demo@example.com'; });
  const hint = text('p', 'auth.accountHint', 'small muted'); hint.id = 'account-hint';
  accountInput.setAttribute('aria-describedby', hint.id); account.append(hint);
  fields.append(account, field('demo-password', 'auth.password', 'password'));
  if (page === 'register') fields.append(field('demo-confirm', 'auth.confirm', 'password'));
  // These are deliberately not a credential form. No values are read or validated.
  pane.append(fields);
  if (page === 'login') { const forgot = button('auth.forgot', () => announce('auth.reset'), true); forgot.classList.add('text-button', 'forgot'); pane.append(forgot); }
  const enter = link('auth.enter', 'overview'); enter.classList.add('wide'); pane.append(enter);
  const foot = el('div', 'auth-foot');
  const switcher = link(page === 'register' ? 'auth.switchLogin' : 'auth.switchRegister', page === 'register' ? 'login' : 'register', true); switcher.classList.add('text-button');
  foot.append(text('span', page === 'register' ? 'ui.already' : 'ui.newHere'), switcher);
  pane.append(foot, text('p', 'auth.notice', 'auth-safety-note'));
  layout.append(pane); main.append(header, layout);
}
function shell(page) {
  const layout = el('div', 'shell');
  const sidebar = el('aside', 'sidebar'); sidebar.id = 'navigation';
  sidebar.append(brand());
  const nav = el('nav'); attr(nav, 'aria-label', 'common.workspace');
  const owner = ['source', 'preview'].includes(page) ? 'analysis' : page === 'report' ? 'visualizations' : page;
  for (const item of navigation) {
    if (item === 'overview' || item === 'settings') nav.append(text('p', item === 'overview' ? 'ui.workspace' : 'ui.preferences', 'nav-group'));
    const anchor = el('a', 'nav-link'); anchor.href = `#${item}`;
    anchor.append(icon(({overview:'◫', analysis:'✧', visualizations:'▥', library:'▤', tasks:'◷', settings:'⚙', account:'♙'})[item]), text('span', `nav.${item}`));
    if (owner === item) anchor.setAttribute('aria-current', 'page');
    nav.append(anchor);
  }
  const bottom = el('div', 'side-bottom'); const profile = el('div', 'profile'); const profileText = el('div');
  profileText.append(text('strong', 'ui.visitor'), text('p', 'ui.session'));
  profile.append(icon('D', 'avatar'), profileText); bottom.append(profile, text('p', 'common.boundary', 'sidebar-note'), link('common.exit', 'login', true));
  sidebar.append(nav, bottom);
  const backdrop = el('button', 'backdrop'); backdrop.type = 'button'; attr(backdrop, 'aria-label', 'common.close');
  const content = el('div', 'content'); const top = el('header', 'topbar');
  const menu = button('ui.more', () => setMenu(true), true); menu.id = 'menu';
  menu.setAttribute('aria-controls', 'navigation'); menu.setAttribute('aria-expanded', 'false');
  const close = button('common.close', () => setMenu(false), true); close.classList.add('mobile-close');
  sidebar.prepend(close);
  const setMenu = open => {
    layout.classList.toggle('menu-open', open); menu.setAttribute('aria-expanded', String(open));
    document.body.classList.toggle('drawer-open', open);
    content.inert = open; sidebar.inert = !open && matchMedia('(max-width: 760px)').matches;
    if (open) close.focus(); else menu.focus();
  };
  sidebar.inert = matchMedia('(max-width: 760px)').matches;
  nav.addEventListener('click', event => {
    if (event.target.closest('a')?.hash === location.hash && layout.classList.contains('menu-open')) setMenu(false);
  });
  backdrop.addEventListener('click', () => setMenu(false));
  sidebar.addEventListener('keydown', event => {
    if (!layout.classList.contains('menu-open')) return;
    if (event.key === 'Escape') { event.preventDefault(); setMenu(false); }
    if (event.key === 'Tab') {
      const items = [...sidebar.querySelectorAll('button,a')];
      if (event.shiftKey && document.activeElement === items[0]) { event.preventDefault(); items.at(-1).focus(); }
      if (!event.shiftKey && document.activeElement === items.at(-1)) { event.preventDefault(); items[0].focus(); }
    }
  });
  const breadcrumb = el('div', 'breadcrumb'); breadcrumb.append(text('span', 'common.workspace', 'workspace-label'), icon('/', 'crumb-divider'), text('strong', `nav.${page}`));
  const ask = link('ui.ask', 'analysis'); ask.classList.add('header-ask');
  top.append(menu, breadcrumb, localeSelect('workspace-locale'), text('span', 'ui.demoShort', 'badge'), ask);
  const main = el('main', 'main'); main.id = 'main';
  const mobileNav = el('nav', 'mobile-primary'); attr(mobileNav, 'aria-label', 'ui.coreNavigation');
  for (const item of ['overview', 'analysis', 'visualizations', 'library']) {
    const anchor = el('a'); anchor.href = `#${item}`; attr(anchor, 'aria-label', `nav.${item}`);
    anchor.append(icon(({overview:'◫',analysis:'✧',visualizations:'▥',library:'▤'})[item]), text('span', `ui.short.${item}`));
    if (owner === item) anchor.setAttribute('aria-current', 'page');
    mobileNav.append(anchor);
  }
  content.append(top, main, mobileNav); layout.append(sidebar, backdrop, content); root.append(layout);
  return main;
}
function chart() {
  const box = el('figure', 'chart'); box.setAttribute('role', 'img'); attr(box, 'aria-label', 'visualizations.chart');
  const bars = el('div', 'bars');
  for (const [quarter, amount] of [['q1', '400'], ['q2', '600']]) {
    const column = el('div', `chart-column ${quarter}`); const bar = el('div', 'bar');
    bar.append(el('span', 'bar-value', `USD ${amount}`)); column.append(bar, el('span', 'bar-label', `2026-${quarter.toUpperCase()}`)); bars.append(column);
  }
  box.append(bars); return box;
}
function preview(main) {
  heading(main, 'preview.title', 'preview.description');
  const container = el('div', 'table-scroll'); const table = el('table');
  const caption = text('caption', 'common.sample'); const head = el('thead'); const row = el('tr');
  for (const label of ['period', 'region', 'revenue_usd']) row.append(el('th', '', label));
  head.append(row); const body = el('tbody');
  for (const values of sampleRows) { const row = el('tr'); values.forEach(value => row.append(el('td', '', value))); body.append(row); }
  table.append(caption, head, body); container.append(table);
  main.append(container, link('common.back', 'analysis'));
}
function preference(id, key, value, onChange) {
  const wrapper = el('div', 'field'); const label = text('label', key); label.htmlFor = id;
  const select = el('select'); select.id = id;
  const auto = text('option', id.endsWith('report-language') ? 'settings.autoReport' : 'settings.auto'); auto.value = 'auto'; select.append(auto);
  for (const [code, label] of [['en', 'English'], ['zh-CN', '简体中文']]) { const option = el('option', '', label); option.value = code; select.append(option); }
  select.value = value; select.addEventListener('change', () => onChange(select.value)); wrapper.append(label, select); return wrapper;
}
function metric(key, value, hintKey, positive = false) {
  const node = card(text('p', key, 'metric-label'), typeof value === 'string' ? el('p', `metric${positive ? ' positive' : ''}`, value) : value);
  node.classList.add('metric-card');
  if (hintKey) node.append(text('p', hintKey, 'small muted'));
  return node;
}
function quickLink(key, hintKey, page, symbol) {
  const node = el('button', 'quick-link'); node.type = 'button';
  const copy = el('span'); copy.append(text('strong', key), text('span', hintKey, 'small muted'));
  node.append(icon(symbol, 'quick-icon'), copy, icon('↗')); node.addEventListener('click', () => go(page)); return node;
}
function findings() {
  const node = card(sectionHead('ui.findings', 'ui.sampleOnly'));
  for (const [title, body] of [['visualizations.growth', 'ui.findingGrowth'], ['ui.region', 'ui.findingRegion'], ['ui.limitation', 'ui.findingLimit']]) {
    const item = el('div', 'finding'); item.append(text('h3', title), text('p', body)); node.append(item);
  }
  node.append(link('visualizations.open', 'report')); return node;
}
function renderPage(main, page) {
  if (page === 'overview') {
    heading(main, 'overview.title', 'overview.description', 'ui.overviewKicker');
    const hero = card(); hero.classList.add('hero'); const copy = el('div');
    const title = el('h2'); title.append(text('span', 'ui.heroLead'), document.createTextNode(' '), text('span', 'ui.heroAccent', 'accent'));
    const actions = el('div', 'actions'); actions.append(link('overview.start', 'analysis'), link('common.add', 'source', true));
    copy.append(text('p', 'ui.heroKicker', 'kicker'), title, text('p', 'overview.description'), actions);
    const art = el('div', 'hero-art'); art.setAttribute('aria-hidden', 'true');
    art.append(text('p', 'ui.previewChart', 'small'), el('strong', 'hero-number', 'USD 600'), chart(), text('span', 'common.sample', 'small'));
    hero.append(copy, art);
    const metrics = el('div', 'grid metrics'); metrics.append(metric('ui.sampleCount', '01', 'common.sample'), metric('ui.latestTask', text('p', 'ui.notRun', 'metric'), 'ui.noExecution'), metric('ui.latestChart', '+50%', 'common.sample', true));
    const quick = card(sectionHead('ui.quickStart'), text('p', 'ui.quickHint', 'muted'), quickLink('common.add', 'ui.sourceHint', 'source', '↑'), quickLink('ui.ask', 'ui.chatHint', 'analysis', '✧'), quickLink('nav.visualizations', 'ui.visualHint', 'visualizations', '▥'));
    const recent = card(sectionHead('ui.recent', 'ui.notHistory'));
    for (const [label, hint, dest, symbol] of [['library.data','ui.sampleRows','preview','CSV'], ['nav.visualizations','ui.visualHint','visualizations','▥'], ['nav.tasks','ui.noExecution','tasks','◷']]) recent.append(quickLink(label, hint, dest, symbol));
    recent.append(text('p', 'overview.note', 'info-note'));
    const grid = el('div', 'grid two overview-bottom'); grid.append(quick, recent); main.append(hero, metrics, grid);
  } else if (page === 'analysis') {
    heading(main, 'analysis.title', 'analysis.description', 'ui.analysisKicker');
    const guide = el('section', 'workflow-guide');
    guide.append(text('h2', 'ui.workflowTitle'), text('p', 'ui.workflowBoundary', 'small muted'));
    const steps = el('ol', 'workflow-steps');
    for (const key of ['ui.stepData', 'ui.stepQuestion', 'ui.stepAnalysis', 'ui.stepReport']) steps.append(text('li', key));
    guide.append(steps); main.append(guide);
    const grid = el('div', 'analysis-grid'); const context = card(sectionHead('analysis.context'), el('p', 'source-chip', 'CSV · sales_2026_sample.csv'), text('p', 'ui.sampleRows', 'small muted'), text('p', 'common.sample', 'info-note'), link('common.preview', 'preview', true), link('common.add', 'source', true));
    context.classList.add('context-panel');
    const conversation = card(); const thread = el('div', 'thread'); thread.id = 'thread'; thread.setAttribute('role', 'log');
    conversation.classList.add('conversation-panel'); conversation.append(sectionHead('ui.conversation', 'ui.sampleOnly'));
    if (!state.messages.length) {
      const empty = el('div', 'conversation-empty'); empty.append(icon('✧', 'empty-icon'), text('h3', 'analysis.question'), text('p', 'analysis.empty')); thread.append(empty);
    }
    for (const message of state.messages) {
      const user = el('div', 'message user'); user.append(text('strong', 'analysis.user'), el('p', '', message.question));
      const answer = el('div', 'message'); const body = el('p', '', message.answer); body.lang = message.language;
      answer.append(text('strong', 'analysis.assistant'), body); thread.append(user, answer);
    }
    const form = el('form', 'composer'); const label = text('label', 'analysis.question'); label.htmlFor = 'question';
    const input = el('textarea'); input.id = 'question'; input.maxLength = 2000; input.value = state.draft;
    attr(input, 'placeholder', 'analysis.placeholder'); input.addEventListener('input', () => { state.draft = input.value; });
    const error = text('p', 'errors.empty', 'field-error'); error.id = 'question-error'; error.hidden = true;
    input.setAttribute('aria-describedby', 'question-help question-error');
    input.addEventListener('input', () => { input.removeAttribute('aria-invalid'); error.hidden = true; });
    const send = text('button', 'analysis.run', 'button'); send.type = 'submit';
    form.addEventListener('submit', event => { event.preventDefault(); if (!addDemoAnswer(state)) { error.hidden = false; input.setAttribute('aria-invalid', 'true'); input.focus(); return; } render(false); document.querySelector('#question').focus(); });
    const help = text('p', 'ui.composeHint', 'small muted'); help.id = 'question-help';
    form.append(label, input, error, help, send);
    const preset = button('ui.insertExample', () => { if (!state.draft.trim()) { state.draft = t('ui.exampleQuestion'); input.value = state.draft; } input.focus(); }, true); preset.classList.add('example-button');
    const progress = el('div', 'execution-placeholder'); progress.append(text('strong', 'ui.noExecution'), text('p', 'ui.executionHelp', 'small muted'), link('nav.tasks', 'tasks', true));
    conversation.append(thread, form, preset, progress);
    const result = state.messages.length
      ? card(sectionHead('ui.contextResult', 'ui.sampleOnly'), text('p', 'ui.resultHint', 'small muted'), chart(), el('p', 'metric positive', '+50%'), text('p', 'ui.methods', 'small muted'), link('analysis.chart', 'visualizations', true))
      : card(sectionHead('ui.analysisSettings'), text('p', 'ui.activeSource', 'small muted'), el('p', 'source-chip', 'sales_2026_sample.csv · USD'),
        preference('analysis-answer-language', 'settings.answer', state.answerLanguage, value => { state.answerLanguage = value; }),
        preference('analysis-report-language', 'settings.report', state.reportLanguage, value => { state.reportLanguage = value; }),
        text('p', 'ui.beforeAnalysis', 'info-note'), text('p', 'ui.noExecution', 'small muted'));
    result.dataset.mode = state.messages.length ? 'demo-preview' : 'settings';
    result.classList.add('result-panel'); grid.append(context, conversation, result); main.append(grid);
  } else if (page === 'source') {
    heading(main, 'source.title', 'source.description'); const box = card(text('p', 'source.notice', 'warning'));
    const label = text('label', 'source.choose'); label.htmlFor = 'local-file';
    const file = el('input'); file.type = 'file'; file.id = 'local-file'; file.accept = '.csv,.xlsx';
    const name = el('p', 'source-id'); const update = () => { name.textContent = state.selectedFile ? t('source.selected', { name: state.selectedFile }) : t('source.none'); };
    bindings.push(update); update(); file.addEventListener('change', () => { state.selectedFile = file.files[0]?.name ?? ''; update(); });
    box.append(label, file, name, link('source.sample', 'preview')); main.append(box);
  } else if (page === 'preview') preview(main);
  else if (page === 'visualizations') {
    heading(main, 'visualizations.title', 'visualizations.description', 'ui.vizKicker'); const grid = el('div', 'grid metrics');
    grid.classList.add('visual-metrics');
    for (const [key, value] of [['baseline', 'USD 400'], ['comparison', 'USD 600'], ['growth', '+50%']]) grid.append(metric(`visualizations.${key}`, value, key === 'growth' ? 'ui.findingGrowth' : 'common.sample', key === 'growth'));
    const visual = el('div', 'grid two visual-grid'); visual.append(card(sectionHead('ui.revenue', 'ui.sampleOnly'), chart(), text('p', 'visualizations.source', 'small muted')), findings());
    main.append(text('p', 'common.sample', 'info-note'), grid, visual, card(sectionHead('ui.related'), text('p', 'report.title'), link('visualizations.open', 'report')));
  } else if (page === 'report') {
    heading(main, 'report.title'); const body = el('p', 'report-body', state.report.body); body.lang = state.report.language;
    const exportButton = button('report.export', () => {}); exportButton.disabled = true;
    main.append(text('p', 'report.notice', 'warning'), card(body, chart(), el('p', 'source-id', `${state.report.id} · ${state.report.source}`)), exportButton);
  } else if (page === 'library') {
    heading(main, 'library.title', 'library.description'); const choices = el('div', 'actions');
    for (const tab of ['data', 'reports', 'lineage']) { const btn = button(`library.${tab}`, () => { state.libraryTab = tab; render(false); document.querySelector(`#library-${tab}`).focus(); }, true); btn.id = `library-${tab}`; btn.setAttribute('aria-pressed', String(state.libraryTab === tab)); choices.append(btn); }
    main.append(choices, text('p', 'library.empty'));
    if (state.libraryTab === 'data') main.append(card(el('p', 'source-id', 'sales_2026_sample.csv'), link('common.preview', 'preview')));
    else if (state.libraryTab === 'reports') main.append(card(text('p', 'common.sample'), link('visualizations.open', 'report')));
    else main.append(card(text('p', 'library.lineageText')));
  } else if (page === 'tasks') {
    heading(main, 'tasks.title'); main.append(card(text('p', 'tasks.empty'), text('p', 'tasks.safe', 'warning')));
    const states = card(sectionHead('ui.taskStates'), text('p', 'ui.taskHelp', 'muted')); const list = el('div', 'status-list');
    for (const status of ['queued', 'running', 'completed', 'failed', 'needs_reconciliation']) list.append(text('span', `status.${status}`, `status status-${status}`));
    states.append(list); main.append(states);
  } else if (page === 'settings') {
    heading(main, 'settings.title', 'ui.settingsDescription', 'ui.settingsKicker'); const wrapper = card(); wrapper.classList.add('settings-wrapper'); const grid = el('div', 'grid two');
    const choices = el('div', 'locale-choices'); choices.id = 'settings-locale'; choices.setAttribute('role', 'group'); attr(choices, 'aria-label', 'settings.ui');
    for (const [code, label] of [['en', 'English'], ['zh-CN', '简体中文']]) {
      const option = el('button', '', label); option.type = 'button'; option.dataset.locale = code;
      bindings.push(() => option.setAttribute('aria-pressed', String(state.locale === code)));
      option.addEventListener('click', () => changeLocale(code)); choices.append(option);
    }
    const preview = el('div', 'locale-preview'); preview.append(text('span', 'ui.previewLabel', 'small muted'), text('strong', 'ui.languagePreview'), text('p', 'ui.quickHint', 'small muted'));
    grid.append(card(text('p', 'ui.languageKicker', 'kicker'), text('h2', 'settings.ui'), text('p', 'settings.uiHelp', 'small muted'), choices, preview),
      card(text('p', 'ui.outputKicker', 'kicker'), preference('answer-language', 'settings.answer', state.answerLanguage, value => { state.answerLanguage = value; }), text('p', 'settings.answerHelp', 'small muted'),
        preference('report-language', 'settings.report', state.reportLanguage, value => { state.reportLanguage = value; }), text('p', 'settings.reportHelp', 'small muted')));
    const later = card(text('p', 'ui.laterKicker', 'kicker'), sectionHead('ui.laterTitle', 'common.planned'));
    for (const key of ['ui.chartPrefs', 'ui.retention']) { const row = el('div', 'preference-row'); row.append(text('span', key), text('span', 'common.planned', 'badge neutral')); later.append(row); }
    later.append(text('p', 'ui.laterHelp', 'small muted'));
    wrapper.append(grid, later, text('p', 'settings.policy', 'warning')); main.append(wrapper);
  } else { heading(main, 'account.title', 'account.description'); main.append(link('auth.switchLogin', 'login')); }
}
function render(focus = true) {
  document.body.classList.remove('drawer-open');
  bindings = []; root.replaceChildren(); const page = route();
  if (['login', 'register'].includes(page)) { const main = el('main', 'auth-main'); root.append(main); auth(main, page); }
  else {
    const skip = text('a', 'common.skip', 'skip'); skip.href = '#main';
    skip.addEventListener('click', event => { event.preventDefault(); document.querySelector('#page-title').focus(); }); root.append(skip);
    const main = shell(page); renderPage(main, page); main.append(text('p', 'common.boundary', 'footer'));
  }
  const notice = el('div', 'notice'); notice.id = 'notice'; notice.setAttribute('role', 'status'); root.append(notice);
  localize(); if (focus) document.querySelector('#page-title')?.focus();
  root.dataset.page = page;
}
window.addEventListener('hashchange', () => render());
// Rebuild navigation accessibility when crossing the mobile breakpoint, preserving draft state.
matchMedia('(max-width: 760px)').addEventListener('change', () => render(false));
render(false);
