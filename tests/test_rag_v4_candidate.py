import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from backend.config import LLMSettings
from backend.llm import ProviderError
from backend.output import parse_answer
from backend.rag import grounded_prompt as v1,grounded_prompt_v3 as v3,grounded_prompt_v4 as v4,generation
from backend.rag.response_semantics_draft import FIELD_RESPONSIBILITIES,ATTRIBUTED_EXAMPLES
from backend.rag.context import ContextBuilder
from eval.rag.candidate_v4 import candidate_freeze,generator_class,generate_synthetic
from test_grounded_generation import FakeProvider,answer


class V4Tests(unittest.TestCase):
    def test_exact_composition_and_hash(self):
        candidate_freeze()
        self.assertEqual(v4.PREFIX[0][1],v3.PREFIX[0][1]+'\nField responsibilities:\n'+FIELD_RESPONSIBILITIES)
        self.assertEqual(v4.PREFIX[1:-1],ATTRIBUTED_EXAMPLES)
        self.assertEqual(v4.PREFIX[-1],v3.PREFIX[-1])
        self.assertNotEqual(v4.SHA256,v3.SHA256)

    def test_examples_use_unchanged_schema_and_validator(self):
        for role,content in v4.PREFIX:
            if role=='assistant':generation.validate_sources(parse_answer(content),[])

    def test_active_prompt_and_validator_unchanged(self):
        cls=generator_class()
        self.assertIs(generation.prompt,v1)
        self.assertEqual(cls.generate.__code__.co_code,generation.GroundedGenerator.generate.__code__.co_code)
        self.assertEqual(cls.generate.__globals__['validate_sources'].__code__.co_code,generation.validate_sources.__code__.co_code)
        from backend.prompt_registry import active_release
        self.assertEqual(active_release().version,'analytics-v2')

    def test_empty_facts_and_proposed_action_succeed_without_fake_citation(self):
        a=answer(summary='Cannot establish the cause',interpretation=['Suggested next step: obtain relevant measurements and compare them.'],limitations=['Relevant measurements absent.'])
        with tempfile.TemporaryDirectory() as t:
            path=Path(t)/'failure.json';p=FakeProvider(a)
            out=generate_synthetic(p,LLMSettings(),'Give a next step',ContextBuilder().build([]),failure_evidence_path=path)
            self.assertFalse(path.exists());self.assertEqual(out['answer'],a.model_dump())
            self.assertEqual(out['generation']['prompt_sha256'],v4.SHA256)
            self.assertEqual(out['citations'],[]);self.assertEqual(p.calls[0][-1]['role'],'user')

    def test_synthetic_failure_saved_without_body_logging(self):
        with tempfile.TemporaryDirectory() as t:
            path=Path(t)/'failure.json';p=FakeProvider(answer(['Synthetic missing attribution']))
            with patch('backend.rag.generation.logger.info') as logs:
                with self.assertRaises(ProviderError) as e:
                    generate_synthetic(p,LLMSettings(),'x',ContextBuilder().build([]),failure_evidence_path=path)
            self.assertEqual(e.exception.code,'rag_missing_attribution')
            data=json.loads(path.read_text());self.assertEqual(data['diagnostic']['failed_field'],'facts')
            self.assertEqual(data['diagnostic']['item_index'],0)
            self.assertEqual(data['rejected_structured_response']['facts'],['Synthetic missing attribution'])
            self.assertEqual(data['generation']['prompt_sha256'],v4.SHA256)
            self.assertNotIn('Synthetic missing attribution',str(logs.call_args_list))
            self.assertEqual(len(p.calls),1)
            with self.assertRaises(FileExistsError):generate_synthetic(p,LLMSettings(),'x',ContextBuilder().build([]),failure_evidence_path=path)
            self.assertEqual(len(p.calls),1)
