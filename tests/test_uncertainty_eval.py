import json
import unittest
from unittest.mock import patch

from backend.llm import DeepSeekProvider, ProviderError
from eval.run_uncertainty import CASES, collect
from test_retry_cost import success


class UncertaintyEvalTests(unittest.TestCase):
    def test_case_coverage_and_explicit_rubrics(self):
        cases = json.loads(CASES.read_text())
        self.assertEqual({c['id'] for c in cases}, {
            'unsupported_cause','unsupported_premise','false_premise',
            'supplied_hypothesis','missing_metric','unavailable_tools'})
        self.assertEqual(len(cases), 6)
        for case in cases:
            self.assertTrue(case['synthetic'])
            self.assertTrue(case['input'].startswith('Synthetic'))
            self.assertTrue(case['expected_behavior'])
            self.assertTrue(case['failure_condition'])

    def test_collection_preserves_metrics_without_auto_passing(self):
        cases = json.loads(CASES.read_text())[:1]
        with patch('backend.llm.urlopen', return_value=success()):
            result = collect(DeepSeekProvider('private-key', 'deepseek-flash'), cases)
        row = result['records'][0]
        self.assertEqual(row['verdict'], 'pending_review')
        self.assertEqual(row['usage']['total_tokens'], 120)
        for key in ['provider','model','latency_ms','retry_count','estimated_cost','pricing_version','cost_currency','cost_complete','answer']:
            self.assertIn(key, row)
        self.assertEqual(len(result['prompt_sha256']),64)
        self.assertEqual(len(result['cases_sha256']),64)
        self.assertNotIn('private-key', json.dumps(result))

    def test_failure_sanitization_and_continuation(self):
        class FailingProvider:
            def chat(self, message):
                raise ProviderError(502, 'private raw error', 'llm_invalid_output')
        result = collect(FailingProvider(), json.loads(CASES.read_text())[:2])
        self.assertEqual(len(result['records']), 2)
        for row in result['records']:
            self.assertEqual(row['verdict'], 'execution_error')
            self.assertIsNone(row['usage'])
            self.assertIsNone(row['estimated_cost'])
            self.assertFalse(row['cost_complete'])
        self.assertNotIn('private', json.dumps(result))
