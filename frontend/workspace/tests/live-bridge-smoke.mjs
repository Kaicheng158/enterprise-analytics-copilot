import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdir,writeFile} from 'node:fs/promises';
const {chromium}=createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE||'playwright');
const base=process.env.WORKSPACE_PREVIEW_URL;assert.ok(base);assert.equal(new URL(base).hostname,'127.0.0.1');
const output=process.env.QA_OUTPUT||'/tmp/eac-workspace-5-8b-live';await mkdir(output,{recursive:true});
const browser=await chromium.launch({channel:'chrome',headless:true});const evidence={requests:[],task:null,checks:[]};
try{
 const page=await browser.newPage({viewport:{width:1440,height:1000}});
 page.on('request',request=>{if(new URL(request.url()).pathname.startsWith('/workspace-api/'))evidence.requests.push({method:request.method(),path:new URL(request.url()).pathname,idempotency:request.headers()['idempotency-key']||null,hasInternalKey:'x-internal-agent-key' in request.headers()});});
 await page.goto(`${base}/#analysis`);await page.waitForSelector('#baseline-period');
 await page.locator('form button[type=submit]').click();
 await page.waitForFunction(()=>document.querySelector('.task-card .status-completed'),null,{timeout:15000});
 const taskText=await page.locator('.task-card').innerText();assert.match(taskText,/3\/3/);evidence.checks.push('real Fake-Planner task reached completed with 3/3 steps');
 await page.screenshot({path:`${output}/real-analysis-completed-en-1440.png`,fullPage:true});
 await page.goto(`${base}/#tasks`);await page.waitForSelector('.status-completed');await page.screenshot({path:`${output}/real-tasks-completed-en-1440.png`,fullPage:true});
 await page.goto(`${base}/#visualizations`);await page.waitForFunction(()=>document.querySelector('main')?.textContent.includes('analytics.sales_orders'));
 assert.match(await page.locator('main').innerText(),/synthetic-sales-db-v1/);await page.screenshot({path:`${output}/real-visualizations-en-1440.png`,fullPage:true});
 evidence.checks.push('real verified chart exposes relation and dataset version');
 const stored=await page.evaluate(()=>JSON.parse(sessionStorage.getItem('eac.workspace.agent-recovery.v1')));evidence.task={taskId:stored.taskId,keyPrefix:stored.key.split('-').slice(0,2).join('-'),request:stored.request};
 assert.equal(evidence.requests.filter(item=>item.method==='POST').length,1);assert.ok(evidence.requests.every(item=>!item.hasInternalKey));evidence.checks.push('browser made one POST and never held the internal Agent key');
 const postCount=evidence.requests.filter(item=>item.method==='POST').length;await page.locator('#workspace-locale').selectOption('zh-CN');await page.waitForTimeout(800);assert.equal(evidence.requests.filter(item=>item.method==='POST').length,postCount);
 evidence.checks.push('language switch made no Agent request');
 await page.setViewportSize({width:390,height:844});await page.goto(`${base}/#analysis`);await page.waitForSelector('.result-panel[data-mode="verified-result"]');assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 await page.screenshot({path:`${output}/real-analysis-completed-zh-390.png`,fullPage:true});evidence.checks.push('real completed result remains usable at 390px');
 await writeFile(`${output}/live-bridge-evidence.json`,JSON.stringify(evidence,null,2));console.log(JSON.stringify({verdict:'PASS',...evidence}));
}finally{await browser.close();}
