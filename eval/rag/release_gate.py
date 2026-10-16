"""Independent, fail-closed gate; historical rubric/verdicts are not rewritten."""
import hashlib
import json
from pathlib import Path
from eval.rag.suite import digest, review
ROOT=Path(__file__).resolve().parent
POLICY_SHA256="ba0f11d6f4cba962f97055dcc9201c48cbaffc6d20d3cb774af04e8c58427441"

def policy():
    data=(ROOT/'release-gate-v1.json').read_bytes()
    if hashlib.sha256(data).hexdigest()!=POLICY_SHA256:raise ValueError('Release policy changed')
    return json.loads(data)

def evaluate(records, decisions, manifest, *, offline_pass, database_pass):
    from eval.rag.suite import load_suite
    gate=policy();rubric,cases,suite_sha=load_suite();fail=[]
    expected={c['id'] for c in cases}
    if len(records)!=15 or {r['case_id'] for r in records}!=expected:fail.append('incomplete_or_duplicate_records')
    if len(decisions)!=15 or {d['case_id'] for d in decisions}!=expected:fail.append('incomplete_or_duplicate_reviews')
    if suite_sha!=gate['suite_sha256']:fail.append('suite_changed')
    if manifest.get('mode')!='live':fail.append('not_live')
    if any(manifest.get('generation_config',{}).get(k)!=v for k,v in gate['runtime'].items()):fail.append('runtime_changed')
    if not offline_pass or not database_pass:fail.append('tests_not_passed')
    mapped={d['case_id']:d for d in decisions};scores=[];reviewed=[]
    manifest_hashes={x['case_id']:x['evidence_sha256'] for x in manifest.get('results',[])}
    for raw in records:
        cid=raw['case_id']
        if raw.get('prompt_sha256')!=gate['candidate_sha256'] or raw.get('suite_sha256')!=suite_sha:fail.append(cid+':identity')
        if manifest_hashes.get(cid)!=digest(raw):fail.append(cid+':manifest_hash')
        try:d=review(raw,mapped[cid],rubric)
        except (KeyError,ValueError,TypeError):fail.append(cid+':invalid_review');continue
        reviewed.append(d)
        if d['verdict']=='FAIL':fail.append(cid+':hard_or_core')
        if any(v is None or v==0 for v in d['quality'].values()):fail.append(cid+':quality_zero_or_unassessed')
        scores.extend(v for v in d['quality'].values() if v is not None)
    quality=sum(scores)/(2*len(scores)) if scores else 0
    if quality<gate['quality_threshold']:fail.append('quality_below_threshold')
    return {'gate_version':gate['version'],'policy_sha256':POLICY_SHA256,'prompt_sha256':gate['candidate_sha256'],'suite_sha256':suite_sha,
            'verdict':'FAIL' if fail else 'PASS','blockers':fail,'quality_normalized':quality,
            'stability':'one run; repeated-run stability not established','offline_pass':offline_pass,'database_pass':database_pass,
            'records_sha256':digest(records),'decisions_sha256':digest(decisions),'manifest_sha256':digest(manifest)}
