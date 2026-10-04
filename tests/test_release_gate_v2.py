"""Offline scoring fixtures only; no real model answers are regraded or requested."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend.config import LLMSettings
from eval.release_gate_v2 import (POLICY_SHA256, load_policy, register, evaluate_batch,
                                 quality_points, write_new)
from eval.run_regression import digest, load_suite


class ReleaseGateV2Tests(unittest.TestCase):
    def setUp(self):
        self.policy = load_policy()
        self.identity = {'prompt_version':'offline-fixture', 'prompt_sha256':'a'*64,
                         'provider':'deepseek', 'model':'deepseek-flash',
                         'runtime_config':LLMSettings().model_dump(exclude={'api_key'})}
        self.plan = register(self.identity)
        self.raws, self.reviews = [], []
        start = datetime.fromisoformat(self.plan['registered_at'])
        self.case_ids = list(self.policy['cases'])
        for n in range(3):
            records = [{**{k:v for k,v in self.identity.items() if k != 'runtime_config'},
                        'case_id':case_id, 'request_id':f'offline-{n}-{i}',
                        'execution_status':'success', 'schema_valid':True,
                        'answer':{'summary':'Offline fixture only', 'facts':[], 'interpretation':[], 'limitations':[]},
                        'usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2},
                        'latency_ms':1,'estimated_cost':'0.000001','cost_complete':True}
                       for i,case_id in enumerate(self.case_ids)]
            raw = {**deepcopy(self.identity), 'mode':'live_provider', 'run_id':f'offline-run-{n}',
                   'created_at':(start+timedelta(seconds=n+1)).isoformat(),
                   'collection_complete':True, 'suite':load_suite(), 'records':records}
            raw['evidence_sha256'] = digest(raw)
            cases = []
            for case_id in self.case_ids:
                dims = list(self.policy['hard_review_dimensions'])
                if self.policy['cases'][case_id]['required_language']:
                    dims.append('requested_language')
                cases.append({'case_id':case_id,
                              'hard':{d:{'verdict':'pass','rationale':'Offline fixture judgement.'} for d in dims},
                              'core':{'verdict':'pass','rationale':'Offline fixture judgement.'},
                              'quality':{d:{'score':2,'rationale':'Offline scoring fixture.'} for d in self.policy['quality_dimensions']}})
            self.raws.append(raw)
            self.reviews.append({'gate_version':self.policy['version'],'policy_sha256':POLICY_SHA256,
                                 'registration_sha256':self.plan['sha256'],
                                 'evidence_sha256':raw['evidence_sha256'],
                                 'reviewer':'Offline test; not a model-quality assessment','cases':cases})

    def evaluate(self):
        return evaluate_batch(self.plan,self.raws,self.reviews)

    def rehash(self,n):
        raw=self.raws[n]
        raw['evidence_sha256']=digest({k:v for k,v in raw.items() if k!='evidence_sha256'})
        self.reviews[n]['evidence_sha256']=raw['evidence_sha256']

    def test_all_pass_is_pure_and_does_not_call_provider(self):
        before=deepcopy((self.plan,self.raws,self.reviews))
        with patch('backend.llm.urlopen',side_effect=AssertionError('No paid request allowed')):
            result=self.evaluate()
        self.assertEqual(result['release_gate'],'passed')
        self.assertFalse(result['publication_performed'])
        self.assertEqual((self.plan,self.raws,self.reviews),before)
        self.assertEqual(result['threshold_percent'],85)
        self.assertEqual(result['policy_sha256'],POLICY_SHA256)

    def test_hard_and_core_failures_cannot_be_averaged_away(self):
        for field in ['hard','core']:
            with self.subTest(field=field):
                saved=deepcopy(self.reviews)
                decision=self.reviews[0]['cases'][0]
                (decision['hard']['factuality'] if field=='hard' else decision['core'])['verdict']='fail'
                result=self.evaluate()
                self.assertEqual(result['release_gate'],'blocked')
                self.assertEqual(result['rounds'][0]['quality_percent'],100)
                self.reviews=saved

    def test_schema_flag_cannot_hide_invalid_answer_or_execution_failure(self):
        for kind in ['missing','type','extra','flag','execution']:
            with self.subTest(kind=kind):
                saved=deepcopy((self.raws,self.reviews))
                row=self.raws[0]['records'][0]
                if kind=='missing':del row['answer']['facts']
                if kind=='type':row['answer']['facts']='wrong'
                if kind=='extra':row['answer']['extra']='wrong'
                if kind=='flag':row['schema_valid']=False
                if kind=='execution':row['execution_status']='error'
                self.rehash(0)
                result=self.evaluate()
                self.assertFalse(result['rounds'][0]['cases'][0]['hard_results']['schema'])
                self.assertEqual(result['release_gate'],'blocked')
                self.raws,self.reviews=saved

    def test_threshold_is_per_round_and_not_rounded_up(self):
        # 15*3*2 = 90 maximum; 77/90 passes, 76/90 fails.
        for count,expected in [(13,'passed'),(14,'blocked')]:
            saved=deepcopy(self.reviews)
            ratings=[r for c in self.reviews[0]['cases'] for r in c['quality'].values()]
            for item in ratings[:count]:item['score']=1
            result=self.evaluate()
            self.assertEqual(result['release_gate'],expected)
            self.reviews=saved

    def test_repeated_serious_issue_blocks_high_average(self):
        for n in [0,1]:self.reviews[n]['cases'][0]['quality']['relevance_actionability']['score']=0
        result=self.evaluate()
        self.assertEqual(result['release_gate'],'blocked')
        self.assertTrue(all(r['quality_pass'] for r in result['rounds']))
        self.assertEqual(len(result['repeated_serious_quality']),1)

    def test_serious_issue_across_cases_is_not_hidden_by_case_ids(self):
        for i in [0,1]:self.reviews[0]['cases'][i]['quality']['clarity_concision']['score']=0
        result=self.evaluate()
        self.assertEqual(result['release_gate'],'blocked')
        self.assertEqual(len(result['cross_case_serious_quality']),1)
        self.assertTrue(result['rounds'][0]['quality_pass'])

    def test_single_noncore_quality_issue_is_visible_not_automatic_hard_failure(self):
        self.reviews[0]['cases'][0]['quality']['clarity_concision']['score']=0
        result=self.evaluate()
        self.assertEqual(result['release_gate'],'passed')
        self.assertEqual(result['rounds'][0]['cases'][0]['serious_quality_dimensions'],['clarity_concision'])

    def test_na_excluded_only_when_predeclared_and_empty_is_not_perfect(self):
        ratings={'a':{'score':1,'rationale':'Some evidence.'},'b':{'score':None,'rationale':'Predeclared N/A.'}}
        self.assertEqual(quality_points(ratings,{'a':True,'b':False}),(1,2,[]))
        self.assertEqual(quality_points({'b':ratings['b']},{'b':False}),(0,0,[]))
        with self.assertRaises(ValueError):quality_points(ratings,{'a':True,'b':True})
        for value in [True,1.0,3,-1,'2']:
            with self.assertRaises(ValueError):quality_points({'a':{'score':value,'rationale':'x'}},{'a':True})

    def test_optional_evidence_request_not_core_failure_but_explicit_next_step_is(self):
        ids={c['case_id']:c for c in self.reviews[0]['cases']}
        # Reviewer can give full credit to a correct proof judgement without an unsolicited follow-up.
        self.assertIn('Answer whether',self.policy['cases']['uncertainty/supplied_hypothesis']['core_requirement'])
        self.assertEqual(self.evaluate()['release_gate'],'passed')
        ids['positive/limited_analysis']['core']={'verdict':'fail','rationale':'Wholly omitted explicitly requested next step.'}
        self.assertEqual(self.evaluate()['release_gate'],'blocked')

    def test_wrong_evidence_identity_or_historical_timestamp_rejected(self):
        for change in ['tamper','historical','config','suite','row_prompt','duplicate_run','duplicate_request']:
            with self.subTest(change=change):
                saved=deepcopy((self.raws,self.reviews))
                if change=='tamper':self.raws[0]['records'][0]['answer']['summary']='changed'
                if change=='historical':self.raws[0]['created_at']=self.policy['preregistered_at']
                if change=='config':self.raws[0]['runtime_config']['temperature']=0.8
                if change=='suite':self.raws[0]['suite']['cases'][0]['expected_behavior']='changed'
                if change=='row_prompt':self.raws[0]['records'][0]['prompt_sha256']='different'
                if change=='duplicate_run':self.raws[0]['run_id']=self.raws[1]['run_id']
                if change=='duplicate_request':self.raws[0]['records'][0]['request_id']=self.raws[1]['records'][0]['request_id']
                if change!='tamper':self.rehash(0)
                with self.assertRaises(ValueError):self.evaluate()
                self.raws,self.reviews=saved

    def test_missing_pending_duplicate_or_unbound_review_rejected(self):
        for change in ['missing','duplicate','pending','rationale','binding','registration','na']:
            with self.subTest(change=change):
                saved=deepcopy(self.reviews)
                r=self.reviews[0]
                if change=='missing':r['cases'].pop()
                if change=='duplicate':r['cases'][-1]=deepcopy(r['cases'][0])
                if change=='pending':r['cases'][0]['core']['verdict']='pending'
                if change=='rationale':r['cases'][0]['core']['rationale']=' '
                if change=='binding':r['evidence_sha256']='wrong'
                if change=='registration':r['registration_sha256']='wrong'
                if change=='na':r['cases'][0]['quality']['task_completeness']['score']=None
                with self.assertRaises(ValueError):self.evaluate()
                self.reviews=saved
        with self.assertRaises(ValueError):evaluate_batch(self.plan,self.raws[:2],self.reviews[:2])

    def test_policy_changes_and_secret_config_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'policy.json';changed=deepcopy(self.policy);changed['quality_threshold_percent']=84
            p.write_text(json.dumps(changed))
            with patch('eval.release_gate_v2.POLICY_PATH',p),self.assertRaises(ValueError):load_policy()
        identity=deepcopy(self.identity);identity['runtime_config']['api_key']='must-not-be-serialized'
        with self.assertRaises(ValueError):register(identity)

    def test_outputs_never_overwrite_existing_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'existing.json';p.write_text('historical evidence')
            with self.assertRaises(FileExistsError):write_new(p,{'replacement':True})
            self.assertEqual(p.read_text(),'historical evidence')
