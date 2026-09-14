import asyncio
import io
import json
import unittest
from unittest.mock import patch
from backend.main import app
from backend.llm import DeepSeekProvider, RetryConfig, get_provider
from test_retry_cost import success


async def send_request(body):
    output=[]
    async def receive(): return {'type':'http.request','body':body,'more_body':False}
    async def send(message): output.append(message)
    await app({'type':'http','asgi':{'version':'3.0'},'http_version':'1.1','method':'POST','scheme':'http',
               'path':'/chat','raw_path':b'/chat','query_string':b'',
               'headers':[(b'content-type',b'application/json')], 'server':('test',80),'client':('test',1)},receive,send)
    return output[0]['status'],json.loads(b''.join(m.get('body',b'') for m in output))


class AcceptanceTests(unittest.TestCase):
    def setUp(self):
        app.dependency_overrides[get_provider]=lambda: DeepSeekProvider('test-key','deepseek-flash',RetryConfig(max_retries=0))
    def tearDown(self): app.dependency_overrides.clear()

    def test_http_success_roles_and_legacy(self):
        for payload in [{'user_message':'Hi','system_message':'Be brief.'},{'message':'Hi'}]:
            with patch('backend.llm.urlopen',return_value=success()) as call:
                status,data=asyncio.run(send_request(json.dumps(payload).encode()))
            self.assertEqual(status,200)
            self.assertEqual(data['usage']['total_tokens'],120)
            self.assertIsNotNone(data['estimated_cost'])
            self.assertTrue(data['request_id'])
            roles=json.loads(call.call_args.args[0].data)['messages']
            self.assertEqual([x['role'] for x in roles],['system','user'])
            if 'system_message' in payload: self.assertEqual(roles[0]['content'],'Be brief.')

    def test_http_validation_no_provider_call(self):
        for body in [b'{}',b'null',b'[]',b'not json',b'{"user_message":5}',b'{"user_message":" "}',
                     b'{"user_message":"Hi","system_message":" "}', b'{"user_message":"Hi","extra":true}',
                     json.dumps({'user_message':'x'*8001}).encode()]:
            with self.subTest(body=body[:50]),patch('backend.llm.urlopen') as call:
                status,_=asyncio.run(send_request(body))
                self.assertEqual(status,422)
                call.assert_not_called()

    def test_http_missing_key(self):
        app.dependency_overrides[get_provider]=lambda: DeepSeekProvider('','deepseek-flash')
        with patch('backend.llm.urlopen') as call:
            status,data=asyncio.run(send_request(b'{"user_message":"Hi"}'))
        self.assertEqual(status,503)
        self.assertEqual(data['detail']['code'],'llm_not_configured')
        call.assert_not_called()

    def test_http_invalid_config(self):
        app.dependency_overrides.clear()
        with patch.dict('os.environ',{'LLM_MODEL':'not-supported'}),patch('backend.llm.urlopen') as call:
            status,data=asyncio.run(send_request(b'{"user_message":"Hi"}'))
        self.assertEqual(status,503)
        self.assertEqual(data['detail']['code'],'llm_invalid_config')
        call.assert_not_called()

    def test_inconsistent_usage_rejected_without_retry(self):
        for change in [{'total_tokens':999},{'prompt_tokens':True},{'prompt_tokens':1.5},
                       {'prompt_cache_hit_tokens':101},{'completion_tokens_details':{'reasoning_tokens':21}}]:
            payload=json.loads(success().getvalue());payload['usage'].update(change)
            with self.subTest(change=change),patch('backend.llm.urlopen',return_value=io.BytesIO(json.dumps(payload).encode())) as call:
                status,data=asyncio.run(send_request(b'{"user_message":"Hi"}'))
            self.assertEqual(status,502)
            self.assertEqual(data['detail']['code'],'llm_invalid_response')
            self.assertEqual(call.call_count,1)
