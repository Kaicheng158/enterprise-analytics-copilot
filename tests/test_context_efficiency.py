import json
import unittest
from backend.output import AnalyticsAnswer
from backend.prompts import build_messages
from eval.context_efficiency import compact_candidate, measurement_prefixes


class ContextEfficiencyTests(unittest.TestCase):
    def test_candidate_only_changes_schema_whitespace(self):
        user = 'Synthetic test, with user text preserved exactly.  数据'
        baseline = build_messages(user)
        candidate = compact_candidate(user)
        before, schema = baseline[0]['content'].rsplit('\nJSON schema: ', 1)
        candidate_before, candidate_schema = candidate[0]['content'].rsplit('\nJSON schema: ', 1)
        self.assertEqual(candidate_before, before)
        self.assertEqual(json.loads(candidate_schema), json.loads(schema))
        self.assertEqual(json.loads(candidate_schema), AnalyticsAnswer.model_json_schema())
        self.assertEqual(candidate[1:], baseline[1:])
        self.assertLessEqual(len(candidate_schema), len(schema))

    def test_prefix_stable_and_user_is_last(self):
        for builder in [build_messages, compact_candidate]:
            first, second = builder('First synthetic question'), builder('Second synthetic question')
            self.assertEqual(first[:-1],second[:-1])
            self.assertEqual(second[-1],{'role':'user','content':'Second synthetic question'})
            first[0]['content'] = 'mutated'
            self.assertEqual(builder('Again')[:-1], second[:-1])

    def test_attribution_changes_one_group_at_a_time(self):
        probes = dict(measurement_prefixes())
        self.assertEqual(list(probes), ['control','system','contract','schema','examples','user_increment'])
        for left,right in [('control','system'),('system','contract'),('contract','schema')]:
            self.assertTrue(probes[right][0]['content'].startswith(probes[left][0]['content']))
            self.assertEqual(probes[right][-1], probes[left][-1])
        self.assertEqual(probes['schema'][0],probes['examples'][0])
        self.assertEqual(probes['examples'][:-1],probes['user_increment'][:-1])
