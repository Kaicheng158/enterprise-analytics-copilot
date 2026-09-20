"""Small unified suite runner with explicit, evidence-bound semantic review."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import uuid

from backend.config import load_settings
from backend.llm import PROVIDER_ADAPTERS
from backend.output import AnalyticsAnswer
from backend.prompt_registry import prompt_metadata
from backend.prompts import build_messages
from eval.run_uncertainty import collect

MANIFEST = Path(__file__).with_name('regression_suite.json')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def load_suite(manifest=MANIFEST):
    definition = json.loads(manifest.read_text())
    cases, source_hashes = [], {}
    for source in definition['sources']:
        if Path(source).name != source:
            raise ValueError('Suite sources must be local filenames')
        items = json.loads((manifest.parent / source).read_text())
        source_hashes[source] = digest(items)
        for item in items:
            if not all(isinstance(item.get(k),str) and item[k].strip()
                       for k in ['id','input','expected_behavior','failure_condition']):
                raise ValueError('Incomplete case rubric')
            if item.get('synthetic') is not True:
                raise ValueError('Only synthetic cases are permitted')
            cases.append({**item,'id':Path(source).stem.removesuffix('_cases')+'/'+item['id']})
    if len({c['id'] for c in cases}) != len(cases):
        raise ValueError('Duplicate case IDs')
    return {'definition':definition, 'source_hashes':source_hashes,
            'cases':cases, 'cases_sha256':digest(cases),
            'suite_sha256':digest({'definition':definition,'cases':cases})}


def summarize(report):
    counts = dict(Counter(r['verdict'] for r in report['records']))
    passed = (report['collection_complete'] and len(report['records']) == len(report['suite']['cases'])
              and all(r['verdict'] == 'pass' for r in report['records']))
    return {'counts':counts,'release_gate':'passed' if passed else 'blocked'}


def run_suite(provider, runtime_config, suite, checkpoint=None):
    report = {'run_id':str(uuid.uuid4()), 'created_at':datetime.now(timezone.utc).isoformat(),
              'mode':'live_provider', **prompt_metadata(build_messages('')),
              'provider':runtime_config['provider'], 'model':runtime_config['model'],
              'runtime_config':runtime_config, 'suite':suite,
              'records':[], 'collection_complete':False}
    for case in suite['cases']:
        row = collect(provider,[case])['records'][0]
        row.setdefault('provider',report['provider'])
        row.setdefault('model',report['model'])
        row.setdefault('retry_count',None)
        if row['execution_status'] == 'success':
            AnalyticsAnswer.model_validate(row['answer'])
            row['schema_valid'] = True
            row['verdict'] = 'pending_review'
        else:
            row['schema_valid'] = False if row['error_code']=='llm_invalid_output' else None
            row['verdict'] = 'execution_error'
        report['records'].append(row)
        report['summary'] = summarize(report)
        if checkpoint:
            checkpoint(report)
    report['collection_complete'] = True
    report['summary'] = summarize(report)
    report['evidence_sha256'] = digest(report)
    if checkpoint:
        checkpoint(report)
    return report


def review_report(report, decisions):
    # Bind judgements to this exact unreviewed run, never silently another run.
    if digest({k:v for k,v in report.items() if k!='evidence_sha256'}) != report.get('evidence_sha256'):
        raise ValueError('Evidence changed or incomplete')
    if decisions.get('evidence_sha256') != report['evidence_sha256']:
        raise ValueError('Review targets different evidence')
    if not isinstance(decisions.get('reviewer'),str) or not decisions['reviewer'].strip():
        raise ValueError('Reviewer required')
    rows = decisions['cases']
    if len({r['case_id'] for r in rows}) != len(rows):
        raise ValueError('Duplicate review case')
    by_id = {r['case_id']:r for r in rows}
    if set(by_id) != {r['case_id'] for r in report['records']}:
        raise ValueError('Review must cover exactly the collected cases')
    result = json.loads(json.dumps(report))
    for row in result['records']:
        decision = by_id[row['case_id']]
        if decision.get('verdict') not in ['pass','fail'] or not isinstance(decision.get('rationale'),str) or not decision['rationale'].strip():
            raise ValueError('Verdict and rationale required')
        if decision['verdict']=='pass' and (row['execution_status']!='success' or row['schema_valid'] is not True):
            raise ValueError('Cannot pass execution/schema failure')
        row.update(verdict=decision['verdict'],review_note=decision['rationale'])
    result['reviewer'] = decisions['reviewer']
    result['reviewed_at'] = datetime.now(timezone.utc).isoformat()
    result['summary'] = summarize(result)
    return result


def write_checkpoint(path, report):
    temporary = path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    temporary.replace(path)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Full synthetic live suite or evidence-bound semantic review.')
    sub = parser.add_subparsers(dest='command',required=True)
    run = sub.add_parser('run')
    run.add_argument('--output',type=Path,required=True)
    review = sub.add_parser('review')
    review.add_argument('--evidence',type=Path,required=True)
    review.add_argument('--decisions',type=Path,required=True)
    review.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    # Exclusive creation: protect previously recorded evidence.
    with args.output.open('x') as f:
        f.write('{}\n')
    if args.command == 'run':
        settings = load_settings()
        provider = PROVIDER_ADAPTERS[settings.provider](api_key=settings.api_key.get_secret_value(),model=settings.model,settings=settings)
        report = run_suite(provider,settings.model_dump(exclude={'api_key'}),load_suite(),lambda r:write_checkpoint(args.output,r))
    else:
        report = review_report(json.loads(args.evidence.read_text()),json.loads(args.decisions.read_text()))
        write_checkpoint(args.output,report)
    print(json.dumps(report['summary']))
    if report['summary']['release_gate'] != 'passed':
        raise SystemExit(1)  # Pending/error/fail must not look like a successful release gate.
