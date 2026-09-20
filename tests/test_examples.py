import unittest
from backend.examples import FEW_SHOT_EXAMPLES
from backend.output import parse_answer
from backend.prompts import build_messages


class FewShotTests(unittest.TestCase):
    def test_two_examples_validate_and_have_distinct_evidence_patterns(self):
        self.assertEqual(len(FEW_SHOT_EXAMPLES), 2)
        answers = [parse_answer(answer) for _, answer in FEW_SHOT_EXAMPLES]
        for user, _ in FEW_SHOT_EXAMPLES:
            self.assertIn('Synthetic', user)
        first, second = answers
        self.assertEqual(len(first.facts), 2)
        self.assertIn('-20 / 100 = -20%', first.facts[1])
        self.assertFalse(any('price' in fact.lower() for fact in first.facts))
        self.assertIn('unverified hypothesis', first.interpretation[0])
        self.assertEqual(second.facts, [])
        self.assertIn('unknown', second.summary)
        self.assertIn('presupposes', second.interpretation[0])
        for answer in answers:
            self.assertTrue(answer.limitations)

    def test_actual_user_is_last_and_examples_are_request_isolated(self):
        user = 'Analyse this separate scenario: 7 orders.'
        messages = build_messages(user)
        self.assertEqual([m['role'] for m in messages],
                         ['system', 'user', 'assistant', 'user', 'assistant', 'user'])
        self.assertEqual(messages[-1], {'role':'user', 'content':user})
        self.assertEqual([m['content'] for m in messages[1:-1]],
                         [text for pair in FEW_SHOT_EXAMPLES for text in pair])
        messages[1]['content'] = 'mutated'
        messages[2]['content'] = '{}'
        messages.pop()
        fresh = build_messages('Next request')
        self.assertEqual(fresh[1]['content'], FEW_SHOT_EXAMPLES[0][0])
        self.assertEqual(fresh[2]['content'], FEW_SHOT_EXAMPLES[0][1])
        self.assertEqual(fresh[-1]['content'], 'Next request')

    def test_client_cannot_supply_examples(self):
        from pydantic import ValidationError
        from backend.main import ChatRequest
        with self.assertRaises(ValidationError):
            ChatRequest(user_message='Hi', examples=[])
