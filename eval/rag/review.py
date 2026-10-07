"""Apply explicit evidence reviews; never generate semantic verdicts from keywords."""
import argparse
from decimal import Decimal
import json
from pathlib import Path
from .suite import verify_freeze,review,digest


def apply(run,decisions,out):
    policy,_,freeze=verify_freeze();run=Path(run);out=Path(out)
    if out.exists():raise ValueError('Never overwrite review evidence')
    manifest=json.loads((run/'manifest.json').read_text())
    if manifest['freeze']!=freeze:raise ValueError('Run freeze mismatch')
    items=json.loads(Path(decisions).read_text());by_id={d['case_id']:d for d in items}
    if len(by_id)!=len(items) or set(by_id)!=set(manifest['selected_cases']):raise ValueError('Review coverage mismatch')
    results=[];input_tokens=output_tokens=embedding_tokens=0;cost=Decimal(0);complete=True;latencies=[];embedding_latencies=[]
    for item in manifest['results']:
        record=json.loads((run/(item['case_id']+'.json')).read_text())
        if digest(record)!=item['evidence_sha256']:raise ValueError('Run evidence changed')
        reviewed=review(record,by_id[item['case_id']],policy)
        gen=record.get('response',{}).get('generation',record.get('error_telemetry',{}))
        usage=gen.get('usage',{})
        input_tokens+=usage.get('prompt_tokens',0);output_tokens+=usage.get('completion_tokens',0)
        complete=complete and gen.get('cost_complete',False)
        cost+=Decimal(gen.get('estimated_cost') or '0');latencies.append(gen.get('latency_ms'))
        embeddings=[req for doc in record['document_embedding'] for req in doc['requests']]
        if record['retrieval']['query_embedding']:embeddings.append(record['retrieval']['query_embedding'])
        for e in embeddings:
            embedding_tokens+=e['input_tokens'];cost+=Decimal(e['estimated_cost_usd']);complete=complete and e['cost_complete'];embedding_latencies.append(e['latency_ms'])
        results.append({**reviewed,'retrieval_metrics':record['retrieval_metrics']})
    scores=[r['quality_normalized'] for r in results if r['quality_normalized'] is not None]
    summary={'suite_sha256':freeze['suite_sha256'],'prompt_sha256':freeze['prompt_sha256'],'results':results,
             'hard_or_core_failure_count':sum(r['verdict']=='FAIL' for r in results),
             'quality_mean_descriptive':sum(scores)/len(scores) if scores else None,
             'telemetry':{'generation_input_tokens':input_tokens,'generation_output_tokens':output_tokens,
                          'embedding_tokens':embedding_tokens,'generation_latency_ms':latencies,'embedding_latency_ms':embedding_latencies,
                          'estimated_total_usd':str(cost),'cost_complete':complete},
             'scope':'Baseline only. No production release verdict. Unselected live cases are not validated.'}
    with out.open('x') as f:json.dump(summary,f,ensure_ascii=False,indent=2);f.write('\n')
    return summary


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run',required=True);p.add_argument('--decisions',required=True);p.add_argument('--out',required=True)
    a=p.parse_args();print(json.dumps(apply(a.run,a.decisions,a.out),ensure_ascii=False,indent=2))
