import io
import json
import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from urllib.error import HTTPError

from pydantic import ValidationError
from backend.llm import DeepSeekProvider, ProviderError, RetryConfig, TokenUsage, get_provider
from backend.pricing import estimate_cost


def success():
    return io.BytesIO(json.dumps({'model':'deepseek-flash','choices':[{'message':{'content':'OK'},'finish_reason':'stop'}],
        'usage':{'prompt_tokens':100,'completion_tokens':20,'total_tokens':120,
                 'prompt_cache_hit_tokens':40,'prompt_cache_miss_tokens':60}}).encode())


def failure(code):
    return HTTPError('https://api.deepseek.com',code,'secret',{},None)


class RetryCostTests(unittest.TestCase):
    def test_retry_then_success_and_logs(self):
        with patch('backend.llm.urlopen',side_effect=[failure(429),failure(503),success()]) as call, patch('backend.llm.time.sleep') as sleep, patch('backend.llm.random.uniform',return_value=0), self.assertLogs('uvicorn.error',level='INFO') as logs:
            result=DeepSeekProvider('private-key','deepseek-flash').chat('private-prompt')
        self.assertEqual(call.call_count,3)
        self.assertEqual([c.args[0] for c in sleep.call_args_list],[1,2])
        self.assertEqual(result.retry_count,2)
        self.assertFalse(result.cost_complete)
        self.assertIsNotNone(result.estimated_cost)
        self.assertEqual(len(logs.records),4)
        records=[json.loads(r.getMessage().split(' ',1)[1]) for r in logs.records]
        self.assertEqual(len({r['request_id'] for r in records}),1)
        self.assertIsNone(records[0]['total_tokens'])
        self.assertIsNone(records[0]['estimated_cost'])
        self.assertEqual(records[-1]['status'],'success')
        self.assertNotIn('private',str(logs.output))

    def test_retry_exhaustion(self):
        for code in (429,500,502,503,504):
            with self.subTest(code=code), patch('backend.llm.urlopen',side_effect=lambda *a,**k: (_ for _ in ()).throw(failure(code))) as call, patch('backend.llm.time.sleep') as sleep:
                with self.assertRaises(ProviderError): DeepSeekProvider('key','deepseek-flash').chat('Hello')
                self.assertEqual(call.call_count,3)
                self.assertEqual(sleep.call_count,2)

    def test_deterministic_errors_never_retry(self):
        for code in (400,401,402,403,404,422):
            with self.subTest(code=code), patch('backend.llm.urlopen',side_effect=failure(code)) as call, patch('backend.llm.time.sleep') as sleep:
                with self.assertRaises(ProviderError): DeepSeekProvider('key','deepseek-flash').chat('Hello')
                self.assertEqual(call.call_count,1)
                sleep.assert_not_called()

    def test_timeout_no_duplicate_call(self):
        with patch('backend.llm.urlopen',side_effect=TimeoutError) as call:
            with self.assertRaises(ProviderError):
                DeepSeekProvider('key','deepseek-flash',RetryConfig(timeout_seconds=7)).chat('Hello')
            self.assertEqual(call.call_count,1)
            self.assertEqual(call.call_args.kwargs['timeout'],7)

    def test_config_bounds(self):
        for config in [{'max_retries':4},{'timeout_seconds':0},{'timeout_seconds':float('inf')},{'backoff_seconds':0}]:
            with self.assertRaises(ValidationError): RetryConfig(**config)
        with patch.dict('os.environ',{'LLM_TIMEOUT_SECONDS':'bad'}):
            with self.assertRaises(ProviderError) as caught: get_provider()
            self.assertEqual(caught.exception.code,'llm_invalid_config')

    def test_prices_and_boundaries(self):
        usage=TokenUsage(prompt_tokens=100,completion_tokens=20,total_tokens=120,prompt_cache_hit_tokens=40,prompt_cache_miss_tokens=60)
        for day,hour,tier,amount in [(14,1,'peak','0.00004224'),(14,4,'off_peak','0.00002112'),(14,6,'peak','0.00004224'),(14,10,'off_peak','0.00002112'),(13,2,'off_peak','0.00002112')]:
            self.assertEqual(estimate_cost('deepseek','deepseek-flash',usage,datetime(2026,9,day,hour,tzinfo=timezone.utc)),(amount,tier))
        self.assertEqual(estimate_cost('deepseek','unknown',usage,datetime.now(timezone.utc)),(None,None))
        usage.prompt_cache_hit_tokens=None
        self.assertIsNone(estimate_cost('deepseek','deepseek-flash',usage,datetime.now(timezone.utc))[0])

    def test_total_latency_includes_backoff(self):
        with patch('backend.llm.urlopen',side_effect=[failure(503),success()]), patch('backend.llm.time.sleep'), patch('backend.llm.time.monotonic',side_effect=[0,0,0,1,3,3,4,4,4]):
            result=DeepSeekProvider('key','deepseek-flash').chat('Hello')
        self.assertEqual(result.latency_ms,4000)
