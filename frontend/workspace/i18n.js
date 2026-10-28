import en from './locales/en.js';
import zh from './locales/zh-CN.js';
export const catalogs = Object.freeze({ en, 'zh-CN': zh });
export const LOCALE_KEY = 'analytics-ui-locale-v41';
export const normalizeLocale = value => Object.hasOwn(catalogs, value) ? value : 'en';
export function translate(locale, key, values = {}) {
  const message = catalogs[normalizeLocale(locale)][key] ?? en[key];
  if (message === undefined) throw new Error(`Missing translation: ${key}`);
  return message.replace(/\{(\w+)\}/g, (_, name) => String(values[name] ?? `{${name}}`));
}
export function readLocale(storage) {
  try { return normalizeLocale(storage.getItem(LOCALE_KEY)); } catch { return 'en'; }
}
export function saveLocale(storage, locale) {
  try { storage.setItem(LOCALE_KEY, normalizeLocale(locale)); return true; } catch { return false; }
}
export const answerLocale = (preference, question) => preference === 'auto'
  ? (/[\u3400-\u9fff]/u.test(question) ? 'zh-CN' : 'en') : normalizeLocale(preference);
