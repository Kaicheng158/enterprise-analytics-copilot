// Non-paid visual evidence, served only from loopback. Refuse to overwrite a capture stage.
import { createRequire } from 'node:module';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import assert from 'node:assert/strict';
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const [stage, output] = process.argv.slice(2);
assert.ok(['reference', 'before', 'after'].includes(stage) && output);
const target = resolve(output, stage);
await mkdir(target); // EEXIST deliberately prevents replacement of previous evidence.
const base = stage === 'reference' ? 'http://127.0.0.1:8775/enterprise_analytics_workspace_v4_1_bilingual.html' : 'http://127.0.0.1:8774/';
const browser = await chromium.launch({ channel: process.env.BROWSER_CHANNEL || 'chrome', headless: true });
const records = [];
try {
  const page = await browser.newPage();
  await page.route('**/*', route => new URL(route.request().url()).origin === new URL(base).origin && route.request().method() === 'GET' ? route.continue() : route.abort());
  for (const width of [1440, 390]) for (const locale of ['en', 'zh-CN']) for (const view of (process.env.CAPTURE_ALL && stage !== 'reference' ? ['login','register','overview','analysis','visualizations','settings','library','tasks','report','account'] : ['login', 'register', 'overview', 'analysis', 'visualizations', 'settings'])) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
    await page.goto(base);
    if (stage === 'reference') {
      await page.evaluate(({ view, locale }) => {
        go(({ login: 'identity', register: 'identity', visualizations: 'report' })[view] || view);
        if (view === 'register') showRegister(true);
        setLanguage(locale);
      }, { view, locale });
    } else {
      await page.evaluate(view => { location.hash = view; }, view);
      await page.waitForFunction(view => document.querySelector('#app')?.dataset.page === view, view);
      await page.locator('select').first().selectOption(locale);
    }
    await page.evaluate(() => { document.activeElement?.blur(); window.scrollTo(0, 0); });
    await page.waitForTimeout(220); // Finish reference-only entry animation; not a functional synchronization.
    const name = `${view}-${locale}-${width}.png`;
    await page.screenshot({ path: resolve(target, name), fullPage: true });
    records.push({ view, locale, width, screenshot: name, overflow: await page.evaluate(() => document.documentElement.scrollWidth > innerWidth) });
  }
  await writeFile(resolve(target, 'manifest.json'), JSON.stringify({ stage, records }, null, 2));
  console.log(JSON.stringify({ stage, screenshots: records.length, overflow: records.filter(record => record.overflow) }));
} finally { await browser.close(); }
