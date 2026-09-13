import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from fastapi import HTTPException
from pydantic import ValidationError
from backend.llm import DeepSeekProvider, ProviderError, RetryConfig
from backend.main import ChatRequest, chat


class ChatTests(unittest.TestCase):
    def payload(self):
        return {'model': 'deepseek-flash', 'choices': [{'message': {'content': 'Hello'}, 'finish_reason': 'stop'}],
                'usage': {'prompt_tokens': 3, 'completion_tokens': 2, 'total_tokens': 5,
                          'prompt_cache_hit_tokens': 1, 'prompt_cache_miss_tokens': 2}}

    def test_usage_and_request_contract(self):
        with patch('backend.llm.urlopen', return_value=io.BytesIO(json.dumps(self.payload()).encode())) as call:
            result = DeepSeekProvider('test-key', 'deepseek-flash', RetryConfig(max_retries=0)).chat('Hello')
        self.assertEqual(result.usage.total_tokens, 5)
        self.assertEqual(result.usage.prompt_cache_hit_tokens, 1)
        body = json.loads(call.call_args.args[0].data)
        self.assertEqual(body['model'], 'deepseek-flash')
        self.assertNotIn('tools', body)
        self.assertNotIn('test-key', result.model_dump_json())

    def test_missing_key(self):
        with patch('backend.llm.urlopen') as call:
            with self.assertRaises(ProviderError) as caught:
                DeepSeekProvider('', 'deepseek-flash', RetryConfig(max_retries=0)).chat('Hello')
            self.assertEqual(caught.exception.status_code, 503)
            call.assert_not_called()

    def test_upstream_errors_are_sanitized(self):
        for code, expected in [(401, 503), (402, 503), (429, 503), (500, 502)]:
            with self.subTest(code=code), patch('backend.llm.urlopen', side_effect=HTTPError('https://api.deepseek.com', code, 'SECRET', {}, None)):
                with self.assertRaises(ProviderError) as caught:
                    DeepSeekProvider('test-key', 'deepseek-flash', RetryConfig(max_retries=0)).chat('Hello')
                self.assertEqual(caught.exception.status_code, expected)
                self.assertNotIn('SECRET', str(caught.exception))

    def test_timeout(self):
        with patch('backend.llm.urlopen', side_effect=TimeoutError):
            with self.assertRaises(ProviderError) as caught:
                DeepSeekProvider('test-key', 'deepseek-flash', RetryConfig(max_retries=0)).chat('Hello')
            self.assertEqual(caught.exception.status_code, 504)

    def test_missing_usage_is_not_fabricated(self):
        data = self.payload(); del data['usage']
        with patch('backend.llm.urlopen', return_value=io.BytesIO(json.dumps(data).encode())):
            with self.assertRaises(ProviderError):
                DeepSeekProvider('test-key', 'deepseek-flash', RetryConfig(max_retries=0)).chat('Hello')

    def test_invalid_messages(self):
        for value in ['', '   ', 'x' * 8001]:
            with self.assertRaises(ValidationError):
                ChatRequest(message=value)

    def test_route_accepts_provider_abstraction(self):
        class Stub:
            def chat(self, message):
                return message
        self.assertEqual(chat(ChatRequest(message='Hello'), Stub()), 'Hello')

    def test_route_maps_provider_error(self):
        class Failing:
            def chat(self, message):
                raise ProviderError(504, 'LLM provider timed out')
        with self.assertRaises(HTTPException) as caught:
            chat(ChatRequest(message='Hello'), Failing())
        self.assertEqual(caught.exception.status_code, 504)


if __name__ == '__main__':
    unittest.main()
