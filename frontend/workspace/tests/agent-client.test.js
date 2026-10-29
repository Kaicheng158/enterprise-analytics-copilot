import test from 'node:test';
import assert from 'node:assert/strict';
import {createAgentClient, agentRecoveryStorageKey} from '../agent-client.js';

const request = Object.freeze({task:'compare_quarterly_sales',baseline_period:'2026-Q1',comparison_period:'2026-Q2',region:'all'});
const task = (status='queued', version=1) => ({
  response_version:'internal-agent-task-v1', task_id:'11111111-1111-4111-8111-111111111111', status, created:true,
  request, progress:{completed_steps:status==='completed'?3:0,total_steps:3},
  result:status==='completed'?{baseline_period:'2026-Q1',comparison_period:'2026-Q2',region:'all',currency:'USD',baseline_total_sales:'400.00',comparison_total_sales:'600.00',absolute_change:'200.00',growth_rate_percent:'50.00',source_relation:'analytics.sales_orders',dataset_version:'synthetic-sales-db-v1'}:null,
  error:null,state_version:version,policy_version:'p',runtime_version:'r',updated_at:`2026-10-28T00:00:0${version}+00:00`
});
const response = (body, status=200) => ({ok:status>=200&&status<300,status,json:async()=>body});
const memoryStorage = initial => { const data=new Map(initial||[]); return {getItem:k=>data.get(k)||null,setItem:(k,v)=>data.set(k,v),removeItem:k=>data.delete(k),data}; };
const noopTimer = () => 1;

test('double submit shares one POST and sends no internal credential', async () => {
  const calls=[]; let release; const waiting=new Promise(resolve=>{release=resolve;});
  const client=createAgentClient({storage:memoryStorage(),uuid:()=> 'fixed',setTimer:noopTimer,clearTimer:()=>{},fetchImpl:async(url,options)=>{calls.push({url,options});await waiting;return response(task());}});
  const first=client.submit(request);const second=client.submit(request);release();await Promise.all([first,second]);
  assert.equal(calls.length,1);assert.equal(calls[0].url,'/workspace-api/tasks');
  assert.equal(calls[0].options.headers['Idempotency-Key'],'workspace-fixed');
  assert.equal('X-Internal-Agent-Key' in calls[0].options.headers,false);
});

test('lost POST response recovers with identical body and idempotency key', async () => {
  const calls=[];let attempt=0;const storage=memoryStorage();
  const client=createAgentClient({storage,uuid:()=> 'recovery',setTimer:noopTimer,clearTimer:()=>{},fetchImpl:async(url,options)=>{calls.push({url,options});if(attempt++===0)throw new TypeError('offline');return response(task());}});
  assert.equal(await client.submit(request),false);assert.equal(client.getSnapshot().phase,'submission_unknown');
  await client.recover();assert.equal(calls.length,2);
  assert.equal(calls[0].options.headers['Idempotency-Key'],calls[1].options.headers['Idempotency-Key']);
  assert.equal(calls[0].options.body,calls[1].options.body);
  assert.ok(storage.data.has(agentRecoveryStorageKey));
});

test('refresh resumes status lookup and terminal result stops polling', async () => {
  const recovery={key:'workspace-refresh',request,taskId:'11111111-1111-4111-8111-111111111111'};
  const storage=memoryStorage([[agentRecoveryStorageKey,JSON.stringify(recovery)]]);let calls=0;
  const client=createAgentClient({storage,setTimer:noopTimer,clearTimer:()=>{},fetchImpl:async()=>{calls+=1;return response(task('completed',4));}});
  client.hydrate();await new Promise(resolve=>setTimeout(resolve,0));
  assert.equal(calls,1);assert.equal(client.getSnapshot().task.status,'completed');
  assert.equal(await client.poll(),false);assert.equal(calls,1);
});

test('older concurrent status cannot overwrite a newer trusted state', async () => {
  const recovery={key:'workspace-order',request,taskId:'11111111-1111-4111-8111-111111111111'};
  const storage=memoryStorage([[agentRecoveryStorageKey,JSON.stringify(recovery)]]);const pending=[];
  const client=createAgentClient({storage,setTimer:noopTimer,clearTimer:()=>{},fetchImpl:()=>new Promise(resolve=>pending.push(resolve))});
  client.hydrate();const second=client.poll();
  pending[1](response(task('running',3)));await second;
  pending[0](response(task('queued',1)));await new Promise(resolve=>setTimeout(resolve,0));
  assert.equal(client.getSnapshot().task.status,'running');assert.equal(client.getSnapshot().task.state_version,3);
});

test('network failure preserves the last trusted task and stops automatic polling', async () => {
  let fail=false;let timers=0;const client=createAgentClient({storage:memoryStorage(),uuid:()=> 'network',setTimer:()=>{timers+=1;return timers;},clearTimer:()=>{},fetchImpl:async()=>{if(fail)throw new TypeError('offline');return response(task('running',2));}});
  await client.submit(request);fail=true;await client.poll();
  assert.equal(client.getSnapshot().task.status,'running');assert.equal(client.getSnapshot().errorKey,'errors.network');
});

test('polling stops at the configured bound without discarding the trusted task', async () => {
  const timers=[];
  const client=createAgentClient({storage:memoryStorage(),uuid:()=> 'bounded',maxPolls:1,
    setTimer:callback=>{timers.push(callback);return timers.length;},clearTimer:()=>{},
    fetchImpl:async()=>response(task('running',2))});
  await client.submit(request);assert.equal(timers.length,1);
  await timers.shift()();await new Promise(resolve=>setTimeout(resolve,0));
  assert.equal(client.getSnapshot().task.status,'running');
  assert.equal(client.getSnapshot().errorKey,'errors.pollLimit');
  assert.equal(client.getSnapshot().polling,false);assert.equal(timers.length,0);
});

test('HTTP errors are mapped without rendering backend detail', async () => {
  const cases=new Map([[401,'errors.unauthorized'],[403,'errors.forbidden'],[409,'errors.conflict'],[422,'errors.validation'],[429,'errors.capacity'],[502,'errors.planner'],[503,'errors.unavailable']]);
  for(const [status,key] of cases){
    const client=createAgentClient({storage:memoryStorage(),uuid:()=>`safe-${status}`,setTimer:noopTimer,clearTimer:()=>{},fetchImpl:async()=>response({detail:{message:'private database body'}},status)});
    await client.submit(request);assert.equal(client.getSnapshot().errorKey,key);
    assert.doesNotMatch(JSON.stringify(client.getSnapshot()),/private database body/);
  }
});
