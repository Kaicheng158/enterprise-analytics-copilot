import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from backend.rag.embedding import EmbeddingError,validate_vectors
from backend.rag.openai_embedding import OpenAIEmbeddingProvider,EmbeddingSettings,NoRedirect,load_embedding_settings


def payload(count=2):
    return {'model':'text-embedding-3-small','data':[{'index':i,'embedding':[float(i+1)]+[0.0]*1535} for i in reversed(range(count))],
            'usage':{'prompt_tokens':10,'total_tokens':10}}


def http_error(status,code='unknown'):
    return HTTPError('https://api.openai.com',status,'private error',{},io.BytesIO(json.dumps({'error':{'code':code,'message':'PRIVATE'}}).encode()))


class EmbeddingTests(unittest.TestCase):
    def setUp(self):self.p=OpenAIEmbeddingProvider(EmbeddingSettings('FAKE_TEST_KEY'))

    def test_request_dimensions_order_and_telemetry(self):
        with patch('backend.rag.openai_embedding.request_payload',return_value=payload()) as call,self.assertLogs('rag.embedding',level='INFO') as logs:
            result=self.p.embed_documents(('hello','中文'))
        body=json.loads(call.call_args.args[0].data)
        self.assertEqual(body,{'model':'text-embedding-3-small','input':['hello','中文'],'dimensions':1536,'encoding_format':'float'})
        self.assertEqual(result.vectors[0][0],1);self.assertEqual(result.vectors[1][0],2)
        self.assertEqual(result.input_tokens,10);self.assertEqual(result.estimated_cost_usd,'2E-7')
        self.assertTrue(result.cost_complete)
        self.assertNotIn('FAKE_TEST_KEY',str(logs.output));self.assertNotIn('hello',str(logs.output))

    def test_bad_vectors(self):
        for vector in [(),(0,0),(float('nan'),1),(float('inf'),1),(True,1),('1',1),(1e100,0)]:
            with self.subTest(vector=vector),self.assertRaises(EmbeddingError):validate_vectors([vector],1,2)
        with self.assertRaises(EmbeddingError):validate_vectors([],1,2)

    def test_malformed_response_fails_without_retry(self):
        variants=[]
        for mutate in [lambda x:x.update(model='wrong'),lambda x:x['data'][0].update(index=0),lambda x:x['data'][0].update(embedding=[1]),lambda x:x['usage'].update(total_tokens=99),lambda x:x.update(data=[]),lambda x:x['data'][0].update(index=True)]:
            p=payload();mutate(p);variants.append(p)
        variants.extend([{},None,[]])
        for p in variants:
            with self.subTest(p=type(p)),patch('backend.rag.openai_embedding.request_payload',return_value=p) as call,self.assertRaises(EmbeddingError):
                self.p.embed_documents(('a','b'))
            self.assertEqual(call.call_count,1)

    def test_retry_backoff_and_cost_incomplete(self):
        with patch('backend.rag.openai_embedding.request_payload',side_effect=[http_error(503),payload()]) as call,patch('backend.rag.openai_embedding.time.sleep') as sleep:
            r=self.p.embed_documents(('a','b'))
        self.assertEqual(call.call_count,2);self.assertEqual(r.retry_count,1);self.assertFalse(r.cost_complete)
        self.assertTrue(1<=sleep.call_args.args[0]<=1.25)

    def test_retry_limit(self):
        with patch('backend.rag.openai_embedding.request_payload',side_effect=[http_error(429) for _ in range(3)]) as call,patch('backend.rag.openai_embedding.time.sleep'),self.assertRaises(EmbeddingError):self.p.embed_documents(('a',))
        self.assertEqual(call.call_count,3)

    def test_auth_quota_network_not_retried_or_leaked(self):
        for error in [http_error(401),http_error(403),http_error(400),http_error(429,'insufficient_quota'),URLError('PRIVATE'),TimeoutError('PRIVATE')]:
            with self.subTest(error=type(error)),patch('backend.rag.openai_embedding.request_payload',side_effect=error) as call,self.assertLogs('rag.embedding',level='INFO') as logs,self.assertRaises(EmbeddingError) as caught:
                self.p.embed_documents(('a',))
            self.assertEqual(call.call_count,1)
            self.assertNotIn('PRIVATE',str(logs.output)+str(caught.exception))

    def test_input_limits_before_network(self):
        for texts in [(),('',),(' '*5,),('中'*3000,),('a',)*17]:
            with patch('backend.rag.openai_embedding.request_payload') as call,self.assertRaises(EmbeddingError):self.p.embed_documents(texts)
            call.assert_not_called()

    def test_configuration_and_secret_repr(self):
        self.assertNotIn('FAKE_TEST_KEY',repr(self.p.settings))
        for kwargs in [{'api_key':''},{'api_key':'fake','dimensions':1024},{'api_key':'fake','model':'other'}]:
            with self.assertRaises(EmbeddingError):EmbeddingSettings(**kwargs)
        with patch('backend.rag.openai_embedding.load_dotenv'),patch.dict('os.environ',{'OPENAI_API_KEY':'fake','OPENAI_EMBEDDING_MODEL':'text-embedding-3-small','OPENAI_EMBEDDING_DIMENSIONS':'1536'}):
            self.assertEqual(load_embedding_settings().dimensions,1536)
        self.assertIsNone(NoRedirect().redirect_request(None,None,302,None,None,'https://untrusted'))
