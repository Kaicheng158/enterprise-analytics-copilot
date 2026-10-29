import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright');
const output = process.env.QA_OUTPUT || '/tmp/eac-workspace-5-8a/interaction';
await mkdir(output, { recursive: true });
const base = 'http://127.0.0.1:8774';
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const checks = []; const errors = []; const blocked = [];
const check = (name, actual, expected = true) => { assert.deepEqual(actual, expected, name); checks.push(name); };
try {
  const context = await browser.newContext();
  await context.route('**/*', route => {
    if (new URL(route.request().url()).origin !== base || route.request().method() !== 'GET' || /internal|\/chat/.test(route.request().url())) { blocked.push(route.request().url()); return route.abort(); }
    return route.continue();
  });
  const page = await context.newPage();
  page.on('pageerror', e => errors.push(e.message));
  page.on('console', e => { if (e.type() === 'error') errors.push(e.text()); });
  const visit = async route => { await page.goto(`${base}/#${route}`); await page.waitForFunction(route => document.querySelector('#app')?.dataset.page === route, route); };
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 844 });
    for (const route of ['login','register','overview','analysis','visualizations','settings','source','preview','library','tasks','account','report']) {
      await visit(route);
      const audit = await page.evaluate(() => {
        const visible = el => el.getClientRects().length && !el.closest('[inert]') && getComputedStyle(el).visibility !== 'hidden';
        const controls = [...document.querySelectorAll('input,textarea,select,button,a')].filter(visible);
        return {
          small: controls.filter(el => !el.classList.contains('skip') && !el.classList.contains('backdrop') && el.getBoundingClientRect().height < 43.5).map(el => el.outerHTML.slice(0, 130)),
          unlabeled: controls.filter(el => !el.textContent.trim() && !el.getAttribute('aria-label') && !el.labels?.length).map(el => el.tagName),
          overflow: document.documentElement.scrollWidth > innerWidth,
          headings: document.querySelectorAll('h1').length
        };
      });
      check(`${width}/${route}/touch-targets`, audit.small, []);
      check(`${width}/${route}/labels`, audit.unlabeled, []);
      check(`${width}/${route}/overflow`, audit.overflow, false);
      check(`${width}/${route}/one-heading`, audit.headings, 1);
    }
  }
  await visit('register');
  await page.locator('#auth-locale').selectOption('en');
  await page.locator('#demo-password').fill('synthetic-demo');
  await page.locator('#demo-confirm').fill('synthetic-demo');
  await page.locator('[aria-controls="demo-confirm"]').click();
  check('confirm visibility independent', await page.locator('#demo-password').getAttribute('type'), 'password');
  await page.locator('#auth-locale').selectOption('zh-CN');
  check('password control translated', await page.locator('[aria-controls="demo-confirm"]').getAttribute('aria-label'), '隐藏密码');
  await page.getByRole('button', { name: '进入演示工作台', exact: true }).click();
  await page.waitForFunction(() => document.querySelector('#app')?.dataset.page === 'overview');
  check('credentials discarded on demo entry', await page.locator('input[type=password]').count(), 0);
  await page.locator('#menu').click();
  await page.locator('.mobile-close').focus(); await page.keyboard.press('Shift+Tab');
  check('drawer reverse focus trap', await page.evaluate(() => document.activeElement === document.querySelector('.side-bottom button')), true);
  await page.keyboard.press('Tab'); check('drawer forward focus trap', await page.locator('.mobile-close').evaluate(el => el === document.activeElement));
  await page.locator('.sidebar nav a[href="#overview"]').click();
  check('current route closes drawer', await page.locator('#menu').getAttribute('aria-expanded'), 'false');
  await page.locator('#menu').click(); await page.screenshot({ path: resolve(output, 'mobile-navigation.png'), fullPage: true });
  await page.keyboard.press('Escape'); check('escape restores focus', await page.locator('#menu').evaluate(el => el === document.activeElement));
  await visit('analysis');
  await page.locator('#comparison-period').selectOption('2026-Q1');
  await page.locator('form button[type=submit]').click();
  check('same-period request is rejected before network', await page.locator('.field-error').isVisible());
  await page.screenshot({ path: resolve(output, 'mobile-inline-error.png'), fullPage: true });
  await page.locator('#sales-region').selectOption('south');
  await page.locator('#workspace-locale').selectOption('en');
  check('structured request retained across locale', await page.locator('#sales-region').inputValue(), 'south');
  check('structured form wraps', await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.screenshot({ path: resolve(output, 'mobile-structured-task.png'), fullPage: true });
  await page.setViewportSize({ width: 768, height: 1024 });
  await visit('settings'); await page.locator('[data-locale="zh-CN"]').click();
  await page.screenshot({ path: resolve(output, 'tablet-settings-768.png'), fullPage: true });
  check('tablet layout', await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await visit('source');
  await page.locator('#local-file').setInputFiles({ name: `${'超长业务文件'.repeat(25)}.csv`, mimeType: 'text/csv', buffer: Buffer.from('synthetic') });
  check('filename wraps safely', await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await visit('report'); const report = await page.locator('.report-body').innerText();
  await page.locator('#workspace-locale').selectOption('en'); check('report body invariant', await page.locator('.report-body').innerText(), report);
  check('export remains disabled', await page.locator('button:disabled').count(), 1);
  const contrast = await page.evaluate(() => {
    const css = getComputedStyle(document.documentElement);
    const lum = hex => { const parts = hex.replace('#','').match(/../g).map(v => parseInt(v,16)/255).map(v => v<=.04045 ? v/12.92 : ((v+.055)/1.055)**2.4); return parts[0]*.2126+parts[1]*.7152+parts[2]*.0722; };
    const ratio = color => 1.05/(lum(color.trim())+.05);
    return Object.fromEntries(['--ink','--text-secondary','--action','--positive-text','--error-text','--control-border'].map(key => [key, ratio(css.getPropertyValue(key))]));
  });
  for (const [key, ratio] of Object.entries(contrast)) check(`${key}/white contrast`, ratio >= (key === '--control-border' ? 3 : 4.5));
  check('no task submitted during visual QA', blocked, []); check('no browser errors', errors, []);
  const result = { checks: checks.length, verdict: 'PASS', cases: checks, contrast, errors, blocked };
  await writeFile(resolve(output, 'ux-results.json'), JSON.stringify(result, null, 2));
  console.log(JSON.stringify(result));
} finally { await browser.close(); }
