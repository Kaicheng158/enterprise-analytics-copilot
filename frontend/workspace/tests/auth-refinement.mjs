import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdir,writeFile} from 'node:fs/promises';
const {chromium}=createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE||'playwright');
const out=process.env.QA_OUTPUT;assert.ok(out);await mkdir(out);
const b=await chromium.launch({channel:'chrome'});const checks=[];const errors=[];const blocked=[];
try{const p=await b.newPage();p.on('pageerror',e=>errors.push(e.message));
await p.route('**/*',r=>{if(new URL(r.request().url()).origin==='http://127.0.0.1:8774'&&r.request().method()==='GET')return r.continue();blocked.push(r.request().url());return r.abort();});
for(const width of [1440,768,390])for(const locale of ['en','zh-CN'])for(const mode of ['login','register']){
 await p.setViewportSize({width,height:width===1440?1000:width===768?1024:844});await p.goto(`http://127.0.0.1:8774/#${mode}`);await p.waitForFunction(m=>document.querySelector('#app').dataset.page===m,mode);await p.locator('#auth-locale').selectOption(locale);
 assert.equal(await p.locator('.brand').innerText(),locale==='en'?'Analytics Copilot':'数据分析助手');assert.equal(await p.locator('.brand-mark').count(),0);
 assert.equal(await p.locator('.auth-art>.kicker').count(),0);
 assert.doesNotMatch(await p.locator('body').innerText(),/企业数据分析助手|ENTERPRISE ANALYTICS COPILOT/);
 assert.equal(await p.locator('.auth-art').isVisible(),width>1000);assert.equal(await p.locator('.auth-floating>.card').count(),3);
 assert.equal(await p.locator('.auth-pane>.wide').count(),1);assert.equal(await p.locator('.auth-demo-label,.auth-pane>.warning').count(),0);
 assert.match(await p.locator('.auth-safety-note').innerText(),locale==='en'?/no real authentication.*Do not enter real credentials/i:/不进行真实认证.*请勿输入真实凭证/);
 assert.equal(await p.locator('.password-toggle').first().innerText(),'');assert.equal(await p.locator('.password-toggle svg').count(),mode==='register'?2:1);
 assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 if(mode==='register')assert.match(await p.locator('.auth-pane .description').innerText(),/No account|不会创建/);
 const audit=await p.evaluate(()=>[...document.querySelectorAll('input,button,select')].filter(e=>e.getClientRects().length).every(e=>e.getBoundingClientRect().height>=44));assert.ok(audit);
 await p.locator('#demo-password').focus();await p.keyboard.press('Tab');const passwordToggle=p.locator('[aria-controls="demo-password"]');
 assert.equal(await p.evaluate(()=>document.activeElement?.getAttribute('aria-controls')),'demo-password');
 assert.notEqual(await passwordToggle.evaluate(e=>getComputedStyle(e).outlineStyle),'none');
 assert.equal(await passwordToggle.getAttribute('aria-label'),locale==='en'?'Show password':'显示密码');
 await p.keyboard.press('Enter');assert.equal(await p.locator('#demo-password').getAttribute('type'),'text');assert.equal(await passwordToggle.getAttribute('aria-pressed'),'true');
 assert.equal(await passwordToggle.getAttribute('aria-label'),locale==='en'?'Hide password':'隐藏密码');
 await p.keyboard.press('Enter');assert.equal(await p.locator('#demo-password').getAttribute('type'),'password');assert.equal(await passwordToggle.getAttribute('aria-pressed'),'false');
 await p.locator('#demo-email').fill('synthetic@example.invalid');await p.locator('#demo-password').fill('synthetic-only');
 await p.locator('#auth-locale').selectOption(locale==='en'?'zh-CN':'en');assert.equal(await p.locator('#demo-email').inputValue(),'synthetic@example.invalid');
 await p.locator('#auth-locale').selectOption(locale);await p.locator('#demo-email').fill('');await p.locator('#demo-password').fill('');
 await p.evaluate(()=>{document.activeElement.blur();scrollTo(0,0);});await p.screenshot({path:`${out}/${mode}-${locale}-${width}.png`,fullPage:true});
 // No credential getter is allowed during the Demo navigation action.
 await p.evaluate(()=>{for(const e of document.querySelectorAll('.auth-fields input'))Object.defineProperty(e,'value',{get(){throw Error('Credential value read');},configurable:true});});
 await p.locator('.auth-pane>.wide').first().click();await p.waitForFunction(()=>document.querySelector('#app').dataset.page==='overview');assert.equal(await p.locator('.sidebar .brand').innerText(),locale==='en'?'Analytics Copilot':'数据分析助手');
 assert.equal(await p.locator('.auth-fields input').count(),0);assert.equal(await p.evaluate(()=>JSON.stringify(localStorage).includes('synthetic')),false);
 checks.push(`${width}/${locale}/${mode}`);
}
assert.deepEqual(errors,[]);assert.deepEqual(blocked,[]);await writeFile(`${out}/results.json`,JSON.stringify({checks,errors,blocked},null,2));console.log(JSON.stringify({checks:checks.length,errors,blocked}));
}finally{await b.close();}
