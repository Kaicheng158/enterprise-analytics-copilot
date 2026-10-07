"""Operator runner: python -m eval.rag.run --mode offline|live --out <new directory>."""
import argparse
from dataclasses import asdict
from datetime import datetime,timezone
import json
from pathlib import Path
import tempfile
import uuid
import psycopg
from psycopg import sql
from backend.config import LLMSettings,load_settings
from backend.llm import ChatResult,DeepSeekProvider,ProviderError,TokenUsage
from backend.output import AnalyticsAnswer
from backend.rag.context import ContextBuilder,ContextConfig
from backend.rag.embedding import EmbeddingBatch
from backend.rag.embedding_store import persist_embeddings
from backend.rag.ingestion import LocalDocumentLoader
from backend.rag.models import EmbeddingProfile,AccessScope
from backend.rag.openai_embedding import OpenAIEmbeddingProvider,load_embedding_settings
from backend.rag.retrieval import RetrievalConfig,retrieve
from backend.rag.storage import ingest
from backend.rag.generation import GroundedGenerator
from scripts.migrate import connect_kwargs,apply_migrations
from .suite import verify_freeze,retrieval_metrics,digest


class FakeEmbedding:
    # All equal vectors intentionally test plumbing/stable ordering, NOT semantic retrieval.
    profile=EmbeddingProfile('fake','rag-eval-fixture','v1',3,'fixture-v1')
    def embed_documents(self,texts):return EmbeddingBatch(tuple((1.,0.,0.) for _ in texts),self.profile,0,0,0,'0',True)
    def embed_query(self,text):return self.embed_documents((text,))


class FakeGeneration:
    def __init__(self,case,context):self.case=case;self.context=context
    def generate_messages(self,messages,metadata):
        facts=[]
        if self.case.get('probe')=='fabricated_label':facts=['Count is 12 [S-0000000000000000]']
        if self.case.get('probe')=='valid_label_unsupported':
            label=self.context['blocks'][0]['source_label'];facts=[f'Enterprise revenue is $9 million [{label}]']
        return ChatResult(answer=AnalyticsAnswer(summary='Deterministic transport fixture; not a semantic answer.',facts=facts,
            interpretation=[],limitations=[]),provider='fake',model='fixture',usage=TokenUsage(prompt_tokens=0,completion_tokens=0,total_tokens=0),
            latency_ms=0,finish_reason='stop',**metadata)


def execute(mode,out):
    policy,cases,freeze=verify_freeze()
    out=Path(out);out.mkdir(parents=True,exist_ok=False) # never overwrite historical evidence
    settings=load_settings() if mode=='live' else LLMSettings()
    required=policy['fixed_runtime']['generation']
    if any(getattr(settings,k)!=v for k,v in required.items()):raise ValueError('Generation config differs from preregistration')
    embedding=OpenAIEmbeddingProvider(load_embedding_settings()) if mode=='live' else FakeEmbedding()
    if mode=='live' and (embedding.profile.model!='text-embedding-3-small' or embedding.profile.dimensions!=1536):raise ValueError('Embedding profile mismatch')
    selected=[c for c in cases if mode=='offline' or c['id'] in policy['live_case_ids']]
    manifest={'mode':mode,'created_utc':datetime.now(timezone.utc).isoformat(),'freeze':freeze,
              'generation_config':settings.model_dump(exclude={'api_key'}),'embedding_profile':asdict(embedding.profile),
              'selected_cases':[c['id'] for c in selected], 'semantic_status':'PENDING_REVIEW','results':[]}
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    kwargs=connect_kwargs();name='eac_test_rag_'+uuid.uuid4().hex
    with psycopg.connect(**kwargs,autocommit=True) as admin:
        admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
        try:
            with psycopg.connect(**{**kwargs,'dbname':name},autocommit=True) as db,tempfile.TemporaryDirectory() as tmp:
                apply_migrations(db)
                for case in selected:
                    tenant='rag-eval-'+case['id'];root=Path(tmp)/case['id'];root.mkdir()
                    loader=LocalDocumentLoader(root,'rag-eval-'+case['id']);docs=[];embedding_records=[];old_revisions=[]
                    for fixture in case['documents']:
                        path=root/fixture['name']
                        if case.get('old_text'):
                            path.write_text(case['old_text']);old=ingest(db,loader,Path(fixture['name']),tenant_id=tenant)
                            old_revisions.append(old['revision'])
                            embedding_records.append(persist_embeddings(db,embedding,tenant_id=tenant,document_id=old['document_id'],revision=old['revision']))
                        path.write_text(fixture['text'])
                        doc=ingest(db,loader,Path(fixture['name']),tenant_id=tenant);docs.append(doc)
                        embedding_records.append(persist_embeddings(db,embedding,tenant_id=tenant,document_id=doc['document_id'],revision=doc['revision']))
                    runtime=policy['fixed_runtime']
                    retrieval=retrieve(db,embedding,case['query'],scope=AccessScope(tenant,tuple(d['document_id'] for d in docs)),
                                       config=RetrievalConfig(runtime['top_k'],runtime['max_distance']))
                    context=ContextBuilder(ContextConfig(runtime['context_max_chars'])).build(retrieval['results'])
                    record={'case_id':case['id'],'suite_sha256':freeze['suite_sha256'],'prompt_sha256':freeze['prompt_sha256'],
                            'query':case['query'],'expected_behavior':case['expected_behavior'],'failure_condition':case['failure_condition'],
                            'document_embedding':embedding_records,'documents':docs,'retrieval':retrieval,
                            'retrieval_metrics':retrieval_metrics(case,retrieval['results']),'context':context,
                            'current_revision_filter_pass':not any(h['source_metadata']['document_revision'] in old_revisions for h in retrieval['results']),
                            'semantic_verdict':'PENDING_REVIEW'}
                    provider=DeepSeekProvider(settings.api_key.get_secret_value(),settings.model,settings=settings) if mode=='live' else FakeGeneration(case,context)
                    try:
                        record['response']=GroundedGenerator(provider,settings).generate(case['query'],context);record['status']='generated'
                    except ProviderError as error:
                        record.update(status='generation_error',error_code=error.code,error_telemetry=error.telemetry)
                    target=out/(case['id']+'.json');target.write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
                    manifest['results'].append({'case_id':case['id'],'status':record['status'],'evidence_sha256':digest(record)})
                    (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
                    print(case['id'],record['status'],flush=True)
        finally:
            admin.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))
    return manifest


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--mode',choices=['offline','live'],required=True);parser.add_argument('--out',required=True)
    a=parser.parse_args();execute(a.mode,a.out)
