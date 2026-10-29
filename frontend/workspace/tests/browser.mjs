// Run against the standalone loopback preview without submitting an Agent task.
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.WORKSPACE_PREVIEW_URL || 'http://127.0.0.1:8774';
assert.equal(new URL(base).hostname, '127.0.0.1');
const browser = await chromium.launch({ headless: true, ...(process.env.BROWSER_CHANNEL ? { channel: process.env.BROWSER_CHANNEL } : {}) });
const errors = []; const forbidden = []; let checks = 0;
try {
  const context = await browser.newContext();
  await context.route('**/*', route => {
    const request = route.request(); const url = new URL(request.url());
    if (url.origin !== new URL(base).origin || request.method() !== 'GET' || /internal|\/chat/.test(url.pathname)) {
      forbidden.push(request.url()); return route.abort();
    }
    return route.continue();
  });
  const page = await context.newPage();
  const visit = async route => {
    await page.goto(`${base}/#${route}`);
    await page.waitForFunction(expected => document.querySelector('#app')?.dataset.page === expected, route);
  };
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
  await page.goto(base);
  for (const width of [1440, 768, 390]) {
    await page.setViewportSize({ width, height: 900 });
    for (const locale of ['en', 'zh-CN']) {
      for (const route of ['login', 'register', 'overview', 'analysis', 'visualizations', 'library', 'tasks', 'settings', 'account', 'source', 'preview', 'report']) {
        await visit(route);
        await page.locator('select').first().selectOption(locale);
        assert.equal(await page.locator('html').getAttribute('lang'), locale);
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `${width}/${locale}/${route} overflow`);
        assert.ok(await page.locator('h1').innerText()); checks++;
      }
    }
  }
  await visit('login');
  await page.locator('#auth-locale').selectOption('en');
  await page.locator('#demo-password').fill('synthetic-not-a-credential');
  await page.getByRole('button', { name: 'Show password', exact: true }).click();
  assert.equal(await page.locator('#demo-password').getAttribute('type'), 'text');
  await page.locator('#auth-locale').selectOption('zh-CN');
  assert.equal(await page.locator('#demo-password').inputValue(), 'synthetic-not-a-credential');
  await page.getByRole('button', { name: '进入演示工作台', exact: true }).click();
  await page.waitForURL('**/#overview'); checks++;
  await page.locator('#menu').click();
  assert.equal(await page.locator('#menu').getAttribute('aria-expanded'), 'true');
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('#menu').getAttribute('aria-expanded'), 'false');
  await page.locator('#menu').click(); await page.locator('.sidebar nav a[href="#settings"]').click();
  await page.locator('#settings-locale [data-locale="en"]').click();
  assert.equal(await page.locator('#settings-locale [data-locale="en"]').getAttribute('aria-pressed'), 'true');
  assert.equal(await page.locator('#workspace-locale').inputValue(), 'en');
  await page.locator('#answer-language').selectOption('zh-CN');
  await page.locator('#report-language').selectOption('zh-CN'); checks++;
  await visit('analysis');
  await page.locator('#baseline-period').selectOption('2026-Q1');
  await page.locator('#comparison-period').selectOption('2026-Q3');
  await page.locator('#sales-region').selectOption('south');
  await page.locator('#workspace-locale').selectOption('zh-CN');
  await page.locator('#workspace-locale').selectOption('en');
  assert.equal(await page.locator('#comparison-period').inputValue(), '2026-Q3');
  assert.equal(await page.locator('#sales-region').inputValue(), 'south'); checks++;
  await page.locator('#menu').click(); await page.locator('.sidebar nav a[href="#settings"]').click();
  await page.locator('#answer-language').selectOption('en');
  await page.locator('#menu').click(); await page.locator('.sidebar nav a[href="#analysis"]').click();
  assert.equal(await page.locator('#comparison-period').inputValue(), '2026-Q3');
  assert.equal(await page.locator('#sales-region').inputValue(), 'south'); checks++;
  await visit('report');
  const report = await page.locator('.report-body').innerText();
  await page.locator('#workspace-locale').selectOption('zh-CN');
  assert.equal(await page.locator('.report-body').innerText(), report);
  assert.equal(await page.locator('button:disabled').count(), 1);
  assert.deepEqual(await page.evaluate(() => Object.keys(localStorage)), ['analytics-ui-locale-v41']);
  await page.reload(); assert.equal(await page.locator('html').getAttribute('lang'), 'zh-CN'); checks++;
  if (process.env.SCREENSHOT_DIR) {
    await visit('settings'); await page.screenshot({ path: `${process.env.SCREENSHOT_DIR}/workspace-mobile.png`, fullPage: true });
    await page.setViewportSize({ width: 1440, height: 1000 });
    await visit('overview'); await page.screenshot({ path: `${process.env.SCREENSHOT_DIR}/workspace-desktop.png`, fullPage: true });
  }
  assert.deepEqual(forbidden, []); assert.deepEqual(errors, []);
  console.log(JSON.stringify({ checks, widths: [1440, 768, 390], locales: ['en', 'zh-CN'], pageErrors: errors, forbiddenRequests: forbidden }));
} finally { await browser.close(); }
