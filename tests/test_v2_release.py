import unittest
from unittest.mock import patch
from backend.prompt_registry import RELEASES, CANDIDATES, active_release, prompt_metadata


class V2ReleaseTests(unittest.TestCase):
    def test_v1_is_immutable_historical_release(self):
        self.assertEqual(RELEASES['analytics-v1'].sha256,
                         'bc480e10f14ae9d6158a70014eb4aef2b2ee0c00137f655e8cb05955a349bfb8')
        RELEASES['analytics-v1'].messages('Synthetic')

    def test_v2_adds_only_generic_instruction_without_changing_examples_or_schema(self):
        v2=({**RELEASES,**CANDIDATES})['analytics-v2']
        before=RELEASES['analytics-v1'].messages('Synthetic user')
        after=v2.messages('Synthetic user')
        self.assertEqual(before[:-1],after[:-2])
        self.assertEqual(before[-1],after[-1])
        self.assertEqual(after[-2]['role'],'system')
        added=after[-2]['content']
        for case_id in ['metric_change','unsupported_cause','limited_analysis']:
            self.assertNotIn(case_id,added)
        self.assertNotEqual(v2.sha256,RELEASES['analytics-v1'].sha256)
        self.assertEqual(prompt_metadata(after)['prompt_version'],'analytics-v2')

    def test_candidate_is_not_activatable_before_publication(self):
        for version in CANDIDATES:
            self.assertNotIn(version,RELEASES)
            with patch('backend.prompt_registry.ACTIVE_PROMPT_VERSION',version):
                with self.assertRaises(ValueError):active_release()
        self.assertIn(active_release().version,RELEASES)

    def test_published_v2_is_active_and_matches_accepted_checksum(self):
        self.assertNotIn('analytics-v2', CANDIDATES)
        self.assertIs(active_release(), RELEASES['analytics-v2'])
        self.assertEqual(active_release().sha256,
                         'b04bd2ea98f0e007b00c7a76e4b6a7e7ef8cd28478f350589e81f1f7b52d3fd6')
        active_release().messages('Synthetic release verification')

    def test_retained_v1_rollback_selection(self):
        with patch('backend.prompt_registry.ACTIVE_PROMPT_VERSION', 'analytics-v1'):
            self.assertIs(active_release(), RELEASES['analytics-v1'])
