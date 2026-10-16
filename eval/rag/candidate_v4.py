"""Explicit synthetic candidate adapter; no live runner or provider construction."""
import hashlib
import importlib.util
import json
from pathlib import Path
from backend.llm import ProviderError
from backend.rag import generation, grounded_prompt_v4
from backend.rag.attribution_diagnostics import DiagnosticGroundedGenerator
from eval.rag.diagnostic_evidence import save_synthetic_failure
from eval.rag.suite import verify_freeze


def candidate_freeze():
    policy,cases,baseline=verify_freeze()
    root=Path(__file__).resolve().parents[2]
    frozen=json.loads((root/'eval/rag/v4-candidate-freeze.json').read_text())
    for path,sha in frozen['code_sha256'].items():
        if hashlib.sha256((root/path).read_bytes()).hexdigest()!=sha:
            raise ValueError('V4 dependency checksum mismatch')
    if frozen['suite_sha256']!=baseline['suite_sha256'] or frozen['prompt_sha256']!=grounded_prompt_v4.SHA256:
        raise ValueError('V4 freeze mismatch')
    grounded_prompt_v4.messages('','')
    return policy,cases,frozen


def generator_class():
    candidate_freeze()
    spec=importlib.util.spec_from_file_location('rag_v4_isolated_generation',generation.__file__)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    module.prompt=grounded_prompt_v4
    return module.GroundedGenerator


def generate_synthetic(provider,settings,query,context,*,failure_evidence_path):
    """Trusted synthetic/eval entry only; caller supplies provider and private new path.
    Uses existing diagnostics and validator. Rejects overwriting evidence before a call.
    No retries beyond the supplied provider; no answer repair.
    """
    path=Path(failure_evidence_path)
    if path.exists() or path.is_symlink():raise FileExistsError('Evidence path already exists')
    if not path.parent.is_dir():raise ValueError('Evidence directory must already exist')
    service=DiagnosticGroundedGenerator(provider,settings,capture_mode='synthetic_eval',generator_type=generator_class())
    try:return service.generate(query,context)
    except ProviderError as error:
        if hasattr(error,'synthetic_eval_evidence'):save_synthetic_failure(path,error)
        raise
