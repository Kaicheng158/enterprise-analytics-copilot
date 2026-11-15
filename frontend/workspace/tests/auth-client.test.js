import assert from 'node:assert/strict';
import test from 'node:test';

import {createAuthClient} from '../auth-client.js';

const response = (status, body) => ({
  ok: status >= 200 && status < 300,
  status,
  async json() { return body; }
});

test('missing or disabled auth service preserves Demo mode', async () => {
  const client = createAuthClient({fetchImpl:async () => response(404,{})});
  await client.bootstrap();
  assert.equal(client.snapshot.ready,true);
  assert.equal(client.enabled,false);
  assert.equal(client.session,null);
});

test('known enabled service failure stays fail-closed instead of falling back to Demo', async () => {
  let calls=0;
  const client=createAuthClient({fetchImpl:async () => {
    calls += 1;
    if(calls===1)return response(200,{enabled:true});
    throw new Error('network unavailable');
  }});
  await client.bootstrap();
  assert.equal(client.enabled,true);
  assert.equal(client.session,null);
  assert.equal(client.snapshot.errorKey,'errors.authUnavailable');
});

test('bootstrap resumes an authenticated session without storing credentials', async () => {
  const calls=[];
  const session={user:{display_name:'Synthetic Analyst'},csrf_token:'csrf-token'};
  const client=createAuthClient({fetchImpl:async (url,options={}) => {
    calls.push([url,options]);
    return url.endsWith('/config') ? response(200,{enabled:true}) : response(200,session);
  }});
  await client.bootstrap();
  assert.equal(client.enabled,true);
  assert.equal(client.session,session);
  assert.deepEqual(calls.map(([url])=>url),['/auth-api/config','/auth-api/session']);
});

test('register submits only the strict auth body and keeps the session in memory', async () => {
  const calls=[];
  const session={user:{display_name:'Synthetic Analyst'},csrf_token:'csrf-token'};
  const client=createAuthClient({fetchImpl:async (url,options={}) => {
    calls.push([url,options]);
    if(url.endsWith('/config'))return response(200,{enabled:true});
    if(url.endsWith('/session'))return response(401,{detail:{code:'auth_required'}});
    return response(201,session);
  }});
  await client.bootstrap();
  const accepted=await client.authenticate('register',{
    account:'analyst@example.com',display_name:'Synthetic Analyst',password:'synthetic-password-123'
  });
  assert.equal(accepted,true);
  assert.equal(client.session,session);
  const [url,options]=calls.at(-1);
  assert.equal(url,'/auth-api/register');
  assert.deepEqual(Object.keys(JSON.parse(options.body)).sort(),['account','display_name','password']);
});

test('logout sends the in-memory CSRF token and clears the session', async () => {
  const calls=[];
  const session={user:{display_name:'Synthetic Analyst'},csrf_token:'csrf-token'};
  const client=createAuthClient({fetchImpl:async (url,options={}) => {
    calls.push([url,options]);
    if(url.endsWith('/config'))return response(200,{enabled:true});
    if(url.endsWith('/session'))return response(200,session);
    return response(204,{});
  }});
  await client.bootstrap();
  assert.equal(await client.logout(),true);
  assert.equal(client.session,null);
  assert.equal(calls.at(-1)[1].headers['X-CSRF-Token'],'csrf-token');
});

test('safe server codes map to local UI keys without echoing response bodies', async () => {
  const client=createAuthClient({fetchImpl:async url =>
    url.endsWith('/config') ? response(200,{enabled:true}) :
    url.endsWith('/session') ? response(401,{detail:{code:'auth_required'}}) :
    response(401,{detail:{code:'auth_invalid_credentials',message:'PRIVATE'}})
  });
  await client.bootstrap();
  assert.equal(await client.authenticate('login',{account:'a@example.com',password:'wrong'}),false);
  assert.equal(client.snapshot.errorKey,'errors.authInvalid');
});
