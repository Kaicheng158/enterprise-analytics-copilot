import asyncio
from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
from types import MappingProxyType
import unittest
from unittest.mock import patch

from backend.llm import DeepSeekProvider, ProviderError
from backend.prompt_registry import RELEASES, PromptRelease, prompt_metadata, prompt_sha256
from backend.prompts import build_messages
from eval.context_efficiency import compact_candidate
from eval.run_uncertainty import collect
from test_acceptance import send_request
from test_retry_cost import success, failure


class PromptVersioningTests(unittest.TestCase):
    def test_release_exactly_matches_phase28_production_snapshot(self):
        artifact = json.loads(Path('eval/efficiency-baseline.json').read_text())
        messages = build_messages('')
        self.assertEqual(messages[:-1], artifact['fixed_prefix_snapshot'])
        self.assertEqual(prompt_sha256(messages), artifact['prompt_sha256'])
        self.assertEqual(prompt_metadata(messages)['prompt_version'], 'analytics-v1')
        self.assertEqual(prompt_sha256(build_messages('private input')), artifact['prompt_sha256'])

    def test_release_and_registry_are_immutable_and_tampering_fails(self):
        release = RELEASES['analytics-v1']
        with self.assertRaises(TypeError): RELEASES['analytics-v1'] = release
        with self.assertRaises(FrozenInstanceError): release.sha256 = 'changed'
        altered = replace(release, prefix=(('system','altered'),))
        with self.assertRaises(ValueError): altered.messages('Hi')
        with patch('backend.prompt_registry.RELEASES', {'analytics-v1':altered}), patch('backend.llm.urlopen') as call:
            with self.assertRaises(ProviderError) as caught:
                DeepSeekProvider('key','deepseek-flash').chat('Hi')
        self.assertEqual(caught.exception.code,'llm_invalid_prompt')
        call.assert_not_called()

    def test_server_selection_and_rollback(self):
        original = build_messages('Hi')
        prefix = (('system','Test-only release; never deployed.'),)
        msgs = [{'role':'system','content':prefix[0][1]},{'role':'user','content':''}]
        fixture = PromptRelease('test-only',prefix,prompt_sha256(msgs))
        with patch('backend.prompt_registry.RELEASES', MappingProxyType({**RELEASES,'test-only':fixture})):
            with patch('backend.prompt_registry.ACTIVE_PROMPT_VERSION','test-only'):
                self.assertEqual(build_messages('Hi')[0]['content'],prefix[0][1])
                self.assertEqual(prompt_metadata(build_messages('Hi'))['prompt_version'],'test-only')
            self.assertEqual(build_messages('Hi'), original)
        with patch('backend.prompt_registry.ACTIVE_PROMPT_VERSION','unknown'):
            with self.assertRaises(ValueError): build_messages('Hi')

    def test_public_chat_rejects_version_override(self):
        with patch('backend.llm.urlopen') as call:
            status,_ = asyncio.run(send_request(b'{"user_message":"Hi","prompt_version":"analytics-v1"}'))
        self.assertEqual(status,422)
        call.assert_not_called()

    def test_metadata_matches_retry_payloads_and_success_logs(self):
        with patch('backend.llm.urlopen',side_effect=[failure(503),success()]) as call, patch('backend.llm.time.sleep'), self.assertLogs('uvicorn.error',level='INFO') as logs:
            result = DeepSeekProvider('private-key','deepseek-flash').chat('private input')
        records=[json.loads(r.getMessage().split(' ',1)[1]) for r in logs.records]
        for r in records:
            self.assertEqual(r['prompt_version'],'analytics-v1')
            self.assertEqual(r['prompt_sha256'],result.prompt_sha256)
        messages=[json.loads(c.args[0].data)['messages'] for c in call.call_args_list]
        self.assertEqual(messages[0],messages[1])
        self.assertEqual(result.prompt_sha256,prompt_sha256(messages[0]))
        self.assertNotIn('private',str(logs.output))

    def test_failure_logs_and_eval_keep_version(self):
        with patch('backend.llm.urlopen',side_effect=failure(401)), self.assertLogs('uvicorn.error',level='INFO') as logs:
            report = collect(DeepSeekProvider('private-key','deepseek-flash'),[{'id':'error','input':'Synthetic'}])
        self.assertEqual(report['prompt_version'],'analytics-v1')
        self.assertEqual(report['records'][0]['prompt_version'],'analytics-v1')
        for log in logs.records:
            row=json.loads(log.getMessage().split(' ',1)[1])
            self.assertEqual(row['prompt_sha256'],report['prompt_sha256'])
            self.assertEqual(row['prompt_version'],report['prompt_version'])

    def test_eval_candidate_is_not_mislabeled_as_published(self):
        with patch('backend.llm.build_messages',compact_candidate), patch('eval.run_uncertainty.build_messages',compact_candidate), patch('backend.llm.urlopen',return_value=success()):
            report = collect(DeepSeekProvider('key','deepseek-flash'),[{'id':'synthetic','input':'Synthetic'}])
        self.assertEqual(report['prompt_version'],'unpublished')
        self.assertEqual(report['records'][0]['prompt_version'],'unpublished')
        self.assertEqual(report['records'][0]['prompt_sha256'],report['prompt_sha256'])
        self.assertNotEqual(report['prompt_sha256'],RELEASES['analytics-v1'].sha256)
