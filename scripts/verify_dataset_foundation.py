"""Reproducible synthetic-only workflow benchmark. No API, DB, or customer input."""
from pathlib import Path
import json
import multiprocessing
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))
from backend.datasets import Column, FileSpec, ParseSettings, ParseFailure, parse_file, preview
from dataset_fixtures import workbook


def main():
    columns=(Column(name='地区'),Column(name='销售额',kind='money',unit='USD'),Column(name='日期',kind='date'))
    rows=[['地区','销售额','日期'],['华东','1200.50','2026-10-01'],['华南','800.25','2026-10-02']]
    settings=ParseSettings(enabled=True)
    def specification(ext, project='synthetic-project'):
        return FileSpec(project_id=project,dataset_id='sales-fixture',filename='synthetic.'+ext,columns=columns)
    csv=('\n'.join(','.join(r) for r in rows)+'\n').encode()
    results=[parse_file(csv,specification('csv'),settings) for _ in range(3)]
    xlsx=parse_file(workbook(rows),specification('xlsx'),settings)
    other=parse_file(csv,specification('csv','other-project'),settings)
    unclear=parse_file(b'date,amount,required\n01/02/2026,"1,000",\n',FileSpec(project_id='synthetic-project',dataset_id='ambiguity',filename='synthetic.csv',columns=(Column(name='date',kind='date'),Column(name='amount',kind='money',unit='USD'),Column(name='required',required=True))),settings)
    anomalies={}
    for label,payload,ext in [('invalid_utf8',b'name\n\xff','csv'),('cached_formula',workbook(rows,changes={'xl/worksheets/sheet1.xml':lambda s:s.replace('<is><t>1200.50</t></is>','<f>1+1</f><v>2</v>')}),'xlsx')]:
        try: parse_file(payload,specification(ext),settings)
        except ParseFailure as exc: anomalies[label]=exc.code
        else: raise AssertionError('unsafe fixture accepted')
    recovered=parse_file(csv,specification('csv'),settings)
    cells=[c for t in results[0].tables for r in t.rows for c in r]
    report={
        'evidence_version':'pv2-01-synthetic-workflow-v1',
        'scope':'local synthetic library; no user-study time savings or persisted dedup claim',
        'policy_version':results[0].policy_version,
        'manual_format_conversions':0,'manual_cell_edits':0,'explicit_column_mapping_sets':1,
        'equivalent_normalized_hash':results[0].normalized_sha256==xlsx.normalized_sha256,
        'distinct_raw_hashes':results[0].source_sha256!=xlsx.source_sha256,
        'duplicate_submissions':3,'unique_revision_ids':len({r.revision_id for r in results}),
        'cross_project_revision_separated':results[0].revision_id!=other.revision_id,
        'issue_collections':1,'issue_categories':sorted({i.code for i in unclear.issues}),
        'traceable_cells':sum(c.location.source_sha256==results[0].source_sha256 and c.location.row>=2 and c.location.column>=1 for c in cells),'total_cells':len(cells),
        'manual_recovery_resets':0,'recovery_same_revision':recovered.revision_id==results[0].revision_id,
        'lingering_workers':len(multiprocessing.active_children()),'paid_calls':0,
        'sample_csv_source_sha256':results[0].source_sha256,'sample_normalized_sha256':results[0].normalized_sha256,
        'sample_revision_id':results[0].revision_id,
        'authorized_synthetic_preview':preview(results[0],reveal_columns=(1,2,3)),
        'rejection_codes':anomalies,
    }
    assert report['equivalent_normalized_hash'] and report['distinct_raw_hashes'] and report['unique_revision_ids']==1
    assert report['cross_project_revision_separated'] and report['recovery_same_revision'] and not report['lingering_workers']
    assert report['traceable_cells']==report['total_cells'] and len(report['issue_categories'])==3
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__': main()
