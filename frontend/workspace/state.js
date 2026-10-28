import { answerLocale, normalizeLocale, translate } from './i18n.js';
export const navigation = Object.freeze(['overview', 'analysis', 'visualizations', 'library', 'tasks', 'settings', 'account']);
export const routes = Object.freeze(['login', 'register', ...navigation, 'source', 'preview', 'report']);
export const sampleRows = Object.freeze([
  ['2026-Q1', 'North', '200.00'], ['2026-Q1', 'South', '200.00'],
  ['2026-Q2', 'North', '300.00'], ['2026-Q2', 'South', '300.00']
].map(Object.freeze));
export const sampleReport = Object.freeze({
  id: 'sample-report-v41', language: 'en', source: 'sales_2026_sample.csv',
  body: 'Synthetic sample: Q1 USD 400; Q2 USD 600. Absolute change USD 200; growth (600 − 400) / 400 = 50%. North and South each increased from USD 200 to USD 300. These four invented rows do not establish causes. Not a saved customer report.'
});
export function createState(locale = 'en') {
  return { locale: normalizeLocale(locale), answerLanguage: 'auto', reportLanguage: 'auto',
    draft: '', messages: [], report: sampleReport, selectedFile: '', libraryTab: 'data' };
}
export function setUiLocale(state, locale) { state.locale = normalizeLocale(locale); }
export function addDemoAnswer(state) {
  const question = state.draft.trim();
  if (!question) return false;
  const language = answerLocale(state.answerLanguage, question);
  state.messages.push(Object.freeze({ question, language, answer: translate(language, 'analysis.sampleAnswer') }));
  state.draft = '';
  return true;
}
