from copy import deepcopy
import unittest
from backend.rag import grounded_prompt as v1,grounded_prompt_v2 as v2,generation
from backend.rag.context import ContextBuilder
from backend.config import LLMSettings
from eval.rag.candidate_runner import generator_class,candidate_freeze
from test_grounded_generation import FakeProvider,answer


class CandidateTests(unittest.TestCase):
    def test_single_generic_addition_only(self):
        self.assertEqual(v2.PREFIX[:-1],v1.PREFIX[:-1])
        self.assertEqual(v2.PREFIX[-1],(v1.PREFIX[-1][0],v1.PREFIX[-1][1]+'\n'+v2.LIMITATION_RELEVANCE))
        self.assertNotEqual(v1.SHA256,v2.SHA256);v2.messages('x','')
        candidate_freeze()
    def test_isolated_provider_metadata_without_active_change(self):
        before=v1.messages('x','');provider=FakeProvider(answer());context=ContextBuilder().build([])
        result=generator_class()(provider,LLMSettings()).generate('x',context)
        self.assertEqual(result['generation']['prompt_version'],v2.VERSION)
        self.assertEqual(result['generation']['prompt_sha256'],v2.SHA256)
        self.assertIs(generation.prompt,v1);self.assertEqual(v1.messages('x',''),before)
    def test_budget_and_validator_same_code(self):
        candidate=generator_class()
        self.assertEqual(candidate.generate.__code__.co_code,generation.GroundedGenerator.generate.__code__.co_code)
        self.assertEqual(candidate.generate.__globals__['validate_sources'].__code__.co_code,generation.validate_sources.__code__.co_code)
