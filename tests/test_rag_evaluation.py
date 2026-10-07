import unittest
from eval.rag.suite import load_suite,verify_freeze,retrieval_metrics,review,digest


class RagEvaluationTests(unittest.TestCase):
    def test_frozen_suite_and_coverage(self):
        p,c,f=verify_freeze();self.assertEqual(len(c),15);self.assertEqual(len(p['live_case_ids']),6)
        self.assertEqual(f['suite_sha256'],load_suite()[2])
    def test_retrieval_metrics_no_false_perfect_empty(self):
        self.assertIsNone(retrieval_metrics({'expected_sources':[]},[])['source_recall_at_k'])
        h={'chunk_id':'c','rank':1,'distance':0.3,'source_metadata':{'source_uri':'local://eval/a.txt'}}
        m=retrieval_metrics({'expected_sources':['a.txt','b.txt']},[h,h]);self.assertEqual(m['source_recall_at_k'],0.5)
        self.assertTrue(m['hit_at_k'])
    def decision(self,record):
        p,_,_=load_suite()
        return {'evidence_sha256':digest(record),'hard':{k:True for k in p['hard_invariants']},'core_blocker':False,
                'quality':{k:2 for k in p['quality']},'rationale':'Reviewed against supplied source.',
                'claim_evidence':[{'claim':'12','evidence':'supplied 12'}],'reviewer':'fixture'}
    def test_valid_citation_cannot_compensate_unsupported_claim(self):
        p,_,_=load_suite();r={'status':'generated'};d=self.decision(r);d['hard']['grounding']=False
        self.assertEqual(review(r,d,p)['verdict'],'FAIL')
    def test_core_blocker_cannot_average_away(self):
        p,_,_=load_suite();r={'status':'generated'};d=self.decision(r);d['core_blocker']=True
        self.assertEqual(review(r,d,p)['verdict'],'FAIL')
    def test_error_cannot_be_semantic_pass(self):
        p,_,_=load_suite();r={'status':'generation_error'};self.assertEqual(review(r,self.decision(r),p)['verdict'],'FAIL')
    def test_review_bound_to_exact_evidence_and_required_fields(self):
        p,_,_=load_suite();r={'status':'generated'};d=self.decision(r)
        with self.assertRaises(ValueError):review({**r,'changed':True},d,p)
        d['hard'].pop('security')
        with self.assertRaises(ValueError):review(r,d,p)
    def test_scores_strict_and_na_excluded(self):
        p,_,_=load_suite();r={'status':'generated'};d=self.decision(r);d['quality']['clarity_concision']=3
        with self.assertRaises(ValueError):review(r,d,p)
        d['quality']={k:None for k in p['quality']};d['na_reason']='Fixture without output'
        self.assertIsNone(review(r,d,p)['quality_normalized'])
