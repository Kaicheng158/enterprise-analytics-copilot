import unittest
from unittest.mock import patch
from backend.rag.retrieval import RetrievalConfig,scope_parameters
from backend.rag.models import AccessScope
from backend.rag.openai_embedding import OpenAIEmbeddingProvider,EmbeddingSettings
from backend.rag.embedding import EmbeddingError


class RetrievalTests(unittest.TestCase):
    def test_config_rejects_bad_limits(self):
        for kwargs in [{'top_k':0},{'top_k':101},{'top_k':True},{'max_distance':float('nan')},{'max_distance':float('inf')},{'max_distance':-1},{'max_distance':2.1},{'max_distance':True}]:
            with self.subTest(kwargs=kwargs),self.assertRaises(ValueError):RetrievalConfig(**kwargs)
        self.assertIsNone(RetrievalConfig().max_distance)

    def test_query_adapter_same_profile_and_request_format(self):
        provider=OpenAIEmbeddingProvider(EmbeddingSettings('fake-test-key'))
        response={'model':'text-embedding-3-small','data':[{'index':0,'embedding':[1.]+[0.]*1535}],'usage':{'prompt_tokens':5,'total_tokens':5}}
        with patch('backend.rag.openai_embedding.request_payload',return_value=response) as call:
            result=provider.embed_query('Revenue definition')
        import json
        self.assertEqual(result.profile,provider.profile)
        self.assertEqual(json.loads(call.call_args.args[0].data)['input'],['Revenue definition'])
        self.assertEqual(len(result.vectors),1)
        self.assertEqual(len(result.vectors[0]),1536)

    def test_scope_is_explicit_and_invalid_ids_rejected(self):
        profile=OpenAIEmbeddingProvider(EmbeddingSettings('fake')).profile
        self.assertEqual(scope_parameters(AccessScope('tenant',()),profile)[1],[])
        with self.assertRaises(EmbeddingError):scope_parameters(AccessScope('tenant',('bad-id',)),profile)
