"""Explicit local synthetic failure writer; no network, telemetry or overwrite."""
import json
import os


def save_synthetic_failure(path,error):
    evidence=getattr(error,'synthetic_eval_evidence',None)
    if evidence is None:raise ValueError('No authorized synthetic evidence')
    record={'error_code':error.code,**evidence}
    data=(json.dumps(record,ensure_ascii=False,indent=2)+'\n').encode()
    # O_EXCL also rejects an existing symlink. Caller selects a trusted local directory.
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'wb') as f:f.write(data)
