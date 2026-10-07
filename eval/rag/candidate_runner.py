"""Evaluate an unpublished prompt through an isolated copy of the unchanged generator.
No production module globals, active selection, policy or historical runner are mutated.
"""
import importlib.util
from pathlib import Path
from backend.rag import generation, grounded_prompt_v2
from .suite import verify_freeze
import json
import hashlib


def generator_class():
    spec=importlib.util.spec_from_file_location("rag_candidate_isolated_generation",generation.__file__)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.prompt=grounded_prompt_v2
    return module.GroundedGenerator


def candidate_freeze():
    p,c,base=verify_freeze()
    root=Path(__file__).resolve().parents[2]
    manifest=json.loads((root/'eval/rag/v2-candidate-freeze.json').read_text())
    for path,sha in manifest['candidate_code_sha256'].items():
        if hashlib.sha256((root/path).read_bytes()).hexdigest()!=sha:raise ValueError("Candidate code changed")
    if manifest['suite_sha256']!=base['suite_sha256'] or manifest['prompt_sha256']!=grounded_prompt_v2.SHA256:raise ValueError("Candidate freeze changed")
    return p,c,manifest


def execute(out):
    root=Path(__file__).resolve().parents[2]
    source=(root/'eval/rag/run.py').read_text()
    selection="selected=[c for c in cases if mode=='offline' or c['id'] in policy['live_case_ids']]"
    assert source.count(selection)==1
    source=source.replace(selection,"selected=list(cases)")
    ns={'__name__':'eval.rag.candidate_execution','__package__':'eval.rag'}
    exec(compile(source,'<all-case candidate orchestration>','exec'),ns)
    ns['verify_freeze']=candidate_freeze
    ns['GroundedGenerator']=generator_class()
    return ns['execute']('live',out)


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',required=True)
    execute(p.parse_args().out)
