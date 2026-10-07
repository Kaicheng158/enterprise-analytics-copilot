import unittest
from backend.rag import grounded_prompt as v1, grounded_prompt_v2 as v2, grounded_prompt_v3 as v3, generation
from backend.rag.context import ContextBuilder
from backend.config import LLMSettings
from eval.rag.candidate_runner_v3 import generator_class,candidate_freeze
from test_grounded_generation import FakeProvider,answer


class V3CandidateTests(unittest.TestCase):
    def test_only_completion_rule_added(self):
        self.assertEqual(v3.PREFIX[:-1],v2.PREFIX[:-1])
        self.assertEqual(v3.PREFIX[-1],(v2.PREFIX[-1][0],v2.PREFIX[-1][1]+'\n'+v3.DELIVERABLE_COMPLETION))
        v3.messages('x','');candidate_freeze()
    def test_isolated_candidate_metadata_and_unchanged_validation(self):
        cls=generator_class();p=FakeProvider(answer())
        result=cls(p,LLMSettings()).generate('x',ContextBuilder().build([]))
        self.assertEqual(result['generation']['prompt_sha256'],v3.SHA256)
        self.assertEqual(result['generation']['prompt_version'],'rag-grounded-v3')
        self.assertIs(generation.prompt,v1)
        self.assertEqual(cls.generate.__code__.co_code,generation.GroundedGenerator.generate.__code__.co_code)
        self.assertEqual(cls.generate.__globals__['validate_sources'].__code__.co_code,generation.validate_sources.__code__.co_code)
