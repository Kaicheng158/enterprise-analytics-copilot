import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdir,writeFile} from 'node:fs/promises';
const {chromium}=createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE||'playwright');
const output=process.env.QA_OUTPUT||'/tmp/eac-workspace-5-8b';await mkdir(output,{recursive:true});
const base=process.env.WORKSPACE_PREVIEW_URL||'http://127.0.0.1:8774';
const browser=await chromium.launch({channel:'chrome',headless:true});const evidence={posts:[],gets:[],checks:[]};
const request={task:'compare_quarterly_sales',baseline_period:'2026-Q1',comparison_period:'2026-Q2',region:'all'};
const task=(status,version)=>({response_version:'internal-agent-task-v1',task_id:'22222222-2222-4222-8222-222222222222',status,created:true,request,
 progress:{completed_steps:status==='completed'?3:status==='running'?1:0,total_steps:3},
 result:status==='completed'?{baseline_period:'2026-Q1',comparison_period:'2026-Q2',region:'all',currency:'USD',baseline_total_sales:'400.00',comparison_total_sales:'600.00',absolute_change:'200.00',growth_rate_percent:'50.00',source_relation:'analytics.sales_orders',dataset_version:'synthetic-sales-db-v1'}:null,
 error:null,state_version:version,policy_version:'bounded-plan-policy-v1',runtime_version:'durable-agent-runtime-v1',updated_at:`2026-10-28T00:00:0${version}+00:00`});
try{
 const context=await browser.newContext();let getCount=0;
 await context.route('**/*',async route=>{const req=route.request();const url=new URL(req.url());
  if(url.origin!==base)return route.abort();
  if(url.pathname==='/workspace-api/tasks'&&req.method()==='POST'){
   evidence.posts.push({body:req.postDataJSON(),idempotency:req.headers()['idempotency-key'],hasInternalKey:'x-internal-agent-key' in req.headers()});
   return route.fulfill({status:202,contentType:'application/json',body:JSON.stringify(task('queued',1))});
  }
  if(url.pathname.startsWith('/workspace-api/tasks/')&&req.method()==='GET'){
   getCount+=1;evidence.gets.push(url.pathname);
   return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(getCount===1?task('running',2):task('completed',3))});
  }
  if(req.method()==='GET')return route.continue();return route.abort();
 });
 const page=await context.newPage();await page.goto(`${base}/#analysis`);await page.waitForSelector('#baseline-period');
 await page.locator('form button[type=submit]').evaluate(button=>{button.click();button.click();});
 await page.waitForFunction(()=>document.querySelector('.task-card .status-completed'));
 assert.equal(evidence.posts.length,1);assert.deepEqual(evidence.posts[0].body,request);assert.ok(evidence.posts[0].idempotency.startsWith('workspace-'));assert.equal(evidence.posts[0].hasInternalKey,false);
 evidence.checks.push('double click produced one structured POST without internal credential');
 const postsBeforeLocale=evidence.posts.length;await page.locator('#workspace-locale').selectOption('zh-CN');await page.waitForTimeout(700);assert.equal(evidence.posts.length,postsBeforeLocale);
 evidence.checks.push('UI locale change produced no Agent submission');
 await page.screenshot({path:`${output}/analysis-completed-zh-1440.png`,fullPage:true});
 await page.goto(`${base}/#tasks`);await page.waitForSelector('.status-completed');assert.match(await page.locator('.task-card').innerText(),/3\/3/);
 await page.screenshot({path:`${output}/tasks-completed-zh-1440.png`,fullPage:true});
 await page.goto(`${base}/#visualizations`);await page.waitForSelector('.badge');assert.match(await page.locator('main').innerText(),/analytics\.sales_orders/);
 await page.screenshot({path:`${output}/visualizations-verified-zh-1440.png`,fullPage:true});
 evidence.checks.push('Tasks and Visualizations render the validated result and source metadata');
 const postsBeforeReload=evidence.posts.length;await page.reload();await page.waitForFunction(()=>document.querySelector('main')?.textContent.includes('analytics.sales_orders'));assert.equal(evidence.posts.length,postsBeforeReload);
 evidence.checks.push('refresh recovered by task id without a new POST');
 await page.setViewportSize({width:390,height:844});await page.goto(`${base}/#analysis`);await page.waitForSelector('.result-panel[data-mode="verified-result"]');
 await page.screenshot({path:`${output}/analysis-completed-zh-390.png`,fullPage:true});
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);evidence.checks.push('completed controlled task is responsive at 390px');
 await writeFile(`${output}/browser-evidence.json`,JSON.stringify(evidence,null,2));console.log(JSON.stringify({verdict:'PASS',...evidence}));
}finally{await browser.close();}
