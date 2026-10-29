import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { catalogs, translate, readLocale, saveLocale, LOCALE_KEY, answerLocale } from '../i18n.js';
import { createState, setUiLocale, addDemoAnswer, navigation, sampleRows } from '../state.js';

test('catalogs have identical semantic keys and interpolation placeholders', () => {
  assert.deepEqual(Object.keys(catalogs.en).sort(), Object.keys(catalogs['zh-CN']).sort());
  for (const [key, value] of Object.entries(catalogs.en)) {
    assert.ok(value && catalogs['zh-CN'][key]);
    assert.deepEqual(value.match(/\{\w+\}/g), catalogs['zh-CN'][key].match(/\{\w+\}/g));
  }
});
test('invalid locale falls back; unknown keys fail visibly during development', () => {
  assert.equal(translate('invalid', 'nav.overview'), 'Overview');
  assert.throws(() => translate('en', 'missing'));
  assert.equal(translate('zh-CN', 'source.selected', { name: '原始.csv' }), '已选择：原始.csv · 未上传或处理');
});
test('storage saves only whitelisted UI locale and fails safely', () => {
  const entries = new Map(); const storage = { getItem: k => entries.get(k), setItem: (k, v) => entries.set(k, v) };
  assert.equal(readLocale(storage), 'en'); assert.equal(saveLocale(storage, 'zh-CN'), true);
  assert.deepEqual([...entries], [[LOCALE_KEY, 'zh-CN']]);
  assert.equal(readLocale(storage), 'zh-CN'); assert.equal(readLocale(null), 'en'); assert.equal(saveLocale(null, 'en'), false);
});
test('locale does not mutate draft, messages, report, preferences or source', () => {
  const state = createState(); state.draft = '比较销售'; addDemoAnswer(state);
  state.draft = 'Unsent <script> draft'; state.selectedFile = '原始.csv'; state.reportLanguage = 'zh-CN';
  const before = structuredClone(state); setUiLocale(state, 'zh-CN');
  assert.deepEqual({ ...state, locale: before.locale }, before);
  assert.equal(state.messages[0].language, 'zh-CN'); assert.equal(state.report.language, 'en');
});
test('answer choice is independent of UI and applies to new answers only', () => {
  const state = createState('zh-CN'); state.answerLanguage = 'en'; state.draft = '销售？'; addDemoAnswer(state);
  const original = state.messages[0]; state.answerLanguage = 'zh-CN'; state.draft = 'Sales?'; addDemoAnswer(state);
  assert.equal(original.language, 'en'); assert.equal(state.messages[1].language, 'zh-CN'); assert.equal(state.messages[0], original);
  assert.equal(answerLocale('auto', 'Sales?'), 'en'); assert.equal(answerLocale('auto', '销售？'), 'zh-CN');
  assert.equal(addDemoAnswer(state), false); assert.equal(state.messages.length, 2);
});
test('v4.1 navigation and synthetic arithmetic are stable', () => {
  assert.deepEqual(navigation, ['overview', 'analysis', 'visualizations', 'library', 'tasks', 'settings', 'account']);
  const total = period => sampleRows.filter(row => row[0] === period).reduce((sum, row) => sum + Number(row[2]), 0);
  assert.equal(total('2026-Q1'), 400); assert.equal(total('2026-Q2'), 600);
  assert.equal((total('2026-Q2') - total('2026-Q1')) / total('2026-Q1'), .5);
});
test('workspace boundary: same-origin transport only, no secret or unsafe HTML sinks', () => {
  const app = readFileSync(new URL('../app.js', import.meta.url), 'utf8');
  const client = readFileSync(new URL('../agent-client.js', import.meta.url), 'utf8');
  assert.doesNotMatch(app + client, /innerHTML|outerHTML|insertAdjacentHTML|createTreeWalker|MutationObserver|XMLHttpRequest|sendBeacon|WebSocket|FileReader|X-Internal-Agent-Key/);
  assert.match(client, /\/workspace-api\/tasks/);
  const html = readFileSync(new URL('../index.html', import.meta.url), 'utf8');
  assert.match(html, /connect-src 'self'/); assert.match(html, /form-action 'none'/);
});
