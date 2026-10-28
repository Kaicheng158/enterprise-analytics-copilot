import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdir,writeFile } from 'node:fs/promises';
import { translate } from '../i18n.js';
const {chromium}=createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright');
const output=process.env.QA_OUTPUT; assert.ok(output); await mkdir(output);
const browser=await chromium.launch({channel:'chrome'}); const checks=[];const errors=[];const network=[];
try{
 const page=await browser.newPage();page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/*',r=>{if(new URL(r.request().url()).origin==='http://127.0.0.1:8774'&&r.request().method()==='GET')return r.continue();network.push(r.request().url());return r.abort();});
 const visit=async p=>{await page.goto(`http://127.0.0.1:8774/#${p}`);await page.waitForFunction(p=>document.querySelector('#app').dataset.page===p,p);};
 for(const locale of ['en','zh-CN']){
  await page.setViewportSize({width:390,height:844}); await visit('analysis');await page.locator('#workspace-locale').selectOption(locale);
  assert.equal(await page.locator('.result-panel').getAttribute('data-mode'),'settings');
  assert.equal(await page.locator('.result-panel .chart').count(),0);
  for(const dest of ['overview','analysis','visualizations','library']){
   await page.locator(`.mobile-primary a[href="#${dest}"]`).click();await page.waitForFunction(p=>document.querySelector('#app').dataset.page===p,dest);
   assert.equal(await page.locator(`.mobile-primary a[href="#${dest}"]`).getAttribute('aria-current'),'page');
  }
  for(const dest of ['tasks','settings','account']){
   await page.locator('#menu').click();assert.equal(await page.locator('.content').evaluate(e=>e.inert),true);
   await page.locator(`.sidebar a[href="#${dest}"]`).click();await page.waitForFunction(p=>document.querySelector('#app').dataset.page===p,dest);
   assert.equal(await page.locator('#menu').getAttribute('aria-expanded'),'false');
  }
  await visit('analysis');await page.locator('#analysis-answer-language').selectOption('en');
  await page.locator('#question').fill('Compare sample sales');await page.locator('.composer button').click();
  assert.equal(await page.locator('.result-panel').getAttribute('data-mode'),'demo-preview');
  assert.ok(await page.locator('.result-panel .chart').isVisible());
  assert.ok((await page.locator('.execution-placeholder').innerText()).includes(translate(locale,'ui.noExecution')));
  await page.locator('#question').focus();
  await page.locator('#question').scrollIntoViewIfNeeded();
  assert.ok(await page.locator('#question').evaluate(e => e.getBoundingClientRect().bottom <= document.querySelector('.mobile-primary').getBoundingClientRect().top));
  await page.locator('#workspace-locale').selectOption(locale==='en'?'zh-CN':'en');
  assert.equal(await page.locator('#thread .message:not(.user) p').getAttribute('lang'),'en');
  await page.locator('#workspace-locale').selectOption(locale);
  await page.evaluate(()=>{document.activeElement.blur();scrollTo(0,0);});
  await page.screenshot({path:`${output}/viewport-${locale}-390.png`});
  await page.screenshot({path:`${output}/demo-result-${locale}-390.png`,fullPage:true});
  // Full reload discards only transient demo state; it does not persist a completed task.
  await page.reload();await page.waitForSelector('.result-panel[data-mode=settings]');
  assert.equal(await page.locator('.result-panel .chart').count(),0);
  for(const width of [390,1440]){
   await page.setViewportSize({width,height:1000});await visit('login');
   assert.equal(await page.locator('.auth-art').isVisible(),width===1440);
   assert.ok(await page.locator('#auth-locale').isVisible());assert.ok(await page.locator('.auth-safety-note').isVisible());
  }
  checks.push(`${locale}: 7 destinations, active links, drawer inert, settings/demo state, language isolation, reload, auth layouts`);
 }
 assert.deepEqual(errors,[]);assert.deepEqual(network,[]);
 await writeFile(`${output}/results.json`,JSON.stringify({checks,errors,network},null,2));console.log(JSON.stringify({verdict:'PASS',checks,errors,network}));
}finally{await browser.close();}
