import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch,Mock
import asyncio
from types import SimpleNamespace
from backend.rag_app import app
from backend.llm import ProviderError
from backend.rag.api import enabled
from backend.rag.runtime import get_service,authorized_release,scoped_access
from backend.rag import grounded_prompt_v4 as v4,grounded_prompt_v5 as v5
from backend.rag.context import serialize
from eval.rag.candidate_v5 import candidate_freeze
from eval.rag.release_gate import evaluate,policy
from eval.rag.suite import digest
ROOT=Path(__file__).resolve().parents[1]

class TestClient:
    def __init__(self,application):self.app=application
    def post(self,path,json):return self.request(path,'POST',json)
    def get(self,path):return self.request(path,'GET',None)
    def request(self,path,method,payload):
        async def run():
            output=[]
            async def receive():return {'type':'http.request','body':json.dumps(payload).encode(),'more_body':False}
            async def send(message):output.append(message)
            await self.app({'type':'http','asgi':{'version':'3.0'},'http_version':'1.1','method':method,'scheme':'http','path':path,'raw_path':path.encode(),'query_string':b'','headers':[(b'content-type',b'application/json')],'server':('test',80),'client':('test',1)},receive,send)
            body=b''.join(x.get('body',b'') for x in output).decode()
            return SimpleNamespace(status_code=output[0]['status'],text=body,json=lambda:json.loads(body))
        return asyncio.run(run())

class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.records=[json.loads(p.read_text()) for p in (ROOT/'eval/rag/v4-live-001').glob('*.json') if p.name!='manifest.json']
        self.decisions=json.loads((ROOT/'eval/rag/v4-live-001-decisions.json').read_text())
        for raw in self.records:raw['prompt_sha256']=v5.SHA256
        for d in self.decisions:
            d.update(hard=dict.fromkeys(d['hard'],True),core_blocker=False,quality=dict.fromkeys(d['quality'],2))
            d['evidence_sha256']=digest(next(r for r in self.records if r['case_id']==d['case_id']))
        self.manifest={'mode':'live','generation_config':policy()['runtime'],'results':[{'case_id':r['case_id'],'evidence_sha256':digest(r)} for r in self.records]}
    def gate(self):return evaluate(self.records,self.decisions,self.manifest,offline_pass=True,database_pass=True)
    def test_valid_fixture_and_frozen_v5(self):
        candidate_freeze();self.assertEqual(self.gate()['verdict'],'PASS')
        self.assertEqual(v5.PREFIX[1:],v4.PREFIX[1:]);self.assertTrue(v5.PREFIX[0][1].startswith(v4.PREFIX[0][1]))
        self.assertEqual(hashlib.sha256(serialize(v5.PREFIX).encode()).hexdigest(),v5.SHA256)
    def test_each_hard_and_core_blocks(self):
        for k in ['schema','grounding','citations','security']:
            self.decisions[0]['hard'][k]=False;self.assertEqual(self.gate()['verdict'],'FAIL');self.decisions[0]['hard'][k]=True
        self.decisions[0]['core_blocker']=True;self.assertEqual(self.gate()['verdict'],'FAIL')
    def test_missing_duplicate_tampered_and_offline_fail(self):
        self.records.pop();self.assertEqual(self.gate()['verdict'],'FAIL')
        self.setUp();self.records[0]['query']='tampered';self.assertEqual(self.gate()['verdict'],'FAIL')
        self.setUp();self.manifest['mode']='offline';self.assertEqual(self.gate()['verdict'],'FAIL')
    def test_quality_does_not_mask_failure(self):
        self.decisions[0]['quality']['task_completeness']=0;self.assertEqual(self.gate()['verdict'],'FAIL')
        for d in self.decisions:d['quality']=dict.fromkeys(d['quality'],1)
        self.assertEqual(self.gate()['verdict'],'FAIL')

