"""Authorized single all-case live orchestration; never overwrites earlier evidence."""
from pathlib import Path
import hashlib
import json
from eval.rag.candidate_v4 import candidate_freeze,generate_synthetic


def execute():
    root=Path(__file__).resolve().parents[2]
    plan=json.loads((root/'eval/rag/v4-live-plan.json').read_text())
    candidate_freeze()
    if hashlib.sha256(Path(__file__).read_bytes()).hexdigest()!=plan['orchestration_sha256']:
        raise ValueError('Live orchestration changed')
    source=(root/'eval/rag/run.py').read_text()
    selection="selected=[c for c in cases if mode=='offline' or c['id'] in policy['live_case_ids']]"
    call="record['response']=GroundedGenerator(provider,settings).generate(case['query'],context);record['status']='generated'"
    assert source.count(selection)==1 and source.count(call)==1
    source=source.replace(selection,'selected=list(cases)')
    source=source.replace(call,"record['response']=generate_synthetic(provider,settings,case['query'],context,failure_evidence_path=private_dir/(case['id']+'.json'));record['status']='generated'")
    private_dir=root/'eval/rag/diagnostics-offline-v4-live-001'
    private_dir.mkdir(mode=0o700,exist_ok=False)
    ns={'__name__':'eval.rag.v4_live_execution','__package__':'eval.rag'}
    exec(compile(source,'<v4 single full live orchestration>','exec'),ns)
    ns.update(verify_freeze=candidate_freeze,generate_synthetic=generate_synthetic,private_dir=private_dir)
    return ns['execute']('live',root/'eval/rag/v4-live-001')


if __name__=='__main__':execute()
