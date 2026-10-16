"""Local-only pipeline. Authorization is server-owned, default deny."""
import importlib.util
import json
import os
import hashlib
import time
import uuid
import logging
from decimal import Decimal
from pathlib import Path
import psycopg
from backend.config import load_settings
from backend.llm import DeepSeekProvider
from backend.rag import generation, grounded_prompt_v5 as prompt
from backend.rag.models import AccessScope
from backend.rag.openai_embedding import OpenAIEmbeddingProvider,load_embedding_settings
from backend.rag.retrieval import retrieve,RetrievalConfig
from backend.rag.context import ContextBuilder,ContextConfig

logger=logging.getLogger('uvicorn.error')
RELEASE_PATH=Path(__file__).with_name('local_release.json')

def authorized_release():
    # Both an operator flag and a hash-pinned PASS receipt are required.
    if os.getenv('RAG_ENABLED','false').lower()!='true':return False
    expected=os.getenv('RAG_RELEASE_SHA256','')
    if len(expected)!=64 or not RELEASE_PATH.is_file():return False
    raw=RELEASE_PATH.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=expected:return False
    try:data=json.loads(raw)
    except (ValueError,TypeError):return False
    return data.get('verdict')=='PASS' and data.get('prompt_sha256')==prompt.SHA256 and not data.get('blockers')

def scoped_access():
    tenant=os.getenv('RAG_TENANT_ID','').strip()
    ids=json.loads(os.getenv('RAG_ALLOWED_DOCUMENT_IDS','[]'))
    if not tenant or not isinstance(ids,list) or any(not isinstance(x,str) for x in ids):raise ValueError('Invalid server scope')
    return AccessScope(tenant,tuple(str(uuid.UUID(x)) for x in ids))

def generator(provider,settings):
    # Isolate the existing immutable implementation rather than mutate its globals.
    spec=importlib.util.spec_from_file_location('rag_local_v5_generation',generation.__file__)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    module.prompt=prompt
    return module.GroundedGenerator(provider,settings)

class RagService:
    def run(self,query):
        started=time.monotonic();scope=scoped_access();settings=load_settings()
        required={'model':'deepseek-flash','temperature':0.2,'thinking':'disabled','max_output_tokens':512,'provider':'deepseek'}
        if any(getattr(settings,k)!=v for k,v in required.items()):raise ValueError('Generation config mismatch')
        embedding=OpenAIEmbeddingProvider(load_embedding_settings())
        with psycopg.connect(host=os.environ['POSTGRES_HOST'],port=os.environ['POSTGRES_PORT'],
            dbname=os.environ['POSTGRES_DB'],user=os.environ['POSTGRES_USER'],password=os.environ['POSTGRES_PASSWORD'],
            connect_timeout=5,options='-c statement_timeout=10000') as connection:
            result=retrieve(connection,embedding,query,scope=scope,config=RetrievalConfig(3,None))
        context=ContextBuilder(ContextConfig(8000)).build(result['results'])
        provider=DeepSeekProvider(settings.api_key.get_secret_value(),settings.model,settings=settings)
        answer=generator(provider,settings).generate(query,context)
        e=result['query_embedding'];g=answer['generation']
        cost=Decimal(g['estimated_cost'])+Decimal(e['estimated_cost_usd'] if e else '0') if g.get('estimated_cost') is not None else None
        # No query, text, paths, source body, credentials, or provider headers.
        logger.info('rag_request %s',json.dumps({'status':'success','request_id':g['request_id'],
            'prompt_version':g['prompt_version'],'prompt_sha256':g['prompt_sha256'],'context_sha256':answer['context_sha256'],
            'provider':g['provider'],'model':g['model'],'retrieved_chunks':len(result['results']),
            'source_labels':[b['source_label'] for b in context['blocks']],
            'generation_usage':g['usage'],'embedding_usage':e,'retry_count':g['retry_count'],
            'latency_ms':round((time.monotonic()-started)*1000),'estimated_total_usd':str(cost) if cost is not None else None}))
        return answer

def get_service():return RagService()
