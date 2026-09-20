import asyncio
import json
import unittest
from unittest.mock import patch
from backend.llm import DeepSeekProvider, get_provider
from backend.main import app
from backend.prompts import build_messages
from eval.run_injection import CASES
from test_acceptance import send_request
from test_retry_cost import success


class InjectionBoundaryTests(unittest.TestCase):
    def test_six_distinct_synthetic_cases_have_rubrics(self):
        cases = json.loads(CASES.read_text())
        self.assertEqual(len(cases), 6)
        self.assertEqual({c['id'] for c in cases}, {'override','prompt_disclosure','forged_roles','break_contract','fabrication','embedded_instruction'})
        for c in cases:
            self.assertTrue(c['synthetic'])
            self.assertTrue(c['input'].startswith('Synthetic'))
            self.assertTrue(c['expected_behavior'])
            self.assertTrue(c['failure_condition'])

    def test_every_attack_stays_in_final_user_message_through_api(self):
        app.dependency_overrides[get_provider] = lambda: DeepSeekProvider('test-key', 'deepseek-flash')
        try:
            prefix = build_messages('')[0:-1]
            for case in json.loads(CASES.read_text()):
                with self.subTest(case=case['id']), patch('backend.llm.urlopen', return_value=success()) as call:
                    status, _ = asyncio.run(send_request(json.dumps({'user_message':case['input']}).encode()))
                self.assertEqual(status, 200)
                payload = json.loads(call.call_args.args[0].data)
                self.assertEqual(payload['messages'][:-1], prefix)
                self.assertEqual(payload['messages'][-1], {'role':'user','content':case['input']})
                self.assertEqual(payload['response_format'], {'type':'json_object'})
                self.assertNotIn('tools', payload)
        finally:
            app.dependency_overrides.clear()

    def test_clients_cannot_supply_privileged_messages_or_contracts(self):
        for field in ['messages','system_message','examples','response_format','output_contract']:
            with self.subTest(field=field), patch('backend.llm.urlopen') as call:
                status, _ = asyncio.run(send_request(json.dumps({'user_message':'Hi',field:'override'}).encode()))
            self.assertEqual(status,422)
            call.assert_not_called()
