"""One authorized generation using the saved synthetic Phase 3.5 retrieval result."""
import json
import logging
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.config import load_settings
from backend.llm import DeepSeekProvider
from backend.rag.context import ContextBuilder,ContextConfig
from backend.rag.generation import GroundedGenerator


def main():
    root=Path(__file__).resolve().parents[1]
    evidence=json.loads((root/'docs/phase3-retrieval-smoke.json').read_text())
    context=ContextBuilder(ContextConfig(3000)).build(evidence['retrieval']['results'])
    settings=load_settings()
    provider=DeepSeekProvider(settings.api_key.get_secret_value(),settings.model,settings=settings)
    query="How is a processed request counted when it is reopened?"
    result=GroundedGenerator(provider,settings).generate(query,context)
    saved={'query':query,'synthetic_only':True,'generation_config':settings.model_dump(exclude={'api_key'}),'result':result}
    (root/'docs/phase3-generation-smoke.json').write_text(json.dumps(saved,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(saved,ensure_ascii=False,indent=2))


if __name__=='__main__':
    logging.basicConfig(level=logging.INFO)
    main()
