import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from backend.config import LLMSettings
from backend.llm import ProviderError
from backend.output import parse_answer
from backend.rag.context import ContextBuilder
from backend.rag.generation import GroundedGenerator,validate_sources
from backend.rag.attribution_diagnostics import DiagnosticGroundedGenerator,diagnose
from backend.rag.response_semantics_draft import ATTRIBUTED_EXAMPLES,HISTORICAL_EXAMPLES,FIELD_RESPONSIBILITIES
from eval.rag.diagnostic_evidence import save_synthetic_failure
from test_grounded_generation import FakeProvider,answer
from test_context_builder import hit


class AttributionDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.context=ContextBuilder().build([hit('Synthetic units = 7.')])
        self.label=self.context['blocks'][0]['source_label']

    def rejected(self,a,mode='production'):
        provider=FakeProvider(a)
        try:DiagnosticGroundedGenerator(provider,LLMSettings(),capture_mode=mode).generate('Summarize',self.context)
        except ProviderError as e:
            self.assertEqual(len(provider.calls),1)
            return e
        self.fail('Expected failure')

    def test_missing_field_and_item_index(self):
        e=self.rejected(answer([f'Seven [{self.label}]','Private failure text']))
        self.assertEqual(e.attribution_diagnostic['failed_field'],'facts')
        self.assertEqual(e.attribution_diagnostic['item_index'],1)
        self.assertEqual(e.attribution_diagnostic['parsing_result'],'missing_recognized_attribution')
        self.assertFalse(hasattr(e,'synthetic_eval_evidence'))
        self.assertNotIn('Private failure text',str(e.telemetry)+str(e.attribution_diagnostic))

    def test_production_logs_do_not_contain_body_even_malformed_token(self):
        with patch('backend.rag.generation.logger.info') as log:
            e=self.rejected(answer(['Secret-body [S-private-secret-text]']))
        logged=str(log.call_args_list)
        for secret in ['Secret-body','private-secret-text']:
            self.assertNotIn(secret,logged+str(e.telemetry)+str(e.attribution_diagnostic))
        self.assertEqual(e.attribution_diagnostic['malformed_token_count'],1)

    def test_synthetic_retains_answer_and_parsing_without_logging(self):
        a=answer([f'Seven [{self.label}]','Not attributed'])
        with patch('backend.rag.generation.logger.info') as log:e=self.rejected(a,'synthetic_eval')
        evidence=e.synthetic_eval_evidence
        self.assertEqual(evidence['rejected_structured_response'],a.model_dump())
        self.assertEqual(evidence['available_source_labels'],[self.label])
        self.assertEqual(evidence['detected_source_labels'],[])
        self.assertNotIn('Not attributed',str(log.call_args_list))

    def test_unknown_label_distinct_from_malformed(self):
        e=self.rejected(answer(['Wrong [S-0000000000000000]']),'synthetic_eval')
        self.assertEqual(e.code,'rag_invalid_citation')
        self.assertEqual(e.attribution_diagnostic['valid_unknown_label_count'],1)
        self.assertEqual(e.synthetic_eval_evidence['detected_source_labels'],['S-0000000000000000'])
        e=self.rejected(answer(['Wrong [S-bad]']),'synthetic_eval')
        self.assertEqual(e.attribution_diagnostic['malformed_token_count'],1)

    def test_bare_label_not_misdiagnosed_as_unknown(self):
        e=self.rejected(answer(['Seven '+self.label]),'synthetic_eval')
        self.assertEqual(e.code,'rag_missing_attribution')
        self.assertEqual(e.attribution_diagnostic['matched_source_token_count'],0)
        self.assertIn(self.label,e.synthetic_eval_evidence['rejected_structured_response']['facts'][0])

    def test_non_fact_invalid_label_and_first_failure_order(self):
        a=answer(['Missing fact attribution'],summary='Wrong [S-bad]')
        e=self.rejected(a)
        self.assertEqual(e.attribution_diagnostic['failed_field'],'summary')
        self.assertEqual(e.code,'rag_invalid_citation')

    def test_acceptance_and_errors_identical_to_original_validator(self):
        texts=['', 'No evidence', '[user]', f'Fact [{self.label}]', '[S-bad]', '[S-0000000000000000]',
               f'Bare {self.label}', f'[{self.label}] [S-bad]', '[S-incomplete', '[USER]']
        for text in texts:
            a=answer([text]);expected=None
            try:validate_sources(a,self.context['blocks'])
            except ProviderError as e:expected=e.code
            provider=FakeProvider(a)
            try:
                output=DiagnosticGroundedGenerator(provider,LLMSettings()).generate('x',self.context)
                self.assertIsNone(expected)
                self.assertEqual(output['answer'],a.model_dump())
            except ProviderError as e:self.assertEqual(e.code,expected)

    def test_empty_facts_insufficient_evidence_requires_no_citation(self):
        a=answer(summary='Cannot determine from evidence',limitations=['Relevant measurements absent'])
        result=DiagnosticGroundedGenerator(FakeProvider(a),LLMSettings()).generate('x',ContextBuilder().build([]))
        self.assertEqual(result['citations'],[])
        self.assertEqual(result['sources'],[])

    def test_upstream_errors_not_relabelled_or_captured(self):
        class Broken:
            def generate_messages(self,*args):raise ProviderError(504,'Timeout','llm_timeout')
        try:DiagnosticGroundedGenerator(Broken(),LLMSettings(),capture_mode='synthetic_eval').generate('x',self.context)
        except ProviderError as e:
            self.assertEqual(e.code,'llm_timeout');self.assertFalse(hasattr(e,'synthetic_eval_evidence'))

    def test_explicit_capture_mode_and_settings_validation(self):
        with self.assertRaises(ValueError):DiagnosticGroundedGenerator(FakeProvider(answer()),LLMSettings(),capture_mode='automatic')
        p=FakeProvider(answer());p.settings=LLMSettings(max_output_tokens=100)
        with self.assertRaises(ValueError):DiagnosticGroundedGenerator(p,LLMSettings())

    def test_writer_private_exclusive_and_opt_in_required(self):
        e=self.rejected(answer(['Synthetic missing attribution']),'synthetic_eval')
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'failure.json';save_synthetic_failure(path,e)
            self.assertEqual(os.stat(path).st_mode & 0o777,0o600)
            self.assertEqual(json.loads(path.read_text())['diagnostic']['failed_field'],'facts')
            with self.assertRaises(FileExistsError):save_synthetic_failure(path,e)
            prod=self.rejected(answer(['No attribution']))
            with self.assertRaises(ValueError):save_synthetic_failure(Path(tmp)/'prod.json',prod)

    def test_few_shot_schema_and_real_validator_consistency(self):
        for old,new in zip(HISTORICAL_EXAMPLES,ATTRIBUTED_EXAMPLES):
            self.assertEqual(old[0],new[0])
            if old[0]=='user':self.assertEqual(old,new);continue
            before=parse_answer(old[1]);after=parse_answer(new[1])
            validate_sources(after,[])
            self.assertEqual(after.facts,[x+' [user]' for x in before.facts])
            self.assertEqual(after.summary,before.summary)
            self.assertEqual(after.interpretation[:-1],before.interpretation)
            self.assertTrue(after.interpretation[-1].startswith('Suggested next step:'))
            self.assertEqual(len(after.limitations),1)
        self.assertIn('interpretation:',FIELD_RESPONSIBILITIES)
        self.assertIn('not as an action already executed',FIELD_RESPONSIBILITIES)
