from __future__ import annotations
from eval.planning.v1.suite_v1 import load_gate,load_suite,suite_sha256,gate_sha256

def evaluate_gate(control_evidence,live_rounds):
    gate=load_gate(); blockers=[]
    if gate['suite_sha256']!=suite_sha256(): blockers.append('suite_identity_mismatch')
    expected={case['id'] for case in load_suite()['cases']}
    records=control_evidence.get('records',[])
    if {r['case_id'] for r in records}!=expected or len(records)!=len(expected): blockers.append('deterministic_case_set_incomplete')
    if any(r['verdict']!='PASS' for r in records): blockers.append('deterministic_controls_failed')
    required=gate['minimum_independent_live_rounds']
    if len(live_rounds)<required: blockers.append('minimum_live_rounds_not_met')
    if live_rounds and any(round_['verdict']!='PASS' for round_ in live_rounds): blockers.append('live_round_failed')
    verdict='PASS' if not blockers else ('BLOCKED' if blockers==['minimum_live_rounds_not_met'] else 'FAIL')
    return {'gate_version':gate['gate_version'],'suite_sha256':suite_sha256(),'gate_sha256':gate_sha256(),'control_cases':len(records),'control_passes':sum(r['verdict']=='PASS' for r in records),'live_rounds_received':len(live_rounds),'verdict':verdict,'blockers':blockers}
