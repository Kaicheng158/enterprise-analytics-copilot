import asyncio
import io
import json
import unittest
from unittest.mock import patch

from backend.llm import get_provider, ChatResult
from backend.main import app
from backend.output import parse_answer
from backend.prompt_registry import active_release
from eval.run_uncertainty import collect
from test_acceptance import send_request
from test_retry_cost import success, failure


class Phase2IntegrationTests(unittest.TestCase):
    def setUp(self):
        app.dependency_overrides.clear()
        self.env = patch.dict('os.environ', {'DEEPSEEK_API_KEY':'private-key','LLM_TIMEOUT_SECONDS':'7','LLM_MAX_RETRIES':'2'}, clear=True)
        self.dotenv = patch('backend.config.load_dotenv')
        self.env.start(); self.dotenv.start()

    def tearDown(self):
        self.dotenv.stop(); self.env.stop()
        app.dependency_overrides.clear()

    def request(self, data):
        return asyncio.run(send_request(json.dumps(data).encode()))

    def test_factory_route_retry_validation_response_logs_and_eval_identity(self):
        user = 'Synthetic private test: comparable toy counts 4 then 5.'
        with patch('backend.llm.urlopen',side_effect=[failure(503),success()]) as call, patch('backend.llm.time.sleep') as sleep, self.assertLogs('uvicorn.error',level='INFO') as logs:
            status, body = self.request({'user_message':user})
        self.assertEqual(status,200)
        response = ChatResult.model_validate(body)
        release = active_release()
        self.assertEqual((response.prompt_version,response.prompt_sha256),(release.version,release.sha256))
        self.assertEqual(response.retry_count,1)
        self.assertFalse(response.cost_complete)
        self.assertIsNotNone(response.estimated_cost)
        self.assertEqual(sleep.call_count,1)
        for attempt in call.call_args_list:
            payload = json.loads(attempt.args[0].data)
            self.assertEqual(payload['messages'],release.messages(user))
            self.assertEqual(payload['response_format'],{'type':'json_object'})
            self.assertEqual(attempt.kwargs['timeout'],7)
            for message in payload['messages']:
                if message['role']=='assistant': parse_answer(message['content'])
        records=[json.loads(r.getMessage().split(' ',1)[1]) for r in logs.records]
        for record in records:
            self.assertEqual(record['prompt_version'],body['prompt_version'])
            self.assertEqual(record['prompt_sha256'],body['prompt_sha256'])
            self.assertEqual(record['request_id'],body['request_id'])
        final=records[-1]
        for key in ['provider','model','latency_ms','retry_count','estimated_cost']:
            self.assertEqual(final[key],body[key])
        self.assertEqual(final['total_tokens'],body['usage']['total_tokens'])
        self.assertEqual(final['status'],'success')
        self.assertNotIn('private',str(logs.output))
        with patch('backend.llm.urlopen',return_value=success()):
            report=collect(get_provider(),[{'id':'synthetic','input':user}])
        self.assertEqual(report['prompt_version'],body['prompt_version'])
        self.assertEqual(report['prompt_sha256'],body['prompt_sha256'])
        self.assertEqual(report['records'][0]['prompt_sha256'],body['prompt_sha256'])
        schema=app.openapi()['components']['schemas']['ChatResult']
        self.assertEqual(set(body),set(schema['properties']))
        self.assertEqual(schema['properties']['answer']['$ref'],'#/components/schemas/AnalyticsAnswer')

    def test_errors_keep_http_boundary_and_sanitized_logs(self):
        invalid=json.loads(success().getvalue())
        invalid['choices'][0]['message']['content']='{"summary":"private bad answer"}'
        for effect,expected,code in [(TimeoutError('private'),504,'llm_timeout'),
                                     (failure(402),503,'llm_insufficient_balance'),
                                     (io.BytesIO(json.dumps(invalid).encode()),502,'llm_invalid_output')]:
            kwargs={'side_effect':effect} if isinstance(effect,Exception) else {'return_value':effect}
            with self.subTest(code=code), patch('backend.llm.urlopen',**kwargs) as call, patch('backend.llm.time.sleep') as sleep, self.assertLogs('uvicorn.error',level='INFO') as logs:
                status,body=self.request({'user_message':'Synthetic'})
            self.assertEqual(status,expected)
            self.assertEqual(body['detail']['code'],code)
            self.assertEqual(call.call_count,1)
            sleep.assert_not_called()
            record=json.loads(logs.records[-1].getMessage().split(' ',1)[1])
            self.assertEqual(record['status'],'error')
            self.assertEqual(record['prompt_sha256'],active_release().sha256)
            self.assertNotIn('private',str(body)+str(logs.output))

    def test_all_client_prompt_controls_rejected_without_upstream(self):
        for field in ['system_message','prompt_version','examples','output_contract','response_format']:
            with self.subTest(field=field), patch('backend.llm.urlopen') as call:
                status,_=self.request({'user_message':'Synthetic',field:'override'})
            self.assertEqual(status,422)
            call.assert_not_called()
