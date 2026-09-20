import asyncio
import io
import json
import unittest
from unittest.mock import patch

from backend.llm import DeepSeekProvider, get_provider
from backend.main import app
from backend.output import AnalyticsAnswer
from test_acceptance import send_request
from test_retry_cost import success


class StructuredOutputTests(unittest.TestCase):
    def setUp(self):
        app.dependency_overrides[get_provider] = lambda: DeepSeekProvider('private-key', 'deepseek-flash')

    def tearDown(self):
        app.dependency_overrides.clear()

    def test_invalid_output_fails_safely_without_retry_or_repair(self):
        valid = {'summary': 'private-output', 'facts': [], 'interpretation': [], 'limitations': []}
        bad = ['', ' ', 'not JSON private-output', '```json\n' + json.dumps(valid) + '\n```',
               json.dumps(valid) + ' trailing', '{"summary":', 'null', '[]',
               '{"summary":"private-output","summary":"other","facts":[],"interpretation":[],"limitations":[]}',
               json.dumps({**valid, 'summary': float('nan')}), json.dumps({**valid, 'extra': 'private-output'})]
        for key in valid:
            bad.append(json.dumps({k:v for k,v in valid.items() if k != key}))
            for value in [None, 42, True, {}, ['private-output'] if key == 'summary' else 'private-output']:
                bad.append(json.dumps({**valid, key:value}))
        for key in ['facts', 'interpretation', 'limitations']:
            bad.append(json.dumps({**valid, key:['valid', 42]}))
        for content, finish in [(c, 'stop') for c in bad] + [(json.dumps(valid), 'length'), (json.dumps(valid), 'content_filter')]:
            payload = json.loads(success().getvalue())
            payload['choices'][0].update(message={'content':content}, finish_reason=finish)
            with self.subTest(content=content, finish=finish), patch('backend.llm.urlopen', return_value=io.BytesIO(json.dumps(payload).encode())) as call, patch('backend.llm.time.sleep') as sleep, self.assertLogs('uvicorn.error', level='INFO') as logs:
                status, data = asyncio.run(send_request(b'{"user_message":"private-prompt"}'))
            self.assertEqual(status, 502)
            self.assertEqual(data, {'detail': {'code':'llm_invalid_output', 'message':'Invalid LLM structured output'}})
            self.assertEqual(call.call_count, 1)
            sleep.assert_not_called()
            self.assertNotIn('private', str(logs.output))
            self.assertEqual(json.loads(logs.records[-1].getMessage().split(' ',1)[1])['status'], 'error')

    def test_openapi_publishes_required_nested_schema(self):
        spec = app.openapi()
        response = spec['paths']['/chat']['post']['responses']['200']['content']['application/json']['schema']
        self.assertEqual(response['$ref'], '#/components/schemas/ChatResult')
        schemas = spec['components']['schemas']
        self.assertEqual(schemas['ChatResult']['properties']['answer']['$ref'], '#/components/schemas/AnalyticsAnswer')
        schema = schemas['AnalyticsAnswer']
        self.assertEqual(set(schema['required']), {'summary','facts','interpretation','limitations'})
        self.assertFalse(schema['additionalProperties'])
        self.assertEqual(schema['properties']['summary']['type'], 'string')
        for key in ['facts','interpretation','limitations']:
            self.assertEqual(schema['properties'][key]['type'], 'array')
            self.assertEqual(schema['properties'][key]['items']['type'], 'string')

    def test_schema_in_prompt_matches_validation_schema(self):
        from backend.prompts import build_messages
        prompt = build_messages('Hello')[0]['content']
        schema = json.loads(prompt.split('\nJSON schema: ',1)[1])
        self.assertEqual(schema, AnalyticsAnswer.model_json_schema())
