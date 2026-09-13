import asyncio
import io
import json
import unittest
from http.client import IncompleteRead, RemoteDisconnected
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from backend.llm import DeepSeekProvider, ProviderError, get_provider, RetryConfig
from backend.main import app


class ErrorTests(unittest.TestCase):
    def check_error(self, error, status, code):
        with patch('backend.llm.urlopen', side_effect=error):
            with self.assertRaises(ProviderError) as caught:
                DeepSeekProvider('private-key', 'deepseek-flash', RetryConfig(max_retries=0)).chat('private-prompt')
        self.assertEqual(caught.exception.status_code, status)
        self.assertEqual(caught.exception.code, code)
        self.assertNotIn('private', str(caught.exception))

    def test_http_categories(self):
        for status, output, code in [
            (400, 502, 'llm_request_rejected'), (401, 503, 'llm_authentication_failed'),
            (402, 503, 'llm_insufficient_balance'), (403, 503, 'llm_access_denied'),
            (422, 502, 'llm_request_rejected'), (429, 503, 'llm_rate_limited'),
            (500, 502, 'llm_upstream_error'), (503, 502, 'llm_upstream_error'),
        ]:
            with self.subTest(status=status):
                body = io.BytesIO(b'private-key private-prompt')
                self.check_error(HTTPError('https://api.deepseek.com', status, 'private', {}, body), output, code)
                self.assertTrue(body.closed)

    def test_transport_failures(self):
        for error, status, code in [
            (TimeoutError('private'), 504, 'llm_timeout'),
            (URLError(TimeoutError('private')), 504, 'llm_timeout'),
            (URLError('private'), 502, 'llm_connection_failed'),
            (ConnectionResetError('private'), 502, 'llm_connection_failed'),
            (IncompleteRead(b'private'), 502, 'llm_connection_failed'),
            (RemoteDisconnected('private'), 502, 'llm_connection_failed'),
        ]:
            with self.subTest(error=type(error).__name__):
                self.check_error(error, status, code)

    def test_bad_payloads(self):
        base = {'model': 'deepseek-flash', 'choices': [{'message': {'content': 'OK'}, 'finish_reason': 'stop'}],
                'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}}
        bad_details = {**base, 'usage': {**base['usage'], 'completion_tokens_details': ['bad']}}
        for raw in [b'not JSON', b'\xff', b'null', b'[]', b'{}', json.dumps(bad_details).encode(),
                    json.dumps({**base, 'usage': None}).encode(), json.dumps({**base, 'choices': []}).encode()]:
            with self.subTest(raw=raw), patch('backend.llm.urlopen', return_value=io.BytesIO(raw)):
                with self.assertRaises(ProviderError) as caught:
                    DeepSeekProvider('private-key', 'deepseek-flash', RetryConfig(max_retries=0)).chat('private-prompt')
                self.assertEqual(caught.exception.code, 'llm_invalid_response')

    def test_asgi_error_contract(self):
        class FailingProvider:
            def chat(self, message):
                raise ProviderError(503, 'LLM provider rate limit reached', 'llm_rate_limited')
        async def request():
            messages = []
            async def receive():
                return {'type': 'http.request', 'body': b'{"message":"Hello"}', 'more_body': False}
            async def send(message):
                messages.append(message)
            await app({'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1',
                       'method': 'POST', 'scheme': 'http', 'path': '/chat', 'raw_path': b'/chat',
                       'query_string': b'', 'headers': [(b'content-type', b'application/json')],
                       'server': ('test', 80), 'client': ('test', 123)}, receive, send)
            return messages
        app.dependency_overrides[get_provider] = lambda: FailingProvider()
        try:
            messages = asyncio.run(request())
        finally:
            app.dependency_overrides.pop(get_provider, None)
        self.assertEqual(messages[0]['status'], 503)
        body = json.loads(b''.join(m.get('body', b'') for m in messages))
        self.assertEqual(body, {'detail': {'code': 'llm_rate_limited', 'message': 'LLM provider rate limit reached'}})