class EndpointTests(unittest.TestCase):
    def tearDown(self):app.dependency_overrides.clear()
    def client(self):
        app.dependency_overrides[enabled]=lambda:None
        self.service=Mock();self.service.run.return_value=json.loads((ROOT/'eval/rag/v4-live-001/supported.json').read_text())['response']
        app.dependency_overrides[get_service]=lambda:self.service
        return TestClient(app)
    def test_disabled_no_calls(self):
        with patch.dict(os.environ,{'RAG_ENABLED':'false'}):
            self.assertEqual(TestClient(app).post('/rag/chat',json={'query':'x'}).status_code,503)
    def test_flag_alone_cannot_enable(self):
        with patch.dict(os.environ,{'RAG_ENABLED':'true','RAG_RELEASE_SHA256':''}):self.assertFalse(authorized_release())
    def test_schema_and_client_boundaries(self):
        c=self.client();self.assertEqual(c.post('/rag/chat',json={'query':'中文问题'}).status_code,200)
        for key in ['prompt_version','context','sources','temperature','top_k','tenant_id','allowed_document_ids','system_message']:
            self.assertEqual(c.post('/rag/chat',json={'query':'x',key:'override'}).status_code,422)
        for val in ['', '  ',123,'x'*8001]:self.assertEqual(c.post('/rag/chat',json={'query':val}).status_code,422)
        self.assertEqual(self.service.run.call_count,1)
        self.assertEqual(c.get('/health').status_code,200)
        self.assertEqual(c.get('/openapi.json').json()['paths']['/rag/chat']['post']['responses']['200']['content']['application/json']['schema']['$ref'],'#/components/schemas/RagResponse')
    def test_errors_are_safe(self):
        import psycopg
        from backend.rag.embedding import EmbeddingError
        c=self.client()
        for error,status in [(ProviderError(504,'PRIVATE BODY','llm_timeout'),504),(psycopg.OperationalError('PRIVATE BODY'),503),(EmbeddingError('PRIVATE BODY'),502),(ValueError('PRIVATE BODY'),503)]:
            self.service.run.side_effect=error
            response=c.post('/rag/chat',json={'query':'x'});self.assertEqual(response.status_code,status);self.assertNotIn('PRIVATE BODY',response.text)
    def test_scope_default_denies_and_is_server_owned(self):
        with patch.dict(os.environ,{'RAG_TENANT_ID':'synthetic','RAG_ALLOWED_DOCUMENT_IDS':'[]'}):self.assertEqual(scoped_access().allowed_document_ids,())
        with patch.dict(os.environ,{'RAG_TENANT_ID':'','RAG_ALLOWED_DOCUMENT_IDS':'[]'}):
            with self.assertRaises(ValueError):scoped_access()

class RuntimeTests(unittest.TestCase):
    def test_real_pipeline_composition_safe_telemetry(self):
        from backend.rag.runtime import RagService
        from backend.config import LLMSettings
        from test_grounded_generation import FakeProvider,answer
        from backend.rag.response import RagResponse
        raw=json.loads((ROOT/'eval/rag/v5-live-001/supported.json').read_text())
        provider=FakeProvider(answer(['40 then50 [S-f18c601ebfefd082]'],summary='Synthetic'))
        env={'RAG_TENANT_ID':'fixture','RAG_ALLOWED_DOCUMENT_IDS':'[]','POSTGRES_HOST':'localhost','POSTGRES_PORT':'5432','POSTGRES_DB':'fixture','POSTGRES_USER':'fixture','POSTGRES_PASSWORD':'private-fixture'}
        with patch.dict(os.environ,env),patch('backend.rag.runtime.load_settings',return_value=LLMSettings()),patch('backend.rag.runtime.load_embedding_settings'),patch('backend.rag.runtime.OpenAIEmbeddingProvider'),patch('backend.rag.runtime.psycopg.connect'),patch('backend.rag.runtime.retrieve',return_value=raw['retrieval']),patch('backend.rag.runtime.DeepSeekProvider',return_value=provider),self.assertLogs('uvicorn.error',level='INFO') as logs:
            result=RagService().run('PRIVATE QUERY BODY')
        RagResponse.model_validate(result)
        self.assertEqual(result['generation']['prompt_sha256'],v5.SHA256)
        self.assertEqual(result['sources'][0]['source_label'],'S-f18c601ebfefd082')
        self.assertNotIn('PRIVATE QUERY BODY',str(logs.output));self.assertNotIn('private-fixture',str(logs.output))
        self.assertIn('estimated_total_usd',str(logs.output))
    def test_receipt_required_and_tampering_rejected(self):
        from backend.rag import runtime
        with tempfile.TemporaryDirectory() as t:
            path=Path(t)/'release.json'
            receipt=json.loads((ROOT/'backend/rag/local_release.json').read_text());path.write_text(json.dumps(receipt))
            sha=hashlib.sha256(path.read_bytes()).hexdigest()
            with patch.object(runtime,'RELEASE_PATH',path),patch.dict(os.environ,{'RAG_ENABLED':'true','RAG_RELEASE_SHA256':sha}):
                self.assertTrue(authorized_release());path.write_text('{}');self.assertFalse(authorized_release())
