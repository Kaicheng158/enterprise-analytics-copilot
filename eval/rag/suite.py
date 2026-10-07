"""Frozen RAG baseline policy, metrics and evidence-bound semantic review."""
import hashlib
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parent


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def load_suite():
    policy=json.loads((ROOT/'policy.json').read_text())
    cases=json.loads((ROOT/'cases.json').read_text())
    if len({c['id'] for c in cases})!=len(cases):raise ValueError('Duplicate case')
    for c in cases:
        if not c['expected_behavior'] or not c['failure_condition']:raise ValueError('Missing rubric')
        if not set(c['expected_sources'])<=set(d['name'] for d in c['documents']):raise ValueError('Invalid expected source')
    return policy,cases,digest({'policy':policy,'cases':cases})


def verify_freeze():
    p,c,sha=load_suite();f=json.loads((ROOT/'freeze.json').read_text())
    if f['suite_sha256']!=sha:raise ValueError('Frozen suite changed')
    from backend.rag.grounded_prompt import SHA256
    if SHA256!=f['prompt_sha256']:raise ValueError('Frozen prompt changed')
    for name,expected in f['code_sha256'].items():
        if hashlib.sha256((ROOT.parents[1]/name).read_bytes()).hexdigest()!=expected:raise ValueError('Frozen implementation changed')
    return p,c,f


def retrieval_metrics(case,results):
    expected=set(case['expected_sources'])
    ranked=[{'source':h['source_metadata']['source_uri'].rsplit('/',1)[-1],
             'chunk_id':h['chunk_id'],'rank':h['rank'],'distance':h['distance']} for h in results]
    found={x['source'] for x in ranked}
    return {'expected_relevant_sources':sorted(expected),'ranked':ranked,
            'hit_at_k':bool(expected&found) if expected else None,
            'source_recall_at_k':len(expected&found)/len(expected) if expected else None,
            'unexpected_sources':sorted(found-expected),'returned_count':len(results)}


def review(record,decision,policy):
    if decision['evidence_sha256']!=digest(record):raise ValueError('Review evidence mismatch')
    hard=decision['hard'];quality=decision['quality']
    if set(hard)!=set(policy['hard_invariants']) or any(type(x) is not bool for x in hard.values()):raise ValueError('Missing hard verdict')
    if type(decision['core_blocker']) is not bool:raise ValueError('Missing core verdict')
    if set(quality)!=set(policy['quality']):raise ValueError('Missing quality dimensions')
    if any(x is not None and (type(x) is not int or x not in (0,1,2)) for x in quality.values()):raise ValueError('Invalid quality score')
    if not decision.get('rationale') or not decision.get('claim_evidence') or not decision.get('reviewer'):raise ValueError('Missing semantic evidence')
    if any(x is None for x in quality.values()) and not decision.get('na_reason'):raise ValueError('Missing N/A rationale')
    scores=[v for v in quality.values() if v is not None]
    failure=record['status']!='generated' or not all(hard.values()) or decision['core_blocker']
    return {**decision,'verdict':'FAIL' if failure else 'REVIEWED_NO_HARD_BLOCKER',
            'quality_normalized':sum(scores)/(2*len(scores)) if scores else None}
