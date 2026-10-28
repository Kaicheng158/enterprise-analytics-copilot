import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdir, writeFile } from 'node:fs/promises';
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright');
const output = process.env.QA_OUTPUT || '/tmp/eac-workspace-final-review/details';
await mkdir(output); // Preserve previous evidence: never replace an existing run.
const browser = await chromium.launch({ channel: 'chrome' });
const checks = []; const errors = []; const denied = [];
try {
 const page = await browser.newPage();
 page.on('pageerror', e => errors.push(e.message));
 await page.route('**/*', r => {
  if (new URL(r.request().url()).origin === 'http://127.0.0.1:8774' && r.request().method() === 'GET') return r.continue();
  denied.push(r.request().url()); return r.abort();
 });
 const visit = async route => { await page.goto(`http://127.0.0.1:8774/#${route}`); await page.waitForFunction(r => document.querySelector('#app').dataset.page === r, route); };
 for (const width of [1440,390]) for (const locale of ['en','zh-CN']) {
  await page.setViewportSize({width,height:844});
  for (const route of ['library','tasks','report','account']) {
   await visit(route); await page.locator('.topbar select').selectOption(locale);
   await page.evaluate(() => { document.activeElement.blur(); scrollTo(0,0); });
   assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),false);
   await page.screenshot({path:`${output}/${route}-${locale}-${width}.png`,fullPage:true}); checks.push(`${route}/${locale}/${width}`);
  }
 }
 for (const locale of ['en','zh-CN']) for (const width of [390,320]) {
  await page.setViewportSize({width,height:844}); await visit('analysis'); await page.locator('.topbar select').selectOption(locale);
  await page.locator('#question').fill((locale === 'en' ? 'Compare the supplied quarterly sales and explain what can be established. ' : '请比较季度销售数据并说明证据局限。').repeat(18));
  await page.locator('.composer button').click();
  const audit = await page.evaluate(() => {
   const title = document.querySelector('.breadcrumb strong'); const range = document.createRange(); range.selectNodeContents(title);
   const rect = title.getBoundingClientRect(); const controls = [...document.querySelectorAll('#menu,.topbar select')].map(e => e.getBoundingClientRect());
   return {lines:range.getClientRects().length,width:rect.width,height:rect.height,controls:controls.every(r => r.height >=44 && r.left >=0 && r.right <=innerWidth),overflow:document.documentElement.scrollWidth>innerWidth};
  });
  assert.equal(audit.lines,1); assert.ok(audit.width>=250); assert.ok(audit.height<=22); assert.ok(audit.controls); assert.equal(audit.overflow,false);
  await page.evaluate(() => {document.activeElement.blur();scrollTo(0,0);});
  assert.ok(await page.locator('.skip').evaluate(e => e.getBoundingClientRect().bottom < 0));
  await page.screenshot({path:`${output}/long-transcript-${locale}-${width}.png`,fullPage:true});
  await page.locator('.skip').focus();
  assert.equal(await page.locator('.skip').evaluate(e => e.getBoundingClientRect().top),10);
  await page.keyboard.press('Enter'); assert.equal(await page.locator('#page-title').evaluate(e=>e===document.activeElement),true);
  checks.push(`long transcript + header + skip/${locale}/${width}`);
 }
 // Browser zoom equivalent layout stress: 390px at 200% has a 195 CSS-pixel layout viewport.
 await page.setViewportSize({width:195,height:422}); await visit('analysis');
 assert.equal(await page.locator('.breadcrumb strong').evaluate(e => {const r=document.createRange();r.selectNodeContents(e);return r.getClientRects().length;}),1);
 checks.push('195 CSS-pixel header layout stress (not native browser zoom)');
 assert.deepEqual(errors,[]); assert.deepEqual(denied,[]);
 await writeFile(`${output}/results.json`,JSON.stringify({checks,errors,denied},null,2)); console.log(JSON.stringify({checks:checks.length,errors,denied}));
} finally {await browser.close();}
