import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend.config import LLMSettings
from backend.llm import DeepSeekProvider
from eval.run_regression import load_suite, run_suite, review_report, digest
from test_retry_cost import success, failure


class RegressionRunnerTests(unittest.TestCase):
    def config(self):
        return LLMSettings().model_dump(exclude={'api_key'})

    def one_case(self):
        suite=load_suite(); suite['cases']=suite['cases'][:1]
        return suite

    def run_mock(self):
        with patch('backend.llm.urlopen',return_value=success()):
            return run_suite(DeepSeekProvider('private-key','deepseek-flash'),self.config(),self.one_case())

    def decisions(self,report,verdict='pass'):
        return {'evidence_sha256':report['evidence_sha256'],'reviewer':'offline test fixture, not live grading',
                'cases':[{'case_id':r['case_id'],'verdict':verdict,'rationale':'Test-only review.'} for r in report['records']]}

    def test_reuses_source_cases_and_has_positive_coverage(self):
        suite=load_suite()
        self.assertEqual(len(suite['cases']),15)
        for group in ['uncertainty','injection','positive']:
            source=json.loads(Path(f'eval/{group}_cases.json').read_text())
            actual=[{**c,'id':c['id'].split('/',1)[1]} for c in suite['cases'] if c['id'].startswith(group+'/')]
            self.assertEqual(actual,source)
        self.assertEqual({c['id'] for c in suite['cases'] if c['id'].startswith('positive/')},
                         {'positive/metric_change','positive/limited_analysis','positive/user_language'})

    def test_hash_changes_with_rubric_changes(self):
        suite=load_suite(); modified=copy.deepcopy(suite['cases'])
        modified[0]['expected_behavior']+=' Additional criterion.'
        self.assertNotEqual(digest(modified),suite['cases_sha256'])
        self.assertEqual(suite['suite_sha256'],digest({'definition':suite['definition'],'cases':suite['cases']}))

    def test_json_validity_remains_pending_and_metadata_is_present(self):
        report=self.run_mock()
        self.assertTrue(report['records'][0]['schema_valid'])
        self.assertEqual(report['summary']['release_gate'],'blocked')
        self.assertEqual(report['records'][0]['verdict'],'pending_review')
        for key in ['prompt_version','prompt_sha256','provider','model','runtime_config']:
            self.assertIn(key,report)
        for key in ['usage','latency_ms','estimated_cost','prompt_version','prompt_sha256']:
            self.assertIn(key,report['records'][0])
        self.assertNotIn('private-key',json.dumps(report))
        self.assertNotIn('api_key',report['runtime_config'])

    def test_review_requires_exact_evidence_and_all_cases(self):
        report=self.run_mock(); decisions=self.decisions(report)
        reviewed=review_report(report,decisions)
        self.assertEqual(reviewed['summary']['release_gate'],'passed')
        self.assertEqual(report['records'][0]['verdict'],'pending_review')
        self.assertEqual(review_report(report,self.decisions(report,'fail'))['summary']['release_gate'],'blocked')
        for changed in [{**decisions,'evidence_sha256':'wrong'}, {**decisions,'cases':[]},
                        {**decisions,'cases':decisions['cases']*2}, {**decisions,'reviewer':''}]:
            with self.assertRaises(ValueError): review_report(report,changed)
        altered=copy.deepcopy(report);altered['records'][0]['answer']['summary']='changed'
        with self.assertRaises(ValueError): review_report(altered,decisions)

    def test_provider_failure_continues_and_cannot_pass(self):
        suite=load_suite();suite['cases']=suite['cases'][:2]; checkpoints=[]
        with patch('backend.llm.urlopen',side_effect=[failure(401),success()]):
            report=run_suite(DeepSeekProvider('key','deepseek-flash'),self.config(),suite,
                             lambda r:checkpoints.append(copy.deepcopy(r)))
        self.assertEqual(len(report['records']),2)
        self.assertEqual(report['records'][0]['verdict'],'execution_error')
        self.assertIsNone(report['records'][0]['usage'])
        self.assertIsNone(report['records'][0]['estimated_cost'])
        self.assertFalse(checkpoints[0]['collection_complete'])
        self.assertEqual(checkpoints[0]['summary']['release_gate'],'blocked')
        self.assertTrue(checkpoints[-1]['collection_complete'])
        with self.assertRaises(ValueError): review_report(report,self.decisions(report))

    def test_duplicate_or_incomplete_cases_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);manifest=root/'suite.json'
            manifest.write_text(json.dumps({'sources':['cases.json']}))
            case=load_suite()['cases'][0]
            for cases in [[case,case],[{**case,'failure_condition':''}]]:
                (root/'cases.json').write_text(json.dumps(cases))
                with self.assertRaises(ValueError):load_suite(manifest)
